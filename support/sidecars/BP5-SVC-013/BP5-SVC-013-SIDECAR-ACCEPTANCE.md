# BP5-SVC-013 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `BP5-SVC-013-SIDECAR-ACCEPTANCE`  
**Helper parent:** `BP5-SVC-013` - Realize operational evolution orchestration and kill-switch fast path  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-16`  
**Parent state observed:** `done` (archived closeout recorded at `2026-04-16T02:55:07Z`)

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, registry truth, or governance truth. It records the accepted delivery surface,
> dependency map, and verification evidence for BP5-SVC-013 so the sidecar reviewer can absorb the
> outcome quickly without re-reading the full task history.

---

## 1. Scope Reminder

This sidecar is strictly observational.

- It does not alter `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
- It does not alter `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- It does not alter `services/runtime-manager/`
- It only captures the accepted repo evidence and downstream implications of the parent slice

The parent task is already archived as `done`, so this packet is a compact acceptance and handoff
record rather than a blocker for mainline closeout.

---

## 2. Upstream Dependency Status

| Dependency | Title | Status | Relevance |
|---|---|---|---|
| `BP5-SVC-008` | Realize rollback and replace execution actions through runtime-manager | `done` | BP5-SVC-013 reuses the canonical rollback / replace runtime-manager boundary and history path |
| `BP5-SVC-011` | Realize incident and postmortem evidence services | `done` | kill-switch / freeze follow-through can point at incident and postmortem evidence surfaces instead of narrative-only references |
| `BP5-SVC-012` | Realize the EvolutionDecision service and governance read path | `done` | freeze / retrain / redeploy follow-through now consumes governed `evolution_decision_id`, `deployment_plan_id`, and `research_job_id` boundaries |

No upstream dependency remains open for the BP5-SVC-013 acceptance surface.

---

## 3. Parent Delivery Snapshot

Parent-task closeout observed from the archived `BP5-SVC-013` snapshot:

| Field | Value |
|---|---|
| Parent terminal status | `done` |
| Parent terminal outcome | `completed` |
| Archived closeout time | `2026-04-16T02:55:07Z` |
| Closeout note | `Owner finalized: idempotent kill-switch/evolution-freeze cross-path verified, 138/138 smoke checks pass. All artifacts in services/runtime-manager/ accepted. Task closed.` |
| Review file | `.coordination/reviews/BP5-SVC-013-review.md` |
| Closeout commit | `c1fde31fc7f628655ed1384deb78b27c905b0b9c` |

Reviewer-approved notes carried on the parent task:

- `已驗證 kill-switch pause 後接 evolution_freeze 的交叉路徑，不再觸發 paused->paused 例外。`
- `新增 pending_pause 與 already-paused smoke coverage，python3 services/runtime-manager/smoke_test.py 通過 138/138。`

This matters because the final parent acceptance was not just route presence. The last review cycle
specifically required idempotent cross-path behavior when a governance freeze follows an earlier
kill-switch drain.

---

## 4. Artifact Inventory

The parent slice is concentrated in `services/runtime-manager/`.

| File | Realized responsibility |
|---|---|
| `services/runtime-manager/service.py` | implements the kill-switch fast path, binding-state execution helper, safe-mode read/advance, and evolution follow-through entry points (`evolution_freeze`, `evolution_retrain`, `evolution_redeploy`) |
| `services/runtime-manager/main.py` | exposes the authoritative HTTP routes for kill-switch dispatch, safe-mode operations, audit log, evolution freeze, evolution retrain, and evolution redeploy |
| `services/runtime-manager/runtime_manager_client.py` | exposes matching client methods so callers use the same canonical runtime-manager surface in local or HTTP mode |
| `services/runtime-manager/smoke_test.py` | provides service-layer and HTTP-layer smoke coverage for kill-switch, rollback, and evolution orchestration, including the final cross-path idempotency cases |

Implemented BP5-SVC-013 HTTP routes observed in `main.py`:

- `POST /api/kill-switch/dispatch`
- `GET /api/kill-switch/<pool_id>/safe-mode`
- `POST /api/kill-switch/<pool_id>/safe-mode`
- `GET /api/kill-switch/audit-log`
- `POST /api/evolution/freeze`
- `POST /api/evolution/retrain`
- `POST /api/evolution/redeploy`

---

## 5. Acceptance Checklist

Formal acceptance criteria from planning:

- `freeze, rollback, retrain, redeploy, kill-switch, and safe-mode actions all use explicit runtime-manager orchestration boundaries`
- `emergency fast path keeps auditability while meeting kill-switch latency expectations`

### 5.1 Explicit runtime-manager orchestration boundaries

| Check | Evidence | Status |
|---|---|---|
| kill-switch dispatch enters through one runtime-manager mutation path | `service.py` implements `execute_kill_switch()` and `_execute_kill_switch_binding_action()`; `main.py` exposes `POST /api/kill-switch/dispatch`; `runtime_manager_client.py` exposes `execute_kill_switch()` | PASS |
| kill-switch fast path executes real binding mutations instead of only emitting audit metadata | `_execute_kill_switch_binding_action()` handles `PAUSE`, `RISK_OFF`, `REPLACE`, `LIQUIDATE`, and `TERMINATE` against `RuntimeBinding` state | PASS |
| safe-mode read / recovery path is canonicalized through runtime-manager | `service.py` implements `get_safe_mode()` and `advance_safe_mode()`; `main.py` exposes matching `GET` and `POST` safe-mode routes | PASS |
| freeze follow-through consumes governed plan context instead of raw ad-hoc binding writes | `evolution_freeze()` requires `deployment_plan_id`, accepts only `freeze_binding` / `pause_then_freeze`, and rejects `liquidate_then_freeze` with explicit routing guidance back to kill-switch / rollback boundaries | PASS |
| retrain follow-through consumes authoritative downstream work-item identity | `evolution_retrain()` requires `research_job_id` and echoes it as `routing_ref` instead of fabricating synthetic receipts | PASS |
| redeploy follow-through consumes a structured `deployment_plan` object rather than raw artifact passthrough | `evolution_redeploy()` requires `deployment_plan` and validates the plan fields before calling `deploy()` | PASS |
| caller-side access uses the same contract in local and HTTP modes | `runtime_manager_client.py` exposes `evolution_freeze()`, `evolution_retrain()`, `evolution_redeploy()`, `get_safe_mode()`, `advance_safe_mode()`, and `get_kill_switch_audit_log()` | PASS |

### 5.2 Emergency fast path auditability and final regression coverage

| Check | Evidence | Status |
|---|---|---|
| kill-switch fast path always preserves audit output | `execute_kill_switch()` returns both `command` and immutable `audit_entry`; HTTP smoke asserts both fields are present | PASS |
| REPLACE emergency path hot-swaps to a fallback runtime instead of only retiring the old binding | `service.py` now deploys the fallback replacement first and retires the old binding after cutover; service and HTTP smoke both verify `replacement_binding` is active | PASS |
| governance freeze is idempotent after a prior kill-switch pause | `evolution_freeze()` tolerates bindings already in `pending_pause` or `paused`; smoke covers both already-paused and partial-drain cases | PASS |
| runtime-manager still rejects invalid or terminal evolution follow-through states | smoke covers terminal-binding rejection for `evolution_freeze()` and invalid action rejection for `evolution_retrain()` | PASS |
| runnable verification remains green after the final review-requested fixes | `python3 services/runtime-manager/smoke_test.py` returned `138 passed, 0 failed out of 138 checks` on `2026-04-16` | PASS |

Note on the latency criterion: this sidecar did not observe a dedicated microbenchmark artifact.
Acceptance is grounded in the final reviewer-approved shape: the emergency path remains in the
runtime-manager fast path, performs in-process binding mutation, and retains audit output without
adding a new cross-service detour.

---

## 6. Verification Snapshot

Command re-run for this sidecar:

```bash
python3 services/runtime-manager/smoke_test.py
```

Observed result on `2026-04-16`:

```text
Results: 138 passed, 0 failed out of 138 checks
```

Reviewer-relevant highlights from the smoke suite:

- kill-switch dispatch returns `command`, `audit_entry`, and `safe_mode_after`
- kill-switch `REPLACE` creates an active fallback replacement binding in both service and HTTP flows
- `evolution_freeze` pauses a live binding and now also succeeds when the binding is already `paused`
- `evolution_freeze` completes a prior `pending_pause` drain to `paused`
- `evolution_retrain` requires and returns an authoritative `research_job_id` / `routing_ref`
- `evolution_redeploy` creates an active binding from a structured `deployment_plan`

---

## 7. Dependency Map

### 7.1 Direct downstream tasks currently depending on `BP5-SVC-013`

| Task | Current status | Why BP5-SVC-013 matters |
|---|---|---|
| `BP5-SVC-015` | `todo` | BFF snapshot/default fallback removal depends on real runtime-manager action truth for operator actions instead of UI-local default behavior |
| `BP5-WB-004` | `in_progress` | Evolution Workbench follow-on packetization needs canonical kill-switch / freeze / redeploy semantics to describe operator and governance flows honestly |
| `BP5-LUV-006` | `todo` | the evolution-center Lovable loop depends on the now-realized runtime-manager orchestration contract |
| `BP5-LUV-008` | `todo` | post-incident review surfaces depend on the kill-switch / freeze evidence chain and safe-mode truth model |

### 7.2 Adjacent consumers that benefit immediately

| Consumer | Benefit |
|---|---|
| Control-plane BFF and operator command paths | can cite real runtime-manager endpoints for emergency and evolution follow-through rather than shadow command semantics |
| Incident and postmortem evidence flows | can reference kill-switch audit entries, safe-mode state, and governed evolution follow-through outputs |
| Workbench and Lovable slices | can build UI packets against executable HTTP surfaces instead of provisional contract text |

### 7.3 Policy anchors that shaped review

| Canonical source | Alignment captured by the parent implementation |
|---|---|
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | emergency dispatch, safe-mode transitions, and audit preservation are all routed through runtime-manager |
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | freeze, retrain, and redeploy follow-through consume governed decision / plan inputs instead of ad-hoc command payloads |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | rollback / replace behavior remains grounded in the runtime-manager execution plane already delivered by BP5-SVC-008 |

---

## 8. Approval And Closeout Notes

- `Claude` approved this packet on `2026-04-16`, confirming that the dependency map, artifact
  inventory, and `138/138` smoke verification are accurate and that the packet stays within
  support-only scope.
- `Codex` performed the final owner closeout after confirming that no further packet edits were
  required and that no canonical, runtime, registry, or governance truth changed as part of this
  sidecar.
- No further action is required from this slice unless the parent owner later chooses to absorb this
  support artifact into a broader review bundle.

---

## 9. Sidecar Scope Declaration

This file is the only support artifact created by this slice.

- No canonical L1 or L2 document was modified by this sidecar
- No runtime-manager implementation file was modified by this sidecar
- No registry, governance, or BFF truth was modified by this sidecar
- Parent-task absorption remains a parent-owner decision; this packet only records the accepted state
