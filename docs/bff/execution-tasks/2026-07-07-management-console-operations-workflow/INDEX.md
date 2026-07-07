# Management Console Operations Workflow Execution Packet - 2026-07-07

Status: ready for fleet dispatch and implementation

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Dispatch Command

For local validation without mutating status:

```sh
python3 scripts/dispatch_management_console_ops_workflow_2026-07-07.py --dry-run
```

For live fleet dispatch from a non-live worktree:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/dispatch_management_console_ops_workflow_2026-07-07.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It preserves progress fields for already
started tasks, assigns unfinished tasks to their owner lanes, and records the
archived operations workflow plan as the source of truth.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `MGMT-OPS-001` | Claude2 | Codex2 | Lock the shared operations read model and source-confidence contract. |
| 1 | `MGMT-OPS-002` | Codex2 | Claude2 | Normalize frontend adapters and data-confidence UI rules across the pages. |
| 1 | `MGMT-OPS-003` | Gemini2 | Codex2 | Turn Portfolio Book into the capital, exposure, and risk monitor. |
| 1 | `MGMT-OPS-004` | Antigravity2 | Claude2 | Fix Performance Attribution drilldown, fallback labeling, and diagnostics. |
| 2 | `MGMT-OPS-005` | Gemini | Codex2 | Reframe Persona League and Quarterly Ranking as governed ranking inputs. |
| 2 | `MGMT-OPS-006` | Antigravity | Claude2 | Wire governed operator actions through Human Review and auditable receipts. |
| 3 | `MGMT-OPS-007` | Codex2 | Human/Ops | Close with PRs, tests, dev publish, hosted smoke, and residual-risk evidence. |

## Dependencies

```text
MGMT-OPS-001: none
MGMT-OPS-002: MGMT-OPS-001
MGMT-OPS-003: MGMT-OPS-001
MGMT-OPS-004: MGMT-OPS-001, MGMT-OPS-002
MGMT-OPS-005: MGMT-OPS-001, MGMT-OPS-002
MGMT-OPS-006: MGMT-OPS-003, MGMT-OPS-004, MGMT-OPS-005
MGMT-OPS-007: MGMT-OPS-002, MGMT-OPS-003, MGMT-OPS-004, MGMT-OPS-005, MGMT-OPS-006
```

## Global Acceptance

Every `MGMT-OPS-*` task must record:

1. branch and PR target;
2. local validation commands and output summary;
3. reviewer approval or explicit blocker;
4. merge commit SHA when merged;
5. hosted FE/BFF evidence when runtime behavior changes;
6. residual risk with owner and expiry.

The packet is not complete until `MGMT-OPS-007` proves the operator workflow:

```text
Portfolio Book -> Persona Fleet -> Performance Attribution -> Human Review
Persona League / Quarterly Ranking -> Review Packet -> Approved Apply Receipt
```

No task may claim a recommendation, promotion, rebalance, pause, or containment
directly mutates live capital without a governed apply command and audit receipt.

## Product Routing Contract

Primary operator flow:

- `/management/capital`
- `/management/persona-fleet`
- `/management/performance-attribution`
- `/management/human-inbox`

Governance inputs:

- `/management/persona-league`
- `/management/quarterly-ranking`

The pages may use local table layouts, but they must share the source-confidence
and action-state semantics from the archived plan.
