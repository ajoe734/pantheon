# BFF Strict Auth and Build Identity

## Overview

This document describes the strict dev authentication posture and build identity metadata integration implemented in the operator BFF.

## Status (2026-07-14, LOOP-PROD-AUTH-001, round 2)

Round 1 (PR #3593, merged `4ce52b850`) fixed `docker-compose.yml`'s own
`PANTHEON_BFF_AUTH_STUB`/`PANTHEON_BFF_AUTH_MODE` defaults and the deploy
script's `DEV_BFF_AUTH_STUB`/`DEV_BFF_AUTH_MODE` defaults. Independent review
(PR #3617) found the fix was directionally correct but not yet safe or
sufficient, and requested four changes, all implemented in this round:

1. **Fail-closed on invalid auth mode values.** `_bff_auth_mode()` (BFF) and
   `validate_request_auth()` (`services/runtime_auth_inbound.py`, shared by
   BFF and runtime-manager) previously treated *any* value other than the
   exact string `"strict"` as non-strict, so a typo'd or malformed
   `PANTHEON_BFF_AUTH_MODE`/`PANTHEON_RUNTIME_AUTH_MODE` (e.g. `"strcit"`)
   silently fell through to permissive/stub behavior. Both now validate
   against an explicit `{"strict", "permissive"}` allowlist and fail closed
   to `strict` for anything else.
2. **Governed dev-login identities, no self-elevation.** `/bff/auth/dev-login`
   previously accepted one shared client-credentials pair, derived one fixed
   subject, and trusted whatever `roles`/`tenant_id`/`allowed_tenants` the
   caller put in the request body — a client could request `roles: ["admin"]`
   or an unconfigured tenant and receive a valid token for it. The endpoint
   now binds each `client_id`/`client_secret` pair server-side to exactly one
   named identity (`viewer`, `operator`, `approver`, `risk_owner`,
   `operator_a`, `operator_b`), each with its own fixed subject, role set,
   and tenant scope sourced from `PANTHEON_BFF_DEV_LOGIN_<IDENTITY>_CLIENT_ID`
   / `_CLIENT_SECRET` / `_TENANT_ID` / `_ALLOWED_TENANTS` / `_MFA_VERIFIED`.
   `operator_a` and `operator_b` share a role but never a subject, closing the
   two-person-proof same-actor gap. Any caller-supplied `roles`/`tenant_id`/
   `allowed_tenants` outside the matched identity's bound values is rejected
   with `403 AUTH_DEV_LOGIN_ESCALATION_DENIED`. Only `operator` falls back to
   the legacy shared `PANTHEON_BFF_DEV_LOGIN_CLIENT_ID`/`PANTHEON_BFF_OIDC_CLIENT_ID`
   credential for backward compatibility; the other five identities require
   their own dedicated credential and are otherwise unavailable (no shared
   fallback).
3. **Credential/readiness gate before strict cutover.** `scripts/deploy_nonprod_vm.sh`
   now refuses to deploy dev (`error`, non-zero exit, before any SSH/build
   step) when `DEV_BFF_AUTH_MODE=strict` and `DEV_BFF_AUTH_STUB!=true` but
   `DEV_BFF_JWT_SECRET`/`DEV_BFF_OIDC_CLIENT_ID`/`DEV_BFF_OIDC_CLIENT_SECRET`
   are not all configured. `.github/workflows/nonprod-deploy.yml` sources
   these from `secrets.DEV_BFF_JWT_SECRET`/`secrets.DEV_BFF_OIDC_CLIENT_ID`/
   `secrets.DEV_BFF_OIDC_CLIENT_SECRET` — **a human must provision these three
   repository/environment secrets in GitHub before the next dev deploy will
   succeed**. Strict dev deployment also requires dedicated client secrets
   for `viewer`, `approver`, `risk_owner`, `operator_a`, and `operator_b`;
   their non-secret client IDs have stable dev defaults. This prevents a
   deployment that is nominally strict but cannot produce distinct governed
   subjects for approval, two-person signing, apply, or containment evidence.
   Post-deploy,
   `assert_bff_auth_gate` (new function, called after `assert_bff_source_sha`
   in both the `root` and `bff` deploy paths) asserts: (a) `/bff/version`
   reports `auth_stub:false, auth_mode:strict`; (b) an authenticated
   `/bff/auth/dev-login` → `/bff/me` round trip succeeds; (c) a fixed/arbitrary
   bearer (`Bearer op-fixed:operator:mfa`) is rejected by `/bff/me`, not
   accepted. The gate is skipped only when `DEV_BFF_AUTH_MODE=permissive` or
   `DEV_BFF_AUTH_STUB=true` is explicitly requested.
4. **Evidence/acceptance truthfulness**, see `evidence.json` directly.

The corrected defaults and gates take effect on the next dev deploy; none of
the hosted/live claims below have been re-observed against the running dev
BFF as of this writing (see `residual_risks` in `evidence.json`).

Two acceptance gaps were recorded by the original cutover. Credential
provisioning is now an enforced deployment input rather than an optional
follow-up:
- **dev-login credential provisioning**: add `DEV_BFF_JWT_SECRET`,
  `DEV_BFF_OIDC_CLIENT_ID`, and `DEV_BFF_OIDC_CLIENT_SECRET`, plus
  `DEV_BFF_DEV_LOGIN_{VIEWER,APPROVER,RISK_OWNER,OPERATOR_A,OPERATOR_B}_CLIENT_SECRET`
  as GitHub secrets. The corresponding dedicated client IDs may use the stable
  workflow defaults or repository variables with matching names. The workflow
  and deploy script fail before GCP/SSH activity when any required pair is
  absent; no credential value is rendered by dry-run diagnostics.
- **image digest**: `/bff/version`'s `image_digest` field has no source in
  this repo (see below) and always reports `unknown`.

## Strict Auth Posture

To close the loop and secure the development environment, the following changes have been implemented:
1. **Disabled Stub Auth Default**: Default authentication stub mode in the dev environment is turned off.
   - `PANTHEON_BFF_AUTH_STUB` defaults to `false`.
   - `PANTHEON_BFF_AUTH_MODE` defaults to `strict`; any value outside
     `{"strict", "permissive"}` fails closed to `strict`.
   - This default is set in both `docker-compose.yml` and
     `scripts/deploy_nonprod_vm.sh` (`DEV_BFF_AUTH_STUB`/`DEV_BFF_AUTH_MODE`);
     the deploy script's value is what actually reaches the hosted dev
     container, since it always overrides the compose file default.
2. **Short-Lived, Governed Identities**: The `/bff/auth/dev-login` route
   exchanges a client-credentials pair for a short-lived JWT (typically 15
   minutes, maximum 1 hour). Each pair is bound server-side to exactly one of
   six named identities (`viewer`, `operator`, `approver`, `risk_owner`,
   `operator_a`, `operator_b`), each with its own fixed subject and role.
   Request payload:
     ```json
     {
       "grant_type": "client_credentials",
       "client_id": "<identity-specific client_id>",
       "client_secret": "<identity-specific client_secret>"
     }
     ```
3. **No Self-Elevation**: Callers cannot request roles or tenants beyond what
   the matched identity is bound to. Echoing `roles`/`tenant_id`/
   `allowed_tenants` in the request is only accepted when it exactly matches
   (or is a subset of) the identity's server-bound values; anything wider is
   rejected with `403 AUTH_DEV_LOGIN_ESCALATION_DENIED`.

## Build Identity Metadata

The `/bff/version` endpoint exposes the following build information for diagnostics, monitoring, and audit verification without leaking sensitive information:
- **git SHA**: Exposes the source git commit SHA via `source_commit_sha` and `commit`. Confirmed live.
- **image digest**: Exposes the image digest via `image_digest` (read from `BFF_IMAGE_DIGEST` or `IMAGE_DIGEST`). No build or deploy step in this repo sets either variable, so this field always reports `unknown`. The dev deploy builds images locally on the VM via `docker compose up -d --build` and never pushes to a registry, so there is no OCI registry digest to report; populating this field would require a follow-up that captures the local image ID (`docker inspect --format='{{.Id}}'`) after build and re-injects it as `BFF_IMAGE_DIGEST` on container start — out of scope for this change (see `residual_risks.RISK-LOOP-PROD-AUTH-001-IMAGE-DIGEST` in `evidence.json`).
- **build time**: Exposes the build timestamp via `build_time` (read from `BFF_BUILD_TIME` or `BUILD_TIME`). Now wired: `services/control-plane/bff/Dockerfile` accepts a `BUILD_TIME` build arg and bakes it as an image `ENV`/OCI label, `docker-compose.yml`'s `operator-bff` service passes it through, and `scripts/deploy_nonprod_vm.sh` sets it to the deploy's UTC timestamp before building. Not yet observed live; takes effect on the next dev deploy.
- **environment**: Exposes the running environment stage via `environment` (read from `PANTHEON_ENV` or `ENVIRONMENT`). Confirmed live (`dev`).
- **config posture**: Exposes a dictionary of non-sensitive configuration states via `config_posture`:
  - `auth_stub`: boolean indicating if stub auth is enabled.
  - `auth_mode`: string indicating the current auth mode (`strict` / `permissive`).
  - `dev_login_enabled`: boolean indicating if the dev login endpoint is active.
  - `mfa_required`: boolean indicating if MFA is enforced.
  - `assistant_kernel_enabled`: boolean indicating if the assistant kernel is active.

## Verification

Run focused auth and staging environment contract coverage:
```bash
PYTHONPATH=services/control-plane/bff python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
PYTHONPATH=services/control-plane/bff python3 -m pytest services/control-plane/bff/test_bff_oidc_staging_env_contract.py -q
PYTHONPATH=services/control-plane/bff python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
PYTHONPATH=services/control-plane/bff python3 -m pytest services/control-plane/bff/tests/test_bff_rebalance_proposals.py -q
python3 -m pytest services/test_runtime_auth_inbound_attack_matrix.py -q
python3 -m pytest scripts/test_deploy_nonprod_bff_strict_auth_default_contract.py -q
```
