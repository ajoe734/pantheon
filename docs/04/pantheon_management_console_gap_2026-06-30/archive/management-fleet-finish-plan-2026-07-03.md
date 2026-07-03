# Management Console Fleet Finish Plan - 2026-07-03

| Field | Value |
|---|---|
| Status | Fleet execution packet for remaining Management Console work |
| Base | `origin/dev` at `1244976c4` |
| Packet | `docs/bff/execution-tasks/2026-07-03-management-console-fleet-finish/INDEX.md` |
| Dispatch script | `scripts/dispatch_management_console_fleet_finish_2026-07-03.py` |
| Supersedes | Ad hoc local AI Ops WIP and any stale partial Management task branch |

## Current Baseline

The 2026-07-02 rerun remains the audit basis, but several frontend and backend
repair PRs are now part of the merged baseline. Fleet owners must inspect the
current source before coding.

- PR #2793, `MGMT-FE-ROUTER-001`, merged at
  `f178346523b76dfe4802405d8b9de4ff6c396d4e`: direct `/management` and
  `/management/*` visits now serve the Management shell instead of blank Vite
  404 pages.
- PR #2794, `MGMT-FE-OODA-001`, merged at
  `716737008e918ace2f0bcac65af4a45046e20cb8`: the OODA packet workflow is now
  an active route panel instead of an unreachable drawer.
- Later dev work also improved live evidence proof tokens and persona fleet
  BFF contract coverage. Re-run current-state checks before implementation.

The local AI Ops implementation attempt is not a completed task. It had not
passed focused validation, was not committed, was not opened as a PR, and was
not merged. Treat it only as stale exploratory work unless a fleet owner
rebases, audits, and proves every line against current `origin/dev`.

## Remaining Work

The remaining work is not "make the old production gate pass." The production
closeout already passed. The remaining work is to turn the thin Management shell
and broad BFF surface into durable operator workflows while deleting or hiding
false surfaces.

| Task | Owner | Reviewer | Purpose |
|---|---|---|---|
| `MGMT-FLEET-001` | Codex | Claude | Reconfirm current merged state before any fleet codes. |
| `MGMT-FLEET-002` | Claude | Codex | Finish the Management AI/NL workflow as a real active panel. |
| `MGMT-FLEET-003` | Gemini | Claude2 | Recluster human inbox, interventions, approvals, sentinel, governance, incidents, and alerts into a decision workbench. |
| `MGMT-FLEET-004` | Claude2 | Codex2 | Build the readiness suite for broker, capital, BFF HA, EP5, and strict publish. |
| `MGMT-FLEET-005` | Codex2 | Gemini | Build performance review surfaces from ranking, persona league, portfolio, attribution, and cost evidence. |
| `MGMT-FLEET-006` | Gemini2 | Codex | Delete, archive, redirect, or demote orphan and duplicate Management UI surfaces. |
| `MGMT-FLEET-007` | Claude | Codex2 | Burn down write-looking CTAs and capability runners to governed receipts or explicit demotion. |
| `MGMT-FLEET-008` | Codex | Claude | Run closeout acceptance, hosted probes, list-contract audit, and final archive. |

## Non-Negotiable Guardrails

- Every fleet task starts by checking `git status -sb`, current branch, remote,
  open PRs, and the current source. Do not continue from stale worktrees.
- Do not re-open completed `MGMT-GAP-*` tasks. This is the post-closeout finish
  layer.
- Do not call a local-only render, unmerged branch, or restarted process
  complete.
- Do not introduce new list-contract smells. Run the management list audit
  whenever a Management read payload changes.
- Do not expose write-looking actions without command id, receipt id, audit or
  readback evidence, or an explicit non-production disabled state.
- Delete only true duplicates or dead surfaces. Valid operator viewpoints should
  be clustered and deepened, not removed because their shells look similar.
- Each implementation task must land through branch, commit, push, PR, visible
  checks, merge, and recorded merge SHA.

## Execution Order

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

## Completion Evidence

`MGMT-FLEET-008` is the only task allowed to close the finish packet. It must
record:

- PR numbers and merge SHAs for all completed fleet work;
- hosted Management route probes for every visible route changed;
- BFF smoke evidence for Management API families touched;
- list-contract audit output with `new=0`;
- source scan result for write-looking controls;
- deletion/demotion inventory with old URL behavior;
- residual risk owner and expiry for anything not completed.
