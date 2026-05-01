import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from fastapi import WebSocket

from ..core.logger import debug_logger


@dataclass
class ExtensionConnection:
    websocket: WebSocket
    worker_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    instance_id: str = ""
    route_key: str = ""
    client_label: str = ""
    managed_api_key_id: Optional[int] = None
    binding_source: str = "none"
    connected_at: float = field(default_factory=time.time)


class ExtensionCaptchaService:
    _instance: Optional["ExtensionCaptchaService"] = None
    _lock = asyncio.Lock()

    def __init__(self, db=None):
        self.db = db
        self.active_connections: list[ExtensionConnection] = []
        self.pending_requests: dict[str, tuple[asyncio.Future, WebSocket]] = {}
        self._state_lock = asyncio.Lock()
        self._connection_changed = asyncio.Condition()
        self._queue_waiters: dict[str, int] = {}

    @classmethod
    async def get_instance(cls, db=None) -> "ExtensionCaptchaService":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db=db)
        elif db is not None and cls._instance.db is None:
            cls._instance.db = db
        return cls._instance

    def _queue_key(self, managed_api_key_id: Optional[int]) -> str:
        return f"key:{managed_api_key_id}" if managed_api_key_id is not None else "legacy"

    async def _notify_connection_change(self) -> None:
        async with self._connection_changed:
            self._connection_changed.notify_all()

    async def _load_persisted_binding(self, route_key: str) -> Tuple[Optional[int], str]:
        normalized = (route_key or "").strip()
        if not normalized or not self.db or not hasattr(self.db, "get_extension_worker_binding_for_route_key"):
            return None, "none"
        try:
            binding = await self.db.get_extension_worker_binding_for_route_key(normalized)
            if binding and binding.get("api_key_id") is not None:
                return int(binding["api_key_id"]), "persisted"
        except Exception as exc:
            debug_logger.log_warning(f"[Extension Captcha] Failed to load binding for route_key={normalized}: {exc}")
        return None, "none"

    async def _resolve_claimed_managed_key(self, raw_value: Any) -> Optional[int]:
        if raw_value in (None, "", "null"):
            return None
        try:
            api_key_id = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError("managed_api_key_id must be an integer")
        if api_key_id <= 0:
            raise ValueError("managed_api_key_id must be positive")
        if not self.db or not hasattr(self.db, "get_api_key_detail"):
            raise ValueError("Managed API key lookup is not available")
        detail = await self.db.get_api_key_detail(api_key_id)
        if not detail:
            raise ValueError(f"Managed API key {api_key_id} does not exist")
        return api_key_id

    async def _apply_route_binding_to_connection(
        self,
        conn: ExtensionConnection,
        *,
        claimed_managed_api_key_id: Any = None,
    ) -> None:
        claimed_key: Optional[int] = None
        claimed = False
        if claimed_managed_api_key_id not in (None, "", "null"):
            claimed = True
            claimed_key = await self._resolve_claimed_managed_key(claimed_managed_api_key_id)
            if conn.route_key and self.db and hasattr(self.db, "upsert_extension_worker_binding"):
                await self.db.upsert_extension_worker_binding(conn.route_key, claimed_key)
            conn.managed_api_key_id = claimed_key
            conn.binding_source = "claimed"
            return

        persisted_key, source = await self._load_persisted_binding(conn.route_key)
        conn.managed_api_key_id = persisted_key
        conn.binding_source = source if source != "none" else ("claimed" if claimed else "none")

    async def connect(
        self,
        websocket: WebSocket,
        *,
        authenticated_managed_api_key_id: Optional[int] = None,
    ):
        await websocket.accept()
        conn = ExtensionConnection(
            websocket=websocket,
            instance_id=(websocket.query_params.get("instance_id") or "").strip(),
            route_key=(websocket.query_params.get("route_key") or "").strip(),
            client_label=(websocket.query_params.get("client_label") or "").strip(),
        )
        if conn.instance_id:
            for existing in list(self.active_connections):
                if existing.instance_id and existing.instance_id == conn.instance_id:
                    try:
                        await existing.websocket.close(code=1000, reason="Replaced by reconnect")
                    except Exception:
                        pass
                    self.disconnect(existing.websocket)
        if authenticated_managed_api_key_id is not None:
            conn.managed_api_key_id = int(authenticated_managed_api_key_id)
            conn.binding_source = "authenticated"
            if conn.route_key and self.db and hasattr(self.db, "upsert_extension_worker_binding"):
                await self.db.upsert_extension_worker_binding(conn.route_key, conn.managed_api_key_id)
        else:
            claimed_managed_key = websocket.query_params.get("managed_api_key_id")
            try:
                await self._apply_route_binding_to_connection(
                    conn,
                    claimed_managed_api_key_id=claimed_managed_key,
                )
            except Exception as exc:
                debug_logger.log_warning(f"[Extension Captcha] Ignoring invalid managed key claim on connect: {exc}")
        self.active_connections.append(conn)
        debug_logger.log_info(
            f"[Extension Captcha] Client connected. Total: {len(self.active_connections)}, "
            f"worker_session_id={conn.worker_session_id}, "
            f"instance_id={conn.instance_id or '-'}, "
            f"route_key={conn.route_key or '-'}, label={conn.client_label or '-'}, "
            f"managed_api_key_id={conn.managed_api_key_id}, source={conn.binding_source}"
        )
        await self._notify_connection_change()

    def disconnect(self, websocket: WebSocket):
        for conn in list(self.active_connections):
            if conn.websocket is websocket:
                self.active_connections.remove(conn)
                debug_logger.log_info(
                    f"[Extension Captcha] Client disconnected. Total: {len(self.active_connections)}, "
                    f"worker_session_id={conn.worker_session_id}, "
                    f"route_key={conn.route_key or '-'}, label={conn.client_label or '-'}"
                )
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._notify_connection_change())
                except Exception:
                    pass
                return

    def _find_connection(self, websocket: WebSocket) -> Optional[ExtensionConnection]:
        for conn in self.active_connections:
            if conn.websocket is websocket:
                return conn
        return None

    def _select_connection(
        self,
        route_key: str,
        managed_api_key_id: Optional[int],
    ) -> Optional[ExtensionConnection]:
        normalized_key = (route_key or "").strip()
        candidate_connections = self.active_connections
        if managed_api_key_id is not None:
            candidate_connections = [
                conn for conn in candidate_connections if conn.managed_api_key_id == managed_api_key_id
            ]
            if not candidate_connections:
                return None
            # Key-first routing: if managed key is known, route_key is only a preference.
            # Prefer exact route_key match when provided, otherwise use any connection
            # under this managed key.
            if normalized_key:
                for conn in candidate_connections:
                    if conn.route_key == normalized_key:
                        return conn
            return candidate_connections[0]
        else:
            # Legacy/global callers must never borrow managed-key scoped workers.
            candidate_connections = [
                conn for conn in candidate_connections if conn.managed_api_key_id is None
            ]

        if normalized_key:
            for conn in candidate_connections:
                if conn.route_key == normalized_key:
                    return conn
            return None
        # Empty token routes are only allowed to use an empty extension route.
        # A keyed route such as "9223" belongs to a specific browser/account
        # and must never be borrowed by another token just because it is the
        # only extension online.
        for conn in candidate_connections:
            if not conn.route_key:
                return conn
        return None

    def _describe_routes(self) -> str:
        labels = []
        for conn in self.active_connections:
            label = conn.route_key or "(empty)"
            if conn.client_label:
                label = f"{label}:{conn.client_label}"
            if conn.managed_api_key_id is not None:
                label = f"{label}@key{conn.managed_api_key_id}"
            if conn.binding_source:
                label = f"{label}#{conn.binding_source}"
            labels.append(label)
        return ", ".join(labels)

    def _describe_workers_verbose(self) -> str:
        if not self.active_connections:
            return "none"
        parts = []
        for conn in self.active_connections:
            route = conn.route_key or "(empty)"
            label = conn.client_label or "-"
            managed = (
                str(conn.managed_api_key_id)
                if conn.managed_api_key_id is not None
                else "unbound"
            )
            source = conn.binding_source or "none"
            parts.append(
                f"route={route}, label={label}, managed_key={managed}, binding={source}"
            )
        return " | ".join(parts)

    def describe_routes(self) -> str:
        return self._describe_routes()

    async def _send_ack(self, websocket: WebSocket, payload: Dict[str, Any]):
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    async def _resolve_route_key(self, token_id: Optional[int]) -> str:
        if not token_id or not self.db:
            return ""
        try:
            token = await self.db.get_token(token_id)
            if token and token.extension_route_key:
                return token.extension_route_key.strip()
        except Exception as e:
            debug_logger.log_warning(f"[Extension Captcha] Failed to resolve route key for token {token_id}: {e}")
        return ""

    def _has_connection_for_route_key(self, route_key: str, managed_api_key_id: Optional[int]) -> bool:
        return self._select_connection(route_key, managed_api_key_id) is not None

    async def has_connection_for_managed_key(self, managed_api_key_id: Optional[int]) -> bool:
        if managed_api_key_id is None:
            return False
        return any(conn.managed_api_key_id == int(managed_api_key_id) for conn in self.active_connections)

    async def has_any_authenticated_connection_for_key(self, managed_api_key_id: Optional[int]) -> bool:
        if managed_api_key_id is None:
            return False
        return any(
            conn.managed_api_key_id == int(managed_api_key_id) and conn.binding_source in {"authenticated", "manual", "claimed"}
            for conn in self.active_connections
        )

    async def has_connection_for_token(
        self,
        token_id: Optional[int],
        managed_api_key_id: Optional[int] = None,
    ) -> tuple[bool, str]:
        route_key = await self._resolve_route_key(token_id)
        if managed_api_key_id is not None:
            has_connection = await self.has_connection_for_managed_key(managed_api_key_id)
            return has_connection, route_key
        return self._has_connection_for_route_key(route_key, managed_api_key_id), route_key

    async def _wait_for_connection(
        self,
        *,
        route_key: str,
        managed_api_key_id: Optional[int],
        timeout: float,
    ) -> Optional[ExtensionConnection]:
        deadline = time.time() + max(0.0, float(timeout))
        queue_key = self._queue_key(managed_api_key_id)
        async with self._state_lock:
            self._queue_waiters[queue_key] = self._queue_waiters.get(queue_key, 0) + 1
        try:
            while True:
                conn = self._select_connection(route_key, managed_api_key_id)
                if conn is not None:
                    return conn
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                async with self._connection_changed:
                    try:
                        await asyncio.wait_for(self._connection_changed.wait(), timeout=min(remaining, 1.5))
                    except asyncio.TimeoutError:
                        pass
        finally:
            async with self._state_lock:
                current = self._queue_waiters.get(queue_key, 0)
                if current <= 1:
                    self._queue_waiters.pop(queue_key, None)
                else:
                    self._queue_waiters[queue_key] = current - 1

    async def handle_message(self, websocket: WebSocket, data: str):
        try:
            payload = json.loads(data)
            message_type = payload.get("type")

            if message_type == "register":
                conn = self._find_connection(websocket)
                if conn:
                    conn.route_key = (payload.get("route_key") or conn.route_key or "").strip()
                    conn.client_label = (payload.get("client_label") or conn.client_label or "").strip()
                    conn.instance_id = (payload.get("instance_id") or conn.instance_id or "").strip()
                    register_error = None
                    if conn.binding_source == "authenticated" and conn.managed_api_key_id is not None:
                        try:
                            if conn.route_key and self.db and hasattr(self.db, "upsert_extension_worker_binding"):
                                await self.db.upsert_extension_worker_binding(conn.route_key, conn.managed_api_key_id)
                        except Exception as exc:
                            register_error = str(exc)
                            debug_logger.log_warning(
                                f"[Extension Captcha] Failed to persist authenticated route binding: {register_error}"
                            )
                    else:
                        try:
                            await self._apply_route_binding_to_connection(
                                conn,
                                claimed_managed_api_key_id=payload.get("managed_api_key_id"),
                            )
                        except Exception as exc:
                            register_error = str(exc)
                            debug_logger.log_warning(f"[Extension Captcha] Invalid managed key claim: {register_error}")
                    debug_logger.log_info(
                        f"[Extension Captcha] Client registered route_key={conn.route_key or '-'}, "
                        f"label={conn.client_label or '-'}, "
                        f"managed_api_key_id={conn.managed_api_key_id}, source={conn.binding_source}"
                    )
                    await self._send_ack(
                        websocket,
                        {
                            "type": "register_ack",
                            "worker_session_id": conn.worker_session_id,
                            "route_key": conn.route_key,
                            "client_label": conn.client_label,
                            "instance_id": conn.instance_id,
                            "managed_api_key_id": conn.managed_api_key_id,
                            "binding_source": conn.binding_source,
                            "status": "error" if register_error else "ok",
                            "error": register_error,
                        },
                    )
                    await self._notify_connection_change()
                return

            req_id = payload.get("req_id")
            if req_id and req_id in self.pending_requests:
                future, owner_websocket = self.pending_requests[req_id]
                if websocket is not owner_websocket:
                    debug_logger.log_warning(
                        f"[Extension Captcha] Ignoring response from non-owner connection: {req_id}"
                    )
                    return
                if not future.done():
                    future.set_result(payload)
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Error handling message: {e}")

    async def get_token(
        self,
        project_id: str,
        action: str = "IMAGE_GENERATION",
        timeout: int = 20,
        token_id: Optional[int] = None,
        managed_api_key_id: Optional[int] = None,
    ) -> Optional[str]:
        route_key = ""
        if managed_api_key_id is None:
            route_key = await self._resolve_route_key(token_id)
        queue_wait_timeout = 20
        if self.db and hasattr(self.db, "get_captcha_config"):
            try:
                captcha_config = await self.db.get_captcha_config()
                queue_wait_timeout = int(getattr(captcha_config, "extension_queue_wait_timeout_seconds", 20) or 20)
            except Exception as exc:
                debug_logger.log_warning(f"[Extension Captcha] Failed to load queue timeout: {exc}")
        queue_wait_timeout = max(1, min(120, queue_wait_timeout))
        conn = await self._wait_for_connection(
            route_key=route_key,
            managed_api_key_id=managed_api_key_id,
            timeout=queue_wait_timeout,
        )
        if conn is None:
            available = self._describe_routes() or "none"
            workers_verbose = self._describe_workers_verbose()
            qkey = self._queue_key(managed_api_key_id)
            waiting_count = self._queue_waiters.get(qkey, 0)
            raise RuntimeError(
                f"No Chrome Extension connection matched this request after waiting {queue_wait_timeout}s: "
                f"managed_api_key_id={managed_api_key_id}, token_id={token_id}, route_key='{route_key}', "
                f"queue={qkey}, queue_waiters={waiting_count}. "
                f"Available route keys: {available}. Active workers: {workers_verbose}"
            )

        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = (future, conn.websocket)

        request_data = {
            "type": "get_token",
            "req_id": req_id,
            "action": action,
            "project_id": project_id,
            "route_key": route_key,
            "managed_api_key_id": managed_api_key_id,
        }

        try:
            debug_logger.log_info(
                f"[Extension Captcha] Dispatching token request via route_key={route_key or '-'}, "
                f"label={conn.client_label or '-'}, project_id={project_id}, action={action}, "
                f"managed_api_key_id={managed_api_key_id}"
            )
            await conn.websocket.send_text(json.dumps(request_data))
            result = await asyncio.wait_for(future, timeout=timeout)

            if result.get("status") == "success":
                return result.get("token")

            error_msg = result.get("error")
            debug_logger.log_error(f"[Extension Captcha] Error from extension: {error_msg}")
            return None

        except asyncio.TimeoutError:
            debug_logger.log_error(f"[Extension Captcha] Timeout waiting for token (req_id: {req_id})")
            return None
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Communication error: {e}")
            return None
        finally:
            self.pending_requests.pop(req_id, None)

    async def report_flow_error(self, project_id: str, error_reason: str, error_message: str = ""):
        _ = project_id, error_message
        debug_logger.log_warning(f"[Extension Captcha] Flow error reported (ignoring): {error_reason}")

    async def list_active_workers(self) -> list[Dict[str, Any]]:
        workers: list[Dict[str, Any]] = []
        for conn in self.active_connections:
            workers.append(
                {
                    "worker_session_id": conn.worker_session_id,
                    "instance_id": conn.instance_id,
                    "route_key": conn.route_key,
                    "client_label": conn.client_label,
                    "managed_api_key_id": conn.managed_api_key_id,
                    "binding_source": conn.binding_source,
                    "connected_at": conn.connected_at,
                }
            )
        return workers

    async def kill_worker(self, worker_session_id: str) -> bool:
        target_id = (worker_session_id or "").strip()
        if not target_id:
            return False
        target: Optional[ExtensionConnection] = None
        for conn in self.active_connections:
            if conn.worker_session_id == target_id:
                target = conn
                break
        if target is None:
            return False
        try:
            await target.websocket.close(code=1000, reason="Worker terminated by admin")
        except Exception:
            pass
        self.disconnect(target.websocket)
        return True

    async def bind_route_key(self, route_key: str, managed_api_key_id: int) -> None:
        normalized_route = (route_key or "").strip()
        if not normalized_route:
            raise ValueError("route_key is required")
        if not self.db or not hasattr(self.db, "upsert_extension_worker_binding"):
            raise ValueError("Binding persistence is unavailable")
        managed_api_key_id = await self._resolve_claimed_managed_key(managed_api_key_id)
        await self.db.upsert_extension_worker_binding(normalized_route, managed_api_key_id)
        for conn in self.active_connections:
            if conn.route_key == normalized_route:
                conn.managed_api_key_id = managed_api_key_id
                conn.binding_source = "manual"
        await self._notify_connection_change()

    async def unbind_route_key(self, route_key: str) -> None:
        normalized_route = (route_key or "").strip()
        if not normalized_route:
            raise ValueError("route_key is required")
        if not self.db or not hasattr(self.db, "delete_extension_worker_binding"):
            raise ValueError("Binding persistence is unavailable")
        await self.db.delete_extension_worker_binding(normalized_route)
        for conn in self.active_connections:
            if conn.route_key == normalized_route:
                conn.managed_api_key_id = None
                conn.binding_source = "none"
        await self._notify_connection_change()

    def get_queue_stats(self) -> Dict[str, int]:
        return dict(self._queue_waiters)
