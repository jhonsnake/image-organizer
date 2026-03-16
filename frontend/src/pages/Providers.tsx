import { useState, useEffect } from 'react';
import {
  Plus, Trash2, CheckCircle2, XCircle, Loader2, RefreshCw,
  ChevronUp, ChevronDown, Monitor, Cloud, ToggleLeft, ToggleRight,
} from 'lucide-react';
import { api } from '../lib/api';
import type { VisionProvider, ProviderType, ProviderInput } from '../lib/api';

type AddStep = 'choose' | 'local' | 'cloud';

export default function Providers() {
  const [providers, setProviders] = useState<VisionProvider[]>([]);
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [addStep, setAddStep] = useState<AddStep | null>(null);

  useEffect(() => {
    loadProviders();
    api.getProviderTypes().then(setProviderTypes);
  }, []);

  const loadProviders = () => api.listProviders().then(setProviders);

  const deleteProvider = async (id: number) => {
    await api.deleteProvider(id);
    setProviders((p) => p.filter((x) => x.id !== id));
  };

  const toggleProvider = async (id: number) => {
    const result = await api.toggleProvider(id);
    setProviders((prev) =>
      prev.map((p) => (p.id === id ? { ...p, enabled: result.enabled } : p))
    );
  };

  const moveProvider = async (index: number, direction: 'up' | 'down') => {
    const swapIndex = direction === 'up' ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= providers.length) return;

    const newList = [...providers];
    [newList[index], newList[swapIndex]] = [newList[swapIndex], newList[index]];

    // Assign priorities based on position
    const order = newList.map((p, i) => ({ id: p.id, priority: (i + 1) * 10 }));
    setProviders(newList.map((p, i) => ({ ...p, priority: (i + 1) * 10 })));

    try {
      await api.reorderProviders(order);
    } catch {
      loadProviders(); // revert on error
    }
  };

  const handleSaved = () => {
    setAddStep(null);
    loadProviders();
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Proveedores de Vision</h1>
        <button
          onClick={() => setAddStep(addStep ? null : 'choose')}
          className="btn-primary flex items-center gap-1.5"
        >
          <Plus className="w-4 h-4" /> Agregar provider
        </button>
      </div>

      {/* Add flow */}
      {addStep === 'choose' && (
        <div className="bg-gray-900 border border-purple-500/30 rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-medium text-gray-300">Tipo de provider</h3>
          <div className="flex gap-3">
            <button
              onClick={() => setAddStep('local')}
              className="flex-1 flex flex-col items-center gap-2 p-4 rounded-lg border border-gray-700 hover:border-purple-500/50 hover:bg-purple-500/5 transition-all"
            >
              <Monitor className="w-6 h-6 text-purple-400" />
              <span className="font-medium text-gray-200">Local</span>
              <span className="text-xs text-gray-500 text-center">LM Studio, Ollama, vLLM</span>
            </button>
            <button
              onClick={() => setAddStep('cloud')}
              className="flex-1 flex flex-col items-center gap-2 p-4 rounded-lg border border-gray-700 hover:border-blue-500/50 hover:bg-blue-500/5 transition-all"
            >
              <Cloud className="w-6 h-6 text-blue-400" />
              <span className="font-medium text-gray-200">Cloud</span>
              <span className="text-xs text-gray-500 text-center">Anthropic, Gemini, OpenAI</span>
            </button>
          </div>
          <button onClick={() => setAddStep(null)} className="text-xs text-gray-500 hover:text-gray-300">
            Cancelar
          </button>
        </div>
      )}

      {addStep === 'local' && (
        <AddLocalForm
          onSave={handleSaved}
          onCancel={() => setAddStep(null)}
        />
      )}

      {addStep === 'cloud' && (
        <AddCloudForm
          types={providerTypes.filter((t) => t.type !== 'openai-compatible')}
          onSave={handleSaved}
          onCancel={() => setAddStep(null)}
        />
      )}

      {/* Provider list */}
      <div className="space-y-2">
        {providers.map((p, index) => (
          <ProviderRow
            key={p.id}
            provider={p}
            index={index}
            total={providers.length}
            onDelete={() => deleteProvider(p.id)}
            onToggle={() => toggleProvider(p.id)}
            onMove={(dir) => moveProvider(index, dir)}
          />
        ))}
        {providers.length === 0 && !addStep && (
          <div className="text-center py-12 text-gray-600">
            No hay proveedores configurados. Agrega uno para empezar.
          </div>
        )}
      </div>

      {/* Info */}
      {providers.length > 1 && (
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4 text-sm text-gray-500">
          <strong className="text-gray-400">Fallback:</strong> Los providers se prueban en orden de arriba a abajo.
          El primero que responda se usa para clasificar.
        </div>
      )}
    </div>
  );
}

