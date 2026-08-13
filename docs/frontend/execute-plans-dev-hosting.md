# Execute-Plans Dev Frontend Hosting

Last updated: 2026-07-19

This is the canonical frontend hosting rule for Pantheon dev.

This document supersedes the dev-hosting portions of these legacy Lovable
runbooks:

- `docs/deployment/lovable-dev-staging-operating-rules.md`
- `docs/deployment/frontend-lovable-environments.md`
- `docs/deployment/bff-https-ingress.md`
- `docs/deployment/nonprod-development-workflow.md`

Those files may still be useful as staging-live or historical Lovable context,
but they are not the dev frontend hosting source of truth.

## Source Repository

- Active frontend repo: `ajoe734/execute-plans`
- Local checkout: `/home/lupin/code/execute-plans`
- Preferred work location for risky edits: a clean task worktree outside the
  dirty checkout, for example `/tmp/execute-plans-<task>`
- Delivery base as of 2026-07-13: `dev` (also the GitHub default branch)
- `main` remains a divergent historical branch and is not implicit dev
  delivery evidence.

Do not use `front-ai-trading-system` for new work. That repository name is
legacy-only. Do not recreate it, mirror new handoffs to it, or assign frontend
tasks to it.

## Dev Hosting Rule

Do not use Lovable as the Pantheon dev frontend host.

Lovable URLs can remain historical evidence or external reference points, but
they are not the dev deployment target and must not block Pantheon dev
acceptance. A stale Lovable bundle is not proof that the current
`execute-plans` commit failed.

Do not ask the operator to press Lovable publish for Pantheon dev delivery, and
do not block on Lovable connector authorization. Current dev deployment flows
through GitHub PRs, an `execute-plans` build, and Pantheon-owned HTTPS hosting.

The dev frontend should be served by Pantheon-owned infrastructure from the
recorded `execute-plans` commit. The intended host is:

- FE: `https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io`

The prior project `pantheon-benjamin-20260528` and IP `35.201.239.38` are
retired from active dev routing because the project is suspended. The
replacement VM is `pantheon-lupin-dev` in project
`pantheon-lupin-dev-20260719`; its backend checkout is `/home/lupin/pantheon`.

If the FE hostname or VM IP changes, update this document and `AGENTS.md`
before routing work to the new target.

## Historical Verified Dev Deployment

Verified on 2026-06-11:

- Backend/BFF repo: `ajoe734/pantheon`
- Backend/BFF branch: `dev`
- Backend/BFF merge commit:
  `0d9fe5864a9b39b1775dcc94da91a54357cdeb9d`
- Backend/BFF deploy evidence:
  GitHub Actions run `27357842338`, `Pantheon Nonprod Deploy`, deployed
  `0d9fe5864a9b39b1775dcc94da91a54357cdeb9d`; the `Nonprod deploy` job
  completed in `10m17s` with `Deploy requested VM stack` and `Public BFF
  smoke` successful.
- Frontend repo: `ajoe734/execute-plans`
- Frontend branch: `dev`
- Frontend merge commit:
  `721bc3c4fe22648c242c6e39c353939575a33637`
- Frontend dev VM document root: `/var/www/pantheon-dev-fe/`
- Frontend deployment manifest:
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
  reports `app=execute-plans`, `sourceBranch=dev`,
  `sourceRef=721bc3c4fe22648c242c6e39c353939575a33637`,
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and
  `VITE_BFF_REAL_WRITES=false`.

If an agent sees a different Lovable bundle, that is not the Pantheon dev FE.
Validate the Pantheon-owned host and the GitHub commits above before changing
code.

## Current Observed Deployment Warning

At 2026-07-13 13:35 UTC the Pantheon-owned frontend host served
`sourceBranch=dev` at commit
`12b78ef210e535cd4a3d80358f78b44c9396e588`, matching the then-current remote
`dev` head. Its manifest still reported `VITE_BFF_REAL_WRITES=true` and
`VITE_BFF_ALLOW_DEV_STUB_WRITES=true`, while BFF `/health` reported only
`version=0.2.0` and no git SHA or image identity. The release-workflow audit
also found that the live symlink could change before probes and a failed probe
had no automatic restoration of the prior release. The observed deployment is
therefore current but not an accepted safe product baseline.

Before the next product closeout, the release workflow must gate the exact
candidate SHA before deployment, probe the candidate before switching, reject
out-of-order deployments, switch atomically, and automatically restore and
re-probe the prior SHA after a post-switch failure. The hosted manifest must
show safe write defaults and exact FE and BFF build identities.

