# MGMT-GAP-004 Closeout - Command Receipts And Write Truth

Date: 2026-07-01

## Status

MGMT-GAP-004 implementation is ready for PR review. This closeout records the
frontend command-truth branch and the Pantheon BFF contract evidence used for
review. It does not claim hosted deployment or final MGMT-GAP-007 release
acceptance.

## Delivery Scope

Frontend repo: `ajoe734/execute-plans`

Frontend branch: `task/mgmt-gap-004-command-receipts-2`

Frontend branch head: `60151a1c8924a4708a2aac0f2cc5ff2da250b16a`

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

## Residual Notes

- Hosted FE deploy and strict-live browser proof remain for MGMT-GAP-006 and
  MGMT-GAP-007 after the frontend PR merges and deploys.
- Studios and capability runtime depth remain MGMT-GAP-005 where command
  endpoints/runners are absent.
- BFF FastAPI startup/shutdown deprecation warnings and `datetime.utcnow`
  warnings are pre-existing hygiene items outside this command-truth slice.
