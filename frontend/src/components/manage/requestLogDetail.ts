import type { LogListItem } from "../../types/admin"

/** Logs may include `response_body` on detail fetches; list rows omit it. */
type LogWithResponseBody = LogListItem & { response_body?: string | null }

/** Same labels as `static/manage.html` `formatLogStatus` (Chinese). */
const STATUS_MAP_ZH: Record<string, string> = {
  started: "已启动",
  token_selected: "已选中账号",
  token_ready: "准备生成环境",
  project_ready: "项目已就绪",
  uploading_images: "上传参考图中",
  solving_image_captcha: "图片打码验证中",
  submitting_image: "图片提交中",
  image_generated: "图片生成完成",
  preparing_video: "准备视频任务",
  submitting_video: "视频提交中",
  video_submitted: "视频任务已提交",
  video_polling: "视频生成中",
  caching_image: "缓存图片中",
  caching_video: "缓存视频中",
  completed: "已完成",
  failed: "失败",
  processing: "处理中",
  upsampling_2k: "正在放大到2K",
  upsampling_4k: "正在放大到4K",
  upsampling_1080p: "正在放大到1080P",
}

export function formatLogStatusZh(l: LogListItem): string {
  const statusText = (l.status_text || "").trim()
  if (statusText) return STATUS_MAP_ZH[statusText] || statusText.replace(/_/g, " ")
  if (l.status_code === 102) return "处理中"
  if (l.status_code === 200) return "已完成"
  if (l.status_code != null && l.status_code >= 400) return "失败"
  return "-"
}

export function parseLogJson(raw: string | null | undefined): unknown | null {
  if (!raw) return null
  try {
    return JSON.parse(raw) as unknown
  } catch {
    return null
  }
}

export function truncateLogText(text: unknown, limit = 240): string {
  const value = String(text ?? "")
    .trim()
    .replace(/\s+/g, " ")
  if (!value) return ""
  return value.length <= limit ? value : `${value.slice(0, limit - 3)}...`
}

export function extractLogErrorSummary(log: LogWithResponseBody | null | undefined, responseBodyObj?: unknown | null): string {
  const direct = log && typeof log.error_summary === "string" ? log.error_summary.trim() : ""
  if (direct) return truncateLogText(direct)
  const payload = responseBodyObj === undefined ? parseLogJson(log?.response_body ?? undefined) : responseBodyObj

  const visit = (value: unknown): string => {
    if (value === null || value === undefined) return ""
    if (typeof value === "string") return truncateLogText(value)
    if (Array.isArray(value)) {
      for (const item of value) {
        const nested = visit(item)
        if (nested) return nested
      }
      return ""
    }
    if (typeof value === "object" && value !== null) {
      const o = value as Record<string, unknown>
      for (const key of ["error_summary", "error_message", "detail", "message"]) {
        if (typeof o[key] === "string" && String(o[key]).trim()) return truncateLogText(o[key])
      }
      const errorValue = o.error
      if (typeof errorValue === "string" && errorValue.trim()) return truncateLogText(errorValue)
      if (errorValue && typeof errorValue === "object") {
        const ev = errorValue as Record<string, unknown>
        for (const key of ["message", "detail", "reason", "code"]) {
          if (typeof ev[key] === "string" && String(ev[key]).trim()) return truncateLogText(ev[key])
        }
      }
      for (const key of ["response", "data", "performance"]) {
        const nested = visit(o[key])
        if (nested) return nested
      }
    }
    return ""
  }

  const summary = visit(payload)
  if (summary) return summary
  if (log?.response_body && !payload) return truncateLogText(log.response_body)
  return ""
}

export function extractLogPrimaryUrl(responseBodyObj: unknown): string | null {
  if (!responseBodyObj || typeof responseBodyObj !== "object") return null
  const o = responseBodyObj as Record<string, unknown>
  const data = o.data as unknown[] | undefined
  const assets = o.generated_assets as Record<string, unknown> | undefined
  return (
    (typeof o.url === "string" ? o.url : null) ||
    (data?.[0] && typeof data[0] === "object" && data[0] !== null && typeof (data[0] as { url?: string }).url === "string"
      ? (data[0] as { url: string }).url
      : null) ||
    (assets && typeof assets.final_video_url === "string" ? assets.final_video_url : null) ||
    (assets?.upscaled_image &&
    typeof assets.upscaled_image === "object" &&
    assets.upscaled_image !== null &&
    typeof (assets.upscaled_image as { local_url?: string }).local_url === "string"
      ? (assets.upscaled_image as { local_url: string }).local_url
      : null) ||
    (assets?.upscaled_image &&
    typeof assets.upscaled_image === "object" &&
    assets.upscaled_image !== null &&
    typeof (assets.upscaled_image as { url?: string }).url === "string"
      ? (assets.upscaled_image as { url: string }).url
      : null) ||
    (assets && typeof assets.final_image_url === "string" ? assets.final_image_url : null) ||
    null
  )
}

