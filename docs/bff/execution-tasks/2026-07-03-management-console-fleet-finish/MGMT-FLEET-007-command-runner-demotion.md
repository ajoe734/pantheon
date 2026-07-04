# MGMT-FLEET-007 - Command Runner Or Demotion

Owner: Claude
Reviewer: Codex2
Depends on: `MGMT-FLEET-002`, `MGMT-FLEET-003`, `MGMT-FLEET-004`, `MGMT-FLEET-005`, `MGMT-FLEET-006`
Type: command safety task

## Purpose

Make every write-looking Management control either production-real or honestly
non-production.

## Scope

- Re-scan Management `toast.success`, `runActionSafe`, `bffWrites`,
  `NonProductionActionButton`, and local state mutation patterns.
- For enabled actions, require governed command id, receipt id, audit link,
  readback, and failure semantics.
- For Formula Studio, Skill Sandbox, Tools, MCP, Skills, and strategy seed
  commands, choose runner or demotion per surface.
- Keep demoted surfaces readable, but remove production-looking execution CTAs.

## Acceptance

- No enabled production action succeeds by local toast alone.
- Remaining disabled controls clearly state why they are not production actions.
- Runner-backed controls expose job id, status readback, trace/evidence, and
  failure reason.
- Any allow-list entry has owner, expiry, and linked follow-up task.

## Validation

```sh
rg "toast\\.success\\(|runActionSafe|bffWrites|NonProductionActionButton|writeOverlay" execute-plans
npm --prefix execute-plans test -- --runInBand --testPathPattern=management
npm --prefix execute-plans run build:management
git diff --check
```
