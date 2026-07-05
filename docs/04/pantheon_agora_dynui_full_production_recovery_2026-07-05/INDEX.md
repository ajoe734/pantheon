# Agora DYNUI Full Production Recovery - 2026-07-05

Status: source-truth recovery and production gap matrix; not a production
completion certificate

Task: `AG-DYNUI-FULL-001`  
Owner: Codex  
Reviewer: Claude

## Decision

The original raw design archive is still not restored:

- `/home/lupin/code/pantheon/AI Trading Desk Design.zip`: missing
- `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip`: missing
- current task worktree repo root: no `AI Trading Desk Design.zip`

The durable design source for this recovery remains the committed 2026-06-28
design pack:

- `docs/04/agora_design_pack_dynui_2026-06-28/README.md`
- `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md`
- `docs/04/agora_design_pack_dynui_2026-06-28/closeout.md`

The volatile extracted reference is currently readable at
`/tmp/ai-trading-desk-design/` and still contains the V10/V11/V6/V4 design
documents, `Agora.dc.html`, and screenshot references. It can be used as a
local inspection aid, but it is not durable task truth.

The available closure zips are not a replacement for the raw design archive:

- `/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`
  lists strategy dialogue, scoring, widget registry, policy, skill, and
  dispatch-unblock documents.
- `/home/lupin/code/pantheon-live-root-cleanup-archive-20260627T124239Z/Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip`
  lists v1.3 contract, schema, workshop, trading-room aggregate, and E2E
  documents.

Those closure packs are supporting contract/design closure evidence. They do
not contain the full V10/V11 visual prototype and screenshot archive, so they
must not be promoted to canonical raw visual source. If a downstream worker
needs raw archive material not already represented in the committed 6/28 pack
or the readable `/tmp` extraction, the correct outcome is a blocker.

## Evidence Checked

- `AG-DYNUI-SRC-001` source map and closeout: PR #2538, merge
  `64036dbebb5d24b967cadf75e69b6983c582257d`.
- `AG-DYNUI-PROD-001` source/task truth map: raw zip missing, 6/28 pack is
  the committed continuation source, `.fe-ep` is stale/historical only.
