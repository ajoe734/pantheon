# Review: LOOP-AUTO-RT-003 - Add runtime session reaper and restart alignment

Reviewer: Claude
Date: 2026-07-01
Decision: **approved**

## Scope Reviewed

Task: Add runtime session reaper and restart alignment
Owner: Codex

Note: this review picks up after Claude2 was reassigned off the task
following a repeated provider-quota terminal failure (see
`task_reassigned` event in `ai-activity-log.jsonl`, 2026-07-01T07:48:58Z).
The implementation was already merged to `dev` via PR #2427
(merge commit `d63d052da9eacdf7e3b455302efc17baca9db0f5`, merged
2026-06-27T14:05:53Z) before this review cycle began.

Reviewed artifacts:

- `services/execution/runtime-manager/paper_fleet_reconciler.py`
- `services/execution/runtime-manager/test_paper_fleet_reconciler.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_pkt010_runtime_state_board_contract.py`
- `services/control-plane/bff/test_read_store_service_clients.py`
- `docs/deployment/evidence/loop-auto-rt-003/README.md`

## Controller Liveness Evidence

- `PaperFleetReconciler._monitoring_session_open` no longer treats
  `ended_at: null` plus stale `active: true` as liveness proof: it now
  checks an explicit `staleness` marker before falling back to the
  `active` flag.
- `_open_monitoring_session` force-closes any prior session for the
  binding (via `_end_monitoring_session(..., force=True)`) before minting
  a fresh `session_id`, so a restarted worker always gets a new session
  rather than inheriting a stale one.
- `_reap_stale_monitoring_sessions` ends sessions carrying an existing
  staleness marker even when the last summaries fetch failed
  (`may_derive_staleness` guard), and separately derives staleness from
  heartbeat age when summaries are available.
- BFF `_runtime_state_monitoring_terminal_reason` /
  `_runtime_state_monitoring_health_check` (main.py) and
  `ReadSurfaceStore._paper_runtime_monitoring_session_active` /
  `_paper_runtime_monitoring_staleness_marker` (read_store.py) apply the
  same staleness-first precedence, so the operator board and the
  reconciler agree on what counts as terminal.
- Runtime behavior (not just static review) is exercised by
  `test_stale_persisted_zombie_session_is_closed_on_restart`,
  `test_staleness_marker_does_not_count_as_live_session_on_restart`,
  `test_stale_heartbeat_ends_session_and_restarts_worker`,
  `test_stack_restart_recreates_workers_for_all_active_bindings`, and
  `test_killing_one_worker_restarts_only_that_worker` in
  `test_paper_fleet_reconciler.py`, and
  `test_pkt010_runtime_state_board_surfaces_terminal_monitoring_session`
  in the BFF contract test.

## Findings

No blocking issues found. No fixes applied during this review.

## Acceptance Assessment

- "Stale monitoring sessions are automatically ended": met —
  `_reap_stale_monitoring_sessions` force-ends sessions on an existing
  staleness marker or derived heartbeat staleness.
- "Restarted workers create fresh sessions": met —
  `_open_monitoring_session` always force-closes the prior session for a
  binding before minting a new `session_id`.
- "BFF surfaces session staleness and terminal reason": met — the
  runtime-state projection carries `terminal_reason` and `staleness`,
  and degrades `row_health` for terminal paper monitoring sessions.

## Verification

```bash
python3 -m pytest \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py \
  services/control-plane/bff/test_pkt010_runtime_state_board_contract.py \
  services/control-plane/bff/test_read_store_service_clients.py \
  services/control-plane/bff/test_loop_health_read_model_contract.py -q
```

Result: `50 passed in 19.90s`.

```bash
python3 -m pytest services/execution/runtime-manager -q
```

Result: `92 passed in 20.55s` (full runtime-manager suite, no regressions).
