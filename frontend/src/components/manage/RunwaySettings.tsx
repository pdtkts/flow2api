import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Button } from "../ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Switch } from "../ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Textarea } from "../ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select"
import { Badge } from "../ui/badge"
import { toast } from "sonner"
import { CheckCircle2, Plus, RefreshCw, Trash2 } from "lucide-react"

type RunwayConfig = {
  enabled: boolean
  base_url: string
  poll_interval_sec: number
  timeout_sec: number
  cache_outputs: boolean
}

type RunwayAccount = {
  id: number
  label: string
  raw_credential: string
  is_active: boolean
  workspace_id: string
  team_id: string
  concurrency_limit: number
  in_flight: number
  last_status: string
  last_error: string
}

type RunwayModelKind = "image" | "video" | "audio" | "upscale"

type RunwayModel = {
  id: number
  public_model_id: string
  display_name: string
  kind: RunwayModelKind
  task_type: string
  builder_key: string
  default_options: string
  request_mapping: string
  capability_schema: string
  media_roles: string
  supported_modes: string
  limits: string
  feature_flags: string
  cost_feature: string
  source_version: string
  live_available: boolean
  disabled_reason: string
  last_synced_at?: string | null
  is_enabled: boolean
}

type RunwayAccountForm = {
  label: string
  raw_credential: string
  concurrency_limit: string
}

type RunwayModelForm = Omit<RunwayModel, "id">

const DEFAULT_CONFIG: RunwayConfig = {
  enabled: false,
  base_url: "https://api.runwayml.com/v1",
  poll_interval_sec: 3,
  timeout_sec: 600,
  cache_outputs: true,
}

const EMPTY_ACCOUNT: RunwayAccountForm = {
  label: "",
  raw_credential: "",
  concurrency_limit: "1",
}

const EMPTY_MODEL: RunwayModelForm = {
  public_model_id: "runway-",
  display_name: "",
  kind: "image",
  task_type: "",
  builder_key: "",
  default_options: "{}",
  request_mapping: "{}",
  capability_schema: "{}",
  media_roles: "[]",
  supported_modes: "[]",
  limits: "{}",
  feature_flags: "[]",
  cost_feature: "",
  source_version: "",
  live_available: true,
  disabled_reason: "",
  is_enabled: true,
}