export function extractLogSuccessSummary(log: LogListItem | null | undefined, responseBodyObj: unknown): string {
  if (Number(log?.status_code) !== 200) return ""
  if (!responseBodyObj || typeof responseBodyObj !== "object") return "生成成功，已返回结果"
  const o = responseBodyObj as Record<string, unknown>
  const assets = o.generated_assets as Record<string, unknown> | undefined
  if (assets && typeof assets === "object" && assets.upscaled_image && typeof assets.upscaled_image === "object") {
    const up = assets.upscaled_image as { resolution?: string }
    return `生成成功，已返回${up.resolution || "高清"}结果`
  }
  const directUrl = extractLogPrimaryUrl(responseBodyObj)
  if (directUrl) {
    if (isVideoUrl(directUrl)) return "生成成功，已返回视频结果"
    if (isImageUrl(directUrl)) return "生成成功，已返回图片结果"
    return "生成成功，已返回结果地址"
  }
  return o.status === "success" ? "生成成功" : "生成成功，已返回结果"
}

export function formatLogPayload(raw: string | null | undefined): string {
  const parsed = parseLogJson(raw)
  if (parsed) {
    return JSON.stringify(
      parsed,
      (_, value: unknown) => {
        if (typeof value !== "string") return value
        if (value.length <= 4096) return value
        if (/^data:(image|video)\//i.test(value)) return `[数据URL已省略，长度=${value.length}]`
        const sample = value.slice(0, 256)
        if (/^[A-Za-z0-9+/=\r\n]+$/.test(sample)) return `[大体积Base64已省略，长度=${value.length}]`
        return `${value.slice(0, 800)}... [已截断，长度=${value.length}]`
      },
      2
    )
  }
  if (!raw) return "无"
  const text = String(raw)
  if (text.length <= 6000) return text
  return `${text.slice(0, 1200)}... [已截断，长度=${text.length}]`
}

export function normalizeLogMediaUrl(url: string): string {
  if (!url) return ""
  const text = String(url).trim()
  if (!text || /^data:/i.test(text)) return text
  try {
    const parsed = new URL(text, typeof window !== "undefined" ? window.location.origin : undefined)
    if (parsed.pathname.startsWith("/tmp/")) {
      const origin = typeof window !== "undefined" ? window.location.origin : ""
      return `${origin}${parsed.pathname}${parsed.search}${parsed.hash}`
    }
    return parsed.toString()
  } catch {
    return text
  }
}

export function isVideoUrl(url: string): boolean {
  if (!url) return false
  const text = String(url).toLowerCase()
  if (text.startsWith("data:video/")) return true
  return /(\.mp4|\.webm|\.mov|\.m3u8)(\?|$)/.test(text) || text.includes("/video/")
}

export function isImageUrl(url: string): boolean {
  if (!url) return false
  const text = String(url).toLowerCase()
  if (text.startsWith("data:image/")) return true
  return /(\.png|\.jpg|\.jpeg|\.webp|\.gif|\.avif|\.bmp)(\?|$)/.test(text) || text.includes("/image/")
}

/** Matches static `formatLogProgress` */
export function formatLogProgressField(l: LogListItem): string {
  if (l.progress === null || l.progress === undefined) return "-"
  const progress = Number(l.progress)
  return Number.isFinite(progress) ? `${Math.max(0, Math.min(100, progress))}%` : "-"
}

/** Same as `static/manage.html` `getLogOperationLabel` */
export function getLogOperationLabelZh(l: LogListItem): string {
  const op = String(l.operation || "").trim()
  if (op === "generate_image") return "图片"
  if (op === "generate_video") return "视频"
  return ""
}

/**
 * Outcome one-liner for the logs list — matches `static/manage.html` `formatLogOutcome`
 * and `formatLogOutcomeClass` for `className`
 */
export function formatLogOutcomeZh(l: LogListItem): string {
  const code = Number(l.status_code)
  if (code === 200) {
    const label = getLogOperationLabelZh(l)
    return label ? `${label}结果已返回` : "已返回结果"
  }
  if (code === 102) return "处理中"
  const err = extractLogErrorSummary(l, undefined)
  if (err) {
    return err.length > 96 ? `${err.slice(0, 93)}…` : err
  }
  if (code >= 400) return "请求失败"
  return "-"
}

export function formatLogOutcomeRowClass(l: LogListItem): string {
  if ((l.status_code || 0) >= 400) return "text-red-600 dark:text-red-300"
  if (l.status_code === 200) return "text-green-700 dark:text-emerald-300"
  if (l.status_code === 102) return "text-amber-700 dark:text-amber-200"
  return "text-muted-foreground"
}

/** Pills in the 状态 column — `static/manage.html` `formatLogStatusClass` */
export function logStatusPillClass(l: LogListItem): string {
  const s = formatLogStatusZh(l)
  if (s === "处理中")
    return "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-200"
  if (s === "已完成")
    return "bg-green-50 text-green-700 dark:bg-emerald-950/50 dark:text-emerald-200"
  if (s === "失败") return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-200"
  return "bg-gray-100 text-gray-700 dark:bg-muted/80 dark:text-foreground/90"
}

export function statusCodePillClass(code: number | null | undefined): string {
  if (code === 200) return "bg-green-50 text-green-700 dark:bg-emerald-950/50 dark:text-emerald-200"
  if (code === 102) return "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
  return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-200"
}
