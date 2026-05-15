# BFF-LUV-GAP-006 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-GAP-006-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-GAP-006
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Codex
Prepared: 2026-05-08T17:24:23Z

## Scope

This is a support-only sidecar for the BFF-LUV-GAP-006 parent implementation. It does not define canonical architecture, update route truth, or change runtime behavior. The parent owner should use it as a short handoff packet when deciding how to finish and verify Agora core BFF compatibility for the current `execute-plans` frontend.

## Current Evidence Snapshot

Commands run from `/home/lupin/code/pantheon`:

```bash
jq '.entries[] | select(.family=="agora-core")' services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json
python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
rg -n "@app\\.(get|post|patch).*bff/agora|def bff_agora|agora_daily|agora_signals|agora_sessions|agora_notes|agora_journal|agora_insights|agora_memory|research_tasks|quarantine|attach_strategy" services/control-plane/bff/main.py
rg -n "agoraKpi|signalFeedback|committeeEvidence|/bff/agora|/bff/research/tasks|/bff/memory|/bff/insights" /home/lupin/code/execute-plans/src -g '*.ts' -g '*.tsx'
```

Findings:

- The coverage report currently shows `agora-core` at `1 implemented / 25 missing`.
- The only exposed FastAPI route in this family is `PATCH /bff/agora/journal/{entry_id}`.
- `services/control-plane/bff/read_store.py` already contains reusable Agora helpers for signals, feedback, watchlist, sessions, notes, decision journal list/create, insights, memory, training examples, and Agora audit events.
- `services/control-plane/bff/models.py` has `CommandType` values for `AgoraSignalFeedback`, `AgoraMessageAction`, `AgoraInsightAction`, and `AgoraMemoryAction`, but the focused search found no matching `action_catalog.py` entries yet.
- The parent task should keep `PATCH /bff/agora/journal/{id}` JSON Merge Patch semantics unchanged.

## Frontend Demand Map

| Frontend source | Operator journey | Active BFF demand |
|---|---|---|
| `/home/lupin/code/execute-plans/src/lib/v3/agoraKpi.ts` | Daily Brief KPI rail | `GET /bff/agora/watchlist`, `GET /bff/agora/signals`, `GET /bff/agora/daily`, `GET /bff/research/tasks` |
| `/home/lupin/code/execute-plans/src/agora/pages/DailyBrief.tsx` | Operator scans daily brief, signals, pending actions, and notes | Same KPI sources above; note save should land on `POST /bff/agora/notes` or `POST /bff/agora/journal` depending whether it is free-form research note or decision journal |
| `/home/lupin/code/execute-plans/src/lib/v3/signalFeedback.ts` | Operator agrees, disagrees, or flags a signal | `POST /bff/agora/signals/{signalId}/feedback` with 1-5 confidence and conditional reason validation |
| `/home/lupin/code/execute-plans/src/agora/pages/SignalReview.tsx` | Signal review list and detail | `GET /bff/agora/signals`, `GET /bff/agora/signals/{signalId}`, feedback POST above |
| `/home/lupin/code/execute-plans/src/lib/v3/agoraHandoff.ts` | Agora content becomes management-console work | Sessions, messages, notes, insights, memory, and training-example routes need stable ids and source refs |
| `/home/lupin/code/execute-plans/src/lib/v3/medium-low/B2-entities.ts` | Insight to strategy association | `POST /bff/insights/{insightId}/actions/attach-strategy` |
| `/home/lupin/code/execute-plans/src/lib/v3/medium-low/B5-misc.ts` | Memory quarantine workflow | `POST /bff/memory/{memoryId}/actions/quarantine` |

Out of scope for this parent slice:

- `/bff/agora/committee/{sessionId}/evidence-pack`
- `/bff/agora/committee/{sessionId}/evidence-pack/files`
- `/bff/agora/persona-lab/{draftId}/actions/submit-commit`

Those are mapped to BFF-LUV-GAP-007 as `agora-extended` in the route snapshot even though the current Committee Room page references the evidence-pack upload flow.

## Parent Absorption Checklist

The parent BFF implementation can finish this gap without changing canonical truth by absorbing these concrete work items:

1. Add FastAPI compatibility routes for all BFF-LUV-GAP-006 `agora-core` snapshot entries.
2. Wire read-only routes to existing `read_store.py` helpers where available:
   - `list_agora_signals`, `get_agora_signal`, `list_agora_watchlist`
   - `list_agora_sessions`, `get_agora_session`, `list_agora_session_messages`
   - `list_agora_notes`, `list_decision_journal_entries`, `list_agora_insights`
   - `list_agora_memory`, `list_agora_training_examples`
   - `list_research_tickets(statuses=["new", "triaged"])` for `/bff/research/tasks`
