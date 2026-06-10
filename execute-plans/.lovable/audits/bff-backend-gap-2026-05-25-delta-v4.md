# execute-plans BFF Backend Gap Delta V4 - 2026-05-25

Status: task-scoped audit record
Task: BFF-B1-001-DELTA-2

This document records the execute-plans-facing BFF CORS delta for Lovable
preview origins. It is an audit artifact, not a new canonical policy source.

## CORS-DELTA-001: Lovable `id-preview` Preflight

Route:

```text
OPTIONS /bff/me
```

Purpose:

Allow strict frontend preview and hosted Lovable origins to complete browser
CORS preflight before auth reaches the BFF.

Frontend origin shapes:

- `https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app`
- `https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com`
- `https://pantheon-dev.lovable.app`
- `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`
- `https://id-preview-<hex>--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app`
- `https://id-preview--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app`

Backend fix:

- `id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app`
  remains in the BFF default CORS allowlist.
- That same static `id-preview` origin is no longer classified as dev-only, so
  production-strict filtering keeps the exact origin.
- The dynamic preview regex now accepts both
  `id-preview-<hex>--<uuid>.lovable.app` and
  `id-preview--<uuid>.lovable.app`.
- Non-hex deploy prefixes remain rejected.

Backend acceptance:

- production-strict exact allowlist:
  `id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app` returns
  preflight 204 with echoed `Access-Control-Allow-Origin`
- production-strict exact allowlist:
  `140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` continues to
  return preflight 204 with echoed `Access-Control-Allow-Origin`
- non-production strict regex path:
  `id-preview-<hex>--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app`
  returns preflight 204
- non-production strict regex path:
  `id-preview--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app` returns
  preflight 204
- non-production strict regex path rejects non-hex deploy prefixes

Validation:

```bash
python3 -m pytest services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 20 passed.

Post-merge release action:

```bash
gh workflow run nonprod-deploy.yml -f environment=dev -f component=auto
```

Deployment env follow-up:

The first post-merge live probe showed that the dev VM was running with a
GitHub Actions `DEV_BFF_CORS_ORIGINS` override containing only:

```text
https://pantheon-dev.lovable.app,https://pantheon-ai-system-front-dev.lovable.app
```

That override replaced the BFF code defaults and excluded both required
`lovableproject.com` origins. The deploy script now appends the mandatory
Lovable dev origins to the provided override before exporting
`PANTHEON_BFF_CORS_ORIGINS` into the compose deployment.

Do not close BFF-B1-001-DELTA-2 until the live dev BFF returns HTTP 204 and an
exact echoed `Access-Control-Allow-Origin` for the four required Lovable origins.

Final live verification:

- PR #568 merged BFF CORS code at `05a3a8febe84e4e7c4b95397400457ca0115c188`.
- PR #569 merged deploy-origin expansion at `88323642913aaf908051b963e43051d1b86cc55e`.
- `nonprod-deploy.yml` run `26383877729` deployed
  `88323642913aaf908051b963e43051d1b86cc55e` to dev and passed public BFF smoke.

Live `OPTIONS /bff/me` results on
`https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`:

| Origin | Status | ACAO |
|---|---:|---|
| `https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app` | 204 | exact origin |
| `https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com` | 204 | exact origin |
| `https://pantheon-dev.lovable.app` | 204 | exact origin |
| `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` | 204 | exact origin |

## ERROR-ENVELOPE-D21-FIELDS-001: Pack D Error Metadata Fields

Route:

```text
all BFF error responses emitted through the Pack D envelope helpers
```

Purpose:

Keep execute-plans strict-mode error rendering from inferring frontend behavior
from `error.code` alone.

Backend fix:

- `error.i18nKey` is now emitted as `errors.<canonical Pack D §D21 code>`.
- `error.retryable` is now emitted as a boolean from the BFF §D21 behavior
  matrix.
- `error.userActionable` is now emitted as a boolean from the same matrix.
- Existing `error.code`, `error.message`, `error.details`, `meta.correlationId`,
  and `X-Correlation-Id` behavior is unchanged.

Backend acceptance:

- 401 `/bff/me` returns `AUTH_REQUIRED`, `errors.AUTH_REQUIRED`, `retryable=false`,
  and `userActionable=true`.
- 404 unknown BFF route returns `RESOURCE_NOT_FOUND`,
  `errors.RESOURCE_NOT_FOUND`, `retryable=false`, and `userActionable=true`.
- 422 validation failures return `VALIDATION_FAILED`,
  `errors.VALIDATION_FAILED`, `retryable=false`, and `userActionable=true`.
- 500 unhandled BFF failures return `INTERNAL_ERROR`, `errors.INTERNAL_ERROR`,
  `retryable=false`, and `userActionable=false`.
- The BFF behavior matrix covers exactly the 26 canonical Pack D §D21 error codes.

Validation:

```bash
python3 -m pytest services/control-plane/bff/test_bff_error_envelope_shape.py \
  services/control-plane/bff/test_final_contract_primitives.py -q
```

Post-merge release action:

```bash
gh workflow run nonprod-deploy.yml -f environment=dev -f component=auto
```

Live verification requirement:

Do not close BFF-INFRA-ENVELOPE-PACKD-FIELDS-001 until the deployed dev BFF
returns all three fields for:

```text
GET /bff/strategies/__nonexistent__
```

Expected Pack D fields:

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "i18nKey": "errors.RESOURCE_NOT_FOUND",
    "retryable": false,
    "userActionable": true
  }
}
```

## PATH-DEDUPE-001: BFF duplicate path family cleanup

Route families:

- personas, capital-pools, deployments, rebalances, incidents, runtimes, skills, tools
- mcp-servers vs mcp/servers
- mcp-tools vs mcp/tools
- ranking-formulas vs ranking/formulas
- strategy actions

Backend fix:

- canonical path parameter declarations stay snake_case (`{persona_id}`, `{pool_id}`,
  `{deployment_id}`, etc.)
- short-form `{id}` duplicate declarations for the scoped route families are removed
- alternate URL shapes return HTTP 410 with `X-Deprecated: true`,
  `X-Deprecated-At: 2026-05-25T08:40:02Z`, and `X-Pantheon-Replacement-Route`
- FE-used forms stay active: `/bff/mcp-servers`, `/bff/mcp-tools`,
  `/bff/ranking-formulas`, and the generic `/bff/actions/{type}/{id}/{action}` path

Validation:

```bash
python3 -m pytest services/control-plane/bff/tests/test_bff_path_dedupe.py -q
```
