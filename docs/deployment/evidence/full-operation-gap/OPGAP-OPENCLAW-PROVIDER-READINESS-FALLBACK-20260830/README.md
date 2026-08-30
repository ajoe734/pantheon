# Evidence: OPGAP-OPENCLAW-PROVIDER-READINESS-FALLBACK-20260830

## Incident Background
In deployment run `33332882810` (job `99314532003`), `wait_for_openclaw_readiness` failed after 5 attempts across 90s with `CURL_REQUEST_TIMEOUT`. The primary model `anthropic/claude-opus-4-8` had an expired CLI OAuth session and hung for the entire 20s budget on each attempt, preventing the live Codex fallback models (`openai/gpt-5.6-sol`, `openai/gpt-5.5`) from ever being evaluated.

## Solution Implemented
1. **Candidate Model Resolution**: OpenClaw gateway adapter resolves primary model (`anthropic/claude-opus-4-8`) and configured fallbacks (`openai/gpt-5.6-sol`, `openai/gpt-5.5`).
2. **Exact Sentinel Verification**: Readiness probe strictly asserts that candidate answers contain the exact `PANTHEON_PROVIDER_READY` sentinel. Mismatches trigger fallback or fail closed with `openclaw_answer_probe_sentinel_mismatch`.
3. **Bounded Readiness Probe**: The 20s readiness probe budget is partitioned across candidates. If primary model times out or returns non-zero exit, probe captures sanitized `primary_unavailable` evidence and attempts fallback candidates within the remaining budget.
4. **Side-Effect Safe Invoke**: Single invoke supports candidate model fallback on unambiguous pre-execution errors (e.g. auth unavailable), but strictly disallows retrying after `TimeoutExpired` (`OPENCLAW_GATEWAY_TIMEOUT`) to prevent ambiguous turns from executing twice.
5. **Upstream Contract Stream**: OpenResponses SSE stream strictly targets the OpenClaw v2026.7.1 contract (`model: "openclaw"`), surfacing typed error events on failures.
6. **Deterministic Fail-Closed**: If all candidate models fail, readiness remains degraded and typed error codes are returned without retrying side-effecting invoke turns.
7. **No Credential Mutation**: No credentials, tokens, or GitHub secrets were modified; credential refresh remains an operational task.

## Verification
- Unit & live test suites: 445 passed, 4 skipped across `services/openclaw-gateway-adapter` and `scripts/test_openclaw_assistant_openclaw_live_smoke.py`.
- Syntax & compose config: `bash -n scripts/openclaw-assistant-openclaw-live-smoke.sh` and `docker compose --profile openclaw config --quiet` passed clean.
