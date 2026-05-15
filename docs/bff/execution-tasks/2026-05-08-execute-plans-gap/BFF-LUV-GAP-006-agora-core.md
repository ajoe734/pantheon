# BFF-LUV-GAP-006 - Agora Core BFF Compatibility

Priority: P0

Area: Agora daily, signals, sessions, notes, journal, insights, memory

## Goal

Implement active Agora BFF routes required by Part 06 and by current `execute-plans/src/lib/v3` helpers.

## Missing Routes

Daily and signals:

- `GET /bff/agora/daily`
- `GET /bff/agora/signals`
- `GET /bff/agora/signals/{signalId}`
- `POST /bff/agora/signals/{signalId}/feedback`
- `GET /bff/agora/watchlist`

Sessions and messages:

- `GET /bff/agora/sessions`
- `POST /bff/agora/sessions`
- `GET /bff/agora/sessions/{sessionId}`
- `GET /bff/agora/sessions/{sessionId}/messages`
- `POST /bff/agora/sessions/{sessionId}/messages`
- `POST /bff/agora/messages/{messageId}/actions/{actionId}`

Notes, journal, insights:

- `GET /bff/agora/notes`
- `POST /bff/agora/notes`
- `GET /bff/agora/journal`
- `POST /bff/agora/journal`
- `GET /bff/agora/insights`
- `POST /bff/agora/insights`
- `POST /bff/agora/insights/{insightId}/actions/{actionId}`

Memory and training:

- `GET /bff/agora/memory`
- `POST /bff/agora/memory/{memoryId}/actions/{actionId}`
- `GET /bff/agora/training-examples`
- `POST /bff/agora/training-examples`
- `GET /bff/research/tasks`
- `POST /bff/memory/{memoryId}/actions/quarantine`
- `POST /bff/insights/{insightId}/actions/attach-strategy`

## Implementation Notes

- `PATCH /bff/agora/journal/{id}` already exists; keep its JSON Merge Patch semantics.
- Use existing consultation/workbench read-store data where possible.
- Actions must emit audit records and use command envelopes when side effects are non-trivial.

## Acceptance Criteria

- Current `execute-plans/src/lib/v3/agoraKpi.ts`, `signalFeedback.ts`, and `committeeEvidence.ts` calls no longer need mock-only fallback for the listed core routes.
- Route tests cover list/detail, feedback, note/journal creation, and error envelopes.
- Existing journal patch tests remain green.

## Implementation Evidence

Implemented in `services/control-plane/bff/main.py`:

- Agora daily, signal list/detail/feedback, watchlist, session/message list/create/action routes.
- Agora notes, journal list/create, insights list/create/action, memory list/action, training examples list/create.
- Compatibility aliases for `GET /bff/research/tasks`, `POST /bff/memory/{memoryId}/actions/quarantine`, and `POST /bff/insights/{insightId}/actions/attach-strategy`.

Supporting updates:

- `services/control-plane/bff/read_store.py` has Agora core local overlay helpers for signals, sessions, notes, journal, insights, memory, training examples, and audit records.
- `services/control-plane/bff/models.py` includes Agora command/object enum values.
- `services/control-plane/bff/action_catalog.py` includes `AgoraSignalFeedback`, `AgoraMessageAction`, `AgoraInsightAction`, and `AgoraMemoryAction`.
- `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json` marks the BFF-LUV-GAP-006 rows implemented.
- `services/control-plane/bff/test_bff_agora_core_contract.py` covers read routes, feedback validation/idempotency, note/journal/insight/training creation, action command envelopes, and missing-object error envelopes.

Focused verification:

```bash
python3 -m pytest services/control-plane/bff/test_bff_agora_core_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py services/control-plane/bff/test_action_catalog.py services/control-plane/bff/test_agora_journal_merge_patch.py -q
```

Result: `25 passed, 6 warnings`. Warnings are the pre-existing `datetime.utcnow()` deprecation warnings from `read_store.py`.
