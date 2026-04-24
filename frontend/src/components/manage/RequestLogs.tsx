import { useState, useEffect, useCallback } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import type { LogDetail, LogListItem } from "../../types/admin"
import {
  formatLogStatus,
  formatOutcome,
  formatProgressLabel,
  formatRelativeTime,
  getOperationKind,
  httpCodeTone,
  operationChipClass,
  operationLabel,
  outcomeTone,
  progressPercent,
  statusTone,
  tonePillClass,
  toneTextClass,
  tryFormatJson,
} from "./requestLogUi"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Badge } from "../ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../ui/dialog"
import { ScrollArea } from "../ui/scroll-area"
import { toast } from "sonner"
import { RefreshCw, Trash2, Loader2, Copy } from "lucide-react"
import { cn } from "@/lib/utils"

async function copyText(text: string, okMsg = "Copied") {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(okMsg)
  } catch {
    toast.error("Copy failed")
  }
}

function JsonBlock({
  title,
  body,
  maxHeight,
}: {
  title: string
  body: string | null | undefined
  maxHeight: string
}) {
  const formatted = tryFormatJson(body) || "—"
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold tracking-tight">{title}</h4>
        {formatted !== "—" ? (
          <Button type="button" variant="ghost" size="sm" className="h-8 shrink-0 gap-1 text-xs" onClick={() => void copyText(formatted, `${title} copied`)}>
            <Copy className="h-3.5 w-3.5" />
            Copy
          </Button>
        ) : null}
      </div>
      <div className="rounded-lg border border-border bg-muted/40 p-0 overflow-hidden">
        <ScrollArea className={cn("w-full", maxHeight)}>
          <pre className="p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-foreground/90">{formatted}</pre>
        </ScrollArea>
      </div>
    </div>
  )
}

