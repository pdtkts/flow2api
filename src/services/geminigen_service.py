"""GeminiGen web-session generation integration."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from curl_cffi.requests import AsyncSession

from ..core.database import Database
from ..core.geminigen_manifest import GEMINIGEN_MODEL_MANIFEST, geminigen_manifest_entry
from ..core.logger import debug_logger
from ..core.models import GeminiGenAccount, GeminiGenTask
from .file_cache import FileCache


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}


class GeminiGenService:
    """Create, poll, and normalize GeminiGen Max web-app jobs."""

    def __init__(self, db: Database, file_cache: FileCache, proxy_manager=None):
        self.db = db
        self.file_cache = file_cache
        self.proxy_manager = proxy_manager

    @staticmethod
    def is_geminigen_model(model: str) -> bool:
        return bool(geminigen_manifest_entry(model or ""))

    @staticmethod
    def model_catalog() -> List[Dict[str, str]]:
        return [
            {
                "id": item["id"],
                "description": f"GeminiGen {item['kind']} generation - {item['endpoint_type']}",
            }
            for item in GEMINIGEN_MODEL_MANIFEST
        ]

    @staticmethod
    def describe_credential(raw_cookie: str, bearer_token: str = "", guard_id: str = "") -> Dict[str, str]:
        if not (raw_cookie or "").strip():
            return {"status": "missing_cookie", "error": "GeminiGen cookie is required"}
        status = "configured"
        warning = ""
        if not bearer_token.strip():
            warning = "Bearer token not configured; cookie-only requests may fail if GeminiGen requires Authorization"
        if not guard_id.strip():
            warning = (warning + "; " if warning else "") + "guard_id not configured; protected endpoints may reject requests"
        return {"status": status, "error": warning}

    async def _request_proxy(self) -> Optional[str]:
        if not self.proxy_manager:
            return None
        try:
            if hasattr(self.proxy_manager, "get_request_proxy_url"):
                return await self.proxy_manager.get_request_proxy_url()
            if hasattr(self.proxy_manager, "get_proxy_url"):
                return await self.proxy_manager.get_proxy_url()
        except Exception as exc:
            debug_logger.log_warning(f"GeminiGen proxy lookup failed: {exc}")
        return None

    @staticmethod
    def _cookie_header(raw_cookie: str) -> str:
        raw = (raw_cookie or "").strip()
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        pairs: List[str] = []
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name:
                    pairs.append(f"{name}={value}")
        elif isinstance(parsed, dict):
            if "name" in parsed and "value" in parsed:
                name = str(parsed.get("name") or "").strip()
                value = str(parsed.get("value") or "").strip()
                if name:
                    pairs.append(f"{name}={value}")
            else:
                for name, value in parsed.items():
                    if str(name).strip():
                        pairs.append(f"{str(name).strip()}={str(value).strip()}")
        if pairs:
            return "; ".join(pairs)
        cleaned = raw.replace("\r", "\n")
        lines = [line.strip().strip(";") for line in cleaned.split("\n") if line.strip()]
        if len(lines) > 1:
            return "; ".join(lines)
        return raw.replace("\r", " ").replace("\n", " ").strip()

    @staticmethod
    def _bearer_header(raw_token: str) -> str:
        token = (raw_token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return f"Bearer {token}" if token else ""

    @staticmethod
    def _headers(account: GeminiGenAccount, path: str, *, multipart: bool = False) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://geminigen.ai",
            "Referer": "https://geminigen.ai/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        }
        cookie = GeminiGenService._cookie_header(account.raw_cookie)
        if cookie:
            headers["Cookie"] = cookie
        bearer = GeminiGenService._bearer_header(account.bearer_token)
        if bearer:
            headers["Authorization"] = bearer
        if account.guard_id:
            headers["x-guard-id"] = account.guard_id
        if not multipart:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _extract_uuid(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("uuid", "id", "history_uuid", "task_id", "job_id"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("data", "result", "history"):
                try:
                    found = GeminiGenService._extract_uuid(payload.get(key))
                    if found:
                        return found
                except Exception:
                    pass
        if isinstance(payload, list):
            for item in payload:
                found = GeminiGenService._extract_uuid(item)
                if found:
                    return found
        raise RuntimeError("GeminiGen response did not include a history uuid")

    @staticmethod
    def _extract_status(payload: Dict[str, Any]) -> str:
        for key in ("status", "state", "generation_status"):
            value = str(payload.get(key) or "").lower()
            if value:
                return value
        return ""

    @staticmethod
    def _walk_urls(value: Any, found: List[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lk = key.lower()
                if lk in {"image_url", "video_url", "file_download_url", "download_url", "url"} and isinstance(item, str):
                    if item.startswith(("http://", "https://")) and item not in found:
                        found.append(item)
                else:
                    GeminiGenService._walk_urls(item, found)
        elif isinstance(value, list):
            for item in value:
                GeminiGenService._walk_urls(item, found)

    @classmethod
    def extract_artifact_urls(cls, payload: Dict[str, Any], kind: str) -> List[str]:
        found: List[str] = []
        if kind == "video":
            cls._walk_urls(payload.get("generated_video"), found)
        else:
            cls._walk_urls(payload.get("generated_image"), found)
        cls._walk_urls(payload.get("file_download_url"), found)
        cls._walk_urls(payload.get("result"), found)
        cls._walk_urls(payload.get("data"), found)
        return found

    def _cache_url(self, filename: str, base_url: Optional[str]) -> str:
        base = (base_url or "").strip().rstrip("/")
        return f"{base}/api/cache/blob/{quote(filename, safe='')}" if base else f"/api/cache/blob/{quote(filename, safe='')}"

    async def _cache_artifacts(self, urls: List[str], *, kind: str, api_key_id: Optional[int], base_url: Optional[str], enabled: bool) -> List[str]:
        if not enabled:
            return []
        cached: List[str] = []
        for raw_url in urls:
            try:
                filename = await self.file_cache.download_and_cache(
                    raw_url,
                    media_type="video" if kind == "video" else "image",
                    api_key_id=api_key_id,
                    token_id=None,
                    flow_project_id=None,
                )
                cached.append(self._cache_url(filename, base_url))
            except Exception as exc:
                debug_logger.log_warning(f"GeminiGen artifact cache failed: {exc}")
        return cached

    @staticmethod
    def _data_url_from_image(image: bytes) -> str:
        mime_type = "image/png"
        if image.startswith(b"\xff\xd8\xff"):
            mime_type = "image/jpeg"
        elif image.startswith(b"RIFF") and image[8:12] == b"WEBP":
            mime_type = "image/webp"
        return f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"

    def _build_form(self, *, public_model_id: str, prompt: str, images: List[bytes], options: Dict[str, Any], extra_options: Dict[str, Any], account: GeminiGenAccount) -> Dict[str, Any]:
        endpoint_type = options["endpoint_type"]
        merged = dict(options.get("options") or {})
        merged.update({k: v for k, v in (extra_options or {}).items() if v is not None})
        form: Dict[str, Any] = {"prompt": prompt}
        if account.turnstile_token:
            form["turnstile_token"] = account.turnstile_token

        if endpoint_type == "imagen":
            form.update(
                {
                    "model": merged.get("model", "nano-banana-2"),
                    "aspect_ratio": merged.get("aspect_ratio", "16:9"),
                    "resolution": str(merged.get("resolution", "4K")).upper(),
                    "output_format": merged.get("output_format", "png"),
                }
            )
            if images:
                form["ref_images"] = [self._data_url_from_image(img) for img in images]
        elif endpoint_type == "grok-image":
            form.update(
                {
                    "orientation": merged.get("orientation", "landscape"),
                    "num_result": str(max(1, min(6, int(merged.get("num_result") or 1)))),
                    "mode": merged.get("mode", "normal"),
                }
            )
            if images:
                form["ref_images"] = [self._data_url_from_image(img) for img in images]
        elif endpoint_type == "veo-video":
            form.update(
                {
                    "model": merged.get("model", "veo-3.1-fast"),
                    "aspect_ratio": merged.get("aspect_ratio", "16:9"),
                    "duration": str(merged.get("duration", "8")),
                    "resolution": merged.get("resolution", "720p"),
                    "service_mode": merged.get("service_mode", "unstable"),
                }
            )
            ref_mode = merged.get("reference_mode")
            if images:
                refs = [self._data_url_from_image(img) for img in images]
                if ref_mode == "frame":
                    form["ref_images"] = refs[:2]
                else:
                    form["ref_images"] = refs
        elif endpoint_type == "grok-video":
            form.update(
                {
                    "model": "grok-video",
                    "aspect_ratio": merged.get("aspect_ratio", "landscape"),
                    "duration": str(merged.get("duration", "6")),
                    "resolution": merged.get("resolution", "720p"),
                }
            )
            if images:
                form["ref_images"] = [self._data_url_from_image(images[0])]
        return form

    @staticmethod
    def _endpoint_path(endpoint_type: str) -> str:
        return {
            "imagen": "/api/generate_image",
            "grok-image": "/api/imagen/grok",
            "veo-video": "/api/video-gen/veo",
            "grok-video": "/api/video-gen/grok-stream",
        }[endpoint_type]

    async def _post_generation(self, *, account: GeminiGenAccount, base_url: str, endpoint_type: str, form: Dict[str, Any]) -> Dict[str, Any]:
        path = self._endpoint_path(endpoint_type)
        url = f"{base_url.rstrip('/')}{path}"
        proxy = await self._request_proxy()
        files: List[Any] = []
        for key, value in form.items():
            if isinstance(value, list):
                for item in value:
                    files.append((key, (None, str(item))))
            else:
                files.append((key, (None, str(value))))
        async with AsyncSession() as session:
            response = await session.post(
                url,
                headers=self._headers(account, path, multipart=True),
                files=files,
                timeout=120,
                proxy=proxy,
                impersonate="chrome120",
            )
        if response.status_code >= 400:
            raise RuntimeError(f"GeminiGen POST {path} failed HTTP {response.status_code}: {response.text[:500]}")
        text = response.text or "{}"
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
        return json.loads(text) if text else {}

    async def _get_history(self, *, account: GeminiGenAccount, base_url: str, upstream_uuid: str) -> Dict[str, Any]:
        path = f"/api/history/{upstream_uuid}"
        proxy = await self._request_proxy()
        async with AsyncSession() as session:
            response = await session.get(
                f"{base_url.rstrip('/')}{path}",
                headers=self._headers(account, path),
                timeout=60,
                proxy=proxy,
                impersonate="chrome120",
            )
        if response.status_code >= 400:
            raise RuntimeError(f"GeminiGen GET {path} failed HTTP {response.status_code}: {response.text[:500]}")
        return response.json() if response.text else {}

    async def start_task(
        self,
        *,
        public_model_id: str,
        prompt: str,
        images: Optional[List[bytes]] = None,
        options: Optional[Dict[str, Any]] = None,
        api_key_id: Optional[int] = None,
    ) -> GeminiGenTask:
        manifest = geminigen_manifest_entry(public_model_id)
        if not manifest:
            raise RuntimeError(f"GeminiGen model does not exist: {public_model_id}")
        cfg = await self.db.get_geminigen_config()
        if not cfg.enabled:
            raise RuntimeError("GeminiGen integration is disabled")
        kind = str(manifest["kind"])
        job_id = f"geminigen-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        queued = GeminiGenTask(
            job_id=job_id,
            api_key_id=api_key_id,
            public_model_id=public_model_id,
            kind=kind,
            endpoint_type=str(manifest["endpoint_type"]),
            prompt=prompt,
            status="queued",
            progress=0,
            request_payload=json.dumps({"images": len(images or []), "options": options or {}}, ensure_ascii=False),
        )
        await self.db.create_geminigen_task(queued)
        return await self._start_queued_task(job_id, images=images or [], options=options or {})

    async def _start_queued_task(self, job_id: str, *, images: List[bytes], options: Dict[str, Any]) -> GeminiGenTask:
        task = await self.db.get_geminigen_task(job_id)
        if not task:
            raise RuntimeError("GeminiGen task not found")
        cfg = await self.db.get_geminigen_config()
        timeout = cfg.timeout_video_sec if task.kind == "video" else cfg.timeout_image_sec
        deadline = time.monotonic() + float(timeout)
        account: Optional[GeminiGenAccount] = None
        while time.monotonic() < deadline:
            account = await self.db.acquire_geminigen_account(task.kind)
            if account:
                break
            await asyncio.sleep(1.0)
        if not account:
            error = "GeminiGen queue timed out waiting for an available account slot"
            await self.db.update_geminigen_task(job_id, status="failed", error_message=error, completed_at=datetime.utcnow())
            raise RuntimeError(error)
        manifest = geminigen_manifest_entry(task.public_model_id)
        release_now = False
        try:
            form = self._build_form(
                public_model_id=task.public_model_id,
                prompt=task.prompt,
                images=images,
                options=manifest or {},
                extra_options=options,
                account=account,
            )
            await self.db.update_geminigen_task(
                job_id,
                account_id=account.id,
                status="processing",
                progress=1,
                started_at=datetime.utcnow(),
                request_payload=json.dumps(form, ensure_ascii=False),
            )
            created = await self._post_generation(
                account=account,
                base_url=cfg.base_url,
                endpoint_type=task.endpoint_type,
                form=form,
            )
            upstream_uuid = self._extract_uuid(created)
            await self.db.update_geminigen_task(
                job_id,
                upstream_uuid=upstream_uuid,
                response_payload=json.dumps(created, ensure_ascii=False),
                progress=5,
            )
            return await self.db.get_geminigen_task(job_id) or task
        except Exception as exc:
            release_now = True
            await self.db.update_geminigen_task(job_id, status="failed", error_message=str(exc), completed_at=datetime.utcnow())
            await self.db.update_geminigen_account(account.id or 0, last_status="failed", last_error=str(exc))
            raise
        finally:
            if release_now:
                await self.db.release_geminigen_account(account.id, task.kind)

    async def poll_task(self, job_id: str, *, api_key_id: Optional[int] = None, base_url: Optional[str] = None) -> GeminiGenTask:
        task = await self.db.get_geminigen_task(job_id)
        if not task:
            raise KeyError("GeminiGen job not found")
        if api_key_id is not None and task.api_key_id is not None and int(api_key_id) != int(task.api_key_id):
            raise PermissionError("Not authorized to view this GeminiGen job")
        if task.status in {"completed", "failed", "cancelled"}:
            return task
        if task.status == "queued":
            return task
        account = await self.db.get_geminigen_account(int(task.account_id or 0))
        if not account:
            await self.db.update_geminigen_task(job_id, status="failed", error_message="GeminiGen account no longer exists", completed_at=datetime.utcnow())
            return await self.db.get_geminigen_task(job_id) or task
        cfg = await self.db.get_geminigen_config()
        try:
            payload = await self._get_history(account=account, base_url=cfg.base_url, upstream_uuid=task.upstream_uuid or "")
            status_text = self._extract_status(payload)
            failed = any(x in status_text for x in ("fail", "error", "reject", "cancel"))
            urls = self.extract_artifact_urls(payload, task.kind)
            completed = bool(urls) or any(x in status_text for x in ("complete", "success", "finished"))
            if completed:
                cached = await self._cache_artifacts(urls, kind=task.kind, api_key_id=task.api_key_id, base_url=base_url, enabled=bool(cfg.cache_outputs))
                await self.db.update_geminigen_task(
                    job_id,
                    status="completed",
                    progress=100,
                    raw_artifact_urls=urls,
                    cached_artifact_urls=cached,
                    response_payload=json.dumps(payload, ensure_ascii=False),
                    completed_at=datetime.utcnow(),
                )
                await self.db.release_geminigen_account(task.account_id, task.kind)
            elif failed:
                error_text = str(payload.get("error") or payload.get("message") or status_text or "GeminiGen task failed")
                await self.db.update_geminigen_task(job_id, status="failed", error_message=error_text, response_payload=json.dumps(payload, ensure_ascii=False), completed_at=datetime.utcnow())
                await self.db.release_geminigen_account(task.account_id, task.kind)
            else:
                await self.db.update_geminigen_task(job_id, status="processing", progress=max(task.progress, 10), response_payload=json.dumps(payload, ensure_ascii=False))
            return await self.db.get_geminigen_task(job_id) or task
        except Exception as exc:
            await self.db.update_geminigen_task(job_id, status="failed", error_message=str(exc), completed_at=datetime.utcnow())
            await self.db.release_geminigen_account(task.account_id, task.kind)
            return await self.db.get_geminigen_task(job_id) or task

    async def wait_for_task(self, job_id: str, *, api_key_id: Optional[int], base_url: Optional[str]) -> GeminiGenTask:
        task = await self.db.get_geminigen_task(job_id)
        cfg = await self.db.get_geminigen_config()
        timeout = cfg.timeout_video_sec if task and task.kind == "video" else cfg.timeout_image_sec
        interval = cfg.poll_interval_video_sec if task and task.kind == "video" else cfg.poll_interval_image_sec
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            task = await self.poll_task(job_id, api_key_id=api_key_id, base_url=base_url)
            if task.status in {"completed", "failed", "cancelled"}:
                return task
            await asyncio.sleep(float(interval))
        await self.db.update_geminigen_task(job_id, status="failed", error_message=f"GeminiGen task did not finish within {timeout}s", completed_at=datetime.utcnow())
        if task:
            await self.db.release_geminigen_account(task.account_id, task.kind)
        return await self.db.get_geminigen_task(job_id) or task

    async def test_account(self, account_id: int) -> Dict[str, Any]:
        account = await self.db.get_geminigen_account(account_id)
        if not account:
            raise ValueError("GeminiGen account not found")
        cfg = await self.db.get_geminigen_config()
        try:
            proxy = await self._request_proxy()
            async with AsyncSession() as session:
                response = await session.get(
                    f"{cfg.base_url.rstrip('/')}/api/me",
                    headers=self._headers(account, "/api/me"),
                    timeout=30,
                    proxy=proxy,
                    impersonate="chrome120",
                )
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            status = "healthy"
            error = ""
        except Exception as exc:
            status = "failed"
            error = str(exc)
        await self.db.update_geminigen_account(account_id, last_status=status, last_error=error)
        return {"success": status == "healthy", "status": status, "error": error}

    @staticmethod
    def task_to_public_dict(task: GeminiGenTask) -> Dict[str, Any]:
        return {
            "job_id": task.job_id,
            "upstream_uuid": task.upstream_uuid,
            "status": task.status,
            "progress": task.progress,
            "model": task.public_model_id,
            "raw_artifact_urls": task.raw_artifact_urls or [],
            "cached_artifact_urls": task.cached_artifact_urls or [],
            "result_urls": task.cached_artifact_urls or task.raw_artifact_urls or [],
            "error_message": task.error_message,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        }

    @staticmethod
    def task_to_openai_payload(task: GeminiGenTask) -> Dict[str, Any]:
        if task.status in {"failed", "cancelled"}:
            return {
                "error": {
                    "message": task.error_message or f"GeminiGen task {task.status}",
                    "type": "server_error",
                    "code": "geminigen_generation_failed",
                    "status_code": 502,
                },
                "job_id": task.job_id,
            }
        urls = task.cached_artifact_urls or task.raw_artifact_urls or []
        parts: List[str] = []
        for url in urls:
            suffix = Path(urlparse(url).path).suffix.lower()
            if task.kind == "video" or suffix in VIDEO_SUFFIXES:
                parts.append(f"<video src='{url}' controls></video>")
            else:
                parts.append(f"![Generated Image]({url})")
        return {
            "id": f"chatcmpl-geminigen-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": task.public_model_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "\n".join(parts)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "job_id": task.job_id,
            "raw_artifact_urls": task.raw_artifact_urls or [],
            "cached_artifact_urls": task.cached_artifact_urls or [],
        }
