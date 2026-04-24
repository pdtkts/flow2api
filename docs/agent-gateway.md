# Agent Gateway (remote_browser bridge)

When `captcha_method` is `remote_browser`, Flow2API calls `remote_browser_base_url` with Bearer `remote_browser_api_key` (see [`src/services/flow_client.py`](../src/services/flow_client.py)). The **agent gateway** implements that HTTP API and forwards work to PCs over **WebSocket** (`/ws/agents`), so home users do not expose inbound HTTP.

## Docker

**Core** is [`docker-compose.yml`](../docker-compose.yml) (Flow2API + `cloudflared`). **Agent stack** is [`docker-compose.agent.yml`](../docker-compose.agent.yml) (agent-gateway + redis). Merge both for the usual deployment:

```bash
docker compose -f docker-compose.yml -f docker-compose.agent.yml up -d --build
# or: make pull-up-docker
```

`GATEWAY_*` in `.env` (see [`.env.agent-gateway.example`](../.env.agent-gateway.example)) and `TUNNEL_TOKEN` in [`.env`](../.env.example).

**App + tunnel only** (no agent): `docker compose up -d` (single file).

**Build the main `flow2api` image from this repo** (fresh UI, not only `ghcr`):

```bash
docker build -t flow2api:local -f Dockerfile .
# Then override `flow2api` image to flow2api:local (e.g. small override compose or edit docker-compose.yml temporarily).
```

Services (merge `docker-compose.yml` + `docker-compose.agent.yml`):

- **flow2api** — main app; internal `http://flow2api:8000`
- **cloudflared** — in `docker-compose.yml`
- **agent-gateway** — in `docker-compose.agent.yml`; port **9080**
- **redis** — in `docker-compose.agent.yml` (Phase 3)

## Flow2API configuration (admin / 打码)

| Field | Value (Docker) |
|--------|----------------|
| `captcha_method` | `remote_browser` |
| `remote_browser_base_url` | `http://agent-gateway:9080` |
| `remote_browser_api_key` | Same as **`GATEWAY_FLOW2API_BEARER`** |
| `remote_browser_timeout` | ≤ `SOLVE_TIMEOUT_SECONDS` (default 120) |

## Environment variables (gateway)

| Variable | Purpose |
|----------|---------|
| `GATEWAY_FLOW2API_BEARER` | Must match Flow2API `remote_browser_api_key`. |
| `GATEWAY_AGENT_DEVICE_TOKEN` | Secret agents send in the WebSocket `register` message. |
| `SOLVE_TIMEOUT_SECONDS` | Max wait for a token (default 120). |
| `REDIS_URL` | Reserved for Phase 3 (optional). |
| `TUNNEL_TOKEN` | See root `.env` — for `cloudflared` in the same compose file. |

## Source layout

- [`src/agent_gateway/`](../src/agent_gateway/) — FastAPI, HTTP, WebSocket, in-memory registry.

## Cloudflare Tunnel (gateway on the public internet)

**One** `cloudflared` in `docker-compose.yml`. In [Zero Trust](https://one.dash.cloudflare.com/) → **Tunnels** → **Public hostnames** (in addition to `flow-api` / `admin-flow` if used):

| Public hostname | Internal URL |
|-----------------|-------------|
| `https://agents.example.com` | `http://agent-gateway:9080` |

- **PC agents:** `wss://agents.example.com/ws/agents`
- **Flow2API (inside Docker)** should keep `remote_browser_base_url` = `http://agent-gateway:9080` (not the public URL).
- `FLOW2API_API_ONLY_HOST` is set for `flow2api` in the default `docker-compose.yml` (overridable via `.env`).

## Phase 2

A **Node.js** agent will use `wss://` and the protocol in [`src/agent_gateway/README.md`](../src/agent_gateway/README.md).
