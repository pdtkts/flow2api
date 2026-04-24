import { useMemo, useState, type ReactNode } from "react"
import type { LogDetail } from "../../types/admin"
import {
  extractLogErrorSummary,
  extractLogPrimaryUrl,
  extractLogSuccessSummaryEn,
  formatLogDetailLocalTimestamp,
  formatLogPayload,
  formatLogProgressField,
  isImageUrl,
  isVideoUrl,
  normalizeLogMediaUrl,
  parseLogJson,
  statusCodePillClass,
} from "./requestLogDetail"
import { formatLogStatus } from "./requestLogUi"
import { cn } from "@/lib/utils"

const sectionTitle = "text-sm font-semibold text-foreground tracking-tight"
const cardBox = "rounded-lg border border-border bg-muted/40 p-3.5"
const kLabel = "text-muted-foreground"

type PayloadVariant = "default" | "fullResponse"

function LogPayloadPre({
  children,
  className,
  variant = "default",
}: {
  children: string
  className?: string
  variant?: PayloadVariant
}) {
  return (
    <pre
      className={cn(
        "rounded-lg border border-border bg-muted/30 p-3.5 text-[13px] font-mono leading-relaxed text-foreground overflow-x-auto whitespace-pre",
        variant === "fullResponse" && "max-h-[min(420px,55vh)] max-w-full overflow-y-auto overscroll-contain [scrollbar-gutter:stable]",
        className
      )}
    >
      {children}
    </pre>
  )
}

