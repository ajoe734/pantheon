# FE-INT-GATE-OIDC-DEV-LOGIN — BFF & Frontend Handoff Packet

**Sidecar Task:** FE-INT-GATE-OIDC-DEV-LOGIN-SIDECAR-BFF-HANDOFF
**Parent Task:** FE-INT-GATE-OIDC-DEV-LOGIN
**Helper Kind:** bff_handoff_packet
**Owner:** Claude
**Reviewer:** Codex
**Date:** 2026-05-15
**Status:** Support artifact only — do not modify canonical truth

---

## 1. Purpose

This packet summarises the BFF query state, remaining deploy-time gaps, operator
journey, and frontend wiring checklist for the `FE-INT-GATE-OIDC-DEV-LOGIN`
parent task. It is a support artifact for the reviewer (Codex) and parent task
owner. It does not modify or supersede any L1 canonical files.

---

## 2. What Was Implemented (by Codex)

### 2.1 BFF: `POST /bff/auth/dev-login`

File: `services/control-plane/bff/main.py`

- New `client_credentials` exchange endpoint for dev BFF only.
- Accepts JSON body: `{ "grant_type": "client_credentials", "client_id": "...", "client_secret": "..." }`.
- Issues HS256 JWT signed with `PANTHEON_BFF_JWT_SECRET`. TTL clamped 300–3600 s (default 900 s via `PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS`).
- Endpoint is **disabled** when `PANTHEON_ENV` or `PANTHEON_DEPLOYMENT_STAGE` ∈ `{canary, live, prod, production, staging-live}`.
- Credentials validated via `hmac.compare_digest` against `PANTHEON_BFF_OIDC_CLIENT_ID` / `PANTHEON_BFF_OIDC_CLIENT_SECRET`.
- Default roles in token: `["operator", "reviewer"]`; overridable with `PANTHEON_BFF_DEV_LOGIN_ROLES`.
- Response: `{ access_token, token_type, expires_in, issued_at, expires_at, meta }`.

### 2.2 CI Workflow: `Acquire BFF JWT` step

File: `execute-plans/.github/workflows/pantheon-integration-gate.yml`

- Step runs inline Node.js before `Authenticated BFF smoke` and `Playwright E2E`.
- Reads `PANTHEON_BFF_OIDC_CLIENT_ID` / `PANTHEON_BFF_OIDC_CLIENT_SECRET` from GitHub secrets.
- POSTs to `${PANTHEON_BFF_BASE_URL}/bff/auth/dev-login`.
- Masks the token with `::add-mask::`.
- Exports `PANTHEON_BFF_ACCESS_TOKEN`, `PANTHEON_BFF_SMOKE_BEARER_TOKEN`, `BFF_AUTH_TOKEN` into `$GITHUB_ENV`.

### 2.3 Frontend: `acquireDevBearerToken()` in `runAction.ts`

File: `execute-plans/src/lib/bff/runAction.ts`

- `acquireDevBearerToken(baseUrl)` reads `VITE_BFF_OIDC_CLIENT_ID` / `VITE_BFF_OIDC_CLIENT_SECRET` from `import.meta.env`.
- Uses `VITE_BFF_DEV_LOGIN_PATH` (default `/bff/auth/dev-login`) for the exchange.
- In-memory token cache with 60 s early-refresh guard; respects `expires_in` clamped 300–3600 s.
- `fetchBffMe()` appends `Authorization: Bearer <token>` when a token is available.
- `liveWriteGated()` gates live-write operations on `session_kind ∈ {cookie, bearer}`.

### 2.4 Probe Script Updates

File: `execute-plans/scripts/probe-bff-authenticated-live.mjs`

- Script reads `PANTHEON_BFF_ACCESS_TOKEN` or `PANTHEON_BFF_SMOKE_BEARER_TOKEN` first.
- Falls back to `PANTHEON_BFF_OIDC_CLIENT_ID` / `PANTHEON_BFF_OIDC_CLIENT_SECRET` → `acquireToken()`.
- Checks 27 authenticated read routes (2xx + envelope shape) and 3 precondition
  write routes (allowed error codes).

### 2.5 Documentation Updates

- `docs/deployment/bff-oidc-staging-auth.md`: HS256 fallback section describes the dev-login endpoint.
- `docs/deployment/lovable-dev-staging-operating-rules.md`: dev Lovable env vars updated to `VITE_BFF_OIDC_CLIENT_ID` / `VITE_BFF_OIDC_CLIENT_SECRET`.
- `execute-plans/.env.integration.example`: shows the two client-credential vars and `PANTHEON_BFF_ACCESS_TOKEN` override.

---

## 3. BFF Query Gap Analysis

The table below maps the 27 authenticated smoke routes to their current BFF
status. "Seed" means the route returns a response drawn from seed fixtures.
"Live" means the route is wired to a live backend service or state store.

