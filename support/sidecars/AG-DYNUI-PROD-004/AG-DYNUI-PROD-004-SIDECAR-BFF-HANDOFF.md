# AG-DYNUI-PROD-004 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-004` |
| Parent title | Trading Room error diagnostics and stale bundle recovery |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar task | `AG-DYNUI-PROD-004-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not define canonical truth, update L1
contracts, edit BFF/runtime code, edit frontend code, or approve the parent
implementation. Parent ownership and review decide how to absorb this packet.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_dynui_prod_004_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful doc/support work should be committed through the task workflow with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout is separate from handoff; this task is not `review_approved` and does not move to `done` here. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-004-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Codex`, reviewer `Codex2`, artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DYNUI-PROD-004` | Parent is in `review`, owner `Codex2`, reviewer `Claude`, current parent branch note cites diagnostics/retry/safe reload at `23a537ab7` and reviewer notes at `2b2fe316a`. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-004-error-cache-diagnostics.md` | Parent scope: preserve BFF status/code/request/correlation details, add retry/safe reload, harden probes, keep secrets out of UI/logs. |
| `task/AG-DYNUI-PROD-004` branch diff from this sidecar base | Parent branch changes Trading Room diagnostics UI/client/tests/probe; this sidecar branch intentionally does not absorb or edit those runtime files. |
| `deploy/caddy/dev.Caddyfile.tmpl` and `deploy/caddy/sync-caddy.sh` | Current dev Caddy template already asserts no-store for SPA shell and `deployment.json`, immutable for hashed assets, and the sync script verifies those headers. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Handoff Summary

`AG-DYNUI-PROD-004` is not a new canonical BFF contract task. It is a
production-diagnostics task for the existing Trading Room read path. The parent
branch should make an operator-facing root failure distinguish among:

- authentication/authorization failure;
- BFF HTTP error with structured code/message;
- BFF request/correlation traceability;
- network/CORS/static-host routing failure;
- stale bundle or stale deployment shell;
- schema/runtime drift that manifests as a root aggregate load failure.

The key handoff is therefore a composition checklist, not a new route family:

1. The page must not collapse root load failure to only `Failed to load Trading Room`.
2. The BFF client must preserve structured diagnostics without leaking secrets.
3. The hosted probe must fail on generic-only failure and collect cache/deploy evidence.
4. Final parent closeout still needs hosted proof after the reviewed branch is
   actually deployed to the Pantheon-owned dev FE host.

---

## 3. BFF Query Surface And Gap Matrix

### 3.1 Existing Trading Room page read path

| Frontend call | BFF route | Root failure impact |
|---|---|---|
| `getTradingRoom()` | `GET /bff/agora/trading-room` | Critical root aggregate. Failure must render diagnostic root error state. |
| `listDecisionEvents()` | `GET /bff/agora/trading-room/decision-events` | Non-root event queue. Current page can degrade event list without blocking aggregate. |
| `getDashboardRecipeById(recipeId)` | `GET /bff/agora/dashboard-recipes/{id}` through dashboard client | Strategy workspace detail only after aggregate selects a strategy; not the AG-DYNUI-PROD-004 root diagnostic target. |
| `decideOnEvent(...)` | `POST /bff/agora/trading-room/decision-events/{id}/decisions` | Existing write-intent support; must keep `If-Match`, `Idempotency-Key`, and `X-Request-Id` behavior untouched. |

### 3.2 Diagnostic gap the parent must close

| Gap | Required handoff behavior |
|---|---|
| Generic root error | Root aggregate failure must render an actionable diagnostics panel, not only the legacy text. |
| Lost HTTP status/code | BFF client should expose `status`, BFF error `code`, sanitized `message`, method, and route. |
| Lost request/correlation ids | Error state and probe should preserve `X-Request-Id`, `X-Correlation-Id`, and equivalent error envelope metadata when present. |
| Static FE origin accidentally used as BFF | Client base resolution should honor `VITE_BFF_BASE_URL` before falling back to browser origin. |
| Network/CORS ambiguity | Network failures should produce a typed diagnostic with `status: 0` or equivalent network marker. |
| Stale bundle suspicion | UI should offer a safe latest-bundle reload that changes the URL query, while Caddy keeps shell/deployment uncacheable. |
| Secret leakage | Diagnostic text must redact bearer/token/password-shaped values and avoid rendering raw response bodies. |
| Probe blind spot | Hosted probe must capture Trading Room BFF request/response statuses, deployment identity/cache headers, console errors, and fail on generic-only error text. |

