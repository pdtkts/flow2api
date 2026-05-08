"""Metadata and cloning prompt generation service."""

import base64
import csv
import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi.requests import AsyncSession

# Adobe Stock top-level metadata categories (id + label). Single source of truth for prompts and normalization.
ADOBE_STOCK_METADATA_CATEGORIES: Tuple[Tuple[int, str], ...] = (
    (1, "Animals"),
    (2, "Buildings and Architecture"),
    (3, "Business"),
    (4, "Drinks"),
    (5, "The Environment"),
    (6, "States of Mind"),
    (7, "Food"),
    (8, "Graphic Resources"),
    (9, "Hobbies and Leisure"),
    (10, "Industry"),
    (11, "Landscape"),
    (12, "Lifestyle"),
    (13, "People"),
    (14, "Plants and Flowers"),
    (15, "Culture and Religion"),
    (16, "Science"),
    (17, "Social Issues"),
    (18, "Sports"),
    (19, "Technology"),
    (20, "Transport"),
    (21, "Travel"),
)


def _adobe_stock_category_table_prompt_lines() -> str:
    return "\n".join(f"{cid} — {name}" for cid, name in ADOBE_STOCK_METADATA_CATEGORIES)


def _adobe_category_id_by_label(name: str) -> Optional[int]:
    n = (name or "").strip().lower()
    if not n:
        return None
    for cid, label in ADOBE_STOCK_METADATA_CATEGORIES:
        if label.lower() == n:
            return cid
    return None


def _parse_adobe_category_id_value(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 21 else None
    if isinstance(value, float):
        if value == int(value) and 1 <= int(value) <= 21:
            return int(value)
        return None
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            v = int(s)
            return v if 1 <= v <= 21 else None
    return None


def _resolve_adobe_stock_category_id(raw: Dict[str, Any]) -> Optional[int]:
    parsed = _parse_adobe_category_id_value(raw.get("categoryId"))
    if parsed is not None:
        return parsed
    cat = raw.get("category")
    if cat is None:
        return None
    s = str(cat).strip()
    if s.isdigit():
        v = int(s)
        if 1 <= v <= 21:
            return v
    return _adobe_category_id_by_label(s)
from fastapi import HTTPException
from ..core.config import config as app_config
from ..core.logger import debug_logger
from .llm_provider_chain import (
    CLONING_PROVIDERS,
    LlmProviderChain,
    METADATA_PROVIDERS,
    get_csv,
    is_retryable_error,
    normalized_retry_count,
)


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


def _ensure_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="today must be in YYYY-MM-DD format") from exc


