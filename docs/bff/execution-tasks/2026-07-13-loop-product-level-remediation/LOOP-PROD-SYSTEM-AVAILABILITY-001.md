# LOOP-PROD-SYSTEM-AVAILABILITY-001

Status: system-feature restoration; security backlog remains deferred

## Observed outage

The current Pantheon dev FE is deployed with `VITE_BFF_MODE=live`,
`VITE_BFF_FALLBACK=strict`, and `VITE_BFF_EMBEDDED_BEARER_TOKEN=false`.
The running BFF requires a Bearer JWT, but the existing dev-login client
credentials are not provisioned/consumed by the deployed browser session. The
result is `401 AUTH_REQUIRED`, `seed fallback blocked`, and an empty management
console. This is an availability regression, not a request to redesign auth.

## Objective

Restore the already-approved dev login transport so the existing system reads
work again. Do not implement new security features.

## Fleet-owned scope

- `execute-plans` runtime auth bridge and its focused tests;
- Pantheon dev deployment configuration that supplies the existing dev-login
  client credentials through protected environment handling; and
- the redacted hosted read-only smoke evidence.

## Hard boundaries

The fleet must not:

- change roles, permissions, tenant rules, MFA rules, token validation,
  token TTL, route authorization, or CORS policy;
- restore an embedded bearer token or any privileged token in the bundle;
- turn on seed/mock fallback to disguise a live 401;
- enable writes, broker access, or capital effects; or
- touch the deferred security task set.

## Acceptance

1. The existing `POST /bff/auth/dev-login` contract is reachable in dev and,
   with the protected pre-existing client credentials, returns a short-lived
   JWT without exposing the client secret in the built bundle.
2. The FE obtains/caches the session through the existing runtime bridge and
   sends it to `/bff/me` and the existing read routes.
3. Hosted dev read-only smoke shows `/bff/me`, `/bff/personas`, dashboard
   summary, incidents, and the Management AI mode surface no longer fail solely
   with missing Bearer token; writes remain disabled.
4. `VITE_BFF_FALLBACK` remains `strict`, embedded bearer remains disabled, and
   no security policy or route matrix changes appear in the diff.
5. A second fleet reviewer verifies the exact FE/BFF commits, deployment
   identity, redacted response/status evidence, and rollback path.

## Non-goals

- no new auth/security implementation;
- no security backlog task execution;
- no fallback/mock data in hosted strict mode; and
- no live write or capital side effect.
