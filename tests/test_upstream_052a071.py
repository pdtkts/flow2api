import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.api.admin import AddTokenRequest, BrowserProfileTokenRequest
from src.core.browser_runtime_status import (
    finish_runtime_prepare,
    get_runtime_status,
    reset_runtime_prepare,
    start_runtime_prepare,
)
from src.core.config import Config, DEFAULT_YESCAPTCHA_TASK_TYPE
from src.core.database import Database
from src.core.model_resolver import VIDEO_BASE_MODELS, resolve_model_name
from src.core.models import Token, TokenRefreshConfig
from src.services import browser_captcha_personal
from src.services.browser_captcha import TokenBrowser
from src.services.generation_handler import MODEL_CONFIG
from src.services.protocol_login import (
    _validate_redirect,
    normalize_proxy_url,
    parse_google_cookies,
)
from src.services.token_manager import TokenManager


class Upstream052ConfigTests(unittest.TestCase):
    def test_s7_is_the_new_default(self):
        self.assertEqual(DEFAULT_YESCAPTCHA_TASK_TYPE, "RecaptchaV3TaskProxylessM1S7")

    def test_browser_retry_settings_are_bounded(self):
        cfg = Config()
        cfg._config.setdefault("captcha", {})["browser_captcha_max_retries"] = 99
        cfg._config["captcha"]["browser_captcha_generation_retries"] = "bad"
        self.assertEqual(cfg.browser_captcha_max_retries, 20)
        self.assertEqual(cfg.browser_captcha_generation_retries, 6)

    def test_omni_aliases_and_model_configs_exist(self):
        self.assertEqual(resolve_model_name("omni", model_config=MODEL_CONFIG), "omni")
        self.assertEqual(VIDEO_BASE_MODELS["omni"]["portrait"], "omni_portrait")
        self.assertEqual(MODEL_CONFIG["omni"]["model_key"], "abra_t2v_8s")
        self.assertEqual(MODEL_CONFIG["omni"]["reference_model_key"], "abra_r2v_8s")
        self.assertEqual(MODEL_CONFIG["omni"]["max_images"], 3)

    def test_protocol_fields_belong_to_normal_token_requests(self):
        request = AddTokenRequest(
            st="session-token",
            protocol_mode="protocol",
            google_cookies="SID=secret",
            login_account="person@example.com",
            proxy_url="127.0.0.1:8080",
            refresh_interval_minutes=45,
        )
        self.assertEqual(request.protocol_mode, "protocol")
        self.assertEqual(request.refresh_interval_minutes, 45)
        self.assertNotIn("protocol_mode", BrowserProfileTokenRequest.model_fields)


class BrowserRuntimeStatusTests(unittest.TestCase):
    def test_status_transitions_are_isolated_and_copy_safe(self):
        reset_runtime_prepare("browser")
        started = start_runtime_prepare("browser", "starting")
        started["message"] = "mutated"
        live = get_runtime_status("browser")
        self.assertEqual(live["state"], "running")
        self.assertEqual(live["message"], "starting")
        finished = finish_runtime_prepare("browser", "ready")
        self.assertEqual(finished["state"], "ready")
        self.assertFalse(finished["active"])
        self.assertIsNotNone(finished["last_completed_at"])


class BrowserEnvironmentPatchTests(unittest.TestCase):
    def test_browser_mode_patch_covers_required_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            browser = TokenBrowser(3, tmp)
            source = browser._build_browser_environment_patch_source()
        for marker in (
            "__flow2apiBrowserEnvironmentV2",
            "hardwareConcurrency",
            "deviceMemory",
            "WebGLRenderingContext",
            "navigator.gpu",
            "navigator.storage",
            "mediaDevices",
            "performance.memory",
            "document.fonts",
            "window.chrome",
        ):
            self.assertIn(marker, source)

    def test_personal_nodriver_bridge_uses_browser_send_and_compat_transaction(self):
        patch_source = inspect.getsource(browser_captcha_personal._patch_nodriver_connection_instance)
        service_source = inspect.getsource(browser_captcha_personal.BrowserCaptchaService)
        self.assertIn("class _CompatTransaction", patch_source)
        self.assertNotIn(".connection.send(", service_source)


class ProtocolLoginUtilityTests(unittest.TestCase):
    def test_cookie_exports_and_cookie_headers_are_parsed(self):
        self.assertEqual(
            parse_google_cookies('[{"name":"SID","value":"one"}]')["SID"],
            "one",
        )
        self.assertEqual(parse_google_cookies("SID=one; HSID=two")["HSID"], "two")

    def test_proxy_notations_are_normalized(self):
        self.assertEqual(normalize_proxy_url("127.0.0.1:8080"), "http://127.0.0.1:8080")
        self.assertEqual(
            normalize_proxy_url("host:1080:user:pass"),
            "http://user:pass@host:1080",
        )
        self.assertIsNone(normalize_proxy_url("ftp://host:21"))

    def test_oauth_redirects_are_allowlisted(self):
        self.assertEqual(
            _validate_redirect("https://accounts.google.com/o/oauth2/auth"),
            "https://accounts.google.com/o/oauth2/auth",
        )
        with self.assertRaises(RuntimeError):
            _validate_redirect("https://example.com/steal")
        with self.assertRaises(RuntimeError):
            _validate_redirect("https://labs.google/not-a-callback", callback_only=True)


class ProtocolRefreshDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_persists_protocol_fields_and_global_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "flow.db"))
            await db.init_db()
            token = Token(
                st="session-token",
                email="person@example.com",
                protocol_mode="protocol",
                google_cookies="SID=secret",
                login_account="person@example.com",
                proxy_url="http://proxy-user:proxy-pass@127.0.0.1:8080",
                refresh_interval_minutes=45,
            )
            token_id = await db.add_token(token)
            stored = await db.get_token(token_id)
            self.assertEqual(stored.protocol_mode, "protocol")
            self.assertEqual(stored.google_cookies, "SID=secret")
            self.assertEqual(stored.refresh_interval_minutes, 45)

            updated = await db.update_token_refresh_config(
                enabled=False,
                refresh_interval_minutes=10_081,
            )
            self.assertFalse(updated.enabled)
            self.assertEqual(updated.refresh_interval_minutes, 10_080)


class ProtocolRefreshManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_token_uses_configured_proxy_for_initial_exchange(self):
        db = MagicMock()
        db.get_token_by_st = AsyncMock(return_value=None)
        db.add_token = AsyncMock(return_value=11)

        flow_client = MagicMock()
        flow_client.proxy_manager.normalize_proxy_url.return_value = "http://proxy:8080"
        flow_client.get_request_fingerprint.return_value = None
        flow_client.st_to_at = AsyncMock(return_value={
            "access_token": "access-token",
            "expires": "2026-08-01T00:00:00Z",
            "user": {"email": "person@example.com"},
        })
        flow_client.get_credits = AsyncMock(return_value={"credits": 12})

        token = await TokenManager(db, flow_client).add_token(
            "session-token",
            proxy_url="proxy:8080",
        )

        self.assertEqual(token.id, 11)
        self.assertEqual(token.proxy_url, "http://proxy:8080")
        flow_client.st_to_at.assert_awaited_once_with("session-token")
        flow_client.get_credits.assert_awaited_once_with("access-token")
        applied_fingerprints = [call.args[0] for call in flow_client._set_request_fingerprint.call_args_list]
        self.assertIn({"proxy_url": "http://proxy:8080"}, applied_fingerprints)

    async def test_due_protocol_token_refreshes_st_at_and_credits(self):
        token = Token(
            id=7,
            st="old-st",
            at="old-at",
            email="person@example.com",
            is_active=True,
            protocol_mode="protocol",
            google_cookies="SID=secret",
            auto_refresh_enabled=True,
            refresh_interval_minutes=1,
            last_st_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        db = MagicMock()
        db.get_token_refresh_config = AsyncMock(return_value=TokenRefreshConfig())
        db.get_active_tokens = AsyncMock(return_value=[token])
        db.get_token = AsyncMock(return_value=token)
        db.update_token = AsyncMock()

        flow_client = MagicMock()
        flow_client.get_request_fingerprint.return_value = None
        flow_client._set_request_fingerprint = MagicMock()
        flow_client.st_to_at = AsyncMock(return_value={
            "access_token": "new-at",
            "expires": "2026-08-01T00:00:00Z",
            "user": {"email": "person@example.com"},
        })
        flow_client.get_credits = AsyncMock(return_value={
            "credits": 321,
            "userPaygateTier": "PAYGATE_TIER_ONE",
        })

        manager = TokenManager(db, flow_client)
        with patch(
            "src.services.protocol_login.protocol_loginer.login",
            new=AsyncMock(return_value={"success": True, "session_token": "new-st"}),
        ):
            await manager.run_protocol_refresh_once()

        updates = [call.kwargs for call in db.update_token.await_args_list]
        self.assertTrue(any(update.get("st") == "new-st" for update in updates))
        self.assertTrue(any(update.get("at") == "new-at" for update in updates))
        self.assertTrue(any(update.get("credits") == 321 for update in updates))

    async def test_not_due_protocol_token_is_skipped(self):
        token = Token(
            id=8,
            st="old-st",
            email="person@example.com",
            protocol_mode="protocol",
            google_cookies="SID=secret",
            last_st_refresh_at=datetime.now(timezone.utc),
            refresh_interval_minutes=120,
        )
        db = MagicMock()
        db.get_token_refresh_config = AsyncMock(return_value=TokenRefreshConfig())
        db.get_active_tokens = AsyncMock(return_value=[token])
        db.update_token = AsyncMock()
        flow_client = MagicMock()
        manager = TokenManager(db, flow_client)
        await manager.run_protocol_refresh_once()
        db.update_token.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
