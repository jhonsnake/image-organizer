import { useState, useEffect } from 'react';
import {
  Plus, Trash2, CheckCircle2, XCircle, Loader2, RefreshCw,
  Zap, ArrowUpDown,
} from 'lucide-react';
import { api } from '../lib/api';
import type { VisionProvider, ProviderType, ProviderInput, DetectedProvider } from '../lib/api';

export default function Providers() {
  const [providers, setProviders] = useState<VisionProvider[]>([]);
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [detected, setDetected] = useState<DetectedProvider[] | null>(null);
  const [recommended, setRecommended] = useState<DetectedProvider | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [showAdd, setShowAdd] = useState(false);

  useEffect(() => {
    api.listProviders().then(setProviders);
    api.getProviderTypes().then(setProviderTypes);
  }, []);

  const detect = async () => {
    setDetecting(true);
    try {
      const result = await api.detectProviders();
      setDetected(result.providers);
      setRecommended(result.recommended);
    } catch { /* ignore */ }
    setDetecting(false);
  };

  const deleteProvider = async (id: number) => {
    await api.deleteProvider(id);
    setProviders((p) => p.filter((x) => x.id !== id));
  };

  const refreshProviders = () => api.listProviders().then(setProviders);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Proveedores de Vision</h1>
        <div className="flex gap-2">
          <button onClick={detect} disabled={detecting} className="btn-secondary flex items-center gap-1.5">
            {detecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            Detectar disponibles
          </button>
          <button onClick={() => setShowAdd(!showAdd)} className="btn-primary flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Agregar
          </button>
        </div>
      </div>

      {/* Detection results */}
      {detected && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <ArrowUpDown className="w-4 h-4" /> Estado de proveedores
          </h3>
          {detected.map((d) => (
            <div key={d.id} className="flex items-center gap-3 text-sm">
              {d.available
                ? <CheckCircle2 className="w-4 h-4 text-green-400" />
                : <XCircle className="w-4 h-4 text-red-400" />
              }
              <span className={d.available ? 'text-gray-200' : 'text-gray-500'}>
                {d.name}
              </span>
              <span className="text-gray-600 text-xs">({d.type})</span>
              {d.available && d.models.length > 0 && (
                <span className="text-gray-500 text-xs">{d.models.length} modelos</span>
              )}
              {recommended?.id === d.id && (
                <span className="text-xs bg-green-500/20 text-green-400 px-2 py-0.5 rounded-full">
                  Recomendado
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add form */}
      {showAdd && (
        <AddProviderForm
          types={providerTypes}
          onSave={() => { setShowAdd(false); refreshProviders(); }}
          onCancel={() => setShowAdd(false)}
        />
      )}

      {/* Provider list */}
      <div className="space-y-3">
        {providers.map((p) => (
          <ProviderCard
            key={p.id}
            provider={p}
            onDelete={() => deleteProvider(p.id)}
            onRefresh={refreshProviders}
          />
        ))}
        {providers.length === 0 && !showAdd && (
          <div className="text-center py-12 text-gray-600">
            No hay proveedores configurados. Agrega uno para empezar.
          </div>
        )}
      </div>

      {/* Info box */}
      <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4 text-sm text-gray-500 space-y-2">
        <p><strong className="text-gray-400">Prioridad:</strong> El proveedor con menor numero se usa primero. Si no esta disponible, se usa el siguiente.</p>
        <p><strong className="text-gray-400">Fallback:</strong> Configura un proveedor local (LM Studio) con prioridad baja y uno cloud con prioridad alta como respaldo.</p>
      </div>
    </div>
  );
}

function ProviderCard({
  provider, onDelete,
}: {
  provider: VisionProvider;
  onDelete: () => void;
  onRefresh: () => void;
}) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ available: boolean; models: string[] } | null>(null);

  const test = async () => {
    setTesting(true);
    try {
      const result = await api.testProvider(provider.id);
      setTestResult(result);
    } catch {
      setTestResult({ available: false, models: [] });
    }
    setTesting(false);
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xs bg-gray-800 text-gray-400 px-2 py-1 rounded">
            #{provider.priority}
          </span>
          <div>
            <div className="font-medium text-gray-200">{provider.name}</div>
            <div className="text-xs text-gray-500">
              {provider.provider_type}
              {provider.base_url && ` · ${provider.base_url}`}
              {provider.model && ` · ${provider.model}`}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {testResult && (
            <span className={`text-xs ${testResult.available ? 'text-green-400' : 'text-red-400'}`}>
              {testResult.available ? 'OK' : 'No disponible'}
            </span>
          )}
          <button onClick={test} disabled={testing} className="btn-secondary text-xs">
            {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          </button>
          <button onClick={onDelete} className="text-red-400/50 hover:text-red-400 transition-colors">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

function AddProviderForm({
  types, onSave, onCancel,
}: {
  types: ProviderType[];
  onSave: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<ProviderInput>({
    name: '',
    provider_type: 'openai-compatible',
    base_url: 'http://100.127.43.94:1234/v1',
    model: '',
    api_key: '',
    priority: 10,
    enabled: true,
  });
  const [saving, setSaving] = useState(false);

  const selectedType = types.find((t) => t.type === form.provider_type);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.createProvider(form);
      onSave();
    } catch { /* ignore */ }
    setSaving(false);
  };

  const update = (key: keyof ProviderInput, value: string | number | boolean) =>
    setForm((p) => ({ ...p, [key]: value }));

  return (
    <div className="bg-gray-900 border border-purple-500/30 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-medium text-gray-300">Nuevo proveedor</h3>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="label">Nombre</label>
          <input className="input" value={form.name} onChange={(e) => update('name', e.target.value)} placeholder="Mi LM Studio" />
        </div>
        <div>
          <label className="label">Tipo</label>
          <select className="input" value={form.provider_type} onChange={(e) => update('provider_type', e.target.value)}>
            {types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
          </select>
        </div>
        {selectedType?.requires_url && (
          <div className="col-span-2">
            <label className="label">URL base</label>
            <input className="input" value={form.base_url} onChange={(e) => update('base_url', e.target.value)} placeholder="http://localhost:1234/v1" />
          </div>
        )}
        {selectedType?.requires_key && (
          <div className="col-span-2">
            <label className="label">API Key</label>
            <input className="input" type="password" value={form.api_key} onChange={(e) => update('api_key', e.target.value)} placeholder="sk-..." />
          </div>
        )}
        <div>
          <label className="label">Modelo</label>
          <input className="input" value={form.model} onChange={(e) => update('model', e.target.value)} placeholder="qwen3-vl-8b-instruct" />
        </div>
        <div>
          <label className="label">Prioridad (menor = preferido)</label>
          <input className="input" type="number" value={form.priority} onChange={(e) => update('priority', Number(e.target.value))} />
        </div>
      </div>

      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="btn-secondary">Cancelar</button>
        <button onClick={handleSave} disabled={!form.name || saving} className="btn-primary flex items-center gap-1.5">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          Guardar
        </button>
      </div>
    </div>
  );
}
