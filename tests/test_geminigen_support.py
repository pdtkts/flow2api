import asyncio
import base64
import json
import time
from types import SimpleNamespace

from src.services.geminigen_service import (
    GEMINIGEN_CAPACITY_ERROR_CODE,
    GeminiGenService,
    GeminiGenUpstreamError,
)
from src.api import routes
from src.core.geminigen_manifest import GEMINIGEN_MODEL_BY_ID, GEMINIGEN_MODEL_MANIFEST
from src.core.models import GeminiGenAccount, GeminiGenTask
from src.core.studio_model_catalog import geminigen_studio_metadata, native_studio_metadata


def test_extract_artifact_urls_prefers_final_download_url_over_preview():
    payload = {
        "generated_image": {
            "image_url": "https://cdn.example/preview.jpg",
        },
        "file_download_url": "https://cdn.example/final.png",
    }

    assert GeminiGenService.extract_artifact_urls(payload, "image") == [
        "https://cdn.example/final.png"
    ]


def test_extract_artifact_urls_falls_back_to_preview_without_download_url():
    payload = {
        "generated_image": {
            "image_url": "https://cdn.example/preview.jpg",
        },
    }

    assert GeminiGenService.extract_artifact_urls(payload, "image") == [
        "https://cdn.example/preview.jpg"
    ]


def test_veo_frame_images_are_real_multipart_files():
    service = object.__new__(GeminiGenService)
    image = b"\x89PNG\r\n\x1a\ncontent"

    form = service._build_form(
        public_model_id="geminigen-veo-3.1-fast-i2v-frame-landscape-720p-4s",
        prompt="A storefront at sunrise",
        images=[image],
        options={
            "endpoint_type": "veo-video",
            "options": {
                "model": "veo-3-fast",
                "reference_mode": "frame",
                "duration": "4",
                "resolution": "720p",
                "aspect_ratio": "16:9",
            },
        },
        extra_options={},
        account=GeminiGenAccount(id=1, name="test", bearer_token="token"),
    )

    assert form["mode_image"] == "frame"
    assert "ref_images" not in form
    assert form["_file_parts"] == [
        {
            "name": "ref_images",
            "data": image,
            "filename": "ref_image_1.png",
            "content_type": "image/png",
        }
    ]


def test_veo_ingredient_prompt_gets_reference_tags():
    service = object.__new__(GeminiGenService)
    form = service._build_form(
        public_model_id="geminigen-veo-3.1-fast-i2v-ingredient-landscape-720p-4s",
        prompt="Place the subject outside the store",
        images=[b"\xff\xd8\xffcontent"],
        options={
            "endpoint_type": "veo-video",
            "options": {"model": "veo-3-fast", "reference_mode": "ingredient"},
        },
        extra_options={},
        account=GeminiGenAccount(id=1, name="test", bearer_token="token"),
    )

    assert form["mode_image"] == "ingredient"
    assert form["prompt"].startswith("@image1 ")
    assert form["_file_parts"][0]["content_type"] == "image/jpeg"


def test_concurrent_token_refresh_is_serialized_per_account():
    def token(exp: int) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    stale = GeminiGenAccount(
        id=1,
        name="test",
        bearer_token=token(int(time.time()) - 60),
        refresh_token="refresh",
    )

    class FakeDatabase:
        account = stale

        async def get_geminigen_account(self, account_id):
            return self.account

    service = object.__new__(GeminiGenService)
    service.db = FakeDatabase()
    service._token_refresh_locks = {}
    refresh_calls = 0

    async def refresh_once(account, base_url):
        nonlocal refresh_calls
        refresh_calls += 1
        await asyncio.sleep(0.01)
        service.db.account = account.model_copy(
            update={"bearer_token": token(int(time.time()) + 3600), "refresh_token": "rotated"}
        )
        return service.db.account

    service._refresh_account_token_unlocked = refresh_once

    async def run():
        return await asyncio.gather(
            service._refresh_account_token(stale, "https://api.geminigen.ai"),
            service._refresh_account_token(stale, "https://api.geminigen.ai"),
        )

    refreshed = asyncio.run(run())

    assert refresh_calls == 1
    assert all(account.refresh_token == "rotated" for account in refreshed)


