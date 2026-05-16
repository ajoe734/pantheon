# EX-002-RB Review — Claude

**Task:** EX-002-RB Loader metadata migration promotion_state -> artifact_state + deployment_stage (rebaseline)
**Owner:** Codex
**Reviewer:** Claude
**Date:** 2026-05-16
**Decision:** Approved

---

## Review Summary

The implementation correctly resolves the prior review gap. All four acceptance criteria are met.

## Acceptance Criteria Verification

**1. PromotionGate emits artifact_state and deployment_stage while preserving promotion_state**

`gate.py:build_execution_projection()` emits all three fields: `artifact_state`, `deployment_stage`, and `promotion_state` (retained for transition period). Lifecycle mapping is correct:
- candidate → artifact_state=candidate / deployment_stage=none
- paper → artifact_state=approved / deployment_stage=paper
- live → artifact_state=approved / deployment_stage=live
- retired → artifact_state=retired / deployment_stage=frozen

**2. ArtifactLoader requires artifact_state=approved for canonical split metadata**

`artifact_loader.py:_validate_metadata()` enforces a two-stage gate:
- If `artifact_state` is present and not `approved`, raise immediately (covers candidate, retired, draft).
- If `deployment_stage` is present but `artifact_state` is absent or not `approved`, raise (closes the missing-artifact_state gap from the prior review).

Both checks confirmed correct by tracing test cases for candidate+paper, missing artifact_state+paper, and None+paper.

**3. Legacy promotion_state-only metadata remains loadable**

When `deployment_stage` is absent and `artifact_state` is absent (pre-migration object store entries), the fallback `metadata.get("deployment_stage") or metadata.get("promotion_state")` correctly picks up `promotion_state`. Confirmed by `test_loader_falls_back_to_legacy_promotion_state`.

**4. All focused tests pass**

Verified personally:
- `pytest services/execution/test_artifact_loader.py -v` → 18 passed
- `python3 services/execution/smoke_test_artifact_loader.py` → passed
- `pytest services/registry/promotion/test_gate.py -v` → 4 passed
- `pytest services/registry/ -q` → 69 passed

## Schema Review

`promoted_artifact_metadata.schema.json` correctly:
- Adds `artifact_state` and `deployment_stage` as optional properties (no breaking change).
- `allOf` condition migrated from `promotion_state == "live"` to `deployment_stage == "live"` (requiring approved_at, approver, rollback for live stage).

## No Blocking Findings

The implementation is clean and properly scoped. The prior review gap is closed. Returning to Codex for finalization.
