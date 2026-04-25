# Agent Gateway (remote_browser bridge)

When `captcha_method` is `remote_browser`, Flow2API calls `remote_browser_base_url` with Bearer `remote_browser_api_key` (see [`src/services/flow_client.py`](../src/services/flow_client.py)). The **agent gateway** implements that HTTP API and forwards work to PCs over **WebSocket** (`/ws/agents`), so home users do not expose inbound HTTP.

## Docker

[`docker-compose.yml`](../docker-compose.yml) has **flow2api** only. [`docker-compose.agent.yml`](../docker-compose.agent.yml) adds **agent-gateway**, **redis**, and **`cloudflared`** (one tunnel; same `TUNNEL_TOKEN` as the rest of your hostnames). Merge both for a **public** deployment with captcha agent:

```bash
docker compose -f docker-compose.yml -f docker-compose.agent.yml up -d --build
# or: make pull-up-docker
```

`GATEWAY_*` in `.env` (see [`.env.agent-gateway.example`](../.env.agent-gateway.example)) and `TUNNEL_TOKEN` in [`.env`](../.env.example).

**App only, no public tunnel, no agent stack:** `docker compose up -d` (only `docker-compose.yml`).

**Build the main `flow2api` image from this repo** (fresh UI, not only `ghcr`):

```bash
docker build -t flow2api:local -f Dockerfile .
# Then override `flow2api` image to flow2api:local (e.g. small override compose or edit docker-compose.yml temporarily).
```

Services (merge `docker-compose.yml` + `docker-compose.agent.yml`):

- **flow2api** — in `docker-compose.yml`; `http://flow2api:8000`
- **cloudflared** — in `docker-compose.agent.yml` (waits for `flow2api` + `agent-gateway` after merge)
- **agent-gateway** — in `docker-compose.agent.yml`; port **9080**
- **redis** — in `docker-compose.agent.yml` (Phase 3)

## Flow2API configuration (admin / 打码)

| Field | Value (Docker) |
|--------|----------------|
| `captcha_method` | `remote_browser` |
| `remote_browser_base_url` | `http://agent-gateway:9080` |
| `remote_browser_api_key` | Same as **`GATEWAY_FLOW2API_BEARER`** |
| `remote_browser_timeout` | ≤ `SOLVE_TIMEOUT_SECONDS` (default 120) |

### Optional: browser mode fallback to gateway

You can keep `captcha_method=browser` (local headed Playwright pool) and enable fallback to gateway when local solve fails:

- `browser_fallback_to_remote_browser = true` (Captcha settings)
- Keep `remote_browser_base_url` and `remote_browser_api_key` configured

Behavior:

- Local headed solve succeeds → gateway is not used.
- Local headed solve fails → Flow2API calls gateway `/api/v1/solve`.
- If fallback is disabled, Flow2API keeps the existing browser-only failure behavior.

## Request and response payloads

Flow2API calls the gateway with **`Authorization: Bearer <remote_browser_api_key>`** (same value as `GATEWAY_FLOW2API_BEARER`). Agents connect over WebSocket and do not use that header.

For a dedicated PC-agent integration walkthrough, see: [agent-client-connection.md](./agent-client-connection.md).

### `GET /health` (no auth)

**Response (200):**

```json
{ "ok": true, "service": "flow2api-agent-gateway" }
```

### `POST /api/v1/solve`

**Request body (JSON):**

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `project_id` | string | Yes | Google Flow / VideoFX project id for this account. |
| `token_id` | integer | Yes | Flow2API database token id; the gateway routes the job to the agent that registered this id. |
| `action` | string | No | Default `"IMAGE_GENERATION"`. Also used: e.g. `"VIDEO_GENERATION"`. |

Example:

```json
{
  "project_id": "b7c9f2a1-0000-0000-0000-000000000000",
  "token_id": 42,
  "action": "IMAGE_GENERATION"
}
```

**Response (200, success):**

| Field | Type | Description |
|--------|------|-------------|
| `token` | string | reCAPTCHA / solve token for upstream requests. |
| `session_id` | string | Opaque id Flow2API treats as the remote browser session (passed back on finish/error). |
| `fingerprint` | object (optional) | Browser fingerprint map; Flow2API may apply it to subsequent API calls. |

```json
{
  "token": "<solve-token-string>",
  "session_id": ";1730000000000",
  "fingerprint": { }
}
```

**Error responses (non-200):** FastAPI style — JSON with a `detail` string, for example:

- **400** — missing `project_id` / `token_id`, or bad input.
- **503** — no agent has registered for that `token_id`.
- **504** — no `solve_result` / `solve_error` within `SOLVE_TIMEOUT_SECONDS`.
- **500 / 502** — agent error, incomplete result, or internal failure.

### `POST /api/v1/prefill`

Optional warm-up hint (body shape matches what Flow2API sends; the MVP gateway only logs and returns ok).

**Request body (JSON):**

```json
{
  "project_id": "<string>",
  "action": "IMAGE_GENERATION",
  "token_id": 42
}
```

