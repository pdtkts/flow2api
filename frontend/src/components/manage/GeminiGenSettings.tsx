import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Badge } from "../ui/badge"
import { Button } from "../ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Switch } from "../ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { toast } from "sonner"
import { CheckCircle2, Edit3, Plus, RefreshCw, Save, Trash2 } from "lucide-react"

type GeminiGenConfig = {
  enabled: boolean
  video_enabled: boolean
  base_url: string
  poll_interval_image_sec: number
  poll_interval_video_sec: number
  timeout_image_sec: number
  timeout_video_sec: number
  global_image_concurrency: number
  global_video_concurrency: number
  cache_outputs: boolean
}

type GeminiGenQuotaSummary = {
  tier: "max" | "free" | "unknown"
  remaining: number | null
  used: number | null
  max: number | null
}

type GeminiGenAccount = {
  id: number
  label: string
  bearer_token: string
  bearer_token_preview: string
  refresh_token: string
  refresh_token_preview: string
  is_active: boolean
  image_concurrency: number
  video_concurrency: number
  image_in_flight: number
  video_in_flight: number
  image_gen_daily_limited?: boolean
  grok_image_daily_limited?: boolean
  video_daily_limited?: boolean
  image_gen_daily_limit_reset_at?: string | null
  grok_image_daily_limit_reset_at?: string | null
  video_daily_limit_reset_at?: string | null
  image_gen_quota?: GeminiGenQuotaSummary
  grok_image_quota?: GeminiGenQuotaSummary
  last_status: string
  last_error: string
  profile_email?: string | null
  profile_full_name?: string | null
  profile_is_active?: boolean | null
  available_credit?: number | null
  plan_credit?: number | null
  purchased_credit?: number | null
  plan_name?: string | null
  plan_expire_at?: string | null
  active_benefits?: Array<{ id?: number | null; name?: string; expire_at?: string | null; estimated_remaining?: number | null }>
  remaining_bulk_videos?: number | null
  remaining_daily_videos?: number | null
  remaining_grok_max_daily_videos?: number | null
  remaining_grok_max_daily_15s_videos?: number | null
  quota_synced_at?: string | null
  quota_sync_status?: string
  quota_sync_error?: string
  profile_synced_at?: string | null
  profile_sync_status?: string
  profile_sync_error?: string
}

type GeminiGenModel = {
  id: string
  description: string
}

type GeminiGenResponse = {
  config?: Partial<GeminiGenConfig>
  accounts?: GeminiGenAccount[]
  models?: GeminiGenModel[]
}

type GeminiGenStatusRow = {
  model_name: string
  group_key: string
  type: string
  success_rate: number | null
  status: string
  status_bucket: string
  generated_at?: string | null
  updated_at?: string | null
  matching_local_model_count: number
}

type GeminiGenStatusResponse = {
  success?: boolean
  status?: string
  error?: string
  window?: string
  generated_at?: string | null
  models?: GeminiGenStatusRow[]
  summary?: {
    operational?: number
    degraded?: number
    outage?: number
    unknown?: number
    matching_model_groups?: number
  }
  geminigen?: {
    enabled?: boolean
    active_account_count?: number
    image_in_flight?: number
    video_in_flight?: number
  }
}

type AccountDraft = {
  id?: number
  label: string
  bearer_token: string
  refresh_token: string
  is_active: boolean
  image_concurrency: string
  video_concurrency: string
}

const DEFAULT_CONFIG: GeminiGenConfig = {
  enabled: false,
  video_enabled: true,
  base_url: "https://api.geminigen.ai",
  poll_interval_image_sec: 3,
  poll_interval_video_sec: 12,
  timeout_image_sec: 600,
  timeout_video_sec: 1800,
  global_image_concurrency: 5,
  global_video_concurrency: 5,
  cache_outputs: true,
}