export function RequestLogs() {
  const { token } = useAuth()
  const [logs, setLogs] = useState<LogListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<LogDetail | null>(null)

  const fetchLogs = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const r = await adminFetch("/api/logs?limit=100", token)
      if (!r?.ok) throw new Error("fetch failed")
      const data = (await r.json()) as LogListItem[]
      setLogs(Array.isArray(data) ? data : [])
    } catch {
      toast.error("Failed to load logs")
    } finally {
      setLoading(false)
    }
  }, [token])

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
        setDetailOpen(false)
        setDetail(null)
      } else toast.error(d.message || "Clear failed")
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
        toast.error("Failed to load log detail")
        return
      }
      setDetail(data)
    } catch {
      toast.error("Failed to load log detail")
    } finally {
      setDetailLoading(false)
    }
  }

  const colCount = 11

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-4 border-b">
        <div>
          <CardTitle className="text-lg">Request logs</CardTitle>
          <p className="text-xs text-muted-foreground mt-1">Last 100 entries · semantic colors for status and HTTP code</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={clearLogs} className="text-destructive hover:text-destructive hover:bg-destructive/10">
            <Trash2 className="h-4 w-4 mr-2" /> Clear
          </Button>
          <Button variant="outline" size="icon" onClick={() => void fetchLogs()} disabled={loading} title="Refresh">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="w-full overflow-auto max-h-[min(70vh,720px)] rounded-b-lg border-t border-border/60">
          <Table>
            <TableHeader className="sticky top-0 z-20 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 shadow-sm">
              <TableRow className="hover:bg-transparent border-b">
                <TableHead className="w-[72px] text-[11px] uppercase tracking-wide text-muted-foreground">Log</TableHead>
                <TableHead className="w-[88px] text-[11px] uppercase tracking-wide text-muted-foreground">Operation</TableHead>
                <TableHead className="min-w-[140px] max-w-[200px] text-[11px] uppercase tracking-wide text-muted-foreground">Token</TableHead>
                <TableHead className="w-[120px] text-[11px] uppercase tracking-wide text-muted-foreground">Status</TableHead>
                <TableHead className="w-[100px] text-[11px] uppercase tracking-wide text-muted-foreground">Progress</TableHead>
                <TableHead className="w-[64px] text-[11px] uppercase tracking-wide text-muted-foreground">HTTP</TableHead>
                <TableHead className="min-w-[12rem] max-w-[18rem] text-[11px] uppercase tracking-wide text-muted-foreground">Summary</TableHead>
                <TableHead className="w-[72px] text-[11px] uppercase tracking-wide text-muted-foreground">Duration</TableHead>
                <TableHead className="w-[128px] text-[11px] uppercase tracking-wide text-muted-foreground">Time</TableHead>
                <TableHead className="w-[72px] text-right text-[11px] uppercase tracking-wide text-muted-foreground"> </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!logs.length ? (
                <TableRow>
                  <TableCell colSpan={colCount} className="text-center text-muted-foreground py-14 text-sm">
                    {loading ? (
                      <span className="inline-flex items-center gap-2 justify-center">
                        <Loader2 className="h-5 w-5 animate-spin" /> Loading…
                      </span>
                    ) : (
                      "No request logs yet"
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                logs.map((log) => {
                  const opKind = getOperationKind(log.operation)
                  const stTone = statusTone(log)
                  const httpTone = httpCodeTone(log.status_code ?? undefined)
                  const outTone = outcomeTone(log)
                  const pct = progressPercent(log)
                  const email = log.token_email || "—"
                  const emailTitle = [log.token_username, log.token_email].filter(Boolean).join(" · ") || email

                  return (
                    <TableRow key={log.id} className="group border-border/60 hover:bg-muted/50 transition-colors">
                      <TableCell className="align-top py-2.5">
                        <span className="font-mono text-[11px] text-muted-foreground tabular-nums" title={`Log id ${log.id}`}>
                          #{log.id}
                        </span>
                        {log.token_id != null ? (
                          <div className="font-mono text-[10px] text-muted-foreground/80 tabular-nums mt-0.5" title={`Token id ${log.token_id}`}>
                            t{log.token_id}
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell className="align-top py-2.5">
                        <span
                          className={cn(
                            "inline-flex items-center rounded-md border px-2 py-0.5 text-[11px]",
                            operationChipClass(opKind)
                          )}
                          title={log.operation || ""}
                        >
                          {operationLabel(opKind, log.operation)}
                        </span>
                      </TableCell>
                      <TableCell className="align-top py-2.5 max-w-[200px]">
                        <div className="flex items-start gap-1">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-xs font-medium text-foreground" title={emailTitle}>
                              {email}
                            </div>
                          </div>
                          {log.token_email ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 shrink-0 opacity-60 group-hover:opacity-100"
                              title="Copy email"
                              onClick={(e) => {
                                e.stopPropagation()
                                void copyText(log.token_email!, "Email copied")
                              }}
                            >
                              <Copy className="h-3.5 w-3.5" />
                            </Button>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="align-top py-2.5">
                        <span className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-[11px]", tonePillClass[stTone])}>
                          {formatLogStatus(log)}
                        </span>
                      </TableCell>
                      <TableCell className="align-top py-2.5">
                        <div className="flex flex-col gap-1 w-full max-w-[92px]">
                          {pct != null ? (
                            <>
                              <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                                <div className="h-full rounded-full bg-primary/80 transition-all" style={{ width: `${pct}%` }} />
                              </div>
                              <span className="text-[10px] tabular-nums text-muted-foreground">{formatProgressLabel(log)}</span>
                            </>
                          ) : (
                            <span className="text-[10px] text-muted-foreground">—</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="align-top py-2.5">
                        <Badge variant="outline" className={cn("font-mono text-[11px] px-1.5 py-0 h-6 border", tonePillClass[httpTone])}>
                          {log.status_code ?? "—"}
                        </Badge>
                      </TableCell>
                      <TableCell className="align-top py-2.5 max-w-[18rem]">
                        <p
                          className={cn("text-xs leading-snug line-clamp-2", toneTextClass[outTone])}
                          title={formatOutcome(log)}
                        >
                          {formatOutcome(log)}
                        </p>
                      </TableCell>
                      <TableCell className="align-top py-2.5 tabular-nums text-xs text-muted-foreground">{Number(log.duration || 0).toFixed(2)}</TableCell>
                      <TableCell className="align-top py-2.5">
                        <div className="text-[11px] leading-tight whitespace-nowrap text-muted-foreground">
                          {log.created_at ? new Date(log.created_at).toLocaleString() : "—"}
                        </div>
                        {log.created_at ? (
                          <div className="text-[10px] text-muted-foreground/70 mt-0.5">{formatRelativeTime(log.created_at)}</div>
                        ) : null}
                      </TableCell>
                      <TableCell className="align-top py-2.5 text-right">
                        <Button variant="secondary" size="sm" className="h-8 text-xs" onClick={() => void openDetail(log.id)}>
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
      </CardContent>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-w-3xl max-h-[88vh] overflow-hidden flex flex-col gap-0 p-0 sm:max-w-3xl">
          <DialogHeader className="px-6 pt-6 pb-4 border-b shrink-0">
            <DialogTitle className="flex items-center gap-2 text-base">
              Log detail
              {detail ? (
                <span className="font-mono text-xs font-normal text-muted-foreground">#{detail.id}</span>
              ) : null}
            </DialogTitle>
          </DialogHeader>
          <div className="overflow-y-auto flex-1 px-6 py-4 space-y-6">
            {detailLoading ? (
              <div className="flex justify-center py-12">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : detail ? (
              <>
                <section className="space-y-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Overview</h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
                    <div className="rounded-lg border bg-card/50 p-3 space-y-2">
                      <div className="text-[11px] text-muted-foreground">Operation</div>
                      <div className="flex flex-wrap gap-2 items-center">
                        <span className={cn("inline-flex rounded-md border px-2 py-0.5 text-xs", operationChipClass(getOperationKind(detail.operation)))}>
                          {operationLabel(getOperationKind(detail.operation), detail.operation)}
                        </span>
                        {detail.operation ? (
                          <span className="font-mono text-[11px] text-muted-foreground truncate" title={detail.operation}>
                            {detail.operation}
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="rounded-lg border bg-card/50 p-3 space-y-2">
                      <div className="text-[11px] text-muted-foreground">HTTP</div>
                      <Badge variant="outline" className={cn("font-mono text-sm", tonePillClass[httpCodeTone(detail.status_code ?? undefined)])}>
                        {detail.status_code ?? "—"}
                      </Badge>
                    </div>
                    <div className="rounded-lg border bg-card/50 p-3 space-y-1 sm:col-span-2">
                      <div className="text-[11px] text-muted-foreground">Status</div>
                      <span className={cn("inline-flex rounded-md border px-2 py-0.5 text-xs", tonePillClass[statusTone(detail)])}>{formatLogStatus(detail)}</span>
                    </div>
                    <div className="rounded-lg border bg-card/50 p-3 space-y-1">
                      <div className="text-[11px] text-muted-foreground">Token</div>
                      <div className="text-sm break-all">{detail.token_email || "—"}</div>
                      {detail.token_username ? <div className="text-xs text-muted-foreground">@{detail.token_username}</div> : null}
                      {detail.token_id != null ? (
                        <div className="font-mono text-xs text-muted-foreground">Token id: {detail.token_id}</div>
                      ) : null}
                    </div>
                    <div className="rounded-lg border bg-card/50 p-3 space-y-1">
                      <div className="text-[11px] text-muted-foreground">Duration</div>
                      <div className="text-sm tabular-nums">{Number(detail.duration || 0).toFixed(2)}s</div>
                    </div>
                    <div className="rounded-lg border bg-card/50 p-3 space-y-1">
                      <div className="text-[11px] text-muted-foreground">Created</div>
                      <div className="text-sm">{detail.created_at ? new Date(detail.created_at).toLocaleString() : "—"}</div>
                      {detail.created_at ? <div className="text-xs text-muted-foreground">{formatRelativeTime(detail.created_at)}</div> : null}
                    </div>
                    <div className="rounded-lg border bg-card/50 p-3 space-y-1">
                      <div className="text-[11px] text-muted-foreground">Updated</div>
                      <div className="text-sm">{detail.updated_at ? new Date(detail.updated_at).toLocaleString() : "—"}</div>
                    </div>
                  </div>
                </section>

                {detail.error_summary ? (
                  <section className="space-y-2">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-destructive">Error</h3>
                    <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive leading-relaxed">{detail.error_summary}</div>
                  </section>
                ) : null}

                <section className="space-y-3 border-t pt-6">
                  <JsonBlock title="Request body" body={detail.request_body} maxHeight="min(220px,28vh)" />
                </section>
                <section className="space-y-3">
                  <JsonBlock title="Response body" body={detail.response_body} maxHeight="min(320px,36vh)" />
                </section>
              </>
            ) : (
              <p className="text-muted-foreground text-sm py-8 text-center">No data</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
