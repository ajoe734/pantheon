# SVC-BFF-AUTH-FACADE-HARDENING BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-BFF-AUTH-FACADE-HARDENING-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-BFF-AUTH-FACADE-HARDENING`
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-29
**Last Refresh**: 2026-04-29T12:30:24Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime-manager behavior, registry logic, governance
implementation, BFF implementation, frontend code, or compose wiring. The
parent owner decides whether and how to absorb this packet into
`SVC-BFF-AUTH-FACADE-HARDENING`.

---

## 1. Parent Scope Snapshot

`SVC-BFF-AUTH-FACADE-HARDENING` hardens the operator BFF identity boundary after
`SVC-RUNTIME-HARDENING` closed the runtime-manager auth path and after
`SVC-SURFACES` made the BFF normal read path service-backed. The parent
acceptance target is:

- BFF production/default mode validates JWT issuer, audience, expiry, and
  subject before building `OperatorIdentity`.
- BFF role and MFA checks reject missing, invalid, insufficient, or non-MFA
  high-risk operator actions.
- Legacy colon-format stub tokens remain available only behind explicit
  dev/test stub mode.
- Tests cover valid JWT, invalid JWT, role denial, MFA denial, and dev stub
  compatibility.
- BFF still propagates auth, MFA, and idempotency context to downstream command
  execution.

Frontend-facing disposition: the browser still calls the BFF only. This slice
does not create browser direct calls to runtime-manager, internal API,
governance, deployment, evolution, or other protected services.

---

## 2. Current BFF Auth Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Production identity path | `_extract_identity()` uses JWT validation unless `PANTHEON_BFF_AUTH_STUB=true`. JWT mode maps BFF env vars to `services.runtime_auth_inbound.validate_request_auth()`. | `services/control-plane/bff/main.py` |
| JWT checks | The shared inbound auth helper validates Bearer shape, HS256 signature, `exp`, optional `nbf`, optional issuer, optional audience, and actor/role claims. | `services/runtime_auth_inbound.py` |
| Stub compatibility | Colon-format tokens such as `Bearer op-admin:admin:mfa` are accepted only when `PANTHEON_BFF_AUTH_STUB=true`. In strict JWT mode, colon-format tokens are rejected. | `services/control-plane/bff/main.py`, `services/control-plane/bff/test_bff_auth_facade.py` |
| MFA handling | `X-MFA-Token` is validated when present. When `PANTHEON_BFF_MFA_REQUIRED=true`, missing MFA raises 401 at identity extraction; high-risk BFF validators also require `identity.mfa_verified`. | `services/control-plane/bff/main.py`, `services/runtime_auth_inbound.py` |
| Secret-missing behavior | Missing BFF JWT secret in strict mode is returned to callers as 401 instead of leaking a server-side 500. | `services/control-plane/bff/main.py`, `services/control-plane/bff/test_bff_auth_facade.py` |
| Read auth | Settings and command status endpoints call `_extract_identity()`. General read-role helpers allow `operator`, `approver`, `admin`, and `reviewer`. | `services/control-plane/bff/main.py` |
| Command write auth | `POST /api/v1/operator/commands` authenticates before command validation, builds foundation command context, stores audit auth/MFA facts, and queues backend execution. | `services/control-plane/bff/main.py` |
| Downstream propagation | Command execution sends `Authorization: Bearer <raw_token>` and `X-MFA-Token` when present. | `services/control-plane/bff/command_executor.py` |
| Test posture | BFF tests default to stub auth through `conftest.py`; auth-specific tests explicitly clear stub mode to exercise strict JWT behavior. | `services/control-plane/bff/conftest.py`, `services/control-plane/bff/test_bff_auth_facade.py` |

Parent status at this refresh: `SVC-BFF-AUTH-FACADE-HARDENING` is in `review`.
The task summary records all 313 BFF tests passing with
`python3 -m pytest services/control-plane/bff/ -q`.

---

## 3. BFF Query And Config Gap Matrix

