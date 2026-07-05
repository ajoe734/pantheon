# AG-DYNUI-FULL-001 Source Truth And Parity Matrix

Owner: Codex  
Reviewer: Claude  
Date: 2026-07-05

This is the owner-scoped artifact for wave 0 of the Agora DYNUI full
production recovery packet. It supplements the packet index with the design
source decision, current FE/BFF truth, and continue/blocker matrix needed by
downstream `AG-DYNUI-FULL-*` tasks.

## Source Decision

The raw design archive is still not recovered:

- `/home/lupin/code/pantheon/AI Trading Desk Design.zip`: missing
- `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip`: missing
- task worktree repo root: no `AI Trading Desk Design.zip`

The durable design source for implementation remains:

- `docs/04/agora_design_pack_dynui_2026-06-28/README.md`
- `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md`
- `docs/04/agora_design_pack_dynui_2026-06-28/closeout.md`

The extracted reference at `/tmp/ai-trading-desk-design/` is currently
readable and contains the V10/V11/V6/V4 design requirements, `Agora.dc.html`,
and screenshots. It is useful for inspection but not durable task truth.

The readable closure zips are supporting contract/design closure references,
not a replacement for the raw visual design archive:

- `/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`
- `/home/lupin/code/pantheon-live-root-cleanup-archive-20260627T124239Z/Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip`

If downstream work needs a raw visual/prototype detail not represented in the
6/28 committed pack or the currently readable `/tmp` extraction, open a
blocker. Do not promote the closure zips to raw visual source and do not
reconstruct from memory.

## Current Delivery Truth

- Hosted FE deployment manifest reports `execute-plans` source branch `dev`,
  deployed commit `f0600b89f5b6ad2aa028e8e2705b7dd1d1dc4828`, deployed at
  `20260705T061334Z`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and
  `VITE_BFF_REAL_WRITES=false`.
- `execute-plans` `origin/dev` is the current hosted dev source. `origin/main`
  is old at `64a963119e85f2e91efbedbd83c4fbd97c7c2e20` and is not the hosted
  dev delivery source.
- `/home/lupin/code/execute-plans` is dirty and ahead/behind; new FE edits must
  use a clean task worktree from the intended remote base.
- `/home/lupin/code/pantheon/.fe-ep` and the pantheon vendored
  `execute-plans/` mirror are historical/stale only and must not be used as
  deployment evidence.
- Hosted BFF `/healthz`, `/livez`, and `/readyz` return HTTP 200.
- Hosted BFF `/openapi.json` returns HTTP 200 and exposes the Trading Room
  proposal, workspace, layout, widget revision, version, and rollback route
  family.
- Direct unauthenticated BFF calls to `/bff/agora/trading-room` and
  `/bff/agora/trading-room/decision-events` return HTTP 401 `AUTH_REQUIRED`.
  That is expected without browser auth and is not a BFF outage signal.

## Continue / Blocker Matrix

| Area | Source requirement | Current FE truth | Live BFF truth | Continue or blocker |
| --- | --- | --- | --- | --- |
| Raw design zip | Exact raw archive should be durable. | Not applicable. | Not applicable. | Blocker for raw-source restoration. Continue only from 6/28 pack plus readable `/tmp` extraction. |
| 6/28 design pack | Non-static V10/V11/V6/V4 source map is canonical for work routing. | FE workers can continue against the committed pack. | BFF workers can continue against committed schemas and OpenAPI. | Continue. |
| Closure zips | Supporting closure evidence only. | Not current hosted FE truth. | Supports contract context, not live proof. | Continue as support; blocker if treated as raw visual source replacement. |
| Frontend source | Active source must be standalone `ajoe734/execute-plans`. | Hosted manifest is `dev` commit `f0600b89`; local checkout dirty. | Not applicable. | Continue from clean task worktrees only. |
| Shell architecture | Agora should not be accidental Management shell chrome. | `origin/dev` has `/agora` through `AgoraLayoutRoute`, `LiveStatusBanner`, and `TradingDeskLayout`; PR #171 merged at `467d930957bf109405fa50a5bc252ff66ec3a7ee`. | Not applicable. | Continue. |
| Default Trading Room entry | Default route must not be inert empty aggregate shell. | PR #173 merged at `691f2ec56af9bbc592814563558c001860d8bc7f`; zero-strategy live screenshot evidence exists. | Current packet evidence says aggregate can still return `strategies: []`. | Continue for empty-state entry; blocker for real ready-strategy live proof. |
| Dynamic route family | Proposal, accept, workspace, layout, revision, versions, rollback must exist. | `origin/dev` imports `WorkspaceProposalPreview`, `WorkspaceGridEditor`, and `WorkspaceWidgetRevisionDrawer`; PR #176 merged at `eaad3fa90d7c55a4476ed8dcda0063457933a1cc`. | Live OpenAPI exposes the route family. | Continue. |
| Full live workflow | Proposal through rollback must run without fixtures. | PROD-006 source/evidence exists, but post-initial steps used route fixtures. | No live strategy reaches `trading_room` readiness yet. | Blocker for production-level E2E; route to FULL-002..006. |
| Error diagnostics | Root errors must expose status/code/request/correlation and safe retry. | `origin/dev` still has a root branch that renders only `Failed to load Trading Room.` in `TradingRoomPage.tsx`; client-side `TradingRoomBffError` exists but is not surfaced there. | BFF structured errors include `AUTH_REQUIRED` and correlation id. | Blocker. Re-verify or port PROD-004 diagnostics in standalone `execute-plans`. |
| CI gates | Production claims need visible gates green or explicitly waived. | `execute-plans` PR #177 merged and deploy succeeded, but its PR `integration-gate` rollup is FAILURE. Current tip PR #179 also shows `integration-gate` FAILURE. | BFF health and OpenAPI are live, but that does not replace CI gate truth. | Blocker for "all gates green" production language. |
| Auth boundary | Do not relax auth. | Hosted FE is strict live mode with safe writes off. | No-token BFF reads return 401; authenticated browser evidence is the right proof path. | Continue. |
| Cache/stale bundle | Shell and manifest should be no-store. | FE route and `deployment.json` return no-store headers in this pass. | Not applicable. | Continue. |

## Required Follow-Up From This Matrix

1. `AG-DYNUI-FULL-001` may close only as a source-truth/parity-matrix
   artifact. It must not certify production completion.
2. `AG-DYNUI-FULL-002` and `AG-DYNUI-FULL-003` need to make live workshop
   cards/readiness and ready strategy materialization real before hosted E2E
   can stop using fixtures.
3. `AG-DYNUI-FULL-004` must keep FE work in a clean `execute-plans` worktree
   and avoid `.fe-ep` or the pantheon vendored mirror.
4. `AG-DYNUI-FULL-005` and `AG-DYNUI-FULL-006` must explicitly prove no
   `page.route()` or network-response fixtures on the production gate path.
5. `AG-DYNUI-FULL-006` or a narrow diagnostic task must reconcile the failed
   `integration-gate` state on execute-plans PR #177/#179.
6. Raw archive recovery remains open until `AI Trading Desk Design.zip` exists
   at a durable path or a reviewed blocker formally limits visual parity to the
   committed 6/28 source map.

## Verification Commands

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