function formatCompactDateTime(value: string | null | undefined) {
  if (!value) return "-"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString("en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatNumberValue(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : value.toLocaleString()
}

function formatDailyLimitCountdown(value: string | null | undefined) {
  if (!value) return "until 00:00 UTC"
  const reset = new Date(value)
  if (Number.isNaN(reset.getTime())) return value
  const remainingMinutes = Math.max(0, Math.ceil((reset.getTime() - Date.now()) / 60_000))
  const hours = Math.floor(remainingMinutes / 60)
  const minutes = remainingMinutes % 60
  return `resets in ${hours}h ${minutes}m (${reset.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
  })} UTC)`
}

function ImageQuotaLine({ label, quota }: { label: string; quota?: GeminiGenQuotaSummary }) {
  const hasKnownMax = quota?.used !== null && quota?.used !== undefined && quota.max !== null && quota.max !== undefined
  const text = hasKnownMax
    ? `${quota.used} / ${quota.max}`
    : quota?.remaining !== null && quota?.remaining !== undefined
      ? `${quota.remaining} remaining`
      : "-"
  const percent = hasKnownMax && quota.max ? Math.max(0, Math.min(100, ((quota.used || 0) / quota.max) * 100)) : 0
  const exhausted = quota?.remaining === 0
  return (
    <div className="min-w-[150px]">
      <div className="flex items-center justify-between gap-3 text-[11px]">
        <span className="text-muted-foreground">{label}</span>
        <span className={exhausted ? "font-medium text-destructive" : "font-medium tabular-nums"}>{text}</span>
      </div>
      {hasKnownMax ? (
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
          <div className={exhausted ? "h-full bg-destructive" : "h-full bg-emerald-500"} style={{ width: `${percent}%` }} />
        </div>
      ) : null}
    </div>
  )
}

function geminiGenVideoQuota(account: GeminiGenAccount) {
  const quotas = [
    account.remaining_daily_videos !== null && account.remaining_daily_videos !== undefined ? `Daily ${account.remaining_daily_videos}` : "",
    account.remaining_bulk_videos !== null && account.remaining_bulk_videos !== undefined ? `Bulk ${account.remaining_bulk_videos}` : "",
    account.remaining_grok_max_daily_videos !== null && account.remaining_grok_max_daily_videos !== undefined
      ? `Grok ${account.remaining_grok_max_daily_videos}`
      : "",
    account.remaining_grok_max_daily_15s_videos !== null && account.remaining_grok_max_daily_15s_videos !== undefined
      ? `15s ${account.remaining_grok_max_daily_15s_videos}`
      : "",
  ].filter(Boolean)
  return quotas.length ? quotas.join(" / ") : "-"
}

function geminiGenImageQuotaTotal(
  accounts: GeminiGenAccount[],
  getQuota: (account: GeminiGenAccount) => GeminiGenQuotaSummary | undefined,
) {
  let used = 0
  let max = 0
  let hasUsed = false
  let hasMax = false
  let remainingOnly = 0
  let hasRemainingOnly = false

  accounts.forEach((account) => {
    const quota = getQuota(account)
    const quotaHasUsed = quota?.used !== null && quota?.used !== undefined
    const quotaHasMax = quota?.max !== null && quota?.max !== undefined

    if (quotaHasUsed) {
      used += quota.used || 0
      hasUsed = true
    }
    if (quotaHasMax) {
      max += quota.max || 0
      hasMax = true
    }
    if (!quotaHasUsed && !quotaHasMax && quota?.remaining !== null && quota?.remaining !== undefined) {
      remainingOnly += quota.remaining
      hasRemainingOnly = true
    }
  })

  const totals = []
  if (hasUsed || hasMax) {
    totals.push(`${hasUsed ? formatNumberValue(used) : "-"} / ${hasMax ? formatNumberValue(max) : "-"}`)
  }
  if (hasRemainingOnly) totals.push(`${formatNumberValue(remainingOnly)} remaining`)
  return totals.length ? totals.join(" + ") : "-"
}

function geminiGenVideoQuotaTotal(accounts: GeminiGenAccount[]) {
  const quotaFields: Array<{ label: string; key: keyof GeminiGenAccount }> = [
    { label: "Daily", key: "remaining_daily_videos" },
    { label: "Bulk", key: "remaining_bulk_videos" },
    { label: "Grok", key: "remaining_grok_max_daily_videos" },
    { label: "15s", key: "remaining_grok_max_daily_15s_videos" },
  ]
  const quotas = quotaFields.flatMap(({ label, key }) => {
    const values = accounts
      .map((account) => account[key])
      .filter((value): value is number => typeof value === "number")
    return values.length ? [`${label} ${formatNumberValue(values.reduce((sum, value) => sum + value, 0))}`] : []
  })
  return quotas.length ? quotas.join(" / ") : "-"
}

const EMPTY_ACCOUNT: AccountDraft = {
  label: "",
  bearer_token: "",
  refresh_token: "",
  is_active: true,
  image_concurrency: "5",
  video_concurrency: "5",
}

export function GeminiGenSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [config, setConfig] = useState<GeminiGenConfig>(DEFAULT_CONFIG)
  const [accounts, setAccounts] = useState<GeminiGenAccount[]>([])
  const [models, setModels] = useState<GeminiGenModel[]>([])
  const [draft, setDraft] = useState<AccountDraft>(EMPTY_ACCOUNT)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [updatingAccountIds, setUpdatingAccountIds] = useState<Set<number>>(new Set())
  const [statusLoading, setStatusLoading] = useState(false)
  const [modelStatus, setModelStatus] = useState<GeminiGenStatusResponse | null>(null)

  const load = useCallback(async () => {
    const resp = await adminJson<GeminiGenResponse>("/api/admin/geminigen/config", token)
    if (!resp.ok || !resp.data) return
    setConfig({ ...DEFAULT_CONFIG, ...(resp.data.config || {}) })
    setAccounts(Array.isArray(resp.data.accounts) ? resp.data.accounts : [])
    setModels(Array.isArray(resp.data.models) ? resp.data.models : [])
  }, [token])

  useEffect(() => {
    if (active) void load()
  }, [active, load])

  const loadStatus = useCallback(async () => {
    if (!active) return
    setStatusLoading(true)
    try {
      const resp = await adminJson<GeminiGenStatusResponse>("/api/admin/geminigen/models/status?window=1h", token)
      if (resp.data) setModelStatus(resp.data)
    } finally {
      setStatusLoading(false)
    }
  }, [active, token])

  useEffect(() => {
    if (active) void loadStatus()
  }, [active, loadStatus])

  const stats = useMemo(() => {
    return {
      accounts: accounts.length,
      active: accounts.filter((account) => account.is_active).length,
      models: models.length,
      imagenQuota: geminiGenImageQuotaTotal(accounts, (account) => account.image_gen_quota),
      grokImageQuota: geminiGenImageQuotaTotal(accounts, (account) => account.grok_image_quota),
      videoQuota: geminiGenVideoQuotaTotal(accounts),
    }
  }, [accounts, models])

  const saveConfig = async () => {
    const r = await adminFetch("/api/admin/geminigen/config", token, {
      method: "POST",
      body: JSON.stringify(config),
    })
    if (r?.ok) {
      toast.success("GeminiGen config saved")
      await load()
    } else {
      const d = await r?.json().catch(() => null)
      toast.error(d?.detail || "Could not save GeminiGen config")
    }
  }

  const openNew = () => {
    setDraft(EMPTY_ACCOUNT)
    setDialogOpen(true)
  }

  const openEdit = (account: GeminiGenAccount) => {
    setDraft({
      id: account.id,
      label: account.label,
      bearer_token: "",
      refresh_token: "",
      is_active: account.is_active,
      image_concurrency: String(account.image_concurrency ?? 5),
      video_concurrency: String(account.video_concurrency ?? 5),
    })
    setDialogOpen(true)
  }

  const saveAccount = async () => {
    if (!draft.id && !draft.bearer_token.trim()) {
      toast.error("Bearer token is required")
      return
    }
    setSaving(true)
    try {
      const payload = {
        label: draft.label.trim() || "GeminiGen account",
        bearer_token: draft.bearer_token.trim(),
        refresh_token: draft.refresh_token.trim(),
        is_active: draft.is_active,
        image_concurrency: Number(draft.image_concurrency) || 5,
        video_concurrency: Number(draft.video_concurrency) || 5,
      }
      const path = draft.id ? `/api/admin/geminigen/accounts/${draft.id}` : "/api/admin/geminigen/accounts"
      const r = await adminFetch(path, token, {
        method: draft.id ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      })
      if (!r?.ok) {
        const d = await r?.json().catch(() => null)
        throw new Error(d?.detail || "Could not save account")
      }
      toast.success(draft.id ? "GeminiGen account saved" : "GeminiGen account added")
      setDialogOpen(false)
      await load()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save account")
    } finally {
      setSaving(false)
    }
  }

  const setAccountEnabled = async (account: GeminiGenAccount, is_active: boolean) => {
    setUpdatingAccountIds((current) => new Set(current).add(account.id))
    try {
      const r = await adminFetch(`/api/admin/geminigen/accounts/${account.id}`, token, {
        method: "PATCH",
        body: JSON.stringify({ is_active }),
      })
      const d = await r?.json().catch(() => null)
      if (!r?.ok) throw new Error(d?.detail || "Could not update account")
      toast.success(is_active ? "GeminiGen account enabled" : "GeminiGen account disabled for new jobs")
      await Promise.all([load(), loadStatus()])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update account")
    } finally {
      setUpdatingAccountIds((current) => {
        const next = new Set(current)
        next.delete(account.id)
        return next
      })
    }
  }

  const testAccount = async (account: GeminiGenAccount) => {
    const r = await adminFetch(`/api/admin/geminigen/accounts/${account.id}/test`, token, { method: "POST" })
    const d = await r?.json().catch(() => null)
    if (r?.ok && d?.success) toast.success("GeminiGen account healthy")
    else toast.error(d?.error || d?.detail || "GeminiGen account test failed")
    await load()
  }

  const deleteAccount = async (account: GeminiGenAccount) => {
    if (!window.confirm(`Delete GeminiGen account "${account.label}"?`)) return
    const r = await adminFetch(`/api/admin/geminigen/accounts/${account.id}`, token, { method: "DELETE" })
    if (r?.ok) {
      const d = await r.json().catch(() => null)
      const cancelled = Number(d?.tasks_cleared || 0)
      toast.success(cancelled ? `GeminiGen account deleted; ${cancelled} active job(s) cancelled` : "GeminiGen account deleted")
      await Promise.all([load(), loadStatus()])
    } else {
      const d = await r?.json().catch(() => null)
      toast.error(d?.detail || "Could not delete account")
    }
  }

  const clearQueue = async () => {
    if (!window.confirm("Clear all GeminiGen queued/processing jobs and reset all GeminiGen slots?")) return
    const r = await adminFetch("/api/admin/geminigen/queue/clear", token, { method: "POST" })
    const d = await r?.json().catch(() => null)
    if (r?.ok && d?.success) {
      toast.success(`GeminiGen queue cleared: ${d.tasks_cleared ?? 0} task(s)`)
      await refreshAll()
    } else {
      toast.error(d?.detail || "Could not clear GeminiGen queue")
    }
  }

  const clearAccountSlots = async (account: GeminiGenAccount) => {
    if (!window.confirm(`Clear GeminiGen slots and active jobs for "${account.label}"?`)) return
    const r = await adminFetch(`/api/admin/geminigen/accounts/${account.id}/clear-slots`, token, { method: "POST" })
    const d = await r?.json().catch(() => null)
    if (r?.ok && d?.success) {
      toast.success(`GeminiGen slots cleared: ${d.tasks_cleared ?? 0} task(s)`)
      await refreshAll()
    } else {
      toast.error(d?.detail || "Could not clear GeminiGen slots")
    }
  }

  const refreshAll = async () => {
    await Promise.all([load(), loadStatus()])
  }

  const statusVariant = (bucket?: string) => {
    if (bucket === "operational") return "default"
    if (bucket === "degraded") return "secondary"
    if (bucket === "outage") return "destructive"
    return "outline"
  }

  const statusRows = Array.isArray(modelStatus?.models)
    ? modelStatus.models.filter((row) => (row.matching_local_model_count || 0) > 0)
    : []

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <CardTitle>GeminiGen Overview</CardTitle>
          <Button size="sm" variant="outline" onClick={refreshAll}>
            <RefreshCw className="h-4 w-4 mr-2" /> Refresh
          </Button>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <div className="text-xs text-muted-foreground">Accounts</div>
            <div className="text-lg font-semibold tabular-nums">{stats.accounts}</div>
          </div>
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <div className="text-xs text-muted-foreground">Active</div>
            <div className="text-lg font-semibold tabular-nums">{stats.active}</div>
          </div>
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <div className="text-xs text-muted-foreground">Models</div>
            <div className="text-lg font-semibold tabular-nums">{stats.models}</div>
          </div>
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <div className="text-xs text-muted-foreground">Imagen quota</div>
            <div className="text-lg font-semibold tabular-nums">{stats.imagenQuota}</div>
          </div>
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <div className="text-xs text-muted-foreground">Grok Image quota</div>
            <div className="text-lg font-semibold tabular-nums">{stats.grokImageQuota}</div>
          </div>
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <div className="text-xs text-muted-foreground">Video quotas</div>
            <div className="text-lg font-semibold leading-snug tabular-nums">{stats.videoQuota}</div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <div>
            <CardTitle>Model Status</CardTitle>
            {modelStatus?.generated_at ? (
              <div className="text-xs text-muted-foreground mt-1">Updated {modelStatus.generated_at}</div>
            ) : null}
          </div>
          <Button size="sm" variant="outline" onClick={loadStatus} disabled={statusLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${statusLoading ? "animate-spin" : ""}`} /> Refresh status
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">Operational</div>
              <div className="text-lg font-semibold tabular-nums">{modelStatus?.summary?.operational ?? 0}</div>
            </div>
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">Degraded</div>
              <div className="text-lg font-semibold tabular-nums">{modelStatus?.summary?.degraded ?? 0}</div>
            </div>
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">Outage</div>
              <div className="text-lg font-semibold tabular-nums">{modelStatus?.summary?.outage ?? 0}</div>
            </div>
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">Matched groups</div>
              <div className="text-lg font-semibold tabular-nums">{modelStatus?.summary?.matching_model_groups ?? 0}</div>
            </div>
          </div>
          {modelStatus?.error ? <div className="text-xs text-destructive">{modelStatus.error}</div> : null}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Provider model</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Success rate</TableHead>
                <TableHead>Local models</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {statusRows.length ? statusRows.map((row) => (
                <TableRow key={`${row.group_key}-${row.model_name}`}>
                  <TableCell>
                    <div className="font-medium">{row.model_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{row.group_key}</div>
                  </TableCell>
                  <TableCell>{row.type || "-"}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(row.status_bucket)}>{row.status || "Unknown"}</Badge>
                  </TableCell>
                  <TableCell className="tabular-nums">{row.success_rate == null ? "-" : `${row.success_rate}%`}</TableCell>
                  <TableCell className="tabular-nums">{row.matching_local_model_count}</TableCell>
                </TableRow>
              )) : (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-muted-foreground">
                    {statusLoading ? "Loading model status..." : "No GeminiGen model status available."}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>GeminiGen Config</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-5">
          <div className="flex items-center gap-2">
            <Switch checked={config.enabled} onCheckedChange={(enabled) => setConfig((c) => ({ ...c, enabled }))} />
            <Label>Enabled</Label>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={config.video_enabled} onCheckedChange={(video_enabled) => setConfig((c) => ({ ...c, video_enabled }))} />
            <Label>Video mode</Label>
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Base URL</Label>
            <Input value={config.base_url} onChange={(e) => setConfig((c) => ({ ...c, base_url: e.target.value }))} />
          </div>
          <div className="flex items-end">
            <Button onClick={saveConfig}>
              <Save className="h-4 w-4 mr-2" /> Save
            </Button>
          </div>
          <div className="space-y-2">
            <Label>Image poll seconds</Label>
            <Input type="number" value={config.poll_interval_image_sec} onChange={(e) => setConfig((c) => ({ ...c, poll_interval_image_sec: Number(e.target.value) || 3 }))} />
          </div>
          <div className="space-y-2">
            <Label>Video poll seconds</Label>
            <Input type="number" value={config.poll_interval_video_sec} onChange={(e) => setConfig((c) => ({ ...c, poll_interval_video_sec: Number(e.target.value) || 12 }))} />
          </div>
          <div className="space-y-2">
            <Label>Image timeout seconds</Label>
            <Input type="number" value={config.timeout_image_sec} onChange={(e) => setConfig((c) => ({ ...c, timeout_image_sec: Number(e.target.value) || 600 }))} />
          </div>
          <div className="space-y-2">
            <Label>Video timeout seconds</Label>
            <Input type="number" value={config.timeout_video_sec} onChange={(e) => setConfig((c) => ({ ...c, timeout_video_sec: Number(e.target.value) || 1800 }))} />
          </div>
          <div className="space-y-2">
            <Label>Global image concurrency</Label>
            <Input type="number" value={config.global_image_concurrency} onChange={(e) => setConfig((c) => ({ ...c, global_image_concurrency: Number(e.target.value) || 5 }))} />
          </div>
          <div className="space-y-2">
            <Label>Global video concurrency</Label>
            <Input type="number" value={config.global_video_concurrency} onChange={(e) => setConfig((c) => ({ ...c, global_video_concurrency: Number(e.target.value) || 5 }))} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <CardTitle>GeminiGen Accounts</CardTitle>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={clearQueue}>
              <RefreshCw className="h-4 w-4 mr-2" /> Clear GeminiGen Queue
            </Button>
            <Button size="sm" onClick={openNew}>
              <Plus className="h-4 w-4 mr-2" /> Add account
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Account</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Credits</TableHead>
                <TableHead>Plan</TableHead>
                <TableHead>Image</TableHead>
                <TableHead>Video</TableHead>
                <TableHead>Image quotas</TableHead>
                <TableHead>Video quota</TableHead>
                <TableHead>Benefits</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {accounts.length ? accounts.map((account) => (
                <TableRow key={account.id}>
                  <TableCell>
                    <div className="font-medium">{account.label}</div>
                    <div className="text-xs text-muted-foreground max-w-md truncate">
                      {account.profile_full_name || account.profile_email || "No profile synced"}
                    </div>
                    {account.profile_full_name && account.profile_email ? (
                      <div className="text-xs text-muted-foreground max-w-md truncate">{account.profile_email}</div>
                    ) : null}
                    {account.profile_sync_error || account.last_error ? (
                      <div className="text-xs text-destructive max-w-md truncate">{account.profile_sync_error || account.last_error}</div>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={account.is_active}
                        disabled={updatingAccountIds.has(account.id)}
                        onCheckedChange={(enabled) => void setAccountEnabled(account, enabled)}
                        aria-label={`${account.is_active ? "Disable" : "Enable"} ${account.label}`}
                      />
                      {!account.is_active ? (
                        <Badge variant="outline">disabled</Badge>
                      ) : account.profile_is_active === false ? (
                        <Badge variant="outline">inactive</Badge>
                      ) : (
                        <Badge>enabled</Badge>
                      )}
                    </div>
                    {account.image_gen_daily_limited || account.grok_image_daily_limited || account.video_daily_limited ? (
                      <div className="mt-2 flex flex-col items-start gap-1">
                        {account.image_gen_daily_limited ? (
                          <Badge variant="outline" className="border-amber-500/50 bg-amber-500/15 text-amber-800 dark:text-amber-300">
                            Imagen daily limit · {formatDailyLimitCountdown(account.image_gen_daily_limit_reset_at)}
                          </Badge>
                        ) : null}
                        {account.grok_image_daily_limited ? (
                          <Badge variant="outline" className="border-amber-500/50 bg-amber-500/15 text-amber-800 dark:text-amber-300">
                            Grok Image daily limit · {formatDailyLimitCountdown(account.grok_image_daily_limit_reset_at)}
                          </Badge>
                        ) : null}
                        {account.video_daily_limited ? (
                          <Badge variant="outline" className="border-amber-500/50 bg-amber-500/15 text-amber-800 dark:text-amber-300">
                            Video daily limit · {formatDailyLimitCountdown(account.video_daily_limit_reset_at)}
                          </Badge>
                        ) : null}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    <div className="font-medium tabular-nums">{formatNumberValue(account.available_credit)}</div>
                    <div className="text-muted-foreground tabular-nums">
                      Plan {formatNumberValue(account.plan_credit)}
                      {account.purchased_credit ? ` + ${account.purchased_credit}` : ""}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    <div className="font-medium">{account.plan_name || "-"}</div>
                    <div className="text-muted-foreground">{account.plan_expire_at ? `Exp ${formatCompactDateTime(account.plan_expire_at)}` : "-"}</div>
                  </TableCell>
                  <TableCell className="tabular-nums">{account.image_in_flight}/{account.image_concurrency}</TableCell>
                  <TableCell className="tabular-nums">{account.video_in_flight}/{account.video_concurrency}</TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    <div className="space-y-2" title={account.quota_sync_error || account.quota_sync_status || ""}>
                      <ImageQuotaLine label="Imagen" quota={account.image_gen_quota} />
                      <ImageQuotaLine label="Grok Image" quota={account.grok_image_quota} />
                      <div className="text-[10px] text-muted-foreground">
                        {account.quota_sync_status || "not synced"} · {formatCompactDateTime(account.quota_synced_at)}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">{geminiGenVideoQuota(account)}</TableCell>
                  <TableCell className="max-w-[220px] text-xs">
                    {account.active_benefits?.length ? (
                      <div className="space-y-1">
                        {account.active_benefits.slice(0, 2).map((benefit, index) => (
                          <div key={`${benefit.id ?? index}-${benefit.name ?? "benefit"}`} className="truncate" title={benefit.name || ""}>
                            {benefit.name || "Benefit"}
                            {benefit.expire_at ? <span className="text-muted-foreground"> - {formatCompactDateTime(benefit.expire_at)}</span> : null}
                          </div>
                        ))}
                        {account.active_benefits.length > 2 ? <div className="text-muted-foreground">+{account.active_benefits.length - 2} more</div> : null}
                        <div className="text-muted-foreground">Synced {formatCompactDateTime(account.profile_synced_at)}</div>
                      </div>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-2">
                      <Button size="icon" variant="ghost" onClick={() => testAccount(account)} title="Test">
                        <CheckCircle2 className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => clearAccountSlots(account)} title="Clear slots">
                        <RefreshCw className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => openEdit(account)} title="Edit">
                        <Edit3 className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => deleteAccount(account)} title="Delete">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              )) : (
                <TableRow>
                  <TableCell colSpan={10} className="text-center text-muted-foreground">No GeminiGen accounts configured.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{draft.id ? "Edit GeminiGen Account" : "Add GeminiGen Account"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>Label</Label>
                <Input value={draft.label} onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))} />
              </div>
              <div className="flex items-center gap-2 pt-7">
                <Switch checked={draft.is_active} onCheckedChange={(is_active) => setDraft((d) => ({ ...d, is_active }))} />
                <Label>Enabled</Label>
              </div>
              <div className="space-y-2">
                <Label>Image concurrency</Label>
                <Input type="number" value={draft.image_concurrency} onChange={(e) => setDraft((d) => ({ ...d, image_concurrency: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label>Video concurrency</Label>
                <Input type="number" value={draft.video_concurrency} onChange={(e) => setDraft((d) => ({ ...d, video_concurrency: e.target.value }))} />
              </div>
            </div>
            <div className="space-y-2">
              <Label>Bearer token {draft.id ? "(leave blank to keep current)" : "*"}</Label>
              <Input className="font-mono text-xs" type="password" value={draft.bearer_token} onChange={(e) => setDraft((d) => ({ ...d, bearer_token: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>Refresh token {draft.id ? "(leave blank to keep current)" : ""}</Label>
              <Input className="font-mono text-xs" type="password" value={draft.refresh_token} onChange={(e) => setDraft((d) => ({ ...d, refresh_token: e.target.value }))} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={saveAccount} disabled={saving}>{saving ? "Saving..." : "Save account"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
