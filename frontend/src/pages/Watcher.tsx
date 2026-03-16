import { useState, useEffect } from 'react';
import { Eye, EyeOff, Loader2, RefreshCw, Activity } from 'lucide-react';
import { api } from '../lib/api';
import type { WatcherStatus, WatcherEvent } from '../lib/api';

export default function Watcher() {
  const [status, setStatus] = useState<WatcherStatus | null>(null);
  const [events, setEvents] = useState<WatcherEvent[]>([]);
  const [stats, setStats] = useState<{ total: number; processed: number; pending: number } | null>(null);
  const [pollInterval, setPollInterval] = useState(30);
  const [autoClassify, setAutoClassify] = useState(true);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    try {
      const [s, e, st] = await Promise.all([
        api.getWatcherStatus(),
        api.getWatcherEvents(50),
        api.getWatcherStats(),
      ]);
      setStatus(s);
      setEvents(e);
      setStats(st);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  const start = async () => {
    setLoading(true);
    try {
      const s = await api.startWatcher(pollInterval, autoClassify);
      setStatus(s);
    } catch { /* ignore */ }
    setLoading(false);
  };

  const stop = async () => {
    setLoading(true);
    try {
      await api.stopWatcher();
      setStatus({ running: false, known_files: 0, watched_dirs: 0 });
    } catch { /* ignore */ }
    setLoading(false);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Monitoreo en tiempo real</h1>
          <p className="text-sm text-gray-500 mt-1">V2 — Clasifica fotos nuevas automaticamente</p>
        </div>
        <button onClick={refresh} className="btn-secondary">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Status + controls */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-3 h-3 rounded-full ${status?.running ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
            <span className="font-medium">
              {status?.running ? 'Monitoreando' : 'Detenido'}
            </span>
            {status?.running && (
              <span className="text-sm text-gray-500">
                {status.known_files?.toLocaleString()} archivos conocidos · {status.watched_dirs} carpetas
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!status?.running ? (
              <button onClick={start} disabled={loading} className="btn-primary flex items-center gap-1.5">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
                Iniciar
              </button>
            ) : (
              <button onClick={stop} disabled={loading} className="btn-danger flex items-center gap-1.5">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <EyeOff className="w-4 h-4" />}
                Detener
              </button>
            )}
          </div>
        </div>

        {/* Settings */}
        {!status?.running && (
          <div className="grid grid-cols-2 gap-4 pt-2 border-t border-gray-800">
            <div>
              <label className="label">Intervalo de polling (segundos)</label>
              <input
                type="number"
                className="input"
                value={pollInterval}
                min={10}
                max={300}
                onChange={(e) => setPollInterval(Number(e.target.value))}
              />
            </div>
            <div className="flex items-center gap-2 pt-5">
              <input
                type="checkbox"
                id="autoClassify"
                checked={autoClassify}
                onChange={(e) => setAutoClassify(e.target.checked)}
                className="accent-purple-500"
              />
              <label htmlFor="autoClassify" className="text-sm text-gray-300">
                Auto-clasificar con IA
              </label>
            </div>
          </div>
        )}
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-3 gap-3">
          <StatCard label="Total detectados" value={stats.total} color="text-purple-400" />
          <StatCard label="Procesados" value={stats.processed} color="text-green-400" />
          <StatCard label="Pendientes" value={stats.pending} color="text-yellow-400" />
        </div>
      )}

      {/* Events log */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center gap-2">
          <Activity className="w-4 h-4 text-purple-400" />
          <h3 className="text-sm font-medium text-gray-300">Eventos recientes</h3>
        </div>
        {events.length > 0 ? (
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="border-b border-gray-800 text-gray-500 text-xs">
                  <th className="text-left p-3">Archivo</th>
                  <th className="text-left p-3">Usuario</th>
                  <th className="text-left p-3">Accion</th>
                  <th className="text-left p-3">Razon</th>
                  <th className="text-left p-3">Provider</th>
                  <th className="text-right p-3">Confianza</th>
                  <th className="text-right p-3">Detectado</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="p-3 truncate max-w-[200px]" title={e.filename}>{e.filename}</td>
                    <td className="p-3 text-gray-400">{e.nas_user}</td>
                    <td className="p-3">
                      <ActionBadge action={e.action} />
                    </td>
                    <td className="p-3 text-xs text-gray-500">{e.reason?.replace(/_/g, ' ') || '-'}</td>
                    <td className="p-3 text-xs text-gray-500">{e.provider_used || '-'}</td>
                    <td className="p-3 text-right text-gray-400">
                      {e.confidence > 0 ? `${(e.confidence * 100).toFixed(0)}%` : '-'}
                    </td>
                    <td className="p-3 text-right text-xs text-gray-500">
                      {new Date(e.detected_at).toLocaleTimeString('es-ES')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-12 text-gray-600">
            No hay eventos. {status?.running ? 'Esperando nuevas fotos...' : 'Inicia el monitoreo para detectar cambios.'}
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
      <div className={`text-2xl font-bold ${color}`}>{value.toLocaleString()}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-gray-600">-</span>;
  const styles: Record<string, string> = {
    keep: 'bg-green-500/20 text-green-400',
    trash: 'bg-red-500/20 text-red-400',
    review: 'bg-yellow-500/20 text-yellow-400',
    documents: 'bg-blue-500/20 text-blue-400',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[action] || 'bg-gray-500/20 text-gray-400'}`}>
      {action}
    </span>
  );
}
