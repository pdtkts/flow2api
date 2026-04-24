import type { LogListItem } from "../../types/admin"
import { formatLogStatus } from "./requestLogUi"

/** Logs may include `response_body` on detail fetches; list rows omit it. */
type LogWithResponseBody = LogListItem & { response_body?: string | null }

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

/** English copy for the log details template (list/detail UI). */
export function extractLogSuccessSummaryEn(
  log: LogListItem | null | undefined,
  responseBodyObj: unknown
): string {
  if (Number(log?.status_code) !== 200) return ""
  if (!responseBodyObj || typeof responseBodyObj !== "object") {
    return "Generation successful, results have been returned."
  }
  const o = responseBodyObj as Record<string, unknown>
  const assets = o.generated_assets as Record<string, unknown> | undefined
  if (assets && typeof assets === "object" && assets.upscaled_image && typeof assets.upscaled_image === "object") {
    const up = assets.upscaled_image as { resolution?: string }
    const res = up.resolution || "high-resolution"
    return `Generation successful, ${res} result returned.`
  }
  const directUrl = extractLogPrimaryUrl(responseBodyObj)
  if (directUrl) {
    if (isVideoUrl(directUrl)) return "Generation successful, video results returned."
    if (isImageUrl(directUrl)) return "Generation successful, image results returned."
    return "Generation successful, a result URL was returned."
  }
  return o.status === "success" ? "Generation successful." : "Generation successful, results have been returned."
}

/** e.g. `2026/4/22 12:04:53` for the Basic information / Time field */
export function formatLogDetailLocalTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`
}

export function formatLogPayload(raw: string | null | undefined): string {
  const parsed = parseLogJson(raw)
  if (parsed) {
    return JSON.stringify(
      parsed,
      (_, value: unknown) => {
        if (typeof value !== "string") return value
        if (value.length <= 4096) return value
        if (/^data:(image|video)\//i.test(value)) return `[data URL omitted, length=${value.length}]`
        const sample = value.slice(0, 256)
        if (/^[A-Za-z0-9+/=\r\n]+$/.test(sample)) return `[large base64 omitted, length=${value.length}]`
        return `${value.slice(0, 800)}... [truncated, length=${value.length}]`
      },
      2
    )
  }
  if (!raw) return "—"
  const text = String(raw)
  if (text.length <= 6000) return text
  return `${text.slice(0, 1200)}... [truncated, length=${text.length}]`
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

export function formatLogOutcomeRowClass(l: LogListItem): string {
  if ((l.status_code || 0) >= 400) return "text-red-600 dark:text-red-300"
  if (l.status_code === 200) return "text-green-700 dark:text-emerald-300"
  if (l.status_code === 102) return "text-amber-700 dark:text-amber-200"
  return "text-muted-foreground"
}

/** Pills in the status column (aligned with `formatLogStatus` from requestLogUi). */
export function logStatusPillClass(l: LogListItem): string {
  const s = formatLogStatus(l)
  if (s === "Processing")
    return "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-200"
  if (s === "Completed")
    return "bg-green-50 text-green-700 dark:bg-emerald-950/50 dark:text-emerald-200"
  if (s === "Failed") return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-200"
  return "bg-gray-100 text-gray-700 dark:bg-muted/80 dark:text-foreground/90"
}

export function statusCodePillClass(code: number | null | undefined): string {
  if (code === 200) return "bg-green-50 text-green-700 dark:bg-emerald-950/50 dark:text-emerald-200"
  if (code === 102) return "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
  return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-200"
}
