import { useState, useEffect, useMemo } from 'react';
import { Pause, Play, Square, Loader2, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { api } from '../lib/api';
import type { Job, JobStats } from '../lib/api';
import type { WsMessage } from '../hooks/useWebSocket';

const STAGES = ['scanning', 'metadata', 'dedup', 'quality', 'vision', 'executing'] as const;

const STAGE_LABELS: Record<string, string> = {
  scanning: 'Escaneo',
  metadata: 'Metadata',
  dedup: 'Duplicados',
  quality: 'Calidad',
  vision: 'IA',
  executing: 'Mover',
  done: 'Completado',
};

const STAGE_LABELS_LONG: Record<string, string> = {
  scanning: 'Escaneando archivos',
  metadata: 'Analizando metadata',
  dedup: 'Buscando duplicados',
  quality: 'Analizando calidad',
  vision: 'Clasificacion IA',
  executing: 'Moviendo archivos',
  done: 'Completado',
};

const PIE_COLORS = {
  keep: '#22c55e',
  trash: '#ef4444',
  review: '#f59e0b',
  documents: '#3b82f6',
};

interface Counts {
  keep: number;
  trash: number;
  review: number;
  documents: number;
}

interface Props {
  latestMsg: WsMessage | null;
  messages: WsMessage[];
}

export default function Progress({ latestMsg, messages }: Props) {
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [stats, setStats] = useState<JobStats | null>(null);
  const [progress, setProgress] = useState({ stage: '', current: 0, total: 0, message: '' });
  const [liveCounts, setLiveCounts] = useState<Counts | null>(null);

  // Load active/recent jobs and hydrate progress from persisted data
  useEffect(() => {
    api.listJobs(undefined, 5).then((j) => {
      const running = j.find((x) => x.status === 'running' || x.status === 'paused');
      const job = running || j[0];
      if (job) {
        setActiveJob(job);
        // Hydrate progress bar from persisted DB values
        if (job.status === 'running' || job.status === 'paused') {
          setProgress({
            stage: job.current_stage,
            current: job.stage_progress || 0,
            total: job.stage_total || 0,
            message: '',
          });
          // Hydrate live counts from job counters
          setLiveCounts({
            keep: job.kept_count,
            trash: job.trash_count,
            review: job.review_count,
            documents: job.documents_count,
          });
        }
      }
    });
  }, []);

  // Poll stats for active job
  useEffect(() => {
    if (!activeJob) return;
    const load = () => api.getJobStats(activeJob.id).then(setStats).catch(() => {});
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [activeJob?.id]);

  // Refresh job on status changes
  useEffect(() => {
    if (!activeJob) return;
    const interval = setInterval(() => {
      api.getJob(activeJob.id).then(setActiveJob).catch(() => {});
    }, 3000);
    return () => clearInterval(interval);
  }, [activeJob?.id]);

  // Handle WebSocket messages
  useEffect(() => {
    if (!latestMsg || !activeJob || latestMsg.job_id !== activeJob.id) return;

    if (latestMsg.event === 'stage') {
      setProgress((p) => ({
        ...p,
        stage: latestMsg.stage as string,
        message: latestMsg.message as string,
        current: 0,
        total: 0,
      }));
    } else if (latestMsg.event === 'progress') {
      setProgress((p) => ({
        ...p,
        stage: latestMsg.stage as string,
        current: latestMsg.current as number,
        total: latestMsg.total as number,
      }));
      // Update live counts from WS
      if (latestMsg.counts) {
        const c = latestMsg.counts as Record<string, number>;
        setLiveCounts({
          keep: c.keep || 0,
          trash: c.trash || 0,
          review: c.review || 0,
          documents: c.documents || 0,
        });
      }
    } else if (latestMsg.event === 'stage_complete') {
      if (latestMsg.counts) {
        const c = latestMsg.counts as Record<string, number>;
        setLiveCounts({
          keep: c.keep || 0,
          trash: c.trash || 0,
          review: c.review || 0,
          documents: c.documents || 0,
        });
      }
      api.getJobStats(activeJob.id).then(setStats);
      api.getJob(activeJob.id).then(setActiveJob);
    } else if (latestMsg.event === 'completed') {
      setLiveCounts(null);
      api.getJobStats(activeJob.id).then(setStats);
      api.getJob(activeJob.id).then(setActiveJob);
    }
  }, [latestMsg, activeJob?.id]);

  // Send browser notification on completion
  useEffect(() => {
    if (latestMsg?.event === 'completed') {
      if (Notification.permission === 'granted') {
        new Notification('NAS Photo Cleaner', { body: 'Limpieza completada!' });
      }
    }
  }, [latestMsg]);

  // Request notification permission
  useEffect(() => {
    if (Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  // Use live counts if available, otherwise fall back to job data
  const displayCounts: Counts = liveCounts ?? {
    keep: activeJob?.kept_count ?? 0,
    trash: activeJob?.trash_count ?? 0,
    review: activeJob?.review_count ?? 0,
    documents: activeJob?.documents_count ?? 0,
  };

  const pieData = useMemo(() => {
    if (!stats) return [];
    return Object.entries(stats.by_action)
      .filter(([, v]) => v.count > 0)
      .map(([key, v]) => ({ name: key, value: v.count }));
  }, [stats]);

  const barData = useMemo(() => {
    if (!stats) return [];
    return Object.entries(stats.by_reason)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([name, value]) => ({ name: name.replace(/_/g, ' '), value }));
  }, [stats]);

  const progressPct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;

  const handlePauseResume = async () => {
    if (!activeJob) return;
    try {
      if (activeJob.status === 'running') {
        await api.pauseJob(activeJob.id);
      } else {
        await api.resumeJob(activeJob.id);
      }
      const updated = await api.getJob(activeJob.id);
      setActiveJob(updated);
    } catch (e) {
      console.error('Pause/Resume failed:', e);
    }
  };

  const handleStop = async () => {
    if (!activeJob) return;
    try {
      await api.stopJob(activeJob.id);
      const updated = await api.getJob(activeJob.id);
      setActiveJob(updated);
    } catch (e) {
      console.error('Stop failed:', e);
    }
  };

  if (!activeJob) {
    return (
      <div className="text-center py-20 text-gray-500">
        <p>No hay jobs activos. Inicia una limpieza desde Config.</p>
      </div>
    );
  }

  const isActive = activeJob.status === 'running' || activeJob.status === 'paused';
  const wasInterrupted = activeJob.status === 'paused' && activeJob.error_message?.includes('Interrumpido');

  return (
    <div className="space-y-6">
      {/* Interrupted banner */}
      {wasInterrupted && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4 flex items-center justify-between">
          <div className="flex items-center gap-2 text-yellow-300">
            <AlertTriangle className="w-5 h-5" />
            <span>Este job fue interrumpido por un reinicio del servidor.</span>
          </div>
          <button onClick={handlePauseResume} className="btn-secondary flex items-center gap-1.5">
            <Play className="w-4 h-4" /> Reanudar
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">
            {activeJob.status === 'completed' ? 'Limpieza completada' : 'Progreso'}
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            {activeJob.nas_user} &middot; {activeJob.total_files.toLocaleString()} archivos
            {activeJob.llm_model && ` \u00b7 ${activeJob.llm_model}`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={activeJob.status} />
          {isActive && (
            <>
              <button onClick={handlePauseResume} className="btn-secondary flex items-center gap-1.5">
                {activeJob.status === 'running' ? (
                  <><Pause className="w-4 h-4" /> Pausar</>
                ) : (
                  <><Play className="w-4 h-4" /> Reanudar</>
                )}
              </button>
              <button onClick={handleStop} className="btn-secondary flex items-center gap-1.5 text-red-400 hover:text-red-300">
                <Square className="w-4 h-4" /> Detener
              </button>
            </>
          )}
        </div>
      </div>

      {/* Progress bar */}
      {isActive && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
          <div className="flex justify-between text-sm">
            <span className="text-gray-300">
              {STAGE_LABELS_LONG[progress.stage] || progress.message || 'Iniciando...'}
            </span>
            <span className="text-gray-500">
              {progress.current.toLocaleString()} / {progress.total.toLocaleString()} ({progressPct}%)
            </span>
          </div>
          <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-purple-500 rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>

          {/* Stage indicators with labels */}
          <div className="flex gap-1">
            {STAGES.map((s) => {
              const currentIdx = STAGES.indexOf(progress.stage as typeof STAGES[number]);
              const thisIdx = STAGES.indexOf(s);
              const done = thisIdx < currentIdx;
              const active = thisIdx === currentIdx;
              return (
                <div key={s} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className={`w-full h-1.5 rounded-full ${
                      done ? 'bg-green-500' : active ? 'bg-purple-500 animate-pulse' : 'bg-gray-800'
                    }`}
                  />
                  <span className={`text-[10px] leading-none ${
                    active ? 'text-purple-400 font-medium' : done ? 'text-green-500/70' : 'text-gray-600'
                  }`}>
                    {STAGE_LABELS[s]}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Stats cards — live-updating */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Mantener" value={displayCounts.keep} color="text-green-400" />
        <StatCard label="Basura" value={displayCounts.trash} color="text-red-400" />
        <StatCard label="Review" value={displayCounts.review} color="text-yellow-400" />
        <StatCard label="Documentos" value={displayCounts.documents} color="text-blue-400" />
      </div>

      {/* Space saved */}
      {activeJob.space_saved_bytes > 0 && (
        <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-4 text-center">
          <div className="text-2xl font-bold text-green-400">
            {(activeJob.space_saved_bytes / 1024 / 1024).toFixed(1)} MB
          </div>
          <div className="text-sm text-green-400/70">espacio recuperado</div>
        </div>
      )}

      {/* Charts */}
      {stats && pieData.length > 0 && (
        <div className="grid md:grid-cols-2 gap-4">
          {/* Pie chart */}
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <h3 className="text-sm font-medium text-gray-300 mb-3">Distribucion</h3>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={({ name, value }) => `${name}: ${value}`}
                >
                  {pieData.map((entry) => (
                    <Cell
                      key={entry.name}
                      fill={PIE_COLORS[entry.name as keyof typeof PIE_COLORS] || '#6b7280'}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                  labelStyle={{ color: '#d1d5db' }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          {/* Bar chart */}
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <h3 className="text-sm font-medium text-gray-300 mb-3">Razones</h3>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={barData} layout="vertical">
                <XAxis type="number" stroke="#6b7280" fontSize={11} />
                <YAxis dataKey="name" type="category" width={120} stroke="#6b7280" fontSize={11} />
                <Tooltip
                  contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                />
                <Bar dataKey="value" fill="#a855f7" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Recent log */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-300 mb-3">Log en vivo</h3>
        <div className="h-40 overflow-y-auto font-mono text-xs text-gray-500 space-y-0.5">
          {messages
            .filter((m) => m.job_id === activeJob.id)
            .slice(-30)
            .map((m, i) => (
              <div key={i}>
                <span className="text-purple-400">[{m.event}]</span>{' '}
                {String(m.message || JSON.stringify(m).slice(0, 100))}
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
      <div className={`text-2xl font-bold tabular-nums transition-all duration-300 ${color}`}>
        {value.toLocaleString()}
      </div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, { icon: typeof Loader2; cls: string }> = {
    running: { icon: Loader2, cls: 'bg-purple-500/20 text-purple-300' },
    paused: { icon: Pause, cls: 'bg-yellow-500/20 text-yellow-300' },
    completed: { icon: CheckCircle2, cls: 'bg-green-500/20 text-green-300' },
    failed: { icon: XCircle, cls: 'bg-red-500/20 text-red-300' },
    pending: { icon: AlertTriangle, cls: 'bg-gray-500/20 text-gray-300' },
  };
  const s = styles[status] || styles.pending;
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${s.cls}`}>
      <Icon className={`w-3.5 h-3.5 ${status === 'running' ? 'animate-spin' : ''}`} />
      {status}
    </span>
  );
}
