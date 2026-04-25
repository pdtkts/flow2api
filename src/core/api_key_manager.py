"""API key manager with account assignment and endpoint rate limits."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple


@dataclass
class AuthContext:
    """Resolved auth context for one request."""

    key_id: Optional[int]
    key_label: str
    is_legacy: bool
    allowed_accounts: Set[int]
    scopes: Set[str]


class ApiKeyManager:
    """Validates API keys, account bindings, and simple fixed-window rate limits."""

    def __init__(self, db, legacy_api_key_provider):
        self.db = db
        self.legacy_api_key_provider = legacy_api_key_provider
        self._rate_limit_lock = asyncio.Lock()
        self._window_counters: Dict[Tuple[int, str, int], int] = {}

    @staticmethod
    def _digest(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_key(prefix: str = "f2a_live") -> Tuple[str, str]:
        secret = secrets.token_urlsafe(36)
        full_key = f"{prefix}_{secret}"
        return full_key, ApiKeyManager._digest(full_key)

    async def create_api_key(
        self,
        client_name: str,
        label: str,
        scopes: str,
        account_ids: list[int],
        endpoint_limits: dict[str, dict[str, int]],
        expires_at: Optional[str] = None,
    ) -> dict:
        full_key, key_hash = self.generate_key()
        key_prefix = full_key[:18]
        key_id = await self.db.create_client_api_key(
            client_name=client_name.strip(),
            label=label.strip() or "default",
            key_prefix=key_prefix,
            key_plaintext=full_key,
            key_hash=key_hash,
            scopes=scopes.strip() or "*",
            account_ids=account_ids,
            endpoint_limits=endpoint_limits,
            expires_at=expires_at,
        )
        return {"id": key_id, "api_key": full_key, "key_prefix": key_prefix}

    async def authenticate(
        self,
        provided_api_key: Optional[str],
        *,
        endpoint: str,
        require_assignment: bool = False,
    ) -> AuthContext:
        if not provided_api_key:
            raise PermissionError("Missing API key")

        # Try managed keys first
        row = await self.db.get_client_api_key_by_hash(self._digest(provided_api_key))
        if row:
            if not bool(row.get("is_active", True)):
                raise PermissionError("API key is disabled")

            if row.get("expires_at"):
                # SQLite compares timestamps lexicographically in ISO-ish format.
                now_unix = int(time.time())
                expires_ts = int(row.get("expires_unix") or 0)
                if expires_ts and now_unix >= expires_ts:
                    raise PermissionError("API key expired")

            key_id = int(row["id"])
            allowed_accounts = set(await self.db.get_api_key_account_ids(key_id))
            scopes = {x.strip() for x in (row.get("scopes") or "").split(",") if x.strip()}
            if not scopes:
                scopes = {"*"}

            if require_assignment and not allowed_accounts:
                raise PermissionError("No accounts assigned to this API key")

            await self._enforce_rate_limits(key_id=key_id, endpoint=endpoint)
            await self.db.touch_api_key_usage(key_id)
            return AuthContext(
                key_id=key_id,
                key_label=str(row.get("label") or row.get("key_prefix") or "managed"),
                is_legacy=False,
                allowed_accounts=allowed_accounts,
                scopes=scopes,
            )

        # Legacy fallback
        legacy = (self.legacy_api_key_provider() or "").strip()
        if legacy and hmac.compare_digest(provided_api_key, legacy):
            return AuthContext(
                key_id=None,
                key_label="legacy-global",
                is_legacy=True,
                allowed_accounts=set(),
                scopes={"*"},
            )

        raise PermissionError("Invalid API key")

    async def _enforce_rate_limits(self, key_id: int, endpoint: str):
        limits = await self.db.get_api_key_rate_limits(key_id, endpoint)
        if not limits:
            return

        now = int(time.time())
        minute_window = now // 60
        hour_window = now // 3600

        rpm = int(limits.get("rpm") or 0)
        rph = int(limits.get("rph") or 0)

        async with self._rate_limit_lock:
            if rpm > 0:
                minute_key = (key_id, endpoint, minute_window)
                minute_count = self._window_counters.get(minute_key, 0) + 1
                if minute_count > rpm:
                    raise RuntimeError(f"Rate limit exceeded: {rpm} requests/min for {endpoint}")
                self._window_counters[minute_key] = minute_count

            if rph > 0:
                hour_key = (key_id, endpoint, hour_window)
                hour_count = self._window_counters.get(hour_key, 0) + 1
                if hour_count > rph:
                    raise RuntimeError(f"Rate limit exceeded: {rph} requests/hour for {endpoint}")
                self._window_counters[hour_key] = hour_count
