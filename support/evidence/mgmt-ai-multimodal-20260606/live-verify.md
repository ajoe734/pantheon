# OPS-MGMT-AI-MULTIMODAL-REDEPLOY-20260606 Live Verify

Date: 2026-06-06 UTC  
Owner: Codex  
Reviewer: Claude  
Service: `openclaw-gateway-adapter`

## Scope

Redeploy the live `pantheon` Compose `openclaw-gateway-adapter` service and
verify the Codex CLI provider accepts Management AI multimodal image payloads
through the adapter's `codex exec -i` path.

## Preconditions

Command:

```bash
gh pr view 1101 --json number,state,mergedAt,mergeCommit,headRefOid,url,title
```

Observed:

- PR #1101 (`MGMT-AI-ATTACH-MULTIMODAL: forward image attachments to codex exec -i`) was merged at `2026-06-06T15:14:11Z`.
- Merge commit: `06b139c70b9eb1b64a21a838f5534ac553f60b9f`.
- Head commit: `c71bfb90a16d6cca2c3b12d2b185c716e68a6a31`.

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

## Codex Mounted Runtime

Commands:

```bash
docker exec pantheon-openclaw-gateway-adapter-1 sh -lc \
  'find /home/pantheon-assistant/.codex -maxdepth 2 -type f -printf "%p\n" | sort'

docker exec pantheon-openclaw-gateway-adapter-1 sh -lc \
  'if [ -f /home/pantheon-assistant/.codex/config.toml ]; then grep -E "^(model|model_provider|approval_policy|sandbox_mode|model_reasoning_effort)" /home/pantheon-assistant/.codex/config.toml; else echo missing_config; fi'
```

Observed:

- Mounted Codex auth state exists, including `/home/pantheon-assistant/.codex/auth.json`.
- Mounted `/home/pantheon-assistant/.codex/config.toml` was absent (`missing_config`), so no configured model line was available to quote from the mounted config.
- Codex readiness still returned `ready=true`, `auth_status=ready`, `version=codex-cli 0.136.0`, `mount_mode=rw`.
- The live image-sight probes below are the positive proof that the active Codex account/model accepted image input through `codex exec -i`.

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

## Management AI Upload Path

Command shape:

```bash
curl -sS -w '\n%{http_code}\n' \
  -X POST http://127.0.0.1:18001/bff/management/nl/ask \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer asst-bff-002:operator' \
  -H 'X-Tenant-Id: pantheon-dev' \
  -H 'Idempotency-Key: ops-mgmt-ai-multimodal-bff-upload-002' \
  --data-binary @/tmp/pantheon-mm-bff-live-request.json
```

Observed response:

- HTTP status: `202`.
- Answer: `left red, right blue`.
- Trace id: `ops-mgmt-ai-multimodal-redeploy-20260606-bff-upload`.
- `providerStatus.provider=codex_cli`.
- `providerStatus.status=completed`.
- `providerStatus.used=true`.
- `providerStatus.multimodal.forwarded=true`.
- `providerStatus.multimodal.attachment_count=1`.
- Attachment source: `management_ai_attachment_store`.

Adapter audit tail for the BFF path:

```json
{"event_type":"assistant.provider.started","route":"POST /bff/management/nl/ask","image_count":1,"image_bytes":125,"operator_id":"asst-bff-002","tenant_id":"pantheon-dev","trace_id":"ops-mgmt-ai-multimodal-redeploy-20260606-bff-upload"}
{"event_type":"assistant.provider.completed","route":"POST /bff/management/nl/ask","duration_ms":9312,"returncode":0,"trace_id":"ops-mgmt-ai-multimodal-redeploy-20260606-bff-upload"}
```

## Malformed Image Graceful Degradation

Command shape:

```bash
curl -sS -w '\n%{http_code}\n' \
  -X POST http://127.0.0.1:18104/api/openclaw-adapter/assistant/providers/codex/invoke \
  -H 'Content-Type: application/json' \
  -H 'X-Operator-Id: codex-live-verify' \
  -H 'X-Trace-Id: ops-mgmt-ai-multimodal-redeploy-20260606-malformed' \
  --data-binary @/tmp/pantheon-mm-malformed-request.json
```

Observed response:

- HTTP status: `200`.
- Provider: `codex_cli`.
- Return code: `0`.
- Duration: `7365ms`.
- Codex output text: `malformed-ok`.

Adapter audit tail:

```json
{"event_type":"assistant.provider.started","image_count":0,"image_bytes":0,"message_id":"live-verify-malformed-image-001","trace_id":"ops-mgmt-ai-multimodal-redeploy-20260606-malformed"}
{"event_type":"assistant.provider.completed","duration_ms":7365,"returncode":0,"message_id":"live-verify-malformed-image-001","trace_id":"ops-mgmt-ai-multimodal-redeploy-20260606-malformed"}
```

The malformed image attachment was ignored for image materialization, the
provider stayed text-only, and the request returned normally with no 5xx.

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
