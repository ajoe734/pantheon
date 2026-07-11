# MGMT-OPS-003 Hosted Gap Execution Packet - 2026-07-11

Status: ready for fleet dispatch after source PR merge

Source archive:

- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/INDEX.md`
- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`

## Execution Order

| Wave | Task | Owner | Reviewer | Repository | Delivery |
|---|---|---|---|---|---|
| 0 | `MGMT-OPS-003-GAP-001` | Codex2 | Copilot | `execute-plans` | Incident, filter, stage, and confidence UI |
| 0 | `MGMT-OPS-003-GAP-002` | Copilot | Codex2 | `pantheon` | Runtime binding and telemetry truth repair |
| 1 | `MGMT-OPS-003-GAP-003` | Codex | Copilot | both | Hosted operator workflow E2E |
| 2 | `MGMT-OPS-003-GAP-004` | Codex2 | Codex | both | Independent difference closeout gate |

No task is assigned to Qwen.

## Dependency Graph

```text
MGMT-OPS-003-GAP-001: none
MGMT-OPS-003-GAP-002: none
MGMT-OPS-003-GAP-003: GAP-001, GAP-002
MGMT-OPS-003-GAP-004: GAP-001, GAP-002, GAP-003
MGMT-PERF-IA-003: existing dependencies plus GAP-004 when present on the board
```

## Repository Rules

- `execute-plans` frontend changes start from `origin/main`, use a clean task
  worktree, and merge to `main`.
- Pantheon changes start from `origin/dev`, use a clean task worktree, and
  merge to `dev`.
- Every repository change requires validation, a task-scoped commit, push, PR,
  required checks, merge, and merge SHA evidence.
- Frontend delivery must build with `VITE_BFF_MODE=live`, the Pantheon-owned dev
  BFF URL, `VITE_BFF_FALLBACK=strict`, and safe write defaults.
- Do not recreate an embedded frontend mirror in Pantheon.

## Reviewer Rule

Every reviewer must use `REVIEWER_CHECKLIST.md`. Approval is fail-closed: a
reviewer must request changes when any required live difference remains,
evidence is missing, the tested SHA differs from the deployed SHA, or the UI
claims stronger confidence than the BFF response.

## Dispatch

Dry run:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_mgmt_ops_003_hosted_gap_2026-07-11.py --dry-run
```

Live dispatch:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_mgmt_ops_003_hosted_gap_2026-07-11.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/ai_status.py sync
```
