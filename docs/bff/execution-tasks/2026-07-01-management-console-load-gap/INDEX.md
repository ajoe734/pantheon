# Management Console Load Gap Execution Packet - 2026-07-01

Status: ready for fleet dispatch

Parent task:

- `MGMT-GAP-010` - Management console load and release gate performance

Source gap spec:

- `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md`

Related production gap packet:

- `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md`

## Dispatch Command

```sh
python3 scripts/dispatch_management_console_load_gap_2026-07-01.py
python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It preserves progress fields for already
started tasks, updates the `MGMT-GAP-010` umbrella to wait on this child packet,
and appends assignment events only for newly created tasks.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `MGMT-LOAD-001` | Gemini2 | Codex | Add browser route-load and BFF fanout baseline probes, with SSE-safe route-ready markers. |
| 1 | `MGMT-LOAD-002` | Claude2 | Codex | Add cheap BFF shell summary counts and canonicalize `/bff/jobs`. |
| 1 | `MGMT-LOAD-004` | Codex2 | Claude | Code split management route families so Evidence is not tied to the full console graph. |
| 2 | `MGMT-LOAD-003` | Claude | Codex | Rewire the FE shell to consume shell summary, defer full lists, and remove duplicate jobs hydration. |
| 2 | `MGMT-LOAD-005` | Gemini | Claude2 | Isolate BFF read concurrency so health and Evidence stay responsive under shell fanout. |
| 3 | `MGMT-LOAD-006` | Gemini2 | Codex | Promote the load probes into release-gate budgets and CI artifacts. |
| 4 | `MGMT-LOAD-007` | Codex | Claude | Close `MGMT-GAP-010` with merged PR, deployed FE/BFF, hosted probe, and residual-risk evidence. |

## Dependencies

```text
MGMT-LOAD-001: MGMT-GAP-001, MGMT-GAP-002
MGMT-LOAD-002: MGMT-GAP-003
MGMT-LOAD-004: MGMT-GAP-001, MGMT-LOAD-001
MGMT-LOAD-003: MGMT-LOAD-001, MGMT-LOAD-002
MGMT-LOAD-005: MGMT-LOAD-001, MGMT-LOAD-002
MGMT-LOAD-006: MGMT-LOAD-001, MGMT-LOAD-002, MGMT-LOAD-003, MGMT-LOAD-004, MGMT-LOAD-005
MGMT-LOAD-007: MGMT-LOAD-006
MGMT-GAP-010: MGMT-GAP-001, MGMT-GAP-002, MGMT-LOAD-007
```

This keeps three lanes open immediately after dispatch:

- baseline/load probe (`MGMT-LOAD-001`);
- BFF cheap shell summary (`MGMT-LOAD-002`);
- FE route splitting (`MGMT-LOAD-004`).

## Global Acceptance

Every `MGMT-LOAD-*` task must record:

1. branch and PR target;
2. local validation commands and output summary;
3. reviewer approval;
4. merge commit SHA;
5. hosted FE/BFF evidence when runtime behavior changes;
6. before/after route timing or BFF latency evidence where applicable;
7. residual risks with owner and expiry.

`MGMT-GAP-010` is not complete until `MGMT-LOAD-007` archives the final proof
and the parent task has reviewer-approved closeout evidence.
