"""Admin API routes"""
import asyncio
import inspect
import json
import mimetypes
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import secrets
import time
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse
from curl_cffi.requests import AsyncSession
from ..core.auth import AuthManager
from ..core.api_key_manager import ApiKeyManager
from ..core.database import Database
from ..core.config import config
from ..core.models import GenerationConfig
from ..core.monitoring import build_public_health_snapshot
from ..services.token_manager import TokenManager
from ..services.proxy_manager import ProxyManager
from ..services.concurrency_manager import ConcurrencyManager

try:
    import httpx
except ImportError:
    httpx = None

router = APIRouter()

# Dependency injection
token_manager: TokenManager = None
proxy_manager: ProxyManager = None
db: Database = None
concurrency_manager: Optional[ConcurrencyManager] = None
api_key_manager: Optional[ApiKeyManager] = None

# Admin session TTLs (seconds)
_ADMIN_SESSION_TTL_REMEMBER = 30 * 24 * 3600  # 30 days when "remember me" is on
_ADMIN_SESSION_TTL_BROWSER = 24 * 3600  # 24 hours when off

SUPPORTED_API_CAPTCHA_METHODS = {"yescaptcha", "capmonster", "ezcaptcha", "capsolver"}


def _generate_worker_registration_key() -> str:
    return f"wk_{secrets.token_urlsafe(32)}"


def _hash_worker_registration_key(raw_key: str) -> str:
    return hashlib.sha256((raw_key or "").encode("utf-8")).hexdigest()


def _worker_key_prefix(raw_key: str) -> str:
    key = (raw_key or "").strip()
    if len(key) <= 12:
        return key
    return f"{key[:8]}...{key[-4:]}"


def _mask_token(token: Optional[str]) -> str:
    if not token:
        return ""
    if len(token) <= 24:
        return token
    return f"{token[:18]}...{token[-8:]}"


def _truncate_text(text: Any, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit - 3]}..."


def _extract_error_summary(payload: Any) -> str:
    """从响应体里提取用户可读的错误摘要。"""
    if payload is None:
        return ""

    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return ""
        try:
            return _extract_error_summary(json.loads(raw))
        except Exception:
            return _truncate_text(raw)

    if isinstance(payload, dict):
        for key in ("error_summary", "error_message", "detail", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _truncate_text(value)

        error_value = payload.get("error")
        if isinstance(error_value, dict):
            for key in ("message", "detail", "reason", "code"):
                value = error_value.get(key)
                if isinstance(value, str) and value.strip():
                    return _truncate_text(value)
        elif isinstance(error_value, str) and error_value.strip():
            return _truncate_text(error_value)

        for nested_key in ("response", "data"):
            nested = payload.get(nested_key)
            if isinstance(nested, (dict, list, str)):
                summary = _extract_error_summary(nested)
                if summary:
                    return summary

        return ""

    if isinstance(payload, list):
        for item in payload:
            summary = _extract_error_summary(item)
            if summary:
                return summary
        return ""

    return _truncate_text(payload)


def _guess_client_hints_from_user_agent(user_agent: str) -> Dict[str, str]:
    """根据 UA 补全常见的 sec-ch-* 头。"""
    ua = (user_agent or "").strip()
    if not ua:
        return {}

    headers: Dict[str, str] = {}
    major_match = re.search(r"(?:Chrome|Chromium|Edg|EdgA|EdgiOS)/(\d+)", ua)
    is_mobile = any(token in ua for token in ("Android", "iPhone", "iPad", "Mobile"))
    headers["sec-ch-ua-mobile"] = "?1" if is_mobile else "?0"

    if "Windows" in ua:
        headers["sec-ch-ua-platform"] = '"Windows"'
    elif "Macintosh" in ua or "Mac OS X" in ua:
        headers["sec-ch-ua-platform"] = '"macOS"'
    elif "Android" in ua:
        headers["sec-ch-ua-platform"] = '"Android"'
    elif "iPhone" in ua or "iPad" in ua:
        headers["sec-ch-ua-platform"] = '"iOS"'
    elif "Linux" in ua:
        headers["sec-ch-ua-platform"] = '"Linux"'

    if major_match:
        major = major_match.group(1)
        if "Edg/" in ua:
            headers["sec-ch-ua"] = (
                f'"Not:A-Brand";v="99", "Microsoft Edge";v="{major}", "Chromium";v="{major}"'
            )
        else:
            headers["sec-ch-ua"] = (
                f'"Not:A-Brand";v="99", "Google Chrome";v="{major}", "Chromium";v="{major}"'
            )

    return headers


def _guess_impersonate_from_user_agent(user_agent: str) -> str:
    """从 UA 选择可用的 curl_cffi 浏览器指纹版本。"""
    ua = (user_agent or "").strip()
    major_match = re.search(r"(?:Chrome|Chromium|Edg|EdgA|EdgiOS)/(\d+)", ua)
    if not major_match:
        return "chrome120"

    try:
        major = int(major_match.group(1))
    except Exception:
        return "chrome120"

    if major >= 124:
        return "chrome124"
    if major >= 120:
        return "chrome120"
    return "chrome120"


def _build_proxy_map(proxy_url: str) -> Optional[Dict[str, str]]:
    normalized = (proxy_url or "").strip()
    if not normalized:
        return None
    return {"http": normalized, "https": normalized}


def _normalize_http_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        raise RuntimeError("远程打码服务地址未配置")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("远程打码服务地址格式错误，必须是 http(s)://host[:port]")

    return normalized


def _get_remote_browser_client_config() -> tuple[str, str, int]:
    base_url = _normalize_http_base_url(config.remote_browser_base_url)
    api_key = (config.remote_browser_api_key or "").strip()
    if not api_key:
        raise RuntimeError("远程打码服务 API Key 未配置")
    timeout = max(5, int(config.remote_browser_timeout or 60))
    return base_url, api_key, timeout


def _build_remote_browser_http_timeout(read_timeout: float) -> Any:
    read_value = max(3.0, float(read_timeout))
    write_value = min(10.0, max(3.0, read_value))
    if httpx is None:
        return read_value
    return httpx.Timeout(
        connect=2.5,
        read=read_value,
        write=write_value,
        pool=2.5,
    )


def _parse_json_response_text(text: str) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


async def _stdlib_json_http_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]],
    timeout: int,
) -> tuple[int, Optional[Any], str]:
    req_headers = dict(headers or {})
    req_headers.setdefault("Accept", "application/json")
    request_method = (method or "GET").upper()
    request_data: Optional[bytes] = None

    if payload is not None:
        req_headers["Content-Type"] = "application/json; charset=utf-8"
        if request_method != "GET":
            request_data = json.dumps(payload).encode("utf-8")

    def do_request() -> tuple[int, str]:
        request = urllib.request.Request(
            url=url,
            data=request_data,
            headers=req_headers,
            method=request_method,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=max(1.0, float(timeout))) as response:
                status_code = int(getattr(response, "status", 0) or response.getcode() or 0)
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return status_code, body.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            charset = exc.headers.get_content_charset() if exc.headers else None
            return int(getattr(exc, "code", 0) or 0), body.decode(charset or "utf-8", errors="replace")

    try:
        status_code, text = await asyncio.to_thread(do_request)
    except Exception as e:
        raise RuntimeError(f"远程打码服务请求失败: {e}") from e

    return status_code, _parse_json_response_text(text), text


async def _sync_json_http_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]],
    timeout: int,
) -> tuple[int, Optional[Any], str]:
    req_headers = dict(headers or {})
    req_headers.setdefault("Accept", "application/json")
    request_method = (method or "GET").upper()
    request_kwargs: Dict[str, Any] = {
        "headers": req_headers,
        "timeout": _build_remote_browser_http_timeout(timeout),
    }

    if payload is not None:
        req_headers["Content-Type"] = "application/json; charset=utf-8"
        if request_method != "GET":
            request_kwargs["json"] = payload

    if httpx is None:
        return await _stdlib_json_http_request(
            method=method,
            url=url,
            headers=req_headers,
            payload=payload,
            timeout=timeout,
        )

    try:
        # remote_browser 控制面是服务间 JSON API，使用 httpx 避免 curl_cffi 在当前
        # Windows + impersonate 场景下 POST body 丢失导致 FastAPI 直接判定 body 缺失。
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as session:
            response = await session.request(
                method=request_method,
                url=url,
                **request_kwargs,
            )
    except Exception as e:
        raise RuntimeError(f"远程打码服务请求失败: {e}") from e

    status_code = int(getattr(response, "status_code", 0) or 0)
    text = response.text or ""
    parsed = _parse_json_response_text(text)

    return status_code, parsed, text


async def _resolve_score_test_verify_proxy(
    captcha_method: str,
    browser_proxy_enabled: bool,
    browser_proxy_url: str
) -> tuple[Optional[Dict[str, str]], bool, str, str]:
    """
    选择 score-test 的 verify 请求代理，优先与浏览器打码代理保持一致。
    返回: (proxies, used, source, proxy_url)
    """
    # 浏览器打码模式优先使用 browser_proxy，确保与取 token 出口一致
    if captcha_method in {"browser", "personal"} and browser_proxy_enabled and browser_proxy_url:
        proxy_map = _build_proxy_map(browser_proxy_url)
        if proxy_map:
            return proxy_map, True, "captcha_browser_proxy", browser_proxy_url

    # 退回请求代理配置
    try:
        if proxy_manager:
            proxy_cfg = await proxy_manager.get_proxy_config()
            if proxy_cfg and proxy_cfg.enabled and proxy_cfg.proxy_url:
                proxy_map = _build_proxy_map(proxy_cfg.proxy_url)
                if proxy_map:
                    return proxy_map, True, "request_proxy", proxy_cfg.proxy_url
    except Exception:
        pass

    return None, False, "none", ""


async def _solve_recaptcha_with_api_service(
    method: str,
    website_url: str,
    website_key: str,
    action: str,
    enterprise: bool = False
) -> Optional[str]:
    """使用当前配置的第三方打码服务获取 token。"""
    if method == "yescaptcha":
        client_key = config.yescaptcha_api_key
        base_url = config.yescaptcha_base_url
        task_type = "RecaptchaV3TaskProxylessM1"
    elif method == "capmonster":
        client_key = config.capmonster_api_key
        base_url = config.capmonster_base_url
        task_type = "RecaptchaV3TaskProxyless"
    elif method == "ezcaptcha":
        client_key = config.ezcaptcha_api_key
        base_url = config.ezcaptcha_base_url
        task_type = "ReCaptchaV3TaskProxylessS9"
    elif method == "capsolver":
        client_key = config.capsolver_api_key
        base_url = config.capsolver_base_url
        task_type = "ReCaptchaV3EnterpriseTaskProxyLess" if enterprise else "ReCaptchaV3TaskProxyLess"
    else:
        raise RuntimeError(f"不支持的打码方式: {method}")

    if not client_key:
        raise RuntimeError(f"{method} API Key 未配置")

    task: Dict[str, Any] = {
        "websiteURL": website_url,
        "websiteKey": website_key,
        "type": task_type,
        "pageAction": action,
    }

    if enterprise and method == "capsolver":
        task["isEnterprise"] = True

    create_url = f"{base_url.rstrip('/')}/createTask"
    get_url = f"{base_url.rstrip('/')}/getTaskResult"

    proxies = None
    try:
        if proxy_manager:
            proxy_cfg = await proxy_manager.get_proxy_config()
            if proxy_cfg and proxy_cfg.enabled and proxy_cfg.proxy_url:
                proxies = _build_proxy_map(proxy_cfg.proxy_url)
    except Exception:
        pass

    # Do not use curl_cffi impersonation for captcha API JSON endpoints: some ASGI servers
    # (for example FastAPI/Uvicorn) may receive an empty body and return 422.
    async with AsyncSession() as session:
        create_resp = await session.post(
            create_url,
            json={"clientKey": client_key, "task": task},
            timeout=30,
            proxies=proxies,
        )
        create_json = create_resp.json()
        task_id = create_json.get("taskId")

        if not task_id:
            error_desc = create_json.get("errorDescription") or create_json.get("errorMessage") or str(create_json)
            raise RuntimeError(f"{method} createTask 失败: {error_desc}")

        for _ in range(40):
            poll_resp = await session.post(
                get_url,
                json={"clientKey": client_key, "taskId": task_id},
                timeout=30,
                proxies=proxies,
            )
            poll_json = poll_resp.json()
            if poll_json.get("status") == "ready":
                solution = poll_json.get("solution", {}) or {}
                token = solution.get("gRecaptchaResponse") or solution.get("token")
                if token:
                    return token
                raise RuntimeError(f"{method} 返回结果缺少 token: {poll_json}")

            if poll_json.get("errorId") not in (None, 0):
                error_desc = poll_json.get("errorDescription") or poll_json.get("errorMessage") or str(poll_json)
                raise RuntimeError(f"{method} getTaskResult 失败: {error_desc}")

            await asyncio.sleep(3)

    raise RuntimeError(f"{method} 获取 token 超时")


