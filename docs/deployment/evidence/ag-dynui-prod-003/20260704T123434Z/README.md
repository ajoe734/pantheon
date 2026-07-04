# AG-DYNUI-PROD-003 — Hosted Dev FE Screenshot Evidence

Captured 2026-07-04 after `ajoe734/execute-plans` PR #173 merged into that
repo's `dev` (merge commit `691f2ec56af9bbc592814563558c001860d8bc7f`) and
its `Pantheon Dev FE Deploy` workflow auto-redeployed the hosted dev FE.
This replaces the interim local-dev-server evidence in
`../20260704T032550Z/` now that hosted proof is possible.

## Deploy verification

- `curl -s https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
  reports `"commit": "691f2ec56af9bbc592814563558c001860d8bc7f"` —
  exactly the PR #173 merge commit, confirming the hosted FE is running the
  fix, not a stale bundle.
- `gh run list --repo ajoe734/execute-plans` shows `Pantheon Dev FE Deploy`
  completed successfully for that commit at `2026-07-04T12:22:40Z`.

## `agora-trading-room-hosted-default-route.png` — no-strategy case

Genuine live capture: Playwright navigated directly to
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`
with no network mocking. The BFF call
(`GET /bff/agora/trading-room`) returned a real live 200 response with zero
strategies for the current tenant scope. The rendered page shows the new
dynamic entry (`Dynamic Entry` / "Strategy Workshop is the next step",
"Strategies: 0", live `Snapshot`/`Data cutoff` timestamps, "Open Strategy
Workshop" CTA) — confirming the hosted default route no longer lands on the
old inert `All Strategies` / empty-table shell.

## `agora-trading-room-hosted-ready-strategy.png` — ready-strategy case

**Not live tenant data**, same caveat as the interim evidence: the live dev
BFF scope has zero strategies and dev writes are disabled
(`VITE_BFF_REAL_WRITES=false`), so no real ready strategy exists in this
tenant to demonstrate auto-entry without fabrication. This capture runs the
same hosted, already-redeployed build, with a Playwright network-level
`page.route()` mock of `GET /bff/agora/trading-room` shaped exactly like the
`TradingRoomAggregate` contract (identical to the reviewed unit-test fixture
in `TradingRoomPage.test.tsx`), returning one `readiness_state: "ready"`
strategy ("Alpha Momentum"). The screenshot shows the app auto-selecting the
"Alpha Momentum" tab (no manual URL surgery) and entering the workspace
proposal view — proving `selectDefaultReadyStrategy()` / `effectiveStrategyId`
auto-entry against the real hosted build, not fabricating shipped product
behavior (no hardcoded strategy data exists in product code; the fixture
only exists in the disposable capture script, not committed anywhere).

## Reproduction

Capture scripts were run from a throwaway location in the shared
`/home/lupin/code/execute-plans` checkout (for its installed `playwright`
dependency) and removed afterward — not committed anywhere, consistent with
the interim evidence's methodology.
