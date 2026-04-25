# Agent Gateway (Phase 1)

FastAPI service that implements the same **HTTP** contract as Flow2API’s `remote_browser` client (`/api/v1/solve`, etc.) and accepts **WebSocket** connections from user machines at `/ws/agents`.

## Data model (MVP)

- **In-process only:** `token_id` → one active agent connection (last registration wins).
- **Redis** is in `docker-compose.agent.yml` (merge with `docker-compose.yml`); the gateway MVP does not require Redis logic yet.
- Optional Pydantic shapes: [`schemas.py`](schemas.py).

## Run (local)

```bash
export GATEWAY_FLOW2API_BEARER=your-secret   # must match Flow2API remote_browser_api_key
export GATEWAY_AGENT_AUTH_MODE=legacy         # legacy | keygen | dual
export GATEWAY_AGENT_DEVICE_TOKEN=agent-secret
python -m src.agent_gateway
```

Health: `GET http://127.0.0.1:9080/health`

## Docker

See [../../docs/agent-gateway.md](../../docs/agent-gateway.md).

## WebSocket protocol

1. Connect to `ws://<host>:9080/ws/agents`.
2. Send one JSON line (`register`) in one of these modes:
   - Legacy: `{"type":"register","device_token":"<GATEWAY_AGENT_DEVICE_TOKEN>","token_ids":[1]}`
   - Keygen: `{"type":"register","agent_token":"<keygen-token>","token_ids":[1]}`
   `token_ids` are a client hint; server intersects with ownership policy from `AGENT_TOKEN_OWNERSHIP_JSON` when configured.
3. Receive `solve_job` messages; reply with `solve_result` or `solve_error`:

```json
{"type":"solve_result","job_id":"...","token":"...","session_id":"...","fingerprint":{}}
```

```json
{"type":"solve_error","job_id":"...","error":"reason"}
```

## Keygen mode (production)

Set `GATEWAY_AGENT_AUTH_MODE=keygen` (or `dual` during migration) and configure:

- `KEYGEN_VERIFY_MODE=jwt|introspection`
- `KEYGEN_PUBLIC_KEY` (jwt mode) or `KEYGEN_API_TOKEN` + `KEYGEN_API_URL` (introspection mode)
- `AGENT_TOKEN_OWNERSHIP_JSON` map, e.g. `{"machine-1":[1,2],"license-abc":[3]}`

In Keygen mode, each agent sends `agent_token` and receives only `authorized_token_ids` in `registered`.
