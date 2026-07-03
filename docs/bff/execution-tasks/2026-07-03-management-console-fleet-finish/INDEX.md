# Management Console Fleet Finish Packet - 2026-07-03

Status: ready for fleet dispatch after this packet merges.

This packet turns the remaining Management Console work into execution tasks.
It is based on the superseding 2026-07-02 re-audit, plus the merged route-shell
and OODA follow-up PRs from 2026-07-03.

Source docs:

- `docs/04/pantheon_management_console_gap_2026-06-30/archive/complete-reaudit-rerun-2026-07-02.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/management-adjustment-development-plan-2026-07-02.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/management-fleet-finish-plan-2026-07-03.md`

Merged baseline:

- PR #2793: `MGMT-FE-ROUTER-001`, merge
  `f178346523b76dfe4802405d8b9de4ff6c396d4e`
- PR #2794: `MGMT-FE-OODA-001`, merge
  `716737008e918ace2f0bcac65af4a45046e20cb8`

Do not continue the local AI Ops WIP as if it were completed. It must be
rebased, audited, validated, and PR-merged like any other implementation.

## Tasks

| Order | Task | Owner | Reviewer | Brief |
|---:|---|---|---|---|
| 1 | [`MGMT-FLEET-001`](MGMT-FLEET-001-current-state-guard.md) | Codex | Claude | Reconfirm current source, merged work, open worktrees, and remaining scope. |
| 2 | [`MGMT-FLEET-002`](MGMT-FLEET-002-ai-ops-nl-workflow.md) | Claude | Codex | Build the Management AI/NL active workflow. |
| 3 | [`MGMT-FLEET-003`](MGMT-FLEET-003-decision-workbench.md) | Gemini | Claude2 | Consolidate decision and operations queues. |
| 4 | [`MGMT-FLEET-004`](MGMT-FLEET-004-readiness-suite.md) | Claude2 | Codex2 | Build broker, capital, BFF HA, EP5, and strict publish readiness. |
| 5 | [`MGMT-FLEET-005`](MGMT-FLEET-005-performance-review-suite.md) | Codex2 | Gemini | Build performance, ranking, portfolio, persona league, and cost review flows. |
| 6 | [`MGMT-FLEET-006`](MGMT-FLEET-006-registry-orphan-prune.md) | Gemini2 | Codex | Delete, archive, redirect, or demote orphan and duplicate surfaces. |
| 7 | [`MGMT-FLEET-007`](MGMT-FLEET-007-command-runner-demotion.md) | Claude | Codex2 | Burn down write CTAs and capability runner/demotion decisions. |
| 8 | [`MGMT-FLEET-008`](MGMT-FLEET-008-closeout-acceptance.md) | Codex | Claude | Run final acceptance and archive closeout evidence. |

## Dependency Order

```text
MGMT-FLEET-001: none
MGMT-FLEET-002: MGMT-FLEET-001
MGMT-FLEET-003: MGMT-FLEET-001
MGMT-FLEET-004: MGMT-FLEET-001
MGMT-FLEET-005: MGMT-FLEET-001
MGMT-FLEET-006: MGMT-FLEET-001
MGMT-FLEET-007: MGMT-FLEET-002, MGMT-FLEET-003, MGMT-FLEET-004,
                MGMT-FLEET-005, MGMT-FLEET-006
MGMT-FLEET-008: MGMT-FLEET-002, MGMT-FLEET-003, MGMT-FLEET-004,
                MGMT-FLEET-005, MGMT-FLEET-006, MGMT-FLEET-007
```

## Dispatch

After this packet merges, dispatch the runtime tasks from a clean, current
checkout:

```sh
python3 scripts/dispatch_management_console_fleet_finish_2026-07-03.py
python3 scripts/ai_status.py sync
```

The dispatch script is intentionally not run by this planning PR because live
task-state files are shared runtime state. The script is the canonical task
definition for fleet ingestion.

## Global Acceptance

Every task must record:

1. current base commit and branch;
2. files changed and ownership scope;
3. exact validation commands and output summary;
4. PR URL, reviewer approval, and merge commit SHA;
5. hosted browser/BFF evidence for user-visible changes;
6. list-contract audit output when Management read payloads change;
7. residual risk with owner and expiry.
