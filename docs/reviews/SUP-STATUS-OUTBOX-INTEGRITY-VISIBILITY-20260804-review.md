# Review Evidence Manifest: SUP-STATUS-OUTBOX-INTEGRITY-VISIBILITY-20260804

- Task: SUP-STATUS-OUTBOX-INTEGRITY-VISIBILITY-20260804
- Title: Make activity-log-integrity-blocked status writes durable and visible instead of silently dropped
- Owner: Antigravity
- Reviewer: Claude

## Summary of Changes
1. **Feature Flag (`PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED`)**:
   - Defined `STATUS_OUTBOX_VISIBILITY_ENABLED_ENV = "PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED"` constant in `.orchestrator/common.py` and `scripts/ai_status.py`.
   - Added `is_status_outbox_visibility_enabled()` check in `scripts/ai_status.py`. Feature flag defaults to `False` (`0` / off).
   - Added shadow comparison support in `.orchestrator/rewrite/shadow.py` (`compare_outbox_indicators`).

2. **Durable & Visible Pending Indicators**:
   - `_update_pending_outbox_indicators(state)` calculates per-task pending counts (`status_write_pending_count`) strictly based on specific activity events and archive snapshots targeted to that `task_id`.
   - Prevents whole-board false positives: unbound events without a `task_id` (such as wave events) only count towards total unbound metrics and do NOT mark untouched tasks as pending.
   - Cleans transient fields `status_write_pending` and `status_write_pending_count` when feature flag is off or outbox is cleared.

3. **Archive Staging Clean-up**:
   - Strips `status_write_pending` and `status_write_pending_count` at snapshot staging time in `archive_terminal_task_from_state` so transient fields never leak into immutable task archives.

4. **Exception Handling & State Persistence**:
   - In `recover_status_activity_outbox`, on `ActivityAuditInvariantError`, `refresh_derived_status_views(state)` is invoked alongside `_update_pending_outbox_indicators(state)` and `save_state(state)` to persist state updates and sync derived mirrors.

5. **Verification & Tests**:
   - Added `test_status_write_pending_indicators` in `scripts/test_ai_status.py` verifying feature flag control, task-bound indicator setting, and prevention of false positives on untouched tasks from unbound events.
   - Verified 188 unit tests passing via `./.venv-pantheon/bin/python3 -m pytest -q scripts/test_ai_status.py`.
   - Verified shadow validation (`PYTHONPATH=.orchestrator ./.venv-pantheon/bin/python3 -m rewrite.shadow --config .orchestrator/config.json --board ai-status.json`).
