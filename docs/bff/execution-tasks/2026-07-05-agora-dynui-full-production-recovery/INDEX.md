# Agora DYNUI Full Production Recovery Execution Packet - 2026-07-05

Status: task-scoped recovery packet for `AG-DYNUI-FULL-001`

Primary archive:

- `docs/04/pantheon_agora_dynui_full_production_recovery_2026-07-05/INDEX.md`

This packet restores the actionable source truth and gap matrix for the Agora
dynamic UI production recovery lane. It does not certify the hosted route as
fully production-complete.

## Current Source Truth

Use this source order for all downstream Agora DYNUI work:

1. `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md`
2. `docs/04/agora_design_pack_dynui_2026-06-28/README.md`
3. `docs/04/agora_design_pack_dynui_2026-06-28/closeout.md`
4. Readable local extraction `/tmp/ai-trading-desk-design/` as inspection aid
   only.
5. Closure packs only as supporting contract/design closure context:
   - `/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`
   - `/home/lupin/code/pantheon-live-root-cleanup-archive-20260627T124239Z/Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip`

Do not treat the closure zips as the canonical raw V10/V11 visual design
archive. The raw archive is still missing at both expected durable paths:

- `/home/lupin/code/pantheon/AI Trading Desk Design.zip`
- `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip`

## Current Delivery Truth

- Active hosted dev FE is `ajoe734/execute-plans` branch `dev`, deployed at
  commit `f0600b89f5b6ad2aa028e8e2705b7dd1d1dc4828`.
- Hosted FE manifest reports `VITE_BFF_MODE=live`,
  `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`.
- Hosted BFF root health endpoints `/healthz`, `/livez`, and `/readyz` return
  HTTP 200.
- Hosted BFF `/openapi.json` exposes the Trading Room proposal, workspace,
  layout, widget revision, version, and rollback route family.
- Direct unauthenticated Agora BFF reads return HTTP 401 `AUTH_REQUIRED`; use
  authenticated browser-session evidence for live data claims.
- `/home/lupin/code/execute-plans` is dirty and ahead/behind; create clean
  task worktrees for edits.
- `/home/lupin/code/pantheon/.fe-ep` and the pantheon vendored `execute-plans/`
  mirror are not deployment sources.

## Continue / Blocker Matrix

| Work item | Decision | Reason |
| --- | --- | --- |
| Continue standalone Agora shell work | Continue | `execute-plans` PR #171 merged and `origin/dev` routes `/agora` through `AgoraLayoutRoute` outside Management `PlatformShell`. |
| Continue default Trading Room entry work | Continue with caveat | PR #173 merged and live zero-strategy hosted evidence exists. Ready-strategy evidence still uses a disclosed route fixture because the live tenant has no ready strategy. |
| Continue BFF contract/runtime work | Continue | Live OpenAPI exposes V11 route family; backend tests in PROD-005 cover idempotency, ETag, scope isolation, widget allowlist, revision apply/keep-copy, versions, and rollback. |
| Continue dynamic workflow FE work | Continue | PR #176 merged and hosted BFF-wiring probe shows browser requests hit the intended live BFF host. |
| Use 6/20 and 6/21 closure zips | Continue as support only | They are readable and useful for contract context, but they are not the raw visual design archive. |
| Claim raw design archive restored | Blocker | `AI Trading Desk Design.zip` is missing from both expected durable paths. |
| Claim generic error diagnostics are closed | Blocker | Standalone `execute-plans` `origin/dev` still has a root branch rendering only `Failed to load Trading Room.` in `TradingRoomPage.tsx`; BFF provides structured errors but the page does not surface them there. |
| Claim all visible release gates green | Blocker | `execute-plans` PR #177 and current tip PR #179 show `integration-gate` FAILURE in GitHub rollup despite successful deploy evidence. |
| Claim fully live V10-to-V11 E2E | Blocker | PROD-006 summaries disclose steps 1-3 live and steps 4-10 BFF-shaped fixtures because no live strategy reaches `trading_room` readiness yet. |
| Treat unauthenticated 401 as BFF outage | Do not block | Direct curl without browser auth correctly returns `AUTH_REQUIRED`; authenticated browser evidence is the right proof surface. |

## Required Follow-Up Tasks

This packet does not materialize new task IDs. It should be used to route or
reopen the existing Agora DYNUI production lanes:

1. **Raw design archive restoration**
   - Owner lane: source/task truth.
   - Required result: restore `AI Trading Desk Design.zip` to a durable path,
     or record a permanent blocker explaining why only the committed 6/28 pack
     can be used.
2. **PROD-004 standalone diagnostics repair**
   - Owner lane: error diagnostics and stale-bundle recovery.
   - Required result: port or re-verify structured Trading Room error state in
     `ajoe734/execute-plans`, with tests and hosted proof that the root state is
     not generic-only.
3. **CI gate reconciliation**
   - Owner lane: hosted publish gate.
   - Required result: explain, waive with evidence, or repair the failed
     `integration-gate` runs on #177 and #179 before any final "all gates green"
     production statement.
4. **Fully live readiness pipeline**
   - Owner lane: upstream servant/persona/readiness pipeline, then hosted E2E.
   - Required result: a real live strategy reaches `trading_room` readiness and
     exercises proposal, accept, grid edit, widget revision, version history,
     and rollback without route fixtures.

## Downstream Guardrails

- Do not rebuild Agora from imagination, screenshots alone, or static mocks.
- Do not use `.fe-ep`, the pantheon vendored `execute-plans/` mirror, or a
  dirty `/home/lupin/code/execute-plans` checkout as source evidence.
- Do not relax BFF auth to make probes pass.
- Do not claim full production closeout from a deploy manifest alone.
- Do not hide route fixtures. If a proof uses `page.route()` or any mock, state
  exactly which steps are live and which are fixture-backed.
- Keep write paths governed: current hosted FE has `VITE_BFF_REAL_WRITES=false`
  unless an operator explicitly enables real write-path testing.

## Verification Summary

Commands and external probes used by `AG-DYNUI-FULL-001` are recorded in the
primary archive. The high-signal results were:

- raw archive tests: missing
- closure zip listing: readable
- `/tmp/ai-trading-desk-design/`: readable
- hosted FE route and deployment manifest: HTTP 200
- hosted BFF health/readiness: HTTP 200
- hosted BFF OpenAPI: HTTP 200 with Trading Room dynamic route family
- unauthenticated Agora BFF reads: HTTP 401 `AUTH_REQUIRED`
- `execute-plans` PRs #171, #173, #176, #177: merged
- `execute-plans` PRs #177 and #179: visible `integration-gate` failure
- PROD-006 E2E summaries: present, with disclosed live/fixture split
