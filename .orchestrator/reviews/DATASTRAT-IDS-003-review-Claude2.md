# Review: DATASTRAT-IDS-003 — Intent Classification (Safety)

Reviewer: Claude2
Date: 2026-06-12
Status: APPROVED

## Scope

Review of commit `3c0aedfa` (`DATASTRAT-IDS-003: add intent classifier`), covering:

- `services/source_ingestion/interaction_intent_classifier.py`
- `docs/contracts/interaction_intent_classification.schema.json`
- `services/source_ingestion/tests/test_interaction_intent_classifier.py`

## Safety Boundary — PASS

The classifier enforces the prime directive from the EPIC correctly:

- Reads governed `InteractionSourceRecord.summary` and metadata only; never reads raw interaction evidence.
- Emits no seed, performs no registry write, opens no execution route.
- This is explicit in the output metadata: `seed_created=False`, `registry_write_performed=False`, `execution_route=none`.
- All tests assert these fields; the round-trip schema test validates they are present and typed.
- `redaction_status=failed` forces `requires_human_review=True` without changing intent — correct escalation, not silent suppression.

## Acceptance Criteria Checklist (EPIC IDS-003)

| Criterion | Result |
|---|---|
| style/coaching → persona_policy (not strategy) | PASS — `test_style_coaching_beats_strategy_language` |
| investable hypothesis → strategy_hypothesis | PASS — parametrized table + dedicated test |
| low confidence → requires_human_review | PASS — `test_low_confidence_interaction_requires_review_and_no_seed_route` |
| non_strategy → archive_only | PASS — parametrized table entry + `archive_only` field |
| Test table mirrors SA test cases | PASS — all 9 intent categories covered in one parametrized test |

## Implementation Quality

- **Scoring model:** strong phrase (+4.0), weak keyword (+1.0), metadata hint (+5.0). Margin-based ambiguity detection (≤1.0 gap → human review). Confidence formula is bounded [0.20, 0.95]. No embedding dependency in v1 — correct per deferral list.
- **Signal transparency:** `matched_signals` records which phrases fired; `secondary_intents` preserves up to 3 runners-up for downstream audit.
- **Schema:** Draft-7, `additionalProperties: false`, all required fields present. Enum sets in schema match `InteractionPrimaryIntent` exactly.
- **Round-trip:** `to_dict` / `from_dict` are inverse; schema validation confirmed in `test_classification_payload_round_trips_and_validates_schema`.
- **Immutability:** `IntentClassification` is a frozen dataclass with `__post_init__` coercion — no mutation path after construction.

## Test Execution

```
python3 -m pytest services/source_ingestion/tests/test_interaction_intent_classifier.py -v
16 passed in 1.54s

python3 -m pytest services/source_ingestion/tests/ -q
434 passed, 1 skipped in 68.86s
```

No regressions in the broader `source_ingestion` suite.

## Minor Notes (non-blocking)

- The commit trailer carries `Reviewer: Claude` (original assignment before re-assignment to Claude2). This is a process artifact from the auth-failure reassignment, not a defect in the implementation.
- The `NON_STRATEGY` strong signals include "hello" / "thanks" which also appear as weak signals — redundant but harmless.

## Decision

**APPROVED.** The IDS-003 safety layer is correctly scoped, passes all acceptance cases, and introduces no execution path or seed creation side-effect. Returning to owner (Codex) for closeout finalization.
