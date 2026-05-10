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

## Completion Result - 2026-05-10

Evidence:

- `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-live-smoke-20260510T024935Z.json`

Result:

- Target: `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`
- Auth source: HS256 JWT minted from `PANTHEON_BFF_SMOKE_JWT_SECRET`; the evidence records only redacted command text and a short secret hash, not the secret or bearer token.
- Health/openapi: 2/2 passed.
- Authenticated read DTO probes: 30/30 passed with `2xx` status across session, strategy/persona/capital, governance, alerts/incidents/audit/artifacts/runtimes, MCP/tools/skills/channels, Agora, and v5 loop/sentinel families.
- Non-capital write-flow probes: 5/5 passed for confirm-token create/read/redeem/delete/read-deleted (`201`, `200`, `202`, `202`, `200`).
- Live-capital side effects: `false`.
- Failed routes: `0`.

Gate outcome:

- `VITE_BFF_MODE=live`: allowed by this authenticated DTO/write smoke.
- `VITE_BFF_REAL_WRITES=true`: this task no longer blocks the gate for reviewed non-capital safe-write surfaces; final frontend enablement still depends on the downstream FE-005/FE-006 cutover/deploy evidence.
