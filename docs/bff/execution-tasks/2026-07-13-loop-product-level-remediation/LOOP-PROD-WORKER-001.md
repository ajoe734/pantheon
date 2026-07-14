# LOOP-PROD-WORKER-001 — Exact-CAS worker outcome and forced termination integrity

Status: ready for fleet dispatch after the additive packet is merged

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `35bb10ca29c738081e4c79973af86dc5db29fd5b9395820fc6bc331721c2018f`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 1 |
| Fleet lane | `worker-outcome-integrity` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | partial guards with reproduced restart and post-launch races |
| Target maturity | proven-live |

## Product outcome

任何 worker 只有在 task、event、owner、reviewer、run、attempt 與 payload
signature 全部仍相符時才能 launch、retry、resume 或寫 terminal outcome；失去
admission 的 process group 與 file-inbox payload 必須被確認清除。

Next action: close the exact-head adversarial findings in the worker outcome
guard and prove restart-safe RPO=0 behavior.

## Dependencies

- `LOOP-PROD-001`
- `LOOP-PROD-002`
- `LOOP-PROD-DELIVERY-001`

Only `done` satisfies a dependency.

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `.gitignore`
- `.orchestrator/adapters/file_inbox.py`
- `.orchestrator/dispatch_policy.py`
- `.orchestrator/runtime_state.py`
- `.orchestrator/supervisor.py`
- `.orchestrator/templates/wakeup.txt`
- `.orchestrator/test_runtime_state.py`
- `.orchestrator/test_supervisor.py`
- `.orchestrator/test_watch_events.py`
- `.orchestrator/watch_events.py`
- `scripts/ai_status.py`
- `scripts/planning_state.py`
- `scripts/test_ai_status.py`
- `scripts/test_planning_state.py`
- `scripts/test_supervisor.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-WORKER-001`

## Acceptance

- boot, poll, self-claim, planning materialization, approval resume, retry, and final outcome share one exact admission/CAS invariant
- task-state and runtime-state whole-file RMW paths use compatible shared locks and cannot stale-overwrite a newer guard, assignment, worker, or failure streak
- a started same-run recovery is re-admitted after restart instead of being skipped by equality shortcuts
- supersede/reassign/done after launch terminates the complete process group, escalates after grace, waits for zero members, and deletes file-inbox payloads before terminal publication
- temporary approval permission cleanup is durable and two-phase: the attempt becomes inactive first, cleanup-pending survives restart, and no terminal/retired marker precedes authoritative permission removal
- approval resume captures the admitted identity before spawn, persists the process-group binding immediately after spawn, and proves that exact group dead after final CAS failure before retirement
- terminal workers remain recoverable cleanup records until payload, permission, queue reservation, process group, and runtime admission cleanup are confirmed exactly once
- watchdog/safe-mode recovery performs fresh locked RMW and cannot overwrite newer workers, queue, ownership, reviewer, failure, or quota truth
- auth/provider pause and retry mutations occur only after final admission; stale attempts cannot leave quota or blocked-until state
- malformed retry snapshots and corrupt failure timestamps are quarantined with an observable reason and cannot crash or permanently bias the supervisor cycle
- deterministic interleavings, SIGTERM-ignoring children, restart, duplicate, and corrupt-state tests pass
- exact PR, merge SHA, checks, reviewer verdict, and checksummed evidence are archived
- PR `#3554` and its local worktree are non-authoritative inputs; the admitted fleet audits the exact head and may adopt, rewrite, or discard it

## Required proof

- focused and full supervisor/runtime-state validation
- deterministic race and restart fixtures
- process-group and payload zero-member evidence
- merged PR and merge SHA
- independent exact-head review and residual-risk owner/expiry

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-WORKER-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- planner does not implement these artifacts; one admitted fleet runtime owns implementation and a different admitted runtime owns formal review
- PR `#3554` and local worker-guard diffs are input only, never acceptance or independent-review proof
- use a clean task worktree and never commit generated runtime state
- preserve unrelated live supervisor state and restore test-generated tracked artifacts
- do not close from unit tests alone; exercise real subprocess termination and restart
- reviewer verifies every reproduced finding against the exact proposed head
