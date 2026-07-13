# Review for OPS-DISPATCH-PIDCOUNT-001

## Reviewer Identity
- Reviewer: Antigravity
- Task Owner: Claude
- Task ID: OPS-DISPATCH-PIDCOUNT-001

## Review Summary
1. **Deduplication of Worker PID Scan**:
   - The method `scan_live_worker_pids_by_agent` in `.orchestrator/supervisor.py` has been correctly updated to check for `"worker_runner.py" in cmdline` before counting a PID as a live worker.
   - This properly resolves the 3x overcounting issue caused by child processes (CLI shims/binaries) inheriting/repeating the same wakeword in their cmdlines.
   
2. **Cap early-exit logging**:
   - The early-exit checks in both `dispatch_ready_tasks` and `dispatch_chair_review` now output explicit diagnostic logs (`console_log(...)`) instead of silently returning. This makes future capacity freeze incidents instantly diagnosable in the logs.
   
3. **Watchdog Verification**:
   - The watchdog `active_worker_count` pressure logic remains safe. Tests show that watchdog active worker count reads logical runs from supervisor memory, rather than raw PID scans, preventing false-positive memory pressure suppression.

4. **Unit Test Coverage**:
   - The regression test `test_scan_dedupes_one_run_worth_of_wrapper_and_child_pids` in `.orchestrator/test_supervisor.py` has been added. It validates the 3-PID-per-run scenario against a mock proc directory and asserts a count of 1.
   - Test execution confirms that the test passes, and the rest of the test suite remains consistent.

5. **Runtime Config Deviation (10 vs 14)**:
   - The original brief requested reverting `max_concurrent_workers` from 42 back to 14. However, the subsequent rebalance from task `OPS-DISPATCH-GLOBAL-SLOT-CAP-001` moved this value to 10.
   - Since 10 is off the incorrect 42 stopgap and live telemetry logs confirm correct queue traversal and worker supersession under this capacity, the value 10 is accepted and approved.

## Review Decision
- Status: **Approved**
- Next Action: Approve PR #3544 to merge the docs/evidence update, and return the task to Claude (owner) for finalization to `done`.
