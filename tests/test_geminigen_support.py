import asyncio
import base64
import json
import os
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, time as datetime_time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from src.core.database import Database
from src.core.config import config
from src.services.cloning_metadata_service import (
    _cloning_remaining_timeout,
    _ensure_meaningful_image_prompt,
    _normalize_image_prompt,
)
from src.services.geminigen_service import (
    GEMINIGEN_CAPACITY_ERROR_CODE,
    GeminiGenService,
    GeminiGenUpstreamError,
)
from src.services.llm_provider_chain import LlmProviderChain, extract_non_empty_json_object
from src.services.runway_service import RunwayService
from src.api import routes
from src.core.geminigen_manifest import GEMINIGEN_MODEL_BY_ID, GEMINIGEN_MODEL_MANIFEST
from src.core.models import GeminiGenAccount, GeminiGenTask, RunwayAccount, RunwayModel, RunwayTask, Token
from src.core.storage_errors import (
    is_sqlite_storage_full_error,
    sqlite_operational_error_handler,
)
from src.core.studio_model_catalog import geminigen_studio_metadata, native_studio_metadata


def test_blank_cloning_prompt_is_rejected():
    blank = _normalize_image_prompt({})
    try:
        _ensure_meaningful_image_prompt(blank)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "blank cloning prompt" in str(exc.detail)
    else:
        raise AssertionError("blank cloning prompt was accepted")


def test_meaningful_cloning_prompt_is_preserved():
    prompt = _normalize_image_prompt({"scene": "Clinician closing a hyperbaric oxygen chamber door"})
    out = _ensure_meaningful_image_prompt(prompt)
    assert out["scene"] == "Clinician closing a hyperbaric oxygen chamber door"


def test_empty_provider_json_content_is_rejected():
    try:
        extract_non_empty_json_object("", "Gemini")
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "Gemini returned empty JSON content" in str(exc.detail)
    else:
        raise AssertionError("empty provider JSON content was accepted")


def test_non_empty_provider_json_content_still_parses():
    assert extract_non_empty_json_object('{"scene":"x"}') == {"scene": "x"}


