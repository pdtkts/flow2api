# Cloning & metadata POST APIs

Reference for the three public endpoints used by clients and smoke tests. Paths are relative to your Flow2API base URL (e.g. `https://your-host`).

## How to call the server (API key and HTTP)

### 1. Base URL

Use your deployed Flow2API origin with **HTTPS** in production, for example:

`https://your-flow2api-host.example`

Endpoints below are path suffixes: `{BASE}/api/generate-cloning-prompts`, etc.

### 2. Which credential to use

These three routes use **`verify_api_key_flexible`**: they expect a **managed API key** (created in the admin UI under **API key manager**), **not** the admin username/password and **not** the admin session JWT used for `/manage` pages.

| Credential | Use on these endpoints? |
|------------|-------------------------|
| **Managed API key** (e.g. `f2a_live_…`) | **Yes** — this is what you put in `Authorization` / alternatives below. |
| Admin login session (`Bearer` admin token from `/api/login`) | **No** — that is for admin APIs such as `/api/config/generation`. |
| Legacy single global API key (if configured) | Works only when managed keys are disabled; these cloning/metadata routes still **require a managed key** (`key_id` set), so you get **403** if the server resolves you as “legacy” without a managed key. |

Create or copy the key from **Admin → API key manager**. Assign token accounts / scopes there if your deployment enforces them.

### 3. How to send the API key

The server accepts the key in **one** of these ways (first match wins in practice: Authorization, then `x-goog-api-key`, then query):

| Method | Example |
|--------|---------|
| **Authorization header (recommended)** | `Authorization: Bearer <your_managed_api_key>` |
| **Google-style header** | `x-goog-api-key: <your_managed_api_key>` |
| **Query string** | `POST .../api/generate-metadata?key=<your_managed_api_key>` |

Always use **HTTPS** so the key is not sent in clear text over the network (avoid query keys in shared logs if possible; prefer `Authorization`).

### 4. Request shape for POST bodies

- **`Content-Type: application/json`**
- **Body:** JSON object as documented under each endpoint below (`POST` with a JSON body).

### 5. Minimal `curl` example

```bash
curl -sS -X POST "${BASE_URL}/api/generate-metadata" \
  -H "Authorization: Bearer ${MANAGED_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://example.com/sample.jpg","metadataSettings":{}}'
```

### 6. Minimal JavaScript (`fetch`) example

```javascript
const baseUrl = "https://your-flow2api-host.example";
const managedApiKey = "f2a_live_..."; // from Admin → API key manager

const res = await fetch(`${baseUrl}/api/generate-cloning-prompts`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${managedApiKey}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    images: [{ title: "Ref", id: "asset-123", image_url: "https://example.com/i.jpg" }],
  }),
});

const data = await res.json(); // 200: success payload; 4xx: usually { detail: "..." }
if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : res.statusText);
```

### 7. Typical HTTP results

| Status | Meaning |
|--------|---------|
| **200** | Success — body is JSON (`prompts`, `prompt`, or metadata fields per endpoint). |
| **401** | Missing/invalid API key. |
| **403** | Key valid but **not** a managed API key (`Managed API key required`), or scope/account restriction from `ApiKeyManager`. |
| **4xx/5xx** | Validation errors (body shape), upstream LLM/proxy errors (`detail` often explains). |

### 8. Authentication summary (quick reference)

| Requirement | Details |
|---------------|---------|
| **Preferred header** | `Authorization: Bearer <managed_api_key>` |
| **Key source** | Admin → **API key manager** (same family of keys as `/v1/chat/completions`). |
| **403 on these routes** | Valid key but not treated as a managed key — use a key issued from API key manager. |

---

## `POST /api/generate-cloning-prompts`

Runs vision LLM cloning over one or more images and returns structured prompt objects per image.

### Top-level body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `images` | `array` | **Yes** | Non-empty list of image items (see below). At least one item is expected in practice. |
| `provider` | `string` | No | Override LLM provider for this request: `gemini_native`, `openai`, `third_party_gemini`, `cloudflare`. If omitted, server uses admin **Cloning** default (`flow2api_cloning_backend`). |
| `model` | `string` | No | Model id for the chosen provider. If omitted, server uses admin **Cloning** default (`flow2api_cloning_model`). |
| `fallbackModels` | `string[]` | No | Ordered fallback model ids if the primary fails. |

Extra unknown JSON keys are accepted (`extra="allow"`) but ignored unless you rely on future behavior.

