# Frontend Lovable Environments

Status date: 2026-05-10

Authoritative operating rules:

- [lovable-dev-staging-operating-rules.md](/home/lupin/code/pantheon/docs/deployment/lovable-dev-staging-operating-rules.md)
- [bff-https-ingress.md](/home/lupin/code/pantheon/docs/deployment/bff-https-ingress.md)

## Current State

The Pantheon UI repo already supports a BFF base URL through
`VITE_BFF_BASE_URL`. The global app shell now also reads
`VITE_PANTHEON_ENV` and renders an environment badge:

- `dev` renders `DEV`
- `staging-live` renders `STAGING LIVE BROKER`

Known Lovable frontend projects:

- dev project name: `pantheon-ui-dev`
- current execute-plans dev frontend URL: `https://pantheon-dev.lovable.app`
- legacy dev frontend URL: `https://pantheon-ai-system-front-dev.lovable.app`
- staging-live project name: `pantheon-ui-staging-live`
- staging-live frontend URL: `https://pantheon-ai-system-front-staging-live.lovable.app`

The target operating model is two separate Lovable hosted apps. Do not rely on
two Lovable projects sharing one GitHub repo through different branches. As of
the current Lovable GitHub documentation, project sync is default-branch based,
each Lovable project has one linked repository, and connecting a project creates
a new repository for that project.

Practical consequence:

- Lovable-hosted dev and staging-live should be two Lovable projects.
- If both are hosted by Lovable, expect them to have separate Lovable project
  state and separate GitHub-linked repos.
- Promotion to staging-live is a controlled copy/sync of a verified change, not
  a Lovable setting that pins staging to `staging-live` while dev pins to `dev`.

## Target Hosted Apps

Dev Lovable app:

```env
VITE_PANTHEON_ENV=dev
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
VITE_BFF_DEV_LOGIN_PATH=/bff/auth/dev-login
VITE_BFF_OIDC_CLIENT_ID=<dev-client-id>
VITE_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>
VITE_PANTHEON_LIVE_BROKER_ENABLED=false
```

Staging-live Lovable app:

```env
VITE_PANTHEON_ENV=staging-live
VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=true
```

`VITE_PANTHEON_LIVE_BROKER_ENABLED` is only a UI hint. The BFF/backend owns the
real enforcement.

## Current Backend URLs

Current BFFs are healthy on public HTTPS endpoints:

- dev BFF: `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`
- staging-live BFF on VM1:
  `https://pantheon-staging-bff.34.81.225.122.sslip.io`

Internal or VM-local endpoints remain:

- dev BFF: `http://10.140.0.6:18001`
- staging-live BFF on VM1: `http://10.140.0.4:38001`

These are not valid Lovable hosted frontend URLs because Lovable runs in the
browser. The browser needs a public HTTPS origin. Do not set Lovable to a GCP
internal IP, and do not use `http://<external-ip>:<port>` from HTTPS Lovable
hosting because browsers will block mixed content.

The current HTTPS ingress uses static VM external IPs, `sslip.io` DNS, and
Caddy TLS reverse proxies on port `443`.

## BFF CORS

The BFF now supports a comma-separated allowlist:

```env
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app
```

For staging-live:

```env
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-staging-live.lovable.app
```

The dev BFF currently allows both the legacy dev Lovable origin and the current
execute-plans dev Lovable origin during the frontend repo/domain migration. Do
not allow both dev and staging origins on the same BFF unless the operator is
intentionally running a temporary migration window.

## Dev BFF Auth

The dev Lovable app uses the BFF dev-login client-credentials exchange before
real operator login/OIDC is wired. The browser calls `POST /bff/auth/dev-login`
with the dev-only `VITE_BFF_OIDC_CLIENT_ID` / `VITE_BFF_OIDC_CLIENT_SECRET` and
receives a short-lived JWT for `/bff/me` and strict BFF probes. The dev BFF must
explicitly run with:

```env
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_JWT_SECRET=<dev-jwt-signing-secret>
PANTHEON_BFF_JWT_ISSUER=pantheon-dev
PANTHEON_BFF_JWT_AUDIENCE=bff-operators
PANTHEON_BFF_OIDC_CLIENT_ID=<dev-client-id>
PANTHEON_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>
```

This pairing is dev-only. Staging-live and production must keep
`PANTHEON_BFF_AUTH_STUB=false` and require their strict OIDC/JWKS tokens.

## Promotion Flow

For the full gate and rollback rules, follow
`docs/deployment/lovable-dev-staging-operating-rules.md`.

1. Make UI changes in the dev Lovable app or locally.
2. Commit and push the verified change.
3. Smoke test the dev Lovable app against the dev BFF.
4. Promote the verified change to the staging-live Lovable project by applying
   the same patch, cherry-picking between the separate GitHub repos, or asking
   Lovable to copy the reviewed change from the dev project.
5. Publish staging-live only after the BFF URL, CORS allowlist, and operator auth
   are verified.

## Smoke Checklist

Dev UI:

- app loads
- header shows `DEV`
- BFF URL points to the dev HTTPS endpoint
- live broker command scope is rejected by the dev BFF

Staging-live UI:

- app loads
- header shows `STAGING LIVE BROKER`
- BFF URL points to the staging VM1 HTTPS endpoint
- CORS allows only the staging Lovable origin
- operator auth works
- read-only broker status is visible before any live order/cancel rehearsal
