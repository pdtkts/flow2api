import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table"
import { Key } from "lucide-react"

const CLOUDFLARE_MODELS = [
  "@cf/moonshotai/kimi-k2.6",
  "@cf/zai-org/glm-4.7-flash",
  "@cf/meta/llama-4-scout-17b-16e-instruct",
  "@cf/google/gemma-4-26b-a4b-it",
]

const PRESET_MODELS: Record<string, string[]> = {
  gemini_native: ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"],
  openai: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-4.1-mini", "gpt-4.1"],
  openrouter: ["google/gemma-4-26b-a4b-it"],
  third_party_gemini: ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"],
  cloudflare: CLOUDFLARE_MODELS,
}

const MARKET_BACKENDS = ["gemini_native", "openai", "openrouter", "third_party_gemini", "cloudflare"]

export function MarketSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [providerOrder, setProviderOrder] = useState<string[]>(MARKET_BACKENDS)
  const [enabledProviders, setEnabledProviders] = useState<string[]>(["gemini_native"])
  const [providerRetryCount, setProviderRetryCount] = useState(1)
  const [model, setModel] = useState("gemini-2.5-flash")
  const [enabledModels, setEnabledModels] = useState<string[]>(["gemini-2.5-flash"])
  const [primaryModel, setPrimaryModel] = useState("gemini-2.5-flash")

  const [geminiKeys, setGeminiKeys] = useState("")
  const [openaiKeys, setOpenaiKeys] = useState("")
  const [thirdPartyKeys, setThirdPartyKeys] = useState("")
  const [thirdPartyBaseUrl, setThirdPartyBaseUrl] = useState("")
  const [openrouterKeys, setOpenrouterKeys] = useState("")
  const [cloudflareAccountId, setCloudflareAccountId] = useState("")
  const [cloudflareApiToken, setCloudflareApiToken] = useState("")

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{ success?: boolean; config?: Record<string, unknown> }>("/api/config/generation", token)
    if (!resp.ok || !resp.data?.success || !resp.data.config) return
    const c = resp.data.config
    const legacyBackend = String(c.flow2api_market_backend || "gemini_native").trim() || "gemini_native"
    const orderFromConfig = String(c.flow2api_market_provider_order || "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
      .filter((x) => MARKET_BACKENDS.includes(x))
    const normalizedOrder = [
      ...(orderFromConfig.length ? orderFromConfig : [legacyBackend]),
      ...MARKET_BACKENDS.filter((x) => !(orderFromConfig.length ? orderFromConfig : [legacyBackend]).includes(x)),
    ]
    const enabledFromConfig = String(c.flow2api_market_enabled_providers || "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
      .filter((x) => normalizedOrder.includes(x))
    const normalizedEnabledProviders = enabledFromConfig.length ? enabledFromConfig : [legacyBackend]
    const selectedProvider = normalizedOrder.find((p) => normalizedEnabledProviders.includes(p)) || normalizedOrder[0] || "gemini_native"
    setProviderOrder(normalizedOrder)
    setEnabledProviders(normalizedEnabledProviders)
    setProviderRetryCount(
      Math.max(0, Math.min(5, Number(c.flow2api_market_provider_retry_count ?? 1) || 1))
    )
    setModel(String(c.flow2api_market_model || PRESET_MODELS[selectedProvider]?.[0] || "gemini-2.5-flash"))
    const configuredEnabledRaw = String(c.flow2api_market_enabled_models || "").trim()
    const configuredPrimary = String(c.flow2api_market_primary_model || "").trim()
    const configuredFallbackRaw = String(c.flow2api_market_fallback_models || "").trim()
    const legacyModel = String(c.flow2api_market_model || "gemini-2.5-flash").trim() || "gemini-2.5-flash"
    const enabled = (configuredEnabledRaw ? configuredEnabledRaw.split(",") : [])
      .map((m) => m.trim())
      .filter(Boolean)
    const fallback = (configuredFallbackRaw ? configuredFallbackRaw.split(",") : [])
      .map((m) => m.trim())
      .filter(Boolean)
    const primary = configuredPrimary || legacyModel
    const normalizedEnabled = Array.from(new Set([...(enabled.length ? enabled : [legacyModel]), primary, ...fallback]))
    setEnabledModels(normalizedEnabled)
    setPrimaryModel(primary)
    setModel(primary)

    setGeminiKeys(String(c.flow2api_gemini_api_keys || ""))
    setOpenaiKeys(String(c.flow2api_openai_api_keys || ""))
    setThirdPartyKeys(String(c.flow2api_third_party_gemini_api_keys || ""))
    setThirdPartyBaseUrl(String(c.flow2api_third_party_gemini_base_url || ""))
    setOpenrouterKeys(String(c.flow2api_openrouter_api_keys || ""))
    setCloudflareAccountId(String(c.cloudflare_account_id || ""))
    setCloudflareApiToken(String(c.cloudflare_api_token || ""))
  }, [token, active])

  const selectedProvider = useMemo(
    () => providerOrder.find((provider) => enabledProviders.includes(provider)) || providerOrder[0] || "gemini_native",
    [providerOrder, enabledProviders],
  )

  const backendModels = useMemo(() => PRESET_MODELS[selectedProvider] || [], [selectedProvider])

  const allModels = useMemo(() => {
    const fromEnabled = enabledModels.map((m) => m.trim()).filter(Boolean)
    const remainingPresetModels = backendModels.filter((m) => !fromEnabled.includes(m))
    return [...fromEnabled, ...remainingPresetModels]
  }, [enabledModels, backendModels])

  const fallbackModels = useMemo(
    () => enabledModels.filter((m) => m !== primaryModel),
    [enabledModels, primaryModel],
  )

  const toggleModel = (modelName: string, checked: boolean) => {
    const value = modelName.trim()
    if (!value) return
    setEnabledModels((prev) => {
      if (checked) return Array.from(new Set([...prev, value]))
      const next = prev.filter((m) => m !== value)
      if (next.length === 0) return prev
      if (!next.includes(primaryModel)) {
        setPrimaryModel(next[0])
        setModel(next[0])
      }
      return next
    })
  }

  const moveModel = (modelName: string, direction: -1 | 1) => {
    setEnabledModels((prev) => {
      const idx = prev.indexOf(modelName)
      if (idx < 0) return prev
      const target = idx + direction
      if (target < 0 || target >= prev.length) return prev
      const next = [...prev]
      const tmp = next[idx]
      next[idx] = next[target]
      next[target] = tmp
      return next
    })
  }

  const toggleProvider = (providerName: string, checked: boolean) => {
    const value = providerName.trim()
    if (!value) return
    setEnabledProviders((prev) => {
      if (checked) return Array.from(new Set([...prev, value]))
      const next = prev.filter((p) => p !== value)
      return next.length ? next : prev
    })
  }

  const moveProvider = (providerName: string, direction: -1 | 1) => {
    setProviderOrder((prev) => {
      const idx = prev.indexOf(providerName)
      if (idx < 0) return prev
      const target = idx + direction
      if (target < 0 || target >= prev.length) return prev
      const next = [...prev]
      const tmp = next[idx]
      next[idx] = next[target]
      next[target] = tmp
      return next
    })
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const save = async () => {
    if (!token) return
    setBusy(true)
    try {
      const r = await adminFetch("/api/config/generation", token, {
        method: "POST",
        body: JSON.stringify({
          flow2api_market_backend: selectedProvider,
          flow2api_market_provider_order: providerOrder.join(","),
          flow2api_market_enabled_providers: enabledProviders.join(","),
          flow2api_market_provider_retry_count: providerRetryCount,
          flow2api_market_model: model,
          flow2api_market_enabled_models: enabledModels.join(","),
          flow2api_market_primary_model: primaryModel,
          flow2api_market_fallback_models: fallbackModels.join(","),

          flow2api_gemini_api_keys: geminiKeys,
          flow2api_openai_api_keys: openaiKeys,
          flow2api_openrouter_api_keys: openrouterKeys,
          flow2api_third_party_gemini_api_keys: thirdPartyKeys,
          flow2api_third_party_gemini_base_url: thirdPartyBaseUrl,
          cloudflare_account_id: cloudflareAccountId,
          cloudflare_api_token: cloudflareApiToken,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Market analysis settings saved")
      else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  if (!active) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Market analysis</CardTitle>
        <CardDescription>
          Configure LLM providers for <code className="text-xs">POST /api/market/analyze-keyword</code> (keyword insights from TAS data).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Label>Market providers (ranked fallback)</Label>
          <div className="rounded-md border overflow-hidden">
            <div className="grid grid-cols-[1fr_90px_110px] gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-medium">
              <span>Provider</span>
              <span className="text-center">Enabled</span>
              <span className="text-center">Order</span>
            </div>
            <div className="max-h-[260px] overflow-y-auto">
              {providerOrder.map((entry) => {
                const enabled = enabledProviders.includes(entry)
                return (
                  <div key={entry} className="grid grid-cols-[1fr_90px_110px] items-center gap-2 border-b last:border-0 px-3 py-2 text-sm hover:bg-muted/20 transition-colors">
                    <span className="font-mono truncate" title={entry}>{entry}</span>
                    <div className="flex justify-center">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-primary rounded cursor-pointer"
                        checked={enabled}
                        onChange={(e) => toggleProvider(entry, e.target.checked)}
                      />
                    </div>
                    <div className="flex gap-1 justify-center">
                      <Button type="button" size="sm" variant="outline" onClick={() => moveProvider(entry, -1)} className="h-7 px-2">Up</Button>
                      <Button type="button" size="sm" variant="outline" onClick={() => moveProvider(entry, 1)} className="h-7 px-2">Down</Button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Uses the first enabled provider, then falls back in order.
          </p>
          <div className="max-w-xs">
            <Label htmlFor="market-provider-retries">Retry per provider before fallback</Label>
            <Input
              id="market-provider-retries"
              type="number"
              min={0}
              max={5}
              className="mt-1 w-32 font-mono text-sm"
              value={providerRetryCount}
              onChange={(e) => setProviderRetryCount(Math.max(0, Math.min(5, Number(e.target.value) || 0)))}
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label>Models (preset)</Label>
          <div className="rounded-md border overflow-hidden">
            <div className="grid grid-cols-[1fr_90px_90px_110px] gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-medium">
              <span>Model</span>
              <span className="text-center">Enabled</span>
              <span className="text-center">Primary</span>
              <span className="text-center">Order</span>
            </div>
            <div className="max-h-[300px] overflow-y-auto">
              {allModels.map((entry) => {
                const enabled = enabledModels.includes(entry)
                return (
                  <div key={entry} className="grid grid-cols-[1fr_90px_90px_110px] items-center gap-2 border-b last:border-0 px-3 py-2 text-sm hover:bg-muted/20 transition-colors">
                    <span className="font-mono truncate" title={entry}>{entry}</span>
                    <div className="flex justify-center">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-primary rounded cursor-pointer"
                        checked={enabled}
                        onChange={(e) => toggleModel(entry, e.target.checked)}
                      />
                    </div>
                    <div className="flex justify-center">
                      <input
                        type="radio"
                        name="market-primary-model"
                        className="h-4 w-4 accent-primary"
                        checked={primaryModel === entry}
                        disabled={!enabled}
                        onChange={() => {
                          setPrimaryModel(entry)
                          setModel(entry)
                        }}
                      />
                    </div>
                    <div className="flex gap-1 justify-center">
                      <Button type="button" size="sm" variant="outline" disabled={!enabled} onClick={() => moveModel(entry, -1)} className="h-7 px-2">Up</Button>
                      <Button type="button" size="sm" variant="outline" disabled={!enabled} onClick={() => moveModel(entry, 1)} className="h-7 px-2">Down</Button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <p className="text-xs text-muted-foreground mt-2">
            Fallback order follows enabled model order excluding primary.
          </p>
        </div>

        <div className="space-y-4 rounded-md border p-4 bg-muted/10">
          <div>
            <h3 className="font-semibold text-lg flex items-center gap-2">
              <Key className="w-5 h-5 text-primary" /> Provider credentials
            </h3>
            <p className="text-xs text-muted-foreground mt-1">
              Same shared keys as metadata generation. Multiple keys may be comma-separated for rotation.
            </p>
          </div>

          <div className="rounded-md border bg-background overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50 hover:bg-muted/50">
                  <TableHead className="w-[200px]">Provider / Service</TableHead>
                  <TableHead className="w-[380px]">Configuration Key</TableHead>
                  <TableHead>Value / Token</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell className="font-medium">Google Gemini</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">FLOW2API_GEMINI_API_KEYS</TableCell>
                  <TableCell>
                    <Textarea className="font-mono text-xs min-h-[60px] bg-muted/20 resize-y" placeholder="AIzaSy..., AIzaSy..." value={geminiKeys} onChange={(e) => setGeminiKeys(e.target.value)} />
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">OpenAI</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">FLOW2API_OPENAI_API_KEYS</TableCell>
                  <TableCell>
                    <Textarea className="font-mono text-xs min-h-[60px] bg-muted/20 resize-y" placeholder="sk-proj-..., sk-proj-..." value={openaiKeys} onChange={(e) => setOpenaiKeys(e.target.value)} />
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">OpenRouter</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">FLOW2API_OPENROUTER_API_KEYS</TableCell>
                  <TableCell>
                    <Textarea className="font-mono text-xs min-h-[60px] bg-muted/20 resize-y" placeholder="sk-or-v1-..., sk-or-v1-..." value={openrouterKeys} onChange={(e) => setOpenrouterKeys(e.target.value)} />
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Third-Party Gemini</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">FLOW2API_THIRD_PARTY_GEMINI_API_KEYS</TableCell>
                  <TableCell>
                    <Textarea className="font-mono text-xs min-h-[60px] bg-muted/20 resize-y" placeholder="Key1, Key2..." value={thirdPartyKeys} onChange={(e) => setThirdPartyKeys(e.target.value)} />
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium text-muted-foreground text-xs pl-8">↳ Base URL</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">FLOW2API_THIRD_PARTY_GEMINI_BASE_URL</TableCell>
                  <TableCell>
                    <Input className="font-mono text-xs h-8 bg-muted/20" placeholder="https://..." value={thirdPartyBaseUrl} onChange={(e) => setThirdPartyBaseUrl(e.target.value)} />
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Cloudflare</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">CLOUDFLARE_ACCOUNT_ID</TableCell>
                  <TableCell>
                    <Input className="font-mono text-xs h-8 bg-muted/20" placeholder="Account ID..." value={cloudflareAccountId} onChange={(e) => setCloudflareAccountId(e.target.value)} />
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium text-muted-foreground text-xs pl-8">↳ API Token</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">CLOUDFLARE_API_TOKEN</TableCell>
                  <TableCell>
                    <Input className="font-mono text-xs h-8 bg-muted/20" placeholder="API Token..." value={cloudflareApiToken} onChange={(e) => setCloudflareApiToken(e.target.value)} />
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>

        <Button onClick={save} disabled={busy} className="w-full sm:w-auto mt-4">Save market settings</Button>
      </CardContent>
    </Card>
  )
}
