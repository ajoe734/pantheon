# ASK-002 Evidence

Task: `ASK-002` — ConsultRequest / ConsultMemo schema

## Scope

- Added Draft-07 JSON schemas for `ConsultRequest` and `ConsultMemo`.
- Added schema load/validate helpers and cross-object memo-to-request lineage validation in `services/consultation/models.py`.
- Tightened consultation Pydantic model field constraints for non-empty identities and memo confidence bounds.
- Kept consultation output advisory-only: schemas reject unknown direct side-effect fields such as deployment commands or broker orders.

## Verification

- `python3 -m py_compile services/consultation/models.py services/consultation/main.py services/consultation/store.py`
- `python3 -m pytest services/consultation/test_models.py -q` — 8 passed
- `python3 -m pytest services/consultation/smoke_test.py services/consultation/test_postgres_store.py -q` — 8 passed
- `python3 -m pytest services/consultation/test_compose_activation.py -q` — 1 passed
- `python3 -m py_compile services/consultation/models.py services/consultation/main.py services/consultation/store.py services/consultation/client.py`
- `python3 -m pytest services/control-plane/bff/test_cw01_consult_request_contract.py services/control-plane/bff/test_read_store_service_clients.py -q` — 10 passed

## Closeout Confirmation

- Reviewed implementation commit: `b23fb2e3` (`ASK-002 add consultation schemas`).
- Reviewer approval: `support/reviews/ASK-002-review-claude.md`, approved by Claude on 2026-05-16.
- Closeout rerun on 2026-05-16:
  - `python3 -m py_compile services/consultation/models.py services/consultation/main.py services/consultation/store.py services/consultation/client.py`
  - `python3 -m pytest services/consultation/test_models.py -q` - 8 passed
  - `python3 -m pytest services/consultation/smoke_test.py services/consultation/test_postgres_store.py -q` - 8 passed
  - `python3 -m pytest services/consultation/test_compose_activation.py -q` - 1 passed
  - `python3 -m pytest services/control-plane/bff/test_cw01_consult_request_contract.py services/control-plane/bff/test_read_store_service_clients.py -q` - 10 passed
