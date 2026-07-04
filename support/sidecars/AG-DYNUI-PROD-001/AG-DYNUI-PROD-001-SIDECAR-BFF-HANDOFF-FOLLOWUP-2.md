# AG-DYNUI-PROD-001 Sidecar BFF Handoff Follow-up 2

Task ID: AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
Parent Task: AG-DYNUI-PROD-001
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Codex
Prepared: 2026-07-04

## Scope

This is a support-only follow-up to
`AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF.md`. It does not change canonical
architecture, L1 contract truth, BFF runtime behavior, route registries,
frontend implementation, deploy configuration, governance policy, or task
state by hand.

The parent `AG-DYNUI-PROD-001` source/task truth map has now absorbed the main
source-truth findings. This follow-up narrows the remaining BFF and frontend
handoff points for downstream `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`,
`AG-DYNUI-PROD-005`, and `AG-DYNUI-PROD-006` owners.

## Delta Since The Original Handoff

| Area | Current follow-up read | Downstream meaning |
| --- | --- | --- |
| Source truth | `AG-DYNUI-PROD-001-source-task-truth.md` now records the missing raw zip, the temporary `/tmp/ai-trading-desk-design/` reference, the committed closure pack, and restored archive continuity. | Downstream workers should stop rediscovering the archive/task-history split and should cite the parent truth map. If a raw zip is required, the exact blocker remains `/home/lupin/code/pantheon/AI Trading Desk Design.zip`. |
| Frontend checkout truth | Parent truth map identifies `/home/lupin/code/execute-plans` as the canonical frontend repo for new DYNUI work and rejects `/home/lupin/code/pantheon/.fe-ep` as a deploy source. Current local `execute-plans` remains dirty/diverged, while `origin/dev` resolves to `702b236`. | Frontend workers must create a clean execute-plans task worktree from the intended remote base before implementation or hosted proof. Do not validate production behavior from `.fe-ep` or the dirty local checkout. |
| Default Trading Room entry | The current route seam still renders `<TradingRoomPage strategyId={strategyId}>`; with no URL strategy id, `TradingRoomPage` remains aggregate-driven. | `AG-DYNUI-PROD-003` owns selecting or deriving a real dynamic entry from BFF data, or routing to a BFF-backed no-ready/readiness state. This sidecar does not prescribe a UI implementation. |
| Workshop join | `StrategyWorkshopPage` still requires an `onAddToTradingRoom` handler for the button to be active, while the route renders `<StrategyWorkshopPage />`. Workshop BFF version/research/consult/conclude routes are still 501 stubs. | The V10-to-V11 join should stay gated until route naming and readiness/version semantics are aligned. Avoid faking a strategy version or enabling a disabled handoff with static data. |
| Strict BFF workflow | Trading Room BFF and frontend client surfaces still expose proposal generation, proposal accept, workspace load, layout patch, widget revision, version list, and rollback paths. | `AG-DYNUI-PROD-005` should treat these as candidate live seams to prove/repair, not as production evidence by themselves. Hosted strict-mode proof is still required. |
| Error diagnostics | Root Trading Room load handling is still the known production gap: generic load failure is not enough for auth/BFF/schema/network/cache diagnosis. | `AG-DYNUI-PROD-004` owns typed status/code/request-id/correlation-id surfacing and stale bundle recovery. |

## BFF Query And Route Watchpoints

The current BFF surfaces are sufficient for downstream probing, but not for
declaring the Agora DYNUI production gap closed:

- Trading Room aggregate and decision-support routes remain observation and
  request-only. Downstream UI must not imply broker order routing, capital
  binding, RuntimeBinding mutation, or promotion approval.
- Workspace proposal, workspace mutation, widget revision, version, and
  rollback routes should be exercised with idempotency keys and current
  `If-Match` ETags. Conflict and stale-Etag behavior should be captured as
  first-class evidence, not treated as generic failures.
- Workshop routes expose 501 stubs for versions, research runs, consultations,
  and conclude. The frontend client currently uses singular
  `/research-run` and `/consultation`, while the BFF stubs are plural
  `/research-runs` and `/consultations`; align this before building operator
  promises on those actions.
