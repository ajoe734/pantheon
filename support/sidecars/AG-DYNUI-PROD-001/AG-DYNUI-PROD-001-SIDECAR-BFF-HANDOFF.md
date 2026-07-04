# AG-DYNUI-PROD-001 Sidecar BFF Handoff Packet

Task ID: AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF
Parent Task: AG-DYNUI-PROD-001
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Codex
Prepared: 2026-07-04T00:14:49Z

## Scope

Support-only sidecar for `AG-DYNUI-PROD-001`. This packet does not define
canonical architecture, promote contract truth, change BFF runtime code, change
frontend code, update registries, or resolve the parent source-truth task.

It packages the BFF query gaps, operator journey, and execute-plans handoff
notes that the parent owner can absorb while restoring Agora DYNUI source/task
truth. Any canonical source decision, runtime implementation, frontend repair,
or hosted proof remains parent or downstream `AG-DYNUI-PROD-*` scope.

Current parent context at packet time:

- Parent owner: Codex.
- Parent reviewer: Claude.
- Parent status in local task packet: `todo`.
- Current support task status from centralized status root:
  `in_progress`.
- Parent production-gap packet:
  `docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md`.
- Parent execution brief:
  `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md`.

## Source Snapshot

| Surface | Current state | Source |
|---|---|---|
| Production-gap audit | Cache/header incident was repaired separately, but Agora DYNUI is not production-complete. The live default Trading Room path can still render a thin aggregate empty state and the route remains under global PlatformShell plus the Agora three-tab layout. | `docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md` |
| Design source map | Prior intake map names V10 Strategy Workshop, V11 Winner Branch Trading Room, V6 dashboard, V4 dashboard control, extracted prototype, and screenshots as the dynamic source set. It explicitly rejects static screenshot/card substitutions. | `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` |
| Current zip availability | `AI Trading Desk Design.zip` was not present at this task worktree root or `/home/lupin/code/pantheon/AI Trading Desk Design.zip`; extracted source material still exists under `/tmp/ai-trading-desk-design/`. Parent must decide whether the extracted copy is acceptable evidence or keep the missing zip blocker open. | `test -f .../AI Trading Desk Design.zip`; `find /tmp/ai-trading-desk-design -maxdepth 2 -type f` |
| Agora BFF Trading Room | Runtime router includes read aggregate, strategy detail, decision events, request-only governed intents, workspace proposal generation, proposal accept, workspace layout/view/widget mutation, widget revision proposal, version list, and rollback routes. | `services/control-plane/bff/agora/trading_room/router.py` |
| Trading Room safety boundary | Router declares no live order routing, no RuntimeBinding/capital binding mutation, no promotion approval, and request-only governed handoff semantics. | `services/control-plane/bff/agora/trading_room/router.py` |
| Workshop BFF | Strategy Workshop router supports list/create/get/message/events/completeness/stream, but version, research-run, consultation, and conclude routes are still 501 stubs. | `services/control-plane/bff/agora/strategy_workshop/router.py` |
| Frontend Trading Room client | execute-plans has a Trading Room client for aggregate reads, proposal generation, proposal acceptance, workspace load, layout patch, version list/rollback, widget revision proposal/accept, and decision event decisions. | `/home/lupin/code/execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` |
| Frontend default route | `/agora/trading-room` renders `TradingRoomPage` without a strategy id. `TradingRoomPage` chooses `AggregateView` unless `strategyId` exists, so the default URL can still show `All Strategies`, empty strategies, empty event queue, and empty positions. | `/home/lupin/code/execute-plans/src/routes/agora.tsx`; `/home/lupin/code/execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` |
| Frontend Workshop join | `StrategyWorkshopPage` only enables Add to Trading Room when readiness is `trading_room` and an `onAddToTradingRoom` handler is provided. The route currently renders `<StrategyWorkshopPage />` without that handler. | `/home/lupin/code/execute-plans/src/routes/agora.tsx`; `/home/lupin/code/execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` |
| Frontend source split | `/home/lupin/code/execute-plans` is on `dev` at `221c48d`, ahead 1946 / behind 18 with local edits; nested `/home/lupin/code/pantheon/.fe-ep` is on `task/mgmt-gap-008-detail-honesty` at `821ad41` with many unrelated dirty files. | `git -C ... status -sb`; `git -C ... rev-parse --short HEAD` |

## BFF Query Gap Matrix

