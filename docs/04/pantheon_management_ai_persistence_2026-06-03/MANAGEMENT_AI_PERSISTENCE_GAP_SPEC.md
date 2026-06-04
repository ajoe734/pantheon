# Management AI Persistence Gap Spec

Date: 2026-06-03
Task: MGMT-AI-PERSIST-P0-STORE-001

## Requirement

Management AI conversation truth must survive BFF restarts and scale across BFF workers without a second assistant transcript store. Local dev may use a simple json/in-memory backend, but staging and production must use the shared Postgres substrate.

## Store Contract

`AssistantConversationStore` exposes:

- `create_session`
- `get_session`
- `list_sessions`
- `touch_session`
- `append_turn`
- `list_turns`
- `put_idempotency`
- `get_idempotency`

The Management AI wrapper keeps the existing handler-facing methods while delegating to the shared substrate.

## Backend Selection

- `MANAGEMENT_AI_STORE_BACKEND=json`: dev JSON file or in-memory store.
- `MANAGEMENT_AI_STORE_BACKEND=postgres`: shared Postgres store.
- `MANAGEMENT_AI_STORE_SCHEMA`: defaults to `management_ai`.
- `MANAGEMENT_AI_STORE_SURFACE`: defaults to `assistant_conversation`.
- `MANAGEMENT_AI_STORE_DSN`: optional DSN override; otherwise `DATABASE_URL` is used.

`services/foundation/persistence_posture.py` registers `operator-bff`, `bff`, and `control-plane-bff` so staging/prod reject dev-only backends.

## Postgres Layout

The default Postgres surface creates:

- `management_ai.assistant_conversation_sessions`
- `management_ai.assistant_conversation_turns`
- `management_ai.assistant_conversation_idempotency`
- `management_ai.assistant_conversation_assistant_sessions`

Sessions, idempotency, and assistant-session metadata use `PostgresJsonOwnerStore`. Turns use a relational table with:

- `turn_id TEXT PRIMARY KEY`
- `session_id TEXT REFERENCES ... ON DELETE CASCADE`
- `text TEXT NOT NULL`
- JSONB columns for attachments, provider status, UI snapshot/actions, and assistant metadata
- `created_at TIMESTAMPTZ`
- `sequence BIGSERIAL`
- index on `(session_id, created_at, sequence)`

Turn text is stored in full. Summaries may still truncate audit excerpts, but the conversation store does not.

## Acceptance Evidence

Focused tests live in `services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py`.

- JSON backend tests run unconditionally and verify restart durability.
- Postgres backend test runs only when `TEST_DATABASE_URL` is set and otherwise skips with an explicit message.
- Posture regression verifies staging-live rejects `MANAGEMENT_AI_STORE_BACKEND=json` for `operator-bff`.
