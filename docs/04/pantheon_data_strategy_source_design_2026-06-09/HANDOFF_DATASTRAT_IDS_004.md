# DATASTRAT-IDS-004 Handoff

Task: Trainer-to-seed bridge
Owner: Codex
Reviewer: Claude2

## Delivered Scope

- Added `TrainerSeedBridge` in `services/source_ingestion` for committed
  Trainer event ingestion.
- Accepted event types are limited to `trainer_commit` and
  `explicit_seed_submission`.
- Raw or uncommitted teaching logs are refused before any store write when raw
  fields such as `message_body`, `messages`, `transcript`, or `events` are
  present.
- The bridge creates an `InteractionSourceRecord`, classifies it, applies the
  IDS-002 redaction guard, then writes a draft `StrategySpecSeed` to the
  existing seed review inbox.
- Supported seed kinds:
  `new_strategy`, `mutation`, `risk_constraint`, `execution_constraint`,
  `negative`, `persona_policy`, and `data_requirement`.
- Draft seeds record `TrainerSeedExtractionRef` lineage under
  `seed.lineage.trainer_seed_extraction_ref`.
- BFF Trainer replay commit responses now include `seed_extraction`; successful
  extraction writes a trainer-sourced draft seed readable through
  `/bff/management/strategy-seeds`.

## Safety Boundaries

- No raw prompt, transcript, or teaching event array is passed into the bridge.
- Redaction failure or archive-only/non-strategy classification blocks seed
  candidate creation.
- The bridge preserves existing seed-store invariants:
  `research_only=true`, `registry_write_performed=false`, and
  `execution_route=none`.
- BFF commit remains committed even if seed extraction is refused; the response
  reports refusal metadata instead of turning a completed Trainer commit into a
  failed replay operation.

## Verification

- `pytest services/source_ingestion/tests/test_trainer_seed_bridge.py services/source_ingestion/tests/test_ids_002_redaction_guard.py services/source_ingestion/tests/test_interaction_intent_classifier.py services/source_ingestion/tests/test_negative_memory_matcher.py services/source_ingestion/tests/test_strategy_seed_store.py -q`
  - 85 passed.
- `python3 -m py_compile services/source_ingestion/trainer_seed_bridge.py services/control-plane/bff/main.py services/control-plane/bff/test_tw04_teaching_replay_contract.py`
  - passed.
- `pytest services/control-plane/bff/test_tw04_teaching_replay_contract.py::test_tw04_commit_succeeds_and_appends_event services/control-plane/bff/test_tw04_teaching_replay_contract.py::test_tw04_commit_appended_event_visible_in_detail services/control-plane/bff/test_tw04_teaching_replay_contract.py::test_tw04_commit_rejected_on_snapshot_mismatch services/control-plane/bff/test_tw04_teaching_replay_contract.py::test_tw04_commit_rejected_when_already_committed services/control-plane/bff/test_tw04_teaching_replay_contract.py::test_tw04_commit_rejected_missing_expected_snapshot -q`
  - 5 passed, 4 deprecation warnings from existing `datetime.utcnow()` usage.
- `pytest services/control-plane/bff/test_tw04_teaching_replay_contract.py services/control-plane/bff/test_datastrat_seed_review_bff.py -q`
  - 38 passed, 12 deprecation warnings from existing `datetime.utcnow()` usage.