def _parse_recurring_event_date(day_month: str, today: date) -> date:
    """Convert day-month labels like 10-Feb to the nearest upcoming date."""
    try:
        parsed = datetime.strptime(day_month.strip(), "%d-%b")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Invalid event date in CSV: {day_month}") from exc
    candidate = date(today.year, parsed.month, parsed.day)
    if candidate < today:
        candidate = date(today.year + 1, parsed.month, parsed.day)
    return candidate


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
    def __init__(self, llm_chain: Optional[LlmProviderChain] = None) -> None:
        self._llm = llm_chain or LlmProviderChain()

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
        include_cat = bool(meta.get("includeCategory"))
        category_block = (
            (
                "Adobe Stock category table — classify the attached image with exactly ONE `categoryId` from this list "
                "(integers 1–21 only). Choose the single best-matching row for what is visible:\n"
                + _adobe_stock_category_table_prompt_lines()
                + "\n\n"
                "For Adobe Stock taxonomy, output ONLY the integer field `categoryId` in the metadata object. "
                "Do not output a separate `category` string field for Adobe.\n\n"
            )
            if include_cat
            else ""
        )
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
        optional_key = ', "categoryId": 12' if include_cat else ""
        optional_instruction = (
            "categoryId is REQUIRED; it MUST be one of the integers listed in the Adobe Stock category table above."
            if include_cat
            else "Do not include categoryId or Adobe category fields in the JSON."
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
            + category_block
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
        provider_chain = self._llm.resolve_provider_chain(
            provider,
            provider_order_csv=app_config.flow2api_cloning_provider_order,
            enabled_providers_csv=app_config.flow2api_cloning_enabled_providers,
            legacy_backend=app_config.flow2api_cloning_backend or "gemini_native",
            allowed_providers=CLONING_PROVIDERS,
        )
        selected_model = (model or app_config.flow2api_cloning_model or "gemini-2.5-flash").strip()
        retry_count = normalized_retry_count(app_config.flow2api_cloning_provider_retry_count)
        out: List[Dict[str, Any]] = []
        for image in images:
            image_bytes, mime_type = await self._fetch_image(image.get("image_url"), image.get("image_base64"))
            prompt = self._build_clone_instruction(image)
            response_json = await self._llm.invoke_with_provider_chain(
                providers=provider_chain,
                retry_count=retry_count,
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
        provider_chain = self._llm.resolve_provider_chain(
            provider,
            provider_order_csv=app_config.flow2api_cloning_provider_order,
            enabled_providers_csv=app_config.flow2api_cloning_enabled_providers,
            legacy_backend=app_config.flow2api_cloning_backend or "gemini_native",
            allowed_providers=CLONING_PROVIDERS,
        )
        selected_model = (model or app_config.flow2api_cloning_model or "gemini-2.5-flash").strip()
        retry_count = normalized_retry_count(app_config.flow2api_cloning_provider_retry_count)
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
        response_json = await self._llm.invoke_with_provider_chain(
            providers=provider_chain,
            retry_count=retry_count,
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
        explicit_provider = str(payload.get("backend") or "").strip().lower() or None
        provider_chain = self._llm.resolve_provider_chain(
            explicit_provider,
            provider_order_csv=app_config.flow2api_metadata_provider_order,
            enabled_providers_csv=app_config.flow2api_metadata_enabled_providers,
            legacy_backend=app_config.flow2api_metadata_backend or "gemini_native",
            allowed_providers=METADATA_PROVIDERS,
        )
        retry_count = normalized_retry_count(app_config.flow2api_metadata_provider_retry_count)
        backend = provider_chain[0] if provider_chain else "gemini_native"
        configured_primary = str(
            app_config.flow2api_metadata_primary_model
            or app_config.flow2api_metadata_model
            or "gemini-2.5-flash"
        ).strip()
        configured_enabled = get_csv(app_config.flow2api_metadata_enabled_models)
        configured_fallback = get_csv(app_config.flow2api_metadata_fallback_models)
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
        include_category = bool(metadata_settings.get("includeCategory"))
        prompt = self._build_metadata_prompt(metadata_settings, bool(payload.get("dnaNoBgWorkflowActive")))
        last_err: Optional[Exception] = None
        attempt_failures: List[str] = []

        for provider_name in provider_chain:
            for attempt in range(retry_count + 1):
                try:
                    if provider_name == "csvgen":
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
                                raise HTTPException(
                                    status_code=resp.status_code if resp.status_code >= 400 else 500,
                                    detail=text[:500] or "Invalid JSON from csvgen",
                                )
                            if resp.status_code >= 400:
                                raise HTTPException(
                                    status_code=resp.status_code,
                                    detail={
                                        "error": data.get("error") or data.get("message") or "csvgen request failed",
                                        "details": data,
                                    },
                                )
                            return self._normalize_csvgen_response(data, include_category=include_category)

                    parsed = await self._llm.invoke_model_json(
                        provider=provider_name,
                        model=model,
                        fallback_models=fallback_models,
                        prompt_text=prompt,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                    )
                    row = parsed.get("metadataSets", [{}])[0] if isinstance(parsed.get("metadataSets"), list) else parsed
                    return self._normalize_csvgen_response(
                        {"optionA": row, "optionB": row},
                        include_category=include_category,
                    )
                except Exception as exc:
                    last_err = exc
                    attempt_failures.append(f"{provider_name}#{attempt + 1}: {exc}")
                    if attempt < retry_count and is_retryable_error(exc):
                        continue
                    break

        detail = str(last_err or "Metadata generation failed")
        if attempt_failures:
            detail = f"{detail} | attempts: {'; '.join(attempt_failures[-6:])}"
        raise HTTPException(status_code=500, detail=detail)

    def _normalize_csvgen_response(
        self,
        data: Dict[str, Any],
        *,
        include_category: bool = False,
    ) -> Dict[str, Any]:
        def coerce(raw: Any) -> Dict[str, Any]:
            if not isinstance(raw, dict):
                return {"title": "", "keywords": "", "description": ""}
            keywords = raw.get("keywords")
            if isinstance(keywords, list):
                keywords = ", ".join([str(x) for x in keywords])
            if not isinstance(keywords, str):
                keywords = ""
            out: Dict[str, Any] = {
                "title": str(raw.get("title") or ""),
                "keywords": keywords,
                "description": str(raw.get("description") or ""),
            }
            if include_category:
                rid = _resolve_adobe_stock_category_id(raw)
                if rid is not None:
                    out["categoryId"] = rid
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

    def _build_suggested_events_prompt(self, *, today_iso: str, event_list_text: str) -> str:
        return (
            f"Today is {today_iso}. The following events fall between today and 90 days from today. "
            "From this list, select the 8-10 most commercially relevant for stock/visual content. "
            "Return each with date in ISO format (YYYY-MM-DD), category (e.g. Holiday, Seasonal, Global Event), "
            "short description, and icon (FontAwesome class such as fa-solid fa-gift).\n\n"
            f"Events:\n{event_list_text}\n\n"
            "Response format:\n"
            "{\n"
            '  "events": [\n'
            "    {\n"
            '      "name": "Event name",\n'
            '      "date": "2026-05-10",\n'
            '      "category": "Holiday",\n'
            '      "description": "Short description",\n'
            '      "icon": "fa-solid fa-gift"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Return valid JSON only."
        )

    def _normalize_suggested_events(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        raw_events = raw.get("events")
        if not isinstance(raw_events, list):
            raise HTTPException(status_code=500, detail="Model response must include an events array")

        normalized: List[Dict[str, str]] = []
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            date_str = str(item.get("date") or "").strip()
            if not name or not date_str:
                continue
            try:
                date_iso = datetime.strptime(date_str, "%Y-%m-%d").date().isoformat()
            except Exception:
                continue
            category = str(item.get("category") or "Seasonal").strip() or "Seasonal"
            description = str(item.get("description") or "").strip()
            icon = str(item.get("icon") or "fa-solid fa-calendar-days").strip() or "fa-solid fa-calendar-days"
            normalized.append(
                {
                    "name": name,
                    "date": date_iso,
                    "category": category,
                    "description": description,
                    "icon": icon,
                }
            )

        if not normalized:
            raise HTTPException(status_code=500, detail="Model response did not include any valid events")

        if len(normalized) > 10:
            normalized = normalized[:10]
        if len(normalized) < 8:
            debug_logger.log_warning(
                f"Suggested events model returned fewer than 8 events: count={len(normalized)}"
            )
        return {"events": normalized}

    async def generate_suggested_events(self, today_iso: Optional[str] = None) -> Dict[str, Any]:
        resolved_today = _ensure_iso_date(today_iso) if today_iso else datetime.utcnow().date()
        window_end = resolved_today + timedelta(days=90)

        calendar_path = Path(__file__).with_name("event_calendar_2026.csv")
        if not calendar_path.exists():
            raise HTTPException(status_code=500, detail="Event calendar CSV file not found")

        in_window_events: List[Dict[str, str]] = []
        try:
            with calendar_path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    raw_day_month = str(row.get("Date") or "").strip()
                    raw_name = str(row.get("Event Name") or "").strip()
                    if not raw_day_month or not raw_name:
                        continue
                    event_date = _parse_recurring_event_date(raw_day_month, resolved_today)
                    if resolved_today <= event_date <= window_end:
                        in_window_events.append(
                            {
                                "name": raw_name,
                                "date": event_date.isoformat(),
                            }
                        )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to parse event calendar CSV: {exc}") from exc

        if not in_window_events:
            raise HTTPException(status_code=400, detail="No events found between today and 90 days from today")

        in_window_events.sort(key=lambda x: x["date"])
        event_list_text = "\n".join(f"- {item['date']} - {item['name']}" for item in in_window_events)
        prompt = self._build_suggested_events_prompt(
            today_iso=resolved_today.isoformat(),
            event_list_text=event_list_text,
        )

        provider_chain = self._llm.resolve_provider_chain(
            None,
            provider_order_csv=app_config.flow2api_metadata_provider_order,
            enabled_providers_csv=app_config.flow2api_metadata_enabled_providers,
            legacy_backend=app_config.flow2api_metadata_backend or "gemini_native",
            allowed_providers=[p for p in METADATA_PROVIDERS if p != "csvgen"],
        )
        model = str(
            app_config.flow2api_metadata_primary_model
            or app_config.flow2api_metadata_model
            or "gemini-2.5-flash"
        ).strip()
        retry_count = normalized_retry_count(app_config.flow2api_metadata_provider_retry_count)
        result = await self._llm.invoke_with_provider_chain(
            providers=provider_chain,
            retry_count=retry_count,
            model=model,
            fallback_models=None,
            prompt_text=prompt,
            image_bytes=None,
            mime_type="image/jpeg",
        )
        return self._normalize_suggested_events(result)
