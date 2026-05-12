import { useState, useEffect, useCallback, useRef, type ReactNode } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import type { DashboardStats, TokenRow, ImportTokenItem } from "../../types/admin"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Switch } from "../ui/switch"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"
import { RefreshCw, Download, Upload, Plus, Loader2, RefreshCcw, Pencil, Trash2, FolderPlus, KeyRound, Copy, Unplug } from "lucide-react"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select"
import type {
  CreateProjectResponse,
  DedicatedExtensionWorkerRow,
  ListDedicatedWorkersResponse,
  CreateDedicatedWorkerResponse,
  DeleteDedicatedWorkerResponse,
  KillDedicatedWorkerSessionsResponse,
} from "../../types/admin"

function formatExpiryDisplay(atExpires: string | null | undefined): ReactNode {
  if (!atExpires) return <span className="text-muted-foreground">-</span>
  const d = new Date(atExpires)
  const now = new Date()
  const diff = d.getTime() - now.getTime()
  const dateStr = d.toLocaleDateString("en-US", { year: "numeric", month: "2-digit", day: "2-digit" })
  const timeStr = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })
  const title = `${dateStr} ${timeStr}`
  const hours = Math.floor(diff / 36e5)
  if (diff < 0)
    return (
      <span className="text-red-600 font-medium" title="Expired">
        Expired
      </span>
    )
  if (hours < 1)
    return (
      <span className="text-red-600 font-medium" title={title}>
        {Math.floor(diff / 6e4)}m
      </span>
    )
  if (hours < 24)
    return (
      <span className="text-orange-600 font-medium" title={title}>
        {hours}h
      </span>
    )
  const days = Math.floor(diff / 864e5)
  if (days < 7)
    return (
      <span className="text-orange-600" title={title}>
        {days}d
      </span>
    )
  return (
    <span className="text-muted-foreground" title={title}>
      {days}d
    </span>
  )
}

function asDedicatedWorkerBool(v: unknown, defaultTrue = true): boolean {
  if (v === undefined || v === null) return defaultTrue
  if (v === false || v === 0 || v === "0") return false
  return true
}

function accountTierBadge(tier: string | null | undefined) {
  if (!tier || tier === "PAYGATE_TIER_NOT_PAID")
    return <span className="inline-flex rounded px-2 py-0.5 text-xs bg-muted text-muted-foreground">Free</span>
  if (tier === "PAYGATE_TIER_ONE")
    return <span className="inline-flex rounded px-2 py-0.5 text-xs bg-blue-500/15 text-blue-700 dark:text-blue-400">Pro</span>
  if (tier === "PAYGATE_TIER_TWO")
    return <span className="inline-flex rounded px-2 py-0.5 text-xs bg-purple-500/15 text-purple-700 dark:text-purple-400">Ult</span>
  return (
    <span className="inline-flex rounded px-2 py-0.5 text-xs bg-amber-500/15 text-amber-800 dark:text-amber-300" title={tier}>
      {tier}
    </span>
  )
}

