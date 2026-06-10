# Execute-Plans Frontend Live Completion Task Pack

Date: 2026-05-09

## Superseded Dev Hosting Note - 2026-06-08

This task pack predates the Pantheon-owned dev frontend hosting decision. For
current frontend work, use `ajoe734/execute-plans` and deploy dev FE from the
Pantheon dev environment. Do not use Lovable publish state as the dev frontend
source of truth.

Canonical current rule:
`docs/frontend/execute-plans-dev-hosting.md`.

Repo under implementation:

- `/home/lupin/code/execute-plans`

Backend/control repo for task tracking:

- `/home/lupin/code/pantheon`

## Why This Pack Exists

The Pantheon BFF route contract is live on lupin dev, but the new Lovable repo
(`execute-plans`) is not fully cut over:

- authenticated DTO/write smoke is still open;
- many frontend read surfaces still use mock fallback;
- write governance is not wired to real command/confirm-token routes;
- realtime still uses mock pub/sub;
- the execute-plans working tree is dirty and behind `origin/main`;
- Lovable env/publish smoke has not been completed.

## Task Order

- `BFF-LUV-AUTHED-LIVE-001`: obtain/validate live operator auth or publish an exact blocker.
- `BFF-LUV-FE-001`: clean/sync execute-plans repo and land transport/session foundation.
- `BFF-LUV-FE-002`: wire Management Console read model families.
- `BFF-LUV-FE-003`: wire Agora, v5, and realtime/SSE surfaces.
- `BFF-LUV-FE-004`: wire safe real write flows behind governance gates.
- `BFF-LUV-FE-005`: run final Lovable/live cutover smoke and publish the handoff.

`BFF-LUV-FE-005` must not close until the preceding tasks either complete or
publish explicit blockers.
