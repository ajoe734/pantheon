# FE-INT-GATE-FOLLOWUP-ME-STARTUP Sidecar BFF Handoff Packet

Task ID: FE-INT-GATE-FOLLOWUP-ME-STARTUP-SIDECAR-BFF-HANDOFF
Parent task: FE-INT-GATE-FOLLOWUP-ME-STARTUP
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Codex
Prepared: 2026-05-14
Mutates canonical truth: false

## Scope

This is a support-only handoff packet for the parent startup session follow-up.
It does not change L1 policy, route truth, runtime implementation, registry
state, or governance behavior. The parent owner decides whether to absorb any
of this into the parent evidence packet.

The packet consolidates:

- the BFF `/bff/me` contract surface needed by the frontend startup path
- the actual startup query gap and the required network evidence
- the operator journey for authenticated, unauthenticated, and stale-hosted
  bundle cases
- the frontend and reviewer checklist for the parent task

## Current Parent State

Parent source work already exists in `/home/lupin/code/execute-plans` on branch
`bff-luv-fe-006-dev-deploy`:

| Commit | Scope |
|---|---|
| `b09d22e` | `FE-INT-GATE-FOLLOWUP-ME-STARTUP: close local role fallback` |
| `df73c3d` | `FE-INT-GATE-FOLLOWUP-ME-STARTUP: surface me startup gap first` |

Pantheon evidence was recorded in commit `0bec9136` at
`support/evidence/FE-INT-GATE-FOLLOWUP-ME-STARTUP.md`.

As recorded in `ai-status.json`, the parent remains blocked because the hosted
Lovable URL must serve a bundle that contains the `df73c3d` source state before
hosted acceptance can pass. This sidecar is not deployment proof and should not
be used as a substitute for the hosted strict Playwright rerun.

## BFF `/bff/me` Contract Surface

Backend route:

- Source: `services/control-plane/bff/main.py`
- Route: `GET /bff/me`
- Contract marker: `BFF-LUV-GAP-009`
- Response envelope: `{ data, meta }`
- Authentication inputs: `Authorization: Bearer ...` or `pantheon_session`
  cookie, plus optional `X-MFA-Token`
- Tenant inputs: query `tenant_id`, `X-Tenant-Id`, or `X-Pantheon-Tenant`
- Locale inputs: `X-Locale` or `Accept-Language`

Required response fields for frontend startup:

| Field | Purpose |
|---|---|
| `data.user`, `data.currentUser`, `data.current_user` | Current operator identity aliases |
| `data.roles` | Role display and downstream permission checks |
| `data.capabilities` | Capability checks such as `runtime.read` |
| `data.tenant`, `data.tenant_id` | Active tenant and allowed tenant scope |
| `data.locale` | Resolved locale and source |
| `data.environment` | Environment name, deployment stage, auth mode, strict-auth flag |
| `data.feature_flags.sessionAuthMe` | Confirms session-auth-me contract is active |
| `data.session` | Session state, freshness, auth mode, MFA, and `session_kind` |

Session kind behavior:

| `session.session_kind` | Meaning | Frontend implication |
|---|---|---|
| `cookie` | Valid cookie-backed session | Authenticated current user; eligible for governed write gate when writes are enabled |
| `bearer` | Valid bearer JWT session | Authenticated current user; eligible for governed write gate when writes are enabled |
| `stub` | Stub auth mode | Acceptable for dev/read smoke, blocked for production strict write gate |

Expected fail-closed responses:

| Case | Status | Frontend expectation |
|---|---:|---|
| No valid auth in strict backend mode | `401` | Show auth/error state; never use `mockMe()` or local role fallback |
| Tenant outside allowed scope | `403` | Show backend error; do not fabricate tenant/user state |
| Caller lacks read role | `403` | Show backend error; do not show current-user UI |

## Startup Query Gap

The gap is not that the backend route is absent. The backend route and contract
tests exist. The gap is that hosted startup must make an application-origin
request to `/bff/me` before displaying any current-user or role UI.

Required startup sequence:

```text
Operator opens Lovable page
  -> TopBar mounts
  -> useMe() starts fetchMe()
  -> fetchMe() calls withLiveOrMock({ method: "GET", path: paths.me() })
  -> bffFetch() sends GET <VITE_BFF_BASE_URL>/bff/me
       with credentials: "include"
       plus Authorization / X-Tenant-Id when present in browser storage or env
  -> 2xx: render BFF-sourced user display name and BFF roles
  -> 401/403 or strict transport error: render Auth/Lock error state
  -> never render local platform role fallback as current user
```

The F01 follow-up assertion is intentionally network-based:

```text
interceptedMeRequests > 0
```

This must be satisfied by the page startup path after `page.goto("/")`. A direct
Playwright API request to `/bff/me` proves the backend DTO, but it does not prove
that the hosted frontend asks for `/bff/me` during startup.

## Frontend Handoff Map

