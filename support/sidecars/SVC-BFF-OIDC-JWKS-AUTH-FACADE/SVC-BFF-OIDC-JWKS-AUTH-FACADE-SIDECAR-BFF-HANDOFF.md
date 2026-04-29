# BFF Handoff Packet — SVC-BFF-OIDC-JWKS-AUTH-FACADE

**Sidecar kind:** `bff_handoff_packet`
**Parent task:** `SVC-BFF-OIDC-JWKS-AUTH-FACADE`
**Prepared by:** Claude2
**Date:** 2026-04-29
**Status:** ready for reviewer (Claude)

---

## 1. Purpose

This packet supports the parent task owner (Claude) implementing OIDC/JWKS auth
validation in the BFF auth facade. It documents:

- the current BFF auth gap relative to the parent task acceptance criteria
- the operator journey changes a frontend client will observe
- env var contracts the frontend/operator tooling must set

This is a support artifact only. It does not change canonical truth or runtime code.

---

## 2. Current BFF Auth Facade State

### Auth resolution path (`services/control-plane/bff/main.py`)

```
_extract_identity(authorization, mfa_token)
  └─ if PANTHEON_BFF_AUTH_STUB=true  → _extract_identity_stub()   [legacy colon format]
  └─ else                            → _extract_identity_jwt()     [production path]
       └─ validate_request_auth() from services.runtime_auth_inbound
            ├─ Bearer <token> required
            ├─ if looks_like_jwt (two dots, no colon)
            │    ├─ strict mode: requires PANTHEON_RUNTIME_JWT_SECRET
            │    └─ verifies HS256 via _verify_jwt_hs256()
            │         checks: alg=HS256, signature, exp, nbf, iss, aud
            └─ else (colon-format)
                 └─ strict mode rejects; permissive accepts structured token
```

BFF env var mapping (wraps runtime_auth_inbound internal names):

| BFF env var | Maps to runtime_auth_inbound key |
|---|---|
| `PANTHEON_BFF_AUTH_MODE` | `PANTHEON_RUNTIME_AUTH_MODE` |
| `PANTHEON_BFF_JWT_SECRET` | `PANTHEON_RUNTIME_JWT_SECRET` |
| `PANTHEON_BFF_JWT_ISSUER` | `PANTHEON_RUNTIME_JWT_ISSUER` |
| `PANTHEON_BFF_JWT_AUDIENCE` | `PANTHEON_RUNTIME_JWT_AUDIENCE` |
| `PANTHEON_BFF_MFA_REQUIRED` | `PANTHEON_RUNTIME_MFA_REQUIRED` |
| `PANTHEON_BFF_DEFAULT_ROLE` | `PANTHEON_RUNTIME_DEFAULT_ROLE` |

### What is NOT currently supported

- **No asymmetric algorithms (RS256, ES256)** — `_verify_jwt_hs256` rejects any `alg ≠ HS256`.
- **No JWKS endpoint fetch** — no public key resolution by `kid`.
- **No OIDC discovery** — issuer/audience validation is present but only for HS256.
- **No `kid` header matching** — JWT header `kid` claim is parsed but never used for key selection.
- **No JWKS key cache** — no retry/fallback on JWKS fetch failure.

---

## 3. BFF Query Gap for Parent Task

These are the gaps the parent task `SVC-BFF-OIDC-JWKS-AUTH-FACADE` must close,
derived from the task's acceptance criteria:

| Gap | Acceptance criterion | Implementation location |
|---|---|---|
| JWKS fetch and cache | "JWKS issuer audience expiry subject and kid validation covered by tests" | `services/runtime_auth_inbound.py` — new `_fetch_jwks()` + cache |
| RS256/ES256 JWT verification | Same | `runtime_auth_inbound._verify_jwt_jwks()` replacing/extending `_verify_jwt_hs256` dispatch |
| `kid` header matching | "JWKS … kid validation" | JWT header decode already exists; needs key selection logic |
| Cache failure → generic 401 | "JWKS fetch cache failures return generic auth errors without env leaks" | Error handling in `validate_request_auth` + BFF `_extract_identity_jwt` |
| BFF env vars for JWKS | Implied by OIDC mode | New `PANTHEON_BFF_JWKS_URI` / `PANTHEON_BFF_OIDC_ISSUER` / `PANTHEON_BFF_OIDC_AUDIENCE` mapped through to runtime_auth_inbound |
| Dev stub still gate-controlled | "HS256 strict mode behavior remains compatible" | `PANTHEON_BFF_AUTH_STUB=true` guard remains; stub must not bleed into OIDC path |

---

## 4. Proposed New Env Vars (Frontend / Operator Contract)

When the parent task adds OIDC/JWKS support, these new BFF env vars are expected.
Frontend teams and compose configs must set them when deploying in OIDC mode.

| Env var | Required? | Description |
|---|---|---|
| `PANTHEON_BFF_JWKS_URI` | Yes (OIDC mode) | HTTPS URL of the JWKS endpoint (e.g. `https://idp.example.com/.well-known/jwks.json`) |
| `PANTHEON_BFF_OIDC_ISSUER` | Recommended | Expected `iss` claim; matches OIDC provider config |
| `PANTHEON_BFF_OIDC_AUDIENCE` | Recommended | Expected `aud` claim; typically the BFF client ID |
| `PANTHEON_BFF_AUTH_MODE` | Existing | Must remain `strict` in production; OIDC mode only activates when `JWKS_URI` is set |
| `PANTHEON_BFF_JWT_SECRET` | HS256 path only | Set for HS256 fallback or hybrid; unset when pure OIDC |

