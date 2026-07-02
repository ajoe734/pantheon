# AG-DYNUI-LIVE-DEFAULT-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-DEFAULT-001` |
| Parent title | Fix live Agora Trading Room default route visual parity |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar task | `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-02` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code (in either repo
copy discussed below), or registry/governance behavior. Parent ownership and
review decide how to act on this packet.

---

## 1. Purpose

The parent task reports that live dev FE `/agora/trading-room` still renders
the old white "Trading Desk" skeleton for the no-strategy/empty state instead
of the dark AGORA dynamic workspace entry. This packet's job is to hand the
parent owner a compact diagnosis of *why*, the BFF query surface the page
depends on, the operator journey to reproduce and verify a fix, and the exact
repo/file boundary the fix must land in — before any implementation time is
spent in the wrong location.

**Headline finding:** this is very likely not a component-logic bug at all. It
is a **frontend delivery/composition gap**: the dark AGORA `TradingDeskLayout`
/ `TradingRoomPage` implementation (`AG-FE-DYNUI-005`) exists only in a
Pantheon-repo-tracked mirror copy under `execute-plans/`, and was never landed
in the actual `ajoe734/execute-plans` repo that the live dev FE is built and
deployed from. See §3 for the verified evidence.

---

## 2. Scope

In scope for this packet:

1. Confirm which repo/commit the live dev FE is actually built from.
2. Diagnose why the reported white skeleton still appears.
3. List the BFF query surface `TradingRoomPage` depends on.
4. Give the parent owner an operator journey and verification plan.
5. Flag the exact repo boundary the parent fix must target.

Non-goals:

- no edits to `pantheon/execute-plans/src/agora/*` (Pantheon-tracked mirror);
- no edits to `ajoe734/execute-plans` (the real frontend repo);
- no edits to BFF/backend Agora routes;
- no new frontend build, deploy, or Playwright run from this sidecar;
- no approval of the parent implementation.

---

## 3. Critical Finding: Two Diverged Frontend Trees

There are **two different copies** of the Agora Trading Room frontend code,
and only one of them is what live dev FE actually serves.

### 3.1 Copy A — Pantheon-repo-tracked mirror (not deployed)

Path: `execute-plans/` inside this `ajoe734/pantheon` repo (created by task
`AG-FE-000: add Agora/Management split entry, build, auth audience`, ~90
tracked files). This is a small, self-contained scaffold with a Vite
multi-entry build (`agora.html` + `management.html`, `vite.agora.config.ts`),
manual `window.history`-based routing in `src/entries/agora-main.tsx`, and
inline-style dark AGORA theming.

`AG-FE-DYNUI-005` ("dark AGORA visual parity") landed here. Confirmed by
reading the current worktree:

- `execute-plans/src/agora/TradingDeskLayout.tsx` defines
  `const C = { shellBg: "#111417", ... }` and renders a fully dark shell.
- `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` uses the
  same dark palette for loading/error/aggregate/empty states.

This copy is **not** what the live dev FE host serves (see §3.3).

### 3.2 Copy B — `ajoe734/execute-plans` (the real, deployed frontend repo)

Per the canonical hosting doc `docs/frontend/execute-plans-dev-hosting.md`:
"Active frontend repo: `ajoe734/execute-plans`" and "Local checkout:
`/home/lupin/code/execute-plans`". This is a much larger, single-SPA app
(`src/App.tsx` with `react-router-dom`, both `/agora/*` and
`/management/*` routes, ~hundreds of tracked files, Tailwind utility
classes via `cn()`).

Fetched directly from GitHub for this packet (`git fetch --depth 1 origin
main dev` against `https://github.com/ajoe734/execute-plans.git`):

- `origin/main` HEAD: `64a963119e85f2e91efbedbd83c4fbd97c7c2e20`
- `origin/dev` HEAD: `4b0b30c010b4158dded4cb77fdbb13c057f59536`
  (`Merge pull request #146 from ajoe734/task/evidence-operations-frontend-20260702`)

`src/agora/TradingDeskLayout.tsx` on `origin/dev` at that commit is the
**old, light-themed** component:

