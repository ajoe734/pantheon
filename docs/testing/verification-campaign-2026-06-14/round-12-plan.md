# Round 12 — Real JWT/MFA auth path (not stub)

**Date:** 2026-06-15
**Depth/breadth step:** Rounds 1–11 authenticated with the **dev stub**
(`op-dev:admin:mfa`, permissive mode). Round 12 audits the **production**
auth path — `_extract_identity_jwt` → `runtime_auth_inbound.validate_request_auth`
— the code that runs in strict mode, including cryptographic verification and
MFA enforcement.

## Why this round (not a duplicate)

The campaign never exercised the JWT path; the existing
`test_runtime_hardening.py` covers some cases but not algorithm-confusion,
issuer/audience enforcement, or strict-mode fail-closed on a missing secret.

## Hypotheses (attack matrix)

- H1: a valid signed JWT (correct secret/iss/aud, unexpired) is accepted.
- H2: every forgery/tamper is rejected — expired, nbf-future, tampered
  signature, wrong secret, `alg:none`, wrong issuer, wrong audience.
- H3 (fail-closed): strict mode with no configured secret refuses the request
  rather than accepting unverifiable tokens; permissive mode does **not**
  fail-open for JWTs.
- H4: MFA pattern is enforced when required.

## Method

1. Read `_extract_identity_jwt` and `validate_request_auth` /
   `_verify_jwt_hs256`; inspect alg pinning, signature comparison, claim checks.
2. In-process evidence harness: mint HS256 tokens and assert accept/reject for
   each attack-matrix case.
3. Add a regression test for the cases the existing suite misses.

## Pass criteria

- H1–H4 hold with captured evidence; any genuine weakness is fixed via the dev
  workflow, any hardening item with uncertain blast radius is documented.
