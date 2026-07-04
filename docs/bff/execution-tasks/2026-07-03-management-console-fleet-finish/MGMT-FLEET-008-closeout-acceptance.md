# MGMT-FLEET-008 - Closeout Acceptance

Owner: Codex
Reviewer: Claude
Depends on: `MGMT-FLEET-002`, `MGMT-FLEET-003`, `MGMT-FLEET-004`, `MGMT-FLEET-005`, `MGMT-FLEET-006`, `MGMT-FLEET-007`
Type: final acceptance and archive task

## Purpose

Close the fleet finish packet only after merged code, hosted evidence, and audit
proof exist.

## Scope

- Gather PR numbers, merge SHAs, validation outputs, and reviewer approvals for
  all `MGMT-FLEET-*` implementation tasks.
- Run hosted or preview Management browser probes for every visible route that
  changed.
- Run BFF smoke probes for touched Management endpoint families.
- Run the management list-contract audit and write-action source scan.
- Archive a final closeout note under
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/`.

## Acceptance

- All prerequisite tasks are merged or explicitly superseded with evidence.
- Hosted route/control evidence shows no blank route, nav failure, or fake
  production write success.
- List-contract audit reports no new issues.
- Final archive lists what was adjusted, deleted or demoted, and deepened.
- Residual risks have owner, expiry, and follow-up task id.

## Validation

```sh
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new
npm --prefix execute-plans run build:management
git diff --check
```
