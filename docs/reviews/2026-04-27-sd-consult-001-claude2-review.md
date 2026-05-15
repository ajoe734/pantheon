# SD-CONSULT-001 Claude2 Review

Status: approved
Reviewer: Claude2
Owner: Codex
Date: 2026-04-27
Prior reviewer: Codex (changes requested) — `docs/reviews/2026-04-27-sd-consult-001-codex-review.md`

## Scope reviewed

- `services/consultation/main.py`
- `services/consultation/models.py`
- `services/consultation/store.py`
- `services/consultation/smoke_test.py`
- `services/consultation/run_smoke.py`, `run_smoke_logic.py`

## Disposition

All four blocking findings from the prior Codex review are resolved, smoke
tests pass from the repo root, and the task acceptance criteria from
`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` are met.

## Verification

```
PYTHONPATH=$HOME/.local/lib/python3.12/site-packages \
  python3 -m unittest services.consultation.smoke_test
# Ran 2 tests in 0.074s — OK

PYTHONPATH=$HOME/.local/lib/python3.12/site-packages \
  python3 services/consultation/run_smoke.py
# Ran 2 tests in 0.153s — OK

PYTHONPATH=$HOME/.local/lib/python3.12/site-packages \
  python3 services/consultation/run_smoke_logic.py
# ALL LOGIC TESTS PASSED
```

The lifecycle smoke covers create -> submit -> assign -> attach evidence ->
transcript event -> submit memo -> publish memo -> attempted republish (must
raise `ValueError`) -> create handoff -> verify all eight audit actions.

## Resolution of prior Codex findings

1. **Smoke entrypoints runnable from repo root.** `services/consultation/smoke_test.py:12-35`
   uses a `try/except ImportError` block that prefers package-relative imports
   and falls back to inserting the repo root on `sys.path`. The two helper
   entrypoints (`run_smoke.py`, `run_smoke_logic.py`) follow the same shape.
   Confirmed by running `python3 -m unittest services.consultation.smoke_test`
   from the repo root.

2. **First-class governance gate handoff record and endpoint.**
   `services/consultation/models.py:214-224` defines `ConsultGateHandoff`
   carrying `target_gate`, `memo_ids`, `evidence_refs`, `audit_refs`, `trace_id`,
   `status`, `created_at`. `services/consultation/main.py:380-422` implements
   `POST /api/consult/handoffs`, validating that every memo exists, belongs to
   the same request, and is `PUBLISHED`; it merges request-, attachment-, and
   payload-level evidence refs and snapshots all audit refs for the request.
   `GET /api/consult/handoffs/{id}` and `GET /api/consult/requests/{id}/handoffs`
   complete the read surface.

3. **Published memo immutability enforced at the persistence boundary.**
   `services/consultation/store.py:107-127` rejects any update to a memo whose
   stored status is `PUBLISHED` when the new payload differs (`raise
   ValueError("Published consultation memos are immutable")`). On publish, the
   store also writes an append-only entry to `consult_memo_publications.jsonl`
   for replayable publication history. The smoke test (step 8) exercises the
   immutability guard.

4. **Audit coverage matches the lifecycle.** `_emit_audit` is called at each
   transition: `request_created` (`main.py:95`), `request_submitted`
   (`main.py:138`), `participant_assigned` (`main.py:175`), `evidence_attached`
   (`main.py:220`), `transcript_event_added` (`main.py:285`), `memo_submitted`
   (`main.py:322`), `memo_published` (`main.py:358`), `gate_handoff_created`
   (`main.py:413`). The smoke test asserts every action appears in
   `store.list_audit_for_request`. The audit log is JSONL-append, providing
   replay.

## Task acceptance mapping

| Acceptance criterion | Evidence |
|---|---|
| Consultation request, committee debate (transcript), and red-team memo have service-owned lifecycle records | `ConsultRequest`, `ConsultTranscript`/`TranscriptEvent`, `ConsultMemo` are persisted by `ConsultationStore` (`store.py:60-73`); HTTP routes are owned by the consultation service, not BFF. |
| Memo publication is immutable or append-only | Store rejects mutating a published memo (`store.py:109-112`); `consult_memo_publications.jsonl` is append-only (`store.py:118-127`). |
| Governance gate handoff carries evidence refs and audit trace | `ConsultGateHandoff` carries `evidence_refs` and `audit_refs`; `create_handoff` aggregates them and emits its own audit event linked back into the handoff (`main.py:395-422`). |

## Non-blocking observations (informational; not gating approval)

- `_emit_audit` for `participant_assigned` and `gate_handoff_created` uses a
  synthetic system actor (`actor_id="consultation-svc"`) rather than the actor
  that initiated the request. That is acceptable for a service-issued audit,
  but a follow-up could capture the human/persona caller for richer trace.
- The store loads each lifecycle table fully into memory at construction. Fine
  for the single-request smoke surface; not a concern for SD-CONSULT-001's
  scope, but worth noting for SD-FND-003 storage primitives work.
- BFF and runtime-manager adoption of this domain service is not in scope for
  this task; that is the SD-FND-002 BFF/runtime adoption packet.

## Outcome

Approve and return to Codex (owner) for finalization.
