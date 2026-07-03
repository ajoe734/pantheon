# AG-DYNUI-LIVE-AUTH-003 BFF and Frontend Handoff Follow-up 4

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-AUTH-003` |
| Parent title | Agora Trading Room frontend BFF auth headers |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-03` |
| Mutates canonical | `false` |
| Previous packet | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Current execute-plans absorption PR | `ajoe734/execute-plans#148` open |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend runtime code, registry
behavior, governance behavior, RuntimeBinding, capital, broker authority, or
parent `AG-DYNUI-LIVE-AUTH-003` acceptance. The parent owner and reviewer decide
whether and how to absorb this packet into the mainline closeout.

---

## 1. Purpose

This fourth follow-up refreshes the BFF/frontend handoff after another
supervisor dispatch. The verified state is unchanged from follow-up 3:

- The public dev BFF is healthy.
- Unauthenticated direct Trading Room BFF probes fail closed with
  `401 AUTH_REQUIRED`.
- The dev frontend is still deployed from `execute-plans` commit
  `745ddd4b460fb263d261393accd003358149b289`.
- That deployed frontend commit still does not attach bearer/tenant auth headers
  from `tradingRoom.ts` call sites.
- `execute-plans` PR #148 remains the visible absorption PR for the broader live
  frontend `tradingRoom.ts` surface and is still open.

This packet does not prove parent AUTH-003 live recovery. It preserves the
current handoff boundary: BFF readiness and unauthenticated fail-closed behavior
are probeable, while authenticated browser success still depends on merging and
deploying PR #148 or an equivalent frontend patch, then running the live browser
verification.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar artifacts are L0 support records and must not override canonical L1/L2 truth. |
| `.orchestrator/task-briefs/ag_dynui_live_auth_003_sidecar_bff_handoff_followup_4.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff materials. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support packet work should be made durable with a task-scoped commit; no stash or broad staging. |
| `.orchestrator/skills/task-closeout-finalization.md` | This dispatch is not `review_approved`, so closeout/done is not the current action. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Sidecar is active `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Parent remains `review_approved`; owner closeout still depends on execute-plans PR #148 merge/deploy and authenticated live browser proof. |
| Prior packets under `support/sidecars/AG-DYNUI-LIVE-AUTH-003/` | Earlier sidecars recorded Pantheon PR #2820, the original BFF `502`, later BFF `200`/unauthenticated `401`, and the downstream execute-plans PR #148 handoff. |
| `gh pr view 148 --repo ajoe734/execute-plans --json ...` | PR #148 is open, head `c0e256aff3912a0f46887c8442482c09f35d5980`, base `dev`, `mergeStateStatus=CLEAN`, `mergeable=MERGEABLE`, and `integration-gate` succeeded. |
| `gh api repos/ajoe734/execute-plans/branches/dev` | execute-plans `dev` tip remains `745ddd4b460fb263d261393accd003358149b289`, merge PR #166, not the PR #148 auth fix. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Dev FE serves execute-plans commit `745ddd4b460fb263d261393accd003358149b289`, source branch `dev`, live strict BFF mode, real writes disabled. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Dev BFF returned HTTP `200`, service `operator-bff`, version `0.2.0`. |
| Unauthenticated `curl -sS -i` to `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events` | Both returned HTTP `401` with `AUTH_REQUIRED`; no-token/no-bearer access remains fail-closed. |
| Raw `execute-plans` source at deployed commit `745ddd4b...` | Deployed `agora/tradingRoom.ts` has local `Accept` headers and `mutationHeaders()` only; it does not call `authHeaders()` or `buildHeaders()`. |
| Raw `execute-plans` PR #148 head source | Adds `getAuthProvider()`/`authHeaders()` and spreads bearer/tenant headers into the broader Trading Room read/write helper surface. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## 3. Current State Summary

### Parent task state

Parent `AG-DYNUI-LIVE-AUTH-003` remains `review_approved`, not `done`. The
Pantheon-side mirror patch is merged through PR #2820, but the parent task's
live acceptance still requires:

- dev FE deployed from a commit containing the actual execute-plans Trading Room
  auth fix;
- authenticated browser proof that `/bff/agora/trading-room` returns `200`;
- authenticated browser proof that `/bff/agora/trading-room/decision-events`
  returns `200`;
- UI proof that `/agora/trading-room` does not show
  `Failed to load Trading Room` or old white Trading Desk layout markers.

This sidecar should not be used to bypass that owner closeout requirement.

