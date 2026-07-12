# MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX

## Objective

Repair the hosted mobile Human Inbox leg uncovered by
`MGMT-OPS-003-GAP-004`.

`MGMT-OPS-003-GAP-004` reran the fail-closed hosted closeout after
`MGMT-OPS-003-GAP-001`, `MGMT-OPS-003-GAP-002`, and
`MGMT-OPS-003-GAP-003` were archived done. It found the current deployed
frontend SHA `a74e58696c900112557b0c748c3f8c69629da106` passes the desktop
Portfolio workflow, but mobile Human Inbox renders strict `Failed to fetch` /
`seed fallback blocked`.

## Scope

- Repository: `ajoe734/execute-plans`
- Base: `origin/dev`
- Hosted FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- Hosted BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Primary files likely involved:
  - `e2e/21-portfolio-workflow-hosted.spec.ts`
  - `src/management/pages/oversight/_core.tsx`
  - `src/lib/bff-v1/*`
  - Human Inbox / Portfolio workflow routing and request code

## Required Work

1. Reproduce the mobile failure against the hosted dev FE/BFF with strict
   fallback enabled.
2. Identify the failing mobile Human Inbox request or route/state transition.
3. Fix the frontend/BFF-client behavior without allowing seed fallback or mock
   data.
4. Add or update regression coverage so the mobile Human Inbox leg fails if the
   required request fails, silently falls back, or loses target context.
5. Merge to `execute-plans` `dev`, wait for dev deployment, and verify
   `/deployment.json` reports the repaired merge SHA.
6. Rerun hosted desktop and mobile workflow probes against Pantheon dev FE/BFF.

## Acceptance

- execute-plans PR is merged to `dev`.
- Dev FE deploy succeeds and `/deployment.json` reports the repaired SHA.
- Post-merge execute-plans integration gate succeeds.
- Hosted desktop and mobile Portfolio workflow reaches Human Inbox with target
  context visible.
- Console errors, failed required requests, BFF 4xx/5xx, lazy chunk failures,
  and fallback-data indicators are zero.
- Evidence records FE SHA, BFF host, commands, desktop/mobile result, and
  request/console failure counts.
- Handoff back to `MGMT-OPS-003-GAP-004` with exact PR, merge SHA, deploy run,
  gate run, and evidence path.

## Reviewer Notes

Reviewer must fail closed if mobile passes only by hiding the Human Inbox
request, allowing seed fallback, using stale deployment evidence, or testing a
SHA different from `/deployment.json`.
