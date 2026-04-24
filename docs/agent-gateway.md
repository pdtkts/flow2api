# Agent Gateway (remote_browser bridge)

When `captcha_method` is `remote_browser`, Flow2API calls `remote_browser_base_url` with Bearer `remote_browser_api_key` (see [`src/services/flow_client.py`](../src/services/flow_client.py)). The **agent gateway** implements that HTTP API and forwards work to PCs over **WebSocket** (`/ws/agents`), so home users do not expose inbound HTTP.

## Docker (gateway + Redis)

From the repo root:

```bash
cp .env.agent-gateway.example .env.agent-gateway
# Edit secrets, then:
docker compose -f docker-compose.yml -f docker-compose.agent-gateway.yml --env-file .env.agent-gateway up -d --build
```

**Build the main app and agent-gateway from your git tree** (current frontend + `src/agent_gateway/`), not only the pre-pulled `ghcr.io/.../flow2api` image: merge [`docker-compose.local-build.yml`](../docker-compose.local-build.yml). It tags **`flow2api:local`** and **`flow2api-agent-gateway:local`**.

```bash
docker compose -f docker-compose.yml -f docker-compose.agent-gateway.yml -f docker-compose.local-build.yml up -d --build
```

With tunnel: add `-f docker-compose.tunnel.yml -f docker-compose.agent-gateway.tunnel.yml` to that command, or run `make pull-up-agent-tunnel-local` / `scripts/pull-up-agent-tunnel-local.ps1`.

Services:

- **agent-gateway** — port **9080** (host and container).
- **redis** — for future horizontal scale (Phase 3); the gateway MVP does not require Redis to function.

## Flow2API configuration

Set in the admin UI (Captcha / 打码) or database:

| Field | Value (Docker same network) |
|--------|-----------------------------|
| `captcha_method` | `remote_browser` |
| `remote_browser_base_url` | `http://agent-gateway:9080` |
| `remote_browser_api_key` | Same string as **`GATEWAY_FLOW2API_BEARER`** |
| `remote_browser_timeout` | ≤ gateway `SOLVE_TIMEOUT_SECONDS` (default 120) |

If Flow2API runs on the host and the gateway only in Docker, use `http://127.0.0.1:9080` instead.

## Environment variables (gateway container)

| Variable | Purpose |
|----------|---------|
| `GATEWAY_FLOW2API_BEARER` | Must match Flow2API `remote_browser_api_key`. |
| `GATEWAY_AGENT_DEVICE_TOKEN` | Secret agents send in the WebSocket `register` message. |
| `SOLVE_TIMEOUT_SECONDS` | Max wait for an agent to return a token (default 120). |
| `REDIS_URL` | Reserved for Phase 3 (optional). |

## Source layout

- [`src/agent_gateway/`](../src/agent_gateway/) — FastAPI app, HTTP routes, WebSocket handler, in-memory registry.

## Cloudflare Tunnel (agent gateway on the internet)

Use the **same** `TUNNEL_TOKEN` as the main Flow2API tunnel (one `cloudflared` container; do not run two tunnels with the same token).

```bash
docker compose -f docker-compose.yml -f docker-compose.agent-gateway.yml -f docker-compose.tunnel.yml -f docker-compose.agent-gateway.tunnel.yml up -d --build
```

Set `TUNNEL_TOKEN` in `.env` (see root `.env.example`). The merge file [`docker-compose.agent-gateway.tunnel.yml`](../docker-compose.agent-gateway.tunnel.yml) makes `cloudflared` start after both `flow2api` and `agent-gateway`.

In [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Networks** → **Tunnels** → your tunnel → **Public hostnames**, add a **new** hostname for the gateway (in addition to any `flow-api` / `admin-flow` routes you already have):

| Public hostname | Service | Internal URL |
|-----------------|---------|--------------|
| `https://agents.example.com` (your choice) | HTTP | `http://agent-gateway:9080` |

- **PC agents (Node):** connect with **`wss://agents.example.com/ws/agents`** (TLS at the edge; Cloudflare supports WebSockets to the origin).
- **Flow2API** (inside Docker) should keep using **`http://agent-gateway:9080`** for `remote_browser_base_url` — do not use the public URL there; the call stays on the bridge network.

If you use `FLOW2API_API_ONLY_HOST` on the API hostname, that only affects the main app; the new `agents.*` hostname is separate.

## Phase 2

A **Node.js** agent will connect to `wss://` (via tunnel) or `ws://` (local) and implement the protocol in [`src/agent_gateway/README.md`](../src/agent_gateway/README.md).
