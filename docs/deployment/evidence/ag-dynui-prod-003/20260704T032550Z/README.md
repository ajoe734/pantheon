# AG-DYNUI-PROD-003 — Local-Dev-Server Screenshot Evidence

Captured 2026-07-04 as the owner-finalization closeout evidence called for by
the task's reviewer note ("capture that screenshot evidence, or an explicit
local-dev-server equivalent, before finalizing this task to done").

## Why local-dev-server instead of the hosted dev FE

The hosted dev FE (`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`)
deploys from the **standalone** `ajoe734/execute-plans` GitHub repo, not the
`pantheon` monorepo's in-tree `execute-plans/` mirror that this task's
original implementation (PR #2860, commit `eab6e0cfd`) landed in. Three prior
sidecar investigations (`AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF{,-FOLLOWUP-2,
-FOLLOWUP-3}`) confirmed the standalone repo had independently diverged and
never received this feature. As the reassigned owner, I re-implemented the
same default-entry behavior directly against the standalone repo's current
`TradingRoomPage.tsx` in `ajoe734/execute-plans` PR #173
(`task/AG-DYNUI-PROD-003-default-route-dynamic-entry`), validated with that
repo's own full test suite (1093/1093), `tsc --noEmit`, and `npm run build`.

Capturing hosted screenshots still requires a human-approved
`workflow_dispatch` of `Pantheon Nonprod Deploy` against `dev` *after* PR #173
merges there — that step is intentionally left for the chair/human gate (see
`ai-status.json` note on this task) rather than attempted by this agent.
These two screenshots are the interim local-dev-server evidence.

## `agora-trading-room-default-route.png` / `.json` — no-strategy case

Real, unmodified build of PR #173's branch, run with
`npx vite --port 8082` and the dev proxy (`PANTHEON_BFF_BASE_URL`) pointed at
the **actual live dev BFF** (`pantheon-lupin-dev-bff...`), using the
repo-committed `.env` dev bearer token. No mocking, no fixtures. A direct
`curl` against the same BFF endpoint (recorded in the JSON) independently
confirms the live tenant scope (`operator:pantheon-dev-browser`) currently
has zero strategies — so this screenshot is genuine live-BFF evidence for the
"no-strategy" default-entry state (`trading-room-workshop-empty-entry`, CTA to
Strategy Workshop), replacing the old inert `All Strategies` / empty-table
shell.

## `agora-trading-room-ready-strategy-auto-entry.png` / `.json` — ready-strategy case

**Not live tenant data.** The live dev BFF scope genuinely has zero
strategies right now (see above), and dev writes are disabled
(`VITE_BFF_REAL_WRITES=false`), so there is no real tenant data available to
demonstrate the ready-strategy path without fabrication. This capture runs
the same real build/route/proxy path, with a Playwright network-level
`page.route()` mock of `GET /bff/agora/trading-room` shaped exactly like the
`TradingRoomAggregate` contract (same shape as the reviewed unit-test
fixtures) returning one `readiness_state: "ready"` strategy. It demonstrates
`selectDefaultReadyStrategy()` / `effectiveStrategyId` auto-entering the
strategy's workspace with no manual URL surgery and the correct lens tab
becoming selected — proving the reviewed logic, not fabricating shipped
product behavior (no hardcoded strategy data exists in the product code
itself; this fixture only exists in the disposable capture script, not
committed anywhere).

## Reproduction

Capture scripts are not committed (throwaway harness, not product code):
`/tmp/execute-plans-agdynui003-port/capture-evidence.mjs` and
`capture-evidence-ready.mjs` in the session that produced this evidence.
