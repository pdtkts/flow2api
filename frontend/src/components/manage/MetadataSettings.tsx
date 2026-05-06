import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"

const DEFAULT_METADATA_PROMPT = `You are generating agency microstock metadata for exactly ONE image (attached).
Return ONLY valid JSON with shape:
{"metadataSets":[{"title":"...","keywords":["word1","word2"],"description":""}]}

Rules:
- Keep output factual and based only on visible image details.
- No markdown fences, no commentary, no extra keys.
- Use concise, searchable title and high-intent keywords.
- If category is requested by client settings, include categoryId as integer.
`

const PRESET_MODELS = [
  "gemini-2.5-flash",
  "gemini-2.5-flash-lite",
  "gpt-4.1-mini",
  "gpt-4.1",
  "@cf/meta/llama-3.1-8b-instruct",
]

const METADATA_BACKENDS = [
  "gemini_native",
  "openai",
  "third_party_gemini",
  "cloudflare",
  "csvgen",
]

export function MetadataSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [backend, setBackend] = useState("gemini_native")
  const [model, setModel] = useState("gemini-2.5-flash")
  const [enabledModels, setEnabledModels] = useState<string[]>(["gemini-2.5-flash"])
  const [primaryModel, setPrimaryModel] = useState("gemini-2.5-flash")
  const [customModelInput, setCustomModelInput] = useState("")
  const [csvgenCookie, setCsvgenCookie] = useState("")
  const [systemPrompt, setSystemPrompt] = useState("")
  const [geminiKeys, setGeminiKeys] = useState("")
  const [openaiKeys, setOpenaiKeys] = useState("")
  const [thirdPartyKeys, setThirdPartyKeys] = useState("")
  const [thirdPartyBaseUrl, setThirdPartyBaseUrl] = useState("")
  const [cloudflareAccountId, setCloudflareAccountId] = useState("")
  const [cloudflareApiToken, setCloudflareApiToken] = useState("")

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{ success?: boolean; config?: Record<string, unknown> }>("/api/generation/timeout", token)
    if (!resp.ok || !resp.data?.success || !resp.data.config) return
    const c = resp.data.config
    setBackend(String(c.flow2api_metadata_backend || "gemini_native"))
    setModel(String(c.flow2api_metadata_model || "gemini-2.5-flash"))
    const configuredEnabledRaw = String(c.flow2api_metadata_enabled_models || "").trim()
    const configuredPrimary = String(c.flow2api_metadata_primary_model || "").trim()
    const configuredFallbackRaw = String(c.flow2api_metadata_fallback_models || "").trim()
    const legacyModel = String(c.flow2api_metadata_model || "gemini-2.5-flash").trim() || "gemini-2.5-flash"
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
    setCsvgenCookie(String(c.flow2api_csvgen_cookie || ""))
    const savedPrompt = String(c.metadata_system_prompt || "").trim()
    setSystemPrompt(savedPrompt || DEFAULT_METADATA_PROMPT)
    setGeminiKeys(String(c.flow2api_gemini_api_keys || ""))
    setOpenaiKeys(String(c.flow2api_openai_api_keys || ""))
    setThirdPartyKeys(String(c.flow2api_third_party_gemini_api_keys || ""))
    setThirdPartyBaseUrl(String(c.flow2api_third_party_gemini_base_url || ""))
    setCloudflareAccountId(String(c.cloudflare_account_id || ""))
    setCloudflareApiToken(String(c.cloudflare_api_token || ""))
  }, [token, active])

  const allModels = useMemo(() => {
    const fromEnabled = enabledModels.map((m) => m.trim()).filter(Boolean)
    const merged = Array.from(new Set([...PRESET_MODELS, ...fromEnabled]))
    return merged
  }, [enabledModels])

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

  const addCustomModel = () => {
    const value = customModelInput.trim()
    if (!value) return
    if (!enabledModels.includes(value)) setEnabledModels((prev) => [...prev, value])
    if (!primaryModel) {
      setPrimaryModel(value)
      setModel(value)
    }
    setCustomModelInput("")
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
      const r = await adminFetch("/api/generation/timeout", token, {
        method: "POST",
        body: JSON.stringify({
          flow2api_metadata_backend: backend,
          flow2api_metadata_model: model,
          flow2api_metadata_enabled_models: enabledModels.join(","),
          flow2api_metadata_primary_model: primaryModel,
          flow2api_metadata_fallback_models: fallbackModels.join(","),
          flow2api_csvgen_cookie: csvgenCookie,
          metadata_system_prompt: systemPrompt,
          flow2api_gemini_api_keys: geminiKeys,
          flow2api_openai_api_keys: openaiKeys,
          flow2api_third_party_gemini_api_keys: thirdPartyKeys,
          flow2api_third_party_gemini_base_url: thirdPartyBaseUrl,
          cloudflare_account_id: cloudflareAccountId,
          cloudflare_api_token: cloudflareApiToken,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Metadata settings saved")
      else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  if (!active) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Metadata Settings</CardTitle>
        <CardDescription>Configure metadata backend, model, credentials, and metadata system prompt.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <Label>Default Metadata Backend</Label>
          <div className="mt-2 rounded-md border">
            <div className="grid grid-cols-[1fr_140px] gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-medium">
              <span>Backend</span>
              <span>Default</span>
            </div>
            {METADATA_BACKENDS.map((entry) => (
              <div key={entry} className="grid grid-cols-[1fr_140px] items-center gap-2 px-3 py-2 text-sm">
                <span className="font-mono">{entry}</span>
                <input
                  type="radio"
                  name="metadata-backend"
                  checked={backend === entry}
                  onChange={() => setBackend(entry)}
                />
              </div>
            ))}
          </div>
        </div>
        <div>
          <Label>Metadata Models (preset + custom)</Label>
          <div className="mt-2 rounded-md border">
            <div className="grid grid-cols-[1fr_90px_90px_110px] gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-medium">
              <span>Model</span>
              <span>Enabled</span>
              <span>Primary</span>
              <span>Order</span>
            </div>
            {allModels.map((entry) => {
              const enabled = enabledModels.includes(entry)
              return (
                <div key={entry} className="grid grid-cols-[1fr_90px_90px_110px] items-center gap-2 px-3 py-2 text-sm">
                  <span className="font-mono">{entry}</span>
                  <input type="checkbox" checked={enabled} onChange={(e) => toggleModel(entry, e.target.checked)} />
                  <input
                    type="radio"
                    name="primary-model"
                    checked={primaryModel === entry}
                    disabled={!enabled}
                    onChange={() => {
                      setPrimaryModel(entry)
                      setModel(entry)
                    }}
                  />
                  <div className="flex gap-1">
                    <Button type="button" size="sm" variant="outline" disabled={!enabled} onClick={() => moveModel(entry, -1)}>Up</Button>
                    <Button type="button" size="sm" variant="outline" disabled={!enabled} onClick={() => moveModel(entry, 1)}>Down</Button>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="mt-2 flex gap-2">
            <Input
              placeholder="Custom model id"
              className="font-mono text-sm"
              value={customModelInput}
              onChange={(e) => setCustomModelInput(e.target.value)}
            />
            <Button type="button" variant="secondary" onClick={addCustomModel}>Add</Button>
          </div>
          <div className="mt-2">
            <Label>Legacy FLOW2API_METADATA_MODEL (compatibility)</Label>
            <Input
              className="mt-1 font-mono text-sm"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            Fallback order follows enabled model order excluding primary.
          </p>
        </div>
        <div>
          <Label>FLOW2API_CSVGEN_COOKIE</Label>
          <Input className="mt-1 font-mono text-sm" value={csvgenCookie} onChange={(e) => setCsvgenCookie(e.target.value)} />
        </div>
        <div>
          <Label>Metadata System Prompt</Label>
          <Textarea className="mt-1 min-h-[160px] font-mono text-xs" value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} />
        </div>
        <div>
          <Label>FLOW2API_GEMINI_API_KEYS</Label>
          <Input className="mt-1 font-mono text-sm" value={geminiKeys} onChange={(e) => setGeminiKeys(e.target.value)} />
        </div>
        <div>
          <Label>FLOW2API_OPENAI_API_KEYS</Label>
          <Input className="mt-1 font-mono text-sm" value={openaiKeys} onChange={(e) => setOpenaiKeys(e.target.value)} />
        </div>
        <div>
          <Label>FLOW2API_THIRD_PARTY_GEMINI_API_KEYS</Label>
          <Input className="mt-1 font-mono text-sm" value={thirdPartyKeys} onChange={(e) => setThirdPartyKeys(e.target.value)} />
        </div>
        <div>
          <Label>FLOW2API_THIRD_PARTY_GEMINI_BASE_URL</Label>
          <Input className="mt-1 font-mono text-sm" value={thirdPartyBaseUrl} onChange={(e) => setThirdPartyBaseUrl(e.target.value)} />
        </div>
        <div>
          <Label>CLOUDFLARE_ACCOUNT_ID</Label>
          <Input className="mt-1 font-mono text-sm" value={cloudflareAccountId} onChange={(e) => setCloudflareAccountId(e.target.value)} />
        </div>
        <div>
          <Label>CLOUDFLARE_API_TOKEN</Label>
          <Input className="mt-1 font-mono text-sm" value={cloudflareApiToken} onChange={(e) => setCloudflareApiToken(e.target.value)} />
        </div>
        <Button onClick={save} disabled={busy}>Save metadata settings</Button>
      </CardContent>
    </Card>
  )
}