| Area | File | Notes |
|---|---|---|
| Startup UI | `/home/lupin/code/execute-plans/src/platform/components/TopBar.tsx` | Imports `useMe()`, displays spinner while loading, displays Lock/Auth on `meError` or missing `me`, and renders BFF-sourced user/roles only after success. |
| Session hook | `/home/lupin/code/execute-plans/src/lib/v4/session/me.ts` | `fetchMe()` calls `withLiveOrMock()` with `paths.me()`, caches for 30s, normalizes flat or enveloped `MeResponse` shapes, and exposes `useMe()`. |
| BFF path | `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | `paths.me()` returns `/bff/me`; legacy session aliases also resolve to `/bff/me`. |
| Live transport | `/home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts` | Strict mode surfaces transport failure as typed error instead of falling back to mock; typed 4xx responses always propagate. |
| Headers | `/home/lupin/code/execute-plans/src/lib/bff-v1/headers.ts` | Sends `credentials: "include"` in client layer, bearer token from browser storage or `VITE_BFF_DEV_BEARER_TOKEN`, tenant from browser storage, and standard request/correlation headers. |
| Startup spec | `/home/lupin/code/execute-plans/e2e/01-startup-session.spec.ts` | 401 test intercepts `/bff/me`, attaches `startup-bff-network`, requires `interceptedMeRequests > 0`, checks Auth/error text, and rejects mock operator text. |

Browser token storage accepted by the current frontend:

```text
sessionStorage["pantheon.bff.bearerToken"]
localStorage["pantheon.bff.bearerToken"]
sessionStorage["pantheon_operator_token"]
localStorage["pantheon_operator_token"]
```

Tenant storage accepted by the current frontend:

```text
sessionStorage["pantheon.bff.tenantId"]
localStorage["pantheon.bff.tenantId"]
sessionStorage["pantheon_tenant_id"]
localStorage["pantheon_tenant_id"]
```

## Operator Journeys

### Authenticated Startup

```text
Operator opens hosted Lovable page with valid cookie or bearer token
  -> /bff/me returns 200 with contract BFF-LUV-GAP-009
  -> TopBar user menu shows BFF current user display name
  -> Role dropdown shows BFF roles
  -> No local platform role selector is used as current-user source
  -> Startup session acceptance can observe at least one /bff/me request
```

### Anonymous Or Expired Startup

```text
Operator opens hosted Lovable page without valid session
  -> /bff/me returns 401 or the test intercept injects 401
  -> TopBar renders Lock/Auth error state
  -> Body does not show "Mock Operator", "op-fe-gate", "portfolio_manager",
     or any serving-mock / seed fallback banner
  -> This is fail-closed and should pass the parent 401 acceptance path
```

### Hosted Bundle Still Stale

```text
Operator opens hosted Lovable page
  -> startup calls other BFF list/v5/SSE routes
  -> startup does not call /bff/me
  -> TopBar still shows local role UI such as admin
  -> Playwright startup-bff-network attachment records interceptedMeRequests=0
  -> Parent task remains blocked pending Lovable deploy refresh or correct URL
```

## Acceptance And Verification Commands

Parent local focused verification already recorded one pass in
`support/evidence/FE-INT-GATE-FOLLOWUP-ME-STARTUP.md`:

```bash
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=http://127.0.0.1:5174 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  -g "does not fall back to mock current-user data" \
  --trace=on --reporter=list \
  --output=/tmp/fe-int-gate-followup-me-startup-local-rerun
```

Hosted acceptance after Lovable refresh:

```bash
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  -g "does not fall back to mock current-user data" \
  --trace=on --reporter=list \
  --output=/tmp/fe-int-gate-followup-me-startup-hosted-rerun
```

Expected hosted result after refresh:

- `interceptedMeRequests > 0`
- page body matches auth/error text for injected 401
- page body does not match serving mock / seed fallback text
- page body does not include `op-fe-gate`, `portfolio_manager`, or mock operator
  current-user data

Optional backend contract guard if the reviewer wants to re-check BFF route
semantics:

```bash
cd /home/lupin/code/pantheon
python3 -m pytest \
  services/control-plane/bff/test_bff_session_auth_me_contract.py \
  services/control-plane/bff/test_bff_consol_013_cookie_session_write_gate.py \
  -q
```

## Parent Absorption Notes

Recommended parent-owner usage:

- Keep `support/evidence/FE-INT-GATE-FOLLOWUP-ME-STARTUP.md` as the parent
  execution evidence.
- Absorb the "Startup Query Gap" and "Operator Journeys" sections only if the
  parent evidence needs a clearer reviewer-facing explanation.
- Do not promote this packet to canonical route truth; backend route truth stays
  in the BFF source, tests, route manifests, and existing canonical contract
  records.
- Do not mark the parent done until hosted strict startup evidence proves the
  deployed Lovable bundle makes the `/bff/me` request.

## Reviewer Checklist

Codex should verify:

| Check | Expected |
|---|---|
| Support-only scope | Only this sidecar artifact is authored by the task, aside from status-system updates |
| No canonical mutation | No L1 policy, route truth, registry, runtime, or governance implementation was changed |
| Contract traceability | `/bff/me` details trace to BFF source/tests and execute-plans frontend source |
| Gap clarity | Packet distinguishes backend DTO proof from frontend startup request proof |
| Parent blocker clarity | Packet does not claim hosted refresh is complete |

## Handoff

This packet is ready for Codex review. The sidecar deliverable is complete once
the task is moved to review with this artifact attached.
