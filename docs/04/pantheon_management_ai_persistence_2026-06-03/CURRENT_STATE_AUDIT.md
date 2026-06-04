# Management AI Persistence Current State Audit

Date: 2026-06-03
Task: MGMT-AI-PERSIST-P0-STORE-001

## Scope

This audit covers the BFF Management AI conversation path:

- `POST /bff/management/nl/ask`
- `GET /bff/management/ai/conversations`
- `GET /bff/management/ai/conversations/{sessionId}`
- `/bff/assistant/sessions/{sessionId}/transcript`

## Pre-Change State

- Management AI sessions and turns were owned by `ManagementAiConversationStore`.
- Assistant session/transcript routes were already adapted to the Management AI conversation store, so assistant transcript readback did not need a second in-memory truth.
- The BFF still had local-dev persistence assumptions: file-backed conversation state, local attachment objects, jsonl audit append, and module-level idempotency cache.
- Staging/prod posture did not have an `operator-bff` entry enforcing a Postgres Management AI store.

## Gap

The missing P0 substrate was a BFF-owned store that:

- exposes a stable handler-facing interface for sessions, turns, touch, and idempotency;
- defaults to local json/in-memory for dev;
- switches to Postgres under `MANAGEMENT_AI_STORE_BACKEND=postgres`;
- reuses `services/foundation/postgres_json_store.py` for KV records instead of adding another psycopg abstraction;
- stores ordered turns in a relational table so multiple BFF workers can append/read one source of truth.

## Delivered Boundary

`services/control-plane/bff/assistant_conversation_store.py` now owns that substrate. It stores:

- sessions in a JSONB owner table;
- turns in a relational Postgres table with JSONB payload columns and `(session_id, created_at, sequence)` index;
- idempotency records in a JSONB owner table;
- assistant-session metadata in a JSONB owner table.

The store is surface-parameterized by schema and surface name, with `management_ai` as the default schema.
