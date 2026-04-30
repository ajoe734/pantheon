# Review: SVC-OPENCLAW-ACTIVATION-READY-E2E

Reviewer: Codex
Date: 2026-04-30
Decision: **approve**

---

## Scope Verified

Task: Add OpenClaw activation-ready E2E profile
Owner: Codex2

Artifacts reviewed:
- `scripts/smoke_openclaw_activation_ready_e2e.py`
- `scripts/test_smoke_openclaw_activation_ready_e2e.py`
- `services/openclaw-gateway-adapter/test_compose_activation.py`
- `docker-compose.yml`
- `docs/deployment/openclaw-activation-ready-e2e.md`
- `services/openclaw-gateway-adapter/Dockerfile`
- `services/openclaw-gateway-adapter/*` route and gate behavior used by the smoke

## Review Result

Approved. The previous P1 compose-container defect is resolved: the
`openclaw-activation-ready-e2e` compose service now builds from
`services/openclaw-gateway-adapter/Dockerfile`, which installs the adapter
FastAPI/httpx dependency set, and `pull_policy: build` prevents reusing a stale
smoke image.

The profile remains disabled by default, publishes no ports, and keeps
production/live broker gates closed while the smoke opens only task-scoped fake
paper fixtures inside the test process.

## Previously Reopened Finding

### P1 - Compose E2E profile was not runnable in its configured container

Initial review found that the compose service built from `Dockerfile.smoke`,
which did not install FastAPI/httpx. The configured compose E2E run failed with:

```text
ModuleNotFoundError: No module named 'fastapi'
```

This is now fixed by building the adapter image from
`services/openclaw-gateway-adapter/Dockerfile`.

## Verification

```bash
docker compose --profile openclaw-activation-ready-e2e run --rm --no-deps openclaw-activation-ready-e2e
# OpenClaw activation-ready E2E: 13/13 passed

docker compose --profile openclaw-activation-ready-e2e config --quiet
# passed

python3 scripts/smoke_openclaw_activation_ready_e2e.py
# OpenClaw activation-ready E2E: 13/13 passed

python3 -m pytest scripts/test_smoke_openclaw_activation_ready_e2e.py services/openclaw-gateway-adapter/test_compose_activation.py -q
# 5 passed

python3 -m pytest services/openclaw-gateway-adapter -q
# 194 passed

git diff --check -- docker-compose.yml scripts/test_smoke_openclaw_activation_ready_e2e.py docs/deployment/openclaw-activation-ready-e2e.md
# passed
```
