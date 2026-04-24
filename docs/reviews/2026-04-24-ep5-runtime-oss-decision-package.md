# 2026-04-24 EP5 / Runtime / OSS Decision Package

Status: adopted operating defaults for the post-EP4 closeout tail

This packet turns the remaining repo-local gaps into one concrete execution order.

## Adopted Decisions

1. `EP5-002` proceeds as `canary-first`, not `live-first`.
2. The first `EP5-002` packet must use one exact runtime tuple for deploy and rollback:
   - code commit
   - env-file revision
   - credentials revision
   - broker / venue target
   - capital and gross scale envelope
3. Runtime verification `32 -> 46` is split into two batches:
   - Batch 1: consultation + knowledge surfaces
   - Batch 2: operator + trainer residuals
4. `Qlib` is the next OSS production-activation candidate.
5. `TRL` remains `smoke-tested` until its data and downstream-consumer gates are true.
6. `FinRL`, `RLlib`, `Ray Tune`, and `W&B` remain deferred until their explicit re-entry gates are
   cited by a reopened task.

## EP5-002 Execution Order

1. Complete `EP5-001` readiness using `docs/deployment/ep5-canary-ready/`.
2. Freeze the target runtime tuple and archive it before deploy.
3. Execute one governed canary deploy.
4. Verify stage transition plus telemetry, lineage, and governance events.
5. Execute one rollback drill against the same runtime tuple.
6. Archive the full packet under `docs/deployment/evidence/ep5-*`.
7. Capture operator signoff with timestamp and approver identity.

## Runtime Verification Batch Plan

### Batch 1

- `CW-01-consult-request`
- `CW-02-debate-transcript`
- `CW-03-committee-board`
- `CW-04-redteam-memo`
- `KW-01-institutional-memory`
- `KW-02-research-notes`
- `KW-03-evidence-refs`
- `KW-04-insight-cards`

Goal: quickly raise proof coverage on the consultation / knowledge lanes where runtime verification
can often share the same operator session and review context.

### Batch 2

- `PKT-004-deployment-approval-drilldowns`
- `PKT-004-persona-management`
- `PKT-005-degradation-banner`
- `PKT-010-runtime-state-board`
- `TW-02-parameter-controls`
- `TW-03-before-after-compare`

Goal: finish operator / trainer residuals after Batch 1 lands, without mixing control-surface proof
into the first consultation / knowledge pass.

## OSS Activation Order

### Qlib

Treat `Qlib` as the next activation candidate once the required dataset, RS-003 replication, and
approved LightGBM-first artifact are archived together with operator signoff.

### TRL

Do not activate `TRL` yet. Keep it at `smoke-tested` until all of the following are true together:

- `>=200` valid FB-002 events
- `>=100` governed preference pairs
- active LP-002 imitation baseline
- one downstream consumer ready
- operator signoff for the selected DPO use case

### Deferred Lanes

`FinRL`, `RLlib`, `Ray Tune`, and `W&B` stay deferred. A reopened task must point at the exact gate
that became true; usefulness alone is not a reopen condition.

## Closeout Rule

This packet records the adopted execution order. It does not itself close `EP5-002`, runtime
verification, or OSS activation. Those close only when their evidence bundles are committed and
reviewable.
