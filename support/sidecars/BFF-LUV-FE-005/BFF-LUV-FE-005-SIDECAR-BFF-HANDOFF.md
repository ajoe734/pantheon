# BFF-LUV-FE-005 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-FE-005-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-FE-005
Helper kind: bff_handoff_packet
Owner: Claude
Reviewer: Codex
Prepared: 2026-05-09T18:00:00Z

## Scope

Support-only sidecar for BFF-LUV-FE-005. This packet does not define canonical
architecture, change route truth, or modify runtime/frontend implementation. It
organizes the dependency readiness status, the Lovable cutover environment
configuration requirements, the final cutover smoke plan, and the evidence
publication checklist for the parent owner to absorb or ignore.

Current parent state at packet time:

- Parent owner: Codex.
- Parent reviewer: Claude.
- Parent status: `todo`.
- Parent depends on: BFF-LUV-AUTHED-LIVE-001 (blocked), BFF-LUV-FE-001 (done),
  BFF-LUV-FE-002 (done), BFF-LUV-FE-003 (done), BFF-LUV-FE-004 (in_progress).
- BFF-LUV-FE-005 cannot start until all dependencies are done or explicitly
  recorded as accepted blockers.

## Dependency Status Matrix

| Task | Status | What it delivered | Gap / blocker at packet time |
|---|---|---|---|
| BFF-LUV-FE-001 | done | Live transport, session/me, auth headers, typed paths, env templates | No gap. Foundation is in place. |
| BFF-LUV-FE-002 | done | Management Console read adapters for 20 families; `managementClient`, `withLiveOrMock` list/detail transport; `MANAGEMENT_FAMILIES` registry | Authenticated live DTO evidence still pending (routed to AUTHED-LIVE-001). |
| BFF-LUV-FE-003 | done | Agora core/v5/realtime live adapters; SSE EventSource to `/bff/events/stream`; v5 loop-runs, sentinel, interventions | Authenticated live SSE probe not yet run; pending AUTHED-LIVE-001. |
| BFF-LUV-FE-004 | in_progress | `src/lib/bff/runAction.ts` write seam (actions, confirm-token lifecycle, decisions); `adaptLive` normalization for command receipts (Rev4); `bff-v1/writes.ts` auth gate | Rev4 Codex review requested adaptLive for `bff-v1/writes.ts` runAction/requestConfirmToken. Awaiting Rev5 from Claude2. |
| BFF-LUV-AUTHED-LIVE-001 | blocked | — | Missing valid lupin dev Bearer JWT token; GCP CLI re-auth fails non-interactively; local env lacks OIDC credentials. Waiting for Codex to resolve auth path. |

## Source Snapshot

The following surfaces are wired as of this packet. All paths are in
`/home/lupin/code/execute-plans` unless noted otherwise.

| Surface | State | Source |
|---|---|---|
| Live transport | `withLiveOrMock(req, mockFn, adaptLive?)` covers all route families | `src/lib/bff-v1/liveTransport.ts` |
| BFF client fetch | `bffFetch` / `bffRequest` — typed fetch with mock/live switch, `credentials: "include"`, header injection | `src/lib/bff-v1/client.ts` |
| Auth headers | Bearer + Tenant-Id from `sessionStorage`/`localStorage`; cookie session via `credentials: "include"` | `src/lib/bff-v1/headers.ts` |
| Typed path builders | Canonical builders for all known BFF routes including confirm-token lifecycle | `src/lib/bff-v1/paths.ts` |
| Session / me | `fetchMe()`, `useMe()`, `refreshSession()`, `logoutSession()` live-wired with TTL cache | `src/lib/v4/session/me.ts` |
| Management reads | `managementClient.<family>.list()` / `.get(id)` for all 20 families; fallback taxonomy wired | `src/lib/bff/client.ts` |
| Agora / v5 reads | Strict live adapters for Agora daily/signals/inbox/journal, v5 loop-runs/sentinel/interventions | `src/lib/bff/v5.ts`, `src/lib/bff/realtime.ts` |
| SSE realtime | EventSource to `/bff/events/stream`; `lastEventId` query replay; mock ticker limited to mock mode | `src/lib/bff/realtime.ts` |
| Write seam | `runAction` gated by `liveWriteGated()` (env + auth) + `adaptLive` normalization; confirm-token full lifecycle; decision routes | `src/lib/bff/runAction.ts` |
| v1 write compat | `bff-v1/writes.ts` `runAction`/`requestConfirmToken` gated by `liveWriteGated()`; adaptLive pending Rev5 | `src/lib/bff-v1/writes.ts` |
| Live status | Fallback tracking, API-version mismatch, `useLiveStatus()` hook | `src/lib/bff-v1/liveStatus.ts` |
| Env templates | `.env.example` (mock), `.env.dev.example` (live+auto), `.env.development.example` (lupin-dev+auto), `.env.staging-live.example` (live+strict) | `execute-plans/` root |

