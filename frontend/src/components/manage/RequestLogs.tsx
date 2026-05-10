import { useState, useEffect, useCallback } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import type { LogDetail, LogListItem, LogsListResponse } from "../../types/admin"
import { formatLogOutcomeRowClass, formatLogProgressField, logStatusPillClass, statusCodePillClass } from "./requestLogDetail"
import { formatLogStatus, formatOutcome } from "./requestLogUi"
import { LogDetailStatic } from "./LogDetailStatic"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Switch } from "../ui/switch"
import { Label } from "../ui/label"
import { Button } from "../ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../ui/dialog"
import { toast } from "sonner"
import { ChevronLeft, ChevronRight, RefreshCw, Trash2, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

const LOG_PAGE_SIZE = 50

export function RequestLogs() {
  const { token } = useAuth()
  const [logs, setLogs] = useState<LogListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<LogDetail | null>(null)
  const [hideGeneration, setHideGeneration] = useState(false)

  const fetchLogs = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const offset = page * LOG_PAGE_SIZE
      const exclude = hideGeneration ? "&exclude_operations=generate_image%2Cgenerate_video" : ""
      const r = await adminFetch(`/api/logs?limit=${LOG_PAGE_SIZE}&offset=${offset}${exclude}`, token)
      if (!r?.ok) throw new Error("fetch failed")
      const data = (await r.json()) as LogsListResponse | LogListItem[]
      if (Array.isArray(data)) {
        setLogs(data)
        setTotal(data.length)
      } else if (data && Array.isArray(data.logs)) {
        setLogs(data.logs)
        setTotal(typeof data.total === "number" ? data.total : data.logs.length)
      } else {
        setLogs([])
        setTotal(0)
      }
    } catch {
      toast.error("Failed to load logs")
    } finally {
      setLoading(false)
    }
  }, [token, page, hideGeneration])

  useEffect(() => {
    queueMicrotask(() => {
      void fetchLogs()
    })
  }, [fetchLogs])

  const clearLogs = async () => {
    if (!token) return
    if (!confirm("Clear all request logs? This cannot be undone.")) return
    try {
      const r = await adminFetch("/api/logs", token, { method: "DELETE" })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        toast.success("Logs cleared")
        setLogs([])
        setTotal(0)
        setPage(0)
        setDetailOpen(false)
        setDetail(null)
      } else toast.error(d.message || "Failed to clear logs")
    } catch {
      toast.error("Network error")
    }
  }

  const openDetail = async (id: number) => {
    if (!token) return
    setDetailOpen(true)
    setDetailLoading(true)
    setDetail(null)
    try {
      const { ok, data } = await adminJson<LogDetail>(`/api/logs/${id}`, token)
      if (!ok || !data) {
        toast.error("Failed to load log details")
        return
      }
      setDetail(data)
    } catch {
      toast.error("Failed to load log details")
    } finally {
      setDetailLoading(false)
    }
  }

  const colCount = 10

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4 pb-4 border-b">
        <div className="space-y-1">
          <CardTitle className="text-lg font-semibold">Request logs</CardTitle>
          <div className="flex items-center gap-2">
            <Switch
              id="hide-gen-logs"
              checked={hideGeneration}
              onCheckedChange={(v) => {
                setHideGeneration(Boolean(v))
                setPage(0)
              }}
            />
            <Label htmlFor="hide-gen-logs" className="text-xs font-normal text-muted-foreground cursor-pointer">
              Hide image/video generation rows
            </Label>
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={clearLogs}
            className="h-8 text-sm text-destructive hover:text-destructive hover:bg-destructive/10"
          >
            <Trash2 className="h-4 w-4 mr-1" />
            Clear
          </Button>
          <Button variant="ghost" size="icon" onClick={() => void fetchLogs()} disabled={loading} className="h-8 w-8" title="Refresh">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="w-full overflow-auto max-h-[min(70vh,600px)]">
          <Table>
            <TableHeader className="sticky top-0 z-20 bg-background">
              <TableRow className="hover:bg-transparent border-b">
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Operation</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">API key</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Token email</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Status</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Progress</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">HTTP</TableHead>
                <TableHead className="h-10 w-[17rem] max-w-[17rem] px-3 text-left font-medium text-muted-foreground">Summary</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Duration (s)</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Time</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!logs.length ? (
                <TableRow>
                  <TableCell colSpan={colCount} className="py-8 px-3 text-center text-sm text-muted-foreground">
                    {loading ? (
                      <span className="inline-flex items-center gap-2 justify-center">
                        <Loader2 className="h-5 w-5 animate-spin" />
                        Loading…
                      </span>
                    ) : (
                      "No logs yet"
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                logs.map((log) => {
                  const outcome = formatOutcome(log)
                  const outcomePreview = outcome.length > 96 ? `${outcome.slice(0, 93)}…` : outcome
                  const email = log.token_email || "Unknown"
                  const keyLabel = log.api_key_label || log.api_key_prefix || ""
                  return (
                    <TableRow key={log.id} className="border-border/60">
                      <TableCell className="py-2.5 px-3 text-sm align-top">{log.operation || "-"}</TableCell>
                      <TableCell className="py-2.5 px-3 text-xs align-top">
                        <span
                          className={cn(keyLabel ? "text-foreground" : "text-muted-foreground")}
                          title={keyLabel || undefined}
                        >
                          {keyLabel || "—"}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 px-3 text-xs align-top">
                        <span className={cn(log.token_email ? "text-primary" : "text-muted-foreground")} title={email}>
                          {email}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top">
                        <span
                          className={cn("inline-flex items-center rounded px-2 py-0.5 text-xs", logStatusPillClass(log))}
                        >
                          {formatLogStatus(log)}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 px-3 text-xs align-top text-foreground">
                        {formatLogProgressField(log)}
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top">
                        <span
                          className={cn(
                            "inline-flex items-center rounded px-2 py-0.5 text-xs",
                            statusCodePillClass(log.status_code)
                          )}
                        >
                          {log.status_code ?? "-"}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top w-[17rem] max-w-[17rem]">
                        <div
                          className={cn(
                            "text-xs leading-5 whitespace-pre-wrap break-words line-clamp-2",
                            formatLogOutcomeRowClass(log)
                          )}
                          title={outcome}
                        >
                          {outcomePreview}
                        </div>
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top tabular-nums text-sm">
                        {Number(log.duration || 0).toFixed(2)}
                      </TableCell>
                      <TableCell className="py-2.5 px-3 text-xs text-muted-foreground align-top whitespace-nowrap">
                        {log.created_at ? new Date(log.created_at).toLocaleString("en-US") : "—"}
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void openDetail(log.id)}
                          className="h-7 px-2 text-xs hover:bg-accent hover:text-accent-foreground"
                        >
                          View
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
          <span>
            {total === 0
              ? "No entries"
              : `Showing ${page * LOG_PAGE_SIZE + 1}–${Math.min(page * LOG_PAGE_SIZE + logs.length, total)} of ${total}`}
          </span>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8"
              disabled={loading || page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8"
              disabled={loading || (page + 1) * LOG_PAGE_SIZE >= total}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </CardContent>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent
          className={cn(
            "flex max-h-[80vh] w-[calc(100vw-2rem)] max-w-3xl translate-x-[-50%] translate-y-[-50%] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl sm:rounded-xl",
            "border-border bg-background text-foreground shadow-lg"
          )}
        >
          <DialogHeader className="shrink-0 flex-row items-center justify-between space-y-0 border-b border-border p-5 text-left pr-12">
            <DialogTitle className="text-lg font-semibold leading-none tracking-tight">Log details</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto bg-background p-5">
            {detailLoading ? (
              <div className="rounded-lg border border-border bg-muted/50 p-4 text-sm text-muted-foreground">Loading log details…</div>
            ) : detail ? (
              <LogDetailStatic log={detail} />
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">No data</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
