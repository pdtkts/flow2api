"""
Direct HTTP client for tastracker.com contributor-search (no browser).

Mirrors upstream secureHeaders / csr-token flow (webpack 7548).
Used by TaskTrackerService and optionally scripts/fetch_contributor_tas.py.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from curl_cffi import requests as curl_requests

HOST = "tastracker.com"
BASE = f"https://{HOST}"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
CSR_REFRESH_REMAINING_MS = 120_000  # 12e4 in upstream bundle (module 9329)
DEFAULT_TLS_PROFILE = "chrome124"


def impersonation_candidates(preferred: str) -> List[Optional[str]]:
    out: List[Optional[str]] = []
    seen: set[Optional[str]] = set()
    for candidate in (preferred, "chrome124", "chrome120", "chrome116", None):
        c = candidate.strip() if isinstance(candidate, str) else None
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def request_with_fallback(method: str, url: str, **kwargs: Any):
    preferred = str(kwargs.pop("impersonate", "") or "").strip()
    errors: List[str] = []
    for imp in impersonation_candidates(preferred):
        call_kwargs = dict(kwargs)
        if imp:
            call_kwargs["impersonate"] = imp
        try:
            return getattr(curl_requests, method)(url, **call_kwargs)
        except Exception as exc:
            msg = str(exc)
            if "Impersonating " in msg and " is not supported" in msg:
                errors.append(msg)
                continue
            raise
    raise RuntimeError("; ".join(errors) if errors else "no working impersonation profile")


def build_referer(search_id: str, order: str, page: int) -> str:
    qs: Dict[str, str] = {
        "search": search_id,
        "order": order,
        "content_type": "all",
        "generative_ai": "all",
    }
    if page > 1:
        qs["page"] = str(page)
    return f"{BASE}/contributor?{urlencode(qs)}"


def map_image(img: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(img, dict):
        return None
    img_id = str(img.get("id") or img.get("assetId") or "").strip()
    if not img_id:
        return None
    return {
        "id": img_id,
        "title": str(img.get("title") or ""),
        "downloads": int(img.get("downloads") or 0),
        "keywords": str(img.get("keywords") or ""),
        "imageUrl": str(img.get("thumbnailUrl") or img.get("imageUrl") or ""),
        "dimensions": str(img.get("dimensions") or ""),
        "mediaType": str(img.get("mediaType") or ""),
        "contentType": str(img.get("contentType") or ""),
        "category": str(img.get("category") or ""),
        "premium": str(img.get("premium") or ""),
        "updatedAt": str(img.get("creationDate") or img.get("updatedAt") or ""),
        "isAI": bool(img.get("isAI")),
        "creator": str(img.get("creator") or ""),
    }


def looks_unauthorized(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    err = str(body.get("error") or body.get("message") or "").lower()
    return bool(
        "unauthor" in err
        or "forbidden" in err
        or ("not" in err and "signed" in err and "in" in err)
        or ("please" in err and "log" in err)
        or "session expired" in err
    )


class CsrTokenCache:
    """In-process cache mirroring tastracker client module 9329."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at_ms: float = 0.0

    def get_valid_token(self) -> Optional[str]:
        now = time.time() * 1000.0
        if not self._token or self._expires_at_ms <= now:
            return None
        remaining = self._expires_at_ms - now
        if remaining > CSR_REFRESH_REMAINING_MS:
            return self._token
        return None

    def set_from_response(self, token: str, expires_in: float) -> None:
        self._token = token
        self._expires_at_ms = time.time() * 1000.0 + float(expires_in)


