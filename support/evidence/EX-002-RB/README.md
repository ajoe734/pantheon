# EX-002-RB Evidence: Loader Metadata Migration

**Task:** EX-002-RB
**Title:** Loader metadata migration promotion_state -> artifact_state + deployment_stage (rebaseline)
**Owner:** Codex
**Reviewer:** Claude
**Date:** 2026-05-16

## Scope

Migrated the Artifact Loader from the legacy `promotion_state` field to the canonical split model:
- `artifact_state` (registry lifecycle: draft/candidate/approved/retired)
- `deployment_stage` (runtime placement: none/paper/canary/live/frozen)

Codex picked up the task after reassignment and addressed the prior review gap: canonical split metadata now
must carry `artifact_state=approved` before the loader accepts a `paper` or `live` execution stage. The legacy
fallback remains available only for pre-migration metadata that has `promotion_state` and no canonical split state.

## Files Changed

| File | Change |
|---|---|
| `services/registry/promotion/gate.py` | `build_execution_projection()` now emits `artifact_state` + `deployment_stage` + `promotion_state` (transition) |
| `services/execution/artifact_loader.py` | `_validate_metadata()` requires `artifact_state=approved` for canonical execution metadata, then reads `deployment_stage` with `promotion_state` fallback |
| `services/registry/lineage/promoted_artifact_metadata.schema.json` | Added `artifact_state` + `deployment_stage` as optional declared properties; `allOf` condition now checks `deployment_stage == "live"` |
| `services/execution/test_artifact_loader.py` | Updated field names in assertions; added `TestEX002RBLoaderMetadataMigration` regression coverage for canonical fields, legacy fallback, and unapproved artifact rejection |
| `services/execution/smoke_test_artifact_loader.py` | Updated assertion to use `deployment_stage` + `artifact_state` |
| `services/execution/artifact-loader/contract.md` | Updated to reflect migration complete |

## Lifecycle Mapping

| lifecycle_state (PromotionGate) | artifact_state | deployment_stage |
|---|---|---|
| candidate | candidate | none |
| paper | approved | paper |
| live | approved | live |
| retired | retired | frozen |

## Backward Compatibility

- Loader requires `artifact_state=approved` for canonical split metadata
- Loader reads `deployment_stage` first; falls back to `promotion_state` for pre-migration object store entries
- Gate emits both new fields AND `promotion_state` during transition period
- Schema declares new fields as optional (no breaking change for existing metadata)

## Verification

```bash
# Main loader test suite
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/test_artifact_loader.py -v
# → 18 passed

# Smoke test
PYTHONDONTWRITEBYTECODE=1 python3 services/execution/smoke_test_artifact_loader.py
# → EX-001 smoke test passed

# Promotion gate regression tests
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/promotion/test_gate.py -v
# → 4 passed

# Promotion gate smoke test
PYTHONDONTWRITEBYTECODE=1 python3 services/registry/promotion/smoke_test_gate.py
# → Execution projection smoke passed

# Registry tests (gate changes)
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/ -q
# → 69 passed
```

## Reviewer Approval

Claude's approval packet is recorded at `support/evidence/EX-002-RB/review-claude.md`.

## Owner Finalization Verification

Re-run by Codex on 2026-05-16 before owner closeout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/test_artifact_loader.py -v
# 18 passed in 8.79s

PYTHONDONTWRITEBYTECODE=1 python3 services/execution/smoke_test_artifact_loader.py
# EX-001 smoke test passed: promotion metadata projected through the LEAN Object Store helper and loaded safely.

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/promotion/test_gate.py -v
# 4 passed in 3.98s

PYTHONDONTWRITEBYTECODE=1 python3 services/registry/promotion/smoke_test_gate.py
# Execution projection smoke passed.

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/ -q
# 69 passed in 71.19s
```
