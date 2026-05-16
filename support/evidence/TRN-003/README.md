# TRN-003 Evidence: rapid-eval request / response

**Task:** TRN-003  
**Owner:** Claude2  
**Reviewer:** Copilot  
**Phase:** Sprint 5 / EPIC-RESEARCH  
**Completed:** 2026-05-16

## Scope

Implemented rapid-eval request/response endpoints for the trainer workbench:

- `POST /api/v1/trainer/sessions/{session_id}/rapid-eval`
- `GET /api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}`

## Deliverables

### BFF main.py (pre-existing routes)

Routes `create_rapid_eval` and `get_rapid_eval` were already scaffolded in
`services/control-plane/bff/main.py` (lines 8830–8943). They reference:

- `_TRN003_RAPID_EVAL_SCOPES = frozenset({"persona_patch", "strategy_patch", "feature_patch", "risk_patch"})`
- `_TRN003_RAPID_EVAL_ACTIVE_STATUSES = frozenset({"active", "paused"})`
- `read_store.create_rapid_eval(...)` and `read_store.get_rapid_eval(...)`

### ReadSurfaceStore additions

Added to `services/control-plane/bff/read_store.py` (end of class):

- `_rapid_eval_store_path()` — reads `PANTHEON_BFF_RAPID_EVAL_STORE` env var
- `_load_rapid_evals()` — loads from JSON file
- `_save_rapid_evals()` — persists to JSON file
- `create_rapid_eval(session_id, *, ...)` — creates a `reval-` prefixed record with status=queued
- `get_rapid_eval(eval_id, *, snapshot_at=None)` — retrieves by eval_id

### Rapid eval record fields

```json
{
  "rapid_eval_id": "reval-YYYYMMDD-NNN",
  "session_id": "trn-...",
  "status": "queued",
  "eval_scope": "persona_patch | strategy_patch | ...",
  "dataset_version_id": "...",
  "max_runtime_seconds": 120,
  "patch_ref": null,
  "persona_id": null,
  "strategy_id": null,
  "requested_by": "...",
  "requested_at": "...",
  "completed_at": null,
  "advisory_note": "...",
  "meta": { "snapshot_at": "...", "surfaces": { "rapid_eval": "ok" } }
}
```

## Validation gates enforced

- 404 when trainer session not found
- 409 when trainer session status is not active/paused
- 422 when eval_scope missing or not in allowed set
- 422 when dataset_version_id missing
- 422 when max_runtime_seconds missing or <= 0
- GET 404 when eval_id not found or belongs to different session

## Verification

```
python3 -m pytest services/control-plane/bff/test_trn003_rapid_eval_contract.py -q
# 13 passed

python3 -m pytest services/control-plane/bff/test_tw01_teaching_dialog_contract.py \
  services/control-plane/bff/test_tw02_parameter_controls_contract.py \
  services/control-plane/bff/test_tw03_before_after_compare_contract.py \
  services/control-plane/bff/test_tw04_teaching_replay_contract.py \
  services/control-plane/bff/test_training_session_service_client.py -q
# 51 passed

python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py -q
# 8 passed
```
