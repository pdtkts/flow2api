import { useMemo, useEffect } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { Layout } from "../components/Layout"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs"
import { TokenManagement } from "../components/manage/TokenManagement"
import { SystemSettings } from "../components/manage/SystemSettings"
import { RequestLogs } from "../components/manage/RequestLogs"
import { CacheManagement } from "../components/manage/CacheManagement"
import { AgentGateway } from "../components/manage/AgentGateway"
import { ApiKeyManagement } from "../components/manage/ApiKeyManagement"
import { cn } from "@/lib/utils"

const MANAGE_TABS = ["tokens", "apikeys", "settings", "logs", "cache", "agent"] as const
type ManageTab = (typeof MANAGE_TABS)[number]

function parseManageTab(raw: string | null): ManageTab {
  if (raw && (MANAGE_TABS as readonly string[]).includes(raw)) return raw as ManageTab
  return "tokens"
}

export default function Manage() {
  const [searchParams, setSearchParams] = useSearchParams()
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
              value="cache"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Cache management
            </TabsTrigger>
            <TabsTrigger
              value="agent"
              className={cn(
                "rounded-none border-b-2 border-transparent px-1 py-3 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              )}
            >
              Agent gateway
            </TabsTrigger>
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
        <TabsContent value="cache" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <CacheManagement active={true} />
          </div>
        </TabsContent>
        <TabsContent value="agent" className="mt-0 outline-none focus-visible:ring-0">
          <div className="animate-in fade-in duration-300">
            <AgentGateway />
          </div>
        </TabsContent>
      </Tabs>
    </Layout>
  )
}
