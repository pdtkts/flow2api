"""
In-memory MVP registry: token_id -> one active WebSocket (last registration wins).
Phase 3 can replace backing store with Redis (dockerised) without changing the HTTP contract.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class PendingSolve:
    job_id: str
    future: asyncio.Future[dict[str, Any]]
    token_id: Optional[int]
    project_id: str
    action: str
    agent_ws: WebSocket


@dataclass
class AgentBinding:
    ws: WebSocket
    auth_method: str
    subject: str
    machine_id: str = ""
    license_id: str = ""
    account_id: str = ""
    claimed_token_ids: tuple[int, ...] = ()
    authorized_token_ids: tuple[int, ...] = ()


class OwnershipStore:
    """
    MVP ownership source of truth:
    - JSON map in env via AGENT_TOKEN_OWNERSHIP_JSON.
      Example: {"machine-1":[1,2], "license-abc":[3]}
    """

    def __init__(self) -> None:
        self._by_subject: dict[str, set[int]] = {}

    def load_json(self, raw: str) -> None:
        self._by_subject.clear()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("invalid AGENT_TOKEN_OWNERSHIP_JSON: %s", e)
            return
        if not isinstance(parsed, dict):
            logger.error("AGENT_TOKEN_OWNERSHIP_JSON must be object map")
            return
        for subject, token_ids in parsed.items():
            if not isinstance(subject, str):
                continue
            if not isinstance(token_ids, list):
                continue
            normalized = {int(v) for v in token_ids if str(v).strip().isdigit()}
            if normalized:
                self._by_subject[subject.strip()] = normalized

    def resolve_authorized_token_ids(
        self,
        *,
        subject: str,
        machine_id: str,
        license_id: str,
        claimed_token_ids: list[int],
    ) -> list[int]:
        # Legacy fallback: no map configured -> trust claimed IDs.
        if not self._by_subject:
            return sorted({int(x) for x in claimed_token_ids})
        allowed: set[int] = set()
        for key in (subject, machine_id, license_id):
            k = (key or "").strip()
            if not k:
                continue
            allowed |= self._by_subject.get(k, set())
        if not claimed_token_ids:
            return sorted(allowed)
        claimed = {int(x) for x in claimed_token_ids}
        return sorted(claimed & allowed)

    def has_any_owner_for_token(self, token_id: int) -> bool:
        if not self._by_subject:
            return True
        for ids in self._by_subject.values():
            if int(token_id) in ids:
                return True
        return False


class AgentRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # int token_id -> binding
        self._by_token: dict[int, AgentBinding] = {}
        # ws id -> set of token_ids
        self._ws_tokens: dict[int, set[int]] = {}
        self._ws_binding: dict[int, AgentBinding] = {}
        self._pending: dict[str, PendingSolve] = {}
        self.ownership = OwnershipStore()

    async def register_agent(
        self,
        ws: WebSocket,
        *,
        auth_method: str,
        subject: str,
        machine_id: str = "",
        license_id: str = "",
        account_id: str = "",
        claimed_token_ids: list[int],
        authorized_token_ids: list[int],
    ) -> None:
        async with self._lock:
            wid = id(ws)
            binding = AgentBinding(
                ws=ws,
                auth_method=auth_method,
                subject=subject,
                machine_id=machine_id,
                license_id=license_id,
                account_id=account_id,
                claimed_token_ids=tuple(sorted({int(x) for x in claimed_token_ids})),
                authorized_token_ids=tuple(sorted({int(x) for x in authorized_token_ids})),
            )
            for tid in binding.authorized_token_ids:
                self._by_token[tid] = binding
            self._ws_tokens[wid] = set(binding.authorized_token_ids)
            self._ws_binding[wid] = binding
            logger.info(
                "agent registered auth=%s subject=%s claimed=%s authorized=%s",
                auth_method,
                subject,
                list(binding.claimed_token_ids),
                list(binding.authorized_token_ids),
            )

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            wid = id(ws)
            tids = self._ws_tokens.pop(wid, set())
            binding = self._ws_binding.pop(wid, None)
            for tid in tids:
                current = self._by_token.get(tid)
                if current and current.ws is ws:
                    del self._by_token[tid]
            for jid, p in list(self._pending.items()):
                if p.agent_ws is ws and not p.future.done():
                    p.future.set_exception(RuntimeError("agent_disconnected"))
                    self._pending.pop(jid, None)
            logger.info(
                "agent unregistered subject=%s token_ids=%s",
                (binding.subject if binding else ""),
                sorted(tids),
            )

    def agent_for_token(self, token_id: Optional[int]) -> Optional[WebSocket]:
        if token_id is None:
            return None
        b = self._by_token.get(int(token_id))
        return b.ws if b else None

    async def dispatch_solve(
        self,
        token_id: Optional[int],
        project_id: str,
        action: str,
        timeout: float,
    ) -> dict[str, Any]:
        if token_id is None:
            raise ValueError("token_id is required for agent routing")
        job_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        async with self._lock:
            binding = self._by_token.get(int(token_id))
            if binding is None:
                raise LookupError("no_agent")
            ws = binding.ws
            p = PendingSolve(
                job_id=job_id,
                future=fut,
                token_id=int(token_id) if token_id is not None else None,
                project_id=project_id,
                action=action,
                agent_ws=ws,
            )
            self._pending[job_id] = p

        msg = {
            "type": "solve_job",
            "job_id": job_id,
            "project_id": project_id,
            "action": action,
            "token_id": int(token_id),
        }
        try:
            await ws.send_json(msg)
        except Exception as e:
            async with self._lock:
                self._pending.pop(job_id, None)
            if not fut.done():
                fut.set_exception(e)
            raise

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            async with self._lock:
                self._pending.pop(job_id, None)
            raise
        except Exception:
            async with self._lock:
                self._pending.pop(job_id, None)
            raise

    async def complete_job(self, job_id: str, result: dict[str, Any]) -> bool:
        async with self._lock:
            p = self._pending.pop(job_id, None)
        if p is None:
            return False
        if not p.future.done():
            p.future.set_result(result)
        return True

    async def fail_job(self, job_id: str, err: str) -> bool:
        async with self._lock:
            p = self._pending.pop(job_id, None)
        if p is None:
            return False
        if not p.future.done():
            p.future.set_exception(RuntimeError(err))
        return True

    def resolve_authorized_token_ids(
        self,
        *,
        subject: str,
        machine_id: str,
        license_id: str,
        claimed_token_ids: list[int],
    ) -> list[int]:
        return self.ownership.resolve_authorized_token_ids(
            subject=subject,
            machine_id=machine_id,
            license_id=license_id,
            claimed_token_ids=claimed_token_ids,
        )

    def has_any_owner_for_token(self, token_id: int) -> bool:
        return self.ownership.has_any_owner_for_token(token_id)


registry = AgentRegistry()