## Required Frontend Build Env

Build the dev frontend with live BFF wiring:

```sh
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io
VITE_BFF_FALLBACK=strict
```

Keep write behavior safe by default:

```sh
VITE_BFF_REAL_WRITES=false
VITE_BFF_ALLOW_DEV_STUB_WRITES=false
```

Only set either write flag to true when the operator explicitly asks for the
specific write-path test and the corresponding BFF governance gates are ready.
The release evidence must record that override and its expiry.

Do not compile a bearer token, client secret, or all-role development identity
into the browser bundle. `/bff/auth/dev-login` is a server-side CI credential
exchange only: its client secret belongs in the workflow/secret store and must
never be called from browser code.

## Strict browser session contract

The product login uses GCP Identity Platform. After Identity Platform
authenticates the human, `execute-plans` registers the current short-lived ID
token with the BFF request header provider and sends it as
`Authorization: Bearer <id-token>`. It must not copy that token into a
build variable, a deployment manifest, `localStorage`, or the static bundle.
`credentials: include` remains enabled for the optional HttpOnly cookie path,
but it is not a substitute for registering the current Identity Platform
bearer.

The dev BFF validates that JWT directly. Configure all of the following on the
server:

```sh
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_JWKS_URI=<idp-jwks-uri>
# Or set PANTHEON_BFF_OIDC_DISCOVERY_URL instead of a direct JWKS URI.
PANTHEON_BFF_OIDC_ISSUER=<exact-token-issuer>
PANTHEON_BFF_OIDC_AUDIENCE=<exact-bff-audience>
PANTHEON_BFF_ROLE_CLAIMS=roles,role
PANTHEON_BFF_ROLE_MAP_MODE=strict
PANTHEON_BFF_ROLE_MAP=<external-operator=operator;external-viewer=viewer;...>
PANTHEON_BFF_DEFAULT_ROLE=viewer
```

The current Pantheon dev GCP Identity verifier uses public, non-secret
metadata:

```sh
PANTHEON_BFF_JWKS_URI=https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com
PANTHEON_BFF_OIDC_ISSUER=https://securetoken.google.com/pantheon-lupin-dev-20260719
PANTHEON_BFF_OIDC_AUDIENCE=pantheon-lupin-dev-20260719
PANTHEON_BFF_MFA_CLAIMS=amr,acr,mfa,mfa_verified,firebase.sign_in_second_factor
PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED=true
```

An authenticated GCP user without an allowlisted custom `roles` claim resolves
to the fail-closed `viewer` default and cannot mutate Persona interactions.

The IdP must assign the Pantheon role in a signed server-owned claim (for
example `roles=["pantheon-operator"]`). Browser profile fields are not an
authorization source. Tenant and allowed-tenant claims are likewise signed IdP
claims or server-owned BFF configuration. `/bff/me` is the frontend's authority
for effective user, roles, tenant, capabilities and session kind.

The operator-live readiness sequence is:

1. GCP Identity login/refresh produces a short-lived ID token with verified
   email and TOTP second-factor claims.
2. The frontend registers that in-memory JWT with the shared BFF auth provider.
3. `GET /bff/me` must report `authenticated=true`, `session_kind=bearer` (or
   `cookie`), an operator-level role, the exact tenant and Agora capability.
4. `GET /bff/auth/readiness` must report `authReady=true` and expose safe
   provider readiness for the exact `sourceCommitSha`; it never returns issuer,
   audience, key, endpoint, credential or token values.
5. Persona mutations carry the same bearer and `Idempotency-Key`. Viewer
   sessions remain readable but receive direct `403` on mutations; missing
   sessions receive `401`; stub sessions cannot satisfy readiness.

On token refresh, update the in-memory BFF provider before retrying a request,
then call `/bff/auth/refresh` with the refreshed bearer if session lifecycle
readback is needed. On sign-out, call `/bff/logout` with the current bearer and
then clear the GCP Identity session and the in-memory BFF provider. A cookie-only
mutation must include an allowed `Origin`; the BFF rejects a missing or
unlisted origin before route execution.

Hosted CI may obtain short-lived operator/viewer/reviewer credentials through
`/bff/auth/dev-login`, but only from an authorized server-side workflow step.
Each identity remains bound to its own client id/secret, role, subject and
tenant. The workflow may pass the returned short-lived bearer to Playwright at
runtime; neither the credential pair nor token may enter the frontend artifact.

## Required BFF CORS Env

Before browser smoke tests, the running dev BFF must allow the Pantheon-owned FE
origin and use the same tenant scope as the FE dev gate:

