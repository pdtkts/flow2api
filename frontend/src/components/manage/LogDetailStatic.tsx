import { useMemo, useState, type ReactNode } from "react"
import type { LogDetail } from "../../types/admin"
import {
  extractLogErrorSummary,
  extractLogPrimaryUrl,
  extractLogSuccessSummary,
  formatLogPayload,
  formatLogProgressField,
  formatLogStatusZh,
  isImageUrl,
  isVideoUrl,
  normalizeLogMediaUrl,
  parseLogJson,
  statusCodePillClass,
} from "./requestLogDetail"
import { cn } from "@/lib/utils"

/** Matches `static/manage.html` `renderLogDetail` & `formatLogPayload` output blocks */
function LogPayloadPre({ children, className }: { children: string; className?: string }) {
  return (
    <pre
      className={cn(
        "rounded-md border border-border p-3 bg-muted/30 text-xs overflow-x-auto whitespace-pre",
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
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {withUrl && !isDataUrl ? (
        <p className="text-xs">
          <span className="font-medium">URL:</span>{" "}
          <a href={previewUrl} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline break-all">
            {previewUrl}
          </a>
        </p>
      ) : null}
      {withUrl && isDataUrl ? (
        <p className="text-xs">
          <span className="font-medium">URL:</span> <span className="text-muted-foreground">data URL（长度 {String(previewUrl).length}）</span>
        </p>
      ) : null}
      {mediaType && !loaded && !failed ? (
        <button
          type="button"
          onClick={() => setLoaded(true)}
          className="inline-flex items-center justify-center rounded-md border border-border px-3 py-1.5 text-xs hover:bg-accent"
        >
          点击加载预览
        </button>
      ) : null}
      {mediaType && loaded && !failed ? (
        <div className="space-y-2">
          {mediaType === "video" ? (
            <video
              src={previewUrl}
              controls
              preload="metadata"
              className="w-full max-h-80 rounded-md border border-border bg-black"
              onError={() => setFailed(true)}
            />
          ) : (
            <img
              src={previewUrl}
              alt={label}
              loading="lazy"
              decoding="async"
              className="max-h-80 rounded-md border border-border object-contain bg-background"
              onError={() => setFailed(true)}
            />
          )}
        </div>
      ) : null}
      {failed ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 space-y-2">
          <p>
            {label}预览加载失败，请直接打开链接查看。
          </p>
          {previewUrl && !isDataUrl ? (
            <p className="text-xs">
              <span className="font-medium">URL:</span>{" "}
              <a href={previewUrl} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline break-all">
                {previewUrl}
              </a>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function LogDetailStatic({ log }: { log: LogDetail }) {
  const { responseBodyObj, requestPayloadText, responsePayloadText, errorSummary, successSummary } = useMemo(() => {
    const responseBodyObj = parseLogJson(log.response_body) as Record<string, unknown> | null
    const requestPayloadText = formatLogPayload(log.request_body)
    const responsePayloadText = formatLogPayload(log.response_body)
    const errorSummary = extractLogErrorSummary(log, responseBodyObj)
    const successSummary = extractLogSuccessSummary(log, responseBodyObj)
    return { responseBodyObj, requestPayloadText, responsePayloadText, errorSummary, successSummary }
  }, [log])

  const code = log.status_code
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
        const upResolution = up.resolution || "放大"
        const upPreviewUrl = up.local_url || up.url || null
        inner = (
          <>
            <p className="text-xs">
              <span className="font-medium">放大分辨率:</span> {upResolution}
            </p>
            {upPreviewUrl ? <LogMediaPreview label={`${upResolution}结果`} url={String(upPreviewUrl)} withUrl={!/^data:/i.test(String(upPreviewUrl || ""))} /> : null}
            {up.base64 ? (
              <>
                <p className="text-xs">
                  <span className="font-medium">Base64长度:</span> {String(up.base64).length}
                </p>
                <details className="rounded border border-border p-2 bg-background">
                  <summary className="cursor-pointer text-xs text-muted-foreground">查看Base64预览</summary>
                  <pre className="mt-2 text-xs overflow-x-auto">
                    {String(up.base64).length > 600 ? `${String(up.base64).slice(0, 600)}...` : String(up.base64)}
                  </pre>
                </details>
              </>
            ) : null}
          </>
        )
      } else {
        const extraMediaUrl = (assets.final_video_url || assets.final_image_url) as string | undefined
        if (extraMediaUrl && extraMediaUrl !== directUrl) {
          inner = <LogMediaPreview label="额外结果" url={String(extraMediaUrl)} withUrl={false} />
        }
      }
      assetsBlock = (
        <div className="space-y-2">
          <h4 className="font-medium text-sm">2K/4K 资产信息</h4>
          <div className="rounded-md border border-border p-3 bg-muted/30 space-y-2">
            {inner || <p className="text-xs text-muted-foreground">无资产详情</p>}
          </div>
        </div>
      )
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h4 className="font-medium text-sm">请求数据</h4>
        <LogPayloadPre>{requestPayloadText}</LogPayloadPre>
      </div>

      {code === 200 ? (
        <>
          {successSummary ? (
            <div className="space-y-2">
              <h4 className="font-medium text-sm text-green-700">结果摘要</h4>
              <div className="rounded-md border border-green-200 p-3 bg-green-50">
                <p className="text-sm text-green-700">{successSummary}</p>
              </div>
            </div>
          ) : null}
          {responseBodyObj ? (
            <>
              {extractLogPrimaryUrl(responseBodyObj) ? (
                <div className="space-y-2">
                  <h4 className="font-medium text-sm">生成结果</h4>
                  <div className="rounded-md border border-border p-3 bg-muted/30 space-y-3">
                    <LogMediaPreview label="主结果" url={String(extractLogPrimaryUrl(responseBodyObj))} />
                  </div>
                </div>
              ) : null}
              {assetsBlock}
              <details className="space-y-2" data-detail-key="full-response">
                <summary className="cursor-pointer text-sm font-medium">完整响应（大字段已截断）</summary>
                <LogPayloadPre className="mt-2">{responsePayloadText}</LogPayloadPre>
              </details>
            </>
          ) : (
            <div className="space-y-2">
              <h4 className="font-medium text-sm">响应数据</h4>
              <LogPayloadPre>{responsePayloadText}</LogPayloadPre>
            </div>
          )}
        </>
      ) : (
        <>
          {errorSummary ? (
            <div className="space-y-2">
              <h4 className="font-medium text-sm text-red-600">错误原因</h4>
              <div className="rounded-md border border-red-200 p-3 bg-red-50">
                <p className="text-sm text-red-700 break-all">{errorSummary}</p>
              </div>
            </div>
          ) : null}
          <div className="space-y-2">
            <h4 className="font-medium text-sm text-red-600">错误响应</h4>
            <pre className="rounded-md border border-red-200 p-3 bg-red-50 text-xs overflow-x-auto whitespace-pre">
              {responsePayloadText}
            </pre>
          </div>
        </>
      )}

      <div className="space-y-2 pt-4 border-t border-border">
        <h4 className="font-medium text-sm">基本信息</h4>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-muted-foreground">操作:</span> {log.operation || "-"}
          </div>
          <div>
            <span className="text-muted-foreground">状态:</span> {formatLogStatusZh(log)}
          </div>
          <div>
            <span className="text-muted-foreground">状态码:</span>{" "}
            <span className={cn("inline-flex items-center rounded px-2 py-0.5 text-xs", statusCodePillClass(log.status_code ?? undefined))}>
              {log.status_code ?? "-"}
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">耗时:</span> {Number(log.duration || 0).toFixed(2)}秒
          </div>
          <div>
            <span className="text-muted-foreground">时间:</span> {log.created_at ? new Date(log.created_at).toLocaleString("zh-CN") : "-"}
          </div>
          <div>
            <span className="text-muted-foreground">Token:</span> {log.token_email || log.token_username || "未知"}
          </div>
          <div>
            <span className="text-muted-foreground">日志ID:</span> {log.id ?? "-"}
          </div>
          <div>
            <span className="text-muted-foreground">进度:</span> {formatLogProgressField(log)}
          </div>
        </div>
      </div>
    </div>
  )
}
