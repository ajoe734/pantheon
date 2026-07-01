# Review: LOOP-AUTO-BFF-002 - BFF downstream health monitor

Reviewer: Codex
Date: 2026-06-27
Decision: **approved after reviewer fix**

## Scope Reviewed

Task: Add BFF downstream health monitor
Owner: Claude

Reviewed artifacts:

- `services/control-plane/bff/downstream_health_monitor.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_downstream_health_monitor.py`
- `docs/deployment/evidence/loop-auto-bff-002/evidence-note.md`
- `.orchestrator/task-briefs/loop_auto_bff_002.md`

Reviewed implementation commit:

- `92245b8d3db1d1fa1c634b74b26a6947347093b5`

The task branch was merged with latest `origin/dev` during review before
rerunning verification.

## Findings

No remaining blocking issues.

Small reviewer fixes applied in this PR:

- Preserve the recovered incident id before clearing local tracking, so the
  recovery log does not report `unknown`.
- Exercise the below-threshold incident guard through `_handle_probe_result()`
  instead of leaving an empty `pass` test.
- Remove trailing whitespace from the evidence note and refresh verification
  counts after merging current `origin/dev`.

## Acceptance Assessment

| Criterion | Verdict | Evidence |
|---|---|---|
| BFF downstream degradation emits telemetry | Pass | `_emit_telemetry_sync()` sends `runtime_health` events to `/api/telemetry/ingest`; tests assert event type, sentinel context, metrics, and target metadata. |
| Health monitor can open or update incidents for sustained failure | Pass | Incident creation is threshold-gated, uses stable sentinel ids, handles HTTP 409 idempotently, and now tests the below-threshold guard through `_handle_probe_result()`. |
| BFF degraded mode does not affect active runtimes | Pass | Probe loop runs in a background task, side effects use `asyncio.to_thread()`, errors are swallowed, and BFF `/health` remains available while monitor state is degraded. |
| Operator-visible truth projection | Pass | `GET /bff/v5/downstream-health` exposes current target state, `overall_ok`, thresholds, and failure details behind read-role auth. |

## Verification Commands

```bash
AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-BFF-002
git merge --no-edit origin/dev
git diff --check
cd services/control-plane/bff
python3 -m pytest test_bff_downstream_health_monitor.py -q
python3 -m pytest test_bff_v5_loop_sentinel_contract.py test_loop_health_read_model_contract.py test_loop_inventory_read_model_contract.py test_pkt011_health_status_board_contract.py test_pkt013_operator_home_contract.py -q
```

Results:

- `test_bff_downstream_health_monitor.py`: 26 passed, 20 warnings.
- Existing health/loop/operator focused suite: 33 passed, 20 warnings.
- `git diff --check`: passed.

## Conclusion

Approved for owner finalization after this review record and reviewer fixes
are committed, pushed, and the task PR merges into `dev`.