- If a downstream route needs a ready strategy/version for smoke tests, it
  should use BFF data or a recorded fixture gate. Do not hardcode a strategy id
  into the default route just to avoid the aggregate empty state.

## Operator Journey Update

Use this as the handoff path after the parent source truth map:

1. Start from a clean `execute-plans` task worktree based on the agreed remote
   commit. Record the commit SHA and explicitly ignore `.fe-ep`.
2. Confirm dev frontend strict live BFF settings and run `/bff/me` plus
   `/bff/agora/me` before Agora-specific route proof.
3. Load `/agora/trading-room` with no strategy id and record whether the route
   enters an actionable BFF-derived state or the old aggregate empty shell.
4. For a ready strategy, prove proposal generation, proposal acceptance,
   workspace load, layout patch, widget revision preview, apply, keep-copy,
   version list, and rollback against the hosted dev frontend and live BFF.
5. Capture typed error envelopes, request/correlation ids where available,
   cache/deployment headers, desktop/mobile screenshots, PR numbers, merge
   SHAs, and deploy run evidence. Do not store secrets, tokens, raw sensitive
   payloads, or full generated strategy text.

## Ownership Boundaries

This follow-up is only a support packet.

Owned here:

- restating the post-parent handoff delta;
- naming BFF/frontend seams that downstream tasks should verify;
- preserving the support-only boundary for Codex review.

Not owned here:

- restoring the raw design zip;
- editing `AG-DYNUI-PROD-001` truth maps or L1/L2 canonical docs;
- changing Pantheon BFF routes, schemas, registries, governance code, or tests;
- changing execute-plans frontend routes/components/clients;
- deploying frontend or BFF services;
- approving or closing downstream production tasks.

## Reviewer Handoff

Reviewer should verify:

1. This packet is a support artifact only and does not introduce canonical
   contract truth.
2. The follow-up accurately reflects the parent truth-map absorption and does
   not reopen source/task-truth research that `AG-DYNUI-PROD-001` already
   captured.
3. The BFF/frontend route watchpoints are framed as downstream validation
   requirements for `AG-DYNUI-PROD-003` through `AG-DYNUI-PROD-006`.
4. The packet does not treat existing route/client code as hosted production
   proof.

## Verification Notes

Verification was source inspection only. No runtime, frontend, canonical,
registry, governance, deploy, or hosted environment changes were made.

Commands used:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,240p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_dynui_prod_001_sidecar_bff_handoff_followup_2.md
sed -n '1,240p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,240p' ai-status.json
rg -n 'AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2|ag-dynui-prod-001-sidecar-bff-handoff-followup-2' ai-status.json
sed -n '1,260p' support/sidecars/AG-DYNUI-PROD-001/AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF.md
sed -n '1,220p' .orchestrator/task-briefs/ag_dynui_prod_001_sidecar_bff_handoff.md
sed -n '1,260p' docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-003-default-route-dynamic-entry.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-004-error-cache-diagnostics.md
sed -n '1,240p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md
sed -n '1,240p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-006-hosted-e2e-publish-gate.md
rg -n '@router|workspace|proposal|version|rollback|widget|layout|decision|intent' services/control-plane/bff/agora/trading_room/router.py
rg -n 'research-runs|consultations|versions|conclude|501|HTTP_501|@router' services/control-plane/bff/agora/strategy_workshop/router.py
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans rev-parse --short HEAD
git -C /home/lupin/code/execute-plans rev-parse --short origin/dev
rg -n 'TradingRoomPage|strategyId|StrategyWorkshopPage|onAddToTradingRoom' /home/lupin/code/execute-plans/src/routes/agora.tsx /home/lupin/code/execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx /home/lupin/code/execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx
rg -n 'research-run|consultation|trading-room|proposal|workspace|rollback|version|layout|widget' /home/lupin/code/execute-plans/src/lib/bff-v1/agora/tradingRoom.ts /home/lupin/code/execute-plans/src/lib/bff-v1/agora/workshops.ts
test -f '/home/lupin/code/pantheon/AI Trading Desk Design.zip'
test -d '/tmp/ai-trading-desk-design'
```
