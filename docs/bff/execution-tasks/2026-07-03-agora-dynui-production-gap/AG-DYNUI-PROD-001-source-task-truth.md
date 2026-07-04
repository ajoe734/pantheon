# AG-DYNUI-PROD-001 - Restore Agora DYNUI Source And Task Truth

Owner: Codex
Reviewer: Claude
Depends on: none

## Problem

The original design-pack dynamic UI tasks were real, but the current clean
`origin/dev` task truth is not a reliable continuation point. Some PRs and task
briefs exist, while active `ai-status.json` and later archive snapshots do not
carry the DYNUI chain forward.

## Scope

- Reconcile the expected `AI Trading Desk Design.zip` with the design closure
  packs that are actually present.
- Reconcile frontend source/deploy truth: the canonical
  `/home/lupin/code/execute-plans` checkout has the shared-auth-header fix,
  while the nested `/home/lupin/code/pantheon/.fe-ep` checkout is dirty and can
  show stale Agora client code.
- Restore or recreate task archive snapshots for the DYNUI tasks that have
  merged evidence but missing archive truth.
- Produce a current source map that names the canonical V10/V11/V6/V4 design
  references, screenshots, live route evidence, and unresolved blockers.
- Record which old tasks are completed, superseded, or must be replaced by the
  `AG-DYNUI-PROD-*` production-gap tasks.

## Acceptance

- Canonical source/design location is recorded, or a blocker names the missing
  file exactly.
- Canonical frontend deploy source is recorded; stale nested checkout state is
  archived, removed from deploy paths, or assigned a cleanup task.
- Current task truth distinguishes completed PR evidence from incomplete hosted
  product behavior.
- Missing archive/task truth does not block downstream workers from starting.
- Closeout includes PR numbers, merge SHAs, and any unresolved design-source
  blocker.
