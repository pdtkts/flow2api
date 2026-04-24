import { useState, useEffect, useCallback } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Switch } from "../ui/switch"
import { toast } from "sonner"
import { RefreshCw, Trash2, File } from "lucide-react"
import type { CacheStatsResponse, CacheConfigResponse, CacheFilesResponse, CacheFileItem } from "../../types/admin"

const SECONDS_PER_DAY = 86400
const MAX_CACHE_DAYS = 7
const MAX_CACHE_SECONDS = MAX_CACHE_DAYS * SECONDS_PER_DAY

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function secondsToDaysField(sec: number): string {
  if (sec <= 0) return "0"
  const d = sec / SECONDS_PER_DAY
  return String(Math.round(d * 1e4) / 1e4)
}

function daysFieldToSeconds(raw: string): { ok: true; seconds: number } | { ok: false; message: string } {
  const t = raw.trim()
  if (t === "") return { ok: true, seconds: 7200 }
  const days = parseFloat(t)
  if (Number.isNaN(days) || days < 0) return { ok: false, message: "Enter a number of days between 0 and 7" }
  if (days > MAX_CACHE_DAYS) return { ok: false, message: `Max retention is ${MAX_CACHE_DAYS} days` }
  if (days === 0) return { ok: true, seconds: 0 }
  const sec = Math.round(days * SECONDS_PER_DAY)
  return { ok: true, seconds: Math.min(MAX_CACHE_SECONDS, sec) }
}

/** Public URL for a file under the API static /tmp mount (dev: Vite proxies /tmp → backend). */
function cacheFilePublicUrl(fileName: string) {
  return `/tmp/${encodeURIComponent(fileName)}`
}

