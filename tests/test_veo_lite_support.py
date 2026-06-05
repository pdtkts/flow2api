import json
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import src.api.routes as routes
from src.api.routes import _extract_async_delivery_fields
from src.core.model_resolver import resolve_model_name
from src.services.flow_client import FlowClient
from src.services.generation_handler import MODEL_CONFIG, GenerationHandler


class FlowClientTransportErrorTests(unittest.TestCase):
    def setUp(self):
        self.client = FlowClient(proxy_manager=None)

    def test_curl_http2_error_uses_urllib_fallback(self):
        self.assertTrue(
            self.client._should_fallback_to_urllib("Failed to perform, curl: (16) .")
        )
        self.assertTrue(
            self.client._is_http2_transport_error("Failed to perform, curl: (16) .")
        )

    def test_curl_http2_error_is_retryable_network_error(self):
        error = "Flow API request failed: Failed to perform, curl: (16) ."

        self.assertTrue(self.client._is_retryable_network_error(error))
        self.assertIn("TLS", self.client._get_retry_reason(error))

    def test_http2_framing_text_is_retryable_network_error(self):
        error = "CURLE_HTTP2: problem detected in the HTTP/2 framing layer"

        self.assertTrue(self.client._is_http2_transport_error(error))
        self.assertTrue(self.client._should_fallback_to_urllib(error))
        self.assertTrue(self.client._is_retryable_network_error(error))


class AsyncImageDeliveryFieldTests(unittest.TestCase):
    def test_failed_upscale_suppresses_cached_1k_fallback(self):
        google_url = "https://flow-content.google/image/source"
        cached_url = "https://api.example.com/api/cache/blob/fallback.jpg"
        payload = {
            "url": cached_url,
            "generated_assets": {
                "type": "image",
                "origin_image_url": google_url,
                "final_image_url": cached_url,
            },
        }

        fields = _extract_async_delivery_fields(
            payload,
            "gemini-3.1-flash-image-landscape-4k",
        )

        self.assertEqual(fields["base_result_urls"], [google_url])
        self.assertIsNone(fields["result_urls"])
        self.assertIsNone(fields["delivery_urls"])
        self.assertEqual(fields["requested_resolution"], "4k")
        self.assertEqual(fields["output_resolution"], "1k")
        self.assertEqual(fields["upscale_status"], "failed")
        self.assertEqual(fields["upscale_error_message"], "Requested image upscale did not complete")

    def test_successful_upscale_delivers_upscaled_url(self):
        google_url = "https://flow-content.google/image/source"
        upscaled_url = "https://api.example.com/api/cache/blob/result_4K.jpg"
        payload = {
            "url": upscaled_url,
            "generated_assets": {
                "type": "image",
                "origin_image_url": google_url,
                "upscaled_image": {
                    "resolution": "4K",
                    "url": upscaled_url,
                },
            },
        }

        fields = _extract_async_delivery_fields(
            payload,
            "gemini-3.1-flash-image-landscape-4k",
        )

        self.assertEqual(fields["base_result_urls"], [google_url])
        self.assertEqual(fields["result_urls"], [upscaled_url])
        self.assertEqual(fields["delivery_urls"], [upscaled_url])
        self.assertEqual(fields["output_resolution"], "4k")
        self.assertEqual(fields["upscale_status"], "completed")

    def test_normal_1k_image_keeps_cached_final_url(self):
        google_url = "https://flow-content.google/image/source"
        cached_url = "https://api.example.com/api/cache/blob/fallback.jpg"
        payload = {
            "url": cached_url,
            "generated_assets": {
                "type": "image",
                "origin_image_url": google_url,
                "final_image_url": cached_url,
            },
        }

        fields = _extract_async_delivery_fields(
            payload,
            "gemini-3.1-flash-image-landscape",
        )

        self.assertEqual(fields["base_result_urls"], [google_url])
        self.assertEqual(fields["result_urls"], [cached_url])
        self.assertEqual(fields["delivery_urls"], [cached_url])
        self.assertIsNone(fields["requested_resolution"])
        self.assertIsNone(fields["output_resolution"])
        self.assertEqual(fields["upscale_status"], "not_requested")


class AsyncImageJobFinalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_payload_fails_requested_upscale_job(self):
        updates = {}

        class FakeDb:
            async def update_task(self, task_id, **kwargs):
                updates["task_id"] = task_id
                updates.update(kwargs)

        handler = SimpleNamespace(db=FakeDb())
        normalized = SimpleNamespace(
            model="gemini-3.1-flash-image-landscape-4k",
            project_id="project-1",
        )
        payload = {
            "url": "https://api.example.com/api/cache/blob/fallback.jpg",
            "generated_assets": {
                "type": "image",
                "origin_image_url": "https://flow-content.google/image/source",
                "final_image_url": "https://api.example.com/api/cache/blob/fallback.jpg",
            },
        }

        with (
            patch.object(routes, "_ensure_generation_handler", return_value=handler),
            patch.object(routes, "_collect_non_stream_result", AsyncMock(return_value=json.dumps(payload))),
        ):
            await routes._run_async_generation_task(
                task_id="job-1",
                normalized=normalized,
                base_url_override=None,
                allowed_token_ids=None,
                selection_context=None,
                api_key_id=1,
            )

        self.assertEqual(updates["task_id"], "job-1")
        self.assertEqual(updates["status"], "failed")
        self.assertEqual(updates["job_phase"], "failed")
        self.assertEqual(updates["upscale_status"], "failed")
        self.assertEqual(updates["captcha_status"], "idle")
        self.assertEqual(updates["result_urls"], [])
        self.assertEqual(updates["delivery_urls"], [])


class ImageUpscaleFailureHandlerTests(unittest.IsolatedAsyncioTestCase):
    def _build_handler(self, upsample_result=None, upsample_error=None, cache_error=None):
        handler = GenerationHandler.__new__(GenerationHandler)
        handler.flow_client = SimpleNamespace(
            generate_image=AsyncMock(
                return_value=(
                    {
                        "media": [
                            {
                                "name": "media-1",
                                "image": {
                                    "generatedImage": {
                                        "fifeUrl": "https://flow-content.google/image/source"
                                    }
                                },
                            }
                        ]
                    },
                    "session-1",
                    {},
                )
            ),
            upsample_image=AsyncMock(side_effect=upsample_error)
            if upsample_error
            else AsyncMock(return_value=upsample_result),
            _get_retry_reason=lambda _err: None,
        )
        handler.file_cache = SimpleNamespace(
            cache_base64_image=AsyncMock(side_effect=cache_error)
            if cache_error
            else AsyncMock(return_value="upscaled_4K.jpg"),
            download_and_cache=AsyncMock(return_value="fallback.jpg"),
        )
        handler._update_request_log_progress = AsyncMock()
        handler._maybe_update_poll_task = AsyncMock()

        async def execute_with_extension_fallback(_name, _extension_operation, local_operation):
            return await local_operation()

        handler._execute_with_extension_fallback = execute_with_extension_fallback
        return handler

    async def _run_image_generation(self, handler):
        token = SimpleNamespace(
            id=7,
            at="at-token",
            image_concurrency=1,
            user_paygate_tier="PAYGATE_TIER_ONE",
        )
        generation_result = handler._create_generation_result()
        response_state = handler._create_response_state()
        chunks = []
        async for chunk in handler._handle_image_generation(
            token=token,
            project_id="project-1",
            model_config=MODEL_CONFIG["gemini-3.1-flash-image-landscape-4k"],
            prompt="prompt",
            images=None,
            stream=False,
            api_key_id=1,
            perf_trace={},
            generation_result=generation_result,
            response_state=response_state,
            request_log_state={"id": None, "progress": 0, "api_key_id": 1},
            pending_token_state={"active": False},
            poll_task_id="job-1",
        ):
            chunks.append(json.loads(chunk))
        return generation_result, chunks, handler

    async def test_upscale_exception_fails_without_1k_fallback(self):
        handler = self._build_handler(upsample_error=Exception("upstream boom"))

        generation_result, chunks, handler = await self._run_image_generation(handler)

        self.assertFalse(generation_result["success"])
        self.assertEqual(generation_result["error_status_code"], 502)
        self.assertEqual(chunks[-1]["error"]["status_code"], 502)
        self.assertEqual(chunks[-1]["upscale_status"], "failed")
        handler.file_cache.download_and_cache.assert_not_awaited()
        self.assertEqual(handler._maybe_update_poll_task.await_args.kwargs["status"], "failed")
        self.assertEqual(handler._maybe_update_poll_task.await_args.kwargs["upscale_status"], "failed")

    async def test_empty_upscale_response_fails_without_1k_fallback(self):
        handler = self._build_handler(upsample_result="")

        generation_result, chunks, handler = await self._run_image_generation(handler)

        self.assertFalse(generation_result["success"])
        self.assertIn("empty image", chunks[-1]["error"]["message"])
        handler.file_cache.download_and_cache.assert_not_awaited()

    async def test_upscale_cache_failure_fails_without_inline_fallback(self):
        handler = self._build_handler(upsample_result="base64-image", cache_error=Exception("disk full"))

        generation_result, chunks, handler = await self._run_image_generation(handler)

        self.assertFalse(generation_result["success"])
        self.assertEqual(chunks[-1]["upscale_status"], "failed")
        self.assertIn("could not cache high-resolution image", chunks[-1]["error"]["message"])
        handler.file_cache.download_and_cache.assert_not_awaited()


