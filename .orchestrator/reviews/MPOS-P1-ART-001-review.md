# Review: MPOS-P1-ART-001 — Wire AllocationPolicyArtifact into registry governance

**Reviewer:** Claude  
**Owner:** Claude2  
**Commit reviewed:** 46bcaa82  
**Date:** 2026-06-09

## Verdict: APPROVED

All five acceptance criteria are satisfied. 67 tests pass. Worktree is clean.

---

## Acceptance Criteria Evaluation

### 1. AllocationPolicyArtifact is a supported registry artifact type with lineage and conflict resolution log ✅

- `ArtifactType.ALLOCATION_POLICY = "allocation_policy"` added to `services/registry/models.py`
- `registry_entry_schema.json` enum updated to include `allocation_policy`
- `promoted_artifact_metadata.schema.json` enum updated to include `allocation_policy`
- Lineage mapping is correct:
  - `provenance_refs → lineage.source_run_ids` (PersonaAllocationProposal ids)
  - `conflict_resolution_log_id → lineage.source_strategy_spec_id`
- Tests `test_register_lineage_provenance_refs` and `test_register_lineage_conflict_log_id` verify extraction.

### 2. Registration as candidate preserves pool id, scope, risk policy ref, and proposal provenance ✅

- `strategy_id = capital_pool_id` establishes pool-scoped identity in the registry
- `artifact_state = CANDIDATE` on registration (governance advances it later)
- `scope_ref`, `risk_budget` (if present), `sponsor_persona_id`, and `synthesis_method` preserved in `evaluation_summary`
- Full artifact preserved in `metadata.allocation_policy_artifact`
- `provenance_refs` preserved as `lineage.source_run_ids`
- Tests: `test_register_evaluation_summary_carries_synthesis_evidence`, `test_register_artifact_stored_inline_in_metadata`

### 3. Promotion gate can require committee evidence and risk evidence for allocation artifacts ✅

- `POST /api/registry/allocation-policy-artifacts/{registry_id}/advance` endpoint exists
- `AdvanceRequest.approver` captures the committee member/bot identity
- `evaluation_summary` at registration time carries all synthesis evidence (method, sponsor, scope, risk_budget, conflict_log_id) so governance reviewers have full provenance before approving
- Standard `advance_artifact_state` governance path is shared with all artifact types — consistent governance model
- Tests: `test_advance_candidate_to_approved`

### 4. DeploymentPlan can reference an approved allocation artifact ✅

- `StagePlanner.create_plan()` already validates `artifact_state == 'approved'` — once an `allocation_policy` artifact is advanced to `approved`, it satisfies this gate
- `strategy_id = capital_pool_id` satisfies `DeploymentPlan.capital_pool_id` field matching
- `artifact_type` is stored as a plain string in `DeploymentPlan` — no type restriction in the planner
- Test: `test_approved_entry_is_deployment_plan_ready` validates the full property set required by DeploymentPlan

### 5. Artifact loader rejects unapproved or pool mismatched allocation artifacts ✅

The existing `artifact_loader.py` already enforces both invariants generically:
- **Unapproved rejection:** `_validate_metadata()` raises `ArtifactLoadError` if `artifact_state != 'approved'` (lines 287–291)
- **Pool mismatch rejection:** `_validate_metadata()` raises `ArtifactLoadError` if `metadata.strategy_id != strategy_id` parameter (lines 276–279) — for `allocation_policy` artifacts, `strategy_id == capital_pool_id`, so a wrong pool argument is caught here
- **Schema validation:** The artifact loader schema chains through `promoted_artifact_metadata.schema.json`, which now includes `allocation_policy` in the enum, so schema validation also passes for valid artifacts

No explicit type guard in `artifact_loader.py` is required — the generic enforcement covers the acceptance criterion. The commit's boundary decision ("Not changing: execution/artifact_loader.py") is architecturally sound.

---

## Code Quality

**Strengths:**
- Clean facade pattern: registry does not import optimizer-svc; caller embeds artifact inline
- `_validate_alloc_policy_artifact()` is narrow and exhaustive for the required fields
- Explicit `_SYNTHESIS_METHODS` allowlist prevents silent acceptance of unknown methods
- Checksum computed from deterministic JSON serialization (sorted keys, compact separators) when not supplied
- Pool-scoped listing via `/api/registry/pools/{capital_pool_id}/allocation-policy-artifacts` with optional state filter
- Type guard in GET/advance endpoints (`_ensure_alloc_policy_view`) prevents cross-type confusion
- 22 targeted tests with fixture-based store isolation

**Minor observation (non-blocking):**
- `import hashlib` is placed lazily inside `_alloc_policy_register_payload()`. Moving it to the top-level imports would be standard Python style, but it has no runtime impact since `hashlib` is stdlib.

---

## Verification

```
python3 -m pytest services/registry/test_allocation_policy_artifact.py services/registry/test_service.py -- 67 passed
git status --short: ?? .orchestrator/task-briefs/mpos_p1_art_001.md  (generated, not in scope)
```

---

## Follow-on Scope (deferred, out of this task)

The commit correctly defers:
- `optimizer-svc` synthesis logic changes (separate task)
- `deployment_plan.py` governance logic changes (separate task)
- `artifact_loader.py` explicit allocation-policy type guard (not needed — generic enforcement is sufficient)

These are documented in the anchor commit body and are not gaps in this task's delivery.
