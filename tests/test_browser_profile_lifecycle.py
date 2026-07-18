import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import main as app_main
from src.api import admin
from src.core.logger import (
    SensitiveAccessLogFilter,
    redact_text_for_log,
    redact_url_for_log,
    sanitize_data_for_log,
    sanitize_headers_for_log,
)
from src.core.models import Token
from src.services.browser_profile_service import (
    BrowserProfileResourceExhaustedError,
    BrowserProfileService,
    ProfileRuntime,
)
from src.services.token_manager import TokenManager


class FakePage:
    def __init__(self, *, closed=False, goto_effects=None):
        self.closed = closed
        self.goto_effects = list(goto_effects or [])
        self.goto_calls = []

    def is_closed(self):
        return self.closed

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if not self.goto_effects:
            return None
        effect = self.goto_effects.pop(0)
        if isinstance(effect, Exception):
            if "target page, context or browser has been closed" in str(effect).lower():
                self.closed = True
            raise effect
        return effect


class FakeContext:
    def __init__(self, page, *, cookies=None, cookie_error=None):
        self.pages = [page]
        self.page = page
        self.close_calls = 0
        self.cookie_rows = list(cookies or [])
        self.cookie_error = cookie_error

    async def new_page(self):
        return self.page

    async def close(self):
        self.close_calls += 1
        self.page.closed = True

    async def cookies(self, *_urls):
        if self.cookie_error is not None:
            raise self.cookie_error
        return list(self.cookie_rows)


class FakeChromium:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.launch_calls = []

    async def launch_persistent_context(self, profile_path, **kwargs):
        self.launch_calls.append((profile_path, kwargs))
        context = self.contexts.pop(0)
        if isinstance(context, Exception):
            raise context
        return context


class FakeFlowClient:
    def __init__(self, *, error=None):
        self.error = error

    async def st_to_at(self, _session_token):
        if self.error is not None:
            raise self.error
        return {
            "access_token": "new-access-token",
            "expires": "2030-01-01T00:00:00Z",
            "user": {"email": "profile@example.com", "name": "Profile"},
        }


class FakeDb:
    def __init__(self, token=None, rows=None):
        self.token = token
        self.rows = list(rows or [])
        self.update_calls = []

    async def get_token(self, token_id):
        if self.token is None or self.token.id != token_id:
            return None
        return self.token

    async def update_token(self, token_id, **changes):
        self.update_calls.append((token_id, changes))
        self.token = self.token.model_copy(update=changes)
        return True

    async def get_all_tokens_with_stats(self):
        return self.rows


def make_token(**overrides):
    values = {
        "id": 24,
        "st": "saved-session-token",
        "at": "saved-access-token",
        "email": "profile@example.com",
        "auth_mode": "browser_profile",
        "browser_profile_status": "connected",
        "browser_profile_login_state": "logged_in",
        "browser_profile_cookie_status": "ok",
        "browser_profile_st_status": "ok",
        "browser_profile_at_status": "ok",
        "is_active": True,
    }
    values.update(overrides)
    return Token(**values)


class BrowserProfileRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = BrowserProfileService()
        self.service.profile_path_for_token = lambda token_id: Path(self.tempdir.name) / f"token-{token_id}"

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    def configure_contexts(self, *contexts):
        chromium = FakeChromium(contexts)
        self.service._playwright = SimpleNamespace(chromium=chromium)
        return chromium

    async def test_reuses_healthy_cached_runtime(self):
        page = FakePage()
        chromium = self.configure_contexts(FakeContext(page))

        first = await self.service._get_runtime(24, open_url="https://example.com/one")
        second = await self.service._get_runtime(24, open_url="https://example.com/two")

        self.assertIs(first, second)
        self.assertEqual(len(chromium.launch_calls), 1)
        self.assertEqual(len(page.goto_calls), 2)

    async def test_replaces_manually_closed_cached_runtime(self):
        stale_page = FakePage(closed=True)
        stale_context = FakeContext(stale_page)
        self.service._runtimes[24] = ProfileRuntime(stale_context, stale_page, asyncio.Lock())
        fresh_page = FakePage()
        fresh_context = FakeContext(fresh_page)
        chromium = self.configure_contexts(fresh_context)

        runtime = await self.service._get_runtime(24, open_url="https://accounts.google.com/")

        self.assertIs(runtime.page, fresh_page)
        self.assertEqual(stale_context.close_calls, 1)
        self.assertEqual(len(chromium.launch_calls), 1)

    async def test_retries_once_when_target_closes_during_navigation(self):
        closed_error = RuntimeError("Page.goto: Target page, context or browser has been closed")
        first_page = FakePage(goto_effects=[closed_error])
        first_context = FakeContext(first_page)
        second_page = FakePage()
        second_context = FakeContext(second_page)
        chromium = self.configure_contexts(first_context, second_context)

        runtime = await self.service._get_runtime(24, open_url="https://accounts.google.com/")

        self.assertIs(runtime.page, second_page)
        self.assertEqual(first_context.close_calls, 1)
        self.assertEqual(len(chromium.launch_calls), 2)
        self.assertEqual(len(second_page.goto_calls), 1)

    async def test_does_not_retry_ordinary_navigation_timeout(self):
        page = FakePage(goto_effects=[TimeoutError("navigation timed out")])
        chromium = self.configure_contexts(FakeContext(page))

        with self.assertRaisesRegex(TimeoutError, "navigation timed out"):
            await self.service._get_runtime(24, open_url="https://accounts.google.com/")

        self.assertEqual(len(chromium.launch_calls), 1)
        self.assertIs(self.service._runtimes[24].page, page)

    async def test_close_runtime_is_idempotent(self):
        page = FakePage()
        context = FakeContext(page)
        self.service._runtimes[24] = ProfileRuntime(context, page, asyncio.Lock())

        self.assertTrue(await self.service.close_runtime(24))
        self.assertFalse(await self.service.close_runtime(24))
        self.assertEqual(context.close_calls, 1)
        self.assertFalse(await self.service.is_runtime_open(24))

    async def test_transient_refresh_closes_scheduler_created_runtime(self):
        token = make_token()
        db = FakeDb(token)
        context = FakeContext(
            FakePage(),
            cookies=[{"name": "__Secure-next-auth.session-token", "value": "new-session-token"}],
        )
        service = BrowserProfileService(db=db, flow_client=FakeFlowClient())
        service.profile_path_for_token = self.service.profile_path_for_token
        service._playwright = SimpleNamespace(chromium=FakeChromium([context]))

        result = await service.refresh_profile(24, retain_runtime=False)

        self.assertEqual(result["profile_status"], "connected")
        self.assertEqual(context.close_calls, 1)
        self.assertFalse(await service.is_runtime_open(24))

    async def test_transient_refresh_closes_runtime_on_failure(self):
        token = make_token()
        db = FakeDb(token)
        context = FakeContext(
            FakePage(),
            cookies=[{"name": "__Secure-next-auth.session-token", "value": "new-session-token"}],
        )
        service = BrowserProfileService(
            db=db,
            flow_client=FakeFlowClient(error=RuntimeError("upstream failed")),
        )
        service.profile_path_for_token = self.service.profile_path_for_token
        service._playwright = SimpleNamespace(chromium=FakeChromium([context]))

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            await service.refresh_profile(24, retain_runtime=False)

        self.assertEqual(context.close_calls, 1)
        self.assertFalse(await service.is_runtime_open(24))

    async def test_transient_refresh_closes_runtime_on_cancellation(self):
        token = make_token()
        db = FakeDb(token)
        context = FakeContext(
            FakePage(),
            cookies=[{"name": "__Secure-next-auth.session-token", "value": "new-session-token"}],
        )
        service = BrowserProfileService(
            db=db,
            flow_client=FakeFlowClient(error=asyncio.CancelledError()),
        )
        service.profile_path_for_token = self.service.profile_path_for_token
        service._playwright = SimpleNamespace(chromium=FakeChromium([context]))

        with self.assertRaises(asyncio.CancelledError):
            await service.refresh_profile(24, retain_runtime=False)

        self.assertEqual(context.close_calls, 1)
        self.assertFalse(await service.is_runtime_open(24))

    async def test_transient_refresh_closes_runtime_when_session_cookie_is_missing(self):
        token = make_token()
        db = FakeDb(token)
        context = FakeContext(FakePage(), cookies=[])
        service = BrowserProfileService(db=db, flow_client=FakeFlowClient())
        service.profile_path_for_token = self.service.profile_path_for_token
        service._playwright = SimpleNamespace(chromium=FakeChromium([context]))

        result = await service.refresh_profile(24, retain_runtime=False)

        self.assertEqual(result["profile_status"], "login_needed")
        self.assertEqual(context.close_calls, 1)
        self.assertFalse(await service.is_runtime_open(24))

    async def test_transient_refresh_preserves_admin_pinned_runtime(self):
        token = make_token()
        db = FakeDb(token)
        context = FakeContext(
            FakePage(),
            cookies=[{"name": "__Secure-next-auth.session-token", "value": "new-session-token"}],
        )
        service = BrowserProfileService(db=db, flow_client=FakeFlowClient())
        service.profile_path_for_token = self.service.profile_path_for_token
        service._playwright = SimpleNamespace(chromium=FakeChromium([context]))

        await service.open_profile(24)
        await service.refresh_profile(24, retain_runtime=False)

        self.assertTrue(service._runtimes[24].pinned)
        self.assertEqual(context.close_calls, 0)
        self.assertTrue(await service.is_runtime_open(24))

    async def test_resource_exhaustion_is_normalized(self):
        chromium = self.configure_contexts(RuntimeError("spawn /chrome EAGAIN"))

        with self.assertRaises(BrowserProfileResourceExhaustedError):
            await self.service._get_runtime(24)

        self.assertEqual(len(chromium.launch_calls), 1)
        self.assertNotIn(24, self.service._runtimes)

    async def test_close_unpinned_and_close_all_preserve_then_release_pinned(self):
        transient_context = FakeContext(FakePage())
        pinned_context = FakeContext(FakePage())
        self.service._runtimes = {
            24: ProfileRuntime(transient_context, transient_context.page, asyncio.Lock()),
            25: ProfileRuntime(pinned_context, pinned_context.page, asyncio.Lock(), pinned=True),
        }
        playwright = SimpleNamespace(stop=AsyncMock())
        self.service._playwright = playwright

        self.assertEqual(await self.service.close_unpinned_runtimes(), 1)
        self.assertEqual(transient_context.close_calls, 1)
        self.assertIn(25, self.service._runtimes)
        self.assertEqual(await self.service.close_all(), 1)
        self.assertEqual(pinned_context.close_calls, 1)
        playwright.stop.assert_awaited_once()


class BrowserProfileAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_profile_preserves_authentication_and_enabled_state(self):
        token = make_token()
        db = FakeDb(token)
        service = BrowserProfileService(db=db)
        page = FakePage()
        context = FakeContext(page)
        service._runtimes[24] = ProfileRuntime(context, page, asyncio.Lock())

        result = await service.close_profile(24)

        self.assertFalse(result["runtime_open"])
        self.assertEqual(db.update_calls, [])
        self.assertEqual(db.token.st, "saved-session-token")
        self.assertEqual(db.token.at, "saved-access-token")
        self.assertEqual(db.token.browser_profile_status, "connected")
        self.assertTrue(db.token.is_active)

    async def test_close_profile_rejects_missing_token(self):
        service = BrowserProfileService(db=FakeDb())

        with self.assertRaisesRegex(ValueError, "Token not found"):
            await service.close_profile(999)

    async def test_open_profile_preserves_connected_health(self):
        token = make_token()
        db = FakeDb(token)
        service = BrowserProfileService(db=db)
        with tempfile.TemporaryDirectory() as tempdir:
            service.profile_path_for_token = lambda token_id: Path(tempdir) / f"token-{token_id}"
            page = FakePage()
            chromium = FakeChromium([FakeContext(page)])
            service._playwright = SimpleNamespace(chromium=chromium)

            result = await service.open_profile(24)

        self.assertEqual(result["profile_status"], "connected")
        self.assertEqual(result["login_state"], "logged_in")
        self.assertTrue(result["runtime_open"])
        self.assertTrue(db.token.is_active)

    async def test_token_list_reports_actual_runtime_state(self):
        row = {
            "id": 24,
            "auth_mode": "browser_profile",
            "email": "profile@example.com",
            "is_active": True,
        }
        fake_db = FakeDb(rows=[row])
        profile_service = SimpleNamespace(is_runtime_open=AsyncMock(return_value=True))

        with (
            patch.object(admin, "db", fake_db),
            patch.object(admin, "token_manager", SimpleNamespace(flow_client=None)),
            patch.object(admin.BrowserProfileService, "get_instance", AsyncMock(return_value=profile_service)),
        ):
            result = await admin.get_tokens(token="admin-token")

        self.assertTrue(result[0]["runtime_open"])
        profile_service.is_runtime_open.assert_awaited_once_with(24)

    async def test_admin_sync_and_refresh_are_transient_unless_runtime_is_pinned(self):
        profile_service = SimpleNamespace(
            sync_profile=AsyncMock(
                return_value={"profile_status": "connected", "st_status": "ok"}
            ),
            refresh_profile=AsyncMock(
                return_value={"profile_status": "connected", "st_status": "ok"}
            ),
        )
        token_manager = SimpleNamespace(
            flow_client=None,
            enable_token=AsyncMock(),
        )

        with (
            patch.object(
                admin.BrowserProfileService,
                "get_instance",
                AsyncMock(return_value=profile_service),
            ),
            patch.object(admin, "token_manager", token_manager),
        ):
            await admin.sync_browser_profile(24, token="admin-token")
            await admin.refresh_browser_profile(24, token="admin-token")

        profile_service.sync_profile.assert_awaited_once_with(24, retain_runtime=False)
        profile_service.refresh_profile.assert_awaited_once_with(24, retain_runtime=False)
        self.assertEqual(token_manager.enable_token.await_count, 2)


class BrowserProfileSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_manager_uses_transient_profile_refresh(self):
        token = make_token()
        db = FakeDb(token)
        profile_service = SimpleNamespace(
            refresh_profile=AsyncMock(
                return_value={"profile_status": "connected", "st_status": "ok"}
            )
        )
        manager = TokenManager(db, flow_client=None)

        with patch.object(
            BrowserProfileService,
            "get_instance",
            AsyncMock(return_value=profile_service),
        ):
            result = await manager._try_refresh_st(24, token)

        self.assertEqual(result, token.st)
        profile_service.refresh_profile.assert_awaited_once_with(24, retain_runtime=False)
        self.assertEqual(manager.consume_st_refresh_reason(24), "success_browser_profile")

    async def test_token_manager_marks_resource_exhaustion(self):
        token = make_token()
        db = FakeDb(token)
        profile_service = SimpleNamespace(
            refresh_profile=AsyncMock(
                side_effect=BrowserProfileResourceExhaustedError("capacity exhausted")
            )
        )
        manager = TokenManager(db, flow_client=None)

        with patch.object(
            BrowserProfileService,
            "get_instance",
            AsyncMock(return_value=profile_service),
        ):
            result = await manager._try_refresh_st(24, token)

        self.assertIsNone(result)
        self.assertEqual(
            manager.consume_st_refresh_reason(24),
            "browser_profile_resource_exhausted",
        )

    async def test_resource_exhaustion_aborts_batch_and_reclaims_transient_runtimes(self):
        profile_service = SimpleNamespace(close_unpinned_runtimes=AsyncMock(return_value=3))

        with (
            patch.object(
                BrowserProfileService,
                "get_existing_instance",
                return_value=profile_service,
            ),
            patch.object(app_main.debug_logger, "log_error") as log_error,
        ):
            should_abort = await app_main._abort_refresh_batch_on_resource_exhaustion(
                source="ST_SCHEDULER",
                reason="browser_profile_resource_exhausted",
            )

        self.assertTrue(should_abort)
        profile_service.close_unpinned_runtimes.assert_awaited_once()
        self.assertIn("stopped this batch", log_error.call_args.args[0])

    async def test_non_resource_failure_does_not_abort_batch(self):
        self.assertFalse(
            await app_main._abort_refresh_batch_on_resource_exhaustion(
                source="ST_SCHEDULER",
                error=RuntimeError("ordinary upstream failure"),
            )
        )


class ProductionHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_database_ready(self):
        with patch.object(
            admin,
            "build_public_health_snapshot",
            AsyncMock(return_value={"backend_running": True, "has_active_tokens": True}),
        ):
            result = await admin.health_check()

        self.assertTrue(result["database_ready"])
        self.assertTrue(result["has_active_tokens"])

    async def test_health_returns_503_when_database_is_unavailable(self):
        with patch.object(
            admin,
            "build_public_health_snapshot",
            AsyncMock(side_effect=RuntimeError("can't start new thread")),
        ):
            result = await admin.health_check()

        self.assertEqual(result.status_code, 503)
        payload = json.loads(result.body)
        self.assertFalse(payload["database_ready"])
        self.assertEqual(payload["error"], "database_unavailable")
        self.assertNotIn("thread", result.body.decode("utf-8").lower())


class ProductionLogRedactionTests(unittest.TestCase):
    def test_uvicorn_log_config_redacts_access_and_websocket_handlers(self):
        from main import build_uvicorn_log_config

        log_config = build_uvicorn_log_config()

        self.assertIn("sensitive_query", log_config["filters"])
        self.assertIn("sensitive_query", log_config["handlers"]["access"]["filters"])
        self.assertIn("sensitive_query", log_config["handlers"]["default"]["filters"])

    def test_redacts_headers_nested_bodies_urls_and_text(self):
        secrets = ["header-secret", "cookie-secret", "query-secret", "body-secret", "password-secret"]
        headers = sanitize_headers_for_log(
            {
                "Authorization": "Bearer header-secret",
                "Set-Cookie": "session=cookie-secret",
                "X-Test": "safe",
            }
        )
        body = sanitize_data_for_log(
            {
                "token_id": 24,
                "access_token": "body-secret",
                "nested": {"password": "password-secret"},
            }
        )
        url = redact_url_for_log("/captcha_ws?key=query-secret&instance_id=safe")
        text = redact_text_for_log("Authorization: Bearer header-secret")
        rendered = repr(headers) + repr(body) + url + text

        for secret in secrets:
            self.assertNotIn(secret, rendered)
        self.assertEqual(headers["X-Test"], "safe")
        self.assertEqual(body["token_id"], 24)
        self.assertIn("instance_id=safe", url)

    def test_uvicorn_access_filter_masks_websocket_key(self):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:1234",
                "WebSocket",
                "/captcha_ws?key=websocket-secret&instance_id=safe",
                "1.1",
                101,
            ),
            None,
        )

        self.assertTrue(SensitiveAccessLogFilter().filter(record))
        rendered = record.getMessage()
        self.assertNotIn("websocket-secret", rendered)
        self.assertIn("instance_id=safe", rendered)

    def test_uvicorn_default_filter_masks_websocket_key(self):
        record = logging.LogRecord(
            "uvicorn.error",
            logging.INFO,
            __file__,
            1,
            '%s - "WebSocket %s" [accepted]',
            (
                "127.0.0.1:1234",
                "/captcha_ws?key=websocket-secret&instance_id=safe",
            ),
            None,
        )

        self.assertTrue(SensitiveAccessLogFilter().filter(record))
        rendered = record.getMessage()
        self.assertNotIn("websocket-secret", rendered)
        self.assertIn("instance_id=safe", rendered)


class HeadedContainerLifecycleTests(unittest.TestCase):
    def test_railway_headed_image_uses_tini_as_pid_one(self):
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile.headed").read_text(
            encoding="utf-8"
        )

        self.assertRegex(dockerfile, r"(?m)^\s*tini\s*\\$")
        self.assertIn('ENTRYPOINT ["/usr/bin/tini", "--"]', dockerfile)


if __name__ == "__main__":
    unittest.main()