| Topic | Current BFF/frontend surface | Remaining handoff gap | Frontend implication |
|---|---|---|---|
| JWT bootstrap config | Code defaults to strict JWT mode and requires `PANTHEON_BFF_JWT_SECRET` for verifiable JWTs. | Root compose currently wires many BFF service URLs, but this sidecar did not find `PANTHEON_BFF_AUTH_MODE`, `PANTHEON_BFF_JWT_SECRET`, `PANTHEON_BFF_JWT_ISSUER`, or `PANTHEON_BFF_JWT_AUDIENCE` in the `operator-bff` compose env. | Environment owners must supply real BFF JWT config before frontend QA expects production JWT login to work. Without it, strict-mode JWT calls fail with 401. |
| Stub mode boundary | `PANTHEON_BFF_AUTH_STUB=true` keeps local colon-token tests and development flows working. | Stub mode must stay explicit and should not be set in production-like frontend QA unless that QA is deliberately stub-backed. | Frontend smoke examples using `op-admin:admin:mfa` are stub-only. Real QA must use signed JWTs from the chosen auth issuer. |
| Global MFA env | `PANTHEON_BFF_MFA_REQUIRED=true` enforces MFA at identity extraction for every BFF route using `_extract_identity()`. | This is a coarse BFF-wide gate. Command-specific validators also enforce MFA for high-risk actions such as `IssueSafeMode`, `LiquidateAll`, `ActivateKillSwitch`, and admin settings writes. | UI should send `X-MFA-Token` only after an MFA prompt/session step; do not assume every read requires MFA unless the deployed env enables the global gate. |
| Role denial semantics | BFF validators return 403 with structured BFF errors for insufficient role or missing MFA on high-risk actions. | Frontend should distinguish 401 auth/session failure from 403 role/MFA denial and 422 payload validation. | Show re-auth for 401; show role/MFA escalation or disabled CTA state for 403; show form/input repair for 422. |
| Command status polling | `GET /api/v1/operator/commands/{command_id}` requires auth but does not take `X-MFA-Token`. | The polling request may fail 401 if the session/JWT is expired or the deployment has global MFA required. | Keep command receipt data visible locally after submit; if polling returns 401, prompt re-auth without losing `command_id`, trace ids, or audit reason. |
| Downstream auth propagation | Command worker stores raw submitted token and MFA token in command audit, then passes them into `command_executor`. | If frontend sends a browser-scoped JWT, downstream runtime/internal APIs must accept the same token contract or the command status will record downstream failure. | UI should render downstream failure from command status and not retry with a different token shape client-side. |
| Idempotency preservation | `POST /api/v1/operator/commands` accepts `X-Idempotency-Key`, deduplicates identical payloads, and rejects changed payloads for the same key. | Auth failures occur before command persistence; idempotency only applies after BFF admission. | Preserve the same idempotency key across safe retries after transport uncertainty, but use a new key after user edits payload/audit reason. |
| Service-surface dependency | `SVC-SURFACES` made `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false` the normal stack path and wired service URLs in compose. | Auth hardening should not reintroduce seeded local fixtures as a way to make frontend QA pass. | Degraded/unavailable read surfaces remain backend-owned states; do not bypass BFF auth by calling protected services directly. |

---

## 4. Operator Journey Handoff

### 4.1 Normal JWT-Backed Journey

1. Operator signs in through the frontend auth provider and receives a signed
   bearer JWT whose claims satisfy the BFF deployment config.
2. Frontend calls BFF read surfaces with `Authorization: Bearer <jwt>`.
3. BFF validates the JWT and resolves `OperatorIdentity` from `sub` plus
   `roles` or `role` claims.
4. UI renders CTAs from backend-owned allowed-action metadata and known role
   posture; it does not create hidden authority locally.
5. Operator submits writes through `POST /api/v1/operator/commands` with:
   `Authorization`, optional `X-MFA-Token`, optional trace/correlation/request
   headers, optional `X-Idempotency-Key`, and audit context.
6. BFF validates auth, role, MFA, command payload, concurrent target safety, and
   read-surface staleness before accepting the command.
7. UI renders the BFF receipt and polls
   `GET /api/v1/operator/commands/{command_id}` until status is terminal.
8. UI refreshes the owning read surface after command completion instead of
   locally deriving runtime, kill-switch, deployment, or governance state.

### 4.2 Auth, Role, Or MFA Denial Journey

1. A 401 from BFF means the browser session/token is missing, invalid, expired,
   has the wrong issuer/audience, lacks a configured secret path, or is missing
   required global MFA.
2. A 403 from BFF means the token identity was resolved but lacks the required
   role or high-risk MFA verification for that operation.
3. A 422 from BFF means the command payload is invalid, not that auth failed.
4. If BFF accepts a command and downstream auth later fails, the failure appears
   through `GET /api/v1/operator/commands/{command_id}` as command status/error.
5. UI should keep the original command receipt, command id, trace/correlation
   ids, and audit reason visible for support and replay investigation.

