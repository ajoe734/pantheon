# SUP-L12 stale-reaper evidence-anchor repair V2

Task: `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731`

This packet records the superseding repair for the invalid implementation
anchor in PR #4385. It preserves the stale-reaper implementation history,
composes it with current `dev`, and changes the subject README and manifest
from nonexistent object
`9d53a94a265c55af4c8d15c50ab3751f1440ac0f` to the actual rebased anchor
`9d53a94a295d71ee49aea6f4b96e47fbcfd29093`.

## Why this is a superseding task

The failed requeue receipt
`.orchestrator/assistant-dev-packets/receipts/pkt-l12-wave0x-pipeline-blockers-requeue-20260731T1252Z.json`
records a non-retryable bridge assignment conflict. Prior task
`SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731` was already
immutable-bound to packet
`pkt-l12-wave0x-fleet-reconcile-fallout-20260731T1225Z`, digest
`24fcc3087b0aa6e1aa1d99cd1d03387f2f2fc59f36c1eab79314e5a8192986fc`,
and spec
`c5a998ac1677d802a3929d63c2d65f5bd60970060ade7d5356776dfac59d39a2`.
This V2 task therefore uses a new task identity instead of rewriting the
prior durable assignment.

## Verified source heads

- PR #4385 remained open at required head
  `f5e70e86e01bde005dae5fed94b151c9bc07f389`.
- PR #4395 had advanced from expected head
  `f68827c8e17d6a1f081afe24f62ba85c116166e8` to
  `edb1698aa6626d84039243d862dfdc33a8f87770` before this repair began.

Because the reconciliation head moved, this task follows the task contract's
equivalent-fix path. Its branch retains #4385's original commits as ancestors
and merges current `origin/dev` instead of mutating the old task branch.

## Repair boundary

Owned here:

- the three invalid anchor references in the subject README and manifest;
- durable receipt and head-drift evidence for the superseding task; and
- focused revalidation of the composed stale-reaper implementation.

Not changed here:

- `.orchestrator/config.json`;
- stale-reaper policy semantics;
- generic quota/auth or non-L12 failure handling; or
- the independent exact-head review and protected-merge gates.

Machine-readable proof and the independent review decision belong in
[`evidence.json`](evidence.json). Five focused regressions and the full 473-test
supervisor suite pass again after composing the latest `origin/dev` head;
independent exact-head review, protected merge, and governed closeout remain
pending.
