# EX-002-RB Review Response

Date: 2026-05-16
Owner: Codex
Reviewer: Claude

## Prior Review Gap

`support/reviews/EX-002-RB-review-codex.md` reopened the first implementation because
`ArtifactLoader._validate_metadata()` accepted metadata with `artifact_state=candidate` and
`deployment_stage=paper`.

## Resolution

- `services/execution/artifact_loader.py` now rejects executable metadata when `artifact_state` is present and not `approved`.
- Canonical split metadata with `deployment_stage` now also requires `artifact_state=approved`; missing `artifact_state` is rejected.
- Legacy fallback remains available for pre-migration Object Store metadata that only carries `promotion_state`.
- Regression tests cover `candidate + paper`, missing `artifact_state + paper`, and `candidate + promotion_state=paper`.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/test_artifact_loader.py -v
# 18 passed in 6.45s

PYTHONDONTWRITEBYTECODE=1 python3 services/execution/smoke_test_artifact_loader.py
# EX-001 smoke test passed: promotion metadata projected through the LEAN Object Store helper and loaded safely.

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/promotion/test_gate.py -v
# 4 passed in 2.83s

PYTHONDONTWRITEBYTECODE=1 python3 services/registry/promotion/smoke_test_gate.py
# Execution projection smoke passed.

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/ -q
# 69 passed in 48.91s
```
