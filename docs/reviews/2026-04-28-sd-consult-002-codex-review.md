# SD-CONSULT-002 Codex Review

Status: approved
Reviewer: Codex
Owner: Codex2
Date: 2026-04-28

## Scope reviewed

- `services/consultation/main.py`
- `services/consultation/models.py`
- `services/consultation/store.py`
- `services/consultation/smoke_test.py`
- Task packet: `docs/reviews/2026-04-28-foundation-source-consultation-execution-task-packet.md`
- Prior slice review: `docs/reviews/2026-04-27-sd-consult-001-claude2-review.md`

## Disposition

No blocking findings.

The implementation satisfies `SD-CONSULT-002` acceptance: consultation lifecycle
objects now persist through an append/replay log, replay preserves the full
request/participant/evidence/transcript/memo/handoff projection, memo publication
immutability is retained, and the formerly synthetic participant assignment and
gate handoff audit events now preserve the initiating actor separately from the
consultation service actor.

## Evidence

- `ConsultationStore` now initializes from `consult_lifecycle_events.jsonl` when
  present, otherwise migrates legacy snapshots into lifecycle events before
  serving the in-memory projection (`services/consultation/store.py:52`).
- Lifecycle writes append a typed lifecycle event and emit a shared foundation
  outbox event with a `TraceContext` service actor
  (`services/consultation/store.py:184`).
- Request, memo, participant, transcript, evidence, and handoff writes all go
  through the lifecycle append path (`services/consultation/store.py:233`,
  `services/consultation/store.py:251`,
  `services/consultation/store.py:301`,
  `services/consultation/store.py:324`,
  `services/consultation/store.py:348`,
  `services/consultation/store.py:368`).
- Published memo mutation remains rejected at the store boundary, and memo
  publication history remains append-only
  (`services/consultation/store.py:251`).
- Audit records now support `service_actor_ref`; participant assignment and gate
  handoff creation use `initiated_by` when supplied and retain
  `consultation-svc` as the service actor
  (`services/consultation/main.py:51`,
  `services/consultation/main.py:182`,
  `services/consultation/main.py:421`).
- The smoke test now covers actor fidelity, replayed lifecycle state, stable
  handoff audit refs, stable audit ids after replay, and outbox emission
  (`services/consultation/smoke_test.py:182`).

## Verification

```
python3 -m unittest services.consultation.smoke_test
# Ran 2 tests in 0.098s - OK

python3 services/consultation/run_smoke.py
# Ran 2 tests in 0.079s - OK

python3 services/consultation/run_smoke_logic.py
# ALL LOGIC TESTS PASSED

python3 -m py_compile services/consultation/main.py services/consultation/models.py services/consultation/store.py services/consultation/smoke_test.py services/consultation/run_smoke.py services/consultation/run_smoke_logic.py
# OK
```

I also ran a temporary-directory legacy snapshot migration replay check. It
verified that old JSON snapshot files are migrated into
`consult_lifecycle_events.jsonl`, then replayed with the published request,
memo, transcript event, handoff audit refs, service actor audit fidelity, and
foundation outbox records intact.

## Non-blocking observations

- Lifecycle state append and outbox append are sequential writes rather than a
  single atomic commit. That is acceptable for this persistence slice; broader
  crash-window hardening remains covered by the foundation recovery work.
- Legacy snapshot migration is covered for normal migration and replay, not for
  process death midway through migration. That is also outside this task's
  explicit acceptance.

## Outcome

Approved. Return to Codex2 for owner finalization from `review_approved` to
`done`.
