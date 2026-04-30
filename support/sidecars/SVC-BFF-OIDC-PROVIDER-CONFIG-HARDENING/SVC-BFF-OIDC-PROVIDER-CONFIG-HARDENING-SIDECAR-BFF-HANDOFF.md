# SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING-SIDECAR-BFF-HANDOFF`  
**Parent Task**: `SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING`  
**Parent Owner**: `Claude` as of closeout (`Codex` at packet generation)  
**Parent Reviewer**: `Codex2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-04-30  
**Mutates Canonical**: `no`

This packet is support material only. It does not change canonical truth, L1
policy, core service contracts, runtime-manager behavior, registry logic,
governance implementation, BFF runtime code, compose wiring, or frontend code.
The parent owner decides whether and how to absorb this packet into
`SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING`.

---

## 1. Parent Scope Snapshot

`SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING` moves the BFF auth posture from the
current env-level JWT/JWKS capability toward a staging IdP configuration that is
explicit enough for operator and frontend rehearsal.

Parent acceptance target from `ai-status.json`:

- OIDC discovery or documented issuer/JWKS config is supported.
- Claim-to-role and MFA mapping is explicit and tested.
- JWKS rotation cache miss and sanitized error behavior are tested.
- Staging env example documents IdP variables without secrets.
- HS256 dev fallback remains explicit.
- Focused auth tests and compose config pass.

Frontend-facing disposition: the browser still calls the BFF only. This sidecar
does not propose browser direct calls to runtime-manager, internal API,
governance, deployment, evolution, registry, or any other protected backend
service.

---

## 2. Current BFF/OIDC Snapshot

| Area | Current fact | Evidence |
| --- | --- | --- |
| BFF auth dispatch | `_extract_identity()` uses strict JWT/JWKS validation unless `PANTHEON_BFF_AUTH_STUB=true`; stub mode is fenced to local/dev/test. | `services/control-plane/bff/main.py` |
| BFF env mapping | BFF env vars map into `services.runtime_auth_inbound.validate_request_auth()`: auth mode, HS256 secret, issuer, audience, default role, MFA required, JWKS URI, OIDC issuer, and OIDC audience. | `services/control-plane/bff/main.py` |
| JWKS path | When `PANTHEON_BFF_JWKS_URI` is set, JWT-shaped bearer tokens use RS256/ES256 JWKS verification instead of HS256. | `services/runtime_auth_inbound.py` |
| JWKS cache | JWKS keys are fetched through `_fetch_jwks_keys()` and cached in memory for five minutes. Fetch/parse failures raise sanitized auth errors. | `services/runtime_auth_inbound.py` |
| Claim to identity | `sub`, `actor_id`, or `preferred_username` becomes the operator id. `roles` list/string or `role` becomes BFF roles; otherwise `PANTHEON_BFF_DEFAULT_ROLE` defaults to `operator`. | `services/runtime_auth_inbound.py` |
| MFA signal | BFF currently treats a valid six-digit `X-MFA-Token` header as MFA verification. OIDC `amr`/`acr`/custom MFA claims are not currently mapped to `mfa_verified`. | `services/runtime_auth_inbound.py`, `services/control-plane/bff/main.py` |
| Read roles | BFF read surfaces require one of `operator`, `approver`, `admin`, or `reviewer`. | `services/control-plane/bff/main.py` |
| High-risk writes | `LiquidateAll`, `IssueSafeMode`, and `ActivateKillSwitch` require `admin` plus MFA. Other command validators enforce route-specific roles. | `services/control-plane/bff/main.py` |
| Command auth propagation | `POST /api/v1/operator/commands` stores the submitted bearer token and MFA token in command audit and passes them to downstream execution. | `services/control-plane/bff/main.py`, `services/control-plane/bff/command_executor.py` |
| Staging env gap | `env/prod-control.env.example` and `docker-compose.control.yml` currently wire BFF CORS, service URLs, and runtime-manager tokens, but do not yet document or pass through the BFF OIDC/JWKS env vars. | `env/prod-control.env.example`, `docker-compose.control.yml` |
| Frontend public URL | Lovable staging-live should use the public HTTPS BFF URL and one-to-one CORS origin, not VM-internal HTTP endpoints. | `docs/deployment/frontend-lovable-environments.md`, `docs/deployment/bff-https-ingress.md`, `docs/deployment/lovable-dev-staging-operating-rules.md` |

---

## 3. BFF Query And Provider Config Gap Matrix

| Topic | Current BFF/frontend surface | Remaining parent gap | Frontend/operator implication |
| --- | --- | --- | --- |
| OIDC discovery vs documented JWKS | The code supports a direct `PANTHEON_BFF_JWKS_URI` plus issuer/audience validation. It does not currently fetch `.well-known/openid-configuration`. | Parent should either implement discovery or explicitly document the issuer/JWKS/audience config as the supported staging path. | Frontend should not derive backend JWKS settings. It only needs an IdP client config that issues tokens matching the BFF issuer/audience expectations. |
| Staging IdP env | `PANTHEON_BFF_JWKS_URI`, `PANTHEON_BFF_OIDC_ISSUER`, `PANTHEON_BFF_OIDC_AUDIENCE`, `PANTHEON_BFF_AUTH_MODE`, `PANTHEON_BFF_DEFAULT_ROLE`, and `PANTHEON_BFF_MFA_REQUIRED` are code-supported. | VM1 compose and `env/prod-control.env.example` should expose non-secret IdP placeholders and keep any secret values out of the example. | Staging-live QA cannot expect real OIDC login until VM1 BFF receives the same issuer/audience/JWKS settings used by the frontend IdP client. |
| Role claim contract | The current mapper accepts `roles`, `role`, or default role. It does not map arbitrary IdP group names or namespaced claims. | Parent should make the accepted claim names and role values explicit and test at least list, string, fallback, and denial cases. | UI must not infer authority from IdP group names. It should use BFF returned data and BFF command denials as the authority signal. |
| MFA mapping | Current MFA verification is header-based through `X-MFA-Token`; high-risk BFF validators check `identity.mfa_verified`. | Parent must decide whether staging IdP MFA is represented by the existing header path or by an OIDC claim such as `amr`/`acr`; whichever path is chosen must be documented and tested. | If MFA remains header-based, the UI needs a step-up prompt that sends `X-MFA-Token` for high-risk writes. If claim-based MFA is added, the UI needs to request/refresh a token containing that claim. |
| JWKS rotation | JWKS keys are cached for five minutes. Unknown `kid` returns a sanitized 401, but the current code does not visibly force a cache bypass/refetch on unknown `kid` within the TTL. | Parent should add or explicitly test rotation cache-miss behavior: stale cache + new `kid` should refetch once and then accept or reject cleanly. | During key rotation, UI should treat 401 as re-auth/refresh needed and preserve the pending command context; it should not retry destructive writes with changed payloads. |
| Sanitized auth errors | BFF maps missing secret, JWKS fetch failure, no matching key, invalid key, and missing crypto library into generic 401 output without env/URI leakage. | Parent should keep this behavior while adding staging provider config and rotation tests. | Frontend should show re-auth/session-invalid UX for 401. It should not display backend config names or JWKS URLs. |
| HS256 and stub fallback | HS256 works when JWKS URI is unset and `PANTHEON_BFF_JWT_SECRET` is set. Colon-format stub tokens work only when `PANTHEON_BFF_AUTH_STUB=true`; permissive structured tokens are still possible if auth mode is explicitly permissive. | Parent should keep staging strict and keep HS256/stub paths documented as dev/test only. | Frontend QA using `Bearer op-admin:admin:mfa` is stub-only. Staging-live should use real JWTs from the chosen IdP. |
| Browser command headers | CORS currently allows `Authorization` and `X-MFA-Token`. Command submission also naturally uses `X-Idempotency-Key`, `X-Trace-Id`, `X-Correlation-Id`, and `X-Request-Id`. | Parent or a follow-up should confirm whether the staging browser client sends those command headers and extend CORS if needed. | If the browser sends non-allowlisted command headers, preflight can fail before auth reaches BFF. Frontend should know which headers are allowed in staging. |
| Downstream token contract | BFF command execution propagates the original bearer token and MFA token to downstream command execution. | If VM2 runtime-manager/internal API will receive the same OIDC JWT, VM2 auth env must accept the same issuer/JWKS/audience or the command can be accepted by BFF but fail downstream. | UI should render downstream command-status failures distinctly from BFF admission failures and keep command id/trace/idempotency context visible. |

---

## 4. Operator Journey Handoff

### 4.1 Staging OIDC Login And Read Journey

1. Operator signs in through the frontend IdP client and receives a bearer JWT.
2. The frontend calls the public HTTPS staging BFF with
   `Authorization: Bearer <oidc-jwt>`.
3. BFF verifies the token through the configured JWKS URI, issuer, audience,
   expiry, signature, and `kid`.
4. BFF maps token claims into `OperatorIdentity` and enforces read-role access.
5. UI renders BFF read surfaces and backend-owned allowed-action metadata. It
   does not call protected services directly and does not invent authority from
   frontend route names or IdP group labels.

### 4.2 High-Risk Command Journey

1. Operator initiates a write through `POST /api/v1/operator/commands`.
2. Frontend sends:
   - `Authorization: Bearer <oidc-jwt>`
   - `X-MFA-Token: <six-digit-step-up-token>` when the deployed MFA path is header-based
   - `X-Idempotency-Key` for safe retry
   - optional `X-Trace-Id`, `X-Correlation-Id`, and `X-Request-Id`
3. BFF authenticates the token, validates role/MFA, validates the command
   payload, checks concurrent target safety, and persists the command receipt.
4. UI renders the 202 receipt and polls
   `GET /api/v1/operator/commands/{command_id}` with the same bearer session.
5. UI refreshes the owning read surface after terminal command status rather
   than locally deriving runtime, kill-switch, deployment, or governance state.

### 4.3 401, 403, 409, And 422 UX Split

| Response | Meaning for this journey | UI handling |
| --- | --- | --- |
| 401 | Missing/expired/invalid JWT, wrong issuer/audience, JWKS fetch/matching failure, or global MFA gate failure. | Prompt re-auth or token refresh. Preserve form state, command receipt, trace ids, and idempotency key. |
| 403 | Token identity is valid but lacks required role or command-specific MFA. | Show role or MFA escalation. Do not silently retry. |
| 409 | Idempotency/concurrent target safety blocked admission. | Keep the existing command reference visible and avoid changing the payload under the same idempotency key. |
| 422 | Payload shape or command params are invalid. | Keep auth/session state intact and ask the operator to repair inputs. |
| Command-status error | BFF accepted the command, but downstream execution failed. | Render as downstream command status; do not resubmit with a different token shape client-side. |

### 4.4 Key Rotation / IdP Outage Journey

1. If a token validates before rotation and later receives 401 after IdP key
   rotation, the UI should request a fresh token and retry only idempotent reads.
2. If a destructive command submission already returned 202, the UI should keep
   polling by command id after re-auth instead of creating a replacement command.
3. If JWKS fetch is unavailable, BFF should return a generic 401 with no
   provider URI or env name. UI should present a session/auth problem and leave
   deeper diagnosis to operator logs.

---

## 5. Frontend Handoff Materials

### 5.1 Staging-Live Frontend Env

Lovable staging-live remains public-browser config only:

```env
VITE_PANTHEON_ENV=staging-live
VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=true
```

Frontend IdP values may include public issuer/client-id/audience settings, but
must not include broker credentials, BFF private keys, JWT signing secrets,
JWKS private keys, runtime-manager tokens, or API tokens.

### 5.2 Expected BFF Staging Env Shape

The parent implementation can use this as a non-secret staging env checklist:

```env
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_JWKS_URI=https://<idp-host>/.well-known/jwks.json
PANTHEON_BFF_OIDC_ISSUER=https://<idp-host>/
PANTHEON_BFF_OIDC_AUDIENCE=<bff-or-frontend-client-audience>
PANTHEON_BFF_DEFAULT_ROLE=operator
PANTHEON_BFF_MFA_REQUIRED=false
```

`PANTHEON_BFF_JWT_SECRET` is HS256/dev fallback only and should not be needed
for pure OIDC/JWKS staging. If a provider secret or client secret is introduced
later, keep it out of example env files and Lovable `VITE_` vars.

### 5.3 Role And MFA Contract To Give Frontend

| IdP/BFF field | Expected staging meaning |
| --- | --- |
| `sub` | Stable operator id shown in audit/command traces. |
| `roles` or `role` | BFF role values: `operator`, `approver`, `admin`, `reviewer`. |
| no role claim | Falls back to `PANTHEON_BFF_DEFAULT_ROLE`; parent should avoid silent privilege escalation. |
| `X-MFA-Token` | Current BFF-recognized MFA signal. Required for admin/destructive operations that check `mfa_verified`. |
| future `amr`/`acr` claim | Not currently mapped; only use it for UI authority after parent implements and tests it. |

### 5.4 Minimal Smoke Requests

Health and CORS:

```bash
curl -fsS https://pantheon-staging-bff.34.81.225.122.sslip.io/health
curl -sS -D - -o /tmp/staging-cors-body \
  -X OPTIONS https://pantheon-staging-bff.34.81.225.122.sslip.io/api/v1/settings \
  -H 'Origin: https://pantheon-ai-system-front-staging-live.lovable.app' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: Authorization,X-MFA-Token'
