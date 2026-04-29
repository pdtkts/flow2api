# Async Job Processing and Polling

This document explains how to use Flow2API's asynchronous generation flow for both image and video models.

## Why Async Polling

Use async polling when generation may take longer than a typical HTTP timeout (especially video and upscale workflows).

Instead of holding one request open:

1. Submit a job and get a `job_id` immediately.
2. Poll job status by `job_id` until it is terminal.

## Endpoints

- `POST /v1/async/chat/completions` - submit async job
- `GET /v1/jobs/{job_id}` - poll job status

Both endpoints require a managed API key (`Authorization: Bearer <key>`).

## Submit Job

### Request

`POST /v1/async/chat/completions` accepts the same body shape as `ChatCompletionRequest` used by `POST /v1/chat/completions`.

Minimal example:

```json
{
  "model": "gemini-3.1-flash-image-landscape-4k",
  "messages": [
    {
      "role": "user",
      "content": "A cinematic mountain landscape at sunrise"
    }
  ]
}
```

### Success Response (`202 Accepted`)

```json
{
  "job_id": "gen-20260429-181455-a1b2c3d4",
  "status": "processing",
  "project_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

Notes:

- `job_id` format is `gen-YYYYMMDD-HHMMSS-<8hex>`.
- `project_id` is selected automatically by the server for the request.

## Poll Job

### Request

`GET /v1/jobs/{job_id}`

### Success Response (`200`)

```json
{
  "job_id": "gen-20260429-181455-a1b2c3d4",
  "status": "completed",
  "progress": 100,
  "model": "gemini-3.1-flash-image-landscape-4k",
  "project_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "result_urls": ["https://..."],
  "base_result_urls": ["https://..."],
  "delivery_urls": ["https://..."],
  "requested_resolution": "4k",
  "output_resolution": "4k",
  "upscale_status": "completed",
  "upscale_error_message": null,
  "error_message": null,
  "created_at": "2026-04-29T13:14:55.000000",
  "completed_at": "2026-04-29T13:15:12.000000"
}
```

### Error Responses

- `404` if job does not exist:

```json
{ "detail": "Job not found" }
```

- `403` if the polling key does not own this job:

```json
{ "detail": "Not authorized to view this job" }
```

## Status Model

Job status transitions:

- `processing` -> `completed`
- `processing` -> `failed`

Terminal states:

- `completed`: generation finished; URLs and metadata may be present.
- `failed`: generation failed; check `error_message`.

## Response Field Semantics

- `result_urls`: final pipeline URLs (often upscaled or final output).
- `base_result_urls`: base/non-upscaled URLs when available.
- `delivery_urls`: preferred URLs clients should render first.
- `requested_resolution`: target resolution inferred from model config (for example `4k`, `2k`, `1080p`).
- `output_resolution`: delivered output resolution.
- `upscale_status`: `not_requested`, `processing`, `completed`, or `failed`.
- `upscale_error_message`: upscale-specific error details when applicable.
- `project_id`: project selected for the job.

## Image and Video Behavior

The async API contract is the same for image and video jobs. The `model` determines the backend generation path.

- Image models can include richer upscale metadata and base/upscaled URL splits.
- Video models also use the same async submit/poll contract, but base vs upscaled URL granularity depends on upstream payload detail.

## Upscale Fallback Rules

Client rendering rule:

- Always prefer `delivery_urls`.

Interpretation:

- Upscale success: `upscale_status="completed"`; `delivery_urls` generally matches final/upscaled output.
- Upscale failed but generation succeeded: `status` may still be `completed`; `delivery_urls` should point to available base output; `upscale_status="failed"` and `upscale_error_message` explains why.

## Polling Strategy Recommendation

Use a bounded polling loop:

1. Poll every 3-5 seconds for normal workloads.
2. Increase interval for long video jobs (for example 8-15 seconds).
3. Stop when status is `completed` or `failed`.
4. Apply an overall timeout in client logic.

## End-to-End cURL Example

Submit:

```bash
curl -X POST "http://localhost:8000/v1/async/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image-landscape-4k",
    "messages": [
      {
        "role": "user",
        "content": "A cinematic mountain landscape at sunrise"
      }
    ]
  }'
```

Poll:

```bash
curl -X GET "http://localhost:8000/v1/jobs/gen-20260429-181455-a1b2c3d4" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Client Checklist

- Treat async submit as accepted work, not completed work.
- Persist `job_id` and `project_id` from submit response.
- Poll only with the same API key owner.
- Stop polling only on terminal status.
- Use `delivery_urls` as primary display source.
- Show `error_message` (and `upscale_error_message` when present) on failure paths.