- Live FE deployment manifest at
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`:
  commit `f0600b89f5b6ad2aa028e8e2705b7dd1d1dc4828`, source branch `dev`,
  deployed `20260705T061334Z`, `VITE_BFF_MODE=live`,
  `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`.
- Live FE route `/agora/trading-room`: HTTP 200 with no-store cache headers.
- Live BFF `/healthz`, `/livez`, `/readyz`: HTTP 200, service ready.
- Live BFF `/openapi.json`: HTTP 200 and exposes the Trading Room proposal,
  workspace, layout, widget revision, version, and rollback route family.
- Unauthenticated direct BFF reads for `/bff/agora/trading-room` and
  `/bff/agora/trading-room/decision-events`: HTTP 401 `AUTH_REQUIRED`, which
  is expected without a browser auth token and is not the authenticated live
  proof.
- `execute-plans` `origin/dev`: commit `f0600b89f5b6ad2aa028e8e2705b7dd1d1dc4828`.
  `origin/main` is old (`64a963119e85f2e91efbedbd83c4fbd97c7c2e20`) and is
  not the current hosted dev delivery source.
- `execute-plans` local checkout `/home/lupin/code/execute-plans` is dirty and
  ahead/behind; it is not a safe edit source. New FE work must use a clean task
  worktree from the intended remote base.

## Gap Matrix

| Area | Design source requirement | Current FE truth | Live BFF truth | Old closeout truth | Classification |
| --- | --- | --- | --- | --- | --- |
| Raw source archive | Raw `AI Trading Desk Design.zip` should be readable for full V10/V11 visual source. | Not applicable. | Not applicable. | 6/28 closeout previously verified the raw archive, but 7/03 and this pass both find it missing. | Blocker for any claim that raw source is restored. Continue only from committed 6/28 pack plus readable `/tmp` extraction. |
| Durable design source | Do not rebuild from screenshots or memory; use V10/V11/V6/V4 docs, prototype, and screenshot map. | FE workers can continue against the committed 6/28 source map. | BFF workers can continue against committed schemas and OpenAPI route family. | `AG-DYNUI-SRC-001` froze the source map and non-static invariants. | Continue. The durable source is `docs/04/agora_design_pack_dynui_2026-06-28/`. |
| Closure packs | Use closure packs for contract and design closure context only. | They do not define the current hosted FE implementation. | They support contract expectations but do not prove live routes. | 6/20 and 6/21 closure zips are readable from known external paths. | Continue as supporting references; blocker if treated as a raw visual-source replacement. |
| Frontend source truth | Active dev FE must come from `ajoe734/execute-plans`, not `.fe-ep` or the pantheon vendored mirror. | Hosted manifest reports `execute-plans` `dev` commit `f0600b89`; local checkout is dirty and not safe for edits. | Not applicable. | `AG-DYNUI-PROD-001` already marked `.fe-ep` stale/historical. | Continue from clean `execute-plans` task worktrees only. |
| Shell architecture | Agora should not be an accidental Management PlatformShell tab. | `origin/dev` has `/agora` as a sibling route with `AgoraLayoutRoute`, `LiveStatusBanner`, and `TradingDeskLayout`, not Management top chrome. | Not applicable. | `AG-DYNUI-PROD-002` merged execute-plans PR #171 at `467d930957bf109405fa50a5bc252ff66ec3a7ee`; hosted proof later closed. | Continue. |
| Default Trading Room entry | `/agora/trading-room` must not land on inert empty aggregate shell. | `origin/dev` includes `selectDefaultReadyStrategy` and dynamic default entry behavior. | Authenticated browser evidence in PROD-003 shows live zero-strategy default entry; ready-strategy capture uses disclosed route fixture because live tenant has no ready strategy. | `AG-DYNUI-PROD-003` execute-plans PR #173 merged at `691f2ec56af9bbc592814563558c001860d8bc7f`. | Continue for FE behavior; blocker remains for fully live ready-strategy data proof. |
| BFF route family | V11 proposal, accept, workspace, layout patch, widget revision, versions, rollback must exist. | FE `origin/dev` imports `WorkspaceProposalPreview`, `WorkspaceGridEditor`, and `WorkspaceWidgetRevisionDrawer`, with tests for rollback and mutation paths. | Live `/openapi.json` exposes proposal, workspace, layout, widget, revision, version, and rollback routes. Root health is ready. | Backend tests in PROD-005 evidence cover idempotency, `If-Match`, scope isolation, allowlists, code-injection rejection, revision apply/keep-copy, and rollback. | Continue. |
| Full dynamic workflow | Proposal, accept, grid edit, widget revision, version history, and rollback should be wired through strict BFF contracts. | execute-plans PR #176 merged at `eaad3fa90d7c55a4476ed8dcda0063457933a1cc`; FE tests passed in closeout. | Hosted BFF wiring probe for PROD-005 saw `/bff/agora/trading-room` and `/decision-events` return 200 in browser context. | PROD-005 is marked done with backend 45 tests and frontend 56 tests. | Continue, with final live-data caveat below. |
| Hosted E2E | Full V10-to-V11 desktop/mobile flow should be proven against hosted FE and live BFF. | PR #177 merged at `2862e2a57c350335ae208aca2f9a203906dee2a2`; hosted FE deploy run `28716752521` succeeded. | PROD-006 screenshots and summaries exist under `/tmp`; steps 1-3 are live, steps 4-10 use BFF-shaped `page.route()` fixtures because no live strategy reaches `trading_room` readiness. | PROD-006 records the fixture disclosure explicitly. | Blocker for a fully live production-complete claim; continue for UI/BFF source validation. |
| Error diagnostics | Root Trading Room errors must preserve status/code/request/correlation and not collapse to generic-only failure. | `execute-plans` `origin/dev` still contains a root error branch that renders only `Failed to load Trading Room.` in `TradingRoomPage.tsx`; `TradingRoomBffError` exists in the client but is not surfaced there. | Direct unauthenticated BFF returns structured `AUTH_REQUIRED` with correlation id, proving the BFF can provide diagnostics. | PROD-004 notes diagnostics work and review, but earlier notes warn the active implementation risk was the pantheon-vendored mirror rather than the standalone hosted repo. | Blocker. Standalone `execute-plans` must port or re-verify PROD-004 diagnostics before production closeout. |
| CI and release gates | Production closeout should not claim all required checks green if visible gates fail. | `execute-plans` PR #177 is merged, but its PR `integration-gate` rollup is FAILURE. PR #179, current deployed `dev` tip, also has `integration-gate` FAILURE. | Hosted FE deploy for #177 succeeded, and FE route/BFF health probes work. | PROD-006 doc says deploy and post-deploy E2E passed, but the visible PR check state is not all green. | Blocker for "all checks green" release language until failures are explained, waived with evidence, or repaired. |
| Live data readiness | The design expects Strategy Workshop to produce a strategy ready for Trading Room proposal flow. | UI can render the flow against contract-shaped data. | Live tenant currently has zero ready strategies in captured evidence. | PROD-003 and PROD-006 disclose route fixtures for ready-strategy/workspace steps. | Blocker for fully live E2E; route to the upstream servant/persona/readiness pipeline workstream. |
| Auth boundary | Agora must not bypass BFF auth or relax backend auth. | Hosted FE is built with live strict BFF mode and safe writes off. | Direct no-token calls return 401 `AUTH_REQUIRED`; authenticated browser probes from existing evidence return 200. | LIVE-AUTH recovery PRs restored browser-session auth headers without relaxing backend auth. | Continue. |
| Cache/stale bundle | SPA shell and deployment metadata must not keep stale bundles alive. | FE route and `deployment.json` return no-store headers; hashed assets remain immutable in prior probes. | Not applicable. | Cache/header repair PR #2845 and later hosted probes confirm policy. | Continue. |

## Required Follow-Up

1. Restore `AI Trading Desk Design.zip` to the expected durable path or record
   an explicit long-lived blocker that the raw archive cannot be recovered.
2. Port or re-verify PROD-004 error diagnostics in the standalone
   `ajoe734/execute-plans` source, then prove the hosted root error state is
   not generic-only.
3. Investigate or explicitly waive with evidence the failed `integration-gate`
   checks visible on execute-plans PR #177 and PR #179.
4. Produce a fully live ready-strategy path, or route the missing
   servant/persona/readiness pipeline as a separate blocker before claiming
   full production E2E.
5. Keep downstream FE work on clean `execute-plans` task worktrees. Do not use
   `/home/lupin/code/pantheon/.fe-ep` or the pantheon vendored mirror as
   deployment evidence.

## Commands Used For This Recovery

```sh
test -f '/home/lupin/code/pantheon/AI Trading Desk Design.zip'
test -f '/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip'
python3 -m zipfile -l '/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip'
python3 -m zipfile -l '/home/lupin/code/pantheon-live-root-cleanup-archive-20260627T124239Z/Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip'
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans rev-parse origin/main
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,statusCheckRollup
gh pr view 173 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,statusCheckRollup
gh pr view 176 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,statusCheckRollup
gh pr view 177 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,statusCheckRollup
gh pr view 179 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,statusCheckRollup
curl -sS -D /tmp/ag-dynui-full-fe-headers.txt -o /tmp/ag-dynui-full-fe.html -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room
curl -sS -D /tmp/ag-dynui-full-deployment-headers.txt -o /tmp/ag-dynui-full-deployment.json -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS -D /tmp/ag-dynui-full-bff-healthz-headers.txt -o /tmp/ag-dynui-full-bff-healthz.json -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz
curl -sS -D /tmp/ag-dynui-full-bff-livez-headers.txt -o /tmp/ag-dynui-full-bff-livez.json -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/livez
curl -sS -D /tmp/ag-dynui-full-bff-readyz-headers.txt -o /tmp/ag-dynui-full-bff-readyz.json -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/readyz
curl -sS -D /tmp/ag-dynui-full-bff-openapi-headers.txt -o /tmp/ag-dynui-full-bff-openapi.json -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/openapi.json
jq -r '.paths | keys[] | select(test("trading-room|trading-intents"))' /tmp/ag-dynui-full-bff-openapi.json
find /tmp -maxdepth 1 -type f -name 'agora-dynui-prod-e2e-*' -print | sort
```
