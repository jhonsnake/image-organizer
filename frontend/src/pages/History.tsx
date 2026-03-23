import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, HardDrive, CheckCircle2, XCircle, Pause, Trash2, BarChart3, Sparkles } from 'lucide-react';
import { api } from '../lib/api';
import type { Job } from '../lib/api';

export default function HistoryPage() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);

  const loadJobs = () => api.listJobs(undefined, 50).then(setJobs).catch(() => {});

  useEffect(() => {
    loadJobs();
  }, []);

  const handleDelete = async (id: number) => {
    try {
      await api.deleteJob(id);
      setJobs((prev) => prev.filter((j) => j.id !== id));
    } catch (e) {
      console.error('Delete failed:', e);
    }
  };

  const handleClear = async () => {
    try {
      await api.clearJobs();
      loadJobs();
    } catch (e) {
      console.error('Clear failed:', e);
    }
  };

  const totalSaved = jobs.reduce((a, j) => a + j.space_saved_bytes, 0);
  const totalProcessed = jobs.reduce((a, j) => a + j.total_files, 0);
  const completedJobs = jobs.filter((j) => j.status === 'completed').length;
  const canClear = jobs.some((j) => j.status !== 'running' && j.status !== 'pending');

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Historial</h1>
        {canClear && (
          <button
            onClick={handleClear}
            className="btn-secondary flex items-center gap-1.5 text-red-400 hover:text-red-300 text-sm"
          >
            <Trash2 className="w-4 h-4" /> Limpiar historial
          </button>
        )}
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        <SummaryCard
          icon={CheckCircle2}
          label="Ejecuciones completadas"
          value={completedJobs.toString()}
          color="text-green-400"
        />
        <SummaryCard
          icon={HardDrive}
          label="Espacio total recuperado"
          value={formatBytes(totalSaved)}
          color="text-purple-400"
        />
        <SummaryCard
          icon={Clock}
          label="Fotos procesadas"
          value={totalProcessed.toLocaleString()}
          color="text-blue-400"
        />
      </div>

      {/* Jobs table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-500 text-xs">
              <th className="text-left p-3">ID</th>
              <th className="text-left p-3">Usuario</th>
              <th className="text-left p-3">Estado</th>
              <th className="text-right p-3">Archivos</th>
              <th className="text-right p-3">Basura</th>
              <th className="text-right p-3">Review</th>
              <th className="text-right p-3">Docs</th>
              <th className="text-right p-3">Espacio</th>
              <th className="text-right p-3">Fecha</th>
              <th className="text-right p-3">Duracion</th>
              <th className="text-right p-3"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const canDelete = job.status !== 'running' && job.status !== 'pending';
              return (
                <tr key={job.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="p-3 text-gray-400">#{job.id}</td>
                  <td className="p-3">{job.nas_user}</td>
                  <td className="p-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="p-3 text-right text-gray-400">{job.total_files.toLocaleString()}</td>
                  <td className="p-3 text-right text-red-400">{job.trash_count}</td>
                  <td className="p-3 text-right text-yellow-400">{job.review_count}</td>
                  <td className="p-3 text-right text-blue-400">{job.documents_count}</td>
                  <td className="p-3 text-right text-green-400">{formatBytes(job.space_saved_bytes)}</td>
                  <td className="p-3 text-right text-gray-500">{formatDate(job.created_at)}</td>
                  <td className="p-3 text-right text-gray-500">{formatDuration(job.started_at, job.completed_at)}</td>
                  <td className="p-3 text-right flex items-center justify-end gap-2">
                    {job.status === 'completed' && (
                      <>
                        <button
                          onClick={() => navigate(`/ai-summary/${job.id}`)}
                          className="text-gray-600 hover:text-purple-400 transition-colors"
                          title="Resumen IA"
                        >
                          <Sparkles className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => navigate(`/analysis/${job.id}`)}
                          className="text-gray-600 hover:text-purple-400 transition-colors"
                          title="Ver analisis"
                        >
                          <BarChart3 className="w-4 h-4" />
                        </button>
                      </>
                    )}
                    {canDelete && (
                      <button
                        onClick={() => handleDelete(job.id)}
                        className="text-gray-600 hover:text-red-400 transition-colors"
                        title="Eliminar"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {jobs.length === 0 && (
          <div className="text-center py-12 text-gray-600">No hay ejecuciones previas</div>
        )}
      </div>
    </div>
  );
}

function SummaryCard({
  icon: Icon, label, value, color,
}: {
  icon: typeof Clock; label: string; value: string; color: string;
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex items-center gap-3">
      <Icon className={`w-8 h-8 ${color}`} />
      <div>
        <div className={`text-xl font-bold ${color}`}>{value}</div>
        <div className="text-xs text-gray-500">{label}</div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { icon: typeof CheckCircle2; cls: string }> = {
    completed: { icon: CheckCircle2, cls: 'text-green-400' },
    failed: { icon: XCircle, cls: 'text-red-400' },
    paused: { icon: Pause, cls: 'text-yellow-400' },
    running: { icon: Clock, cls: 'text-purple-400' },
  };
  const s = map[status] || map.running;
  const Icon = s!.icon;
  return (
    <span className={`inline-flex items-center gap-1 ${s!.cls}`}>
      <Icon className="w-3.5 h-3.5" />
      <span className="text-xs">{status}</span>
    </span>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('es-ES', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return '-';
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const secs = Math.round((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}
