"""Tests for API-captcha User-Agent and proxy binding."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.config import config
from src.services.flow_client import FlowClient


PROVIDER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class _FakeProxyManager:
    def __init__(self, proxy_url="http://127.0.0.1:8080"):
        self.proxy_url = proxy_url
        self.calls = 0

    async def get_request_proxy_url(self):
        self.calls += 1
        return self.proxy_url

    async def get_proxy_url(self):
        return self.proxy_url


class _RotatingProxyManager:
    def __init__(self):
        self.calls = 0

    async def get_request_proxy_url(self):
        self.calls += 1
        return None if self.calls == 1 else "http://different-proxy:8080"


class _FakeCaptchaSession:
    def __init__(self, user_agent=PROVIDER_UA):
        self.calls = []
        self.user_agent = user_agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, **kwargs):
        self.calls.append(kwargs)
        response = MagicMock()
        response.status_code = 200
        if len(self.calls) == 1:
            response.json.return_value = {"errorId": 0, "taskId": "task-1"}
        else:
            response.json.return_value = {
                "errorId": 0,
                "status": "ready",
                "solution": {
                    "gRecaptchaResponse": "captcha-token",
                    "userAgent": self.user_agent,
                },
            }
        return response


class ApiCaptchaProviderResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_returns_token_and_user_agent_using_supplied_proxy(self):
        client = FlowClient(proxy_manager=_FakeProxyManager())
        session = _FakeCaptchaSession()
        with (
            patch("src.services.flow_client.AsyncSession", return_value=session),
            patch("src.services.flow_client.config") as captcha_config,
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            captcha_config.yescaptcha_api_key = "test-key"
            captcha_config.yescaptcha_base_url = "https://api.yescaptcha.test"
            captcha_config.yescaptcha_task_type = "RecaptchaV3TaskProxylessM1S9"
            result = await client._get_api_captcha_token(
                "yescaptcha",
                "project-1",
                proxy_url="http://127.0.0.1:8080",
                proxy_resolved=True,
                user_agent="Solver UA",
            )

        self.assertEqual(result, ("captcha-token", PROVIDER_UA))
        self.assertEqual(
            session.calls[0]["proxies"],
            {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"},
        )
        self.assertEqual(session.calls[0]["json"]["task"]["userAgent"], "Solver UA")


class ApiCaptchaFingerprintTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_method = config.captcha_method
        config.set_captcha_method("yescaptcha")

    async def asyncTearDown(self):
        config.set_captcha_method(self.previous_method)

    async def test_provider_user_agent_merges_with_exact_solver_proxy(self):
        proxy_manager = _FakeProxyManager()
        client = FlowClient(proxy_manager=proxy_manager)
        client._get_api_captcha_token = AsyncMock(return_value=("captcha-token", PROVIDER_UA))

        token, browser_id = await client._get_recaptcha_token("project-1")

        self.assertEqual((token, browser_id), ("captcha-token", None))
        self.assertEqual(proxy_manager.calls, 1)
        expected_solver_ua = client._generate_user_agent("project-1")
        client._get_api_captcha_token.assert_awaited_once_with(
            "yescaptcha",
            "project-1",
            "IMAGE_GENERATION",
            proxy_url="http://127.0.0.1:8080",
            proxy_resolved=True,
            user_agent=expected_solver_ua,
        )
        fingerprint = client.get_request_fingerprint()
        self.assertEqual(fingerprint["proxy_url"], "http://127.0.0.1:8080")
        self.assertEqual(fingerprint["user_agent"], PROVIDER_UA)
        self.assertEqual(fingerprint["project_id"], "project-1")
        self.assertEqual(fingerprint["origin"], "https://labs.google")
        self.assertIn('"Google Chrome";v="147"', fingerprint["sec_ch_ua"])

    async def test_legacy_plain_token_and_missing_user_agent_remain_usable(self):
        client = FlowClient(proxy_manager=_FakeProxyManager())
        client._get_api_captcha_token = AsyncMock(return_value="legacy-token")

        token, _ = await client._get_recaptcha_token("project-1")

        self.assertEqual(token, "legacy-token")
        fingerprint = client.get_request_fingerprint()
        self.assertEqual(fingerprint["proxy_url"], "http://127.0.0.1:8080")
        self.assertEqual(fingerprint["project_id"], "project-1")
        self.assertTrue(fingerprint["user_agent"])

    async def test_failed_provider_result_clears_fingerprint(self):
        client = FlowClient(proxy_manager=_FakeProxyManager())
        client._get_api_captcha_token = AsyncMock(return_value=None)

        self.assertEqual(await client._get_recaptcha_token("project-1"), (None, None))
        self.assertIsNone(client.get_request_fingerprint())

    async def test_direct_solver_binding_blocks_later_rotating_proxy(self):
        proxy_manager = _RotatingProxyManager()
        client = FlowClient(proxy_manager=proxy_manager)
        client._get_api_captcha_token = AsyncMock(return_value=("captcha-token", PROVIDER_UA))
        await client._get_recaptcha_token("project-1")
        self.assertEqual(client.get_request_fingerprint()["proxy_url"], "")

        captured = {}
        response = SimpleNamespace(status_code=200, headers={}, text="{}", json=lambda: {})

        class FakeFlowSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, _url, **kwargs):
                captured.update(kwargs)
                return response

        with patch("src.services.flow_client.AsyncSession", return_value=FakeFlowSession()):
            await client._make_request("POST", "https://aisandbox-pa.googleapis.com/v1/test", json_data={})
        self.assertIsNone(captured["proxy"])

    async def test_flow_request_uses_provider_user_agent_and_matching_client_hints(self):
        proxy_manager = _FakeProxyManager()
        client = FlowClient(proxy_manager=proxy_manager)
        client._get_api_captcha_token = AsyncMock(return_value=("captcha-token", PROVIDER_UA))
        await client._get_recaptcha_token("project-1")

        captured = {}
        response = SimpleNamespace(status_code=200, headers={}, text="{}", json=lambda: {})

        class FakeFlowSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, _url, **kwargs):
                captured.update(kwargs)
                return response

        with patch("src.services.flow_client.AsyncSession", return_value=FakeFlowSession()):
            await client._make_request(
                "POST",
                "https://aisandbox-pa.googleapis.com/v1/test",
                json_data={"clientContext": {"projectId": "project-1"}},
            )

        headers = captured["headers"]
        self.assertEqual(headers["User-Agent"], PROVIDER_UA)
        self.assertIn('"Google Chrome";v="147"', headers["sec-ch-ua"])
        self.assertEqual(headers["sec-ch-ua-platform"], '"Windows"')
        self.assertEqual(captured["proxy"], "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()
