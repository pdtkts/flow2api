import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../contexts/AuthContext"
import { adminFetch, adminJson } from "../../lib/adminApi"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Textarea } from "../ui/textarea"
import { toast } from "sonner"

const DEFAULT_CLONING_IMAGE_PROMPT = `You are an OCR + structured prompt generator.
Read the image, extract visible text, and return ONLY valid JSON that follows the Nexus DNA schema.
No markdown, no analysis text, no extra wrapper.`

const DEFAULT_CLONING_VIDEO_PROMPT = `You are a structured JSON generator for Nexus DNA video cloning.
Return one JSON object only, matching the same schema as the image cloning template.
Optimize for temporal motion, timeline actions, and video continuity.`

export function CloningSettings({ active }: { active: boolean }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [model, setModel] = useState("gemini-2.5-flash")
  const [imagePrompt, setImagePrompt] = useState("")
  const [videoPrompt, setVideoPrompt] = useState("")

  const load = useCallback(async () => {
    if (!token || !active) return
    const resp = await adminJson<{ success?: boolean; config?: Record<string, unknown> }>("/api/generation/timeout", token)
    if (!resp.ok || !resp.data?.success || !resp.data.config) return
    const c = resp.data.config
    setModel(String(c.flow2api_cloning_model || "gemini-2.5-flash"))
    const savedImagePrompt = String(c.cloning_image_system_prompt || "").trim()
    const savedVideoPrompt = String(c.cloning_video_system_prompt || "").trim()
    setImagePrompt(savedImagePrompt || DEFAULT_CLONING_IMAGE_PROMPT)
    setVideoPrompt(savedVideoPrompt || DEFAULT_CLONING_VIDEO_PROMPT)
  }, [token, active])

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
          flow2api_cloning_model: model,
          cloning_image_system_prompt: imagePrompt,
          cloning_video_system_prompt: videoPrompt,
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
        <CardDescription>Configure cloning model and custom system prompts.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <Label>FLOW2API_CLONING_MODEL</Label>
          <Input className="mt-1 font-mono text-sm" value={model} onChange={(e) => setModel(e.target.value)} />
        </div>
        <div>
          <Label>Cloning Image System Prompt</Label>
          <Textarea className="mt-1 min-h-[180px] font-mono text-xs" value={imagePrompt} onChange={(e) => setImagePrompt(e.target.value)} />
        </div>
        <div>
          <Label>Cloning Video System Prompt</Label>
          <Textarea className="mt-1 min-h-[180px] font-mono text-xs" value={videoPrompt} onChange={(e) => setVideoPrompt(e.target.value)} />
        </div>
        <Button onClick={save} disabled={busy}>Save cloning settings</Button>
      </CardContent>
    </Card>
  )
}

