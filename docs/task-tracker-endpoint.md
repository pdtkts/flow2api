## TAS Tracker API

Public HTTP endpoints that your client app can call to query **tastracker.com** over **direct HTTPS** using `curl-cffi` (no Playwright for these routes).

> Admins configure TAS Tracker in **Admin → Task Tracker Settings** (saved with generation config).
>
> Required in admin settings: full **Cookie** header, **Device ID** (`x-device-id`), and **Device token** (`X-Device-Token` used on `POST /api/auth/csr-token`, copied from DevTools).
>
> Optional in admin settings: **Turnstile token** and **TLS impersonation profile**.
>
> As a client user, you only need the base URL and a managed API key.

**Migration:** `POST /api/tracker/fetch` was removed. Use **`POST /api/tracker/contributor`** for the same contributor behavior.

---

### `POST /api/tracker/contributor`

**Purpose:**  
Fetch TAS **contributor** search results for a given `search_id`, optionally across multiple pages, media types, AI filters, and title filter.

This route uses the same **managed API key** mechanism as cloning/metadata endpoints:
- Auth: managed API key required
- Use the key provided by the project owner (Admin UI → API key manager)

For base URL and key passing details, see `docs/cloning-metadata-endpoints.md`.

#### Authentication

- **Header (recommended):** `Authorization: Bearer <your_managed_api_key>`
- Non-managed keys return **403** with `{"detail":"Managed API key required"}`.

#### Request body

`Content-Type: application/json`

Body schema (`TaskTrackerContributorFetchRequest`; alias `TaskTrackerFetchRequest`):

| Field | Type | Required | Description |
|---|---|---|---|
| `search_id` | `string` | **Yes** | Search identifier used by TAS UI. |
| `order` | `string` | No | Sort order. Examples: `relevance`, `creation`, `downloads`, `featured`. Defaults to `creation`. Unknown values are passed through to upstream. |
| `content_type` | `string` | No | Media filter. Supported values: `all`, `photo`, `illustration`, `vector`, `video`, `template`, `3d`, `audio`. Defaults to `all`. |
| `generative_ai` | `string` | No | AI filter. API accepts `all`, `ai_only`, `exclude_ai`. Defaults to `all`. Internally mapped to TAS upstream values (`all` omitted, `ai_only` → `only`, `exclude_ai` → `exclude`). |
| `pages` | `number[]` | No | Page numbers (1-based). Duplicates removed; values `< 1` ignored. Empty/missing falls back to `[1]`. |
| `title_filter` | `string` | No | Case-insensitive substring filter on mapped title field. |

#### Responses

- **200 OK** – JSON array of mapped asset objects.
- **400 Bad Request** – invalid request or TAS auth/session problem (cookies/device token/turnstile mismatch or expiry).
- **403 Forbidden** – API key is not managed.
- **500 Internal Server Error** – unexpected server error.

#### Example success response

```json
[
  {
    "id": "1995833416",
    "title": "Warm Amber Tea Poured from Glass Teapot into Transparent Cup with Soft Bokeh Background",
    "downloads": 0,
    "keywords": "",
    "imageUrl": "https://t3.ftcdn.net/jpg/19/95/83/34/360_F_1995833416_cOQHwaD1li1VDncir10p9WvdxUk9M8br.jpg",
    "dimensions": "3677 x 2061",
    "mediaType": "Photo",
    "contentType": "image/jpeg",
    "category": "Drinks",
    "premium": "Standard",
    "updatedAt": "2026-04-23 13:21:09.803957",
    "isAI": true,
    "creator": "The Little Hut"
  }
]
```

Response is intentionally a simplified mapped shape (not the full raw upstream image object).

#### Minimal `curl` example (contributor)

```bash
BASE_URL="https://admin-flow.your-domain.example"
API_KEY="f2a_live_..." # managed API key

curl -sS -X POST "${BASE_URL}/api/tracker/contributor" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "search_id": "209617558",
    "order": "relevance",
    "content_type": "vector",
    "generative_ai": "exclude_ai",
    "pages": [1, 2],
    "title_filter": "elephant"
  }'
```

---

### `POST /api/tracker/keyword`

**Purpose:**  
Proxy TAS **keyword** search (`GET https://tastracker.com/api/search?...`) with browser-like `Referer` on `/search?...`. Returns **upstream JSON** (typically an object with an `images` array and fields such as `thumbnailUrl`, `creationDate`, etc.).

Same managed-key auth and admin TAS settings as contributor.

#### Request body

Body schema (`TaskTrackerKeywordSearchRequest`):

| Field | Type | Required | Description |
|---|---|---|---|
| `q` | `string` | **Yes** | Free-text query (e.g. `Valentine Day`). |
| `order` | `string` | No | Sort order; passed through. Defaults to `relevance`. Examples: `relevance`, `creation`, `downloads`, `featured`. |
| `content_type` | `string` | No | Single value (`vector`) or **comma-separated** list (`vector,photo`). Each token must be one of: `all`, `photo`, `illustration`, `vector`, `video`, `template`, `3d`, `audio`. Invalid tokens are dropped; empty after validation → `all`. |
| `generative_ai` | `string` | No | Same mapping as contributor (`all` / `ai_only` / `exclude_ai`). |
| `pages` | `number[]` | No | Same pagination convention as contributor (`page` query only when page is greater than 1). |

#### Responses

- **200 OK** – JSON object from TAS (merged across requested pages: `images` concatenated; other top-level keys taken from the first page).
- **400** / **403** / **500** – same semantics as contributor.

#### Minimal `curl` example (keyword)

```bash
curl -sS -X POST "${BASE_URL}/api/tracker/keyword" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "q": "Valentine Day",
    "order": "featured",
    "content_type": "vector,photo",
    "generative_ai": "all",
    "pages": [1, 2]
  }'
```

If you get a **400** auth/session error on either route, refresh TAS Tracker settings in admin:
- cookies
- device token
- optional turnstile token (if your account/session currently needs it)