No new canonical route, schema, registry, governance, or deployment policy is
required by this sidecar packet.

---

## 4. Parent Branch Findings To Compose

The parent task branch visible from this worktree contains:

- `d6065dec6` - `AG-DYNUI-PROD-004: anchor diagnostics ui`
- `23a537ab7` - `AG-DYNUI-PROD-004: harden trading room diagnostics`
- `2b2fe316a` - `AG-DYNUI-PROD-004: record reviewer verification notes`

The branch diff against this sidecar base changes these relevant files:

| File | Handoff meaning |
|---|---|
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Adds diagnostic root error state, retry, safe reload, data attributes for probe extraction, and sanitized diagnostic text. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.test.tsx` | Covers root diagnostics and error paths, including HTTP statuses and network failure. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Adds `TradingRoomBffError` / diagnostic extraction and honors `VITE_BFF_BASE_URL`. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.test.ts` | Covers BFF client diagnostics, env base URL, and existing header contracts. |
| `execute-plans/scripts/probe-hosted-browser-bff.mjs` | Extends hosted probe for Trading Room, deployment/cache evidence, BFF statuses, request/correlation ids, and generic-only failure guard. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-004-error-cache-diagnostics.md` | Records implementation notes, validation, and reviewer closeout caveat. |

Composition caution: this sidecar branch does not contain those runtime edits at
`HEAD`. Reviewers should inspect the parent branch/PR as the runtime source of
truth for the parent task. This packet is only the handoff checklist.

---

## 5. Operator Journey Packet

### Journey A: Root load succeeds

1. Operator opens `/agora/trading-room` on the Pantheon-owned dev FE host.
2. Frontend loads the static shell from FE origin.
3. `getTradingRoom()` calls the configured dev BFF origin from
   `VITE_BFF_BASE_URL`.
4. Aggregate renders with strategy lens, queue summary, risk banner, and event
   queue.
5. No diagnostic panel is visible.

### Journey B: BFF HTTP failure

1. `GET /bff/agora/trading-room` returns a non-2xx response.
2. Client extracts status, BFF code/message, request id, and correlation id
   from headers or the error envelope.
3. Page renders a Trading Room load diagnostic panel with status/code and trace
   ids, not a generic-only failure string.
4. Operator can click retry; retry should re-run `getTradingRoom()`.
5. Safe reload remains available when stale bundle or stale shell is suspected.

Expected status-specific interpretation:

| Status | Operator-facing meaning |
|---:|---|
| 401 | Missing/expired auth; do not imply data/schema failure. |
| 403 | Scope/permission failure; clear any stale cross-scope state. |
| 404 | Route or tenant-scoped aggregate missing; capture endpoint and trace ids. |
| 409 | Runtime/state conflict; preserve code/message for backend traceability. |
| 412 | Stale precondition semantics if encountered; preserve code/message. |
| 500 | BFF/server failure; preserve request/correlation ids for server logs. |
| 0/network | Network, CORS, DNS, TLS, or static-origin misrouting suspicion. |

### Journey C: Stale bundle/cache suspicion

1. Operator sees diagnostic failure after deploy or cache cutover.
2. Error panel offers `Reload latest bundle`.
3. Reload URL includes a cache-busting query parameter.
4. Caddy must serve the SPA shell and `/deployment.json` with `no-store`.
5. Hashed assets remain long-lived immutable assets.
6. Hosted probe records deployment id/source commit and cache headers before
   parent closeout claims hosted proof.

### Journey D: Probe catches regression

1. Probe opens `/agora/trading-room` on hosted FE.
2. Probe observes BFF calls and console errors.
3. Probe reads `deployment.json` and HEADs shell/assets for cache policy.
4. Probe fails if root failure text is generic-only.
5. Probe output must name FE URL, BFF URL, deployment id/source commit, cache
   headers, BFF statuses, request/correlation ids when present, and console
   failures.

---

## 6. Frontend Handoff Checklist

Parent review should verify:

- `TradingRoomPage` imports and handles the typed BFF diagnostic surface.
- Root load `catch` stores diagnostics rather than only toggling `error`.
- The error state exposes stable test/probe hooks:
  - `data-testid="trading-room-error"`
  - `data-bff-status`
  - `data-bff-code`
  - `data-request-id`
  - `data-correlation-id`
  - `data-testid="trading-room-retry"`
  - `data-testid="trading-room-safe-reload"`
- Retry re-runs the root aggregate fetch and clears prior error state.
- Safe reload does not call any write route and only redirects to the current
  Trading Room URL with a cache-busting query parameter.
- The rendered diagnostic truncates/redacts token-like strings.
- The page still preserves V10/V11 dynamic UI behavior outside the root error
  state: strategy lens, aggregate view, grid editor path, decision event queue,
  version/history/rollback surfaces owned by adjacent tasks.
- No order-routing, broker, capital binding, or RuntimeBinding control is added
  by this diagnostics task.

---

## 7. BFF Client Handoff Checklist

Parent review should verify:

- `resolvedBase()` uses explicit argument first, then `readBffEnv().VITE_BFF_BASE_URL`,
  then browser origin fallback.
- HTTP failures throw a typed `TradingRoomBffError` with `diagnostic`.
- Network failures throw a diagnostic with `status: 0` or equivalent network
  marker and `retryable: true`.
- Request/correlation ids are extracted from both envelope metadata and headers.
- Existing read/write contracts stay intact:
  - reads include shared auth headers and `credentials: "include"`;
  - `decideOnEvent` keeps `If-Match`, `Idempotency-Key`, and `X-Request-Id`;
  - no direct `fetch()` calls are added in the page outside the BFF module.
- Error parser does not expose raw response bodies or secret-bearing URLs in
  user-visible text.

---

## 8. Probe And Cache Handoff Checklist

Parent review/closeout should verify:

| Check | Expected result |
|---|---|
| `node --check execute-plans/scripts/probe-hosted-browser-bff.mjs` | Probe script parses. |
| Hosted probe target | Defaults or env points at `/agora/trading-room`, not only `/management`. |
| BFF observation | Probe records `/bff/agora/trading-room` request/response status and request/correlation headers. |
| Generic-only guard | Probe fails if page only exposes `Failed to load Trading Room` without status/code/ids or text diagnostics. |
| Deployment evidence | Probe records `/deployment.json` status, cache header, and deployment id/source commit when available. |
| Cache policy | SPA shell and `/deployment.json` are `no-store`; hashed assets are `immutable`. |
| Console evidence | Probe records console errors for chunk/CORS/static-origin failures. |
| Output location | Audit output should be redirected to `/tmp` or a task artifact path intentionally included in scope; do not leave generated probe files unowned. |

The reviewer note on parent branch states the pre-deploy hosted smoke was
expected `pass=false` because the deployed dev host had not yet received the
branch and still exposed unrelated CORS/chunk failures. Therefore final hosted
proof must happen after the reviewed parent branch is deployed.

---

## 9. Suggested Reviewer Questions For Codex2

1. Does the parent PR include both the UI diagnostic render path and BFF client
   diagnostic extraction in the same deployable frontend branch?
2. Does `TradingRoomPage.test.tsx` assert status/code/request/correlation
   surfaces for at least representative HTTP and network failures?
3. Does the hosted probe fail on the legacy generic-only failure string?
4. Does the final parent closeout distinguish local validation from hosted proof
   after the dev FE deploy?
5. Are cache headers still verified through the Caddy template/sync path rather
   than assumed from local build output?
6. Is any generated audit output either written outside the repo or explicitly
   committed as a task artifact?

---

## 10. Recommended Parent Closeout Evidence

Before `AG-DYNUI-PROD-004` moves from review approval toward done, record:

- parent PR number and merged commit SHA;
- dev FE deployment id/source commit that contains the diagnostics changes;
- hosted probe command, FE URL, BFF URL, and output artifact path;
- hosted probe result after deployment;
- cache headers for `/agora/trading-room`, `/deployment.json`, and one hashed
  asset;
- at least one captured root failure path showing status/code and trace ids, or
  a documented authenticated-pass path plus a forced failure test in local/CI;
- confirmation that generic-only `Failed to load Trading Room` no longer passes
  the probe.

This packet should be handed to `Codex2` for sidecar review and to the parent
owner/reviewer as support material. It should not be treated as implementation
approval by itself.