function LogMediaPreview({ label, url, withUrl = true }: { label: string; url: string; withUrl?: boolean }) {
  const previewUrl = normalizeLogMediaUrl(url)
  const mediaType = isVideoUrl(previewUrl) ? "video" : isImageUrl(previewUrl) ? "image" : ""
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)

  const isDataUrl = /^data:/i.test(String(previewUrl))

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {withUrl && !isDataUrl ? (
        <p className="text-xs text-foreground leading-relaxed">
          <span className="font-medium text-muted-foreground">URL:</span>{" "}
          <a href={previewUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline break-all">
            {previewUrl}
          </a>
        </p>
      ) : null}
      {withUrl && isDataUrl ? (
        <p className="text-xs text-foreground">
          <span className="font-medium text-muted-foreground">URL:</span>{" "}
          <span className="text-muted-foreground">data URL (length {String(previewUrl).length})</span>
        </p>
      ) : null}
      {mediaType && !loaded && !failed ? (
        <button
          type="button"
          onClick={() => setLoaded(true)}
          className="inline-flex items-center justify-center rounded-lg border border-border bg-secondary px-3.5 py-1.5 text-xs font-medium text-secondary-foreground shadow-sm transition-colors hover:bg-secondary/80"
        >
          Click to load preview
        </button>
      ) : null}
      {mediaType && loaded && !failed ? (
        <div className="space-y-2">
          {mediaType === "video" ? (
            <video
              src={previewUrl}
              controls
              preload="metadata"
              className="w-full max-h-80 rounded-lg border border-border bg-black"
              onError={() => setFailed(true)}
            />
          ) : (
            <img
              src={previewUrl}
              alt={label}
              loading="lazy"
              decoding="async"
              className="max-h-80 rounded-lg border border-border object-contain bg-muted"
              onError={() => setFailed(true)}
            />
          )}
        </div>
      ) : null}
      {failed ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive space-y-2">
          <p>Preview could not be loaded. Open the link in a new tab.</p>
          {previewUrl && !isDataUrl ? (
            <p>
              <span className="font-medium text-foreground">URL:</span>{" "}
              <a href={previewUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline break-all">
                {previewUrl}
              </a>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function BasicInfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="text-sm text-foreground">
      <span className={kLabel}>{label}:</span> {children}
    </div>
  )
}

export function LogDetailStatic({ log }: { log: LogDetail }) {
  const { responseBodyObj, requestPayloadText, responsePayloadText, errorSummary, successSummary } = useMemo(() => {
    const responseBodyObj = parseLogJson(log.response_body) as Record<string, unknown> | null
    const requestPayloadText = formatLogPayload(log.request_body)
    const responsePayloadText = formatLogPayload(log.response_body)
    const errorSummary = extractLogErrorSummary(log, responseBodyObj)
    const successSummary = extractLogSuccessSummaryEn(log, responseBodyObj)
    return { responseBodyObj, requestPayloadText, responsePayloadText, errorSummary, successSummary }
  }, [log])

  const code = log.status_code
  const tokenDisplay = log.token_email || log.token_username || "—"
  const durationStr = `${Number(log.duration || 0).toFixed(2)} seconds`
  const assets =
    responseBodyObj && typeof responseBodyObj === "object"
      ? (responseBodyObj.generated_assets as Record<string, unknown> | undefined)
      : undefined

  let assetsBlock: ReactNode = null
  if (code === 200 && responseBodyObj) {
    const directUrl = extractLogPrimaryUrl(responseBodyObj)
    if (assets && typeof assets === "object") {
      let inner: ReactNode = null
      if (assets.upscaled_image && typeof assets.upscaled_image === "object") {
        const up = assets.upscaled_image as { resolution?: string; local_url?: string; url?: string; base64?: string }
        const upResolution = up.resolution || "upscaled"
        const upPreviewUrl = up.local_url || up.url || null
        inner = (
          <div className="space-y-4">
            <p className="text-xs text-foreground">
              <span className="font-medium text-muted-foreground">Zoomed resolution:</span> {upResolution}
            </p>
            {upPreviewUrl ? (
              <LogMediaPreview
                label={`${upResolution} results`}
                url={String(upPreviewUrl)}
                withUrl={!/^data:/i.test(String(upPreviewUrl || ""))}
              />
            ) : null}
            {up.base64 ? (
              <>
                <p className="text-xs text-foreground">
                  <span className="font-medium text-muted-foreground">Base64 length:</span> {String(up.base64).length}
                </p>
                <details className="rounded-lg border border-border bg-muted/30 p-2">
                  <summary className="cursor-pointer text-xs text-muted-foreground">View base64 preview</summary>
                  <pre className="mt-2 text-xs text-foreground overflow-x-auto max-h-48 overflow-y-auto">
                    {String(up.base64).length > 600 ? `${String(up.base64).slice(0, 600)}...` : String(up.base64)}
                  </pre>
                </details>
              </>
            ) : null}
          </div>
        )
      } else {
        const extraMediaUrl = (assets.final_video_url || assets.final_image_url) as string | undefined
        if (extraMediaUrl && extraMediaUrl !== directUrl) {
          inner = <LogMediaPreview label="Additional result" url={String(extraMediaUrl)} withUrl={false} />
        }
      }
      assetsBlock = (
        <div className="space-y-2.5">
          <h4 className={sectionTitle}>2K/4K Asset Information</h4>
          <div className={cn(cardBox, "space-y-2.5")}>
            {inner || <p className="text-xs text-muted-foreground">No asset details</p>}
          </div>
        </div>
      )
    }
  }

  return (
    <div className="space-y-5 text-foreground">
      <div className="space-y-2.5">
        <h4 className={sectionTitle}>Request data</h4>
        <LogPayloadPre>{requestPayloadText}</LogPayloadPre>
      </div>

      {code === 200 ? (
        <>
          {successSummary ? (
            <div className="space-y-2.5">
              <h4 className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">Results Summary</h4>
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3.5">
                <p className="text-sm font-medium text-emerald-800 dark:text-emerald-200">{successSummary}</p>
              </div>
            </div>
          ) : null}
          {responseBodyObj ? (
            <>
              {extractLogPrimaryUrl(responseBodyObj) ? (
                <div className="space-y-2.5">
                  <h4 className={sectionTitle}>Result</h4>
                  <div className={cn(cardBox, "space-y-0")}>
                    <LogMediaPreview label="Main result" url={String(extractLogPrimaryUrl(responseBodyObj))} />
                  </div>
                </div>
              ) : null}
              {assetsBlock}
              <details className="border-t border-border pt-4 open:[&>summary>span.chevron]:rotate-90" data-detail-key="full-response">
                <summary className="cursor-pointer list-none text-sm font-semibold text-foreground marker:hidden select-none [&::-webkit-details-marker]:hidden">
                  <span className="chevron inline-block translate-y-px pr-1 text-muted-foreground transition-transform duration-200">▶</span>
                  Full response (large fields have been truncated)
                </summary>
                <LogPayloadPre variant="fullResponse" className="mt-3">
                  {responsePayloadText}
                </LogPayloadPre>
              </details>
            </>
          ) : (
            <div className="space-y-2.5">
              <h4 className={sectionTitle}>Response</h4>
              <LogPayloadPre>{responsePayloadText}</LogPayloadPre>
            </div>
          )}
        </>
      ) : (
        <>
          {errorSummary ? (
            <div className="space-y-2.5">
              <h4 className="text-sm font-semibold text-destructive">Error</h4>
              <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3.5">
                <p className="text-sm text-destructive break-all">{errorSummary}</p>
              </div>
            </div>
          ) : null}
          <div className="space-y-2.5">
            <h4 className="text-sm font-semibold text-destructive">Error response</h4>
            <pre className="rounded-lg border border-destructive/30 bg-destructive/10 p-3.5 text-xs font-mono text-destructive overflow-x-auto whitespace-pre">
              {responsePayloadText}
            </pre>
          </div>
        </>
      )}

      <div className="space-y-3 border-t border-border pt-5">
        <h4 className={sectionTitle}>Basic Information</h4>
        <div className="flex flex-col gap-2.5 text-sm">
          <BasicInfoRow label="Operation">{log.operation || "—"}</BasicInfoRow>
          <BasicInfoRow label="Status">{formatLogStatus(log)}</BasicInfoRow>
          <BasicInfoRow label="Status code">
            <span
              className={cn(
                "inline-flex min-w-[2.25rem] items-center justify-center rounded px-2 py-0.5 text-xs font-medium tabular-nums",
                statusCodePillClass(log.status_code ?? undefined)
              )}
            >
              {log.status_code ?? "—"}
            </span>
          </BasicInfoRow>
          <BasicInfoRow label="Time taken">{durationStr}</BasicInfoRow>
          <BasicInfoRow label="Time">{formatLogDetailLocalTimestamp(log.created_at)}</BasicInfoRow>
          <BasicInfoRow label="Token">{tokenDisplay}</BasicInfoRow>
          <BasicInfoRow label="Log ID">{log.id ?? "—"}</BasicInfoRow>
          <BasicInfoRow label="Progress">{formatLogProgressField(log)}</BasicInfoRow>
        </div>
      </div>
    </div>
  )
}
