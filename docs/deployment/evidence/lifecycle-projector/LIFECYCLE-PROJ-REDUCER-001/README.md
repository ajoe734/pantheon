# Evidence Summary: LIFECYCLE-PROJ-REDUCER-001

- Task ID: LIFECYCLE-PROJ-REDUCER-001
- Title: Replace full rebuild with a bounded incremental reducer
- Owner: Codex2
- Reviewer: Antigravity
- Status: in_progress (CI-history repair; fresh review required)
- Base Branch: dev
- Task Branch: task/LIFECYCLE-PROJ-REDUCER-001
- Code head this manifest describes: `b80826a1b90d83566ab8281b08dc0ff5526a4d89`

`b80826a1b` is the commit whose contents are checksummed and measured below.
This manifest is committed on top of it, so the PR head is one commit later;
the manifest deliberately does not name its own commit.

## Authorship

The prior task history was rejected by CI because three anchor subjects exceeded
the 72-character repository limit. Codex2 reconstructed the same verified
reducer patch on the current `dev` tip as `b80826a1b`; historical commits and
their review tags remain audit history only. Antigravity is the reviewer of
record and must review the reconstructed exact head before approval.

## Verifying the checksums

```bash
# from the repository root
sha256sum -c docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-REDUCER-001/SHA256SUMS
```

The paths in `SHA256SUMS` are repo-relative, so the repository root is the only
cwd from which it verifies. `SHA256SUMS` and `evidence.json.file_checksums`
were generated from the same command at `b80826a1b` and agree.

## Validation Commands and Results

1. Full `services/trade_journey` suite:
   ```bash
   /home/lupin/pantheon/.venv/bin/python -m pytest -q services/trade_journey
   ```
   Result: PASS (128 passed, 19 skipped)

2. Focused projector and canonical-paper integration tests:
   ```bash
   /home/lupin/pantheon/.venv/bin/python -m pytest -q \
     services/trade_journey/test_lifecycle_projector.py \
     services/trade_journey/test_canonical_paper_lifecycle_integration.py
   ```
   Result: PASS (29 passed)

3. Downstream consumers of the projector state and bundle:
   ```bash
   /home/lupin/pantheon/.venv/bin/python -m pytest -q \
     services/control-plane/bff/test_lifecycle_projector_readiness.py \
     services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
     scripts/test_wait_for_bff_lifecycle_readiness.py
   ```
   Result: PASS (76 passed)

4. Checksum verification and whitespace check:
   ```bash
   sha256sum -c docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-REDUCER-001/SHA256SUMS
   git diff --check
   ```
   Result: PASS (5 files OK)

`services/trade_journey` went from 123 passed to 128 passed: four tests that
asserted the removed internals were replaced by seven test functions (nine
collected items, because the SIGKILL test is parametrized over two crash
points), a net +5. The replacements cover equivalence against an independent
baseline, bounded per-poll work, live promotion, intra-batch idempotency,
staged-conflict atomicity, SIGKILL convergence and state-transaction-failure
convergence. No test was deleted without a stronger replacement.

## What Changed

`BoundedAggregateState` now holds *folded* read-model state for one journey:
the derived Trade Journey events, per-event fingerprints and source modes,
folded loop-run counters, and the cached loop-run record. `events_by_id` and
`canonical_events` are gone, so no all-history raw canonical event log survives
in runtime state.

- `project_records`, `record_poll` and `record_source_failure` no longer call
  `copy.deepcopy(self.state)`. `_bounded_state_copy` duplicates only scalars,
  the fixed-size controller dict, the `<=1000` entry quarantine ring, and the
  keys of `identity_chains`.
- `stage_batch` deep-copies and rebuilds only the aggregates a batch touches,
  and commits them to live state only after the bundle and controller state
  have both landed.
- `render_full_payloads` concatenates cached per-aggregate event slices and
  overlays a fixed-size controller stamp on cached loop records. It derives no
  event and rebuilds no aggregate.
- Live re-delivery of a recovery/backfill event promotes the aggregate and
  rematerializes it, so `source_modes`, `accepted_live` and `projection_mode`
  reach `loop_runs.json` again.
- Idempotency is resolved against committed aggregates *and* against entries
  already accepted from the same batch, so an intra-batch duplicate is counted
  once and an intra-batch conflict fails closed before checkpoint, generation
  or controller move. The accepted count comes from `stage_batch`.
- The dead full-rebuild path (`_render`, `_loop_records`,
  `_loop_records_for_entries`, `_entry_sort_key`) is deleted from the projector.

## Measured Bound (old `06c94cae0` vs reconstructed `b80826a1b`)

One new 8-event journey per poll, total history growing 8 to 40 events:

| poll | canonical entries derived (old / new) | aggregates rebuilt (old / new) | whole-state bytes deep-copied (old / new) |
| ---- | ------------------------------------- | ------------------------------ | ----------------------------------------- |
| 1    | 16 / 8                                | 1 / 1                          | 859 / 0                                   |
| 2    | 24 / 8                                | 1 / 1                          | 37352 / 0                                 |
| 3    | 32 / 8                                | 1 / 1                          | 73710 / 0                                 |
| 4    | 40 / 8                                | 1 / 1                          | 110068 / 0                                |
| 5    | 48 / 8                                | 1 / 1                          | 146426 / 0                                |

A pure duplicate-replay poll derived 40 entries on the old head and derives 0
on the new one. The same harness also reproduced the other two behavioural
blockers: an 8-row batch plus one byte-identical copy of row 0 returned
`accepted=9, duplicates=0` on the old head and returns `accepted=8,
duplicates=1` now; and a recovery batch re-delivered live published
`source_modes=['recovery'] accepted_live=False projection_mode=recovery` on the
old head and publishes `['live'] / True / live` now.

The measurement harness is a throwaway reviewer script run against a
`git archive` extract of `06c94cae0` and against this worktree; it is
intentionally not committed. `test_reducer_work_is_bounded_by_batch_and_affected_aggregates`
pins the same bound from counters wired into the live path, so the claim does
not depend on the harness being rerun.

## Residual Cost, Stated Explicitly

Two costs remain proportional to total read-model size:

1. concatenating and ordering the already-materialized events into the
   published bundle, and
2. serializing `controller_state.json`.

Both are structural to publishing a full-snapshot bundle to a single file, not
to the reducer: neither re-derives an event nor rebuilds an aggregate.
Per-aggregate persistence belongs to the `LIFECYCLE-PROJ-STORE-001` relational
store and is out of scope here (`projection_store` schema is listed under
`not_changing`).
