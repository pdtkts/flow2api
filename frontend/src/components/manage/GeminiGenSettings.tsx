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
  base_url: string
  poll_interval_image_sec: number
  poll_interval_video_sec: number
  timeout_image_sec: number
  timeout_video_sec: number
  global_image_concurrency: number
  global_video_concurrency: number
  cache_outputs: boolean
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
  last_status: string
  last_error: string
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
  base_url: "https://api.geminigen.ai",
  poll_interval_image_sec: 3,
  poll_interval_video_sec: 12,
  timeout_image_sec: 600,
  timeout_video_sec: 1800,
  global_image_concurrency: 5,
  global_video_concurrency: 5,
  cache_outputs: true,
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

  const patchAccount = async (account: GeminiGenAccount, patch: Partial<GeminiGenAccount>) => {
    const r = await adminFetch(`/api/admin/geminigen/accounts/${account.id}`, token, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
    if (r?.ok) await load()
    else toast.error("Could not update account")
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
      toast.success("GeminiGen account deleted")
      await load()
    } else {
      toast.error("Could not delete account")
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
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="flex items-center gap-2">
            <Switch checked={config.enabled} onCheckedChange={(enabled) => setConfig((c) => ({ ...c, enabled }))} />
            <Label>Enabled</Label>
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
                <TableHead>Image</TableHead>
                <TableHead>Video</TableHead>
                <TableHead>Token</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {accounts.length ? accounts.map((account) => (
                <TableRow key={account.id}>
                  <TableCell>
                    <div className="font-medium">{account.label}</div>
                    {account.last_error ? <div className="text-xs text-destructive max-w-md truncate">{account.last_error}</div> : null}
                  </TableCell>
                  <TableCell>{account.is_active ? <Badge>enabled</Badge> : <Badge variant="outline">disabled</Badge>}</TableCell>
                  <TableCell className="tabular-nums">{account.image_in_flight}/{account.image_concurrency}</TableCell>
                  <TableCell className="tabular-nums">{account.video_in_flight}/{account.video_concurrency}</TableCell>
                  <TableCell className="font-mono text-xs">{account.bearer_token_preview || "***"}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-2">
                      <Button size="icon" variant="ghost" onClick={() => testAccount(account)} title="Test">
                        <CheckCircle2 className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => clearAccountSlots(account)} title="Clear slots">
                        <RefreshCw className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => patchAccount(account, { is_active: !account.is_active })} title="Toggle">
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
                  <TableCell colSpan={6} className="text-center text-muted-foreground">No GeminiGen accounts configured.</TableCell>
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
