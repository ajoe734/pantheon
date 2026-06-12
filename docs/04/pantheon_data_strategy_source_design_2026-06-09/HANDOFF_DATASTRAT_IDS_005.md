# Handoff: DATASTRAT-IDS-005

Owner: Codex
Reviewer: Claude2
Date: 2026-06-12
Status: ready for review

## Delivered Scope

`DATASTRAT-IDS-005` adds the governed interaction bridge for Agora,
Committee, Red Team, Postmortem, and Evolution artifacts:

- `services/source_ingestion/agora_seed_bridge.py` implements the IDS-005
  bridge.
- `services/source_ingestion/__init__.py` exports the bridge types and helper.
- `services/source_ingestion/tests/test_agora_seed_bridge.py` covers the
  acceptance paths.

The bridge accepts published `ConsultMemo`, `CommitteeVerdict`, `RedTeamMemo`,
explicit `SeedProposal`, `Postmortem`, and `EvolutionDecision` artifacts. It
refuses raw `DebateTranscript` or transcript-like artifacts before any seed
store write.

## Behavior

The bridge flow is:

```text
InteractionSourceRecord
  -> redaction / visibility guard
  -> intent classification
  -> artifact-kind gate
  -> StrategySpecSeed draft
  -> StrategySpecSeedStore review inbox
```

Raw interaction refs remain evidence-only through `raw_ref` and are recorded in
`AgoraSeedExtractionRef` with `raw_ref_role=evidence_only`. Seed content is
always built from a published memo/verdict/proposal/postmortem/evolution
artifact ref, not from the raw transcript ref.

The bridge records:

- `lineage.AgoraSeedExtractionRef`
- `metadata.AgoraSeedExtractionRef`
- source-surface and artifact-kind metadata
- deterministic intent classification summary
- trust profile and review bias
- `seed_kind` mapping for risk, execution, negative, and strategy seeds

Trust handling distinguishes committee/red-team artifacts from ordinary memos:

- committee verdicts are prioritized as reviewed governance artifacts;
- red-team memos are safety-first and remain review-required;
- ordinary consult memos use a lower trust weight and require review;
- postmortem/evolution records are treated as governed safety follow-up
  evidence.

## Validation

Focused implementation checks:

```bash
python3 -m py_compile services/source_ingestion/agora_seed_bridge.py services/source_ingestion/__init__.py
```

Result: passed.

```bash
python3 -m pytest services/source_ingestion/tests/test_agora_seed_bridge.py -q
```

Result: 6 passed in 1.02s.

Adjacent safety/materialization regression checks:

```bash
python3 -m pytest services/source_ingestion/tests/test_agora_seed_bridge.py services/source_ingestion/tests/test_ids_002_redaction_guard.py services/source_ingestion/tests/test_interaction_intent_classifier.py services/source_ingestion/tests/test_negative_memory_matcher.py services/source_ingestion/tests/test_strategy_seed_builder.py services/source_ingestion/tests/test_strategy_seed_store.py -q
```

Result: 81 passed in 8.56s.

```bash
git diff --check
```

Result: passed.

## Non-Scope

- No BFF route is added here.
- No Trainer-to-seed bridge is added; IDS-004 remains separate.
- No seed review action model, promotion workflow, or registry write is added.
- No runtime binding, deployment plan, broker route, or live execution authority
  is created.
