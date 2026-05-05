import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket

from ..core.logger import debug_logger

# Dedicated-worker hybrid routing (health + score + RR tie-break)
_DEDICATED_EMA_ALPHA = 0.25
_DEDICATED_TIE_DELTA = 5.0
_DEDICATED_FAILURE_WINDOW_SEC = 30.0
_DEDICATED_COOLDOWN_SEC = 20.0
_DEDICATED_FAILS_FOR_COOLDOWN = 2
_DEDICATED_SCORE_WEIGHT_SUCCESS = 100.0
_DEDICATED_SCORE_WEIGHT_INFLIGHT = 15.0
_DEDICATED_SCORE_WEIGHT_EMA_DIVISOR = 50.0
_DEDICATED_SCORE_WEIGHT_TIMEOUT = 20.0
_DEDICATED_TIMEOUT_WINDOW_SEC = 60.0


@dataclass
class DedicatedWorkerStats:
    """In-memory health/latency signals per extension worker_session_id (dedicated workers)."""

    inflight_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    ema_latency_ms: float = 0.0
    has_latency_sample: bool = False
    fail_timestamps: List[float] = field(default_factory=list)
    timeout_timestamps: List[float] = field(default_factory=list)
    cooldown_until: float = 0.0


@dataclass
class ExtensionConnection:
    websocket: WebSocket
    worker_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    instance_id: str = ""
    route_key: str = ""
    client_label: str = ""
    managed_api_key_id: Optional[int] = None
    binding_source: str = "none"
    dedicated_worker_id: Optional[int] = None
    dedicated_token_id: Optional[int] = None
    connected_at: float = field(default_factory=time.time)
    # Serialize send+wait on this WebSocket (FIFO waiters); matches extension tokenQueue.
    dispatch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ExtensionCaptchaService:
    _instance: Optional["ExtensionCaptchaService"] = None
    _lock = asyncio.Lock()

    def __init__(self, db=None):
        self.db = db
        self.active_connections: list[ExtensionConnection] = []
        self.pending_requests: dict[str, tuple[asyncio.Future, WebSocket]] = {}
        # generation_req_id -> websocket owner (submit_generation / poll_generation)
        self.pending_generation_requests: dict[str, tuple[asyncio.Future, WebSocket]] = {}
        # req_id -> websocket to notify after Flow upstream accepts/rejects the token
        self._upstream_verdict_targets: dict[str, WebSocket] = {}
        self._state_lock = asyncio.Lock()
        self._connection_changed = asyncio.Condition()
        self._queue_waiters: dict[str, int] = {}
        # Round-robin cursor per managed API key (see _queue_key). Lock-free counter:
        # concurrent picks may occasionally duplicate; modulo on read keeps indices valid.
        self._rr_cursor: dict[str, int] = {}
        # Hybrid dedicated-worker routing: stats keyed by worker_session_id
        self._dedicated_worker_stats: dict[str, DedicatedWorkerStats] = {}
        # RR cursor among top-scoring dedicated workers per token_id (string key dedicated:{id})
        self._dedicated_hybrid_rr: dict[str, int] = {}
        self._dedicated_stats_lock = asyncio.Lock()

    def _dedicated_stats(self, worker_session_id: str) -> DedicatedWorkerStats:
        sid = (worker_session_id or "").strip()
        if not sid:
            sid = "_"
        if sid not in self._dedicated_worker_stats:
            self._dedicated_worker_stats[sid] = DedicatedWorkerStats()
        return self._dedicated_worker_stats[sid]

    def _prune_timestamps(self, stamps: List[float], now: float, window: float) -> None:
        cutoff = now - window
        stamps[:] = [t for t in stamps if t >= cutoff]

    def _dedicated_worker_score(self, stats: DedicatedWorkerStats, now: float) -> float:
        self._prune_timestamps(stats.fail_timestamps, now, _DEDICATED_FAILURE_WINDOW_SEC)
        self._prune_timestamps(stats.timeout_timestamps, now, _DEDICATED_TIMEOUT_WINDOW_SEC)
        total = stats.success_count + stats.fail_count
        success_rate = (stats.success_count / total) if total > 0 else 1.0
        ema = stats.ema_latency_ms if stats.has_latency_sample else 0.0
        timeouts_recent = len(stats.timeout_timestamps)
        score = (
            success_rate * _DEDICATED_SCORE_WEIGHT_SUCCESS
            - stats.inflight_count * _DEDICATED_SCORE_WEIGHT_INFLIGHT
            - (ema / _DEDICATED_SCORE_WEIGHT_EMA_DIVISOR)
            - timeouts_recent * _DEDICATED_SCORE_WEIGHT_TIMEOUT
        )
        return float(score)

    def _pick_dedicated_connection_hybrid(
        self,
        pool: List[ExtensionConnection],
        preferred_token_id: int,
        *,
        exclude_worker_session_ids: Optional[Set[str]] = None,
        selection_meta_out: Optional[Dict[str, Any]] = None,
    ) -> Optional[ExtensionConnection]:
        tid = int(preferred_token_id)
        exclude = exclude_worker_session_ids or set()
        candidates = [
            c
            for c in pool
            if c.dedicated_token_id is not None and int(c.dedicated_token_id) == tid and c.worker_session_id not in exclude
        ]
        if not candidates:
            return None
        now = time.time()
        healthy: list[ExtensionConnection] = []
        for c in candidates:
            st = self._dedicated_stats(c.worker_session_id)
            if st.cooldown_until <= now:
                healthy.append(c)
        pick_from = healthy if healthy else list(candidates)

        scored: list[tuple[float, ExtensionConnection]] = []
        for c in pick_from:
            st = self._dedicated_stats(c.worker_session_id)
            scored.append((self._dedicated_worker_score(st, now), c))
        best_score = max(s[0] for s in scored)
        tied = [c for s, c in scored if abs(s - best_score) <= _DEDICATED_TIE_DELTA]
        tied_sorted = sorted(tied, key=lambda c: c.worker_session_id)
        rr_key = f"dedicated:{tid}"
        n = len(tied_sorted)
        idx = self._dedicated_hybrid_rr.get(rr_key, 0) % n
        chosen = tied_sorted[idx]
        self._dedicated_hybrid_rr[rr_key] = (idx + 1) % n

        if selection_meta_out is not None:
            selection_meta_out.clear()
            selection_meta_out["dedicated_hybrid"] = True
            selection_meta_out["dedicated_token_id"] = tid
            selection_meta_out["dedicated_score"] = round(best_score, 2)
            selection_meta_out["dedicated_rr_idx"] = idx
            selection_meta_out["dedicated_pool_size"] = len(candidates)
            selection_meta_out["dedicated_pick_from"] = len(pick_from)

        debug_logger.log_info(
            "[Extension Captcha] Dedicated hybrid pick: "
            f"token_id={tid}, worker_session_id={chosen.worker_session_id}, score={best_score:.2f}, "
            f"rr_idx={idx}/{n}, candidates={len(candidates)}, healthy={len(healthy)}"
        )
        return chosen

    def _dedicated_record_failure_locked(self, stats: DedicatedWorkerStats, now: float, *, is_timeout: bool) -> None:
        """Caller must hold ``_dedicated_stats_lock``."""
        if is_timeout:
            stats.timeout_timestamps.append(now)
            self._prune_timestamps(stats.timeout_timestamps, now, _DEDICATED_TIMEOUT_WINDOW_SEC)
            stats.cooldown_until = max(stats.cooldown_until, now + _DEDICATED_COOLDOWN_SEC)
            return
        stats.fail_count += 1
        stats.fail_timestamps.append(now)
        self._prune_timestamps(stats.fail_timestamps, now, _DEDICATED_FAILURE_WINDOW_SEC)
        if len(stats.fail_timestamps) >= _DEDICATED_FAILS_FOR_COOLDOWN:
            stats.cooldown_until = max(stats.cooldown_until, now + _DEDICATED_COOLDOWN_SEC)

    def _dedicated_record_success_locked(self, stats: DedicatedWorkerStats, latency_ms: float) -> None:
        """Caller must hold ``_dedicated_stats_lock``."""
        stats.success_count += 1
        if stats.has_latency_sample:
            stats.ema_latency_ms = (
                _DEDICATED_EMA_ALPHA * latency_ms + (1.0 - _DEDICATED_EMA_ALPHA) * stats.ema_latency_ms
            )
        else:
            stats.ema_latency_ms = latency_ms
            stats.has_latency_sample = True

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
        authenticated_worker: Optional[Dict[str, Any]] = None,
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
        if authenticated_worker:
            conn.dedicated_worker_id = int(authenticated_worker.get("id"))
            token_id = authenticated_worker.get("token_id")
            conn.dedicated_token_id = int(token_id) if token_id is not None else None
            try:
                if self.db and hasattr(self.db, "update_dedicated_extension_worker"):
                    await self.db.update_dedicated_extension_worker(
                        conn.dedicated_worker_id,
                        route_key=(conn.route_key or authenticated_worker.get("route_key") or None),
                        last_instance_id=conn.instance_id or None,
                        mark_seen=True,
                        last_error="",
                    )
            except Exception as exc:
                debug_logger.log_warning(f"[Extension Captcha] Failed to persist dedicated worker route: {exc}")
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
                stale_reqs = [rid for rid, ws in list(self._upstream_verdict_targets.items()) if ws is websocket]
                for rid in stale_reqs:
                    self._upstream_verdict_targets.pop(rid, None)
                stale_gen_reqs = [
                    rid for rid, (_fut, ws) in list(self.pending_generation_requests.items()) if ws is websocket
                ]
                for rid in stale_gen_reqs:
                    future, _ = self.pending_generation_requests.pop(rid, (None, None))
                    if future is not None and not future.done():
                        try:
                            future.set_exception(RuntimeError("Extension worker disconnected"))
                        except Exception:
                            pass
                debug_logger.log_info(
                    f"[Extension Captcha] Client disconnected. Total: {len(self.active_connections)}, "
                    f"worker_session_id={conn.worker_session_id}, "
                    f"route_key={conn.route_key or '-'}, label={conn.client_label or '-'}"
                )
                mid = conn.managed_api_key_id
                if mid is not None:
                    qk = self._queue_key(int(mid))
                    if not any(
                        c.managed_api_key_id is not None and int(c.managed_api_key_id) == int(mid)
                        for c in self.active_connections
                    ):
                        self._rr_cursor.pop(qk, None)
                self._dedicated_worker_stats.pop(conn.worker_session_id, None)
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

    def _connection_pool(
        self, *, exclude_dedicated_token_id: Optional[int] = None
    ) -> list[ExtensionConnection]:
        """Active connections, optionally excluding dedicated worker(s) bound to a token."""
        if exclude_dedicated_token_id is None:
            return list(self.active_connections)
        tid = int(exclude_dedicated_token_id)
        out: list[ExtensionConnection] = []
        for conn in self.active_connections:
            did = conn.dedicated_token_id
            if did is None:
                out.append(conn)
                continue
            if int(did) != tid:
                out.append(conn)
        return out

    def _finalize_managed_rr_cursor_after_pick(
        self,
        conn: ExtensionConnection,
        *,
        route_key: str,
        managed_api_key_id: Optional[int],
        preferred_token_id: Optional[int],
        exclude_dedicated_token_id: Optional[int],
    ) -> None:
        """Advance RR cursor after dispatch picks a connection (not while polling in wait loop)."""
        if managed_api_key_id is None:
            return
        if preferred_token_id is not None and conn.dedicated_token_id is not None:
            if int(conn.dedicated_token_id) == int(preferred_token_id):
                return
        pool = self._connection_pool(exclude_dedicated_token_id=exclude_dedicated_token_id)
        normalized_key = (route_key or "").strip()
        candidate_connections = [
            c for c in pool if c.managed_api_key_id == managed_api_key_id
        ]
        if not candidate_connections:
            return
        sorted_candidates = sorted(candidate_connections, key=lambda c: c.worker_session_id)
        if normalized_key:
            for c in sorted_candidates:
                if c.route_key == normalized_key:
                    if c.websocket is conn.websocket:
                        return
                    break
        try:
            idx = sorted_candidates.index(conn)
        except ValueError:
            return
        queue_key = self._queue_key(managed_api_key_id)
        n = len(sorted_candidates)
        self._rr_cursor[queue_key] = (idx + 1) % n

    def _select_connection(
        self,
        route_key: str,
        managed_api_key_id: Optional[int],
        preferred_token_id: Optional[int] = None,
        *,
        exclude_dedicated_token_id: Optional[int] = None,
        exclude_worker_session_ids: Optional[Set[str]] = None,
        use_dedicated_hybrid: bool = True,
        selection_meta_out: Optional[Dict[str, Any]] = None,
    ) -> Optional[ExtensionConnection]:
        pool = self._connection_pool(exclude_dedicated_token_id=exclude_dedicated_token_id)
        if preferred_token_id is not None:
            if use_dedicated_hybrid:
                picked = self._pick_dedicated_connection_hybrid(
                    pool,
                    int(preferred_token_id),
                    exclude_worker_session_ids=exclude_worker_session_ids,
                    selection_meta_out=selection_meta_out,
                )
                if picked is not None:
                    return picked
            else:
                for conn in pool:
                    if exclude_worker_session_ids and conn.worker_session_id in exclude_worker_session_ids:
                        continue
                    if conn.dedicated_token_id is not None and conn.dedicated_token_id == int(preferred_token_id):
                        return conn
        normalized_key = (route_key or "").strip()
        candidate_connections = pool
        if managed_api_key_id is not None:
            candidate_connections = [
                conn for conn in candidate_connections if conn.managed_api_key_id == managed_api_key_id
            ]
            if not candidate_connections:
                return None
            sorted_candidates = sorted(candidate_connections, key=lambda c: c.worker_session_id)
            # Key-first routing: if managed key is known, route_key is only a preference.
            # Prefer exact route_key match when provided (no RR advance); otherwise round-robin.
            if normalized_key:
                for conn in sorted_candidates:
                    if conn.route_key == normalized_key:
                        return conn
            queue_key = self._queue_key(managed_api_key_id)
            n = len(sorted_candidates)
            idx = self._rr_cursor.get(queue_key, 0) % n
            chosen = sorted_candidates[idx]
            if selection_meta_out is not None:
                selection_meta_out.clear()
                selection_meta_out["pool_size"] = n
                selection_meta_out["rr_idx"] = idx
            return chosen
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

    async def has_connection_for_dedicated_token(self, token_id: Optional[int]) -> bool:
        if token_id is None:
            return False
        target_token_id = int(token_id)
        return any(conn.dedicated_token_id == target_token_id for conn in self.active_connections)

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
            if not has_connection:
                # Dedicated worker mode can route by dedicated token binding even without managed key.
                has_connection = await self.has_connection_for_dedicated_token(token_id)
            return has_connection, route_key
        return self._has_connection_for_route_key(route_key, managed_api_key_id), route_key

    async def _wait_for_connection(
        self,
        *,
        route_key: str,
        managed_api_key_id: Optional[int],
        preferred_token_id: Optional[int] = None,
        timeout: float,
        exclude_dedicated_token_id: Optional[int] = None,
        exclude_worker_session_ids: Optional[Set[str]] = None,
        use_dedicated_hybrid: bool = True,
        selection_meta_out: Optional[Dict[str, Any]] = None,
    ) -> Optional[ExtensionConnection]:
        deadline = time.time() + max(0.0, float(timeout))
        queue_key = self._queue_key(managed_api_key_id)
        async with self._state_lock:
            self._queue_waiters[queue_key] = self._queue_waiters.get(queue_key, 0) + 1
        try:
            while True:
                conn = self._select_connection(
                    route_key,
                    managed_api_key_id,
                    preferred_token_id=preferred_token_id,
                    exclude_dedicated_token_id=exclude_dedicated_token_id,
                    exclude_worker_session_ids=exclude_worker_session_ids,
                    use_dedicated_hybrid=use_dedicated_hybrid,
                    selection_meta_out=selection_meta_out,
                )
                if conn is not None:
                    self._finalize_managed_rr_cursor_after_pick(
                        conn,
                        route_key=route_key,
                        managed_api_key_id=managed_api_key_id,
                        preferred_token_id=preferred_token_id,
                        exclude_dedicated_token_id=exclude_dedicated_token_id,
                    )
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
                    if conn.dedicated_worker_id and self.db and hasattr(self.db, "update_dedicated_extension_worker"):
                        try:
                            await self.db.update_dedicated_extension_worker(
                                conn.dedicated_worker_id,
                                route_key=conn.route_key or None,
                                last_instance_id=conn.instance_id or None,
                                mark_seen=True,
                                last_error="",
                            )
                        except Exception as exc:
                            debug_logger.log_warning(
                                f"[Extension Captcha] Failed to update dedicated worker heartbeat: {exc}"
                            )
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
                            "dedicated_worker_id": conn.dedicated_worker_id,
                            "dedicated_token_id": conn.dedicated_token_id,
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
                        f"[Extension Captcha] Ignoring captcha response from non-owner connection: {req_id}"
                    )
                    return
                if not future.done():
                    future.set_result(payload)
                return
            if req_id and req_id in self.pending_generation_requests:
                future, owner_websocket = self.pending_generation_requests[req_id]
                if websocket is not owner_websocket:
                    debug_logger.log_warning(
                        f"[Extension Captcha] Ignoring generation response from non-owner connection: {req_id}"
                    )
                    return
                if not future.done():
                    future.set_result(payload)
                return
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Error handling message: {e}")

    async def _generation_request_once(
        self,
        conn: ExtensionConnection,
        *,
        message_type: str,
        request_payload: Dict[str, Any],
        timeout: int,
    ) -> Dict[str, Any]:
        req_id = f"gen_req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_generation_requests[req_id] = (future, conn.websocket)
        message = {"type": message_type, "req_id": req_id, **request_payload}
        try:
            await conn.websocket.send_text(json.dumps(message))
            result = await asyncio.wait_for(future, timeout=max(5, int(timeout or 30)))
            if not isinstance(result, dict):
                raise RuntimeError("Invalid extension generation response format")
            if result.get("status") == "success":
                return result
            error_msg = str(result.get("error") or "Extension generation request failed")
            raise RuntimeError(error_msg)
        finally:
            self.pending_generation_requests.pop(req_id, None)

    async def submit_generation_via_extension(
        self,
        *,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: int = 60,
        managed_api_key_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        conn = await self._wait_for_connection(
            route_key="",
            managed_api_key_id=managed_api_key_id,
            preferred_token_id=None,
            timeout=min(max(1, int(timeout or 60)), 120),
            exclude_dedicated_token_id=None,
            selection_meta_out=None,
        )
        if conn is None:
            raise RuntimeError("No extension worker available for generation submit")
        payload = {
            "url": str(url or "").strip(),
            "method": str(method or "POST").strip().upper(),
            "headers": dict(headers or {}),
            "json_data": json_data if isinstance(json_data, dict) else {},
        }
        async with conn.dispatch_lock:
            return await self._generation_request_once(
                conn,
                message_type="submit_generation",
                request_payload=payload,
                timeout=timeout,
            )

    async def poll_generation_via_extension(
        self,
        *,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: int = 45,
        managed_api_key_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        conn = await self._wait_for_connection(
            route_key="",
            managed_api_key_id=managed_api_key_id,
            preferred_token_id=None,
            timeout=min(max(1, int(timeout or 45)), 120),
            exclude_dedicated_token_id=None,
            selection_meta_out=None,
        )
        if conn is None:
            raise RuntimeError("No extension worker available for generation polling fallback")
        payload = {
            "url": str(url or "").strip(),
            "method": str(method or "POST").strip().upper(),
            "headers": dict(headers or {}),
            "json_data": json_data if isinstance(json_data, dict) else {},
        }
        async with conn.dispatch_lock:
            return await self._generation_request_once(
                conn,
                message_type="poll_generation",
                request_payload=payload,
                timeout=timeout,
            )

    async def _extension_recaptcha_token_once(
        self,
        conn: ExtensionConnection,
        *,
        project_id: str,
        action: str,
        route_key: str,
        managed_api_key_id: Optional[int],
        timeout: int,
        selection_meta: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        track_dedicated = conn.dedicated_token_id is not None
        t0 = time.time()
        if track_dedicated:
            async with self._dedicated_stats_lock:
                self._dedicated_stats(conn.worker_session_id).inflight_count += 1
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
            dispatch_parts = [
                f"route_key={route_key or '-'}",
                f"label={conn.client_label or '-'}",
                f"worker_session_id={conn.worker_session_id}",
                f"project_id={project_id}",
                f"action={action}",
                f"managed_api_key_id={managed_api_key_id}",
            ]
            if selection_meta:
                if "pool_size" in selection_meta:
                    dispatch_parts.append(f"pool_size={selection_meta['pool_size']}")
                if "rr_idx" in selection_meta:
                    dispatch_parts.append(f"rr_idx={selection_meta['rr_idx']}")
                if selection_meta.get("dedicated_hybrid"):
                    dispatch_parts.append(f"dedicated_score={selection_meta.get('dedicated_score', '-')}")
                    dispatch_parts.append(f"dedicated_rr_idx={selection_meta.get('dedicated_rr_idx', '-')}")
            debug_logger.log_info(
                "[Extension Captcha] Dispatching token request via "
                + ", ".join(dispatch_parts)
            )
            await conn.websocket.send_text(json.dumps(request_data))
            result = await asyncio.wait_for(future, timeout=timeout)
            latency_ms = (time.time() - t0) * 1000.0
            if result.get("status") == "success":
                tok = result.get("token")
                if isinstance(tok, str) and tok.strip():
                    if track_dedicated:
                        async with self._dedicated_stats_lock:
                            self._dedicated_record_success_locked(
                                self._dedicated_stats(conn.worker_session_id), latency_ms
                            )
                    async with self._state_lock:
                        self._upstream_verdict_targets[req_id] = conn.websocket
                    return tok.strip(), req_id
                if track_dedicated:
                    async with self._dedicated_stats_lock:
                        self._dedicated_record_failure_locked(
                            self._dedicated_stats(conn.worker_session_id), time.time(), is_timeout=False
                        )
                return None, None
            error_msg = result.get("error")
            debug_logger.log_error(f"[Extension Captcha] Error from extension: {error_msg}")
            if track_dedicated:
                async with self._dedicated_stats_lock:
                    self._dedicated_record_failure_locked(
                        self._dedicated_stats(conn.worker_session_id), time.time(), is_timeout=False
                    )
            return None, None
        except asyncio.TimeoutError:
            debug_logger.log_error(f"[Extension Captcha] Timeout waiting for token (req_id: {req_id})")
            if track_dedicated:
                async with self._dedicated_stats_lock:
                    self._dedicated_record_failure_locked(
                        self._dedicated_stats(conn.worker_session_id), time.time(), is_timeout=True
                    )
            return None, None
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Communication error: {e}")
            if track_dedicated:
                async with self._dedicated_stats_lock:
                    self._dedicated_record_failure_locked(
                        self._dedicated_stats(conn.worker_session_id), time.time(), is_timeout=False
                    )
            return None, None
        finally:
            if track_dedicated:
                async with self._dedicated_stats_lock:
                    st = self._dedicated_stats(conn.worker_session_id)
                    st.inflight_count = max(0, st.inflight_count - 1)
            self.pending_requests.pop(req_id, None)

    async def notify_upstream_verdict(
        self,
        req_id: Optional[str],
        *,
        accepted: bool,
        captcha_rejected: bool,
        detail: Optional[str] = None,
    ) -> None:
        """Tell the extension whether Flow accepted the reCAPTCHA token (same WebSocket as get_token)."""
        rid = (req_id or "").strip()
        if not rid:
            return
        async with self._state_lock:
            websocket = self._upstream_verdict_targets.pop(rid, None)
        if websocket is None:
            return
        payload = {
            "type": "captcha_upstream_verdict",
            "req_id": rid,
            "accepted": bool(accepted),
            "captcha_rejected": bool(captcha_rejected),
            "detail": (detail or "")[:500],
        }
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception as exc:
            debug_logger.log_warning(f"[Extension Captcha] Failed to send upstream verdict: {exc}")

    async def abandon_upstream_verdict(self, req_id: Optional[str]) -> None:
        """Remove pending verdict routing without notifying (e.g. request failed before HTTP response)."""
        rid = (req_id or "").strip()
        if not rid:
            return
        async with self._state_lock:
            self._upstream_verdict_targets.pop(rid, None)

    async def get_token(
        self,
        project_id: str,
        action: str = "IMAGE_GENERATION",
        timeout: int = 20,
        token_id: Optional[int] = None,
        managed_api_key_id: Optional[int] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        route_key = ""
        if managed_api_key_id is None:
            route_key = await self._resolve_route_key(token_id)
        queue_wait_timeout = 20
        fallback_to_managed = False
        if self.db and hasattr(self.db, "get_captcha_config"):
            try:
                captcha_config = await self.db.get_captcha_config()
                queue_wait_timeout = int(getattr(captcha_config, "extension_queue_wait_timeout_seconds", 20) or 20)
                fallback_to_managed = bool(
                    getattr(captcha_config, "extension_fallback_to_managed_on_dedicated_failure", False)
                )
            except Exception as exc:
                debug_logger.log_warning(f"[Extension Captcha] Failed to load queue timeout: {exc}")
        queue_wait_timeout = max(1, min(120, queue_wait_timeout))
        sel_meta: Dict[str, Any] = {}
        conn = await self._wait_for_connection(
            route_key=route_key,
            managed_api_key_id=managed_api_key_id,
            preferred_token_id=token_id if token_id is not None else None,
            timeout=queue_wait_timeout,
            exclude_dedicated_token_id=None,
            selection_meta_out=sel_meta,
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

        async with conn.dispatch_lock:
            token, ext_req_id = await self._extension_recaptcha_token_once(
                conn,
                project_id=project_id,
                action=action,
                route_key=route_key,
                managed_api_key_id=managed_api_key_id,
                timeout=timeout,
                selection_meta=sel_meta if sel_meta else None,
            )
        if token:
            return token, ext_req_id

        # One-shot retry on another dedicated worker for the same token (before managed fallback).
        if (
            token_id is not None
            and conn.dedicated_token_id is not None
            and int(conn.dedicated_token_id) == int(token_id)
        ):
            sel_meta_alt: Dict[str, Any] = {}
            conn_alt = self._select_connection(
                route_key,
                managed_api_key_id,
                preferred_token_id=token_id,
                exclude_dedicated_token_id=None,
                exclude_worker_session_ids={conn.worker_session_id},
                use_dedicated_hybrid=True,
                selection_meta_out=sel_meta_alt,
            )
            if conn_alt is not None and conn_alt.websocket is not conn.websocket:
                async with conn_alt.dispatch_lock:
                    token_alt, ext_req_id_alt = await self._extension_recaptcha_token_once(
                        conn_alt,
                        project_id=project_id,
                        action=action,
                        route_key=route_key,
                        managed_api_key_id=managed_api_key_id,
                        timeout=timeout,
                        selection_meta=sel_meta_alt if sel_meta_alt else None,
                    )
                if token_alt:
                    return token_alt, ext_req_id_alt

        use_fallback = (
            fallback_to_managed
            and managed_api_key_id is not None
            and token_id is not None
            and conn.dedicated_token_id is not None
            and int(conn.dedicated_token_id) == int(token_id)
        )
        if not use_fallback:
            return None, None

        sel_meta2: Dict[str, Any] = {}
        conn2 = await self._wait_for_connection(
            route_key=route_key,
            managed_api_key_id=managed_api_key_id,
            preferred_token_id=None,
            timeout=queue_wait_timeout,
            exclude_dedicated_token_id=int(token_id),
            selection_meta_out=sel_meta2,
        )
        if conn2 is None or conn2.websocket is conn.websocket:
            return None, None
        debug_logger.log_info(
            "[Extension Captcha] Retrying reCAPTCHA on managed-key end-user extension "
            f"after dedicated worker failure (token_id={token_id}, managed_api_key_id={managed_api_key_id})"
        )
        async with conn2.dispatch_lock:
            return await self._extension_recaptcha_token_once(
                conn2,
                project_id=project_id,
                action=action,
                route_key=route_key,
                managed_api_key_id=managed_api_key_id,
                timeout=timeout,
                selection_meta=sel_meta2 if sel_meta2 else None,
            )

    async def _extension_refresh_st_once(
        self,
        conn: ExtensionConnection,
        *,
        token_id: int,
        timeout: int,
    ) -> Optional[str]:
        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = (future, conn.websocket)
        try:
            await conn.websocket.send_text(
                json.dumps(
                    {
                        "type": "refresh_st",
                        "req_id": req_id,
                        "token_id": int(token_id),
                    }
                )
            )
            result = await asyncio.wait_for(future, timeout=timeout)
            if result.get("status") == "success":
                return str(result.get("session_token") or "").strip() or None
            return None
        except Exception as exc:
            debug_logger.log_warning(f"[Extension Captcha] refresh_st failed for token_id={token_id}: {exc}")
            return None
        finally:
            self.pending_requests.pop(req_id, None)

    async def refresh_session_token(
        self,
        *,
        token_id: int,
        timeout: int = 45,
    ) -> Optional[str]:
        """ST refresh is always sent to the extension bound for this token (dedicated if present).

        Intentionally no fallback to other extension connections: session cookies must not be
        read or refreshed on a different browser profile than the account's dedicated worker.
        """
        if token_id is None:
            return None
        conn = self._select_connection(
            route_key="",
            managed_api_key_id=None,
            preferred_token_id=token_id,
            use_dedicated_hybrid=False,
        )
        if conn is None:
            return None
        async with conn.dispatch_lock:
            return await self._extension_refresh_st_once(conn, token_id=token_id, timeout=timeout)

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
                    "dedicated_worker_id": conn.dedicated_worker_id,
                    "dedicated_token_id": conn.dedicated_token_id,
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
