# SVC-POLICY-LEARNING-BOUNDARY Review

Reviewer: Codex
Reviewed at: 2026-04-28
Decision: Approved

## Scope Reviewed

- `services/policy-learning/`
- `docker-compose.yml` policy-learning wiring
- `scripts/smoke_honest_stack.py` policy-learning smoke coverage
- `services/training-session/tests` only as verification dependency from the handoff

## Findings

No blocking findings.

## Acceptance Check

- Health, capability list, job proposal, job list/status, and explicit rejection APIs are present.
- The service stores replayable JSON state under `POLICY_LEARNING_DATA_DIR`.
- `policy-learning-svc` is wired into compose with `PORT=8100`, named storage, healthcheck, and `POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS=false`.
- Qlib, TRL, RL/FinRL/RLlib, and W&B production adapter requests are rejected by default; no production learning runtime is activated.
- Tests cover stub lifecycle replay, explicit reject lifecycle, disabled production adapter rejection, and compose wiring.

## Verification

```text
pytest -q services/policy-learning/tests services/training-session/tests
6 passed

python3 -m py_compile services/policy-learning/main.py services/policy-learning/store.py scripts/smoke_honest_stack.py
passed

docker compose config --quiet
passed
```
