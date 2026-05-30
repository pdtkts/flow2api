"""Runway web-task integration used by Flow2API."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from curl_cffi.requests import AsyncSession

from ..core.database import Database
from ..core.logger import debug_logger
from ..core.models import RunwayAccount, RunwayModel, RunwayTask
from .file_cache import FileCache


RUNWAY_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
RUNWAY_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"}


@dataclass
class RunwayCredential:
    bearer_token: str
    workspace_id: Optional[str] = None
    team_id: Optional[str] = None


class RunwayService:
    """Create, poll, and normalize Runway web-app tasks."""

    def __init__(self, db: Database, file_cache: FileCache, proxy_manager=None):
        self.db = db
        self.file_cache = file_cache
        self.proxy_manager = proxy_manager

    @staticmethod
    def is_runway_model(model: str) -> bool:
        return bool((model or "").strip().startswith("runway-"))

    @staticmethod
    def _json_loads(raw: Any, fallback: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        text = str(raw or "").strip()
        if not text:
            return fallback
        try:
            return json.loads(text)
        except Exception:
            return fallback

    @staticmethod
    def _decode_jwt_payload(token: str) -> Dict[str, Any]:
        parts = (token or "").split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
            data = json.loads(decoded.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @classmethod
    def normalize_credential(cls, raw_credential: str) -> RunwayCredential:
        raw = (raw_credential or "").strip()
        if not raw:
            raise ValueError("Runway credential is empty")

        token = raw if raw.count(".") == 2 and raw.startswith("eyJ") else ""
        if not token:
            match = RUNWAY_JWT_RE.search(raw)
            token = match.group(0) if match else ""
        if not token:
            raise ValueError("Could not find a Runway JWT in the credential")

        payload = cls._decode_jwt_payload(token)
        workspace_id = payload.get("workspaceId") or payload.get("workspace_id") or payload.get("id")
        team_id = payload.get("teamId") or payload.get("team_id") or workspace_id
        return RunwayCredential(
            bearer_token=token,
            workspace_id=str(workspace_id) if workspace_id is not None else None,
            team_id=str(team_id) if team_id is not None else None,
        )

    @classmethod
    def describe_credential(cls, raw_credential: str) -> Dict[str, Any]:
        try:
            cred = cls.normalize_credential(raw_credential)
            return {
                "workspace_id": cred.workspace_id,
                "team_id": cred.team_id,
                "status": "configured" if cred.workspace_id else "missing_workspace",
                "error": "" if cred.workspace_id else "JWT decoded but no workspace id was found",
            }
        except Exception as exc:
            return {
                "workspace_id": "",
                "team_id": "",
                "status": "invalid_credential",
                "error": str(exc),
            }

    @staticmethod
    def build_headers(credential: RunwayCredential, workspace_id: Optional[str] = None) -> Dict[str, str]:
        wid = (workspace_id or credential.workspace_id or "").strip()
        if not wid:
            raise ValueError("Runway workspace id is required")
        return {
            "Authorization": f"Bearer {credential.bearer_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://app.runwayml.com",
            "Referer": "https://app.runwayml.com/",
            "x-runway-workspace": wid,
        }

    async def _request_proxy(self) -> Optional[str]:
        if not self.proxy_manager:
            return None
        try:
            if hasattr(self.proxy_manager, "get_request_proxy_url"):
                return await self.proxy_manager.get_request_proxy_url()
            if hasattr(self.proxy_manager, "get_proxy_url"):
                return await self.proxy_manager.get_proxy_url()
        except Exception as exc:
            debug_logger.log_warning(f"Runway proxy lookup failed: {exc}")
        return None

    @staticmethod
    def _extract_upstream_task_id(payload: Dict[str, Any]) -> str:
        task = payload.get("task") if isinstance(payload, dict) else None
        if isinstance(task, dict):
            for key in ("id", "taskId", "uuid"):
                value = task.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("id", "taskId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError("Runway create task response did not include a task id")

    @staticmethod
    def normalize_status(raw_status: Any) -> str:
        status = str(raw_status or "").strip().upper()
        if status == "SUCCEEDED":
            return "completed"
        if status in {"FAILED", "CANCELLED", "REJECTED"}:
            return "failed"
        return "processing"

    @staticmethod
    def normalize_progress(task: Dict[str, Any]) -> int:
        raw = task.get("progressRatio", task.get("progress", 0))
        try:
            value = float(raw)
        except Exception:
            return 0
        if value <= 1:
            value *= 100
        return max(0, min(100, int(round(value))))

    @staticmethod
    def _walk_artifact_urls(value: Any, found: List[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"url", "fifeurl", "fileurl", "downloadurl"} and isinstance(item, str):
                    if item.startswith(("http://", "https://")) and item not in found:
                        found.append(item)
                else:
                    RunwayService._walk_artifact_urls(item, found)
        elif isinstance(value, list):
            for item in value:
                RunwayService._walk_artifact_urls(item, found)

    @classmethod
    def extract_artifact_urls(cls, payload: Dict[str, Any]) -> List[str]:
        task = payload.get("task") if isinstance(payload, dict) else None
        found: List[str] = []
        if isinstance(task, dict):
            cls._walk_artifact_urls(task.get("artifacts"), found)
            cls._walk_artifact_urls(task.get("outputs"), found)
            cls._walk_artifact_urls(task.get("output"), found)
        cls._walk_artifact_urls(payload.get("artifacts"), found)
        cls._walk_artifact_urls(payload.get("output"), found)
        return found

    @staticmethod
    def _media_type_for_url(url: str, model_kind: str) -> str:
        if model_kind == "video":
            return "video"
        suffix = Path(urlparse(url).path).suffix.lower()
        guessed, _ = mimetypes.guess_type(urlparse(url).path)
        if suffix in {".mp4", ".webm", ".mov", ".mkv", ".m4v"} or (guessed or "").startswith("video/"):
            return "video"
        return "image"

    def _cache_url(self, filename: str, base_url: Optional[str]) -> str:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            base = ""
        return f"{base}/api/cache/blob/{quote(filename, safe='')}" if base else f"/api/cache/blob/{quote(filename, safe='')}"

    async def _cache_artifacts(
        self,
        raw_urls: List[str],
        *,
        model_kind: str,
        api_key_id: Optional[int],
        base_url: Optional[str],
        enabled: bool,
    ) -> List[str]:
        if not enabled:
            return []
        cached: List[str] = []
        for raw_url in raw_urls:
            try:
                media_type = self._media_type_for_url(raw_url, model_kind)
                filename = await self.file_cache.download_and_cache(
                    raw_url,
                    media_type=media_type,
                    api_key_id=api_key_id,
                    token_id=None,
                    flow_project_id=None,
                )
                cached.append(self._cache_url(filename, base_url))
            except Exception as exc:
                debug_logger.log_warning(f"Runway artifact cache failed: {exc}")
        return cached

    async def create_upstream_task(
        self,
        *,
        account: RunwayAccount,
        payload: Dict[str, Any],
        base_url: str,
    ) -> Dict[str, Any]:
        credential = self.normalize_credential(account.raw_credential)
        workspace_id = account.workspace_id or credential.workspace_id
        headers = self.build_headers(credential, workspace_id=workspace_id)
        proxy = await self._request_proxy()
        async with AsyncSession() as session:
            response = await session.post(
                f"{base_url.rstrip('/')}/tasks",
                headers=headers,
                json=payload,
                timeout=120,
                proxy=proxy,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Runway create task failed HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    async def get_upstream_task(
        self,
        *,
        account: RunwayAccount,
        upstream_task_id: str,
        base_url: str,
    ) -> Dict[str, Any]:
        credential = self.normalize_credential(account.raw_credential)
        workspace_id = account.workspace_id or credential.workspace_id
        team_id = account.team_id or credential.team_id or workspace_id
        headers = self.build_headers(credential, workspace_id=workspace_id)
        proxy = await self._request_proxy()
        async with AsyncSession() as session:
            response = await session.get(
                f"{base_url.rstrip('/')}/tasks/{upstream_task_id}",
                headers=headers,
                params={"asTeamId": team_id},
                timeout=60,
                proxy=proxy,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Runway get task failed HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    async def test_account(self, account_id: int) -> Dict[str, Any]:
        account = await self.db.get_runway_account(account_id)
        if not account:
            raise ValueError("Runway account not found")
        config = await self.db.get_runway_config()
        try:
            credential = self.normalize_credential(account.raw_credential)
            workspace_id = account.workspace_id or credential.workspace_id
            headers = self.build_headers(credential, workspace_id=workspace_id)
            proxy = await self._request_proxy()
            async with AsyncSession() as session:
                response = await session.get(
                    f"{config.base_url.rstrip('/')}/tasks/flow2api-health-check",
                    headers=headers,
                    params={"asTeamId": account.team_id or credential.team_id or workspace_id},
                    timeout=20,
                    proxy=proxy,
                )
            ok = response.status_code in {200, 404}
            status = "healthy" if ok else "failed"
            error = "" if ok else f"HTTP {response.status_code}: {response.text[:240]}"
        except Exception as exc:
            status = "failed"
            error = str(exc)
        await self.db.update_runway_account(account_id, last_status=status, last_error=error)
        return {"success": status == "healthy", "status": status, "error": error}

    def build_task_payload(
        self,
        *,
        model: RunwayModel,
        account: RunwayAccount,
        prompt: str,
        media: Optional[List[Dict[str, Any]]] = None,
        aspect_ratio: Optional[str] = None,
        duration: Optional[int] = None,
        image_size: Optional[str] = None,
        num_outputs: Optional[int] = None,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        default_options = self._json_loads(model.default_options, {})
        if not isinstance(default_options, dict):
            default_options = {}
        request_mapping = self._json_loads(model.request_mapping, {})
        if not isinstance(request_mapping, dict):
            request_mapping = {}

        merged_options: Dict[str, Any] = dict(default_options)
        if isinstance(options, dict):
            merged_options.update(options)

        prompt_field = str(request_mapping.get("prompt") or "text_prompt")
        if prompt:
            merged_options[prompt_field] = prompt
            merged_options.setdefault("name", prompt[:96])

        if aspect_ratio:
            merged_options[str(request_mapping.get("aspect_ratio") or "aspect_ratio")] = aspect_ratio
        if duration is not None:
            merged_options[str(request_mapping.get("duration") or "duration")] = int(duration)
        if image_size:
            merged_options[str(request_mapping.get("image_size") or "image_size")] = image_size
        if num_outputs is not None:
            output_field = str(request_mapping.get("num_outputs") or ("num_images" if model.kind == "image" else "num_outputs"))
            merged_options[output_field] = max(1, int(num_outputs))
        if seed is not None:
            merged_options[str(request_mapping.get("seed") or "seed")] = int(seed)

        media_items = media or []
        media_values = [item.get("url") or item.get("data_url") or item.get("uri") for item in media_items if isinstance(item, dict)]
        media_values = [str(item) for item in media_values if item]
        if media_values:
            media_field = str(request_mapping.get("media") or "reference_images")
            if media_field in {"prompt_image", "promptImage", "image", "uri"}:
                merged_options[media_field] = media_values[0]
            else:
                merged_options[media_field] = media_values

        credential = self.normalize_credential(account.raw_credential)
        team_id = account.team_id or credential.team_id or account.workspace_id or credential.workspace_id
        return {
            "taskType": model.task_type,
            "options": merged_options,
            "asTeamId": int(team_id) if str(team_id or "").isdigit() else team_id,
            "sessionId": str(uuid.uuid4()),
        }

    async def start_task(
        self,
        *,
        public_model_id: str,
        prompt: str,
        media: Optional[List[Dict[str, Any]]] = None,
        aspect_ratio: Optional[str] = None,
        duration: Optional[int] = None,
        image_size: Optional[str] = None,
        num_outputs: Optional[int] = None,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
        api_key_id: Optional[int] = None,
    ) -> RunwayTask:
        config = await self.db.get_runway_config()
        if not config.enabled:
            raise RuntimeError("Runway integration is disabled")

        model = await self.db.get_runway_model(public_model_id)
        if not model or not model.is_enabled:
            raise RuntimeError(f"Runway model is not enabled or does not exist: {public_model_id}")

        account = await self.db.acquire_runway_account()
        if not account:
            raise RuntimeError("No active Runway account is available")

        job_id = f"runway-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        release_now = False
        try:
            payload = self.build_task_payload(
                model=model,
                account=account,
                prompt=prompt,
                media=media,
                aspect_ratio=aspect_ratio,
                duration=duration,
                image_size=image_size,
                num_outputs=num_outputs,
                seed=seed,
                options=options,
            )
            created = await self.create_upstream_task(account=account, payload=payload, base_url=config.base_url)
            upstream_task_id = self._extract_upstream_task_id(created)
            task = RunwayTask(
                job_id=job_id,
                upstream_task_id=upstream_task_id,
                account_id=account.id,
                api_key_id=api_key_id,
                public_model_id=public_model_id,
                prompt=prompt,
                status="processing",
                progress=0,
                request_payload=json.dumps(payload, ensure_ascii=False),
                response_payload=json.dumps(created, ensure_ascii=False),
            )
            await self.db.create_runway_task(task)
            await self.db.update_runway_account(account.id or 0, last_status="task_created", last_error="")
            return task
        except Exception as exc:
            release_now = True
            await self.db.update_runway_account(account.id or 0, last_status="failed", last_error=str(exc))
            raise
        finally:
            if release_now:
                await self.db.release_runway_account(account.id)

    async def poll_task(
        self,
        job_id: str,
        *,
        api_key_id: Optional[int] = None,
        base_url: Optional[str] = None,
    ) -> RunwayTask:
        task = await self.db.get_runway_task(job_id)
        if not task:
            raise KeyError("Runway job not found")
        if api_key_id is not None and task.api_key_id is not None and int(api_key_id) != int(task.api_key_id):
            raise PermissionError("Not authorized to view this Runway job")
        if task.status in {"completed", "failed"}:
            return task

        config = await self.db.get_runway_config()
        account = await self.db.get_runway_account(int(task.account_id or 0))
        if not account:
            await self.db.update_runway_task(job_id, status="failed", error_message="Runway account no longer exists", completed_at=datetime.utcnow())
            return await self.db.get_runway_task(job_id) or task

        try:
            payload = await self.get_upstream_task(
                account=account,
                upstream_task_id=task.upstream_task_id or "",
                base_url=config.base_url,
            )
            upstream_task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
            raw_status = upstream_task.get("status") if isinstance(upstream_task, dict) else ""
            status = self.normalize_status(raw_status)
            progress = 100 if status == "completed" else self.normalize_progress(upstream_task if isinstance(upstream_task, dict) else {})
            raw_urls = self.extract_artifact_urls(payload) if status == "completed" else []
            cached_urls = await self._cache_artifacts(
                raw_urls,
                model_kind=(await self.db.get_runway_model(task.public_model_id) or RunwayModel(public_model_id=task.public_model_id, task_type="", kind="image")).kind,
                api_key_id=task.api_key_id,
                base_url=base_url,
                enabled=bool(config.cache_outputs),
            ) if raw_urls else []
            error_message = ""
            if status == "failed":
                error_message = str(
                    upstream_task.get("failureReason")
                    or upstream_task.get("error")
                    or upstream_task.get("message")
                    or raw_status
                    or "Runway task failed"
                )
            terminal = status in {"completed", "failed"}
            await self.db.update_runway_task(
                job_id,
                status=status,
                progress=progress,
                raw_artifact_urls=raw_urls if raw_urls else None,
                cached_artifact_urls=cached_urls if cached_urls else None,
                response_payload=json.dumps(payload, ensure_ascii=False),
                error_message=error_message or None,
                completed_at=datetime.utcnow() if terminal else None,
            )
            if terminal:
                await self.db.release_runway_account(task.account_id)
            return await self.db.get_runway_task(job_id) or task
        except Exception as exc:
            await self.db.update_runway_task(
                job_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.utcnow(),
            )
            await self.db.release_runway_account(task.account_id)
            return await self.db.get_runway_task(job_id) or task

    async def wait_for_task(
        self,
        job_id: str,
        *,
        api_key_id: Optional[int],
        base_url: Optional[str],
    ) -> RunwayTask:
        config = await self.db.get_runway_config()
        deadline = time.monotonic() + float(config.timeout_sec)
        task = await self.db.get_runway_task(job_id)
        while time.monotonic() < deadline:
            task = await self.poll_task(job_id, api_key_id=api_key_id, base_url=base_url)
            if task.status in {"completed", "failed"}:
                return task
            await asyncio.sleep(float(config.poll_interval_sec))
        await self.db.update_runway_task(
            job_id,
            status="failed",
            error_message=f"Runway task did not finish within {config.timeout_sec}s",
            completed_at=datetime.utcnow(),
        )
        if task:
            await self.db.release_runway_account(task.account_id)
        return await self.db.get_runway_task(job_id) or task

    @staticmethod
    def task_to_public_dict(task: RunwayTask) -> Dict[str, Any]:
        return {
            "job_id": task.job_id,
            "upstream_task_id": task.upstream_task_id,
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
    def task_to_openai_payload(task: RunwayTask) -> Dict[str, Any]:
        if task.status == "failed":
            return {
                "error": {
                    "message": task.error_message or "Runway task failed",
                    "type": "server_error",
                    "code": "runway_generation_failed",
                    "status_code": 502,
                },
                "job_id": task.job_id,
                "raw_artifact_urls": task.raw_artifact_urls or [],
            }
        urls = task.cached_artifact_urls or task.raw_artifact_urls or []
        parts: List[str] = []
        for url in urls:
            media_type = "video" if Path(urlparse(url).path).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".m4v"} else "image"
            if media_type == "video":
                parts.append(f"<video src='{url}' controls></video>")
            else:
                parts.append(f"![Generated Image]({url})")
        content = "\n".join(parts) if parts else "Runway task completed without artifact URLs."
        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": task.public_model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "job_id": task.job_id,
            "raw_artifact_urls": task.raw_artifact_urls or [],
            "cached_artifact_urls": task.cached_artifact_urls or [],
            "url": urls[0] if urls else None,
        }

