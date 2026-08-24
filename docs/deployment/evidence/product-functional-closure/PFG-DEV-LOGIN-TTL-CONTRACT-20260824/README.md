# Task Evidence: PFG-DEV-LOGIN-TTL-CONTRACT-20260824

## Scope
Align dev BFF login-token TTL with bounded proof window.

- Set default dev-login issuer TTL to 1200 seconds (20 minutes) to support functional closure browser journeys.
- Keep minimum (300s) and maximum (3600s) bounds intact.
- Align `docker-compose.yml` operator-bff environment to `${PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS:-1200}`.
- Added comprehensive unit and compose contract tests in `test_bff_session_auth_me_contract.py`, `test_bff_oidc_staging_env_contract.py`, and `test_product_functional_compose_contract.py`.
- Verified live capital remains disabled and source ingestion remains reconcile-only.
