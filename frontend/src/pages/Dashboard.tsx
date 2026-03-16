import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import {
  Save, Play, ChevronRight, AlertCircle, CheckCircle2, Loader2,
  Monitor, Cloud, Settings2,
} from 'lucide-react';
import { api } from '../lib/api';
import type { AppConfig, NasUser, VisionProvider } from '../lib/api';

const defaults: Omit<AppConfig, 'id'> = {
  nas_user: '',
  source_dir: '',
  llm_url: '',
  llm_model: '',
  blur_threshold: 50,
  hash_threshold: 8,
  darkness_threshold: 15,
  brightness_threshold: 245,
  confidence_threshold: 0.7,
  max_image_size: 512,
};

export default function Dashboard() {
  const navigate = useNavigate();
  const [users, setUsers] = useState<NasUser[]>([]);
  const [config, setConfig] = useState<AppConfig>(defaults as AppConfig);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Providers state
  const [providers, setProviders] = useState<VisionProvider[]>([]);
  const [activeProvider, setActiveProvider] = useState<VisionProvider | null>(null);
  const [providerStatus, setProviderStatus] = useState<'checking' | 'online' | 'offline' | null>(null);

  useEffect(() => {
    api.getUsers().then(setUsers).catch(() => {});
    loadProviders();
  }, []);

  const loadProviders = async () => {
    try {
      const list = await api.listProviders();
      setProviders(list);
      const enabled = list.filter((p) => p.enabled !== false);
      if (enabled.length > 0) {
        setActiveProvider(enabled[0]);
        checkProviderStatus(enabled[0].id);
      }
    } catch {
      setProviders([]);
    }
  };

  const checkProviderStatus = async (id: number) => {
    setProviderStatus('checking');
    try {
      const result = await api.testProvider(id);
      setProviderStatus(result.available ? 'online' : 'offline');
    } catch {
      setProviderStatus('offline');
    }
  };

  useEffect(() => {
    if (!config.nas_user) return;
    api.getConfig(config.nas_user).then((cfg) => {
      if (cfg) setConfig(cfg);
      else {
        const user = users.find((u) => u.username === config.nas_user);
        setConfig((prev) => ({
          ...defaults,
          nas_user: prev.nas_user,
          source_dir: user?.photos_dir || '',
        }));
      }
    });
  }, [config.nas_user, users]);

  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const result = await api.saveConfig(config.nas_user, config);
      setConfig(result);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error guardando');
    }
    setSaving(false);
  };

  const handleStart = async () => {
    setStarting(true);
    setError('');
    try {
      await api.saveConfig(config.nas_user, config);
      await api.createJob({
        nas_user: config.nas_user,
        source_dir: config.source_dir,
        blur_threshold: config.blur_threshold,
        hash_threshold: config.hash_threshold,
        confidence_threshold: config.confidence_threshold,
      });
      navigate('/progress');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error iniciando');
    }
    setStarting(false);
  };

  const update = (key: keyof AppConfig, value: string | number) =>
    setConfig((p) => ({ ...p, [key]: value }));

  const enabledProviders = providers.filter((p) => p.enabled !== false);
  const canStart = config.nas_user && config.source_dir && enabledProviders.length > 0;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Configurar Limpieza</h1>

      {/* User selector */}
      <Section title="Usuario NAS">
        <select
          className="input"
          value={config.nas_user}
          onChange={(e) => update('nas_user', e.target.value)}
        >
          <option value="">Selecciona usuario...</option>
          {users.map((u) => (
            <option key={u.username} value={u.username}>{u.username}</option>
          ))}
        </select>
      </Section>

      {/* Source directory */}
      {config.nas_user && (
        <Section title="Carpeta de fotos">
          <input
            className="input"
            value={config.source_dir}
            onChange={(e) => update('source_dir', e.target.value)}
            placeholder="/data/homes/usuario/Photos"
          />
        </Section>
      )}

      {/* AI Provider section */}
      <Section title="Modelo de IA">
        {enabledProviders.length === 0 ? (
          <div className="text-center py-6 space-y-3">
            <div className="text-gray-500 text-sm">No hay providers configurados</div>
            <Link
              to="/providers"
              className="inline-flex items-center gap-2 btn-primary text-sm"
            >
              <Settings2 className="w-4 h-4" />
              Configurar providers
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Active provider card */}
            {activeProvider && (
              <div className="flex items-center gap-3 p-3 rounded-lg border border-gray-700 bg-gray-800/50">
                <div className="shrink-0">
                  {activeProvider.provider_type === 'openai-compatible' ? (
                    <Monitor className="w-5 h-5 text-purple-400" />
                  ) : (
                    <Cloud className="w-5 h-5 text-blue-400" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-200 truncate">{activeProvider.name}</div>
                  <div className="text-xs text-gray-500">
                    {activeProvider.model || activeProvider.provider_type}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {providerStatus === 'checking' && (
                    <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
                  )}
                  {providerStatus === 'online' && (
                    <span className="flex items-center gap-1 text-xs text-green-400">
                      <CheckCircle2 className="w-3.5 h-3.5" /> Online
                    </span>
                  )}
                  {providerStatus === 'offline' && (
                    <span className="flex items-center gap-1 text-xs text-red-400">
                      <AlertCircle className="w-3.5 h-3.5" /> Offline
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Fallback info */}
            {enabledProviders.length > 1 && (
              <div className="text-xs text-gray-500">
                +{enabledProviders.length - 1} provider{enabledProviders.length > 2 ? 's' : ''} de respaldo configurado{enabledProviders.length > 2 ? 's' : ''}
              </div>
            )}

            <Link
              to="/providers"
              className="inline-flex items-center gap-1 text-xs text-purple-400 hover:text-purple-300 transition-colors"
            >
              Cambiar provider <ChevronRight className="w-3 h-3" />
            </Link>
          </div>
        )}
      </Section>

      {/* Advanced settings */}
      <div>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronRight className={`w-4 h-4 transition-transform ${showAdvanced ? 'rotate-90' : ''}`} />
          Ajustes avanzados
        </button>
        {showAdvanced && (
          <div className="mt-3 grid grid-cols-2 gap-4">
            <Field label="Threshold blur" value={config.blur_threshold} onChange={(v) => update('blur_threshold', v)} />
            <Field label="Threshold hash" value={config.hash_threshold} onChange={(v) => update('hash_threshold', v)} />
            <Field label="Threshold oscuridad" value={config.darkness_threshold} onChange={(v) => update('darkness_threshold', v)} />
            <Field label="Threshold brillo" value={config.brightness_threshold} onChange={(v) => update('brightness_threshold', v)} />
            <Field label="Confianza minima" value={config.confidence_threshold} onChange={(v) => update('confidence_threshold', v)} step={0.05} />
            <Field label="Tamano imagen (px)" value={config.max_image_size} onChange={(v) => update('max_image_size', v)} />
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm text-red-400 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3 pt-2">
        <button onClick={handleSave} disabled={!config.nas_user || saving} className="btn-secondary flex items-center gap-2">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saved ? 'Guardado!' : 'Guardar config'}
        </button>
        <button onClick={handleStart} disabled={!canStart || starting} className="btn-primary flex items-center gap-2">
          {starting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Iniciar limpieza
        </button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-medium text-gray-300 mb-3">{title}</h2>
      {children}
    </div>
  );
}

function Field({
  label, value, onChange, step = 1,
}: {
  label: string; value: number; onChange: (v: number) => void; step?: number;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <input type="number" className="input" value={value} step={step} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}
