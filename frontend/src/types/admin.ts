/** Dashboard counters from GET /api/stats */
export interface DashboardStats {
  total_tokens: number
  active_tokens: number
  total_images: number
  total_videos: number
  total_errors: number
  total_metadata: number
  today_images: number
  today_videos: number
  today_errors: number
  today_metadata: number
  metadata_errors?: number
  today_metadata_errors?: number
}

/** Row from GET /api/tokens */
export interface TokenRow {
  id: number
  st?: string | null
  at?: string | null
  token?: string | null
  email?: string | null
  remark?: string | null
  is_active: boolean
  at_expires?: string | null
  credits?: number | null
  user_paygate_tier?: string | null
  current_project_id?: string | null
  current_project_name?: string | null
  captcha_proxy_url?: string
  image_enabled: boolean
  video_enabled: boolean
  image_concurrency?: number | null
  video_concurrency?: number | null
  image_count?: number
  video_count?: number
  error_count?: number
  extension_route_key?: string | null
  /** When false, Flow generation uses server HTTP; extension still used for captcha if method is extension. */
  use_extension_for_generation?: boolean | number | null
}

export interface CaptchaWorkerKeyRow {
  id: number
  key_prefix: string
  label?: string | null
  key_plaintext?: string | null
  is_active?: boolean | number | null
  last_seen_at?: string | null
  last_instance_id?: string | null
  last_error?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface CaptchaWorkerSessionRow {
  worker_session_id: string
  instance_id?: string
  captcha_worker_id?: number | null
  captcha_worker_key_label?: string | null
  captcha_worker_key_prefix?: string | null
  connected_at?: number
}

export interface ListCaptchaWorkerKeysResponse {
  success?: boolean
  keys?: CaptchaWorkerKeyRow[]
  sessions?: CaptchaWorkerSessionRow[]
}

export interface CreateCaptchaWorkerKeyResponse {
  success?: boolean
  key?: CaptchaWorkerKeyRow
  captcha_worker_key?: string
  detail?: string
}

export interface DeleteCaptchaWorkerKeyResponse {
  success?: boolean
  key_id?: number
  detail?: string
}

export interface KillCaptchaWorkerSessionsResponse {
  success?: boolean
  killed_count?: number
  message?: string
  detail?: string
}

/** Paginated list from GET /api/logs */
export interface LogsListResponse {
  logs: LogListItem[]
  total: number
  limit: number
  offset: number
}

/** List item from GET /api/logs */
export interface LogListItem {
  id: number
  token_id?: number | null
  token_email?: string | null
  token_username?: string | null
  api_key_id?: number | null
  api_key_label?: string | null
  api_key_prefix?: string | null
  operation?: string | null
  status_code?: number | null
  duration?: number | null
  status_text?: string
  progress?: number | null
  created_at?: string | null
  updated_at?: string | null
  error_summary?: string
}

/** Detail from GET /api/logs/:id */
export interface LogDetail extends LogListItem {
  request_body?: string | null
  response_body?: string | null
}

export interface ImportTokenItem {
  email?: string | null
  access_token?: string | null
  session_token?: string | null
  is_active?: boolean
  captcha_proxy_url?: string | null
  image_enabled?: boolean
  video_enabled?: boolean
  image_concurrency?: number
  video_concurrency?: number
}

export interface CacheConfigResponse {
  success?: boolean
  config?: {
    enabled?: boolean
    /** Retention in seconds (internal). */
    timeout?: number
    /** Retention in days (same as timeout / 86400; 0 = no auto-expiry). */
    timeout_days?: number
    base_url?: string
    effective_base_url?: string
  }
}

export interface CacheStatsResponse {
  success?: boolean
  cache_dir?: string
  file_count?: number
  total_bytes?: number
}

export interface CacheFileItem {
  name: string
  size_bytes: number
  kind: "image" | "video" | "other"
  modified_at?: string | null
}

export interface CacheFilesResponse {
  success?: boolean
  files?: CacheFileItem[]
}

export interface TokenProjectRow {
  id?: number
  project_id: string
  project_name: string
  token_id?: number
  is_active?: boolean
  is_current_for_token?: boolean
  project_status?: "active" | "old"
  created_at?: string | null
}

/** GET /api/admin/managed-apikeys/:id/projects */
export interface ManagedApiKeyAccountSummary {
  token_id: number
  email?: string | null
  active_project_id?: string | null
  active_project_name?: string | null
  current_project_id?: string | null
  current_project_name?: string | null
}

export interface ManagedApiKeyProjectsResponse {
  success?: boolean
  projects?: TokenProjectRow[]
  total?: number
  limit?: number
  offset?: number
  accounts?: ManagedApiKeyAccountSummary[]
}

export interface CreateProjectResponse {
  success?: boolean
  project?: TokenProjectRow
  token?: {
    id?: number
    current_project_id?: string | null
    current_project_name?: string | null
  }
}
