# Task Brief: DATASTRAT-IDS-004

Generated in the worker workspace because the supervisor root did not have a task brief file.
Updated by Codex for owner closeout on 2026-06-12.

## Task
- Title: Trainer-to-seed bridge
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Owner closeout validation complete; pending closeout PR merge before `done`.

## Summary
消費 committed Trainer event(trainer_commit/explicit_seed_submission,絕不收 raw)->InteractionSourceRecord->classify->redact->SeedCandidate;支援多種 seed_kind;記 TrainerSeedExtractionRef。

## Review Scope
- Implementation: `services/source_ingestion/trainer_seed_bridge.py`
- Public exports: `services/source_ingestion/__init__.py`
- BFF integration: `services/control-plane/bff/main.py`
- Focused tests:
  `services/source_ingestion/tests/test_trainer_seed_bridge.py`,
  `services/control-plane/bff/test_tw04_teaching_replay_contract.py`
- Handoff artifact:
  `docs/04/pantheon_data_strategy_source_design_2026-06-09/HANDOFF_DATASTRAT_IDS_004.md`
- Review artifact:
  `docs/04/pantheon_data_strategy_source_design_2026-06-09/REVIEW_DATASTRAT_IDS_004.md`

## Closeout Record
- Closeout owner: Codex.
- Current disposition: reviewer approved; owner closeout validation complete;
  pending closeout PR merge before `done`.
- Review approval: Claude2 approved all acceptance criteria in
  `REVIEW_DATASTRAT_IDS_004.md`.
- Composed with latest `origin/dev` before final closeout commit, including
  DATASTRAT-IDS-005 closeout artifacts.
- Validation:
  - `python3 -m pytest services/source_ingestion/tests/test_trainer_seed_bridge.py services/source_ingestion/tests/test_agora_seed_bridge.py services/source_ingestion/tests/test_ids_002_redaction_guard.py services/source_ingestion/tests/test_interaction_intent_classifier.py services/source_ingestion/tests/test_negative_memory_matcher.py services/source_ingestion/tests/test_strategy_seed_store.py -q` - 94 passed.
  - `python3 -m pytest services/control-plane/bff/test_tw04_teaching_replay_contract.py services/control-plane/bff/test_datastrat_seed_review_bff.py -q` - 38 passed, 12 existing `datetime.utcnow()` deprecation warnings from `services/control-plane/bff/read_store.py`.
  - `python3 -m py_compile services/source_ingestion/trainer_seed_bridge.py services/source_ingestion/agora_seed_bridge.py services/control-plane/bff/main.py services/control-plane/bff/test_tw04_teaching_replay_contract.py` - passed.
  - `git diff --check` - passed.
