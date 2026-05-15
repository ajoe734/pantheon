# BFF-LUV-AUTHED-LIVE-001 - Blocker Record

**Task ID:** BFF-LUV-AUTHED-LIVE-001
**Status:** BLOCKED
**Date:** 2026-05-09
**Owner:** Gemini

## Summary
The authenticated live DTO and write-flow smoke test against the `lupin dev` BFF is currently blocked. No valid Bearer token or `JWT_SECRET` for this environment was found in the workspace, and GCP CLI re-authentication fails in the current headless execution environment.

## Findings & Attempts
- **Workspace Search:** Exhaustive search in `docs/`, `env/`, `services/`, and root `.env.example` yielded no valid tokens or secrets for the `lupin dev` target (`https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`).
- **GCP CLI:** Attempted `gcloud auth print-identity-token` but received: `Reauthentication failed. cannot prompt during non-interactive execution.`
- **Target Verification:** Confirmed via anonymous probe that the target BFF is running in `strict` auth mode (returns 401 with `AUTH_TOKEN_FORMAT` for non-JWT tokens).

## Resolution Path (Required Action)
To unblock this task and the subsequent `BFF-LUV-FE` frontend cutover sequence, a human operator must perform one of the following:

1. **Provide a valid Bearer token:** Obtain a JWT Bearer token for the `lupin dev` environment and provide it to the auto worker (e.g., via an environment variable or a temporary secure file).
2. **Enable Auth Stub:** Temporarily set `PANTHEON_BFF_AUTH_STUB=true` on the `lupin dev` deployment to allow the smoke test to proceed with structured tokens.
3. **Fix GCP CLI:** Ensure the auto worker environment has a refreshed and valid GCP identity token that can be accessed via `gcloud auth print-identity-token`.

The task will remain `blocked` until the authentication path is resolved.

## 2026-05-10 Resolution

The auth path was resolved for the smoke run by supplying `PANTHEON_BFF_SMOKE_JWT_SECRET` to `scripts/probe_bff_authenticated_live.py`, which minted a short-lived HS256 JWT without writing the secret or bearer token into evidence.

Passing evidence:

- `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-live-smoke-20260510T024935Z.json`

Summary:

- 37/37 probes passed: health/openapi, 30 authenticated read DTO probes, and 5 confirm-token write-flow probes.
- Failed routes: `0`.
- Live-capital side effects: `false`.
