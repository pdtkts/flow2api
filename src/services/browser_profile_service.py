"""Persistent headed Chrome profiles for Flow account login and refresh."""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.logger import debug_logger
from ..core.models import Token
from ..core.config import get_runtime_data_dir
from .browser_cookie_utils import extract_session_token_from_cookie_payload


BROWSER_PROFILE_ROOT = get_runtime_data_dir() / "browser_profiles"
LOGIN_URL = "https://accounts.google.com/"
FLOW_URL = "https://labs.google/fx/tools/flow"


@dataclass
class ProfileRuntime:
    context: Any
    page: Any
    lock: asyncio.Lock


class BrowserProfileService:
    """Manage one persistent headed browser profile per Flow token/account."""

    _instance: Optional["BrowserProfileService"] = None
    _instance_lock = asyncio.Lock()

    def __init__(self, db=None, flow_client=None):
        self.db = db
        self.flow_client = flow_client
        self._playwright = None
        self._runtimes: Dict[int, ProfileRuntime] = {}
        self._runtime_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, db=None, flow_client=None) -> "BrowserProfileService":
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(db=db, flow_client=flow_client)
            else:
                if db is not None:
                    cls._instance.db = db
                if flow_client is not None:
                    cls._instance.flow_client = flow_client
            return cls._instance

    @staticmethod
    def build_placeholder_st() -> str:
        return f"__browser_profile_pending_{uuid.uuid4().hex}"

    @staticmethod
    def build_placeholder_email(token_id: Optional[int] = None) -> str:
        suffix = str(token_id) if token_id else uuid.uuid4().hex[:8]
        return f"browser-profile-{suffix}@pending.local"

    def profile_path_for_token(self, token_id: int) -> Path:
        return (BROWSER_PROFILE_ROOT / f"token-{int(token_id)}").resolve()

    async def _ensure_playwright(self):
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        return self._playwright

    def _launch_options(self) -> Dict[str, Any]:
        executable_path = os.environ.get("BROWSER_EXECUTABLE_PATH", "").strip() or None
        headless = os.environ.get("PERSONAL_BROWSER_HEADLESS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        options: Dict[str, Any] = {
            "headless": headless,
            "viewport": {"width": 1365, "height": 900},
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if executable_path and Path(executable_path).exists():
            options["executable_path"] = executable_path
        return options

    async def _get_runtime(self, token_id: int, *, open_url: Optional[str] = None) -> ProfileRuntime:
        async with self._runtime_lock:
            existing = self._runtimes.get(int(token_id))
            if existing is not None:
                if open_url:
                    await existing.page.goto(open_url, wait_until="domcontentloaded", timeout=45000)
                return existing

            playwright = await self._ensure_playwright()
            profile_path = self.profile_path_for_token(token_id)
            profile_path.mkdir(parents=True, exist_ok=True)
            context = await playwright.chromium.launch_persistent_context(
                str(profile_path),
                **self._launch_options(),
            )
            page = context.pages[0] if context.pages else await context.new_page()
            if open_url:
                await page.goto(open_url, wait_until="domcontentloaded", timeout=45000)
            runtime = ProfileRuntime(context=context, page=page, lock=asyncio.Lock())
            self._runtimes[int(token_id)] = runtime
            return runtime

    async def close_runtime(self, token_id: int) -> None:
        async with self._runtime_lock:
            runtime = self._runtimes.pop(int(token_id), None)
        if runtime is not None:
            try:
                await runtime.context.close()
            except Exception:
                pass

    def _token_status_payload(self, token: Token) -> Dict[str, Any]:
        return {
            "token_id": token.id,
            "auth_mode": token.auth_mode,
            "profile_path": token.browser_profile_path,
            "profile_status": token.browser_profile_status,
            "login_state": token.browser_profile_login_state,
            "cookie_status": token.browser_profile_cookie_status,
            "st_status": token.browser_profile_st_status,
            "at_status": token.browser_profile_at_status,
            "email": token.browser_profile_email or token.email,
            "name": token.browser_profile_name or token.name,
            "last_opened_at": token.browser_profile_last_opened_at.isoformat() if token.browser_profile_last_opened_at else None,
            "last_sync_at": token.browser_profile_last_sync_at.isoformat() if token.browser_profile_last_sync_at else None,
            "last_refresh_at": token.browser_profile_last_refresh_at.isoformat() if token.browser_profile_last_refresh_at else None,
            "last_error": token.browser_profile_last_error,
            "runtime_open": int(token.id or 0) in self._runtimes,
        }

    async def status(self, token_id: int) -> Dict[str, Any]:
        token = await self.db.get_token(int(token_id))
        if not token:
            raise ValueError("Token not found")
        return self._token_status_payload(token)

    async def open_profile(self, token_id: int) -> Dict[str, Any]:
        token = await self.db.get_token(int(token_id))
        if not token:
            raise ValueError("Token not found")
        profile_path = str(self.profile_path_for_token(token_id))
        now = datetime.now(timezone.utc)
        try:
            await self._get_runtime(token_id, open_url=LOGIN_URL)
            await self.db.update_token(
                token_id,
                auth_mode="browser_profile",
                browser_profile_path=profile_path,
                browser_profile_status="opened",
                browser_profile_login_state="login_needed",
                browser_profile_last_opened_at=now,
                browser_profile_last_error=None,
            )
        except Exception as exc:
            await self.db.update_token(
                token_id,
                auth_mode="browser_profile",
                browser_profile_path=profile_path,
                browser_profile_status="error",
                browser_profile_last_error=str(exc)[:500],
            )
            raise
        updated = await self.db.get_token(token_id)
        return self._token_status_payload(updated)

    async def _collect_cookies(self, runtime: ProfileRuntime) -> list[dict[str, Any]]:
        cookies = await runtime.context.cookies(
            [
                "https://labs.google/",
                "https://accounts.google.com/",
                "https://www.google.com/",
            ]
        )
        return [dict(cookie) for cookie in cookies]

    async def sync_profile(self, token_id: int) -> Dict[str, Any]:
        token = await self.db.get_token(int(token_id))
        if not token:
            raise ValueError("Token not found")
        profile_path = str(self.profile_path_for_token(token_id))
        now = datetime.now(timezone.utc)
        runtime = await self._get_runtime(token_id, open_url=FLOW_URL)
        async with runtime.lock:
            try:
                cookies = await self._collect_cookies(runtime)
                session_token = extract_session_token_from_cookie_payload(cookies)
                if not session_token:
                    await self.db.update_token(
                        token_id,
                        auth_mode="browser_profile",
                        browser_profile_path=profile_path,
                        browser_profile_status="login_needed",
                        browser_profile_login_state="login_needed",
                        browser_profile_cookie_status="missing_session_cookie",
                        browser_profile_st_status="missing",
                        browser_profile_last_sync_at=now,
                        browser_profile_last_error="Session cookie not found in browser profile",
                    )
                    updated = await self.db.get_token(token_id)
                    return self._token_status_payload(updated)

                if self.flow_client is None:
                    raise RuntimeError("Flow client is unavailable")
                result = await self.flow_client.st_to_at(session_token)
                access_token = result.get("access_token")
                expires = result.get("expires")
                at_expires = None
                if expires:
                    try:
                        at_expires = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                    except Exception:
                        at_expires = None
                user_info = result.get("user", {}) if isinstance(result, dict) else {}
                email = str(user_info.get("email") or token.email or "").strip()
                name = str(user_info.get("name") or token.name or "").strip()
                if not email or email.endswith("@pending.local"):
                    email = self.build_placeholder_email(token_id)
                await self.db.update_token(
                    token_id,
                    st=session_token,
                    at=access_token,
                    at_expires=at_expires,
                    email=email,
                    name=name or email.split("@")[0],
                    auth_mode="browser_profile",
                    browser_profile_path=profile_path,
                    browser_profile_status="connected",
                    browser_profile_email=email,
                    browser_profile_name=name or email.split("@")[0],
                    browser_profile_login_state="logged_in",
                    browser_profile_cookie_status="ok",
                    browser_profile_st_status="ok",
                    browser_profile_at_status="ok" if access_token else "missing",
                    browser_profile_last_sync_at=now,
                    browser_profile_last_refresh_at=now,
                    browser_profile_last_error=None,
                )
            except Exception as exc:
                debug_logger.log_warning(f"[BrowserProfile] sync failed for token {token_id}: {exc}")
                await self.db.update_token(
                    token_id,
                    auth_mode="browser_profile",
                    browser_profile_path=profile_path,
                    browser_profile_status="error",
                    browser_profile_last_sync_at=now,
                    browser_profile_last_error=str(exc)[:500],
                )
                raise
        updated = await self.db.get_token(token_id)
        return self._token_status_payload(updated)

    async def refresh_profile(self, token_id: int) -> Dict[str, Any]:
        return await self.sync_profile(token_id)

    async def reset_profile(self, token_id: int) -> Dict[str, Any]:
        token = await self.db.get_token(int(token_id))
        if not token:
            raise ValueError("Token not found")
        await self.close_runtime(token_id)
        profile_path = self.profile_path_for_token(token_id)
        if profile_path.exists():
            shutil.rmtree(profile_path)
        await self.db.update_token(
            token_id,
            st=self.build_placeholder_st(),
            at=None,
            at_expires=None,
            is_active=False,
            auth_mode="browser_profile",
            browser_profile_path=str(profile_path),
            browser_profile_status="not_created",
            browser_profile_login_state="unknown",
            browser_profile_cookie_status="unknown",
            browser_profile_st_status="unknown",
            browser_profile_at_status="unknown",
            browser_profile_last_error=None,
        )
        updated = await self.db.get_token(token_id)
        return self._token_status_payload(updated)
