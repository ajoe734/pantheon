# FE-INT-GATE-OIDC-DEV-LOGIN Evidence

Task: Dev BFF OIDC short-lived JWT for CI + hosted Lovable
Owner: Codex
Reviewer: Codex2
Date: 2026-05-15

## Implementation Summary

- Added dev-only `POST /bff/auth/dev-login` to the operator BFF.
- The endpoint accepts JSON client credentials:
  `grant_type=client_credentials`, `client_id`, `client_secret`.
- Accepted credentials come from `PANTHEON_BFF_OIDC_CLIENT_ID` /
  `PANTHEON_BFF_OIDC_CLIENT_SECRET`, with `PANTHEON_BFF_DEV_LOGIN_*`
  override aliases.
- Issued JWTs are HS256, short-lived, and validated by the existing strict BFF
  JWT path. TTL is clamped to 300-3600 seconds; default is 900 seconds.
- The endpoint is disabled when `PANTHEON_ENV` or
  `PANTHEON_DEPLOYMENT_STAGE` is `canary`, `live`, `prod`, `production`, or
  `staging-live`.
- CI workflow now acquires a JWT before authenticated smoke/browser/e2e and
  exports it as `PANTHEON_BFF_ACCESS_TOKEN`, legacy-compatible
  `PANTHEON_BFF_SMOKE_BEARER_TOKEN`, and `BFF_AUTH_TOKEN`.
- Hosted Lovable dev env docs now use `VITE_BFF_OIDC_CLIENT_ID` /
  `VITE_BFF_OIDC_CLIENT_SECRET` with runtime fetch instead of a long-lived
  colon-format bearer token.

## Verification

```bash
python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
```

Result: `16 passed, 1 warning`

```bash
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py services/control-plane/bff/test_bff_oidc_staging_env_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: `77 passed`

```bash
python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable -q
```

Result: `18 passed, 2 warnings`

```bash
node --check docs/05/pantheon_integration_test_package_2026-05-10/scripts/probe-bff-authenticated-live.mjs
node --check docs/05/pantheon_integration_test_package_2026-05-10/scripts/probe-bff-routes.mjs
node --check docs/05/pantheon_integration_test_package_2026-05-10/scripts/probe-hosted-browser-bff.mjs
node --check execute-plans/scripts/probe-bff-authenticated-live.mjs
node --check execute-plans/scripts/probe-bff-routes.mjs
node --check execute-plans/scripts/probe-hosted-browser-bff.mjs
```

Result: all commands exited 0.

```bash
npx --no-install esbuild execute-plans/src/lib/bff/runAction.ts --bundle --format=esm --platform=browser --outfile=/tmp/pantheon-runAction-check.js
```

Result: exited 0.

Workflow YAML parse check:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml
for path in [
    Path('docs/05/pantheon_integration_test_package_2026-05-10/.github/workflows/pantheon-integration-gate.yml'),
    Path('execute-plans/.github/workflows/pantheon-integration-gate.yml'),
]:
    yaml.safe_load(path.read_text(encoding='utf-8'))
PY
```

Result: both workflow files parsed.

## Owner Closeout Verification

Run at: 2026-05-15T08:00:55Z

```bash
python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
```

Result: `16 passed, 1 warning`

```bash
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py services/control-plane/bff/test_bff_oidc_staging_env_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: `77 passed`

```bash
python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable -q
```

Result: `2 passed, 1 warning`

```bash
node --check execute-plans/scripts/probe-bff-authenticated-live.mjs
node --check execute-plans/scripts/probe-bff-routes.mjs
node --check execute-plans/scripts/probe-hosted-browser-bff.mjs
node --check docs/05/pantheon_integration_test_package_2026-05-10/scripts/probe-bff-authenticated-live.mjs
node --check docs/05/pantheon_integration_test_package_2026-05-10/scripts/probe-bff-routes.mjs
node --check docs/05/pantheon_integration_test_package_2026-05-10/scripts/probe-hosted-browser-bff.mjs
```

Result: all commands exited 0.

```bash
npx --no-install esbuild execute-plans/src/lib/bff/runAction.ts --bundle --format=esm --platform=browser --outfile=/tmp/pantheon-runAction-check.js
```

Result: exited 0.

```bash
python3 -c "from pathlib import Path; import yaml; paths=[Path('docs/05/pantheon_integration_test_package_2026-05-10/.github/workflows/pantheon-integration-gate.yml'), Path('execute-plans/.github/workflows/pantheon-integration-gate.yml')]; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in paths]; print('parsed', len(paths), 'workflow files')"
```

Result: `parsed 2 workflow files`

## Deploy-Time Checks Remaining

- Add GitHub repo secrets `PANTHEON_BFF_OIDC_CLIENT_ID` and
  `PANTHEON_BFF_OIDC_CLIENT_SECRET`.
- Configure the dev BFF with matching client credentials and
  `PANTHEON_BFF_JWT_SECRET` / issuer / audience.
- Rebuild and republish the Lovable dev project with
  `VITE_BFF_OIDC_CLIENT_ID`, `VITE_BFF_OIDC_CLIENT_SECRET`, and
  `VITE_BFF_DEV_LOGIN_PATH=/bff/auth/dev-login`.
- Rerun the GitHub integration gate and hosted browser probe against the
  deployed dev BFF.
- Verify a dev-issued JWT is rejected by staging-live.
