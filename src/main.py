"""FastAPI application initialization"""
import asyncio
import errno
import gc
import heapq
import os
import shutil
import sqlite3
import sys
import tempfile
import warnings
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from contextlib import asynccontextmanager
from pathlib import Path

from .core.config import config
from .core.database import Database
from .core.storage_errors import (
    is_sqlite_recoverable_storage_error,
    sqlite_operational_error_handler,
)
from .core.monitoring import CONTENT_TYPE_LATEST, render_main_metrics
from .services.flow_client import FlowClient
from .services.proxy_manager import ProxyManager
from .services.token_manager import TokenManager
from .services.load_balancer import LoadBalancer
from .services.concurrency_manager import ConcurrencyManager
from .services.generation_handler import GenerationHandler
from .services.geminigen_service import GeminiGenService
from .services.runway_service import RunwayService
from .services.st_refresh_reasons import describe_st_refresh_reason
from .api import routes, admin
from .core.api_key_manager import ApiKeyManager
from .core.auth import set_api_key_manager
from .core.logger import debug_logger


_LOCAL_NO_PROXY_HOSTS = ("127.0.0.1", "localhost", "::1")


def _configure_stdio() -> None:
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _configure_local_no_proxy() -> None:
    for env_name in ("NO_PROXY", "no_proxy"):
        entries = [item.strip() for item in str(os.environ.get(env_name, "") or "").replace(";", ",").split(",") if item.strip()]
        normalized = {item.lower() for item in entries}
        for host in _LOCAL_NO_PROXY_HOSTS:
            if host.lower() not in normalized:
                entries.append(host)
                normalized.add(host.lower())
        os.environ[env_name] = ",".join(entries)


def _configure_asyncio_policy() -> None:
    if os.name != "nt":
        return
    policy_class = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_class is not None and not isinstance(asyncio.get_event_loop_policy(), policy_class):
        asyncio.set_event_loop_policy(policy_class())


def _configure_process_runtime() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    _configure_stdio()
    _configure_local_no_proxy()
    _configure_asyncio_policy()
    warnings.filterwarnings(
        "ignore",
        message=r".*Proactor event loop does not implement add_reader family of methods required.*",
        category=RuntimeWarning,
    )


_configure_process_runtime()


def _normalize_host(host: str) -> str:
    if not host:
        return ""
    return host.split(":")[0].strip().lower()


def _api_only_hostnames() -> set[str]:
    """Comma-separated FQDNs from env FLOW2API_API_ONLY_HOST only (see docker-compose.yml, .env)."""
    raw = (os.environ.get("FLOW2API_API_ONLY_HOST") or "").strip()
    if not raw:
        return set()
    return {_normalize_host(h) for h in raw.split(",") if h.strip()}


def _incoming_hostname(request: Request) -> str:
    # RFC 7239 Forwarded (some proxies; Cloudflare may use X-Forwarded-Host only)
    fwd = (request.headers.get("forwarded") or "")
    if fwd:
        for segment in fwd.split(","):
            for token in segment.split(";"):
                t = token.strip()
                if t.lower().startswith("host="):
                    v = t.split("=", 1)[-1].strip().strip('"')
                    if v:
                        return _normalize_host(v)
    xf = (request.headers.get("x-forwarded-host") or "").strip()
    if xf:
        return _normalize_host(xf.split(",")[0].strip())
    cdn = (request.headers.get("x-cdn-request-host") or "").strip()  # rare
    if cdn:
        return _normalize_host(cdn)
    return _normalize_host(request.headers.get("host", ""))


