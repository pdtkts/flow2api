import { useState, useEffect, useCallback, useRef } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Switch } from "../ui/switch"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "../ui/select"
import { toast } from "sonner"
import { Loader2 } from "lucide-react"

type CaptchaForm = {
  captcha_method: string
  yescaptcha_api_key: string
  yescaptcha_base_url: string
  capmonster_api_key: string
  capmonster_base_url: string
  ezcaptcha_api_key: string
  ezcaptcha_base_url: string
  capsolver_api_key: string
  capsolver_base_url: string
  remote_browser_base_url: string
  remote_browser_api_key: string
  remote_browser_timeout: number
  browser_fallback_to_remote_browser: boolean
  browser_captcha_page_url: string
  browser_proxy_enabled: boolean
  browser_proxy_url: string
  browser_count: number
  personal_project_pool_size: number
  personal_max_resident_tabs: number
  personal_idle_tab_ttl_seconds: number
  session_refresh_enabled: boolean
  session_refresh_browser_first: boolean
  session_refresh_inject_st_cookie: boolean
  session_refresh_warmup_urls: string
  session_refresh_wait_seconds_per_url: number
  session_refresh_overall_timeout_seconds: number
  session_refresh_update_st_from_cookie: boolean
  session_refresh_fail_if_st_refresh_fails: boolean
  session_refresh_local_only: boolean
  session_refresh_scheduler_enabled: boolean
  session_refresh_scheduler_interval_minutes: number
  session_refresh_scheduler_batch_size: number
  session_refresh_scheduler_only_expiring_within_minutes: number
  extension_queue_wait_timeout_seconds: number
  personal_proxy_enabled: boolean
  personal_proxy_url: string
}

const defaultCaptcha: CaptchaForm = {
  captcha_method: "yescaptcha",
  yescaptcha_api_key: "",
  yescaptcha_base_url: "https://api.yescaptcha.com",
  capmonster_api_key: "",
  capmonster_base_url: "https://api.capmonster.cloud",
  ezcaptcha_api_key: "",
  ezcaptcha_base_url: "https://api.ez-captcha.com",
  capsolver_api_key: "",
  capsolver_base_url: "https://api.capsolver.com",
  remote_browser_base_url: "",
  remote_browser_api_key: "",
  remote_browser_timeout: 60,
  browser_fallback_to_remote_browser: true,
  browser_captcha_page_url: "https://labs.google/fx/api/auth/providers",
  browser_proxy_enabled: false,
  browser_proxy_url: "",
  browser_count: 1,
  personal_project_pool_size: 4,
  personal_max_resident_tabs: 5,
  personal_idle_tab_ttl_seconds: 600,
  session_refresh_enabled: true,
  session_refresh_browser_first: true,
  session_refresh_inject_st_cookie: true,
  session_refresh_warmup_urls: "https://labs.google/fx/tools/flow,https://labs.google/fx",
  session_refresh_wait_seconds_per_url: 60,
  session_refresh_overall_timeout_seconds: 180,
  session_refresh_update_st_from_cookie: true,
  session_refresh_fail_if_st_refresh_fails: true,
  session_refresh_local_only: true,
  session_refresh_scheduler_enabled: false,
  session_refresh_scheduler_interval_minutes: 30,
  session_refresh_scheduler_batch_size: 10,
  session_refresh_scheduler_only_expiring_within_minutes: 60,
  extension_queue_wait_timeout_seconds: 20,
  personal_proxy_enabled: false,
  personal_proxy_url: "",
}

type ExtensionWorkerRow = {
  connection_id: number
  route_key: string
  client_label: string
  managed_api_key_id: number | null
  binding_source: string
  connected_at: number
}

type ExtensionBindingRow = {
  id: number
  route_key: string
  api_key_id: number
  api_key_label?: string
}