| Gap | Current state | Why it matters | Suggested absorption |
|---|---|---|---|
| Canonical design archive | The prior `AG-DYNUI-SRC-001` closeout says the zip was readable on 2026-06-28, but this worktree and centralized `/home/lupin/code/pantheon` path do not currently contain `AI Trading Desk Design.zip`; only `/tmp/ai-trading-desk-design/` remains readable. | Parent acceptance requires the canonical design source to be recorded or a precise blocker. Extracted temp files are useful, but not the same as durable canonical repo/source truth. | Parent should either restore/record the canonical archive location or explicitly record that the extracted `/tmp` source is temporary evidence and keep a blocker for durable source placement. |
| Default Trading Room entry | BFF `GET /bff/agora/trading-room` can return strategies/readiness/queue data, but the frontend default route still stays in aggregate mode unless a URL strategy id is present. | Hosted `/agora/trading-room` can pass route/BFF smoke while still failing the design-pack dynamic entry requirement. | `AG-DYNUI-PROD-003` should select a ready strategy from live BFF data or route to a Workshop/readiness path; no hardcoded strategy or static fallback. |
| Workshop to Trading Room handoff | Workshop readiness is displayed, but the route does not pass `onAddToTradingRoom`; version/research/consult/conclude are still BFF stubs, and FE workshop client paths include singular `/research-run` and `/consultation` while BFF stubs are plural `/research-runs` and `/consultations`. | V10-to-V11 acceptance depends on a real join path, not manual URL surgery or a permanently disabled button. Path drift will create false frontend readiness even before runtime implementation. | Parent/downstream owners should align workshop route names, implement or intentionally gate stubs, and wire `onAddToTradingRoom` to BFF-backed proposal generation only after readiness is trustworthy. |
| Error diagnostics | Trading Room proposal errors preserve typed status/code in strategy workspace state, but the root Trading Room load catch still collapses `getTradingRoom()` failure into `Failed to load Trading Room.` | Production-gap task 004 needs auth/BFF/schema/network/cache diagnostics at the root route, including correlation/request ids where available. | `AG-DYNUI-PROD-004` should carry BFF error envelope metadata through root load state and hosted probes. This sidecar does not change it. |
| Strict BFF write proof | Frontend and BFF have idempotency/ETag paths for workspace layout, widget revision accept, rollback, and decision events, with unit tests. Hosted strict-mode proof of the full V11 flow is still not recorded. | Existing code/tests are not equivalent to hosted E2E against dev FE + live BFF. | `AG-DYNUI-PROD-005` and `AG-DYNUI-PROD-006` should run strict live flow evidence for proposal generation, accept, layout patch, widget revision, keep-copy, version history, and rollback. |
| Source checkout split | The parent audit names `/home/lupin/code/execute-plans` as the active frontend repo, but current checkout state is dirty and massively diverged from `origin/dev`; nested `.fe-ep` is a separate dirty task branch. | Workers can inspect or deploy from the wrong checkout and produce contradictory Agora source truth. | Parent should record the canonical frontend checkout/commit for DYNUI continuation before asking frontend workers to modify or validate production behavior. |

## Operator Journey

Recommended read-only-to-write-gated journey for the parent owner and
downstream Agora DYNUI workers. This is a handoff path, not evidence that the
journey currently passes hosted validation.

1. Source check: confirm the durable design source or blocker before changing
   UI behavior. Use the V10/V11/V6/V4 source map and extracted prototype only
   as dynamic reference material; do not recreate screenshots as static pages.
2. Session bootstrap: run the hosted dev frontend with `VITE_BFF_MODE=live`,
   `VITE_BFF_FALLBACK=strict`, and the dev BFF base URL. Confirm `/bff/me` and
   `/bff/agora/me` return the expected user scope before Agora reads.
3. Default entry smoke: load `/agora/trading-room`. Assert it does not stop at
   a passive aggregate empty shell. If no ready strategy exists, the UI should
   route to Strategy Workshop/readiness with a clear BFF-driven reason.
4. Ready strategy smoke: from a ready strategy, generate
   `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals`, inspect
   proposal views/widget counts/warnings/personalization, then accept the
   proposal into a workspace.
5. Workspace mutation smoke: use the accepted workspace ETag for layout patch,
   widget revision proposal, accept/apply, keep-original-add-copy, version list,
   and rollback. Use idempotency keys on mutation requests and record typed
   409/412 behavior for stale ETags.
6. Safety smoke: verify decision events and governed intent handoffs remain
   request-only; no broker order, RuntimeBinding mutation, capital binding, or
   promotion approval path appears in Agora UI or BFF payloads.
7. Evidence capture: record target FE URL, BFF base URL, git SHAs, route status
   codes, ETag/idempotency behavior, screenshot paths, and correlation/request
   ids only. Do not store tokens, PII, raw sensitive payloads, or generated
   full strategy descriptions.

Do not treat any of these as sidecar-owned:

- restoring the missing design zip;
- choosing the canonical frontend checkout;
- changing OpenAPI/schema/registry truth;
- implementing BFF routes or frontend pages;
- running production or capital-affecting actions.

## Frontend Handoff Notes

- Treat `/home/lupin/code/execute-plans` as the intended frontend repo, but do
  not start implementation from its current dirty/diverged state until the
  parent owner records the expected branch/commit. Do not use nested `.fe-ep`
  as the source of truth unless the parent explicitly reassigns it.
- Use `src/lib/bff-v1/agora/tradingRoom.ts` as the Trading Room route seam.
  Page components should not add ad hoc fetch calls for proposal/workspace or
  widget revision flows.
- Keep the root `/agora/trading-room` path honest. The default route should
  derive ready/no-ready/degraded state from BFF data and should not require
  operators to manually append `/strategyId?strategyVersion=...`.
