import { useState, useEffect, useCallback, type ReactNode } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import type { DashboardStats, TokenRow, ImportTokenItem } from "../../types/admin"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Switch } from "../ui/switch"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"
import { RefreshCw, Download, Upload, Plus, Loader2, RefreshCcw, Pencil, Trash2, ExternalLink } from "lucide-react"

type GeminiGenAccountSummary = {
  id: number
  label: string
  bearer_token_preview?: string
  is_active: boolean
  image_concurrency: number
  video_concurrency: number
  image_in_flight: number
  video_in_flight: number
  image_generated_today?: number
  image_generated_total?: number
  video_generated_today?: number
  video_generated_total?: number
  last_status?: string
  last_error?: string
  last_used_at?: string | null
  profile_email?: string | null
  profile_full_name?: string | null
  profile_is_active?: boolean | null
  available_credit?: number | null
  plan_credit?: number | null
  purchased_credit?: number | null
  locked_credit?: number | null
  subscription_credit?: number | null
  plan_name?: string | null
  plan_expire_at?: string | null
  active_benefits?: Array<{ id?: number | null; name?: string; expire_at?: string | null; estimated_remaining?: number | null }>
  remaining_bulk_videos?: number | null
  remaining_daily_videos?: number | null
  remaining_grok_max_daily_videos?: number | null
  remaining_grok_max_daily_720p_videos?: number | null
  remaining_grok_max_daily_10s_videos?: number | null
  profile_synced_at?: string | null
  profile_sync_status?: string
  profile_sync_error?: string
}

type GeminiGenConfigResponse = {
  success?: boolean
  config?: { enabled?: boolean }
  accounts?: GeminiGenAccountSummary[]
}

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

function geminiGenVideoQuota(account: GeminiGenAccountSummary) {
  const quotas = [
    account.remaining_daily_videos !== null && account.remaining_daily_videos !== undefined ? `Daily ${account.remaining_daily_videos}` : "",
    account.remaining_bulk_videos !== null && account.remaining_bulk_videos !== undefined ? `Bulk ${account.remaining_bulk_videos}` : "",
    account.remaining_grok_max_daily_videos !== null && account.remaining_grok_max_daily_videos !== undefined
      ? `Grok ${account.remaining_grok_max_daily_videos}`
      : "",
  ].filter(Boolean)
  return quotas.length ? quotas.join(" / ") : "-"
}

function buildNoVncUrl() {
  const configured = String(import.meta.env.VITE_NOVNC_URL || "").trim()
  if (configured) return configured
  if (typeof window === "undefined") return "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"
  return `http://${window.location.hostname}:6080/vnc.html?autoconnect=1&resize=scale`
}

