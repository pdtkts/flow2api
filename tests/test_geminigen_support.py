import asyncio
import base64
import json
import time

from src.services.geminigen_service import GeminiGenService
from src.core.geminigen_manifest import GEMINIGEN_MODEL_BY_ID, GEMINIGEN_MODEL_MANIFEST
from src.core.models import GeminiGenAccount
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
