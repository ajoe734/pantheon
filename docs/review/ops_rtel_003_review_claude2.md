# Review: OPS-RTEL-003 — Paper Monitoring Session Stale Reaper

Reviewer: Claude2
Date: 2026-06-06
Status: **APPROVED**

## Scope Verified

The implementation correctly addresses the stated goal: `paper_runtime_monitoring`
sessions are now owned and managed by the fleet reconciler rather than inferred
from `ended_at == null`.

## Implementation Assessment

### `paper_fleet_reconciler.py`

**Session lifecycle — CORRECT**

- `_open_monitoring_session()` supersedes any open session for the same binding
  before opening a new one. Zombie sessions from a crashed reconciler instance
  cannot accumulate silently.
- `_end_monitoring_session()` is idempotent: checks `_monitoring_session_open()`
  before mutating, so double-close is a no-op.
- `_monitoring_staleness()` handles both cases: missing heartbeat entirely (grace
  period anchored to `started_at`) and stale heartbeat. The `started_at` fallback
  gives new workers the full `stale_after_seconds` grace period before the first
  heartbeat is required.
- `_reap_stale_monitoring_sessions()` terminates the tracked worker after closing
  a stale session; the next reconcile cycle restores it with a fresh session.

**Persistence — CORRECT**

- Atomic write via `os.replace(tmp, dest)` prevents corrupt JSON on crash.
- `_persist_monitoring_sessions` is called both inline from `_end_monitoring_session`
  and via the `changed` flag for heartbeat-only updates. The `changed = False`
  reset after `_end_monitoring_session` is correct: the persist inside that
  method already captures any pending in-memory heartbeat mutations (since it
  iterates `self._monitoring_sessions.values()` globally).

**Thread safety — CORRECT**

- `self._lock` is `threading.RLock()`. `_reap_stale_monitoring_sessions` is
  called inside `reconcile_once()`'s `with self._lock:` block. `_terminate_worker`
  (called from the reaper) re-enters the same RLock. No deadlock.

**Degraded-fetch safety — CORRECT (from OPS-RTEL-002, preserved)**

- `None` return from `_fetch_active_paper_bindings` skips start/stop reconcile
  but process exit polling still runs. Stale-session reaping also skips when
  summaries are None, preventing false positives during network outages.

**Minor observation (non-blocking):**
- `_fetch_runtime_summaries` does not send auth headers. This appears intentional
  for an internal read-only telemetry endpoint. If the telemetry service later
  requires auth, a `PANTHEON_TELEMETRY_API_TOKEN` env var will need to be wired.

### `read_store.py` — BFF Read Layer

**CORRECT**

- Both `_DATASETS` (file-backed) and `_HTTP_DATASETS` (fleet reconciler endpoint)
  entries are correctly configured.
- `get_paper_runtime_monitoring_session()` prefers active sessions when multiple
  matches exist, then falls back to the most-recent session. Stable and useful for
  display.
- `_paper_runtime_monitoring_session_active()` checks `ended_at`, explicit `active`
  flag, `status` string, and `staleness` dict — robust against partial or legacy
  session shapes.
- BFF has no write path to monitoring sessions. Read-only boundary upheld.

### `main.py` — BFF Projection

**CORRECT**

- `_project_runtime_state_monitoring_session()` projects only declared fields,
  preventing schema bleed from internal reconciler state.
- `paper_runtime_monitoring` surface status is properly propagated as degraded
  when runtime rows exist but monitoring evidence is missing.
- `_derive_runtime_state_last_updated_at` incorporates monitoring session
  timestamps, so the board reflects the most recent known event even when
  telemetry is unavailable.

### Test Coverage

**25 tests in `test_paper_fleet_reconciler.py` (verified count matches):**

- `test_worker_start_opens_monitoring_session` — session opened on spawn ✓
- `test_stale_heartbeat_ends_session_and_restarts_worker` — core stale reaper ✓
- `test_stale_persisted_zombie_session_is_closed_on_restart` — persisted zombie
  closed on reconciler start ✓
- Remaining 22 tests from OPS-RTEL-002 preserved and passing ✓

**4 tests in `test_pkt010_runtime_state_board_contract.py`:**

- Full contract payload including `paper_runtime_monitoring` ✓
- Unavailable surface propagation ✓
- Pagination/filtering ✓
- Live HTTP path via `_http_json_get` monkeypatch ✓

## Acceptance Criteria Check

| Criterion | Verdict |
|---|---|
| Stale monitoring sessions are automatically ended | PASS — `_reap_stale_monitoring_sessions` closes sessions exceeding threshold |
| Restarted workers create fresh sessions | PASS — `_open_monitoring_session` supersedes open sessions before creating new |
| BFF surfaces session staleness and terminal reason | PASS — `staleness` and `ended_reason` projected onto runtime state board |
| `ended_at == null` not treated as liveness proof | PASS — `_paper_runtime_monitoring_session_active` checks multiple signals |

## Verdict

**APPROVED.** All four acceptance criteria pass. Implementation is correct,
test coverage is targeted and sufficient, BFF write boundary is upheld.
No blocking issues.
