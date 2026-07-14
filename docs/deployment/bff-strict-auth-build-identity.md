# BFF Strict Auth and Build Identity

## Overview

This document describes the strict dev authentication posture and build identity metadata integration implemented in the operator BFF.

## Status (2026-07-14, LOOP-PROD-AUTH-001)

`docker-compose.yml`'s own defaults are strict (`PANTHEON_BFF_AUTH_STUB=false`,
`PANTHEON_BFF_AUTH_MODE=strict`), but the hosted dev BFF still reported
`auth_stub:true, auth_mode:permissive` on `/bff/version` after that change
merged (PR #3593), because `scripts/deploy_nonprod_vm.sh` always passes an
*explicit* `PANTHEON_BFF_AUTH_STUB`/`PANTHEON_BFF_AUTH_MODE` value into the
compose environment on every dev deploy, which overrides the compose file's
default regardless of what it says. The script's own default
(`DEV_BFF_AUTH_STUB`/`DEV_BFF_AUTH_MODE`) was still `true`/`permissive`, so
every dev deploy silently re-forced stub/permissive auth. That default has
now been corrected to `false`/`strict` (see `evidence.json` for the exact
commit and the pre-fix hosted readback that proves the gap). The corrected
default takes effect on the next dev deploy; it has not yet been observed
live as of this writing, and requires a human-triggered/approved nonprod
deploy run to confirm (see `residual_risks` in `evidence.json`).

Two acceptance gaps remain open and are recorded as residual risks rather
than closed:
- **dev-login credential provisioning**: `/bff/auth/dev-login` additionally
  requires `PANTHEON_BFF_DEV_LOGIN_CLIENT_ID`/`_SECRET` (or the OIDC
  fallback vars) and `PANTHEON_BFF_JWT_SECRET` to be present in the runtime
  environment. Neither `docker-compose.yml` nor the deploy script provisions
  dev-login client credentials; they must come from a secret store outside
  this repo. Until they are configured, `/bff/version` reports
  `dev_login_enabled:false` and the distinct viewer/operator/approver/risk
  owner/operator-A/operator-B scoped-identity proof required by this task
  cannot be captured end-to-end against the hosted BFF.
- **image digest**: `/bff/version`'s `image_digest` field has no source in
  this repo (see below) and always reports `unknown`.

## Strict Auth Posture

To close the loop and secure the development environment, the following changes have been implemented:
1. **Disabled Stub Auth Default**: Default authentication stub mode in the dev environment is turned off.
   - `PANTHEON_BFF_AUTH_STUB` defaults to `false`.
   - `PANTHEON_BFF_AUTH_MODE` defaults to `strict`.
   - This default is set in both `docker-compose.yml` and
     `scripts/deploy_nonprod_vm.sh` (`DEV_BFF_AUTH_STUB`/`DEV_BFF_AUTH_MODE`);
     the deploy script's value is what actually reaches the hosted dev
     container, since it always overrides the compose file default.
2. **Short-Lived Identities**: The `/bff/auth/dev-login` route is used to exchange client credentials for short-lived JWTs (typically 15 minutes, maximum 1 hour).
3. **No Default All-Role Bearer**:
   - The default roles granted to a client-credentials token (when no specific roles are requested and no environment overrides are set) has been restricted from all roles (`["operator", "reviewer", "approver"]`) to a single minimal role (`["operator"]`).
   - Clients must explicitly request needed roles (e.g., `["operator", "reviewer", "approver"]`) and tenant configurations in the request payload:
     ```json
     {
       "grant_type": "client_credentials",
       "client_id": "<client_id>",
       "client_secret": "<client_secret>",
       "roles": ["operator", "reviewer"],
       "tenant_id": "tenant-alpha"
     }
     ```

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
```
