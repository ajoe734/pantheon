# Redispatch blocker — 2026-08-08

This record supplements the 2026-08-02 NO-GO preflight evidence. It does not
replace that evidence or claim a live rollout.

## Current coordination result

The mutable-incumbent bootstrap repair is now canonically complete: its exact
Human/Ops-reviewed PR #4524 merged as
`1d0355768e4e66984ec3f1d6daab06f66c6ef7ad`. The later pycache promotion
hardening is also canonically complete: its reviewed PR #4629 merged as
`619acd04184e8d3fc3aef322d160e7c9106670ad`.

However, the canonical task row for
`SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808` is currently `blocked` and
assigns the same owner, Codex2. Its task-scoped record says that promotion of
`619acd04184e8d3fc3aef322d160e7c9106670ad` already aborted before config or
PID mutation, and that its required signed source-only follow-up packet remains
in supervisor bridge processing without a receipt or canonical task
materialization.

That task owns the current candidate rollout and its bridge recovery. Retrying
promotion from this V9 task would duplicate the active governed scope and would
contradict its explicit no-manual-retry containment. No config edit, process
signal, sync, candidate cleanup, cache probe, quota fallback dispatch, or
rollback operation was performed during this redispatch.

## Required resolution before V9 can continue

Human/Ops must obtain a supervisor receipt and canonical task materialization
for the successor follow-up, then resolve the active rollout task through its
own governed path. Before any later V9 evidence PR, reconcile the canonical
reviewer assignment (currently Codex) with this task's immutable packet and
acceptance requirement for an exact-head Human/Ops gate. The discrepancy is
recorded here; no reviewer policy was changed by this task.

## Redispatch update — 2026-08-10

The supervisor dispatched V10 rollout verification and this V9 task into
separate active Codex slots. V10 retained the only promotion authority. V9
observed the active lease and did not issue a duplicate sync, promotion,
signal, provider probe, or dispatch.

V10 selected accepted `dev` candidate
`6607b6a706b59670009965375e0a5dd6b5824fcf` at tree
`5eb3d3a18ba01bb2e2f53e442842aa7c86fec23c`. Independent readback found its
Git status empty and its filesystem free of `__pycache__`, `.pyc`, and `.pyo`.
The transaction nevertheless aborted before baseline, config, signal, launch,
or rollback because the immutable incumbent `5877b644...` contains three
historical cache directories and 36 bytecode files.

Durable external transaction evidence:

```text
/home/lupin/pantheon-ci-deploy/runtime/promotion-evidence/
supervisor-runtime-promotion-20260810T140901039717Z-636096.json
SHA-256: bbd2b6f09587225682b5ac90070a49f14f72eb230cde154c6ae9f2943fe377ec
```

Post-abort PID `2272245` remained alive at start ticks `16301949`, cwd/runtime
`5877b64425c8d6aede147d6cbbc6fbb9e228c259`, with live-config SHA-256
`8168c57646339d510499dafa7f02f5f7a7aa7f24c2d05e23c68e698f6dc6662e`.

V10 anchored a new source-only packet,
`SUP-RUNTIME-V10-IMMUTABLE-INCUMBENT-PYCACHE-RESIDUE-20260810`, to add only a
provenance-bound capture boundary and a separately materialized clean rollback
checkout. The active incumbent must not be cleaned, reset, edited, copied as a
rollback source, or manually retried. V9 remains blocked until that source task
is admitted, exact-head reviewed, merged, archived, and a later governed V10
rollout succeeds.

The reviewer discrepancy also remains: canonical state now assigns Codex2,
but the immutable V9 acceptance requires an exact-head Human/Ops review. Chair
or Human/Ops must reconcile that gate before any completion review; V9 does not
change reviewer policy itself.

## Redispatch update — 2026-08-10 (second pass, owner reassigned to Claude)

Chair reassigned this task's owner from Codex to Claude; canonical reviewer
remains Codex2. This pass revalidated the 2026-08-10 blocker instead of
re-reporting it unchanged.

`SUP-RUNTIME-V10-IMMUTABLE-INCUMBENT-PYCACHE-RESIDUE-20260810` and
`SUP-RUNTIME-V10-IMMUTABLE-INCUMBENT-LEGACY-RESIDUE-20260810` are now both
merged and canonically archived (PR #4718 and PR #4716; `dev` tip
`0c34a0da0` contains both). That prior source-only blocker is resolved.

However, the chain that unblocks V9 is not yet complete:

- `SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810` is still
  `review` (owner Antigravity2, reviewer Codex2), PR #4724, head
  `d69c3e66e543c8d290c792d07c65d843ea0cda95`, `OPEN`/`MERGEABLE`, no
  `reviewDecision` recorded yet.
- `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808` is `in_progress`
  (owner Codex, reviewer Claude) and holds the only active promotion
  lease/authority. V9 remains an observer.

A read-only `promote_supervisor_runtime.py --discover-only --json` check
was run against the dev-root checkout only (not any candidate
command-runtime clone), to avoid duplicating V10's active candidate-rooted
preflight while it holds the lease. It correctly failed
`candidate_runtime_identity_immutable` because `/home/lupin/pantheon` is
not a child of `/home/lupin/pantheon-ci-deploy/command-runtimes` -- this
confirms no accidental candidate binding, and no signal, launch, config
edit, or mutation occurred.

V9 stays blocked until: the split-entrypoint PR is exact-head reviewed and
merged, `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808` completes its
governed rollout (or hands back the lease), and the unreconciled
Codex2-vs-Human/Ops final review-gate discrepancy is resolved by Chair or
Human/Ops.

## Redispatch update — 2026-08-11 (owned_ready_dispatch)

The split-entrypoint PR (#4724) is now `done` and canonically archived; its
head `d69c3e66e543c8d290c792d07c65d843ea0cda95` is confirmed an ancestor of
`origin/dev` tip `8b7624999`. That prior source-only blocker is resolved.

`SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808` is still `in_progress`
(owner Codex, reviewer Claude) and its `next` field, updated
2026-08-11T01:51:12Z, reads: "PR #4737 source repair is merged into dev;
starting the separately dispatched transactional rollout retry through
sync-dev-root only." It is actively mid-transaction right now and remains
the only task holding promotion authority.

V9 ran only a read-only `promote_supervisor_runtime.py --discover-only
--json` against this task worktree checkout to confirm no accidental
candidate/incumbent binding; no config edit, process signal, sync,
candidate cleanup, cache probe, quota fallback dispatch, or rollback
operation was performed.

V9 remains blocked until `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808`
completes its in-flight rollout retry or releases the lease, and until
Chair/Human/Ops reconciles the Codex2-vs-Human/Ops final review-gate
discrepancy.
