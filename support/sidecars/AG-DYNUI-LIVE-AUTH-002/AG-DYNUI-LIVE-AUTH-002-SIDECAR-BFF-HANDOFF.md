# AG-DYNUI-LIVE-AUTH-002 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-AUTH-002` |
| Parent title | Fix live Agora Trading Room auth headers |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-LIVE-AUTH-002-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-03` |
| Mutates canonical | `false` |
| Status | Reviewer approved; ready for parent-owner absorption |
| Reviewed by | `Claude` on `2026-07-03` |
| Support PR | `#2807` merged to `dev` as `813568c7a1e2db36f11cedd18de46b11d15c71bc` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner decides whether and how to absorb this
packet into the mainline fix.

Closeout note: reviewer approval confirmed the packet is support-only, fact
checked against `execute-plans@dev` commit
`6556534b937e433b40cf94d87b8ab25a792aed35`, and does not mutate canonical
truth. Finalization records only this task-scoped handoff state.

---

## 1. Purpose

`AG-DYNUI-LIVE-AUTH-002` is not a backend auth implementation task. The live
Agora Trading Room page already reaches a deployed BFF, but the page's Trading
Room data client bypasses the shared BFF header builder and sends direct
`fetch()` calls without the live bearer token. The immediate symptom is:

- live `/agora/trading-room` no longer shows the old white Trading Desk markers;
- the content panel still shows `Failed to load Trading Room.`;
- browser BFF responses recorded by the parent show `/bff/me` as `200`, while
  `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events` return
  `401`;
- direct unauthenticated curl to `/bff/agora/trading-room` returns
  `AUTH_REQUIRED`, which is the correct fail-closed backend posture.

The handoff target is therefore narrow: update the real `execute-plans`
Trading Room BFF client so every Trading Room read and mutation uses the same
shared auth, tenant, correlation, request id, and BFF API-version header path
used by the rest of the frontend.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets are L0 support artifacts and do not override canonical L1 product or contract truth. |
| `.orchestrator/task-briefs/ag_dynui_live_auth_002_sidecar_bff_handoff.md` | Scope is support-only: BFF query gap, operator journey, and frontend handoff materials. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-002-SIDECAR-BFF-HANDOFF` | Sidecar is active `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-002` | Parent is active `in_progress`, owner `Claude`, reviewer `Codex`; acceptance requires shared auth headers on all Trading Room calls, unit tests, dev FE deploy, no error state, and live BFF 200 probes. |
| `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Current dev FE deployment, checked 2026-07-03, serves `execute-plans` `dev` commit `6556534b937e433b40cf94d87b8ab25a792aed35` with `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`. |
| `git ls-remote https://github.com/ajoe734/execute-plans.git refs/heads/dev` | `execute-plans@dev` currently points at the same `6556534b937e433b40cf94d87b8ab25a792aed35` commit. |
| `/tmp/agora-live-after-pr147.json` | Parent evidence after PR #147: page navigation `200`, old markers absent, new AGORA/Servant/Trading Room markers present, but `Failed to load Trading Room` present and Trading Room BFF calls `401`. |
| `execute-plans@FETCH_HEAD:src/lib/bff-v1/agora/tradingRoom.ts` | Latest remote client still uses direct `fetch()` calls with hand-written headers; it imports `readBffEnv` for base URL but does not import or call `buildHeaders` or `bffFetch`. |
| `execute-plans@FETCH_HEAD:src/lib/bff-v1/headers.ts` | `buildHeaders()` already injects `Authorization`, `X-Tenant-Id`, `X-Request-Id`, `X-Correlation-Id`, and `X-BFF-Api-Version` from browser storage/provider/dev-token env. |
| `execute-plans@FETCH_HEAD:src/agora/pages/trading-room/TradingRoomPage.tsx` | Initial load calls `getTradingRoom()` and `listDecisionEvents()`; strategy flows call proposal, workspace, grid, revision, and decision helpers from the same client module. |
| `services/control-plane/bff/main.py` | `_extract_identity()` accepts strict JWT/cookie session or explicit stub mode; missing bearer/cookie auth returns structured `AUTH_REQUIRED`. |
| `services/control-plane/bff/agora/trading_room/router.py` | Trading Room routes call `extract_identity(authorization)` and `require_read_role(identity)` before serving aggregate, events, proposals, workspaces, revisions, and decisions. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## 3. Current Finding