def _path_allowed_on_api_only_host(path: str) -> bool:
    """
    Paths allowed on the API-only public host (no admin SPA / /api on this host).

    - OpenAI / Chat Completions style: /v1/chat/completions, /v1/models, /v1/models/aliases, /v1/projects, …
    - Gemini (Google) style: /v1beta/models/…:generateContent, :streamGenerateContent, list models, …
    - Same body on alternate paths: /models, /models/{m}:generateContent, …
    - Cached media (authenticated): /api/cache/file, /api/cache/file/{project_id}, /api/cache/blob/...
    - Desktop presence (authenticated): /api/client/presence
    - Extension workers: /captcha_ws
    - Discovery/liveness: /openapi.json, /health, /metrics
    """
    if path in ("/openapi.json", "/health", "/metrics", "/captcha_ws"):
        return True
    if path.startswith(("/v1/", "/v1beta/")) or path in ("/v1", "/v1beta"):
        return True
    if path.startswith("/models/") or path == "/models":
        return True
    if path.startswith("/api/cache/"):
        return True
    if path.startswith("/api/extension/"):
        return True
    if path.startswith("/api/tracker/"):
        return True
    if path == "/api/client/presence":
        return True
    # Public cloning + metadata endpoints (managed-API-key auth) used by external clients.
    if path in (
        "/api/generate-cloning-prompts",
        "/api/generate-cloning-video-prompt",
        "/api/generate-metadata",
    ):
        return True
    return False


class ApiOnlyHostMiddleware(BaseHTTPMiddleware):
    """
    If FLOW2API_API_ONLY_HOST is set (comma-separated FQDNs), requests whose Host
    (or X-Forwarded-Host) matches get public OpenAI- and Gemini-style routes + /tmp only;
    SPA, /api, /assets, /docs, /redoc, etc. return 404.
    Add before CORS in code so CORS still wraps the response.
    """

    async def dispatch(self, request: Request, call_next):
        hosts = _api_only_hostnames()
        if not hosts:
            return await call_next(request)
        h = _incoming_hostname(request)
        if h not in hosts:
            return await call_next(request)
        path = request.url.path
        if _path_allowed_on_api_only_host(path):
            return await call_next(request)
        return JSONResponse({"detail": "Not Found"}, status_code=404)


def _storage_recovery_diagnostic(stats: dict) -> str:
    return (
        "Flow2API startup blocked: storage I/O remains unavailable after cache recovery "
        f"(free={int(stats.get('free_after', 0))} bytes, "
        f"reclaimed={int(stats.get('reclaimed_bytes', 0))} bytes, "
        f"target={int(stats.get('target_free', 0))} bytes)."
    )


EMERGENCY_PRUNE_TABLES = (
    "request_logs",
    "tasks",
    "cache_files",
    "geminigen_tasks",
    "runway_tasks",
    "api_key_audit_logs",
    "admin_sessions",
)
REQUEST_LOG_RETENTION_DAYS = 3
REQUEST_LOG_CLEANUP_INTERVAL_SECONDS = 12 * 3600


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}TB"


def _directory_size(path: Path) -> int:
    total = 0
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    for root, dirnames, filenames in os.walk(path, followlinks=False):
        root_path = Path(root)
        dirnames[:] = [
            name for name in dirnames
            if not (root_path / name).is_symlink()
        ]
        for filename in filenames:
            file_path = root_path / filename
            try:
                if not file_path.is_symlink():
                    total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _largest_files(path: Path, limit: int = 20):
    largest = []
    for root, dirnames, filenames in os.walk(path, followlinks=False):
        root_path = Path(root)
        dirnames[:] = [
            name for name in dirnames
            if not (root_path / name).is_symlink()
        ]
        for filename in filenames:
            file_path = root_path / filename
            try:
                if file_path.is_symlink():
                    continue
                size = file_path.stat().st_size
            except OSError:
                continue
            item = (size, str(file_path))
            if len(largest) < limit:
                heapq.heappush(largest, item)
            elif size > largest[0][0]:
                heapq.heapreplace(largest, item)
    return sorted(largest, reverse=True)