| Route | Gate Status | Notes |
|---|---|---|
| `GET /bff/me` | Live | Returns session identity from JWT claims. |
| `GET /bff/strategies` | Seed | Returns seed fixture list. |
| `GET /bff/personas` | Seed | Returns seed fixture list. |
| `GET /bff/capital-pools` | Seed | Returns seed fixture list. |
| `GET /bff/rebalances` | Seed | Returns seed fixture list. |
| `GET /bff/deployments` | Seed | Returns seed fixture list. |
| `GET /bff/jobs` | Seed | Returns seed fixture list. |
| `GET /bff/alerts` | Seed | Returns seed fixture list. |
| `GET /bff/incidents` | Seed | Returns seed fixture list. |
| `GET /bff/audit` | Seed | Returns seed fixture list. |
| `GET /bff/artifacts` | Seed | Returns seed fixture list. |
| `GET /bff/runtimes` | Seed | Returns seed fixture list. |
| `GET /bff/mcp-servers` | Seed | Returns seed fixture list. |
| `GET /bff/mcp-tools` | Seed | Returns seed fixture list. |
| `GET /bff/skills` | Seed | Returns seed fixture list. |
| `GET /bff/channels` | Seed | Returns seed fixture list. |
| `GET /bff/tools` | Seed | Returns seed fixture list. |
| `GET /bff/ranking-formulas` | Seed | Returns seed fixture list. |
| `GET /bff/research-experiments` | Seed | Returns seed fixture list. |
| `GET /bff/agora/signals` | Seed | Returns seed fixture list. |
| `GET /bff/agora/inbox` | Seed | Returns seed fixture list. |
| `GET /bff/agora/journal` | Seed | Returns seed fixture list. |
| `GET /bff/agora/postmortems` | Seed | Returns seed fixture list. |
| `GET /bff/agora/ask/sessions` | Seed | Returns seed fixture list. |
| `GET /bff/v5/loop-runs` | Seed | Returns seed fixture list. |
| `GET /bff/v5/sentinel/findings` | Seed | Returns seed fixture list. |
| `GET /bff/v5/interventions` | Seed | Returns seed fixture list. |

**Auth-gate boundary:** With the new `dev-login` flow, all of the above routes
now require a valid bearer JWT rather than the old colon-format stub token. The
session contract is `session_kind=bearer`.

**Precondition write checks covered:**
- `POST /bff/actions/strategies/strategy-dev/promote` → expects `CONFIRM_TOKEN_REQUIRED` or similar
- `POST /bff/approvals/approval-dev/decide` → expects `STATE_CONFLICT` / `RESOURCE_NOT_FOUND`
- `POST /bff/v5/interventions/intervention-dev/decide` → expects `STATE_CONFLICT` / `RESOURCE_NOT_FOUND`

---

## 4. Deploy-Time Checklist (Remaining Gaps)

These items were not completable by the static implementation work; they require
live credentials and runtime deployment actions.

### 4.1 Dev BFF — Environment Variables to Configure

```env
# JWT signing key — generate a unique random secret for the dev BFF only
PANTHEON_BFF_JWT_SECRET=<random-256bit-hex-or-base64>
PANTHEON_BFF_JWT_ISSUER=pantheon-dev
PANTHEON_BFF_JWT_AUDIENCE=bff-operators

# Client credentials — match what CI and Lovable will send
PANTHEON_BFF_OIDC_CLIENT_ID=<dev-client-id>
PANTHEON_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>

# Optional: adjust TTL; default 900 s (15 min)
PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS=900

# Ensure dev_login is not accidentally disabled
# These must NOT be set to staging-live/live/prod/canary values:
PANTHEON_ENV=dev
PANTHEON_DEPLOYMENT_STAGE=dev
```

### 4.2 GitHub Repository Secrets

Add the following secrets in the `execute-plans` repository (or the parent
repository that runs the integration gate workflow):

| Secret Name | Value | Replaces |
|---|---|---|
| `PANTHEON_BFF_OIDC_CLIENT_ID` | `<dev-client-id>` | `PANTHEON_BFF_SMOKE_BEARER_TOKEN` (old colon-format token) |
| `PANTHEON_BFF_OIDC_CLIENT_SECRET` | `<dev-client-secret>` | (new) |

Remove the old `PANTHEON_BFF_SMOKE_BEARER_TOKEN` secret after CI is verified green.

### 4.3 Lovable Dev Project (`pantheon-ui-dev`) — Rebuild Required

Update the Lovable dev project environment variables and trigger a publish/rebuild:

```env
VITE_PANTHEON_ENV=dev
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
VITE_BFF_DEV_LOGIN_PATH=/bff/auth/dev-login
VITE_BFF_OIDC_CLIENT_ID=<dev-client-id>
VITE_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>
VITE_PANTHEON_LIVE_BROKER_ENABLED=false
```

`VITE_BFF_OIDC_CLIENT_SECRET` is intentionally public in the dev project
(dev-only, short-TTL, revocable). Do **not** set this in the staging-live
project.

### 4.4 Dev BFF CORS

Verify `PANTHEON_BFF_CORS_ORIGINS` includes both current Lovable dev origins:

```env
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app
```

### 4.5 Verification Sequence (Post-Deploy)

Run these after the dev BFF is redeployed and secrets are configured:

