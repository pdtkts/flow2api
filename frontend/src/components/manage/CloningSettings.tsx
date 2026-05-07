import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select"

const DEFAULT_CLONING_IMAGE_PROMPT = `You are an OCR + structured prompt generator.
Read the image, extract visible text, and return ONLY valid JSON that follows the Nexus DNA schema.
No markdown, no analysis text, no extra wrapper.`

const DEFAULT_CLONING_VIDEO_PROMPT = `You are a structured JSON generator for Nexus DNA video cloning.
Return one JSON object only, matching the same schema as the image cloning template.
Optimize for temporal motion, timeline actions, and video continuity.`

const PRESET_MODELS: Record<string, string[]> = {
  gemini_native: ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"],
  openai: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-4.1-mini", "gpt-4.1"],
  third_party_gemini: ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"],
  cloudflare: ["@cf/meta/llama-3.1-8b-instruct", "@cf/meta/llama-3-8b-instruct"]
}

const CLONING_BACKENDS = [
  "gemini_native",
  "openai",
  "third_party_gemini",
  "cloudflare",
]

export function CloningSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [backend, setBackend] = useState("gemini_native")
  const [model, setModel] = useState("gemini-2.5-flash")
  const [imagePrompt, setImagePrompt] = useState("")
  const [videoPrompt, setVideoPrompt] = useState("")

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
    setBackend(String(c.flow2api_cloning_backend || "gemini_native"))
    setModel(String(c.flow2api_cloning_model || "gemini-2.5-flash"))
    const savedImagePrompt = String(c.cloning_image_system_prompt || "").trim()
    const savedVideoPrompt = String(c.cloning_video_system_prompt || "").trim()
    setImagePrompt(savedImagePrompt || DEFAULT_CLONING_IMAGE_PROMPT)
    setVideoPrompt(savedVideoPrompt || DEFAULT_CLONING_VIDEO_PROMPT)

    setGeminiKeys(String(c.flow2api_cloning_gemini_api_keys || ""))
    setOpenaiKeys(String(c.flow2api_cloning_openai_api_keys || ""))
    setThirdPartyKeys(String(c.flow2api_cloning_third_party_gemini_api_keys || ""))
    setThirdPartyBaseUrl(String(c.flow2api_cloning_third_party_gemini_base_url || ""))
    setCloudflareAccountId(String(c.flow2api_cloning_cloudflare_account_id || ""))
    setCloudflareApiToken(String(c.flow2api_cloning_cloudflare_api_token || ""))
  }, [token, active])

  const backendModels = useMemo(() => PRESET_MODELS[backend] || [], [backend])

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
          flow2api_cloning_backend: backend,
          flow2api_cloning_model: model,
          cloning_image_system_prompt: imagePrompt,
          cloning_video_system_prompt: videoPrompt,
          flow2api_cloning_gemini_api_keys: geminiKeys,
          flow2api_cloning_openai_api_keys: openaiKeys,
          flow2api_cloning_third_party_gemini_api_keys: thirdPartyKeys,
          flow2api_cloning_third_party_gemini_base_url: thirdPartyBaseUrl,
          flow2api_cloning_cloudflare_account_id: cloudflareAccountId,
          flow2api_cloning_cloudflare_api_token: cloudflareApiToken,
        }),
      })
      if (!r) return
      const d = await r.json()
      if (d.success) toast.success("Cloning settings saved")
      else toast.error(d.message || "Failed")
    } finally {
      setBusy(false)
    }
  }

  if (!active) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cloning Settings</CardTitle>
        <CardDescription>Configure cloning backend, model, credentials, and custom system prompts.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Label>Cloning Backend</Label>
          <Select value={backend} onValueChange={(v) => {
            setBackend(v)
            const fallback = PRESET_MODELS[v]?.[0] || ""
            setModel(fallback)
          }}>
            <SelectTrigger className="w-full sm:w-[300px]">
              <SelectValue placeholder="Select cloning backend" />
            </SelectTrigger>
            <SelectContent>
              {CLONING_BACKENDS.map(entry => (
                <SelectItem key={entry} value={entry} className="font-mono">{entry}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label>Cloning Model</Label>
          <div className="flex gap-2 w-full sm:w-[400px]">
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select model" />
              </SelectTrigger>
              <SelectContent>
                {backendModels.map(entry => (
                  <SelectItem key={entry} value={entry} className="font-mono">{entry}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="mt-2">
            <Label className="text-xs text-muted-foreground">Custom Model (if not in preset list)</Label>
            <Input
              className="mt-1 font-mono text-sm sm:w-[400px]"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. gemini-2.5-flash"
            />
          </div>
        </div>

        <div className="space-y-4 rounded-md border p-4 bg-muted/10">
          <h3 className="font-semibold text-sm">Cloning Provider Credentials</h3>
          <p className="text-xs text-muted-foreground">Cloning operates independently of Metadata and requires its own API keys.</p>
          
          <div className="space-y-2">
            <Label>FLOW2API_CLONING_GEMINI_API_KEYS</Label>
            <Input className="font-mono text-sm" placeholder="AIzaSy..." value={geminiKeys} onChange={(e) => setGeminiKeys(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>FLOW2API_CLONING_OPENAI_API_KEYS</Label>
            <Input className="font-mono text-sm" placeholder="sk-proj-..." value={openaiKeys} onChange={(e) => setOpenaiKeys(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>FLOW2API_CLONING_THIRD_PARTY_GEMINI_API_KEYS</Label>
            <Input className="font-mono text-sm" value={thirdPartyKeys} onChange={(e) => setThirdPartyKeys(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>FLOW2API_CLONING_THIRD_PARTY_GEMINI_BASE_URL</Label>
            <Input className="font-mono text-sm" placeholder="https://..." value={thirdPartyBaseUrl} onChange={(e) => setThirdPartyBaseUrl(e.target.value)} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>CLONING_CLOUDFLARE_ACCOUNT_ID</Label>
              <Input className="font-mono text-sm" value={cloudflareAccountId} onChange={(e) => setCloudflareAccountId(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>CLONING_CLOUDFLARE_API_TOKEN</Label>
              <Input className="font-mono text-sm" value={cloudflareApiToken} onChange={(e) => setCloudflareApiToken(e.target.value)} />
            </div>
          </div>
        </div>

        <div className="space-y-2 mt-4">
          <Label>Cloning Image System Prompt</Label>
          <Textarea className="min-h-[180px] font-mono text-xs resize-y" value={imagePrompt} onChange={(e) => setImagePrompt(e.target.value)} />
        </div>
        
        <div className="space-y-2">
          <Label>Cloning Video System Prompt</Label>
          <Textarea className="min-h-[180px] font-mono text-xs resize-y" value={videoPrompt} onChange={(e) => setVideoPrompt(e.target.value)} />
        </div>
        
        <Button onClick={save} disabled={busy} className="w-full sm:w-auto mt-4">Save cloning settings</Button>
      </CardContent>
    </Card>
  )
}
