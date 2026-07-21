# DEPTH-EVO004 Review

Reviewer: Codex  
Owner: Claude  
Date: 2026-04-18

## Findings

1. `redeploy-followthrough` currently accepts any executed parent decision, including an executed `freeze`, even though the contract text and `EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1` only allow redeploy follow-through after retrain / revalidate / revive / freeze-lift readiness. `services/evolution/main.py` forwards every executed decision to `create_redeploy_followthrough()` without an action-family guard, and `services/control-plane/governance/evolution_controller.py` only checks `decision_state == executed`. I reproduced this by executing a high-risk freeze and then calling `POST /api/evolution/proposals/{id}/redeploy-followthrough`, which returned `200` with `action_type=redeploy_followthrough`. Relevant refs: `services/evolution/main.py:622-656`, `services/control-plane/governance/evolution_controller.py:626-680`.

2. `rollback-followthrough` hardcodes `has_active_runtime=True` and does not require an `active_binding_id`, so it can emit a runtime rollback path even when the freeze path has no active runtime or no binding identity. That violates the split between `freeze` with no active runtime and `rollback` operational follow-through in `EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1` and `ROLLBACK_AND_POSITION_SEMANTICS.md §10`. I reproduced this by approving a high-risk freeze and calling `POST /api/evolution/proposals/{id}/rollback-followthrough` without `active_binding_id`; the endpoint still returned `200` and moved the decision to `executed`. Relevant refs: `services/evolution/main.py:564-580`, `services/control-plane/governance/evolution_controller.py:384-418`, `services/control-plane/governance/evolution_controller.py:503-548`.

3. The new machine-readable `GET /api/evolution/action-paths` matrix does not use the same path keys or granularity as the live router in `boundary_for()`, so a consumer cannot reliably map `/api/evolution/proposals/{id}/boundary` output back to the published routing matrix. Examples: `freeze_paper_canary` vs `freeze_non_live`, `freeze_live_no_runtime` vs `freeze_live_no_active_runtime`, and `retrain_revalidate` vs `research_retrain` / `research_revalidate`. Relevant refs: `services/evolution/main.py:662-740`, `services/control-plane/governance/evolution_controller.py:384-430`.

## Verification

Reviewed against:

- `services/evolution/main.py`
- `services/evolution/models.py`
- `services/control-plane/governance/evolution_controller.py`
- `services/control-plane/governance/evolution_decision.py`
- `services/evolution/test_evolution_service.py`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`

Executed:

```bash
pytest -q services/evolution/test_evolution_service.py
python3 - <<'PY'
import uuid
from services.evolution.test_evolution_service import client, HIGH_RISK_BODY, uid, advance_to_executed

freeze_id = uid()
body = {**HIGH_RISK_BODY, "decision_id": freeze_id, "target_id": f"persona-rd-{uuid.uuid4().hex[:6]}"}
client.post("/api/evolution/proposals", json=body).raise_for_status()
client.post(f"/api/evolution/proposals/{freeze_id}/review", json={"actor_role": "governance_committee", "actor_id": "committee", "approval_decision_id": "apv-1"}).raise_for_status()
client.post(f"/api/evolution/proposals/{freeze_id}/approve", json={"actor_role": "governance_committee", "actor_id": "committee"}).raise_for_status()
advance_to_executed(freeze_id)
print(client.post(f"/api/evolution/proposals/{freeze_id}/redeploy-followthrough", json={"artifact_id": "artifact-x", "artifact_version": "v2", "approval_decision_id": "apv-rd", "target_stage": "paper"}).status_code)

freeze2_id = uid()
body2 = {**HIGH_RISK_BODY, "decision_id": freeze2_id, "target_id": f"persona-rb-{uuid.uuid4().hex[:6]}"}
client.post("/api/evolution/proposals", json=body2).raise_for_status()
client.post(f"/api/evolution/proposals/{freeze2_id}/review", json={"actor_role": "governance_committee", "actor_id": "committee2", "approval_decision_id": "apv-2"}).raise_for_status()
client.post(f"/api/evolution/proposals/{freeze2_id}/approve", json={"actor_role": "governance_committee", "actor_id": "committee2"}).raise_for_status()
print(client.post(f"/api/evolution/proposals/{freeze2_id}/rollback-followthrough", json={"actor_role": "evolution_controller", "actor_id": "ctrl"}).status_code)
PY
```

Results:

- `pytest`: 47 tests passed
- repro 1: executed `freeze` incorrectly allowed `redeploy-followthrough` with `200 OK`
- repro 2: missing-binding rollback follow-through incorrectly returned `200 OK`

## Decision

`DEPTH-EVO004` is **not approved**. Reopen for owner fixes before returning to review.
