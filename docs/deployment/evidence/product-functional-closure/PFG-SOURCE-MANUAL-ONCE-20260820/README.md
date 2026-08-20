# PFG-SOURCE-MANUAL-ONCE-20260820 Evidence

## Overview

This task delivers safe bounded manual one-tick Source Ingestion pulls for dev acceptance while locking the default dev posture to zero-egress internal reconciliation.

1. **Reconcile-Only Default Posture & Compose Defaults**:
   `services/source_ingestion/controller_worker.py` and `docker-compose.yml` (`source-ingest-scheduler`) default to `RECONCILE_ONLY_MODE` (`SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`) with `scheduled_tick` truth, `MAX_TICKS=0`, and `unless-stopped` restart. Internal connector requirements and state are continuously reconciled with **zero provider egress** (`provider_egress_attempted: false`). Default docker-compose startup runs zero-egress internal reconciliation cleanly without requiring provider connector selection.

2. **PR #5064 Candidate Reuse**:
   Reuses and consolidates the candidate architecture from PR #5064 (`OPS-DEV-SOURCE-MANUAL-PULL-20260820-V2`), proving bounded one-shot pull capabilities and fail-closed validation.

3. **Explicit Bounded One-Shot Execution & Required Connector Selection**:
   Added `controller_worker.py:run_controller_once()` and updated the CLI entrypoint `scripts/source_ingest_scheduler_once.py` to strictly require explicit allowlisted connector selection in `reconcile_and_pull` mode, running exactly one bounded tick (`max_ticks=1`), validating terminal readback, and exiting cleanly with code `0` (success) or `1` (failure). Empty connector sets in `reconcile_and_pull` mode are rejected at both CLI and controller layers.

4. **Cross-Process Serialization, Request Fingerprint & Replay Deduplication**:
   Invocations serialize across processes using an exclusive file lock on `<state_path>.lock`. When an `operation_key` is supplied, operations are bound to a canonical request fingerprint (SHA256 of normalized request parameters). Replayed executions with matching fingerprints return terminal readback without re-triggering provider egress, while mismatched parameters raise a fatal `operation_key_conflict` error. Multi-process concurrent tests prove that concurrent executions on the same key execute exactly one provider tick while sibling processes receive deduplicated replay readback. Neither failure nor completion leaves recurring processes, background daemons, or polling loops.

5. **Code Disposition & Simplification**:
   Audited legacy scheduler utilities and consolidated the retained one-shot CLI entrypoint onto `controller_worker.py`.

See `evidence.json` and `code-disposition.json` for full audit trails, test commands, and reviewer handoff.
