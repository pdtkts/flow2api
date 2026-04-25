# Agent Client -> Gateway Connection Guide

This guide explains how a PC agent connects to the gateway WebSocket and exchanges captcha jobs/results in production.

## 1) Endpoint and transport

- WebSocket endpoint: `wss://<agents-host>/ws/agents`
- Example: `wss://agents.prismacreative.online/ws/agents`
- Public Cloudflare hostname should route to `http://agent-gateway:9080`

## 2) Auth modes (server-side)

Gateway supports `GATEWAY_AGENT_AUTH_MODE`:

- `legacy`: requires `device_token`
- `keygen`: requires `agent_token`
- `dual`: accepts either (migration mode)

Configure in gateway env:

- `GATEWAY_AGENT_AUTH_MODE=legacy|keygen|dual`
- Legacy: `GATEWAY_AGENT_DEVICE_TOKEN`
- Keygen: `KEYGEN_VERIFY_MODE=jwt|introspection` and related `KEYGEN_*`

## 3) First frame: register

The first message after WebSocket connect must be JSON with `type: "register"`.

### Legacy register

```json
{
  "type": "register",
  "device_token": "<GATEWAY_AGENT_DEVICE_TOKEN>",
  "token_ids": [1, 2, 3]
}
```

### Keygen register

```json
{
  "type": "register",
  "agent_token": "<keygen-token>",
  "token_ids": [1, 2, 3]
}
```

Notes:

- `token_ids` is a **hint** from client.
- Server intersects this with policy from `AGENT_TOKEN_OWNERSHIP_JSON`.
- If result is empty, connection is rejected with close reason `no authorized token_ids for this agent`.

## 4) Registration response

On success gateway sends:

```json
{
  "type": "registered",
  "token_ids": [2],
  "authorized_token_ids": [2],
  "subject": "machine-1",
  "auth_method": "keygen"
}
```

`authorized_token_ids` is the final server-accepted set for dispatch.

## 5) Solve job flow

### Server -> client: `solve_job`

```json
{
  "type": "solve_job",
  "job_id": "uuid",
  "project_id": "flow-project-id",
  "action": "IMAGE_GENERATION",
  "token_id": 2
}
```

### Client -> server: success

```json
{
  "type": "solve_result",
  "job_id": "same-job-id",
  "token": "<real-recaptcha-token>",
  "session_id": "<opaque-session-id>",
  "fingerprint": {
    "user_agent": "Mozilla/5.0 ..."
  }
}
```

### Client -> server: failure

```json
{
  "type": "solve_error",
  "job_id": "same-job-id",
  "error": "human-readable reason"
}
```

## 6) Connection failures and reasons

Common close reasons from gateway:

- `GATEWAY_AGENT_DEVICE_TOKEN is not set`
- `first message must be register`
- `expected JSON`
- `legacy mode requires device_token`
- `agent_token required`
- `invalid device token`
- `agent auth failed: ...`
- `token_ids must be a list of integers`
- `no authorized token_ids for this agent`

## 7) Ownership policy

`AGENT_TOKEN_OWNERSHIP_JSON` format:

```json
{
  "machine-1": [1, 2],
  "license-abc": [3]
}
```

Lookup behavior:

- Gateway checks identity keys in order: `subject`, `machine_id`, `license_id`
- Union of those entries = allowed set
- Final authorized set = `claimed token_ids ∩ allowed set`
- If ownership JSON is empty, gateway falls back to legacy trust of claimed IDs

## 8) Minimal client checklist

- Connect to `wss://<host>/ws/agents`
- Send `register` as first frame
- Wait for `registered`
- Keep socket alive and read messages continuously
- On each `solve_job`, respond with `solve_result` or `solve_error`
- Reconnect with backoff on close/error

## 9) Quick test

Health:

```bash
curl -sS https://<agents-host>/health
```

Manual solve trigger (HTTP side; bearer is `GATEWAY_FLOW2API_BEARER`):

```bash
curl -sS -X POST "https://<agents-host>/api/v1/solve" \
  -H "Authorization: Bearer <GATEWAY_FLOW2API_BEARER>" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"test","token_id":2,"action":"IMAGE_GENERATION"}'
```