The latest live and remote frontend state still has the auth transport gap.
`execute-plans@dev` commit `6556534b937e433b40cf94d87b8ab25a792aed35` has
`src/lib/bff-v1/agora/tradingRoom.ts` direct calls like:

```ts
await fetch(url, {
  method: "GET",
  credentials: "include",
  headers: { Accept: "application/json" },
});
```

and mutation helpers build local objects containing only `Accept`,
`Content-Type`, `If-Match`, `Idempotency-Key`, or caller-supplied
`X-Request-Id`. None of these local header objects carries the shared
`Authorization`, tenant, correlation, or BFF API-version headers.

The existing shared helper in `src/lib/bff-v1/headers.ts` already does the
needed transport work:

- reads token from `pantheon.bff.bearerToken` or legacy
  `pantheon_operator_token`;
- reads tenant from `pantheon.bff.tenantId` or legacy `pantheon_tenant_id`;
- reads `VITE_BFF_DEV_BEARER_TOKEN` in live mode;
- emits `Authorization: Bearer <token>` when a token exists;
- emits `X-Tenant-Id`, `X-Request-Id`, `X-Correlation-Id`, and
  `X-BFF-Api-Version`.

Therefore the parent fix should not relax BFF auth, add fixture fallback, or
special-case Trading Room in the backend. The page should use the existing BFF
header path consistently.

Implementation warning: the local checkout at `/home/lupin/code/execute-plans`
was observed dirty and stale (`dev...origin/dev [ahead 1946, behind 34]`, with
local modifications/deletions). Use a clean `execute-plans` task worktree or a
fresh branch from remote `dev` for the parent implementation, not that dirty
checkout as-is.

---

## 4. BFF Query Gap Matrix

This is a frontend header propagation gap, not a missing BFF route gap.

| Frontend helper | Route | Required transport posture |
|---|---|---|
| `getTradingRoom` | `GET /bff/agora/trading-room` | Send shared read headers, especially `Authorization`; preserve typed aggregate normalization. |
| `getTradingRoomStrategy` | `GET /bff/agora/trading-room/strategies/{strategyId}` | Send shared read headers; keep `404 -> null`. |
| `listDecisionEvents` | `GET /bff/agora/trading-room/decision-events` | Send shared read headers; keep query filters and ETag capture. |
| `getDecisionEvent` | `GET /bff/agora/trading-room/decision-events/{id}` | Send shared read headers; keep `404 -> null`. |
| `createTradingRoomWorkspaceProposal` | `POST /bff/agora/strategies/{strategyId}/trading-room/proposals` | Send shared mutation headers plus JSON body; idempotency may be supplied or auto-generated by shared helper. |
| `getTradingRoomWorkspaceProposal` | `GET /bff/agora/strategies/{strategyId}/trading-room/proposals/{proposalId}` | Send shared read headers; preserve `BffError` normalization. |
| `acceptTradingRoomWorkspaceProposalWithMeta` | `POST /bff/agora/strategies/{strategyId}/trading-room/proposals/{proposalId}/accept` | Send shared mutation headers; preserve ETag/workspace metadata handling. |
| `getTradingRoomWorkspaceWithMeta` | `GET /bff/agora/trading-room/workspaces/{workspaceId}` | Send shared read headers; preserve ETag capture. |
| `patchTradingRoomWorkspaceLayout` | `PATCH /bff/agora/trading-room/workspaces/{workspaceId}/layout` | Send shared mutation headers with exact `If-Match` and idempotency. |
| `listTradingRoomWorkspaceVersions` | `GET /bff/agora/trading-room/workspaces/{workspaceId}/versions` | Send shared read headers. |
| `rollbackTradingRoomWorkspaceVersion` | `POST /bff/agora/trading-room/workspaces/{workspaceId}/versions/{versionId}/rollback` | Send shared mutation headers with exact `If-Match` and idempotency. |
| `createWidgetRevisionProposal` | `POST /bff/agora/trading-room/workspaces/{workspaceId}/widgets/{widgetId}/revision-proposals` | Send shared mutation headers and JSON body; preserve typed before/after preview errors. |
| `acceptWidgetRevisionProposal` | `POST /bff/agora/trading-room/widget-revision-proposals/{proposalId}/accept` | Send shared mutation headers with exact `If-Match` and idempotency. |
| `decideOnEvent` | `POST /bff/agora/trading-room/decision-events/{id}/decisions` | Send shared mutation headers with exact `If-Match`, idempotency, and exact caller `X-Request-Id` when provided. |