function MediaTile({ file }: { file: CacheFileItem }) {
  const url = cacheFilePublicUrl(file.name)
  const meta = (
    <div className="border-t bg-muted/40 px-2 py-1.5 text-xs">
      <p className="truncate font-mono text-[10px] text-muted-foreground" title={file.name}>
        {file.name}
      </p>
      <p className="text-muted-foreground">{formatBytes(file.size_bytes)}</p>
    </div>
  )

  if (file.kind === "image") {
    return (
      <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
        <a href={url} target="_blank" rel="noreferrer" className="block aspect-[4/3] bg-muted">
          <img src={url} alt="" className="h-full w-full object-cover" loading="lazy" decoding="async" />
        </a>
        {meta}
      </div>
    )
  }

  if (file.kind === "video") {
    return (
      <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
        <div className="aspect-video bg-black">
          <video
            src={url}
            className="h-full w-full object-contain"
            controls
            playsInline
            preload="metadata"
            muted
          />
        </div>
        {meta}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-[11rem] flex-col overflow-hidden rounded-lg border bg-card shadow-sm">
      <div className="flex flex-1 flex-col items-center justify-center gap-2 bg-muted/30 p-3">
        <File className="h-10 w-10 text-muted-foreground" />
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="line-clamp-2 break-all text-center text-xs text-primary hover:underline"
        >
          {file.name}
        </a>
      </div>
      {meta}
    </div>
  )
}

export function CacheManagement({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [cacheEnabled, setCacheEnabled] = useState(true)
  const [cacheTimeoutDays, setCacheTimeoutDays] = useState("0.0833")
  const [cacheBaseUrl, setCacheBaseUrl] = useState("")
  const [cacheEffectiveUrl, setCacheEffectiveUrl] = useState("")

  const [storeLoading, setStoreLoading] = useState(false)
  const [fileCount, setFileCount] = useState<number | null>(null)
  const [totalBytes, setTotalBytes] = useState<number | null>(null)
  const [cacheDir, setCacheDir] = useState("")
  const [galleryFiles, setGalleryFiles] = useState<CacheFileItem[]>([])

  const [busy, setBusy] = useState(false)

  const loadConfig = useCallback(async () => {
    if (!token || !active) return
    const cache = await adminJson<CacheConfigResponse>("/api/cache/config", token)
    if (cache.ok && cache.data?.success && cache.data.config) {
      setCacheEnabled(cache.data.config.enabled !== false)
      const sec = cache.data.config.timeout ?? 7200
      if (typeof cache.data.config.timeout_days === "number" && !Number.isNaN(cache.data.config.timeout_days)) {
        const d = cache.data.config.timeout_days
        setCacheTimeoutDays(d <= 0 ? "0" : String(Math.round(d * 1e4) / 1e4))
      } else {
        setCacheTimeoutDays(secondsToDaysField(sec))
      }
      setCacheBaseUrl(cache.data.config.base_url || "")
      setCacheEffectiveUrl(cache.data.config.effective_base_url || "")
    }
  }, [token, active])

  const refreshStore = useCallback(async () => {
    if (!token || !active) return
    setStoreLoading(true)
    try {
      const [stats, files] = await Promise.all([
        adminJson<CacheStatsResponse>("/api/cache/stats", token),
        adminJson<CacheFilesResponse>("/api/cache/files", token),
      ])
      if (stats.ok && stats.data?.success) {
        setFileCount(stats.data.file_count ?? 0)
        setTotalBytes(stats.data.total_bytes ?? 0)
        setCacheDir(stats.data.cache_dir || "")
      } else toast.error("Failed to load cache stats")
      if (files.ok && files.data?.success && Array.isArray(files.data.files)) {
        setGalleryFiles(files.data.files)
      } else if (files.ok) {
        setGalleryFiles([])
      } else {
        toast.error("Failed to load cache files list")
        setGalleryFiles([])
      }
    } catch {
      toast.error("Failed to refresh cache store")
    } finally {
      setStoreLoading(false)
    }
  }, [token, active])

  const loadAll = useCallback(async () => {
    await Promise.all([loadConfig(), refreshStore()])
  }, [loadConfig, refreshStore])

  useEffect(() => {
    if (!active) return
    const id = requestAnimationFrame(() => {
      void loadAll()
    })
    return () => cancelAnimationFrame(id)
  }, [active, loadAll])

  const saveCache = async () => {
    if (!token) return
    const parsed = daysFieldToSeconds(cacheTimeoutDays)
    if (parsed.ok === false) {
      toast.error(parsed.message)
      return
    }
    const timeout = parsed.seconds
    const baseUrl = cacheBaseUrl.trim()
    if (baseUrl && !baseUrl.startsWith("http://") && !baseUrl.startsWith("https://")) return toast.error("Base URL must start with http(s)://")
    setBusy(true)
    try {
      const r0 = await adminFetch("/api/cache/enabled", token, {
        method: "POST",
        body: JSON.stringify({ enabled: cacheEnabled }),
      })
      if (!r0) return
      const d0 = await r0.json()
      if (!d0.success) return toast.error("Cache enabled save failed")
      const r1 = await adminFetch("/api/cache/config", token, {
        method: "POST",
        body: JSON.stringify({ timeout }),
      })
      if (!r1) return
      const d1 = await r1.json()
      if (!d1.success) return toast.error("Cache timeout save failed")
      const r2 = await adminFetch("/api/cache/base-url", token, {
        method: "POST",
        body: JSON.stringify({ base_url: baseUrl }),
      })
      if (!r2) return
      const d2 = await r2.json()
      if (d2.success) {
        toast.success("Cache config saved")
        await new Promise((r) => setTimeout(r, 200))
        await loadConfig()
      } else toast.error("Cache base URL failed")
    } finally {
      setBusy(false)
    }
  }

  const clearCache = async () => {
    if (!token) return
    if (!confirm("Delete all files in the cache directory? This cannot be undone.")) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/cache/clear", token, { method: "POST" })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        const n = d.removed_count ?? 0
        toast.success(`Removed ${n} file(s)`)
        await refreshStore()
      } else toast.error(d.detail || d.message || "Clear failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <Card>
        <CardHeader>
          <CardTitle>File cache</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Switch checked={cacheEnabled} onCheckedChange={setCacheEnabled} />
            <Label>Enable file cache</Label>
          </div>
          {cacheEnabled ? (
            <>
              <div>
                <Label>Cache retention (days)</Label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  How long to keep files before the cleanup task can remove them. 0 = no automatic expiry. Max {MAX_CACHE_DAYS}{" "}
                  days.
                </p>
                <Input
                  type="number"
                  min={0}
                  max={MAX_CACHE_DAYS}
                  step={0.01}
                  className="mt-1"
                  value={cacheTimeoutDays}
                  onChange={(e) => setCacheTimeoutDays(e.target.value)}
                />
              </div>
              <div>
                <Label>Public base URL for cached files</Label>
                <Input className="mt-1" value={cacheBaseUrl} onChange={(e) => setCacheBaseUrl(e.target.value)} placeholder="https://yourdomain.com" />
              </div>
              {cacheEffectiveUrl ? (
                <p className="text-xs text-muted-foreground">
                  Effective URL: <code className="bg-muted px-1 rounded">{cacheEffectiveUrl}</code>
                </p>
              ) : null}
            </>
          ) : null}
          <Button onClick={saveCache} disabled={busy}>
            Save cache settings
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>Cache store</CardTitle>
            {cacheDir ? (
              <p className="text-xs text-muted-foreground font-normal mt-1 break-all">
                Path: {cacheDir}
              </p>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => void refreshStore()} disabled={storeLoading || busy}>
              <RefreshCw className={`h-4 w-4 mr-1 ${storeLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Button type="button" variant="destructive" size="sm" onClick={clearCache} disabled={busy || storeLoading}>
              <Trash2 className="h-4 w-4 mr-1" />
              Clear cache
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span>
              <span className="text-foreground font-medium">Files: </span>
              {fileCount !== null ? fileCount : "—"}
            </span>
            <span>
              <span className="text-foreground font-medium">Size: </span>
              {totalBytes !== null ? formatBytes(totalBytes) : "—"}
            </span>
          </div>

          {storeLoading && !galleryFiles.length && fileCount === null ? (
            <p className="text-sm text-muted-foreground">Loading gallery…</p>
          ) : galleryFiles.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center border rounded-lg border-dashed">No files in cache</p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
              {galleryFiles.map((f) => (
                <MediaTile key={f.name} file={f} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
