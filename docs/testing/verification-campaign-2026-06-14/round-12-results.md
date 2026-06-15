# Round 12 — Results

**Executed:** 2026-06-15 (UTC). **Method:** code audit of
`services/runtime_auth_inbound.py` + `_extract_identity_jwt` (`main.py:1000`) and
an in-process evidence harness against `validate_request_auth`.

## Code audit

`_verify_jwt_hs256` (`runtime_auth_inbound.py:160`):
- **alg pinned** to `HS256` (`alg != "HS256"` → reject); JWKS path restricts to
  `{RS256, ES256}`. Algorithm-confusion / `alg:none` cannot bypass.
- **constant-time** signature check (`hmac.compare_digest`).
- `exp`, `nbf`, `iss`, `aud` all enforced when present/configured.

`validate_request_auth` (`:628`):
- JWT shape + no secret → `AUTH_JWT_UNVERIFIED` 401 in **both** modes — no
  fail-open in permissive mode.
- strict + missing secret → `AUTH_JWT_SECRET_MISSING` 500 (fail-closed).
- non-JWT token in strict → `AUTH_TOKEN_FORMAT` 401; only permissive falls back
  to the structured stub token.

## Evidence (attack matrix, in-process)

| Case | Result |
|---|---|
| valid signed token | ACCEPTED (actor=u1, roles=[operator]) |
| expired | REJECTED `AUTH_JWT_EXPIRED`/401 |
| nbf in future | REJECTED `AUTH_JWT_NOT_YET_VALID`/401 |
| tampered signature | REJECTED `AUTH_JWT_BAD_SIGNATURE`/401 |
| `alg:none` forgery | REJECTED `AUTH_TOKEN_FORMAT`/401 |
| wrong secret | REJECTED `AUTH_JWT_BAD_SIGNATURE`/401 |
| wrong issuer | REJECTED `AUTH_JWT_ISSUER_MISMATCH`/401 |
| wrong audience | REJECTED `AUTH_JWT_AUDIENCE_MISMATCH`/401 |
| missing secret (strict) | REJECTED `AUTH_JWT_SECRET_MISSING`/500 (fail-closed) |
| **no `exp` claim** | **ACCEPTED** (see F10) |

**H1–H4 PASS.** The production auth path is cryptographically sound and
fail-closed against every forgery/tamper tested.

## Finding

### F10 — tokens without an `exp` claim are accepted indefinitely (hardening; not changed)

`_verify_jwt_hs256` only checks expiry `if exp is not None`. A validly-signed
token that omits `exp` never expires. The BFF's own issuer (`_issue_dev_login_jwt`,
`main.py:855`) always sets `exp`, so this does not affect first-party tokens —
but a signed-but-exp-less token (e.g. a long-lived service token minted by
another component) would live forever, and a leaked one could not be aged out.

**Decision: not changed.** Rejecting exp-less tokens is the recommended
hardening, but service-to-service tokens minted elsewhere may legitimately omit
`exp`; flipping verification to require it has uncertain blast radius
(cf. F7's discipline). Recommended for an auth-owner decision: require `exp` in
strict mode (optionally gated by `PANTHEON_RUNTIME_JWT_REQUIRE_EXP`).

## Fix (regression coverage — no behavior change)

Added `services/test_runtime_auth_inbound_attack_matrix.py` (6 passed) covering
the cases the existing `test_runtime_hardening.py` omits: `alg:none`,
issuer-mismatch, audience-mismatch, strict-missing-secret fail-closed, tampered
signature, and the valid baseline. This locks the verified-good rejection
behavior so a future change cannot silently weaken JWT verification.

## Net

The production JWT auth path **PASSES** a full attack-matrix audit with captured
evidence — fail-closed, alg-pinned, constant-time, claim-enforcing. One
hardening item (F10, no-exp acceptance) is documented for the auth owner; the
verified rejections are locked by a new regression test.