### `images[]` item (`CloneImageItemRequest`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | No | Arbitrary asset id echoed into prompts/metadata in the pipeline. |
| `title` | `string` | No | Reference title for prompting context. |
| **Image source (exactly one)** | | **Yes** | See validation rule below. |
| `image_url` | `string` | Conditional | HTTPS/HTTP URL to an image the server will fetch. |
| `image_base64` | `string` | Conditional | Raw base64 or `data:image/...;base64,...` style payload. |
| `mimeType` | `string` | No | MIME type hint (e.g. `image/jpeg`). Recommended when using `image_base64`. |

**Validation:** Each item must include **exactly one** of `image_url` or `image_base64` (not both, not neither).

### Successful response

JSON object:

| Field | Type | Description |
|-------|------|-------------|
| `prompts` | `array` | One normalized prompt object per input image (shape aligned with the internal cloning template). |

### Example (minimal)

```json
{
  "images": [
    {
      "title": "sample",
      "image_url": "https://example.com/image.jpg"
    }
  ]
}
```

### Example (with routing overrides)

```json
{
  "images": [
    {
      "id": "asset-1",
      "title": "sample",
      "image_url": "https://example.com/image.jpg"
    }
  ],
  "provider": "cloudflare",
  "model": "@cf/meta/llama-3.1-8b-instruct",
  "fallbackModels": ["@cf/meta/llama-3-8b-instruct"]
}
```

---

## `POST /api/generate-cloning-video-prompt`

Turns an **image-clone JSON string** (still image prompt) plus video parameters into a **single JSON object** for video / I2V workflows, returned as a stringified JSON in `prompt`.

### Top-level body (declared fields)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `imageClonePrompt` | `string` | **Yes** | JSON **string** whose parsed value is a JSON object (the clone prompt from the still pipeline). Invalid JSON → `400`. |
| `cameraMotion` | `string` | **Yes** | Target camera motion description (also written into merged output). |
| `duration` | `string` | **Yes** | Target duration (e.g. seconds); merged into export metadata. |
| `negativePrompt` | `string` | No | Default `""`. Comma-separated terms merged into `visual_rules.prohibited_elements`. |
| `title` | `string` | No | Default `""`. Optional conceptual title for instructions. |
| `image_base64` | `string` | No | Optional reference frame; if set, must pair with `mimeType`. |
| `mimeType` | `string` | No | Required when `image_base64` is present (e.g. `image/jpeg`). |

**Validation:** `image_base64` and `mimeType` must **both** be provided or **both** omitted.

### Extra JSON keys (same request body)

These are **not** separate query parameters; send them alongside the fields above. Allowed because the model uses `extra="allow"`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | `string` | No | Same semantics as cloning prompts; defaults to admin **Cloning** backend. |
| `model` | `string` | No | Defaults to admin **Cloning** model. |
| `fallbackModels` | `string[]` | No | Fallback chain for the LLM call. |

### Successful response

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | `string` | JSON **string** (parse it client-side) of the merged video-oriented template. |

### Example (text-only LLM, no reference image)

```json
{
  "imageClonePrompt": "{\"scene\":\"...\",\"style\":\"...\"}",
  "cameraMotion": "slow_push_in",
  "duration": "6",
  "negativePrompt": "",
  "title": "smoke-test"
}
```

### Example (with optional vision frame + routing extras)

```json
{
  "imageClonePrompt": "{ ... valid JSON object as string ... }",
  "cameraMotion": "pan_left",
  "duration": "8",
  "negativePrompt": "blur, watermark",
  "title": "product",
  "image_base64": "<base64 or data URL>",
  "mimeType": "image/jpeg",
  "provider": "cloudflare",
  "model": "@cf/meta/llama-3.1-8b-instruct",
  "fallbackModels": ["@cf/meta/llama-3-8b-instruct"]
}
```

---

## `POST /api/generate-metadata`

Generates stock metadata (title, keywords, description, optional category) for **one** image using configurable rules.

### Top-level body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| **Image source (exactly one)** | | **Yes** | See validation rule below. |
| `image_url` | `string` | Conditional | URL the server will fetch. |
| `image_base64` | `string` | Conditional | Base64 image (raw or `data:...;base64,...`). |
| `metadataSettings` | `object` | **Yes** | Rules for lengths, platforms, etc. (see below). All nested fields have server-side defaults if omitted. |
| `dnaNoBgWorkflowActive` | `boolean` | No | Default `false`. Adjusts transparency / cutout wording when relevant. |
| `backend` | `string` | No | Override metadata backend: `gemini_native`, `openai`, `third_party_gemini`, `cloudflare`. If omitted, uses admin **Metadata** default (`flow2api_metadata_backend`). **Note:** `csvgen` is selected via admin config, not this enum in the request model. |
| `model` | `string` | No | Primary model for this call. If omitted, uses admin-configured primary/fallback chain. |
| `fallbackModels` | `string[]` | No | Explicit fallback list; overrides configured fallbacks when provided. |

