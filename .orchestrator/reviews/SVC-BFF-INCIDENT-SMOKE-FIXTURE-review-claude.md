# Review: SVC-BFF-INCIDENT-SMOKE-FIXTURE

Reviewer: Claude
Date: 2026-04-30
Decision: **approved**

## Scope Reviewed

Task: BFF incident and postmortem smoke fixture honesty
Owner: Codex
Reviewed commit: `29e8215`

Artifact reviewed:
- `services/control-plane/bff/smoke_test_incident.py`

## Findings

No blocking findings.

The change keeps the smoke fixture honest:
- Incident and postmortem records are seeded through a temporary service-backed store.
- `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK` is disabled for the smoke run.
- External incident/postmortem endpoint variables are cleared so the test does not accidentally pass through an ambient backend.
- A regression check confirms missing backend paths return degraded empty/404 responses instead of fabricated incident records.

## Verification Run

```bash
PANTHEON_BFF_AUTH_STUB=true python3 services/control-plane/bff/smoke_test_incident.py
# Smoke test: 21 passed, 0 failed
```

```bash
python3 -m pytest -q services/control-plane/bff/smoke_test_incident.py services/control-plane/bff/test_read_store_bootstrap_snapshot.py services/control-plane/bff/test_read_store_incident.py
# 24 passed in 15.74s
```

```bash
git diff --check -- services/control-plane/bff/smoke_test_incident.py
# clean
```

## Acceptance Assessment

Approved for owner finalization. The smoke now uses explicit service-backed fixture data and preserves honest degraded behavior when incident backends are absent.
