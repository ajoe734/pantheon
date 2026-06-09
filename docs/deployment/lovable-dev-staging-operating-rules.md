# Lovable Dev/Staging Operating Rules

Status date: 2026-06-08

## Current Dev Override

This file is superseded for Pantheon dev frontend hosting.

For current dev frontend work, use
`docs/frontend/execute-plans-dev-hosting.md`. The active frontend repository is
`ajoe734/execute-plans`, and the Pantheon-owned dev FE host is:

```text
https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
```

Do not use Lovable publish state, `https://pantheon-dev.lovable.app`, or
`front-ai-trading-system` as the dev frontend host or acceptance source.

This document remains as historical/staging-live Lovable context only.

## Purpose

This is the legacy rulebook for Lovable-hosted Pantheon non-prod environments.
It is not authoritative for current Pantheon dev frontend hosting.

The remaining goal is to preserve staging-live Lovable context while preventing
agents from routing current dev UI work through Lovable.

## Source Of Truth

Authoritative topology docs:

- `docs/frontend/execute-plans-dev-hosting.md`
- `docs/deployment/nonprod-development-workflow.md`
- `docs/deployment/staging-live-topology.md`
- `docs/deployment/frontend-lovable-environments.md`
- `docs/deployment/bff-https-ingress.md`

Official Lovable behavior this policy relies on:

- GitHub sync is default-branch based.
- Each Lovable project has one linked repository.
- Connecting a Lovable project to GitHub creates a repository for that project.
- Publishing deploys a snapshot; edits are not live until `Publish -> Update`.
- `VITE_` variables are embedded into the frontend build output at build time.

References:

- `https://docs.lovable.dev/integrations/github`
- `https://docs.lovable.dev/features/publish`
- `https://docs.lovable.dev/tips-tricks/external-deployment-hosting`

## Legacy Lovable Model

Use this section only when the operator explicitly asks for Lovable or
staging-live Lovable work. Do not use it for Pantheon dev frontend hosting.

Use two Lovable projects:

| Environment | Lovable project | Lovable frontend URL | Purpose | Publish policy |
| --- | --- | --- | --- | --- |
| legacy dev | `pantheon-ui-dev` | `https://pantheon-dev.lovable.app` | historical/external Lovable reference | operator-approved only |
| staging-live | `pantheon-ui-staging-live` | `https://pantheon-ai-system-front-staging-live.lovable.app` | EP5-002/live broker rehearsal frontend | publish only from a verified promotion |

Do not use one Lovable project as both dev and staging-live.

Do not rely on two Lovable projects sharing one GitHub repo through separate
branches. Treat that as unsupported unless Lovable later documents a stable
per-project branch binding.

## Project Creation

Legacy dev Lovable project:

- Do not use this as the current Pantheon dev frontend host.
- Keep it only as historical evidence or an explicit external reference unless
  the operator asks for Lovable-specific work.
- Use the existing Lovable project only if the operator explicitly replaces the
  current Pantheon-owned dev FE flow for a task.
- Rename/display-name it as `pantheon-ui-dev`.
- Current subdomain: `pantheon-dev`.
- Legacy frontend URL: `https://pantheon-dev.lovable.app`.
- Older legacy frontend URL: `https://pantheon-ai-system-front-dev.lovable.app`.
- Keep it connected to the dev BFF only.

Staging-live project:

- Create it by Remix/copying the dev project after the dev project contains the
  current baseline UI.
- Name/display-name it `pantheon-ui-staging-live`.
- Current subdomain: `pantheon-ai-system-front-staging-live`.
- Current frontend URL:
  `https://pantheon-ai-system-front-staging-live.lovable.app`.
- If connected to GitHub, expect a separate repository for this Lovable project.
- Do not use staging-live for exploratory prompting.

## Environment Variables

Legacy dev Lovable project:

```env
VITE_PANTHEON_ENV=dev
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
VITE_BFF_DEV_LOGIN_PATH=/bff/auth/dev-login
VITE_BFF_OIDC_CLIENT_ID=<dev-client-id>
VITE_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>
VITE_PANTHEON_LIVE_BROKER_ENABLED=false
```

Staging-live Lovable project:

```env
VITE_PANTHEON_ENV=staging-live
VITE_BFF_BASE_URL=https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=true
```

Rules:

- `VITE_` values are public browser build values, not secrets.
- The dev project uses `POST /bff/auth/dev-login` to exchange the dev-only
  client id/secret for a short-lived JWT. The BFF clamps token TTL to 5 minutes
  minimum and 1 hour maximum; the default is 15 minutes.
- `VITE_BFF_OIDC_CLIENT_SECRET` is acceptable only for this dev-only browser
  login path because `VITE_` values are public. Rotate or revoke the dev
  client secret if a build is exposed outside dev. Do not set these variables
  for staging-live.
- Never place broker credentials, TWS credentials, API tokens, or private keys
  in Lovable frontend env vars.
- `VITE_PANTHEON_LIVE_BROKER_ENABLED` is a UI hint only. The BFF/backend owns
  the real enforcement.