def mint_csr_token(
    cookie: str,
    impersonate: str,
    device_token: Optional[str],
    csr_referer: str,
) -> Tuple[Optional[str], float, str]:
    headers: Dict[str, str] = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "Origin": BASE,
        "Referer": csr_referer,
        "User-Agent": DEFAULT_UA,
    }
    if device_token:
        headers["X-Device-Token"] = device_token
    try:
        r = request_with_fallback(
            "post",
            f"{BASE}/api/auth/csr-token",
            headers=headers,
            json={},
            impersonate=impersonate,
            timeout=60,
        )
    except Exception as exc:
        return None, 0.0, f"csr-token request failed: {exc}"
    if not r.ok:
        return None, 0.0, f"csr-token HTTP {r.status_code}: {r.text[:500]}"
    try:
        data = r.json()
    except Exception:
        return None, 0.0, "csr-token response is not JSON"
    token = data.get("token")
    if not token:
        return None, 0.0, f"csr-token missing in JSON: {json.dumps(data)[:300]}"
    expires_in = data.get("expiresIn")
    if expires_in is None:
        return None, 0.0, "csr-token response missing expiresIn"
    return str(token), float(expires_in), ""


def ensure_csr_token(
    cache: CsrTokenCache,
    cookie: str,
    impersonate: str,
    device_token: Optional[str],
    csr_override: Optional[str],
    csr_referer: str,
) -> Tuple[str, str]:
    if csr_override:
        return csr_override, ""
    cached = cache.get_valid_token()
    if cached:
        return cached, ""
    token, expires_in, err = mint_csr_token(cookie, impersonate, device_token, csr_referer)
    if err or not token:
        return "", err or "empty csr token"
    cache.set_from_response(token, expires_in)
    return token, ""


def fetch_contributor_search_page(
    search_id: str,
    order: str,
    page: int,
    cookie: str,
    csr_token: str,
    turnstile_token: Optional[str],
    device_id: str,
    impersonate: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    params: Dict[str, str] = {"search": search_id, "order": order}
    if page > 1:
        params["page"] = str(page)
    qs = urlencode(params)
    url = f"{BASE}/api/contributor-search?{qs}"
    headers: Dict[str, str] = {
        "Accept": "*/*",
        "Accept-Language": "en-GB,en;q=0.9",
        "Cookie": cookie,
        "Referer": build_referer(search_id, order, page),
        "User-Agent": DEFAULT_UA,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSR-Token": csr_token,
        "x-device-id": device_id,
        "X-Device-Id": device_id,
    }
    if turnstile_token:
        headers["X-Turnstile-Token"] = turnstile_token
    try:
        r = request_with_fallback(
            "get",
            url,
            headers=headers,
            impersonate=impersonate,
            timeout=60,
        )
    except Exception as exc:
        return None, str(exc)
    try:
        body = r.json()
    except Exception:
        return None, f"non-JSON body HTTP {r.status_code}: {r.text[:400]}"
    if not r.ok:
        return body, f"HTTP {r.status_code}"
    if looks_unauthorized(body):
        return body, "unauthorized body"
    return body, ""


def fetch_contributor_raw_images(
    search_id: str,
    order: str,
    pages: List[int],
    cookie: str,
    device_id: str,
    device_token: Optional[str],
    turnstile_token: Optional[str],
    tls_profile: str,
    csr_cache: CsrTokenCache,
    csr_token_override: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch all pages; return flattened list of raw image dicts from API, or ([], error).
    """
    if not pages:
        return [], "no pages"
    impersonate = (tls_profile or "").strip() or DEFAULT_TLS_PROFILE
    csr_referer = build_referer(search_id, order, pages[0])
    all_images: List[Dict[str, Any]] = []
    for page in pages:
        csr, err = ensure_csr_token(
            csr_cache, cookie, impersonate, device_token, csr_token_override, csr_referer
        )
        if err:
            return [], err
        body, err = fetch_contributor_search_page(
            search_id,
            order,
            page,
            cookie,
            csr,
            turnstile_token,
            device_id,
            impersonate,
        )
        if err:
            return [], f"page={page}: {err}"
        assert body is not None
        imgs = body.get("images")
        if isinstance(imgs, list):
            all_images.extend(imgs)
    return all_images, ""
