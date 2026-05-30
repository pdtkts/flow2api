# Runway Integration

Flow2API can route `runway-*` models to Runway web tasks while keeping Flow/Veo models on the existing Flow pipeline.

## Admin Setup

1. Open the admin panel and go to `Runway`.
2. Enable Runway.
3. Add a Runway account by pasting the working Runway JWT or cookie string.
4. Confirm the decoded workspace/team values appear.
5. Use the health button to validate that Flow2API can authenticate to Runway.
6. Keep model ids prefixed with `runway-`.

The integration follows the same web-task flow as `tests/runway_task.py`:

- `POST https://api.runwayml.com/v1/tasks`
- `GET https://api.runwayml.com/v1/tasks/{task_id}`
- `Authorization: Bearer <jwt>`
- `Origin: https://app.runwayml.com`
- `Referer: https://app.runwayml.com/`
- `x-runway-workspace: <workspace_id>`

Rotate any Runway JWT that was previously committed or shared.

## Native API

List models:

```bash
curl http://localhost:8000/v1/runway/models \
  -H "Authorization: Bearer $FLOW2API_MANAGED_KEY"
```

Create a task:

```bash
curl -X POST http://localhost:8000/v1/runway/tasks \
  -H "Authorization: Bearer $FLOW2API_MANAGED_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "runway-gemini-3-1-flash-image",
    "prompt": "A mossy log on a clean white background",
    "aspect_ratio": "21:9",
    "image_size": "4K",
    "num_outputs": 1
  }'
```

Poll a task:

```bash
curl http://localhost:8000/v1/runway/tasks/runway-20260530-120000-abcd1234 \
  -H "Authorization: Bearer $FLOW2API_MANAGED_KEY"
```

Upload media:

```bash
curl -X POST http://localhost:8000/v1/runway/uploads \
  -H "Authorization: Bearer $FLOW2API_MANAGED_KEY" \
  -F "file=@reference.png"
```

The upload response includes a cache URL and a `data_url`. Use either value in `media`.

## OpenAI-Compatible API

Runway models are exposed in `/v1/models` when Runway is enabled. They require a managed API key with the `runway:generate` scope.

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $FLOW2API_MANAGED_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "runway-gemini-3-1-flash-image",
    "messages": [
      {"role": "user", "content": "A cinematic macro photo of moss on bark"}
    ],
    "stream": true
  }'
```

Async OpenAI-compatible calls are also supported:

```bash
curl -X POST http://localhost:8000/v1/async/chat/completions \
  -H "Authorization: Bearer $FLOW2API_MANAGED_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "runway-gemini-3-1-flash-image",
    "messages": [{"role": "user", "content": "A product photo of a wooden log slice"}]
  }'
```

Poll with `/v1/jobs/{job_id}`.

## Outputs

Runway task responses include:

- `raw_artifact_urls`: upstream Runway artifact links.
- `cached_artifact_urls`: Flow2API `/api/cache/blob/...` links when cache output is enabled.
- `result_urls`: cached URLs first, raw URLs as fallback.

