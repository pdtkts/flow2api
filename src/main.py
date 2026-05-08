"""FastAPI application initialization"""
import os
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
from .core.monitoring import CONTENT_TYPE_LATEST, render_main_metrics
from .services.flow_client import FlowClient
from .services.proxy_manager import ProxyManager
from .services.token_manager import TokenManager
from .services.load_balancer import LoadBalancer
from .services.concurrency_manager import ConcurrencyManager
from .services.generation_handler import GenerationHandler
from .services.st_refresh_reasons import describe_st_refresh_reason
from .api import routes, admin
from .core.api_key_manager import ApiKeyManager
from .core.auth import set_api_key_manager
from .core.logger import debug_logger


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
    - Cached media (authenticated): /api/cache/file, /api/cache/file/{project_id}, /api/cache/blob/..., discovery, liveness: /openapi.json, /health, /metrics
    """
    if path in ("/openapi.json", "/health", "/metrics"):
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
    if path.startswith("/api/market/"):
        return True
    # Public cloning + metadata endpoints (managed-API-key auth) used by external clients.
    if path in (
        "/api/generate-cloning-prompts",
        "/api/generate-cloning-video-prompt",
        "/api/generate-metadata",
        "/api/suggested-events",
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

    # Initialize database tables structure
    await db.init_db()

    # Handle database initialization based on startup type
    if is_first_startup:
        print("🎉 First startup detected. Initializing database and configuration from setting.toml...")
        await db.init_config_from_toml(config_dict, is_first_startup=True)
        print("✓ Database and configuration initialized successfully.")
    else:
        print("🔄 Existing database detected. Checking for missing tables and columns...")
        await db.check_and_migrate_db(config_dict)
        print("✓ Database migration check completed.")

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
        from .services.browser_captcha_personal import BrowserCaptchaService
        browser_service = await BrowserCaptchaService.get_instance(db)
        print("✓ Browser captcha service initialized (nodriver mode)")

        warmup_limit = max(1, int(config.personal_max_resident_tabs or 1))
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
                "⚠ Browser captcha resident warmup failed: "
                f"{type(e).__name__}: {e}"
            )
        if warmed_slots:
            print(
                f"✓ Browser captcha shared resident tabs warmed "
                f"({len(warmed_slots)} slot(s), limit={warmup_limit})"
            )
        elif warmup_error is not None:
            print("⚠ Browser captcha resident warmup skipped for this startup")
        elif tokens:
            print("⚠ Browser captcha resident warmup skipped: no tab warmed successfully")
        else:
            # 没有任何可用 token 时，打开登录窗口供用户手动操作
            await browser_service.open_login_window()
            print("⚠ No active token found, opened login window for manual setup")
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
            print(f"✓ Remote browser pool prefill started for {warmed_projects} project(s)")
        except Exception as e:
            print(f"⚠ Remote browser pool prefill failed: {e}")

    # Start 429 auto-unban task
    import asyncio
    async def auto_unban_task():
        """定时任务：每小时检查并解禁429被禁用的token"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时执行一次
                await token_manager.auto_unban_429_tokens()
            except Exception as e:
                print(f"❌ Auto-unban task error: {e}")

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
                        print(f"⚠ Scheduled refresh failed for token {token.id}: {refresh_err}")
            except Exception as e:
                print(f"❌ Scheduled token refresh task error: {e}")

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

    print(f"✓ Database initialized")
    print(f"✓ Total tokens: {len(tokens)}")
    ct = config.cache_timeout
    d = f", ~{ct / 86400.0:.3g}d" if ct and ct > 0 else " (no auto-expiry)"
    print(f"✓ Cache: {'Enabled' if config.cache_enabled else 'Disabled'} (timeout: {ct}s{d})")
    if cache_cleanup_enabled:
        print("✓ File cache cleanup task started")
    else:
        print("✓ File cache cleanup task disabled (timeout <= 0)")
    print(f"✓ 429 auto-unban task started (runs every hour)")
    print("✓ Scheduled token refresh task started")
    print("✓ Scheduled ST-only refresh task started")
    print(f"✓ Server running on http://{config.server_host}:{config.server_port}")
    print("=" * 60)

    yield

    # Shutdown
    print("Flow2API Shutting down...")
    # Stop file cache cleanup task
    await generation_handler.file_cache.stop_cleanup_task()
    # Stop auto-unban task
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
    # Close browser if initialized
    if browser_service:
        await browser_service.close()
        print("✓ Browser captcha service closed")
    print("✓ File cache cleanup task stopped")
    print("✓ 429 auto-unban task stopped")
    print("✓ Scheduled token refresh task stopped")
    print("✓ Scheduled ST-only refresh task stopped")


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
managed_api_key_manager = ApiKeyManager(db, legacy_api_key_provider=lambda: config.api_key)

# Set dependencies
routes.set_generation_handler(generation_handler)
admin.set_dependencies(token_manager, proxy_manager, db, concurrency_manager, managed_api_key_manager)
set_api_key_manager(managed_api_key_manager)

# Create FastAPI app
app = FastAPI(
    title="Flow2API",
    description="OpenAI-compatible API for Google VideoFX (Veo)",
    version="1.0.0",
    lifespan=lifespan
)

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
