# Task Brief: DATASTRAT-IDS-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora/Committee/Postmortem-to-seed bridge
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved by Claude2 on 2026-06-12. All EPIC IDS-005 acceptance criteria met: raw transcript blocked, AgoraSeedExtractionRef lineage recorded, trust differentiation correct, safety pipeline (IDS-002/003/007) applied, 6 tests pass. Review artifact: docs/04/pantheon_data_strategy_source_design_2026-06-09/REVIEW_DATASTRAT_IDS_005.md. Returned to Codex for closeout.

## Summary
ConsultMemo/CommitteeVerdict/RedTeamMemo/SeedProposal->SeedCandidate(raw transcript 僅 evidence);Postmortem/Evolution->Risk/Execution/Negative seed;記 AgoraSeedExtractionRef。

## Closeout Record
- Closeout owner: Codex.
- Current disposition: reviewer approved; owner closeout validation complete; pending PR merge before `done`.
- Review artifact: docs/04/pantheon_data_strategy_source_design_2026-06-09/REVIEW_DATASTRAT_IDS_005.md.
- Composed with latest `origin/dev` before final closeout commit.
- Validation:
  - `python3 -m py_compile services/source_ingestion/agora_seed_bridge.py services/source_ingestion/__init__.py` — passed.
  - `python3 -m pytest services/source_ingestion/tests/test_agora_seed_bridge.py services/source_ingestion/tests/test_ids_002_redaction_guard.py services/source_ingestion/tests/test_interaction_intent_classifier.py services/source_ingestion/tests/test_negative_memory_matcher.py services/source_ingestion/tests/test_strategy_seed_builder.py services/source_ingestion/tests/test_strategy_seed_store.py services/source_ingestion/tests/test_trainer_seed_bridge.py services/control-plane/bff/test_datastrat_seed_review_bff.py -q` — 103 passed, 4 existing `datetime.utcnow()` deprecation warnings from `services/control-plane/bff/read_store.py`.
  - `git diff --check` — passed.
