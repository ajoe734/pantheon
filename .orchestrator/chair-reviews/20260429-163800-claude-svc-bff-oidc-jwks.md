# Review: SVC-BFF-OIDC-JWKS-AUTH-FACADE

**Reviewer:** Claude  
**Date:** 2026-04-29  
**Task:** Add optional OIDC/JWKS validation to BFF auth facade

## Verification

```
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
52 passed in 3.62s
```

## Artifacts Reviewed

- `services/control-plane/bff/main.py` — `_extract_identity_jwt` OIDC env mapping and opaque-code sanitization
- `services/control-plane/bff/test_bff_auth_facade.py` — full JWKS test class
- `services/runtime_auth_inbound.py` — JWKS fetch/cache/verify implementation

## Acceptance Criteria Check

| Criterion | Status | Notes |
|---|---|---|
| HS256 strict mode remains compatible | ✅ | Unchanged path when `PANTHEON_BFF_JWKS_URI` is absent |
| JWKS issuer/audience/expiry/subject/kid covered by tests | ✅ | `TestExtractIdentityJwks` covers all cases |
| JWKS fetch/cache failures return generic 401 without env leaks | ✅ | Opaque-code set in `_extract_identity_jwt`; tests verify URI and code not in response |
| MFA and role enforcement remain unchanged | ✅ | JWKS path goes through same `validate_request_auth`; MFA/role logic untouched |
| BFF focused tests pass | ✅ | 52 passed |

## Implementation Notes

- JWKS opt-in is clean: only activated when `PANTHEON_BFF_JWKS_URI` is non-empty.
- RS256 and ES256 supported; HS256 (symmetric) explicitly rejected in JWKS mode.
- `_JWKS_ALLOWED_ALGS` whitelist prevents algorithm confusion.
- Cache is thread-safe (`threading.Lock`); TTL is 5 min.
- `_public_key_from_jwk` validates `alg` and `use` fields before key construction.
- ES256 signature is correctly converted from raw r||s (64 bytes) to DER before `verify`.
- Stub token (`actor:role` colon format) correctly rejected in JWKS mode — test `test_stub_token_rejected_in_jwks_mode` covers this.
- OIDC issuer/audience fall back to generic JWT variants when OIDC-specific vars are absent.
- No env variable names or JWKS URIs appear in error responses.

## Decision

**APPROVED** — implementation is correct, secure, additive, and fully covered by tests.
