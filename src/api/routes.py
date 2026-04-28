"""API routes for OpenAI-compatible and Gemini generateContent endpoints."""

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple
import base64
import json
import mimetypes
import re
from urllib.parse import parse_qs, quote, urlparse

from curl_cffi.requests import AsyncSession
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..core.auth import verify_api_key_flexible
from ..core.api_key_manager import AuthContext
from ..core.logger import debug_logger
from ..core.model_resolver import get_base_model_aliases, resolve_model_name
from ..core.models import (
    ChatCompletionRequest,
    ChatMessage,
    FlowProjectCreateRequest,
    GeminiContent,
    GeminiGenerateContentRequest,
    Project,
)
from ..services.generation_handler import MODEL_CONFIG, GenerationHandler

router = APIRouter()

MARKDOWN_IMAGE_RE = re.compile(r"!\[.*?\]\((.*?)\)")
HTML_VIDEO_RE = re.compile(r"<video[^>]+src=['\"](.*?)['\"]", re.IGNORECASE)
DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)
MEDIA_PROMPT_TOOL_BLOCK_RE = re.compile(r"<tools>.*?</tools>", re.IGNORECASE | re.DOTALL)
MEDIA_SYSTEM_INSTRUCTION_MARKERS = (
    "<tools>",
    "</tools>",
    "function calling ai model",
    "function signatures",
    "\"$schema\"",
    "\"additionalproperties\"",
)
MEDIA_PROMPT_PREAMBLE_PATTERNS = (
    re.compile(r"^you are a function calling ai model\.?$", re.IGNORECASE),
    re.compile(
        r"^you are provided with function signatures within .* xml tags\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^you may call one or more functions to assist with the user query\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^don't make assumptions about what values to plug into functions\.?$",
        re.IGNORECASE,
    ),
    re.compile(r"^here are the available tools:.*$", re.IGNORECASE),
)
GEMINI_STATUS_MAP = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "ABORTED",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    502: "UNAVAILABLE",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}

# Dependency injection will be set up in main.py
generation_handler: GenerationHandler = None


@dataclass
class NormalizedGenerationRequest:
    """Internal request shape shared by OpenAI and Gemini entrypoints."""

    model: str
    prompt: str
    images: List[bytes]
    messages: Optional[List[ChatMessage]] = None
    project_id: Optional[str] = None


