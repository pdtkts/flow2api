import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"

export function TaskTrackerSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [deviceId, setDeviceId] = useState("")
  const [deviceName, setDeviceName] = useState("")
  const [cookies, setCookies] = useState("")
  const [deviceToken, setDeviceToken] = useState("")
  const [turnstileToken, setTurnstileToken] = useState("")
  const [tlsProfile, setTlsProfile] = useState("")

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{ success?: boolean; config?: Record<string, unknown> }>(
      "/api/config/generation",
      token
    )
    if (!resp.ok || !resp.data?.success || !resp.data.config) return
    const c = resp.data.config
    setDeviceId(String(c.task_tracker_device_id || ""))
    setDeviceName(String(c.task_tracker_device_name || ""))
    setCookies(String(c.task_tracker_cookies || ""))
    setDeviceToken(String(c.task_tracker_device_token || ""))
    setTurnstileToken(String(c.task_tracker_turnstile_token || ""))
    setTlsProfile(String(c.task_tracker_tls_profile || ""))
  }, [token, active])

  useEffect(() => {
    load()
  }, [load])

  const save = async () => {
    if (!token) return
    setBusy(true)
    try {
      const resp = await adminFetch("/api/config/generation", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_tracker_device_id: deviceId.trim(),
          task_tracker_device_name: deviceName.trim(),
          task_tracker_cookies: cookies.trim(),
          task_tracker_device_token: deviceToken.trim(),
          task_tracker_turnstile_token: turnstileToken.trim(),
          task_tracker_tls_profile: tlsProfile.trim(),
        }),
      })
      if (!resp || !resp.ok) {
        toast.error("Failed to save Task Tracker settings")
      } else {
        toast.success("Task Tracker settings saved")
      }
    } catch (e) {
      toast.error(String(e))
    }
    setBusy(false)
  }

  if (!active) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Task Tracker Settings</CardTitle>
        <CardDescription>
          Configure credentials for direct HTTPS fetches to tastracker.com (no browser automation).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Label>Device ID</Label>
          <Input
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            placeholder="dev_d6u2k6_wabygqst2z9_mocsd0nz"
          />
          <p className="text-xs text-muted-foreground">
            Sent as <code>x-device-id</code> / <code>X-Device-Id</code> on contributor-search. Falls back to default
            if left empty.
          </p>
        </div>

        <div className="space-y-2">
          <Label>Device name (optional)</Label>
          <Input
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="Chrome on Windows"
          />
          <p className="text-xs text-muted-foreground">Reserved for future use; not sent on the direct HTTP path.</p>
        </div>

        <div className="space-y-2">
          <Label>Device token (required)</Label>
          <Input
            value={deviceToken}
            onChange={(e) => setDeviceToken(e.target.value)}
            placeholder="UUID from DevTools → x-device-token on POST /api/auth/csr-token"
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Sent as <code>X-Device-Token</code> when minting <code>/api/auth/csr-token</code>. Without this, CSR mint
            often returns 401.
          </p>
        </div>

        <div className="space-y-2">
          <Label>Cookies (full header)</Label>
          <Textarea
            value={cookies}
            onChange={(e) => setCookies(e.target.value)}
            placeholder="__Secure-next-auth.session-token=...; cf_clearance=..."
            rows={5}
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Full <code>Cookie</code> header from a logged-in browser. Must include{" "}
            <code>__Secure-next-auth.session-token</code>.
          </p>
        </div>

        <div className="space-y-2">
          <Label>Turnstile token (optional)</Label>
          <Textarea
            value={turnstileToken}
            onChange={(e) => setTurnstileToken(e.target.value)}
            placeholder="Paste x-turnstile-token if upstream requires it"
            rows={3}
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            If set, sent as <code>X-Turnstile-Token</code> on each search request. Expires quickly; refresh when
            searches fail.
          </p>
        </div>

        <div className="space-y-2">
          <Label>TLS impersonation profile (optional)</Label>
          <Input
            value={tlsProfile}
            onChange={(e) => setTlsProfile(e.target.value)}
            placeholder="chrome124"
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            <code>curl_cffi</code> impersonate label (e.g. <code>chrome124</code>). Leave empty to use built-in default
            and fallback chain.
          </p>
        </div>

        <Button onClick={save} disabled={busy}>
          {busy ? "Saving..." : "Save Settings"}
        </Button>
      </CardContent>
    </Card>
  )
}
