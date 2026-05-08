"""Metadata and cloning prompt generation service."""

import base64
import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi.requests import AsyncSession
from fastapi import HTTPException
from ..core.config import config as app_config


DEFAULT_TEMPLATE: Dict[str, Any] = {
    "scene": "",
    "style": "",
    "constraints": ["SILENT_OUTPUT"],
    "shot": {
        "composition": "",
        "camera_motion": "",
        "frame_rate": "60 fps",
        "resolution": "1920 × 1080",
        "lens": "",
    },
    "voice_over": {
        "language": "",
        "tone": "",
        "mode": "",
        "emotion": "",
        "narration_text": "",
        "duration_sec": "",
    },
    "house_settings": {
        "typeface": {"hook": "", "subtext": ""},
        "overlay_style": "none",
        "animation": {"enter": "", "enter_duration_ms": 600, "exit": "", "exit_duration_ms": 500},
        "callouts": {"stroke_px": 0, "corner_radius_px": 0},
        "sizes": {"hook_font_height_pct": "", "sublabel_font_height_pct": "", "safe_margins_pct": 7},
    },
    "timeline": [
        {"time": "0.0–1.5 s", "action": ""},
        {"time": "1.5–3.0 s", "action": ""},
        {"time": "3.0–4.0 s", "action": ""},
        {"time": "4.0–5.5 s", "action": ""},
        {"time": "5.5–6.5 s", "action": ""},
        {"time": "6.5–7.5 s", "action": ""},
        {"time": "7.5–END", "action": ""},
    ],
    "lighting": {"primary": "", "secondary": "", "accents": ""},
    "audio": {
        "mode": "none",
        "ambient": "none",
        "sfx": [],
        "music": {
            "track": "none",
            "description": "no music",
            "tempo": "n/a",
            "key": "n/a",
            "dynamic_curve": "flat",
        },
        "mix": {"integrated_loudness": "-inf", "sidechain_music_db_on_impacts": 0, "natural_reverb": False},
    },
    "text_rules": {"emoji_policy": "no emojis", "contrast": ""},
    "color_palette": {"background": "", "ink_primary": "", "ink_secondary": "", "splatter": "", "text_primary": ""},
    "transitions": {"between_scenes": "", "impact_frame_usage": "", "forbidden": []},
    "vfx_rules": {"grain": "none", "particles": "none", "camera_shake": "none"},
    "visual_rules": {"prohibited_elements": [], "grain": "none", "sharpen": "none"},
    "export": {"preset": "1920x1080_h264_high", "target_duration_sec": ""},
    "metadata": {"series": "", "task": "", "scene_number": "", "tags": []},
}


def _template_text() -> str:
    return json.dumps(DEFAULT_TEMPLATE, ensure_ascii=False, indent=2)


def _extract_json_object(raw: str) -> Dict[str, Any]:
    cleaned = str(raw or "").replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise HTTPException(status_code=500, detail="Model response did not contain a JSON object")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Model response JSON parse failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="Model response JSON root must be an object")
    return parsed


def _get_csv(raw: str) -> List[str]:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def _normalize_image_prompt(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scene": p.get("scene") or "",
        "style": p.get("style") or "",
        "constraints": p.get("constraints") if isinstance(p.get("constraints"), list) else ["SILENT_OUTPUT"],
        "shot": {
            "composition": ((p.get("shot") or {}).get("composition")) or "",
            "resolution": ((p.get("shot") or {}).get("resolution")) or "1920 × 1080",
            "lens": ((p.get("shot") or {}).get("lens")) or "",
        },
        "lighting": {
            "primary": ((p.get("lighting") or {}).get("primary")) or "",
            "secondary": ((p.get("lighting") or {}).get("secondary")) or "",
            "accents": ((p.get("lighting") or {}).get("accents")) or "",
        },
        "color_palette": {
            "background": ((p.get("color_palette") or {}).get("background")) or "",
            "ink_primary": ((p.get("color_palette") or {}).get("ink_primary")) or "",
            "ink_secondary": ((p.get("color_palette") or {}).get("ink_secondary")) or "",
            "text_primary": ((p.get("color_palette") or {}).get("text_primary")) or "",
        },
        "visual_rules": {
            "prohibited_elements": ((p.get("visual_rules") or {}).get("prohibited_elements"))
            if isinstance((p.get("visual_rules") or {}).get("prohibited_elements"), list)
            else [],
            "grain": ((p.get("visual_rules") or {}).get("grain")) or "none",
            "sharpen": ((p.get("visual_rules") or {}).get("sharpen")) or "none",
        },
        "metadata": {
            "series": ((p.get("metadata") or {}).get("series")) or "",
            "task": ((p.get("metadata") or {}).get("task")) or "",
            "scene_number": ((p.get("metadata") or {}).get("scene_number")) or "",
            "tags": ((p.get("metadata") or {}).get("tags")) if isinstance((p.get("metadata") or {}).get("tags"), list) else [],
        },
    }