def _strip_optional_project_id(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def set_generation_handler(handler: GenerationHandler):
    """Set generation handler instance."""
    global generation_handler
    generation_handler = handler


def _ensure_generation_handler() -> GenerationHandler:
    if generation_handler is None:
        raise HTTPException(status_code=500, detail="Generation handler not initialized")
    return generation_handler


def _build_model_description(model_config: Dict[str, Any]) -> str:
    """Build a human-readable description for model listing endpoints."""
    description = f"{model_config['type'].capitalize()} generation"
    if model_config["type"] == "image":
        description += f" - {model_config['model_name']}"
    else:
        description += f" - {model_config['model_key']}"
    return description


def _get_openai_model_catalog() -> List[Dict[str, str]]:
    """Collect OpenAI-compatible model list entries."""
    return [
        {
            "id": model_id,
            "description": _build_model_description(model_config),
        }
        for model_id, model_config in MODEL_CONFIG.items()
    ]


def _get_gemini_model_catalog() -> Dict[str, str]:
    """Collect Gemini-compatible model metadata for /models endpoints."""
    catalog: Dict[str, str] = {}

    for alias_id, description in get_base_model_aliases().items():
        catalog[alias_id] = description

    for model_id, model_config in MODEL_CONFIG.items():
        catalog.setdefault(model_id, _build_model_description(model_config))

    return catalog


def _build_gemini_model_resource(model_id: str, description: str) -> Dict[str, Any]:
    """Build a Gemini-compatible model resource payload."""
    return {
        "name": f"models/{model_id}",
        "displayName": model_id,
        "description": description,
        "version": "flow2api",
        "inputTokenLimit": 0,
        "outputTokenLimit": 0,
        "supportedGenerationMethods": [
            "generateContent",
            "streamGenerateContent",
        ],
    }


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    match = DATA_URL_RE.match(data_url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid data URL")
    return match.group("mime"), base64.b64decode(match.group("data"))


def _detect_image_mime_type(image_bytes: bytes, fallback: str = "image/png") -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def _guess_mime_type(uri: str, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(urlparse(uri).path)
    return guessed or fallback


def _extract_cache_filename(url: str) -> Optional[str]:
    """Resolve filename from owner-scoped cache URLs (blob path; legacy /api/cache/file/ still accepted)."""
    path = urlparse(url).path
    for marker in ("/api/cache/blob/", "/api/cache/file/"):
        if marker not in path:
            continue
        filename = path.split(marker, 1)[-1].strip().split("/", 1)[0]
        if not filename:
            return None
        return Path(filename).name
    return None


def _cache_file_row_to_list_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a cache_files row for GET /api/cache/file list APIs."""
    fn_safe = Path(str(row.get("filename") or "")).name
    flow = _strip_optional_project_id(row.get("flow_project_id"))
    download_path = f"/api/cache/blob/{fn_safe}"
    if flow:
        download_path = f"{download_path}?project_id={quote(flow, safe='')}"
    created = row.get("created_at")
    updated = row.get("updated_at")
    return {
        "filename": fn_safe,
        "flow_project_id": flow,
        "media_type": row.get("media_type"),
        "source_url": row.get("source_url"),
        "token_id": row.get("token_id"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else (str(created) if created is not None else None),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else (str(updated) if updated is not None else None),
        "download_path": download_path,
    }


async def retrieve_image_data(
    url: str,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> Optional[bytes]:
    """Read image bytes from protected cache endpoint or remote URL."""
    file_cache = getattr(generation_handler, "file_cache", None)
    try:
        cache_filename = _extract_cache_filename(url)
        if cache_filename and file_cache and api_key_id is not None:
            db = getattr(generation_handler, "db", None)
            if db is None:
                return None
            metadata = await db.get_cache_file_for_api_key(cache_filename, api_key_id)
            if not metadata:
                return None
            meta_flow = _strip_optional_project_id(metadata.get("flow_project_id"))
            if meta_flow:
                parsed = urlparse(url)
                q_vals = parse_qs(parsed.query).get("project_id") or []
                url_project = _strip_optional_project_id(q_vals[0] if q_vals else None)
                if not url_project or url_project != meta_flow:
                    return None
                proj = await db.get_project_by_id(url_project, api_key_id)
                if not proj:
                    return None
                if allowed_token_ids is not None and int(proj.token_id) not in allowed_token_ids:
                    return None
            filename = Path(cache_filename).name
            local_file_path = file_cache.cache_dir / filename

            if local_file_path.exists() and local_file_path.is_file():
                data = local_file_path.read_bytes()
                if data:
                    return data
    except Exception as exc:
        debug_logger.log_warning(f"[CONTEXT] 本地缓存读取失败: {str(exc)}")

    proxy_url = None
    try:
        if file_cache and hasattr(file_cache, "_resolve_download_proxy"):
            proxy_url = await file_cache._resolve_download_proxy("image")
    except Exception as exc:
        debug_logger.log_warning(f"[CONTEXT] 图片下载代理解析失败: {str(exc)}")

    try:
        async with AsyncSession() as session:
            response = await session.get(
                url,
                timeout=60,
                proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
                headers={
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Referer": "https://labs.google/",
                },
                impersonate="chrome120",
                verify=False,
            )
            if response.status_code == 200 and response.content:
                return response.content
            debug_logger.log_warning(
                f"[CONTEXT] 图片下载失败，状态码: {response.status_code}"
            )
    except Exception as exc:
        debug_logger.log_error(f"[CONTEXT] 图片下载异常: {str(exc)}")

    return None


async def _load_image_bytes_from_uri(
    uri: str,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> bytes:
    if not uri:
        raise HTTPException(status_code=400, detail="Image URI cannot be empty")

    if uri.startswith("data:image"):
        _, image_bytes = _decode_data_url(uri)
        return image_bytes

    if (
        uri.startswith("http://")
        or uri.startswith("https://")
        or "/api/cache/blob/" in uri
        or "/api/cache/file/" in uri
    ):
        image_bytes = await retrieve_image_data(
            uri, api_key_id=api_key_id, allowed_token_ids=allowed_token_ids
        )
        if image_bytes:
            return image_bytes
        raise HTTPException(status_code=400, detail=f"Failed to load image from {uri}")

    raise HTTPException(status_code=400, detail=f"Unsupported image URI: {uri}")


def _coerce_gemini_contents(raw_contents: Optional[List[Any]]) -> List[GeminiContent]:
    contents: List[GeminiContent] = []
    for item in raw_contents or []:
        if isinstance(item, GeminiContent):
            contents.append(item)
        else:
            contents.append(GeminiContent.model_validate(item))
    return contents


def _extract_text_from_gemini_content(content: Optional[GeminiContent]) -> str:
    if content is None:
        return ""
    text_parts = [part.text.strip() for part in content.parts if part.text]
    return "\n".join(part for part in text_parts if part).strip()


def _should_ignore_media_system_instruction(system_instruction: str) -> bool:
    """Drop agent/tool scaffolding before sending media prompts upstream."""
    if not system_instruction:
        return False

    normalized = system_instruction.lower()
    if len(system_instruction) > 1200:
        return True

    return any(marker in normalized for marker in MEDIA_SYSTEM_INSTRUCTION_MARKERS)


def _sanitize_media_prompt(prompt: str) -> str:
    """Strip agent/tool scaffolding that image/video models cannot use."""
    if not prompt:
        return ""

    sanitized = MEDIA_PROMPT_TOOL_BLOCK_RE.sub(" ", prompt.strip())
    cleaned_lines: List[str] = []
    for raw_line in sanitized.splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if any(pattern.fullmatch(line) for pattern in MEDIA_PROMPT_PREAMBLE_PATTERNS):
            continue
        cleaned_lines.append(line)

    sanitized = "\n".join(cleaned_lines).strip()
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


async def _extract_prompt_and_images_from_openai_messages(
    messages: List[ChatMessage],
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> tuple[str, List[bytes]]:
    last_message = messages[-1]
    content = last_message.content
    prompt_parts: List[str] = []
    images: List[bytes] = []

    if isinstance(content, str):
        prompt_parts.append(content)
    elif isinstance(content, list):
        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text", "").strip()
                if text:
                    prompt_parts.append(text)
            elif item_type == "image_url":
                image_url = item.get("image_url", {}).get("url", "")
                images.append(
                    await _load_image_bytes_from_uri(
                        image_url,
                        api_key_id=api_key_id,
                        allowed_token_ids=allowed_token_ids,
                    )
                )

    prompt = "\n".join(part for part in prompt_parts if part).strip()
    return prompt, images


async def _append_openai_reference_images(
    model: str,
    messages: List[ChatMessage],
    images: List[bytes],
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> List[bytes]:
    model_config = MODEL_CONFIG.get(model)
    if not model_config or model_config["type"] != "image" or len(messages) <= 1:
        return images

    debug_logger.log_info(f"[CONTEXT] 开始查找历史参考图，消息数量: {len(messages)}")

    for msg in reversed(messages[:-1]):
        if msg.role == "assistant" and isinstance(msg.content, str):
            matches = MARKDOWN_IMAGE_RE.findall(msg.content)
            if not matches:
                continue

            for image_url in reversed(matches):
                if (
                    not image_url.startswith("http")
                    and "/api/cache/blob/" not in image_url
                    and "/api/cache/file/" not in image_url
                ):
                    continue
                try:
                    downloaded_bytes = await retrieve_image_data(
                        image_url,
                        api_key_id=api_key_id,
                        allowed_token_ids=allowed_token_ids,
                    )
                    if downloaded_bytes:
                        images.insert(0, downloaded_bytes)
                        debug_logger.log_info(
                            f"[CONTEXT] ✅ 添加历史参考图: {image_url}"
                        )
                        return images
                    debug_logger.log_warning(
                        f"[CONTEXT] 图片下载失败或为空，尝试下一个: {image_url}"
                    )
                except Exception as exc:
                    debug_logger.log_error(
                        f"[CONTEXT] 处理参考图时出错: {str(exc)}"
                    )
    return images


async def _extract_prompt_and_images_from_gemini_contents(
    contents: List[GeminiContent],
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> tuple[str, List[bytes]]:
    if not contents:
        raise HTTPException(status_code=400, detail="contents cannot be empty")

    target_content = next(
        (content for content in reversed(contents) if (content.role or "user") == "user"),
        contents[-1],
    )

    prompt_parts: List[str] = []
    images: List[bytes] = []

    for part in target_content.parts:
        if part.text:
            text = part.text.strip()
            if text:
                prompt_parts.append(text)
        elif part.inlineData is not None:
            mime_type = part.inlineData.mimeType.lower()
            if not mime_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported inlineData mime type: {part.inlineData.mimeType}",
                )
            images.append(base64.b64decode(part.inlineData.data))
        elif part.fileData is not None:
            mime_type = (part.fileData.mimeType or "").lower()
            if mime_type and not mime_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported fileData mime type: {part.fileData.mimeType}",
                )
            images.append(
                await _load_image_bytes_from_uri(
                    part.fileData.fileUri,
                    api_key_id=api_key_id,
                    allowed_token_ids=allowed_token_ids,
                )
            )

    prompt = "\n".join(part for part in prompt_parts if part).strip()
    return prompt, images


def _resolve_request_model(model: str, request: Any) -> str:
    resolved_model = resolve_model_name(model=model, request=request, model_config=MODEL_CONFIG)
    if resolved_model != model:
        debug_logger.log_info(f"[ROUTE] 模型名已转换: {model} → {resolved_model}")
    return resolved_model


def _get_request_base_url(request: Request) -> Optional[str]:
    """根据实际请求头推导对外可访问的基础地址。"""
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = (forwarded_host or request.headers.get("host") or "").strip()

    if not host:
        return None

    proto = forwarded_proto or request.url.scheme or "http"
    return f"{proto}://{host}"


async def _normalize_openai_request(
    request: ChatCompletionRequest,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> NormalizedGenerationRequest:
    if request.messages:
        prompt, images = await _extract_prompt_and_images_from_openai_messages(
            request.messages,
            api_key_id=api_key_id,
            allowed_token_ids=allowed_token_ids,
        )
        if request.image and not images:
            images.append(
                await _load_image_bytes_from_uri(
                    request.image,
                    api_key_id=api_key_id,
                    allowed_token_ids=allowed_token_ids,
                )
            )
        model = _resolve_request_model(request.model, request)
        images = await _append_openai_reference_images(
            model,
            request.messages,
            images,
            api_key_id=api_key_id,
            allowed_token_ids=allowed_token_ids,
        )
        return NormalizedGenerationRequest(
            model=model,
            prompt=prompt,
            images=images,
            messages=request.messages,
            project_id=_strip_optional_project_id(request.project_id),
        )

    if request.contents:
        gemini_request = GeminiGenerateContentRequest(
            contents=_coerce_gemini_contents(request.contents),
            generationConfig=request.generationConfig,
            project_id=request.project_id,
        )
        normalized = await _normalize_gemini_request(
            request.model,
            gemini_request,
            api_key_id=api_key_id,
            allowed_token_ids=allowed_token_ids,
        )
        normalized.messages = request.messages
        if request.project_id is not None and normalized.project_id is None:
            normalized.project_id = _strip_optional_project_id(request.project_id)
        return normalized

    raise HTTPException(status_code=400, detail="Messages or contents cannot be empty")


async def _normalize_gemini_request(
    model: str,
    request: GeminiGenerateContentRequest,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> NormalizedGenerationRequest:
    resolved_model = _resolve_request_model(model, request)
    prompt, images = await _extract_prompt_and_images_from_gemini_contents(
        request.contents,
        api_key_id=api_key_id,
        allowed_token_ids=allowed_token_ids,
    )
    system_instruction = _extract_text_from_gemini_content(request.systemInstruction)
    model_config = MODEL_CONFIG.get(resolved_model)
    media_model = bool(model_config and model_config.get("type") in {"image", "video"})

    if media_model:
        prompt = _sanitize_media_prompt(prompt)

    if system_instruction:
        if media_model and _should_ignore_media_system_instruction(system_instruction):
            debug_logger.log_warning(
                f"[GEMINI] 忽略媒体模型的 systemInstruction: model={resolved_model}, len={len(system_instruction)}"
            )
        else:
            if media_model:
                system_instruction = _sanitize_media_prompt(system_instruction)
            prompt = f"{system_instruction}\n\n{prompt}".strip()

    return NormalizedGenerationRequest(
        model=resolved_model,
        prompt=prompt,
        images=images,
        project_id=_strip_optional_project_id(request.project_id),
    )


async def _collect_non_stream_result(
    normalized: NormalizedGenerationRequest,
    base_url_override: Optional[str] = None,
    allowed_token_ids: Optional[set[int]] = None,
    api_key_id: Optional[int] = None,
) -> str:
    handler = _ensure_generation_handler()
    result = None
    async for chunk in handler.handle_generation(
        model=normalized.model,
        prompt=normalized.prompt,
        images=normalized.images if normalized.images else None,
        stream=False,
        base_url_override=base_url_override,
        allowed_token_ids=allowed_token_ids,
        api_key_id=api_key_id,
        requested_project_id=normalized.project_id,
    ):
        result = chunk

    if result is None:
        raise HTTPException(status_code=500, detail="Generation failed: No response")

    return result


def _parse_handler_result(result: str) -> Dict[str, Any]:
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return {"result": result}


def _get_error_status_code(payload: Dict[str, Any]) -> int:
    error = payload.get("error")
    if isinstance(error, dict):
        status_code = error.get("status_code")
        if isinstance(status_code, int):
            return status_code
        if isinstance(status_code, str) and status_code.isdigit():
            return int(status_code)
        return 400
    return 200


def _build_openai_json_response(payload: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(content=payload, status_code=_get_error_status_code(payload))


def _build_gemini_error_payload(status_code: int, message: str) -> Dict[str, Any]:
    return {
        "error": {
            "code": status_code,
            "message": message,
            "status": GEMINI_STATUS_MAP.get(status_code, "UNKNOWN"),
        }
    }


def _build_gemini_error_response_from_handler(payload: Dict[str, Any]) -> JSONResponse:
    error = payload.get("error", {})
    status_code = _get_error_status_code(payload)
    message = error.get("message", "Generation failed")
    return JSONResponse(
        status_code=status_code,
        content=_build_gemini_error_payload(status_code, message),
    )


def _extract_openai_message_content(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return payload.get("result", "")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


def _extract_url_from_openai_payload(payload: Dict[str, Any]) -> Optional[str]:
    direct_url = payload.get("url")
    if isinstance(direct_url, str) and direct_url.strip():
        return direct_url.strip()

    content = _extract_openai_message_content(payload).strip()
    if not content:
        return None

    image_match = MARKDOWN_IMAGE_RE.search(content)
    if image_match:
        return image_match.group(1).strip()

    video_match = HTML_VIDEO_RE.search(content)
    if video_match:
        return video_match.group(1).strip()

    return None


def _enrich_payload_with_direct_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    extracted_url = _extract_url_from_openai_payload(payload)
    if extracted_url and not payload.get("url"):
        payload["url"] = extracted_url
    return payload


async def _build_image_parts_from_uri(
    uri: str,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> List[Dict[str, Any]]:
    if uri.startswith("data:image"):
        mime_type, _ = _decode_data_url(uri)
        match = DATA_URL_RE.match(uri)
        if match:
            return [{"inlineData": {"mimeType": mime_type, "data": match.group("data")}}]

    image_bytes = await retrieve_image_data(
        uri, api_key_id=api_key_id, allowed_token_ids=allowed_token_ids
    )
    if image_bytes:
        mime_type = _detect_image_mime_type(
            image_bytes,
            fallback=_guess_mime_type(uri, "image/png"),
        )
        return [
            {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                }
            }
        ]

    return [
        {
            "fileData": {
                "mimeType": _guess_mime_type(uri, "image/png"),
                "fileUri": uri,
            }
        },
        {"text": uri},
    ]


def _build_video_parts_from_uri(uri: str) -> List[Dict[str, Any]]:
    return [
        {
            "fileData": {
                "mimeType": _guess_mime_type(uri, "video/mp4"),
                "fileUri": uri,
            }
        }
    ]


async def _build_gemini_parts_from_output(
    output: str,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> List[Dict[str, Any]]:
    if not output:
        return []

    image_matches = MARKDOWN_IMAGE_RE.findall(output)
    if image_matches:
        parts: List[Dict[str, Any]] = []
        for uri in image_matches:
            parts.extend(
                await _build_image_parts_from_uri(
                    uri, api_key_id=api_key_id, allowed_token_ids=allowed_token_ids
                )
            )
        return parts

    video_matches = HTML_VIDEO_RE.findall(output)
    if video_matches:
        parts: List[Dict[str, Any]] = []
        for uri in video_matches:
            parts.extend(_build_video_parts_from_uri(uri))
        return parts

    # Progress / thought streams: blank-line-separated blocks render as separate steps in Gemini UIs.
    blocks = [b.strip() for b in output.split("\n\n") if b.strip()]
    if len(blocks) > 1:
        return [{"text": b} for b in blocks]
    return [{"text": output}]


async def _build_gemini_success_payload(
    payload: Dict[str, Any],
    response_model: str,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    output = _extract_openai_message_content(payload)
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": await _build_gemini_parts_from_output(
                        output,
                        api_key_id=api_key_id,
                        allowed_token_ids=allowed_token_ids,
                    ),
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "modelVersion": response_model,
    }


def _normalize_finish_reason(reason: Optional[str]) -> Optional[str]:
    if reason is None:
        return None
    mapping = {
        "stop": "STOP",
        "length": "MAX_TOKENS",
        "content_filter": "SAFETY",
    }
    return mapping.get(reason, "STOP")


async def _convert_openai_stream_chunk_to_gemini_event(
    payload: Dict[str, Any],
    response_model: str,
    api_key_id: Optional[int] = None,
    allowed_token_ids: Optional[Set[int]] = None,
) -> Optional[str]:
    choices = payload.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    text = delta.get("reasoning_content") or delta.get("content") or ""
    finish_reason = _normalize_finish_reason(choice.get("finish_reason"))

    candidate: Dict[str, Any] = {"index": choice.get("index", 0)}
    if text:
        candidate["content"] = {
            "role": "model",
            "parts": await _build_gemini_parts_from_output(
                text,
                api_key_id=api_key_id,
                allowed_token_ids=allowed_token_ids,
            ),
        }
    if finish_reason:
        candidate["finishReason"] = finish_reason

    if len(candidate) == 1:
        return None

    chunk = {
        "candidates": [candidate],
        "modelVersion": response_model,
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _iterate_openai_stream(
    normalized: NormalizedGenerationRequest,
    base_url_override: Optional[str] = None,
    allowed_token_ids: Optional[set[int]] = None,
    api_key_id: Optional[int] = None,
):
    handler = _ensure_generation_handler()
    async for chunk in handler.handle_generation(
        model=normalized.model,
        prompt=normalized.prompt,
        images=normalized.images if normalized.images else None,
        stream=True,
        base_url_override=base_url_override,
        allowed_token_ids=allowed_token_ids,
        api_key_id=api_key_id,
        requested_project_id=normalized.project_id,
    ):
        if chunk.startswith("data: "):
            yield chunk
            continue

        payload = _parse_handler_result(chunk)
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


async def _iterate_gemini_stream(
    normalized: NormalizedGenerationRequest,
    response_model: str,
    base_url_override: Optional[str] = None,
    allowed_token_ids: Optional[set[int]] = None,
    api_key_id: Optional[int] = None,
):
    handler = _ensure_generation_handler()
    async for chunk in handler.handle_generation(
        model=normalized.model,
        prompt=normalized.prompt,
        images=normalized.images if normalized.images else None,
        stream=True,
        base_url_override=base_url_override,
        allowed_token_ids=allowed_token_ids,
        api_key_id=api_key_id,
        requested_project_id=normalized.project_id,
    ):
        if chunk.startswith("data: "):
            payload_text = chunk[6:].strip()
            if payload_text == "[DONE]":
                continue
            payload = _parse_handler_result(payload_text)
            if "error" in payload:
                yield (
                    f"data: {json.dumps(_build_gemini_error_payload(_get_error_status_code(payload), payload['error'].get('message', 'Generation failed')), ensure_ascii=False)}\n\n"
                )
                return

            event = await _convert_openai_stream_chunk_to_gemini_event(
                payload,
                response_model,
                api_key_id=api_key_id,
                allowed_token_ids=allowed_token_ids,
            )
            if event:
                yield event
            continue

        payload = _parse_handler_result(chunk)
        if "error" in payload:
            yield (
                f"data: {json.dumps(_build_gemini_error_payload(_get_error_status_code(payload), payload['error'].get('message', 'Generation failed')), ensure_ascii=False)}\n\n"
            )
            return

        event = await _convert_openai_stream_chunk_to_gemini_event(
            payload,
            response_model,
            api_key_id=api_key_id,
            allowed_token_ids=allowed_token_ids,
        )
        if event:
            yield event


def _resolve_allowed_token_ids(auth_ctx: AuthContext) -> Optional[set[int]]:
    if not auth_ctx.is_legacy and auth_ctx.allowed_accounts:
        return {int(x) for x in auth_ctx.allowed_accounts}
    return None


async def _resolve_project_pin(
    project_id: Optional[str],
    auth_ctx: AuthContext,
) -> Tuple[Optional[Set[int]], Optional[str]]:
    """If project_id is set, validate DB row and return ({token_id}, canonical_id); else (None, None)."""
    pid = _strip_optional_project_id(project_id)
    if not pid:
        return (None, None)
    handler = _ensure_generation_handler()
    if auth_ctx.key_id is not None:
        proj = await handler.db.get_project_by_id(pid, auth_ctx.key_id)
        if not proj:
            raise HTTPException(
                status_code=400,
                detail="project_id not found for this API key",
            )
        tid = int(proj.token_id)
        if tid not in auth_ctx.allowed_accounts:
            raise HTTPException(
                status_code=400,
                detail="project_id is not assigned to this API key",
            )
        return ({tid}, pid)
    proj = await handler.db.get_project_by_id(pid, None)
    if not proj:
        raise HTTPException(status_code=400, detail="project_id not found")
    return ({int(proj.token_id)}, pid)


def _require_managed_projects_read(auth_ctx: AuthContext) -> None:
    """Managed keys: list/get projects if legacy or scopes include read/write wildcard."""
    if auth_ctx.is_legacy:
        return
    if "*" in auth_ctx.scopes or "projects:read" in auth_ctx.scopes or "projects:write" in auth_ctx.scopes:
        return
    raise HTTPException(
        status_code=403,
        detail="Missing scope: allow '*', 'projects:read', or 'projects:write'",
    )


def _project_row_to_api_dict(p: Project) -> Dict[str, Any]:
    """Serialize a Project model for JSON APIs."""
    d = p.model_dump()
    created = d.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        d["created_at"] = created.isoformat()
    return {
        "project_id": d.get("project_id"),
        "project_name": d.get("project_name"),
        "token_id": d.get("token_id"),
        "is_active": bool(d.get("is_active", True)),
        "created_at": d.get("created_at"),
    }


def _require_managed_projects_write(auth_ctx: AuthContext) -> None:
    """Managed keys need wildcard or projects:write to create Flow projects."""
    if auth_ctx.is_legacy:
        return
    if "*" in auth_ctx.scopes or "projects:write" in auth_ctx.scopes:
        return
    raise HTTPException(
        status_code=403,
        detail="Missing scope: allow '*' or add 'projects:write' for this key",
    )


@router.get("/v1/projects")
async def list_flow_projects(
    account_id: Optional[int] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """List VideoFX projects visible to this managed API key (optional filter by account / token id)."""
    if auth_ctx.key_id is None:
        raise HTTPException(status_code=403, detail="Managed API key required")
    _require_managed_projects_read(auth_ctx)
    handler = _ensure_generation_handler()
    kid = auth_ctx.key_id
    limit_clean = max(1, min(int(limit), 100))
    offset_clean = max(0, int(offset))
    if account_id is not None:
        aid = int(account_id)
        if aid not in auth_ctx.allowed_accounts:
            raise HTTPException(status_code=400, detail="account_id is not assigned to this API key")
        total = await handler.db.count_projects_for_api_key_account(kid, aid)
        projects = await handler.db.list_projects_for_api_key_account(
            kid, aid, limit=limit_clean, offset=offset_clean
        )
    else:
        total = await handler.db.count_projects_by_api_key(kid)
        projects = await handler.db.list_projects_by_api_key(
            kid, limit=limit_clean, offset=offset_clean
        )
    data = [_project_row_to_api_dict(p) for p in projects]
    return {
        "object": "list",
        "data": data,
        "total": total,
        "limit": limit_clean,
        "offset": offset_clean,
    }


@router.get("/v1/projects/{project_id}")
async def get_flow_project(
    project_id: str,
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """Return one VideoFX project row if it belongs to this managed API key."""
    if auth_ctx.key_id is None:
        raise HTTPException(status_code=403, detail="Managed API key required")
    _require_managed_projects_read(auth_ctx)
    handler = _ensure_generation_handler()
    pid = project_id.strip()
    if not pid:
        raise HTTPException(status_code=400, detail="project_id is required")
    proj = await handler.db.get_project_by_id(pid, auth_ctx.key_id)
    if not proj or int(proj.token_id) not in auth_ctx.allowed_accounts:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"object": "flow_project", **_project_row_to_api_dict(proj)}


@router.post("/v1/projects")
async def create_flow_project(
    body: FlowProjectCreateRequest,
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """Create VideoFX project(s) for managed key assigned account(s)."""
    if auth_ctx.key_id is None:
        raise HTTPException(status_code=403, detail="Managed API key required")
    _require_managed_projects_write(auth_ctx)
    if not auth_ctx.allowed_accounts:
        raise HTTPException(
            status_code=400,
            detail="No accounts assigned to this API key",
        )
    if body.account_id is not None:
        account_id = int(body.account_id)
        if account_id not in auth_ctx.allowed_accounts:
            raise HTTPException(status_code=400, detail="account_id is not assigned to this API key")
        target_accounts = [account_id]
    else:
        target_accounts = sorted(auth_ctx.allowed_accounts)

    handler = _ensure_generation_handler()
    title = (body.title or "").strip() or None
    created_projects = []
    try:
        for account_id in target_accounts:
            project = await handler.token_manager.create_project_for_token(
                account_id,
                title=title,
                set_as_current=bool(body.set_as_current),
                api_key_id=auth_ctx.key_id,
            )
            created_projects.append(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create project failed: {str(e)}")

    if body.account_id is not None:
        project = created_projects[0]
        return {
            "object": "flow_project",
            "project_id": project.project_id,
            "project_name": project.project_name,
            "token_id": project.token_id,
            "set_as_current": body.set_as_current,
        }

    return {
        "object": "list",
        "data": [
            {
                "object": "flow_project",
                "project_id": project.project_id,
                "project_name": project.project_name,
                "token_id": project.token_id,
                "set_as_current": body.set_as_current,
            }
            for project in created_projects
        ],
        "total": len(created_projects),
    }


@router.get("/api/cache/file")
async def list_cache_files_for_key(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """List cache file metadata rows owned by this managed API key."""
    if auth_ctx.key_id is None:
        raise HTTPException(status_code=403, detail="Managed API key required")
    handler = _ensure_generation_handler()
    kid = auth_ctx.key_id
    lim = int(limit)
    off = int(offset)
    total = await handler.db.count_cache_files_for_api_key(kid)
    rows = await handler.db.list_cache_files_for_api_key(kid, limit=lim, offset=off)
    data = [_cache_file_row_to_list_item(r) for r in rows]
    return {
        "object": "list",
        "data": data,
        "pagination": {
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": off + len(data) < total,
        },
    }


@router.get("/api/cache/file/{project_id}")
async def list_cache_files_for_key_project(
    project_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """List cache file metadata for one Flow project UUID under this managed API key."""
    if auth_ctx.key_id is None:
        raise HTTPException(status_code=403, detail="Managed API key required")
    pid = project_id.strip()
    if not pid:
        raise HTTPException(status_code=400, detail="project_id is required")
    handler = _ensure_generation_handler()
    proj = await handler.db.get_project_by_id(pid, auth_ctx.key_id)
    if not proj:
        raise HTTPException(status_code=400, detail="project_id not found for this API key")
    tid = int(proj.token_id)
    if tid not in auth_ctx.allowed_accounts:
        raise HTTPException(status_code=400, detail="project_id is not assigned to this API key")
    kid = auth_ctx.key_id
    lim = int(limit)
    off = int(offset)
    total = await handler.db.count_cache_files_for_api_key_project(kid, pid)
    rows = await handler.db.list_cache_files_for_api_key_project(
        kid, pid, limit=lim, offset=off
    )
    data = [_cache_file_row_to_list_item(r) for r in rows]
    return {
        "object": "list",
        "data": data,
        "pagination": {
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": off + len(data) < total,
        },
    }


@router.get("/api/cache/blob/{filename}")
async def get_cached_blob(
    filename: str,
    project_id: Optional[str] = Query(None),
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """Stream a cache file owned by this managed API key (use list endpoints to discover filenames)."""
    handler = _ensure_generation_handler()
    if auth_ctx.key_id is None:
        raise HTTPException(status_code=403, detail="Managed API key required")
    safe_name = Path(filename).name
    metadata = await handler.db.get_cache_file_for_api_key(safe_name, auth_ctx.key_id)
    if not metadata:
        raise HTTPException(status_code=403, detail="Cache file not owned by this API key")
    meta_flow = _strip_optional_project_id(metadata.get("flow_project_id"))
    if meta_flow:
        q = _strip_optional_project_id(project_id)
        if not q or q != meta_flow:
            raise HTTPException(
                status_code=403,
                detail="project_id query parameter required and must match the cache entry",
            )
        proj = await handler.db.get_project_by_id(q, auth_ctx.key_id)
        if not proj:
            raise HTTPException(status_code=400, detail="project_id not found for this API key")
        tid = int(proj.token_id)
        if tid not in auth_ctx.allowed_accounts:
            raise HTTPException(status_code=400, detail="project_id is not assigned to this API key")
    file_path = handler.file_cache.cache_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Cache file not found")
    media_type = metadata.get("media_type") or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return FileResponse(path=file_path, media_type=media_type, filename=safe_name)


@router.get("/v1/models")
async def list_models(auth_ctx: AuthContext = Depends(verify_api_key_flexible)):
    """List available models."""
    models = [
        {
            "id": model["id"],
            "object": "model",
            "owned_by": "flow2api",
            "description": model["description"],
        }
        for model in _get_openai_model_catalog()
    ]

    return {"object": "list", "data": models}


@router.get("/v1/models/aliases")
async def list_model_aliases(auth_ctx: AuthContext = Depends(verify_api_key_flexible)):
    """List simplified model aliases for generationConfig-based resolution."""
    aliases = get_base_model_aliases()
    alias_models = []
    for alias_id, description in aliases.items():
        alias_models.append(
            {
                "id": alias_id,
                "object": "model",
                "owned_by": "flow2api",
                "description": description,
                "is_alias": True,
            }
        )
    return {"object": "list", "data": alias_models}


@router.get("/v1beta/models")
@router.get("/models")
async def list_gemini_models(auth_ctx: AuthContext = Depends(verify_api_key_flexible)):
    """List available models using Gemini-compatible response shape."""
    catalog = _get_gemini_model_catalog()
    return {
        "models": [
            _build_gemini_model_resource(model_id, description)
            for model_id, description in catalog.items()
        ]
    }


@router.get("/v1beta/models/{model}")
@router.get("/models/{model}")
async def get_gemini_model(model: str, auth_ctx: AuthContext = Depends(verify_api_key_flexible)):
    """Return a single model using Gemini-compatible response shape."""
    catalog = _get_gemini_model_catalog()
    description = catalog.get(model)
    if not description:
        return JSONResponse(
            status_code=404,
            content=_build_gemini_error_payload(404, f"Model not found: {model}"),
        )

    return _build_gemini_model_resource(model, description)


@router.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
    raw_request: Request,
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """OpenAI-compatible unified generation endpoint."""
    try:
        if auth_ctx.key_id is None:
            raise HTTPException(status_code=403, detail="Managed API key required for generation")
        base_allowed = _resolve_allowed_token_ids(auth_ctx)
        normalized = await _normalize_openai_request(
            request,
            api_key_id=auth_ctx.key_id,
            allowed_token_ids=base_allowed,
        )
        if not normalized.prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")

        request_base_url = _get_request_base_url(raw_request)
        pin_set, pin_pid = await _resolve_project_pin(normalized.project_id, auth_ctx)
        allowed_token_ids = pin_set if pin_set is not None else base_allowed
        if pin_pid is not None:
            normalized = replace(normalized, project_id=pin_pid)

        if request.stream:
            return StreamingResponse(
                _iterate_openai_stream(
                    normalized,
                    request_base_url,
                    allowed_token_ids,
                    api_key_id=auth_ctx.key_id,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        payload = _enrich_payload_with_direct_url(
            _parse_handler_result(
                await _collect_non_stream_result(
                    normalized,
                    request_base_url,
                    allowed_token_ids,
                    api_key_id=auth_ctx.key_id,
                )
            )
        )
        return _build_openai_json_response(payload)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v1beta/models/{model}:generateContent")
@router.post("/models/{model}:generateContent")
async def generate_content(
    model: str,
    request: GeminiGenerateContentRequest,
    raw_request: Request,
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """Gemini official generateContent endpoint."""
    try:
        if auth_ctx.key_id is None:
            raise HTTPException(status_code=403, detail="Managed API key required for generation")
        base_allowed = _resolve_allowed_token_ids(auth_ctx)
        normalized = await _normalize_gemini_request(
            model,
            request,
            api_key_id=auth_ctx.key_id,
            allowed_token_ids=base_allowed,
        )
        if not normalized.prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")

        request_base_url = _get_request_base_url(raw_request)
        pin_set, pin_pid = await _resolve_project_pin(normalized.project_id, auth_ctx)
        allowed_token_ids = pin_set if pin_set is not None else base_allowed
        if pin_pid is not None:
            normalized = replace(normalized, project_id=pin_pid)

        payload = _enrich_payload_with_direct_url(
            _parse_handler_result(
                await _collect_non_stream_result(
                    normalized,
                    request_base_url,
                    allowed_token_ids,
                    api_key_id=auth_ctx.key_id,
                )
            )
        )
        if "error" in payload:
            return _build_gemini_error_response_from_handler(payload)

        return JSONResponse(
            content=await _build_gemini_success_payload(
                payload,
                normalized.model,
                api_key_id=auth_ctx.key_id,
                allowed_token_ids=allowed_token_ids,
            )
        )

    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_gemini_error_payload(exc.status_code, str(exc.detail)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_build_gemini_error_payload(500, str(exc)),
        )


@router.post("/v1beta/models/{model}:streamGenerateContent")
@router.post("/models/{model}:streamGenerateContent")
async def stream_generate_content(
    model: str,
    request: GeminiGenerateContentRequest,
    raw_request: Request,
    alt: Optional[str] = Query(None),
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """Gemini official streamGenerateContent endpoint."""
    try:
        if auth_ctx.key_id is None:
            raise HTTPException(status_code=403, detail="Managed API key required for generation")
        base_allowed = _resolve_allowed_token_ids(auth_ctx)
        normalized = await _normalize_gemini_request(
            model,
            request,
            api_key_id=auth_ctx.key_id,
            allowed_token_ids=base_allowed,
        )
        if not normalized.prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")

        request_base_url = _get_request_base_url(raw_request)
        pin_set, pin_pid = await _resolve_project_pin(normalized.project_id, auth_ctx)
        allowed_token_ids = pin_set if pin_set is not None else base_allowed
        if pin_pid is not None:
            normalized = replace(normalized, project_id=pin_pid)

        return StreamingResponse(
            _iterate_gemini_stream(
                normalized,
                normalized.model,
                request_base_url,
                allowed_token_ids,
                api_key_id=auth_ctx.key_id,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_gemini_error_payload(exc.status_code, str(exc.detail)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_build_gemini_error_payload(500, str(exc)),
        )


@router.get("/v1/api-key/allowed-tokens")
async def get_allowed_tokens(
    auth_ctx: AuthContext = Depends(verify_api_key_flexible),
):
    """Get the allowed tokens (accounts) and their credits for the current API key."""
    handler = _ensure_generation_handler()
    db = handler.db
    
    tokens_info = []
    for token_id in auth_ctx.allowed_accounts:
        token = await db.get_token(token_id)
        if token and token.is_active:
            tokens_info.append({
                "id": token.id,
                "email": token.email,
                "label": token.remark or token.name or "default",
                "credits": token.credits,
                "user_paygate_tier": token.user_paygate_tier,
                "is_active": token.is_active
            })
            
    return {
        "success": True,
        "api_key_label": auth_ctx.key_label,
        "allowed_tokens": tokens_info
    }