- Changing these values requires rebuilding and republishing the Lovable app.
  That rebuild is not Pantheon dev deployment unless the operator explicitly
  asks for Lovable-hosted dev work.

## BFF URL And CORS

Any browser-hosted frontend must call browser-reachable HTTPS BFF endpoints.

Do not use:

```text
http://10.140.0.6:18001
http://10.140.0.4:38001
http://<external-ip>:<port>
```

Current BFF HTTPS URLs:

```text
https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io
```

Current Pantheon-owned dev FE URL:

```text
https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
```

BFF CORS must be one-to-one:

```env
# dev BFF on pantheon-lupin-dev
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_JWT_SECRET=<dev-jwt-signing-secret>
PANTHEON_BFF_JWT_ISSUER=pantheon-dev
PANTHEON_BFF_JWT_AUDIENCE=bff-operators
PANTHEON_BFF_OIDC_CLIENT_ID=<dev-client-id>
PANTHEON_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>
PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS=900

# staging BFF on pantheon-lupin-staging-control
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-staging-live.lovable.app
PANTHEON_BFF_AUTH_STUB=false
```

The dev BFF may temporarily keep legacy Lovable origins during migration, but
they are not the acceptance origin for current dev work.

Do not allow both dev and staging Lovable origins on the same BFF unless the
operator has explicitly approved a temporary migration window.

The `/bff/auth/dev-login` route is disabled when `PANTHEON_ENV` or
`PANTHEON_DEPLOYMENT_STAGE` is `staging-live`, `live`, `prod`, `production`, or
`canary`. A dev JWT must be rejected by staging-live because staging-live uses
its own OIDC/JWKS issuer and audience instead of the dev HS256 signing path.

## Promotion Rules

Promotion means copying a verified dev change into the staging-live Lovable
project. It is not branch auto-sync.

Allowed promotion methods:

- apply the reviewed patch to the staging-live Lovable project
- cherry-pick between the separate GitHub repos if both projects are connected
  to GitHub
- ask Lovable in the staging-live project to copy a specific reviewed change
  from the dev project, with exact file references and acceptance criteria

Every promotion must record:

- dev project URL
- staging-live project URL
- source commit, patch id, or reviewed file list
- dev smoke result
- staging BFF URL
- staging Lovable published URL
- operator approval for staging-live publish
- rollback note

## Human Workflow

Dev:

1. Make changes in `execute-plans` unless the operator explicitly asks for
   Lovable-hosted dev work.
2. Verify the UI builds.
3. Deploy to the Pantheon-owned dev FE host unless Lovable-hosted dev is
   explicitly requested.
4. Smoke test against the dev BFF.
5. Confirm the header shows `DEV`.
6. Confirm live broker command scope is unavailable/rejected.

Promotion:

1. Freeze the reviewed dev change.
2. Apply only that reviewed change to `pantheon-ui-staging-live`.
3. Confirm staging env vars point to the staging HTTPS BFF.
4. Confirm BFF CORS allows only the staging Lovable origin.
5. Publish/update staging-live.
6. Confirm the header shows `STAGING LIVE BROKER`.
7. Record the promotion evidence.

Staging-live:

1. Use staging-live only for controlled rehearsal.
2. Do not use staging-live for speculative Lovable prompts.
3. Do not publish staging-live unless the operator asked for it.
4. Before EP5-002 live order/cancel, complete read-only broker status checks.

## LLM Agent Rules

Before touching Lovable, frontend env, BFF URL, CORS, or publish behavior:

1. Read `docs/frontend/execute-plans-dev-hosting.md`.
2. Read this file only if the work explicitly involves Lovable or staging-live.
3. Read `docs/deployment/nonprod-development-workflow.md`.
4. State whether the requested work affects dev, staging-live, or both.
5. Do not claim Lovable supports same-repo different-branch project binding.
6. Do not create a staging-live prompt that says "sync latest dev draft".
7. Do not publish or update staging-live unless the user explicitly asks.
8. Do not put secrets in frontend env.
9. Do not point Lovable at internal IPs or non-HTTPS BFF URLs.
10. If the BFF HTTPS endpoint does not exist yet, say staging Lovable cannot be
   fully wired.

## Staging-Live Publish Gate

All must be true before staging-live publish/update:

- staging BFF has a browser-reachable HTTPS URL
- staging Lovable env uses `VITE_PANTHEON_ENV=staging-live`
- staging Lovable env uses
  `VITE_BFF_BASE_URL=https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io`
- staging BFF CORS allows only the staging Lovable origin
- operator auth works
- BFF health is green
- VM1 can reach VM2 runtime-manager
- live broker scope is intentionally enabled on staging BFF
- dev BFF still rejects live broker scope
- rollback/unpublish path is known

## Rollback

Frontend rollback options:

- Lovable `Publish -> Update` to a previously verified state if available
- manually revert the staging-live project change and republish
- unpublish the staging-live Lovable app
- change staging BFF CORS to remove the staging Lovable origin

Backend stop option:

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-control --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && docker compose -f docker-compose.control.yml stop operator-bff'
```

If any broker order remains open, cancel it in TWS first and record the fact
before stopping services.
