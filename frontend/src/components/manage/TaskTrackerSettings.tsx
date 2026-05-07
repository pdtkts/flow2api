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

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{ success?: boolean; config?: Record<string, unknown> }>(
      "/api/generation/timeout",
      token
    )
    if (!resp.ok || !resp.data?.success || !resp.data.config) return
    const c = resp.data.config
    setDeviceId(String(c.task_tracker_device_id || ""))
    setDeviceName(String(c.task_tracker_device_name || ""))
    setCookies(String(c.task_tracker_cookies || ""))
  }, [token, active])

  useEffect(() => {
    load()
  }, [load])

  const save = async () => {
    if (!token) return
    setBusy(true)
    try {
      const resp = await adminFetch("/api/generation/timeout", token, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_tracker_device_id: deviceId.trim(),
          task_tracker_device_name: deviceName.trim(),
          task_tracker_cookies: cookies.trim(),
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
          Configure device credentials and authentication cookies for the automated Task Tracker fetches.
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
            A stable device ID used by the tracker. Will fallback to default if left empty.
          </p>
        </div>

        <div className="space-y-2">
          <Label>Device Name</Label>
          <Input
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="Chrome on Windows"
          />
        </div>

        <div className="space-y-2">
          <Label>Cookies (TRACK_ADOBE_COOKIES)</Label>
          <Textarea
            value={cookies}
            onChange={(e) => setCookies(e.target.value)}
            placeholder="__Secure-next-auth.session-token=..."
            rows={5}
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Full Cookie header from a logged-in session. Must contain <code>__Secure-next-auth.session-token</code>.
          </p>
        </div>

        <Button onClick={save} disabled={busy}>
          {busy ? "Saving..." : "Save Settings"}
        </Button>
      </CardContent>
    </Card>
  )
}
