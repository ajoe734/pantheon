# AG-DYNUI-LIVE-AUTH-003 BFF and Frontend Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-AUTH-003` |
| Parent title | Agora Trading Room frontend BFF auth headers |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-03` |
| Mutates canonical | `false` |
| Previous sidecar | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF`, done via PR #2823 |
| Current execute-plans absorption PR | `ajoe734/execute-plans#148` open |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend runtime code, registry
behavior, governance behavior, RuntimeBinding, capital, or broker authority.
The parent owner and reviewer decide whether and how to absorb this packet into
the mainline AUTH-003 closeout.

---

## 1. Purpose

The first AUTH-003 BFF handoff packet closed the Pantheon-repo support sidecar
while the public dev BFF endpoint was returning HTTP `502` and the dev frontend
was still serving an older `execute-plans` commit. This follow-up records the
changed live situation:

- The public dev BFF endpoint is healthy again.
- Unauthenticated direct Trading Room BFF probes now fail closed with
  `401 AUTH_REQUIRED`.
- The dev frontend has redeployed to `execute-plans` commit
  `ea22b56c7747d0f0968e91188559ceff7a2dc7e1`, but that deployed source still
  has Trading Room fetch call sites that do not attach bearer auth headers.
- `execute-plans` PR #148 is open and appears to be the concrete frontend
  absorption PR for the live repo's broader Trading Room client surface.

This packet's main handoff is therefore not "AUTH-003 is live-proven." It is:
the BFF side is now probeable and fail-closed, while live frontend delivery still
depends on merging/deploying `execute-plans` PR #148 or an equivalent patch.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar artifacts are support records and must not override canonical L1/L2 truth. |
| `.orchestrator/task-briefs/ag_dynui_live_auth_003_sidecar_bff_handoff_followup_2.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff materials. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Follow-up sidecar is active `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Parent AUTH-003 is `review_approved`; owner closeout still must prove dev FE deploy and authenticated live browser success. |
| Prior packet `support/sidecars/AG-DYNUI-LIVE-AUTH-003/AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF.md` | Previous sidecar documented Pantheon PR #2820 and the earlier live gaps: stale FE commit and BFF `502`. |
| `gh pr view 2820 --repo ajoe734/pantheon --json ...` | Parent Pantheon PR #2820 is merged into `dev` as `c04a7db9452ccca07e5af3b3f1c313f51d861beb`; visible checks passed. |
| `gh pr view 2822` and `gh pr view 2823` | Previous sidecar packet and closeout PRs are both merged; latest `origin/dev` is `837be58d8ecf15397605aae359bda7cb08d3e8ec`. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Dev FE is now deployed at `execute-plans` commit `ea22b56c7747d0f0968e91188559ceff7a2dc7e1`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Dev BFF returned HTTP `200` with `{"status":"ok","service":"operator-bff"}`. |
| Unauthenticated `curl -sS -i` to `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events` | Both returned HTTP `401` with `AUTH_REQUIRED`, proving the unauthenticated path is fail-closed now that BFF is healthy. |
| Raw `execute-plans` source at deployed commit `ea22b56c...` | `headers.ts` has auth-capable `buildHeaders()`, but deployed `tradingRoom.ts` does not use it and still builds local headers at Trading Room call sites. |
| `gh pr view 148 --repo ajoe734/execute-plans --json ...` | PR #148 is open, titled "fix missing Authorization header on trading-room BFF calls"; its integration gate passed. |
| Raw `execute-plans` PR #148 head `c0e256aff...` | Adds local `authHeaders()` from `getAuthProvider()` and spreads it into Trading Room read/write header construction sites. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## 3. Current State Summary

### Pantheon repo state

Parent PR #2820 is already merged into Pantheon `dev`. In the Pantheon-tracked
`execute-plans/` mirror, AUTH-003 added a small shared header helper and routed
the five mirror-visible Trading Room helpers through it:

- `getTradingRoom`
- `getTradingRoomStrategy`
- `listDecisionEvents`
- `getDecisionEvent`
- `decideOnEvent`

The previous support sidecar is also done. It was merged through:

- PR #2822: packet add, merge commit
  `11dae28cb030e76dcf9fe0f749f8f719a400dc94`
- PR #2823: sidecar closeout addendum, merge commit
  `837be58d8ecf15397605aae359bda7cb08d3e8ec`

### Live BFF state

