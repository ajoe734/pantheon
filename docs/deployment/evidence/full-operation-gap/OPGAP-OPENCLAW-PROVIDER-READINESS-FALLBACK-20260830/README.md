# Evidence: OPGAP-OPENCLAW-PROVIDER-READINESS-FALLBACK-20260830

## Incident Background
In deployment run `33332882810` (job `99314532003`), `wait_for_openclaw_readiness` failed after 5 attempts across 90s with `CURL_REQUEST_TIMEOUT`. The primary model `anthropic/claude-opus-4-8` had an expired CLI OAuth session and hung for the entire 20s budget on each attempt, preventing the live Codex fallback models (`openai/gpt-5.6-sol`, `openai/gpt-5.5`) from ever being evaluated.

## Solution Implemented
1. **Candidate Model Resolution**: OpenClaw gateway adapter resolves primary model (`anthropic/claude-opus-4-8`) and configured fallbacks (`openai/gpt-5.6-sol`, `openai/gpt-5.5`).
2. **Bounded Readiness Probe**: The 20s readiness probe budget is partitioned across candidates. If primary model times out or returns non-zero exit, probe captures sanitized `primary_unavailable` evidence and attempts fallback candidates within the remaining budget.
3. **Invoke & Stream Fallback Parity**: Single invoke and OpenResponses SSE streaming both support automatic model fallback across configured candidate models.
4. **Deterministic Fail-Closed**: If all candidate models fail, readiness remains degraded and typed error codes are returned without retrying side-effecting invoke turns.
5. **No Credential Mutation**: No credentials, tokens, or GitHub secrets were modified; credential refresh remains an operational task.

## Verification
- Unit & live test suites: 433 passed, 4 skipped in `services/openclaw-gateway-adapter`.
- Live smoke script test suite: 7 passed in `scripts/test_openclaw_assistant_openclaw_live_smoke.py`.
- Syntax & compose config: `bash -n scripts/openclaw-assistant-openclaw-live-smoke.sh` and `docker compose --profile openclaw config --quiet` passed clean.