### 4.3 Stub-Backed Local Development Journey

1. Set `PANTHEON_BFF_AUTH_STUB=true` only for local/dev/test work.
2. Use colon-format tokens such as:
   - `Bearer op-operator:operator`
   - `Bearer op-approver:approver`
   - `Bearer op-admin:admin:mfa`
3. Keep this mode fenced from production-like QA. In strict JWT mode the same
   colon-format token must return 401.

### 4.4 Degraded Or BFF-Outage Journey

1. If BFF is reachable but read surfaces are degraded, keep backend-owned
   degraded/unavailable metadata visible and disable dependent CTAs.
2. Read `GET /api/v1/operator/degraded-control-guidance` for secondary control
   path instructions.
3. Browser code must not call runtime-manager, internal APIs, governance,
   deployment, evolution, or other protected backend services as a hidden
   fallback.
4. High-privilege fallback remains an operator/admin workflow outside the
   browser, following the BFF HA policy and protected service auth gates.

---

## 5. Frontend Handoff Materials

This sidecar does not create a new Lovable task or a new frontend
implementation request. Use the existing BFF/frontend materials and apply the
auth notes below.

| Screen / flow | Existing material | Auth facade handoff note |
|---|---|---|
| Operator command path | `POST /api/v1/operator/commands`, `GET /api/v1/operator/commands/{command_id}` | Preserve `Authorization`, `X-MFA-Token`, trace/correlation/request headers, and `X-Idempotency-Key`. Render BFF 401/403/422 distinctly. |
| Settings | `GET /api/v1/settings`, `POST /api/v1/settings`, settings import/export routes | Reads require auth. Writes/import require admin plus MFA. Do not show settings write success until BFF returns 200. |
| Incident action drawer | `docs/bff/PKT-002-incident-action-drawer.md`, `docs/pantheon-delivery/PKT-002-incident-action-drawer/CONTRACT_LOCK.md` | Runtime-impacting actions should collect MFA before submission when the action is high risk. |
| Runtime and health boards | `docs/bff/PKT-010-runtime-state-board.md`, `docs/bff/PKT-011-health-status-board.md` | Keep BFF as the read authority. Auth failure is not a cue to call runtime-manager directly. |
| Deployment / approval review | `services/control-plane/bff/APP_001C_QUERY_CONTRACT_OUTLINE.md`, `docs/bff/PKT-001-deployment-review-console.md` where present | Approval/reject/revision actions require approver/admin roles. Use backend returned allowed-action state and BFF denial details. |
| Mutation / evolution review | `docs/bff/EW-05-mutation-review.md` | Continue routing approve/reject/execute through BFF command submission and render allowed-action denial as backend-owned. |
| Committee sponsor decision | CW03 committee board BFF surface and `RecordSponsorDecision` command | Use `allowedActions.canRecordSponsorDecision`; do not locally override unavailable committee-board state. |

Frontend implementation constraints:

- keep the existing BFF client as the browser-facing integration point;
- keep stub tokens out of production-like QA unless `PANTHEON_BFF_AUTH_STUB=true`
  is deliberately part of that test environment;
- send `Authorization` on all BFF reads/writes that require identity;
- send `X-MFA-Token` for admin/destructive/high-risk writes after an MFA prompt;
- preserve `X-Idempotency-Key` for command retry safety;
- render 401, 403, 409, 422, and command-status downstream failures without
  collapsing them into one generic network error;
- do not infer approval, runtime, kill-switch, or evolution authority from UI
  role labels, fixture ids, local route names, or approval id formats.

---

## 6. Minimal Smoke Requests For Frontend QA

### 6.1 Stub-Mode Local Smoke Only

Use only when `PANTHEON_BFF_AUTH_STUB=true`.

```http
GET /health
```

```http
GET /api/v1/settings
Authorization: Bearer op-operator:operator
```

```http
POST /api/v1/settings
Authorization: Bearer op-admin:admin:mfa
X-MFA-Token: 000000
Content-Type: application/json

{
  "settings": {
    "general": {
      "theme": "dark"
    }
  }
}
```

```http
POST /api/v1/operator/commands
Authorization: Bearer op-admin:admin:mfa
X-MFA-Token: 000000
X-Idempotency-Key: qa-bff-auth-safe-mode-001
X-Trace-Id: trace-qa-bff-auth-001
Content-Type: application/json

{
  "command": "IssueSafeMode",
  "target": { "type": "Runtime", "id": "runtime-042" },
  "params": { "safe_mode_level": "soft" },
  "audit_context": { "reason": "QA validation of BFF auth facade command receipt flow" }
}
```

