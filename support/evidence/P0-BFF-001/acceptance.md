# P0-BFF-001 Acceptance Evidence

Task: `P0-BFF-001` - `GET /bff/me` session bootstrap
Owner: `Claude2` (reassigned from Codex2 after Codex2 quota exhaustion)
Reviewer: `Claude`
Status: done

## Scope Checked

- `GET /bff/me` is implemented in `services/control-plane/bff/main.py`.
- The route authenticates through the BFF auth facade, including bearer-token JWT, cookie-backed JWT, and local stub mode.
- The response includes the frontend bootstrap DTO fields required by the P0 BFF session slice:
  - `user`, `currentUser`, `current_user`
  - `roles`
  - `capabilities`
  - `tenant`, `tenant_id`
  - `locale`
  - `environment`
  - `feature_flags`
  - `session`
- Tenant and locale overrides are persisted through the session lifecycle store.
- Logged-out lifecycle state is reflected in the `/bff/me` session payload.

## Verification

Commands run from repo root:

```bash
pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
pytest services/control-plane/bff/test_bff_auth_facade.py -q
```

Results:

- `services/control-plane/bff/test_bff_session_auth_me_contract.py`: `18 passed, 1 warning`
- `services/control-plane/bff/test_bff_auth_facade.py`: `66 passed`

The FastAPI OpenAPI warning is pre-existing duplicate-operation-id noise in `main.py`; it did not fail the focused `/bff/me` session bootstrap contract.

## Finalization (Claude2, 2026-05-15)

Re-verification at closeout (owner: Claude2, after ownership reassignment):

```bash
python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
# 18 passed

python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
# 66 passed
```

All approved scope verified stable. Review verdict: APPROVED by Claude (review file: `support/reviews/P0-BFF-001-review-claude.md`).

Isolated commit exception: `main.py` and `test_bff_session_auth_me_contract.py` contain concurrent hunks from sibling tasks (P0-BFF-002, P0-BFF-003, P0-ACT-001, P0-REG-001, P0-CAP-001). Non-interactive isolation is not possible per background worker git rule. Task-scoped commit covers only the cleanly task-owned artifacts: `support/evidence/P0-BFF-001/` and `support/reviews/P0-BFF-001-review-claude.md`.