export function TokenManagement() {
  const { token } = useAuth()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [tokens, setTokens] = useState<TokenRow[]>([])
  const [geminiGenAccounts, setGeminiGenAccounts] = useState<GeminiGenAccountSummary[]>([])
  const [geminiGenConfigured, setGeminiGenConfigured] = useState(false)
  const [geminiGenLoading, setGeminiGenLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [atAutoRefresh, setAtAutoRefresh] = useState(true)
  const [protocolAutoRefresh, setProtocolAutoRefresh] = useState(true)
  const [protocolRefreshInterval, setProtocolRefreshInterval] = useState("120")

  const [addOpen, setAddOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const [addSt, setAddSt] = useState("")
  const [addRemark, setAddRemark] = useState("")
  const [addCaptchaProxy, setAddCaptchaProxy] = useState("")
  const [addImageEn, setAddImageEn] = useState(true)
  const [addVideoEn, setAddVideoEn] = useState(true)
  const [addImgConc, setAddImgConc] = useState("-1")
  const [addVidConc, setAddVidConc] = useState("-1")
  const [addPreviewAt, setAddPreviewAt] = useState("")
  const [addProtocolMode, setAddProtocolMode] = useState(false)
  const [addGoogleCookies, setAddGoogleCookies] = useState("")
  const [addLoginAccount, setAddLoginAccount] = useState("")
  const [addLoginPassword, setAddLoginPassword] = useState("")
  const [addProtocolProxy, setAddProtocolProxy] = useState("")
  const [addRefreshInterval, setAddRefreshInterval] = useState("120")

  const [editId, setEditId] = useState<number | null>(null)
  const [editSt, setEditSt] = useState("")
  const [editRemark, setEditRemark] = useState("")
  const [editCaptchaProxy, setEditCaptchaProxy] = useState("")
  const [editImageEn, setEditImageEn] = useState(true)
  const [editVideoEn, setEditVideoEn] = useState(true)
  const [editImgConc, setEditImgConc] = useState("")
  const [editVidConc, setEditVidConc] = useState("")
  const [editPreviewAt, setEditPreviewAt] = useState("")
  const [editUseExtensionGen, setEditUseExtensionGen] = useState(true)
  const [editProfileSaving, setEditProfileSaving] = useState(false)
  const [editProtocolMode, setEditProtocolMode] = useState(false)
  const [editGoogleCookies, setEditGoogleCookies] = useState("")
  const [editHasGoogleCookies, setEditHasGoogleCookies] = useState(false)
  const [editLoginAccount, setEditLoginAccount] = useState("")
  const [editLoginPassword, setEditLoginPassword] = useState("")
  const [editProtocolProxy, setEditProtocolProxy] = useState("")
  const [editRefreshInterval, setEditRefreshInterval] = useState("120")

  const [importFile, setImportFile] = useState<File | null>(null)
  const [profileBusyId, setProfileBusyId] = useState<number | "new" | null>(null)

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
    const { ok, data } = await adminJson<{
      success?: boolean
      config?: {
        at_auto_refresh_enabled?: boolean
        protocol_refresh_enabled?: boolean
        refresh_interval_minutes?: number
      }
    }>(
      "/api/token-refresh/config",
      token
    )
    if (ok && data?.config) {
      setAtAutoRefresh(!!data.config.at_auto_refresh_enabled)
      setProtocolAutoRefresh(data.config.protocol_refresh_enabled !== false)
      setProtocolRefreshInterval(String(data.config.refresh_interval_minutes ?? 120))
    }
  }, [token])

  const loadGeminiGenAccounts = useCallback(async () => {
    if (!token) return
    setGeminiGenLoading(true)
    try {
      const { ok, data } = await adminJson<GeminiGenConfigResponse>("/api/admin/geminigen/config", token)
      if (ok && data?.success) {
        const accounts = Array.isArray(data.accounts) ? data.accounts : []
        setGeminiGenAccounts(accounts)
        setGeminiGenConfigured(!!data.config?.enabled || accounts.length > 0)
      } else {
        setGeminiGenAccounts([])
        setGeminiGenConfigured(false)
      }
    } catch {
      setGeminiGenAccounts([])
      setGeminiGenConfigured(false)
    } finally {
      setGeminiGenLoading(false)
    }
  }, [token])

  useEffect(() => {
    loadStats()
    loadTokens()
    loadAtRefreshConfig()
    loadGeminiGenAccounts()
  }, [loadStats, loadTokens, loadAtRefreshConfig, loadGeminiGenAccounts])

  useEffect(() => {
    const refreshRuntimeState = () => loadTokens()
    window.addEventListener("focus", refreshRuntimeState)
    return () => window.removeEventListener("focus", refreshRuntimeState)
  }, [loadTokens])

  const refreshAll = async () => {
    await loadStats()
    await loadTokens()
    await loadGeminiGenAccounts()
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

  const updateProtocolRefreshConfig = async (enabled: boolean, intervalValue: string) => {
    if (!token) return
    const interval = Math.max(1, Math.min(10080, Number.parseInt(intervalValue, 10) || 120))
    const previousEnabled = protocolAutoRefresh
    const previousInterval = protocolRefreshInterval
    setProtocolAutoRefresh(enabled)
    setProtocolRefreshInterval(String(interval))
    const { ok, data } = await adminJson<{
      success?: boolean
      config?: { protocol_refresh_enabled?: boolean; refresh_interval_minutes?: number }
      detail?: string
      message?: string
    }>("/api/token-refresh/config", token, {
      method: "POST",
      body: JSON.stringify({ enabled, refresh_interval_minutes: interval }),
    })
    if (ok && data?.success) {
      setProtocolAutoRefresh(data.config?.protocol_refresh_enabled !== false)
      setProtocolRefreshInterval(String(data.config?.refresh_interval_minutes ?? interval))
      toast.success("Protocol ST refresh settings updated")
      return
    }
    setProtocolAutoRefresh(previousEnabled)
    setProtocolRefreshInterval(previousInterval)
    toast.error(data?.detail || data?.message || "Failed to update protocol refresh settings")
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
      protocol_mode: t.protocol_mode || "session",
      google_cookies: t.google_cookies || "",
      login_account: t.login_account || "",
      login_password: t.login_password || "",
      proxy_url: t.proxy_url || "",
      auto_refresh_enabled: t.auto_refresh_enabled !== false,
      refresh_interval_minutes: t.refresh_interval_minutes || 120,
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
          captcha_proxy_url: addCaptchaProxy.trim() || null,
          image_enabled: addImageEn,
          video_enabled: addVideoEn,
          image_concurrency: parseInt(addImgConc, 10) || -1,
          video_concurrency: parseInt(addVidConc, 10) || -1,
          protocol_mode: addProtocolMode ? "protocol" : "session",
          google_cookies: addProtocolMode ? addGoogleCookies.trim() || null : null,
          login_account: addProtocolMode ? addLoginAccount.trim() || null : null,
          login_password: addProtocolMode ? addLoginPassword || null : null,
          proxy_url: addProtocolMode ? addProtocolProxy.trim() || null : null,
          auto_refresh_enabled: addProtocolMode,
          refresh_interval_minutes: Math.max(1, parseInt(addRefreshInterval, 10) || 120),
        }),
      })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        toast.success("Token added")
        setAddOpen(false)
        setAddSt("")
        setAddRemark("")
        setAddCaptchaProxy("")
        setAddImageEn(true)
        setAddVideoEn(true)
        setAddImgConc("-1")
        setAddVidConc("-1")
        setAddPreviewAt("")
        setAddProtocolMode(false)
        setAddGoogleCookies("")
        setAddLoginAccount("")
        setAddLoginPassword("")
        setAddProtocolProxy("")
        setAddRefreshInterval("120")
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
    setEditCaptchaProxy(row.captcha_proxy_url || "")
    setEditImageEn(row.image_enabled !== false)
    setEditVideoEn(row.video_enabled !== false)
    setEditImgConc(String(row.image_concurrency ?? -1))
    setEditVidConc(String(row.video_concurrency ?? -1))
    setEditPreviewAt("")
    setEditUseExtensionGen(
      !(row.use_extension_for_generation === false || row.use_extension_for_generation === 0)
    )
    setEditProtocolMode(row.protocol_mode === "protocol")
    setEditGoogleCookies("")
    setEditHasGoogleCookies(!!row.has_google_cookies)
    setEditLoginAccount(row.login_account || "")
    setEditLoginPassword(row.login_password || "")
    setEditProtocolProxy(row.proxy_url || "")
    setEditRefreshInterval(String(row.refresh_interval_minutes || 120))
    setEditOpen(true)
  }

  const submitEdit = async () => {
    if (!token || editId == null) return
    const st = editSt.trim()
    const selected = tokens.find((t) => t.id === editId)
    if (!st && selected?.auth_mode !== "browser_profile") {
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
          captcha_proxy_url: editCaptchaProxy.trim() || null,
          image_enabled: editImageEn,
          video_enabled: editVideoEn,
          image_concurrency: editImgConc ? parseInt(editImgConc, 10) : null,
          video_concurrency: editVidConc ? parseInt(editVidConc, 10) : null,
          use_extension_for_generation: editUseExtensionGen,
          protocol_mode: editProtocolMode ? "protocol" : "session",
          google_cookies: editGoogleCookies.trim() || null,
          login_account: editProtocolMode ? editLoginAccount.trim() || null : null,
          login_password: editProtocolMode ? editLoginPassword || null : null,
          proxy_url: editProtocolProxy.trim() || null,
          auto_refresh_enabled: editProtocolMode,
          refresh_interval_minutes: Math.max(1, parseInt(editRefreshInterval, 10) || 120),
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

  const createBrowserProfileAccount = async () => {
    if (!token) return
    setProfileBusyId("new")
    try {
      const r = await adminFetch("/api/tokens/browser-profile", token, {
        method: "POST",
        body: JSON.stringify({
          image_enabled: true,
          video_enabled: true,
          image_concurrency: -1,
          video_concurrency: -1,
        }),
      })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (r.ok && d.success) {
        toast.success("Browser profile account created")
        await refreshAll()
      } else toast.error(d.detail || d.message || "Create profile failed")
    } finally {
      setProfileBusyId(null)
    }
  }

  const browserProfileAction = async (id: number, action: "open" | "close" | "sync" | "refresh" | "reset") => {
    if (!token) return
    if (action === "reset" && !confirm("Reset this browser profile? You will need to log in again.")) return
    setProfileBusyId(id)
    try {
      const r = await adminFetch(`/api/tokens/${id}/browser-profile/${action}`, token, { method: "POST" })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (r.ok && d.success) {
        const labels = {
          open: "Profile opened in Fluxbox",
          close: "Profile browser closed",
          sync: "Profile synced",
          refresh: "Profile refreshed",
          reset: "Profile reset",
        }
        toast.success(labels[action])
        await refreshAll()
      } else {
        toast.error(d.detail || d.message || `${action} failed`)
      }
    } finally {
      setProfileBusyId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
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
            <p className="text-sm font-medium text-muted-foreground mb-2">Today / Total Metadata</p>
            <h3 className="text-xl font-bold text-cyan-600">
              {(stats?.today_metadata ?? 0)}/{(stats?.total_metadata ?? 0)}
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

      {geminiGenConfigured ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-4 border-b">
            <CardTitle className="text-lg font-semibold">GeminiGen accounts</CardTitle>
            <Button size="icon" variant="outline" onClick={loadGeminiGenAccounts} disabled={geminiGenLoading} title="Refresh GeminiGen accounts">
              <RefreshCw className={`h-4 w-4 ${geminiGenLoading ? "animate-spin" : ""}`} />
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <div className="w-full overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Account</TableHead>
                    <TableHead className="text-center">Status</TableHead>
                    <TableHead>Credits</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead className="text-center">Image slots</TableHead>
                    <TableHead className="text-center">Video slots</TableHead>
                    <TableHead className="text-center">Image generated</TableHead>
                    <TableHead className="text-center">Video generated</TableHead>
                    <TableHead>Video quota</TableHead>
                    <TableHead>Benefits</TableHead>
                    <TableHead>Last used</TableHead>
                    <TableHead>Profile sync</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {!geminiGenAccounts.length ? (
                    <TableRow>
                      <TableCell colSpan={12} className="text-center text-muted-foreground py-8">
                        {geminiGenLoading ? "Loading..." : "No GeminiGen accounts configured"}
                      </TableCell>
                    </TableRow>
                  ) : (
                    geminiGenAccounts.map((account) => (
                      <TableRow key={account.id}>
                        <TableCell className="max-w-[240px]" title={account.profile_email || account.label || ""}>
                          <div className="font-medium truncate">{account.label || `Account ${account.id}`}</div>
                          <div className="text-xs text-muted-foreground truncate">
                            {account.profile_full_name || account.profile_email || "No profile synced"}
                          </div>
                          {account.profile_full_name && account.profile_email ? (
                            <div className="text-xs text-muted-foreground truncate">{account.profile_email}</div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-center">
                          <span
                            className={`inline-flex rounded px-2 py-0.5 text-xs ${
                              account.is_active && account.profile_is_active !== false
                                ? "bg-green-500/15 text-green-700 dark:text-green-400"
                                : "bg-muted text-muted-foreground"
                            }`}
                          >
                            {!account.is_active ? "Disabled" : account.profile_is_active === false ? "Inactive" : "Active"}
                          </span>
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
                        <TableCell className="text-center tabular-nums">
                          {account.image_in_flight ?? 0}/{account.image_concurrency ?? 0}
                        </TableCell>
                        <TableCell className="text-center tabular-nums">
                          {account.video_in_flight ?? 0}/{account.video_concurrency ?? 0}
                        </TableCell>
                        <TableCell className="text-center tabular-nums">
                          {formatNumberValue(account.image_generated_today ?? 0)}/{formatNumberValue(account.image_generated_total ?? 0)}
                        </TableCell>
                        <TableCell className="text-center tabular-nums">
                          {formatNumberValue(account.video_generated_today ?? 0)}/{formatNumberValue(account.video_generated_total ?? 0)}
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
                            </div>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </TableCell>
                        <TableCell className="text-xs whitespace-nowrap">{formatCompactDateTime(account.last_used_at)}</TableCell>
                        <TableCell className="max-w-[240px] truncate text-xs" title={account.profile_sync_error || account.last_error || account.profile_sync_status || ""}>
                          {account.profile_sync_error || account.last_error ? (
                            <span className="text-destructive">{account.profile_sync_error || account.last_error}</span>
                          ) : (
                            <>
                              <div>{account.profile_sync_status || account.last_status || "-"}</div>
                              <div className="text-muted-foreground">{formatCompactDateTime(account.profile_synced_at)}</div>
                            </>
                          )}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-4 border-b">
          <CardTitle className="text-lg font-semibold">Token list</CardTitle>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2" title="When AT expires in &lt;1h, refresh from ST (server policy)">
              <span className="text-xs text-muted-foreground">Auto refresh AT</span>
              <Switch checked={atAutoRefresh} onCheckedChange={onToggleAtAutoRefresh} />
            </div>
            <div className="flex items-center gap-2" title="Refresh protocol-mode ST values from stored Google cookies">
              <span className="text-xs text-muted-foreground">Auto refresh protocol ST</span>
              <Switch
                checked={protocolAutoRefresh}
                onCheckedChange={(enabled) => updateProtocolRefreshConfig(enabled, protocolRefreshInterval)}
              />
              <Input
                type="number"
                min={1}
                max={10080}
                className="h-8 w-20"
                value={protocolRefreshInterval}
                onChange={(event) => setProtocolRefreshInterval(event.target.value)}
                onBlur={() => updateProtocolRefreshConfig(protocolAutoRefresh, protocolRefreshInterval)}
                aria-label="Protocol refresh interval in minutes"
              />
              <span className="text-xs text-muted-foreground">min</span>
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
            <Button size="sm" variant="outline" onClick={createBrowserProfileAccount} disabled={profileBusyId === "new"} title="Create a persistent headed Chrome profile account">
              {profileBusyId === "new" ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />}
              Browser profile
            </Button>
            <Button size="sm" variant="outline" asChild title="Open Fluxbox desktop through noVNC">
              <a href={buildNoVncUrl()} target="_blank" rel="noreferrer">
                <ExternalLink className="h-4 w-4 mr-2" /> Open Fluxbox
              </a>
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
                  <TableHead className="text-center">Profile</TableHead>
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
                    const isProfile = t.auth_mode === "browser_profile"
                    const profileStatus = t.browser_profile_status || "not_created"
                    const health = [t.browser_profile_cookie_status, t.browser_profile_st_status, t.browser_profile_at_status]
                      .filter(Boolean)
                      .join(" / ")
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
                          {isProfile ? (
                            <div className="space-y-1">
                              <span
                                className={`inline-flex rounded px-2 py-0.5 text-xs ${
                                  profileStatus === "connected"
                                    ? "bg-green-500/15 text-green-700 dark:text-green-400"
                                    : profileStatus === "error"
                                      ? "bg-red-500/15 text-red-700 dark:text-red-400"
                                      : "bg-muted text-muted-foreground"
                                }`}
                                title={t.browser_profile_last_error || health}
                              >
                                {profileStatus}
                              </span>
                              <div className="text-[10px] text-muted-foreground">
                                {health || "unknown"} · {t.runtime_open ? "running" : "stopped"}
                              </div>
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground">Session token</span>
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
                            {isProfile ? (
                              <>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 px-2 text-xs"
                                  disabled={profileBusyId === t.id}
                                  onClick={() => browserProfileAction(t.id, t.runtime_open ? "close" : "open")}
                                  title={t.runtime_open ? "Close Chromium and preserve the saved profile" : "Open the saved profile in Chromium"}
                                >
                                  {profileBusyId === t.id ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : null}
                                  {t.runtime_open ? "Close" : "Open"}
                                </Button>
                                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={profileBusyId === t.id} onClick={() => browserProfileAction(t.id, "sync")}>
                                  Sync
                                </Button>
                                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={profileBusyId === t.id} onClick={() => browserProfileAction(t.id, "refresh")}>
                                  Profile refresh
                                </Button>
                                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-destructive" disabled={profileBusyId === t.id} onClick={() => browserProfileAction(t.id, "reset")}>
                                  Reset
                                </Button>
                              </>
                            ) : null}
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
              <Label>Captcha proxy URL</Label>
              <Input className="mt-1 font-mono text-sm" value={addCaptchaProxy} onChange={(e) => setAddCaptchaProxy(e.target.value)} />
            </div>
            <div className="space-y-3 border-t pt-3">
              <div className="flex items-start gap-3">
                <Switch checked={addProtocolMode} onCheckedChange={setAddProtocolMode} className="mt-0.5" />
                <div>
                  <Label className="!mt-0">Protocol ST refresh</Label>
                  <p className="text-xs text-muted-foreground">Use exported Google cookies to renew the Labs session token automatically.</p>
                </div>
              </div>
              {addProtocolMode ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="sm:col-span-2">
                    <Label>Google cookies</Label>
                    <Textarea className="mt-1 font-mono text-xs" rows={3} value={addGoogleCookies} onChange={(e) => setAddGoogleCookies(e.target.value)} placeholder="Cookie JSON export or SID=...; HSID=..." />
                  </div>
                  <div>
                    <Label>Google account hint</Label>
                    <Input className="mt-1" value={addLoginAccount} onChange={(e) => setAddLoginAccount(e.target.value)} placeholder="name@example.com" />
                  </div>
                  <div>
                    <Label>Google account password</Label>
                    <Input className="mt-1" type="password" value={addLoginPassword} onChange={(e) => setAddLoginPassword(e.target.value)} autoComplete="new-password" />
                  </div>
                  <div>
                    <Label>Refresh interval (minutes)</Label>
                    <Input className="mt-1" type="number" min={1} max={10080} value={addRefreshInterval} onChange={(e) => setAddRefreshInterval(e.target.value)} />
                  </div>
                  <div className="sm:col-span-2">
                    <Label>Protocol proxy URL (optional)</Label>
                    <Input className="mt-1 font-mono text-sm" value={addProtocolProxy} onChange={(e) => setAddProtocolProxy(e.target.value)} />
                  </div>
                </div>
              ) : null}
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
              <Label>Captcha proxy URL</Label>
              <Input className="mt-1 font-mono text-sm" value={editCaptchaProxy} onChange={(e) => setEditCaptchaProxy(e.target.value)} />
            </div>
            <div className="space-y-3 border-t pt-3">
              <div className="flex items-start gap-3">
                <Switch checked={editProtocolMode} onCheckedChange={setEditProtocolMode} className="mt-0.5" />
                <div>
                  <Label className="!mt-0">Protocol ST refresh</Label>
                  <p className="text-xs text-muted-foreground">Cookie values are write-only and never returned by the API.</p>
                </div>
              </div>
              {editProtocolMode ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="sm:col-span-2">
                    <Label>Replace Google cookies</Label>
                    <Textarea className="mt-1 font-mono text-xs" rows={3} value={editGoogleCookies} onChange={(e) => setEditGoogleCookies(e.target.value)} placeholder={editHasGoogleCookies ? "Cookies are configured; leave blank to keep them" : "Cookie JSON export or SID=...; HSID=..."} />
                  </div>
                  <div>
                    <Label>Google account hint</Label>
                    <Input className="mt-1" value={editLoginAccount} onChange={(e) => setEditLoginAccount(e.target.value)} />
                  </div>
                  <div>
                    <Label>Google account password</Label>
                    <Input className="mt-1" type="password" value={editLoginPassword} onChange={(e) => setEditLoginPassword(e.target.value)} autoComplete="new-password" />
                  </div>
                  <div>
                    <Label>Refresh interval (minutes)</Label>
                    <Input className="mt-1" type="number" min={1} max={10080} value={editRefreshInterval} onChange={(e) => setEditRefreshInterval(e.target.value)} />
                  </div>
                  <div className="sm:col-span-2">
                    <Label>Replace protocol proxy URL</Label>
                    <Input className="mt-1 font-mono text-sm" value={editProtocolProxy} onChange={(e) => setEditProtocolProxy(e.target.value)} placeholder="Leave blank to keep the configured proxy" />
                  </div>
                </div>
              ) : null}
            </div>
            <div className="flex items-start gap-3 border-t pt-3">
              <Switch checked={editUseExtensionGen} onCheckedChange={setEditUseExtensionGen} className="mt-0.5" />
              <div className="space-y-0.5">
                <Label className="!mt-0">Use extension for generation</Label>
                <p className="text-xs text-muted-foreground">
                  When off, Flow image/video requests use server HTTP; the extension can still handle captcha for this token.
                </p>
              </div>
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
