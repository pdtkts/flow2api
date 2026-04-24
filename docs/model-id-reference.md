# Model ID reference

> **Maintenance:** When you add or change entries in `MODEL_CONFIG` in [`src/services/generation_handler.py`](../src/services/generation_handler.py), update this document so it stays accurate. Keys not listed in the tables below still exist in that file (e.g. `veo_3_1_interpolation_lite_*`).

This page decodes public model `id`s from `GET /v1/models` / `GET /v1beta/models`: how each **family pattern** maps to **internal** settings (upstream `model_key` or `model_name`, aspect, image counts, and optional **upscale** pipelines). Concrete ids are the Cartesian product of the pattern parts listed (full enumeration lives in `MODEL_CONFIG`).

**Source of truth:** `MODEL_CONFIG` in `generation_handler.py`.  
**List descriptions:** [`_build_model_description`](../src/api/routes.py) — images append `model_name` (e.g. `GEM_PIX_2`); videos append the video `model_key` (e.g. `veo_3_1_t2v_fast`).

For HTTP usage and examples, see [customer-guide-video-jobs.md](./customer-guide-video-jobs.md). For job timing and video upscale behavior, see [§7 there](./customer-guide-video-jobs.md#7-video-upscaling-4k-and-1080p-models).

---

## Naming legend

| Pattern | Meaning |
|---------|---------|
| **Image `gemini-*` / `imagen-*`** | `{product}-{version}-{role}-image-{orientation}` optional **`-2k` / `-4k`**. Orientation token sets aspect; suffix selects **image upsample** tier after base render (see image table). No `-2k`/`-4k` ⇒ no image upsample step in Flow2API for that id (informally “base” tier; not a literal “1K” product name). |
| **Video `veo_3_1_*`** | `veo_3_1_{family}_{modifiers}_{aspect?}…` — **`t2v`** / **`i2v_s`** / **`i2v_lite`** / **`r2v`** in the id stem indicate mode or branch. **No `_4k` / `_1080p` suffix** ⇒ base render only (docs label **720p** tier: no Flow2API video upsampler; actual pixels depend on Google). **`_4k` / `_1080p`** ⇒ second-phase **video upsampler** after base video. Portrait vs landscape is in the id (`…_portrait` / `…_landscape`) except some landscape ids omit `_landscape` (see video sections). |

---

## Image models (`type: image`)

**Model name** is a readable label; **Model** is the short family key used in docs and id patterns. Real `id`s look like `gemini-3.1-flash-image-{orientation}` or `…-2k` (see **Full `id` pattern**).

| Model name | Model | Full `id` pattern | Internal `model_name` | Orientations | Resolution (suffix × each aspect) | Pipeline |
|------------|-------|-------------------|------------------------|--------------|-----------------------------------|----------|
| Gemini 2.5 Flash Image | gemini-2.5 | `gemini-2.5-flash-image-{orientation}` | GEM_PIX | `landscape`, `portrait` | **base** (no suffix) | Base image only |
| Gemini 3.0 Pro Image | gemini-3.0-pro | `gemini-3.0-pro-image-{orientation}[-2k\|-4k]` | GEM_PIX_2 | `landscape`, `portrait`, `square`, `four-three`, `three-four` | **base**, **2K**, **4K** | Each aspect × each suffix; `-2k`/`-4k` → `upsampleImage` |
| Gemini 3.1 Flash Image | gemini-3.1 | `gemini-3.1-flash-image-{orientation}[-2k\|-4k]` | NARWHAL | same five as 3.0-pro | **base**, **2K**, **4K** | Same as gemini-3.0-pro |
| Imagen 4.0 (preview) | imagen-4.0 | `imagen-4.0-generate-preview-{orientation}` | IMAGEN_3_5 | `landscape`, `portrait` | **base** | Base only |

---

## Video models — map by mode

Every video row below is **Veo 3.1** in `MODEL_CONFIG`. Use this table first: choose the **mode** (what you send—prompt only, one or two frames, or references), then open the matching section for catalog `id`s and studio presets (**FAST**, **FAST ULTRA**, **Quality**, **Lite**, … are **tier / quality lines**; the same labels appear in more than one mode where that tier exists).

| Line (how you use it) | Capability | `video_type` | Catalog `id` stem | Images in `MODEL_CONFIG` | `supports_images` |
|-----------------------|------------|--------------|-------------------|---------------------------|---------------------|
| **Text-to-video** | Video from **text only**; attached images are not used for generation | `t2v` | `veo_3_1_t2v_*` | n/a | `false` |
| **Image-to-video (standard)** | **1–2** input images (first frame, or first + last) | `i2v` | `veo_3_1_i2v_s_*` | `min_images` 1, `max_images` 2 | `true` |
| **Image-to-video (lite)** | **One** first-frame image; lite / v2 config | `i2v` | `veo_3_1_i2v_lite_*` | 1–1 | `true` |
| **Reference-to-video (R2V)** | **0–3** reference images with the prompt (multi-image / “refs” style) | `r2v` | `veo_3_1_r2v_*` | `min_images` 0, `max_images` 3 | `true` |

Additional **`video_type`: `i2v`** ids (e.g. `veo_3_1_interpolation_lite_*`, two images) are only in `MODEL_CONFIG`; they are not expanded in the tables on this page.

---

## Video — `veo_3_1_t2v` (text-to-video)

**Mode:** text-to-video — see **Video models — map by mode** above.

Text only (`video_type: t2v`). **`supports_images`: false** — ignore attached images for these ids.

**Resolution:** **720p** = base render only (no `upsample` in `MODEL_CONFIG`; doc label, not a Google enum). **4K** / **1080p** = base video then **video** upsampler (`veo_3_1_upsampler_4k` / `veo_3_1_upsampler_1080p`).

**Catalog `id`:** when **Resolution** lists `720p, 4K, 1080p`, **Catalog `id`** is six ids in order: *(720p portrait, 720p landscape, 4K portrait, 4K landscape, 1080p portrait, 1080p landscape)*. When only **720p**, two ids *(portrait, landscape)*. **Base `model_key`:** always the **phase-1** pair *(portrait key, landscape key)* — the same two keys apply to every tier in that row. Landscape **fast** keys omit `_landscape` in the string (`veo_3_1_t2v_fast`).

**Model name** is the readable product label; **Model** is the `id` family token (same on every row in a section).

| Model name | Model | Studio preset | Aspect | Resolution | Catalog `id` | Base `model_key` (phase 1) |
|------------|-------|---------------|--------|------------|--------------|----------------------------|
| Veo 3.1 FAST | veo_3_1_t2v | fast | portrait, landscape | 720p, 4K, 1080p | `veo_3_1_t2v_fast_portrait`, `veo_3_1_t2v_fast_landscape`, `veo_3_1_t2v_fast_portrait_4k`, `veo_3_1_t2v_fast_4k`, `veo_3_1_t2v_fast_portrait_1080p`, `veo_3_1_t2v_fast_1080p` | `veo_3_1_t2v_fast_portrait`, `veo_3_1_t2v_fast` |
| Veo 3.1 FAST ULTRA | veo_3_1_t2v | fast_ultra | portrait, landscape | 720p, 4K, 1080p | `veo_3_1_t2v_fast_portrait_ultra`, `veo_3_1_t2v_fast_ultra`, `veo_3_1_t2v_fast_portrait_ultra_4k`, `veo_3_1_t2v_fast_ultra_4k`, `veo_3_1_t2v_fast_portrait_ultra_1080p`, `veo_3_1_t2v_fast_ultra_1080p` | `veo_3_1_t2v_fast_portrait_ultra`, `veo_3_1_t2v_fast_ultra` |
| Veo 3.1 Low Priority | veo_3_1_t2v | fast_ultra_relaxed | portrait, landscape | 720p | `veo_3_1_t2v_fast_portrait_ultra_relaxed`, `veo_3_1_t2v_fast_ultra_relaxed` | `veo_3_1_t2v_fast_portrait_ultra_relaxed`, `veo_3_1_t2v_fast_ultra_relaxed` |
| Veo 3.1 Quality | veo_3_1_t2v | standard | portrait, landscape | 720p | `veo_3_1_t2v_portrait`, `veo_3_1_t2v_landscape` | `veo_3_1_t2v_portrait`, `veo_3_1_t2v` |
| Veo 3.1 Lite | veo_3_1_t2v | lite | portrait, landscape | 720p | `veo_3_1_t2v_lite_portrait`, `veo_3_1_t2v_lite_landscape` | `veo_3_1_t2v_lite`, `veo_3_1_t2v_lite` |

**Lite** uses `use_v2_model_config` and `allow_tier_upgrade`: false in `MODEL_CONFIG`.

---

## Video — `veo_3_1_i2v_s` (image-to-video)

**Mode:** image-to-video (standard, 1–2 images) — see **Video models — map by mode** above.

**Studio preset `s`** line: catalog `id`s start with `veo_3_1_i2v_s` before the next `_` segment. **`video_type`: i2v**; **1–2** reference images.

Comma-separated **Aspect** / **`id`** / **`model_key`** columns are parallel (portrait, then landscape). Landscape **s_fast_fl** catalog id is `…_fast_fl` (no `_landscape` in the id).

**Resolution** and **Catalog `id`** ordering match the **`veo_3_1_t2v`** section (720p pair, then optional 4K / 1080p pairs). **Model name** / **Model** — same meaning as the t2v table; **Model** here is **`veo_3_1_i2v_s`**.

| Model name | Model | Studio preset | Aspect | Images | Resolution | Catalog `id` | Base `model_key` (phase 1) |
|------------|-------|---------------|--------|--------|------------|--------------|----------------------------|
| Veo 3.1 FAST | veo_3_1_i2v_s | s_fast_fl | portrait, landscape | 1–2 | 720p | `veo_3_1_i2v_s_fast_portrait_fl`, `veo_3_1_i2v_s_fast_fl` | `veo_3_1_i2v_s_fast_portrait_fl`, `veo_3_1_i2v_s_fast_fl` |
| Veo 3.1 FAST ULTRA | veo_3_1_i2v_s | s_fast_ultra_fl | portrait, landscape | 1–2 | 720p, 4K, 1080p | `veo_3_1_i2v_s_fast_portrait_ultra_fl`, `veo_3_1_i2v_s_fast_ultra_fl`, `veo_3_1_i2v_s_fast_portrait_ultra_fl_4k`, `veo_3_1_i2v_s_fast_ultra_fl_4k`, `veo_3_1_i2v_s_fast_portrait_ultra_fl_1080p`, `veo_3_1_i2v_s_fast_ultra_fl_1080p` | `veo_3_1_i2v_s_fast_portrait_ultra_fl`, `veo_3_1_i2v_s_fast_ultra_fl` |
| Veo 3.1 Low Priority | veo_3_1_i2v_s | s_fast_ultra_relaxed | portrait, landscape | 1–2 | 720p | `veo_3_1_i2v_s_fast_portrait_ultra_relaxed`, `veo_3_1_i2v_s_fast_ultra_relaxed` | `veo_3_1_i2v_s_fast_portrait_ultra_relaxed`, `veo_3_1_i2v_s_fast_ultra_relaxed` |
| Veo 3.1 Quality | veo_3_1_i2v_s | s | portrait, landscape | 1–2 | 720p | `veo_3_1_i2v_s_portrait`, `veo_3_1_i2v_s_landscape` | `veo_3_1_i2v_s`, `veo_3_1_i2v_s` |

---

## Video — `veo_3_1_i2v_lite` (image-to-video, lite)

**Mode:** image-to-video (lite, one first frame) — see **Video models — map by mode** above.

Catalog ids **`veo_3_1_i2v_lite_*`**; **`model_key`** `veo_3_1_i2v_lite`. **1–1** images (first frame only). Uses v2 model config in `MODEL_CONFIG`.

| Model name | Model | Studio preset | Aspect | Images | Resolution | Catalog `id` | Base `model_key` (phase 1) |
|------------|-------|---------------|--------|--------|------------|--------------|----------------------------|
| Veo 3.1 Lite | veo_3_1_i2v_lite | lite | portrait, landscape | **1–1** | 720p | `veo_3_1_i2v_lite_portrait`, `veo_3_1_i2v_lite_landscape` | `veo_3_1_i2v_lite`, `veo_3_1_i2v_lite` |

---

## Video — `veo_3_1_r2v` (reference-to-video)

**Mode:** reference-to-video (R2V, 0–3 reference images) — see **Video models — map by mode** above.

Comma-separated columns are parallel (portrait, landscape). Landscape **fast** uses short catalog id `veo_3_1_r2v_fast` with base key `veo_3_1_r2v_fast_landscape`.

**Resolution** and **Catalog `id`** ordering match **`veo_3_1_t2v`**. **Model name** / **Model** — same meaning as the t2v table above.

| Model name | Model | Studio preset | Aspect | Images | Resolution | Catalog `id` | Base `model_key` (phase 1) |
|------------|-------|---------------|--------|--------|------------|--------------|----------------------------|
| Veo 3.1 FAST | veo_3_1_r2v | fast | portrait, landscape | 0–3 | 720p | `veo_3_1_r2v_fast_portrait`, `veo_3_1_r2v_fast` | `veo_3_1_r2v_fast_portrait`, `veo_3_1_r2v_fast_landscape` |
| Veo 3.1 FAST ULTRA | veo_3_1_r2v | fast_ultra | portrait, landscape | 0–3 | 720p, 4K, 1080p | `veo_3_1_r2v_fast_portrait_ultra`, `veo_3_1_r2v_fast_ultra`, `veo_3_1_r2v_fast_portrait_ultra_4k`, `veo_3_1_r2v_fast_ultra_4k`, `veo_3_1_r2v_fast_portrait_ultra_1080p`, `veo_3_1_r2v_fast_ultra_1080p` | `veo_3_1_r2v_fast_portrait_ultra`, `veo_3_1_r2v_fast_landscape_ultra` |
| Veo 3.1 Low Priority | veo_3_1_r2v | fast_ultra_relaxed | portrait, landscape | 0–3 | 720p | `veo_3_1_r2v_fast_portrait_ultra_relaxed`, `veo_3_1_r2v_fast_ultra_relaxed` | `veo_3_1_r2v_fast_portrait_ultra_relaxed`, `veo_3_1_r2v_fast_landscape_ultra_relaxed` |

---

## Aliases (image only)

Short **base** ids (e.g. `gemini-3.0-pro-image`) are **not** full `MODEL_CONFIG` keys: the server resolves them to a concrete id using [`src/core/model_resolver.py`](../src/core/model_resolver.py) plus `generationConfig.imageConfig` (`aspectRatio`, optional `imageSize` for 2k/4k). See [customer guide §2.1](./customer-guide-video-jobs.md#21-alias-and-generationconfig-image-only) and `GET /v1/models/aliases`.

---

## Informal nickname glossary (not official product names)

Some operators use colloquial names when talking to users. They map to **internal `model_name`** values only:

| Informal label | Internal `model_name` in `MODEL_CONFIG` |
|----------------|----------------------------------------|
| (community “nano banana” tier, if you use it) | `GEM_PIX` (Gemini 2.5 Flash image) |
| (“nano banana pro”, if you use it) | `GEM_PIX_2` (Gemini 3.0 Pro image) |
| (“nano banana 2”, if you use it) | `NARWHAL` (Gemini 3.1 Flash image) |

These nicknames are **not** returned by the API as `id`; always send the real model id (or alias + `generationConfig` as documented).