```tsx
<header className="flex h-12 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4" ...>
<nav aria-label="Trading desk sections" className="flex h-10 shrink-0 items-end gap-0 border-b border-slate-200 bg-white px-4" ...>
<footer className="flex h-10 shrink-0 items-center gap-0 border-t border-slate-200 bg-slate-50 px-4" ...>
```

It uses `react-router-dom` (`<Outlet />`, `useLocation`, `useNavigate`), not
the manual pathname router used in Copy A. `src/agora/pages/trading-room/TradingRoomPage.tsx`
on the same commit has no dark palette constant at all; its loading/error/root
states set no `background`, so they render on the page's default white
background (`trading-room-loading`, `trading-room-error`, and
`trading-room-page` all lack a `background` style; `strategy-list-empty`
renders `No strategies in the Trading Room.` in light-gray text `#94a3b8` on
that same white background). `git log origin/dev --oneline -- src/agora/TradingDeskLayout.tsx`
(depth-1) shows no dark-theme or `AG-FE-DYNUI-005`-equivalent commit history
for this file in the real repo.

Route wiring on the real repo (`src/routes/agora.tsx`, mounted in
`src/App.tsx`):

```
/agora            -> AgoraLayoutRoute (TradingDeskLayout, no children -> <Outlet/>)
  index           -> redirect to /agora/trading-room
  trading-room     -> AgoraTradingRoomRoute (TradingRoomPage)
  trading-room/:strategyId -> AgoraTradingRoomRoute
  strategy-workshop -> AgoraStrategyWorkshopRoute
  strategy-performance -> AgoraStrategyPerformanceRoute
  *                -> redirect to /agora/trading-room
```

### 3.3 Live verification that Copy B is what is deployed

`docs/frontend/execute-plans-dev-hosting.md` names the Pantheon-owned dev FE
host as `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`, built from
`ajoe734/execute-plans` `dev`. Live probes run from this sidecar worktree at
packet time:

```
$ curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260702T040955Z",
  "commit": "4b0b30c010b4158dded4cb77fdbb13c057f59536",
  "sourceRef": "4b0b30c010b4158dded4cb77fdbb13c057f59536",
  "sourceBranch": "dev",
  "feHost": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
  "bffHost": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
  "buildMode": { "VITE_BFF_MODE": "live", "VITE_BFF_FALLBACK": "strict", "VITE_BFF_REAL_WRITES": "false" }
}
```

The deployed commit (`4b0b30c0...`) is exactly the `origin/dev` HEAD of
`ajoe734/execute-plans` inspected in §3.2 — the one with the old white
`TradingDeskLayout`. This is airtight: **live dev FE renders the white
skeleton because that is genuinely what is built and deployed; it is not a
caching artifact and not a client-side conditional bug.**

`curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`
returns the SPA's `index.html` (`<title>Pantheon Management Console</title>`,
single unified bundle), consistent with the real repo's one-app
`react-router` shape rather than Copy A's separate `agora.html` multi-entry
build. This corroborates that the deployed app is Copy B, not Copy A.

### 3.4 What this means for the parent fix

`AG-DYNUI-LIVE-DEFAULT-001`'s listed candidate files
(`src/agora/TradingDeskLayout.tsx`,
`src/agora/pages/trading-room/TradingRoomPage.tsx`, `src/agora/trading-room/*`,
`src/lib/bff-v1/agora/tradingRoom.ts`) exist in **both** copies at different
paths (`pantheon/execute-plans/...` vs. the real `ajoe734/execute-plans`
checkout). If the parent owner edits only the Pantheon-tracked mirror (Copy
A), the change will look correct in this repo's diff but **will not affect
the live dev FE**, exactly repeating the composition gap already flagged by
the archived `AG-FE-DYNUI-005-SIDECAR-ACCEPTANCE-FOLLOWUP-3` packet
(`support/sidecars/AG-FE-DYNUI-005/AG-FE-DYNUI-005-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`),
which already warned: "No separate execute-plans repo PR or remote
`task/AG-FE-DYNUI-005*` branch was found" and "Parent closeout must record
delivery target and composition path."

