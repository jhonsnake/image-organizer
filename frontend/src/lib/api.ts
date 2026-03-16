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
    llm_url: string;
    llm_model: string;
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

  thumbnailUrl: (filename: string) => `/api/review/thumbnail/${filename}`,
  fullImageUrl: (photoId: number) => `/api/review/full/${photoId}`,
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
}
