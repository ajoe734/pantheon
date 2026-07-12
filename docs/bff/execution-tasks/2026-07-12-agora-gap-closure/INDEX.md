# Agora Gap Closure - 2026-07-12

Status: execution packet for fleet supervision.

Source assessment: `docs/04/pantheon_agora_gap_assessment_2026-07-12/INDEX.md`.
This packet converts the 2026-07-12 implementation-vs-documentation audit into
execution tasks. It follows the production rules established by the 2026-07-05
full production recovery packet and the 2026-07-06 inventory packet.

## Production-Level Rule

No item is complete because it works locally, exists in a stale board, or was
assigned to an exhausted worker. Completion requires clean branch/worktree,
validation, staged intended files only, commit trailers, push, PR, green
required checks, merge, deploy when runtime changes are made, and recorded
live proof for user-visible behavior. Fixture-backed E2E (`page.route`,
mock BFF) never counts as production proof.

## Context Snapshot (2026-07-12)

- Three Agora tabs pass the fixture-free hosted gate (AG-DYNUI-LIVE-TABS-GATE-011).
- Backend persistence is dev-grade: only strategy_workshop has a Postgres store
  (default off); trading_room / research / dashboard are in-memory; legacy
  main.py surfaces persist to `read_surfaces.json`.
- OpenAPI v1_1 promises six workshop routes that are 501 stubs in
  `services/control-plane/bff/agora/strategy_workshop/router.py:1430-1483`.
- `docs/contracts/agora/dev-compatibility-manifest.json` is stale
  (`compatibility_status: pending`, frontend runtime commit placeholder,
  frozen at 2026-06-21, does not cover v1_2-v1_5).
- `/bff/agora/capabilities` returns an empty list while `/bff/agora/me` returns
  granted capabilities (verified live 2026-07-12).
- `AI Trading Desk Design.zip` remains missing; design parity has no baseline.

## Task Graph

| Task | Wave | Title | Depends on |
|---|---|---|---|
| AG-GAP-001 | 0 | Enable and prove durable workshop Postgres backend on dev | - |
| AG-GAP-002 | 0 | Durable Postgres store for trading_room | AG-GAP-001 |
| AG-GAP-003 | 0 | Durable Postgres store for research | AG-GAP-001 |
| AG-GAP-004 | 0 | Durable Postgres store for dashboard recipes | AG-GAP-001 |
| AG-GAP-005 | 0 | Contract honesty: resolve 501 routes + refresh compatibility manifest | - |
| AG-GAP-006 | 1 | Migrate identity/personalization/shadow routes out of main.py | - |
| AG-GAP-007 | 1 | Fix /bff/agora/capabilities mismatch + clean dev probe residue | - |
| AG-GAP-008 | 1 | Implement typed Trading Room SSE stream | - |
| AG-GAP-009 | 1 | Real PrivateContentStore replacing priv-content-stub refs | AG-GAP-001 |
| AG-GAP-010 | 2 | Declare design parity baseline (design zip lost) | - |
| AG-GAP-011 | 2 | Reconcile nested FE checkouts; enforce canonical execute-plans | - |
| AG-GAP-012 | 2 | 12-block completeness additive contract (bundle v1_6) | - |
| AG-GAP-013 | 2 | Agora market-data activation readback (SRCLIVE line) | - |

Task briefs live next to this INDEX as `AG-GAP-0NN-*.md`.

## Supervisor Instructions

1. Owner lane is Codex with reviewer Codex2, matching current
   `ready_dispatcher` availability (Claude/Claude2/Antigravity*/Copilot disabled).
   If lanes recover, prefer Claude ownership for new assignments.
2. Wave 0 tasks are the production blockers; do not start Wave 2 polish tasks
   while Wave 0 is starved for workers.
3. Frontend changes go to `ajoe734/execute-plans@dev` only. Block any worker
   that patches `.fe-ep/` or `.fe-human-inbox-persona-focus/` nested checkouts
   (see AG-GAP-011).
4. Do not re-implement surfaces that already pass live gates (three-tab shell,
   DynUI renderer chain, workshop SSE). These tasks close persistence, contract,
   and data-truth gaps only.
5. Every task that touches runtime behavior requires post-deploy live curl or
   hosted browser proof against
   `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` /
   `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`.
6. Restart-persistence proof (write -> restart BFF -> read back) is the
   acceptance spine for AG-GAP-001..004 and AG-GAP-009.
