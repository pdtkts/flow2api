import asyncio

from src.services.browser_captcha_extension import ExtensionCaptchaService


class _DummyWebSocket:
    async def send_text(self, _payload: str):
        return None


def test_submit_generation_uses_token_aware_selection_context():
    service = ExtensionCaptchaService(db=None)
    captured = {}

    async def _wait_for_connection(**kwargs):
        captured.update(kwargs)
        class _Conn:
            websocket = _DummyWebSocket()
            worker_session_id = "worker-1"
            route_key = ""
            dispatch_lock = asyncio.Lock()
        return _Conn()

    async def _request_once(conn, *, message_type, request_payload, timeout):
        _ = conn, message_type, request_payload, timeout
        return {"status": "success", "response_status": 200, "response_json": {"ok": True}}

    service._wait_for_connection = _wait_for_connection  # type: ignore[attr-defined]
    service._generation_request_once = _request_once  # type: ignore[attr-defined]

    async def _run():
        result = await service.submit_generation_via_extension(
            url="https://example.com/gen",
            method="POST",
            headers={},
            json_data={"x": 1},
            token_id=6,
            managed_api_key_id=2,
            timeout=30,
        )
        assert result["status"] == "success"

    asyncio.run(_run())
    assert captured.get("preferred_token_id") == 6
    assert captured.get("managed_api_key_id") == 2


def test_poll_generation_uses_token_aware_selection_context():
    service = ExtensionCaptchaService(db=None)
    captured = {}

    async def _wait_for_connection(**kwargs):
        captured.update(kwargs)
        class _Conn:
            websocket = _DummyWebSocket()
            worker_session_id = "worker-2"
            route_key = ""
            dispatch_lock = asyncio.Lock()
        return _Conn()

    async def _request_once(conn, *, message_type, request_payload, timeout):
        _ = conn, message_type, request_payload, timeout
        return {"status": "success", "response_status": 200, "response_json": {"ok": True}}

    service._wait_for_connection = _wait_for_connection  # type: ignore[attr-defined]
    service._generation_request_once = _request_once  # type: ignore[attr-defined]

    async def _run():
        result = await service.poll_generation_via_extension(
            url="https://example.com/poll",
            method="POST",
            headers={},
            json_data={"ops": []},
            token_id=9,
            managed_api_key_id=3,
            timeout=20,
        )
        assert result["status"] == "success"

    asyncio.run(_run())
    assert captured.get("preferred_token_id") == 9
    assert captured.get("managed_api_key_id") == 3


def test_submit_generation_raises_when_no_worker_available():
    service = ExtensionCaptchaService(db=None)

    async def _wait_for_connection(**_kwargs):
        return None

    service._wait_for_connection = _wait_for_connection  # type: ignore[attr-defined]

    async def _run():
        try:
            await service.submit_generation_via_extension(
                url="https://example.com/gen",
                method="POST",
                headers={},
                json_data={},
                token_id=1,
                managed_api_key_id=2,
                timeout=5,
            )
        except RuntimeError as exc:
            assert "No extension worker available for generation submit" in str(exc)
            return
        raise AssertionError("Expected RuntimeError when no extension worker is available")

    asyncio.run(_run())
