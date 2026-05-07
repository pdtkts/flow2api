import { useMemo, useEffect, useState, useCallback } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { Layout } from "../components/Layout"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs"
import { TokenManagement } from "../components/manage/TokenManagement"
import { SystemSettings } from "../components/manage/SystemSettings"
import { RequestLogs } from "../components/manage/RequestLogs"
import { CacheManagement } from "../components/manage/CacheManagement"
import { AgentGateway } from "../components/manage/AgentGateway"
import { ApiKeyManagement } from "../components/manage/ApiKeyManagement"
import { MetadataSettings } from "../components/manage/MetadataSettings"
import { CloningSettings } from "../components/manage/CloningSettings"
import { TaskTrackerSettings } from "../components/manage/TaskTrackerSettings"
import { cn } from "@/lib/utils"
import { useAuth } from "../contexts/AuthContext"
import { adminJson } from "../lib/adminApi"

const MANAGE_TABS = ["tokens", "apikeys", "settings", "metadata", "cloning", "tracker", "logs", "cache", "agent"] as const
type ManageTab = (typeof MANAGE_TABS)[number]

function parseManageTab(raw: string | null): ManageTab {
  if (raw && (MANAGE_TABS as readonly string[]).includes(raw)) return raw as ManageTab
  return "tokens"
}

export default function Manage() {
  const { token } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const [showAgentTab, setShowAgentTab] = useState(false)
  const tab = useMemo(
    () => parseManageTab(searchParams.get("tab")),
    [searchParams]
  )
  const setTab = (v: string) => {
    if (v === "tokens") setSearchParams({})
    else setSearchParams({ tab: v })
  }
  useEffect(() => {
    const raw = searchParams.get("tab")
    if (raw && !MANAGE_TABS.includes(raw as ManageTab)) {
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams])

  const refreshAgentVisibility = useCallback(async () => {
    const resp = await adminJson<Record<string, unknown>>("/api/captcha/config", token)
    if (!resp.ok || !resp.data) return
    const captchaMethod = String(resp.data.captcha_method || "")
    const browserFallback = resp.data.browser_fallback_to_remote_browser !== false
    const visible = captchaMethod === "remote_browser" || (captchaMethod === "browser" && browserFallback)
    setShowAgentTab(visible)
  }, [token])

  useEffect(() => {
    void refreshAgentVisibility()
    const timer = window.setInterval(() => {
      void refreshAgentVisibility()
    }, 5000)
    return () => window.clearInterval(timer)
  }, [refreshAgentVisibility])

  useEffect(() => {
    if (tab === "agent" && !showAgentTab) {
      setTab("settings")
    }
  }, [tab, showAgentTab])

  return (
    <Layout>
      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <div className="border-b border-border mb-6 flex flex-wrap items-end gap-6">
          <TabsList className="h-auto w-full min-w-0 flex-1 justify-start rounded-none bg-transparent p-0 gap-6">
            <TabsTrigger
              value="tokens"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Token management
            </TabsTrigger>
            <TabsTrigger
              value="apikeys"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              API key manager
            </TabsTrigger>
            <TabsTrigger
              value="settings"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              System settings
            </TabsTrigger>
            <TabsTrigger
              value="logs"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Request logs
            </TabsTrigger>
            <TabsTrigger
              value="metadata"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Metadata
            </TabsTrigger>
            <TabsTrigger
              value="cloning"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Cloning
            </TabsTrigger>
            <TabsTrigger
              value="tracker"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Task Tracker
            </TabsTrigger>
            <TabsTrigger
              value="cache"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Cache management
            </TabsTrigger>
            {showAgentTab ? (
              <TabsTrigger
                value="agent"
                className={cn(
                  "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                )}
              >
                Agent gateway
              </TabsTrigger>
            ) : null}
          </TabsList>
          <Link
            to="/test"
            className={cn(
              "text-sm font-medium py-3 px-1 border-b-2 border-transparent text-muted-foreground hover:text-foreground transition-colors shrink-0 mb-px"
            )}
          >
            Test page
          </Link>
        </div>

        <TabsContent value="tokens" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <TokenManagement />
          </div>
        </TabsContent>
        <TabsContent value="settings" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <SystemSettings active={true} />
          </div>
        </TabsContent>
        <TabsContent value="apikeys" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <ApiKeyManagement />
          </div>
        </TabsContent>
        <TabsContent value="logs" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <RequestLogs />
          </div>
        </TabsContent>
        <TabsContent value="metadata" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <MetadataSettings active={true} />
          </div>
        </TabsContent>
        <TabsContent value="cloning" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <CloningSettings active={true} />
          </div>
        </TabsContent>
        <TabsContent value="tracker" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <TaskTrackerSettings active={true} />
          </div>
        </TabsContent>
        <TabsContent value="cache" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <CacheManagement active={true} />
          </div>
        </TabsContent>
        {showAgentTab ? (
          <TabsContent value="agent" className="mt-0 outline-none focus-visible:ring-0">
            <div className="animate-in fade-in duration-300">
              <AgentGateway />
            </div>
          </TabsContent>
        ) : null}
      </Tabs>
    </Layout>
  )
}
