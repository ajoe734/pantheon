# Persona Promotion Governance Gap Execution Packet - 2026-07-05

Status: ready for fleet dispatch and implementation
Source gap spec:

- `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md`

Policy references:

- `PAPER_CANARY_LIVE_POLICY.md`
- `PERSONA_RUNTIME_MODEL.md`

## Dispatch Command

```sh
python3 scripts/dispatch_persona_promotion_governance_2026-07-05.py
python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It preserves progress fields for already
started tasks, assigns unfinished tasks to their owner lanes, and records the
gap spec as the source of truth.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `PPL-GOV-001` | Codex | Claude | Lock the gap spec, current-state audit, and acceptance rules. |
| 1 | `PPL-GOV-002` | Claude2 | Codex | Add BFF promotion-review list/detail/decision routes. |
| 1 | `PPL-GOV-003` | Gemini2 | Codex | Add BFF recommendation submit bridge into governance/Human Inbox. |
| 2 | `PPL-GOV-004` | Codex2 | Claude | Wire Persona League and Quarterly Ranking recommendation submit UI to BFF. |
| 2 | `PPL-GOV-005` | Claude | Codex | Finish Human Inbox / Human Gate promotion-review decision UX. |
| 2 | `PPL-GOV-006` | Gemini | Claude2 | Encode emergency risk disposal rules without promotion side effects. |
| 3 | `PPL-GOV-007` | Codex | Claude | Close production validation, PR merge, dev publish, and hosted smoke evidence. |

## Dependencies

```text
PPL-GOV-001: none
PPL-GOV-002: PPL-GOV-001
PPL-GOV-003: PPL-GOV-001
PPL-GOV-004: PPL-GOV-002, PPL-GOV-003
PPL-GOV-005: PPL-GOV-002, PPL-GOV-004
PPL-GOV-006: PPL-GOV-001
PPL-GOV-007: PPL-GOV-002, PPL-GOV-003, PPL-GOV-004, PPL-GOV-005, PPL-GOV-006
```

## Global Acceptance

Every `PPL-GOV-*` task must record:

1. branch and PR target;
2. local validation commands and output summary;
3. reviewer approval or explicit blocker;
4. merge commit SHA when merged;
5. hosted FE/BFF evidence when runtime behavior changes;
6. residual risk with owner and expiry.

The packet is not complete until `PPL-GOV-007` proves the full path:

```text
recommendation -> submit -> promotion review -> human decision -> auditable receipt
```

No task may claim recommendation submission directly mutates live capital.

## User-Facing Product Answer

After this packet lands, the management locations should be:

- Recommendation source:
  `/management/persona-league` and `/management/quarterly-ranking`.
- Approval queue:
  `/management/human-inbox`, filtered to `promotion_review`.
- Decision surface:
  `/management/human-inbox/{promotion_review_id}` with
  approve / approve-with-conditions / reject controls.

The recommendation engine ranks all paper, canary, and live personas together,
but stage gates decide what an action means. Paper can only request canary.
Canary can request live. Live can request capital rank changes, demotion, freeze,
suspend, or retirement.