export function TokenManagement() {
  const { token } = useAuth()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [tokens, setTokens] = useState<TokenRow[]>([])
  const [loading, setLoading] = useState(false)
  const [atAutoRefresh, setAtAutoRefresh] = useState(true)

  const [addOpen, setAddOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const [addSt, setAddSt] = useState("")
  const [addRemark, setAddRemark] = useState("")
  const [addProjectId, setAddProjectId] = useState("")
  const [addProjectName, setAddProjectName] = useState("")
  const [addCaptchaProxy, setAddCaptchaProxy] = useState("")
  const [addImageEn, setAddImageEn] = useState(true)
  const [addVideoEn, setAddVideoEn] = useState(true)
  const [addImgConc, setAddImgConc] = useState("-1")
  const [addVidConc, setAddVidConc] = useState("-1")
  const [addPreviewAt, setAddPreviewAt] = useState("")

  const [editId, setEditId] = useState<number | null>(null)
  const [editSt, setEditSt] = useState("")
  const [editRemark, setEditRemark] = useState("")
  const [editProjectId, setEditProjectId] = useState("")
  const [editProjectName, setEditProjectName] = useState("")
  const [editCaptchaProxy, setEditCaptchaProxy] = useState("")
  const [editImageEn, setEditImageEn] = useState(true)
  const [editVideoEn, setEditVideoEn] = useState(true)
  const [editImgConc, setEditImgConc] = useState("")
  const [editVidConc, setEditVidConc] = useState("")
  const [editPreviewAt, setEditPreviewAt] = useState("")
  const [editProfileSaving, setEditProfileSaving] = useState(false)

  const [importFile, setImportFile] = useState<File | null>(null)

  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const [newProjectTokenId, setNewProjectTokenId] = useState<string>("")
  const [newProjectTitle, setNewProjectTitle] = useState("")
  const [newProjectSetCurrent, setNewProjectSetCurrent] = useState(true)
  const [newProjectSaving, setNewProjectSaving] = useState(false)

  const [workerKeyOpen, setWorkerKeyOpen] = useState(false)
  const [workerKeyToken, setWorkerKeyToken] = useState<TokenRow | null>(null)
  const [workerKeyLabel, setWorkerKeyLabel] = useState("")
  const [workerKeyRouteKey, setWorkerKeyRouteKey] = useState("")
  const [workerKeySaving, setWorkerKeySaving] = useState(false)
  const [workerKeyGenerated, setWorkerKeyGenerated] = useState<string | null>(null)
  const [workerKeyList, setWorkerKeyList] = useState<DedicatedExtensionWorkerRow[]>([])
  const [workerKeyListLoading, setWorkerKeyListLoading] = useState(false)
  const [workerKeyDeletingId, setWorkerKeyDeletingId] = useState<number | null>(null)
  const [workerKeyAllowCaptcha, setWorkerKeyAllowCaptcha] = useState(true)
  const [workerKeyAllowSessionRefresh, setWorkerKeyAllowSessionRefresh] = useState(true)
  const [workerRowDrafts, setWorkerRowDrafts] = useState<
    Record<number, { label: string; allow_captcha: boolean; allow_session_refresh: boolean }>
  >({})
  const [workerRowSavingId, setWorkerRowSavingId] = useState<number | null>(null)
  const [workerKeyKillAllBusy, setWorkerKeyKillAllBusy] = useState(false)
  const workerRowDraftsRef = useRef(workerRowDrafts)
  workerRowDraftsRef.current = workerRowDrafts

  const loadStats = useCallback(async () => {
    if (!token) return
    const { ok, data } = await adminJson<DashboardStats>("/api/stats", token)
    if (ok && data) setStats(data)
  }, [token])

  const loadTokens = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const r = await adminFetch("/api/tokens", token)
      if (!r?.ok) throw new Error("fetch failed")
      const data = (await r.json()) as TokenRow[]
      setTokens(Array.isArray(data) ? data : [])
    } catch {
      toast.error("Failed to load tokens")
    } finally {
      setLoading(false)
    }
  }, [token])

  const loadAtRefreshConfig = useCallback(async () => {
    if (!token) return
    const { ok, data } = await adminJson<{ success?: boolean; config?: { at_auto_refresh_enabled?: boolean } }>(
      "/api/token-refresh/config",
      token
    )
    if (ok && data?.config) setAtAutoRefresh(!!data.config.at_auto_refresh_enabled)
  }, [token])

  useEffect(() => {
    loadStats()
    loadTokens()
    loadAtRefreshConfig()
  }, [loadStats, loadTokens, loadAtRefreshConfig])

  const refreshAll = async () => {
    await loadStats()
    await loadTokens()
  }

  const onToggleAtAutoRefresh = async (enabled: boolean) => {
    if (!token) return
    const prev = atAutoRefresh
    setAtAutoRefresh(enabled)
    const r = await adminFetch("/api/token-refresh/enabled", token, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    })
    if (!r) return
    const d = await r.json().catch(() => ({}))
    if (d.success) toast.success(typeof d.message === "string" ? d.message : enabled ? "AT auto-refresh enabled" : "Updated")
    else {
      setAtAutoRefresh(prev)
      toast.error(d.detail || d.message || "Failed")
    }
  }

  const exportTokens = () => {
    if (!tokens.length) {
      toast.error("No tokens to export")
      return
    }
    const exportData = tokens.map((t) => ({
      email: t.email,
      access_token: t.token ?? t.at,
      session_token: t.st ?? null,
      is_active: t.is_active,
      captcha_proxy_url: t.captcha_proxy_url || "",
      image_enabled: t.image_enabled !== false,
      video_enabled: t.video_enabled !== false,
      image_concurrency: t.image_concurrency ?? -1,
      video_concurrency: t.video_concurrency ?? -1,
    }))
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `tokens_${new Date().toISOString().split("T")[0]}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(`Exported ${tokens.length} token(s)`)
  }

  const st2at = async (st: string, which: "add" | "edit") => {
    if (!token || !st.trim()) {
      toast.error("Enter Session Token first")
      return
    }
    toast.info("Converting ST→AT…")
    const r = await adminFetch("/api/tokens/st2at", token, {
      method: "POST",
      body: JSON.stringify({ st: st.trim() }),
    })
    if (!r) return
    const d = await r.json().catch(() => ({}))
    if (d.success && d.access_token) {
      if (which === "add") setAddPreviewAt(d.access_token)
      else setEditPreviewAt(d.access_token)
      toast.success("Converted — AT shown below for reference")
    } else toast.error(d.detail || d.message || "Conversion failed")
  }

  const submitAdd = async () => {
    if (!token) return
    const st = addSt.trim()
    if (!st) {
      toast.error("Session Token is required")
      return
    }
    setSaving(true)
    try {
      const r = await adminFetch("/api/tokens", token, {
        method: "POST",
        body: JSON.stringify({
          st,
          remark: addRemark.trim() || null,
          project_id: addProjectId.trim() || null,
          project_name: addProjectName.trim() || null,
          captcha_proxy_url: addCaptchaProxy.trim() || null,
          image_enabled: addImageEn,
          video_enabled: addVideoEn,
          image_concurrency: parseInt(addImgConc, 10) || -1,
          video_concurrency: parseInt(addVidConc, 10) || -1,
        }),
      })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        toast.success("Token added")
        setAddOpen(false)
        setAddSt("")
        setAddRemark("")
        setAddProjectId("")
        setAddProjectName("")
        setAddCaptchaProxy("")
        setAddImageEn(true)
        setAddVideoEn(true)
        setAddImgConc("-1")
        setAddVidConc("-1")
        setAddPreviewAt("")
        await refreshAll()
      } else toast.error(d.detail || d.message || "Add failed")
    } finally {
      setSaving(false)
    }
  }

  const openEdit = (row: TokenRow) => {
    setEditId(row.id)
    setEditSt(row.st || "")
    setEditRemark(row.remark || "")
    setEditProjectId(row.current_project_id || "")
    setEditProjectName(row.current_project_name || "")
    setEditCaptchaProxy(row.captcha_proxy_url || "")
    setEditImageEn(row.image_enabled !== false)
    setEditVideoEn(row.video_enabled !== false)
    setEditImgConc(String(row.image_concurrency ?? -1))
    setEditVidConc(String(row.video_concurrency ?? -1))
    setEditPreviewAt("")
    setEditOpen(true)
  }

  const submitEdit = async () => {
    if (!token || editId == null) return
    const st = editSt.trim()
    if (!st) {
      toast.error("Session Token is required")
      return
    }
    setSaving(true)
    try {
      const r = await adminFetch(`/api/tokens/${editId}`, token, {
        method: "PUT",
        body: JSON.stringify({
          st,
          remark: editRemark.trim() || null,
          project_id: editProjectId.trim() || null,
          project_name: editProjectName.trim() || null,
          captcha_proxy_url: editCaptchaProxy.trim() || null,
          image_enabled: editImageEn,
          video_enabled: editVideoEn,
          image_concurrency: editImgConc ? parseInt(editImgConc, 10) : null,
          video_concurrency: editVidConc ? parseInt(editVidConc, 10) : null,
        }),
      })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        toast.success("Token updated")
        setEditOpen(false)
        await refreshAll()
      } else toast.error(d.detail || d.message || "Update failed")
    } finally {
      setSaving(false)
    }
  }

  const refreshEditEmail = async () => {
    if (!token || editId == null) return
    const st = editSt.trim()
    if (!st) {
      toast.error("Session Token is required")
      return
    }
    setEditProfileSaving(true)
    try {
      const r = await adminFetch(`/api/tokens/${editId}/refresh-profile`, token, {
        method: "POST",
        body: JSON.stringify({ st }),
      })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        const updatedEmail = d.token?.email || "updated"
        toast.success(`Email updated: ${updatedEmail}`)
        await loadTokens()
      } else {
        toast.error(d.detail || d.message || "Update email failed")
      }
    } finally {
      setEditProfileSaving(false)
    }
  }

  const submitImport = async () => {
    if (!token || !importFile) {
      toast.error("Choose a JSON file")
      return
    }
    setSaving(true)
    try {
      const text = await importFile.text()
      const importData = JSON.parse(text) as ImportTokenItem[]
      if (!Array.isArray(importData)) {
        toast.error("JSON must be an array")
        return
      }
      if (!importData.length) {
        toast.error("JSON array is empty")
        return
      }
      const r = await adminFetch("/api/tokens/import", token, {
        method: "POST",
        body: JSON.stringify({ tokens: importData }),
      })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        toast.success(`Imported: added ${d.added ?? 0}, updated ${d.updated ?? 0}`)
        if (d.errors?.length) toast.warning(`${d.errors.length} row(s) reported errors`)
        setImportOpen(false)
        setImportFile(null)
        await refreshAll()
      } else toast.error(d.detail || d.message || "Import failed")
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Invalid JSON")
    } finally {
      setSaving(false)
    }
  }

  const refreshCredits = async (id: number) => {
    if (!token) return
    toast.info("Refreshing credits…")
    const r = await adminFetch(`/api/tokens/${id}/refresh-credits`, token, { method: "POST" })
    if (!r) return
    const d = await r.json().catch(() => ({}))
    if (d.success) {
      toast.success(`Credits: ${d.credits}`)
      await loadTokens()
    } else toast.error(d.detail || "Refresh failed")
  }

  const refreshAt = async (id: number) => {
    if (!token) return
    toast.info("Refreshing AT…")
    const r = await adminFetch(`/api/tokens/${id}/refresh-at`, token, { method: "POST" })
    if (!r) return
    const d = await r.json().catch(() => ({}))
    if (d.success) {
      const exp = d.token?.at_expires ? new Date(d.token.at_expires).toLocaleString("en-US") : "unknown"
      toast.success(`AT updated. Expires: ${exp}`)
      await refreshAll()
    } else toast.error(d.detail || "Refresh AT failed")
  }

  const toggleActive = async (id: number, isActive: boolean) => {
    if (!token) return
    const action = isActive ? "disable" : "enable"
    const r = await adminFetch(`/api/tokens/${id}/${action}`, token, { method: "POST" })
    if (!r) return
    const d = await r.json().catch(() => ({}))
    if (d.success) {
      toast.success(isActive ? "Token disabled" : "Token enabled")
      await loadTokens()
    } else toast.error("Toggle failed")
  }

  const deleteTok = async (id: number) => {
    if (!confirm("Delete this token?")) return
    if (!token) return
    const r = await adminFetch(`/api/tokens/${id}`, token, { method: "DELETE" })
    if (!r) return
    const d = await r.json().catch(() => ({}))
    if (d.success) {
      toast.success("Deleted")
      await refreshAll()
    } else toast.error("Delete failed")
  }

  const copyProjectId = async (pid: string) => {
    try {
      await navigator.clipboard.writeText(pid)
      toast.success("Project ID copied")
    } catch {
      toast.error("Copy failed")
    }
  }

  const loadDedicatedWorkersForToken = async (tid: number) => {
    if (!token) return
    setWorkerKeyListLoading(true)
    try {
      const { ok, data } = await adminJson<ListDedicatedWorkersResponse>("/api/admin/dedicated-extension/workers", token)
      if (!ok || !data?.workers) {
        setWorkerKeyList([])
        setWorkerRowDrafts({})
        return
      }
      const filtered = data.workers.filter((w) => w.token_id === tid)
      setWorkerKeyList(filtered)
      const drafts: Record<number, { label: string; allow_captcha: boolean; allow_session_refresh: boolean }> = {}
      for (const w of filtered) {
        drafts[w.id] = {
          label: (w.label ?? "").trim(),
          allow_captcha: asDedicatedWorkerBool(w.allow_captcha),
          allow_session_refresh: asDedicatedWorkerBool(w.allow_session_refresh),
        }
      }
      setWorkerRowDrafts(drafts)
    } catch {
      setWorkerKeyList([])
    } finally {
      setWorkerKeyListLoading(false)
    }
  }

  const openWorkerKeyDialog = (t: TokenRow) => {
    setWorkerKeyToken(t)
    const baseLabel = (t.remark || t.email || `token-${t.id}`).trim()
    setWorkerKeyLabel(baseLabel ? `Worker: ${baseLabel}` : `Worker: ${t.id}`)
    setWorkerKeyRouteKey((t.extension_route_key || "").trim())
    setWorkerKeyGenerated(null)
    setWorkerKeyAllowCaptcha(true)
    setWorkerKeyAllowSessionRefresh(true)
    setWorkerKeyOpen(true)
    void loadDedicatedWorkersForToken(t.id)
  }

  const closeWorkerKeyDialog = (open: boolean) => {
    setWorkerKeyOpen(open)
    if (!open) {
      setWorkerKeyToken(null)
      setWorkerKeyGenerated(null)
      setWorkerKeyList([])
      setWorkerRowDrafts({})
      setWorkerKeyDeletingId(null)
      setWorkerRowSavingId(null)
      setWorkerKeyKillAllBusy(false)
    }
  }

  const deleteDedicatedWorkerForToken = async (workerId: number) => {
    if (!token || !workerKeyToken) return
    if (
      !confirm(
        "Delete this worker registration key? It cannot be undone. Any extension using this key must be given a new key."
      )
    ) {
      return
    }
    setWorkerKeyDeletingId(workerId)
    try {
      const { ok, status, data } = await adminJson<DeleteDedicatedWorkerResponse>(
        `/api/admin/dedicated-extension/workers/${workerId}`,
        token,
        { method: "DELETE" }
      )
      if (ok && data?.success) {
        toast.success("Worker key deleted")
        await loadDedicatedWorkersForToken(workerKeyToken.id)
      } else {
        const d = data as DeleteDedicatedWorkerResponse & { detail?: unknown }
        const detail = d?.detail
        const msg =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail) && detail[0] && typeof (detail[0] as { msg?: string }).msg === "string"
              ? (detail[0] as { msg: string }).msg
              : `Failed (${status})`
        toast.error(msg)
      }
    } finally {
      setWorkerKeyDeletingId(null)
    }
  }

  const copyWorkerRegistrationKey = async () => {
    if (!workerKeyGenerated) return
    try {
      await navigator.clipboard.writeText(workerKeyGenerated)
      toast.success("Worker registration key copied")
    } catch {
      toast.error("Copy failed")
    }
  }

  const saveDedicatedWorkerRow = async (workerId: number) => {
    if (!token || !workerKeyToken) return
    const draft = workerRowDraftsRef.current[workerId]
    if (!draft) return
    if (!draft.allow_captcha && !draft.allow_session_refresh) {
      toast.error("At least one of Captcha or Refresh AT/ST must stay enabled")
      return
    }
    const labelStr = typeof draft.label === "string" ? draft.label.trim() : String(draft.label ?? "").trim()
    setWorkerRowSavingId(workerId)
    try {
      const { ok, status, data } = await adminJson<{ success?: boolean; detail?: string | unknown[] }>(
        `/api/admin/dedicated-extension/workers/${workerId}`,
        token,
        {
          method: "PATCH",
          body: JSON.stringify({
            label: labelStr || `Worker: ${workerId}`,
            allow_captcha: !!draft.allow_captcha,
            allow_session_refresh: !!draft.allow_session_refresh,
          }),
        }
      )
      if (ok && data?.success) {
        toast.success("Worker updated — reconnect extension to apply capability changes.")
        await loadDedicatedWorkersForToken(workerKeyToken.id)
      } else {
        let msg = `Failed (${status})`
        const d = data?.detail
        if (typeof d === "string") msg = d
        else if (Array.isArray(d) && d[0] && typeof (d[0] as { msg?: string }).msg === "string")
          msg = (d[0] as { msg: string }).msg
        toast.error(msg)
      }
    } finally {
      setWorkerRowSavingId(null)
    }
  }

  const copyWorkerRowPlaintext = async (secret: string) => {
    const t = String(secret || "").trim()
    if (!t) return
    try {
      await navigator.clipboard.writeText(t)
      toast.success("Full registration key copied")
    } catch {
      toast.error("Copy failed")
    }
  }

  const copyWorkerFullKeyOrExplain = (w: DedicatedExtensionWorkerRow) => {
    const full = String(w.worker_key_plaintext || "").trim()
    if (!full) {
      toast.error(
        "No full secret is stored for this worker (older key). Use “Generate registration key” below — new keys are saved so you can copy them anytime."
      )
      return
    }
    void copyWorkerRowPlaintext(full)
  }

  const killAllDedicatedWorkerSessions = async () => {
    if (!token || !workerKeyToken) return
    if (
      !confirm(
        "Terminate every active Worker-mode extension connection for this token? Each browser using a dedicated registration key for this token will disconnect (they may reconnect automatically)."
      )
    ) {
      return
    }
    setWorkerKeyKillAllBusy(true)
    try {
      const { ok, data } = await adminJson<KillDedicatedWorkerSessionsResponse>(
        "/api/admin/dedicated-extension/workers/kill-sessions",
        token,
        { method: "POST", body: JSON.stringify({ token_id: workerKeyToken.id }) }
      )
      if (ok && data?.success) {
        toast.success(
          typeof data.message === "string" ? data.message : `${data.killed_count ?? 0} session(s) closed`
        )
      } else {
        const d = data as KillDedicatedWorkerSessionsResponse & { detail?: unknown }
        const detail = d?.detail
        const msg =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail) && detail[0] && typeof (detail[0] as { msg?: string }).msg === "string"
              ? (detail[0] as { msg: string }).msg
              : "Kill sessions failed"
        toast.error(msg)
      }
    } finally {
      setWorkerKeyKillAllBusy(false)
    }
  }

  const generateDedicatedWorkerKey = async () => {
    if (!token || !workerKeyToken) return
    if (!workerKeyAllowCaptcha && !workerKeyAllowSessionRefresh) {
      toast.error("Enable at least one of Captcha or Refresh AT/ST")
      return
    }
    setWorkerKeySaving(true)
    setWorkerKeyGenerated(null)
    try {
      const body: {
        label: string
        token_id: number
        route_key?: string | null
        allow_captcha: boolean
        allow_session_refresh: boolean
      } = {
        label: workerKeyLabel.trim() || `Worker: ${workerKeyToken.id}`,
        token_id: workerKeyToken.id,
        allow_captcha: workerKeyAllowCaptcha,
        allow_session_refresh: workerKeyAllowSessionRefresh,
      }
      const rk = workerKeyRouteKey.trim()
      if (rk) body.route_key = rk
      const { ok, status, data } = await adminJson<CreateDedicatedWorkerResponse>("/api/admin/dedicated-extension/workers", token, {
        method: "POST",
        body: JSON.stringify(body),
      })
      if (ok && data?.success && data.worker_registration_key) {
        setWorkerKeyGenerated(data.worker_registration_key)
        toast.success("Registration key created — copy it now; it will not be shown again.")
        await loadDedicatedWorkersForToken(workerKeyToken.id)
      } else {
        const msg =
          (data as { detail?: string })?.detail ||
          (typeof (data as { message?: string })?.message === "string" ? (data as { message?: string }).message : null) ||
          `Failed (${status})`
        toast.error(msg)
      }
    } finally {
      setWorkerKeySaving(false)
    }
  }

  const openNewProject = () => {
    const first = tokens[0]
    setNewProjectTokenId(first ? String(first.id) : "")
    setNewProjectTitle("")
    setNewProjectSetCurrent(true)
    setNewProjectOpen(true)
  }

  const submitNewProject = async () => {
    if (!token) return
    const tid = newProjectTokenId.trim()
    if (!tid) {
      toast.error("Select a token")
      return
    }
    setNewProjectSaving(true)
    try {
      const r = await adminFetch(`/api/tokens/${tid}/projects`, token, {
        method: "POST",
        body: JSON.stringify({
          title: newProjectTitle.trim() || null,
          set_as_current: newProjectSetCurrent,
        }),
      })
      if (!r) return
      const d = (await r.json().catch(() => ({}))) as CreateProjectResponse & { detail?: string }
      if (r.ok && d.success) {
        const name = d.project?.project_name || "Project"
        const pid = d.project?.project_id || ""
        toast.success(pid ? `Created: ${name} (${pid.slice(0, 8)}…)` : `Created: ${name}`)
        setNewProjectOpen(false)
        await refreshAll()
      } else {
        const err = d.detail || (d as { message?: string }).message || "Create failed"
        toast.error(typeof err === "string" ? err : "Create failed")
      }
    } finally {
      setNewProjectSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 grid-cols-2 md:grid-cols-5">
        <Card>
          <CardContent className="p-4">
            <p className="text-sm font-medium text-muted-foreground mb-2">Total Tokens</p>
            <h3 className="text-xl font-bold">{stats?.total_tokens ?? "—"}</h3>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm font-medium text-muted-foreground mb-2">Active Tokens</p>
            <h3 className="text-xl font-bold text-green-600">{stats?.active_tokens ?? "—"}</h3>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm font-medium text-muted-foreground mb-2">Today / Total Images</p>
            <h3 className="text-xl font-bold text-blue-600">
              {(stats?.today_images ?? 0)}/{(stats?.total_images ?? 0)}
            </h3>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm font-medium text-muted-foreground mb-2">Today / Total Videos</p>
            <h3 className="text-xl font-bold text-purple-600">
              {(stats?.today_videos ?? 0)}/{(stats?.total_videos ?? 0)}
            </h3>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm font-medium text-muted-foreground mb-2">Today / Total Errors</p>
            <h3 className="text-xl font-bold text-destructive">
              {(stats?.today_errors ?? 0)}/{(stats?.total_errors ?? 0)}
            </h3>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-4 border-b">
          <CardTitle className="text-lg font-semibold">Token list</CardTitle>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2" title="When AT expires in &lt;1h, refresh from ST (server policy)">
              <span className="text-xs text-muted-foreground">Auto refresh AT</span>
              <Switch checked={atAutoRefresh} onCheckedChange={onToggleAtAutoRefresh} />
            </div>
            <Button size="icon" variant="outline" onClick={() => refreshAll()} disabled={loading} title="Refresh">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
            <Button size="sm" variant="default" className="bg-blue-600 hover:bg-blue-700" onClick={exportTokens}>
              <Download className="h-4 w-4 mr-2" /> Export
            </Button>
            <Button size="sm" variant="default" className="bg-green-600 hover:bg-green-700" onClick={() => setImportOpen(true)}>
              <Upload className="h-4 w-4 mr-2" /> Import
            </Button>
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="h-4 w-4 mr-2" /> Add
            </Button>
            <Button size="sm" variant="outline" onClick={openNewProject} disabled={!tokens.length} title="Create a VideoFX project for a token">
              <FolderPlus className="h-4 w-4 mr-2" /> New project
            </Button>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="w-full overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16 text-center">ID</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-center">Status</TableHead>
                  <TableHead className="text-center">Expires</TableHead>
                  <TableHead className="text-center">Credits</TableHead>
                  <TableHead className="text-center">Tier</TableHead>
                  <TableHead className="text-center">Project ID</TableHead>
                  <TableHead className="text-center">Images</TableHead>
                  <TableHead className="text-center">Videos</TableHead>
                  <TableHead className="text-center">Errors</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {!tokens.length ? (
                  <TableRow>
                    <TableCell colSpan={11} className="text-center text-muted-foreground py-8">
                      {loading ? "Loading…" : "No tokens"}
                    </TableCell>
                  </TableRow>
                ) : (
                  tokens.map((t) => {
                    const imgDisp = t.image_enabled ? String(t.image_count ?? 0) : "—"
                    const vidDisp = t.video_enabled ? String(t.video_count ?? 0) : "—"
                    const pid = t.current_project_id || ""
                    const shortPid = pid.length > 8 ? `${pid.slice(0, 8)}…` : pid || "—"
                    return (
                      <TableRow key={t.id}>
                        <TableCell className="text-center font-medium">{t.id}</TableCell>
                        <TableCell className="font-medium max-w-[180px] truncate" title={t.email || ""}>
                          {t.email || "—"}
                        </TableCell>
                        <TableCell className="text-center">
                          <span
                            className={`inline-flex rounded px-2 py-0.5 text-xs ${
                              t.is_active ? "bg-green-500/15 text-green-700 dark:text-green-400" : "bg-muted text-muted-foreground"
                            }`}
                          >
                            {t.is_active ? "Active" : "Disabled"}
                          </span>
                        </TableCell>
                        <TableCell className="text-center text-xs whitespace-nowrap">{formatExpiryDisplay(t.at_expires)}</TableCell>
                        <TableCell className="text-center">
                          <Button variant="ghost" size="sm" className="h-8 gap-1" onClick={() => refreshCredits(t.id)} title="Refresh credits">
                            <span>{t.credits !== undefined && t.credits !== null ? t.credits : "—"}</span>
                            <RefreshCcw className="h-3 w-3" />
                          </Button>
                        </TableCell>
                        <TableCell className="text-center">{accountTierBadge(t.user_paygate_tier)}</TableCell>
                        <TableCell className="text-center">
                          {pid ? (
                            <Button variant="outline" size="sm" className="h-7 text-[10px] font-mono px-2" onClick={() => copyProjectId(pid)} title={pid}>
                              {shortPid}
                            </Button>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-center text-sm">{imgDisp}</TableCell>
                        <TableCell className="text-center text-sm">{vidDisp}</TableCell>
                        <TableCell className={`text-center text-sm ${(t.error_count ?? 0) > 0 ? "text-red-600" : ""}`}>{t.error_count ?? 0}</TableCell>
                        <TableCell className="text-right whitespace-nowrap">
                          <div className="flex justify-end gap-1 flex-wrap">
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => refreshAt(t.id)}>
                              Refresh AT
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => openWorkerKeyDialog(t)}
                              title="Create Chrome extension worker registration key for this account"
                            >
                              <KeyRound className="h-3 w-3 mr-1" />
                              Worker key
                            </Button>
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => openEdit(t)}>
                              <Pencil className="h-3 w-3 mr-1" />
                              Edit
                            </Button>
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => toggleActive(t.id, t.is_active)}>
                              {t.is_active ? "Disable" : "Enable"}
                            </Button>
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-destructive" onClick={() => deleteTok(t.id)}>
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Add token</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Session Token (ST) *</Label>
              <Textarea className="font-mono text-sm mt-1" rows={3} value={addSt} onChange={(e) => setAddSt(e.target.value)} placeholder="Session token" />
            </div>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={() => st2at(addSt, "add")}>
                Convert ST→AT (preview)
              </Button>
            </div>
            {addPreviewAt ? (
              <div>
                <Label>Converted AT (reference only)</Label>
                <Textarea readOnly className="font-mono text-xs mt-1" rows={2} value={addPreviewAt} />
              </div>
            ) : null}
            <div>
              <Label>Remark</Label>
              <Input className="mt-1" value={addRemark} onChange={(e) => setAddRemark(e.target.value)} />
            </div>
            <div>
              <Label>Project ID</Label>
              <Input className="mt-1 font-mono text-sm" value={addProjectId} onChange={(e) => setAddProjectId(e.target.value)} />
            </div>
            <div>
              <Label>Project name</Label>
              <Input className="mt-1" value={addProjectName} onChange={(e) => setAddProjectName(e.target.value)} />
            </div>
            <div>
              <Label>Captcha proxy URL</Label>
              <Input className="mt-1 font-mono text-sm" value={addCaptchaProxy} onChange={(e) => setAddCaptchaProxy(e.target.value)} />
            </div>
            <div className="flex flex-col gap-3 border-t pt-3">
              <div className="flex items-center gap-3">
                <Switch checked={addImageEn} onCheckedChange={setAddImageEn} />
                <Label className="!mt-0">Image generation</Label>
                <Input className="w-20 h-8" value={addImgConc} onChange={(e) => setAddImgConc(e.target.value)} title="Concurrency, -1 = unlimited" />
              </div>
              <div className="flex items-center gap-3">
                <Switch checked={addVideoEn} onCheckedChange={setAddVideoEn} />
                <Label className="!mt-0">Video generation</Label>
                <Input className="w-20 h-8" value={addVidConc} onChange={(e) => setAddVidConc(e.target.value)} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitAdd} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Add"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit token</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Session Token (ST) *</Label>
              <Textarea className="font-mono text-sm mt-1" rows={3} value={editSt} onChange={(e) => setEditSt(e.target.value)} />
            </div>
            <Button type="button" variant="secondary" size="sm" onClick={() => st2at(editSt, "edit")}>
              Convert ST→AT (preview)
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={refreshEditEmail} disabled={editProfileSaving}>
              {editProfileSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Update Email"}
            </Button>
            {editPreviewAt ? (
              <div>
                <Label>Converted AT (reference only)</Label>
                <Textarea readOnly className="font-mono text-xs mt-1" rows={2} value={editPreviewAt} />
              </div>
            ) : null}
            <div>
              <Label>Remark</Label>
              <Input className="mt-1" value={editRemark} onChange={(e) => setEditRemark(e.target.value)} />
            </div>
            <div>
              <Label>Project ID</Label>
              <Input className="mt-1 font-mono text-sm" value={editProjectId} onChange={(e) => setEditProjectId(e.target.value)} />
            </div>
            <div>
              <Label>Project name</Label>
              <Input className="mt-1" value={editProjectName} onChange={(e) => setEditProjectName(e.target.value)} />
            </div>
            <div>
              <Label>Captcha proxy URL</Label>
              <Input className="mt-1 font-mono text-sm" value={editCaptchaProxy} onChange={(e) => setEditCaptchaProxy(e.target.value)} />
            </div>
            <div className="flex flex-col gap-3 border-t pt-3">
              <div className="flex items-center gap-3">
                <Switch checked={editImageEn} onCheckedChange={setEditImageEn} />
                <Label className="!mt-0">Image generation</Label>
                <Input className="w-20 h-8" value={editImgConc} onChange={(e) => setEditImgConc(e.target.value)} />
              </div>
              <div className="flex items-center gap-3">
                <Switch checked={editVideoEn} onCheckedChange={setEditVideoEn} />
                <Label className="!mt-0">Video generation</Label>
                <Input className="w-20 h-8" value={editVidConc} onChange={(e) => setEditVidConc(e.target.value)} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitEdit} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={newProjectOpen} onOpenChange={setNewProjectOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Token</Label>
              <Select value={newProjectTokenId} onValueChange={setNewProjectTokenId}>
                <SelectTrigger className="mt-1 w-full">
                  <SelectValue placeholder="Select account" />
                </SelectTrigger>
                <SelectContent>
                  {tokens.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.email || `Token #${t.id}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Project title (optional)</Label>
              <Input
                className="mt-1"
                value={newProjectTitle}
                onChange={(e) => setNewProjectTitle(e.target.value)}
                placeholder="Leave empty for auto name (e.g. … P3)"
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={newProjectSetCurrent} onCheckedChange={setNewProjectSetCurrent} />
              <Label className="!mt-0">Set as current project for this token</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNewProjectOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitNewProject} disabled={newProjectSaving || !newProjectTokenId}>
              {newProjectSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={workerKeyOpen} onOpenChange={closeWorkerKeyDialog}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Extension worker key</DialogTitle>
            <DialogDescription>
              For token #{workerKeyToken?.id}
              {workerKeyToken?.email ? ` (${workerKeyToken.email})` : ""}. Paste the generated key into the Chrome extension
              <strong className="font-medium"> Worker</strong> tab. The full secret is shown only once — store it safely. The list
              below shows the public key id; when the server has stored the registration secret (new keys), you can view and copy it here. Older keys only have the prefix until you generate a replacement.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Worker label</Label>
              <Input className="mt-1" value={workerKeyLabel} onChange={(e) => setWorkerKeyLabel(e.target.value)} placeholder="e.g. office PC" />
            </div>
            <div>
              <Label>Extension route key (optional)</Label>
              <Input
                className="mt-1 font-mono text-sm"
                value={workerKeyRouteKey}
                onChange={(e) => setWorkerKeyRouteKey(e.target.value)}
                placeholder="Leave empty if you only use dedicated worker binding"
              />
            </div>
            <div className="rounded-md border bg-muted/40 p-3 space-y-2">
              <p className="text-sm font-medium text-foreground">Capabilities for new key</p>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border border-input"
                  checked={workerKeyAllowCaptcha}
                  onChange={(e) => setWorkerKeyAllowCaptcha(e.target.checked)}
                />
                <span>Captcha (reCAPTCHA / get_token)</span>
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border border-input"
                  checked={workerKeyAllowSessionRefresh}
                  onChange={(e) => setWorkerKeyAllowSessionRefresh(e.target.checked)}
                />
                <span>Refresh AT/ST (session token via extension)</span>
              </label>
              <p className="text-xs text-muted-foreground">At least one must stay checked. Use captcha-only on untrusted machines.</p>
            </div>
            <div className="rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <p className="font-medium text-foreground">Workers already linked to this token</p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-[11px] gap-1 border-destructive/60 text-destructive hover:bg-destructive/10"
                  disabled={
                    !workerKeyToken ||
                    workerKeyKillAllBusy ||
                    workerKeyListLoading ||
                    workerRowSavingId !== null ||
                    workerKeyDeletingId !== null
                  }
                  title="Disconnect all extension clients using dedicated registration keys for this token"
                  onClick={() => void killAllDedicatedWorkerSessions()}
                >
                  {workerKeyKillAllBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Unplug className="h-3 w-3" />}
                  Kill all sessions
                </Button>
              </div>
              {workerKeyListLoading ? (
                <p className="text-xs">Loading…</p>
              ) : workerKeyList.length === 0 ? (
                <p className="text-xs">None yet. Generate a key below.</p>
              ) : (
                <ul className="space-y-3 text-xs">
                  {workerKeyList.map((w) => {
                    const draft = workerRowDrafts[w.id]
                    if (!draft) return null
                    return (
                      <li key={w.id} className="rounded border border-border/60 bg-background p-3 space-y-2">
                        <div className="flex flex-wrap gap-2 justify-between items-start">
                          <div className="space-y-1 min-w-0 flex-1">
                            <Label className="text-[11px] text-muted-foreground">Name</Label>
                            <Input
                              className="h-8 text-sm font-normal"
                              value={draft.label}
                              onChange={(e) =>
                                setWorkerRowDrafts((prev) => ({
                                  ...prev,
                                  [w.id]: { ...draft, label: e.target.value },
                                }))
                              }
                            />
                            <p className="font-mono text-[11px] text-muted-foreground break-all pt-1">
                              Key id: {w.worker_key_prefix}
                              {!w.is_active ? " · inactive" : ""}
                              {w.last_seen_at ? ` · last seen ${w.last_seen_at}` : ""}
                            </p>
                            <div className="pt-2 space-y-1">
                              <Label className="text-[11px] text-muted-foreground">Registration key (full secret)</Label>
                              <div className="flex gap-2">
                                <Textarea
                                  readOnly
                                  className="font-mono text-[11px] min-h-[56px] py-1.5"
                                  value={String(w.worker_key_plaintext || "").trim()}
                                  placeholder="Not stored for this worker — generate a new key below"
                                />
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="icon"
                                  className="h-8 w-8 shrink-0 mt-0.5"
                                  title={
                                    String(w.worker_key_plaintext || "").trim()
                                      ? "Copy full registration key"
                                      : "No full key on file — tap for instructions"
                                  }
                                  onClick={() => copyWorkerFullKeyOrExplain(w)}
                                >
                                  <Copy className="h-3.5 w-3.5" />
                                </Button>
                              </div>
                            </div>
                          </div>
                          <div className="flex flex-col gap-1 shrink-0">
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              className="h-8 text-[11px]"
                              disabled={
                                workerRowSavingId !== null || workerKeyDeletingId !== null || workerKeyListLoading
                              }
                              onClick={() => void saveDedicatedWorkerRow(w.id)}
                            >
                              {workerRowSavingId === w.id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="destructive"
                              className="h-8 gap-1 text-[11px]"
                              disabled={workerKeyDeletingId !== null || workerKeyListLoading}
                              onClick={() => void deleteDedicatedWorkerForToken(w.id)}
                            >
                              {workerKeyDeletingId === w.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                              Delete
                            </Button>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-4 pt-1">
                          <label className="flex items-center gap-2 text-xs cursor-pointer">
                            <input
                              type="checkbox"
                              className="h-3.5 w-3.5 rounded border border-input"
                              checked={draft.allow_captcha}
                              onChange={(e) =>
                                setWorkerRowDrafts((prev) => ({
                                  ...prev,
                                  [w.id]: { ...draft, allow_captcha: e.target.checked },
                                }))
                              }
                            />
                            Captcha
                          </label>
                          <label className="flex items-center gap-2 text-xs cursor-pointer">
                            <input
                              type="checkbox"
                              className="h-3.5 w-3.5 rounded border border-input"
                              checked={draft.allow_session_refresh}
                              onChange={(e) =>
                                setWorkerRowDrafts((prev) => ({
                                  ...prev,
                                  [w.id]: { ...draft, allow_session_refresh: e.target.checked },
                                }))
                              }
                            />
                            Refresh AT/ST
                          </label>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
            {workerKeyGenerated ? (
              <div className="space-y-2">
                <Label>Worker registration key (copy now)</Label>
                <div className="flex gap-2">
                  <Textarea readOnly className="font-mono text-xs min-h-[72px]" value={workerKeyGenerated} />
                  <Button type="button" variant="outline" size="icon" className="shrink-0" onClick={() => void copyWorkerRegistrationKey()} title="Copy">
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
          <DialogFooter className="flex-col sm:flex-row gap-2">
            <Button type="button" variant="secondary" onClick={() => workerKeyToken && void loadDedicatedWorkersForToken(workerKeyToken.id)} disabled={workerKeyListLoading}>
              Refresh list
            </Button>
            <Button type="button" onClick={() => void generateDedicatedWorkerKey()} disabled={workerKeySaving || !workerKeyToken}>
              {workerKeySaving ? <Loader2 className="h-4 w-4 animate-spin" /> : workerKeyGenerated ? "Generate another key" : "Generate registration key"}
            </Button>
            <Button variant="outline" onClick={() => closeWorkerKeyDialog(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import tokens</DialogTitle>
          </DialogHeader>
          <div>
            <Label>JSON file</Label>
            <Input className="mt-1" type="file" accept=".json,application/json" onChange={(e) => setImportFile(e.target.files?.[0] ?? null)} />
            <p className="text-xs text-muted-foreground mt-2">Array of objects with session_token (required per row). Existing emails are updated.</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setImportOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitImport} disabled={saving || !importFile}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Import"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
