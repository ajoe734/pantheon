# MGMT-LOAD-005 - BFF Read Concurrency Isolation

Owner: Gemini
Reviewer: Claude2
Parent: `MGMT-GAP-010`
Depends on: `MGMT-LOAD-001`, `MGMT-LOAD-002`

## Problem

Concurrent shell startup reads can delay unrelated BFF routes. A fast isolated
Evidence request is not enough if `/health` and Evidence queue behind alert,
approval, job, or runtime aggregation work.

## Scope

- Add or harden per-route elapsed-time logging for management shell, Evidence,
  alerts, approvals, jobs, and health reads.
- Bound expensive synchronous read-store aggregation with cache, precomputed
  counters, threadpool isolation, or explicit timeout/degraded envelope.
- Keep `/health` independent from management read aggregation.
- Re-run the BFF fanout probe from `MGMT-LOAD-001` against the deployed dev BFF.

## Acceptance

- `/health` p95 <= 200 ms while shell summary and Evidence are concurrently
  requested in the dev probe.
- `/bff/management/evidence` p95 <= 750 ms during shell fanout, or blocker
  evidence identifies the exact backend read path that still queues.
- Expensive degraded paths return explicit degraded metadata instead of hanging
  unrelated routes.
- Tests cover timeout/degraded behavior and confirm `/health` does not depend on
  management read aggregation.

## 2026-07-01 Implementation Evidence

Task branch: `task/MGMT-LOAD-005`

Implemented in `services/control-plane/bff/main.py`:

- `_run_management_read()` offloads a synchronous read-store aggregation to a
  worker thread via `asyncio.to_thread` and bounds the wait with
  `asyncio.wait(..., timeout=budget)` (not `asyncio.wait_for`, since a real OS
  thread already running synchronous code cannot be cancelled - `asyncio.wait`
  lets the route return immediately at the budget instead of blocking for the
  full slow-call duration). The budget defaults to 0.6 s and is configurable
  via `PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS`.
- `/bff/management/evidence`, `/bff/alerts`, `/bff/approvals`, `/bff/jobs` all
  route their read-store aggregation through `_run_management_read` and
  return an explicit `meta.surfaces.<dataset>.status == "degraded"`,
  `reason == "read_timeout"` envelope (empty data) when the budget is
  exceeded, instead of hanging the caller.
- `/health` and `/bff/management/shell-summary` needed no thread offload:
  `/health` has no backing read at all, and shell-summary is already a sync
  FastAPI handler (MGMT-LOAD-002), which Starlette runs in its own external
  threadpool automatically - both got only elapsed-time logging via the new
  `_log_management_read_timing()` helper (`bff.management_read route=...
  elapsed_ms=... status=ok|timeout_degraded`).
- Abandoned worker threads (a read that finishes after its route already
  returned degraded) are not resurrected as a stale response; their result or
  exception is only logged via `_discard_late_management_read_result`.

Contract coverage:

- `services/control-plane/bff/test_mgmt_load_005_read_concurrency.py` (new,
  7 tests): `/health` stays fast while Evidence or jobs reads sleep 0.5 s
  behind it; evidence/alerts/approvals/jobs each return a degraded envelope
  near the timeout budget instead of hanging when their backing read is
  slower than the budget; the happy path is unchanged when reads are fast.

Validation run:

```text
python3 -m pytest services/control-plane/bff/test_mgmt_load_005_read_concurrency.py -q
7 passed, 4 warnings in 7.49s

python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py \
  services/control-plane/bff/test_bff_management_delta_routes.py \
  services/control-plane/bff/test_pkt012_alerts_rail_contract.py \
  services/control-plane/bff/test_bff_approvals_decide_contract.py \
  services/control-plane/bff/test_pkt006_approval_queue_contract.py -q
1 failed, 54 passed
```

The one failure
(`test_bff_management_delta_routes.py::test_governance_ledger_unifies_approval_intervention_and_override_sources`)
was confirmed pre-existing and unrelated: it fails identically with
`main.py` reverted to the pre-MGMT-LOAD-005 commit (`d1390c5e3`), before any
change in this task, and is unrelated to read-concurrency/timeout behavior
(a governance-ledger interventions-composition assertion). Not fixed here;
out of this task's scope.

Local before/after evidence (not hosted): see
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-local-before-after-2026-07-01.md`
and the accompanying `.json`. Summary: with a synthetic 400 ms slow backend
read on evidence/alerts/approvals/jobs, the pre-fix shape (inline synchronous
read) produced `/health` p95 1629 ms (matching the `MGMT-LOAD-001` hosted
baseline's `/health` p95 1328 ms under the same fanout); the current fix
produces `/health` p95 189 ms (meets the `<= 200 ms` bound) with
evidence/alerts/approvals/jobs p95 425-591 ms (meets the `<= 750 ms` Evidence
bound).

Hosted dev BFF evidence was not produced in this worker: no
`PANTHEON_BFF_ACCESS_TOKEN` / dev BFF credential was configured (same gap
`MGMT-LOAD-002` recorded), and the hosted BFF at
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` still serves `dev`
without this fix until this branch merges, so re-running the
`MGMT-LOAD-001` fanout probe against it now would only reproduce the known
baseline, not validate the fix. The hosted post-merge fanout probe re-run is
a residual item for `MGMT-LOAD-007`, which explicitly closes `MGMT-GAP-010`
with "merged PR, deployed FE/BFF, hosted probe, and residual-risk evidence"
per `INDEX.md`.

## 2026-07-01 Owner Closeout

PR #2682 already merged to `dev`; this task branch HEAD is confirmed an
ancestor of `origin/dev` (no further code change required for this task).
Owner re-verified the approved scope is still true in the worktree:

```text
python3 -m pytest services/control-plane/bff/test_mgmt_load_005_read_concurrency.py \
  services/control-plane/bff/test_mgmt_load_002_shell_summary.py -q
12 passed, 8 warnings
```

Residual hosted post-merge fanout probe remains explicitly deferred to
`MGMT-LOAD-007` per reviewer note; not a blocker for this task's `done`
transition.
