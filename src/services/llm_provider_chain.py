"""Shared LLM provider orchestration (Gemini / OpenAI / OpenRouter / third-party / Cloudflare)."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from curl_cffi.requests import AsyncSession
from fastapi import HTTPException

from ..core.config import config as app_config

CLONING_PROVIDERS = [
    "gemini_native",
    "openai",
    "openrouter",
    "third_party_gemini",
    "cloudflare",
]

METADATA_PROVIDERS = [
    "gemini_native",
    "openai",
    "openrouter",
    "third_party_gemini",
    "cloudflare",
    "csvgen",
]


def extract_json_object(raw: str) -> Dict[str, Any]:
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


def get_csv(raw: str) -> List[str]:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        status = int(getattr(exc, "status_code", 500) or 500)
        return status == 429 or status >= 500
    text = str(exc).lower()
    return any(token in text for token in ("timeout", "timed out", "connection", "temporar", "rate limit"))


def normalized_retry_count(raw_value: Any) -> int:
    try:
        return max(0, min(5, int(raw_value)))
    except Exception:
        return 1


class LlmProviderChain:
    @staticmethod
    def resolve_provider_chain(
        explicit_provider: Optional[str],
        *,
        provider_order_csv: str,
        enabled_providers_csv: str,
        legacy_backend: str,
        allowed_providers: List[str],
    ) -> List[str]:
        explicit = str(explicit_provider or "").strip().lower()
        if explicit:
            return [explicit]

        allowed = [p for p in allowed_providers]
        allowed_set = set(allowed)

        legacy = str(legacy_backend or "").strip().lower()
        if legacy not in allowed_set:
            legacy = allowed[0]

        configured_order = [p.strip().lower() for p in str(provider_order_csv or "").split(",") if p.strip()]
        configured_order = [p for p in configured_order if p in allowed_set]
        if not configured_order:
            configured_order = [legacy] + [p for p in allowed if p != legacy]
        else:
            configured_order = configured_order + [p for p in allowed if p not in configured_order]

        enabled = [p.strip().lower() for p in str(enabled_providers_csv or "").split(",") if p.strip()]
        enabled = [p for p in enabled if p in allowed_set]
        if not enabled:
            enabled = [legacy]

        ordered_enabled = [p for p in configured_order if p in set(enabled)]
        return ordered_enabled

    async def invoke_with_provider_chain(
        self,
        *,
        providers: List[str],
        retry_count: int,
        model: str,
        fallback_models: Optional[List[str]],
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        if not providers:
            raise HTTPException(status_code=400, detail="No enabled providers configured")

        retries = normalized_retry_count(retry_count)
        last_err: Optional[Exception] = None
        attempt_failures: List[str] = []

        for provider in providers:
            for attempt in range(retries + 1):
                try:
                    return await self.invoke_model_json(
                        provider=provider,
                        model=model,
                        fallback_models=fallback_models,
                        prompt_text=prompt_text,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        use_cloning_credentials=use_cloning_credentials,
                    )
                except Exception as exc:
                    last_err = exc
                    attempt_failures.append(f"{provider}#{attempt + 1}: {exc}")
                    if attempt < retries and is_retryable_error(exc):
                        continue
                    break

        detail = str(last_err or "Model invocation failed")
        if attempt_failures:
            detail = f"{detail} | attempts: {'; '.join(attempt_failures[-6:])}"
        raise HTTPException(status_code=500, detail=detail)

    async def invoke_model_json(
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
        models = [model] + [m for m in (fallback_models or []) if m and m != model]
        last_err: Optional[Exception] = None
        for candidate in models:
            try:
                if provider == "openai":
                    return await self._invoke_openai(
                        candidate, prompt_text, image_bytes, mime_type, use_cloning_credentials
                    )
                if provider == "openrouter":
                    return await self._invoke_openrouter(
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
        keys = get_csv(app_config.flow2api_openai_api_keys)
        if use_cloning_credentials:
            alt = get_csv(app_config.flow2api_cloning_openai_api_keys)
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
                return extract_json_object(text)
        raise HTTPException(status_code=500, detail="OpenAI request failed")

    async def _invoke_openrouter(
        self,
        model: str,
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        keys = get_csv(app_config.flow2api_openrouter_api_keys)
        if use_cloning_credentials:
            alt = get_csv(app_config.flow2api_cloning_openrouter_api_keys)
            if alt:
                keys = alt
        if not keys:
            raise HTTPException(status_code=503, detail="OpenRouter API key not configured")
        content: Any = prompt_text
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ]
        url = "https://openrouter.ai/api/v1/chat/completions"
        extra_headers = {
            "Referer": "https://github.com/flow2api",
            "X-Title": "Flow2API",
        }
        for key in keys:
            async with AsyncSession() as session:
                resp = await session.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        **extra_headers,
                    },
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
                return extract_json_object(text)
        raise HTTPException(status_code=500, detail="OpenRouter request failed")

    async def _invoke_gemini(
        self,
        model: str,
        prompt_text: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        use_cloning_credentials: bool = False,
    ) -> Dict[str, Any]:
        keys = get_csv(app_config.flow2api_gemini_api_keys)
        if use_cloning_credentials:
            alt = get_csv(app_config.flow2api_cloning_gemini_api_keys)
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
                return extract_json_object(text or "{}")
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
        keys = get_csv(app_config.flow2api_third_party_gemini_api_keys)
        if use_cloning_credentials:
            ce = str(app_config.flow2api_cloning_third_party_gemini_base_url or "").strip().rstrip("/")
            if ce:
                endpoint = ce
            alt = get_csv(app_config.flow2api_cloning_third_party_gemini_api_keys)
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
                return extract_json_object(text)
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
            if c_aid and c_tok:
                account_id = c_aid
                api_token = c_tok
        if not account_id or not api_token:
            raise HTTPException(status_code=503, detail="Cloudflare Workers AI is not configured")
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            content: Any = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ]
        else:
            content = [{"type": "text", "text": prompt_text}]
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
            return extract_json_object(text)