**Mode selection logic (proposed):**

- `PANTHEON_BFF_JWKS_URI` is set → OIDC/JWKS path (RS256/ES256)
- `PANTHEON_BFF_JWT_SECRET` is set, no `JWKS_URI` → HS256 path (existing)
- Neither set + `AUTH_MODE=strict` → 401 (no verifiable tokens)
- `PANTHEON_BFF_AUTH_STUB=true` → stub path (dev/test only)

---

## 5. Operator Journey: Before and After

### Before (HS256 only)

```
Frontend                    BFF (FastAPI)              runtime_auth_inbound
   │                              │                            │
   │  POST /api/v1/...            │                            │
   │  Authorization: Bearer <HS256 JWT>                        │
   │  X-MFA-Token: 123456         │                            │
   ├─────────────────────────────>│                            │
   │                              │  validate_request_auth()   │
   │                              ├──────────────────────────> │
   │                              │  _verify_jwt_hs256()       │
   │                              │  checks: alg, sig, exp,    │
   │                              │  nbf, iss, aud             │
   │                              │ <──────────────────────────│
   │                              │  OperatorIdentity built    │
   │  200 / command accepted      │                            │
   │ <────────────────────────────│                            │
```

### After (OIDC/JWKS added)

```
Frontend                    BFF (FastAPI)              runtime_auth_inbound    IdP JWKS endpoint
   │                              │                            │                       │
   │  POST /api/v1/...            │                            │                       │
   │  Authorization: Bearer <RS256 JWT with kid>               │                       │
   │  X-MFA-Token: 123456         │                            │                       │
   ├─────────────────────────────>│                            │                       │
   │                              │  validate_request_auth()   │                       │
   │                              ├──────────────────────────> │                       │
   │                              │  decode header → kid       │                       │
   │                              │  cache hit? use cached key │                       │
   │                              │  cache miss →              │                       │
   │                              │  ─────────────────────────────────────────────────>│
   │                              │  GET PANTHEON_BFF_JWKS_URI  │                      │
   │                              │  <─────────────────────────────────────────────────│
   │                              │  select key by kid         │                       │
   │                              │  _verify_jwt_jwks()        │                       │
   │                              │  checks: alg, sig, exp,    │                       │
   │                              │  nbf, iss, aud, kid        │                       │
   │                              │ <──────────────────────────│                       │
   │                              │  OperatorIdentity built    │                       │
   │  200 / command accepted      │                            │                       │
   │ <────────────────────────────│                            │                       │
   │                              │  [JWKS fetch fail]         │                       │
   │                              │  → generic 401, no env leak│                       │
   │ 401 generic auth error       │                            │                       │
   │ <────────────────────────────│                            │                       │
```

---

## 6. Frontend Integration Checklist

Items frontend teams / operator tooling must verify after parent task delivery:

- [ ] Token issuer (`iss`) matches `PANTHEON_BFF_OIDC_ISSUER`
- [ ] Token audience (`aud`) includes `PANTHEON_BFF_OIDC_AUDIENCE`
- [ ] JWT header contains `kid` that matches a key in the configured JWKS endpoint
- [ ] Token algorithm is RS256 or ES256 (HS256 only when `JWKS_URI` is unset)
- [ ] `PANTHEON_BFF_JWKS_URI` is reachable from the BFF container (network policy)
- [ ] `PANTHEON_BFF_AUTH_STUB` is NOT set in production or canary
- [ ] MFA header (`X-MFA-Token`) is still required on `mfa_required` routes
- [ ] Error response shape remains `{"error": {"code": ..., "message": ..., "details": {...}}}` — no new fields
- [ ] CORS: `Authorization` and `X-MFA-Token` remain in `allow_headers` (no change needed)

---

## 7. Error Codes Frontend May Observe (No Change)

Parent task acceptance requires no env leak on JWKS failure. The BFF will
return the same existing error codes. Frontend must handle these as before:

| HTTP status | `error.code` | When |
|---|---|---|
| 401 | `INVALID_TOKEN` | Missing/malformed token, bad signature, expired, issuer/audience mismatch, JWKS fetch failure |
| 400 | `MFA_REQUIRED` | MFA token absent when required, or invalid format |
| 403 | `INSUFFICIENT_ROLE` | Token valid but role not authorized for the route |

JWKS fetch failures must never surface `PANTHEON_BFF_JWKS_URI` or internal error
details in the 401 response body.

---

## 8. Test Coverage Gap Reference

For the parent task owner, the existing test file
`services/control-plane/bff/test_bff_auth_facade.py` covers:

- HS256 valid/invalid signature, expiry, issuer, audience, blank subject
- MFA present/absent/invalid
- Dev stub compatibility
- Role enforcement (via higher-level integration tests)

**Not yet covered (gaps for parent task tests):**

- RS256/ES256 token → verified against JWKS-provided public key
- `kid` present in header → correct key selected from JWKS
- `kid` absent → fallback or clear rejection
- JWKS endpoint unreachable → generic 401, no URL leak in response
- JWKS returns stale keys (old `kid`) → retry fetch then reject cleanly
- Cached JWKS hit → no outbound HTTP on second request
- HS256 path remains unchanged when `JWKS_URI` is unset (regression guard)

---

## 9. Sidecar Constraints (reminder)

- This file is the only output of this sidecar task.
- No canonical truth files were modified.
- No runtime code was changed.
- Parent task owner (Claude) decides whether and how to absorb this material.

---

*Handoff packet prepared by Claude2 — ready for reviewer Claude.*
