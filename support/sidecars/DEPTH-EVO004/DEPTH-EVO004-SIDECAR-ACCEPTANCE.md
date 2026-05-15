# DEPTH-EVO004 Acceptance Packet (Sidecar)

**Parent Task**: `DEPTH-EVO004` — Wire operational evolution orchestration paths (freeze/rollback/retrain/redeploy)
**Parent Owner**: Claude
**Parent Reviewer**: Gemini
**Parent Status**: `todo` in durable task board, but implementation evidence already exists
**Sidecar Owner**: Codex2
**Sidecar Reviewer**: Claude
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-18T05:39:33Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages the dependency state, acceptance evidence, and reviewer handoff notes for `DEPTH-EVO004`.

## 1. Scope Verdict

`DEPTH-EVO004` is not a greenfield slice anymore. The current repo already contains:

- an HTTP evolution service with explicit lifecycle and execution routes in `services/evolution/main.py`
- a normal-path routing controller for `freeze`, rollback companion flow, research actions such as `retrain`, and redeploy follow-through in `services/control-plane/governance/evolution_controller.py`
- passing service-level tests in `services/evolution/test_evolution_service.py`

Verification run for this sidecar:

- `python3 -m pytest services/evolution/test_evolution_service.py -q` -> `39 passed in 2.52s`

Practical implication:

- the parent task likely needs verification, any final gap note, and task-board closeout more than net-new implementation

## 2. Dependency Map

### 2.1 Canonical inputs the parent must reuse

| Source | Locked truth reused by current implementation |
|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | risk tiers, reviewed/approved owner matrix, threshold families, freeze vs rollback separation |
| `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` | cooldown windows, observation windows, single-active-rule, rollback companion does not open a new evolution window |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | rollback is operational mitigation; Runtime Manager is the only binding writer; approval chain is inherited from the parent path |

### 2.2 Effective upstream execution dependencies already reflected in code

| Dependency | Evidence in repo | Why it matters |
|---|---|---|
| Evolution decision lifecycle | `services/evolution/main.py` | exposes propose/review/approve/execute/boundary endpoints |
| Action boundary router | `services/control-plane/governance/evolution_controller.py` | maps action path -> execution plane, owner roles, cooldown, observation, follow-through |
| Acceptance coverage | `services/evolution/test_evolution_service.py` | verifies lifecycle, boundary query, threshold evaluation, freeze mode validation, owner enforcement |

### 2.3 Downstream consumers

| Consumer | Why DEPTH-EVO004 output is needed |
|---|---|
| `DEPTH-EVO005` | fast-path emergency flow depends on a clear normal-path evolution boundary |
| `APP-002` / BFF evolution surfaces | operator UI needs stable boundary and follow-through semantics for freeze / rollback / retrain / redeploy |

## 3. Acceptance Evidence Map

The durable task acceptance is:

> freeze、rollback、retrain、redeploy 各有 API endpoint 且文件化 owner/threshold/cooldown
> cooldown enforcement 有測試（重複觸發被拒）
> EVO-004 正式關閉或在 DEVELOPMENT_WORKBREAKDOWN.md 標記 done

Expanded evidence:

| Acceptance item | Evidence | Verdict |
|---|---|---|
| Freeze path has API surface and owner/threshold/cooldown boundary | `POST /api/evolution/proposals/{decision_id}/execute`, `GET /api/evolution/proposals/{decision_id}/boundary`, plus `boundary_for()` returns `freeze_live_active_runtime`, `freeze_live_no_active_runtime`, `freeze_non_live` with owner roles and 7/14-day windows | PASS |
| Rollback path is formalized without collapsing into freeze | `boundary_for()` exposes runtime rollback follow-through, `dispatch_approved()` emits `RollbackCommand`, and `ROLLBACK_AND_POSITION_SEMANTICS.md` is preserved as the operational writer truth | PASS |
| Retrain path is explicit and research-scoped | research actions route to `ExecutionPlane.RESEARCH`; boundary note says execution means governed research work item with no direct deploy/runtime mutation | PASS |
| Redeploy follow-through is explicit | `create_redeploy_followthrough()` builds a deployment-plane command, requires fresh `approval_decision_id`, enforces observation-window timing, and states `requires_new_deployment_plan` in metadata | PASS |
| Threshold mapping exists | `ThresholdEvaluator.classify()` covers performance degradation, execution drift, feature drift, human correction, governance incident, and manual review | PASS |
| Cooldown / observation metadata is enforced and tested | lifecycle tests assert low-risk `3d/7d`, medium-risk `7d`, high-risk `14d`; service execution auto-populates timestamps | PASS |
| Repeated trigger rejection / single-active-rule exists | propose path enforces single-active-rule via domain object validation; test suite includes single-active-rule coverage | PASS |
| Formal closeout in task board / backlog | still a parent-owner action; sidecar cannot close canonical task or mark workbreakdown done | OPEN |

## 4. Action Path Snapshot

| Path | Execution plane | Owner boundary | Cooldown / observation | Operational follow-through |
|---|---|---|---|---|
| `freeze` on `paper` / `canary` | governance | medium-risk reviewed/approved roles from canonical matrices | `7d / 7d` | optional `deployment.freeze_stage` when active runtime exists |
| `freeze` on `live` with active runtime | governance | high-risk committee path | `14d / 14d` | `deployment.freeze_stage` and/or `runtime.rollback` companion depending on execute mode |
| `rollback` companion | runtime | inherited from parent incident/evolution approval chain | no separate evolution window | emitted as `RollbackCommand`; Runtime Manager remains sole writer |
| `retrain` | research | low-risk reviewer-on-duty path | `3d / 7d` | governed research work item only |
| redeploy follow-through | deployment | fresh deployment approval required | inherits parent observation and stage policy | explicit deployment command, not a shadow runtime command |

## 5. Reviewer Focus For Claude

- Check whether the parent task should now be treated as verification/closeout rather than new implementation work. The code path and tests are already present.
- Confirm the naming bridge between backlog `EVO-004` and task-board `DEPTH-EVO004` so final closure updates the right record.
- Preserve the distinction that `freeze` is governance quarantine while rollback is only a companion operational mitigation.
- Preserve the deployment chain for redeploy follow-through. Current code correctly requires a fresh deployment approval and avoids direct runtime mutation.

## 6. Recommended Parent Closeout Steps

1. Re-run `python3 -m pytest services/evolution/test_evolution_service.py -q`.
2. Review `services/evolution/main.py` and `services/control-plane/governance/evolution_controller.py` against the acceptance table above.
3. If no further gap is found, move `DEPTH-EVO004` through review with a note that implementation already satisfies the action-boundary acceptance and only formal task closure remained.
4. Update the parent task / reviewer record. Do not let the existing implementation stay stranded behind a stale `todo`.

## 7. Files Referenced

- `services/evolution/main.py`
- `services/control-plane/governance/evolution_controller.py`
- `services/evolution/test_evolution_service.py`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
