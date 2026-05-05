import asyncio

from src.core.config import config
from src.services.flow_client import FlowClient


class _DummyProxyManager:
    async def get_proxy_url(self):
        return None

    async def get_next_proxy(self):
        return None

    async def get_current_proxy(self):
        return None


def test_make_request_routes_generation_submit_to_extension_when_enabled():
    client = FlowClient(proxy_manager=_DummyProxyManager(), db=None)
    config.set_extension_generation_enabled(True)
    config.set_captcha_method("extension")
    client.set_force_local_http(False)

    called = {"count": 0, "token_id": None}

    async def _submit_generation(**kwargs):
        called["count"] += 1
        called["token_id"] = kwargs.get("token_id")
        return {"operations": [{"status": "MEDIA_GENERATION_STATUS_ACTIVE"}]}

    async def _run():
        client.extension_generation_service.submit_generation = _submit_generation
        client.set_active_generation_token_id(6)
        result = await client._make_request(
            "POST",
            f"{client.api_base_url}/video:batchAsyncGenerateVideoText",
            headers={"Authorization": "Bearer test"},
            json_data={
                "generateVideoInput": {"dummy": True},
                "clientContext": {
                    "recaptchaContext": {
                        "token": "captcha-token",
                    }
                },
            },
            timeout=10,
        )
        assert "operations" in result

    asyncio.run(_run())
    assert called["count"] == 1
    assert called["token_id"] == 6
