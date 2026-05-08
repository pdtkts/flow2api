## POST `/api/market/analyze-keyword`

**Purpose:** Given an event name and the **`images` array** from `POST /api/tracker/keyword` (TAS merged JSON), call the configured LLM to produce a commercial brief (best sellers + shot list), **synthetic** 12‑month demand/saturation trends, an **`insights` echo** of the input slice (up to `max_items`), and an empty **`sources`** stub (reserved for future grounding).

**Auth:** Managed API key — `Authorization: Bearer <f2a_live_...>` (same as `/api/tracker/keyword` and `/api/generate-metadata`).

**Admin:** **Manage → Market** tab configures `flow2api_market_*` (provider order, models, retries). API keys are the same shared generation keys as metadata (Gemini, OpenAI, etc.).

### Request (`Content-Type: application/json`)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `eventName` | string | Yes | Display name for the theme (e.g. `valentine day`). |
| `rawData` | array | No | Objects from `trackerKeywordResponse.images` (TAS image rows). |
| `max_items` | number | No | Cap on `insights` echo and analysis slice; default **200**, max **1000**. |
| `backend` | string | No | Override provider: `gemini_native`, `openai`, `openrouter`, `third_party_gemini`, `cloudflare`. |
| `model` | string | No | Override primary model. |
| `fallbackModels` | string[] | No | Override model fallback chain. |

### Response (`200 OK`)

```json
{
  "brief": {
    "event": "valentine day",
    "bestSellers": ["..."],
    "shotList": [
      {
        "idea": "...",
        "type": "photo",
        "description": "...",
        "whyItWorks": "..."
      }
    ]
  },
  "trends": [
    { "month": "Jan", "demand": 78, "saturation": 42 }
  ],
  "insights": [],
  "sources": []
}
```

- **`trends`:** Always **12** entries, `Jan` … `Dec`, `demand` / `saturation` integers **0–100** (LLM-estimated, not real market data).
- **`insights`:** Echo of `rawData` truncated to `max_items` (objects only; non-objects dropped).
- **`sources`:** Currently always `[]`.

### Nexus flow (two steps)

1. `POST /api/tracker/keyword` with `q`, `order`, `content_type`, `pages`, etc. → read `images` from the JSON body.
2. `POST /api/market/analyze-keyword` with `eventName`, `rawData: <images>`, optional `max_items`.

### curl example

```bash
BASE_URL="https://your-flow2api-host"
API_KEY="f2a_live_..."

curl -sS -X POST "${BASE_URL}/api/market/analyze-keyword" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "eventName": "valentine day",
    "rawData": [],
    "max_items": 200
  }'
```

### API-only host

If `FLOW2API_API_ONLY_HOST` is set, `/api/market/*` is allowed (same pattern as `/api/tracker/*`).