**Validation:** Exactly one of `image_url` or `image_base64` must be present.

### `metadataSettings` (`MetadataSettingsRequest`)

| Field | Type | Required | Default (if omitted) | Description |
|-------|------|----------|------------------------|-------------|
| `titleMin` | `integer` | No | `50` | Minimum title length (characters). |
| `titleMax` | `integer` | No | `80` | Maximum title length. |
| `keywordMin` | `integer` | No | `32` | Minimum keyword count. |
| `keywordMax` | `integer` | No | `50` | Maximum keyword count. |
| `descriptionMin` | `integer` | No | `0` | Minimum description length. |
| `descriptionMax` | `integer` | No | `0` | Maximum description length (`0` can mean empty description only — see prompt rules). |
| `platforms` | `string[]` | No | `["adobe-stock"]` | Target marketplaces. |
| `includeCategory` | `boolean` | No | `false` | Whether to require `categoryId` in output. |
| `includeReleases` | `boolean` | No | `false` | Release / legal hints in the prompt. |
| `titleStyle` | `string` | No | `"seo-optimized"` | Style label for title instructions. |
| `keywordTypes` | `object` | No | see below | Controls single vs double vs mixed keywords. |
| `transparentBackground` | `boolean` | No | `false` | Cutout / transparency wording when `dnaNoBgWorkflowActive` is used. |
| `customPrompt` | `object` | No | see below | Optional extra client rules. |

#### `keywordTypes` (`KeywordTypesConfig`)

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `singleWord` | `boolean` | No | `false` |
| `doubleWord` | `boolean` | No | `false` |
| `mixed` | `boolean` | No | `true` |

#### `customPrompt` (`CustomPromptConfig`)

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `enabled` | `boolean` | No | `false` |
| `text` | `string` | No | `""` |

### Successful response

Normalized metadata object (structure includes `optionA` / `optionB` mirrors for UI parity). Typical fields:

| Field | Description |
|-------|-------------|
| `optionA.title`, `optionB.title` | Generated titles. |
| `optionA.keywords`, `optionB.keywords` | Keywords string or list (normalized by service). |
| `optionA.description`, `optionB.description` | Descriptions. |
| `optionA.category` / `optionB.category` | Present when category rules apply. |

Exact shape matches `generate_metadata` normalization in code.

### Example (minimal)

```json
{
  "image_url": "https://example.com/image.jpg",
  "metadataSettings": {}
}
```

### Example (fuller)

```json
{
  "image_url": "https://example.com/image.jpg",
  "metadataSettings": {
    "titleMin": 50,
    "titleMax": 80,
    "keywordMin": 32,
    "keywordMax": 50,
    "descriptionMin": 0,
    "descriptionMax": 200,
    "platforms": ["adobe-stock"],
    "includeCategory": false,
    "includeReleases": false,
    "titleStyle": "seo-optimized",
    "keywordTypes": {
      "singleWord": false,
      "doubleWord": false,
      "mixed": true
    },
    "transparentBackground": false,
    "customPrompt": {
      "enabled": false,
      "text": ""
    }
  },
  "dnaNoBgWorkflowActive": false,
  "backend": "cloudflare",
  "model": "@cf/meta/llama-3.1-8b-instruct",
  "fallbackModels": ["@cf/meta/llama-3-8b-instruct"]
}
```

---

## Admin configuration (credentials & defaults)

Routing and API keys are **not** part of these JSON bodies; they come from the database / admin UI:

| Area | Admin / config fields (conceptual) |
|------|--------------------------------------|
| **Metadata** | `flow2api_metadata_backend`, models, Gemini/OpenAI/third-party/Cloudflare **metadata** keys, prompts. |
| **Cloning** | `flow2api_cloning_backend`, `flow2api_cloning_model`, **cloning-specific** provider keys (optional override); if cloning Cloudflare account + token are both empty, main Cloudflare credentials are used. |

REST aliases: `GET`/`POST` **`/api/config/generation`** (same generation config as legacy **`/api/generation/timeout`**).

---

## References in repo

| Item | Location |
|------|----------|
| Request models | [`src/core/models.py`](../src/core/models.py) — `GenerateCloningPromptsRequest`, `CloneImageItemRequest`, `GenerateCloningVideoPromptRequest`, `GenerateMetadataRequest`, `MetadataSettingsRequest` |
| Routes | [`src/api/routes.py`](../src/api/routes.py) |
| Smoke script | [`scripts/test_flow2api_metadata_cloning.py`](../scripts/test_flow2api_metadata_cloning.py) |
