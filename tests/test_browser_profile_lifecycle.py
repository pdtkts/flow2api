import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.api import admin
from src.core.models import Token
from src.services.browser_profile_service import BrowserProfileService, ProfileRuntime


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
    def __init__(self, page):
        self.pages = [page]
        self.page = page
        self.close_calls = 0

    async def new_page(self):
        return self.page

    async def close(self):
        self.close_calls += 1
        self.page.closed = True


class FakeChromium:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.launch_calls = []

    async def launch_persistent_context(self, profile_path, **kwargs):
        self.launch_calls.append((profile_path, kwargs))
        return self.contexts.pop(0)


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


if __name__ == "__main__":
    unittest.main()
