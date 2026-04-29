# Review: SVC-OPENCLAW-RUNTIME-ADOPTION-SCAFFOLD

Reviewer: Codex
Date: 2026-04-29

## Scope Verified

- `OPENCLAW_RUNTIME_CONTRACT.md` - fail-closed implementation boundary for OpenClaw adapter/facade work.
- `services/openclaw-gateway-adapter/main.py` - capability metadata, health details, upstream degradation, and deferred session creation.
- `services/openclaw-gateway-adapter/test_main.py` - adapter health/capability/session and execution-path guard coverage.
- `services/openclaw-gateway-adapter/test_compose_activation.py` - compose wiring and smoke-boundary checks.
- `services/research-worker-gateway/main.py` - OpenClaw deferred capability metadata and production-adapter rejection path.
- `services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py` - OpenClaw dispatch rejection and capability-surface tests.

## Acceptance Criteria Result

| Criterion | Result |
|---|---|
| repo-authoritative runtime surfaces can read OpenClaw adapter capability metadata in fail-closed mode | PASS |
| session creation and broker/live routes remain denied | PASS |
| upstream absence degrades without blocking runtime-manager safety paths | PASS |
| tests prove no paper, canary, live, or capital binding activation | PASS |

## Implementation Notes

- `/api/openclaw-adapter/capabilities` returns static metadata without a live upstream call and now exposes `capital_binding: deferred`, `fail_closed: true`, and explicit activation gate names.
- Adapter health details expose broker, paper, live, and capital-binding guard booleans; all default false.
- `POST /api/openclaw-adapter/sessions` remains a non-retryable 503 `CAPABILITY_DENIED`; `GET /sessions` degrades to `upstream_unavailable` when upstream is absent.
- `research-worker-gateway` registers `openclaw` as `deferred` with `gate_state: fail_closed` and `allowed_scope: capability_metadata_read_only`; dispatch is rejected by the existing production-adapter fence.
- The root compose adapter wiring keeps broker and paper activation false, and omitted live/capital envs default false in the adapter.

## Verification

```bash
python3 -m pytest services/openclaw-gateway-adapter/ services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py
# 28 passed in 1.60s

python3 -m py_compile services/openclaw-gateway-adapter/main.py services/research-worker-gateway/main.py
# exit 0

git diff --check -- services/openclaw-gateway-adapter/main.py services/openclaw-gateway-adapter/test_main.py services/openclaw-gateway-adapter/test_compose_activation.py services/research-worker-gateway/main.py services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py OPENCLAW_RUNTIME_CONTRACT.md docker-compose.yml scripts/smoke_honest_stack.py
# exit 0
```

## Decision

Approved. The scaffold satisfies the activation-gated OpenClaw boundary without enabling broker sessions, paper/canary/live execution, or capital binding. Returning to Claude2 for owner closeout.
