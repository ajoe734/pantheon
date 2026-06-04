# MGMT-AI-PERSIST-P0-STORE-001 Closeout Evidence

Date: 2026-06-04
Owner: Codex
Reviewer: Claude

## Delivered Scope

The task delivered the shared Management AI `AssistantConversationStore`
substrate with:

- JSON and in-memory dev backends.
- Environment-gated `MANAGEMENT_AI_STORE_BACKEND` selection.
- Postgres backend using `PostgresJsonOwnerStore` for JSONB owner records.
- `management_ai` schema bootstrap for sessions, ordered turns,
  idempotency records, and assistant-session metadata.
- Relational turn storage with full text and JSONB payload fields.
- Staging/prod persistence posture requiring Postgres for `operator-bff`.

## Publication

- Implementation PR: #875
- Implementation merge commit: `9ed9ebab0d6930d8429272518282f427c1c3150c`
- Review closeout PR: #877
- Review closeout merge commit: `0699853d2c66700ffdc1140b031d81fa54610b23`

## Verification

Local closeout validation run by Codex:

```bash
python3 -m py_compile services/control-plane/bff/assistant_conversation_store.py services/control-plane/bff/management_ai_store.py services/control-plane/bff/main.py
python3 -m pytest services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py services/control-plane/bff/tests/test_assistant_sessions.py::TestManagementAiBackedAssistantStores services/control-plane/bff/tests/test_assistant_dev_compose_flags.py services/foundation/tests/test_persistence_posture.py services/control-plane/bff/tests/test_management_nl_assistant_provider.py services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py -q -k "conversation_store_persists or persists_30 or idempotency_replay or uses_server_history or missing_session or attachment or TestManagementAiBackedAssistantStores or dev_compose or staging_live_operator_bff or json_assistant_conversation_store or postgres_assistant_conversation_store"
git diff --check
```

Result:

- `py_compile`: passed
- focused pytest: `13 passed, 1 skipped, 45 deselected`
- `git diff --check`: passed
- GitHub Branch CI Gate on PR #877: Commit trailers, Runtime mirror guard,
  and Smoke acceptance passed before merge.