Recommendation: the parent owner should implement and land the fix in the
real `ajoe734/execute-plans` repo (local checkout
`/home/lupin/code/execute-plans`, or a clean task worktree per
`docs/frontend/execute-plans-dev-hosting.md`), open a PR against that repo's
`dev`, and only then trigger/record a new dev FE deployment. Porting the same
fix into the Pantheon-tracked `execute-plans/` mirror (Copy A) afterward, for
whatever purpose that mirror still serves, is optional and secondary — it is
not live-facing.

---

## 4. BFF Query Surface (`TradingRoomPage`, real repo Copy B)

`src/lib/bff-v1/agora/tradingRoom.ts` in `ajoe734/execute-plans` calls these
routes, all under the resolved BFF base URL (`credentials: "include"`, no
direct fetch elsewhere in the page):

| Function | Route | Used by |
|---|---|---|
| `getTradingRoom` | `GET /bff/agora/trading-room` | Root aggregate load (strategies, queue summary, risk summary) |
| `getTradingRoomStrategy` | `GET /bff/agora/trading-room/strategies/{id}` | Strategy-level detail (used elsewhere in the page family) |
| `listDecisionEvents` | `GET /bff/agora/trading-room/decision-events` | Decision event queue; returns `items` + `ETag` |
| `getDecisionEvent` | `GET /bff/agora/trading-room/decision-events/{id}` | Single event detail |
| `decideOnEvent` | `POST /bff/agora/trading-room/decision-events/{id}/decisions` | Trader decision; requires `If-Match`, `Idempotency-Key`, `X-Request-Id` |
| workspace/proposal routes | `GET/POST /bff/agora/strategies/{id}/trading-room/proposals[...]`, `/bff/agora/trading-room/workspaces/{id}[...]` | Strategy workspace grid/proposal/version/widget-revision flows (out of scope for the default-route bug but share the same BFF base) |

Live BFF probe from this sidecar (auth omitted deliberately to confirm
fail-closed behavior, not to bypass auth):

```
$ curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
{"status":"ok","service":"operator-bff","version":"0.2.0","timestamp":"2026-07-02T04:46:43Z"}

$ curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room
{"error":{"code":"AUTH_REQUIRED", ..., "message":"Missing or invalid Authorization header", ...}}
```

The BFF is up and fails closed as expected. This packet did not attempt an
authenticated call; the parent owner's Playwright/browser evidence should
capture the authenticated aggregate response shape and confirm it matches
`TradingRoomAggregate` (`spec_version`, `strategies[]`, `queue_summary`,
`risk_summary`, `snapshot_at`, `data_cutoff`).

**BFF query gap to verify, not assumed:** this packet did not find evidence
that the BFF-side `/bff/agora/trading-room` contract differs between what
Copy A's `pantheon/execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` expects
and what Copy B's equivalent file expects — both declare the same
`TradingRoomAggregate`/`TradingDecisionEvent` shapes and the same `If-Match` /
`Idempotency-Key` / `X-Request-Id` write-path headers. The divergence in §3 is
purely a **UI/shell/routing** divergence, not a contract divergence. The
parent owner should still diff the two `tradingRoom.ts` files if the fix
touches data shape handling, since Copy B has additional workspace/proposal
functions Copy A does not.

---

## 5. Operator Journey To Reproduce And Verify

1. Navigate directly to
   `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`
   with no `strategyId` in the path (the reported no-strategy/empty case).
2. Confirm current (buggy) behavior: `trading-desk-command-bar`,
   `trading-desk-tab-bar`, and `trading-desk-bottom-strip` render with
   `bg-white` / `bg-slate-50` / `border-slate-200`; the page body under
   `trading-room-page` has no explicit background, so it renders white.
3. After the BFF aggregate loads, `strategy-list-empty` shows "No strategies
   in the Trading Room." in light gray text on that white background — this
   is the "old white Trading Desk skeleton" the parent task describes.
4. Confirm the network tab shows `GET /bff/agora/trading-room` and
   `GET /bff/agora/trading-room/decision-events` succeeding (200) against
   `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`; a genuinely empty
   aggregate (`strategies: []`) is an expected, valid BFF response and must
   not be treated as an error state by the fix.
5. After the parent fix lands in `ajoe734/execute-plans` and a new dev FE
   deployment is recorded (new `commit`/`deployedAt` in
   `GET /deployment.json`), repeat steps 1-3 and confirm the shell/tab
   bar/bottom strip and the no-strategy empty state render on the dark AGORA
   palette instead of white.
