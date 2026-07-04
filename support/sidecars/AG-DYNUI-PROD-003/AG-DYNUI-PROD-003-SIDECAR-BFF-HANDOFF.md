# AG-DYNUI-PROD-003 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-003` |
| Parent title | Trading Room default dynamic entry |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
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
| `.orchestrator/task-briefs/ag_dynui_prod_003_sidecar_bff_handoff.md` | Sidecar scope is BFF query gap, operator journey, and frontend handoff material only; no canonical truth edits. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful docs/support work should be committed through the task branch workflow with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout requires a task-scoped commit, PR, and merge before `done`; this sidecar is not `review_approved` yet, so this packet is the implementation deliverable, not a closeout. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, artifact is this file. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-DYNUI-PROD-003` | Parent is `status: review`, owner `Codex`, reviewer `Claude2`. Parent note: PR #2860 merged into `dev` (merge `ec5d902fce715dbfb2254641ae86825130c4cddd`, head `eab6e0cfdaa50b1f6c7891ae4a94db7872203ae2`); local validation `npm test -- --run src/agora/pages/trading-room/TradingRoomPage.test.tsx` (51/51) and `npm run build:agora` passed. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-003-default-route-dynamic-entry.md` | Parent scope: default `/agora/trading-room` must not stop at the empty `AggregateView`/`All Strategies` shell; must reach dynamic entry/readiness/workshop/proposal workflow from BFF data, without hardcoded strategies. |
| `docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md` | Production gap context: Agora is not yet production-complete; default route and hosted proof were open items feeding this task wave. |
| `docs/frontend/execute-plans-dev-hosting.md` | Canonical frontend repo is `ajoe734/execute-plans`, local checkout `/home/lupin/code/execute-plans`, dev host is Pantheon-owned FE built from that repo's `dev` branch. |
| `git show ec5d902fc --stat` / `git diff 28744d78d eab6e0cfd -- execute-plans/...` | Merge commit changed `TradingRoomPage.tsx`, `TradingRoomPage.test.tsx`, and `agora-main.tsx` inside this repo's own `execute-plans/` working copy, not the standalone `/home/lupin/code/execute-plans` checkout. |
| `services/control-plane/bff/agora/trading_room/router.py` | BFF `TradingRoomStrategyEntry` exposes `readiness_state`, `monitoring_state`, `dashboard_recipe_id`, `candidate_count`, and `staleness_reasons`, which the new default-entry UI consumes directly. |
| `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` and `execute-plans/src/entries/agora-main.tsx` | `StrategyWorkshopPage` accepts an `onAddToTradingRoom` callback, but the live `agora-main.tsx` entry still renders `<StrategyWorkshopPage workshopId={workshopId} />` without wiring that callback. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Hosted dev FE currently reports `commit`/`sourceRef` `702b236adb76a4e9a2029fce1a4b9c487f69a290`, `sourceBranch=dev`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict` — this predates the PROD-003 change. |
| `scripts/deploy_nonprod_vm.sh` | Nonprod dev deploy pulls the frontend build from `https://github.com/ajoe734/execute-plans.git`, i.e. the standalone GitHub repo, not this repo's in-tree `execute-plans/` mirror. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Handoff Summary

`AG-DYNUI-PROD-003` implementation is already merged into this repo's `dev`
branch (`ec5d902fc`, task branch `task/AG-DYNUI-PROD-003`, head `eab6e0cfd`).
The change replaces the passive `AggregateView`/`StrategyList` empty shell with
`TradingRoomDefaultEntry`:

- If BFF returns zero strategies: shows a "Strategy Workshop is the next step"
  panel with an `Open Strategy Workshop` action (wired to
  `handleTabChange("strategy-workshop")` in `agora-main.tsx`).
- If BFF returns strategies but none is `ready`: shows readiness cards sorted
  by readiness state (`conditional` → `stale` → `blocked` → `ready`), each
  with a `Review readiness` action into the Workshop tab.
