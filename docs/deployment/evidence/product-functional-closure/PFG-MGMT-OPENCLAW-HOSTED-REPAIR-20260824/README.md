# Hosted OpenClaw Responses provider repair evidence

This task keeps the existing Management AI path intact:

```text
POST /bff/management/nl/ask
  -> OpenClaw adapter invoke
  -> CLI for argv-safe prompts, or OpenClaw POST /v1/responses for a large body

POST /bff/management/nl/ask/stream
  -> OpenClaw adapter invoke/stream
  -> OpenClaw POST /v1/responses
```

The adapter uses OpenClaw's canonical provider model alias together with the
explicit `X-OpenClaw-Agent-Id` routing header. An oversized Management AI
context no longer becomes a deterministic BFF fallback because of the CLI argv
limit: the same selected agent receives that full prompt through the existing
body-based Responses transport. The adapter also preserves a valid terminal
`response.output_text.done` value when a stream has no incremental deltas, and
returns bounded typed failures for timeout versus unreachable transport
conditions. The deployed smoke script now sends the adapter service token on
each request when the token is configured.

No BFF route, browser-direct provider call, Compose/deployment configuration,
frontend code, source-writing capability, or broker/capital authority changes
are included.

## Validation

The task-scoped manifest records the focused local checks. After the reviewed
head merges to `dev` and the protected root deployment serves that exact
revision, the existing four-stage deployed smoke must pass:

```bash
PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN=<governed-token> \
OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \
  bash scripts/openclaw-assistant-openclaw-live-smoke.sh
```

That command proves a real bounded answer, one real CLI turn, and one non-empty
OpenResponses SSE answer without emitting credentials or raw response bodies.
Hosted evidence remains pending until that command runs against the deployed
exact revision; this artifact makes no earlier hosted-success claim.
