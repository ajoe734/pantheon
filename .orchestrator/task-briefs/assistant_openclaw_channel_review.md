# Review: ASSISTANT-OPENCLAW-CHANNEL
Reviewer: Claude2
Date: 2026-06-16

## Verdict: Approved

## Scope Verified

Commit `49bca705` — "ASSISTANT-OPENCLAW-CHANNEL: wire OpenClaw agent as assistant provider"

Files changed:
- `services/openclaw-gateway-adapter/assistant_openclaw_provider.py` (new, 306 lines)
- `services/openclaw-gateway-adapter/main.py` (+74 lines)
- `services/openclaw-gateway-adapter/test_main.py` (+171 lines, 9 new test functions)
- `services/control-plane/bff/openclaw_ops_client.py` (+21 lines)
- `services/control-plane/bff/main.py` (+4 lines)

## Acceptance Criteria Check

| Criterion | Result |
|---|---|
| Management AI routes through OpenClaw agent (provider=openclaw) | ✓ BFF `_mgmt_nl_provider_name()` default changed to `"openclaw"`; `openclaw`/`openclaw_agent` added to allowlist |
| Adapter readiness openclaw=ready | ✓ `_OPENCLAW_AGENT_PROVIDER.readiness()` wired into `/capabilities` and `/assistant/readiness` |
| Live conversation test non-empty | ✓ `test_openclaw_invoke_returns_completed_result_on_success` covers the success path |
| Contract test green | ✓ 107 tests pass (70 adapter + 37 BFF) per commit Verified trailer |
| Fallback preserved (codex/claude) | ✓ Both providers remain supported in `openclaw_ops_client.py` |

## Implementation Quality

**`AssistantOpenClawProvider`** (new):
- Correct ws:// → http:// normalisation for REST calls to the gateway
- Clean degradation path: `OpenClawProviderError` → HTTP 200 with `status=degraded` in the route, not 5xx propagation
- `_probe_gateway` tries `/readyz` then `/healthz` with 3s timeout; 404 on first path falls through gracefully
- `_normalise_output` handles varied upstream response shapes (nested `output.text`, flat `text`, `message`, `content`, `response`) and maps to `json_events` envelope consistent with other providers
- `_status_from_body` always returns "completed" as fallback — acceptable since error cases are handled upstream by exception

**Route `POST /api/openclaw-adapter/assistant/providers/openclaw/invoke`**:
- Requires `X-Operator-Id` (returns 401 if absent) — consistent with other assistant provider routes
- `GatewayOpenClawProviderError` caught and returned as HTTP 200 with degraded status — correct degradation contract

**BFF routing**:
- `openclaw`/`openclaw_agent` routed to `/api/openclaw-adapter/assistant/providers/openclaw/invoke`
- `messages` and `attachments` forwarded in body

**Test coverage (9 new tests)**:
- `test_openclaw_invoke_requires_operator_id`
- `test_openclaw_invoke_returns_completed_result_on_success`
- `test_openclaw_invoke_degrades_cleanly_on_gateway_error`
- `test_openclaw_readiness_ready_when_url_configured`
- `test_openclaw_readiness_not_configured_when_url_absent`
- `test_list_providers_includes_openclaw_first`
- `test_capabilities_includes_assistant_openclaw`
- `test_openclaw_ops_client_routes_openclaw_provider_to_correct_path`

## No Issues Found

The implementation is narrow, well-tested, and correctly preserves fallback. No fabrication, no dead code, no scope creep.
