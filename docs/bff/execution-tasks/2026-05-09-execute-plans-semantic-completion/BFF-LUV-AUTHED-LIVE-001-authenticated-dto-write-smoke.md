# BFF-LUV-AUTHED-LIVE-001 - Authenticated Live DTO and Write Smoke

Priority: P0

Area: execute-plans live BFF cutover completion

## Problem

The lupin dev public BFF has route registration coverage (`/openapi.json` 200 and contract routes returning 401 instead of 404), but the authenticated live DTO and write-flow smoke was not completed.

This must not be treated as complete frontend cutover evidence.

## Current Evidence

Target:

- `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

Observed on 2026-05-09:

- Anonymous/stub route smoke verifies registration only: protected routes return `401`.
- Stub operator token is rejected by public strict auth:
  - Command: `curl -sk -H 'Authorization: Bearer op-live-smoke:operator,admin,reviewer:mfa' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/me`
  - Result: `401 INVALID_TOKEN`, reason `AUTH_TOKEN_FORMAT`, message `Strict auth mode requires a JWT bearer token`.
- Local environment does not expose `PANTHEON_BFF_JWT_SECRET`, `PANTHEON_BFF_JWKS_URI`, OIDC credentials, or a reusable operator Bearer token.
- GCP CLI is configured for project `pantheon-lupin-20260502`, but token refresh currently fails non-interactively with `Reauthentication failed. cannot prompt during non-interactive execution.`

## Required Work

- Locate the approved lupin dev operator auth path:
  - valid operator Bearer token, or
  - documented IdP/OIDC flow for obtaining one, or
  - approved temporary dev auth-stub deployment window for smoke only.
- Run authenticated live read DTO smoke against representative execute-plans contract families:
  - session bootstrap: `GET /bff/me`
  - strategy/persona/capital read models
  - governance approvals/interventions
  - alerts/incidents/audit/artifacts/runtimes
  - MCP/tools/skills/channels
  - Agora core and v5 loop/sentinel surfaces
- Run write-flow smoke only on approved non-capital-side-effect surfaces or dedicated smoke fixtures:
  - confirm-token create/read/redeem/delete, or
  - command submission with dry-run/smoke target, or
  - alert acknowledge / approval decide only if a reviewed smoke fixture exists.
- Publish evidence under `docs/bff/evidence/` with redacted token handling.
- Update the execute-plans handoff to say whether `VITE_BFF_MODE=live` and `VITE_BFF_REAL_WRITES=true` are allowed.

## Acceptance Criteria

- Authenticated live DTO smoke returns `2xx` and validates minimal DTO shape for each selected route family.
- Write-flow smoke returns the expected governed command/receipt envelope without live-capital side effects.
- Evidence records exact target URL, timestamp, route list, status codes, DTO shape checks, and redacted auth source.
- If auth cannot be obtained, the task remains blocked with exact owner/action needed; do not close as done.
