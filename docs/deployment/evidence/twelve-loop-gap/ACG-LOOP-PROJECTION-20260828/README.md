# ACG-LOOP-PROJECTION-20260828 loop-health projection evidence

Owner: Claude
Reviewer: Antigravity
Status: implementation complete; awaiting independent review

## Outcome

`GET /bff/v5/loop-health` previously composed current truth from four
authorities: static catalog metadata, `LoopControllerStore` rows, a BFF local
snapshot fallback (`read_store.list_loop_health_records`), and
`DownstreamHealthMonitor` synthesis run inline inside the read request
(including a read-side call to `publish_loop_12_controller_truth()` and a
`loop_target_map` block that could manufacture or override rows for Loops
1-11 from BFF dependency probes). It also mixed the one composite overlay
(`per_persona_ooda`) into the same `items` array as the twelve canonical
loops.

This task makes loop-health a pure, single-source projection:

- **`services/control-plane/bff/management_read_models/loop_truth.py`**
  (new): the only place current loop-health truth is fetched. It reads
  `LoopControllerStore` only, never a local snapshot, and never triggers a
  controller-runtime write.
- **`loop_inventory.py`**: `list_loop_health_entries`/`get_loop_health_entry`
  now iterate only the twelve canonical catalog loops
  (`_canonical_registry_entries`), never the composite overlay. Two admission
  gaps in `_runtime_controller_record_qualified` were also fixed: (1) a blank
  catalog controller identity can no longer be satisfied by any nonblank
  reported name (previously a permissive second validator on top of the
  catalog contract); (2) a controller record must now also carry an
  authoritative `desired_state_presence` and `downstream_actual_state` to
  qualify as current truth.
- **`main.py`**: deleted `_loop_health_store_records()` and
  `_async_loop_health_records()` (the fs-snapshot merge, the read-side
  `publish_loop_12_controller_truth()` call, and the cross-loop manufacture
  block). The two loop-health routes now call into `loop_truth` instead. The
  composite overlay is surfaced separately as
  `meta.composite_overlay_inventory`, never inside `items`.

`downstream_health_monitor.py` needed no functional change: it already
publishes Loop 12 controller truth exclusively from its own background
`_probe_all()` cycle, not from any read path, and it has no cross-loop
manufacture logic of its own (that lived entirely in `main.py`).

## Acceptance

All four of this task's acceptance criteria are met and covered by
`test_loop_health_read_model_contract.py` /
`test_loop_inventory_read_model_contract.py` (25/25 passing):

1. loop-health returns exactly twelve canonical rows.
2. current truth comes only from `LoopControllerStore` joined with catalog
   metadata.
3. `GET` loop-health performs no writes and the monitor cannot manufacture
   cross-loop rows.
4. the composite overlay is separately named noncanonical inventory data.

## Known out-of-scope regressions (documented, not silently fixed)

Removing the fs-snapshot merge and the downstream-monitor cross-loop
manufacture block -- both explicit `REMOVE` dispositions for this task --
breaks tests in two files that are **not** in this task's declared
`artifact_conflict_guard.artifact_scope**` and were left unmodified per scope
discipline:

- `test_current_twelve_owner_truth.py` (9 tests): its entire fixture
  mechanism injects records exclusively through the now-removed
  `PANTHEON_BFF_LOOP_HEALTH_STORE` fs-snapshot file.
- `test_bff_v5_loop_sentinel_contract.py::test_worker_functional_health_probing_and_paper_signal_producer_attribution`:
  asserts the exact cross-loop manufacture behavior `ACG-04-008` removes.

Full detail, exact failing test names, and recommended follow-up are in
`evidence.json` under `known_regressions_out_of_scope`. A `git stash`-based
before/after comparison against dev tip confirms these are the only new
regressions; the evidence.json also lists the failures already present on
dev tip before this task, to keep the two sets distinguishable.

`ACG-04-003`'s exact-deployed-SHA and controller-specific cadence admission
predicates are deliberately deferred (see `evidence.json`
`scope_deferral`) -- not required by this task's own acceptance array, and
the catalog does not yet declare the fields a real implementation would
need for the nine not-yet-implemented controllers.
