# IMT-001 Review — TraderTrajectory Schema

Reviewer: Claude
Task: IMT-001 TraderTrajectory schema
Commit reviewed: 98bf9db2
Date: 2026-05-16

## Verdict

**Approved.** All governance invariants are correctly enforced at both the JSON Schema and Python model layers.

## Schema Review

- `$schema`: Draft-07 with correct `$id`. ✓
- All required top-level fields enforced via `"required"` array. ✓
- `actor_role` restricted to `operator`/`approver` — no system/algorithm actor allowed. ✓
- `target.promotion_state` restricted to `candidate`/`paper` — live stage correctly excluded. ✓
- `storage_ref.checksum` enforced with `^sha256:[0-9a-f]{64}$` pattern. ✓
- `lineage.source_trace_refs` has `minItems: 1` — lineage cannot be empty. ✓
- `no_order_reason` required at top-level when `decision=no_order` via `allOf/if-then`. ✓
- `no_order_reason` required at step level when `action=no_order`. ✓
- `governance.research_only: const true`, `direct_live_influence: const false`. ✓
- `additionalProperties: false` at root and all key definitions — schema is strict. ✓

## Model Review (trajectory_models.py)

- Frozen dataclasses with `__post_init__` validation consistent with schema invariants. ✓
- `TraderTrajectory.from_dict` validates JSON schema first, then applies Python-level cross-field checks. ✓
- `target.strategy_id` cross-validated against top-level `strategy_id`. ✓
- `no_order_reason` checked at both decision and step level in Python. ✓
- `TrajectoryGovernance.__post_init__` enforces `research_only is True` and `direct_live_influence is False` with identity checks (not equality) — correct for booleans. ✓
- `to_dataset_session()` returns only the fields consumed by `DatasetBuildRequest`. ✓
- Round-trip (`from_dict → to_dict`) is clean and tested. ✓

## Test Coverage

10 tests covering:
- Schema validity as Draft-07 meta-schema
- Full round-trip through model and schema validation
- Rejection of system actor_role
- Rejection of live promotion_state target
- Rejection of mismatched target.strategy_id
- no_order_reason enforcement at decision and step levels
- governance.direct_live_influence=True rejection
- storage_ref invalid checksum rejection
- DatasetBuildRequest compatibility (end-to-end feed)

All tests are focused and correctly test the governance boundaries.

## Package Exports

`__init__.py` exports all IMT-001 public symbols correctly with a complete `__all__`. ✓

## No Issues Found

No required changes. Owner Codex to finalize to done.
