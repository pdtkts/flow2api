import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Switch } from "../ui/switch"
import { Badge } from "../ui/badge"
import { toast } from "sonner"
import { Loader2, Plus, RefreshCw } from "lucide-react"

type TokenRow = { id: number; email?: string; is_active?: boolean }
type ManagedApiKey = {
  id: number
  client_name: string
  label: string
  key_prefix: string
  scopes: string
  is_active: boolean
  expires_at?: string | null
  last_used_at?: string | null
  created_at?: string | null
  account_ids: number[]
}
type AuditLog = {
  id: number
  api_key_id?: number | null
  key_prefix?: string | null
  label?: string | null
  endpoint: string
  account_id?: number | null
  status_code: number
  detail?: string
  ip?: string
  created_at?: string
}

type LimitRow = { endpoint: string; rpm: string; rph: string; burst: string }

export function ApiKeyManagement() {
  const { token } = useAuth()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [keys, setKeys] = useState<ManagedApiKey[]>([])
  const [tokens, setTokens] = useState<TokenRow[]>([])
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])

  const [createOpen, setCreateOpen] = useState(false)
  const [clientName, setClientName] = useState("")
  const [label, setLabel] = useState("default")
  const [scopes, setScopes] = useState("*")
  const [expiresAt, setExpiresAt] = useState("")
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([])
  const [limits, setLimits] = useState<LimitRow[]>([
    { endpoint: "/v1/chat/completions", rpm: "60", rph: "2000", burst: "10" },
  ])
  const [createdKey, setCreatedKey] = useState("")

  const loadAll = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const [k, t, a] = await Promise.all([
        adminJson<{ success?: boolean; keys?: ManagedApiKey[] }>("/api/admin/managed-apikeys", token),
        adminJson<TokenRow[]>("/api/tokens", token),
        adminJson<{ success?: boolean; logs?: AuditLog[] }>("/api/admin/managed-apikeys/audit?limit=80", token),
      ])
      if (k.ok && k.data?.keys) setKeys(k.data.keys)
      if (t.ok && Array.isArray(t.data)) setTokens(t.data)
      if (a.ok && a.data?.logs) setAuditLogs(a.data.logs)
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadAll()
    }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [loadAll])

  const toggleAccount = (id: number) => {
    setSelectedAccountIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const addLimitRow = () => {
    setLimits((prev) => [...prev, { endpoint: "", rpm: "0", rph: "0", burst: "0" }])
  }

  const updateLimitRow = (idx: number, patch: Partial<LimitRow>) => {
    setLimits((prev) => prev.map((row, i) => (i === idx ? { ...row, ...patch } : row)))
  }

  const buildLimitsPayload = () => {
    const out: Record<string, { rpm: number; rph: number; burst: number }> = {}
    for (const row of limits) {
      const endpoint = row.endpoint.trim()
      if (!endpoint) continue
      out[endpoint] = {
        rpm: Math.max(0, parseInt(row.rpm || "0", 10) || 0),
        rph: Math.max(0, parseInt(row.rph || "0", 10) || 0),
        burst: Math.max(0, parseInt(row.burst || "0", 10) || 0),
      }
    }
    return out
  }

  const createKey = async () => {
    if (!token) return
    if (!clientName.trim()) return toast.error("Client name is required")
    if (!selectedAccountIds.length) return toast.error("Select at least one account")

    setSaving(true)
    try {
      const res = await adminFetch("/api/admin/managed-apikeys", token, {
        method: "POST",
        body: JSON.stringify({
          client_name: clientName.trim(),
          label: label.trim() || "default",
          scopes: scopes.trim() || "*",
          account_ids: selectedAccountIds,
          endpoint_limits: buildLimitsPayload(),
          expires_at: expiresAt.trim() || null,
        }),
      })
      if (!res) return
      const data = await res.json()
      if (data.success && data.key?.api_key) {
        setCreatedKey(String(data.key.api_key))
        toast.success("Managed API key created")
        await loadAll()
      } else {
        toast.error(data.detail || data.message || "Create failed")
      }
    } finally {
      setSaving(false)
    }
  }

  const toggleKeyEnabled = async (key: ManagedApiKey) => {
    if (!token) return
    const res = await adminFetch(`/api/admin/managed-apikeys/${key.id}`, token, {
      method: "PUT",
      body: JSON.stringify({ is_active: !key.is_active }),
    })
    if (!res) return
    const data = await res.json()
    if (data.success) {
      toast.success(!key.is_active ? "Key enabled" : "Key disabled")
      await loadAll()
    } else {
      toast.error(data.detail || "Update failed")
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Managed API keys</CardTitle>
          <div className="flex items-center gap-2">
            <Button size="icon" variant="outline" onClick={() => loadAll()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-2" /> New key
            </Button>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Client</TableHead>
                <TableHead>Label</TableHead>
                <TableHead>Prefix</TableHead>
                <TableHead>Accounts</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!keys.length ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                    {loading ? "Loading..." : "No managed API keys"}
                  </TableCell>
                </TableRow>
              ) : (
                keys.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell>{k.client_name}</TableCell>
                    <TableCell>{k.label}</TableCell>
                    <TableCell className="font-mono text-xs">{k.key_prefix}</TableCell>
                    <TableCell>{k.account_ids.length}</TableCell>
                    <TableCell className="text-xs">{k.last_used_at || "-"}</TableCell>
                    <TableCell>
                      <Badge variant={k.is_active ? "default" : "secondary"}>{k.is_active ? "Active" : "Disabled"}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="inline-flex items-center gap-2">
                        <Switch checked={k.is_active} onCheckedChange={() => void toggleKeyEnabled(k)} />
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent key audit logs</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Endpoint</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!auditLogs.length ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-muted-foreground py-6">
                    No audit logs yet
                  </TableCell>
                </TableRow>
              ) : (
                auditLogs.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="text-xs">{log.created_at || "-"}</TableCell>
                    <TableCell className="font-mono text-xs">{log.key_prefix || "legacy/none"}</TableCell>
                    <TableCell className="text-xs">{log.endpoint}</TableCell>
                    <TableCell>{log.status_code}</TableCell>
                    <TableCell className="text-xs">{log.detail || ""}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Create managed API key</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Client name</Label>
                <Input className="mt-1" value={clientName} onChange={(e) => setClientName(e.target.value)} />
              </div>
              <div>
                <Label>Label</Label>
                <Input className="mt-1" value={label} onChange={(e) => setLabel(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Scopes (comma-separated)</Label>
                <Input className="mt-1 font-mono text-sm" value={scopes} onChange={(e) => setScopes(e.target.value)} />
              </div>
              <div>
                <Label>Expires at (optional)</Label>
                <Input className="mt-1" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} placeholder="YYYY-MM-DD HH:MM:SS" />
              </div>
            </div>

            <div className="space-y-2 border rounded-md p-3">
              <Label>Assign accounts (server-side routing pool)</Label>
              <div className="max-h-52 overflow-auto space-y-2">
                {tokens.map((t) => (
                  <label key={t.id} className="flex items-center justify-between gap-3 text-sm">
                    <span>
                      #{t.id} {t.email || ""}
                    </span>
                    <div className="flex items-center gap-2">
                      <Badge variant={t.is_active ? "default" : "secondary"}>{t.is_active ? "active" : "disabled"}</Badge>
                      <input type="checkbox" checked={selectedAccountIds.includes(t.id)} onChange={() => toggleAccount(t.id)} />
                    </div>
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-2 border rounded-md p-3">
              <div className="flex items-center justify-between">
                <Label>Per-endpoint limits</Label>
                <Button type="button" variant="outline" size="sm" onClick={addLimitRow}>
                  Add row
                </Button>
              </div>
              {limits.map((row, idx) => (
                <div key={idx} className="grid grid-cols-4 gap-2">
                  <Input placeholder="/v1/chat/completions" value={row.endpoint} onChange={(e) => updateLimitRow(idx, { endpoint: e.target.value })} />
                  <Input placeholder="rpm" value={row.rpm} onChange={(e) => updateLimitRow(idx, { rpm: e.target.value })} />
                  <Input placeholder="rph" value={row.rph} onChange={(e) => updateLimitRow(idx, { rph: e.target.value })} />
                  <Input placeholder="burst" value={row.burst} onChange={(e) => updateLimitRow(idx, { burst: e.target.value })} />
                </div>
              ))}
            </div>

            {createdKey ? (
              <div className="space-y-2 border rounded-md p-3">
                <Label>Generated API key (shown once)</Label>
                <Input readOnly className="font-mono text-xs" value={createdKey} />
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Close
            </Button>
            <Button onClick={createKey} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