```bash
# 1. Confirm dev-login responds correctly on the live BFF
curl -s -X POST https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/auth/dev-login \
  -H "Content-Type: application/json" \
  -d '{"grant_type":"client_credentials","client_id":"<dev-client-id>","client_secret":"<dev-client-secret>"}' \
  | jq '{access_token_present: (.access_token!=null), expires_in, token_type}'

# 2. Use acquired token to call /bff/me
TOKEN=$(curl -s ... | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/me | jq .data.session

# 3. Re-run the integration gate CI workflow
#    (trigger workflow_dispatch on execute-plans/.github/workflows/pantheon-integration-gate.yml)

# 4. Verify dev JWT is rejected by staging-live BFF
curl -s -H "Authorization: Bearer $TOKEN" \
  https://pantheon-staging-bff.34.81.225.122.sslip.io/bff/me
# Expect: 401 INVALID_TOKEN or similar

# 5. Run focused BFF auth tests
python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py services/control-plane/bff/test_bff_oidc_staging_env_contract.py -q
```

---

## 5. Operator Journey

This section describes the dev operator experience after deployment is complete.

### 5.1 Browser Login (Lovable Dev)

1. Open `https://pantheon-dev.lovable.app`.
2. The frontend reads `VITE_BFF_OIDC_CLIENT_ID` and `VITE_BFF_OIDC_CLIENT_SECRET`
   from the build-time env.
3. `acquireDevBearerToken()` POSTs to `/bff/auth/dev-login` and caches the JWT.
4. Subsequent calls to `/bff/me`, `/bff/strategies`, etc. include
   `Authorization: Bearer <token>`.
5. Token auto-refreshes 60 s before expiry (default 15 min TTL).
6. Dashboard shows authenticated session, `session_kind=bearer`.
7. Operator can view all read routes (strategies, personas, etc.) without 401.

### 5.2 CI Gate Login

1. Workflow runs "Acquire BFF JWT" step using `PANTHEON_BFF_OIDC_CLIENT_ID` /
   `PANTHEON_BFF_OIDC_CLIENT_SECRET` GitHub secrets.
2. Token is masked and exported to all downstream steps as
   `PANTHEON_BFF_ACCESS_TOKEN`.
3. "Authenticated BFF smoke" and "Playwright E2E" steps use this token.
4. Smoke checks 30 routes; e2e checks `MeResponse` shape.

### 5.3 Staging-Live Isolation

- Staging BFF uses RS256/OIDC discovery — no `PANTHEON_BFF_JWT_SECRET`.
- `PANTHEON_DEPLOYMENT_STAGE=staging-live` disables the `/bff/auth/dev-login` route.
- A dev HS256 token sent to staging BFF returns `401 INVALID_TOKEN`.
- This boundary enforces that dev credentials cannot reach the live-broker-adjacent
  staging environment.

---

## 6. Frontend Wiring Summary

| Env Var | Where | Purpose |
|---|---|---|
| `VITE_BFF_OIDC_CLIENT_ID` | Lovable dev build | Client id for dev-login |
| `VITE_BFF_OIDC_CLIENT_SECRET` | Lovable dev build | Client secret (dev-only, public) |
| `VITE_BFF_DEV_LOGIN_PATH` | Lovable dev build | Override login path (default `/bff/auth/dev-login`) |
| `VITE_BFF_MODE` | Lovable dev build | Set to `live` for live BFF mode |
| `VITE_BFF_BASE_URL` | Lovable dev build | BFF HTTPS base URL |
| `PANTHEON_BFF_OIDC_CLIENT_ID` | GitHub secret | CI JWT acquisition |
| `PANTHEON_BFF_OIDC_CLIENT_SECRET` | GitHub secret | CI JWT acquisition |

---

## 7. Known Gaps Not Covered by This Sidecar

- **Parent task `FE-INT-GATE-OIDC-DEV-LOGIN`** is currently in `review` with Codex2
  as reviewer. This packet is parallel support; it does not bypass or replace that
  review gate.
- **`BFF-CONSOL-023`** (prod strict cutover) is blocked on a Lovable main env
  rebuild. The deploy-time steps in §4 above apply to the dev BFF only; prod BFF
  cutover is tracked separately.
- **Live CI / browser probe verification** depends on the operator completing §4.
  Neither the parent task nor this sidecar can pre-verify live endpoints.
- **Staging-live rejection test** (acceptance criterion 9 in parent task) must be
  run manually or via CI after credentials are configured.

---

## 8. Review Handoff

This packet is ready for Codex review. Suggested review focus areas:

1. Verify §3 BFF query gap table is accurate against current `main.py` route set.
2. Confirm §4.1 env var list matches the `bff-oidc-staging-auth.md` and
   `lovable-dev-staging-operating-rules.md` docs updated by the parent task.
3. Check §5 operator journey is consistent with the actual frontend token cache
   logic in `execute-plans/src/lib/bff/runAction.ts`.
4. Flag any deploy-time gap missing from §4 that the parent task owner needs
   before acceptance criteria can be verified.

If this packet is accepted, the parent task owner (Codex) can use §4 as a
deploy checklist and §5 as a smoke-test runbook during final verification.
