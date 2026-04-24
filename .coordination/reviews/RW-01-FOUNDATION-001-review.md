# Review: RW-01-FOUNDATION-001

**Reviewer:** Claude  
**Date:** 2026-04-19  
**Task:** Publish Research Ticket identity and lifecycle foundation  
**Owner:** Codex2  
**Status:** APPROVED

## Artifacts Reviewed

- `docs/bff/RW-01-research-ticket.md` — BFF contract
- `docs/examples/RW-01-research-ticket.json` — example payloads
- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` — workbench overview

## Acceptance Criteria

| Criterion | Result |
|---|---|
| Research ticket identity and lifecycle contract are published | ✓ PASS — `docs/bff/RW-01-research-ticket.md` published with all four routes |
| Overview can hand off to a truthful list and detail flow | ✓ PASS — PACKET_FAMILY.md updated to `contract-published`; ticket_id is the canonical anchor |
| Future RW-02 to RW-05 have a stable upstream anchor | ✓ PASS — dependency chain documented; `GET /api/v1/research/tickets/{ticket_id}` is the upstream anchor for all downstream modules |

## Route Shape

All four required routes are present and correctly specified:

- `POST /api/v1/research/tickets` — create with required body fields and response shape ✓
- `GET /api/v1/research/tickets` — list with pagination and filter params (`status`, `owner`, `page_token`, `page_size`) ✓
- `GET /api/v1/research/tickets/{ticket_id}` — detail with `lifecycle_history[]`, `linked_experiments[]`, `linked_artifacts[]` ✓
- `PATCH /api/v1/research/tickets/{ticket_id}` — lifecycle transition with state machine validation ✓

## Lifecycle and allowedActions

State machine `open → in_progress → closed → archived` is correct. The three `allowedActions` invariants are properly specified and verified against the example payload:

- `canClose: false` when `closed` or `archived` ✓
- `canArchive: false` when `open` or `in_progress` ✓  
- `canEdit: false` when `archived` ✓
- Invalid state hop rejection explicitly required ✓

## Example Payload Consistency

All example payloads are consistent with the contract:

- `create_response` returns `status: "open"`, `canArchive: false` ✓
- `list_response` closed ticket has `canEdit: false`, `canClose: false`, `canArchive: true` ✓
- `patch_close_response` has `status: "closed"`, correct allowedActions ✓

## Degradation and Write Authority

- Degradation rules reference PKT-005 substrate correctly ✓
- `meta.surfaces.ticket_list` and `meta.surfaces.ticket_detail` staleness signals defined ✓
- Write authority is scoped to tickets only; experiment, artifact, and search writes excluded ✓
- `linked_experiments[]` and `linked_artifacts[]` are read-only BFF projections ✓

## Minor Observation (non-blocking)

The state machine notation implies a strict linear path (`open → in_progress → closed → archived`), but the example shows `canClose: true` for `open` status tickets, implying direct `open → closed` transition is permitted (without going through `in_progress`). The contract explicitly forbids only `open → archived` as a skip. This is internally consistent — the example payload governs — but a one-line clarifying note in the contract would strengthen it for future implementors. Not a blocker.

## Disposition

**APPROVED** — all three acceptance criteria met. Contract is coherent, field shapes are correct, downstream anchor is established. RW-01 is contract-published and ready for BFF implementation; RW-02 through RW-05 may proceed once this is live.