```sh
PANTHEON_BFF_CORS_ORIGINS=...,https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io
PANTHEON_BFF_TENANT_ID=tenant-dev
PANTHEON_BFF_ALLOWED_TENANTS=tenant-dev,pantheon-dev
```

Do not rely on Lovable origins to validate the Pantheon dev FE. A local or
Pantheon-owned FE origin missing from `PANTHEON_BFF_CORS_ORIGINS` will fail in
the browser even when curl to the BFF succeeds. A BFF tenant scope that excludes
`tenant-dev` will make `/bff/me` fail while older read probes may appear healthy;
that is not valid dev frontend proof.

## Agora Compatibility Gate

Agora dev deployment is gated by the generated cross-repo manifest:

```text
docs/contracts/agora/dev-compatibility-manifest.json
```

For repo sanity checks, the Pantheon side may verify a pending manifest while
the execute-plans generated type mirror is still catching up:

```sh
python3 scripts/agora_compat_manifest.py verify \
  --allow-pending \
  --manifest docs/contracts/agora/dev-compatibility-manifest.json
```

For an actual dev deployment, pending status is not enough. The deployment gate
must pass against the immutable backend commit and, when available, the matching
execute-plans manifest from the frontend repo:

```sh
python3 scripts/agora_compat_manifest.py deployment-gate \
  --manifest docs/contracts/agora/dev-compatibility-manifest.json \
  --frontend-manifest /home/lupin/code/execute-plans/docs/contracts/agora/dev-compatibility-manifest.json \
  --backend-runtime-commit <pantheon-backend-commit>
```

The gate fails closed when either repo has placeholder commits, mismatched
bundle/OpenAPI hashes, stale generated types, missing required Agora
capabilities, or `compatibility_status != compatible`.

## Deployment Shape

The dev deploy should:

1. Build `execute-plans` from the recorded commit.
2. Run the Agora compatibility deployment gate for Agora frontend/BFF changes.
3. Serve the build from the dev VM through Caddy or equivalent Pantheon-owned
   HTTPS routing.
4. Point the build at the dev BFF URL.
5. Add the FE origin to dev BFF CORS.
6. Restart only the services needed for the FE/CORS change.
7. Run browser smoke against the Pantheon-owned FE URL.

## Acceptance Smoke

Minimum smoke evidence:

- `GET /` on the FE host returns the new build.
- The loaded JS bundle contains the intended dev BFF URL and does not contain
  obsolete BFF URLs.
- `/bff/me` from the browser returns the expected authenticated or governed
  auth response.
- Management AI routes can read BFF provider/control-mode status.
- SSE opens from the browser origin or has an explicit tested fallback.
- If write paths are tested, the result is either a governed success or a
  documented fail-closed response.

Management AI/OpenClaw dev work has an additional readiness gate. Provider
readiness is not enough: `/bff/assistant/mode` must report
`kernel_enabled: true`, and control mode must be activatable by an authorized
operator/admin session before claiming that Management AI can read/write VM
files or coordinate debugging through OpenClaw. See
`docs/operations/management-ai-openclaw-dev-bridge.md`.

Do not use `/bff/assistant/tools/*` as the VM file access proof. That route
family is for governed Pantheon action preview/validation/execute contracts.
OpenClaw VM inspection goes through Management AI conversation routes such as
`POST /bff/management/nl/ask`; write-capable repair also requires valid
`openclaw.repair` metadata and a clean repair task worktree under the configured
repair root.

Operator POST routes in the final BFF contract require a stable
`Idempotency-Key` header. `X-Idempotency-Key` is accepted only as a temporary
compatibility alias. Do not place idempotency keys in the JSON body.

## CI Rule

Pull-request CI may run Playwright against a local PR frontend so stale external
hosting cannot block a valid branch. Post-merge and dev deployment smoke should
target the Pantheon-owned FE host above, not Lovable.

Any workflow or script that still defaults to `https://pantheon-dev.lovable.app`
for dev acceptance should be treated as legacy until it is updated to accept the
Pantheon-owned FE URL.

## Retired Legacy Automation

The repository no longer ships the Lovable/`front-ai-trading-system`
coordination publisher, mirror, drift guard, bootstrap helper, receiver, or
manual-replay workflows. Current frontend work must use `execute-plans`, the
assistant dev bridge, and governed repair worktrees described above.

Historical specifications and delivery evidence can still mention
`front-ai-trading-system`, `lovable-ui-task`, or Lovable publish flows. Those
references are archival evidence only; they are not executable development
instructions and must not be used to recreate the retired automation.
