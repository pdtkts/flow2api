/** Dashboard counters from GET /api/stats */
export interface DashboardStats {
  total_tokens: number
  active_tokens: number
  total_images: number
  total_videos: number
  total_errors: number
  today_images: number
  today_videos: number
  today_errors: number
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
}

/** List item from GET /api/logs */
export interface LogListItem {
  id: number
  token_id?: number | null
  token_email?: string | null
  token_username?: string | null
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