`token_id` may be `null` or omitted depending on the caller.

**Response (200):**

```json
{ "ok": true }
```

### `POST /api/v1/sessions/{session_id}/finish`

Notifies the remote service that the captcha session completed successfully. `session_id` is URL-encoded when sent (from Flow2API: typically the `session_id` returned from `/api/v1/solve`).

**Request body (JSON, as sent by Flow2API):**

```json
{ "status": "success" }
```

**Response (200):**

```json
{ "ok": true }
```

### `POST /api/v1/sessions/{session_id}/error`

Reports an upstream error for that session.

**Request body (JSON, as sent by Flow2API):**

```json
{ "error_reason": "human-readable reason" }
```

**Response (200):**

```json
{ "ok": true }
```

### WebSocket `GET /ws/agents` (PC agents; not Bearer auth)

1. **Client → server (first text frame, JSON):** `register`

```json
{
  "type": "register",
  "device_token": "<GATEWAY_AGENT_DEVICE_TOKEN>",
  "token_ids": [1, 2, 3]
}
```

Or in Keygen mode:

```json
{
  "type": "register",
  "agent_token": "<keygen-token>",
  "token_ids": [1, 2, 3]
}
```

2. **Server → client (ack):**

```json
{
  "type": "registered",
  "token_ids": [2],
  "authorized_token_ids": [2],
  "subject": "machine-1",
  "auth_method": "keygen"
}
```

3. **Server → client (work):** `solve_job` (one per HTTP `/api/v1/solve` dispatched to this agent’s `token_id`).

```json
{
  "type": "solve_job",
  "job_id": "<uuid>",
  "project_id": "<string>",
  "action": "IMAGE_GENERATION",
  "token_id": 42
}
```

4. **Client → server (result):** reply with either:

**Success:**

```json
{
  "type": "solve_result",
  "job_id": "<same as solve_job>",
  "token": "<solve-token-string>",
  "session_id": "<string>",
  "fingerprint": { }
}
```

`fingerprint` is optional; omit or use `{}` if none.

**Failure:**

```json
{
  "type": "solve_error",
  "job_id": "<same as solve_job>",
  "error": "short reason"
}
```

5. **Server → client (protocol errors on later frames):** e.g. invalid JSON or unknown `type`:

```json
{ "type": "error", "detail": "..." }
```

Pydantic reference types: [`src/agent_gateway/schemas.py`](../src/agent_gateway/schemas.py).

## Environment variables (gateway)

| Variable | Purpose |
|----------|---------|
| `GATEWAY_FLOW2API_BEARER` | Must match Flow2API `remote_browser_api_key`. |
| `GATEWAY_AGENT_DEVICE_TOKEN` | Legacy shared secret (`legacy`/`dual` mode) for WebSocket register. |
| `GATEWAY_AGENT_AUTH_MODE` | `legacy` (default), `keygen`, or `dual`. |
| `KEYGEN_VERIFY_MODE` | `jwt` or `introspection`. |
| `KEYGEN_PUBLIC_KEY` | Keygen public key (jwt mode). |
| `KEYGEN_API_TOKEN` | Keygen API token (introspection mode). |
| `KEYGEN_API_URL` | Keygen base URL (default `https://api.keygen.sh`). |
| `KEYGEN_ISSUER` / `KEYGEN_AUDIENCE` | JWT claim checks for Keygen token verification. |
| `AGENT_TOKEN_OWNERSHIP_JSON` | Ownership map JSON, e.g. `{"machine-1":[1,2],"license-abc":[3]}`; server intersects this with claimed token_ids. |
| `SOLVE_TIMEOUT_SECONDS` | Max wait for a token (default 120). |
| `REDIS_URL` | Reserved for Phase 3 (optional). |
| `TUNNEL_TOKEN` | See root `.env` — for `cloudflared` in `docker-compose.agent.yml` (merge with `docker-compose.yml`). |

## Source layout

- [`src/agent_gateway/`](../src/agent_gateway/) — FastAPI, HTTP, WebSocket, in-memory registry.

## Cloudflare Tunnel (gateway on the public internet)

**One** `cloudflared` in `docker-compose.agent.yml` (after merge; do not run a second connector). In [Zero Trust](https://one.dash.cloudflare.com/) → **Tunnels** → **Public hostnames** (in addition to `flow-api` / `admin-flow` if used):

| Public hostname | Internal URL |
|-----------------|-------------|
| `https://agents.example.com` | `http://agent-gateway:9080` |

- **PC agents:** `wss://agents.example.com/ws/agents`
- **Flow2API (inside Docker)** should keep `remote_browser_base_url` = `http://agent-gateway:9080` (not the public URL).
- `FLOW2API_API_ONLY_HOST` is set for `flow2api` in the default `docker-compose.yml` (overridable via `.env`).

## Phase 2

A **Node.js** agent will use `wss://` and the protocol in [`src/agent_gateway/README.md`](../src/agent_gateway/README.md).
