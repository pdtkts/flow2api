"""CSVGEN metadata: Adobe categories injection in outbound settings."""

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services import cloning_metadata_service as cms


class TestAdobeCategoriesForCsvgenSettings(unittest.TestCase):
    def test_helper_shape_and_length(self):
        rows = cms._adobe_stock_categories_for_csvgen_settings()
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[0], {"id": 1, "name": "Animals"})
        self.assertEqual(rows[-1], {"id": 21, "name": "Travel"})


class TestCsvgenPostIncludesCategories(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._captured_json = None

        async def fake_post(*_a, **kw):
            self._captured_json = kw.get("json")
            m = MagicMock()
            m.status_code = 200
            m.text = '{"title":"t","keywords":"k","description":""}'
            return m

        inner = MagicMock()
        inner.post = AsyncMock(side_effect=fake_post)
        outer = MagicMock()
        outer.__aenter__ = AsyncMock(return_value=inner)
        outer.__aexit__ = AsyncMock(return_value=None)
        self._session_cm = outer

        self._cfg = SimpleNamespace(
            flow2api_metadata_backend="csvgen",
            flow2api_metadata_provider_order="",
            flow2api_metadata_enabled_providers="",
            flow2api_metadata_provider_retry_count=1,
            flow2api_metadata_primary_model="",
            flow2api_metadata_model="gemini-2.5-flash",
            flow2api_metadata_enabled_models="",
            flow2api_metadata_fallback_models="",
            flow2api_csvgen_cookie="session=fake",
            flow2api_csvgen_api_keys="",
        )

    async def test_categories_present_when_include_category_and_adobe(self):
        async def fake_fetch(_self, _url, _b64, *_args, **_kwargs):
            return b"\xff\xd8\xff", "image/jpeg"

        with (
            patch.object(cms.CloningMetadataService, "_fetch_image", fake_fetch),
            patch.object(cms, "app_config", self._cfg),
            patch.object(cms, "AsyncSession", return_value=self._session_cm),
        ):
            svc = cms.CloningMetadataService(llm_chain=MagicMock())
            await svc.generate_metadata(
                {
                    "image_base64": "AAAA",
                    "metadataSettings": {
                        "platforms": ["adobe-stock"],
                        "includeCategory": True,
                    },
                }
            )

        settings = self._captured_json["settings"]
        cats = settings.get("categories")
        self.assertIsNotNone(cats)
        self.assertEqual(len(cats), 21)
        self.assertEqual(cats[0], {"id": 1, "name": "Animals"})

    async def test_categories_absent_without_include_category(self):
        async def fake_fetch(_self, _url, _b64, *_args, **_kwargs):
            return b"\xff\xd8\xff", "image/jpeg"

        with (
            patch.object(cms.CloningMetadataService, "_fetch_image", fake_fetch),
            patch.object(cms, "app_config", self._cfg),
            patch.object(cms, "AsyncSession", return_value=self._session_cm),
        ):
            svc = cms.CloningMetadataService(llm_chain=MagicMock())
            await svc.generate_metadata(
                {
                    "image_base64": "AAAA",
                    "metadataSettings": {
                        "platforms": ["adobe-stock"],
                        "includeCategory": False,
                    },
                }
            )

        settings = self._captured_json["settings"]
        self.assertNotIn("categories", settings)

    async def test_categories_absent_without_adobe_platform(self):
        async def fake_fetch(_self, _url, _b64, *_args, **_kwargs):
            return b"\xff\xd8\xff", "image/jpeg"

        with (
            patch.object(cms.CloningMetadataService, "_fetch_image", fake_fetch),
            patch.object(cms, "app_config", self._cfg),
            patch.object(cms, "AsyncSession", return_value=self._session_cm),
        ):
            svc = cms.CloningMetadataService(llm_chain=MagicMock())
            await svc.generate_metadata(
                {
                    "image_base64": "AAAA",
                    "metadataSettings": {
                        "platforms": ["shutterstock"],
                        "includeCategory": True,
                    },
                }
            )

        settings = self._captured_json["settings"]
        self.assertNotIn("categories", settings)

    async def test_adobe_stock_platform_matching_is_case_insensitive(self):
        async def fake_fetch(_self, _url, _b64, *_args, **_kwargs):
            return b"\xff\xd8\xff", "image/jpeg"

        with (
            patch.object(cms.CloningMetadataService, "_fetch_image", fake_fetch),
            patch.object(cms, "app_config", self._cfg),
            patch.object(cms, "AsyncSession", return_value=self._session_cm),
        ):
            svc = cms.CloningMetadataService(llm_chain=MagicMock())
            await svc.generate_metadata(
                {
                    "image_base64": "AAAA",
                    "metadataSettings": {
                        "platforms": ["Adobe-Stock"],
                        "includeCategory": True,
                    },
                }
            )

        settings = self._captured_json["settings"]
        self.assertIn("categories", settings)
        self.assertEqual(len(settings["categories"]), 21)


class TestMetadataImageMimeHandling(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_image_detects_png_data_url(self):
        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        svc = cms.CloningMetadataService(llm_chain=MagicMock())

        content, mime = await svc._fetch_image(None, data_url)

        self.assertEqual(content, png_bytes)
        self.assertEqual(mime, "image/png")

    async def test_fetch_image_honors_explicit_mime_for_raw_base64(self):
        payload = base64.b64encode(b"not-magic-but-webp-by-hint").decode("ascii")
        svc = cms.CloningMetadataService(llm_chain=MagicMock())

        _content, mime = await svc._fetch_image(None, payload, mime_type_hint="image/webp")

        self.assertEqual(mime, "image/webp")

    async def test_generate_metadata_passes_png_mime_to_llm(self):
        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        llm = MagicMock()
        llm.resolve_provider_chain.return_value = ["gemini_native"]
        llm.invoke_model_json = AsyncMock(
            return_value={"metadataSets": [{"title": "Yellow tray cutout", "keywords": ["tray"], "description": ""}]}
        )
        cfg = SimpleNamespace(
            flow2api_metadata_backend="gemini_native",
            flow2api_metadata_provider_order="",
            flow2api_metadata_enabled_providers="",
            flow2api_metadata_provider_retry_count=0,
            flow2api_metadata_primary_model="gemini-2.5-flash",
            flow2api_metadata_model="gemini-2.5-flash",
            flow2api_metadata_enabled_models="",
            flow2api_metadata_fallback_models="",
        )
        with patch.object(cms, "app_config", cfg):
            svc = cms.CloningMetadataService(llm_chain=llm)
            await svc.generate_metadata(
                {
                    "image_base64": base64.b64encode(png_bytes).decode("ascii"),
                    "mimeType": "image/png",
                    "metadataSettings": {"transparentBackground": True},
                    "dnaNoBgWorkflowActive": True,
                }
            )

        self.assertEqual(llm.invoke_model_json.await_args.kwargs["mime_type"], "image/png")
        prompt = llm.invoke_model_json.await_args.kwargs["prompt_text"]
        self.assertIn("isolated on transparent background", prompt)
        self.assertIn("Do not say solid black background", prompt)

    async def test_transparent_background_prompt_without_dna_no_bg_workflow(self):
        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        llm = MagicMock()
        llm.resolve_provider_chain.return_value = ["gemini_native"]
        llm.invoke_model_json = AsyncMock(
            return_value={"metadataSets": [{"title": "Yellow tray cutout", "keywords": ["tray"], "description": ""}]}
        )
        cfg = SimpleNamespace(
            flow2api_metadata_backend="gemini_native",
            flow2api_metadata_provider_order="",
            flow2api_metadata_enabled_providers="",
            flow2api_metadata_provider_retry_count=0,
            flow2api_metadata_primary_model="gemini-2.5-flash",
            flow2api_metadata_model="gemini-2.5-flash",
            flow2api_metadata_enabled_models="",
            flow2api_metadata_fallback_models="",
        )
        with patch.object(cms, "app_config", cfg):
            svc = cms.CloningMetadataService(llm_chain=llm)
            await svc.generate_metadata(
                {
                    "image_base64": base64.b64encode(png_bytes).decode("ascii"),
                    "mimeType": "image/png",
                    "metadataSettings": {"transparentBackground": True},
                    "dnaNoBgWorkflowActive": False,
                }
            )

        prompt = llm.invoke_model_json.await_args.kwargs["prompt_text"]
        self.assertIn("isolated on transparent background", prompt)
        self.assertIn("Do not say solid black background", prompt)


if __name__ == "__main__":
    unittest.main()
