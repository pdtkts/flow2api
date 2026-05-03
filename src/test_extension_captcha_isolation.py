import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest

# Test environment may not install Prometheus extras required by unrelated imports.
if "prometheus_client" not in sys.modules:
    prom_stub = types.ModuleType("prometheus_client")

    class _NoopMetric:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            return None

        def observe(self, *args, **kwargs):
            return None

    class _CollectorRegistry:
        def __init__(self, *args, **kwargs):
            pass

    prom_stub.Counter = lambda *args, **kwargs: _NoopMetric()
    prom_stub.Gauge = lambda *args, **kwargs: _NoopMetric()
    prom_stub.Histogram = lambda *args, **kwargs: _NoopMetric()
    prom_stub.CollectorRegistry = _CollectorRegistry
    prom_stub.generate_latest = lambda *args, **kwargs: b""
    prom_stub.CONTENT_TYPE_LATEST = "text/plain"
    sys.modules["prometheus_client"] = prom_stub

from src.services.browser_captcha_extension import ExtensionCaptchaService


class FakeWebSocket:
    def __init__(self, query_params=None):
        self.query_params = query_params or {}
        self.headers = {}
        self.sent_payloads: list[dict] = []

    async def accept(self):
        return None

    async def send_text(self, data: str):
        self.sent_payloads.append(json.loads(data))


class FakeDB:
    def __init__(self):
        self.bindings: dict[str, int] = {}
        self.tokens: dict[int, SimpleNamespace] = {}
        self.captcha_timeout = 3
        self.api_keys: set[int] = {1, 2, 3}
        self.extension_fallback_to_managed_on_dedicated_failure = False

    async def get_token(self, token_id: int):
        return self.tokens.get(token_id)

    async def get_captcha_config(self):
        return SimpleNamespace(
            extension_queue_wait_timeout_seconds=self.captcha_timeout,
            extension_fallback_to_managed_on_dedicated_failure=self.extension_fallback_to_managed_on_dedicated_failure,
        )

    async def get_extension_worker_binding_for_route_key(self, route_key: str):
        if route_key in self.bindings:
            return {"route_key": route_key, "api_key_id": self.bindings[route_key]}
        return None

    async def upsert_extension_worker_binding(self, route_key: str, api_key_id: int):
        self.bindings[route_key] = int(api_key_id)

    async def delete_extension_worker_binding(self, route_key: str):
        self.bindings.pop(route_key, None)

    async def get_api_key_detail(self, key_id: int, include_plaintext: bool = False):
        _ = include_plaintext
        if int(key_id) in self.api_keys:
            return {"id": int(key_id), "label": f"key-{key_id}"}
        return None


def test_extension_get_token_isolated_by_managed_api_key():
    async def _run():
        ExtensionCaptchaService._instance = None
        db = FakeDB()
        db.tokens[100] = SimpleNamespace(id=100, extension_route_key="rk-1")
        db.bindings["rk-1"] = 1
        service = await ExtensionCaptchaService.get_instance(db=db)

        ws = FakeWebSocket({"route_key": "rk-1", "managed_api_key_id": "1"})
        await service.connect(ws)

        token_task = asyncio.create_task(
            service.get_token(
                project_id="p1",
                action="IMAGE_GENERATION",
                timeout=2,
                token_id=100,
                managed_api_key_id=1,
            )
        )

        for _ in range(20):
            if ws.sent_payloads:
                break
            await asyncio.sleep(0.05)
        assert ws.sent_payloads
        req_id = ws.sent_payloads[-1]["req_id"]

        await service.handle_message(
            ws,
            json.dumps({"req_id": req_id, "status": "success", "token": "tok-abc"}),
        )
        token, _ext_rid = await asyncio.wait_for(token_task, timeout=2)
        assert token == "tok-abc"

        with pytest.raises(RuntimeError):
            await service.get_token(
                project_id="p1",
                action="IMAGE_GENERATION",
                timeout=1,
                token_id=100,
                managed_api_key_id=2,
            )

    asyncio.run(_run())


def test_extension_get_token_fallback_after_dedicated_failure():
    async def _run():
        ExtensionCaptchaService._instance = None
        db = FakeDB()
        db.extension_fallback_to_managed_on_dedicated_failure = True
        db.captcha_timeout = 3
        db.tokens[100] = SimpleNamespace(id=100, extension_route_key="rk1")
        db.bindings["rk1"] = 1
        db.bindings["rk2"] = 1
        service = await ExtensionCaptchaService.get_instance(db=db)

        ws_d = FakeWebSocket({"route_key": "rk1", "managed_api_key_id": "1"})
        await service.connect(ws_d, authenticated_worker={"id": 1, "token_id": 100})
        ws_u = FakeWebSocket({"route_key": "rk2", "managed_api_key_id": "1"})
        await service.connect(ws_u, authenticated_managed_api_key_id=1)

        token_task = asyncio.create_task(
            service.get_token(
                project_id="p1",
                action="IMAGE_GENERATION",
                timeout=2,
                token_id=100,
                managed_api_key_id=1,
            )
        )

        for _ in range(50):
            if ws_d.sent_payloads:
                break
            await asyncio.sleep(0.05)
        assert ws_d.sent_payloads
        req1 = ws_d.sent_payloads[-1]["req_id"]
        await service.handle_message(
            ws_d,
            json.dumps({"req_id": req1, "status": "error", "error": "dedicated_failed"}),
        )

        for _ in range(50):
            if ws_u.sent_payloads:
                break
            await asyncio.sleep(0.05)
        assert ws_u.sent_payloads
        req2 = ws_u.sent_payloads[-1]["req_id"]
        await service.handle_message(
            ws_u,
            json.dumps({"req_id": req2, "status": "success", "token": "tok-fallback"}),
        )
        token, _ext_rid = await asyncio.wait_for(token_task, timeout=2)
        assert token == "tok-fallback"

    asyncio.run(_run())


def test_extension_waits_queue_until_worker_connects():
    async def _run():
        ExtensionCaptchaService._instance = None
        db = FakeDB()
        db.captcha_timeout = 2
        db.tokens[101] = SimpleNamespace(id=101, extension_route_key="rk-wait")
        db.bindings["rk-wait"] = 2
        service = await ExtensionCaptchaService.get_instance(db=db)

        token_task = asyncio.create_task(
            service.get_token(
                project_id="p2",
                action="VIDEO_GENERATION",
                timeout=2,
                token_id=101,
                managed_api_key_id=2,
            )
        )

        await asyncio.sleep(0.2)
        ws = FakeWebSocket({"route_key": "rk-wait", "managed_api_key_id": "2"})
        await service.connect(ws)

        for _ in range(20):
            if ws.sent_payloads:
                break
            await asyncio.sleep(0.05)
        assert ws.sent_payloads
        req_id = ws.sent_payloads[-1]["req_id"]

        await service.handle_message(
            ws,
            json.dumps({"req_id": req_id, "status": "success", "token": "tok-wait"}),
        )
        token, _ext_rid = await asyncio.wait_for(token_task, timeout=2)
        assert token == "tok-wait"

    asyncio.run(_run())
