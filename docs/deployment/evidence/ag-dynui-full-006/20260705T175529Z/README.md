# AG-DYNUI-FULL-006 Hosted Live Workflow Evidence

Captured: 2026-07-05T17:55Z

Owner: Codex
Reviewer: Codex

## Scope

This packet records durable evidence for the hosted live AG-DYNUI-FULL-006
workflow. It proves that the Agora Trading Room path was no longer
fixture-backed and that the hosted FE/BFF completed the dynamic workspace
workflow end to end.

It does not close the AG-DYNUI-FULL board by itself. `AG-DYNUI-FULL-007`
remains responsible for board/archive reconciliation and residual design-parity
hardening.

## Deployment

- Execute-plans PR:
  `https://github.com/ajoe734/execute-plans/pull/187`
- Execute-plans merge commit:
  `37f8e320ac9a3fed621bfe3d36d34138f2b7c73d`
- Integration gate:
  `https://github.com/ajoe734/execute-plans/actions/runs/28749332352`
- Pantheon deploy inputs:
  - PR #3033, merge `3e553bb3a1c4e2d8572d233c3030349249b99d75`
  - PR #3034, merge `f010383fd367c8b960f6341c0c3c4ad93c1865cd`
  - PR #3035, merge `d002ed5a7fcec5c30c8fee13efd6cb6c30fbf8fb`
- Hosted manifest: `deployment.json`

The hosted manifest reports `VITE_BFF_MODE=live`,
`VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`.

## Browser Proof

The Playwright gate used hosted FE
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` and live BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` with auth subject
`pantheon-dev-browser` and tenant `pantheon-dev`.

The gate did not use `page.route`, `route.fulfill`, or fixture-backed Agora
responses. Evidence summaries:

- `live-summary-desktop.json`
- `live-summary-mobile.json`
- `hosted-browser-proof-summary.json`
- `hosted-trading-room-final.png`

The raw summaries preserve the original `/tmp/ag-dynui-full-006-*` screenshot
paths produced during the test run. The full screenshot set is also attached to
the execute-plans integration gate artifact for run `28749332352`.

## Proven Workflow

Both desktop and mobile runs completed:

1. Live workshop readiness/cards discovery with `highest_ready_gate` set to
   `trading_room`.
2. Required card types:
   `user_strategy_description`, `completeness_update`, `readiness_gate`.
3. Strategy Workshop "Add to Trading Room" handoff.
4. Workspace proposal creation.
5. Workspace proposal acceptance.
6. Grid layout edit and save.
7. Widget revision proposal creation and keep-copy acceptance.
8. Dashboard version history readback.
9. Rollback to an earlier dashboard version.

The final hosted probe reported:

```json
{
  "hasAgora": true,
  "hasReady": true,
  "hasFailed": false,
  "responseCount": 3,
  "badResponses": []
}
```

## Result

`AG-DYNUI-FULL-006` is functionally proven and published. The remaining work is
not another live-workflow implementation task; it is the `AG-DYNUI-FULL-007`
closeout task to reconcile stale board state, archive the evidence, and record
any residual design-parity risks.
