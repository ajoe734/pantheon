# Review: DATASTRAT-IDS-005

Reviewer: Claude2
Date: 2026-06-12
Status: approved

## Scope Reviewed

- `services/source_ingestion/agora_seed_bridge.py`
- `services/source_ingestion/__init__.py`
- `services/source_ingestion/tests/test_agora_seed_bridge.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/HANDOFF_DATASTRAT_IDS_005.md`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/EPIC_INTERACTION_DERIVED_SEEDS.md`

## Acceptance Criteria Check

### Prime directive (raw transcript stays evidence-only)

- `_FORBIDDEN_TRANSCRIPT_KINDS` blocks `debate_transcript`, `raw_debate_transcript`,
  `transcript`, `chat_transcript`, `messages`, and variants at the artifact-kind gate.
- `_artifact_ref` enforces that the resolved artifact ref must differ from
  `record.raw_ref`; raises `AgoraSeedBridgeError` when only `raw_ref` is available.
- IDS-002 redaction guard (`apply_redaction` + `guard_seed_candidate`) is invoked
  before any classification or seed build, blocking inline raw-transcript content at
  the redaction boundary.
- Covered by `test_raw_debate_transcript_is_refused_before_store_write` and
  `test_inline_raw_transcript_summary_fails_redaction_before_store_write`.

### Artifact kinds accepted

`AgoraSeedArtifactKind` covers all required types: `ConsultMemo`, `CommitteeVerdict`,
`RedTeamMemo`, `SeedProposal`, `Postmortem`, `EvolutionDecision`. Alias map is
comprehensive. Surface-based fallback correctly infers `postmortem`, `red_team_memo`,
`committee_verdict` from `InteractionSourceSurface`.

### AgoraSeedExtractionRef lineage recorded

Both `lineage["AgoraSeedExtractionRef"]` and `metadata["AgoraSeedExtractionRef"]`
are set with `raw_ref_role=evidence_only`, `artifact_ref`, `artifact_kind`, and
`seed_kind`. `promotion_requires` includes `seed_review_inbox`. Verified by
`test_committee_verdict_enters_review_store_with_agora_lineage`.

### Trust handling

| Artifact kind       | source_weight | requires_human_review | review_bias              |
|---------------------|:-------------:|:---------------------:|--------------------------|
| committee_verdict   | 0.90          | classifier decides    | committee_prioritized    |
| red_team_memo       | 0.88          | always True           | safety_first             |
| seed_proposal       | 0.78          | always True           | explicit_submission      |
| postmortem          | 0.92          | classifier decides    | postincident_safety      |
| evolution_decision  | 0.90          | classifier decides    | governed_evolution       |
| ordinary memo       | 0.65          | always True           | ordinary_memo_requires_review |

Trust differentiation verified by `test_trust_profile_distinguishes_ordinary_memo_from_red_team`.

### Postmortem/Evolution -> seed kind mapping

- Postmortem -> `NEGATIVE` (when classifier intent is `NEGATIVE_MEMORY` or artifact fallback).
- EvolutionDecision -> `RISK_CONSTRAINT` (artifact fallback in `_seed_kind`).
- Also governed by classifier intent first: `RISK_OVERLAY`, `EXECUTION_POLICY`,
  `NEGATIVE_MEMORY`, etc. are checked before the artifact-kind fallback.
- Verified by `test_postmortem_and_evolution_map_to_risk_execution_negative_seed_kinds`.

### Safety pipeline (IDS-002 / IDS-003 / IDS-007)

`apply_redaction` → `guard_seed_candidate` (IDS-002), then `InteractionIntentClassifier`
(IDS-003), then `_builder.build_seed(..., negative_memory_records=...)` (IDS-007).
All three guard layers are exercised before any store write.

### Registry invariants

Seed metadata sets `research_only=True`, `execution_route=none`,
`registry_write_performed=False`. All correct.

## Validation Run (by reviewer)

```
python3 -m pytest services/source_ingestion/tests/test_agora_seed_bridge.py -q
```

Result: 6 passed in 0.98s. Confirms all IDS-005 acceptance paths.

## Verdict

Implementation is complete and correct. All EPIC IDS-005 acceptance criteria are met.
Approved and returned to Codex for closeout.
