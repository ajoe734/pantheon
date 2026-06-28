# Review: LOOP-AUTO-BFF-003

Reviewer: Claude
Task: LOOP-AUTO-BFF-003 — Label seed snapshot registry scheduled and live truth
Date: 2026-06-27

## Summary

Review approved. All three acceptance criteria are met with contract tests and a working frontend panel.

## Acceptance Criteria Check

### 1. No panel displays seed or fixture as live proof ✅

`loop_inventory.py` enforces `accepted_as_live = level in _LIVE_EVIDENCE_LEVELS and status == "present" and source != "local_snapshot"`. The `_LIVE_EVIDENCE_LEVELS` set contains only `reconciled_live_proof` and `proven_live_evidence`. Seed/fixture and snapshot sources cannot pass this gate.

`test_loop_health_local_snapshot_is_labeled_snapshot_not_live` verifies the critical edge case: even when a local snapshot holds a record at `reconciled_live_proof` truth level, the `operator_truth` label becomes `Snapshot fallback` with `accepted_as_live: false` and `can_claim_reconciled: false`.

`LoopTruthPanel.tsx` renders `data-live-proof="false"` for any non-live badge and shows an AlertTriangle icon.

### 2. Registry metadata and live truth are labeled separately ✅

The label taxonomy is explicit in `_TRUTH_SOURCE_LABELS` and `_TRUTH_SOURCE_TYPES`:

| Level | Label | source_type |
|---|---|---|
| seed_fixture | Seed / fixture | seed_fixture |
| snapshot_fallback | Snapshot fallback | snapshot |
| registry_metadata | Registry metadata | registry |
| scheduled_tick | Scheduled tick | scheduled |
| reconciled_live_proof | Reconciled live truth | live_truth |
| proven_live_evidence | Proven live truth | live_truth |

`test_loop_health_service_store_live_truth_is_separate_from_registry` verifies that when `reconciled_live_proof` is present from the service store, the `registry_metadata` source_type remains `"registry"` and the `reconciled_live_proof` source_type remains `"live_truth"` — the two levels never merge.

`truth_label_payload()` surfaces all six labels to the frontend's `meta.truth_labels` so the `TruthLegend` component can render them without hardcoding.

### 3. Degraded truth source is visible in operator payload ✅

`_operator_truth_source` sets `degraded: True` and `degraded_reason` whenever no accepted-live truth source is present. The `live_status.operator_truth` sub-object is included in both the list and detail endpoints.

`test_loop_health_registry_only_lists_all_loops_without_live_claim` verifies that registry-only loops surface `operator_truth.degraded: true` with a human-readable degraded reason, and that `meta.surfaces.loop_health.status == "degraded"`.

The `LoopRow` component renders `data-testid="degraded-reason-{loop_id}"` with the warning message when `truth.degraded` is true.

## Test Evidence

- `python3 -m pytest services/control-plane/bff/test_loop_health_read_model_contract.py services/control-plane/bff/test_loop_inventory_read_model_contract.py` → 9 passed
- `npm test -- --run src/management/components/loop-truth/LoopTruthPanel.test.tsx` → 1 passed
- `npm test -- --run src/lib/bff/__tests__/client.test.ts -t loopHealth` → 1 passed, 28 skipped
- `npm run build:management` → passed
- PR #2433 merged into dev at commit `9203f87e`

## Minor Observations (non-blocking)

- `client.test.ts` has pre-existing baseline failures (`liveStatus._reset is not a function`, mock seed list expectations) unrelated to this task. These are tracked as baseline noise and should be cleaned up in a separate task.
- `snapshot_fallback` and `seed_fixture` share the same rank (0). This is a correct design choice: both are non-live and rank equally below registry_metadata. The operator_truth selection logic correctly prefers snapshot_fallback over seed_fixture when a snapshot is present (it is presented as a higher-quality visible audit trail).

## Verdict

Approved for owner closeout. Codex may run `done` after confirming the PR merge is recorded.