## Lovable Environment Configuration

For the final cutover smoke, the Lovable project must use these env settings
before BFF-LUV-FE-005 can run the final evidence sweep.

| Env var | Required value for cutover | Notes |
|---|---|---|
| `VITE_BFF_MODE` | `live` | Switch from mock. Without this, no live BFF probe occurs. |
| `VITE_BFF_BASE_URL` | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` | Target lupin dev BFF. Must match the BFF cert/hostname exactly. |
| `VITE_BFF_FALLBACK` | `strict` | Prevents silently masking live BFF failures with seed data. |
| `VITE_BFF_REAL_WRITES` | `false` (phase 1), then `true` (phase 2 after auth smoke) | Start writes disabled; enable only after AUTHED-LIVE-001 unblocks. |

Auth token injection (dev browser only, no code changes):

```javascript
sessionStorage.setItem("pantheon.bff.bearerToken", "<valid_jwt_token>")
sessionStorage.setItem("pantheon.bff.tenantId", "<tenant_id>")
```

The Lovable deploy environment must set these at the project/env level, not
locally, for a cross-team reproducible cutover smoke.

## BFF Query Gap Matrix for Cutover

These are the remaining gaps between current implementation and the final
Lovable cutover evidence required by BFF-LUV-FE-005 acceptance criteria.

| Gap | Current state | Why it matters for cutover | Recommended action |
|---|---|---|---|
| Authenticated live DTO smoke | Not run; all live evidence is from anonymous 401/404 route checks or unit mocks | Cutover cannot be declared without proof that the live BFF returns 2xx authenticated DTO shapes for the contract families | Unblock AUTHED-LIVE-001; run authenticated probe once a valid JWT is available. |
| `bff-v1/writes.ts` adaptLive | Rev4 added adaptLive to `runAction.ts`; `bff-v1/writes.ts` still missing adaptLive for `runAction` and `requestConfirmToken` | UI callers through the v1 compat seam can receive raw `status/data/meta` command receipts | Close FE-004 Rev5 first; this must be resolved before write smoke. |
| Write smoke evidence | Write smoke plan is documented in FE-004 artifact but not yet executed | Final handoff requires proof that `VITE_BFF_REAL_WRITES=true` does not trigger live-capital side effects | Run non-capital write smoke (confirm-token create/delete, alert acknowledge) against lupin dev after auth is available. |
| SSE live connection evidence | SSE adapter wired to `/bff/events/stream` but no live connection log or authenticated EventSource smoke | Realtime cutover claim requires at least one authenticated SSE open/message event from the live BFF | Two-track: (a) browser/Lovable cookie-session probe — `connectLiveSse()` uses `{ withCredentials: true }` only; native `EventSource` cannot inject an `Authorization` header; cookie/session auth is the only browser path; (b) optional non-browser Bearer probe via curl or Node.js EventSource polyfill; record which track was tested. |
| execute-plans commit hash | FE-004 not yet closed | Final handoff must record the exact execute-plans HEAD commit that went into the Lovable deploy | Wait for FE-004 to close; use its final commit as the execute-plans cutover commit. |
| pantheon commit hash | Pantheon side is clean after earlier BFF gap tasks | Cutover evidence must reference the pantheon commit matching the BFF contract tested | Record `git -C /home/lupin/code/pantheon rev-parse HEAD` at the time of evidence capture. |
| README env section | execute-plans README documents env vars | Lovable team needs the final env var table and token-injection instructions in the README for self-service | Update README live-mode section to reflect final cutover env vars and Lovable-specific setup once FE-004 and AUTHED-LIVE-001 close. |

## Operator Journey for Cutover Smoke

The following is the recommended cutover smoke sequence for BFF-LUV-FE-005.
All steps depend on AUTHED-LIVE-001 resolving a valid Bearer token path.

### Phase 1: Read-Only Live Verification (strict mode, writes disabled)

Set `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`.
Inject Bearer token into `sessionStorage["pantheon.bff.bearerToken"]`.

1. Session bootstrap: verify `GET /bff/me` returns 2xx `MeResponse` with `user`,
   `tenant`, `roles`, `capabilities`, `sessionExpiresAt`. Reject if `useMe()`
   falls back to mock silently.
2. Management read smoke: call `managementClient[family].list()` for every
   `MANAGEMENT_FAMILIES` entry. Assert `items`, `cursor`, `pageSize`, and
   correct `totalCountExact` class semantics. Expect `alerts` to omit
   `estimatedTotal` by design.
3. Management detail smoke: for every non-audit family with ≥1 list item, call
   `get(item.id)` and assert a 2xx DTO or typed BFF 404 envelope. Unexpected
   5xx, raw HTML, or untyped errors are failures.
4. Agora smoke: call live adapters for `bff.agora.daily()`, `bff.agora.signals()`,
   `bff.agora.inbox()`, and `bff.agora.journal()`. Assert 2xx or explicit empty
   list. Do not accept silent mock fallback.
5. v5 smoke: call `bff.v5.loopRuns()`, `bff.v5.sentinelFindings()`, and
   `bff.v5.pendingInterventions()`. Assert 2xx responses.
6. SSE smoke — two tracks (run at least one; record which track was used):

   **Track A — Browser/Lovable cookie-session probe (preferred for Lovable smoke):**
   The live SSE connector (`src/lib/bff-v1/sse/liveSse.ts`) opens native browser
   `EventSource` with `{ withCredentials: true }` only. Native browser `EventSource`
   does not support custom request headers; `Authorization: Bearer ...` cannot be
   injected by the frontend. SSE auth depends entirely on the active session cookie.
   Steps: log in with a valid operator account so that the session cookie is set;
   open or reload the page that triggers `connectLiveSse()`; record whether the
   `open` event fires and the first event received (`type`, `id`, `timestamp`).
   A 401 at this step means the session cookie is missing or invalid — not that
   Bearer injection is needed.

   **Track B — Non-browser Bearer-token probe (optional, out-of-band):**
   To verify the `/bff/events/stream` endpoint independently of cookies, run a
   probe with `curl` or a Node.js `EventSource` polyfill that supports request
   headers. Example:
   ```bash
   curl -N -H "Authorization: Bearer <jwt>" \
        -H "Accept: text/event-stream" \
        https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/events/stream
   ```
   Label this explicitly as a non-browser probe in the evidence log. Do not
   describe it as the current Lovable/browser behavior.

   Regardless of track, verify no mock ticker fires in live mode.
7. Fallback-negative check: with `VITE_BFF_FALLBACK=strict`, cause a deliberate
   transport failure (e.g., set `VITE_BFF_BASE_URL` to unreachable host for one
   probe); verify `BffError` surfaces and mock data does not silently appear.

### Phase 2: Write Smoke (non-capital only, after Phase 1 evidence captured)

Set `VITE_BFF_REAL_WRITES=true` while keeping `VITE_BFF_FALLBACK=strict` and
a valid Bearer token present. Run only after FE-004 is fully closed.

1. Gate-negative smoke: with Bearer token removed, call `runAction` and
   `requestConfirmToken`; assert `fetch` is not called (`liveWriteGated()` returns
   false without auth).
2. Non-capital confirm-token smoke: call `requestConfirmToken` → `readConfirmToken`
   → `deleteConfirmToken` for `/bff/confirm-tokens`. Assert:
   - `POST /bff/confirm-tokens` carries `Authorization: Bearer ...` and
     `Idempotency-Key` headers.
   - Response carries normalized `confirmToken`, not raw `tokenId`.
3. Non-capital action smoke: call `acknowledgeAlert(alertId)` where a live alert
   exists. Assert `POST /bff/alerts/{id}/acknowledge` carries idempotency headers
   and response carries normalized `CommandResponse` with `ok: true`, `correlationId`,
   `idempotencyKey`.
4. Idempotency replay smoke: resubmit the same `Idempotency-Key` for one of the
   above calls; assert the BFF returns the same result or an explicit 409 conflict
   envelope (not an unexpected 500 or silent second mutation).

Do NOT run write smoke against live-capital side-effect routes:

- strategy/persona deploy/promote/pause/resume/rollback/emergency-kill;
- deployment create or patch;
- capital allocation or rebalance mutations;
- any route that can emit a broker order or change real capital exposure.

### Phase 3: Evidence Publication

1. Record Phase 1 and Phase 2 results in a narrow Markdown/JSON evidence file
   under `docs/bff/evidence/`:
   - target BFF URL, exact timestamp, environment variables used;
   - per-family status codes and minimal field names only;
   - redacted auth source (e.g., "JWT from lupin-dev-operator credential");
   - SSE first-event metadata and which auth track was used (Track A: browser cookie/session; Track B: non-browser Bearer probe; or blocked with reason);
   - write smoke idempotency key + status.
2. Record exact commit hashes:
   - `git -C /home/lupin/code/execute-plans rev-parse HEAD` (execute-plans cutover commit).
   - `git -C /home/lupin/code/pantheon rev-parse HEAD` (pantheon BFF contract commit).
3. Publish the final handoff statement with explicit decisions on:
   - `VITE_BFF_MODE=live` — allowed or blocked (reason if blocked);
   - `VITE_BFF_REAL_WRITES=true` — allowed or blocked (reason if blocked);
   - any remaining route families not proven: must be explicitly labeled as
     deferred or blocked, not silently omitted.

## Frontend Handoff Notes

- `src/lib/bff/runAction.ts` is the canonical write seam for FE-004 scope.
  Do not reach into `bff-v1/writes.ts` directly for new write callers; use the
  canonical seam.
- Before calling any live write, always verify `liveWriteGated()` returns true
  in a developer console check. The gate requires both `VITE_BFF_REAL_WRITES=true`
  and a non-empty Bearer token.
- The FE-004 `adaptLive` callback in `runAction.ts` normalizes backend command
  receipts. If `bff-v1/writes.ts` still lacks adaptLive after Rev5, UI callers
  through the v1 compat seam may receive raw backend envelopes. Check the FE-004
  closeout before shipping.
- Do not assume `AUTHED-LIVE-001` unblocking also means all route families are
  proven. The AUTHED-LIVE-001 task requires evidence under `docs/bff/evidence/`;
  only routes listed there with 2xx status codes are considered proven.
- Confirm-token display copy (`ttlSeconds`, `requiredPhrase`) must be sourced
  from the local `HIGH_RISK_ACTIONS` catalog in `src/lib/v3/highRiskActions.ts`
  because the live BFF read route does not return these fields.
- SSE in live mode does not use the mock ticker. Reconnect logic via `lastEventId`
  query parameter is wired; the UI should not manually debounce reconnects that
  the EventSource already handles.
- The live SSE connector uses native browser `EventSource({ withCredentials: true })`.
  Native browser `EventSource` does not support injecting an `Authorization` header;
  session cookie is the only authentication mechanism for the browser SSE path. A
  401 on `/bff/events/stream` in the browser means the session cookie is absent or
  expired — not that Bearer injection from `sessionStorage` is needed. If a Bearer-
  authenticated SSE probe is required for debugging or server-side verification, use
  curl or a Node.js EventSource polyfill that supports request headers (not the
  deployed Lovable frontend).
- 4xx responses (401, 403, 409, 428) are real backend replies and must not fall
  back to mock in strict mode. Surface the typed `BffError` envelope where the
  UI already has error state.
- Hybrid auto-fallback mode is acceptable for developer iteration but must be
  labeled for operators. The `getLiveStatusSnapshot()` helper exposes `effective`,
  `fellBackAt`, and `lastError`; Management Console and Agora pages should surface
  this status so operators can distinguish seed data from live data.

## Parent Absorption Checklist

Before Codex (parent owner) starts BFF-LUV-FE-005 execution:

- [ ] BFF-LUV-FE-004 is closed (`done`) with FE-004 final execute-plans commit
  recorded, including the Rev5 adaptLive fix for `bff-v1/writes.ts` if still
  pending at FE-005 start time.
- [ ] BFF-LUV-AUTHED-LIVE-001 is either done (auth path found and smoke run)
  or formally blocked with an explicit owner/action recorded in `ai-status.json`.
  Do not start FE-005 cutover smoke with the task in undefined limbo.
- [ ] Lovable project env vars are set as specified in the "Lovable Environment
  Configuration" section above.
- [ ] A valid operator Bearer JWT is available for lupin dev (not the stub token
  format `op-live-smoke:...` which is rejected by strict auth mode).
- [ ] `npm run test` is green on the execute-plans branch being tested.
- [ ] `npm run build` is clean on the execute-plans branch being tested.
- [ ] Phase 1 (read-only) evidence is captured before Phase 2 (write smoke)
  starts; do not run `VITE_BFF_REAL_WRITES=true` before Phase 1 results are
  recorded.
- [ ] Evidence files published under `docs/bff/evidence/` with redacted tokens.
- [ ] Final handoff statement published as part of the FE-005 artifact doc with
  exact execute-plans and pantheon commit hashes.
- [ ] README live-mode section updated to reflect final cutover env vars and
  token injection instructions for the Lovable team.
- [ ] BFF-LUV-FE-005 artifact doc records explicit allow/block decisions for
  `VITE_BFF_MODE=live` and `VITE_BFF_REAL_WRITES=true` on the target Lovable env.

## Verification Notes For This Sidecar

No runtime, canonical, or frontend implementation was changed by this sidecar.
Verification consisted of source inspection only:

```bash
jq '.tasks[] | select(.id=="BFF-LUV-FE-005" or .id=="BFF-LUV-FE-005-SIDECAR-BFF-HANDOFF")' ai-status.json
jq '.tasks[] | select(.id | startswith("BFF-LUV-FE-0")) | {id, status, next}' ai-status.json
cat docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-005-lovable-cutover-smoke.md
cat docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-004-safe-write-flow.md
cat docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-AUTHED-LIVE-001-authenticated-dto-write-smoke.md
cat support/sidecars/BFF-LUV-FE-001/BFF-LUV-FE-001-SIDECAR-BFF-HANDOFF.md
cat support/sidecars/BFF-LUV-FE-002/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF.md
cat support/sidecars/BFF-LUV-FE-004/BFF-LUV-FE-004-SIDECAR-BFF-HANDOFF.md
git diff --check -- support/sidecars/BFF-LUV-FE-005/BFF-LUV-FE-005-SIDECAR-BFF-HANDOFF.md
git status --short -- support/sidecars/BFF-LUV-FE-005/BFF-LUV-FE-005-SIDECAR-BFF-HANDOFF.md
```

## Reviewer Handoff

Reviewer (Codex) should verify:

1. This packet is support-only and does not modify canonical truth, runtime
   implementation, registry state, or frontend implementation.
2. The dependency status matrix correctly reflects the state of all five
   BFF-LUV-FE-005 dependencies as of this packet time.
3. The Lovable environment configuration section lists correct env vars and
   does not contradict the established transport/auth model from BFF-LUV-FE-001.
4. The cutover smoke journey excludes live-capital side-effect routes in Phase 2.
5. The SSE smoke step distinguishes browser cookie/session EventSource (Track A,
   no Bearer header injection) from non-browser Bearer probe (Track B, curl/Node.js);
   the packet does not ask the Lovable browser to inject a Bearer Authorization
   header into native EventSource.
6. The parent absorption checklist aligns with BFF-LUV-FE-005 acceptance
   criteria (all deps done or blocked, evidence published, commit hashes
   recorded, final handoff published).
7. Parent owner (Codex) can use this packet as advisory input without treating
   it as an approved replacement for the BFF-LUV-FE-005 implementation record.

This packet is ready for Codex review and parent-owner absorption decision.