- If at least one strategy is `ready`: `selectDefaultReadyStrategy()` picks the
  best one (has a `dashboard_recipe_id` first, then highest pending event
  count, then monitoring priority, then title) and the page auto-enters
  `StrategyWorkspaceView` for that strategy — the proposal/workspace path — with
  **no manual URL/query-string edit required**.
- No hardcoded strategy id or fabricated readiness data is introduced; all
  branching reads live `TradingRoomAggregate.strategies` from the BFF.

This satisfies the parent's stated acceptance text at the code level and is
already covered by an expanded `TradingRoomPage.test.tsx` (51/51 passing per
the parent's own status note).

Two gaps remain open that the sidecar found while confirming this handoff, and
that the reviewer/parent should not skip past because CI is green:

1. **Not yet on the standalone canonical frontend repo / hosted deploy.** The
   merged commit lives in this pantheon repo's in-tree `execute-plans/`
   mirror. The hosted dev FE (`deployment.json`) still reports commit
   `702b236a...`, which predates this change, and the nonprod deploy script
   pulls its frontend source from the separate `ajoe734/execute-plans` GitHub
   repo, not from this in-tree mirror. Local unit test / build pass is not the
   same as hosted proof, and the parent's own acceptance list requires
   "hosted proof" before close.
2. **Workshop → Trading Room join is still not wired end to end.** The default
   entry's `Open Strategy Workshop` / `Review readiness` actions correctly
   route into the Workshop tab, but `StrategyWorkshopPage`'s
   `onAddToTradingRoom` prop is still not passed from `agora-main.tsx`. An
   operator who reaches "ready" state in the Workshop still cannot click a
   button in the Workshop itself to jump back into the now-fixed Trading Room
   default entry; they rely on tab navigation, not a direct handoff action.
   This does not block PROD-003's own acceptance text (which is scoped to the
   Trading Room side) but is adjacent and worth naming so a later task does
   not assume it already exists.

No sidecar-owned code change is made here.

---

## 3. Implementation Snapshot

| Surface | Current state on this repo's `dev` (`eab6e0cfd`) | Handoff meaning |
|---|---|---|
| `TradingRoomPage.tsx` default branch | `effectiveStrategyId = strategyId ?? defaultReadyStrategy?.strategy_id`; renders `StrategyWorkspaceView` when any id resolves, else `TradingRoomDefaultEntry`. | Root cause of the old empty-shell bug (branching only on the URL `strategyId`) is fixed; the default path is now BFF-driven. |
| `selectDefaultReadyStrategy()` | Filters `readiness_state === "ready"`, sorts by `dashboard_recipe_id` presence, pending event total, `MONITORING_PRIORITY`, then title. | Deterministic, live-data selection — no hardcoded id. Reviewer should confirm this ordering matches the intended "most actionable ready strategy first" product intent; the sidecar did not find a written product spec pinning this exact tie-break order. |
| `TradingRoomDefaultEntry` (0 strategies) | Shows workshop-first empty state, `trading-room-workshop-empty-entry` testid. | Matches acceptance "never lands on inert empty table shell" for the zero-strategy case. |
| `TradingRoomDefaultEntry` (strategies, none ready) | Shows `trading-room-readiness-entry` cards per strategy with `readinessReason()` copy and a disabled/enabled workshop action depending on whether `onOpenWorkshop` is supplied. | Matches acceptance for the "has strategies but not ready" case; UI still requires the caller to pass `onOpenWorkshop` or the action degrades to disabled — confirmed wired in `agora-main.tsx`. |
| `agora-main.tsx` | Adds `onOpenWorkshop={() => handleTabChange("strategy-workshop")}` to the `TradingRoomPage` call. | Only new prop wiring; no route-table changes were needed since Agora uses tab-based in-app navigation, not React Router routes, in this entry point. |
| `StrategyLensSwitcher` | Label changed from `All Strategies` to `Workbench Entry`. | Minor copy change consistent with the new default-entry framing; not a functional gap. |
| Frontend source location | Implementation commit lives in this pantheon repo's own `execute-plans/` directory (tracked in pantheon git history), not in the standalone `/home/lupin/code/execute-plans` checkout that `docs/frontend/execute-plans-dev-hosting.md` names as canonical. | Parent/reviewer must confirm how this in-tree change reaches the real `ajoe734/execute-plans` repo before treating this as hosted-ready. See §4 publish gap. |
| Hosted dev FE | `deployment.json` reports `commit=702b236a...`, predating `eab6e0cfd`. | Hosted proof required by the parent's own acceptance list is not yet available; do not treat merged-to-`dev`-in-pantheon as equivalent to "deployed". |

