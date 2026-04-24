import type { LogListItem } from "../../types/admin"

export const STATUS_MAP: Record<string, string> = {
  started: "Started",
  token_selected: "Token selected",
  token_ready: "Token ready",
  project_ready: "Project ready",
  uploading_images: "Uploading images",
  solving_image_captcha: "Solving captcha",
  submitting_image: "Submitting image",
  image_generated: "Image done",
  preparing_video: "Preparing video",
  submitting_video: "Submitting video",
  video_submitted: "Video submitted",
  video_polling: "Video polling",
  caching_image: "Caching image",
  caching_video: "Caching video",
  completed: "Completed",
  failed: "Failed",
  processing: "Processing",
  upsampling_2k: "Upsampling 2K",
  upsampling_4k: "Upsampling 4K",
  upsampling_1080p: "Upsampling 1080p",
}

export type UITone = "success" | "processing" | "error" | "neutral"

/** Image / video / other for operation chip */
export function getOperationKind(operation: string | null | undefined): "image" | "video" | "other" {
  const op = String(operation || "").trim()
  if (op === "generate_image") return "image"
  if (op === "generate_video") return "video"
  return "other"
}

export function operationLabel(kind: ReturnType<typeof getOperationKind>, raw: string | null | undefined): string {
  if (kind === "image") return "Image"
  if (kind === "video") return "Video"
  const op = String(raw || "").trim()
  return op.length > 18 ? `${op.slice(0, 16)}…` : op || "—"
}

/** Row status badge tone (aligned with static status_code + status_text semantics) */
export function statusTone(l: LogListItem): UITone {
  const code = l.status_code
  const st = (l.status_text || "").trim().toLowerCase()
  if (st === "failed") return "error"
  if (code != null && code >= 400) return "error"
  if (code === 102) return "processing"
  if (code === 200) return "success"
  if (code != null && code < 400 && st && st !== "completed" && st !== "failed") return "processing"
  if (st && st !== "completed") return "processing"
  return "neutral"
}

/** HTTP status code pill */
export function httpCodeTone(code: number | null | undefined): UITone {
  if (code == null) return "neutral"
  if (code >= 400) return "error"
  if (code === 102) return "processing"
  if (code === 200) return "success"
  return "neutral"
}

/** Summary line color (static formatLogOutcomeClass) */
export function outcomeTone(l: LogListItem): UITone {
  const code = Number(l.status_code)
  if (code >= 400) return "error"
  if (code === 200) return "success"
  if (code === 102) return "processing"
  return "neutral"
}

export function formatLogStatus(l: LogListItem): string {
  const st = (l.status_text || "").trim()
  if (st) return STATUS_MAP[st] || st.replace(/_/g, " ")
  if (l.status_code === 102) return "Processing"
  if (l.status_code === 200) return "Completed"
  if (l.status_code != null && l.status_code >= 400) return "Failed"
  return "—"
}

export function progressPercent(l: LogListItem): number | null {
  if (l.progress === null || l.progress === undefined) return null
  const n = Number(l.progress)
  if (!Number.isFinite(n)) return null
  return Math.max(0, Math.min(100, n))
}

export function formatProgressLabel(l: LogListItem): string {
  const p = progressPercent(l)
  return p == null ? "—" : `${p}%`
}

export function formatOutcome(l: LogListItem): string {
  const code = Number(l.status_code)
  if (code === 200) {
    const op = String(l.operation || "").trim()
    if (op === "generate_image") return "Image result returned"
    if (op === "generate_video") return "Video result returned"
    return "Result returned"
  }
  if (code === 102) return "Processing"
  const err = (l.error_summary || "").trim()
  if (err) return err.length > 96 ? `${err.slice(0, 93)}…` : err
  if (code >= 400) return "Request failed"
  return "—"
}

/** Pill / badge shell (border + bg opacity) for light + dark */
export const tonePillClass: Record<UITone, string> = {
  success:
    "border-emerald-500/30 bg-emerald-500/15 text-emerald-900 dark:text-emerald-200 font-medium",
  processing:
    "border-amber-500/35 bg-amber-500/15 text-amber-950 dark:text-amber-200 font-medium",
  error: "border-red-500/35 bg-red-500/15 text-red-900 dark:text-red-200 font-medium",
  neutral: "border-border bg-muted/70 text-muted-foreground font-medium",
}

export const toneTextClass: Record<UITone, string> = {
  success: "text-emerald-800 dark:text-emerald-300",
  processing: "text-amber-900 dark:text-amber-200",
  error: "text-red-800 dark:text-red-300",
  neutral: "text-muted-foreground",
}

/** Operation chip: distinct from status */
export function operationChipClass(kind: ReturnType<typeof getOperationKind>): string {
  if (kind === "image")
    return "border-sky-500/30 bg-sky-500/12 text-sky-900 dark:text-sky-200 font-medium"
  if (kind === "video")
    return "border-violet-500/30 bg-violet-500/12 text-violet-900 dark:text-violet-200 font-medium"
  return "border-border bg-secondary/80 text-secondary-foreground font-medium"
}

export function tryFormatJson(raw: string | null | undefined): string {
  if (!raw) return ""
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const now = Date.now()
  const diffSec = Math.round((then - now) / 1000)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })
  const abs = Math.abs(diffSec)
  if (abs < 60) return rtf.format(Math.round(diffSec), "second")
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute")
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour")
  if (abs < 604800) return rtf.format(Math.round(diffSec / 86400), "day")
  return rtf.format(Math.round(diffSec / 604800), "week")
}
