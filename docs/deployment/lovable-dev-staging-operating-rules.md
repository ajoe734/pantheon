# Lovable Dev/Staging Operating Rules

Status date: 2026-04-27

## Purpose

This is the authoritative rulebook for using Lovable as the hosted frontend for
Pantheon non-prod environments.

The goal is to preserve Lovable's development speed while preventing dev UI
work from accidentally reaching the staging-live broker rehearsal path.

## Source Of Truth

Authoritative topology docs:

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

## Required Model

Use two Lovable projects:

| Environment | Lovable project | Lovable frontend URL | Purpose | Publish policy |
| --- | --- | --- | --- | --- |
| dev | `pantheon-ui-dev` | `https://pantheon-dev.lovable.app` | daily UI iteration | may publish frequently after basic smoke |
| staging-live | `pantheon-ui-staging-live` | `https://pantheon-ai-system-front-staging-live.lovable.app` | EP5-002/live broker rehearsal frontend | publish only from a verified promotion |

Do not use one Lovable project as both dev and staging-live.

Do not rely on two Lovable projects sharing one GitHub repo through separate
branches. Treat that as unsupported unless Lovable later documents a stable
per-project branch binding.

## Project Creation

Dev project:

- Use the existing Lovable project unless the operator explicitly replaces it.
- Rename/display-name it as `pantheon-ui-dev`.
- Current subdomain: `pantheon-dev`.
- Current frontend URL: `https://pantheon-dev.lovable.app`.
- Legacy frontend URL: `https://pantheon-ai-system-front-dev.lovable.app`.
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

Dev Lovable project:

```env
VITE_PANTHEON_ENV=dev
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=false
```

Staging-live Lovable project:

```env
VITE_PANTHEON_ENV=staging-live
VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=true
```

Rules:

- `VITE_` values are public browser build values, not secrets.
- Never place broker credentials, TWS credentials, API tokens, or private keys
  in Lovable frontend env vars.
- `VITE_PANTHEON_LIVE_BROKER_ENABLED` is a UI hint only. The BFF/backend owns
  the real enforcement.
- Changing these values requires rebuilding and republishing the Lovable app.

## BFF URL And CORS

Lovable-hosted frontend must call browser-reachable HTTPS BFF endpoints.

Do not use:

```text
http://10.140.0.6:18001
http://10.140.0.4:38001
http://<external-ip>:<port>
```

Current BFF HTTPS URLs:

```text
https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
https://pantheon-staging-bff.34.81.225.122.sslip.io
```

BFF CORS must be one-to-one:

```env
# dev BFF on pantheon-dev-vm1
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app

# staging BFF on pantheon-taiwan
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-staging-live.lovable.app
```

The dev BFF keeps the legacy dev Lovable origin while execute-plans completes
its domain/repo cutover. Remove the legacy origin once no active dev traffic
uses it.

Do not allow both dev and staging Lovable origins on the same BFF unless the
operator has explicitly approved a temporary migration window.

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

1. Make changes in `pantheon-ui-dev` or locally.
2. Verify the UI builds.
3. Publish dev if needed.
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

1. Read this file.
2. Read `docs/deployment/nonprod-development-workflow.md`.
3. State whether the requested work affects dev, staging-live, or both.
4. Do not claim Lovable supports same-repo different-branch project binding.
5. Do not create a staging-live prompt that says "sync latest dev draft".
6. Do not publish or update staging-live unless the user explicitly asks.
7. Do not put secrets in frontend env.
8. Do not point Lovable at internal IPs or non-HTTPS BFF URLs.
9. If the BFF HTTPS endpoint does not exist yet, say staging Lovable cannot be
   fully wired.

## Staging-Live Publish Gate

All must be true before staging-live publish/update:

- staging BFF has a browser-reachable HTTPS URL
- staging Lovable env uses `VITE_PANTHEON_ENV=staging-live`
- staging Lovable env uses
  `VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io`
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
gcloud compute ssh edna@pantheon-taiwan --zone=asia-east1-b --project=pantheon-493602 -- \
  'cd /home/lupin/code/pantheon && docker compose -f docker-compose.control.yml stop operator-bff'
```

If any broker order remains open, cancel it in TWS first and record the fact
before stopping services.
