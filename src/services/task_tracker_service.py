import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright, BrowserContext

from ..core.config import config
from ..core.logger import debug_logger

HOST = "tastracker.com"
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
PAGE_TIMEOUT_MS = 60000

class TaskTrackerService:
    def __init__(self):
        self.user_data_dir = Path(".cache/tas-profile")
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    def _parse_cookie_header(self, header: str) -> List[Dict[str, Any]]:
        cookies = []
        if not header:
            return cookies
            
        parts = [p.strip() for p in header.split(";") if p.strip()]
        for p in parts:
            if "=" not in p:
                continue
            idx = p.index("=")
            name = p[:idx].strip()
            value = p[idx+1:].strip()
            if not name:
                continue
                
            cookie = {
                "name": name,
                "value": value,
                "domain": f".{HOST}",
                "path": "/",
                "secure": False,
                "httpOnly": False,
                "sameSite": "Lax",
            }
            if name.startswith("__Host-"):
                cookie["domain"] = HOST
                cookie["path"] = "/"
                cookie["secure"] = True
            elif name.startswith("__Secure-"):
                cookie["secure"] = True
                
            cookies.append(cookie)
        return cookies

    def _map_image(self, img: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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

    def _looks_unauthorized(self, body: Any) -> bool:
        if not isinstance(body, dict):
            return False
        err = str(body.get("error") or body.get("message") or "").lower()
        if "unauthor" in err or "forbidden" in err or ("not" in err and "signed" in err and "in" in err) or ("please" in err and "log" in err) or "session expired" in err:
            return True
        return False

    def _build_seed_url(self, search: str, order: str, page: int) -> str:
        qs = {
            "search": search,
            "order": order,
            "content_type": "all",
            "generative_ai": "all",
        }
        if page > 1:
            qs["page"] = str(page)
        return f"https://{HOST}/contributor?{urlencode(qs)}"

    async def fetch_contributor_assets(
        self,
        search_id: str,
        order: str = "creation",
        pages: Optional[List[int]] = None,
        title_filter: str = "",
    ) -> List[Dict[str, Any]]:
        search_id = search_id.strip()
        order = order.strip()
        title_filter = title_filter.strip().lower()
        if not pages:
            pages = [1]
            
        pages = sorted(list(set(p for p in pages if p >= 1)))
        if not pages:
            pages = [1]
            
        first_page = pages[0]
        rest_pages = pages[1:]
        
        auth_cookie = config.task_tracker_cookies.strip()
        device_id = config.task_tracker_device_id.strip()
        device_name = config.task_tracker_device_name.strip()
        
        if not auth_cookie or "__Secure-next-auth.session-token=" not in auth_cookie:
            raise ValueError("Invalid Task Tracker cookies. Must contain __Secure-next-auth.session-token")
            
        if not device_id:
            device_id = "dev_d6u2k6_wabygqst2z9_mocsd0nz"
        if not device_name:
            device_name = "Chrome on Windows"

        debug_logger.log_info(f"[TaskTracker] Launching chromium for search={search_id} pages={pages}")

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(self.user_data_dir.absolute()),
                headless=True,
                user_agent=DEFAULT_UA,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            try:
                await context.add_cookies(self._parse_cookie_header(auth_cookie))
                
                pages_list = context.pages
                page = pages_list[0] if pages_list else await context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                
                init_script = """
                ({ stableDeviceId, stableDeviceName }) => {
                    try {
                        localStorage.setItem("_dvx.id", stableDeviceId);
                        localStorage.setItem("_dvx.nm", stableDeviceName);
                        sessionStorage.setItem("_dvx.id", stableDeviceId);
                        sessionStorage.setItem("_dvx.nm", stableDeviceName);
                    } catch (e) {}

                    const originalFetch = window.fetch.bind(window);
                    window.fetch = (input, init = {}) => {
                        const headers = new Headers(init.headers || {});
                        if (!headers.has("x-device-id")) headers.set("x-device-id", stableDeviceId);
                        if (!headers.has("X-Device-Id")) headers.set("X-Device-Id", stableDeviceId);
                        return originalFetch(input, { ...init, headers });
                    };
                }
                """
                await context.add_init_script(init_script, arg={"stableDeviceId": device_id, "stableDeviceName": device_name})
                
                seed_url = self._build_seed_url(search_id, order, first_page)
                debug_logger.log_info(f"[TaskTracker] navigating: {seed_url}")
                
                async with page.expect_response(
                    lambda r: "/api/contributor-search" in r.url and r.request.method == "GET",
                    timeout=PAGE_TIMEOUT_MS
                ) as response_info:
                    try:
                        await page.goto(seed_url, wait_until="domcontentloaded")
                    except Exception as e:
                        if "/login" in page.url or "/auth/" in page.url:
                            raise ValueError("Auth session looks expired or invalid (redirected to login).")
                        raise e
                        
                response = await response_info.value
                
                if not response.ok:
                    try:
                        err_json = await response.json()
                        if self._looks_unauthorized(err_json):
                            raise ValueError("Auth session looks expired or invalid.")
                    except:
                        pass
                    raise ValueError(f"Initial /api/contributor-search returned {response.status} {response.status_text}")
                    
                first_body = await response.json()
                if self._looks_unauthorized(first_body):
                    raise ValueError("Auth session looks expired or invalid.")
                    
                all_bodies = [{"page": first_page, "body": first_body}]
                
                if rest_pages:
                    debug_logger.log_info(f"[TaskTracker] fetching extra pages in-page: {rest_pages}")
                    eval_script = """
                    async ({ search, order, pages }) => {
                        const out = [];
                        for (const p of pages) {
                            const qs = new URLSearchParams({ search, page: String(p), order });
                            try {
                                const r = await fetch(`/api/contributor-search?${qs}`, {
                                    method: "GET",
                                    credentials: "include",
                                });
                                const json = await r.json().catch(() => null);
                                out.push({ page: p, status: r.status, body: json });
                            } catch (err) {
                                out.push({ page: p, status: 0, body: { error: String(err?.message || err) } });
                            }
                        }
                        return out;
                    }
                    """
                    extra = await page.evaluate(eval_script, {"search": search_id, "order": order, "pages": rest_pages})
                    
                    for entry in extra:
                        status = entry.get("status", 0)
                        body = entry.get("body")
                        if 200 <= status < 300 and body and not self._looks_unauthorized(body):
                            all_bodies.append({"page": entry.get("page"), "body": body})
                        else:
                            if self._looks_unauthorized(body):
                                raise ValueError("Auth session looks expired or invalid during extra pages.")
                                
                all_images = []
                for b in all_bodies:
                    body = b.get("body", {})
                    if isinstance(body.get("images"), list):
                        all_images.extend(body["images"])
                        
                mapped = [self._map_image(img) for img in all_images if self._map_image(img)]
                
                if title_filter:
                    filtered = [r for r in mapped if title_filter in r["title"].lower()]
                else:
                    filtered = mapped
                    
                debug_logger.log_info(f"[TaskTracker] complete. mapped={len(mapped)} filtered={len(filtered)}")
                return filtered
                
            finally:
                try:
                    await context.close()
                except:
                    pass