def _sqlite_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _remove_sqlite_sidecars(db_path: Path) -> int:
    removed = 0
    candidates = [
        db_path.with_name(db_path.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    ]
    candidates.extend(db_path.parent.glob(f"{db_path.name}.upload-*"))
    for path in candidates:
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _emergency_prune_sqlite_history(database) -> dict:
    db_path = Path(getattr(database, "db_path", "") or "")
    if not db_path.is_file():
        return {"success": False, "reason": "database file not found"}

    old_size = db_path.stat().st_size
    source_path = Path(tempfile.gettempdir()) / f"flow2api-source-{os.getpid()}.db"
    compact_path = Path(tempfile.gettempdir()) / f"flow2api-compact-{os.getpid()}.db"
    for path in (source_path, compact_path):
        try:
            path.unlink()
        except OSError:
            pass

    shutil.copy2(db_path, source_path)
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.is_file() and not sidecar.is_symlink():
            shutil.copy2(sidecar, source_path.with_name(source_path.name + suffix))

    deleted_rows = {}
    source_uri = f"{source_path.resolve().as_uri()}?mode=rw"
    conn = sqlite3.connect(source_uri, uri=True, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError as exc:
            print(f"WARN Emergency DB prune could not checkpoint WAL: {exc}")
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA temp_store = MEMORY")

        for table_name in EMERGENCY_PRUNE_TABLES:
            if not _sqlite_table_exists(conn, table_name):
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                conn.execute(f"DELETE FROM {table_name}")
                deleted_rows[table_name] = int(count or 0)
            except sqlite3.DatabaseError as exc:
                print(f"WARN Emergency DB prune skipped {table_name}: {exc}")
        conn.commit()

        conn.execute(f"VACUUM INTO {_sqlite_literal(compact_path)}")
    finally:
        conn.close()
        del conn
        gc.collect()

    with sqlite3.connect(f"{compact_path.resolve().as_uri()}?mode=ro", uri=True) as compact:
        check = compact.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"Compacted SQLite quick_check failed: {check}")
    gc.collect()

    _remove_sqlite_sidecars(db_path)
    try:
        os.replace(compact_path, db_path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        db_path.unlink()
        shutil.copy2(compact_path, db_path)
        compact_path.unlink()

    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            source_path.with_name(source_path.name + suffix).unlink()
        except OSError:
            pass

    new_size = db_path.stat().st_size
    return {
        "success": True,
        "old_size": int(old_size),
        "new_size": int(new_size),
        "reclaimed_bytes": int(max(0, old_size - new_size)),
        "deleted_rows": deleted_rows,
    }


def _try_emergency_prune_sqlite_history(database) -> dict:
    try:
        result = _emergency_prune_sqlite_history(database)
    except Exception as exc:
        print(f"WARN Emergency DB history prune failed: {exc}")
        return {"success": False, "reason": str(exc)}

    if result.get("success"):
        deleted = ", ".join(
            f"{table}={count}" for table, count in sorted(result.get("deleted_rows", {}).items())
        ) or "none"
        print(
            "WARN Emergency DB history prune compacted SQLite "
            f"from {_format_bytes(result.get('old_size', 0))} "
            f"to {_format_bytes(result.get('new_size', 0))}; "
            f"reclaimed={_format_bytes(result.get('reclaimed_bytes', 0))}; "
            f"deleted_rows={deleted}"
        )
    else:
        print(f"WARN Emergency DB history prune skipped: {result.get('reason')}")
    return result


def _log_volume_usage_report(file_cache) -> None:
    cache_dir = getattr(file_cache, "cache_dir", None)
    root_value = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or (
        str(Path(cache_dir).parent) if cache_dir else ""
    )
    if not root_value:
        return
    root = Path(root_value)
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        print(f"WARN Unable to inspect runtime volume {root}: {exc}")
        return

    print(
        "WARN Runtime volume usage: "
        f"path={root}, total={_format_bytes(usage.total)}, "
        f"used={_format_bytes(usage.used)}, free={_format_bytes(usage.free)}"
    )
    try:
        children = list(root.iterdir())
    except OSError as exc:
        print(f"WARN Unable to list runtime volume {root}: {exc}")
        children = []
    if children:
        print("WARN Runtime volume top-level usage:")
        for child in sorted(children, key=_directory_size, reverse=True)[:20]:
            print(f"WARN   {_format_bytes(_directory_size(child))}\t{child}")

    largest = _largest_files(root, limit=20)
    if largest:
        print("WARN Runtime volume largest files:")
        for size, path in largest:
            print(f"WARN   {_format_bytes(size)}\t{path}")


async def _run_database_startup(database, config_dict: dict = None, is_first_startup: bool = None) -> None:
    await database.init_db()
    if is_first_startup is None:
        return

    if is_first_startup:
        print("First startup detected. Initializing database and configuration from setting.toml...")
        await database.init_config_from_toml(config_dict, is_first_startup=True)
        print("OK Database and configuration initialized successfully.")
    else:
        print("Existing database detected. Checking for missing tables and columns...")
        await database.check_and_migrate_db(config_dict)
        print("OK Database migration check completed.")


async def _init_database_with_storage_recovery(
    database,
    file_cache,
    config_dict: dict = None,
    is_first_startup: bool = None,
) -> None:
    """Run SQLite startup, evicting generated cache files on recoverable storage errors once."""
    await file_cache._cleanup_expired_files()
    try:
        await _run_database_startup(database, config_dict, is_first_startup)
        return
    except Exception as exc:
        if not is_sqlite_recoverable_storage_error(exc):
            raise
        first_error = exc

    _try_emergency_prune_sqlite_history(database)
    stats = await file_cache.reclaim_cache_space()
    if stats["free_after"] < stats["target_free"]:
        _log_volume_usage_report(file_cache)
        raise RuntimeError(_storage_recovery_diagnostic(stats)) from first_error

    print(
        "WARN SQLite startup storage error "
        f"({first_error}); generated cache recovery reclaimed "
        f"{stats['reclaimed_bytes']} bytes. Retrying database startup once."
    )
    try:
        await _run_database_startup(database, config_dict, is_first_startup)
    except Exception as exc:
        if is_sqlite_recoverable_storage_error(exc):
            _log_volume_usage_report(file_cache)
            raise RuntimeError(_storage_recovery_diagnostic(stats)) from exc
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    print("=" * 60)
    print("Flow2API Starting...")
    api_only = _api_only_hostnames()
    if api_only:
        print(
            f"API-only host(s) (no web UI on these hosts): {', '.join(sorted(api_only))}"
        )
    print("=" * 60)

    # Get config from setting.toml
    config_dict = config.get_raw_config()

    # Check if database exists (determine if first startup)
    is_first_startup = not db.db_exists()

    # Initialize database tables/configuration, reclaiming generated cache once on storage pressure.
    await _init_database_with_storage_recovery(
        db,
        generation_handler.file_cache,
        config_dict=config_dict,
        is_first_startup=is_first_startup,
    )

    # 启动时统一把数据库配置同步到内存，避免 personal/brower 相关运行时配置遗漏。
    await db.reload_config_to_memory()
    generation_handler.file_cache.set_timeout(config.cache_timeout)
    cache_cleanup_enabled = await generation_handler.file_cache.refresh_cleanup_task()
    captcha_config = await db.get_captcha_config()

    # 尽量在浏览器服务启动前就拿到 token 快照，后续并发管理和预热共用。
    tokens = await token_manager.get_all_tokens()

    # Initialize browser captcha service if needed
    browser_service = None
    if captcha_config.captcha_method == "personal":
        from .services.browser_captcha_personal import (
            BrowserCaptchaService,
            PERSONAL_POOL_MAX_TOTAL_RESIDENT_TABS,
            resolve_effective_browser_count,
            resolve_effective_personal_max_resident_tabs,
        )
        browser_service = await BrowserCaptchaService.get_instance(db)
        print("OK Browser captcha service initialized (nodriver mode)")

        warmup_limit = max(1, min(
            PERSONAL_POOL_MAX_TOTAL_RESIDENT_TABS,
            resolve_effective_browser_count(config.browser_count)
            * resolve_effective_personal_max_resident_tabs(config.personal_max_resident_tabs),
        ))
        warmup_project_ids = await token_manager.get_personal_warmup_project_ids(
            tokens=tokens,
            limit=warmup_limit,
        )

        warmed_slots = []
        warmup_error = None
        try:
            warmed_slots = await browser_service.warmup_resident_tabs(
                warmup_project_ids,
                limit=warmup_limit,
            )
        except Exception as e:
            warmup_error = e
            print(
                "WARN Browser captcha resident warmup failed: "
                f"{type(e).__name__}: {e}"
            )
        if warmed_slots:
            print(
                f"OK Browser captcha shared resident tabs warmed "
                f"({len(warmed_slots)} slot(s), limit={warmup_limit})"
            )
        elif warmup_error is not None:
            print("WARN Browser captcha resident warmup skipped for this startup")
        elif tokens:
            print("WARN Browser captcha resident warmup skipped: no tab warmed successfully")
        else:
            # 没有任何可用 token 时，打开登录窗口供用户手动操作
            await browser_service.open_login_window()
            print("WARN No active token found, opened login window for manual setup")
    elif captcha_config.captcha_method == "browser":
        from .services.browser_captcha import BrowserCaptchaService
        browser_service = await BrowserCaptchaService.get_instance(db)
        await browser_service.warmup_browser_slots()
        print("Browser captcha service initialized (headed / Playwright pool)")

    # Initialize concurrency manager
    await concurrency_manager.initialize(tokens)

    if config.captcha_method == "remote_browser":
        try:
            warmed_projects = await flow_client.prefill_remote_browser_for_tokens(tokens, action="IMAGE_GENERATION")
            print(f"OK Remote browser pool prefill started for {warmed_projects} project(s)")
        except Exception as e:
            print(f"WARN Remote browser pool prefill failed: {e}")

    # Start 429 auto-unban task
    import asyncio

    async def request_log_cleanup_task():
        """Prune request log rows while leaving durable telemetry untouched."""
        while True:
            try:
                deleted = await db.delete_request_logs_older_than(REQUEST_LOG_RETENTION_DAYS)
                if deleted:
                    debug_logger.log_info(
                        f"[REQUEST_LOG_CLEANUP] Deleted {deleted} request log row(s) older than {REQUEST_LOG_RETENTION_DAYS} days"
                    )
                await asyncio.sleep(REQUEST_LOG_CLEANUP_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                debug_logger.log_warning(f"[REQUEST_LOG_CLEANUP] task error: {e}")
                await asyncio.sleep(3600)

    request_log_cleanup_handle = asyncio.create_task(request_log_cleanup_task())

    async def auto_unban_task():
        """定时任务：每小时检查并解禁429被禁用的token"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时执行一次
                await token_manager.auto_unban_429_tokens()
            except Exception as e:
                print(f"ERR Auto-unban task error: {e}")

    auto_unban_task_handle = asyncio.create_task(auto_unban_task())

    async def scheduled_token_refresh_task():
        """Configurable scheduled token refresh that reuses existing refresh path."""
        while True:
            try:
                interval_minutes = max(1, int(config.session_refresh_scheduler_interval_minutes))
                await asyncio.sleep(interval_minutes * 60)
                if not config.session_refresh_scheduler_enabled:
                    continue

                all_tokens = await token_manager.get_active_tokens()
                if not all_tokens:
                    continue

                expiring_within_minutes = max(
                    1,
                    int(config.session_refresh_scheduler_only_expiring_within_minutes),
                )
                expiring_window = expiring_within_minutes * 60
                now = datetime.now(timezone.utc)
                candidates = []
                for token in all_tokens:
                    if not token:
                        continue
                    if token.at_expires is None:
                        candidates.append(token)
                        continue
                    exp = token.at_expires
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    remaining = (exp - now).total_seconds()
                    if remaining <= expiring_window:
                        candidates.append(token)

                batch_size = max(1, int(config.session_refresh_scheduler_batch_size))
                for token in candidates[:batch_size]:
                    try:
                        await token_manager._refresh_at(token.id)
                    except Exception as refresh_err:
                        print(f"WARN Scheduled refresh failed for token {token.id}: {refresh_err}")
            except Exception as e:
                print(f"ERR Scheduled token refresh task error: {e}")

    scheduled_token_refresh_handle = asyncio.create_task(scheduled_token_refresh_task())

    async def scheduled_st_only_refresh_task():
        """ST-only refresh scheduler.

        For each active token whose at_expires is within X minutes (or already
        expired / unknown), pull a fresh __Secure-next-auth.session-token from
        the bound extension worker (or local headed browser fallback) without
        minting a new AT. Per-token in-memory debounce of X minutes prevents
        re-attacking the same token within a single window. Failures are logged
        with the friendly hint from describe_st_refresh_reason; tokens are NOT
        disabled by this scheduler.
        """
        last_attempt: dict[int, float] = {}
        while True:
            try:
                interval_minutes = max(1, int(config.st_only_refresh_scheduler_interval_minutes))
                await asyncio.sleep(interval_minutes * 60)
                if not config.st_only_refresh_scheduler_enabled:
                    continue

                all_tokens = await token_manager.get_active_tokens()
                if not all_tokens:
                    continue

                window_minutes = max(1, int(config.st_only_refresh_scheduler_expiring_within_minutes))
                window_seconds = window_minutes * 60
                now = datetime.now(timezone.utc)
                tokens_due = []
                for tk in all_tokens:
                    if not tk:
                        continue
                    exp = tk.at_expires
                    if exp is None:
                        tokens_due.append(tk)
                        continue
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if (exp - now).total_seconds() <= window_seconds:
                        tokens_due.append(tk)

                debounce_seconds = window_seconds
                now_ts = now.timestamp()
                tokens_due = [
                    t for t in tokens_due
                    if (now_ts - last_attempt.get(t.id or 0, 0.0)) >= debounce_seconds
                ]

                batch_size = max(1, int(config.st_only_refresh_scheduler_batch_size))
                for tk in tokens_due[:batch_size]:
                    if tk.id is None:
                        continue
                    last_attempt[tk.id] = now_ts
                    try:
                        ok = await token_manager.refresh_st_only(tk.id)
                        reason = token_manager.consume_st_refresh_reason(tk.id)
                        if ok:
                            debug_logger.log_info(
                                f"[ST_SCHEDULER] Token {tk.id}: ST refreshed "
                                f"(reason={reason or 'success'})"
                            )
                        else:
                            hint = describe_st_refresh_reason(reason)
                            debug_logger.log_warning(
                                f"[ST_SCHEDULER] Token {tk.id}: ST refresh failed "
                                f"(reason={reason or 'unknown'}; {hint or 'no hint'})"
                            )
                    except Exception as refresh_err:
                        debug_logger.log_warning(
                            f"[ST_SCHEDULER] Token {tk.id}: scheduled ST refresh raised: {refresh_err}"
                        )
            except Exception as e:
                debug_logger.log_error(f"[ST_SCHEDULER] task error: {e}")

    scheduled_st_only_refresh_handle = asyncio.create_task(scheduled_st_only_refresh_task())
    token_manager.start_protocol_refresher()
    resumed_geminigen_tasks = await geminigen_service.resume_active_tasks()

    print("OK Database initialized")
    print(f"OK Total tokens: {len(tokens)}")
    ct = config.cache_timeout
    d = f", ~{ct / 86400.0:.3g}d" if ct and ct > 0 else " (no auto-expiry)"
    print(f"OK Cache: {'Enabled' if config.cache_enabled else 'Disabled'} (timeout: {ct}s{d})")
    if cache_cleanup_enabled:
        print("OK File cache cleanup task started")
    else:
        print("WARN File cache cleanup task failed to start")
    print("OK 429 auto-unban task started (runs every hour)")
    print(f"OK Request log cleanup task started (retention: {REQUEST_LOG_RETENTION_DAYS} days)")
    print("OK Scheduled token refresh task started")
    print("OK Scheduled ST-only refresh task started")
    print("OK Protocol token refresh task started")
    if resumed_geminigen_tasks:
        print(f"OK GeminiGen active task resume started ({resumed_geminigen_tasks} task(s))")
    print(f"OK Server running on http://{config.server_host}:{config.server_port}")
    print("=" * 60)

    yield

    # Shutdown
    print("Flow2API Shutting down...")
    # Stop file cache cleanup task
    await generation_handler.file_cache.stop_cleanup_task()
    # Stop auto-unban task
    request_log_cleanup_handle.cancel()
    try:
        await request_log_cleanup_handle
    except asyncio.CancelledError:
        pass
    auto_unban_task_handle.cancel()
    try:
        await auto_unban_task_handle
    except asyncio.CancelledError:
        pass
    # Stop scheduled token refresh task
    scheduled_token_refresh_handle.cancel()
    try:
        await scheduled_token_refresh_handle
    except asyncio.CancelledError:
        pass
    # Stop scheduled ST-only refresh task
    scheduled_st_only_refresh_handle.cancel()
    try:
        await scheduled_st_only_refresh_handle
    except asyncio.CancelledError:
        pass
    await token_manager.stop_protocol_refresher()
    # Close browser if initialized
    if browser_service:
        await browser_service.close()
        print("OK Browser captcha service closed")
    print("OK File cache cleanup task stopped")
    print("OK Request log cleanup task stopped")
    print("OK 429 auto-unban task stopped")
    print("OK Scheduled token refresh task stopped")
    print("OK Scheduled ST-only refresh task stopped")
    print("OK Protocol token refresh task stopped")


# Initialize components
db = Database()
proxy_manager = ProxyManager(db)
flow_client = FlowClient(proxy_manager, db)
token_manager = TokenManager(db, flow_client)
concurrency_manager = ConcurrencyManager()
load_balancer = LoadBalancer(token_manager, concurrency_manager)
generation_handler = GenerationHandler(
    flow_client,
    token_manager,
    load_balancer,
    db,
    concurrency_manager,
    proxy_manager  # 添加 proxy_manager 参数
)
runway_service = RunwayService(db, generation_handler.file_cache, proxy_manager)
geminigen_service = GeminiGenService(db, generation_handler.file_cache, proxy_manager)
managed_api_key_manager = ApiKeyManager(db, legacy_api_key_provider=lambda: config.api_key)

# Set dependencies
routes.set_generation_handler(generation_handler)
routes.set_runway_service(runway_service)
routes.set_geminigen_service(geminigen_service)
admin.set_dependencies(token_manager, proxy_manager, db, concurrency_manager, managed_api_key_manager, runway_service, geminigen_service)
set_api_key_manager(managed_api_key_manager)

# Create FastAPI app
app = FastAPI(
    title="Flow2API",
    description="OpenAI-compatible API for Google VideoFX (Veo)",
    version="1.0.0",
    lifespan=lifespan
)
app.add_exception_handler(sqlite3.OperationalError, sqlite_operational_error_handler)

# CORS is added after this block so CORS is outer and still applies to 404s
app.add_middleware(ApiOnlyHostMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(routes.router)
app.include_router(admin.router)


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint for the main Flow2API service."""
    payload = await render_main_metrics(db, concurrency_manager=concurrency_manager)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

# HTML routes for frontend
static_path = Path(__file__).parent.parent / "static"

# Serve static assets (js, css, images from Vite build)
assets_path = static_path / "assets"
if assets_path.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Catch-all route to serve the React SPA"""
    # If the user tries to access the API directly via an undefined route, let it return 404 naturally
    # Or if it's an API route that somehow wasn't matched (though it should be matched earlier)
    if full_path.startswith("api/"):
        return HTMLResponse(content='{"detail": "Not Found"}', status_code=404)
        
    index_file = static_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse(content="<h1>Flow2API GUI</h1><p>Frontend not found. Please build the frontend first.</p>", status_code=404)
