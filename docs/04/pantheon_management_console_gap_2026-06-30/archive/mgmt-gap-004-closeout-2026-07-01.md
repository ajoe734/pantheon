# MGMT-GAP-004 Closeout - Command Receipts And Write Truth

Date: 2026-07-01

## Status

MGMT-GAP-004 is closed at the task level.

The frontend command-truth branch was merged to `ajoe734/execute-plans` `dev`,
the Pantheon dev FE deployed the merged commit, `/deployment.json` reports that
commit, the dev FE-BFF integration gate passed for the merged commit, and BFF
health was verified after deploy.

This does not close the full management-console production gap. `MGMT-GAP-006`
must still build the hosted all-route production acceptance harness, and
`MGMT-GAP-007` must still reconcile every remaining task into final production
proof.

## Delivery Scope

Frontend repo: `ajoe734/execute-plans`

Frontend branch: `task/mgmt-gap-004-command-receipts-2`

Frontend branch head: `60151a1c8924a4708a2aac0f2cc5ff2da250b16a`

Frontend PR: `https://github.com/ajoe734/execute-plans/pull/132`

Frontend merge commit: `8ad6e034e9f831a11f143496b0320beba7a41dc2`

Implemented frontend changes:

- Added `src/lib/bff-v1/commandReceipt.ts` so command/audit ids, status,
  correlation ids, and idempotency keys appear in success feedback.
- Updated `runActionSafe` success feedback to show command/audit receipt
  descriptions instead of generic local success.
- Routed ranking recalculate, compare, override, freeze, publish, and active
  formula actions through `runActionSafe`.
- Converted governance, workflow, hook, settings, knowledge, studio, permission,
  route-policy, and unsupported editor CTAs that do not have production command
  endpoints into explicit disabled non-production actions.
- Updated detail and operations write CTAs that already have governed action
  paths to display command/audit receipt descriptions and only update local UI
  after accepted receipts.

Pantheon repo: `ajoe734/pantheon`

Pantheon task branch: `task/MGMT-GAP-004`

Pantheon backend/test change:

- Updated `test_aud_002_audit_action_write_engine.py` to exercise canonical
  `POST /bff/actions/runtime/{runtime_id}/{action_id}` instead of the deprecated
  runtime action route.
- Aligned audit assertions with the canonical Runtime target scope and separate
  `command_ref` / `trace_id` semantics.

## Local Validation

Frontend focused tests:

```sh
npx vitest run \
  src/lib/bff-v1/__tests__/commandReceipt.test.ts \
  src/management/components/NonProductionActionButton.test.tsx
```

Result: `2 passed`, `4 tests passed`.

Frontend lint and patch checks:

```sh
npx eslint \
  src/lib/bff-v1/commandReceipt.ts \
  src/lib/bff-v1/runActionSafe.ts \
  src/management/components/NonProductionActionButton.tsx \
  src/management/pages/phase2/RankingDashboard.tsx \
  src/management/pages/phase2/HookCronManager.tsx \
  src/management/pages/phase2/WorkflowTemplates.tsx \
  src/management/pages/governance/MemoryGovernancePage.tsx \
  src/management/pages/governance/ConsultRulesPage.tsx \
  src/management/components/governance/PermissionMatrix.tsx \
  src/management/components/governance/RoutePolicyEditor.tsx
```

Result: passed.

```sh
git diff --check origin/dev..HEAD
git diff --name-only origin/dev..HEAD | grep -E '\.(ts|tsx)$' | xargs npx eslint
```

Result: passed.

Frontend production build:

```sh
npm run build
```

Result: passed, built in `2m 45s` after rebasing onto the current
`origin/dev`.

Observed warnings:

- Browserslist data is old.
- Existing CSS minifier warning: `Expected identifier but found "-"`.
- Existing dynamic import/chunk placement warning for `src/lib/bff/realtime.ts`.
- Existing large chunk warning, including `index-*.js` gzip around `1.6MB`.
  This is tracked by the management load-gap tasks, not MGMT-GAP-004.

Pantheon BFF focused validation:

```sh
python3 -m pytest \
  services/control-plane/bff/test_final_command_execution_bridge.py \
  services/control-plane/bff/test_bff_dry_run_rbac_contract.py \
  services/control-plane/bff/test_aud_002_audit_action_write_engine.py
```

Result: `17 passed`, `16 warnings`.

This proves:

- `/bff/v1/commands` and canonical action routes return durable command
  receipts and replay safely through idempotency.
- Dry-run command/write routes do not write command store records or live read
  surfaces.
- Accepted runtime action commands are queryable through BFF audit projection
  by command reference and target entity.

## Hosted Closeout Evidence

execute-plans PR:

- PR: `https://github.com/ajoe734/execute-plans/pull/132`
- Branch: `task/mgmt-gap-004-command-receipts-2`
- Branch head: `60151a1c8924a4708a2aac0f2cc5ff2da250b16a`
- Merge commit on `dev`: `8ad6e034e9f831a11f143496b0320beba7a41dc2`
- PR integration gate:
  `https://github.com/ajoe734/execute-plans/actions/runs/28500266955`

Merged `dev` evidence:

- Dev FE-BFF integration gate:
  `https://github.com/ajoe734/execute-plans/actions/runs/28500441725`
- Passing job after rerun:
  `https://github.com/ajoe734/execute-plans/actions/runs/28500441725/job/84480698924`
- Dev FE deploy:
  `https://github.com/ajoe734/execute-plans/actions/runs/28500441733`
- Deployment proof:
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
- BFF health:
  `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz`

Observed deployment payload:

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260701T071752Z",
  "commit": "8ad6e034e9f831a11f143496b0320beba7a41dc2",
  "sourceRef": "8ad6e034e9f831a11f143496b0320beba7a41dc2",
  "sourceBranch": "dev",
  "feHost": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
  "bffHost": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```

The first merged-dev integration gate attempt hit transient BFF/SSE readiness
failures during release aggregation. The same target commit was rerun and passed
all gate steps, including lint, unit/integration tests, build, contract drift,
management persona validation, anonymous BFF route probe, authenticated BFF
smoke, live dry-run write probe, management live deep validation, browser BFF
probe, Playwright E2E, and release-gate aggregation.

## Residual Notes

- The task-level command-truth slice is closed, but the all-route strict-live
  production harness remains `MGMT-GAP-006`.
- Studios and capability runtime depth remain MGMT-GAP-005 where command
  endpoints/runners are absent.
- Detail DTO/render honesty remains `MGMT-GAP-008`.
- Session/provider-auth/RBAC coherence remains `MGMT-GAP-009`.
- Load/bundle/release-gate performance remains `MGMT-GAP-010`.
- BFF FastAPI startup/shutdown deprecation warnings and `datetime.utcnow`
  warnings are pre-existing hygiene items outside this command-truth slice.