6. Repeat with an existing `strategyId` in the path
   (`/agora/trading-room/{id}`) to confirm the strategy workspace view is
   also dark, and that grid/proposal/widget-revision flows still function
   (do not regress `AG-FE-DYNUI-001` through `AG-FE-DYNUI-004` behavior,
   which live only in the real repo's history, not Copy A's).
7. Capture live Playwright DOM/screenshot evidence against the Pantheon-owned
   FE host above, per `docs/frontend/execute-plans-dev-hosting.md` §"Acceptance
   Smoke" — do not accept a Lovable URL or a local Copy A `agora.html`
   preview as acceptance evidence.

---

## 6. Frontend Handoff Rules For The Parent Fix

| Rule | Why |
|---|---|
| Implement in `ajoe734/execute-plans` (`/home/lupin/code/execute-plans` or a clean task worktree), not `pantheon/execute-plans/` | Only the real repo's build is deployed to live dev FE (§3.3). |
| Preserve `react-router-dom` `<Outlet/>`-based routing in `src/agora/TradingDeskLayout.tsx` and `src/routes/agora.tsx` | Copy B's routing shape differs from Copy A; do not port Copy A's manual pathname router. |
| Apply dark theming via the existing Tailwind/`cn()` styling convention used elsewhere in Copy B (`src/components/ui/*`), not by porting Copy A's inline `style={{ ... }}` objects wholesale | Keeps the fix consistent with the rest of the real app's design system and avoids introducing a second styling convention. |
| Explicitly set a dark background on `trading-room-loading`, `trading-room-error`, and the root `trading-room-page` container, not just the shell bars | Today none of those three set `background`; only recoloring the shell bars would leave the content area white. |
| Do not remove or reduce `strategy-list-empty`, `event-queue-empty`, or other `data-testid` hooks used by `TradingRoomPage.test.tsx` in the real repo | Preserves existing test coverage; this packet did not modify or re-run those tests. |
| Treat a genuinely empty BFF aggregate (`strategies: []`) as a valid state to theme, not an error to special-case away | The BFF is not the source of the white skeleton; do not add defensive/error-path logic that isn't needed. |
| Record the exact `execute-plans` commit and new `deployment.json` snapshot in parent closeout evidence | Matches the acceptance smoke and dev-hosting rule in `docs/frontend/execute-plans-dev-hosting.md`. |

---

## 7. Residual Items To Keep Visible

| Item | Owner to absorb | Why it matters |
|---|---|---|
| Whether Pantheon-tracked `pantheon/execute-plans/` (Copy A) should also be updated or retired | `AG-DYNUI-LIVE-DEFAULT-001` owner / chair | Copy A is not live-facing, but leaving it dark while Copy B was white was exactly the trap that produced this bug; decide whether Copy A should be kept in sync, clearly marked non-deployed, or removed. |
| `docs/contracts/agora/dev-compatibility-manifest.json` still shows `frontend.runtime_commit` as all-zero placeholder | parent owner or a follow-up compat-manifest task | The Agora dev-compatibility gate is not fully wired to the real deployed frontend commit; not required to fix the white-skeleton bug, but relevant to closeout evidence quality. |
| No live Playwright DOM/screenshot evidence was produced by this sidecar | parent owner | Parent acceptance requires live Playwright evidence per its own acceptance criteria; this packet only performed `curl`-level verification. |

---

## 8. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet and the generated sidecar task brief are changed |
| Canonical truth untouched | PASS if no L1 docs, BFF code, or either `execute-plans` copy's runtime code changed by this sidecar |
| Repo divergence claim is verifiable | PASS if `git fetch --depth 1 origin dev` against `ajoe734/execute-plans` and inspecting `src/agora/TradingDeskLayout.tsx` reproduces the `bg-white`/`border-slate-200` content quoted in §3.2 |
| Live deployment claim is verifiable | PASS if `GET https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` reports a `commit` whose `TradingDeskLayout.tsx` is the light-themed version |
| BFF surface list matches real repo | PASS if `src/lib/bff-v1/agora/tradingRoom.ts` in `ajoe734/execute-plans` exposes the routes listed in §4 |
| Recommendation points at the correct repo | PASS if the packet tells the parent owner to fix `ajoe734/execute-plans`, not `pantheon/execute-plans/` |

