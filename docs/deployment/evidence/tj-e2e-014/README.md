# TJ-E2E-014: First-class Trade Journey producer in the paper runtime

Task: TJ-E2E-014 — First-class Trade Journey producer in the paper runtime
Owner: Antigravity
Reviewer: Claude
Date: 2026-07-13

## Delivered Behavior

This task implements direct generation of Trade Journey events from the paper runtime, replacing the legacy 5-minute telemetry cron bridge to achieve real-time visibility.

### Key Changes
1. **Paper Runtime Integration (`services/execution/lean_runtime/paper_runtime.py`)**:
   - Modified runtime to directly emit first-class Trade Journey events (Signal -> Trade Decision -> Order Submission -> Fill Management) inside the paper runtime execution loop.
   - Handled stage chaining by linking stages with a single `journey_id` derived from the original `signal_id` (e.g., `tj-<signal_id>`).
   - Standardized occurred timestamps across the decision, order, and fill stages of the journey.
2. **BFF Management API Hardening (`services/control-plane/bff/trade_journeys.py`)**:
   - Secured `/bff/management/trade-journeys/events` with tenant and role check validations.
   - Enforced timezone-aware ISO format timestamp validations.
   - Implemented strict batch-duplicate checks and database-level duplicate conflicts check.
3. **Outbox/Bridge Convergence**:
   - Converged `backfill_trade_journeys_from_telemetry.py` to align with the new first-class journey event generation.

## Test Coverage

### 1. Paper Runtime Tests (`services/execution/lean_runtime/test_paper_runtime.py`)
- Verified that Trade Journey events are successfully generated and chained with correct `journey_id`, `signal_id`, and matching timestamps across decision, order, and fill stages.

### 2. BFF API Tests (`services/control-plane/bff/test_tj_e2e_005_trade_journeys_read_api.py`)
- Verified unauthorized request checking (401).
- Verified empty batch request checking (400).
- Verified validation failures for missing fields and non-timezone-aware timestamps (400).
- Verified conflicting duplicates in batch (400) and in store (409).
- Verified tenant-constrained access controls (403).

## Validation Run

Verified successfully on local workspace:
```bash
python3 -m unittest services/execution/lean_runtime/test_paper_runtime.py
# Ran 27 tests in 2.422s
# OK

python3 -m pytest services/control-plane/bff/test_tj_e2e_005_trade_journeys_read_api.py
# 29 passed, 4 warnings in 17.69s
```

## Delivery and PR details
- Ephemeral Branch: `task/TJ-E2E-014`
- Delivered via:
  - PR #3511 (merged to dev)
  - PR #3518 (merged to dev, merge commit `4e410f2cf`)
- Ancestry verification: HEAD is ancestor of `origin/dev` (fully merged).
