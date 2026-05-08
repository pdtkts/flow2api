## Task tracker fetch API

Public HTTP endpoint that your client app can call to fetch Task Tracker assets (images/entries) for a given `search_id`.

> Admins configure Task Tracker in **Admin → Task Tracker Settings** (saved with generation config). The server calls `tastracker.com` over **direct HTTPS** using `curl-cffi` (no Playwright browser for this endpoint).
>
> Required: full **Cookie** header, **Device ID** (`x-device-id`), and **Device token** (`X-Device-Token` on `POST /api/auth/csr-token`, from DevTools). Optional: **Turnstile token** and **TLS impersonation profile** if your upstream session needs them.
>
> As a client user, you only need the base URL and a managed API key.

---

### `POST /api/tracker/fetch`

**Purpose:**  
Fetch Task Tracker assets for a given `search_id`, optionally across multiple pages and with a title filter.

This route uses the same **managed API key** mechanism as the cloning/metadata endpoints:
- Auth: managed API key required
- Use the key provided to you by the project owner (they create it in the Flow2API admin UI).

For full details on base URL and how to send the key, see **"How to call the server (API key and HTTP)"** in `docs/cloning-metadata-endpoints.md`:
- Base URL (e.g. `https://admin-flow…` or `https://flow-api…`)
- How to pass the API key (`Authorization: Bearer …`, `x-goog-api-key`, `?key=…`)

#### Authentication

- **Header (recommended):** `Authorization: Bearer <your_managed_api_key>`
- If the key is not a managed key, the server returns **403** with `{"detail":"Managed API key required"}`.

#### Request body

`Content-Type: application/json`

Body schema (`TaskTrackerFetchRequest`):

| Field         | Type        | Required | Description |
|--------------|-------------|----------|-------------|
| `search_id`  | `string`    | **Yes**  | Search identifier used by the Task Tracker UI (the same value you see in the URL / query when searching). |
| `order`      | `string`    | No       | Sort order; defaults to `"creation"`. Other values are passed through as-is to the upstream UI. |
| `pages`      | `number[]`  | No       | List of page numbers (1-based) to fetch. Duplicates are removed and values `< 1` are ignored. If omitted or empty, the server uses `[1]`. |
| `title_filter` | `string`  | No       | Case-insensitive substring filter on the asset title. If provided, only assets whose lowercased `title` contains this string are returned. |

Notes:
- If `pages` is empty or all entries are `< 1`, the server falls back to page 1.

#### Responses

- **200 OK** – JSON array of asset objects.
- **400 Bad Request** – request or Task Tracker session is invalid (for example, the upstream Task Tracker session needs to be refreshed by the admin).
- **403 Forbidden** – the API key is not a managed key or doesn’t have access.
- **500 Internal Server Error** – unexpected server-side error.

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

This matches what the server really returns today: an array of simplified asset objects with `imageUrl` and the other fields listed above.

#### Minimal `curl` example

```bash
BASE_URL="https://your-flow2api-host.example"
API_KEY="f2a_live_..." # managed API key from Admin → API key manager

curl -sS -X POST "${BASE_URL}/api/tracker/fetch" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "search_id": "my-search-id",
    "order": "creation",
    "pages": [1, 2],
    "title_filter": "keyword"
  }'
```

On success this returns a JSON array of assets. If you see a **400** with an auth-related message, refresh **cookies** and **device token** in Task Tracker settings (and optionally Turnstile token), then save and retry.

