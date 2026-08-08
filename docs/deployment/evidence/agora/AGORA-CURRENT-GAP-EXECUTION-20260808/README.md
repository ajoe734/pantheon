# Agora current-gap admission readback

Task: `AGORA-CURRENT-GAP-EXECUTION-20260808`

Owner: Codex

Reviewer: Codex2

Status: **review pending; admission blocked**

Review manifest: `evidence.json`

## Outcome

The planned nine-task DAG is internally valid, but it is not the DAG that the
authoritative task store admitted. No packet was replayed and no canonical row
was overwritten during this reconciliation.

The preliminary packet
`pkt-agora-current-environment-gap-20260808-e765bcbed` was not merely left in
`processing`: the supervisor processed it at `2026-08-08T14:38:54Z`, wrote a
durable admission record, and materialized all seven of its signed task rows.
Those rows include the two now-obsolete broad tasks
`AGORA-L12-CROSS-LOOP-INTEGRATE-20260808` and
`AGORA-L12-REAL-VERIFIER-20260808`.

The final packet
`pkt-agora-current-environment-gap-final-20260808-2d1f16b1a` failed at
`2026-08-08T14:40:14Z`. It found five same-ID hash conflicts and materialized
four previously absent rows, but the all-or-nothing admission record was not
written. The four exact rows therefore have signed bridge metadata but no
durable packet admission record and remain fail closed for supervisor
dispatch.

The five conflicts are:

- `AGORA-CURRENT-GAP-EXECUTION-20260808`
- `AGORA-DEV-DEPLOY-RECOVERY-20260808`
- `AGORA-CURRENT-HOSTED-REACCEPT-20260808`
- `AGORA-UI-LIFECYCLE-RECONCILE-20260808`
- `AGORA-CURRENT-CLOSE-20260808`

The four exact final-packet rows are:

- `AGORA-IMIT-HANDOFF-CONSUME-20260808`
- `IMIT-CONSULTATION-INTAKE-20260808`
- `AGORA-LEARNING-CROSS-LOOP-BIND-20260808`
- `L12-VERIFY-LEARN-REAL-VERIFIER-001`

## Why the gate remains blocked

The desired task file validates as an acyclic nine-task DAG with every node
reachable from the admission task and one final sink,
`AGORA-CURRENT-CLOSE-20260808`. The authoritative rows do not match it:

- the current hosted task still depends on obsolete
  `AGORA-L12-REAL-VERIFIER-20260808`, not
  `L12-VERIFY-LEARN-REAL-VERIFIER-001`;
- the split handoff, intake, binding, and real-verifier chain terminates at a
  second sink because the current hosted row does not consume it;
- the obsolete broad integration row overlaps the new split component scopes
  without the final packet's intended ordering; and
- the five same-ID specs cannot be replaced through ordinary bridge
  materialization because bridge packet, digest, and task-spec hash bindings
  are immutable.

The preliminary downstream rows and the four stranded final rows all still
depend, directly or transitively, on this admission task. Keeping this task
blocked prevents either incomplete graph from becoming implementation
authority.

## Collision snapshot

The bounded audit recorded that the seven new addendum IDs were absent before
the bridge packets ran. The fresh post-receipt snapshot at
`2026-08-08T14:50:08Z` found all seven active because the two packet attempts
had already materialized them. It found:

- no archived task snapshot for any final or obsolete addendum ID;
- no downstream implementation PR;
- no downstream remote task branch;
- no downstream worker worktree; and
- only coordination PR #4628, its task branch, and this task worktree.

This is a one-time admission readback, not an auto-worker progress poll.

## Required governed remediation

No ordinary retry is safe. A follow-up governed reconciliation must preserve
both receipts and choose one supported immutable path:

1. retire the two obsolete preliminary task IDs and the five conflicting
   revisions without deleting their audit history;
2. either admit uniquely versioned successor IDs for the conflicting specs or
   add a reviewed supervisor transaction that can complete a partial packet
   admission without rewriting existing bridge provenance;
3. prove the resulting canonical graph has one sink and that hosted
   reacceptance consumes the real Learning verifier; and
4. repeat exact-head independent review before this A0 gate can become done.

No product, deployment, queue, canonical JSON, orchestrator configuration, or
live runtime mutation is part of this evidence task.

## Validation

The exact commands and structured results are recorded in `evidence.json`.
They cover the current `origin/dev` and command-runtime identity, both packet
receipts, authoritative task hashes and assignments, active/archive/PR/branch/
worktree collision checks, DAG validation, JSON parsing, and `git diff
--check`.