def test_geminigen_capacity_error_is_retryable_and_sanitized():
    body = json.dumps(
        {
            "detail": {
                "error_code": GEMINIGEN_CAPACITY_ERROR_CODE,
                "error_message": "You have reached the maximum number of 5 concurrent image generations allowed by your plan.",
            }
        }
    )

    error = GeminiGenService._extract_upstream_error(400, body)

    assert error.retryable_capacity is True
    assert error.error_code == GEMINIGEN_CAPACITY_ERROR_CODE
    assert "capacity is full" in str(error)
    assert "/api/generate_image" not in str(error)


def test_geminigen_non_capacity_400_is_not_retryable():
    body = json.dumps({"detail": {"error_code": "BAD_PROMPT", "error_message": "Prompt is invalid"}})

    error = GeminiGenService._extract_upstream_error(400, body)

    assert error.retryable_capacity is False
    assert error.error_code == "BAD_PROMPT"
    assert "Prompt is invalid" in str(error)


class FakeGeminiGenDatabase:
    def __init__(self, *, timeout_image_sec=3.0):
        self.account = GeminiGenAccount(id=1, label="primary", bearer_token="token", image_concurrency=1, image_in_flight=0)
        self.task = GeminiGenTask(
            job_id="geminigen-test",
            request_log_id=10,
            public_model_id="geminigen-nano-banana-pro-image-landscape-1k",
            kind="image",
            endpoint_type="imagen",
            prompt="A calm lake",
            status="queued",
            progress=0,
        )
        self.config = SimpleNamespace(
            enabled=True,
            base_url="https://api.geminigen.ai",
            timeout_image_sec=timeout_image_sec,
            timeout_video_sec=3.0,
        )
        self.releases = 0
        self.acquire_exclusions = []
        self.task_updates = []
        self.account_updates = []

    async def get_geminigen_task(self, job_id):
        return self.task

    async def get_geminigen_config(self):
        return self.config

    async def get_geminigen_account(self, account_id):
        return self.account

    async def acquire_geminigen_account(self, kind, excluded_account_ids=None):
        self.acquire_exclusions.append(list(excluded_account_ids or []))
        if self.account.id in (excluded_account_ids or []):
            return None
        self.account.image_in_flight += 1
        return self.account

    async def release_geminigen_account(self, account_id, kind):
        self.releases += 1
        self.account.image_in_flight = max(0, self.account.image_in_flight - 1)

    async def update_geminigen_task(self, job_id, **kwargs):
        self.task_updates.append(kwargs)
        self.task = self.task.model_copy(update=kwargs)

    async def update_geminigen_account(self, account_id, **kwargs):
        self.account_updates.append(kwargs)


def build_capacity_test_service(db):
    service = object.__new__(GeminiGenService)
    service.db = db
    service.file_cache = None
    service.proxy_manager = None
    service._capacity_cooldowns = {}
    service._capacity_cooldown_attempts = {}
    service._token_refresh_locks = {}
    service._guard_skew_ms = 0
    service._guard_skew_synced_at = 0.0
    service._guard_skew_lock = asyncio.Lock()

    async def update_request_log(*args, **kwargs):
        return None

    service._update_request_log = update_request_log
    return service


