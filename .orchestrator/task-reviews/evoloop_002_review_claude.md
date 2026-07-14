# Review: EVOLOOP-002 — Real performance telemetry supply (PnL + drawdown)

Reviewer: Claude
Owner: Codex2
Task: EVOLOOP-002
PR: #3622 (ajoe734/pantheon), merged into `dev` at `9f292de3a627b72441a12b478ef307119fa2c9ba` (task branch HEAD `2a3936190`)
Outcome: APPROVED

## Scope

~5,232 additions / 88 deletions across 27 files. Core valuation and
diagnostic logic in `services/execution/lean_runtime/performance_telemetry.py`
(new, +758), wiring/ledger persistence in `paper_runtime.py` (+1294/-50),
projector changes in `services/telemetry/runtime_summary.py` (+320/-8), plus
matching test suites. Reviewed against the written contract in
`docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-002-performance-telemetry.md`.

## Findings

Verified against each contract point (file:line references from the checked-out
`2a3936190` state):

1. **Valuation boundary** — `value_portfolio` (`performance_telemetry.py:483-633`)
   values strictly from the current `provider_marks` resolve-cycle dict
   (`paper_runtime.py:2501-2515`), never from `Security.Price`. Any missing,
   non-market, stale, future, or pre-ledger mark short-circuits to a
   diagnostic with no snapshot (lines 527-598). Flat book correctly bypasses
   the mark requirement, using `last_fill_at` as `as_of` (600-612).
2. **No synthetic-price leakage** — `SetSecurityPrice` defaults
   `authoritative=False`; only `SetSecurityMark` (fed exclusively by
   `provider_marks`, or by `executor.py`'s `_seed_signal_market_price` when
   `metadata.market_data` carries its own price+as_of+source) sets
   `MarkAuthoritative=True`. `value_portfolio` never reads
   `Security.Price`/`MarkAuthoritative` (paper_runtime.py:953-974,
   executor.py:389-435).
3. **Drawdown math** (`performance_telemetry.py:636-758`) — 20-day window is
   inclusive, `peak <= 0` raises and the caller rolls the tracker back to a
   pre-call checkpoint and emits `invalid_drawdown_series` with no snapshot
   (paper_runtime.py:2524-2541) — genuinely fail-closed, not clamped.
4. **Atomic pair durability** (`paper_runtime.py:2572-2647, 1129-1187,
   1426-1487`) — pair is staged (fsync temp file + `os.replace` + directory
   fsync) before either POST; retry resends the stored `leg["payload"]`
   verbatim; ack-persist failure reverts via a pre-mutation deep copy so a
   retry resends the same `event_id`; drawdown window commits only after
   both legs ack.
5. **Ledger persistence / binding scoping** — non-JSON, non-UUID id,
   binding-mismatch, out-of-range drawdown, and wrong-primary-metric-set
   states are all rejected (1027-1127). `paper_fleet_reconciler.py` derives a
   sha256-suffixed, binding-specific state filename so a rollover can't reuse
   a stale ledger under a fixed service path.
6. **Runtime-summary projector** (`services/telemetry/runtime_summary.py:328-365,
   422-480`) — independent `pnl_at`/`drawdown_at` regression rejection per
   metric; `_retired_binding_ids` tombstones a replaced binding so a delayed
   event can't reclaim a newer generation.
7. **Threshold sweep** — reads real `pnl`/`drawdown_pct` with per-field
   staleness checks, not treating fresh values as missing.

No correctness bugs found. No path identified where a synthetic/internal
price could masquerade as a market mark, and no edge case (zero open
positions, partial mark set, non-positive peak) that bypasses fail-closed
behavior.

## Test verification

Ran on the checked-out task-branch state (`2a3936190`):

- `test_performance_telemetry.py` + `test_paper_runtime.py` +
  `test_runtime_summary_projection.py`: 89 passed, 21 subtests passed.
- Broader sweep (`services/execution/lean_runtime/`,
  `test_threshold_sweep_worker.py`, `test_paper_fleet_reconciler.py`,
  `services/telemetry/`): 568 passed, 3 skipped, 28 subtests passed, 0
  failures. One pre-existing, unrelated collection error in
  `test_lineage_write_path.py` (`ModuleNotFoundError: runtime_manager_client`)
  — confirmed via `git diff` that this file is untouched by PR #3622.

## Residual / hosted items (not blocking this review)

The task doc's own residual table is accurate and unchanged by this review:
hosted moving-metric proof, hosted missing-mark proof, deploy-identity
evidence, threshold governance enable (`EVOLOOP-005`), and the durable
telemetry receipt gap are explicitly out of scope for EVOLOOP-002 and remain
open follow-ups owned elsewhere.

## Process note

This task is not present in `ai-status.json` (live state only tracks 6 tasks
currently; EVOLOOP-002 exists solely via this task-brief file and the PR
#3622 commit trailers). `ai_status.py approve`/`note EVOLOOP-002` therefore
fail with `Unknown task: EVOLOOP-002` even before considering that `approve`
was also blocked by the harness's self-approval classifier (reviewer identity
`Claude` matches the acting agent). Recording this review as a committed
artifact instead, per the same pattern used for other untracked task
reviews in this directory.

**Recommendation:** APPROVED on the merits. A human (or an agent with a
distinct, non-self identity) should perform the formal `ai-status.sh approve`
/ owner finalization step if/when this task is registered in `ai-status.json`.
