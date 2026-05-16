# TRN-001 Evidence: TeachingSession / TeachingEvent Schema

Task: TRN-001 - TeachingSession / TeachingEvent schema
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Scope

Added the schema-backed trainer teaching contracts:

- `services/training-session/teaching_session.schema.json`
- `services/training-session/teaching_event.schema.json`
- `services/training-session/models.py`
- `services/training-session/tests/test_teaching_models.py`
- `services/training-session/README.md`

The training-session HTTP service now emits schema-compatible session and event
records for create, append-message, complete/replay materialization, and
commit/discard decisions. Canonical event fields (`actor_type`, `payload`,
`timestamp`, `correlation_id`) are emitted while preserving existing BFF-facing
aliases (`actor`, `message_body`, `emitted_at`, `sequence_number`, replay refs).

## Acceptance Mapping

- TeachingSession schema records persona scope, opener, mode, lifecycle status,
  started/ended timestamps, current control-state ref, trace id, context refs,
  append-only events, outcomes, and replay artifacts.
- TeachingEvent schema records event identity, session identity, type, actor
  type, payload, canonical timestamp, correlation id, and sequence number.
- Model validation enforces terminal session `ended_at`, active/paused non-ended
  sessions, duplicate event-id rejection, timestamp alias consistency, and
  event/session id matching helper coverage.
- Runtime service-created sessions/events validate against the new models.
- No live persona mutation, registry write, deployment route, or broker action
  was added.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/training-session/models.py services/training-session/main.py services/training-session/tests/test_teaching_models.py services/training-session/tests/test_http_service.py
python3 -m json.tool services/training-session/teaching_session.schema.json
python3 -m json.tool services/training-session/teaching_event.schema.json
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/tests/test_teaching_models.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/tests/test_http_service.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_training_session_service_client.py -q
git diff --check -- services/training-session/models.py services/training-session/main.py services/training-session/teaching_session.schema.json services/training-session/teaching_event.schema.json services/training-session/tests/test_teaching_models.py services/training-session/tests/test_http_service.py services/training-session/README.md support/evidence/TRN-001/README.md
```

Results:

- `py_compile`: passed
- `json.tool`: passed for both schemas
- Teaching model tests: 7 passed
- HTTP lifecycle tests: 3 passed
- full `services/training-session/tests`: 13 passed
- BFF training-session client contract: 3 passed, 2 existing
  `datetime.utcnow()` warnings from `read_store.py`
- targeted `git diff --check`: passed

## Worktree Boundary

The repository had unrelated dirty orchestrator state, archived task snapshots,
review files, and other task artifacts before TRN-001 implementation. TRN-001
owned changes are limited to the training-session schema/model/service/test
files and this evidence packet.

## Closeout Finalization

Reviewer approval was recorded by Claude in
`support/reviews/TRN-001-review-claude.md` on 2026-05-16. Owner finalization
re-ran the focused verification on the current worktree after later trainer
changes had landed:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/training-session/models.py services/training-session/main.py services/training-session/tests/test_teaching_models.py services/training-session/tests/test_http_service.py`: passed
- `python3 -m json.tool services/training-session/teaching_session.schema.json`: passed
- `python3 -m json.tool services/training-session/teaching_event.schema.json`: passed
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/tests -q`: 17 passed
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_training_session_service_client.py -q`: 3 passed, 2 existing `datetime.utcnow()` warnings
- targeted `git diff --check` over TRN-001 files: passed

The implementation commit is `f7d155a9`. This closeout evidence update records
the review artifact and final verification without changing canonical
architecture scope. Owner finalization is performed with `AI_NAME=Codex`.
