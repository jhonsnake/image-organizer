import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Trash2, FileText, Eye, CheckCircle2, Sparkles, Loader2,
  AlertTriangle, ChevronUp,
} from 'lucide-react';
import { api } from '../lib/api';
import type { AiSummary, AiSummaryGroup } from '../lib/api';

const ACTION_BG: Record<string, string> = {
  trash: 'bg-red-500/10 border-red-500/30',
  documents: 'bg-blue-500/10 border-blue-500/30',
  review: 'bg-yellow-500/10 border-yellow-500/30',
  keep: 'bg-green-500/10 border-green-500/30',
};

const ACTION_LABELS: Record<string, string> = {
  trash: 'Basura',
  documents: 'Documentos',
  review: 'Pendiente',
  keep: 'Conservar',
};

export default function AiSummaryPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<AiSummary | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [groupStates, setGroupStates] = useState<Record<string, 'pending' | 'executing' | 'done' | 'error'>>({});
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ reason: string; action: string; label: string; count: number } | null>(null);
  const [approvingAll, setApprovingAll] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    api.getAiSummary(Number(jobId))
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, [jobId]);

  const handleAction = async (group: AiSummaryGroup, action: string) => {
    if (!data) return;
    setConfirmDialog(null);
    setGroupStates((s) => ({ ...s, [group.reason]: 'executing' }));

    try {
      // Reclassify photos to the chosen action
      if (action !== group.suggested_action || action === 'keep') {
        await api.batchByReason(data.job_id, group.reason, action);
      }

      // Execute file moves for trash/documents (moves files on disk)
      if (action === 'trash' || action === 'documents') {
        await api.executeGroup(data.job_id, group.reason);
      }

      setGroupStates((s) => ({ ...s, [group.reason]: 'done' }));
    } catch {
      setGroupStates((s) => ({ ...s, [group.reason]: 'error' }));
    }
  };

  const handleApproveAll = async () => {
    if (!data) return;
    setApprovingAll(true);

    const pending = data.groups.filter(
      (g) => !groupStates[g.reason] || groupStates[g.reason] === 'pending'
    );

    for (const group of pending) {
      if (group.suggested_action === 'keep') continue;
      await handleAction(group, group.suggested_action);
    }

    setApprovingAll(false);
  };

  const showConfirm = (group: AiSummaryGroup, action: string) => {
    const labels: Record<string, string> = {
      trash: `Mover ${group.count} archivos a la papelera`,
      documents: `Mover ${group.count} archivos a Documentos`,
      keep: `Conservar ${group.count} archivos`,
      review: `Enviar ${group.count} archivos a revisión manual`,
    };
    setConfirmDialog({ reason: group.reason, action, label: labels[action] || action, count: group.count });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-purple-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-20">
        <AlertTriangle className="w-8 h-8 text-red-400 mx-auto mb-2" />
        <p className="text-red-400">{error}</p>
      </div>
    );
  }

  if (!data) return null;

  const pendingGroups = data.groups.filter(
    (g) => !groupStates[g.reason] || groupStates[g.reason] === 'pending'
  );
  const doneGroups = data.groups.filter((g) => groupStates[g.reason] === 'done');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="text-gray-400 hover:text-gray-200">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Sparkles className="w-6 h-6 text-purple-400" />
            Resumen IA
          </h1>
          {data.summary_text && (
            <p className="text-sm text-gray-400 mt-1">{data.summary_text}</p>
          )}
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Categorías encontradas" value={data.groups.length} color="text-purple-400" />
        <StatCard label="Archivos clasificados" value={data.total_classified} color="text-blue-400" />
        <StatCard label="Acciones completadas" value={doneGroups.length} color="text-green-400" />
      </div>

      {/* Groups */}
      <div className="space-y-3">
        {data.groups.map((group) => {
          const state = groupStates[group.reason] || 'pending';
          const isExpanded = expandedGroup === group.reason;

          return (
            <div
              key={group.reason}
              className={`border rounded-lg overflow-hidden transition-all ${
                state === 'done'
                  ? 'bg-gray-900/50 border-gray-800 opacity-60'
                  : `bg-gray-900 border-gray-800`
              }`}
            >
              {/* Group header */}
              <div className="p-4">
                <div className="flex items-start gap-4">
                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-xs px-2 py-0.5 rounded-full border ${ACTION_BG[group.suggested_action]}`}>
                        {ACTION_LABELS[group.suggested_action]}
                      </span>
                      <h3 className="font-medium">{group.label}</h3>
                      <span className="text-xs text-gray-500">
                        {group.count} archivos &middot; {formatBytes(group.size_bytes)}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500">{group.description}</p>

                    {/* Sample thumbnails */}
                    {group.sample_photos.length > 0 && (
                      <div className="flex gap-2 mt-3">
                        {group.sample_photos.map((sample) => (
                          <div
                            key={sample.id}
                            className="w-16 h-16 rounded-md overflow-hidden bg-gray-800 flex-shrink-0"
                          >
                            {sample.thumbnail_path ? (
                              <img
                                src={api.thumbnailUrl(sample.thumbnail_path.split('/').pop() || '')}
                                alt={sample.filename}
                                className="w-full h-full object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <div className="w-full h-full flex items-center justify-center text-gray-600 text-xs">
                                Sin img
                              </div>
                            )}
                          </div>
                        ))}
                        {group.count > 5 && (
                          <button
                            onClick={() => setExpandedGroup(isExpanded ? null : group.reason)}
                            className="w-16 h-16 rounded-md bg-gray-800 flex items-center justify-center text-gray-400 hover:text-gray-200 flex-shrink-0"
                          >
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : (
                              <span className="text-xs">+{group.count - 5}</span>
                            )}
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {state === 'pending' && (
                      <>
                        {group.suggested_action === 'trash' && (
                          <>
                            <button
                              onClick={() => showConfirm(group, 'trash')}
                              className="px-3 py-1.5 rounded-md bg-red-500/20 text-red-300 hover:bg-red-500/30 text-sm flex items-center gap-1.5"
                            >
                              <Trash2 className="w-3.5 h-3.5" /> Borrar
                            </button>
                            <button
                              onClick={() => showConfirm(group, 'keep')}
                              className="px-3 py-1.5 rounded-md bg-green-500/20 text-green-300 hover:bg-green-500/30 text-sm"
                            >
                              Conservar
                            </button>
                          </>
                        )}
                        {group.suggested_action === 'documents' && (
                          <>
                            <button
                              onClick={() => showConfirm(group, 'documents')}
                              className="px-3 py-1.5 rounded-md bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 text-sm flex items-center gap-1.5"
                            >
                              <FileText className="w-3.5 h-3.5" /> Mover a Docs
                            </button>
                            <button
                              onClick={() => showConfirm(group, 'keep')}
                              className="px-3 py-1.5 rounded-md bg-green-500/20 text-green-300 hover:bg-green-500/30 text-sm"
                            >
                              Conservar
                            </button>
                          </>
                        )}
                        {group.suggested_action === 'review' && (
                          <>
                            <button
                              onClick={() => showConfirm(group, 'trash')}
                              className="px-3 py-1.5 rounded-md bg-red-500/20 text-red-300 hover:bg-red-500/30 text-sm flex items-center gap-1.5"
                            >
                              <Trash2 className="w-3.5 h-3.5" /> Borrar
                            </button>
                            <button
                              onClick={() => showConfirm(group, 'keep')}
                              className="px-3 py-1.5 rounded-md bg-green-500/20 text-green-300 hover:bg-green-500/30 text-sm"
                            >
                              Conservar
                            </button>
                          </>
                        )}
                        {group.suggested_action === 'keep' && (
                          <button
                            onClick={() => showConfirm(group, 'trash')}
                            className="px-3 py-1.5 rounded-md bg-red-500/20 text-red-300 hover:bg-red-500/30 text-sm flex items-center gap-1.5"
                          >
                            <Trash2 className="w-3.5 h-3.5" /> Borrar
                          </button>
                        )}
                        <button
                          onClick={() => showConfirm(group, 'review')}
                          className="px-3 py-1.5 rounded-md bg-yellow-500/20 text-yellow-300 hover:bg-yellow-500/30 text-sm flex items-center gap-1.5"
                          title="Enviar a revisión manual"
                        >
                          <Eye className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                    {state === 'executing' && (
                      <span className="flex items-center gap-1.5 text-purple-400 text-sm">
                        <Loader2 className="w-4 h-4 animate-spin" /> Procesando...
                      </span>
                    )}
                    {state === 'done' && (
                      <span className="flex items-center gap-1.5 text-green-400 text-sm">
                        <CheckCircle2 className="w-4 h-4" /> Completado
                      </span>
                    )}
                    {state === 'error' && (
                      <span className="flex items-center gap-1.5 text-red-400 text-sm">
                        <AlertTriangle className="w-4 h-4" /> Error
                      </span>
                    )}
                  </div>
                </div>

                {/* Confidence bar */}
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-xs text-gray-600">Confianza:</span>
                  <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden max-w-[200px]">
                    <div
                      className={`h-full rounded-full ${
                        group.avg_confidence > 0.8 ? 'bg-green-500' :
                        group.avg_confidence > 0.5 ? 'bg-yellow-500' : 'bg-red-500'
                      }`}
                      style={{ width: `${group.avg_confidence * 100}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-500">{Math.round(group.avg_confidence * 100)}%</span>
                  {group.total_moved > 0 && (
                    <span className="text-xs text-gray-600 ml-2">
                      ({group.total_moved} ya movidos)
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer actions */}
      {pendingGroups.length > 0 && (
        <div className="flex items-center justify-between border-t border-gray-800 pt-4">
          <p className="text-sm text-gray-500">
            {pendingGroups.length} categorías pendientes de acción
          </p>
          <div className="flex gap-3">
            <button
              onClick={() => navigate('/review')}
              className="px-4 py-2 rounded-md bg-gray-800 text-gray-300 hover:bg-gray-700 text-sm"
            >
              Ir a Review manual
            </button>
            <button
              onClick={handleApproveAll}
              disabled={approvingAll}
              className="px-4 py-2 rounded-md bg-purple-600 text-white hover:bg-purple-500 text-sm flex items-center gap-2 disabled:opacity-50"
            >
              {approvingAll ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Procesando...</>
              ) : (
                <><Sparkles className="w-4 h-4" /> Aprobar todo</>
              )}
            </button>
          </div>
        </div>
      )}

      {/* Confirmation Dialog */}
      {confirmDialog && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-medium mb-2">Confirmar acción</h3>
            <p className="text-gray-400 text-sm mb-4">{confirmDialog.label}</p>
            <p className="text-xs text-gray-500 mb-6">
              Esta acción afectará {confirmDialog.count} archivos.
              {confirmDialog.action === 'trash' && ' Los archivos se moverán a _cleanup/trash/.'}
              {confirmDialog.action === 'documents' && ' Los archivos se moverán a la carpeta Documentos.'}
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setConfirmDialog(null)}
                className="px-4 py-2 rounded-md bg-gray-800 text-gray-300 hover:bg-gray-700 text-sm"
              >
                Cancelar
              </button>
              <button
                onClick={() => {
                  const group = data.groups.find((g) => g.reason === confirmDialog.reason);
                  if (group) handleAction(group, confirmDialog.action);
                }}
                className={`px-4 py-2 rounded-md text-white text-sm ${
                  confirmDialog.action === 'trash' ? 'bg-red-600 hover:bg-red-500' :
                  confirmDialog.action === 'documents' ? 'bg-blue-600 hover:bg-blue-500' :
                  confirmDialog.action === 'keep' ? 'bg-green-600 hover:bg-green-500' :
                  'bg-yellow-600 hover:bg-yellow-500'
                }`}
              >
                Confirmar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex items-center gap-3">
      <div>
        <div className={`text-xl font-bold ${color}`}>{value}</div>
        <div className="text-xs text-gray-500">{label}</div>
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