---

## 9. Verification Performed

| Command | Result |
|---|---|
| `git branch --show-current` / `git status` | Sidecar worktree on `task/AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF`; only pre-existing untracked file was the generated task brief. |
| `python3 -c "..."` against `/home/lupin/code/pantheon/ai-status.json` | Read live parent/sidecar task records (owner, reviewer, artifacts, acceptance) since neither task exists yet in the sidecar worktree's local `ai-status.json`. |
| `grep -n "AG-DYNUI-LIVE-DEFAULT-001" ai-activity-log.jsonl` (PANTHEON_STATUS_ROOT copy) | Confirmed dispatch history for both the parent and this sidecar task. |
| `find`/`Read` on `execute-plans/src/agora/TradingDeskLayout.tsx`, `.../pages/trading-room/TradingRoomPage.tsx`, `src/lib/bff-v1/agora/tradingRoom.ts`, `src/entries/agora-main.tsx`, `vite.agora.config.ts`, `agora.html` | Confirmed Copy A's dark theme, multi-entry build, and manual router. |
| `git clone`/`git fetch --depth 1 origin main dev` against `https://github.com/ajoe734/execute-plans.git` | Retrieved real repo `main` (`64a9631...`) and `dev` (`4b0b30c0...`) tips. |
| `git show origin/dev:src/agora/TradingDeskLayout.tsx`, `.../pages/trading-room/TradingRoomPage.tsx`, `src/routes/agora.tsx`, `src/App.tsx`, `src/lib/bff-v1/agora/tradingRoom.ts` | Confirmed Copy B's light theme, `react-router-dom` wiring, and BFF route list in §4. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Confirmed live dev FE is built from `ajoe734/execute-plans` `dev` at `4b0b30c010b4158dded4cb77fdbb13c057f59536`, deployed `2026-07-02T04:09:55Z`. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room` | Returned the unified SPA `index.html` (`Pantheon Management Console` title), consistent with Copy B's single-app shape. |
| `curl https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` and `/bff/agora/trading-room` | BFF healthy; unauthenticated aggregate call correctly returns `401 AUTH_REQUIRED`. |
| `Read support/sidecars/AG-FE-DYNUI-005/AG-FE-DYNUI-005-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md` | Confirmed this exact composition-gap risk (no execute-plans-repo PR found for `AG-FE-DYNUI-005`) was already flagged once before and evidently never closed. |
| `cat docs/frontend/execute-plans-dev-hosting.md` | Confirmed canonical hosting rule: real repo `ajoe734/execute-plans`, local checkout `/home/lupin/code/execute-plans`, Pantheon-owned FE/BFF hosts, and required acceptance-smoke evidence. |

No runtime tests were run for this sidecar because it changes only a support
artifact. This packet did not modify either `execute-plans` copy, run a
frontend build, or trigger a new dev FE deployment.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned, per
the task brief's read-order guidance.

---

## 10. Handoff And Review Status

This packet is ready for `Claude` review as support material for the
`AG-DYNUI-LIVE-DEFAULT-001` parent implementation. It is not an approval of
any parent implementation and does not itself change runtime, BFF, or
frontend code in either `execute-plans` copy.

Suggested review command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-LIVE-DEFAULT-001/AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-REVIEW.md \
REVIEW_NOTES_ZH="審查通過：sidecar packet 正確定位 live dev FE 實際部署自 ajoe734/execute-plans dev（非 Pantheon-tracked mirror），並提供 BFF query surface 與 operator journey||後續：parent owner 需在真實 execute-plans repo 落地修正並記錄新 deployment.json" \
./scripts/ai-status.sh approve AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF \
"Sidecar packet approved; support-only BFF/frontend handoff returned to owner for closeout."
```

If a future reader finds a factual mismatch (for example, a newer dev FE
deployment already fixed this), open a narrow follow-up packet with the exact
correction instead of changing canonical or parent runtime files from this
sidecar.

Prepared by `Claude2` for the `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF`
support slice.
