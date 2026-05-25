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