- Route Workshop join through a handler that can select or derive the
  strategy id/version from BFF-backed readiness. If readiness or version data is
  unavailable, show a blocker/degraded state instead of fabricating a version.
- Align singular/plural workshop route paths before relying on frontend
  workshop research/consultation actions. Current FE client uses
  `/research-run` and `/consultation`; current BFF stubs expose
  `/research-runs` and `/consultations`.
- Keep Agora visual and language work subordinate to runtime truth. The design
  pack requires dynamic proposal/workspace/version behavior; screenshots and
  prototype HTML are references, not deliverable runtime.
- Continue to preserve the request-only boundary. UI copy for canary/live
  should say request/review, not execute/order/place trade.

## Parent Absorption Checklist

Before `AG-DYNUI-PROD-001` lets downstream work depend on this handoff, confirm:

- The canonical design source is restored or a blocker names the exact missing
  durable source. This packet observed no zip at either checked root.
- The parent source map names one canonical execute-plans checkout/commit and
  quarantines or assigns the dirty `.fe-ep` checkout risk.
- Downstream tasks can distinguish existing BFF/FE capabilities from hosted
  production proof gaps.
- `AG-DYNUI-PROD-003` owns default-route dynamic entry; this sidecar only
  identifies the BFF/FE seam and gap.
- `AG-DYNUI-PROD-004` owns root error diagnostics and stale-bundle recovery;
  this sidecar only records the generic root error behavior.
- `AG-DYNUI-PROD-005` owns strict BFF dynamic workflow proof/repair; this
  sidecar only lists the existing BFF/FE route family.
- `AG-DYNUI-PROD-006` owns hosted E2E, screenshots, PR/check/deploy evidence,
  and final publish gate.

## Verification Notes For This Sidecar

No runtime, canonical, BFF, registry, or frontend implementation was changed by
this sidecar. Verification was source inspection only:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF
sed -n '1,260p' .orchestrator/task-briefs/ag_dynui_prod_001_sidecar_bff_handoff.md
sed -n '1,260p' docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-003-default-route-dynamic-entry.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-004-error-cache-diagnostics.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-006-hosted-e2e-publish-gate.md
sed -n '1,240p' docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md
sed -n '1,220p' docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md
sed -n '1,220p' services/control-plane/specs/agora/v4/capability_manifest_v1_3.json
rg --files services/control-plane/bff | rg 'agora|strategy|trading|workspace|workshop'
nl -ba services/control-plane/bff/agora/trading_room/router.py | sed -n '1,260p'
rg -n 'workspace|proposal|version|rollback|widget|layout|dashboard|@router|def .*workspace|def .*decision|def .*intent' services/control-plane/bff/agora/trading_room/router.py
nl -ba services/control-plane/bff/agora/strategy_workshop/router.py | sed -n '1,280p'
nl -ba services/control-plane/bff/agora/strategy_workshop/router.py | sed -n '540,596p'
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans rev-parse --short HEAD
git -C /home/lupin/code/pantheon/.fe-ep status -sb
git -C /home/lupin/code/pantheon/.fe-ep rev-parse --short HEAD
nl -ba /home/lupin/code/execute-plans/src/App.tsx | sed -n '220,360p'
nl -ba /home/lupin/code/execute-plans/src/routes/agora.tsx | sed -n '1,220p'
nl -ba /home/lupin/code/execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx | sed -n '1,1180p'
nl -ba /home/lupin/code/execute-plans/src/lib/bff-v1/agora/tradingRoom.ts | sed -n '1,980p'
nl -ba /home/lupin/code/execute-plans/src/lib/bff-v1/agora/workshops.ts | sed -n '1,380p'
rg -n 'onAddToTradingRoom|Add to Trading Room|AgoraStrategyWorkshopRoute|StrategyWorkshopPage' /home/lupin/code/execute-plans/src/routes/agora.tsx /home/lupin/code/execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx /home/lupin/code/execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx
test -f '/tmp/pantheon-worker-worktrees/pantheon/ag-dynui-prod-001-sidecar-bff-handoff/AI Trading Desk Design.zip' || true
test -f '/home/lupin/code/pantheon/AI Trading Desk Design.zip' || true
find /tmp/ai-trading-desk-design -maxdepth 2 -type f | sort
git diff --check -- support/sidecars/AG-DYNUI-PROD-001/AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF.md
```

## Reviewer Handoff

Reviewer (Codex) should verify:

1. This packet stays support-only and does not mutate canonical truth, runtime
   code, frontend code, route registry, or governance behavior.
2. The source snapshot accurately distinguishes production-gap evidence,
   historical design-source evidence, current missing zip observation, and
   temporary extracted design material.
3. The BFF/FE route statements match the current Pantheon and execute-plans
   code inspected above.
4. The query gaps are framed as parent/downstream absorption items, not as new
   sidecar implementation authority.
5. Parent owner can use this packet without treating it as review approval for
   `AG-DYNUI-PROD-001` or any downstream production task.
