# Round 001 - FE<->BFF deployment-config & integration-probe IP drift

- Date: 2026-06-14
- Path tested: Loop #7 (Promotion/Deployment) entry + Loop #11 (BFF Health Monitoring),
  via the FE->BFF deployment-config and the integration/probe/e2e verification harness.
- Branch: task/verify-r1-bff-ip-drift  (off origin/dev)

## Verification method

1. Read canonical loop inventory (`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`,
   `PAPER_CANARY_LIVE_POLICY.md`, evolution/kill-switch/rollback/delivery docs).
2. Live-probed the dev surface:
   - dev BFF `35.201.239.38`: `/health` 200 (operator-bff v0.2.0), `/readyz` ready=true
     with all 3 downstream deps ok (runtime-manager:8081, governance:8082, deployment:8095),
     `/metrics` service_up=1, `/openapi.json` 443 routes covering the full MPOS loop domain.
   - Auth gate fail-closed: `/bff/me`, `/api/v1/deployment-plans`, `/api/v1/kill-switch/status`
     all return 401 AUTH_REQUIRED with structured i18n error envelope.
3. Static config audit for post-GCP-migration drift.

## Finding (gap)

The live dev BFF moved to `35.201.239.38` during the 2026-05-30 GCP migration, and the live
dev FE bundle already targets it. But the **integration-verification harness and FE deploy
config still defaulted to the dead pre-migration IP `34.81.75.241`**:

- `execute-plans/.lovable/prod-strict.env`, `preview-strict.env` (VITE_BFF_BASE_URL)
- `execute-plans/.env.integration.example` (PANTHEON_BFF_BASE_URL)
- `execute-plans/scripts/probe-bff-routes.mjs`, `probe-bff-authenticated-live.mjs`,
  `probe-hosted-browser-bff.mjs` (default BASE fallback)
- `execute-plans/tests/e2e/detail-smoke-{a,b,c}.spec.ts` (default BFF_BASE_URL)
- `execute-plans/.github/workflows/pantheon-integration-gate.yml` (workflow input default)

Impact: anyone running the integration gate / probes / e2e smoke WITHOUT explicitly
overriding the env var would hit a dead host (curl timeout), so the FE<->BFF integration
verification loop could not pass against live dev. Historical evidence/task-archive/sidecar
records that mention the old IP were intentionally left untouched (immutable history).

## Fix

Repointed the 10 operational files from `pantheon-lupin-dev-bff.34.81.75.241.sslip.io`
to `pantheon-lupin-dev-bff.35.201.239.38.sslip.io`. All are env-overridable defaults;
the swap only corrects the fallback to live infra.

## Test evidence

Ran the corrected `probe-bff-routes.mjs` (anonymous) against the new default:

```
## Gate
PASS: no canonical route returned 404.
```

Every canonical `/bff/*` and `/bff/v5/*` route returned 401 (correct fail-closed), none
404 -> the corrected default reaches the live BFF and the full loop-entry route surface is
present and gated. (Authenticated e2e + the loops' actual cycling still require a dev OIDC
token; deferred to a future round.)

## Deferred / follow-ups

- `execute-plans/.env.integration.example` still has `PANTHEON_FE_BASE_URL=pantheon-dev.lovable.app`;
  dev FE is now self-hosted at `pantheon-lupin-dev-fe.35.201.239.38.sslip.io`. FE-URL drift is
  a separate judgment call -> a later round.
- "actually runs" column for all 16 loops remains unproven without a dev token.

## Loop coverage delta

| Loop | design | API | actually runs |
|------|:------:|:---:|:-------------:|
| #7 Promotion/Deployment | yes | yes (routes present, gated) | unproven (needs token) |
| #11 BFF Health Monitoring | yes | yes (/health,/readyz,/metrics live) | partial (deps ok via /readyz) |
