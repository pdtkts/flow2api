import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select"
import { toast } from "sonner"

export function MetadataSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [backend, setBackend] = useState("gemini_native")
  const [model, setModel] = useState("gemini-2.5-flash")
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
    setCsvgenCookie(String(c.flow2api_csvgen_cookie || ""))
    setSystemPrompt(String(c.metadata_system_prompt || ""))
    setGeminiKeys(String(c.flow2api_gemini_api_keys || ""))
    setOpenaiKeys(String(c.flow2api_openai_api_keys || ""))
    setThirdPartyKeys(String(c.flow2api_third_party_gemini_api_keys || ""))
    setThirdPartyBaseUrl(String(c.flow2api_third_party_gemini_base_url || ""))
    setCloudflareAccountId(String(c.cloudflare_account_id || ""))
    setCloudflareApiToken(String(c.cloudflare_api_token || ""))
  }, [token, active])

  useEffect(() => {
    void load()
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
          <Select value={backend} onValueChange={setBackend}>
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="gemini_native">gemini_native</SelectItem>
              <SelectItem value="openai">openai</SelectItem>
              <SelectItem value="third_party_gemini">third_party_gemini</SelectItem>
              <SelectItem value="cloudflare">cloudflare</SelectItem>
              <SelectItem value="csvgen">csvgen</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label>FLOW2API_METADATA_MODEL</Label>
          <Input className="mt-1 font-mono text-sm" value={model} onChange={(e) => setModel(e.target.value)} />
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