async def _score_test_with_remote_browser_service(
    website_url: str,
    website_key: str,
    verify_url: str,
    action: str,
    enterprise: bool = False,
) -> Dict[str, Any]:
    """调用远程有头打码服务执行页面内打码+分数校验。"""
    base_url, api_key, timeout = _get_remote_browser_client_config()
    endpoint = f"{base_url}/api/v1/custom-score"
    request_payload = {
        "website_url": website_url,
        "website_key": website_key,
        "verify_url": verify_url,
        "action": action,
        "enterprise": enterprise,
    }

    status_code, response_payload, response_text = await _sync_json_http_request(
        method="POST",
        url=endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        payload=request_payload,
        timeout=timeout,
    )

    if status_code >= 400:
        detail = ""
        if isinstance(response_payload, dict):
            detail = response_payload.get("detail") or response_payload.get("message") or str(response_payload)
        if not detail:
            detail = (response_text or "").strip()
        raise RuntimeError(f"远程打码服务请求失败 (HTTP {status_code}): {detail or '未知错误'}")

    if not isinstance(response_payload, dict):
        raise RuntimeError("远程打码服务返回格式错误")
    return response_payload


async def _probe_agent_gateway_mode(base_url: str) -> Dict[str, Any]:
    normalized = _normalize_http_base_url(base_url)
    health_url = f"{normalized}/health"
    status_code, response_payload, response_text = await _sync_json_http_request(
        method="GET",
        url=health_url,
        headers={},
        payload=None,
        timeout=8,
    )
    if status_code >= 400:
        detail = ""
        if isinstance(response_payload, dict):
            detail = str(
                response_payload.get("detail")
                or response_payload.get("message")
                or ""
            ).strip()
        if not detail:
            detail = (response_text or "").strip()
        raise RuntimeError(f"HTTP {status_code}{f': {detail}' if detail else ''}")

    if not isinstance(response_payload, dict):
        raise RuntimeError("health 返回格式错误")

    return {
        "ok": bool(response_payload.get("ok")),
        "service": str(response_payload.get("service") or ""),
        "agent_auth_mode": str(response_payload.get("auth_mode") or "").strip().lower(),
        "keygen_verify_mode": str(response_payload.get("verify_mode") or "").strip().lower(),
        "health_body": response_payload,
    }


async def _fetch_agent_gateway_connections(base_url: str, api_key: str) -> Dict[str, Any]:
    normalized = _normalize_http_base_url(base_url)
    endpoint = f"{normalized}/api/v1/agents"
    status_code, response_payload, response_text = await _sync_json_http_request(
        method="GET",
        url=endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        payload=None,
        timeout=8,
    )
    if status_code >= 400:
        detail = ""
        if isinstance(response_payload, dict):
            detail = str(
                response_payload.get("detail")
                or response_payload.get("message")
                or ""
            ).strip()
        if not detail:
            detail = (response_text or "").strip()
        raise RuntimeError(f"HTTP {status_code}{f': {detail}' if detail else ''}")
    if not isinstance(response_payload, dict):
        raise RuntimeError("agents 返回格式错误")
    return response_payload


def _supports_kwarg(fn: Any, kwarg: str) -> bool:
    """Best-effort compatibility helper for rolling upgrades."""
    try:
        return kwarg in inspect.signature(fn).parameters
    except Exception:
        return False


def set_dependencies(
    tm: TokenManager,
    pm: ProxyManager,
    database: Database,
    cm: Optional[ConcurrencyManager] = None,
    akm: Optional[ApiKeyManager] = None,
):
    """Set service instances"""
    global token_manager, proxy_manager, db, concurrency_manager, api_key_manager
    token_manager = tm
    proxy_manager = pm
    db = database
    concurrency_manager = cm
    api_key_manager = akm


# ========== Request Models ==========

class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = True


class AddTokenRequest(BaseModel):
    st: str
    project_id: Optional[str] = None  # 用户可选输入project_id
    project_name: Optional[str] = None
    remark: Optional[str] = None
    captcha_proxy_url: Optional[str] = None
    extension_route_key: Optional[str] = None
    image_enabled: bool = True
    video_enabled: bool = True
    image_concurrency: int = -1
    video_concurrency: int = -1


class UpdateTokenRequest(BaseModel):
    st: str  # Session Token (必填，用于刷新AT)
    project_id: Optional[str] = None  # 用户可选输入project_id
    project_name: Optional[str] = None
    remark: Optional[str] = None
    captcha_proxy_url: Optional[str] = None
    extension_route_key: Optional[str] = None
    image_enabled: Optional[bool] = None
    video_enabled: Optional[bool] = None
    image_concurrency: Optional[int] = None
    video_concurrency: Optional[int] = None


class ProxyConfigRequest(BaseModel):
    proxy_enabled: bool
    proxy_url: Optional[str] = None
    media_proxy_enabled: Optional[bool] = None
    media_proxy_url: Optional[str] = None


class ProxyTestRequest(BaseModel):
    proxy_url: str
    test_url: Optional[str] = "https://labs.google/"
    timeout_seconds: Optional[int] = 15


class CaptchaScoreTestRequest(BaseModel):
    website_url: Optional[str] = "https://antcpt.com/score_detector/"
    website_key: Optional[str] = "6LcR_okUAAAAAPYrPe-HK_0RULO1aZM15ENyM-Mf"
    action: Optional[str] = "homepage"
    verify_url: Optional[str] = "https://antcpt.com/score_detector/verify.php"
    enterprise: Optional[bool] = False


class GenerationConfigRequest(BaseModel):
    image_timeout: Optional[int] = None
    video_timeout: Optional[int] = None
    max_retries: Optional[int] = None
    extension_generation_enabled: Optional[bool] = None
    extension_generation_fallback_mode: Optional[str] = None
    flow2api_gemini_api_keys: Optional[str] = None
    flow2api_openai_api_keys: Optional[str] = None
    flow2api_openrouter_api_keys: Optional[str] = None
    flow2api_third_party_gemini_api_keys: Optional[str] = None
    flow2api_third_party_gemini_base_url: Optional[str] = None
    cloudflare_account_id: Optional[str] = None
    cloudflare_api_token: Optional[str] = None
    flow2api_csvgen_cookie: Optional[str] = None
    flow2api_cloning_model: Optional[str] = None
    flow2api_metadata_backend: Optional[str] = None
    flow2api_metadata_model: Optional[str] = None
    flow2api_metadata_enabled_models: Optional[str] = None
    flow2api_metadata_primary_model: Optional[str] = None
    flow2api_metadata_fallback_models: Optional[str] = None
    metadata_system_prompt: Optional[str] = None
    flow2api_cloning_backend: Optional[str] = None
    flow2api_cloning_gemini_api_keys: Optional[str] = None
    flow2api_cloning_openai_api_keys: Optional[str] = None
    flow2api_cloning_openrouter_api_keys: Optional[str] = None
    flow2api_cloning_third_party_gemini_api_keys: Optional[str] = None
    flow2api_cloning_third_party_gemini_base_url: Optional[str] = None
    flow2api_cloning_cloudflare_account_id: Optional[str] = None
    flow2api_cloning_cloudflare_api_token: Optional[str] = None
    cloning_image_system_prompt: Optional[str] = None
    cloning_video_system_prompt: Optional[str] = None
    task_tracker_device_id: Optional[str] = None
    task_tracker_device_name: Optional[str] = None
    task_tracker_cookies: Optional[str] = None


class CallLogicConfigRequest(BaseModel):
    call_mode: str


class ChangePasswordRequest(BaseModel):
    username: Optional[str] = None
    old_password: str
    new_password: str


class UpdateAPIKeyRequest(BaseModel):
    new_api_key: str


class CreateManagedApiKeyRequest(BaseModel):
    client_name: str
    label: str = "default"
    scopes: str = "*"
    account_ids: List[int]
    endpoint_limits: Dict[str, Dict[str, int]] = {}
    expires_at: Optional[str] = None


class UpdateManagedApiKeyRequest(BaseModel):
    client_name: Optional[str] = None
    label: Optional[str] = None
    is_active: Optional[bool] = None
    scopes: Optional[str] = None
    expires_at: Optional[str] = None
    account_ids: Optional[List[int]] = None
    endpoint_limits: Optional[Dict[str, Dict[str, int]]] = None


class ExtensionWorkerBindRequest(BaseModel):
    route_key: str
    api_key_id: int


class ExtensionWorkerUnbindRequest(BaseModel):
    route_key: str


class ExtensionWorkerKillRequest(BaseModel):
    worker_session_id: str


class DedicatedWorkerCreateRequest(BaseModel):
    label: str = ""
    token_id: Optional[int] = None
    route_key: Optional[str] = None


class DedicatedWorkerUpdateRequest(BaseModel):
    label: Optional[str] = None
    token_id: Optional[int] = None
    route_key: Optional[str] = None
    is_active: Optional[bool] = None


class UpdateDebugConfigRequest(BaseModel):
    enabled: bool


class UpdateAdminConfigRequest(BaseModel):
    error_ban_threshold: int


class ST2ATRequest(BaseModel):
    """ST转AT请求"""
    st: str


class ImportTokenItem(BaseModel):
    """导入Token项"""
    email: Optional[str] = None
    access_token: Optional[str] = None
    session_token: Optional[str] = None
    is_active: bool = True
    captcha_proxy_url: Optional[str] = None
    extension_route_key: Optional[str] = None
    image_enabled: bool = True
    video_enabled: bool = True
    image_concurrency: int = -1
    video_concurrency: int = -1


class ImportTokensRequest(BaseModel):
    """导入Token请求"""
    tokens: List[ImportTokenItem]


# ========== Auth Middleware ==========