function ProviderRow({
  provider, index, total, onDelete, onToggle, onMove,
}: {
  provider: VisionProvider;
  index: number;
  total: number;
  onDelete: () => void;
  onToggle: () => void;
  onMove: (dir: 'up' | 'down') => void;
}) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ available: boolean } | null>(null);

  const test = async () => {
    setTesting(true);
    try {
      const result = await api.testProvider(provider.id);
      setTestResult(result);
    } catch {
      setTestResult({ available: false });
    }
    setTesting(false);
  };

  const isLocal = provider.provider_type === 'openai-compatible';
  const isEnabled = provider.enabled !== false;

  return (
    <div className={`bg-gray-900 border rounded-lg p-3 flex items-center gap-3 transition-opacity ${
      isEnabled ? 'border-gray-800' : 'border-gray-800/50 opacity-50'
    }`}>
      {/* Reorder arrows */}
      <div className="flex flex-col gap-0.5">
        <button
          onClick={() => onMove('up')}
          disabled={index === 0}
          className="text-gray-600 hover:text-gray-300 disabled:opacity-20 disabled:hover:text-gray-600 transition-colors"
        >
          <ChevronUp className="w-4 h-4" />
        </button>
        <button
          onClick={() => onMove('down')}
          disabled={index === total - 1}
          className="text-gray-600 hover:text-gray-300 disabled:opacity-20 disabled:hover:text-gray-600 transition-colors"
        >
          <ChevronDown className="w-4 h-4" />
        </button>
      </div>

      {/* Icon */}
      {isLocal ? (
        <Monitor className="w-4 h-4 text-purple-400 shrink-0" />
      ) : (
        <Cloud className="w-4 h-4 text-blue-400 shrink-0" />
      )}

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="font-medium text-gray-200 truncate">{provider.name}</div>
        <div className="text-xs text-gray-500 truncate">
          {provider.provider_type}
          {provider.model && ` · ${provider.model}`}
        </div>
      </div>

      {/* Status */}
      {testResult && (
        <span className="shrink-0">
          {testResult.available ? (
            <CheckCircle2 className="w-4 h-4 text-green-400" />
          ) : (
            <XCircle className="w-4 h-4 text-red-400" />
          )}
        </span>
      )}

      {/* Actions */}
      <button onClick={test} disabled={testing} className="btn-secondary text-xs p-1.5" title="Probar">
        {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
      </button>

      <button onClick={onToggle} className="text-gray-400 hover:text-gray-200 transition-colors" title={isEnabled ? 'Desactivar' : 'Activar'}>
        {isEnabled ? (
          <ToggleRight className="w-5 h-5 text-green-400" />
        ) : (
          <ToggleLeft className="w-5 h-5 text-gray-600" />
        )}
      </button>

      <button onClick={onDelete} className="text-red-400/50 hover:text-red-400 transition-colors" title="Eliminar">
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
  );
}

function AddLocalForm({ onSave, onCancel }: { onSave: () => void; onCancel: () => void }) {
  const [name, setName] = useState('');
  const [url, setUrl] = useState('http://100.127.43.94:1234/v1');
  const [model, setModel] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [saving, setSaving] = useState(false);

  const detectModels = async () => {
    setDetecting(true);
    try {
      const info = await api.getLlmModels(url);
      setModels(info.models);
      if (info.models.length > 0 && !model) {
        setModel(info.models[0]);
      }
    } catch {
      setModels([]);
    }
    setDetecting(false);
  };

  useEffect(() => {
    if (url) {
      const t = setTimeout(detectModels, 500);
      return () => clearTimeout(t);
    }
  }, [url]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.createProvider({
        name: name || 'Local LLM',
        provider_type: 'openai-compatible',
        base_url: url,
        model,
        priority: 10,
        enabled: true,
      });
      onSave();
    } catch { /* ignore */ }
    setSaving(false);
  };

  return (
    <div className="bg-gray-900 border border-purple-500/30 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Monitor className="w-4 h-4 text-purple-400" /> Nuevo provider local
      </h3>

      <div className="space-y-3">
        <div>
          <label className="label">Nombre</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Mi LM Studio" />
        </div>
        <div>
          <label className="label">URL base</label>
          <div className="flex gap-2">
            <input className="input flex-1" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:1234/v1" />
            <button onClick={detectModels} disabled={detecting} className="btn-secondary">
              {detecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            </button>
          </div>
        </div>
        <div>
          <label className="label">Modelo</label>
          {models.length > 0 ? (
            <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <input className="input" value={model} onChange={(e) => setModel(e.target.value)} placeholder="qwen3-vl-8b-instruct" />
          )}
        </div>
      </div>

      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="btn-secondary">Cancelar</button>
        <button onClick={handleSave} disabled={!url || saving} className="btn-primary flex items-center gap-1.5">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          Guardar
        </button>
      </div>
    </div>
  );
}

function AddCloudForm({
  types, onSave, onCancel,
}: {
  types: ProviderType[];
  onSave: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<ProviderInput>({
    name: '',
    provider_type: types[0]?.type || 'anthropic',
    api_key: '',
    model: '',
    priority: 20,
    enabled: true,
  });
  const [saving, setSaving] = useState(false);

  const update = (key: keyof ProviderInput, value: string | number | boolean) =>
    setForm((p) => ({ ...p, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.createProvider(form);
      onSave();
    } catch { /* ignore */ }
    setSaving(false);
  };

  return (
    <div className="bg-gray-900 border border-blue-500/30 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Cloud className="w-4 h-4 text-blue-400" /> Nuevo provider cloud
      </h3>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="label">Nombre</label>
          <input className="input" value={form.name} onChange={(e) => update('name', e.target.value)} placeholder="Mi Claude API" />
        </div>
        <div>
          <label className="label">Tipo</label>
          <select className="input" value={form.provider_type} onChange={(e) => update('provider_type', e.target.value)}>
            {types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
          </select>
        </div>
        <div className="col-span-2">
          <label className="label">API Key</label>
          <input className="input" type="password" value={form.api_key} onChange={(e) => update('api_key', e.target.value)} placeholder="sk-..." />
        </div>
        <div className="col-span-2">
          <label className="label">Modelo</label>
          <input className="input" value={form.model} onChange={(e) => update('model', e.target.value)} placeholder="claude-sonnet-4-6" />
        </div>
      </div>

      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="btn-secondary">Cancelar</button>
        <button onClick={handleSave} disabled={!form.name || !form.api_key || saving} className="btn-primary flex items-center gap-1.5">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          Guardar
        </button>
      </div>
    </div>
  );
}
