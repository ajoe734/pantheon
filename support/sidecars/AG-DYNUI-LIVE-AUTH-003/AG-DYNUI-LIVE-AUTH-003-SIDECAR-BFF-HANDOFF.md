# AG-DYNUI-LIVE-AUTH-003 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-AUTH-003` |
| Parent title | Agora Trading Room frontend BFF auth headers |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-03` |
| Mutates canonical | `false` |
| Parent PR | `#2820` merged to `dev` as `c04a7db9452ccca07e5af3b3f1c313f51d861beb` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend runtime code, registry
behavior, governance behavior, RuntimeBinding, capital, or broker authority.
The parent owner and reviewer decide whether and how to absorb this packet into
the mainline AUTH-003 review/closeout.

---

## 1. Purpose

`AG-DYNUI-LIVE-AUTH-003` follows the live Agora Trading Room recovery chain:

- `AG-DYNUI-LIVE-DEFAULT-001` identified the live frontend delivery/composition
  path and the old white Trading Room layout symptom.
- `AG-DYNUI-LIVE-AUTH-002` fixed the Pantheon BFF Trading Room router so it can
  forward `pantheon_session` cookies into identity extraction.
- AUTH-002 post-merge evidence still reported browser `401` responses for
  `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events` while
  `/bff/me` succeeded in the same authenticated browser session.

The parent AUTH-003 patch responds to the remaining transport gap in the
Pantheon-tracked `execute-plans/` frontend mirror: Trading Room client fetches
used `credentials: "include"` and `Accept`, but did not add an `Authorization`
header when the browser session is bearer-backed. Parent PR #2820 adds a shared
BFF header builder and routes the Trading Room client through it.

This packet records what #2820 changed, what the BFF/front-end query handoff now
looks like, and what proof remains before the parent can claim live recovery.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecars are L0 support artifacts and must not override canonical L1/L2 truth. |
| `.orchestrator/task-briefs/ag_dynui_live_auth_003_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, and frontend handoff materials. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Codex2`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Parent is `review`, owner `Claude`, reviewer `Codex`; acceptance still requires merged PR, dev FE deploy, live browser `200` responses for aggregate and decision-events, and absence of `Failed to load Trading Room`. |
| `gh pr view 2820 --repo ajoe734/pantheon --json ...` | Parent PR #2820 merged at `2026-07-03T12:23:32Z` as `c04a7db9452ccca07e5af3b3f1c313f51d861beb`; visible GitHub checks passed. |
| `gh pr diff 2820 --repo ajoe734/pantheon --name-only` | Parent PR changed `.orchestrator/task-briefs/ag_dynui_live_auth_003.md`, `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`, `tradingRoom.test.ts`, `headers.ts`, and `headers.test.ts`. |
| `git show 75a0e857...:execute-plans/src/lib/bff-v1/headers.ts` | New shared `buildHeaders()` adds `Accept`, optional `Authorization`, optional `X-Tenant-Id`, and caller-supplied extras. |
| `git show 75a0e857...:execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Five Trading Room client functions now call `buildHeaders()` while preserving `credentials: "include"`. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Live dev FE still serves `execute-plans` commit `6556534b937e433b40cf94d87b8ab25a792aed35`, strict live BFF mode. |
| `curl -fsS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Returned HTTP `502` during this sidecar run, so live BFF readiness was not provable from the public dev endpoint. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room` | Returned HTTP `502`; unauthenticated fail-closed behavior could not be reassessed while the dev BFF endpoint was unavailable. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## 3. Parent Delta Summary

Parent PR #2820 landed one commit:

`75a0e857cf60c56fb317523a3a6209b000ec57d1`
`AG-DYNUI-LIVE-AUTH-003: add shared BFF auth headers to tradingRoom.ts`

Files changed:

| File | Parent delta |
|---|---|
| `execute-plans/src/lib/bff-v1/headers.ts` | Adds `buildHeaders()` plus `setAuthProvider()` test hook. Reads bearer token from injected provider, `pantheon.bff.bearerToken`, legacy `pantheon_operator_token`, or `VITE_BFF_DEV_BEARER_TOKEN`; reads tenant from `pantheon.bff.tenantId` or legacy `pantheon_tenant_id`. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Replaces local `{ Accept: "application/json" }` read headers and local mutation header object with `buildHeaders()`, preserving `credentials: "include"` and existing request semantics. |
| `execute-plans/src/lib/bff-v1/headers.test.ts` | Adds focused unit coverage for no-auth, provider injection, storage fallback, tenant header, and extra header merge/conflict behavior. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.test.ts` | Adds Authorization coverage for all five client functions and preserves existing mutation header behavior tests. |
| `.orchestrator/task-briefs/ag_dynui_live_auth_003.md` | Records the parent task brief generated by the worker workspace. |