3. Wire create/action routes to BFF-local dev-store helpers only where the helper already exists, and preserve explicit canonical write-authority metadata in the response where the store helper returns it.
4. Add action catalog entries for the Agora command types before relying on `/bff/v1/commands` or command-envelope execution:
   - `AgoraSignalFeedback`
   - `AgoraMessageAction`
   - `AgoraInsightAction`
   - `AgoraMemoryAction`
5. Emit audit records for feedback and action routes through the existing `record_agora_audit_event` helper or the existing final-command audit path.
6. Update `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json` only after routes are implemented and covered by tests.
7. Keep unsupported long-tail Agora routes deferred to BFF-LUV-GAP-007 instead of silently widening BFF-LUV-GAP-006.

## Route-Level Notes

| Route group | Current support in repo | Handoff note |
|---|---|---|
| Daily and KPI sources | Watchlist/signals helpers exist; daily aggregate route does not | `GET /bff/agora/daily` should aggregate daily notes, persona briefs, and KPI source counts without inventing canonical state. |
| Signal list/detail/feedback | Signals and feedback helpers exist | Feedback should validate confidence 1-5; `disagree` with high confidence and `flag_suspicious` need a reason per frontend v3 helper. |
| Sessions/messages | Session create/list/get/message helpers exist | Message actions should be explicit action ids such as `create-note`, `create-insight`, or `create-training-example`; unknown actions should return the standard BFF error envelope. |
| Notes/journal | Research-note helpers and journal list/create/patch helpers exist | Preserve the existing `PATCH /bff/agora/journal/{id}` RFC 7396 merge-patch behavior. |
| Insights | List/create helpers exist | Generic insight actions should either map to explicit supported actions or fail with a clear unsupported-action BFF error. |
| Memory/training | List/get/create helpers exist | `POST /bff/memory/{memoryId}/actions/quarantine` should mark/reply with `status: quarantined` and create or reference a review task when the parent implements it. |
| Research tasks | `/api/v1/research/tickets` exists | `/bff/research/tasks` can be a compatibility alias/projection of research tickets filtered to `new` and `triaged`. |

## Suggested Focused Tests

Add or extend a focused test file for BFF-LUV-GAP-006, then run it with the existing journal patch test:

```bash
python3 -m pytest \
  services/control-plane/bff/test_bff_agora_core_contract.py \
  services/control-plane/bff/test_agora_journal_merge_patch.py
```

Minimum assertions:

- `GET /bff/agora/signals` returns a BFF envelope or stable list shape and supports `reviewStatus=pending_trader_review`.
- `GET /bff/agora/signals/{signalId}` returns 404 with the standard BFF error envelope for missing ids.
- `POST /bff/agora/signals/{signalId}/feedback` accepts valid feedback, rejects invalid confidence, requires a reason for `flag_suspicious`, updates the signal review status, and writes an audit/event record.
- `GET /bff/agora/sessions`, `POST /bff/agora/sessions`, `GET /bff/agora/sessions/{sessionId}/messages`, and `POST /bff/agora/sessions/{sessionId}/messages` cover list/detail/create/message append.
- `GET /bff/agora/notes` and `POST /bff/agora/notes` cover free-standing note creation.
- `GET /bff/agora/journal`, `POST /bff/agora/journal`, and the existing patch tests remain green.
- `GET /bff/agora/insights`, `POST /bff/agora/insights`, `GET /bff/agora/memory`, `GET/POST /bff/agora/training-examples`, `GET /bff/research/tasks`, and the two non-Agora compatibility action routes are covered.
- `python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py` shows `agora-core` missing count at `0` after registry update.

## Reviewer Handoff

Reviewer should check that this packet stays support-only and that the parent owner can trace every suggestion back to:

- `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-006-agora-core.md`
- `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`
- `/home/lupin/code/execute-plans/src/lib/v3/agoraKpi.ts`
- `/home/lupin/code/execute-plans/src/lib/v3/signalFeedback.ts`
- `/home/lupin/code/execute-plans/src/agora/pages/DailyBrief.tsx`
- `/home/lupin/code/execute-plans/src/agora/pages/SignalReview.tsx`

This packet is ready for Codex review and parent-owner absorption decisions.
