from src.services.geminigen_service import GeminiGenService
from src.core.models import GeminiGenAccount


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
                "model": "veo-3.1-fast",
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
            "options": {"model": "veo-3.1-fast", "reference_mode": "ingredient"},
        },
        extra_options={},
        account=GeminiGenAccount(id=1, name="test", bearer_token="token"),
    )

    assert form["mode_image"] == "ingredient"
    assert form["prompt"].startswith("@image1 ")
    assert form["_file_parts"][0]["content_type"] == "image/jpeg"