```

Read smoke:

```http
GET /api/v1/settings
Authorization: Bearer <oidc-jwt-with-operator-or-admin-role>
```

High-risk command smoke after operator approval:

```http
POST /api/v1/operator/commands
Authorization: Bearer <oidc-jwt-with-admin-role>
X-MFA-Token: 000000
X-Idempotency-Key: qa-bff-oidc-safe-mode-001
X-Trace-Id: trace-qa-bff-oidc-001
Content-Type: application/json

{
  "command": "IssueSafeMode",
  "target": { "type": "Runtime", "id": "runtime-042" },
  "params": { "safe_mode_level": "soft" },
  "audit_context": { "reason": "QA validation of BFF OIDC provider config command receipt flow" }
}
```

Failure probes:

- expired token returns 401;
- wrong issuer or audience returns 401;
- unknown or rotated `kid` returns sanitized 401 unless parent implements cache
  miss refresh and the new key is reachable;
- `Bearer op-admin:admin:mfa` returns 401 unless `PANTHEON_BFF_AUTH_STUB=true`;
- valid operator role without admin returns 403 for admin-only commands;
- admin token without MFA returns 403 for high-risk commands.

---

## 6. Verification Run For This Packet

Commands run from `/home/edna/code/pantheon` on 2026-04-30:

```bash
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
git status --short -- support/sidecars/SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING/SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING-SIDECAR-BFF-HANDOFF.md ai-status.json current-work.md ai-activity-log.jsonl
```

Result:

- `services/control-plane/bff/test_bff_auth_facade.py`: `52 passed in 4.14s`
- support artifact exists as the only task-scoped file added by this sidecar
- `ai-status.json` and `current-work.md` were already dirty in the shared
  worktree before sidecar handoff; they are status/generated files and should be
  reviewed separately from this packet content

Closeout verification on 2026-04-30T03:22:52Z:

```bash
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
```

Result: `56 passed in 4.07s`. The increased count reflects current shared
worktree auth test coverage; this sidecar still changes support material only.

---

## 7. Reviewer Checklist

Recommended Claude review focus for this sidecar:

| Check | Expected answer |
| --- | --- |
| Did this sidecar avoid canonical/runtime implementation edits? | Yes. It only adds this support packet. |
| Does it separate current implemented facts from parent gaps? | Yes. Current facts are tied to BFF/runtime auth code and deployment docs; gaps are phrased as parent decisions. |
| Does it avoid leaking or inventing IdP secrets? | Yes. It uses placeholders only and keeps secrets out of frontend/env examples. |
| Does it preserve browser-to-BFF-only integration? | Yes. It repeatedly keeps protected backend services behind BFF/operator paths. |
| Does it identify frontend-visible auth outcomes? | Yes. It splits 401/403/409/422 and downstream command-status failure behavior. |

## 8. Non-Claims

This packet does not claim:

| Non-claim | Correct disposition |
| --- | --- |
| The parent task is implemented, reviewed, or done. | Parent remains separately owned and reviewed through `ai-status.json`. |
| A real staging IdP has been selected or provisioned. | The packet only identifies the handoff contract and missing config surface. |
| OIDC discovery is currently implemented. | Current support is direct documented JWKS/issuer/audience config. |
| OIDC `amr`/`acr` MFA claims are already honored. | Current support is `X-MFA-Token`; parent must implement/test claim-based MFA if desired. |
| JWKS rotation cache miss is fully hardened. | Current cache exists; unknown `kid` behavior needs parent hardening or explicit tests. |
| Staging-live Lovable can be published by this sidecar. | Publishing remains an operator-controlled workflow outside this support slice. |

## 9. Handoff

To: `Claude`  
From: `Codex`  
Requested review outcome: approve this sidecar if it is an accurate,
support-only BFF/frontend handoff packet for
`SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING`.

Suggested parent-owner pickup:

1. Wire/document non-secret BFF OIDC env vars in staging compose/env examples.
2. Decide whether direct JWKS config is sufficient or OIDC discovery is needed.
3. Make role and MFA claim mapping explicit and test the chosen contract.
4. Add or test JWKS stale-cache/new-`kid` rotation behavior.
5. Confirm browser CORS headers cover the frontend command headers actually used
   in staging-live.
6. Confirm downstream VM2 auth accepts the propagated token shape before
   high-risk staging-live command rehearsal.
