# PFG-LIFECYCLE-READINESS-HEARTBEAT-20260824: Relational Lifecycle Projector Readiness Heartbeat After Catch-up

## Summary

Fixes the PostgreSQL lifecycle projector readiness heartbeat state machine where `record_poll` and `project_records` could keep the controller in `status='recovering'` and `accepted_live=False` even after the live cursor caught up to the watermark.

### Root Cause
1. `_controller_mutation` computed `optimistic_backlog = max(0, source_high_watermark - previous.checkpoint_seq)` without accepting an explicit or predicted backlog.
2. In `record_poll`, `_controller_mutation` computed `accepted_live` and `status` before `mutation.backlog_count = max(0, backlog)` was assigned, leaving `status="recovering"` and `accepted_live=False` when polling with `backlog=0`.
3. In `project_records`, `_controller_mutation` computed backlog against `previous.checkpoint_seq` rather than the batch's predicted checkpoint, momentarily writing `status="recovering"` during batch ingestion even when in `mode="live"`.

### Solution
1. `_controller_mutation` now accepts an optional `backlog: int | None = None` parameter and uses it when provided.
2. `record_poll` passes `backlog=backlog` to `_controller_mutation`, ensuring `optimistic_backlog=0`, `accepted_live=True`, and `status="ready"` when polling caught-up live state.
3. `project_records` computes `predicted_checkpoint` and `predicted_backlog` from the batch records and passes it to `_controller_mutation`.
4. `record_source_failure` passes `backlog=backlog` to `_controller_mutation`.
5. Unit and regression tests added in `services/trade_journey/test_lifecycle_projector.py`.
