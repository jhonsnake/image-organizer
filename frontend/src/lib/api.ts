const BASE = '';

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ── Config ──

export interface NasUser {
  username: string;
  photos_dir: string;
  available: boolean;
}

export interface AppConfig {
  id?: number;
  nas_user: string;
  source_dir: string;
  llm_url: string;
  llm_model: string;
  blur_threshold: number;
  hash_threshold: number;
  darkness_threshold: number;
  brightness_threshold: number;
  confidence_threshold: number;
  max_image_size: number;
  active_provider_id?: number;
}

export interface LlmInfo {
  available: boolean;
  models: string[];
  url: string;
}

export interface DirEntry {
  name: string;
  path: string;
}

export const api = {
  getUsers: () => request<NasUser[]>('/api/config/users'),
  getConfig: (user: string) => request<AppConfig | null>(`/api/config/${encodeURIComponent(user)}`),
  saveConfig: (user: string, cfg: AppConfig) =>
    request<AppConfig>(`/api/config/${encodeURIComponent(user)}`, {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  getLlmModels: (url: string) =>
    request<LlmInfo>(`/api/config/llm/models?llm_url=${encodeURIComponent(url)}`),
  browseDirs: (path: string) =>
    request<{ current: string; directories: DirEntry[] }>(`/api/config/browse?path=${encodeURIComponent(path)}`),

  // ── Jobs ──
  createJob: (data: {
    nas_user: string;
    source_dir: string;
    blur_threshold: number;
    hash_threshold: number;
    confidence_threshold: number;
  }) => request<Job>('/api/jobs/', { method: 'POST', body: JSON.stringify(data) }),

  listJobs: (user?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (user) params.set('nas_user', user);
    return request<Job[]>(`/api/jobs/?${params}`);
  },

  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),
  getJobStats: (id: number) => request<JobStats>(`/api/jobs/${id}/stats`),
  pauseJob: (id: number) => request<{ status: string }>(`/api/jobs/${id}/pause`, { method: 'POST' }),
  resumeJob: (id: number) => request<{ status: string }>(`/api/jobs/${id}/resume`, { method: 'POST' }),
  stopJob: (id: number) => request<{ status: string }>(`/api/jobs/${id}/stop`, { method: 'POST' }),
  deleteJob: (id: number) => request<{ deleted: boolean }>(`/api/jobs/${id}`, { method: 'DELETE' }),
  clearJobs: () => request<{ deleted: number }>('/api/jobs/', { method: 'DELETE' }),

  // ── Review ──
  getReviewPhotos: (jobId: number, page = 1, pageSize = 50, minConf = 0, maxConf = 1) =>
    request<ReviewPhoto[]>(
      `/api/review/${jobId}/photos?page=${page}&page_size=${pageSize}&min_confidence=${minConf}&max_confidence=${maxConf}`,
    ),

  countReviewPhotos: (jobId: number) =>
    request<{ count: number }>(`/api/review/${jobId}/photos/count`),

  reclassifyPhoto: (photoId: number, action: string) =>
    request<ReviewPhoto>(`/api/review/photo/${photoId}`, {
      method: 'PUT',
      body: JSON.stringify({ action }),
    }),

  batchReclassify: (photoIds: number[], action: string) =>
    request<{ updated: number }>('/api/review/batch', {
      method: 'PUT',
      body: JSON.stringify({ photo_ids: photoIds, action }),
    }),

  aiReclassify: (jobId: number, photoIds?: number[], confidenceThreshold = 0.7) =>
    request<AiReclassifyStartResult>(`/api/review/${jobId}/reclassify-ai`, {
      method: 'POST',
      body: JSON.stringify({
        photo_ids: photoIds || null,
        confidence_threshold: confidenceThreshold,
      }),
    }),

  cancelAiReclassify: (taskId: string) =>
    request<{ ok: boolean }>(`/api/review/reclassify-ai/${taskId}/cancel`, { method: 'POST' }),

  pauseAiReclassify: (taskId: string) =>
    request<{ ok: boolean }>(`/api/review/reclassify-ai/${taskId}/pause`, { method: 'POST' }),

  resumeAiReclassify: (taskId: string) =>
    request<{ ok: boolean }>(`/api/review/reclassify-ai/${taskId}/resume`, { method: 'POST' }),

  getActiveAiTask: () =>
    request<AiActiveTask | null>('/api/review/reclassify-ai/active'),

  getProviderInfo: () =>
    request<AiProviderInfo>('/api/review/reclassify-ai/provider-info'),

  thumbnailUrl: (filename: string) => `/api/review/thumbnail/${filename}`,
  fullImageUrl: (photoId: number) => `/api/review/full/${photoId}`,

  // ── V2: Providers ──
  getProviderTypes: () => request<ProviderType[]>('/api/providers/types'),
  listProviders: () => request<VisionProvider[]>('/api/providers/'),
  createProvider: (data: ProviderInput) =>
    request<VisionProvider>('/api/providers/', { method: 'POST', body: JSON.stringify(data) }),
  updateProvider: (id: number, data: ProviderInput) =>
    request<VisionProvider>(`/api/providers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteProvider: (id: number) =>
    request<{ deleted: boolean }>(`/api/providers/${id}`, { method: 'DELETE' }),
  detectProviders: () =>
    request<{ providers: DetectedProvider[]; recommended: DetectedProvider | null }>('/api/providers/detect', { method: 'POST' }),
  testProvider: (id: number) =>
    request<{ available: boolean; models: string[]; provider_name: string }>(`/api/providers/${id}/test`, { method: 'POST' }),
  reorderProviders: (order: { id: number; priority: number }[]) =>
    request<{ updated: number }>('/api/providers/reorder', { method: 'PUT', body: JSON.stringify(order) }),
  toggleProvider: (id: number) =>
    request<{ id: number; enabled: boolean }>(`/api/providers/${id}/toggle`, { method: 'PATCH' }),
  getProviderModels: (id: number) =>
    request<{ models: string[] }>(`/api/providers/${id}/models`),

  // ── V2: Watcher ──
  getWatcherStatus: () => request<WatcherStatus>('/api/watcher/status'),
  startWatcher: (pollInterval = 30, autoClassify = true) =>
    request<WatcherStatus>('/api/watcher/start', {
      method: 'POST',
      body: JSON.stringify({ poll_interval: pollInterval, auto_classify: autoClassify }),
    }),
  stopWatcher: () => request<{ status: string }>('/api/watcher/stop', { method: 'POST' }),
  getWatcherEvents: (limit = 50) =>
    request<WatcherEvent[]>(`/api/watcher/events?limit=${limit}`),
  getWatcherStats: () => request<{ total: number; processed: number; pending: number }>('/api/watcher/events/stats'),

  // ── Analysis ──
  getSpaceBreakdown: (jobId: number) =>
    request<SpaceBreakdown>(`/api/analysis/${jobId}/space-breakdown`),

  getAiSummary: (jobId: number) =>
    request<AiSummary>(`/api/analysis/${jobId}/ai-summary`),

  // ── AI Summary actions ──
  batchByReason: (jobId: number, reason: string, newAction: string) =>
    request<{ updated: number }>('/api/review/batch-by-reason', {
      method: 'PUT',
      body: JSON.stringify({ job_id: jobId, reason, new_action: newAction }),
    }),

  executeGroup: (jobId: number, reason: string) =>
    request<{ moved: number; errors: number; size_freed: number }>('/api/review/execute-group', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, reason }),
    }),
};

// ── Types ──

export interface Job {
  id: number;
  nas_user: string;
  source_dir: string;
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed';
  current_stage: string;
  total_files: number;
  processed_files: number;
  kept_count: number;
  trash_count: number;
  review_count: number;
  documents_count: number;
  space_saved_bytes: number;
  stage_progress: number;
  stage_total: number;
  llm_model: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface JobStats {
  total: number;
  by_action: Record<string, { count: number; size_bytes: number }>;
  by_stage: Record<string, number>;
  by_reason: Record<string, number>;
}

export interface ReviewPhoto {
  id: number;
  job_id: number;
  path: string;
  filename: string;
  extension: string | null;
  media_type: 'image' | 'video';
  size_bytes: number;
  width: number;
  height: number;
  action: string;
  reason: string;
  confidence: number;
  stage_decided: number;
  vision_label: string | null;
  vision_confidence: number;
  blur_score: number;
  brightness: number;
  duplicate_group: string | null;
  thumbnail_path: string | null;
  duration: number | null;
  video_codec: string | null;
}

// ── AI Reclassify ──

export interface AiReclassifyStartResult {
  task_id: string;
  total: number;
  provider_used: string;
}

export interface AiReclassifyProgress {
  task_id: string;
  job_id: number;
  processed: number;
  total: number;
  current_file: string;
  photo_id: number;
  result: string;
  classified: number;
  kept: number;
  trashed: number;
  documents: number;
  still_review: number;
}

export interface AiProviderInfo {
  name: string | null;
  model: string | null;
  available: boolean;
}

export interface AiActiveTask {
  task_id: string;
  status: 'running' | 'paused';
  job_id: number;
  total: number;
  processed: number;
  current_file: string;
  provider_used: string;
  classified: number;
  kept: number;
  trashed: number;
  documents: number;
  still_review: number;
}

// ── Space Analysis ──

export interface SpaceBreakdown {
  job_id: number;
  total_files: number;
  total_size_bytes: number;
  recoverable_bytes: number;
  by_reason: Record<string, { count: number; size_bytes: number; action: string }>;
  by_action: Record<string, { count: number; size_bytes: number }>;
  by_media_type: Record<string, { count: number; size_bytes: number }>;
  recommendations: SpaceRecommendation[];
  top_large_files: LargeFile[];
}

export interface SpaceRecommendation {
  reason: string;
  count: number;
  size_bytes: number;
  moved: number;
}

export interface LargeFile {
  id: number;
  filename: string;
  size_bytes: number;
  reason: string;
  media_type: string;
  thumbnail_path: string | null;
}

// ── AI Summary ──

export interface AiSummarySample {
  id: number;
  filename: string;
  thumbnail_path: string | null;
  confidence: number;
  size_bytes: number;
}

export interface AiSummaryGroup {
  reason: string;
  label: string;
  description: string;
  suggested_action: 'keep' | 'trash' | 'review' | 'documents';
  count: number;
  total_moved: number;
  size_bytes: number;
  avg_confidence: number;
  sample_photos: AiSummarySample[];
}

export interface AiSummary {
  job_id: number;
  total_classified: number;
  groups: AiSummaryGroup[];
  summary_text: string;
}

// ── Reason labels in Spanish ──

export const REASON_LABELS: Record<string, string> = {
  screenshot_filename: 'Screenshot (nombre)',
  screenshot_dims_no_exif: 'Screenshot (dimensiones)',
  messaging_image: 'Imagen de mensajeria',
  tiny_image: 'Imagen muy pequena',
  small_file: 'Archivo muy pequeno',
  duplicate: 'Duplicado',
  blurry: 'Borrosa',
  too_dark: 'Muy oscura',
  overexposed: 'Sobreexpuesta',
  vision_screenshot: 'Screenshot (IA)',
  vision_meme: 'Meme',
  vision_document: 'Documento',
  vision_invoice: 'Factura/Recibo',
  vision_accidental: 'Foto accidental',
  vision_ambiguous: 'Ambigua',
  vision_photo: 'Foto personal',
  whatsapp_sticker: 'Sticker WhatsApp',
  whatsapp_status: 'Estado WhatsApp',
  unclassified: 'Sin clasificar',
  legitimate: 'Legitima',
  manual_keep: 'Mantener (manual)',
  manual_trash: 'Basura (manual)',
  manual_documents: 'Documento (manual)',
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] || reason.replace(/_/g, ' ');
}

// ── V2 Types ──

export interface ProviderType {
  type: string;
  label: string;
  requires_url: boolean;
  requires_key: boolean;
}

export interface ProviderInput {
  name: string;
  provider_type: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  priority?: number;
  enabled?: boolean;
}

export interface VisionProvider extends ProviderInput {
  id: number;
  available?: boolean;
  models?: string[];
}

export interface DetectedProvider {
  id: number;
  type: string;
  name: string;
  available: boolean;
  models: string[];
  provider_name: string;
  priority: number;
}

export interface WatcherStatus {
  running: boolean;
  known_files: number;
  watched_dirs: number;
  poll_interval?: number;
  status?: string;
}

export interface WatcherEvent {
  id: number;
  filepath: string;
  filename: string;
  nas_user: string;
  action: string | null;
  reason: string | null;
  confidence: number;
  provider_used: string | null;
  processed: boolean;
  moved: boolean;
  detected_at: string;
  processed_at: string | null;
}
