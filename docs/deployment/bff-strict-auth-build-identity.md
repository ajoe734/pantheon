# BFF Strict Auth and Build Identity

## Overview

This document describes the strict dev authentication posture and build identity metadata integration implemented in the operator BFF.

## Strict Auth Posture

To close the loop and secure the development environment, the following changes have been implemented:
1. **Disabled Stub Auth Default**: Default authentication stub mode in the dev environment is turned off.
   - `PANTHEON_BFF_AUTH_STUB` defaults to `false`.
   - `PANTHEON_BFF_AUTH_MODE` defaults to `strict`.
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
- **git SHA**: Exposes the source git commit SHA via `source_commit_sha` and `commit`.
- **image digest**: Exposes the image digest via `image_digest` (read from `BFF_IMAGE_DIGEST` or `IMAGE_DIGEST`).
- **build time**: Exposes the build timestamp via `build_time` (read from `BFF_BUILD_TIME` or `BUILD_TIME`).
- **environment**: Exposes the running environment stage via `environment` (read from `PANTHEON_ENV` or `ENVIRONMENT`).
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
