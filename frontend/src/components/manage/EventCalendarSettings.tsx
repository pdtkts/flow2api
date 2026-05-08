import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { toast } from "sonner"

type EventCalendarRow = {
  date: string
  month: string
  event_name: string
}

export function EventCalendarSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [rows, setRows] = useState<EventCalendarRow[]>([])

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{ success?: boolean; events?: EventCalendarRow[] }>(
      "/api/admin/event-calendar",
      token
    )
    if (!resp.ok || !resp.data?.success) {
      toast.error("Failed to load event calendar")
      return
    }
    setRows(Array.isArray(resp.data.events) ? resp.data.events : [])
  }, [token, active])

  useEffect(() => {
    void load()
  }, [load])

  const updateRow = (idx: number, key: keyof EventCalendarRow, value: string) => {
    setRows((prev) => {
      const next = [...prev]
      next[idx] = { ...next[idx], [key]: value }
      return next
    })
  }

  const addRow = () => {
    setRows((prev) => [...prev, { date: "", month: "", event_name: "" }])
  }

  const removeRow = (idx: number) => {
    setRows((prev) => prev.filter((_, i) => i !== idx))
  }

  const save = async () => {
    if (!token) return
    setBusy(true)
    try {
      const payload = {
        events: rows.map((r) => ({
          date: r.date.trim(),
          month: r.month.trim(),
          event_name: r.event_name.trim(),
        })),
      }
      const resp = await adminFetch("/api/admin/event-calendar", token, {
        method: "POST",
        body: JSON.stringify(payload),
      })
      if (!resp) return
      const data = await resp.json().catch(() => null)
      if (!resp.ok || !data?.success) {
        toast.error(data?.detail || data?.message || "Failed to save event calendar")
        return
      }
      toast.success("Event calendar saved")
      await load()
    } catch (err) {
      toast.error(String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!active) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Suggested Events Calendar</CardTitle>
        <CardDescription>
          Configure event dates used by the suggested-events endpoint. CSV columns map to Date, Month, and Event Name.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-2">
          <Label>Event Rows ({rows.length})</Label>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={addRow}>
              Add Row
            </Button>
            <Button type="button" variant="outline" onClick={() => void load()}>
              Reload
            </Button>
          </div>
        </div>

        <div className="rounded-md border overflow-auto max-h-[560px]">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 sticky top-0">
              <tr>
                <th className="text-left px-3 py-2 w-[140px]">Date</th>
                <th className="text-left px-3 py-2 w-[150px]">Month</th>
                <th className="text-left px-3 py-2">Event Name</th>
                <th className="text-right px-3 py-2 w-[90px]">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr key={`${idx}-${row.event_name}`} className="border-t">
                  <td className="px-3 py-2">
                    <Input
                      value={row.date}
                      onChange={(e) => updateRow(idx, "date", e.target.value)}
                      placeholder="1-Jan"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <Input
                      value={row.month}
                      onChange={(e) => updateRow(idx, "month", e.target.value)}
                      placeholder="January"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <Input
                      value={row.event_name}
                      onChange={(e) => updateRow(idx, "event_name", e.target.value)}
                      placeholder="Event name"
                    />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button type="button" variant="ghost" onClick={() => removeRow(idx)}>
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="text-xs text-muted-foreground">
          Keep Date format like <code>10-Feb</code> and month labels aligned for readability.
        </p>

        <Button onClick={() => void save()} disabled={busy}>
          {busy ? "Saving..." : "Save Event Calendar"}
        </Button>
      </CardContent>
    </Card>
  )
}
