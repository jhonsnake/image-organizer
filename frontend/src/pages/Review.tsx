import { useState, useEffect, useCallback } from 'react';
import {
  Trash2, Check, FileText, X, ChevronLeft, ChevronRight,
  ZoomIn, Loader2, Filter, Sparkles,
} from 'lucide-react';
import { api, reasonLabel } from '../lib/api';
import type { Job, ReviewPhoto, AiReclassifyResult } from '../lib/api';

export default function Review() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [photos, setPhotos] = useState<ReviewPhoto[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [lightbox, setLightbox] = useState<number | null>(null); // photo index
  const [minConf] = useState(0);
  const [maxConf, setMaxConf] = useState(1);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiResult, setAiResult] = useState<AiReclassifyResult | null>(null);

  const pageSize = 48;

  // Load jobs that have review photos
  useEffect(() => {
    api.listJobs(undefined, 20).then((j) => {
      const withData = j.filter((x) => x.review_count > 0 || x.status === 'completed' || x.status === 'running');
      setJobs(withData);
      if (withData.length && !selectedJobId) setSelectedJobId(withData[0].id);
    });
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

  const totalPages = Math.ceil(totalCount / pageSize);

  // ── Actions ──

  const reclassify = async (photoId: number, action: string) => {
    await api.reclassifyPhoto(photoId, action);
    setPhotos((prev) => prev.filter((p) => p.id !== photoId));
    setTotalCount((c) => c - 1);
    setSelected((s) => { s.delete(photoId); return new Set(s); });
    // If in lightbox, advance to next
    if (lightbox !== null) {
      const idx = photos.findIndex((p) => p.id === photoId);
      if (idx >= 0 && idx < photos.length - 1) {
        // Will auto-adjust since photo is removed
      } else {
        setLightbox(null);
      }
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
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === photos.length) setSelected(new Set());
    else setSelected(new Set(photos.map((p) => p.id)));
  };

  const aiReclassify = async (photoIds?: number[]) => {
    if (!selectedJobId) return;
    setAiLoading(true);
    setAiResult(null);
    try {
      const result = await api.aiReclassify(selectedJobId, photoIds);
      setAiResult(result);
      // Reload photos to reflect changes
      await loadPhotos();
      setSelected(new Set());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Error desconocido';
      alert(`Error: ${msg}`);
    }
    setAiLoading(false);
  };

  // ── Lightbox navigation ──
  const lbPhoto = lightbox !== null ? photos[lightbox] : null;
  const lbPrev = () => setLightbox((i) => (i !== null && i > 0 ? i - 1 : i));
  const lbNext = () => setLightbox((i) => (i !== null && i < photos.length - 1 ? i + 1 : i));

  // Keyboard nav
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

          {/* AI Reclassify */}
          <button
            onClick={() => aiReclassify(selected.size > 0 ? [...selected] : undefined)}
            disabled={aiLoading}
            className="btn-primary flex items-center gap-1.5 text-sm"
            title={selected.size > 0 ? `Clasificar ${selected.size} seleccionadas con IA` : 'Clasificar todas con IA'}
          >
            {aiLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            {aiLoading ? 'Clasificando...' : selected.size > 0 ? `IA (${selected.size})` : 'Clasificar con IA'}
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

      {/* AI result banner */}
      {aiResult && (
        <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 flex items-center justify-between">
          <div className="text-sm text-purple-200">
            <Sparkles className="w-4 h-4 inline mr-1.5" />
            IA clasifico {aiResult.classified}/{aiResult.total} fotos con <span className="font-medium">{aiResult.provider_used}</span>
            {' — '}
            <span className="text-green-400">{aiResult.kept} mantener</span>
            {', '}
            <span className="text-red-400">{aiResult.trashed} basura</span>
            {', '}
            <span className="text-blue-400">{aiResult.documents} docs</span>
            {aiResult.still_review > 0 && <>, <span className="text-yellow-400">{aiResult.still_review} aun en review</span></>}
          </div>
          <button onClick={() => setAiResult(null)} className="text-gray-500 hover:text-gray-300">
            <X className="w-4 h-4" />
          </button>
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
      {/* Checkbox */}
      <button
        onClick={onSelect}
        className="absolute top-1.5 left-1.5 z-10 w-5 h-5 rounded border border-gray-600 bg-gray-900/80 flex items-center justify-center"
      >
        {isSelected && <Check className="w-3.5 h-3.5 text-purple-400" />}
      </button>

      {/* Image */}
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

      {/* Info + actions */}
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
        {/* Close */}
        <button onClick={onClose} className="absolute top-4 right-4 text-gray-400 hover:text-white z-10">
          <X className="w-6 h-6" />
        </button>

        {/* Nav arrows */}
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

        {/* Image */}
        <img
          src={api.fullImageUrl(photo.id)}
          alt={photo.filename}
          className="max-h-[80vh] max-w-[85vw] object-contain"
        />

        {/* Bottom bar */}
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
