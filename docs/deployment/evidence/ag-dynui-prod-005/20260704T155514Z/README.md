# AG-DYNUI-PROD-005 — dynamic workflow closeout evidence

Post-merge hosted-deploy evidence for `execute-plans` PR #176 ("wire
workshop route handoff"), the implementation PR that closes the V11
Trading Room dynamic workflow wiring gap tracked by this task.

## Deploy confirmation

Hosted dev FE bundle is served with `nocache=0089eea81467`, the short
SHA of PR #176's implementation commit (`0089eea AG-DYNUI-PROD-005:
wire workshop route handoff`, merged as `eaad3fa` into
`ajoe734/execute-plans` `dev`). `git merge-base --is-ancestor 0089eea
origin/dev` on a real `ajoe734/execute-plans` checkout confirms the
commit is on `dev`.

## Evidence files

- `hosted-browser-bff-probe-2026-07-04.md` — generic hosted
  browser↔BFF probe (`scripts/probe-hosted-browser-bff.mjs` from
  `execute-plans`, run with `PANTHEON_HOSTED_PROBE_PATH=/agora/trading-room`
  and `PANTHEON_HOSTED_REQUIRED_BFF_PATHS=/bff/agora/trading-room`).
  Confirms the deployed `/agora/trading-room` bundle issues its BFF
  reads against the intended live host
  (`pantheon-lupin-dev-bff.35.201.239.38.sslip.io`), not the old BFF
  host or the frontend's own origin: `GET /bff/agora/trading-room` and
  `GET /bff/agora/trading-room/decision-events` both return `200`,
  zero requests hit the old BFF URL, zero failed requests, zero
  console errors.

## How this was captured

Run from the real `ajoe734/execute-plans` `task/AG-DYNUI-PROD-005`
checkout (HEAD `0089eea`, confirmed ancestor of `origin/dev`):

```
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
PANTHEON_HOSTED_PROBE_PATH=/agora/trading-room
PANTHEON_HOSTED_REQUIRED_BFF_PATHS=/bff/agora/trading-room
node scripts/probe-hosted-browser-bff.mjs
```

This is a deploy-freshness and BFF-wiring probe, not an authenticated
interactive walkthrough. It proves the merged implementation is live
and talking to the correct BFF; it does not exercise the full
proposal → accept → layout-patch → widget-revision → rollback flow
under authentication with screenshots. That interactive hosted E2E
proof (desktop + mobile screenshots across the full V11 flow) is
explicitly owned by `AG-DYNUI-PROD-006` (Hosted Winner Branch E2E
publish gate), which depends on this task and is dispatched next in
the packet's execution order.

## Local verification (this task's own gate)

Local automated verification for the full V11 dynamic workflow ran
against the real implementation and passed:

- Backend (`services/control-plane` repo, this checkout):
  `python3 -m pytest bff/agora/trading_room/test_trading_room.py -q`
  → 45 passed. Covers idempotency-key enforcement, `If-Match`
  optimistic concurrency (stale-etag rejection), cross-user scope
  isolation (`test_workspace_cross_user_read_is_forbidden`), widget
  allowlist enforcement and code-injection rejection
  (`test_workspace_view_and_widget_mutations_are_registry_validated`,
  `test_workspace_rejects_servant_direct_patch_and_code_injection`),
  widget-revision apply/keep-copy, and version-history rollback.
- Frontend (`ajoe734/execute-plans` `task/AG-DYNUI-PROD-005` checkout,
  HEAD `0089eea`): `npx vitest run
  src/agora/pages/trading-room/TradingRoomPage.test.tsx` → 56 passed.
  Covers proposal generation/accept, layout patch (move/resize/
  remove/restore/duplicate/add-registered-widget) with workspace ETag
  + idempotency key, widget-revision proposal apply/keep-copy/cancel,
  version rollback with current ETag, and typed 403/409/422/501
  failures that surface without any fake-success fallback.
