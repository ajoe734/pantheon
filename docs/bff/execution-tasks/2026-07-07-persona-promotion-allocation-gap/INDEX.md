# Persona Promotion And Allocation Gap Execution Packet - 2026-07-07

Status: ready for fleet dispatch and implementation

Source gap spec:

- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`

Extends:

- `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md`
- `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/archive/PPL-GOV-007-PRODUCTION-CLOSEOUT-2026-07-05.md`

## Dispatch Command

```sh
python3 scripts/dispatch_persona_promotion_allocation_2026-07-07.py
python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It preserves progress fields for already
started tasks, assigns unfinished tasks to their owner lanes, and records this
gap spec as the source of truth.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `PPL-ALLOC-001` | Codex | Claude | Lock current-state audit, page inventory, and acceptance rules. |
| 1 | `PPL-ALLOC-002` | Claude2 | Codex | Add atomic persona create-paper-bundle BFF command and tests. |
| 1 | `PPL-ALLOC-003` | Gemini2 | Claude | Normalize paper ledger, canary sleeve, live sleeve/pool binding read models. |
| 1 | `PPL-ALLOC-004` | Gemini | Claude2 | Implement stage-aware ranking, target-weight policy, and rebalance proposal contract. |
| 2 | `PPL-ALLOC-005` | Codex2 | Claude | Replace generic persona create UI with Create Paper Persona flow. |
| 2 | `PPL-ALLOC-006` | Claude | Codex | Expand Promotion & Allocation into the primary operator workbench. |
| 2 | `PPL-ALLOC-007` | Antigravity | Codex2 | Fix capital/persona binding visibility and legacy page routing. |
| 2 | `PPL-ALLOC-008` | Antigravity2 | Claude2 | Implement emergency containment policy and UI/BFF guards. |
| 3 | `PPL-ALLOC-009` | Codex | Claude | Close with PRs, tests, dev publish, hosted smoke, and residual risks. |

## Dependencies

```text
PPL-ALLOC-001: none
PPL-ALLOC-002: PPL-ALLOC-001
PPL-ALLOC-003: PPL-ALLOC-001
PPL-ALLOC-004: PPL-ALLOC-001, PPL-ALLOC-003
PPL-ALLOC-005: PPL-ALLOC-002, PPL-ALLOC-003
PPL-ALLOC-006: PPL-ALLOC-003, PPL-ALLOC-004
PPL-ALLOC-007: PPL-ALLOC-003, PPL-ALLOC-006
PPL-ALLOC-008: PPL-ALLOC-001, PPL-ALLOC-004
PPL-ALLOC-009: PPL-ALLOC-002, PPL-ALLOC-003, PPL-ALLOC-004, PPL-ALLOC-005, PPL-ALLOC-006, PPL-ALLOC-007, PPL-ALLOC-008
```

## Global Acceptance

Every `PPL-ALLOC-*` task must record:

1. branch and PR target;
2. local validation commands and output summary;
3. reviewer approval or explicit blocker;
4. merge commit SHA when merged;
5. hosted FE/BFF evidence when runtime behavior changes;
6. residual risk with owner and expiry.

The packet is not complete until `PPL-ALLOC-009` proves the full path:

```text
create persona -> paper_running bundle
paper ranking -> promotion review -> human decision
real ranking -> target weights -> rebalance proposal -> human approval -> apply receipt
emergency breach -> containment action without promotion/increase side effect
```

No task may claim recommendation submission, promotion approval, or emergency
containment directly mutates live capital without the required governed apply
command and audit receipt.

## Product Routing Contract

The primary operator entry is:

- `/management/promotion-allocation`

Supporting pages:

- `/management/persona-fleet`
- `/management/personas`
- `/management/human-inbox`
- `/management/capital`
- `/management/rebalance/:id`

Diagnostic/readiness pages must not present themselves as the approval or
allocation source of truth:

- `/management/ranking`
- `/management/readiness/capital-binding-live`

Legacy routes remain redirects only:

- `/management/persona-league`
- `/management/quarterly-ranking`
- `/management/rebalance`
- `/management/rebalances`