Two details matter for implementation:

1. `buildHeaders({ method })` emits `X-Request-Id` for reads as well as writes.
   Existing tests that describe read calls as sending no `X-Request-Id` are
   stale for live shared-header behavior and should be updated.
2. Existing ETag values are already quoted strings. Do not pass an existing
   ETag through `ifMatchVersion` if that double-quotes it. Prefer
   `extra: { "If-Match": options.ifMatch }` so the exact server ETag is sent.

---

## 5. Suggested Frontend Composition

Use the existing `src/lib/bff-v1/headers.ts` helper rather than adding another
auth reader inside `tradingRoom.ts`.

Recommended shape:

```ts
import { buildHeaders } from "../headers";

function readHeaders(): Record<string, string> {
  return buildHeaders({ method: "GET" });
}

function jsonMutationHeaders(
  method: "POST" | "PATCH",
  options?: { idempotencyKey?: string; ifMatch?: string | null; requestId?: string },
): Record<string, string> {
  const extra: Record<string, string> = {};
  if (options?.ifMatch) extra["If-Match"] = options.ifMatch;
  if (options?.requestId) extra["X-Request-Id"] = options.requestId;
  return buildHeaders({
    method,
    idempotency: options?.idempotencyKey,
    extra,
  });
}
```

Keep existing `credentials: "include"` on live requests. The shared helper adds
bearer headers; cookie credentials remain harmless and may still serve session
flows where configured.

Avoid these implementation traps:

- do not use only `credentials: "include"` as proof of auth, because the failing
  live path already did that;
- do not add `Authorization` manually in only the two initial GETs;
- do not bypass `tradingRoom.ts` from `TradingRoomPage.tsx`;
- do not collapse typed `BffError` handling back to generic `Error` for
  proposal/workspace/revision flows;
- do not replace live failures with local fixture or mock workspace data in
  strict mode;
- do not change BFF route auth requirements to make this page load.

---

## 6. Operator Journey Handoff

### Journey A: Initial Trading Room Load

1. Operator opens `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`.
2. Frontend bootstrap obtains or already has a live BFF token through the shared
   BFF auth provider/storage/dev-token path.
3. `TradingRoomPage` calls `getTradingRoom()` and `listDecisionEvents()`.
4. Both calls send `Authorization`, `X-Tenant-Id` when present,
   `X-Correlation-Id`, `X-Request-Id`, and `X-BFF-Api-Version`.
5. BFF returns `200`; the page renders the dark AGORA Trading Room shell and
   queue/empty state, not `Failed to load Trading Room`.

### Journey B: Workspace Proposal And Grid

1. Operator selects or enters a strategy workspace flow.
2. Proposal generation, proposal read, accept, workspace read, layout patch,
   version list/rollback, and widget revision calls all use the same shared
   auth transport.
3. If the BFF returns `401`, the UI presents an auth/session state. It must not
   keep showing stale proposal/workspace data as if the operator remains in
   scope.
4. If the BFF returns `403`, `404`, `409`, `412`, `422`, or `501`, preserve the
   existing typed error semantics used by proposal/workspace/revision flows.

### Journey C: Decision Event Write

