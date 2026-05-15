---
task_id: P0-LIVE-GUARD-001
reviewer: Claude
reviewed_at: 2026-05-01
verdict: approved
---

# Review: P0-LIVE-GUARD-001 — Assert live fail-closed and bracket logged-only honesty

## Acceptance Criteria

### AC-1: live role cannot broker connect or place order without activation ✅

`runtime_bootstrap.py` — `_activation_guard_state()` always returns:
- `health_only: True`
- `activation_status: "not_activated"`
- `live_broker_enabled: False` (hard-coded regardless of `PANTHEON_LIVE_BROKER_ENABLED`)
- `broker_connect_allowed: False`
- `order_placement_allowed: False`
- `bracket_order_submission_allowed: False`

`_SidecarHandler.do_POST()` returns HTTP 403 + blocked payload for all `_BROKER_ACTION_PATHS`.
The guard correctly captures `requested_live_broker_enabled` from env for audit but never activates the broker.

Verified by:
- `test_live_sidecar_health_reports_not_activated` — health payload checks all guard fields
- `test_live_sidecar_blocks_broker_connect_and_order_posts` — confirms 403 on `/api/broker/connect` and `/api/orders` even when `PANTHEON_LIVE_BROKER_ENABLED=true`

### AC-2: bracket_order_logged is not treated as broker submitted order ✅

`executor.py`:
- `BRACKET_ORDER_STATUS_LOGGED_ONLY = "logged_only"` constant prevents magic strings
- `_record_bracket_order_logged()` always calls with `submitted_to_broker=False`; no `LimitOrder`/`StopMarketOrder` call in the bracket path
- Uses `getattr(algo, "RecordBracketOrderLogged", None)` duck-typing — no-op if running under full LEAN

`paper_runtime.py`:
- `OrderEvent` carries `submitted_to_broker: bool = False` and `broker_submission_status: str | None = None`
- `RecordBracketOrderLogged()` records `submitted_to_broker=False`, `broker_submission_status="logged_only"`, and signal metadata
- `_handle_order_event()` propagates `submitted_to_broker=False` into telemetry metadata

Verified by:
- `test_bracket_order_event_is_logged_only_not_broker_submitted` — checks state snapshot and telemetry events

## Documentation Review

**SD-P0-02** changes are accurate and narrow:
- §2 Current Facts: live placeholder now explicitly states `activation_status=not_activated`
- §2 Current Facts: bracket gap wording updated to `logged_only audit evidence only and is not submitted_to_broker`
- INV-BOOT-004 and INV-BOOT-010: strengthened invariant wording
- §10.3 live role: `activation_status=not_activated` added
- §12.2: two new integration test entries correctly reflect what's implemented
- AC-BOOT-003 and AC-BOOT-007: tightened to match actual implementation behavior

**SA-20** risk register: R-EXE-001 (live health-only fail-closed) and R-EXE-003 (bracket log-only) are already present with correct acceptance language. Document consistent with implementation.

## Test Run

```
python3 -m pytest services/execution/lean_runtime/test_runtime_bootstrap.py services/execution/lean_runtime/test_executor.py services/execution/lean_runtime/test_paper_runtime.py -q
→ 6 passed

python3 -m pytest services/execution/lean_runtime -q
→ 35 passed

git diff --check (scoped files)
→ clean
```

## Verdict

**Approved.** Both acceptance criteria met. Docs precise and consistent. Tests cover the key fail-closed invariants. Returned to Codex for closeout.
