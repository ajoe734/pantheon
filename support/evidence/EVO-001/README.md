# EVO-001 Evidence Packet: EvolutionDecision Service

**Task:** EVO-001  
**Title:** EvolutionDecision service  
**Phase:** Sprint 6 / EPIC-EVOLUTION  
**Owner:** Claude  
**Reviewer:** Codex  
**Date:** 2026-05-16  

## Scope

EVO-001 delivers the `EvolutionDecision` service — the first-class governance record and HTTP API for proposing, reviewing, approving, and executing evolution decisions. The service enforces L1 policy from `EVOLUTION_REVIEW_AND_THRESHOLDS.md` and `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`.

BFF read-surface integration (`/bff/v5/loop-runs`, `/bff/v5/sentinel/findings`) is out of scope for this task — those are separate Sprint 6 tasks.

## Implementation Files

| File | Role |
|---|---|
| `services/evolution/main.py` | FastAPI HTTP API surface (1224 lines) |
| `services/evolution/models.py` | Typed request/response models (302 lines) |
| `services/evolution/test_evolution_service.py` | Focused API test suite (57 tests) |
| `services/control-plane/governance/evolution_decision.py` | Domain object, role matrices, risk inference, persistence, single-active enforcement (1105 lines) |
| `services/control-plane/governance/evolution_controller.py` | Threshold classification, boundary routing, cooldown calculation, follow-through command envelopes (704 lines) |
| `services/control-plane/governance/evolution_decision.contract.md` | L1-adjacent service contract |

## Route Inventory

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/evolution/proposals` | Create proposal |
| POST | `/api/evolution/proposals/from-incident` | Incident-derived proposal |
| GET | `/api/evolution/proposals` | List proposals (filter by state, target_id, active) |
| GET | `/api/evolution/proposals/{decision_id}` | Get single proposal |
| GET | `/api/evolution/proposals/{decision_id}/observation-report` | Observation window report |
| POST | `/api/evolution/proposals/{decision_id}/review` | Review (moves to `reviewed`) |
| POST | `/api/evolution/proposals/{decision_id}/approve` | Approve (moves to `approved`) |
| POST | `/api/evolution/proposals/{decision_id}/reject` | Reject (moves to `rejected`) |
| POST | `/api/evolution/proposals/{decision_id}/cancel` | Cancel (moves to `canceled`) |
| POST | `/api/evolution/proposals/{decision_id}/execute` | Execute (moves to `executed`) |
| GET | `/api/evolution/proposals/{decision_id}/boundary` | Boundary and write-owner info |
| POST | `/api/evolution/proposals/{decision_id}/rollback-followthrough` | Rollback companion command |
| POST | `/api/evolution/proposals/{decision_id}/redeploy-followthrough` | Redeploy dispatch command |
| GET | `/api/evolution/action-paths` | Action path catalog with owner/cooldown |
| POST | `/api/evolution/threshold-evaluate` | Threshold evaluation |

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| `EvolutionDecision` lifecycle implemented | pass | `proposed → reviewed → approved → executed/rejected/canceled` all tested |
| Evidence is mandatory | pass | Proposal without evidence returns 422; tested in `test_propose_missing_evidence_link_rejected` |
| Risk derived from action/stage | pass | Low/medium/high risk inferred by `infer_risk_level()`; caller cannot override |
| Actor-role gates match L1 | pass | Review/approve/execute wrong-role tests pass; medium-risk operator-alone approval rejected |
| Approval linkage from `reviewed` onward | pass | `approval_decision_id` required at review; threaded through later states |
| Cooldown/observation windows set on execute | pass | `3d/7d` low, `7d/7d` medium, `14d/14d` high — directly asserted |
| Single-active rule blocks duplicate targets | pass | Duplicate same-target proposal rejected while active decision exists |
| Incident/postmortem proposal path | pass | `/proposals/from-incident` derives links; no runtime/broker/capital mutation |
| Postmortem reverse link synchronized | pass | `Postmortem.linked_evolution_decision_id` asserted after proposal |
| Boundary/action-path write-owner separation | pass | `/boundary` and `/action-paths` return owner roles, cooldowns, execution plane |
| Rollback follow-through companion-only | pass | Requires approved freeze + `active_binding_id`; returns follow-through metadata only |
| Redeploy follow-through post-execute only | pass | Accepted only after eligible executed parent actions |
| HTTP errors controlled | pass | Invalid evidence ref and invalid freeze mode return 400/422 not 500 |
| BFF v5 integration not silently implied | confirmed out-of-scope | BFF v5 loop/sentinel endpoints are separate tasks |

## Verification

### Focused suite

```
python3 -m pytest services/evolution/test_evolution_service.py -q
# 57 passed in 30.54s
```

### Route spot-check

```
grep -n "^@app\." services/evolution/main.py
# 16 routes confirmed
```

### Invariant spot-check

```
grep -n "APPROVAL_OWNER_MATRIX\|REVIEW_OWNER_MATRIX\|single_active\|infer_risk_level" \
  services/control-plane/governance/evolution_decision.py
# confirmed present

grep -n "boundary_for\|execute_approved\|create_redeploy_followthrough\|ThresholdEvaluator" \
  services/control-plane/governance/evolution_controller.py
# confirmed present
```

### Worktree cleanliness

```
git status --short services/evolution/
# (empty — service files clean, all committed)
```

## Sidecar Support

`support/sidecars/EVO-001/EVO-001-SIDECAR-ACCEPTANCE.md` prepared by Codex maps the full acceptance checklist, dependency map, and parent scope questions. Reviewed and approved by Claude on 2026-05-16.

## Boundary

- Evolution service does not mutate `RuntimeBinding` directly.
- Deployment follow-through (redeploy, rollback) returns dispatch commands for the deployment plane to consume.
- Capital, broker, and live trading are not affected by this task.
- JSON-file store is acceptable for this P3 Sprint 6 slice.