def test_geminigen_capacity_releases_slot_and_retries_until_success():
    db = FakeGeminiGenDatabase()
    service = build_capacity_test_service(db)
    post_calls = 0

    def no_cooldown(account_id, kind):
        service._capacity_cooldowns[(account_id, kind)] = time.monotonic() - 1
        return 0.0

    async def no_sleep(*args, **kwargs):
        return None

    async def post_generation(**kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            raise GeminiGenUpstreamError(
                status_code=400,
                message=f"GeminiGen upstream capacity is full ({GEMINIGEN_CAPACITY_ERROR_CODE})",
                error_code=GEMINIGEN_CAPACITY_ERROR_CODE,
                retryable_capacity=True,
            )
        return {"uuid": "upstream-uuid"}

    service._set_capacity_cooldown = no_cooldown
    service._sleep_for_capacity_or_slot = no_sleep
    service._post_generation = post_generation

    result = asyncio.run(
        service._start_queued_task(
            "geminigen-test",
            images=[],
            options={},
            request_log_id=10,
            started_at=time.perf_counter(),
        )
    )

    assert result.upstream_uuid == "upstream-uuid"
    assert post_calls == 2
    assert db.releases == 1
    assert any(update.get("status") == "queued" for update in db.task_updates)
    assert db.account.image_in_flight == 1


def test_geminigen_capacity_timeout_uses_sanitized_error():
    db = FakeGeminiGenDatabase(timeout_image_sec=0.01)
    service = build_capacity_test_service(db)

    def long_cooldown(account_id, kind):
        service._capacity_cooldowns[(account_id, kind)] = time.monotonic() + 30
        return 30.0

    async def no_sleep(*args, **kwargs):
        return None

    async def post_generation(**kwargs):
        raise GeminiGenUpstreamError(
            status_code=400,
            message=f"GeminiGen upstream capacity is full ({GEMINIGEN_CAPACITY_ERROR_CODE})",
            error_code=GEMINIGEN_CAPACITY_ERROR_CODE,
            retryable_capacity=True,
        )

    service._set_capacity_cooldown = long_cooldown
    service._sleep_for_capacity_or_slot = no_sleep
    service._post_generation = post_generation

    try:
        asyncio.run(
            service._start_queued_task(
                "geminigen-test",
                images=[],
                options={},
                request_log_id=10,
                started_at=time.perf_counter(),
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "GeminiGen capacity is still full; generation did not start before timeout"
    else:
        raise AssertionError("Expected capacity timeout")

    assert db.releases == 1
    assert db.task.error_message == "GeminiGen capacity is still full; generation did not start before timeout"
    assert "POST /api/generate_image" not in db.task.error_message
    assert any(1 in excluded for excluded in db.acquire_exclusions)


def test_geminigen_public_status_dict_exposes_phase_and_terminal_urls():
    completed = GeminiGenTask(
        job_id="geminigen-complete",
        public_model_id="geminigen-nano-banana-pro-image-landscape-1k",
        kind="image",
        endpoint_type="imagen",
        prompt="done",
        status="completed",
        progress=100,
        raw_artifact_urls=["https://cdn.example/raw.png"],
        cached_artifact_urls=["https://flow.example/api/cache/blob/raw.png"],
        response_payload=json.dumps({"status": "SUCCESSFUL"}),
    )
    cancelled = completed.model_copy(
        update={
            "job_id": "geminigen-cancelled",
            "status": "cancelled",
            "progress": 5,
            "raw_artifact_urls": [],
            "cached_artifact_urls": [],
            "error_message": "User cancelled",
            "response_payload": json.dumps({"status": "cancelled"}),
        }
    )

    completed_public = GeminiGenService.task_to_public_dict(completed)
    cancelled_public = GeminiGenService.task_to_public_dict(cancelled)

    assert completed_public["status"] == "completed"
    assert completed_public["job_phase"] == "completed"
    assert completed_public["upstream_status"] == "SUCCESSFUL"
    assert completed_public["result_urls"] == ["https://flow.example/api/cache/blob/raw.png"]
    assert cancelled_public["status"] == "cancelled"
    assert cancelled_public["job_phase"] == "cancelled"
    assert cancelled_public["error_message"] == "User cancelled"


def test_geminigen_upstream_cancel_status_is_not_classified_as_failed():
    payload = {"status": "cancelled", "message": "Cancelled by user"}

    assert GeminiGenService._history_cancelled(payload, "cancelled") is True
    assert GeminiGenService._history_failed(payload, "cancelled") is False


def test_geminigen_poll_maps_upstream_cancel_to_cancelled_and_releases_account():
    db = FakeGeminiGenDatabase()
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": "upstream-uuid", "progress": 12})
    service = build_capacity_test_service(db)

    async def get_history(**kwargs):
        return {"status": "stopped", "message": "Stopped by upstream"}

    service._get_history = get_history

    result = asyncio.run(service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"))

    assert result.status == "cancelled"
    assert result.error_message == "Stopped by upstream"
    assert db.releases == 1
    assert any(update.get("status") == "cancelled" for update in db.task_updates)


def test_geminigen_wait_for_task_exits_on_cancelled():
    service = object.__new__(GeminiGenService)
    cancelled = GeminiGenTask(
        job_id="geminigen-cancelled",
        public_model_id="geminigen-nano-banana-pro-image-landscape-1k",
        kind="image",
        endpoint_type="imagen",
        prompt="cancel",
        status="cancelled",
        progress=1,
        error_message="Cancelled",
    )

    async def get_task(job_id):
        return cancelled

    async def get_config():
        return SimpleNamespace(timeout_image_sec=5, timeout_video_sec=5, poll_interval_image_sec=0.1, poll_interval_video_sec=0.1)

    async def poll_task(job_id, api_key_id=None, base_url=None):
        return cancelled

    service.db = SimpleNamespace(get_geminigen_task=get_task, get_geminigen_config=get_config)
    service.poll_task = poll_task

    result = asyncio.run(service.wait_for_task("geminigen-cancelled", api_key_id=None, base_url=None))

    assert result.status == "cancelled"


def test_geminigen_stream_exits_on_cancelled(monkeypatch):
    cancelled = GeminiGenTask(
        job_id="geminigen-cancelled",
        public_model_id="geminigen-nano-banana-pro-image-landscape-1k",
        kind="image",
        endpoint_type="imagen",
        prompt="cancel",
        status="cancelled",
        progress=1,
        error_message="Cancelled by user",
    )

    class FakeService:
        is_geminigen_terminal_status = staticmethod(GeminiGenService.is_geminigen_terminal_status)
        task_to_openai_payload = staticmethod(GeminiGenService.task_to_openai_payload)

        async def poll_task(self, job_id, api_key_id=None, base_url=None):
            return cancelled

    async def start_task(*args, **kwargs):
        return cancelled

    monkeypatch.setattr(routes, "_ensure_geminigen_service", lambda: FakeService())
    monkeypatch.setattr(routes, "_start_geminigen_from_request", start_task)

    async def collect():
        chunks = []
        async for chunk in routes._iterate_geminigen_openai_stream(None, None, api_key_id=None, base_url=None):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())

    assert any("geminigen_generation_cancelled" in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"


def test_veo_ingredient_manifest_only_exposes_supported_eight_second_duration():
    assert "geminigen-veo-3.1-fast-i2v-ingredient-landscape-720p-8s" in GEMINIGEN_MODEL_BY_ID
    assert "geminigen-veo-3.1-fast-i2v-ingredient-landscape-720p-4s" not in GEMINIGEN_MODEL_BY_ID
    assert "geminigen-veo-3.1-fast-i2v-ingredient-landscape-720p-6s" not in GEMINIGEN_MODEL_BY_ID


def test_studio_metadata_exposes_native_variant_and_geminigen_reference_mode():
    native = native_studio_metadata(
        "gemini-3.1-flash-image-square-4k",
        {
            "type": "image",
            "model_name": "NARWHAL",
            "aspect_ratio": "IMAGE_ASPECT_RATIO_SQUARE",
            "upsample": "UPSAMPLE_IMAGE_RESOLUTION_4K",
        },
    )
    assert native["family_id"] == "native:gemini-3.1-flash-image"
    assert native["variant"] == {"resolution": "4K", "aspect_ratio": "1:1"}

    geminigen = next(item for item in GEMINIGEN_MODEL_MANIFEST if "i2v-frame" in item["id"])
    assert geminigen_studio_metadata(geminigen)["modes"] == ["image_to_video"]
