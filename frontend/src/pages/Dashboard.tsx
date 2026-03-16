import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Save, Play, RefreshCw, ChevronRight, AlertCircle, CheckCircle2, Loader2,
  Monitor, Cloud, Zap, XCircle,
} from 'lucide-react';
import { api } from '../lib/api';
import type { AppConfig, NasUser, LlmInfo, DetectedProvider } from '../lib/api';

type VisionMode = 'direct' | 'providers';

const defaults: Omit<AppConfig, 'id'> = {
  nas_user: '',
  source_dir: '',
  llm_url: 'http://100.127.43.94:1234/v1',
  llm_model: 'qwen3-vl-8b-instruct',
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
  const [llmInfo, setLlmInfo] = useState<LlmInfo | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // V2: vision mode
  const [visionMode, setVisionMode] = useState<VisionMode>('direct');
  const [detectedProviders, setDetectedProviders] = useState<DetectedProvider[] | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [recommended, setRecommended] = useState<DetectedProvider | null>(null);

  useEffect(() => {
    api.getUsers().then(setUsers).catch(() => {});
  }, []);

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

  // Check direct LLM
  const checkLlm = async () => {
    setLlmLoading(true);
    setLlmInfo(null);
    try {
      const info = await api.getLlmModels(config.llm_url);
      setLlmInfo(info);
      if (info.models.length && !info.models.includes(config.llm_model)) {
        setConfig((p) => ({ ...p, llm_model: info.models[0] }));
      }
    } catch {
      setLlmInfo({ available: false, models: [], url: config.llm_url });
    }
    setLlmLoading(false);
  };

  useEffect(() => {
    if (visionMode === 'direct' && config.llm_url) {
      const t = setTimeout(checkLlm, 500);
      return () => clearTimeout(t);
    }
  }, [config.llm_url, visionMode]);

  // V2: detect providers
  const detectProviders = async () => {
    setDetecting(true);
    try {
      const result = await api.detectProviders();
      setDetectedProviders(result.providers);
      setRecommended(result.recommended);
    } catch {
      setDetectedProviders([]);
    }
    setDetecting(false);
  };

  useEffect(() => {
    if (visionMode === 'providers') {
      detectProviders();
    }
  }, [visionMode]);

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
        llm_url: config.llm_url,
        llm_model: config.llm_model,
        use_providers: visionMode === 'providers',
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

  const anyProviderAvailable = detectedProviders?.some((p) => p.available) ?? false;
  const canStart = config.nas_user && config.source_dir &&
    (visionMode === 'direct' || anyProviderAvailable);

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

      {/* Vision mode selector */}
      <Section title="Modelo de IA">
        <div className="space-y-4">
          {/* Mode toggle */}
          <div className="flex gap-2">
            <button
              onClick={() => setVisionMode('direct')}
              className={`flex-1 flex items-center gap-2 p-3 rounded-lg border text-sm transition-all ${
                visionMode === 'direct'
                  ? 'border-purple-500 bg-purple-500/10 text-purple-300'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              <Monitor className="w-4 h-4" />
              <div className="text-left">
                <div className="font-medium">Conexion directa</div>
                <div className="text-xs opacity-70">URL de un servidor local (LM Studio, Ollama...)</div>
              </div>
            </button>
            <button
              onClick={() => setVisionMode('providers')}
              className={`flex-1 flex items-center gap-2 p-3 rounded-lg border text-sm transition-all ${
                visionMode === 'providers'
                  ? 'border-purple-500 bg-purple-500/10 text-purple-300'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              <Cloud className="w-4 h-4" />
              <div className="text-left">
                <div className="font-medium">Providers con fallback</div>
                <div className="text-xs opacity-70">Local + cloud, usa el mejor disponible</div>
              </div>
            </button>
          </div>

          {/* Direct mode */}
          {visionMode === 'direct' && (
            <div className="space-y-3">
              <div>
                <label className="label">URL del servidor LLM</label>
                <div className="flex gap-2">
                  <input
                    className="input flex-1"
                    value={config.llm_url}
                    onChange={(e) => update('llm_url', e.target.value)}
                    placeholder="http://100.127.43.94:1234/v1"
                  />
                  <button onClick={checkLlm} className="btn-secondary" disabled={llmLoading}>
                    {llmLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                  </button>
                </div>
                {llmInfo && (
                  <div className={`flex items-center gap-1.5 mt-1.5 text-xs ${llmInfo.available ? 'text-green-400' : 'text-red-400'}`}>
                    {llmInfo.available ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                    {llmInfo.available ? `Conectado - ${llmInfo.models.length} modelos` : 'No disponible'}
                  </div>
                )}
              </div>
              <div>
                <label className="label">Modelo</label>
                {llmInfo?.models.length ? (
                  <select className="input" value={config.llm_model} onChange={(e) => update('llm_model', e.target.value)}>
                    {llmInfo.models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                ) : (
                  <input className="input" value={config.llm_model} onChange={(e) => update('llm_model', e.target.value)} />
                )}
              </div>
            </div>
          )}

          {/* Providers mode */}
          {visionMode === 'providers' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">
                  Se usara el provider de mayor prioridad que este disponible
                </span>
                <button onClick={detectProviders} disabled={detecting} className="btn-secondary text-xs flex items-center gap-1">
                  {detecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
                  Verificar
                </button>
              </div>

              {detectedProviders === null && !detecting && (
                <div className="text-center py-6 text-gray-600 text-sm">
                  Verificando providers...
                </div>
              )}

              {detectedProviders?.length === 0 && (
                <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3 text-sm text-yellow-400 flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  No hay providers configurados. Ve a la pagina de Providers para agregar uno.
                </div>
              )}

              {detectedProviders && detectedProviders.length > 0 && (
                <div className="space-y-2">
                  {detectedProviders.map((p) => (
                    <div
                      key={p.id}
                      className={`flex items-center gap-3 p-2.5 rounded-lg border text-sm ${
                        p.available ? 'border-green-500/30 bg-green-500/5' : 'border-gray-800 bg-gray-900/50 opacity-50'
                      }`}
                    >
                      {p.available
                        ? <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
                        : <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                      }
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-gray-200 truncate">{p.name}</div>
                        <div className="text-xs text-gray-500">{p.type} · Prioridad #{p.priority}</div>
                      </div>
                      {p.available && p.models.length > 0 && (
                        <span className="text-xs text-gray-500">{p.models.length} modelos</span>
                      )}
                      {recommended?.id === p.id && (
                        <span className="text-[10px] bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded-full shrink-0">
                          Se usara este
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
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
            <Field label="Tamaño imagen (px)" value={config.max_image_size} onChange={(v) => update('max_image_size', v)} />
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
