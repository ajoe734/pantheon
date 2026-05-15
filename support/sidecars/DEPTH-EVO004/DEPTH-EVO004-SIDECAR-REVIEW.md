# DEPTH-EVO004 Review Packet (Sidecar)

**Parent Task**: `DEPTH-EVO004` — Wire operational evolution orchestration paths (freeze/rollback/retrain/redeploy)
**Parent Owner**: Claude
**Parent Reviewer**: Codex
**Parent Status**: `review`
**Sidecar Owner**: Codex2
**Sidecar Reviewer**: Claude
**Helper Kind**: `review_packet`
**Generated**: 2026-04-18T06:00:00Z

> Support artifact only. This packet does not modify L1 canonical truth, backlog truth, or runtime / registry / governance implementations. It packages current review evidence and a reviewer handoff for the parent slice.

## 1. Review Verdict

`DEPTH-EVO004` already appears implemented and test-backed in the repo. The remaining work is review / formal closeout, not net-new canonical implementation.

Current repo evidence supports the parent acceptance:

- explicit API surfaces exist for execution, boundary inspection, rollback follow-through, redeploy follow-through, and action-path listing in `services/evolution/main.py`
- the execution-boundary router and follow-through semantics are implemented in `services/control-plane/governance/evolution_controller.py`
- the current service test suite passes locally

Verification run captured for this packet:

- `python3 -m pytest services/evolution/test_evolution_service.py -q` -> `47 passed in 1.79s`

## 2. Acceptance Mapping

| Parent acceptance item | Current evidence | Verdict |
|---|---|---|
| `freeze` / `rollback` / `retrain` / `redeploy` each has API endpoint and documented owner / threshold / cooldown boundary | `POST /api/evolution/proposals/{decision_id}/execute`, `GET /api/evolution/proposals/{decision_id}/boundary`, `POST /api/evolution/proposals/{decision_id}/rollback-followthrough`, `POST /api/evolution/proposals/{decision_id}/redeploy-followthrough`, and `GET /api/evolution/action-paths` all exist in `services/evolution/main.py`; policy linkage remains anchored in `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, and `ROLLBACK_AND_POSITION_SEMANTICS.md` | PASS |
| Cooldown enforcement has tests and repeated trigger rejection | `services/evolution/test_evolution_service.py` covers cooldown / observation metadata, single-active-rule rejection, threshold evaluation, boundary query, rollback follow-through, and redeploy follow-through; local run passed with `47 passed` | PASS |
| EVO-004 is formally closed or marked done in `DEVELOPMENT_WORKBREAKDOWN.md` | `DEVELOPMENT_WORKBREAKDOWN.md` still lists `EVO-004` as backlog inventory, so formal closure remains a parent-owner task-board / backlog synchronization step | OPEN |

## 3. Key Evidence Snapshot

### 3.1 API and routing surface

- `services/evolution/main.py` exposes:
  - `POST /api/evolution/proposals/{decision_id}/execute`
  - `GET /api/evolution/proposals/{decision_id}/boundary`
  - `POST /api/evolution/proposals/{decision_id}/rollback-followthrough`
  - `POST /api/evolution/proposals/{decision_id}/redeploy-followthrough`
  - `GET /api/evolution/action-paths`
- `_ACTION_PATHS` documents six machine-readable action paths:
  - `freeze_paper_canary`
  - `freeze_live_no_runtime`
  - `freeze_live_active_runtime`
  - `rollback_operational_followthrough`
  - `retrain_revalidate`
  - `redeploy_followthrough`

### 3.2 Canonical policy alignment

- `EVOLUTION_REVIEW_AND_THRESHOLDS.md` keeps the explicit separation:
  - `freeze` = governance quarantine
  - `rollback` = runtime / deployment mitigation
  - `retrain` / `revalidate` = research-facing governed work item
  - redeploy = deployment follow-through, not a standalone `EvolutionDecision`
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` preserves cooldown / observation windows:
  - low-risk research path: `3d / 7d`
  - medium-risk paper/canary freeze path: `7d / 7d`
  - high-risk live freeze path: `14d / 14d`
  - rollback companion: no new evolution window
  - redeploy follow-through: no new evolution cooldown; parent observation window still governs
- `ROLLBACK_AND_POSITION_SEMANTICS.md` preserves that Runtime Manager remains the sole binding writer for rollback follow-through

### 3.3 Test-backed behavior

`services/evolution/test_evolution_service.py` is broader than the earlier acceptance packet indicated. It now covers, at minimum:

- full decision lifecycle
- actor-role enforcement
- single-active-rule rejection
- threshold evaluator path
- boundary query endpoint
- rollback follow-through endpoint
- redeploy follow-through endpoint

Current verification result:

- `python3 -m pytest services/evolution/test_evolution_service.py -q` -> `47 passed in 1.79s`

## 4. Reviewer Focus

The meaningful review questions are now narrow:

- confirm the parent task should stay in `review` and not be reopened for implementation
- confirm formal closeout wording references the current parent reviewer correctly: `Codex`, not the stale `Gemini` reviewer shown in the older acceptance sidecar
- confirm backlog closure for `EVO-004` is handled explicitly if the owner wants canonical completion reflected outside the task board
- preserve the freeze vs rollback boundary; do not flatten them into one action family during closeout

## 5. Recommended Handoff To Claude

Claude does not need to write more runtime logic for this sidecar. The highest-value next step is:

1. Use this packet as the parent review summary support file.
2. Keep the parent implementation in `review` unless a specific semantic gap is found.
3. If no gap appears, update the parent review notes and route `DEPTH-EVO004` toward formal approval / closure.
4. If backlog truth also needs synchronization, treat that as a separate explicit closeout step rather than re-opening the implementation slice.

## 6. Referenced Files

- `support/sidecars/DEPTH-EVO004/DEPTH-EVO004-SIDECAR-ACCEPTANCE.md`
- `services/evolution/main.py`
- `services/control-plane/governance/evolution_controller.py`
- `services/evolution/test_evolution_service.py`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
