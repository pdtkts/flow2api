import { useState, useEffect, useCallback } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import type { LogDetail, LogListItem } from "../../types/admin"
import {
  formatLogOutcomeRowClass,
  formatLogOutcomeZh,
  formatLogProgressField,
  formatLogStatusZh,
  logStatusPillClass,
  statusCodePillClass,
} from "./requestLogDetail"
import { LogDetailStatic } from "./LogDetailStatic"
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../ui/dialog"
import { toast } from "sonner"
import { RefreshCw, Trash2, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

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
      toast.error("加载日志失败")
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
    if (!confirm("确定要清空所有日志吗？此操作不可恢复！")) return
    try {
      const r = await adminFetch("/api/logs", token, { method: "DELETE" })
      if (!r) return
      const d = await r.json().catch(() => ({}))
      if (d.success) {
        toast.success("日志已清空")
        setLogs([])
        setDetailOpen(false)
        setDetail(null)
      } else toast.error(d.message || "清空失败")
    } catch {
      toast.error("网络错误")
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
        toast.error("加载日志详情失败")
        return
      }
      setDetail(data)
    } catch {
      toast.error("加载日志详情失败")
    } finally {
      setDetailLoading(false)
    }
  }

  const colCount = 9

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4 pb-4 border-b">
        <CardTitle className="text-lg font-semibold">请求日志</CardTitle>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={clearLogs}
            className="h-8 text-sm text-destructive hover:text-destructive hover:bg-red-50"
          >
            <Trash2 className="h-4 w-4 mr-1" />
            清空
          </Button>
          <Button variant="ghost" size="icon" onClick={() => void fetchLogs()} disabled={loading} className="h-8 w-8" title="刷新">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="w-full overflow-auto max-h-[min(70vh,600px)]">
          <Table>
            <TableHeader className="sticky top-0 z-20 bg-background">
              <TableRow className="hover:bg-transparent border-b">
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">操作</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">Token 邮箱</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">状态</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">进度</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">状态码</TableHead>
                <TableHead className="h-10 w-[17rem] max-w-[17rem] px-3 text-left font-medium text-muted-foreground">结果摘要</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">耗时(秒)</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">时间</TableHead>
                <TableHead className="h-10 px-3 text-left font-medium text-muted-foreground">详情</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!logs.length ? (
                <TableRow>
                  <TableCell colSpan={colCount} className="py-8 px-3 text-center text-sm text-muted-foreground">
                    {loading ? (
                      <span className="inline-flex items-center gap-2 justify-center">
                        <Loader2 className="h-5 w-5 animate-spin" />
                        加载中…
                      </span>
                    ) : (
                      "暂无日志"
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                logs.map((log) => {
                  const outcome = formatLogOutcomeZh(log)
                  const outcomePreview = outcome.length > 96 ? `${outcome.slice(0, 93)}…` : outcome
                  const email = log.token_email || "未知"
                  return (
                    <TableRow key={log.id} className="border-border/60">
                      <TableCell className="py-2.5 px-3 text-sm align-top">{log.operation || "-"}</TableCell>
                      <TableCell className="py-2.5 px-3 text-xs align-top">
                        <span className={cn(log.token_email ? "text-blue-600" : "text-muted-foreground")} title={email}>
                          {email}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top">
                        <span
                          className={cn("inline-flex items-center rounded px-2 py-0.5 text-xs", logStatusPillClass(log))}
                        >
                          {formatLogStatusZh(log)}
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
                        {log.created_at ? new Date(log.created_at).toLocaleString("zh-CN") : "-"}
                      </TableCell>
                      <TableCell className="py-2.5 px-3 align-top">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void openDetail(log.id)}
                          className="h-7 px-2 text-xs hover:bg-blue-50 hover:text-blue-700"
                        >
                          详情
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
        <DialogContent className="flex max-h-[80vh] w-[calc(100vw-2rem)] max-w-3xl translate-x-[-50%] translate-y-[-50%] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          <DialogHeader className="shrink-0 flex-row items-center justify-between space-y-0 border-b border-border p-5 text-left">
            <DialogTitle className="text-lg font-semibold leading-none">日志详情</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {detailLoading ? (
              <div className="rounded-md border border-border p-4 bg-muted/30 text-sm text-muted-foreground">
                日志详情加载中…
              </div>
            ) : detail ? (
              <LogDetailStatic log={detail} />
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">无数据</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
