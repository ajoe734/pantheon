# SUP-L12 dispatcher authoritative snapshot scaling

Task-scoped owner evidence for replacing guarded-dispatcher full journal replay
with one validated checkpoint snapshot while retaining authoritative admission
and task-state integrity.

| Field | Value |
|---|---|
| Owner | Codex |
| Reviewer | Human/Ops |
| Branch | `task/SUP-L12-DISPATCHER-AUTHORITATIVE-SNAPSHOT-SCALING-20260802` |
| Implementation candidate | `626631be8a7912cc4e9f9409f2756ce6d59a2c22` |
| Review state | `review_pending` |
| Catalog bytes | unchanged, SHA-256 `7f67b32555341de19feaa46b98fd09ad69de2a5b2f6767c40287626d9c01fdca` |

## Result

The 2026-08-02 baseline used the live 2,174,900,966-byte, 8,632-event
journal. `load_events()` plus `project_latest_state()` did not reach catalog
admission within 30 seconds and peaked at 5,604,472 KiB RSS.

Candidate `626631be8` used a shared-lock-consistent scratch clone of the later
2,232,574,631-byte, 8,723-event generation. It verified the complete SHA-256
prefix, accepted the checkpoint only after binding its cached head to the
actual final prefix record, revalidated zero tail events, and reached the
guarded admission verdict in 2.100 seconds at 59,076 KiB peak child RSS.
Snapshot validation itself took 1.776 seconds at 43,432 KiB process peak RSS.

The admission verdict was a correct fail-closed decision, not a timeout:
`L12-CONTROLLER-BFF-20260731` overlaps the currently live nonterminal
`LIFECYCLE-PROJ-BFF-001` without dependency ordering. This source task does
not resolve that runtime/catalog conflict and did not materialize any task.

## Integrity and atomicity

- Dispatcher authority and post-commit canonical readback now use
  `load_snapshot()` and keep state, event/head identity, and tail telemetry
  bound to one journal generation.
- Prefix validation remains SHA-256 over every journal byte. Checkpoint head,
  sequence, previous-event hash, event digest, state digest, and ID validation
  remain unchanged.
- Hashing is chunked and JSONL replay is record-wise; completed mmap pages are
  released where the platform supports it, removing the multi-gigabyte RSS
  high-water mark without weakening validation.
- Dry-run selects the non-mutating snapshot mode. Warm, stale-tail, missing,
  and corrupt checkpoint cases preserve journal, projection, checkpoint bytes
  or absence, and the checkpoint temp-file set while still validating every
  prefix byte and every required tail event.
- Mutation-capable reads retain checkpoint refresh. Concurrent append, forced
  replay parity, edited/truncated history, forged checkpoint, sequence/previous
  hash mismatch, short append, and pre-admission transaction failures remain
  deterministic regressions.
- The current catalog remains exactly 28 unique tasks: G1=25, G2=2, G3=1.
  The BFF revalidation edge remains on the release gate and the unique sink is
  `L12-VERIFY-LEARN-REAL-VERIFIER-001`.

## Reproduction

```bash
.venv-pantheon/bin/python \
  docs/deployment/evidence/supervisor/SUP-L12-DISPATCHER-AUTHORITATIVE-SNAPSHOT-SCALING-20260802/dispatcher_snapshot_scale_bench.py \
  --live-config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --command-root "$PANTHEON_COMMAND_ROOT" \
  --python .venv-pantheon/bin/python \
  --json docs/deployment/evidence/supervisor/SUP-L12-DISPATCHER-AUTHORITATIVE-SNAPSHOT-SCALING-20260802/bench-report.json
```

The filesystem did not support reflink, so this run used the harness's physical
scratch-copy fallback under the journal shared lock. Source journal and
checkpoint were never opened for writing; the scratch generation was removed
after the run. On the scratch generation, journal stat, checkpoint SHA/size,
and checkpoint temp-file set were identical before and after both the direct
snapshot read and guarded dry-run.

## Validation

```text
PYTHONPATH=.orchestrator .venv-pantheon/bin/python -m pytest -q \
  .orchestrator/rewrite/test_task_state_store.py \
  scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py \
  .orchestrator/test_supervisor.py::TaskStateShadowCatchupTests
→ 107 passed in 16.12s

.venv-pantheon/bin/python scripts/dispatch_twelve_loop_gap_2026_07_26.py \
  --validate-only --current
→ valid; 28 tasks; G1 maximum parallel frontier 25; exact catalog digests

PYTHONPATH=.orchestrator .venv-pantheon/bin/python -m pytest -q \
  scripts/test_dispatch_twelve_loop_gap_2026_07_26.py
→ 31 passed, 1 pre-existing unrelated legacy task-card assertion failed
```

The legacy failure is present on `origin/dev`: the immutable legacy catalog
expects `L12-SIGNOFF-001` owner `Claude`, while its human card still says
`Codex`. Neither file is changed here because the task forbids catalog/product
mutation. The 31 applicable legacy dispatcher tests pass.

## Non-interference and residual risk

No config, canonical journal/checkpoint, status file, provider policy, worker,
product controller, catalog task, service, or deployment was mutated. Rollout
is source merge followed by a separately governed command-runtime promotion
and live canary. Rollback is PR revert; no journal migration or destructive
operation is required.

Cold or integrity-forced full replay remains more expensive than a warm
checkpoint because every historical record must be revalidated. It remains an
explicit fail-safe path (`PANTHEON_TASK_STATE_STORE_FULL_REPLAY=1`), not an
admission fast path. The current live artifact conflict must be resolved by its
own governed owner before the 28-task catalog can be admitted.