---

## 4. BFF Query Surface And Gap Matrix

No new canonical BFF contract is needed for this feature; the existing
`TradingRoomAggregate` / `TradingRoomStrategyEntry` shape already carries the
fields the new UI consumes.

| Need | Current surface | Handoff guidance |
|---|---|---|
| Ready-strategy selection input | `GET /bff/agora/trading-room` returns `strategies[]` with `readiness_state`, `monitoring_state`, `dashboard_recipe_id`, `candidate_count`, `pending_event_counts`, `staleness_reasons`. | Already sufficient for `selectDefaultReadyStrategy()`; no BFF change required for PROD-003 itself. |
| Zero/no-ready empty state | Same aggregate response with `strategies: []` or all non-`ready`. | UI-only branching; confirmed no fake/static fallback strategy is introduced. |
| Publish/deploy gap | Nonprod deploy (`scripts/deploy_nonprod_vm.sh`) sources frontend from `https://github.com/ajoe734/execute-plans.git`; this repo's `execute-plans/` mirror is a separate git history. | Before parent/PROD-006 claims hosted proof, the merged `eab6e0cfd` change (or an equivalent commit) must land on the real `ajoe734/execute-plans` `dev` branch and be redeployed; `deployment.json`'s `commit` field is the authoritative check. |
| Workshop → Trading Room handoff action | `StrategyWorkshopPage` already supports `onAddToTradingRoom`; `agora-main.tsx` does not pass it. | Out of PROD-003's stated scope (Trading-Room-side default entry), but downstream dynamic-workflow work (`AG-DYNUI-PROD-005`) should not assume this wiring already exists. |
| Tie-break ordering for default strategy pick | `selectDefaultReadyStrategy()` order is `dashboard_recipe_id` present → highest pending events → monitoring priority → title. | No design-pack or BFF contract document was found pinning this exact order; reviewer should confirm this matches intended operator priority (e.g. should strategies with more pending decisions truly outrank recipe presence in every case) or accept it as a reasonable implementation default. |

---

## 5. Operator Journey Packet

### Journey A: Zero strategies (cold start)

1. Operator opens `/agora/trading-room` with no query params.
2. BFF `GET /bff/agora/trading-room` returns `strategies: []`.
3. `TradingRoomDefaultEntry` renders the "Strategy Workshop is the next step"
   panel and an active `Open Strategy Workshop` button.
4. Clicking the button calls `handleTabChange("strategy-workshop")`, switching
   the Agora tab to the Workshop without a full navigation/reload.

### Journey B: Strategies exist, none ready

1. BFF returns one or more strategies, all `readiness_state !== "ready"`.
2. `TradingRoomDefaultEntry` renders a readiness card per strategy, sorted
   `conditional` → `stale` → `blocked` → `ready`, each showing
   `readinessReason()` copy, version id, candidate count, and pending count.
3. `Review readiness` on any card routes to the Workshop tab (same
   `onOpenWorkshop` handler; it does not yet target the specific workshop
   session id for that strategy — see §2 gap 2 and the Workshop
   deep-link/context gap already tracked in the `AG-DYNUI-PROD-001` sidecar
   packet).

