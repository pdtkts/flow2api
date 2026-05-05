import asyncio

from src.services.generation_handler import GenerationHandler
from src.core.config import config


class _DummyFlowClient:
    pass


class _DummyDb:
    pass


def _make_handler() -> GenerationHandler:
    return GenerationHandler(
        flow_client=_DummyFlowClient(),
        token_manager=None,
        load_balancer=None,
        db=_DummyDb(),
        concurrency_manager=None,
        proxy_manager=None,
    )


def test_extension_fallback_classifier_matches_recaptcha_rejection():
    handler = _make_handler()
    config.set_extension_generation_fallback_mode("local_http_on_recaptcha")
    assert handler._should_fallback_to_local_http(Exception("PUBLIC_ERROR_UNUSUAL_ACTIVITY: reCAPTCHA evaluation failed"))
    assert not handler._should_fallback_to_local_http(Exception("timeout while uploading image"))


def test_extension_fallback_mode_none_disables_fallback():
    handler = _make_handler()
    config.set_extension_generation_fallback_mode("none")
    assert not handler._should_fallback_to_local_http(Exception("PUBLIC_ERROR_UNUSUAL_ACTIVITY: reCAPTCHA evaluation failed"))


def test_execute_with_extension_fallback_uses_local_when_primary_recaptcha_fails():
    handler = _make_handler()
    config.set_extension_generation_enabled(True)
    config.set_extension_generation_fallback_mode("local_http_on_recaptcha")

    async def _run():
        async def extension_op():
            raise RuntimeError("PUBLIC_ERROR_UNUSUAL_ACTIVITY: reCAPTCHA evaluation failed")

        async def local_op():
            return {"ok": True, "path": "local"}

        result = await handler._execute_with_extension_fallback(
            "unit_test_submit",
            extension_op,
            local_op,
        )
        assert result["ok"] is True
        assert result["path"] == "local"

    asyncio.run(_run())