class VeoLiteModelResolverTests(unittest.TestCase):
    def test_resolve_t2v_lite_alias_to_portrait_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )

        resolved = resolve_model_name(
            "veo_3_1_t2v_lite",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_lite_portrait")

    def test_resolve_quality_4s_upsample_alias_to_portrait_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )

        resolved = resolve_model_name(
            "veo_3_1_t2v_4s_4k",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_portrait_4s_4k")

    def test_resolve_video_image_size_to_upsample_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="landscape", imageSize="1080p")
        )

        resolved = resolve_model_name(
            "veo_3_1_i2v_s_6s",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_i2v_s_6s_1080p")


class VeoLiteGenerationHandlerTests(unittest.TestCase):
    def test_tier_two_does_not_upgrade_lite_model_to_fake_ultra(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        model_key, message = handler._resolve_video_model_key_for_tier(
            {
                "model_key": "veo_3_1_t2v_lite",
                "allow_tier_upgrade": False,
            },
            "PAYGATE_TIER_TWO",
        )

        self.assertEqual(model_key, "veo_3_1_t2v_lite")
        self.assertIsNone(message)

    def test_tier_two_still_upgrades_regular_model(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        model_key, message = handler._resolve_video_model_key_for_tier(
            {
                "model_key": "veo_3_1_t2v_fast",
            },
            "PAYGATE_TIER_TWO",
        )

        self.assertEqual(model_key, "veo_3_1_t2v_fast_ultra")
        self.assertIn("ultra", message)

    def test_quality_model_does_not_upgrade_to_fake_ultra(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        model_key, message = handler._resolve_video_model_key_for_tier(
            {
                "model_key": "veo_3_1_t2v",
            },
            "PAYGATE_TIER_TWO",
        )

        self.assertEqual(model_key, "veo_3_1_t2v")
        self.assertIsNone(message)

    def test_quality_4s_upsample_model_generates_then_upsamples(self):
        cfg = MODEL_CONFIG["veo_3_1_t2v_4s_4k"]

        self.assertEqual(cfg["model_key"], "veo_3_1_t2v_quality_4s")
        self.assertEqual(cfg["video_type"], "t2v")
        self.assertEqual(cfg["upsample"]["model_key"], "veo_3_1_upsampler_4k")
        self.assertEqual(cfg["upsample"]["resolution"], "VIDEO_RESOLUTION_4K")

    def test_quality_6s_i2v_1080p_model_generates_then_upsamples(self):
        cfg = MODEL_CONFIG["veo_3_1_i2v_s_6s_1080p"]

        self.assertEqual(cfg["model_key"], "veo_3_1_i2v_s_quality_6s_fl")
        self.assertEqual(cfg["video_type"], "i2v")
        self.assertEqual(cfg["upsample"]["model_key"], "veo_3_1_upsampler_1080p")
        self.assertEqual(cfg["upsample"]["resolution"], "VIDEO_RESOLUTION_1080P")

    def test_direct_upsampler_keys_are_not_public_models(self):
        self.assertNotIn("veo_3_1_upsampler_4k", MODEL_CONFIG)
        self.assertNotIn("veo_3_1_upsampler_1080p", MODEL_CONFIG)


class VeoLiteFlowClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FlowClient(proxy_manager=None)
        self.client._acquire_video_launch_gate = AsyncMock(return_value=(True, None, None))
        self.client._release_video_launch_gate = AsyncMock()
        self.client._get_recaptcha_token = AsyncMock(return_value=("recaptcha-token", "browser-1"))
        self.client._notify_browser_captcha_request_finished = AsyncMock()

    async def test_upsample_image_error_emits_terminal_failed_progress(self):
        updates = []

        async def collect_progress(update):
            updates.append(dict(update))

        self.client._make_request = AsyncMock(side_effect=Exception("bad request"))

        with self.assertRaises(Exception):
            await self.client.upsample_image(
                at="at-token",
                project_id="project-1",
                media_id="media-1",
                poll_task_progress=collect_progress,
            )

        self.assertTrue(
            any(
                update.get("status") == "failed"
                and update.get("job_phase") == "failed"
                and update.get("upscale_status") == "failed"
                for update in updates
            )
        )

    async def test_upsample_image_empty_response_emits_terminal_failed_progress(self):
        updates = []

        async def collect_progress(update):
            updates.append(dict(update))

        self.client._make_request = AsyncMock(return_value={})

        result = await self.client.upsample_image(
            at="at-token",
            project_id="project-1",
            media_id="media-1",
            poll_task_progress=collect_progress,
        )

        self.assertEqual(result, "")
        self.assertTrue(
            any(
                update.get("status") == "failed"
                and update.get("job_phase") == "failed"
                and update.get("upscale_status") == "failed"
                for update in updates
            )
        )

    async def test_generate_video_text_uses_v2_payload_for_lite(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["url"] = url
            captured["json_data"] = json_data
            return {"operations": [{"operation": {"name": "task-1"}}]}

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        await self.client.generate_video_text(
            at="at-token",
            project_id="project-1",
            prompt="猫猫",
            model_key="veo_3_1_t2v_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            use_v2_model_config=True,
        )

        json_data = captured["json_data"]
        request_data = json_data["requests"][0]
        self.assertTrue(json_data["useV2ModelConfig"])
        self.assertIn("batchId", json_data["mediaGenerationContext"])
        self.assertEqual(
            request_data["textInput"]["structuredPrompt"]["parts"][0]["text"],
            "猫猫",
        )
        self.assertNotIn("prompt", request_data["textInput"])
        self.assertEqual(request_data["videoModelKey"], "veo_3_1_t2v_lite")
        self.assertEqual(
            json_data["mediaGenerationContext"]["audioFailurePreference"],
            "BLOCK_SILENCED_VIDEOS",
        )

    async def test_generate_video_text_normalizes_media_only_create_response(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["json_data"] = json_data
            return {
                "remainingCredits": 30,
                "workflows": [
                    {
                        "name": "workflow-1",
                        "metadata": {"primaryMediaId": "media-1"},
                        "projectId": "project-1",
                    }
                ],
                "media": [
                    {
                        "name": "media-1",
                        "projectId": "project-1",
                        "mediaMetadata": {
                            "mediaStatus": {
                                "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_PENDING"
                            }
                        },
                    }
                ],
            }

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        result = await self.client.generate_video_text(
            at="at-token",
            project_id="project-1",
            prompt="猫猫",
            model_key="veo_3_1_t2v_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            use_v2_model_config=True,
        )

        self.assertEqual(
            captured["json_data"]["mediaGenerationContext"]["audioFailurePreference"],
            "BLOCK_SILENCED_VIDEOS",
        )
        self.assertEqual(result["operations"][0]["operation"]["name"], "media-1")
        self.assertEqual(result["operations"][0]["projectId"], "project-1")
        self.assertEqual(
            result["operations"][0]["status"],
            "MEDIA_GENERATION_STATUS_PENDING",
        )

    async def test_check_video_status_uses_media_payload_and_normalizes_response(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["json_data"] = json_data
            return {
                "media": [
                    {
                        "name": "media-1",
                        "projectId": "project-1",
                        "mediaMetadata": {
                            "mediaStatus": {
                                "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL"
                            }
                        },
                        "video": {
                            "fifeUrl": "https://flow-content.google/video/11111111-1111-1111-1111-111111111111?token=abc",
                            "generatedVideo": {
                                "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE"
                            },
                        },
                    }
                ]
            }

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        result = await self.client.check_video_status(
            at="at-token",
            operations=[
                {
                    "operation": {"name": "media-1"},
                    "projectId": "project-1",
                }
            ],
        )

        self.assertEqual(
            captured["json_data"],
            {"media": [{"name": "media-1", "projectId": "project-1"}]},
        )
        operation = result["operations"][0]
        self.assertEqual(operation["operation"]["name"], "media-1")
        self.assertEqual(operation["status"], "MEDIA_GENERATION_STATUS_SUCCESSFUL")
        self.assertEqual(
            operation["operation"]["metadata"]["video"]["fifeUrl"],
            "https://flow-content.google/video/11111111-1111-1111-1111-111111111111?token=abc",
        )

    async def test_generate_video_start_end_uses_v2_payload_for_interpolation_lite(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["url"] = url
            captured["json_data"] = json_data
            return {"operations": [{"operation": {"name": "task-2"}}]}

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        await self.client.generate_video_start_end(
            at="at-token",
            project_id="project-1",
            prompt="变身猫猫",
            model_key="veo_3_1_interpolation_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_PORTRAIT",
            start_media_id="start-media",
            end_media_id="end-media",
            use_v2_model_config=True,
        )

        json_data = captured["json_data"]
        request_data = json_data["requests"][0]
        self.assertTrue(json_data["useV2ModelConfig"])
        self.assertIn("batchId", json_data["mediaGenerationContext"])
        self.assertEqual(request_data["videoModelKey"], "veo_3_1_interpolation_lite")
        self.assertEqual(request_data["startImage"]["mediaId"], "start-media")
        self.assertEqual(request_data["endImage"]["mediaId"], "end-media")
        self.assertEqual(
            request_data["textInput"]["structuredPrompt"]["parts"][0]["text"],
            "变身猫猫",
        )


if __name__ == "__main__":
    unittest.main()
