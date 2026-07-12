# AG-GAP-011: Reconcile nested FE checkouts; enforce canonical execute-plans

## Scope

Ops/hygiene task. The pantheon repo contains two nested execute-plans
checkouts that have diverged from each other and from canonical:

- `.fe-ep/` — branch `task/mgmt-gap-008-detail-honesty`, HEAD 2026-07-01,
  stale (placeholder strategy-performance page, old auth path).
- `.fe-human-inbox-persona-focus/` — branch
  `fix/persona-research-link-targets`, HEAD 2026-07-11.

This split already produced one phantom-done (AG-DYNUI-LIVE-WORKSHOP-009
patched the wrong tree). Canonical FE source is `ajoe734/execute-plans@dev`.

## Work

1. For each nested checkout: enumerate local branches/commits not present on
   `ajoe734/execute-plans` remotes; push or PR anything worth keeping.
2. After salvage, remove the stale checkout(s) or replace them with a single
   documented, regularly-synced checkout location if fleet workers need one.
3. Add/extend a guard so FE task briefs and workers reference the canonical
   repo path only (worker docs or scope-check level; align with the existing
   scope-check trailer machinery).
4. Document the canonical FE workflow in the packet evidence.

## Acceptance

- No orphaned FE work lost: salvage list recorded, each item pushed or
  explicitly discarded with a reason.
- At most one nested FE checkout remains, documented; stale trees deleted.
- A written rule (worker-facing doc) states FE changes land only via
  `ajoe734/execute-plans@dev`.

## References

- `.fe-ep/`, `.fe-human-inbox-persona-focus/`
- `ai-task-archive/tasks/AG-DYNUI-LIVE-WORKSHOP-009.json` (superseded: wrong repo)
- `docs/04/pantheon_agora_gap_assessment_2026-07-12/INDEX.md`
