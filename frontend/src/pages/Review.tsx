import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Trash2, Check, FileText, X, ChevronLeft, ChevronRight,
  ZoomIn, Loader2, Filter, Sparkles, Ban, Pause, Play,
} from 'lucide-react';
import { api, reasonLabel } from '../lib/api';
import type { Job, ReviewPhoto, AiReclassifyProgress, AiProviderInfo } from '../lib/api';

// ── AI progress state ──
interface AiProgressState {
  taskId: string;
  jobId: number;
  total: number;
  processed: number;
  currentFile: string;
  classified: number;
  kept: number;
  trashed: number;
  documents: number;
  stillReview: number;
  providerUsed: string;
  status: 'running' | 'paused' | 'done' | 'cancelled' | 'error';
  error?: string;
}

export default function Review() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [photos, setPhotos] = useState<ReviewPhoto[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [minConf] = useState(0);
  const [maxConf, setMaxConf] = useState(1);

  // AI state
  const [aiProgress, setAiProgress] = useState<AiProgressState | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ mode: 'all' | 'selected'; count: number } | null>(null);
  const [providerInfo, setProviderInfo] = useState<AiProviderInfo | null>(null);

  // WebSocket ref
  const wsRef = useRef<WebSocket | null>(null);

  const pageSize = 48;

  // Load jobs
  useEffect(() => {
    api.listJobs(undefined, 20).then((j) => {
      const withData = j.filter((x) => x.review_count > 0 || x.status === 'completed' || x.status === 'running');
      setJobs(withData);
      if (withData.length && !selectedJobId) setSelectedJobId(withData[0].id);
    });
  }, []);

  // Load provider info for confirmation dialog
  useEffect(() => {
    api.getProviderInfo().then(setProviderInfo).catch(() => {});
  }, []);

  // Restore active AI task on mount (e.g. after tab switch)
  useEffect(() => {
    api.getActiveAiTask().then((task) => {
      if (task) {
        setAiProgress({
          taskId: task.task_id,
          jobId: task.job_id,
          total: task.total,
          processed: task.processed,
          currentFile: task.current_file,
          classified: task.classified,
          kept: task.kept,
          trashed: task.trashed,
          documents: task.documents,
          stillReview: task.still_review,
          providerUsed: task.provider_used,
          status: task.status,
        });
      }
    }).catch(() => {});
  }, []);

  // Load review photos
  const loadPhotos = useCallback(async () => {
    if (!selectedJobId) return;
    setLoading(true);
    try {
      const [data, count] = await Promise.all([
        api.getReviewPhotos(selectedJobId, page, pageSize, minConf, maxConf),
        api.countReviewPhotos(selectedJobId),
      ]);
      setPhotos(data);
      setTotalCount(count.count);
    } catch { /* ignore */ }
    setLoading(false);
  }, [selectedJobId, page, minConf, maxConf]);

  useEffect(() => { loadPhotos(); }, [loadPhotos]);

  // Auto-reload when current page empties (AI classified all visible photos)
  useEffect(() => {
    if (photos.length === 0 && !loading && aiProgress?.status === 'running' && totalCount > 0) {
      const timer = setTimeout(() => loadPhotos(), 1000);
      return () => clearTimeout(timer);
    }
  }, [photos.length, loading, aiProgress?.status, totalCount, loadPhotos]);

  // WebSocket for AI progress
  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.event === 'ai_reclassify_progress') {
          const p = msg as AiReclassifyProgress;
          setAiProgress((prev) => prev ? {
            ...prev,
            processed: p.processed,
            currentFile: p.current_file,
            classified: p.classified,
            kept: p.kept,
            trashed: p.trashed,
            documents: p.documents,
            stillReview: p.still_review,
          } : prev);
          // Remove classified photos from grid in real-time
          if (p.result && p.result !== 'processing' && p.result !== 'review' && p.result !== 'skip_video') {
            setPhotos((prev) => prev.filter((ph) => ph.id !== p.photo_id));
            setTotalCount((c) => Math.max(0, c - 1));
          }
        } else if (msg.event === 'ai_reclassify_done') {
          setAiProgress((prev) => prev ? { ...prev, status: 'done', processed: msg.total } : prev);
        } else if (msg.event === 'ai_reclassify_cancelled') {
          setAiProgress((prev) => prev ? { ...prev, status: 'cancelled' } : prev);
        } else if (msg.event === 'ai_reclassify_paused') {
          setAiProgress((prev) => prev ? { ...prev, status: 'paused' } : prev);
        } else if (msg.event === 'ai_reclassify_resumed') {
          setAiProgress((prev) => prev ? { ...prev, status: 'running' } : prev);
        } else if (msg.event === 'ai_reclassify_error') {
          setAiProgress((prev) => prev ? { ...prev, status: 'error', error: msg.error } : prev);
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      // Reconnect
      setTimeout(() => {
        if (wsRef.current === ws) wsRef.current = null;
      }, 2000);
    };

    return () => { ws.close(); };
  }, []);

  const totalPages = Math.ceil(totalCount / pageSize);

  // ── Actions ──
  const reclassify = async (photoId: number, action: string) => {
    await api.reclassifyPhoto(photoId, action);
    setPhotos((prev) => prev.filter((p) => p.id !== photoId));
    setTotalCount((c) => c - 1);
    setSelected((s) => { s.delete(photoId); return new Set(s); });
    if (lightbox !== null) {
      const idx = photos.findIndex((p) => p.id === photoId);
      if (!(idx >= 0 && idx < photos.length - 1)) setLightbox(null);
    }
  };

  const batchAction = async (action: string) => {
    if (selected.size === 0) return;
    await api.batchReclassify([...selected], action);
    setPhotos((prev) => prev.filter((p) => !selected.has(p.id)));
    setTotalCount((c) => c - selected.size);
    setSelected(new Set());
  };

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === photos.length) setSelected(new Set());
    else setSelected(new Set(photos.map((p) => p.id)));
  };

  // AI reclassify
  const showConfirmDialog = (mode: 'all' | 'selected') => {
    const count = mode === 'selected' ? selected.size : totalCount;
    setConfirmDialog({ mode, count });
  };

  const startAiReclassify = async () => {
    if (!selectedJobId || !confirmDialog) return;
    const photoIds = confirmDialog.mode === 'selected' ? [...selected] : undefined;
    setConfirmDialog(null);

    try {
      const result = await api.aiReclassify(selectedJobId, photoIds);
      setAiProgress({
        taskId: result.task_id,
        jobId: selectedJobId,
        total: result.total,
        processed: 0,
        currentFile: '',
        classified: 0,
        kept: 0,
        trashed: 0,
        documents: 0,
        stillReview: 0,
        providerUsed: result.provider_used,
        status: 'running',
      });
      setSelected(new Set());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Error desconocido';
      alert(`Error: ${msg}`);
    }
  };

  const cancelAi = async () => {
    if (aiProgress?.taskId) {
      try { await api.cancelAiReclassify(aiProgress.taskId); } catch { /* ignore */ }
    }
  };

  const pauseAi = async () => {
    if (aiProgress?.taskId) {
      try { await api.pauseAiReclassify(aiProgress.taskId); } catch { /* ignore */ }
    }
  };

  const resumeAi = async () => {
    if (aiProgress?.taskId) {
      try { await api.resumeAiReclassify(aiProgress.taskId); } catch { /* ignore */ }
    }
  };

  const dismissAi = () => {
    setAiProgress(null);
    loadPhotos();
  };

  // Lightbox
  const lbPhoto = lightbox !== null ? photos[lightbox] : null;
  const lbPrev = () => setLightbox((i) => (i !== null && i > 0 ? i - 1 : i));
  const lbNext = () => setLightbox((i) => (i !== null && i < photos.length - 1 ? i + 1 : i));

  useEffect(() => {
    if (lightbox === null) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') lbPrev();
      else if (e.key === 'ArrowRight') lbNext();
      else if (e.key === 'Escape') setLightbox(null);
      else if (e.key === 'd' || e.key === 'Delete') lbPhoto && reclassify(lbPhoto.id, 'trash');
      else if (e.key === 'k') lbPhoto && reclassify(lbPhoto.id, 'keep');
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [lightbox, lbPhoto]);

  if (!selectedJobId) {
    return (
      <div className="text-center py-20 text-gray-500">
        No hay fotos pendientes de review.
      </div>
    );
  }

  const aiRunning = aiProgress?.status === 'running';

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Review</h1>
        <span className="text-sm text-gray-500">{totalCount} fotos pendientes</span>

        <div className="ml-auto flex items-center gap-3">
          {/* Job selector */}
          <select
            className="input w-auto text-sm"
            value={selectedJobId}
            onChange={(e) => { setSelectedJobId(Number(e.target.value)); setPage(1); }}
          >
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                Job #{j.id} - {j.nas_user} ({j.review_count} review)
              </option>
            ))}
          </select>

          {/* AI buttons */}
          {selected.size > 0 && (
            <button
              onClick={() => showConfirmDialog('selected')}
              disabled={aiRunning}
              className="btn-primary flex items-center gap-1.5 text-sm"
            >
              <Sparkles className="w-4 h-4" />
              IA seleccionadas ({selected.size})
            </button>
          )}
          <button
            onClick={() => showConfirmDialog('all')}
            disabled={aiRunning || totalCount === 0}
            className="btn-primary flex items-center gap-1.5 text-sm"
          >
            <Sparkles className="w-4 h-4" />
            IA todas ({totalCount})
          </button>

          {/* Confidence filter */}
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Filter className="w-4 h-4" />
            <input
              type="range"
              min={0} max={1} step={0.05}
              value={maxConf}
              onChange={(e) => setMaxConf(Number(e.target.value))}
              className="w-24 accent-purple-500"
            />
            <span className="w-8">{maxConf}</span>
          </div>
        </div>
      </div>

      {/* AI progress banner */}
      {aiProgress && (
        <AiProgressBanner
          progress={aiProgress}
          onCancel={cancelAi}
          onPause={pauseAi}
          onResume={resumeAi}
          onDismiss={dismissAi}
        />
      )}

      {/* Confirmation dialog */}
      {confirmDialog && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center" onClick={() => setConfirmDialog(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-md w-full mx-4 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-purple-400" />
              Clasificar con IA
            </h3>
            <p className="text-gray-300">
              Se van a clasificar <span className="font-bold text-white">{confirmDialog.count} fotos</span>
              {confirmDialog.mode === 'selected' ? ' seleccionadas' : ' en review'}
              {providerInfo?.available && (
                <> usando <span className="font-medium text-purple-300">{providerInfo.name} ({providerInfo.model})</span></>
              )}
            </p>
            {!providerInfo?.available && (
              <p className="text-red-400 text-sm">No hay ningun provider de IA disponible.</p>
            )}
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmDialog(null)} className="btn-secondary">
                Cancelar
              </button>
              <button
                onClick={startAiReclassify}
                disabled={!providerInfo?.available}
                className="btn-primary flex items-center gap-1.5"
              >
                <Sparkles className="w-4 h-4" />
                Iniciar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Batch actions */}
      {selected.size > 0 && (
        <div className="flex items-center gap-2 bg-gray-900 border border-gray-800 rounded-lg p-3">
          <span className="text-sm text-gray-300">{selected.size} seleccionadas</span>
          <button onClick={() => batchAction('trash')} className="btn-danger flex items-center gap-1 text-sm">
            <Trash2 className="w-3.5 h-3.5" /> Basura
          </button>
          <button onClick={() => batchAction('keep')} className="btn-success flex items-center gap-1 text-sm">
            <Check className="w-3.5 h-3.5" /> Mantener
          </button>
          <button onClick={() => batchAction('documents')} className="btn-secondary flex items-center gap-1 text-sm">
            <FileText className="w-3.5 h-3.5" /> Documento
          </button>
          <button onClick={selectAll} className="text-xs text-gray-500 hover:text-gray-300 ml-2">
            {selected.size === photos.length ? 'Deseleccionar todo' : 'Seleccionar todo'}
          </button>
        </div>
      )}

      {/* Photo grid */}
      {loading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="w-8 h-8 animate-spin text-purple-400" />
        </div>
      ) : (
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
          {photos.map((photo, idx) => (
            <PhotoCard
              key={photo.id}
              photo={photo}
              isSelected={selected.has(photo.id)}
              onSelect={() => toggleSelect(photo.id)}
              onOpen={() => setLightbox(idx)}
              onAction={(a) => reclassify(photo.id, a)}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="btn-secondary"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">{page} / {totalPages}</span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="btn-secondary"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Lightbox */}
      {lbPhoto && lightbox !== null && (
        <Lightbox
          photo={lbPhoto}
          onClose={() => setLightbox(null)}
          onPrev={lbPrev}
          onNext={lbNext}
          onAction={(a) => reclassify(lbPhoto.id, a)}
          hasPrev={lightbox > 0}
          hasNext={lightbox < photos.length - 1}
        />
      )}
    </div>
  );
}

// ── AI Progress Banner ──

function AiProgressBanner({
  progress,
  onCancel,
  onPause,
  onResume,
  onDismiss,
}: {
  progress: AiProgressState;
  onCancel: () => void;
  onPause: () => void;
  onResume: () => void;
  onDismiss: () => void;
}) {
  const pct = progress.total > 0 ? (progress.processed / progress.total) * 100 : 0;
  const isRunning = progress.status === 'running';
  const isPaused = progress.status === 'paused';
  const isActive = isRunning || isPaused;
  const isDone = progress.status === 'done';
  const isCancelled = progress.status === 'cancelled';
  const isError = progress.status === 'error';

  const borderColor = isDone
    ? 'border-green-500/30'
    : isError
      ? 'border-red-500/30'
      : isCancelled
        ? 'border-yellow-500/30'
        : isPaused
          ? 'border-orange-500/30'
          : 'border-purple-500/30';

  const bgColor = isDone
    ? 'bg-green-500/10'
    : isError
      ? 'bg-red-500/10'
      : isCancelled
        ? 'bg-yellow-500/10'
        : isPaused
          ? 'bg-orange-500/10'
          : 'bg-purple-500/10';

  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg p-4 space-y-3`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isRunning && <Loader2 className="w-4 h-4 animate-spin text-purple-400" />}
          {isPaused && <Pause className="w-4 h-4 text-orange-400" />}
          {isDone && <Check className="w-4 h-4 text-green-400" />}
          {isCancelled && <Ban className="w-4 h-4 text-yellow-400" />}
          {isError && <X className="w-4 h-4 text-red-400" />}
          <span className="font-medium text-sm">
            {isRunning && 'Clasificando con IA...'}
            {isPaused && 'Clasificacion IA pausada'}
            {isDone && 'Clasificacion IA completada'}
            {isCancelled && 'Clasificacion IA cancelada'}
            {isError && 'Error en clasificacion IA'}
          </span>
          <span className="text-xs text-gray-500">{progress.providerUsed}</span>
        </div>
        <div className="flex items-center gap-2">
          {isRunning && (
            <button onClick={onPause} className="btn-secondary text-xs flex items-center gap-1">
              <Pause className="w-3 h-3" /> Pausar
            </button>
          )}
          {isPaused && (
            <button onClick={onResume} className="btn-primary text-xs flex items-center gap-1">
              <Play className="w-3 h-3" /> Reanudar
            </button>
          )}
          {isActive && (
            <button onClick={onCancel} className="btn-danger text-xs flex items-center gap-1">
              <Ban className="w-3 h-3" /> Cancelar
            </button>
          )}
          {!isActive && (
            <button onClick={onDismiss} className="text-gray-500 hover:text-gray-300">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-800 rounded-full h-2">
        <div
          className={`h-2 rounded-full transition-all duration-300 ${
            isDone ? 'bg-green-500' : isError ? 'bg-red-500' : 'bg-purple-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Stats */}
      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-4">
          <span className="text-gray-400">{progress.processed}/{progress.total} fotos</span>
          <span className="text-green-400">{progress.kept} mantener</span>
          <span className="text-red-400">{progress.trashed} basura</span>
          <span className="text-blue-400">{progress.documents} docs</span>
          {progress.stillReview > 0 && <span className="text-yellow-400">{progress.stillReview} review</span>}
        </div>
        {isRunning && progress.currentFile && (
          <span className="text-xs text-gray-500 truncate max-w-[200px]">{progress.currentFile}</span>
        )}
      </div>

      {isError && progress.error && (
        <p className="text-xs text-red-400">{progress.error}</p>
      )}
    </div>
  );
}

// ── Photo Card ──

function PhotoCard({
  photo, isSelected, onSelect, onOpen, onAction,
}: {
  photo: ReviewPhoto;
  isSelected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onAction: (a: string) => void;
}) {
  const thumbUrl = photo.thumbnail_path ? api.thumbnailUrl(photo.thumbnail_path) : '';

  return (
    <div
      className={`group relative bg-gray-900 border rounded-lg overflow-hidden transition-all ${
        isSelected ? 'border-purple-500 ring-1 ring-purple-500/50' : 'border-gray-800 hover:border-gray-700'
      }`}
    >
      <button
        onClick={onSelect}
        className="absolute top-1.5 left-1.5 z-10 w-5 h-5 rounded border border-gray-600 bg-gray-900/80 flex items-center justify-center"
      >
        {isSelected && <Check className="w-3.5 h-3.5 text-purple-400" />}
      </button>

      <div className="aspect-square cursor-pointer relative" onClick={onOpen}>
        {thumbUrl ? (
          <img src={thumbUrl} alt={photo.filename} className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full bg-gray-800 flex items-center justify-center text-gray-600 text-xs">
            Sin preview
          </div>
        )}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-all flex items-center justify-center">
          <ZoomIn className="w-6 h-6 text-white opacity-0 group-hover:opacity-80 transition-opacity" />
        </div>
        {photo.media_type === 'video' && (
          <span className="absolute top-1.5 right-1.5 bg-black/70 text-white text-[9px] px-1.5 py-0.5 rounded font-medium">
            VIDEO{photo.duration ? ` ${Math.round(photo.duration)}s` : ''}
          </span>
        )}
      </div>

      <div className="p-1.5 space-y-1">
        <div className="text-[10px] text-gray-500 truncate">{photo.filename}</div>
        <div className="text-[10px] text-gray-600">
          {reasonLabel(photo.reason)} &middot; {(photo.confidence * 100).toFixed(0)}%
          {photo.media_type === 'video' && photo.size_bytes > 0 && (
            <> &middot; {(photo.size_bytes / 1024 / 1024).toFixed(1)}MB</>
          )}
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => onAction('trash')}
            className="flex-1 py-0.5 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 text-[10px]"
          >
            Basura
          </button>
          <button
            onClick={() => onAction('keep')}
            className="flex-1 py-0.5 rounded bg-green-500/10 text-green-400 hover:bg-green-500/20 text-[10px]"
          >
            Mantener
          </button>
          <button
            onClick={() => onAction('documents')}
            className="flex-1 py-0.5 rounded bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 text-[10px]"
          >
            Doc
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Lightbox ──

function Lightbox({
  photo, onClose, onPrev, onNext, onAction, hasPrev, hasNext,
}: {
  photo: ReviewPhoto;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onAction: (a: string) => void;
  hasPrev: boolean;
  hasNext: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 bg-black/95 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute top-4 right-4 text-gray-400 hover:text-white z-10">
          <X className="w-6 h-6" />
        </button>

        {hasPrev && (
          <button onClick={onPrev} className="absolute left-4 text-gray-400 hover:text-white">
            <ChevronLeft className="w-10 h-10" />
          </button>
        )}
        {hasNext && (
          <button onClick={onNext} className="absolute right-4 text-gray-400 hover:text-white">
            <ChevronRight className="w-10 h-10" />
          </button>
        )}

        <img
          src={api.fullImageUrl(photo.id)}
          alt={photo.filename}
          className="max-h-[80vh] max-w-[85vw] object-contain"
        />

        <div className="absolute bottom-0 inset-x-0 bg-gray-900/90 backdrop-blur p-4">
          <div className="max-w-2xl mx-auto flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-200">{photo.filename}</div>
              <div className="text-xs text-gray-500">
                {photo.media_type === 'video' ? (
                  <>{photo.duration ? `${Math.round(photo.duration)}s` : ''} &middot; {(photo.size_bytes / 1024 / 1024).toFixed(1)}MB</>
                ) : (
                  <>{photo.width}x{photo.height} &middot; {(photo.size_bytes / 1024).toFixed(0)}KB</>
                )}
                &middot; {reasonLabel(photo.reason)}
                &middot; Confianza: {(photo.confidence * 100).toFixed(0)}%
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={() => onAction('trash')} className="btn-danger flex items-center gap-1.5">
                <Trash2 className="w-4 h-4" /> Basura
              </button>
              <button onClick={() => onAction('keep')} className="btn-success flex items-center gap-1.5">
                <Check className="w-4 h-4" /> Mantener
              </button>
              <button onClick={() => onAction('documents')} className="btn-secondary flex items-center gap-1.5">
                <FileText className="w-4 h-4" /> Documento
              </button>
            </div>
          </div>
          <div className="text-center text-[10px] text-gray-600 mt-2">
            Atajos: ← → navegar &middot; D = basura &middot; K = mantener &middot; Esc = cerrar
          </div>
        </div>
      </div>
    </div>
  );
}