The live dev BFF is no longer in the previous `502` state:

| Probe | Result | Handoff meaning |
|---|---|---|
| `GET /health` | HTTP `200`, service `operator-bff` | BFF readiness is probeable again from the public dev endpoint. |
| Unauthenticated `GET /bff/agora/trading-room` | HTTP `401`, code `AUTH_REQUIRED` | No-token aggregate read remains fail-closed. |
| Unauthenticated `GET /bff/agora/trading-room/decision-events` | HTTP `401`, code `AUTH_REQUIRED` | No-token event list remains fail-closed. |

This is useful BFF evidence, but it is not the authenticated browser proof
required for parent AUTH-003 closeout.

### Live frontend state

The live dev frontend now serves:

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260703T123939Z",
  "commit": "ea22b56c7747d0f0968e91188559ceff7a2dc7e1",
  "sourceBranch": "dev",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```

That is a fresh frontend deployment, but the deployed `execute-plans` source
does not yet include the Trading Room bearer-header fix:

- `src/lib/bff-v1/headers.ts` at `ea22b56c...` has an auth-capable
  `buildHeaders()` implementation.
- `src/lib/bff-v1/agora/tradingRoom.ts` at `ea22b56c...` does not import or
  call `buildHeaders()`.
- The deployed `tradingRoom.ts` still passes local `{ Accept: "application/json" }`
  or local `mutationHeaders()` into Trading Room fetches.
- `mutationHeaders()` at the deployed commit adds `Accept`, `Content-Type`,
  `If-Match`, and `Idempotency-Key`, but not `Authorization` or `X-Tenant-Id`.

This means the live frontend deployment is newer than the stale commit from the
first sidecar, but it still has the auth transport gap for bearer-backed Trading
Room sessions.

---

## 4. BFF Query Gap Matrix

The live `execute-plans` Trading Room client is broader than the Pantheon mirror
file summarized by PR #2820. The deployed frontend source contains workspace
proposal, workspace layout/version, and widget revision helpers in addition to
the five mirror-visible aggregate/decision helpers.

| Live frontend helper family at `ea22b56c...` | Current deployed header behavior | PR #148 handoff state |
|---|---|---|
| Aggregate reads: `getTradingRoom`, `getTradingRoomStrategy` | `credentials: "include"` plus local `Accept` only | PR #148 spreads `authHeaders()` into both read calls. |
| Decision-event reads: `listDecisionEvents`, `getDecisionEvent` | `credentials: "include"` plus local `Accept` only | PR #148 spreads `authHeaders()` into both read calls. |
| Decision-event write: `decideOnEvent` | Local `Accept`, `Content-Type`, optional `If-Match`, `Idempotency-Key`, `X-Request-Id`; no bearer/tenant auth | PR #148 adds `authHeaders()` while preserving mutation headers. |
| Workspace proposal create/accept | Local `Accept`, `Content-Type`, optional idempotency key; no bearer/tenant auth | PR #148 adds `authHeaders()` to these write headers. |
| Workspace/proposal/workspace-version reads | `credentials: "include"` plus local `Accept` only | PR #148 adds `authHeaders()` to these read headers. |
| Workspace layout, rollback, widget revision create/accept | Shared local `mutationHeaders()` without bearer/tenant auth | PR #148 changes `mutationHeaders()` to include `authHeaders()`. |

Important boundary: PR #148 intentionally does not adopt the full `buildHeaders()`
behavior because the live frontend tests assert that unconditional
`X-Request-Id`, `X-Correlation-Id`, and `Accept-Language` headers stay absent
unless explicitly supplied. It uses the existing `getAuthProvider()` surface to
add only bearer and tenant headers.

---

## 5. Operator Journey Handoff

### Journey A: Current deployed frontend before PR #148 merge

1. Operator opens `/agora/trading-room` on the Pantheon dev frontend.
2. The SPA is served from `execute-plans` commit `ea22b56c...` in live strict
   BFF mode.
3. Trading Room client calls route to the live dev BFF, not the SPA fallback.
4. Calls include browser credentials, but deployed Trading Room helpers do not
   attach bearer auth headers.
5. The BFF is healthy and fail-closed, so a bearer-required session can still see
   `401 AUTH_REQUIRED` on Trading Room BFF reads/writes.

### Journey B: Expected path after PR #148 merge and deploy

1. `execute-plans` PR #148 merges into `dev`.
2. Dev FE deploys a commit at or after PR #148 head
   `c0e256aff3912a0f46887c8442482c09f35d5980`.
3. Trading Room helpers add bearer/tenant headers from `getAuthProvider()` while
   preserving `credentials: "include"`.
4. Authenticated browser probe confirms:
   - `/bff/agora/trading-room` returns `200`;
   - `/bff/agora/trading-room/decision-events` returns `200`;
   - `/agora/trading-room` no longer shows `Failed to load Trading Room`;
   - old white Trading Desk layout markers remain absent.

### Journey C: Decision and workspace writes

PR #148 is the safer live-frontend absorption point for writes because it covers
the broader deployed `tradingRoom.ts` surface, not only the five-function mirror
subset in Pantheon PR #2820. Reviewer/parent closeout should verify at least:

- `decideOnEvent()` preserves caller `If-Match`, `Idempotency-Key`, and
  `X-Request-Id` while adding bearer auth.
- Workspace proposal/accept and workspace layout/version/widget revision helpers
  preserve their idempotency and ETag semantics while adding bearer auth.
- All writes remain decision-support or workspace-preview operations; no order
  route, RuntimeBinding, capital binding, broker authority, or promotion
  approval is introduced.

---

## 6. Reviewer Checklist

For this sidecar review:

1. Confirm this packet only adds support material and the generated task brief.
2. Confirm the live BFF evidence is scoped correctly: health `200` and
   unauthenticated Trading Room routes `401 AUTH_REQUIRED` are not authenticated
   browser success.
3. Confirm the deployed FE commit `ea22b56c...` is correctly described as fresh
   but not yet carrying the Trading Room bearer-header fix.
4. Confirm `execute-plans` PR #148 is the current downstream frontend absorption
   PR and is still open at the time of this packet.
5. Confirm parent AUTH-003 should not be closed from Pantheon PR #2820 alone.
6. Confirm no secret token, cookie, bearer value, session value, or browser
   credential is committed in this packet.

For parent AUTH-003 closeout after frontend absorption:

1. Merge or otherwise absorb `execute-plans` PR #148.
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
| `git status -sb`, `git branch --show-current`, `git remote -v` | Started on `task/AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; remote `origin` is `https://github.com/ajoe734/pantheon.git`; only initial dirty item was this generated task brief. |
| `git fetch origin dev`, `git rev-parse HEAD`, `git rev-parse origin/dev` | Confirmed branch HEAD and `origin/dev` both at `837be58d8ecf15397605aae359bda7cb08d3e8ec` before writing this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Confirmed follow-up sidecar is active `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Confirmed parent AUTH-003 is `review_approved`; remaining closeout requires dev FE deploy and authenticated live browser proof. |
| `gh pr view 2820 --repo ajoe734/pantheon --json ...` | Confirmed parent PR #2820 is merged as `c04a7db9452ccca07e5af3b3f1c313f51d861beb`; visible checks passed. |
| `gh pr view 2822` / `gh pr view 2823` | Confirmed previous sidecar packet and closeout PRs are merged. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Confirmed dev FE deployment `ea22b56c7747d0f0968e91188559ceff7a2dc7e1`, live strict BFF mode, real writes false. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Returned HTTP `200`, service `operator-bff`. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room` | Returned HTTP `401` with `AUTH_REQUIRED`; no-token aggregate read is fail-closed. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room/decision-events` | Returned HTTP `401` with `AUTH_REQUIRED`; no-token decision-events read is fail-closed. |
| `gh api .../execute-plans/commits/ea22b56c...` | Confirmed deployed commit is execute-plans merge commit `ea22b56c...`, "MGMT-FLEET: list data sources individually". |
| Raw `execute-plans` source reads at `ea22b56c...` | Confirmed deployed `tradingRoom.ts` does not route Trading Room helpers through auth-bearing headers. |
| `gh pr list --repo ajoe734/execute-plans --state all --search "Trading Room auth headers"` | Found open PR #148 for the missing Trading Room Authorization header. |
| `gh pr view 148 --repo ajoe734/execute-plans --json ...` | Confirmed PR #148 is open; integration gate passed; body records 44 focused tests, full vitest, tsc, eslint, and build passing. |
| Raw PR #148 head source at `c0e256aff...` | Confirmed `authHeaders()` is added and spread into the broader live Trading Room helper surface. |

Prepared by Codex for reviewer `Claude`.
