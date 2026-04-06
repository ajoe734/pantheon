# Trader Feedback Ingestion

**Task:** FB-002  
**Owner:** Codex  
**Reviewer:** Claude

This service captures explicit trader feedback events as governed learning input.

It records:

- `approve`
- `edit`
- `reject`
- `rationale`

It does **not**:

- promote artifacts
- mutate live LEAN state
- bypass OC-001 approval rules

## API

### `POST /trader-feedback`

Accepts a `TraderFeedbackEvent` shaped by `services/feedback/schema/trader_feedback_event.schema.json`.

Important runtime checks:

- `actor_role` is aligned to OC-001 roles: `operator`, `approver`, `system`
- `edit` events must include `edits`
- `rationale` events must include `rationale`
- schema and runtime validation both require `target` to link back to a governed artifact through:
  - `registry_id`, or
  - `artifact_version + artifact_type`, or
  - `lineage_ref + artifact_type`

Idempotency:

- `event_id` is treated as an idempotency key
- duplicate submissions return the previously stored event instead of writing a second line

### `GET /trader-feedback`

Query filters:

- `strategy_id`
- `registry_id`
- `promotion_state`
- `event_type`
- `actor_id`
- `created_after`
- `created_before`
- `limit`

`created_after` and `created_before` must be RFC3339 timestamps with timezone.
Invalid timestamps, or an inverted time window where `created_after > created_before`,
return HTTP 422 instead of silently dropping the filter.

## Storage

The local dev store is append-only JSONL.

- env: `TRADER_FEEDBACK_STORE_PATH`
- default: `.orchestrator/feedback/trader_feedback_events.jsonl` under the repo root

This is a storage stub for FB-002. Backend replacement is allowed later as long as append-only semantics and event ids stay stable.

## Audit

Every accepted or duplicate ingest attempt is mirrored into the governance audit log when `services/control-plane/governance/audit.py` is available.
