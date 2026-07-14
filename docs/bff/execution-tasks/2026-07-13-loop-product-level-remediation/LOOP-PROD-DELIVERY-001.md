# LOOP-PROD-DELIVERY-001 — Fleet-only delivery provenance and independent review admission

Status: blocked until `LOOP-PROD-RUNTIME-BOOT-001` is done and the guarded
dispatcher materializes this task

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `5ff12f3d8e233020db524a892bbd5fe02bd690688ec25382694b5df230b3719b`
The complete catalog task contract is machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 0 |
| Fleet lane | `fleet-only-delivery-provenance` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | planner and worker identities can be conflated; delivery is not bound to canonical admission |
| Target maturity | proven-live |

## Product outcome

Planner 只能規劃、歸檔、派工、監控與審查。所有產品實作必須由 supervisor
admit 的 fleet worker 在 clean task worktree 執行，並由另一個 runtime identity
做正式 exact-head review。沒有 canonical task、run/scope/lease binding 或獨立審查
的 branch、PR、repair、revert、merge 與 deploy 一律 fail closed。

Next action: implement the provenance gate and replay the content-addressed PR
3557, 3587, 3588, and execute-plans 323 incident sequence before later
additive work is admitted.

The immutable incident catalog is
`fixtures/browser-auth-incidents.v1.json`, SHA-256
`71038929281e844b26a3d8ba6c48f167a94b9d6281183dbdf45f2627b549eb19`.

## Dependencies

- `LOOP-PROD-002`

Only `done` satisfies a dependency.

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `.github/workflows/task-delivery-provenance.yml`
- `.orchestrator/supervisor.py`
- `.orchestrator/test_supervisor.py`
- `scripts/validate_task_delivery_provenance.py`
- `scripts/test_validate_task_delivery_provenance.py`
- `docs/deployment/fleet-only-delivery-provenance.md`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-DELIVERY-001`

## Acceptance

- every implementation commit, branch, PR, review, merge, and deployment resolves to one canonical task and exact plan, packet, catalog, task contract, and execution-authority provenance
- planner writes are limited to planning, packet, dispatcher, monitoring, and review records; changed product artifacts from the planner identity are rejected
- worker admission binds exact task, run, provider, slot, clean worktree, declared scope, expected branch, remote, and merge target
- changed paths, commit trailers, PR head, candidate, and deployment remain on that exact binding; stale, foreign, planner-authored, and cross-scope heads fail closed
- owner and reviewer are distinct admitted runtime identities and the repository records a formal exact-head review; self-review, a same-session subagent note, or a trailer is insufficient
- one task/repair lease admits at most one active branch, PR, cutover, revert, or retry for the same intent; a semantically empty duplicate repair is rejected before merge
- missing brief, live/archive row, source reference, admission, formal review, or archive handoff blocks delivery instead of inventing an ad hoc Task-ID
- the exact fixture binds full PR commit graphs, trees, blobs, timestamps,
  deployment pair, artifact digests, and observed reason codes; mutable PR prose
  is not code truth
- PR 3557 and execute-plans PR 323 abort before split activation; PR 3587 is
  the one effective rollback; PR 3588 is rejected before merge/deploy as the
  same semantic repair with merge tree equal to its first-parent tree
- restart, concurrent enqueue, reassignment, supersede, stale head, forged trailer, duplicate PR, and direct-merge tests preserve one outcome and append-only audit truth

## Required proof

- exact task/run/provider/slot/worktree/scope/branch/remote/merge-target admission manifest
- changed-path and planner-nonimplementation negative evidence
- formal distinct-runtime-identity exact-head review evidence
- exact incident-fixture digest and PR 3557/3587/3588/323 replay matrix
- restart, reassignment, duplicate, stale-head, forged-trailer, and direct-merge evidence
- merged PR, merge SHA, protected checks, checksummed audit, and independent residual-risk verdict

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-DELIVERY-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- start only after every dependency is done; superseded does not satisfy a dependency
- the planner may author and dispatch this task contract but must not implement its declared product artifacts
- use one supervisor-admitted clean task worktree and a formal review from a distinct admitted fleet runtime identity
- open draft PRs, local diffs, and unmerged worktrees are non-authoritative inputs that the fleet may audit, adopt, rewrite, or discard
- no implementation may merge or deploy without exact canonical task, run, scope, lease, and review bindings
- fixture drift, abbreviated Git IDs, alternative status sets, or prose-only
  replay claims fail closed