def test_non_retryable_model_output_does_not_try_fallback_model():
    class Chain(LlmProviderChain):
        def __init__(self):
            self.calls = []

        async def _invoke_gemini(self, model, *args, **kwargs):
            self.calls.append(model)
            raise HTTPException(status_code=422, detail="Gemini returned empty JSON content")

    chain = Chain()
    try:
        asyncio.run(
            chain.invoke_model_json(
                provider="gemini_native",
                model="primary-model",
                fallback_models=["fallback-model"],
                prompt_text="prompt",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("non-retryable model output was accepted")
    assert chain.calls == ["primary-model"]


def test_retryable_model_error_tries_fallback_model():
    class Chain(LlmProviderChain):
        def __init__(self):
            self.calls = []

        async def _invoke_gemini(self, model, *args, **kwargs):
            self.calls.append(model)
            if model == "primary-model":
                raise HTTPException(status_code=502, detail="temporary upstream failure")
            return {"scene": "fallback worked"}

    chain = Chain()
    out = asyncio.run(
        chain.invoke_model_json(
            provider="gemini_native",
            model="primary-model",
            fallback_models=["fallback-model"],
            prompt_text="prompt",
        )
    )
    assert out == {"scene": "fallback worked"}
    assert chain.calls == ["primary-model", "fallback-model"]


def test_cloning_deadline_returns_controlled_timeout_before_proxy_limit():
    try:
        _cloning_remaining_timeout(time.monotonic() - 1, 60.0)
    except HTTPException as exc:
        assert exc.status_code == 504
        assert "deadline exceeded" in str(exc.detail)
    else:
        raise AssertionError("expired cloning deadline did not fail")


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


def test_extract_artifact_urls_dedupes_signed_urls_for_same_artifact():
    payload = {
        "generated_image": {
            "image_url": "https://cdn.example/preview.jpg",
        },
        "result": {
            "file_download_url": "https://cdn.example/final.png?Expires=1&Signature=abc",
        },
        "data": [
            {
                "download_url": "https://cdn.example/final.png?Expires=2&Signature=def",
            }
        ],
    }

    assert GeminiGenService.extract_artifact_urls(payload, "image") == [
        "https://cdn.example/final.png?Expires=1&Signature=abc"
    ]


def test_extract_artifact_urls_preserves_distinct_multi_outputs():
    payload = {
        "data": [
            {"file_download_url": "https://cdn.example/result-1.png?Signature=abc"},
            {"file_download_url": "https://cdn.example/result-2.png?Signature=def"},
        ],
    }

    assert GeminiGenService.extract_artifact_urls(payload, "image") == [
        "https://cdn.example/result-1.png?Signature=abc",
        "https://cdn.example/result-2.png?Signature=def",
    ]


def _local_date_as_utc_naive(day):
    local_tz = datetime.now().astimezone().tzinfo
    local_noon = datetime.combine(day, datetime_time(hour=12), tzinfo=local_tz)
    return local_noon.astimezone(timezone.utc).replace(tzinfo=None)


def test_dashboard_stats_combine_flow_and_terminal_geminigen_tasks():
    async def run():
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db = Database(tmp.name)
        try:
            await db.init_db()
            today = datetime.now().astimezone().date()
            today_completed_at = _local_date_as_utc_naive(today)
            old_completed_at = _local_date_as_utc_naive(today - timedelta(days=2))

            token_id = await db.add_token(Token(st="session", email="flow@example.com"))
            async with db._connect(write=True) as conn:
                await conn.execute(
                    """
                    UPDATE token_stats
                    SET image_count = 4, video_count = 3, error_count = 2,
                        today_image_count = 2, today_video_count = 1, today_error_count = 1,
                        today_date = ?
                    WHERE token_id = ?
                    """,
                    (today.isoformat(), token_id),
                )
                await conn.commit()

            tasks = [
                ("image-today", "image", "completed", today_completed_at),
                ("image-old", "image", "completed", old_completed_at),
                ("video-today", "video", "completed", today_completed_at),
                ("error-today", "image", "failed", today_completed_at),
                ("error-old", "video", "failed", old_completed_at),
            ]
            for job_id, kind, status, completed_at in tasks:
                await db.create_geminigen_task(
                    GeminiGenTask(
                        job_id=job_id,
                        public_model_id=f"test-{kind}-model",
                        kind=kind,
                        endpoint_type="veo-video" if kind == "video" else "imagen",
                        status=status,
                        completed_at=completed_at,
                    )
                )

            return await db.get_dashboard_stats()
        finally:
            os.unlink(tmp.name)

    stats = asyncio.run(run())

    assert stats["total_images"] == 6
    assert stats["total_videos"] == 4
    assert stats["total_errors"] == 4
    assert stats["today_images"] == 3
    assert stats["today_videos"] == 2
    assert stats["today_errors"] == 2


def test_dashboard_stats_exclude_non_terminal_and_cancelled_geminigen_tasks():
    async def run():
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db = Database(tmp.name)
        try:
            await db.init_db()
            completed_at = _local_date_as_utc_naive(datetime.now().astimezone().date())
            tasks = [
                ("queued", "image", "queued", None),
                ("processing", "video", "processing", None),
                ("cancelled", "image", "cancelled", completed_at),
            ]
            for job_id, kind, status, terminal_at in tasks:
                await db.create_geminigen_task(
                    GeminiGenTask(
                        job_id=job_id,
                        public_model_id=f"test-{kind}-model",
                        kind=kind,
                        endpoint_type="veo-video" if kind == "video" else "imagen",
                        status=status,
                        completed_at=terminal_at,
                    )
                )
            return await db.get_dashboard_stats()
        finally:
            os.unlink(tmp.name)

    stats = asyncio.run(run())

    assert stats["total_images"] == 0
    assert stats["total_videos"] == 0
    assert stats["total_errors"] == 0
    assert stats["today_images"] == 0
    assert stats["today_videos"] == 0
    assert stats["today_errors"] == 0


class _AdminSessionCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _FullAdminSessionConnection:
    def __init__(self, row=None):
        self.row = row

    async def execute(self, query, _params=None):
        if query.lstrip().upper().startswith("SELECT"):
            return _AdminSessionCursor(self.row)
        return _AdminSessionCursor(None)

    async def commit(self):
        raise sqlite3.OperationalError("database or disk is full")


def _database_with_connection(connection):
    db = Database(":memory:")

    @asynccontextmanager
    async def connect(*, write=False):
        yield connection

    db._connect = connect
    return db


def test_sqlite_storage_full_classifier_uses_code_and_message_fallback():
    coded = sqlite3.OperationalError("write failed")
    coded.sqlite_errorcode = sqlite3.SQLITE_FULL

    assert is_sqlite_storage_full_error(coded) is True
    assert is_sqlite_storage_full_error(
        sqlite3.OperationalError("database or disk is full")
    ) is True
    assert is_sqlite_storage_full_error(sqlite3.OperationalError("database is locked")) is False
    assert is_sqlite_storage_full_error(RuntimeError("disk is full")) is False


def test_valid_admin_session_survives_storage_full_activity_touch():
    expires_at = int(time.time()) + 3600
    db = _database_with_connection(_FullAdminSessionConnection((expires_at,)))

    assert asyncio.run(db.is_admin_session_valid("admin-session")) is True


def test_expired_admin_session_stays_rejected_when_storage_is_full():
    expires_at = int(time.time()) - 1
    db = _database_with_connection(_FullAdminSessionConnection((expires_at,)))

    assert asyncio.run(db.is_admin_session_valid("expired-session")) is False


def test_new_admin_session_storage_failure_maps_to_507_without_returning():
    db = _database_with_connection(_FullAdminSessionConnection())

    try:
        asyncio.run(db.insert_admin_session("new-session", int(time.time()) + 3600))
    except sqlite3.OperationalError as exc:
        response = asyncio.run(sqlite_operational_error_handler(None, exc))
    else:
        raise AssertionError("Expected session persistence to fail")

    payload = json.loads(response.body)
    assert response.status_code == 507
    assert payload["code"] == "storage_full"
    assert "clear cached media" in payload["detail"]


def test_non_storage_sqlite_error_is_not_converted_to_507():
    error = sqlite3.OperationalError("database is locked")

    try:
        asyncio.run(sqlite_operational_error_handler(None, error))
    except sqlite3.OperationalError as exc:
        assert exc is error
    else:
        raise AssertionError("Expected non-storage SQLite error to be re-raised")


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


def test_geminigen_message_only_capacity_error_is_retryable():
    body = json.dumps(
        {
            "detail": {
                "error_message": "You have reached the maximum number of 5 concurrent image generations allowed by your plan. Please wait for one to finish before starting another.",
            }
        }
    )

    error = GeminiGenService._extract_upstream_error(400, body)

    assert error.retryable_capacity is True
    assert error.error_code is None
    assert "capacity is full" in str(error)


def test_geminigen_non_capacity_400_is_not_retryable():
    body = json.dumps({"detail": {"error_code": "BAD_PROMPT", "error_message": "Prompt is invalid"}})

    error = GeminiGenService._extract_upstream_error(400, body)

    assert error.retryable_capacity is False
    assert error.error_code == "BAD_PROMPT"
    assert "Prompt is invalid" in str(error)


def test_geminigen_global_image_concurrency_queues_after_five_slots():
    async def run():
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db = Database(tmp.name)
        try:
            await db.init_db()
            await db.update_geminigen_config(enabled=True, global_image_concurrency=5, global_video_concurrency=5)
            for index in range(3):
                await db.create_geminigen_account(
                    label=f"account-{index}",
                    raw_cookie="",
                    bearer_token=f"token-{index}",
                    image_concurrency=5,
                    video_concurrency=5,
                )

            acquired = [await db.acquire_geminigen_account("image") for _ in range(5)]
            blocked = await db.acquire_geminigen_account("image")
            await db.release_geminigen_account(acquired[0].id, "image")
            acquired_after_release = await db.acquire_geminigen_account("image")

            return acquired, blocked, acquired_after_release
        finally:
            os.unlink(tmp.name)

    acquired, blocked, acquired_after_release = asyncio.run(run())

    assert all(account is not None for account in acquired)
    assert blocked is None
    assert acquired_after_release is not None


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
            cache_outputs=True,
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
    service._finalize_locks = {}
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
            raise GeminiGenService._extract_upstream_error(
                400,
                json.dumps(
                    {
                        "detail": {
                            "error_message": "You have reached the maximum number of 5 concurrent image generations allowed by your plan.",
                        }
                    }
                ),
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
    queued = GeminiGenTask(
        job_id="geminigen-queued",
        public_model_id="geminigen-nano-banana-pro-image-landscape-1k",
        kind="image",
        endpoint_type="imagen",
        prompt="waiting",
        status="queued",
        progress=0,
    )
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

    queued_public = GeminiGenService.task_to_public_dict(queued)
    completed_public = GeminiGenService.task_to_public_dict(completed)
    cancelled_public = GeminiGenService.task_to_public_dict(cancelled)

    assert queued_public["status"] == "queued"
    assert queued_public["job_phase"] == "queued"
    assert queued_public["upstream_status"] is None
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


def test_geminigen_poll_does_not_query_history_while_submission_is_in_flight():
    db = FakeGeminiGenDatabase()
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": None, "progress": 1})
    service = build_capacity_test_service(db)

    async def unexpected_history_call(**kwargs):
        raise AssertionError("history must not be queried before upstream_uuid exists")

    service._get_history = unexpected_history_call

    result = asyncio.run(service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"))

    assert result.status == "processing"
    assert result.upstream_uuid is None


def test_geminigen_poll_respects_global_cache_kill_switch():
    db = FakeGeminiGenDatabase()
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": "upstream-uuid"})
    service = build_capacity_test_service(db)
    service.file_cache = SimpleNamespace(download_and_cache=AsyncMock(return_value="cached.png"))
    log_updates = []

    async def get_history(**kwargs):
        return {"status": "SUCCESSFUL", "file_download_url": "https://cdn.example/result.png"}

    async def update_request_log(*args, **kwargs):
        log_updates.append(kwargs)

    service._get_history = get_history
    service._update_request_log = update_request_log
    original_cache_enabled = config.cache_enabled
    config.set_cache_enabled(False)
    try:
        result = asyncio.run(service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"))
    finally:
        config.set_cache_enabled(original_cache_enabled)

    assert result.status == "completed"
    assert result.raw_artifact_urls == ["https://cdn.example/result.png"]
    assert result.cached_artifact_urls == []
    assert GeminiGenService.task_to_public_dict(result)["result_urls"] == ["https://cdn.example/result.png"]
    assert GeminiGenService.task_to_openai_payload(result)["result_urls"] == ["https://cdn.example/result.png"]
    assert "https://cdn.example/result.png" in GeminiGenService.task_to_openai_payload(result)["choices"][0]["message"]["content"]
    assert log_updates[-1]["response"]["result_urls"] == ["https://cdn.example/result.png"]
    service.file_cache.download_and_cache.assert_not_awaited()


def test_geminigen_poll_returns_cached_url_when_global_and_provider_cache_enabled():
    db = FakeGeminiGenDatabase()
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": "upstream-uuid"})
    service = build_capacity_test_service(db)
    service.file_cache = SimpleNamespace(download_and_cache=AsyncMock(return_value="cached.png"))

    async def get_history(**kwargs):
        return {"status": "SUCCESSFUL", "file_download_url": "https://cdn.example/result.png"}

    service._get_history = get_history
    original_cache_enabled = config.cache_enabled
    config.set_cache_enabled(True)
    try:
        result = asyncio.run(service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"))
    finally:
        config.set_cache_enabled(original_cache_enabled)

    cached_url = "https://flow.example/api/cache/blob/cached.png"
    assert result.status == "completed"
    assert result.raw_artifact_urls == ["https://cdn.example/result.png"]
    assert result.cached_artifact_urls == [cached_url]
    assert GeminiGenService.task_to_public_dict(result)["result_urls"] == [cached_url]
    assert GeminiGenService.task_to_openai_payload(result)["result_urls"] == [cached_url]
    service.file_cache.download_and_cache.assert_awaited_once_with(
        "https://cdn.example/result.png",
        media_type="image",
        api_key_id=None,
        token_id=None,
        flow_project_id=None,
    )


def test_geminigen_poll_returns_direct_url_when_provider_cache_disabled():
    db = FakeGeminiGenDatabase()
    db.config.cache_outputs = False
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": "upstream-uuid"})
    service = build_capacity_test_service(db)
    service.file_cache = SimpleNamespace(download_and_cache=AsyncMock(return_value="cached.png"))

    async def get_history(**kwargs):
        return {"status": "SUCCESSFUL", "file_download_url": "https://cdn.example/result.png"}

    service._get_history = get_history
    original_cache_enabled = config.cache_enabled
    config.set_cache_enabled(True)
    try:
        result = asyncio.run(service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"))
    finally:
        config.set_cache_enabled(original_cache_enabled)

    assert result.status == "completed"
    assert result.raw_artifact_urls == ["https://cdn.example/result.png"]
    assert result.cached_artifact_urls == []
    assert GeminiGenService.task_to_public_dict(result)["result_urls"] == ["https://cdn.example/result.png"]
    assert GeminiGenService.task_to_openai_payload(result)["result_urls"] == ["https://cdn.example/result.png"]
    service.file_cache.download_and_cache.assert_not_awaited()


def test_geminigen_concurrent_completion_caches_once():
    db = FakeGeminiGenDatabase()
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": "upstream-uuid"})
    service = build_capacity_test_service(db)
    history_calls = 0

    async def download_and_cache(*args, **kwargs):
        await asyncio.sleep(0.01)
        return "cached.png"

    async def get_history(**kwargs):
        nonlocal history_calls
        history_calls += 1
        await asyncio.sleep(0)
        return {
            "status": "SUCCESSFUL",
            "file_download_url": f"https://cdn.example/result.png?Signature={history_calls}",
        }

    async def run():
        original_cache_enabled = config.cache_enabled
        config.set_cache_enabled(True)
        try:
            return await asyncio.gather(
                service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"),
                service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"),
            )
        finally:
            config.set_cache_enabled(original_cache_enabled)

    service.file_cache = SimpleNamespace(download_and_cache=AsyncMock(side_effect=download_and_cache))
    service._get_history = get_history

    results = asyncio.run(run())

    cached_url = "https://flow.example/api/cache/blob/cached.png"
    assert all(result.status == "completed" for result in results)
    assert all(result.cached_artifact_urls == [cached_url] for result in results)
    service.file_cache.download_and_cache.assert_awaited_once()
    assert db.releases == 1


def test_geminigen_completed_without_artifact_urls_fails_clearly():
    db = FakeGeminiGenDatabase()
    db.task = db.task.model_copy(update={"status": "processing", "account_id": 1, "upstream_uuid": "upstream-uuid"})
    service = build_capacity_test_service(db)
    service.file_cache = SimpleNamespace(download_and_cache=AsyncMock(return_value="cached.png"))
    log_updates = []

    async def get_history(**kwargs):
        return {"status": "SUCCESSFUL"}

    async def update_request_log(*args, **kwargs):
        log_updates.append(kwargs)

    service._get_history = get_history
    service._update_request_log = update_request_log

    result = asyncio.run(service.poll_task("geminigen-test", api_key_id=None, base_url="https://flow.example"))

    assert result.status == "failed"
    assert result.error_message == "GeminiGen completed but did not return any artifact URLs"
    assert GeminiGenService.task_to_public_dict(result)["result_urls"] == []
    assert GeminiGenService.task_to_openai_payload(result)["result_urls"] == []
    assert log_updates[-1]["status_code"] == 502
    assert log_updates[-1]["response"]["result_urls"] == []
    service.file_cache.download_and_cache.assert_not_awaited()


class FakeRunwayDatabase:
    def __init__(self):
        self.task = RunwayTask(
            job_id="runway-test",
            upstream_task_id="upstream-task",
            account_id=1,
            api_key_id=5,
            public_model_id="runway-video",
            status="processing",
        )
        self.account = RunwayAccount(id=1, label="primary", raw_credential="token")
        self.model = RunwayModel(public_model_id="runway-video", display_name="Runway Video", kind="video", task_type="video")
        self.config = SimpleNamespace(base_url="https://api.runwayml.com/v1", cache_outputs=True)
        self.releases = 0

    async def get_runway_task(self, job_id):
        return self.task

    async def get_runway_config(self):
        return self.config

    async def get_runway_account(self, account_id):
        return self.account

    async def get_runway_model(self, public_model_id):
        return self.model

    async def update_runway_task(self, job_id, **kwargs):
        self.task = self.task.model_copy(update=kwargs)

    async def release_runway_account(self, account_id):
        self.releases += 1


def test_runway_poll_respects_global_cache_kill_switch():
    db = FakeRunwayDatabase()
    service = object.__new__(RunwayService)
    service.db = db
    service.file_cache = SimpleNamespace(download_and_cache=AsyncMock(return_value="cached.mp4"))
    service.proxy_manager = None

    async def get_upstream_task(**kwargs):
        return {"task": {"status": "SUCCEEDED", "artifacts": [{"url": "https://cdn.example/result.mp4"}]}}

    async def get_upstream_task_generation(**kwargs):
        return {}

    service.get_upstream_task = get_upstream_task
    service.get_upstream_task_generation = get_upstream_task_generation

    original_cache_enabled = config.cache_enabled
    config.set_cache_enabled(False)
    try:
        result = asyncio.run(service.poll_task("runway-test", api_key_id=5, base_url="https://flow.example"))
    finally:
        config.set_cache_enabled(original_cache_enabled)

    assert result.status == "completed"
    assert result.raw_artifact_urls == ["https://cdn.example/result.mp4"]
    assert result.cached_artifact_urls is None
    service.file_cache.download_and_cache.assert_not_awaited()


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
