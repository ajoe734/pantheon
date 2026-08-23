# PFG-MGMT-AI-RESPONSES-DEV-20260823 evidence

This task closes the Management AI dev gap where a healthy OpenClaw gateway
returned HTTP 404 for `POST /v1/responses`. The gateway now persists the
upstream opt-in before every Compose start, and the dev model-pool reconciliation
reasserts the same setting after a root deployment.

The adapter keeps a bounded actual-answer readiness probe. Its streaming route
now fails closed when an upstream Responses stream completes without assistant
text; a gateway health response or an empty terminal event is not a Management
AI answer.

`evidence.json` is the task-scoped review manifest for `Antigravity2`. It
contains owner evidence only, does not grant approval, and deliberately
excludes credentials, source-ingestion writes, frontend E2E changes, broker
activation, and capital authority.

## Hosted probe

After the task branch is deployed to the Pantheon dev VM, run:

```bash
OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
  bash scripts/openclaw-assistant-openclaw-live-smoke.sh
```

The probe fails unless the bounded readiness answer and the streamed
`/v1/responses` answer both contain the `OPENCLAW_LIVE` sentinel and the latter
reports `transport=responses_http`. Record only sanitized status, transport,
byte count, deployment SHA, and endpoint outcome in the manifest.