The parent commit message records:

- `npx vitest run src/lib/bff-v1` passed with 68 tests.
- `npm run gate:agora` succeeded.
- Full `npx vitest run` still had 18 pre-existing unrelated failures in
  `AssistantModeBadge.test.tsx` and `client.test.ts`.
- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` in this repo exposes
  only five functions today; it does not include workspace/proposal/widget
  revision endpoints, so the parent task's workspace-mutation acceptance bullet
  does not map to a current client function.

Visible PR #2820 checks passed: Commit trailers, Runtime mirror guard, Smoke
acceptance, and Orchestrator Sync.

---

## 4. BFF Query Gap Matrix

AUTH-003 closes a frontend request-header gap in the current Pantheon-tracked
Trading Room client. It does not add or relax BFF routes.

| Frontend helper | Route | AUTH-003 handoff state |
|---|---|---|
| `getTradingRoom` | `GET /bff/agora/trading-room` | Calls `buildHeaders({ method: "GET" })`; sends `Authorization` when a bearer token is available and preserves cookie-backed `credentials: "include"`. |
| `getTradingRoomStrategy` | `GET /bff/agora/trading-room/strategies/{strategyId}` | Same shared read header path; preserves `404 -> null`. |
| `listDecisionEvents` | `GET /bff/agora/trading-room/decision-events` | Same shared read header path; preserves query filters and response `ETag` capture; does not add mutation headers. |
| `getDecisionEvent` | `GET /bff/agora/trading-room/decision-events/{id}` | Same shared read header path; preserves `404 -> null`. |
| `decideOnEvent` | `POST /bff/agora/trading-room/decision-events/{id}/decisions` | Calls `buildHeaders({ method: "POST", extra })`; preserves `Content-Type`, caller `If-Match`, caller `Idempotency-Key`, caller `X-Request-Id`, JSON body, and cookie-backed `credentials: "include"`. |

Important boundaries:

1. `buildHeaders()` adds bearer and tenant headers only when values are
   available; it does not synthesize or expose secrets in evidence.
2. Caller-supplied extras win on conflicts, so exact `If-Match`,
   `Idempotency-Key`, and `X-Request-Id` values are preserved for writes.
3. The unauthenticated path must still fail closed at the BFF. Adding browser
   bearer headers must not make no-token/no-cookie curl succeed.
4. `decideOnEvent()` remains decision-support only. It does not place orders,
   create RuntimeBinding, bind capital, or approve promotion.

---

## 5. Operator Journey Handoff

### Journey A: Initial Trading Room Load

1. Operator opens `/agora/trading-room` in the Pantheon dev frontend.
2. Browser already has either a cookie-backed session or a bearer token in the
   shared BFF auth provider/storage/dev-token path.
3. `getTradingRoom()` and `listDecisionEvents()` send both
   `credentials: "include"` and shared BFF headers.
4. BFF accepts the authenticated credential path and returns `200` for both
   aggregate and decision-events.
5. UI renders the AGORA Trading Room shell and queue/empty state; it does not
   render `Failed to load Trading Room`.

### Journey B: Strategy Detail

1. Operator navigates to a strategy-scoped Trading Room view.
2. `getTradingRoomStrategy(strategyId)` sends shared read headers and cookie
   credentials.
3. `404` remains a normal missing-strategy result (`null`), while `401` still
   means neither accepted auth path was present/valid.

### Journey C: Decision Event Write

1. Operator reviews a decision event and submits a choice.
2. `decideOnEvent()` sends shared auth headers plus the exact event `ETag`,
   idempotency key, and request id supplied by the caller.
3. BFF records a governed decision-support intent only.
4. Existing write guards remain: `401` for missing/invalid auth, `403` for role
   failure, `409` for idempotency conflict, and `412` for stale `If-Match`.

---

## 6. Remaining Parent Proof Gap

The parent PR is merged, but this sidecar cannot treat the live recovery as
proven yet.

Current live probes during this sidecar run:

| Probe | Result | Implication |
|---|---|---|
| `GET /deployment.json` on dev FE | `execute-plans` commit `6556534b937e433b40cf94d87b8ab25a792aed35`, strict live BFF mode | Does not prove AUTH-003 is served by the live dev FE. The parent/reviewer must still record the actual frontend delivery path and deployed commit. |
| `GET /health` on dev BFF | HTTP `502` | Dev BFF public endpoint was unavailable during this sidecar run; browser/BFF live acceptance cannot be completed from this evidence. |
| Unauthenticated `GET /bff/agora/trading-room` | HTTP `502` | Cannot reassess fail-closed `401 AUTH_REQUIRED` while the dev BFF endpoint is unavailable. |

Do not close parent AUTH-003 from PR merge and local tests alone. The parent
acceptance requires post-deploy browser evidence with the actual live dev FE and
BFF endpoints.

---

## 7. Parent Review Checklist

For parent review/closeout, verify:

1. `buildHeaders()` does not log, persist, or commit raw bearer/session secrets.
2. `tradingRoom.ts` has no remaining direct local `{ Accept: "application/json" }`
   fetch headers on its five public functions.
3. Read helpers send Authorization when a bearer token is available and still
   rely on `credentials: "include"` for cookie-backed sessions.
4. `decideOnEvent()` preserves caller-provided `If-Match`,
   `Idempotency-Key`, and `X-Request-Id` exactly.
5. Tests cover all five Trading Room client functions for bearer auth.
6. Any claim about workspace/proposal/widget revision auth is either removed or
   explicitly marked not applicable to the current Pantheon-tracked client,
   because those functions are not present in `tradingRoom.ts` in PR #2820.
7. Live dev FE deployment evidence names the deployed commit or explains the
   current mirror-to-real-frontend delivery boundary.
8. Live browser evidence proves:
   - `/bff/agora/trading-room` returns `200`;
   - `/bff/agora/trading-room/decision-events` returns `200`;
   - `Failed to load Trading Room` is absent;
   - old white Trading Desk markers remain absent;
   - no raw token/cookie value is committed in evidence.
9. Once the dev BFF endpoint is healthy again, unauthenticated direct curl to
   `/bff/agora/trading-room` should return `401 AUTH_REQUIRED`, not `200`.

---

## 8. Verification Performed For This Sidecar

| Command | Result |
|---|---|
| `git status -sb` / `git branch --show-current` / `git remote -v` | Started on `task/AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF`; remote `origin` is `https://github.com/ajoe734/pantheon.git`; initial dirty item was this generated sidecar task brief. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF` | Confirmed sidecar is active `in_progress`, owner `Codex2`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Confirmed parent is `review`; acceptance still requires live dev FE/browser proof after deploy. |
| `git fetch origin dev task/AG-DYNUI-LIVE-AUTH-003` and `git merge --ff-only origin/dev` | Fast-forwarded this sidecar branch to include parent merge commit `c04a7db9452ccca07e5af3b3f1c313f51d861beb` before writing the packet. |
| `gh pr view 2820 --repo ajoe734/pantheon --json ...` | Confirmed parent PR #2820 merged, head `75a0e857cf60c56fb317523a3a6209b000ec57d1`, visible checks passed. |
| `gh pr diff 2820 --repo ajoe734/pantheon --name-only` | Confirmed parent changed only the generated parent brief plus Pantheon-tracked `execute-plans` BFF client/test files. |
| `git show 75a0e857...:execute-plans/src/lib/bff-v1/headers.ts` and `tradingRoom.ts` | Verified shared header helper and Trading Room client call sites summarized above. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Confirmed live dev FE still serves `execute-plans` commit `6556534b937e433b40cf94d87b8ab25a792aed35` in strict live BFF mode. |
| `curl -fsS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Returned HTTP `502`; live BFF readiness not provable during this sidecar run. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room` | Returned HTTP `502`; unauthenticated fail-closed behavior not reassessed during this sidecar run. |

Prepared by Codex2 for the support-only BFF/frontend handoff sidecar.
