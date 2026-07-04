# AG-DYNUI-PROD-002 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-002` |
| Parent title | Agora standalone workbench shell |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not define canonical truth, update L1
contracts, edit BFF/runtime code, edit frontend code, change route registries,
or approve the parent implementation. Parent ownership and review decide how
to absorb this packet.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override L1/L2 architecture or task ownership. |
| `.orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff.md` | Sidecar scope is BFF query gap, operator journey, and frontend handoff material only. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful docs/support work should be committed through the task branch workflow with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | A `review_approved` task returns to the owner for finalization, task-scoped commit/PR flow, and `done` only after merge. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF` | Sidecar is `review_approved`, owner `Codex`, reviewer `Claude`, artifact path is this file, with reviewer notes recorded in status. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-002` | Parent is `in_progress`, owner `Claude`, reviewer `Codex`; acceptance requires an intentional standalone shell or an approved exception while preserving auth/live state. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md` | Parent scope is shell architecture, deep-linking, mobile safety, and real contextual shell state or blocker. |
| `docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md` | Production gap remains: Agora is still under global `PlatformShell` plus tab shell, and default route/workflow proof remains incomplete. |
| `docs/frontend/execute-plans-dev-hosting.md` | Active frontend source is `ajoe734/execute-plans`, local checkout `/home/lupin/code/execute-plans`, dev host is Pantheon-owned FE with live strict BFF env. |
| `support/sidecars/AG-DYNUI-PROD-001/*` | Parent source truth now points downstream workers at a clean execute-plans task worktree and rejects dirty `.fe-ep` as deploy source. |
| `/home/lupin/code/execute-plans` remote `origin/dev` | Current clean remote basis inspected for this packet is `702b236adb76a4e9a2029fce1a4b9c487f69a290`; local checkout is dirty/diverged and was not used as source truth. |
| Pantheon dev FE `/deployment.json` | Hosted dev FE currently reports execute-plans commit `702b236adb76a4e9a2029fce1a4b9c487f69a290`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Handoff Summary

`AG-DYNUI-PROD-002` does not need a new BFF route family to decide the shell
architecture. The key decision is frontend composition:

1. Make `/agora/*` an intentional Agora workbench shell that is not merely an
   embedded management tab surface; or
2. Record an explicit approved exception explaining why Agora remains inside
   global `PlatformShell`, and prove the nested shell does not reduce Agora to
   unrelated management chrome.

Current clean execute-plans `origin/dev` has a dark `TradingDeskLayout` and
Trading Room proposal/workspace client surfaces. The unresolved parent gap is
that `src/App.tsx` still mounts `/agora` under `PlatformShellRoute`, and the
inner Agora shell still has contextual holes:

- `/agora` is a child of global `PlatformShellRoute`.
- `PlatformShell` owns `LiveStatusBanner`, `TopBar`, global SSE connection,
  notification/job/handoff drawers, and rollback/bulk-result overlays.
- `TradingDeskLayout` owns the Agora command bar, tab bar, servant drawer, main
  outlet, and bottom strip.
- The route declares `/agora/strategy-workshop/:workshopId`, but
  `AgoraStrategyWorkshopRoute` does not read the route param, and
  `AgoraLayoutRoute` passes no `workshopId` into `TradingDeskLayout`; the
  servant drawer cannot receive workshop context from the URL today.
- `TradingRoomPage` still falls back to `AggregateView` when no `strategyId`
  is present. That default-entry behavior is primarily `AG-DYNUI-PROD-003`,
  but parent shell work must not make it harder to fix.

No sidecar-owned code change is made here.

---

## 3. Current Frontend Composition Snapshot

| Surface | Current state on execute-plans `origin/dev` | Parent meaning |
|---|---|---|
| App route tree | `App.tsx` wraps both `/management` and `/agora` in `<Route element={<PlatformShellRoute />}>`; `/agora` is nested under that wrapper. | To make Agora standalone, move the `/agora` route outside the `PlatformShellRoute` subtree while keeping it inside `AuthProvider` and `ErrorBoundary`. If not, document the exception. |
| Global shell state | `PlatformShell` mounts `LiveStatusBanner`, `TopBar`, `RightDrawer`, `NotificationCenter`, `JobProgressDrawer`, `HandoffDrawer`, `BulkResultDrawer`, `RollbackSagaDrawer`, and global `connectLiveSse()`. | Standalone Agora must either retain necessary auth/live/notification affordances through a shared non-management surface or explicitly accept the exception. Do not drop live state silently. |
| Agora shell | `TradingDeskLayout` renders dark AGORA command bar, tabs, optional servant drawer, content outlet, and bottom strip. Existing `TradingDeskLayout.test.tsx` covers internal tabs/drawer/strip but not global `PlatformShell` absence/presence. | Parent tests need to cover route composition, not only internal tab navigation. |
| Workshop route context | `strategy-workshop/:workshopId` exists in the route table, but the wrapper returns `<StrategyWorkshopPage />` without passing `workshopId`; `TradingDeskLayout` has a `workshopId` prop but receives none. | Deep-linkable workshop sessions and contextual servant drawer remain a concrete shell gap. Parent should wire params or record a BFF/design blocker. |
| Trading Room route | `/agora/trading-room` passes no `strategyId`; `TradingRoomPage` loads aggregate and renders `AggregateView`. `/agora/trading-room/:strategyId` starts proposal/workspace flow when a strategy version is available. | Shell refactor must preserve both URLs and their query string semantics; default dynamic entry remains downstream `AG-DYNUI-PROD-003`. |
| Local checkout | `/home/lupin/code/execute-plans` is dirty and diverged (`dev...origin/dev [ahead 1946, behind 18]`). | Parent implementation should use a clean execute-plans task worktree from `origin/dev`, not the dirty checkout. |
| Parent branch | No remote `task/AG-DYNUI-PROD-002*` branch was found during this packet. | Reviewer may need to compare against the parent PR/branch later; this packet only reflects current remote `origin/dev`. |

---

## 4. BFF Query Surface And Gap Matrix

This sidecar found no need for a new canonical BFF contract to complete the
parent shell decision. The relevant query/watch surfaces are existing ones:

| Need | Current surface | Parent handoff guidance |
|---|---|---|
| Auth/session preservation | `AuthProvider` wraps routes above `PlatformShell` in `App.tsx`; BFF calls use auth headers and `credentials: "include"`. | Moving `/agora` outside `PlatformShell` should not move it outside `AuthProvider`. Keep BFF strict/live env behavior unchanged. |
| Live status and global SSE | `PlatformShell` calls `connectLiveSse()` and renders `LiveStatusBanner`. | If Agora becomes standalone, provide an Agora-safe status surface or shared shell substrate. Do not make operators lose live/offline/strict-mode visibility. |
| Agora identity readiness | Runtime route `GET /bff/agora/me` exists and unauthenticated curl fails closed with `AUTH_REQUIRED`. | If parent shell needs Agora scope/capability display, use a narrow BFF client; do not add ad hoc fetches in page components. |
| Trading Room aggregate | `getTradingRoom()` calls `GET /bff/agora/trading-room`; `listDecisionEvents()` calls `/decision-events`. | Route composition must preserve these strict BFF reads. Root error diagnostics are `AG-DYNUI-PROD-004`, not this shell task. |
| Strategy workspace flow | Frontend client exposes proposal generation/read/accept, workspace read, layout patch, versions, rollback, widget revision, and decision-event writes. Backend has matching Trading Room route family with ETag/idempotency safety. | Shell changes should not reroute around `src/lib/bff-v1/agora/tradingRoom.ts` or bypass validators/allowlists. Dynamic workflow proof remains `AG-DYNUI-PROD-005/006`. |
| Workshop context | Workshop BFF supports list/create/get/messages/events/completeness/stream. Versions, research runs, consultations, and conclude are 501 stubs. | If the parent makes the servant drawer contextual, source context from implemented workshop reads/completeness/stream, or show a blocker/degraded state. Do not fake unavailable research/consult/conclude state. |
| Governed intent boundary | Trading Room BFF declares no live order routing, no RuntimeBinding mutation, no capital binding, and request-only governed handoff. | Shell copy/buttons must keep request/review language and must not add broker/order/promotion controls. |

---

## 5. Operator Journey Packet

### Journey A: Current Embedded Path

1. Operator opens `/agora/trading-room` on the Pantheon dev FE host.
2. `AuthProvider` wraps the app.
3. `PlatformShell` renders global live banner, management top bar/dropdowns,
   global SSE, and platform drawers.
4. Inside that shell, `TradingDeskLayout` renders AGORA command bar, tabs,
   optional servant drawer, main content, and bottom strip.
5. `TradingRoomPage` loads the Trading Room aggregate and decision events from
   the configured dev BFF.

This path preserves global live state but still leaves Agora embedded in the
management shell.

### Journey B: Standalone Workbench Target

1. `/agora/*` remains inside `AuthProvider`, `TooltipProvider`, and the app
   `ErrorBoundary`.
2. `/agora/*` is no longer a child of `PlatformShellRoute`.
3. An Agora workbench shell owns its top chrome, route tabs, contextual servant
   surface, and bottom job/shadow/journal surface.
4. Required live/auth status is provided through a shared substrate or an
   Agora-specific status strip, without rendering unrelated management IA.
5. `/agora/trading-room`, `/agora/trading-room/:strategyId`, and
   `/agora/strategy-workshop/:workshopId` remain deep-linkable.
6. Browser smoke proves desktop and mobile layout do not overlap, hide live
   status, or break strict BFF calls.

### Journey C: Approved Embedded Exception

If parent keeps `/agora` under `PlatformShellRoute`, the exception should be
visible in the parent artifact and tests:

1. State why global `PlatformShell` remains necessary for current auth/live
   status.
2. Identify which pieces are allowed shared substrate and which management IA
   must not leak into Agora.
3. Prove `TradingDeskLayout` still dominates the first viewport and is not
   reduced to an old three-tab placeholder.
4. Include screenshots showing the exact retained global chrome.
5. Record residual risk and owner for removing or refactoring the exception.

### Journey D: Workshop Deep Link And Servant Context

1. Operator opens `/agora/strategy-workshop/{workshopId}`.
2. Route wrapper reads `workshopId` from params.
3. `StrategyWorkshopPage` loads that session rather than the workshop list.
4. `TradingDeskLayout` receives the same `workshopId` so the servant drawer can
   show contextual state.
5. If required BFF context is unavailable, the drawer shows a degraded/blocker
   state rather than placeholder text.

---

## 6. Parent Frontend Handoff Checklist

Parent implementation/review should verify:

- Route composition is intentional:
  - standalone: `/agora` is outside `PlatformShellRoute`;
  - exception: parent artifact names the approved reason and visible retained
    chrome.
- `AuthProvider` remains above Agora routes.
- Strict BFF env behavior remains:
  `VITE_BFF_MODE=live`, `VITE_BFF_BASE_URL` set to dev BFF,
  `VITE_BFF_FALLBACK=strict`, and safe writes by default.
- `TradingDeskLayout` keeps AGORA command bar/tab/bottom surfaces and does not
  become a nested card inside management content.
- `strategy-workshop/:workshopId` passes `workshopId` into
  `StrategyWorkshopPage` and any contextual drawer/shell state.
- If servant drawer remains placeholder-only, parent records the exact missing
  data contract or defers that surface explicitly.
- Existing Trading Room BFF client module remains the BFF seam; page components
  should not add ad hoc fetches for shell status, proposals, workspaces, or
  decisions.
- Mobile layout is verified. The current drawer is a fixed `w-80` side rail;
  parent should prove mobile behavior or make the drawer/bottom surface
  responsive.
- Tests cover:
  - route composition / PlatformShell absence or approved presence;
  - top chrome and live status preservation;
  - drawer/bottom surface behavior;
  - deep links for `/agora/trading-room/:strategyId` and
    `/agora/strategy-workshop/:workshopId`;
  - mobile or narrow viewport layout at least through Playwright/browser
    screenshots.

---

## 7. Parent Boundary Notes

Owned by `AG-DYNUI-PROD-002` parent:

- frontend shell composition in `execute-plans`;
- route table placement for `/agora/*`;
- Agora workbench chrome, contextual shell state, route deep-linking, and
  responsive shell behavior;
- documenting an explicit approved exception if global `PlatformShell` stays.

Not owned by this sidecar or parent shell task:

- canonical source restoration (`AG-DYNUI-PROD-001`);
- default route dynamic entry from no-strategy URL (`AG-DYNUI-PROD-003`);
- root error diagnostics/stale bundle recovery (`AG-DYNUI-PROD-004`);
- proposal/grid/revision/version/rollback dynamic workflow closeout
  (`AG-DYNUI-PROD-005`);
- hosted E2E and publish gate (`AG-DYNUI-PROD-006`);
- BFF route/schema/registry/governance runtime changes unless parent explicitly
  opens a separate backend task.

---

## 8. Recommended Parent Closeout Evidence

Before the parent moves toward review approval or done, record:

- execute-plans task branch, PR URL, and merge commit SHA;
- whether `/agora/*` is standalone or an approved embedded exception;
- if standalone, how live status, auth, notifications/jobs, and SSE are
  preserved without management chrome;
- if embedded, screenshots proving the retained `PlatformShell` chrome and the
  Agora first viewport;
- local validation commands and results;
- hosted FE deployment id/source commit from `/deployment.json`;
- desktop and mobile screenshots for `/agora/trading-room`;
- browser probe evidence that strict BFF calls still target the dev BFF;
- residual risks with owner and expiry.

---

## 9. Reviewer Handoff

Reviewer should verify:

1. This packet is support-only and does not mutate canonical truth or runtime
   implementation.
2. The frontend snapshot uses clean execute-plans `origin/dev` and hosted
   deployment evidence, not the dirty local checkout.
3. The shell gap is framed as route/composition work, not as a new BFF contract.
4. Workshop deep-link/context propagation is correctly called out as a shell
   gap.
5. Parent can use the checklist without treating this packet as approval for
   the parent implementation.

---

## 10. Reviewer Approval And Finalization

Reviewer approval recorded in `ai-status`:

- Verified against execute-plans `origin/dev` commit
  `702b236adb76a4e9a2029fce1a4b9c487f69a290`.
- Confirmed the packet's route composition claims are accurate.
- Confirmed this task only added support material and did not touch canonical
  truth, BFF/runtime code, frontend code, registries, governance, or deployment
  state.
- Approved the sidecar packet and returned it to owner `Codex` for closeout.

Finalization remains support-only. The owner closeout commit should include
this packet plus the task-scoped brief/status record, then follow the task PR
flow before `AI_NAME=Codex ./scripts/ai-status.sh done`.

---

## 11. Verification Notes

Verification was source inspection and anonymous hosted read probing only. No
runtime, frontend, canonical, registry, governance, deploy, or hosted
environment changes were made.

Commands used:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,240p' AI_COLLABORATION_GUIDE.md
sed -n '241,520p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,260p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF
AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-002
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md
sed -n '1,260p' docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md
sed -n '1,260p' docs/frontend/execute-plans-dev-hosting.md
sed -n '1,260p' support/sidecars/AG-DYNUI-PROD-001/AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF.md
sed -n '1,260p' support/sidecars/AG-DYNUI-PROD-001/AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans fetch origin dev main --prune
git -C /home/lupin/code/execute-plans rev-parse --short origin/dev
git -C /home/lupin/code/execute-plans ls-remote --heads origin 'task/AG-DYNUI-PROD-002*'
git -C /home/lupin/code/execute-plans show origin/dev:src/App.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/platform/PlatformShell.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/routes/agora.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/TradingDeskLayout.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/TradingDeskLayout.test.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/trading-room/TradingRoomPage.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/lib/bff-v1/agora/tradingRoom.ts
rg -n '@router|trading-room|proposal|workspace|versions|rollback|revision|decision|intent' services/control-plane/bff/agora/trading_room/router.py
rg -n '@router|versions|research-runs|consultations|conclude|readiness|completeness|stream|HTTP_501|501' services/control-plane/bff/agora/strategy_workshop/router.py
curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sSI https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/me
git diff --check -- support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md
AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF
```