export function RunwaySettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [config, setConfig] = useState<RunwayConfig>(DEFAULT_CONFIG)
  const [accounts, setAccounts] = useState<RunwayAccount[]>([])
  const [models, setModels] = useState<RunwayModel[]>([])
  const [newAccount, setNewAccount] = useState<RunwayAccountForm>(EMPTY_ACCOUNT)
  const [newModel, setNewModel] = useState<RunwayModelForm>(EMPTY_MODEL)

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{
      success?: boolean
      config?: Partial<RunwayConfig>
      accounts?: RunwayAccount[]
      models?: RunwayModel[]
    }>("/api/admin/runway/config", token)
    if (!resp.ok || !resp.data?.success) return
    setConfig({ ...DEFAULT_CONFIG, ...(resp.data.config || {}) })
    setAccounts(Array.isArray(resp.data.accounts) ? resp.data.accounts : [])
    setModels(Array.isArray(resp.data.models) ? resp.data.models : [])
  }, [token, active])

  useEffect(() => {
    void load()
  }, [load])

  const saveConfig = async () => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/runway/config", token, {
        method: "POST",
        body: JSON.stringify({
          enabled: config.enabled,
          base_url: config.base_url.trim(),
          poll_interval_sec: Number(config.poll_interval_sec) || 3,
          timeout_sec: Number(config.timeout_sec) || 600,
          cache_outputs: config.cache_outputs,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Runway config saved")
        await load()
      } else toast.error(d.detail || "Save failed")
    } finally {
      setBusy(false)
    }
  }

  const addAccount = async () => {
    if (!token) return
    if (!newAccount.raw_credential.trim()) return toast.error("Credential required")
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/runway/accounts", token, {
        method: "POST",
        body: JSON.stringify({
          label: newAccount.label.trim() || "Runway account",
          raw_credential: newAccount.raw_credential.trim(),
          is_active: true,
          concurrency_limit: Number(newAccount.concurrency_limit) || 1,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Runway account added")
        setNewAccount(EMPTY_ACCOUNT)
        await load()
      } else toast.error(d.detail || "Add failed")
    } finally {
      setBusy(false)
    }
  }

  const patchAccount = async (account: RunwayAccount, patch: Partial<RunwayAccount>) => {
    if (!token) return
    const r = await adminFetch(`/api/admin/runway/accounts/${account.id}`, token, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
    if (!r) return
    const d = await r.json()
    if (d.success) await load()
    else toast.error(d.detail || "Update failed")
  }

  const deleteAccount = async (account: RunwayAccount) => {
    if (!token) return
    if (!confirm(`Delete ${account.label || account.id}?`)) return
    const r = await adminFetch(`/api/admin/runway/accounts/${account.id}`, token, { method: "DELETE" })
    if (!r) return
    const d = await r.json()
    if (d.success) {
      toast.success("Account deleted")
      await load()
    } else toast.error(d.detail || "Delete failed")
  }

  const testAccount = async (account: RunwayAccount) => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch(`/api/admin/runway/accounts/${account.id}/test`, token, { method: "POST" })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Runway account healthy")
      else toast.error(d.error || "Runway account test failed")
      await load()
    } finally {
      setBusy(false)
    }
  }

  const saveModel = async (model: RunwayModel | RunwayModelForm, isNew = false) => {
    if (!token) return
    if (!model.public_model_id.trim().startsWith("runway-")) return toast.error("Model id must start with runway-")
    if (!model.task_type.trim()) return toast.error("Task type required")
    try {
      JSON.parse(model.default_options || "{}")
      JSON.parse(model.request_mapping || "{}")
      JSON.parse(model.capability_schema || "{}")
      JSON.parse(model.media_roles || "[]")
      JSON.parse(model.supported_modes || "[]")
      JSON.parse(model.limits || "{}")
      JSON.parse(model.feature_flags || "[]")
    } catch {
      return toast.error("Model JSON is invalid")
    }
    const url = isNew ? "/api/admin/runway/models" : `/api/admin/runway/models/${(model as RunwayModel).id}`
    const r = await adminFetch(url, token, {
      method: isNew ? "POST" : "PATCH",
      body: JSON.stringify(model),
    })
    if (!r) return
    const d = await r.json()
    if (d.success) {
      toast.success(isNew ? "Model added" : "Model saved")
      if (isNew) setNewModel(EMPTY_MODEL)
      await load()
    } else toast.error(d.detail || "Save failed")
  }

  const deleteModel = async (model: RunwayModel) => {
    if (!token) return
    if (!confirm(`Delete ${model.public_model_id}?`)) return
    const r = await adminFetch(`/api/admin/runway/models/${model.id}`, token, { method: "DELETE" })
    if (!r) return
    const d = await r.json()
    if (d.success) {
      toast.success("Model deleted")
      await load()
    } else toast.error(d.detail || "Delete failed")
  }

  const syncModels = async () => {
    if (!token) return
    const r = await adminFetch("/api/admin/runway/models/sync", token, { method: "POST" })
    if (!r) return
    const d = await r.json()
    if (d.success) {
      toast.success(`Synced ${d.synced || 0} model presets`)
      await load()
    } else toast.error(d.detail || "Sync failed")
  }

  if (!active) return null

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Runway</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <div className="flex items-center gap-3">
            <Switch checked={config.enabled} onCheckedChange={(enabled) => setConfig((c) => ({ ...c, enabled }))} />
            <Label>Enabled</Label>
          </div>
          <div className="space-y-2 xl:col-span-2">
            <Label>Base URL</Label>
            <Input value={config.base_url} onChange={(e) => setConfig((c) => ({ ...c, base_url: e.target.value }))} />
          </div>
          <div className="space-y-2">
            <Label>Poll seconds</Label>
            <Input
              type="number"
              min={1}
              value={config.poll_interval_sec}
              onChange={(e) => setConfig((c) => ({ ...c, poll_interval_sec: Number(e.target.value) || 3 }))}
            />
          </div>
          <div className="space-y-2">
            <Label>Timeout seconds</Label>
            <Input
              type="number"
              min={10}
              value={config.timeout_sec}
              onChange={(e) => setConfig((c) => ({ ...c, timeout_sec: Number(e.target.value) || 600 }))}
            />
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={config.cache_outputs} onCheckedChange={(cache_outputs) => setConfig((c) => ({ ...c, cache_outputs }))} />
            <Label>Cache outputs</Label>
          </div>
          <div className="md:col-span-2 xl:col-span-5">
            <Button onClick={saveConfig} disabled={busy}>Save Runway config</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Runway Accounts</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[180px_1fr_130px_auto]">
            <Input placeholder="Label" value={newAccount.label} onChange={(e) => setNewAccount((a) => ({ ...a, label: e.target.value }))} />
            <Input
              type="password"
              placeholder="JWT or cookie string"
              value={newAccount.raw_credential}
              onChange={(e) => setNewAccount((a) => ({ ...a, raw_credential: e.target.value }))}
            />
            <Input
              type="number"
              value={newAccount.concurrency_limit}
              onChange={(e) => setNewAccount((a) => ({ ...a, concurrency_limit: e.target.value }))}
            />
            <Button onClick={addAccount} disabled={busy}>
              <Plus className="h-4 w-4 mr-2" />
              Add
            </Button>
          </div>

          <div className="rounded-md border overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Label</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Workspace</TableHead>
                  <TableHead>Team</TableHead>
                  <TableHead>Limit</TableHead>
                  <TableHead className="w-[180px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((account) => (
                  <TableRow key={account.id}>
                    <TableCell>
                      <Input
                        value={account.label}
                        onChange={(e) => setAccounts((rows) => rows.map((row) => row.id === account.id ? { ...row, label: e.target.value } : row))}
                        onBlur={(e) => void patchAccount(account, { label: e.currentTarget.value })}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Switch checked={account.is_active} onCheckedChange={(is_active) => void patchAccount(account, { is_active })} />
                        <Badge variant={account.last_status === "failed" ? "destructive" : "secondary"}>{account.last_status || "new"}</Badge>
                      </div>
                      {account.last_error ? <div className="text-xs text-destructive mt-1 max-w-[260px] truncate" title={account.last_error}>{account.last_error}</div> : null}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{account.workspace_id || "-"}</TableCell>
                    <TableCell className="font-mono text-xs">{account.team_id || "-"}</TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        className="w-24"
                        value={account.concurrency_limit}
                        onChange={(e) => setAccounts((rows) => rows.map((row) => row.id === account.id ? { ...row, concurrency_limit: Number(e.target.value) || 1 } : row))}
                        onBlur={(e) => void patchAccount(account, { concurrency_limit: Number(e.currentTarget.value) || 1 })}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => void testAccount(account)} disabled={busy}>
                          <CheckCircle2 className="h-4 w-4" />
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => void deleteAccount(account)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Runway Models</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex justify-end">
            <Button variant="outline" onClick={syncModels}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Sync presets
            </Button>
          </div>

          <div className="grid gap-3 lg:grid-cols-[200px_180px_120px_160px_160px_1fr_auto]">
            <Input value={newModel.public_model_id} onChange={(e) => setNewModel((m) => ({ ...m, public_model_id: e.target.value }))} />
            <Input placeholder="Display name" value={newModel.display_name} onChange={(e) => setNewModel((m) => ({ ...m, display_name: e.target.value }))} />
            <Select value={newModel.kind} onValueChange={(kind) => setNewModel((m) => ({ ...m, kind: kind as RunwayModelKind }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="image">image</SelectItem>
                <SelectItem value="video">video</SelectItem>
                <SelectItem value="audio">audio</SelectItem>
                <SelectItem value="upscale">upscale</SelectItem>
              </SelectContent>
            </Select>
            <Input placeholder="taskType" value={newModel.task_type} onChange={(e) => setNewModel((m) => ({ ...m, task_type: e.target.value }))} />
            <Input placeholder="builder" value={newModel.builder_key} onChange={(e) => setNewModel((m) => ({ ...m, builder_key: e.target.value }))} />
            <Textarea className="font-mono text-xs min-h-[80px]" value={newModel.default_options} onChange={(e) => setNewModel((m) => ({ ...m, default_options: e.target.value }))} />
            <Textarea className="font-mono text-xs min-h-[80px]" value={newModel.request_mapping} onChange={(e) => setNewModel((m) => ({ ...m, request_mapping: e.target.value }))} />
            <Button onClick={() => void saveModel(newModel, true)}>Add</Button>
          </div>

          <div className="space-y-3">
            {models.map((model) => (
              <div key={model.id} className="rounded-md border p-3 space-y-3">
                <div className="grid gap-3 xl:grid-cols-[200px_180px_120px_160px_160px_1fr_130px]">
                <Input value={model.public_model_id} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, public_model_id: e.target.value } : row))} />
                <Input value={model.display_name} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, display_name: e.target.value } : row))} />
                <Select value={model.kind} onValueChange={(kind) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, kind: kind as RunwayModelKind } : row))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="image">image</SelectItem>
                    <SelectItem value="video">video</SelectItem>
                    <SelectItem value="audio">audio</SelectItem>
                    <SelectItem value="upscale">upscale</SelectItem>
                  </SelectContent>
                </Select>
                <Input value={model.task_type} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, task_type: e.target.value } : row))} />
                <Input value={model.builder_key} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, builder_key: e.target.value } : row))} />
                <Textarea className="font-mono text-xs min-h-[90px]" value={model.default_options} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, default_options: e.target.value } : row))} />
                <Textarea className="font-mono text-xs min-h-[90px]" value={model.request_mapping} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, request_mapping: e.target.value } : row))} />
                <div className="flex flex-col gap-2">
                  <Badge variant={model.live_available ? "secondary" : "destructive"}>
                    {model.live_available ? "available" : "blocked"}
                  </Badge>
                  <div className="flex items-center gap-2">
                    <Switch checked={model.is_enabled} onCheckedChange={(is_enabled) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, is_enabled } : row))} />
                    <Label>Enabled</Label>
                  </div>
                  <Button size="sm" onClick={() => void saveModel(model)}>Save</Button>
                  <Button size="sm" variant="outline" onClick={() => void deleteModel(model)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                </div>
                {model.disabled_reason ? <div className="text-xs text-destructive">{model.disabled_reason}</div> : null}
                <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-5">
                  <div className="space-y-1">
                    <Label>Schema</Label>
                    <Textarea className="font-mono text-xs min-h-[90px]" value={model.capability_schema} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, capability_schema: e.target.value } : row))} />
                  </div>
                  <div className="space-y-1">
                    <Label>Roles</Label>
                    <Textarea className="font-mono text-xs min-h-[90px]" value={model.media_roles} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, media_roles: e.target.value } : row))} />
                  </div>
                  <div className="space-y-1">
                    <Label>Modes</Label>
                    <Textarea className="font-mono text-xs min-h-[90px]" value={model.supported_modes} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, supported_modes: e.target.value } : row))} />
                  </div>
                  <div className="space-y-1">
                    <Label>Limits</Label>
                    <Textarea className="font-mono text-xs min-h-[90px]" value={model.limits} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, limits: e.target.value } : row))} />
                  </div>
                  <div className="space-y-1">
                    <Label>Feature Flags</Label>
                    <Textarea className="font-mono text-xs min-h-[90px]" value={model.feature_flags} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, feature_flags: e.target.value } : row))} />
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  <Input placeholder="Cost feature" value={model.cost_feature} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, cost_feature: e.target.value } : row))} />
                  <Input placeholder="Source version" value={model.source_version} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, source_version: e.target.value } : row))} />
                  <Input placeholder="Disabled reason" value={model.disabled_reason} onChange={(e) => setModels((rows) => rows.map((row) => row.id === model.id ? { ...row, disabled_reason: e.target.value } : row))} />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
