# PFG-SOURCE-MANUAL-ONCE-20260820 Evidence

## Overview

This task delivers safe bounded manual one-tick Source Ingestion pulls for dev acceptance while locking the default dev posture to zero-egress internal reconciliation.

1. **Reconcile-Only Default Posture**:
   `services/source_ingestion/controller_worker.py` defaults to `RECONCILE_ONLY_MODE` (`SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`) with `scheduled_tick` truth and `MAX_TICKS=0`. Internal connector requirements and state are continuously reconciled with **zero provider egress** (`provider_egress_attempted: false`).

2. **PR #5064 Candidate Reuse**:
   Reuses and consolidates the candidate architecture from PR #5064 (`OPS-DEV-SOURCE-MANUAL-PULL-20260820-V2`), proving bounded one-shot pull capabilities and fail-closed validation.

3. **Explicit Bounded One-Shot Execution**:
   Added `controller_worker.py:run_controller_once()` and updated the CLI entrypoint `scripts/source_ingest_scheduler_once.py` to support selecting allowlisted connector IDs, running exactly one bounded tick (`max_ticks=1`), validating terminal readback, and exiting cleanly with code `0` (success) or `1` (failure).

4. **Boundedness & Idempotency**:
   Neither failure nor completion leaves recurring processes, background daemons, or polling loops. Replayed invocations update `ControllerStateStore` monotonically with verified checksum integrity.

5. **Code Disposition & Simplification**:
   Audited legacy scheduler utilities and consolidated the retained one-shot CLI entrypoint onto `controller_worker.py`.

See `evidence.json` and `code-disposition.json` for full audit trails, test commands, and reviewer handoff.