### Live BFF state

The live dev BFF is healthy and fail-closed for unauthenticated Trading Room
access:

| Probe | Result | Handoff meaning |
|---|---|---|
| `GET /health` | HTTP `200`, service `operator-bff`, version `0.2.0` | Public dev BFF readiness is probeable. |
| Unauthenticated `GET /bff/agora/trading-room` | HTTP `401`, code `AUTH_REQUIRED` | No-token aggregate read remains fail-closed. |
| Unauthenticated `GET /bff/agora/trading-room/decision-events` | HTTP `401`, code `AUTH_REQUIRED` | No-token event list remains fail-closed. |

This is not authenticated browser success. It only proves the public BFF is
reachable and does not allow no-token Trading Room access.

### Live frontend state

The live dev frontend still serves:

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260703T124319Z",
  "commit": "745ddd4b460fb263d261393accd003358149b289",
  "sourceRef": "745ddd4b460fb263d261393accd003358149b289",
  "sourceBranch": "dev",
  "feHost": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
  "bffHost": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```

Direct source inspection at `745ddd4b...` shows the deployed frontend still has
the Trading Room auth transport gap:

- `src/lib/bff-v1/headers.ts` has auth-capable `buildHeaders()` and
  `getAuthProvider()` surfaces.
- `src/lib/bff-v1/agora/tradingRoom.ts` does not import `buildHeaders()` or
  `getAuthProvider()`.
- `tradingRoom.ts` does not define or use `authHeaders()`.
- Read helpers still pass local `{ Accept: "application/json" }` headers.
- `mutationHeaders()` still builds local `Accept`, `Content-Type`, `If-Match`,
  `Idempotency-Key`, and `X-Request-Id` headers without bearer/tenant auth.

The deployed commit is execute-plans PR #166, not the Trading Room auth fix.

---

## 4. BFF Query Gap Matrix

The current live `execute-plans` frontend has a broader `tradingRoom.ts` surface
than the Pantheon mirror file covered by PR #2820. The deployed commit still
lacks auth-bearing headers on that broader surface.

| Live frontend helper family at `745ddd4b...` | Deployed header behavior | PR #148 handoff state |
|---|---|---|
| Aggregate reads: `getTradingRoom`, `getTradingRoomStrategy` | `credentials: "include"` plus local `Accept`; no bearer/tenant header from the call site | PR #148 adds `authHeaders()` to both read calls. |
| Decision-event reads: `listDecisionEvents`, `getDecisionEvent` | `credentials: "include"` plus local `Accept`; no bearer/tenant header from the call site | PR #148 adds `authHeaders()` to both read calls. |
| Decision-event write: `decideOnEvent` | Local `Accept`, `Content-Type`, optional `If-Match`, `Idempotency-Key`, `X-Request-Id`; no bearer/tenant auth | PR #148 adds `authHeaders()` while preserving mutation headers. |
| Workspace proposal create/accept | Local `Accept`, `Content-Type`, optional idempotency key; no bearer/tenant auth | PR #148 adds `authHeaders()` to these write headers. |
| Workspace/proposal/workspace-version reads | `credentials: "include"` plus local `Accept`; no bearer/tenant header from the call site | PR #148 adds `authHeaders()` to these read headers. |
| Workspace layout, rollback, widget revision create/accept | Shared local `mutationHeaders()` without bearer/tenant auth | PR #148 updates `mutationHeaders()` to include `authHeaders()`. |

PR #148 intentionally uses the existing `getAuthProvider()` surface to add only
bearer and tenant headers. It does not adopt all of `buildHeaders()` because the
execute-plans tests assert that request/correlation/language headers remain
absent unless explicitly supplied.

---

## 5. Operator Journey Handoff

### Journey A: Current deployed frontend before PR #148 merge

1. Operator opens `/agora/trading-room` on the Pantheon dev frontend.
2. The SPA is served from execute-plans commit `745ddd4b...` in live strict BFF
   mode.
3. Trading Room client calls route to the live dev BFF.
4. Calls include browser credentials, but deployed Trading Room helpers do not
   attach bearer/tenant headers from the auth provider.
5. The BFF is healthy and fail-closed, so a bearer-required session can still
   see `401 AUTH_REQUIRED` on Trading Room BFF reads/writes.

### Journey B: Expected path after PR #148 merge and deploy

1. `execute-plans` PR #148 merges into `dev`, or an equivalent patch lands.
2. Dev FE deploys a commit that contains PR #148 head
   `c0e256aff3912a0f46887c8442482c09f35d5980`, or a descendant.
3. Trading Room helpers add bearer/tenant headers from `getAuthProvider()` while
   preserving `credentials: "include"`.
4. Authenticated browser proof confirms live `200` for both aggregate and
   decision-events routes.
5. UI proof confirms `/agora/trading-room` does not show
   `Failed to load Trading Room` or old white layout markers.

### Journey C: Writes remain governed support/workspace operations

The PR #148 path covers decision and workspace helper families, but it should
not be interpreted as runtime authority expansion. Reviewer/parent closeout
should still confirm:

- `decideOnEvent()` preserves caller `If-Match`, `Idempotency-Key`, and
  `X-Request-Id` while adding bearer auth.
- Workspace proposal/accept, layout/version, rollback, and widget revision
  helpers preserve existing idempotency and ETag behavior while adding bearer
  auth.
- No order route, RuntimeBinding, capital binding, broker authority, or
  promotion approval is introduced by the frontend auth transport fix.

---

## 6. Reviewer Checklist

For this sidecar review:

1. Confirm this packet only adds support material and the generated task brief.
2. Confirm BFF evidence is scoped correctly: `/health` `200` and no-token
   Trading Room `401 AUTH_REQUIRED` do not prove authenticated browser success.
3. Confirm deployed FE commit `745ddd4b...` is correctly described as live
   strict BFF mode but still missing Trading Room bearer/tenant headers.
4. Confirm execute-plans PR #148 is still open and is the visible downstream
   absorption PR for the broader live frontend Trading Room client surface.
5. Confirm parent AUTH-003 should remain owner-closeout blocked until a
   post-absorption deploy and authenticated browser probe pass.
6. Confirm no secret token, cookie, bearer value, session value, or browser
   credential is committed in this packet.

For parent AUTH-003 closeout after frontend absorption:

1. Merge or otherwise absorb execute-plans PR #148.
2. Redeploy dev FE and record the deployed `execute-plans` commit.
3. Run an authenticated browser probe without committing raw credentials.
4. Verify live `200` for aggregate and decision-events BFF calls.
5. Verify the Trading Room page does not show `Failed to load Trading Room` or
   the old white layout.
6. Keep unauthenticated direct BFF probes fail-closed with `401 AUTH_REQUIRED`.

---

## 7. Verification Performed For This Sidecar

| Command | Result |
|---|---|
| `git status -sb`, `git branch --show-current`, `git remote -v` | Started on `task/AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`; remote `origin` is `https://github.com/ajoe734/pantheon.git`; only initial dirty item was this generated task brief. |
| `git rev-parse HEAD`, `git rev-parse origin/dev` | Confirmed branch HEAD and `origin/dev` were both `6a9d58d9fa3bc0cb3e565010f2532f7d68b745d9` before writing this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Confirmed follow-up sidecar is active `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Confirmed parent AUTH-003 is `review_approved`; remaining owner closeout requires execute-plans PR #148 merge, dev FE deploy, and authenticated live browser proof. |
| `gh pr view 148 --repo ajoe734/execute-plans --json ...` | Confirmed PR #148 is open, head `c0e256aff3912a0f46887c8442482c09f35d5980`, base `dev`, `mergeStateStatus=CLEAN`, `mergeable=MERGEABLE`, and `integration-gate` success. |
| `gh api repos/ajoe734/execute-plans/branches/dev` | Confirmed execute-plans `dev` tip is still `745ddd4b460fb263d261393accd003358149b289`, merge PR #166, not the PR #148 auth fix merge. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Confirmed dev FE deployment `745ddd4b460fb263d261393accd003358149b289`, live strict BFF mode, real writes false. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Returned HTTP `200`, service `operator-bff`, version `0.2.0`. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room` | Returned HTTP `401` with `AUTH_REQUIRED`; no-token aggregate read is fail-closed. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room/decision-events` | Returned HTTP `401` with `AUTH_REQUIRED`; no-token decision-events read is fail-closed. |
| Raw `execute-plans` source reads at `745ddd4b...` | Confirmed deployed `tradingRoom.ts` does not route Trading Room helpers through auth-bearing headers. |
| Raw PR #148 head source at `c0e256aff...` | Confirmed `authHeaders()` is added from `getAuthProvider()` and spread into the broader live Trading Room helper surface. |

Prepared by Codex for reviewer `Claude`.
