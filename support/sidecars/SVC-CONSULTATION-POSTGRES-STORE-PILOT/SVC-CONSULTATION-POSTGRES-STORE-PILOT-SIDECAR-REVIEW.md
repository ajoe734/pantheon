# SVC-CONSULTATION-POSTGRES-STORE-PILOT Sidecar Review Packet

Task: `SVC-CONSULTATION-POSTGRES-STORE-PILOT-SIDECAR-REVIEW`
Parent task: `SVC-CONSULTATION-POSTGRES-STORE-PILOT`
Prepared by: Codex
Reviewer: Claude
Date: 2026-04-29

## Scope

This packet is a support-sidecar handoff only. It does not change canonical
truth, runtime implementation, registry behavior, governance behavior, or
database ownership policy. The parent task is already archived as `done` with
task-scoped commit `5729f5d680bb644ee635537b2d5d708aaea90270`.

The sidecar review question for Claude is whether this packet is sufficient as
supplemental evidence for the already completed optional consultation Postgres
store pilot.

## Parent Outcome Snapshot

The parent task delivered an optional Postgres-backed store for
`consultation-svc` while keeping JSONL as the default single-VM baseline.

Key implementation surfaces:

- `services/consultation/store.py`
  - JSONL `ConsultationStore` remains the default append/replay store.
  - `PostgresConsultationStore` is an explicit pilot implementation.
  - `build_consultation_store()` selects Postgres only when
    `CONSULTATION_STORE_BACKEND=postgres`.
- `services/consultation/main.py`
  - service startup still uses `build_consultation_store(DATA_DIR)`.
  - health details expose the selected backend without changing API routes.
- `docker-compose.yml`
  - compose default remains `CONSULTATION_STORE_BACKEND:
    ${CONSULTATION_STORE_BACKEND:-jsonl}` with an empty DSN unless explicitly
    supplied.
- `CONSULTATION_POSTGRES_STORE_PILOT.md`
  - documents activation, bootstrap, table names, and write-owner boundary.

## Acceptance Evidence

| Acceptance item | Evidence summary |
|---|---|
| JSONL remains default for single-VM baseline | `build_consultation_store()` returns `ConsultationStore` when the backend env is absent or `jsonl`; compose default is `${CONSULTATION_STORE_BACKEND:-jsonl}`. |
| Postgres store can be enabled by explicit env only | Postgres path requires `CONSULTATION_STORE_BACKEND=postgres` plus `CONSULTATION_STORE_DSN` or `DATABASE_URL`; invalid backends raise `ValueError`. |
| consult request, memo handoff, audit, and outbox behavior pass through store abstraction | Postgres tests exercise request, memo publication, gate handoff, audit append/list, outbox owner, and reload replay through the same store-facing methods. |
| migration or schema bootstrap is documented | `CONSULTATION_POSTGRES_STORE_PILOT.md` records default bootstrap via `CONSULTATION_STORE_BOOTSTRAP=1`, plus `CONSULTATION_STORE_BOOTSTRAP=0` for migration/platform-owned DDL. |
| compose default remains unchanged unless explicitly profiled | Compose keeps JSONL default and does not add a default `postgres` dependency for `consultation-svc`; activation note says operators must add the dependency for the profile they run. |

## Data Ownership Notes

- Write owner is `consultation-svc`.
- Default pilot tables are:
  - `consult_svc.lifecycle_events`
  - `consult_svc.audit_events`
  - `consult_svc.memo_publications`
  - `consult_svc.outbox_records`
- Other services should use the consultation API or a read-only database role.
- This matches the existing ownership map where the `consult` domain is owned
  by `consultation-svc`.

## Verification Run For This Packet

Commands run on 2026-04-29:

```bash
pytest -q services/consultation/test_postgres_store.py services/consultation/test_compose_activation.py
python3 -m py_compile services/consultation/store.py services/consultation/main.py services/consultation/test_postgres_store.py
git diff --check -- CONSULTATION_POSTGRES_STORE_PILOT.md docker-compose.yml services/consultation/main.py services/consultation/store.py services/consultation/test_compose_activation.py services/consultation/test_postgres_store.py
```

Result:

- `5 passed in 0.63s`
- `py_compile` passed
- scoped `git diff --check` passed

Parent archive also records broader verification before closeout:

```bash
pytest -q services/consultation/test_postgres_store.py services/consultation/test_compose_activation.py services/control_plane/test_internal_api_incident.py services/control-plane/bff/test_cw01_consult_request_contract.py services/control-plane/bff/test_cw03_committee_board_contract.py services/control-plane/bff/test_consultation_surfaces.py
pytest -q services/consultation
python3 -m py_compile services/consultation/store.py services/consultation/main.py services/consultation/test_postgres_store.py
git diff --check on consultation pilot files
```

## Reviewer Checklist

Recommended Claude review focus:

- Confirm this sidecar artifact is support-only and does not alter canonical
  truth.
- Confirm the acceptance evidence is sufficient for supplemental review
  packet purposes.
- Confirm no follow-up is needed for the parent task, since the parent is
  already `done` in the archive.
- If approved, move this sidecar task through the normal lifecycle; the owner
  will perform closeout after `review_approved`.

## Review Approval And Closeout

Claude approved this packet on 2026-04-29. The review notes are recorded in:

- `support/sidecars/SVC-CONSULTATION-POSTGRES-STORE-PILOT/review-notes-claude.md`

Approval summary:

- packet is support-only and does not modify L1 canonical truth, runtime,
  registry, governance, or database ownership policy files
- JSONL remains the default consultation store path
- Postgres activation remains explicitly env-gated
- focused consultation and compose-boundary verification passed
- no parent follow-up is required unless the Postgres store is promoted beyond
  pilot, which would require a separate truth-sync task

## Handoff

Claude review is approved. This packet supports the already completed parent
task and should not be absorbed into L1 canonical truth unless the parent owner
or reviewer opens a separate explicit truth-sync task.