class CloningMetadataService:
    async def _fetch_image(self, image_url: Optional[str], image_base64: Optional[str]) -> Tuple[bytes, str]:
        if image_url:
            async with AsyncSession() as session:
                resp = await session.get(image_url, timeout=60, verify=False)
                if resp.status_code != 200 or not resp.content:
                    raise HTTPException(status_code=400, detail=f"Failed to fetch image: HTTP {resp.status_code}")
                mime = resp.headers.get("content-type") or "image/jpeg"
                return bytes(resp.content), mime
        if image_base64:
            raw = image_base64.strip()
            if "base64," in raw:
                raw = raw.split("base64,", 1)[1]
            try:
                return base64.b64decode(raw), "image/jpeg"
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid base64 image: {exc}") from exc
        raise HTTPException(status_code=400, detail="One image source is required")

    async def _invoke_model_json(
        self,
        *,
        provider: str,
        model: str,
        fallback_models: Optional[List[str]],
        prompt_text: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        providers = [provider]
        if provider == "gemini_native":
            providers = ["gemini_native"]
        models = [model] + [m for m in (fallback_models or []) if m and m != model]
        last_err: Optional[Exception] = None
        for candidate in models:
            try:
                if provider == "openai":
                    return await self._invoke_openai(
                        candidate, prompt_text, image_bytes, mime_type, use_cloning_credentials
                    )
                if provider == "third_party_gemini":
                    return await self._invoke_third_party(
                        candidate, prompt_text, image_bytes, mime_type, use_cloning_credentials
                    )
                if provider == "cloudflare":
                    return await self._invoke_cloudflare(
                        candidate, prompt_text, image_bytes, mime_type, use_cloning_credentials
                    )
                return await self._invoke_gemini(
                    candidate, prompt_text, image_bytes, mime_type, use_cloning_credentials
                )
            except Exception as exc:
                last_err = exc
                continue
        raise HTTPException(status_code=500, detail=str(last_err or "Model invocation failed"))

    async def _invoke_openai(
        self,
        model: str,
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        keys = _get_csv(app_config.flow2api_openai_api_keys)
        if use_cloning_credentials:
            alt = _get_csv(app_config.flow2api_cloning_openai_api_keys)
            if alt:
                keys = alt
        if not keys:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured")
        content: Any = prompt_text
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ]
        for key in keys:
            async with AsyncSession() as session:
                resp = await session.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": content}],
                    },
                    timeout=120,
                )
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                text = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
                return _extract_json_object(text)
        raise HTTPException(status_code=500, detail="OpenAI request failed")

    async def _invoke_gemini(
        self,
        model: str,
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        keys = _get_csv(app_config.flow2api_gemini_api_keys)
        if use_cloning_credentials:
            alt = _get_csv(app_config.flow2api_cloning_gemini_api_keys)
            if alt:
                keys = alt
        if not keys:
            raise HTTPException(status_code=503, detail="Gemini API key not configured")
        for key in keys:
            parts: List[Dict[str, Any]] = []
            if image_bytes:
                parts.append({"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}})
            parts.append({"text": prompt_text})
            body = {"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json"}}
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            async with AsyncSession() as session:
                resp = await session.post(url, json=body, timeout=120)
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                text_parts = (((((data or {}).get("candidates") or [{}])[0].get("content") or {}).get("parts")) or [])
                text = "\n".join([str(p.get("text") or "") for p in text_parts if not p.get("thought")]).strip()
                return _extract_json_object(text or "{}")
        raise HTTPException(status_code=500, detail="Gemini request failed")

    async def _invoke_third_party(
        self,
        model: str,
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        endpoint = str(app_config.flow2api_third_party_gemini_base_url or "").strip().rstrip("/")
        keys = _get_csv(app_config.flow2api_third_party_gemini_api_keys)
        if use_cloning_credentials:
            ce = str(app_config.flow2api_cloning_third_party_gemini_base_url or "").strip().rstrip("/")
            if ce:
                endpoint = ce
            alt = _get_csv(app_config.flow2api_cloning_third_party_gemini_api_keys)
            if alt:
                keys = alt
        if not endpoint or not keys:
            raise HTTPException(status_code=503, detail="Third-party Gemini is not configured")
        content: Any = prompt_text
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ]
        for key in keys:
            async with AsyncSession() as session:
                resp = await session.post(
                    f"{endpoint}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": content}]},
                    timeout=120,
                )
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                text = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
                return _extract_json_object(text)
        raise HTTPException(status_code=500, detail="Third-party Gemini request failed")

    async def _invoke_cloudflare(
        self,
        model: str,
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        account_id = str(app_config.cloudflare_account_id or "").strip()
        api_token = str(app_config.cloudflare_api_token or "").strip()
        if use_cloning_credentials:
            c_aid = str(app_config.flow2api_cloning_cloudflare_account_id or "").strip()
            c_tok = str(app_config.flow2api_cloning_cloudflare_api_token or "").strip()
            # Only override when BOTH cloning fields are set; otherwise use main credentials (partial
            # cloning values used to mix placeholder IDs with real tokens and broke Workers AI calls).
            if c_aid and c_tok:
                account_id = c_aid
                api_token = c_tok
        if not account_id or not api_token:
            raise HTTPException(status_code=503, detail="Cloudflare Workers AI is not configured")
        content: Any = prompt_text
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ]
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
        async with AsyncSession() as session:
            resp = await session.post(
                url,
                headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
                json={"model": model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": content}]},
                timeout=120,
            )
            if resp.status_code >= 400:
                snippet = (resp.text or "").replace("\r\n", "\n").strip()
                if len(snippet) > 1200:
                    snippet = snippet[:1200] + "…"
                raise HTTPException(
                    status_code=502,
                    detail=f"Cloudflare Workers AI HTTP {resp.status_code}: {snippet or '(empty body)'}",
                )
            data = resp.json()
            if isinstance(data, dict) and data.get("success") is False:
                snippet = str(data.get("errors") or data)[:1200]
                raise HTTPException(status_code=502, detail=f"Cloudflare API error: {snippet}")
            text = (((data or {}).get("result") or {}).get("response") or "")
            if not text:
                text = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
            return _extract_json_object(text)

    def _build_clone_instruction(self, image: Dict[str, Any]) -> str:
        item_id = str(image.get("id") or "")
        title = str(image.get("title") or "")
        default_prompt = (
            "You are an OCR + structured prompt generator.\n"
            "Read the image, extract visible text, and return ONLY valid JSON.\n"
            "Do not output analysis, thoughts, markdown, or extra text.\n"
            "Output must be one JSON object only, using this exact schema:\n"
            + _template_text()
            + "\n\nNexus DNA cloning context: This is a reference stock image. "
            + f'Title: "{title}". '
            + 'Set metadata.scene_number to the asset id when filling the object; use "'
            + item_id
            + '" for this image. Prefer metadata.series "cloning" and metadata.task "clone" when appropriate. '
            + "The scene, style, and related fields you output must describe original stock imagery in the spirit of that title and image-not a pixel-perfect or slavish recreation-so a generated image can be clearly distinct from the reference."
        )
        return default_prompt

    def _build_video_instruction(self, image_clone_prompt: str, camera_motion: str, duration: str, negative_prompt: str, title: str) -> str:
        default_prompt = (
            "You are a structured JSON generator for Nexus DNA video cloning.\n"
            "Return ONLY one JSON object. No markdown fences, no analysis, no extra text.\n\n"
            "The object MUST conform to this schema (same structure and nesting; fill every section appropriately for video):\n"
            + _template_text()
            + "\n\nNexus DNA video cloning instructions:\n"
            "- The reference JSON below was used to generate a cloned STILL image. Produce a NEW object of the SAME SCHEMA tailored for I2V (start-frame video): temporal beats, motion-aware scene and style wording, and meaningful timeline[].action entries across the clip.\n"
            "- Preserve the general subject theme but optimize for movement and duration; do not copy the still prompt verbatim.\n"
            "- Prefer metadata.series 'cloning' and metadata.task that reflects video (e.g. clone_video) when appropriate.\n"
            "- The server will overwrite shot.camera_motion, export.target_duration_sec, and merge negative prompts; align your draft with these targets:\n"
            + f"  camera_motion: {json.dumps(str(camera_motion or '').strip())}\n"
            + f"  target_duration_sec: {json.dumps(str(duration or '').strip())}\n"
            + f"  extra avoid terms: {json.dumps(str(negative_prompt or '').strip() or '(none)')}\n"
            + (f"  reference stock title (concept only; never render as text): {json.dumps(str(title or '').strip())}\n" if title else "")
            + "\nReference image-clone JSON:\n"
            + image_clone_prompt
        )
        return default_prompt

    def _build_metadata_prompt(self, metadata_settings: Dict[str, Any], dna_no_bg: bool) -> str:
        meta = metadata_settings or {}
        title_min = int(meta.get("titleMin", 50) or 50)
        title_max = int(meta.get("titleMax", 80) or 80)
        keyword_min = int(meta.get("keywordMin", 32) or 32)
        keyword_max = int(meta.get("keywordMax", 50) or 50)
        desc_min = int(meta.get("descriptionMin", 0) or 0)
        desc_max = int(meta.get("descriptionMax", 0) or 0)
        platforms = ", ".join([str(x).strip() for x in (meta.get("platforms") or ["adobe-stock"]) if str(x).strip()]) or "adobe-stock"
        title_style = str(meta.get("titleStyle") or "seo-optimized")
        keyword_types = meta.get("keywordTypes") or {}
        mixed = bool(keyword_types.get("mixed"))
        double = bool(keyword_types.get("doubleWord"))
        keyword_shape = (
            "Each keyword may be one to three words: use a mix of single-word tokens and two-word phrases where they add disambiguation; never exceed three words per keyword token."
            if mixed
            else ("Each keyword MUST be exactly two words." if double else "Each keyword MUST be a single English token.")
        )
        description_rule = '`description` MUST be exactly "" (empty string).' if desc_max == 0 else f"`description` MUST be between {desc_min} and {desc_max} characters (plain text, no HTML)."
        category_line = "categoryId is REQUIRED (Adobe Stock integer category)." if bool(meta.get("includeCategory")) else ""
        releases = (
            "Releases: if the image shows identifiable people, private property, or prominent branded products, buyers may need model or property releases."
            if bool(meta.get("includeReleases"))
            else ""
        )
        bg_line = (
            "When the asset is clearly subject on transparency (cutout), you may use isolated on transparent style wording in title."
            if bool(meta.get("transparentBackground")) and dna_no_bg
            else "Do not suggest transparent cutout language unless the image is clearly transparent."
        )
        custom_cfg = meta.get("customPrompt") or {}
        custom = (
            "\n\nAdditional client rules (must follow):\n" + str(custom_cfg.get("text") or "").strip()
            if bool(custom_cfg.get("enabled")) and str(custom_cfg.get("text") or "").strip()
            else ""
        )
        title_style_line = f'Follow this title style label: "{title_style}".'
        optional_key = ', "categoryId": 7' if bool(meta.get("includeCategory")) else ""
        optional_instruction = (
            "categoryId is REQUIRED; integer 1-21 only."
            if bool(meta.get("includeCategory"))
            else "Do not include categoryId in the JSON."
        )
        title_length = f"Title MUST be between {title_min} and {title_max} characters inclusive."
        keyword_count = f"Generate between {keyword_min} and {keyword_max} keywords inclusive."
        default_prompt = (
            "You are generating agency microstock metadata for exactly ONE image (attached).\n\n"
            "CRITICAL LENGTH (stay as close as possible):\n"
            f"* {title_length}\n"
            f"* {keyword_count}\n"
            f"* {keyword_shape}\n"
            f"* {description_rule}\n\n"
            "Target platforms:\n"
            f"* Optimize for: {platforms}.\n\n"
            "Title content:\n"
            f"* {title_style_line}\n"
            "* Sentence case for the title; no clickbait; no redundant site names.\n"
            "* Do not put keyword lists in the title.\n\n"
            "Keywords content:\n"
            "* English unless a proper noun clearly requires another language.\n"
            "* No duplicate or near-duplicate keywords; no camera serials; no filler words image, photo, picture.\n"
            "* No hashtags.\n\n"
            "Safety / accuracy:\n"
            "* Describe only what is visible. Do not invent people, brands, or locations.\n"
            "* No URLs, email addresses, or watermark text in any field.\n\n"
            + (f"{category_line}\n\n" if category_line else "")
            + (f"{releases}\n\n" if releases else "")
            + f"{bg_line}\n"
            + custom
            + "\n\nOUTPUT - respond with ONLY valid JSON (no markdown fences, no commentary before or after). Use exactly this shape with one object inside the array:\n"
            "{\n"
            '  "metadataSets": [\n'
            "    {\n"
            '      "title": "...",\n'
            '      "keywords": ["word1", "word2"],\n'
            '      "description": ""'
            + optional_key
            + "\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            + f"Where keywords is a JSON array of strings ({keyword_count}). {optional_instruction}\n\n"
            "REMINDER: Return ONLY the JSON object."
        )
        return default_prompt

    async def generate_cloning_prompts(
        self,
        images: List[Dict[str, Any]],
        provider: Optional[str],
        model: Optional[str],
        fallback_models: Optional[List[str]],
    ) -> Dict[str, Any]:
        selected_provider = (provider or app_config.flow2api_cloning_backend or "gemini_native").strip().lower()
        selected_model = (model or app_config.flow2api_cloning_model or "gemini-2.5-flash").strip()
        out: List[Dict[str, Any]] = []
        for image in images:
            image_bytes, mime_type = await self._fetch_image(image.get("image_url"), image.get("image_base64"))
            prompt = self._build_clone_instruction(image)
            response_json = await self._invoke_model_json(
                provider=selected_provider,
                model=selected_model,
                fallback_models=fallback_models,
                prompt_text=prompt,
                image_bytes=image_bytes,
                mime_type=str(image.get("mimeType") or mime_type),
                use_cloning_credentials=True,
            )
            out.append(_normalize_image_prompt(response_json))
        return {"prompts": out}

    async def generate_cloning_video_prompt(
        self,
        payload: Dict[str, Any],
        provider: Optional[str],
        model: Optional[str],
        fallback_models: Optional[List[str]],
    ) -> Dict[str, Any]:
        selected_provider = (provider or app_config.flow2api_cloning_backend or "gemini_native").strip().lower()
        selected_model = (model or app_config.flow2api_cloning_model or "gemini-2.5-flash").strip()
        clone_prompt_raw = payload.get("imageClonePrompt") or ""
        try:
            clone_prompt_json = json.dumps(json.loads(clone_prompt_raw), ensure_ascii=False, indent=2)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid imageClonePrompt: {exc}") from exc
        instruction = self._build_video_instruction(
            clone_prompt_json,
            str(payload.get("cameraMotion") or ""),
            str(payload.get("duration") or ""),
            str(payload.get("negativePrompt") or ""),
            str(payload.get("title") or ""),
        )
        image_bytes = None
        mime_type = "image/jpeg"
        if payload.get("image_base64"):
            image_bytes, mime_type = await self._fetch_image(None, payload.get("image_base64"))
        response_json = await self._invoke_model_json(
            provider=selected_provider,
            model=selected_model,
            fallback_models=fallback_models,
            prompt_text=instruction,
            image_bytes=image_bytes,
            mime_type=str(payload.get("mimeType") or mime_type),
            use_cloning_credentials=True,
        )
        merged = deepcopy(DEFAULT_TEMPLATE)
        merged.update({k: v for k, v in response_json.items() if k in merged and not isinstance(v, dict)})
        for key in ("shot", "voice_over", "house_settings", "lighting", "audio", "text_rules", "color_palette", "transitions", "vfx_rules", "visual_rules", "export", "metadata"):
            if isinstance(response_json.get(key), dict):
                merged[key].update(response_json[key])
        if isinstance(response_json.get("timeline"), list):
            for i, seg in enumerate(response_json["timeline"][: len(merged["timeline"])]):
                if isinstance(seg, dict) and isinstance(seg.get("action"), str):
                    merged["timeline"][i]["action"] = seg["action"]
        merged["shot"]["camera_motion"] = str(payload.get("cameraMotion") or "").strip()
        merged["export"]["target_duration_sec"] = str(payload.get("duration") or "").strip()
        negatives = [x.strip() for x in str(payload.get("negativePrompt") or "").split(",") if x.strip()]
        existing = merged.get("visual_rules", {}).get("prohibited_elements") or []
        if not isinstance(existing, list):
            existing = []
        merged["visual_rules"]["prohibited_elements"] = list(dict.fromkeys([*existing, *negatives]))
        return {"prompt": json.dumps(merged, ensure_ascii=False)}

    async def generate_metadata(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        backend = str(payload.get("backend") or app_config.flow2api_metadata_backend or "gemini_native").strip().lower()
        configured_primary = str(
            app_config.flow2api_metadata_primary_model
            or app_config.flow2api_metadata_model
            or "gemini-2.5-flash"
        ).strip()
        configured_enabled = _get_csv(app_config.flow2api_metadata_enabled_models)
        configured_fallback = _get_csv(app_config.flow2api_metadata_fallback_models)
        if not configured_enabled:
            configured_enabled = [configured_primary, *configured_fallback]
        configured_enabled = list(dict.fromkeys([m for m in configured_enabled if m]))
        if configured_primary not in configured_enabled:
            configured_enabled.insert(0, configured_primary)
        default_fallback_chain = [m for m in configured_enabled if m != configured_primary]
        if configured_fallback:
            default_fallback_chain = list(
                dict.fromkeys([m for m in configured_fallback if m and m != configured_primary] + default_fallback_chain)
            )

        model = str(payload.get("model") or configured_primary).strip()
        fallback_models = payload.get("fallbackModels") or default_fallback_chain
        image_bytes, mime_type = await self._fetch_image(payload.get("image_url"), payload.get("image_base64"))
        metadata_settings = payload.get("metadataSettings") or {}
        if backend == "csvgen":
            cookie = str(app_config.flow2api_csvgen_cookie or "").strip()
            if not cookie:
                raise HTTPException(status_code=400, detail="CSVGEN cookie not configured")
            b64 = base64.b64encode(image_bytes).decode("ascii")
            body = {"base64ImageData": b64, "metadataSettings": metadata_settings}
            async with AsyncSession() as session:
                resp = await session.post(
                    "https://www.csvgen.com/api/generate-metadata",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "*/*",
                        "Origin": "https://www.csvgen.com",
                        "Referer": "https://www.csvgen.com/app",
                        "Cookie": cookie,
                    },
                    json=body,
                    timeout=120,
                )
                text = resp.text
                try:
                    data = json.loads(text)
                except Exception:
                    raise HTTPException(status_code=resp.status_code if resp.status_code >= 400 else 500, detail=text[:500] or "Invalid JSON from csvgen")
                if resp.status_code >= 400:
                    raise HTTPException(status_code=resp.status_code, detail={"error": data.get("error") or data.get("message") or "csvgen request failed", "details": data})
                return self._normalize_csvgen_response(data)
        prompt = self._build_metadata_prompt(metadata_settings, bool(payload.get("dnaNoBgWorkflowActive")))
        parsed = await self._invoke_model_json(
            provider=backend,
            model=model,
            fallback_models=fallback_models,
            prompt_text=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        row = parsed.get("metadataSets", [{}])[0] if isinstance(parsed.get("metadataSets"), list) else parsed
        return self._normalize_csvgen_response({"optionA": row, "optionB": row})

    def _normalize_csvgen_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        def coerce(raw: Any) -> Dict[str, Any]:
            if not isinstance(raw, dict):
                return {"title": "", "keywords": "", "description": ""}
            keywords = raw.get("keywords")
            if isinstance(keywords, list):
                keywords = ", ".join([str(x) for x in keywords])
            if not isinstance(keywords, str):
                keywords = ""
            out = {
                "title": str(raw.get("title") or ""),
                "keywords": keywords,
                "description": str(raw.get("description") or ""),
            }
            if raw.get("category") is not None:
                out["category"] = str(raw.get("category"))
            return out

        a = coerce(data.get("optionA") if isinstance(data, dict) else None)
        b = coerce(data.get("optionB") if isinstance(data, dict) else None)
        if not any(a.values()) and any(b.values()):
            a = deepcopy(b)
        if not any(b.values()) and any(a.values()):
            b = deepcopy(a)
        base: Dict[str, Any] = {"optionA": a, "optionB": b}
        if isinstance(data, dict) and "creditsRemaining" in data:
            base["creditsRemaining"] = data["creditsRemaining"]
        return base