1. Operator reviews a decision event from the event queue.
2. Frontend submits `decideOnEvent()` with the ETag from the event read/list,
   an idempotency key, and a stable request id.
3. The submitted headers include shared auth plus exact `If-Match`,
   `Idempotency-Key`, and `X-Request-Id`.
4. BFF records a decision-support intent only. This route still does not place
   orders, create RuntimeBinding, bind capital, or approve promotion.

---

## 7. Parent Scope Boundary

`AG-DYNUI-LIVE-AUTH-002` should own:

- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`;
- `execute-plans/src/lib/bff-v1/agora/tradingRoom.test.ts`;
- live browser evidence after merge/deploy proving the Trading Room page does
  not show `Failed to load Trading Room`;
- live network evidence that the browser's authenticated Trading Room requests
  return `200`.

It should not own:

- `services/control-plane/bff/main.py` auth facade changes;
- `services/control-plane/bff/agora/trading_room/router.py` route relaxation;
- new canonical L1/L2 contract truth;
- registry, governance, RuntimeBinding, capital, or broker behavior;
- fixture fallback or local mock substitution in live strict mode;
- a dirty local `execute-plans` checkout recovery unless explicitly split into
  a repo hygiene task.

---

## 8. Suggested Tests

Focused unit tests in `src/lib/bff-v1/agora/tradingRoom.test.ts` should be
updated to prove the live transport contract directly.

Recommended assertions:

| Test area | Expected assertion |
|---|---|
| `getTradingRoom` | Header object contains `Authorization: Bearer <test-token>`, `X-Tenant-Id`, `X-Correlation-Id`, `X-Request-Id`, and `X-BFF-Api-Version`. |
| `listDecisionEvents` | Same shared read headers plus existing query-string and ETag behavior. |
| `getDecisionEvent` and `getTradingRoomStrategy` | Same shared read headers and current `404 -> null` behavior. |
| Proposal create/accept | Shared auth headers plus JSON content and idempotency. |
| Workspace read/version list | Shared read headers and ETag/version extraction behavior. |
| Workspace layout and rollback | Shared auth headers plus exact `If-Match` and idempotency. |
| Widget revision create/accept | Shared auth headers plus existing typed `BffError` behavior. |
| `decideOnEvent` | Shared auth headers plus exact caller `If-Match`, `Idempotency-Key`, and `X-Request-Id`. |

Use `setAuthProvider()` from `src/lib/bff-v1/headers.ts` in tests instead of
hardcoding browser storage. Reset the provider after each test so unrelated BFF
client tests do not inherit Trading Room auth fixtures.

Stale tests to revise:

- read-only tests currently assert no `X-Request-Id`; shared BFF headers emit a
  request id for reads;
- mutation tests currently allow missing idempotency/request headers in places
  where the backend either requires them or the shared helper safely generates
  them;
- tests that only check `Content-Type`/`Accept` do not prove the live auth fix.

---

## 9. Live Verification Checklist

After the parent implementation merges into `ajoe734/execute-plans` `dev` and
the dev FE deploy completes:

1. Confirm deployment target:

   ```bash
   curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
   ```

   Expected: `commit` equals the merged parent fix commit, `sourceBranch` is
   `dev`, `VITE_BFF_MODE=live`, and `VITE_BFF_FALLBACK=strict`.

2. Run a browser probe against:

   ```text
   https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room
   ```

   Expected DOM/network result:

   - old white markers remain absent;
   - `AGORA`, `Servant`, and `Trading Room` remain present;
   - `Failed to load Trading Room` is absent;
   - browser responses for `/bff/agora/trading-room` and
     `/bff/agora/trading-room/decision-events` are `200`;
   - captured request headers include `Authorization` without exposing the raw
     token in committed evidence.

3. Keep unauthenticated curl semantics fail-closed:

   ```bash
   curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room
   ```

   Expected: `401 AUTH_REQUIRED`. This is not a parent failure; it confirms the
   BFF still rejects missing bearer/session auth.

Do not close parent acceptance on a network-idle-only browser run. The evidence
must inspect DOM text and the two Trading Room BFF response statuses.