export function SystemSettings({ active }: { active: boolean }) {
  const { token } = useAuth()

  const [adminUsername, setAdminUsername] = useState("")
  const [currentApiKey, setCurrentApiKey] = useState("")
  const [oldPwd, setOldPwd] = useState("")
  const [newPwd, setNewPwd] = useState("")
  const [newApiKeyInput, setNewApiKeyInput] = useState("")
  const [errorBan, setErrorBan] = useState("3")
  const [debugEnabled, setDebugEnabled] = useState(false)

  const [proxyEnabled, setProxyEnabled] = useState(false)
  const [proxyUrl, setProxyUrl] = useState("")
  const [mediaProxyEnabled, setMediaProxyEnabled] = useState(false)
  const [mediaProxyUrl, setMediaProxyUrl] = useState("")
  const [proxyTestMsg, setProxyTestMsg] = useState("")

  const [imgTimeout, setImgTimeout] = useState("300")
  const [vidTimeout, setVidTimeout] = useState("1500")
  const [maxRetries, setMaxRetries] = useState("3")

  const [callMode, setCallMode] = useState<"default" | "polling">("default")

  const [pluginUrl, setPluginUrl] = useState("")
  const [pluginToken, setPluginToken] = useState("")
  const [pluginAutoEnable, setPluginAutoEnable] = useState(false)

  const [captcha, setCaptcha] = useState<CaptchaForm>(defaultCaptcha)
  const [extensionWorkers, setExtensionWorkers] = useState<ExtensionWorkerRow[]>([])
  const [extensionBindings, setExtensionBindings] = useState<ExtensionBindingRow[]>([])
  const [managedKeys, setManagedKeys] = useState<Array<{ id: number; label?: string; key_prefix?: string }>>([])
  const [bindRouteKey, setBindRouteKey] = useState("")
  const [bindApiKeyId, setBindApiKeyId] = useState("")

  const [busy, setBusy] = useState(false)
  /** Bumps when leaving extension mode so in-flight worker list fetches do not repopulate state. */
  const extensionFetchGen = useRef(0)

  const loadAll = useCallback(async () => {
    if (!token || !active) return

    const [a, p, g, c, plug, cap, keysResp, workersResp] = await Promise.all([
      adminJson<{
        admin_username?: string
        api_key?: string
        error_ban_threshold?: number
        debug_enabled?: boolean
      }>("/api/admin/config", token),
      adminJson<{ proxy_enabled?: boolean; proxy_url?: string; media_proxy_enabled?: boolean; media_proxy_url?: string }>(
        "/api/proxy/config",
        token
      ),
      adminJson<{ success?: boolean; config?: { image_timeout?: number; video_timeout?: number; max_retries?: number } }>(
        "/api/generation/timeout",
        token
      ),
      adminJson<{ success?: boolean; config?: { call_mode?: string } }>("/api/call-logic/config", token),
      adminJson<{ success?: boolean; config?: { connection_url?: string; connection_token?: string; auto_enable_on_update?: boolean } }>(
        "/api/plugin/config",
        token
      ),
      adminJson<Record<string, unknown>>("/api/captcha/config", token),
      adminJson<{ success?: boolean; keys?: Array<{ id: number; label?: string; key_prefix?: string }> }>(
        "/api/admin/managed-apikeys",
        token
      ),
      adminJson<{
        success?: boolean
        workers?: ExtensionWorkerRow[]
        bindings?: ExtensionBindingRow[]
      }>("/api/admin/extension/workers", token),
    ])

    if (a.data) {
      setAdminUsername(a.data.admin_username || "admin")
      setCurrentApiKey(a.data.api_key || "")
      setErrorBan(String(a.data.error_ban_threshold ?? 3))
      setDebugEnabled(!!a.data.debug_enabled)
    }
    if (p.data) {
      setProxyEnabled(!!p.data.proxy_enabled)
      setProxyUrl(p.data.proxy_url || "")
      setMediaProxyEnabled(!!p.data.media_proxy_enabled)
      setMediaProxyUrl(p.data.media_proxy_url || "")
    }
    if (g.ok && g.data?.success && g.data.config) {
      setImgTimeout(String(g.data.config.image_timeout ?? 300))
      setVidTimeout(String(g.data.config.video_timeout ?? 1500))
      setMaxRetries(String(g.data.config.max_retries ?? 3))
    }
    if (c.ok && c.data?.success && c.data.config?.call_mode)
      setCallMode(c.data.config.call_mode === "polling" ? "polling" : "default")
    if (plug.ok && plug.data?.success && plug.data.config) {
      setPluginUrl(plug.data.config.connection_url || "")
      setPluginToken(plug.data.config.connection_token || "")
      setPluginAutoEnable(!!plug.data.config.auto_enable_on_update)
    }
    if (cap.ok && cap.data && typeof cap.data === "object") {
      const raw = cap.data as Record<string, unknown>
      setCaptcha(() => ({
        ...defaultCaptcha,
        captcha_method: String(raw.captcha_method || "yescaptcha"),
        yescaptcha_api_key: String(raw.yescaptcha_api_key ?? ""),
        yescaptcha_base_url: String(raw.yescaptcha_base_url || defaultCaptcha.yescaptcha_base_url),
        capmonster_api_key: String(raw.capmonster_api_key ?? ""),
        capmonster_base_url: String(raw.capmonster_base_url || defaultCaptcha.capmonster_base_url),
        ezcaptcha_api_key: String(raw.ezcaptcha_api_key ?? ""),
        ezcaptcha_base_url: String(raw.ezcaptcha_base_url || defaultCaptcha.ezcaptcha_base_url),
        capsolver_api_key: String(raw.capsolver_api_key ?? ""),
        capsolver_base_url: String(raw.capsolver_base_url || defaultCaptcha.capsolver_base_url),
        remote_browser_base_url: String(raw.remote_browser_base_url ?? ""),
        remote_browser_api_key: String(raw.remote_browser_api_key ?? ""),
        remote_browser_timeout: Number(raw.remote_browser_timeout ?? 60),
        browser_fallback_to_remote_browser: raw.browser_fallback_to_remote_browser !== false,
        browser_captcha_page_url: String(
          raw.browser_captcha_page_url || "https://labs.google/fx/api/auth/providers"
        ),
        browser_proxy_enabled: !!raw.browser_proxy_enabled,
        browser_proxy_url: String(raw.browser_proxy_url ?? ""),
        browser_count: Math.max(1, Number(raw.browser_count ?? 1)),
        personal_project_pool_size: Number(raw.personal_project_pool_size ?? 4),
        personal_max_resident_tabs: Number(raw.personal_max_resident_tabs ?? 5),
        personal_idle_tab_ttl_seconds: Number(raw.personal_idle_tab_ttl_seconds ?? 600),
        session_refresh_enabled: raw.session_refresh_enabled !== false,
        session_refresh_browser_first: raw.session_refresh_browser_first !== false,
        session_refresh_inject_st_cookie: raw.session_refresh_inject_st_cookie !== false,
        session_refresh_warmup_urls: Array.isArray(raw.session_refresh_warmup_urls)
          ? raw.session_refresh_warmup_urls.join(",")
          : String(raw.session_refresh_warmup_urls ?? defaultCaptcha.session_refresh_warmup_urls),
        session_refresh_wait_seconds_per_url: Number(raw.session_refresh_wait_seconds_per_url ?? 60),
        session_refresh_overall_timeout_seconds: Number(raw.session_refresh_overall_timeout_seconds ?? 180),
        session_refresh_update_st_from_cookie: raw.session_refresh_update_st_from_cookie !== false,
        session_refresh_fail_if_st_refresh_fails: raw.session_refresh_fail_if_st_refresh_fails !== false,
        session_refresh_local_only: raw.session_refresh_local_only !== false,
        session_refresh_scheduler_enabled: !!raw.session_refresh_scheduler_enabled,
        session_refresh_scheduler_interval_minutes: Number(raw.session_refresh_scheduler_interval_minutes ?? 30),
        session_refresh_scheduler_batch_size: Number(raw.session_refresh_scheduler_batch_size ?? 10),
        session_refresh_scheduler_only_expiring_within_minutes: Number(
          raw.session_refresh_scheduler_only_expiring_within_minutes ?? 60
        ),
        extension_queue_wait_timeout_seconds: Number(raw.extension_queue_wait_timeout_seconds ?? 20),
        personal_proxy_enabled: !!raw.browser_proxy_enabled,
        personal_proxy_url: String(raw.browser_proxy_url ?? ""),
      }))
    }
    if (keysResp.ok && keysResp.data?.success) {
      setManagedKeys(Array.isArray(keysResp.data.keys) ? keysResp.data.keys : [])
    }
    const loadedCaptchaMethod =
      cap.ok && cap.data && typeof cap.data === "object"
        ? String((cap.data as Record<string, unknown>).captcha_method || "yescaptcha")
        : ""
    if (workersResp.ok && workersResp.data?.success) {
      if (loadedCaptchaMethod === "extension") {
        setExtensionWorkers(Array.isArray(workersResp.data.workers) ? workersResp.data.workers : [])
        setExtensionBindings(Array.isArray(workersResp.data.bindings) ? workersResp.data.bindings : [])
      } else {
        setExtensionWorkers([])
        setExtensionBindings([])
      }
    }
  }, [token, active])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const saveErrorBan = async () => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/config", token, {
        method: "POST",
        body: JSON.stringify({ error_ban_threshold: parseInt(errorBan, 10) || 3 }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Saved")
      else toast.error("Save failed")
    } finally {
      setBusy(false)
    }
  }

  const savePassword = async () => {
    if (!token) return
    if (!oldPwd || !newPwd) {
      toast.error("Enter old and new password")
      return
    }
    if (newPwd.length < 4) {
      toast.error("New password at least 4 characters")
      return
    }
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/password", token, {
        method: "POST",
        body: JSON.stringify({
          username: adminUsername.trim() || undefined,
          old_password: oldPwd,
          new_password: newPwd,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Password updated — please sign in again")
        setTimeout(() => {
          localStorage.removeItem("adminToken")
          window.location.href = "/login"
        }, 1500)
      } else toast.error(d.detail || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const saveApiKey = async () => {
    if (!token) return
    const k = newApiKeyInput.trim()
    if (!k || k.length < 6) {
      toast.error("New API key at least 6 characters")
      return
    }
    if (!confirm("Update API key? All clients must use the new key.")) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/apikey", token, {
        method: "POST",
        body: JSON.stringify({ new_api_key: k }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("API key updated")
        setCurrentApiKey(k)
        setNewApiKeyInput("")
      } else toast.error(d.detail || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const onDebugToggle = async (enabled: boolean) => {
    if (!token) return
    const prev = debugEnabled
    setDebugEnabled(enabled)
    const r = await adminFetch("/api/admin/debug", token, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    })
    if (!r) return
    const d = await r.json()
    if (d.success) toast.success(enabled ? "Debug on" : "Debug off")
    else {
      setDebugEnabled(prev)
      toast.error(d.detail || "Failed")
    }
  }

  const saveProxy = async () => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/proxy/config", token, {
        method: "POST",
        body: JSON.stringify({
          proxy_enabled: proxyEnabled,
          proxy_url: proxyUrl.trim(),
          media_proxy_enabled: mediaProxyEnabled,
          media_proxy_url: mediaProxyUrl.trim(),
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Proxy saved")
      else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const testProxy = async () => {
    if (!token) return
    const tests: { name: string; proxy_url: string }[] = []
    const pu = proxyUrl.trim()
    const mu = mediaProxyUrl.trim()
    if (pu) tests.push({ name: "Request proxy", proxy_url: pu })
    if (mu && mu !== pu) tests.push({ name: "Media proxy", proxy_url: mu })
    if (!tests.length) {
      setProxyTestMsg("Fill at least one proxy URL")
      toast.error("Fill at least one proxy URL")
      return
    }
    setBusy(true)
    setProxyTestMsg("")
    try {
      const parts: string[] = []
      let allOk = true
      for (const t of tests) {
        const r = await adminFetch("/api/proxy/test", token, {
          method: "POST",
          body: JSON.stringify({ proxy_url: t.proxy_url, test_url: "https://labs.google/" }),
        })
        if (!r) continue
        const d = await r.json()
        if (!d.success) allOk = false
        parts.push(`${t.name}: ${d.success ? "OK" : "Fail"} ${d.message || ""}${d.status_code != null ? ` (HTTP ${d.status_code})` : ""}`)
      }
      const summary = parts.join(" | ")
      setProxyTestMsg(summary)
      if (allOk) toast.success(summary)
      else toast.error(summary)
    } catch (e) {
      setProxyTestMsg(String(e))
      toast.error("Proxy test failed")
    } finally {
      setBusy(false)
    }
  }

  const saveGeneration = async () => {
    if (!token) return
    const it = parseInt(imgTimeout, 10) || 300
    const vt = parseInt(vidTimeout, 10) || 1500
    const mr = parseInt(maxRetries, 10) || 3
    if (it < 60 || it > 3600) return toast.error("Image timeout 60–3600s")
    if (vt < 60 || vt > 7200) return toast.error("Video timeout 60–7200s")
    if (mr < 1) return toast.error("Max retries ≥ 1")
    setBusy(true)
    try {
      const r = await adminFetch("/api/generation/timeout", token, {
        method: "POST",
        body: JSON.stringify({ image_timeout: it, video_timeout: vt, max_retries: mr }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Generation config saved")
      else toast.error("Failed")
    } finally {
      setBusy(false)
    }
  }

  const saveCallLogic = async () => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/call-logic/config", token, {
        method: "POST",
        body: JSON.stringify({ call_mode: callMode }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Call logic saved")
      else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const savePlugin = async () => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/plugin/config", token, {
        method: "POST",
        body: JSON.stringify({
          connection_token: pluginToken.trim(),
          auto_enable_on_update: pluginAutoEnable,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Plugin config saved")
        if (d.connection_token) setPluginToken(d.connection_token)
        await loadAll()
      } else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const saveCaptcha = async () => {
    if (!token) return
    const method = captcha.captcha_method
    const finalProxyEnabled = method === "personal" ? captcha.personal_proxy_enabled : captcha.browser_proxy_enabled
    const finalProxyUrl = method === "personal" ? captcha.personal_proxy_url : captcha.browser_proxy_url
    setBusy(true)
    try {
      const r = await adminFetch("/api/captcha/config", token, {
        method: "POST",
        body: JSON.stringify({
          captcha_method: method,
          yescaptcha_api_key: captcha.yescaptcha_api_key.trim(),
          yescaptcha_base_url: captcha.yescaptcha_base_url.trim(),
          capmonster_api_key: captcha.capmonster_api_key.trim(),
          capmonster_base_url: captcha.capmonster_base_url.trim(),
          ezcaptcha_api_key: captcha.ezcaptcha_api_key.trim(),
          ezcaptcha_base_url: captcha.ezcaptcha_base_url.trim(),
          capsolver_api_key: captcha.capsolver_api_key.trim(),
          capsolver_base_url: captcha.capsolver_base_url.trim(),
          remote_browser_base_url: captcha.remote_browser_base_url.trim(),
          remote_browser_api_key: captcha.remote_browser_api_key.trim(),
          remote_browser_timeout: captcha.remote_browser_timeout,
          browser_fallback_to_remote_browser: captcha.browser_fallback_to_remote_browser,
          browser_captcha_page_url: captcha.browser_captcha_page_url.trim(),
          browser_proxy_enabled: finalProxyEnabled,
          browser_proxy_url: finalProxyUrl,
          browser_count: captcha.browser_count,
          personal_project_pool_size: captcha.personal_project_pool_size,
          personal_max_resident_tabs: captcha.personal_max_resident_tabs,
          personal_idle_tab_ttl_seconds: captcha.personal_idle_tab_ttl_seconds,
          session_refresh_enabled: captcha.session_refresh_enabled,
          session_refresh_browser_first: captcha.session_refresh_browser_first,
          session_refresh_inject_st_cookie: captcha.session_refresh_inject_st_cookie,
          session_refresh_warmup_urls: captcha.session_refresh_warmup_urls
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          session_refresh_wait_seconds_per_url: captcha.session_refresh_wait_seconds_per_url,
          session_refresh_overall_timeout_seconds: captcha.session_refresh_overall_timeout_seconds,
          session_refresh_update_st_from_cookie: captcha.session_refresh_update_st_from_cookie,
          session_refresh_fail_if_st_refresh_fails: captcha.session_refresh_fail_if_st_refresh_fails,
          session_refresh_local_only: captcha.session_refresh_local_only,
          session_refresh_scheduler_enabled: captcha.session_refresh_scheduler_enabled,
          session_refresh_scheduler_interval_minutes: captcha.session_refresh_scheduler_interval_minutes,
          session_refresh_scheduler_batch_size: captcha.session_refresh_scheduler_batch_size,
          session_refresh_scheduler_only_expiring_within_minutes:
            captcha.session_refresh_scheduler_only_expiring_within_minutes,
          extension_queue_wait_timeout_seconds: captcha.extension_queue_wait_timeout_seconds,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Captcha config saved")
        await loadAll()
      } else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const refreshExtensionWorkers = useCallback(async () => {
    if (!token) return
    const gen = ++extensionFetchGen.current
    const r = await adminJson<{ success?: boolean; workers?: ExtensionWorkerRow[]; bindings?: ExtensionBindingRow[] }>(
      "/api/admin/extension/workers",
      token
    )
    if (gen !== extensionFetchGen.current) return
    if (!r.ok || !r.data?.success) return
    setExtensionWorkers(Array.isArray(r.data.workers) ? r.data.workers : [])
    setExtensionBindings(Array.isArray(r.data.bindings) ? r.data.bindings : [])
  }, [token])

  useEffect(() => {
    if (captcha.captcha_method !== "extension") {
      extensionFetchGen.current++
      void Promise.resolve().then(() => {
        setExtensionWorkers([])
        setExtensionBindings([])
      })
      return
    }
    if (!token || !active) return
    void Promise.resolve().then(() => {
      void refreshExtensionWorkers()
    })
  }, [captcha.captcha_method, token, active, refreshExtensionWorkers])

  const bindExtensionWorker = async () => {
    if (!token) return
    const routeKey = bindRouteKey.trim()
    const apiKeyId = parseInt(bindApiKeyId, 10)
    if (!routeKey) return toast.error("Route key required")
    if (!Number.isFinite(apiKeyId) || apiKeyId <= 0) return toast.error("Managed API key ID required")
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/extension/workers/bind", token, {
        method: "POST",
        body: JSON.stringify({ route_key: routeKey, api_key_id: apiKeyId }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Worker binding saved")
        setBindRouteKey("")
        await refreshExtensionWorkers()
      } else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const unbindExtensionWorker = async (routeKey: string) => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/admin/extension/workers/unbind", token, {
        method: "POST",
        body: JSON.stringify({ route_key: routeKey }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) {
        toast.success("Worker binding removed")
        await refreshExtensionWorkers()
      } else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  const copyText = async (label: string, text: string) => {
    if (!text) return toast.error(`${label} is empty`)
    try {
      await navigator.clipboard.writeText(text)
      toast.success("Copied")
    } catch {
      toast.error("Copy failed")
    }
  }

  const genPluginToken = () => {
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    let out = ""
    for (let i = 0; i < 32; i++) out += chars[Math.floor(Math.random() * chars.length)]
    setPluginToken(out)
    toast.success("Random token generated")
  }

  if (!active) return null

  const m = captcha.captcha_method

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Security</CardTitle>
          <CardDescription>Admin username and password</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Admin username</Label>
            <Input className="mt-1" value={adminUsername} onChange={(e) => setAdminUsername(e.target.value)} />
          </div>
          <div>
            <Label>Old password</Label>
            <Input className="mt-1" type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)} />
          </div>
          <div>
            <Label>New password</Label>
            <Input className="mt-1" type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} />
          </div>
          <Button className="w-full" onClick={savePassword} disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Update password"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Client API key</CardTitle>
          <CardDescription>Key used by OpenAI-compatible clients</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Current API key</Label>
            <Input className="mt-1 font-mono text-sm" readOnly disabled value={currentApiKey} />
          </div>
          <div>
            <Label>New API key</Label>
            <Input className="mt-1 font-mono text-sm" value={newApiKeyInput} onChange={(e) => setNewApiKeyInput(e.target.value)} />
          </div>
          <Button className="w-full" onClick={saveApiKey} disabled={busy}>
            Update API key
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Error handling</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Error ban threshold</Label>
            <Input className="mt-1" type="number" value={errorBan} onChange={(e) => setErrorBan(e.target.value)} />
            <p className="text-xs text-muted-foreground mt-1">Disable token after this many consecutive errors</p>
          </div>
          <Button onClick={saveErrorBan} disabled={busy}>
            Save
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Debug</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Switch checked={debugEnabled} onCheckedChange={onDebugToggle} />
            <Label>Enable debug logging</Label>
          </div>
          <p className="text-xs text-muted-foreground">Writes verbose upstream logs (disk usage).</p>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Proxy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Switch checked={proxyEnabled} onCheckedChange={setProxyEnabled} />
            <Label>Enable request proxy</Label>
          </div>
          <div>
            <Label>Proxy URL</Label>
            <Input className="mt-1 font-mono text-sm" value={proxyUrl} onChange={(e) => setProxyUrl(e.target.value)} placeholder="http://127.0.0.1:7890" />
          </div>
          <div className="flex items-center gap-2 border-t pt-4">
            <Switch checked={mediaProxyEnabled} onCheckedChange={setMediaProxyEnabled} />
            <Label>Media upload/download proxy</Label>
          </div>
          {mediaProxyEnabled ? (
            <div>
              <Label>Media proxy URL</Label>
              <Input className="mt-1 font-mono text-sm" value={mediaProxyUrl} onChange={(e) => setMediaProxyUrl(e.target.value)} />
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={testProxy} disabled={busy}>
              Test proxy
            </Button>
            <Button onClick={saveProxy} disabled={busy}>
              Save proxy
            </Button>
          </div>
          <p className={`text-xs ${proxyTestMsg.includes("Fail") ? "text-destructive" : "text-muted-foreground"}`}>
            Target: https://labs.google/ {proxyTestMsg ? `— ${proxyTestMsg}` : ""}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Generation timeouts</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Image timeout (s)</Label>
            <Input type="number" className="mt-1" value={imgTimeout} onChange={(e) => setImgTimeout(e.target.value)} min={60} max={3600} />
          </div>
          <div>
            <Label>Video timeout (s)</Label>
            <Input type="number" className="mt-1" value={vidTimeout} onChange={(e) => setVidTimeout(e.target.value)} min={60} max={7200} />
          </div>
          <div>
            <Label>Max retries</Label>
            <Input type="number" className="mt-1" value={maxRetries} onChange={(e) => setMaxRetries(e.target.value)} min={1} />
          </div>
          <Button onClick={saveGeneration} disabled={busy}>
            Save
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Token polling</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Mode</Label>
            <Select value={callMode} onValueChange={(v) => setCallMode(v as "default" | "polling")}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">Random (default)</SelectItem>
                <SelectItem value="polling">Sequential polling</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button onClick={saveCallLogic} disabled={busy}>
            Save
          </Button>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Chrome plugin</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input readOnly className="font-mono text-sm" value={pluginUrl} />
            <Button type="button" variant="secondary" onClick={() => copyText("URL", pluginUrl)}>
              Copy URL
            </Button>
          </div>
          <div className="flex gap-2">
            <Input className="font-mono text-sm" value={pluginToken} onChange={(e) => setPluginToken(e.target.value)} placeholder="Connection token" />
            <Button type="button" variant="secondary" onClick={genPluginToken}>
              Random
            </Button>
            <Button type="button" variant="secondary" onClick={() => copyText("Token", pluginToken)}>
              Copy
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={pluginAutoEnable} onCheckedChange={setPluginAutoEnable} />
            <Label>Auto-enable token on plugin update</Label>
          </div>
          <Button onClick={savePlugin} disabled={busy}>
            Save plugin settings
          </Button>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Captcha</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 max-h-[70vh] overflow-y-auto">
          <div>
            <Label>Method</Label>
            <Select value={captcha.captcha_method} onValueChange={(v) => setCaptcha((c) => ({ ...c, captcha_method: v }))}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>Extension mode</SelectLabel>
                  <SelectItem value="extension">Extension mode (Chrome WebSocket)</SelectItem>
                </SelectGroup>
                <SelectSeparator />
                <SelectGroup>
                  <SelectLabel>Third-party solving APIs</SelectLabel>
                  <SelectItem value="yescaptcha">YesCaptcha</SelectItem>
                  <SelectItem value="capmonster">CapMonster</SelectItem>
                  <SelectItem value="ezcaptcha">EzCaptcha</SelectItem>
                  <SelectItem value="capsolver">CapSolver</SelectItem>
                </SelectGroup>
                <SelectSeparator />
                <SelectGroup>
                  <SelectLabel>Server / browser automation</SelectLabel>
                  <SelectItem value="browser">Headed browser</SelectItem>
                  <SelectItem value="personal">Built-in browser</SelectItem>
                  <SelectItem value="remote_browser">Remote browser gateway</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
            {m === "extension" ? (
              <p className="text-xs text-muted-foreground mt-1">
                Uses your Chrome extension connected to <code className="rounded bg-muted px-1">/captcha_ws</code> — not
                headed Playwright/Chromium. Configure queue timeout and worker bindings in the sections that appear
                only for this method.
              </p>
            ) : null}
          </div>
          {m === "extension" ? (
            <div>
              <Label>Extension queue wait timeout (s)</Label>
              <Input
                type="number"
                min={1}
                max={120}
                value={captcha.extension_queue_wait_timeout_seconds}
                onChange={(e) =>
                  setCaptcha((c) => ({
                    ...c,
                    extension_queue_wait_timeout_seconds: Math.max(1, Math.min(120, parseInt(e.target.value, 10) || 20)),
                  }))
                }
              />
              <p className="text-xs text-muted-foreground mt-1">
                When Method is Chrome extension, managed-key requests wait in their own queue up to this timeout before
                failing.
              </p>
            </div>
          ) : null}

          {(m === "yescaptcha" || !m) && (
            <div className="space-y-2 border rounded-md p-3">
              <Label>YesCaptcha API key</Label>
              <Input value={captcha.yescaptcha_api_key} onChange={(e) => setCaptcha((c) => ({ ...c, yescaptcha_api_key: e.target.value }))} />
              <Label>Base URL</Label>
              <Input value={captcha.yescaptcha_base_url} onChange={(e) => setCaptcha((c) => ({ ...c, yescaptcha_base_url: e.target.value }))} />
            </div>
          )}
          {m === "capmonster" && (
            <div className="space-y-2 border rounded-md p-3">
              <Label>CapMonster API key</Label>
              <Input value={captcha.capmonster_api_key} onChange={(e) => setCaptcha((c) => ({ ...c, capmonster_api_key: e.target.value }))} />
              <Label>Base URL</Label>
              <Input value={captcha.capmonster_base_url} onChange={(e) => setCaptcha((c) => ({ ...c, capmonster_base_url: e.target.value }))} />
            </div>
          )}
          {m === "ezcaptcha" && (
            <div className="space-y-2 border rounded-md p-3">
              <Label>EzCaptcha API key</Label>
              <Input value={captcha.ezcaptcha_api_key} onChange={(e) => setCaptcha((c) => ({ ...c, ezcaptcha_api_key: e.target.value }))} />
              <Label>Base URL</Label>
              <Input value={captcha.ezcaptcha_base_url} onChange={(e) => setCaptcha((c) => ({ ...c, ezcaptcha_base_url: e.target.value }))} />
            </div>
          )}
          {m === "capsolver" && (
            <div className="space-y-2 border rounded-md p-3">
              <Label>CapSolver API key</Label>
              <Input value={captcha.capsolver_api_key} onChange={(e) => setCaptcha((c) => ({ ...c, capsolver_api_key: e.target.value }))} />
              <Label>Base URL</Label>
              <Input value={captcha.capsolver_base_url} onChange={(e) => setCaptcha((c) => ({ ...c, capsolver_base_url: e.target.value }))} />
            </div>
          )}
          {m === "browser" && (
            <div className="space-y-2 border rounded-md p-3">
              <Label>Captcha page URL</Label>
              <Input
                value={captcha.browser_captcha_page_url}
                onChange={(e) => setCaptcha((c) => ({ ...c, browser_captcha_page_url: e.target.value }))}
              />
              <div className="flex items-center gap-2">
                <Switch
                  checked={captcha.browser_fallback_to_remote_browser}
                  onCheckedChange={(v) =>
                    setCaptcha((c) => ({ ...c, browser_fallback_to_remote_browser: v }))
                  }
                />
                <Label>Fallback to gateway on browser failure</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={captcha.browser_proxy_enabled}
                  onCheckedChange={(v) => setCaptcha((c) => ({ ...c, browser_proxy_enabled: v }))}
                />
                <Label>Browser proxy</Label>
              </div>
              {captcha.browser_proxy_enabled ? (
                <Input value={captcha.browser_proxy_url} onChange={(e) => setCaptcha((c) => ({ ...c, browser_proxy_url: e.target.value }))} />
              ) : null}
              <Label>Browser count</Label>
              <Input
                type="number"
                min={1}
                max={20}
                value={captcha.browser_count}
                onChange={(e) => setCaptcha((c) => ({ ...c, browser_count: parseInt(e.target.value, 10) || 1 }))}
              />
            </div>
          )}
          {m === "personal" && (
            <div className="space-y-2 border rounded-md p-3">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label>Project pool size</Label>
                  <Input
                    type="number"
                    value={captcha.personal_project_pool_size}
                    onChange={(e) => setCaptcha((c) => ({ ...c, personal_project_pool_size: parseInt(e.target.value, 10) || 4 }))}
                  />
                </div>
                <div>
                  <Label>Max tabs</Label>
                  <Input
                    type="number"
                    value={captcha.personal_max_resident_tabs}
                    onChange={(e) => setCaptcha((c) => ({ ...c, personal_max_resident_tabs: parseInt(e.target.value, 10) || 5 }))}
                  />
                </div>
              </div>
              <Label>Idle TTL (s)</Label>
              <Input
                type="number"
                value={captcha.personal_idle_tab_ttl_seconds}
                onChange={(e) => setCaptcha((c) => ({ ...c, personal_idle_tab_ttl_seconds: parseInt(e.target.value, 10) || 600 }))}
              />
              <div className="flex items-center gap-2">
                <Switch
                  checked={captcha.personal_proxy_enabled}
                  onCheckedChange={(v) => setCaptcha((c) => ({ ...c, personal_proxy_enabled: v }))}
                />
                <Label>Proxy</Label>
              </div>
              {captcha.personal_proxy_enabled ? (
                <Input value={captcha.personal_proxy_url} onChange={(e) => setCaptcha((c) => ({ ...c, personal_proxy_url: e.target.value }))} />
              ) : null}
            </div>
          )}
          {m === "remote_browser" && (
            <div className="space-y-2 border rounded-md p-3">
              <Label>Remote base URL</Label>
              <Input value={captcha.remote_browser_base_url} onChange={(e) => setCaptcha((c) => ({ ...c, remote_browser_base_url: e.target.value }))} />
              <Label>API key</Label>
              <Input value={captcha.remote_browser_api_key} onChange={(e) => setCaptcha((c) => ({ ...c, remote_browser_api_key: e.target.value }))} />
              <Label>Timeout (s)</Label>
              <Input
                type="number"
                value={captcha.remote_browser_timeout}
                onChange={(e) => setCaptcha((c) => ({ ...c, remote_browser_timeout: parseInt(e.target.value, 10) || 60 }))}
              />
            </div>
          )}
          <Button onClick={saveCaptcha} disabled={busy}>
            Save captcha settings
          </Button>
        </CardContent>
      </Card>

      {m === "browser" || m === "remote_browser" ? (
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Session refresh (ST warmup)</CardTitle>
            <CardDescription>Local headed Playwright warmup before AT refresh.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-2">
              <Switch
                checked={captcha.session_refresh_enabled}
                onCheckedChange={(v) => setCaptcha((c) => ({ ...c, session_refresh_enabled: v }))}
              />
              <Label>Enable session refresh (ST warmup path)</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={captcha.session_refresh_browser_first}
                onCheckedChange={(v) => setCaptcha((c) => ({ ...c, session_refresh_browser_first: v }))}
              />
              <Label>Browser-first before AT refresh</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={captcha.session_refresh_inject_st_cookie}
                onCheckedChange={(v) => setCaptcha((c) => ({ ...c, session_refresh_inject_st_cookie: v }))}
              />
              <Label>Inject current ST cookie before warmup</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={captcha.session_refresh_update_st_from_cookie}
                onCheckedChange={(v) => setCaptcha((c) => ({ ...c, session_refresh_update_st_from_cookie: v }))}
              />
              <Label>Update ST from cookie after warmup</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={captcha.session_refresh_fail_if_st_refresh_fails}
                onCheckedChange={(v) => setCaptcha((c) => ({ ...c, session_refresh_fail_if_st_refresh_fails: v }))}
              />
              <Label>Fail AT refresh if ST refresh fails (strict)</Label>
            </div>
            <Label>Warmup URLs (comma separated)</Label>
            <Input
              value={captcha.session_refresh_warmup_urls}
              onChange={(e) => setCaptcha((c) => ({ ...c, session_refresh_warmup_urls: e.target.value }))}
            />
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label>Wait per URL (s)</Label>
                <Input
                  type="number"
                  min={0}
                  value={captcha.session_refresh_wait_seconds_per_url}
                  onChange={(e) =>
                    setCaptcha((c) => ({ ...c, session_refresh_wait_seconds_per_url: parseInt(e.target.value, 10) || 0 }))
                  }
                />
              </div>
              <div>
                <Label>Overall timeout (s)</Label>
                <Input
                  type="number"
                  min={10}
                  value={captcha.session_refresh_overall_timeout_seconds}
                  onChange={(e) =>
                    setCaptcha((c) => ({
                      ...c,
                      session_refresh_overall_timeout_seconds: parseInt(e.target.value, 10) || 180,
                    }))
                  }
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={captcha.session_refresh_scheduler_enabled}
                onCheckedChange={(v) => setCaptcha((c) => ({ ...c, session_refresh_scheduler_enabled: v }))}
              />
              <Label>Enable scheduled auto refresh</Label>
            </div>
            {captcha.session_refresh_scheduler_enabled ? (
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <Label>Interval (min)</Label>
                  <Input
                    type="number"
                    min={1}
                    value={captcha.session_refresh_scheduler_interval_minutes}
                    onChange={(e) =>
                      setCaptcha((c) => ({
                        ...c,
                        session_refresh_scheduler_interval_minutes: parseInt(e.target.value, 10) || 30,
                      }))
                    }
                  />
                </div>
                <div>
                  <Label>Batch size</Label>
                  <Input
                    type="number"
                    min={1}
                    value={captcha.session_refresh_scheduler_batch_size}
                    onChange={(e) =>
                      setCaptcha((c) => ({
                        ...c,
                        session_refresh_scheduler_batch_size: parseInt(e.target.value, 10) || 10,
                      }))
                    }
                  />
                </div>
                <div>
                  <Label>Expiring window (min)</Label>
                  <Input
                    type="number"
                    min={1}
                    value={captcha.session_refresh_scheduler_only_expiring_within_minutes}
                    onChange={(e) =>
                      setCaptcha((c) => ({
                        ...c,
                        session_refresh_scheduler_only_expiring_within_minutes: parseInt(e.target.value, 10) || 60,
                      }))
                    }
                  />
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {m === "extension" ? (
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Extension worker binding</CardTitle>
            <CardDescription>
              Only for captcha Method &quot;Chrome extension&quot;. Maps extension <code className="text-xs">route_key</code>{" "}
              to a managed API key for per-key isolation — unrelated to headed or remote browser captcha.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <Input
                placeholder="Route key (e.g. 9223)"
                value={bindRouteKey}
                onChange={(e) => setBindRouteKey(e.target.value)}
              />
              <Select value={bindApiKeyId} onValueChange={setBindApiKeyId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select managed API key" />
                </SelectTrigger>
                <SelectContent>
                  {managedKeys.map((k) => (
                    <SelectItem key={k.id} value={String(k.id)}>
                      #{k.id} {k.label || k.key_prefix || "managed-key"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="flex gap-2">
                <Button onClick={bindExtensionWorker} disabled={busy}>
                  Bind
                </Button>
                <Button variant="outline" onClick={refreshExtensionWorkers} disabled={busy}>
                  Refresh
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Label>Active workers</Label>
              <div className="rounded-md border">
                <div className="grid grid-cols-5 gap-2 px-3 py-2 text-xs font-medium border-b">
                  <span>Route key</span>
                  <span>Label</span>
                  <span>Managed key</span>
                  <span>Source</span>
                  <span>Connected at</span>
                </div>
                {(extensionWorkers.length ? extensionWorkers : []).map((w) => (
                  <div key={`${w.connection_id}-${w.route_key}`} className="grid grid-cols-5 gap-2 px-3 py-2 text-xs border-b last:border-b-0">
                    <span className="font-mono">{w.route_key || "(empty)"}</span>
                    <span>{w.client_label || "-"}</span>
                    <span>{w.managed_api_key_id ?? "-"}</span>
                    <span>{w.binding_source || "-"}</span>
                    <span>{w.connected_at ? new Date(w.connected_at * 1000).toLocaleTimeString() : "-"}</span>
                  </div>
                ))}
                {extensionWorkers.length === 0 ? <div className="px-3 py-3 text-xs text-muted-foreground">No active workers</div> : null}
              </div>
            </div>

            <div className="space-y-2">
              <Label>Persisted bindings</Label>
              <div className="rounded-md border">
                <div className="grid grid-cols-4 gap-2 px-3 py-2 text-xs font-medium border-b">
                  <span>Route key</span>
                  <span>Managed key</span>
                  <span>Label</span>
                  <span>Action</span>
                </div>
                {(extensionBindings.length ? extensionBindings : []).map((b) => (
                  <div key={`${b.id}-${b.route_key}`} className="grid grid-cols-4 gap-2 px-3 py-2 text-xs border-b last:border-b-0">
                    <span className="font-mono">{b.route_key}</span>
                    <span>{b.api_key_id}</span>
                    <span>{b.api_key_label || "-"}</span>
                    <span>
                      <Button size="sm" variant="outline" onClick={() => unbindExtensionWorker(b.route_key)} disabled={busy}>
                        Unbind
                      </Button>
                    </span>
                  </div>
                ))}
                {extensionBindings.length === 0 ? <div className="px-3 py-3 text-xs text-muted-foreground">No persisted bindings</div> : null}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
