---
reviewer: Claude
task_id: SVC-CONSULTATION-POSTGRES-STORE-PILOT-SIDECAR-REVIEW
parent_task: SVC-CONSULTATION-POSTGRES-STORE-PILOT
date: 2026-04-29
outcome: approved
---

# Review Notes: SVC-CONSULTATION-POSTGRES-STORE-PILOT-SIDECAR-REVIEW

## Scope Confirmation

Packet is support-only. No L1 canonical truth, runtime, registry, governance, or database ownership policy files are modified. Parent task is already archived as `done` at commit `5729f5d`.

## Source Verification

- `build_consultation_store()` in `store.py` confirms JSONL is the default path; Postgres is activated only when `CONSULTATION_STORE_BACKEND=postgres` and a DSN is supplied; invalid backends raise `ValueError`. ✓
- `PostgresConsultationStore` overrides `_append_lifecycle_event`, `put_memo`, `append_audit`, and `list_audit_for_request` to target Postgres. JSONL path is unchanged. ✓
- `_quote_pg()` validates identifiers with a regex guard before interpolating into SQL, preventing injection. ✓
- `psycopg` import is lazy inside `_connect()`; the JSONL default runs without psycopg installed. `requirements.txt` pins `psycopg[binary]` for the Postgres path. ✓
- `test_compose_activation.py` asserts `CONSULTATION_STORE_BACKEND == "${CONSULTATION_STORE_BACKEND:-jsonl}"` in compose, confirming no compose default change. ✓

## Test Coverage

Four focused tests without a real DB (fake psycopg):

| Test | Coverage |
|---|---|
| `test_build_consultation_store_jsonl_default` | default factory returns JSONL store |
| `test_build_consultation_store_postgres_env_gated` | explicit env activates Postgres; bootstrap DDL runs |
| `test_postgres_consultation_store_lifecycle_audit_outbox_and_reload` | request/memo/handoff/audit/outbox/reload roundtrip |
| `test_build_consultation_store_invalid_backend` | invalid backend raises ValueError |

Plus compose-boundary test: `test_root_compose_wires_consultation_service_boundary`.

5 passed, py_compile passed, scoped git diff --check passed — all consistent with packet claims.

## Data Ownership

Write owner `consultation-svc` and the four `consult_svc.*` tables are correctly documented. Consistent with existing consultation domain ownership boundary.

## Findings

No issues. The packet is accurate, scoped, and sufficient as supplemental evidence for the already-completed parent task.

## Recommendation

Approve. No follow-up required for the parent task. If the Postgres store is promoted beyond pilot, a separate canonical truth update task should be opened by the parent owner.