### Journey C: At least one ready strategy (auto-entry)

1. BFF returns one or more strategies with `readiness_state === "ready"`.
2. `selectDefaultReadyStrategy()` deterministically picks one from live data.
3. `TradingRoomPage` renders `StrategyWorkspaceView` for that strategy
   immediately — the proposal/workspace/version/rollback surface — with no
   manual `?strategyId=...` URL edit.
4. `StrategyLensSwitcher` shows the auto-selected strategy as active so the
   operator can see why that strategy is active and switch manually if needed.

### Journey D: Hosted proof (not yet completed by this sidecar or parent)

1. Confirm the real `ajoe734/execute-plans` repo has an equivalent commit to
   `eab6e0cfd` on its `dev` branch.
2. Run `scripts/deploy_nonprod_vm.sh` (or the standard nonprod deploy path) and
   re-check `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
   for a `commit`/`sourceRef` that includes the PROD-003 change.
3. Load the hosted `/agora/trading-room` for a zero-strategy scope, a
   has-strategies-none-ready scope, and a has-ready-strategy scope; capture
   screenshots for each, matching the parent's acceptance bullet on hosted
   proof.
4. Confirm the BFF calls remain strict (`VITE_BFF_MODE=live`,
   `VITE_BFF_FALLBACK=strict`) throughout.

---

## 6. Parent / Reviewer Checklist

Before `Claude2` approves `AG-DYNUI-PROD-003` into `review_approved`, verify:

- [ ] The three default-entry states (zero strategies / not-ready / ready)
  match §5 Journeys A–C against the actual merged code, not just this
  packet's description.
- [ ] `selectDefaultReadyStrategy()`'s tie-break order is accepted as intended
  operator priority, or a follow-up is filed if it should differ.
- [ ] The parent's own acceptance bullet "Close only after branch, PR, checks,
  merge, deploy when needed, and hosted proof" is read literally: `dev` merge
  in this pantheon repo is not the same as a deploy to the hosted dev FE. The
  publish gap in §2/§4 should be closed (or explicitly waived with a reason)
  before `AG-DYNUI-PROD-006`'s hosted E2E gate is expected to pass.
- [ ] The Workshop→Trading-Room `onAddToTradingRoom` wiring gap is either
  filed against `AG-DYNUI-PROD-005` or explicitly accepted as out of scope,
  rather than silently assumed to exist.
- [ ] No hardcoded/fake strategy data was introduced (confirmed by this
  sidecar's diff read — all branches read `aggregate.strategies` from the BFF
  response).

---

## 7. Parent Boundary Notes

Owned by `AG-DYNUI-PROD-003` parent (already implemented in `eab6e0cfd`):

- default-entry branching logic in `TradingRoomPage.tsx`;
- `selectDefaultReadyStrategy()` and `TradingRoomDefaultEntry` component;
- `onOpenWorkshop` wiring in `agora-main.tsx` for the Trading-Room-side entry
  point.

Not owned by this sidecar or the PROD-003 parent:

- publishing the change to the standalone `ajoe734/execute-plans` repo and
  redeploying the hosted dev FE (deploy/publish ownership; likely
  `AG-DYNUI-PROD-006` hosted gate or a dedicated publish step);
- wiring `onAddToTradingRoom` from Workshop back into Trading Room
  (`AG-DYNUI-PROD-005` dynamic workflow closeout, or a new task);
- workshop deep-link/context propagation for `strategy-workshop/:workshopId`
  (tracked separately in the `AG-DYNUI-PROD-001`/`AG-DYNUI-PROD-002` sidecar
  packets);
- root error diagnostics and stale-bundle recovery (`AG-DYNUI-PROD-004`);
- BFF route/schema/registry/governance runtime changes — none were found
  necessary for this feature.

---

## 8. Recommended Parent Closeout Evidence

Before the parent moves from `review` to `done`, record:

- confirmation that `eab6e0cfd` (or an equivalent) is present on the real
  `ajoe734/execute-plans` `dev` branch;
- a fresh `deployment.json` read showing a `commit`/`sourceRef` that includes
  this change;
- hosted screenshots for the three default-entry states in §5;
- explicit acceptance or follow-up ticket for the tie-break ordering and the
  `onAddToTradingRoom` gap named in §2/§6.

---

## 9. Reviewer Handoff

Reviewer (`Claude2`) should verify:

1. This packet is support-only and does not mutate canonical truth, runtime
   code, frontend code, route registry, or governance behavior.
2. The implementation snapshot in §3 accurately reflects the merged diff
   (`git diff 28744d78d eab6e0cfd -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx execute-plans/src/entries/agora-main.tsx`).
3. The publish/hosted-proof gap in §2/§4 is a legitimate open item against the
   parent's own acceptance text, not a sidecar overreach into parent scope.
4. The Workshop-join gap is correctly scoped as adjacent/downstream, not as a
   PROD-003 blocker.
5. Parent can use this packet without treating it as review approval for
   `AG-DYNUI-PROD-003` itself.

---

## 10. Verification Notes

Verification was source inspection and one anonymous hosted read probe only.
No runtime, frontend, canonical, registry, governance, deploy, or hosted
environment changes were made.

Commands used:

```bash
git status --short
git branch --show-current
git log --oneline -5
grep -n "AG-DYNUI-PROD-003" ai-status.json
AI_NAME=Claude ./scripts/ai-status.sh show AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF
AI_NAME=Claude ./scripts/ai-status.sh show AG-DYNUI-PROD-003
cat docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-003-default-route-dynamic-entry.md
find support/sidecars -iname "*AG-DYNUI-PROD*"
sed -n '1,320p' support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md
sed -n '1,200p' support/sidecars/AG-DYNUI-PROD-001/AG-DYNUI-PROD-001-SIDECAR-BFF-HANDOFF.md
ls -la /home/lupin/code/execute-plans
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans remote -v
gh pr view 2860 --repo ajoe734/execute-plans --json number,title,url,mergeCommit,headRefName,state,files
git show ec5d902fc --stat
git diff 28744d78d eab6e0cfd -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
git diff 28744d78d eab6e0cfd -- execute-plans/src/entries/agora-main.tsx
find services/control-plane/bff -iname "*trading_room*"
rg -n "readiness_state|dashboard_recipe_id|staleness_reasons|candidate_count|monitoring_state" services/control-plane/bff/agora/trading_room/router.py
cat .gitmodules
git log --oneline -3 -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
cat docs/frontend/execute-plans-dev-hosting.md
grep -rl "execute-plans" scripts/git/ scripts/*.sh
curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
grep -n "execute-plans" scripts/git/task_finalize.sh scripts/git/task_start.sh scripts/git/worker_commit.py
git log --oneline -10 -- execute-plans/
rg -n "onAddToTradingRoom|Add to Trading Room" execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx execute-plans/src/entries/agora-main.tsx
```

---

## 11. Closeout Confirmation

`Claude2` approved this sidecar (`review_approved`) with review notes confirming
the packet's factual claims against the repo and hosted state. At owner
finalization (`Claude`, 2026-07-04) the two open gaps named in §2/§4 were
re-checked and are still current:

- `rg -n "onAddToTradingRoom" execute-plans/src/entries/agora-main.tsx` still
  returns no match — the Workshop→Trading-Room callback remains unwired.
- `curl .../deployment.json` still reports `commit=702b236a...`, predating
  `eab6e0cfd` — the hosted publish gap is still open.

No further sidecar-owned changes were needed; this section only records that
the approved packet's claims were re-verified as still true immediately before
closing the task to `done`.
