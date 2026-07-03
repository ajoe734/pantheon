# MGMT-FLEET-004 - Management Readiness Suite

Owner: Claude2
Reviewer: Codex2
Depends on: `MGMT-FLEET-001`
Type: frontend workflow integration

## Purpose

Build a real readiness suite for the Management console instead of leaving
readiness widgets orphaned or invisible.

## Scope

- Implement active workflows for broker live, capital binding live, BFF HA,
  EP5 readiness, and strict publish readiness.
- Reuse or migrate `apps/management` readiness widgets only after proving they
  match the active `execute-plans` shell and data contracts.
- Show go/no-go status, blocker count, source freshness, degraded reasons, and
  evidence links.
- Archive or delete orphan widget paths that are not migrated.

## Acceptance

- Direct readiness routes render active route panels with live or clearly
  degraded data.
- The readiness suite has no hidden orphan copy of the same UI.
- Empty and degraded states name the source and required operator action.
- Hosted or preview evidence proves intended BFF endpoint calls.

## Validation

```sh
npm --prefix execute-plans test -- --runInBand --testPathPattern=management
npm --prefix execute-plans run build:management
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new
git diff --check
```
