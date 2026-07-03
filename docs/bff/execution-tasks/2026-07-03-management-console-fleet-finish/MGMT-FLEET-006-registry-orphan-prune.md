# MGMT-FLEET-006 - Registry And Orphan Prune

Owner: Gemini2
Reviewer: Codex
Depends on: `MGMT-FLEET-001`
Type: cleanup and route behavior task

## Purpose

Remove false Management surfaces while preserving valid operator viewpoints.

## Scope

- Inventory `apps/management` widgets, historical Management aliases, duplicate
  route names, and registry/list routes that are not active workflows.
- For each surface, decide one outcome: migrate, redirect, demote, archive, or
  delete.
- Keep redirects for old bookmarks only when they land on canonical routes.
- Remove direct rendering of old duplicate components.
- Update route tests and docs with the final behavior.

## Acceptance

- Every orphan or duplicate surface has an explicit outcome and evidence.
- Deleted or archived code is not imported by active Management builds.
- Redirects preserve bookmarks without duplicating UI.
- No valid operator job is removed solely because it shares a table component.

## Validation

```sh
rg "apps/management|management/.*legacy|control-room-legacy" .
npm --prefix execute-plans test -- --runInBand --testPathPattern=management
npm --prefix execute-plans run build:management
git diff --check
```
