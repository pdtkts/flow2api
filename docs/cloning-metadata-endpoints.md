# Metadata + Cloning Endpoints

All endpoints require a managed API key (same auth as `/v1/chat/completions`).

## `POST /api/generate-cloning-prompts`

Request:

- `images` (required): array
  - `id` (optional)
  - `title` (optional)
  - exactly one source:
    - `image_url` (recommended), or
    - `image_base64` (with `mimeType` recommended)
- optional routing:
  - `provider`
  - `model`
  - `fallbackModels`

Response:

- `{ "prompts": [...] }`

## `POST /api/generate-cloning-video-prompt`

Request:

- `imageClonePrompt` (required, JSON string)
- `cameraMotion` (required, string)
- `duration` (required, string)
- optional:
  - `negativePrompt`
  - `title`
  - `image_base64`
  - `mimeType`
- optional routing (advanced):
  - `provider`
  - `model`
  - `fallbackModels`

Response:

- `{ "prompt": "<json-string>" }`

## `POST /api/generate-metadata`

Request:

- exactly one image source:
  - `image_url`, or
  - `image_base64`
- `metadataSettings` (required):
  - `titleMin`, `titleMax`
  - `keywordMin`, `keywordMax`
  - `descriptionMin`, `descriptionMax`
  - `platforms`
  - `includeCategory`
  - `includeReleases`
  - `titleStyle`
  - `keywordTypes.singleWord`, `keywordTypes.doubleWord`, `keywordTypes.mixed`
  - `transparentBackground`
  - `customPrompt.enabled`, `customPrompt.text`
- optional:
  - `dnaNoBgWorkflowActive`
  - `backend` (`gemini_native | openai | third_party_gemini | cloudflare | csvgen`)
  - `model`
  - `fallbackModels`

Response:

- Normalized metadata:
  - `optionA.title`
  - `optionA.keywords`
  - `optionA.description`
  - `optionB.title`
  - `optionB.keywords`
  - `optionB.description`
  - optional `creditsRemaining`

## Environment Variables

Provider credentials are read from environment variables:

- `FLOW2API_GEMINI_API_KEYS` (comma-separated)
- `FLOW2API_OPENAI_API_KEYS` (comma-separated)
- `FLOW2API_THIRD_PARTY_GEMINI_API_KEYS` (comma-separated)
- `FLOW2API_THIRD_PARTY_GEMINI_BASE_URL`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `FLOW2API_CSVGEN_COOKIE` (only when backend=`csvgen`)