async def verify_admin_token(authorization: str = Header(None)):
    """Verify admin session token (NOT API key)"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = authorization[7:]

    if not await db.is_admin_session_valid(token):
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")

    return token


# ========== Auth Endpoints ==========

@router.post("/api/admin/login")
async def admin_login(request: LoginRequest):
    """Admin login - returns session token (NOT API key)"""
    admin_config = await db.get_admin_config()

    if not AuthManager.verify_admin(request.username, request.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate independent session token
    session_token = f"admin-{secrets.token_urlsafe(32)}"

    ttl = _ADMIN_SESSION_TTL_REMEMBER if request.remember_me else _ADMIN_SESSION_TTL_BROWSER
    expires_at = int(time.time()) + ttl
    await db.insert_admin_session(session_token, expires_at)

    return {
        "success": True,
        "token": session_token,  # Session token (NOT API key)
        "username": admin_config.username
    }


@router.post("/api/admin/logout")
async def admin_logout(token: str = Depends(verify_admin_token)):
    """Admin logout - invalidate session token"""
    await db.delete_admin_session(token)
    return {"success": True, "message": "退出登录成功"}


@router.post("/api/admin/change-password")
async def change_password(
    request: ChangePasswordRequest,
    token: str = Depends(verify_admin_token)
):
    """Change admin password"""
    admin_config = await db.get_admin_config()

    # Verify old password
    if not AuthManager.verify_admin(admin_config.username, request.old_password):
        raise HTTPException(status_code=400, detail="旧密码错误")

    # Update password and username in database
    update_params = {"password": request.new_password}
    if request.username:
        update_params["username"] = request.username

    await db.update_admin_config(**update_params)

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()

    # 🔑 Invalidate all admin session tokens (force re-login for security)
    await db.delete_all_admin_sessions()

    return {"success": True, "message": "密码修改成功,请重新登录"}


# ========== Token Management ==========

@router.get("/api/tokens")
async def get_tokens(token: str = Depends(verify_admin_token)):
    """Get all tokens with statistics"""
    token_rows = await db.get_all_tokens_with_stats()
    to_iso = lambda value: value.isoformat() if hasattr(value, "isoformat") else value
    now = datetime.now(timezone.utc)

    def normalize_dt(value):
        if not value:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return [{
        "id": row.get("id"),
        "st": row.get("st"),  # Session Token for editing
        "at": row.get("at"),  # Access Token for editing (从ST转换而来)
        "at_expires": to_iso(row.get("at_expires")) if row.get("at_expires") else None,  # 🆕 AT过期时间
        "at_expired": bool(normalize_dt(row.get("at_expires")) and normalize_dt(row.get("at_expires")) <= now),
        "at_expiring_within_1h": bool(
            normalize_dt(row.get("at_expires"))
            and normalize_dt(row.get("at_expires")) > now
            and (normalize_dt(row.get("at_expires")) - now).total_seconds() < 3600
        ),
        "token": row.get("at"),  # 兼容前端 token.token 的访问方式
        "email": row.get("email"),
        "name": row.get("name"),
        "remark": row.get("remark"),
        "is_active": bool(row.get("is_active")),
        "created_at": to_iso(row.get("created_at")) if row.get("created_at") else None,
        "last_used_at": to_iso(row.get("last_used_at")) if row.get("last_used_at") else None,
        "use_count": row.get("use_count"),
        "credits": row.get("credits"),  # 🆕 余额
        "user_paygate_tier": row.get("user_paygate_tier"),
        "current_project_id": row.get("current_project_id"),  # 🆕 项目ID
        "current_project_name": row.get("current_project_name"),  # 🆕 项目名称
        "captcha_proxy_url": row.get("captcha_proxy_url") or "",
        "extension_route_key": row.get("extension_route_key") or "",
        "image_enabled": bool(row.get("image_enabled")),
        "video_enabled": bool(row.get("video_enabled")),
        "image_concurrency": row.get("image_concurrency"),
        "video_concurrency": row.get("video_concurrency"),
        "image_count": row.get("image_count", 0),
        "video_count": row.get("video_count", 0),
        "error_count": row.get("error_count", 0),
        "today_error_count": row.get("today_error_count", 0),
        "consecutive_error_count": row.get("consecutive_error_count", 0),
        "last_error_at": to_iso(row.get("last_error_at")) if row.get("last_error_at") else None,
        "ban_reason": row.get("ban_reason"),
        "banned_at": to_iso(row.get("banned_at")) if row.get("banned_at") else None,
    } for row in token_rows]  # 直接返回数组,兼容前端


@router.post("/api/tokens")
async def add_token(
    request: AddTokenRequest,
    token: str = Depends(verify_admin_token)
):
    """Add a new token"""
    try:
        add_kwargs: Dict[str, Any] = {
            "st": request.st,
            "project_id": request.project_id,  # 🆕 支持用户指定project_id
            "project_name": request.project_name,
            "remark": request.remark,
            "captcha_proxy_url": request.captcha_proxy_url.strip() if request.captcha_proxy_url is not None else None,
            "image_enabled": request.image_enabled,
            "video_enabled": request.video_enabled,
            "image_concurrency": request.image_concurrency,
            "video_concurrency": request.video_concurrency,
        }
        if _supports_kwarg(token_manager.add_token, "extension_route_key"):
            add_kwargs["extension_route_key"] = (
                request.extension_route_key.strip()
                if request.extension_route_key is not None
                else None
            )
        new_token = await token_manager.add_token(**add_kwargs)

        # 热更新并发限制，避免必须重启服务
        if concurrency_manager:
            await concurrency_manager.reset_token(
                new_token.id,
                image_concurrency=new_token.image_concurrency,
                video_concurrency=new_token.video_concurrency
            )

        return {
            "success": True,
            "message": "Token添加成功",
            "token": {
                "id": new_token.id,
                "email": new_token.email,
                "credits": new_token.credits,
                "project_id": new_token.current_project_id,
                "project_name": new_token.current_project_name
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加Token失败: {str(e)}")


@router.put("/api/tokens/{token_id}")
async def update_token(
    token_id: int,
    request: UpdateTokenRequest,
    token: str = Depends(verify_admin_token)
):
    """Update token - 使用ST自动刷新AT"""
    try:
        # 先ST转AT
        result = await token_manager.flow_client.st_to_at(request.st)
        at = result["access_token"]
        expires = result.get("expires")

        # 解析过期时间
        from datetime import datetime
        at_expires = None
        if expires:
            try:
                at_expires = datetime.fromisoformat(expires.replace('Z', '+00:00'))
            except:
                pass

        # 更新token (包含AT、ST、AT过期时间、project_id和project_name)
        update_kwargs: Dict[str, Any] = {
            "token_id": token_id,
            "st": request.st,
            "at": at,
            "at_expires": at_expires,  # 🆕 更新AT过期时间
            "project_id": request.project_id,
            "project_name": request.project_name,
            "remark": request.remark,
            "captcha_proxy_url": request.captcha_proxy_url.strip() if request.captcha_proxy_url is not None else None,
            "image_enabled": request.image_enabled,
            "video_enabled": request.video_enabled,
            "image_concurrency": request.image_concurrency,
            "video_concurrency": request.video_concurrency,
        }
        if _supports_kwarg(token_manager.update_token, "extension_route_key"):
            update_kwargs["extension_route_key"] = (
                request.extension_route_key.strip()
                if request.extension_route_key is not None
                else None
            )
        await token_manager.update_token(**update_kwargs)

        # 热更新并发限制，确保管理台修改立即生效
        if concurrency_manager:
            updated_token = await token_manager.get_token(token_id)
            if updated_token:
                await concurrency_manager.reset_token(
                    token_id,
                    image_concurrency=updated_token.image_concurrency,
                    video_concurrency=updated_token.video_concurrency
                )

        return {"success": True, "message": "Token更新成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/tokens/{token_id}")
async def delete_token(
    token_id: int,
    token: str = Depends(verify_admin_token)
):
    """Delete token"""
    try:
        await token_manager.delete_token(token_id)
        if concurrency_manager:
            await concurrency_manager.remove_token(token_id)
        return {"success": True, "message": "Token删除成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tokens/{token_id}/enable")
async def enable_token(
    token_id: int,
    token: str = Depends(verify_admin_token)
):
    """Enable token"""
    await token_manager.enable_token(token_id)
    return {"success": True, "message": "Token已启用"}


@router.post("/api/tokens/{token_id}/disable")
async def disable_token(
    token_id: int,
    token: str = Depends(verify_admin_token)
):
    """Disable token"""
    await token_manager.disable_token(token_id)
    return {"success": True, "message": "Token已禁用"}


@router.post("/api/tokens/{token_id}/refresh-credits")
async def refresh_credits(
    token_id: int,
    token: str = Depends(verify_admin_token)
):
    """刷新Token余额 🆕"""
    try:
        credits = await token_manager.refresh_credits(token_id)
        return {
            "success": True,
            "message": "余额刷新成功",
            "credits": credits
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新余额失败: {str(e)}")


@router.post("/api/tokens/{token_id}/refresh-at")
async def refresh_at(
    token_id: int,
    token: str = Depends(verify_admin_token)
):
    """手动刷新Token的AT (使用ST转换) 🆕
    
    如果 AT 刷新失败且处于 personal 模式，会自动尝试通过浏览器刷新 ST
    """
    from ..core.logger import debug_logger
    from ..core.config import config
    from ..services.st_refresh_reasons import describe_st_refresh_reason

    debug_logger.log_info(f"[API] 手动刷新 AT 请求: token_id={token_id}, captcha_method={config.captcha_method}")
    
    try:
        # 调用token_manager的内部刷新方法（包含 ST 自动刷新逻辑）
        success = await token_manager._refresh_at(token_id)
        st_refresh_reason = token_manager.consume_st_refresh_reason(token_id)
        captcha_mode = str(config.captcha_method or "").strip()
        supports_st_refresh = captcha_mode in {"personal", "browser", "extension"} or bool(
            getattr(config, "dedicated_extension_enabled", False)
        )

        if success:
            # 获取更新后的token信息
            updated_token = await token_manager.get_token(token_id)
            
            message = "AT刷新成功"
            if supports_st_refresh:
                message += "（支持ST自动刷新）"
            
            debug_logger.log_info(f"[API] AT 刷新成功: token_id={token_id}")
            
            return {
                "success": True,
                "message": message,
                "token": {
                    "id": updated_token.id,
                    "email": updated_token.email,
                    "at_expires": updated_token.at_expires.isoformat() if updated_token.at_expires else None
                }
            }
        else:
            debug_logger.log_error(f"[API] AT 刷新失败: token_id={token_id}")
            
            error_detail = "AT刷新失败"
            if supports_st_refresh:
                error_detail += (
                    f"（当前打码模式: {captcha_mode or '-'}，已尝试 ST 自动刷新后重试 AT）"
                )
                reason_hint = describe_st_refresh_reason(st_refresh_reason)
                if reason_hint:
                    error_detail += f"；原因: {reason_hint}"
            else:
                error_detail += (
                    f"（当前打码模式: {captcha_mode or '-'}，当前模式未启用 ST 自动刷新能力）"
                )
            if captcha_mode == "browser":
                error_detail += (
                    f"，gateway fallback={'on' if bool(config.browser_fallback_to_remote_browser) else 'off'}"
                )
            
            raise HTTPException(status_code=500, detail=error_detail)
    except HTTPException:
        raise
    except Exception as e:
        debug_logger.log_error(f"[API] 刷新AT异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"刷新AT失败: {str(e)}")


@router.post("/api/tokens/{token_id}/refresh-profile")
async def refresh_token_profile(
    token_id: int,
    request: ST2ATRequest,
    token: str = Depends(verify_admin_token),
):
    """Refresh token email/name using ST -> AT user profile payload."""
    try:
        token_obj = await token_manager.get_token(token_id)
        if not token_obj:
            raise HTTPException(status_code=404, detail="Token not found")

        st_value = (request.st or "").strip()
        if not st_value:
            raise HTTPException(status_code=400, detail="st is required")

        result = await token_manager.flow_client.st_to_at(st_value)
        user_info = result.get("user", {}) if isinstance(result, dict) else {}
        email = str(user_info.get("email", "") or "").strip()
        name = str(user_info.get("name", "") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="Failed to resolve email from session token")
        if not name:
            name = email.split("@")[0] if "@" in email else email

        await db.update_token(token_id, email=email, name=name)

        return {
            "success": True,
            "message": "Token profile refreshed",
            "token": {
                "id": token_id,
                "email": email,
                "name": name,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新邮箱失败: {str(e)}")


def _project_to_dict(project):
    to_iso = lambda value: value.isoformat() if hasattr(value, "isoformat") and value else None
    return {
        "id": project.id,
        "project_id": project.project_id,
        "project_name": project.project_name,
        "token_id": project.token_id,
        "is_active": bool(project.is_active),
        "created_at": to_iso(project.created_at),
    }


@router.get("/api/tokens/{token_id}/projects")
async def list_token_projects(
    token_id: int,
    token: str = Depends(verify_admin_token)
):
    """List VideoFX projects stored for a token."""
    t = await token_manager.get_token(token_id)
    if not t:
        raise HTTPException(status_code=404, detail="Token not found")
    projects = await token_manager.db.get_projects_by_token(token_id)
    return {"success": True, "projects": [_project_to_dict(p) for p in projects]}


@router.post("/api/tokens/{token_id}/projects")
async def create_token_project(
    token_id: int,
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Create a new VideoFX project for an existing token."""
    t = await token_manager.get_token(token_id)
    if not t:
        raise HTTPException(status_code=404, detail="Token not found")

    title = request.get("title")
    if title is not None and not isinstance(title, str):
        raise HTTPException(status_code=400, detail="title must be a string")
    if isinstance(title, str):
        title = title.strip() or None
    set_as_current = request.get("set_as_current", True)
    if not isinstance(set_as_current, bool):
        raise HTTPException(status_code=400, detail="set_as_current must be a boolean")

    try:
        project = await token_manager.create_project_for_token(
            token_id,
            title=title,
            set_as_current=set_as_current,
        )
        updated = await token_manager.get_token(token_id) if set_as_current else None
        return {
            "success": True,
            "message": "Project created",
            "project": _project_to_dict(project),
            "token": (
                {
                    "id": updated.id,
                    "current_project_id": updated.current_project_id,
                    "current_project_name": updated.current_project_name,
                }
                if updated
                else None
            ),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create project failed: {str(e)}")


@router.post("/api/tokens/st2at")
async def st_to_at(
    request: ST2ATRequest,
    token: str = Depends(verify_admin_token)
):
    """Convert Session Token to Access Token (仅转换,不添加到数据库)"""
    try:
        result = await token_manager.flow_client.st_to_at(request.st)
        return {
            "success": True,
            "message": "ST converted to AT successfully",
            "access_token": result["access_token"],
            "email": result.get("user", {}).get("email"),
            "expires": result.get("expires")
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/tokens/import")
async def import_tokens(
    request: ImportTokensRequest,
    token: str = Depends(verify_admin_token)
):
    """批量导入Token"""
    from datetime import datetime, timezone

    added = 0
    updated = 0
    errors = []
    # 保持与历史逻辑一致：按 created_at DESC 的结果中，优先命中同邮箱“最新一条”
    existing_by_email = {}
    for existing_token in await token_manager.get_all_tokens():
        if existing_token.email and existing_token.email not in existing_by_email:
            existing_by_email[existing_token.email] = existing_token

    for idx, item in enumerate(request.tokens):
        try:
            st = item.session_token

            if not st:
                errors.append(f"第{idx+1}项: 缺少 session_token")
                continue

            # 使用 ST 转 AT 获取用户信息
            try:
                result = await token_manager.flow_client.st_to_at(st)
                at = result["access_token"]
                email = result.get("user", {}).get("email")
                expires = result.get("expires")

                if not email:
                    errors.append(f"第{idx+1}项: 无法获取邮箱信息")
                    continue

                # 解析过期时间
                at_expires = None
                is_expired = False
                if expires:
                    try:
                        at_expires = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                        # 判断是否过期
                        now = datetime.now(timezone.utc)
                        is_expired = at_expires <= now
                    except:
                        pass

                # 使用邮箱检查是否已存在
                existing = existing_by_email.get(email)

                if existing:
                    # 更新现有Token
                    import_update_kwargs: Dict[str, Any] = {
                        "token_id": existing.id,
                        "st": st,
                        "at": at,
                        "at_expires": at_expires,
                        "captcha_proxy_url": item.captcha_proxy_url.strip() if item.captcha_proxy_url is not None else None,
                        "image_enabled": item.image_enabled,
                        "video_enabled": item.video_enabled,
                        "image_concurrency": item.image_concurrency,
                        "video_concurrency": item.video_concurrency,
                    }
                    if _supports_kwarg(token_manager.update_token, "extension_route_key"):
                        import_update_kwargs["extension_route_key"] = (
                            item.extension_route_key.strip()
                            if item.extension_route_key is not None
                            else None
                        )
                    await token_manager.update_token(**import_update_kwargs)
                    # 如果过期则禁用
                    if is_expired:
                        await token_manager.disable_token(existing.id)
                        existing.is_active = False
                    existing.st = st
                    existing.at = at
                    existing.at_expires = at_expires
                    existing.captcha_proxy_url = item.captcha_proxy_url
                    if hasattr(existing, "extension_route_key"):
                        existing.extension_route_key = item.extension_route_key
                    existing.image_enabled = item.image_enabled
                    existing.video_enabled = item.video_enabled
                    existing.image_concurrency = item.image_concurrency
                    existing.video_concurrency = item.video_concurrency
                    updated += 1
                else:
                    # 添加新Token
                    import_add_kwargs: Dict[str, Any] = {
                        "st": st,
                        "captcha_proxy_url": item.captcha_proxy_url.strip() if item.captcha_proxy_url is not None else None,
                        "image_enabled": item.image_enabled,
                        "video_enabled": item.video_enabled,
                        "image_concurrency": item.image_concurrency,
                        "video_concurrency": item.video_concurrency,
                    }
                    if _supports_kwarg(token_manager.add_token, "extension_route_key"):
                        import_add_kwargs["extension_route_key"] = (
                            item.extension_route_key.strip()
                            if item.extension_route_key is not None
                            else None
                        )
                    new_token = await token_manager.add_token(**import_add_kwargs)
                    # 如果过期则禁用
                    if is_expired:
                        await token_manager.disable_token(new_token.id)
                        new_token.is_active = False
                    existing_by_email[email] = new_token
                    added += 1

            except Exception as e:
                errors.append(f"第{idx+1}项: {str(e)}")

        except Exception as e:
            errors.append(f"第{idx+1}项: {str(e)}")

    return {
        "success": True,
        "added": added,
        "updated": updated,
        "errors": errors if errors else None,
        "message": f"导入完成: 新增 {added} 个, 更新 {updated} 个" + (f", {len(errors)} 个失败" if errors else "")
    }


# ========== Config Management ==========

@router.get("/api/config/proxy")
async def get_proxy_config(token: str = Depends(verify_admin_token)):
    """Get proxy configuration"""
    config = await proxy_manager.get_proxy_config()
    return {
        "success": True,
        "config": {
            "enabled": config.enabled,
            "proxy_url": config.proxy_url,
            "media_proxy_enabled": config.media_proxy_enabled,
            "media_proxy_url": config.media_proxy_url
        }
    }


@router.get("/api/proxy/config")
async def get_proxy_config_alias(token: str = Depends(verify_admin_token)):
    """Get proxy configuration (alias for frontend compatibility)"""
    config = await proxy_manager.get_proxy_config()
    return {
        "proxy_enabled": config.enabled,  # Frontend expects proxy_enabled
        "proxy_url": config.proxy_url,
        "media_proxy_enabled": config.media_proxy_enabled,
        "media_proxy_url": config.media_proxy_url
    }


@router.post("/api/proxy/config")
async def update_proxy_config_alias(
    request: ProxyConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update proxy configuration (alias for frontend compatibility)"""
    try:
        await proxy_manager.update_proxy_config(
            enabled=request.proxy_enabled,
            proxy_url=request.proxy_url,
            media_proxy_enabled=request.media_proxy_enabled,
            media_proxy_url=request.media_proxy_url
        )
    except ValueError as e:
        return {"success": False, "message": str(e)}
    return {"success": True, "message": "代理配置更新成功"}


@router.post("/api/config/proxy")
async def update_proxy_config(
    request: ProxyConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update proxy configuration"""
    try:
        await proxy_manager.update_proxy_config(
            enabled=request.proxy_enabled,
            proxy_url=request.proxy_url,
            media_proxy_enabled=request.media_proxy_enabled,
            media_proxy_url=request.media_proxy_url
        )
    except ValueError as e:
        return {"success": False, "message": str(e)}
    return {"success": True, "message": "代理配置更新成功"}


@router.post("/api/proxy/test")
async def test_proxy_connectivity(
    request: ProxyTestRequest,
    token: str = Depends(verify_admin_token)
):
    """测试代理是否可访问目标站点（默认 https://labs.google/）"""
    proxy_input = (request.proxy_url or "").strip()
    test_url = (request.test_url or "https://labs.google/").strip()
    timeout_seconds = int(request.timeout_seconds or 15)
    timeout_seconds = max(5, min(timeout_seconds, 60))

    if not proxy_input:
        return {
            "success": False,
            "message": "代理地址为空",
            "test_url": test_url
        }

    try:
        proxy_url = proxy_manager.normalize_proxy_url(proxy_input)
    except ValueError as e:
        return {
            "success": False,
            "message": str(e),
            "test_url": test_url
        }

    start_time = time.time()
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        async with AsyncSession() as session:
            resp = await session.get(
                test_url,
                proxies=proxies,
                timeout=timeout_seconds,
                impersonate="chrome120",
                allow_redirects=True,
                verify=False
            )

        elapsed_ms = int((time.time() - start_time) * 1000)
        status_code = resp.status_code
        final_url = str(resp.url)
        ok = 200 <= status_code < 400

        return {
            "success": ok,
            "message": "代理可用" if ok else f"代理可连通，但目标返回状态码 {status_code}",
            "test_url": test_url,
            "final_url": final_url,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms
        }
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "success": False,
            "message": f"代理测试失败: {str(e)}",
            "test_url": test_url,
            "elapsed_ms": elapsed_ms
        }


@router.get("/api/config/generation")
async def get_generation_config(token: str = Depends(verify_admin_token)):
    """Get generation timeout configuration"""
    config = await db.get_generation_config()
    if config is None:
        config = GenerationConfig()
    return {
        "success": True,
        "config": {
            "image_timeout": config.image_timeout,
            "video_timeout": config.video_timeout,
            "max_retries": config.max_retries,
            "extension_generation_enabled": bool(
                getattr(config, "extension_generation_enabled", False)
            ),
            "extension_generation_fallback_mode": str(
                getattr(config, "extension_generation_fallback_mode", "local_http_on_recaptcha")
                or "local_http_on_recaptcha"
            ),
            "flow2api_gemini_api_keys": str(getattr(config, "flow2api_gemini_api_keys", "") or ""),
            "flow2api_openai_api_keys": str(getattr(config, "flow2api_openai_api_keys", "") or ""),
            "flow2api_openrouter_api_keys": str(getattr(config, "flow2api_openrouter_api_keys", "") or ""),
            "flow2api_third_party_gemini_api_keys": str(
                getattr(config, "flow2api_third_party_gemini_api_keys", "") or ""
            ),
            "flow2api_third_party_gemini_base_url": str(
                getattr(config, "flow2api_third_party_gemini_base_url", "") or ""
            ),
            "cloudflare_account_id": str(getattr(config, "cloudflare_account_id", "") or ""),
            "cloudflare_api_token": str(getattr(config, "cloudflare_api_token", "") or ""),
            "flow2api_csvgen_cookie": str(getattr(config, "flow2api_csvgen_cookie", "") or ""),
            "flow2api_cloning_model": str(
                getattr(config, "flow2api_cloning_model", "gemini-2.5-flash")
                or "gemini-2.5-flash"
            ),
            "flow2api_cloning_backend": str(
                getattr(config, "flow2api_cloning_backend", "gemini_native") or "gemini_native"
            ),
            "flow2api_cloning_gemini_api_keys": str(
                getattr(config, "flow2api_cloning_gemini_api_keys", "") or ""
            ),
            "flow2api_cloning_openai_api_keys": str(
                getattr(config, "flow2api_cloning_openai_api_keys", "") or ""
            ),
            "flow2api_cloning_openrouter_api_keys": str(
                getattr(config, "flow2api_cloning_openrouter_api_keys", "") or ""
            ),
            "flow2api_cloning_third_party_gemini_api_keys": str(
                getattr(config, "flow2api_cloning_third_party_gemini_api_keys", "") or ""
            ),
            "flow2api_cloning_third_party_gemini_base_url": str(
                getattr(config, "flow2api_cloning_third_party_gemini_base_url", "") or ""
            ),
            "flow2api_cloning_cloudflare_account_id": str(
                getattr(config, "flow2api_cloning_cloudflare_account_id", "") or ""
            ),
            "flow2api_cloning_cloudflare_api_token": str(
                getattr(config, "flow2api_cloning_cloudflare_api_token", "") or ""
            ),
            "flow2api_metadata_backend": str(
                getattr(config, "flow2api_metadata_backend", "gemini_native")
                or "gemini_native"
            ),
            "flow2api_metadata_model": str(
                getattr(config, "flow2api_metadata_model", "gemini-2.5-flash")
                or "gemini-2.5-flash"
            ),
            "flow2api_metadata_enabled_models": str(
                getattr(config, "flow2api_metadata_enabled_models", "") or ""
            ),
            "flow2api_metadata_primary_model": str(
                getattr(config, "flow2api_metadata_primary_model", "") or ""
            ),
            "flow2api_metadata_fallback_models": str(
                getattr(config, "flow2api_metadata_fallback_models", "") or ""
            ),
            "metadata_system_prompt": str(getattr(config, "metadata_system_prompt", "") or ""),
            "cloning_image_system_prompt": str(
                getattr(config, "cloning_image_system_prompt", "") or ""
            ),
            "cloning_video_system_prompt": str(
                getattr(config, "cloning_video_system_prompt", "") or ""
            ),
            "task_tracker_device_id": str(getattr(config, "task_tracker_device_id", "") or ""),
            "task_tracker_device_name": str(getattr(config, "task_tracker_device_name", "") or ""),
            "task_tracker_cookies": str(getattr(config, "task_tracker_cookies", "") or ""),
        }
    }


@router.post("/api/config/generation")
async def update_generation_config(
    request: GenerationConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update generation timeout configuration"""
    captcha_cfg = await db.get_captcha_config()
    extension_mode_active = str(getattr(captcha_cfg, "captcha_method", "") or "").strip().lower() == "extension"
    extension_generation_enabled = bool(request.extension_generation_enabled) if extension_mode_active else False
    extension_generation_fallback_mode = (
        request.extension_generation_fallback_mode if extension_mode_active else "none"
    )
    await db.update_generation_config(
        image_timeout=request.image_timeout,
        video_timeout=request.video_timeout,
        max_retries=request.max_retries,
        extension_generation_enabled=extension_generation_enabled,
        extension_generation_fallback_mode=extension_generation_fallback_mode,
        flow2api_gemini_api_keys=request.flow2api_gemini_api_keys,
        flow2api_openai_api_keys=request.flow2api_openai_api_keys,
        flow2api_openrouter_api_keys=request.flow2api_openrouter_api_keys,
        flow2api_third_party_gemini_api_keys=request.flow2api_third_party_gemini_api_keys,
        flow2api_third_party_gemini_base_url=request.flow2api_third_party_gemini_base_url,
        cloudflare_account_id=request.cloudflare_account_id,
        cloudflare_api_token=request.cloudflare_api_token,
        flow2api_csvgen_cookie=request.flow2api_csvgen_cookie,
        flow2api_cloning_model=request.flow2api_cloning_model,
        flow2api_cloning_backend=request.flow2api_cloning_backend,
        flow2api_cloning_gemini_api_keys=request.flow2api_cloning_gemini_api_keys,
        flow2api_cloning_openai_api_keys=request.flow2api_cloning_openai_api_keys,
        flow2api_cloning_openrouter_api_keys=request.flow2api_cloning_openrouter_api_keys,
        flow2api_cloning_third_party_gemini_api_keys=request.flow2api_cloning_third_party_gemini_api_keys,
        flow2api_cloning_third_party_gemini_base_url=request.flow2api_cloning_third_party_gemini_base_url,
        flow2api_cloning_cloudflare_account_id=request.flow2api_cloning_cloudflare_account_id,
        flow2api_cloning_cloudflare_api_token=request.flow2api_cloning_cloudflare_api_token,
        flow2api_metadata_backend=request.flow2api_metadata_backend,
        flow2api_metadata_model=request.flow2api_metadata_model,
        flow2api_metadata_enabled_models=request.flow2api_metadata_enabled_models,
        flow2api_metadata_primary_model=request.flow2api_metadata_primary_model,
        flow2api_metadata_fallback_models=request.flow2api_metadata_fallback_models,
        metadata_system_prompt=request.metadata_system_prompt,
        cloning_image_system_prompt=request.cloning_image_system_prompt,
        cloning_video_system_prompt=request.cloning_video_system_prompt,
    )

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()

    return {"success": True, "message": "生成配置更新成功"}


@router.get("/api/call-logic/config")
async def get_call_logic_config(token: str = Depends(verify_admin_token)):
    """Get token call logic configuration."""
    config_obj = await db.get_call_logic_config()
    call_mode = getattr(config_obj, "call_mode", None)
    if call_mode not in ("default", "polling"):
        call_mode = "polling" if getattr(config_obj, "polling_mode_enabled", False) else "default"
    return {
        "success": True,
        "config": {
            "call_mode": call_mode,
            "polling_mode_enabled": call_mode == "polling",
        }
    }


@router.post("/api/call-logic/config")
async def update_call_logic_config(
    request: CallLogicConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update token call logic configuration."""
    call_mode = request.call_mode if request.call_mode in ("default", "polling") else None
    if call_mode is None:
        raise HTTPException(status_code=400, detail="Invalid call_mode")

    await db.update_call_logic_config(call_mode)
    await db.reload_config_to_memory()

    return {
        "success": True,
        "message": "Token轮询模式保存成功",
        "config": {
            "call_mode": call_mode,
            "polling_mode_enabled": call_mode == "polling",
        }
    }


# ========== System Info ==========

@router.get("/api/system/info")
async def get_system_info(token: str = Depends(verify_admin_token)):
    """Get system information"""
    stats = await db.get_system_info_stats()

    return {
        "success": True,
        "info": {
            "total_tokens": stats["total_tokens"],
            "active_tokens": stats["active_tokens"],
            "total_credits": stats["total_credits"],
            "version": "1.0.0"
        }
    }


# ========== Additional Routes for Frontend Compatibility ==========

@router.post("/api/login")
async def login(request: LoginRequest):
    """Login endpoint (alias for /api/admin/login)"""
    return await admin_login(request)


@router.post("/api/logout")
async def logout(token: str = Depends(verify_admin_token)):
    """Logout endpoint (alias for /api/admin/logout)"""
    return await admin_logout(token)


@router.get("/health")
async def health_check():
    """Public health check endpoint - no auth required"""
    try:
        return await build_public_health_snapshot(db)
    except Exception:
        return {"backend_running": True, "has_active_tokens": False}


@router.get("/api/stats")
async def get_stats(token: str = Depends(verify_admin_token)):
    """Get statistics for dashboard"""
    return await db.get_dashboard_stats()


@router.get("/api/logs")
async def get_logs(
    limit: int = 50,
    offset: int = 0,
    token: str = Depends(verify_admin_token)
):
    """Get lightweight request logs for list view (paginated)."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total = await db.count_request_logs()
    logs = await db.get_logs(limit=limit, offset=offset, include_payload=False)

    result = []
    for log in logs:
        raw_status_code = log.get("status_code")
        try:
            status_code = int(raw_status_code) if raw_status_code is not None else None
        except (TypeError, ValueError):
            status_code = None
        result.append({
            "id": log.get("id"),
            "token_id": log.get("token_id"),
            "token_email": log.get("token_email"),
            "token_username": log.get("token_username"),
            "operation": log.get("operation"),
            "status_code": status_code if status_code is not None else raw_status_code,
            "duration": log.get("duration"),
            "status_text": log.get("status_text") or "",
            "progress": log.get("progress") or 0,
            "created_at": log.get("created_at"),
            "updated_at": log.get("updated_at"),
            "error_summary": _extract_error_summary(log.get("response_body_excerpt")) if status_code is not None and status_code >= 400 else "",
        })
    return {"logs": result, "total": total, "limit": limit, "offset": offset}


@router.get("/api/logs/{log_id}")
async def get_log_detail(
    log_id: int,
    token: str = Depends(verify_admin_token)
):
    """Get single request log detail (payload loaded on demand)"""
    log = await db.get_log_detail(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")

    error_summary = _extract_error_summary(log.get("response_body"))

    return {
        "id": log.get("id"),
        "token_id": log.get("token_id"),
        "token_email": log.get("token_email"),
        "token_username": log.get("token_username"),
        "operation": log.get("operation"),
        "status_code": log.get("status_code"),
        "duration": log.get("duration"),
        "status_text": log.get("status_text") or "",
        "progress": log.get("progress") or 0,
        "created_at": log.get("created_at"),
        "updated_at": log.get("updated_at"),
        "error_summary": error_summary,
        "request_body": log.get("request_body"),
        "response_body": log.get("response_body")
    }


@router.delete("/api/logs")
async def clear_logs(token: str = Depends(verify_admin_token)):
    """Clear all logs"""
    try:
        await db.clear_all_logs()
        return {"success": True, "message": "所有日志已清空"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/config")
async def get_admin_config(token: str = Depends(verify_admin_token)):
    """Get admin configuration"""
    admin_config = await db.get_admin_config()

    return {
        "admin_username": admin_config.username,
        "api_key": admin_config.api_key,
        "error_ban_threshold": admin_config.error_ban_threshold,
        "debug_enabled": config.debug_enabled  # Return actual debug status
    }


@router.post("/api/admin/config")
async def update_admin_config(
    request: UpdateAdminConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update admin configuration (error_ban_threshold)"""
    # Update error_ban_threshold in database
    await db.update_admin_config(error_ban_threshold=request.error_ban_threshold)

    return {"success": True, "message": "配置更新成功"}


@router.post("/api/admin/password")
async def update_admin_password(
    request: ChangePasswordRequest,
    token: str = Depends(verify_admin_token)
):
    """Update admin password"""
    return await change_password(request, token)


@router.post("/api/admin/apikey")
async def update_api_key(
    request: UpdateAPIKeyRequest,
    token: str = Depends(verify_admin_token)
):
    """Update API key (for external API calls, NOT for admin login)"""
    # Update API key in database
    await db.update_admin_config(api_key=request.new_api_key)

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()

    return {"success": True, "message": "API Key更新成功"}


@router.get("/api/admin/managed-apikeys")
async def list_managed_api_keys(token: str = Depends(verify_admin_token)):
    if not api_key_manager:
        raise HTTPException(status_code=503, detail="API key manager not initialized")
    keys = await db.list_api_keys()
    return {"success": True, "keys": keys}


@router.post("/api/admin/managed-apikeys")
async def create_managed_api_key(
    request: CreateManagedApiKeyRequest,
    token: str = Depends(verify_admin_token),
):
    if not api_key_manager:
        raise HTTPException(status_code=503, detail="API key manager not initialized")
    if not request.account_ids:
        raise HTTPException(status_code=400, detail="account_ids cannot be empty")
    created = await api_key_manager.create_api_key(
        client_name=request.client_name,
        label=request.label,
        scopes=request.scopes,
        account_ids=request.account_ids,
        endpoint_limits=request.endpoint_limits or {},
        expires_at=request.expires_at,
    )
    return {
        "success": True,
        "message": "Managed API key created",
        "key": created,
        "warning": "Store api_key now. It is shown only once.",
    }


@router.put("/api/admin/managed-apikeys/{key_id}")
async def update_managed_api_key(
    key_id: int,
    request: UpdateManagedApiKeyRequest,
    token: str = Depends(verify_admin_token),
):
    # Keep account mappings clean even when account_ids are not edited.
    await db.prune_stale_api_key_accounts(key_id)

    valid_account_ids = request.account_ids
    if request.account_ids is not None:
        cleaned_ids = sorted({int(x) for x in request.account_ids if int(x) > 0})
        if cleaned_ids:
            missing_ids = []
            for account_id in cleaned_ids:
                token_obj = await db.get_token(account_id)
                if token_obj is None:
                    missing_ids.append(account_id)
            if missing_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Some account_ids do not exist: {missing_ids}",
                )
        valid_account_ids = cleaned_ids

    await db.update_api_key(
        key_id,
        client_name=request.client_name,
        label=request.label,
        is_active=request.is_active,
        scopes=request.scopes,
        expires_at=request.expires_at,
        account_ids=valid_account_ids,
        endpoint_limits=request.endpoint_limits,
    )
    return {"success": True, "message": "Managed API key updated"}


@router.get("/api/admin/managed-apikeys/audit")
async def list_managed_api_key_audit(
    key_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    token: str = Depends(verify_admin_token),
):
    """Must be registered before /managed-apikeys/{key_id} or 'audit' is parsed as key_id (422)."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total = await db.count_api_key_audit_logs(key_id=key_id)
    logs = await db.list_api_key_audit_logs(limit=limit, offset=offset, key_id=key_id)
    return {"success": True, "logs": logs, "total": total, "limit": limit, "offset": offset}


@router.get("/api/admin/managed-apikeys/{key_id}/projects")
async def list_managed_api_key_projects(
    key_id: int,
    limit: int = 10,
    offset: int = 0,
    token: str = Depends(verify_admin_token),
):
    """Paginated VideoFX projects scoped to a managed API key + per-account current project cursors."""
    detail = await db.get_api_key_detail(key_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Managed API key not found")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total = await db.count_projects_by_api_key(key_id)
    projects = await db.list_projects_by_api_key(key_id, limit=limit, offset=offset)
    projects_by_token: dict[int, list[Any]] = {}
    for p in projects:
        try:
            token_id = int(getattr(p, "token_id", 0) or 0)
        except Exception:
            token_id = 0
        if token_id <= 0:
            continue
        projects_by_token.setdefault(token_id, []).append(p)

    accounts_out = []
    active_by_token: dict[int, str] = {}
    for account_id in await db.get_api_key_account_ids(key_id):
        t = await token_manager.get_token(account_id)
        if t:
            token_scoped_projects = projects_by_token.get(int(t.id), [])
            active_project = next((proj for proj in token_scoped_projects if bool(getattr(proj, "is_active", False))), None)
            active_pid = str(active_project.project_id) if active_project and active_project.project_id else None
            active_name = str(active_project.project_name) if active_project and active_project.project_name else None
            if active_pid:
                active_by_token[int(t.id)] = active_pid
            accounts_out.append(
                {
                    "token_id": t.id,
                    "email": t.email or None,
                    "active_project_id": active_pid,
                    "active_project_name": active_name,
                    "current_project_id": active_pid,
                    "current_project_name": active_name,
                }
            )
        else:
            accounts_out.append(
                {
                    "token_id": account_id,
                    "email": None,
                    "active_project_id": None,
                    "active_project_name": None,
                    "current_project_id": None,
                    "current_project_name": None,
                }
            )

    projects_out = []
    for p in projects:
        pd = _project_to_dict(p)
        tid_raw = pd.get("token_id")
        tid = int(tid_raw) if isinstance(tid_raw, int) else None
        is_current_for_token = bool(
            tid is not None and active_by_token.get(tid) == str(pd.get("project_id") or "")
        )
        pd["is_current_for_token"] = is_current_for_token
        pd["project_status"] = "active" if is_current_for_token else "old"
        projects_out.append(pd)

    return {
        "success": True,
        "projects": projects_out,
        "total": total,
        "limit": limit,
        "offset": offset,
        "accounts": accounts_out,
    }


@router.post("/api/admin/managed-apikeys/{key_id}/projects")
async def create_managed_api_key_project(
    key_id: int,
    request: dict,
    token: str = Depends(verify_admin_token),
):
    """Create a VideoFX project for a token assigned to this managed key; tags projects.api_key_id."""
    detail = await db.get_api_key_detail(key_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Managed API key not found")

    raw_tid = request.get("token_id")
    if raw_tid is None:
        raise HTTPException(status_code=400, detail="token_id is required")
    try:
        token_id = int(raw_tid)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="token_id must be an integer")
    allowed = set(await db.get_api_key_account_ids(key_id))
    if token_id not in allowed:
        raise HTTPException(status_code=400, detail="token_id is not assigned to this API key")

    t = await token_manager.get_token(token_id)
    if not t:
        raise HTTPException(status_code=404, detail="Token not found")

    title = request.get("title")
    if title is not None and not isinstance(title, str):
        raise HTTPException(status_code=400, detail="title must be a string")
    if isinstance(title, str):
        title = title.strip() or None
    set_as_current = request.get("set_as_current", True)
    if not isinstance(set_as_current, bool):
        raise HTTPException(status_code=400, detail="set_as_current must be a boolean")

    try:
        project = await token_manager.create_project_for_token(
            token_id,
            title=title,
            set_as_current=set_as_current,
            api_key_id=key_id,
        )
        updated = await token_manager.get_token(token_id) if set_as_current else None
        return {
            "success": True,
            "message": "Project created",
            "project": _project_to_dict(project),
            "token": (
                {
                    "id": updated.id,
                    "current_project_id": updated.current_project_id,
                    "current_project_name": updated.current_project_name,
                }
                if updated
                else None
            ),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create project failed: {str(e)}")


@router.get("/api/admin/managed-apikeys/{key_id}")
async def get_managed_api_key(
    key_id: int,
    reveal_plaintext: bool = False,
    token: str = Depends(verify_admin_token),
):
    detail = await db.get_api_key_detail(key_id, include_plaintext=reveal_plaintext)
    if not detail:
        raise HTTPException(status_code=404, detail="Managed API key not found")
    return {"success": True, "key": detail}


@router.delete("/api/admin/managed-apikeys/{key_id}")
async def delete_managed_api_key(
    key_id: int,
    token: str = Depends(verify_admin_token),
):
    detail = await db.get_api_key_detail(key_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Managed API key not found")
    await db.delete_api_key(key_id)
    return {"success": True, "message": "Managed API key deleted"}


@router.get("/api/admin/extension/workers")
async def list_extension_workers(token: str = Depends(verify_admin_token)):
    from ..services.browser_captcha_extension import ExtensionCaptchaService

    service = await ExtensionCaptchaService.get_instance(db=db)
    active_workers = await service.list_active_workers()
    bindings = await db.list_extension_worker_bindings()
    queue_stats = service.get_queue_stats()
    return {
        "success": True,
        "mode": "managed_key_primary_with_dedicated_fallback",
        "note": "Requests prefer workers bound to the same managed API key; dedicated worker-mode connections can be used as token-bound fallback when managed-key binding is absent.",
        "workers": active_workers,
        "bindings": bindings,
        "queue_stats": queue_stats,
    }


@router.post("/api/admin/extension/workers/bind")
async def bind_extension_worker(
    request: ExtensionWorkerBindRequest,
    token: str = Depends(verify_admin_token),
):
    from ..services.browser_captcha_extension import ExtensionCaptchaService

    route_key = (request.route_key or "").strip()
    if not route_key:
        raise HTTPException(status_code=400, detail="route_key is required")
    detail = await db.get_api_key_detail(int(request.api_key_id))
    if not detail:
        raise HTTPException(status_code=404, detail="Managed API key not found")
    service = await ExtensionCaptchaService.get_instance(db=db)
    await service.bind_route_key(route_key, int(request.api_key_id))
    return {
        "success": True,
        "message": "Worker binding updated. Requests for this managed key now require this worker route.",
    }


@router.post("/api/admin/extension/workers/unbind")
async def unbind_extension_worker(
    request: ExtensionWorkerUnbindRequest,
    token: str = Depends(verify_admin_token),
):
    from ..services.browser_captcha_extension import ExtensionCaptchaService

    route_key = (request.route_key or "").strip()
    if not route_key:
        raise HTTPException(status_code=400, detail="route_key is required")
    service = await ExtensionCaptchaService.get_instance(db=db)
    await service.unbind_route_key(route_key)
    return {
        "success": True,
        "message": "Worker binding removed. Requests for this route will fail until a matching worker binds again.",
    }


@router.post("/api/admin/extension/workers/kill")
async def kill_extension_worker(
    request: ExtensionWorkerKillRequest,
    token: str = Depends(verify_admin_token),
):
    from ..services.browser_captcha_extension import ExtensionCaptchaService

    worker_session_id = (request.worker_session_id or "").strip()
    if not worker_session_id:
        raise HTTPException(status_code=400, detail="worker_session_id is required")
    service = await ExtensionCaptchaService.get_instance(db=db)
    killed = await service.kill_worker(worker_session_id)
    if not killed:
        return {"success": False, "message": "Worker not found"}
    return {"success": True, "message": "Worker terminated", "worker_session_id": worker_session_id}


@router.get("/api/admin/dedicated-extension/workers")
async def list_dedicated_extension_workers(token: str = Depends(verify_admin_token)):
    workers = await db.list_dedicated_extension_workers()
    return {"success": True, "workers": workers}


@router.post("/api/admin/dedicated-extension/workers")
async def create_dedicated_extension_worker(
    request: DedicatedWorkerCreateRequest,
    token: str = Depends(verify_admin_token),
):
    token_id = int(request.token_id) if request.token_id is not None else None
    if token_id is not None:
        existing_token = await db.get_token(token_id)
        if not existing_token:
            raise HTTPException(status_code=404, detail="Token not found")
    worker_key = _generate_worker_registration_key()
    worker_id = await db.create_dedicated_extension_worker(
        worker_key_prefix=_worker_key_prefix(worker_key),
        worker_key_hash=_hash_worker_registration_key(worker_key),
        label=(request.label or "").strip(),
        token_id=token_id,
        route_key=(request.route_key or "").strip() or None,
    )
    worker = await db.get_dedicated_extension_worker(worker_id)
    return {"success": True, "worker": worker, "worker_registration_key": worker_key}


@router.patch("/api/admin/dedicated-extension/workers/{worker_id}")
async def update_dedicated_extension_worker(
    worker_id: int,
    request: DedicatedWorkerUpdateRequest,
    token: str = Depends(verify_admin_token),
):
    existing = await db.get_dedicated_extension_worker(worker_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Dedicated worker not found")
    if request.token_id is not None:
        token_obj = await db.get_token(int(request.token_id))
        if not token_obj:
            raise HTTPException(status_code=404, detail="Token not found")
    await db.update_dedicated_extension_worker(
        worker_id,
        label=request.label,
        token_id=request.token_id,
        route_key=request.route_key,
        is_active=request.is_active,
    )
    updated = await db.get_dedicated_extension_worker(worker_id)
    return {"success": True, "worker": updated}


@router.post("/api/admin/dedicated-extension/workers/{worker_id}/unbind")
async def unbind_dedicated_extension_worker(
    worker_id: int,
    token: str = Depends(verify_admin_token),
):
    existing = await db.get_dedicated_extension_worker(worker_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Dedicated worker not found")
    await db.update_dedicated_extension_worker(worker_id, clear_token_binding=True)
    updated = await db.get_dedicated_extension_worker(worker_id)
    return {"success": True, "worker": updated}


@router.delete("/api/admin/dedicated-extension/workers/{worker_id}")
async def delete_dedicated_extension_worker(
    worker_id: int,
    token: str = Depends(verify_admin_token),
):
    existing = await db.get_dedicated_extension_worker(worker_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Dedicated worker not found")
    await db.delete_dedicated_extension_worker(worker_id)
    return {"success": True, "worker_id": worker_id}


@router.post("/api/admin/debug")
async def update_debug_config(
    request: UpdateDebugConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update debug configuration"""
    try:
        # Persist to database so value survives restart/rebuild.
        await db.update_debug_config(enabled=request.enabled)
        # Hot reload updated value into runtime config.
        await db.reload_config_to_memory()

        status = "enabled" if request.enabled else "disabled"
        return {"success": True, "message": f"Debug mode {status}", "enabled": request.enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update debug config: {str(e)}")


@router.get("/api/generation/timeout")
async def get_generation_timeout(token: str = Depends(verify_admin_token)):
    """Get generation timeout configuration"""
    return await get_generation_config(token)


@router.post("/api/generation/timeout")
async def update_generation_timeout(
    request: GenerationConfigRequest,
    token: str = Depends(verify_admin_token)
):
    """Update generation timeout configuration"""
    captcha_cfg = await db.get_captcha_config()
    extension_mode_active = str(getattr(captcha_cfg, "captcha_method", "") or "").strip().lower() == "extension"
    extension_generation_enabled = bool(request.extension_generation_enabled) if extension_mode_active else False
    extension_generation_fallback_mode = (
        request.extension_generation_fallback_mode if extension_mode_active else "none"
    )
    await db.update_generation_config(
        image_timeout=request.image_timeout,
        video_timeout=request.video_timeout,
        max_retries=request.max_retries,
        extension_generation_enabled=extension_generation_enabled,
        extension_generation_fallback_mode=extension_generation_fallback_mode,
        flow2api_gemini_api_keys=request.flow2api_gemini_api_keys,
        flow2api_openai_api_keys=request.flow2api_openai_api_keys,
        flow2api_openrouter_api_keys=request.flow2api_openrouter_api_keys,
        flow2api_third_party_gemini_api_keys=request.flow2api_third_party_gemini_api_keys,
        flow2api_third_party_gemini_base_url=request.flow2api_third_party_gemini_base_url,
        cloudflare_account_id=request.cloudflare_account_id,
        cloudflare_api_token=request.cloudflare_api_token,
        flow2api_csvgen_cookie=request.flow2api_csvgen_cookie,
        flow2api_cloning_model=request.flow2api_cloning_model,
        flow2api_cloning_backend=request.flow2api_cloning_backend,
        flow2api_cloning_gemini_api_keys=request.flow2api_cloning_gemini_api_keys,
        flow2api_cloning_openai_api_keys=request.flow2api_cloning_openai_api_keys,
        flow2api_cloning_openrouter_api_keys=request.flow2api_cloning_openrouter_api_keys,
        flow2api_cloning_third_party_gemini_api_keys=request.flow2api_cloning_third_party_gemini_api_keys,
        flow2api_cloning_third_party_gemini_base_url=request.flow2api_cloning_third_party_gemini_base_url,
        flow2api_cloning_cloudflare_account_id=request.flow2api_cloning_cloudflare_account_id,
        flow2api_cloning_cloudflare_api_token=request.flow2api_cloning_cloudflare_api_token,
        flow2api_metadata_backend=request.flow2api_metadata_backend,
        flow2api_metadata_model=request.flow2api_metadata_model,
        flow2api_metadata_enabled_models=request.flow2api_metadata_enabled_models,
        flow2api_metadata_primary_model=request.flow2api_metadata_primary_model,
        flow2api_metadata_fallback_models=request.flow2api_metadata_fallback_models,
        metadata_system_prompt=request.metadata_system_prompt,
        cloning_image_system_prompt=request.cloning_image_system_prompt,
        cloning_video_system_prompt=request.cloning_video_system_prompt,
    )

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()

    return {"success": True, "message": "生成配置更新成功"}


# ========== AT Auto Refresh Config ==========

@router.get("/api/token-refresh/config")
async def get_token_refresh_config(token: str = Depends(verify_admin_token)):
    """Get scheduled AT auto refresh configuration."""
    captcha_config = await db.get_captcha_config()
    return {
        "success": True,
        "config": {
            "at_auto_refresh_enabled": bool(
                getattr(captcha_config, "session_refresh_scheduler_enabled", False)
            )
        }
    }


@router.post("/api/token-refresh/enabled")
async def update_token_refresh_enabled(
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Update scheduled AT auto refresh enabled."""
    enabled = bool(request.get("enabled", False))
    await db.update_captcha_config(session_refresh_scheduler_enabled=enabled)
    await db.reload_config_to_memory()
    return {
        "success": True,
        "message": f"定时自动刷新已{'启用' if enabled else '禁用'}"
    }


async def _sync_runtime_cache_config():
    from . import routes
    if routes.generation_handler and routes.generation_handler.file_cache:
        file_cache = routes.generation_handler.file_cache
        file_cache.set_timeout(config.cache_timeout)
        await file_cache.refresh_cleanup_task()

# ========== Cache Configuration Endpoints ==========

@router.get("/api/cache/config")
async def get_cache_config(token: str = Depends(verify_admin_token)):
    """Get cache configuration"""
    cache_config = await db.get_cache_config()

    # Calculate effective base URL
    effective_base_url = cache_config.cache_base_url if cache_config.cache_base_url else f"http://127.0.0.1:8000"

    ct = cache_config.cache_timeout or 0
    timeout_days = 0.0 if ct <= 0 else round(ct / 86400.0, 4)

    return {
        "success": True,
        "config": {
            "enabled": cache_config.cache_enabled,
            "timeout": cache_config.cache_timeout,
            "timeout_days": timeout_days,
            "base_url": cache_config.cache_base_url or "",
            "effective_base_url": effective_base_url
        }
    }


@router.get("/api/cache/stats")
async def get_cache_stats(token: str = Depends(verify_admin_token)):
    """Disk usage for the file cache directory."""
    from . import routes
    if not routes.generation_handler or not routes.generation_handler.file_cache:
        raise HTTPException(status_code=503, detail="File cache not initialized")
    stats = routes.generation_handler.file_cache.get_dir_stats()
    return {"success": True, **stats}


@router.get("/api/cache/files")
async def list_cache_files(token: str = Depends(verify_admin_token)):
    """List cached files for admin gallery (names, sizes, image/video/other)."""
    from . import routes
    if not routes.generation_handler or not routes.generation_handler.file_cache:
        raise HTTPException(status_code=503, detail="File cache not initialized")
    files = routes.generation_handler.file_cache.list_gallery_files()
    return {"success": True, "files": files}


@router.get("/api/cache/admin/file/{filename}")
async def get_cache_file_admin_preview(filename: str, token: str = Depends(verify_admin_token)):
    """Stream a cache file for the admin UI (Bearer auth). Plain <img src> cannot use managed-key /api/cache/blob/… URLs."""
    from . import routes

    if not routes.generation_handler or not routes.generation_handler.file_cache:
        raise HTTPException(status_code=503, detail="File cache not initialized")
    safe_name = Path(filename).name
    cache_dir = routes.generation_handler.file_cache.cache_dir.resolve()
    file_path = (cache_dir / safe_name).resolve()
    try:
        file_path.relative_to(cache_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Cache file not found")
    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return FileResponse(path=file_path, media_type=media_type, filename=safe_name)


@router.post("/api/cache/clear")
async def clear_cache_files(token: str = Depends(verify_admin_token)):
    """Delete all files in the cache directory (admin only)."""
    from . import routes
    if not routes.generation_handler or not routes.generation_handler.file_cache:
        raise HTTPException(status_code=503, detail="File cache not initialized")
    try:
        removed_count, removed_bytes = routes.generation_handler.file_cache.clear_all_files()
        return {
            "success": True,
            "message": "Cache cleared",
            "removed_count": removed_count,
            "removed_bytes": removed_bytes,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/cache/enabled")
async def update_cache_enabled(
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Update cache enabled status"""
    enabled = request.get("enabled", False)
    await db.update_cache_config(enabled=enabled)

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()
    await _sync_runtime_cache_config()

    return {"success": True, "message": f"缓存已{'启用' if enabled else '禁用'}"}


@router.post("/api/cache/config")
async def update_cache_config_full(
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Update complete cache configuration"""
    enabled = request.get("enabled")
    timeout = request.get("timeout")
    base_url = request.get("base_url")

    if timeout is not None:
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="缓存超时时间必须为整数")
        if timeout < 0:
            raise HTTPException(status_code=400, detail="缓存超时时间不能小于 0")
        max_cache_seconds = 7 * 86400
        if timeout > max_cache_seconds:
            raise HTTPException(
                status_code=400,
                detail=f"Cache timeout cannot exceed 7 days ({max_cache_seconds} seconds)",
            )

    await db.update_cache_config(enabled=enabled, timeout=timeout, base_url=base_url)

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()
    await _sync_runtime_cache_config()

    return {"success": True, "message": "缓存配置更新成功"}


@router.post("/api/cache/base-url")
async def update_cache_base_url(
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Update cache base URL"""
    base_url = request.get("base_url", "")
    await db.update_cache_config(base_url=base_url)

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()
    await _sync_runtime_cache_config()

    return {"success": True, "message": "缓存Base URL更新成功"}


@router.post("/api/captcha/config")
async def update_captcha_config(
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Update captcha configuration"""
    from ..services.browser_captcha import validate_browser_proxy_url

    captcha_method = request.get("captcha_method")
    yescaptcha_api_key = request.get("yescaptcha_api_key")
    yescaptcha_base_url = request.get("yescaptcha_base_url")
    capmonster_api_key = request.get("capmonster_api_key")
    capmonster_base_url = request.get("capmonster_base_url")
    ezcaptcha_api_key = request.get("ezcaptcha_api_key")
    ezcaptcha_base_url = request.get("ezcaptcha_base_url")
    capsolver_api_key = request.get("capsolver_api_key")
    capsolver_base_url = request.get("capsolver_base_url")
    remote_browser_base_url = request.get("remote_browser_base_url")
    remote_browser_api_key = request.get("remote_browser_api_key")
    remote_browser_timeout = request.get("remote_browser_timeout", 60)
    browser_fallback_to_remote_browser = request.get("browser_fallback_to_remote_browser", True)
    browser_proxy_enabled = request.get("browser_proxy_enabled", False)
    browser_proxy_url = request.get("browser_proxy_url", "")
    browser_count = request.get("browser_count", 1)
    personal_project_pool_size = request.get("personal_project_pool_size")
    personal_max_resident_tabs = request.get("personal_max_resident_tabs")
    personal_idle_tab_ttl_seconds = request.get("personal_idle_tab_ttl_seconds")
    browser_captcha_page_url = request.get("browser_captcha_page_url")
    session_refresh_enabled = request.get("session_refresh_enabled")
    session_refresh_browser_first = request.get("session_refresh_browser_first")
    session_refresh_inject_st_cookie = request.get("session_refresh_inject_st_cookie")
    session_refresh_warmup_urls = request.get("session_refresh_warmup_urls")
    session_refresh_wait_seconds_per_url = request.get("session_refresh_wait_seconds_per_url")
    session_refresh_overall_timeout_seconds = request.get("session_refresh_overall_timeout_seconds")
    session_refresh_update_st_from_cookie = request.get("session_refresh_update_st_from_cookie")
    session_refresh_fail_if_st_refresh_fails = request.get("session_refresh_fail_if_st_refresh_fails")
    session_refresh_local_only = request.get("session_refresh_local_only")
    session_refresh_scheduler_enabled = request.get("session_refresh_scheduler_enabled")
    session_refresh_scheduler_interval_minutes = request.get("session_refresh_scheduler_interval_minutes")
    session_refresh_scheduler_batch_size = request.get("session_refresh_scheduler_batch_size")
    session_refresh_scheduler_only_expiring_within_minutes = request.get("session_refresh_scheduler_only_expiring_within_minutes")
    st_only_refresh_scheduler_enabled = request.get("st_only_refresh_scheduler_enabled")
    st_only_refresh_scheduler_interval_minutes = request.get("st_only_refresh_scheduler_interval_minutes")
    st_only_refresh_scheduler_batch_size = request.get("st_only_refresh_scheduler_batch_size")
    st_only_refresh_scheduler_expiring_within_minutes = request.get("st_only_refresh_scheduler_expiring_within_minutes")
    extension_queue_wait_timeout_seconds = request.get("extension_queue_wait_timeout_seconds")
    dedicated_extension_enabled = request.get("dedicated_extension_enabled")
    dedicated_extension_captcha_timeout_seconds = request.get("dedicated_extension_captcha_timeout_seconds")
    dedicated_extension_st_refresh_timeout_seconds = request.get("dedicated_extension_st_refresh_timeout_seconds")
    extension_fallback_to_managed_on_dedicated_failure = request.get(
        "extension_fallback_to_managed_on_dedicated_failure"
    )

    # 验证浏览器打码页面 URL
    if browser_captcha_page_url is not None:
        raw_page = str(browser_captcha_page_url).strip()
        if raw_page:
            from urllib.parse import urlparse

            parsed = urlparse(raw_page)
            if parsed.scheme not in ("http", "https"):
                return {"success": False, "message": "打码页面地址必须以 http:// 或 https:// 开头"}
            if not (parsed.netloc or "").strip():
                return {"success": False, "message": "打码页面地址无效"}

    # 验证浏览器代理URL格式
    if browser_proxy_enabled and browser_proxy_url:
        is_valid, error_msg = validate_browser_proxy_url(browser_proxy_url)
        if not is_valid:
            return {"success": False, "message": error_msg}

    if remote_browser_base_url:
        try:
            remote_browser_base_url = _normalize_http_base_url(remote_browser_base_url)
        except RuntimeError as e:
            return {"success": False, "message": str(e)}

    try:
        remote_browser_timeout = max(5, int(remote_browser_timeout or 60))
    except Exception:
        return {"success": False, "message": "远程打码超时时间必须是整数秒"}
    browser_fallback_to_remote_browser = bool(browser_fallback_to_remote_browser)
    if extension_queue_wait_timeout_seconds is not None:
        try:
            extension_queue_wait_timeout_seconds = int(extension_queue_wait_timeout_seconds)
        except Exception:
            return {"success": False, "message": "extension_queue_wait_timeout_seconds must be an integer"}
        if extension_queue_wait_timeout_seconds < 1 or extension_queue_wait_timeout_seconds > 120:
            return {"success": False, "message": "extension_queue_wait_timeout_seconds must be between 1 and 120"}
    if dedicated_extension_captcha_timeout_seconds is not None:
        try:
            dedicated_extension_captcha_timeout_seconds = int(dedicated_extension_captcha_timeout_seconds)
        except Exception:
            return {"success": False, "message": "dedicated_extension_captcha_timeout_seconds must be an integer"}
        if dedicated_extension_captcha_timeout_seconds < 5 or dedicated_extension_captcha_timeout_seconds > 180:
            return {"success": False, "message": "dedicated_extension_captcha_timeout_seconds must be between 5 and 180"}
    if dedicated_extension_st_refresh_timeout_seconds is not None:
        try:
            dedicated_extension_st_refresh_timeout_seconds = int(dedicated_extension_st_refresh_timeout_seconds)
        except Exception:
            return {"success": False, "message": "dedicated_extension_st_refresh_timeout_seconds must be an integer"}
        if dedicated_extension_st_refresh_timeout_seconds < 10 or dedicated_extension_st_refresh_timeout_seconds > 300:
            return {"success": False, "message": "dedicated_extension_st_refresh_timeout_seconds must be between 10 and 300"}

    if captcha_method == "remote_browser":
        if not (remote_browser_base_url or "").strip():
            return {"success": False, "message": "remote_browser 模式需要配置远程打码服务地址"}
        if not (remote_browser_api_key or "").strip():
            return {"success": False, "message": "remote_browser 模式需要配置远程打码服务 API Key"}

    await db.update_captcha_config(
        captcha_method=captcha_method,
        yescaptcha_api_key=yescaptcha_api_key,
        yescaptcha_base_url=yescaptcha_base_url,
        capmonster_api_key=capmonster_api_key,
        capmonster_base_url=capmonster_base_url,
        ezcaptcha_api_key=ezcaptcha_api_key,
        ezcaptcha_base_url=ezcaptcha_base_url,
        capsolver_api_key=capsolver_api_key,
        capsolver_base_url=capsolver_base_url,
        remote_browser_base_url=remote_browser_base_url,
        remote_browser_api_key=remote_browser_api_key,
        remote_browser_timeout=remote_browser_timeout,
        browser_fallback_to_remote_browser=browser_fallback_to_remote_browser,
        browser_proxy_enabled=browser_proxy_enabled,
        browser_proxy_url=browser_proxy_url if browser_proxy_enabled else None,
        browser_count=max(1, int(browser_count)) if browser_count else 1,
        personal_project_pool_size=personal_project_pool_size,
        personal_max_resident_tabs=personal_max_resident_tabs,
        personal_idle_tab_ttl_seconds=personal_idle_tab_ttl_seconds,
        browser_captcha_page_url=browser_captcha_page_url,
        session_refresh_enabled=session_refresh_enabled,
        session_refresh_browser_first=session_refresh_browser_first,
        session_refresh_inject_st_cookie=session_refresh_inject_st_cookie,
        session_refresh_warmup_urls=(
            ",".join(str(item).strip() for item in session_refresh_warmup_urls if str(item).strip())
            if isinstance(session_refresh_warmup_urls, list)
            else session_refresh_warmup_urls
        ),
        session_refresh_wait_seconds_per_url=session_refresh_wait_seconds_per_url,
        session_refresh_overall_timeout_seconds=session_refresh_overall_timeout_seconds,
        session_refresh_update_st_from_cookie=session_refresh_update_st_from_cookie,
        session_refresh_fail_if_st_refresh_fails=session_refresh_fail_if_st_refresh_fails,
        session_refresh_local_only=session_refresh_local_only,
        session_refresh_scheduler_enabled=session_refresh_scheduler_enabled,
        session_refresh_scheduler_interval_minutes=session_refresh_scheduler_interval_minutes,
        session_refresh_scheduler_batch_size=session_refresh_scheduler_batch_size,
        session_refresh_scheduler_only_expiring_within_minutes=session_refresh_scheduler_only_expiring_within_minutes,
        st_only_refresh_scheduler_enabled=st_only_refresh_scheduler_enabled,
        st_only_refresh_scheduler_interval_minutes=st_only_refresh_scheduler_interval_minutes,
        st_only_refresh_scheduler_batch_size=st_only_refresh_scheduler_batch_size,
        st_only_refresh_scheduler_expiring_within_minutes=st_only_refresh_scheduler_expiring_within_minutes,
        extension_queue_wait_timeout_seconds=extension_queue_wait_timeout_seconds,
        dedicated_extension_enabled=dedicated_extension_enabled,
        dedicated_extension_captcha_timeout_seconds=dedicated_extension_captcha_timeout_seconds,
        dedicated_extension_st_refresh_timeout_seconds=dedicated_extension_st_refresh_timeout_seconds,
        extension_fallback_to_managed_on_dedicated_failure=extension_fallback_to_managed_on_dedicated_failure,
    )

    # 🔥 Hot reload: sync database config to memory
    await db.reload_config_to_memory()

    # 如果使用 browser 打码，热重载浏览器数量配置
    if captcha_method == "browser":
        try:
            from ..services.browser_captcha import BrowserCaptchaService
            service = await BrowserCaptchaService.get_instance(db)
            await service.reload_browser_count()
        except Exception:
            pass

    # 如果使用 personal 打码，热重载配置
    if captcha_method == "personal":
        try:
            from ..services.browser_captcha_personal import BrowserCaptchaService
            service = await BrowserCaptchaService.get_instance(db)
            await service.reload_config()
        except Exception as e:
            print(f"[Admin] Personal 配置热更新失败: {e}")

    return {"success": True, "message": "验证码配置更新成功"}


@router.get("/api/captcha/config")
async def get_captcha_config(token: str = Depends(verify_admin_token)):
    """Get captcha configuration"""
    captcha_config = await db.get_captcha_config()
    return {
        "captcha_method": captcha_config.captcha_method,
        "yescaptcha_api_key": captcha_config.yescaptcha_api_key,
        "yescaptcha_base_url": captcha_config.yescaptcha_base_url,
        "capmonster_api_key": captcha_config.capmonster_api_key,
        "capmonster_base_url": captcha_config.capmonster_base_url,
        "ezcaptcha_api_key": captcha_config.ezcaptcha_api_key,
        "ezcaptcha_base_url": captcha_config.ezcaptcha_base_url,
        "capsolver_api_key": captcha_config.capsolver_api_key,
        "capsolver_base_url": captcha_config.capsolver_base_url,
        "remote_browser_base_url": captcha_config.remote_browser_base_url,
        "remote_browser_api_key": captcha_config.remote_browser_api_key,
        "remote_browser_timeout": captcha_config.remote_browser_timeout,
        "browser_fallback_to_remote_browser": bool(
            getattr(captcha_config, "browser_fallback_to_remote_browser", True)
        ),
        "browser_proxy_enabled": captcha_config.browser_proxy_enabled,
        "browser_proxy_url": captcha_config.browser_proxy_url or "",
        "browser_count": captcha_config.browser_count,
        "personal_project_pool_size": captcha_config.personal_project_pool_size,
        "personal_max_resident_tabs": captcha_config.personal_max_resident_tabs,
        "personal_idle_tab_ttl_seconds": captcha_config.personal_idle_tab_ttl_seconds,
        "browser_captcha_page_url": (
            (getattr(captcha_config, "browser_captcha_page_url", None) or "").strip()
            or "https://labs.google/fx/api/auth/providers"
        ),
        "session_refresh_enabled": bool(getattr(captcha_config, "session_refresh_enabled", True)),
        "session_refresh_browser_first": bool(getattr(captcha_config, "session_refresh_browser_first", True)),
        "session_refresh_inject_st_cookie": bool(getattr(captcha_config, "session_refresh_inject_st_cookie", True)),
        "session_refresh_warmup_urls": [
            item.strip()
            for item in str(
                getattr(
                    captcha_config,
                    "session_refresh_warmup_urls",
                    "https://labs.google/fx/tools/flow,https://labs.google/fx",
                )
                or ""
            ).split(",")
            if item.strip()
        ],
        "session_refresh_wait_seconds_per_url": int(
            getattr(captcha_config, "session_refresh_wait_seconds_per_url", 60) or 60
        ),
        "session_refresh_overall_timeout_seconds": int(
            getattr(captcha_config, "session_refresh_overall_timeout_seconds", 180) or 180
        ),
        "session_refresh_update_st_from_cookie": bool(
            getattr(captcha_config, "session_refresh_update_st_from_cookie", True)
        ),
        "session_refresh_fail_if_st_refresh_fails": bool(
            getattr(captcha_config, "session_refresh_fail_if_st_refresh_fails", True)
        ),
        "session_refresh_local_only": bool(getattr(captcha_config, "session_refresh_local_only", True)),
        "session_refresh_scheduler_enabled": bool(
            getattr(captcha_config, "session_refresh_scheduler_enabled", False)
        ),
        "session_refresh_scheduler_interval_minutes": int(
            getattr(captcha_config, "session_refresh_scheduler_interval_minutes", 30) or 30
        ),
        "session_refresh_scheduler_batch_size": int(
            getattr(captcha_config, "session_refresh_scheduler_batch_size", 10) or 10
        ),
        "session_refresh_scheduler_only_expiring_within_minutes": int(
            getattr(captcha_config, "session_refresh_scheduler_only_expiring_within_minutes", 60) or 60
        ),
        "st_only_refresh_scheduler_enabled": bool(
            getattr(captcha_config, "st_only_refresh_scheduler_enabled", False)
        ),
        "st_only_refresh_scheduler_interval_minutes": int(
            getattr(captcha_config, "st_only_refresh_scheduler_interval_minutes", 5) or 5
        ),
        "st_only_refresh_scheduler_batch_size": int(
            getattr(captcha_config, "st_only_refresh_scheduler_batch_size", 20) or 20
        ),
        "st_only_refresh_scheduler_expiring_within_minutes": int(
            getattr(captcha_config, "st_only_refresh_scheduler_expiring_within_minutes", 5) or 5
        ),
        "dedicated_extension_enabled": bool(getattr(captcha_config, "dedicated_extension_enabled", False)),
        "dedicated_extension_captcha_timeout_seconds": int(
            getattr(captcha_config, "dedicated_extension_captcha_timeout_seconds", 25) or 25
        ),
        "dedicated_extension_st_refresh_timeout_seconds": int(
            getattr(captcha_config, "dedicated_extension_st_refresh_timeout_seconds", 45) or 45
        ),
        "extension_queue_wait_timeout_seconds": int(
            getattr(captcha_config, "extension_queue_wait_timeout_seconds", 20) or 20
        ),
        "extension_fallback_to_managed_on_dedicated_failure": bool(
            getattr(captcha_config, "extension_fallback_to_managed_on_dedicated_failure", False)
        ),
    }


@router.get("/api/agent-gateway/mode")
async def get_agent_gateway_mode(token: str = Depends(verify_admin_token)):
    """Probe configured agent-gateway mode via remote_browser_base_url health."""
    captcha_config = await db.get_captcha_config()
    base_url = (getattr(captcha_config, "remote_browser_base_url", "") or "").strip()
    if not base_url:
        return {
            "success": False,
            "status": "not_configured",
            "message": "remote_browser_base_url is not configured",
            "agent_auth_mode": "unknown",
            "keygen_verify_mode": "",
            "gateway_reachable": False,
            "base_url": "",
        }
    try:
        probe = await _probe_agent_gateway_mode(base_url)
    except Exception as e:
        return {
            "success": False,
            "status": "unreachable",
            "message": f"agent-gateway health probe failed: {e}",
            "agent_auth_mode": "unknown",
            "keygen_verify_mode": "",
            "gateway_reachable": False,
            "base_url": base_url,
        }

    mode = probe.get("agent_auth_mode") or "legacy"
    verify_mode = probe.get("keygen_verify_mode") or ""
    return {
        "success": True,
        "status": "reachable",
        "message": "agent-gateway reachable",
        "agent_auth_mode": mode,
        "keygen_verify_mode": verify_mode,
        "gateway_reachable": bool(probe.get("ok")),
        "base_url": base_url,
        "service": probe.get("service") or "",
    }


@router.get("/api/agent-gateway/connections")
async def get_agent_gateway_connections(token: str = Depends(verify_admin_token)):
    """Fetch currently connected agent sessions from configured gateway."""
    captcha_config = await db.get_captcha_config()
    base_url = (getattr(captcha_config, "remote_browser_base_url", "") or "").strip()
    api_key = (getattr(captcha_config, "remote_browser_api_key", "") or "").strip()
    if not base_url:
        return {
            "success": False,
            "status": "not_configured",
            "message": "remote_browser_base_url is not configured",
            "connections": [],
            "count": 0,
            "base_url": "",
        }
    if not api_key:
        return {
            "success": False,
            "status": "missing_api_key",
            "message": "remote_browser_api_key is not configured",
            "connections": [],
            "count": 0,
            "base_url": base_url,
        }
    try:
        payload = await _fetch_agent_gateway_connections(base_url, api_key)
    except Exception as e:
        return {
            "success": False,
            "status": "unreachable",
            "message": f"agent-gateway connections probe failed: {e}",
            "connections": [],
            "count": 0,
            "base_url": base_url,
        }

    raw_agents = payload.get("agents")
    connections = raw_agents if isinstance(raw_agents, list) else []
    return {
        "success": True,
        "status": "reachable",
        "message": "agent-gateway connections loaded",
        "base_url": base_url,
        "count": int(payload.get("count") or len(connections)),
        "connections": connections,
    }


@router.post("/api/captcha/score-test")
async def test_captcha_score(
    _request: Optional[CaptchaScoreTestRequest] = None,
    _token: str = Depends(verify_admin_token)
):
    """分数测试已禁用。"""
    raise HTTPException(status_code=403, detail="已禁用分数测试")


# ========== Plugin Configuration Endpoints ==========

@router.get("/api/plugin/config")
async def get_plugin_config(request: Request, token: str = Depends(verify_admin_token)):
    """Get plugin configuration"""
    plugin_config = await db.get_plugin_config()

    # Get the actual domain and port from the request
    # This allows the connection URL to reflect the user's actual access path
    host_header = request.headers.get("host", "")

    # Generate connection URL based on actual request
    if host_header:
        # Use the actual domain/IP and port from the request
        connection_url = f"http://{host_header}/api/plugin/update-token"
    else:
        # Fallback to config-based URL
        from ..core.config import config
        server_host = config.server_host
        server_port = config.server_port

        if server_host == "0.0.0.0":
            connection_url = f"http://127.0.0.1:{server_port}/api/plugin/update-token"
        else:
            connection_url = f"http://{server_host}:{server_port}/api/plugin/update-token"

    return {
        "success": True,
        "config": {
            "connection_token": plugin_config.connection_token,
            "connection_url": connection_url,
            "auto_enable_on_update": plugin_config.auto_enable_on_update
        }
    }


@router.post("/api/plugin/config")
async def update_plugin_config(
    request: dict,
    token: str = Depends(verify_admin_token)
):
    """Update plugin configuration"""
    connection_token = request.get("connection_token", "")
    auto_enable_on_update = request.get("auto_enable_on_update", True)  # 默认开启

    # Generate random token if empty
    if not connection_token:
        connection_token = secrets.token_urlsafe(32)

    await db.update_plugin_config(
        connection_token=connection_token,
        auto_enable_on_update=auto_enable_on_update
    )

    return {
        "success": True,
        "message": "插件配置更新成功",
        "connection_token": connection_token,
        "auto_enable_on_update": auto_enable_on_update
    }


@router.post("/api/plugin/update-token")
async def plugin_update_token(request: dict, authorization: Optional[str] = Header(None)):
    """Receive token update from Chrome extension (no admin auth required, uses connection_token)"""
    # Verify connection token
    plugin_config = await db.get_plugin_config()

    # Extract token from Authorization header
    provided_token = None
    if authorization:
        if authorization.startswith("Bearer "):
            provided_token = authorization[7:]
        else:
            provided_token = authorization

    # Check if token matches
    if not plugin_config.connection_token or provided_token != plugin_config.connection_token:
        raise HTTPException(status_code=401, detail="Invalid connection token")

    # Extract session token from request
    session_token = request.get("session_token")

    if not session_token:
        raise HTTPException(status_code=400, detail="Missing session_token")

    # Step 1: Convert ST to AT to get user info (including email)
    try:
        result = await token_manager.flow_client.st_to_at(session_token)
        at = result["access_token"]
        expires = result.get("expires")
        user_info = result.get("user", {})
        email = user_info.get("email", "")

        if not email:
            raise HTTPException(status_code=400, detail="Failed to get email from session token")

        # Parse expiration time
        from datetime import datetime
        at_expires = None
        if expires:
            try:
                at_expires = datetime.fromisoformat(expires.replace('Z', '+00:00'))
            except:
                pass

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid session token: {str(e)}")

    # Step 2: Check if token with this email exists
    existing_token = await db.get_token_by_email(email)

    if existing_token:
        # Update existing token
        try:
            # Update token
            await token_manager.update_token(
                token_id=existing_token.id,
                st=session_token,
                at=at,
                at_expires=at_expires
            )

            # Check if auto-enable is enabled and token is disabled
            if plugin_config.auto_enable_on_update and not existing_token.is_active:
                await token_manager.enable_token(existing_token.id)
                return {
                    "success": True,
                    "message": f"Token updated and auto-enabled for {email}",
                    "action": "updated",
                    "auto_enabled": True
                }

            return {
                "success": True,
                "message": f"Token updated for {email}",
                "action": "updated"
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update token: {str(e)}")
    else:
        # Add new token
        try:
            new_token = await token_manager.add_token(
                st=session_token,
                remark="Added by Chrome Extension"
            )

            return {
                "success": True,
                "message": f"Token added for {new_token.email}",
                "action": "added",
                "token_id": new_token.id
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to add token: {str(e)}")
