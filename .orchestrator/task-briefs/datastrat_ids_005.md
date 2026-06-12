# Task Brief: DATASTRAT-IDS-005

Generated in the worker workspace because the supervisor root did not have a task brief file.
Updated by Codex for review handoff on 2026-06-12.

## Task
- Title: Agora/Committee/Postmortem-to-seed bridge
- Status: review
- Owner: Codex
- Reviewer: Claude2
- Next: IDS-005 bridge implemented; ready for Claude2 review.

## Summary
ConsultMemo/CommitteeVerdict/RedTeamMemo/SeedProposal->SeedCandidate(raw transcript 僅 evidence);Postmortem/Evolution->Risk/Execution/Negative seed;記 AgoraSeedExtractionRef。

## Review Scope
- Implementation: `services/source_ingestion/agora_seed_bridge.py`
- Public exports: `services/source_ingestion/__init__.py`
- Focused tests: `services/source_ingestion/tests/test_agora_seed_bridge.py`
- Handoff artifact: `docs/04/pantheon_data_strategy_source_design_2026-06-09/HANDOFF_DATASTRAT_IDS_005.md`

## Validation
- `python3 -m py_compile services/source_ingestion/agora_seed_bridge.py services/source_ingestion/__init__.py`
- `python3 -m pytest services/source_ingestion/tests/test_agora_seed_bridge.py -q`
- `python3 -m pytest services/source_ingestion/tests/test_agora_seed_bridge.py services/source_ingestion/tests/test_ids_002_redaction_guard.py services/source_ingestion/tests/test_interaction_intent_classifier.py services/source_ingestion/tests/test_negative_memory_matcher.py services/source_ingestion/tests/test_strategy_seed_builder.py services/source_ingestion/tests/test_strategy_seed_store.py -q`
