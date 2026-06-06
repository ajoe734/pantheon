# OPS-MGMT-AI-MULTIMODAL-REDEPLOY-20260606 Live Verify

Date: 2026-06-06 UTC  
Owner: Codex  
Reviewer: Claude  
Service: `openclaw-gateway-adapter`

## Scope

Redeploy the live `pantheon` Compose `openclaw-gateway-adapter` service and
verify the Codex CLI provider accepts Management AI multimodal image payloads
through the adapter's `codex exec -i` path.

## Redeploy

Command:

```bash
docker compose -p pantheon up -d --build openclaw-gateway-adapter
```

Result:

- Build completed from the task worktree.
- Container `pantheon-openclaw-gateway-adapter-1` was recreated and started.
- Post-redeploy container start time: `2026-06-06T15:26:04.968856927Z`.
- Post-redeploy image digest: `sha256:7774405343cdfe2afd23852f25561587387053b69b72e91310512345185c90c3`.
- Health status after redeploy: `healthy`.

## Post-Redeploy Health

Commands:

```bash
curl -fsS http://127.0.0.1:18104/healthz
curl -fsS http://127.0.0.1:18104/readyz
curl -fsS 'http://127.0.0.1:18104/api/openclaw-adapter/assistant/readiness/codex?auth_probe=true'
docker compose -p pantheon ps openclaw-gateway-adapter
```

Observed:

- `/healthz`: `status=ok`, `live=true`, `ready=true`.
- `/readyz`: `status=ok`, `live=true`, `ready=true`.
- Upstream OpenClaw gateway dependency reachable through `/readyz` with HTTP 200.
- Codex readiness: `ready=true`, `status=ready`, `auth_status=ready`.
- Codex CLI version: `codex-cli 0.136.0`.
- Credential mount: `mount_mode=rw`, `owner_check=matched`.
- Compose status: `Up ... (healthy)` on port `18104->8104`.

## Live Image Sight Probe

Probe payload:

- Generated a temporary 80x40 PNG under `/tmp`.
- Left half: red.
- Right half: blue.
- Sent as `data:image/png;base64,...` in both `messages[].content` and
  top-level `attachments`.
- Prompt: ask the model to identify left and right half colors.

Command shape:

```bash
curl -sS -w '\n%{http_code}\n' \
  -X POST http://127.0.0.1:18104/api/openclaw-adapter/assistant/providers/codex/invoke \
  -H 'Content-Type: application/json' \
  -H 'X-Operator-Id: codex-live-verify' \
  -H 'X-Trace-Id: ops-mgmt-ai-multimodal-redeploy-20260606-post-redeploy' \
  --data-binary @/tmp/pantheon-mm-live-request.json
```

Observed response:

- HTTP status: `200`.
- Provider: `codex_cli`.
- Runtime: `openclaw_gateway_cli_mount`.
- Sandbox: `read-only`.
- Workspace class: `read_only`.
- Return code: `0`.
- Duration: `6951ms`.
- Codex output text: `left red, right blue`.
- Stderr: `Reading prompt from stdin...`.

Adapter audit tail:

```json
{"event_type":"assistant.provider.started","image_count":1,"image_bytes":125,"message_id":"live-verify-image-sight-002","trace_id":"ops-mgmt-ai-multimodal-redeploy-20260606-post-redeploy","sandbox":"read-only","workspace_class":"read_only","timeout_seconds":60}
{"event_type":"assistant.provider.completed","duration_ms":6951,"returncode":0,"message_id":"live-verify-image-sight-002","trace_id":"ops-mgmt-ai-multimodal-redeploy-20260606-post-redeploy"}
```

The audit confirms the adapter materialized one image for the Codex CLI
invocation, and the live response confirms the post-redeploy provider could see
the left-red/right-blue image.

## Local Regression Verification

Commands:

```bash
PYTHONPATH=services/openclaw-gateway-adapter:services/control-plane/bff \
  python3 -m pytest services/openclaw-gateway-adapter/tests/test_assistant_codex_provider.py -q

PYTHONPATH=services/openclaw-gateway-adapter:services/control-plane/bff \
  python3 -m py_compile \
    services/openclaw-gateway-adapter/assistant_codex_provider.py \
    services/openclaw-gateway-adapter/assistant_provider_runtime.py \
    services/openclaw-gateway-adapter/main.py

git diff --check -- services/openclaw-gateway-adapter/assistant_codex_provider.py
```

Results:

- `15 passed in 1.11s`.
- `py_compile` passed.
- `git diff --check` passed.

## Safety Notes

- No broker, live, canary, production, or capital gates were enabled.
- Post-redeploy health showed `production_broker_enabled=false`,
  `paper_adapter_enabled=false`, `live_adapter_enabled=false`,
  `canary_adapter_enabled=false`, and `capital_binding_enabled=false`.
- Provider invocation ran in `read-only` sandbox.
- Audit redacted `session_id`.