```http
GET /api/v1/operator/commands/{command_id}
Authorization: Bearer op-admin:admin:mfa
```

### 6.2 Strict JWT Smoke

Use in production-like QA after the BFF environment supplies:

- `PANTHEON_BFF_AUTH_MODE=strict`
- `PANTHEON_BFF_JWT_SECRET`
- optional `PANTHEON_BFF_JWT_ISSUER`
- optional `PANTHEON_BFF_JWT_AUDIENCE`
- optional `PANTHEON_BFF_MFA_REQUIRED=true`

```http
GET /api/v1/settings
Authorization: Bearer <signed-jwt-with-operator-role>
```

Expected failure probes:

- bad signature or expired JWT returns 401;
- wrong issuer or audience returns 401 when those env expectations are set;
- colon-format stub token returns 401 unless stub mode is explicitly enabled;
- operator-only token on settings write returns 403;
- admin token without MFA on settings write returns 403 unless MFA was already
  validated in the resolved identity.

For command QA, use fixture ids that exist in the target environment and keep
the BFF as the only browser-facing API.

---

## 7. Suggested Parent/Reviewer Verification Focus

| Verification target | Suggested evidence |
|---|---|
| Production JWT path is active by default | Tests or runtime smoke with `PANTHEON_BFF_AUTH_STUB` unset and signed JWTs accepted only when the configured secret/issuer/audience match. |
| Stub mode is explicit | Test proving `Bearer op-admin:admin:mfa` is rejected in strict JWT mode and accepted only with `PANTHEON_BFF_AUTH_STUB=true`. |
| Missing JWT secret is caller-safe | Test proving strict-mode missing secret returns 401 and does not leak a 500 to clients. |
| MFA env is enforced | Test proving `PANTHEON_BFF_MFA_REQUIRED=true` rejects missing `X-MFA-Token` at identity extraction. |
| Route-level MFA still works | Tests proving admin/destructive BFF writes reject identities without MFA even when global MFA env is false. |
| Downstream propagation remains intact | Command execution tests or smoke proving submitted auth and MFA headers reach downstream command executor paths. |
| Idempotency behavior survives auth hardening | Repeat same command with same `X-Idempotency-Key` and payload; assert same receipt. Repeat same key with changed payload; assert conflict and no second side effect. |
| Compose/deploy config is explicit | Deployment environment documents or supplies BFF JWT mode/secret/issuer/audience and keeps stub mode off outside local/dev/test. |

---

## 8. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched by this sidecar | PASS |
| Parent acceptance mapped | PASS |
| BFF auth/query/config gaps identified | PASS |
| Operator journey handoff included | PASS |
| Frontend auth/MFA/idempotency guidance included | PASS |
| Stub-vs-JWT QA boundary included | PASS |
| Reviewer disposition pending | APPROVED — see Section 9 |

---

## 9. Handoff Status

**APPROVED by Claude (reviewer) — 2026-04-29**

Verification performed against live BFF implementation:

- `_extract_identity_jwt` correctly maps `AUTH_JWT_SECRET_MISSING` to 401 (confirmed `main.py:246`).
- `PANTHEON_BFF_MFA_REQUIRED` env var is read and passed to `validate_request_auth` as `mfa_required` (confirmed `main.py:228-230`).
- `conftest.py` autouse fixture sets `PANTHEON_BFF_AUTH_STUB=true` by default; auth-specific tests explicitly clear this to exercise strict JWT mode (confirmed `conftest.py:14-18`).
- `test_settings_contract.py` uses `@patch.dict(PANTHEON_BFF_AUTH_STUB=true)` explicitly (confirmed `test_settings_contract.py:21-24`).
- 34 `test_bff_auth_facade.py` test functions cover all acceptance criteria; 313 BFF tests pass with `python3 -m pytest services/control-plane/bff/ -q`.
- Compose JWT config gap (no `PANTHEON_BFF_JWT_SECRET` / `PANTHEON_BFF_JWT_ISSUER` / `PANTHEON_BFF_JWT_AUDIENCE` in operator-bff env) is correctly identified and noted for environment owners.

This packet is support-only and accurately reflects the hardened auth facade.
Parent owner (`Claude`) can absorb it as support material for
`SVC-BFF-AUTH-FACADE-HARDENING`; it should not be treated as canonical design
promotion by itself.
