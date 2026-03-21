import { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Trash2, HardDrive, Film, Image } from 'lucide-react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis } from 'recharts';
import { api, reasonLabel } from '../lib/api';
import type { SpaceBreakdown } from '../lib/api';

const ACTION_COLORS: Record<string, string> = {
  trash: '#ef4444',
  keep: '#22c55e',
  review: '#f59e0b',
  documents: '#3b82f6',
};

const REASON_COLORS = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308',
  '#84cc16', '#22c55e', '#14b8a6', '#06b6d4',
  '#3b82f6', '#8b5cf6', '#a855f7', '#ec4899',
];

export default function SpaceAnalysis() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<SpaceBreakdown | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!jobId) return;
    api.getSpaceBreakdown(Number(jobId))
      .then(setData)
      .catch((e) => setError(e.message));
  }, [jobId]);

  const actionPieData = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.by_action)
      .filter(([, v]) => v.count > 0)
      .map(([key, v]) => ({ name: key, value: v.size_bytes }));
  }, [data]);

  const reasonBarData = useMemo(() => {
    if (!data) return [];
    return data.recommendations
      .slice(0, 10)
      .map((r) => ({
        name: reasonLabel(r.reason),
        value: r.size_bytes,
        count: r.count,
      }));
  }, [data]);

  if (error) {
    return (
      <div className="text-center py-20 text-red-400">
        <p>Error: {error}</p>
        <button onClick={() => navigate('/history')} className="btn-secondary mt-4">Volver</button>
      </div>
    );
  }

  if (!data) {
    return <div className="text-center py-20 text-gray-500">Cargando analisis...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={() => navigate('/history')} className="text-gray-400 hover:text-white">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">Analisis de espacio</h1>
          <p className="text-sm text-gray-400">
            Job #{jobId} &middot; {data.total_files.toLocaleString()} archivos &middot; {formatBytes(data.total_size_bytes)} total
          </p>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard
          icon={Trash2}
          label="Espacio recuperable"
          value={formatBytes(data.recoverable_bytes)}
          color="text-red-400"
        />
        <SummaryCard
          icon={HardDrive}
          label="Espacio total"
          value={formatBytes(data.total_size_bytes)}
          color="text-gray-300"
        />
        <SummaryCard
          icon={Image}
          label="Imagenes"
          value={`${data.by_media_type.image?.count ?? 0}`}
          color="text-blue-400"
        />
        <SummaryCard
          icon={Film}
          label="Videos"
          value={`${data.by_media_type.video?.count ?? 0}`}
          color="text-purple-400"
        />
      </div>

      {/* Charts */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* Donut chart: space by action */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-300 mb-3">Espacio por categoria</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={actionPieData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={90}
                label={({ name, value }) => `${name}: ${formatBytes(value)}`}
              >
                {actionPieData.map((entry) => (
                  <Cell key={entry.name} fill={ACTION_COLORS[entry.name] || '#6b7280'} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                formatter={(value) => formatBytes(Number(value))}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Bar chart: space by reason */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-300 mb-3">Basura por razon</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={reasonBarData} layout="vertical">
              <XAxis type="number" stroke="#6b7280" fontSize={11} tickFormatter={(v) => formatBytes(v)} />
              <YAxis dataKey="name" type="category" width={130} stroke="#6b7280" fontSize={11} />
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                formatter={(value) => formatBytes(Number(value))}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {reasonBarData.map((_, i) => (
                  <Cell key={i} fill={REASON_COLORS[i % REASON_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Recommendations */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-300 mb-3">Recomendaciones</h3>
        <div className="space-y-2">
          {data.recommendations.map((rec) => (
            <div key={rec.reason} className="flex items-center justify-between p-3 bg-gray-800/50 rounded-lg">
              <div>
                <span className="text-sm text-gray-200">
                  Borra {rec.count} {rec.count === 1 ? 'archivo' : 'archivos'} de{' '}
                  <span className="text-purple-300 font-medium">{reasonLabel(rec.reason)}</span>
                </span>
                <span className="text-sm text-gray-500 ml-2">
                  ({rec.moved}/{rec.count} ya movidos)
                </span>
              </div>
              <span className="text-sm font-bold text-green-400">{formatBytes(rec.size_bytes)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Top 20 largest files */}
      {data.top_large_files.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-300 mb-3">Top archivos mas grandes (basura)</h3>
          <div className="space-y-1">
            {data.top_large_files.map((f) => (
              <div key={f.id} className="flex items-center justify-between p-2 text-sm hover:bg-gray-800/30 rounded">
                <div className="flex items-center gap-2 min-w-0">
                  {f.media_type === 'video' ? (
                    <Film className="w-4 h-4 text-purple-400 shrink-0" />
                  ) : (
                    <Image className="w-4 h-4 text-blue-400 shrink-0" />
                  )}
                  <span className="text-gray-300 truncate">{f.filename}</span>
                  <span className="text-gray-600 text-xs shrink-0">{reasonLabel(f.reason)}</span>
                </div>
                <span className="text-gray-400 font-mono text-xs shrink-0 ml-2">{formatBytes(f.size_bytes)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryCard({
  icon: Icon, label, value, color,
}: {
  icon: typeof HardDrive; label: string; value: string; color: string;
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

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
