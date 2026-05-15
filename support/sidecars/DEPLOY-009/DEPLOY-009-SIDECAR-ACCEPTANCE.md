# DEPLOY-009 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `DEPLOY-009-SIDECAR-ACCEPTANCE`
**Helper parent:** `DEPLOY-009` - dual-VM acceptance harness
**Parent owner:** `Codex`
**Parent reviewer:** `Codex2`
**Prepared by:** `Codex2`
**Date:** `2026-04-18`
**Packet status:** `review approved; finalized for sidecar handoff`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth,
> deployment/runtime contracts, registry or governance truth, or the primary DEPLOY-009
> implementation. It packages the current acceptance surface, dependency map, and reviewer
> handoff context for the already-delivered parent task.

---

## 1. Purpose

This sidecar exists to make `DEPLOY-009` easy to audit after parent delivery:

1. restate the accepted dual-VM scope from durable state and archived parent closeout
2. map the delivered artifacts to the cross-VM acceptance flow they are intended to prove
3. record the dependency chain that makes the harness meaningful
4. hand the packet to the assigned sidecar reviewer without reopening canonical planning history

---

## 2. Parent Task Truth

From the archived `DEPLOY-009` snapshot in `ai-task-archive/tasks/DEPLOY-009.json`:

- parent status: `done`
- terminal outcome: `completed`
- archived at: `2026-04-18T00:34:18Z`
- owner: `Codex`
- reviewer: `Codex2`
- upstream dependencies: `DEPLOY-007`, `DEPLOY-008`
- delivered artifacts:
  - `scripts/smoke_test_dual_vm.sh`
  - `docs/deployment/dual-vm-acceptance-results.md`
  - `docs/deployment/operator-failover-guide.md`
- recorded delivery commit: `7587f2099427f7c00542f876cd7ff2d576ad63e7`

Archived review notes already lock the intended acceptance boundary:

- the smoke harness was checked against existing deployment, runtime-manager, and telemetry API
  contracts
- local verification covered `bash -n` and `--help`
- no real dual-VM acceptance run was executed inside this workspace
- the VM-2 paper runtime is still a bootstrap stub, so the parent task proves cross-VM binding,
  telemetry, kill-switch, and rollback flow, not the final LEAN order loop

This sidecar does not reopen that disposition. It records it clearly for future review and reuse.

---

## 3. Delivered Artifact Surface

### 3.1 Smoke harness

`scripts/smoke_test_dual_vm.sh` now packages the end-to-end dual-VM control flow:

1. VM-1 health checks for deployment and telemetry services
2. VM-2 health checks for `runtime-manager` and paper runtime
3. `DeploymentPlan` validate/create/dispatch on VM-1
4. outbox consume plus deployment status progression on VM-1
5. `RuntimeBinding` creation on VM-2 through `/api/runtimes/deploy`
6. deployment saga callbacks on VM-1 for `binding_created` and `runtime_active`
7. telemetry ingest on VM-1 using the VM-2-created binding identity
8. kill-switch dispatch from VM-1 to VM-2
9. rollback execution on VM-2
10. post-rollback telemetry backflow to VM-1
11. generation of a durable `summary.json` acceptance artifact

The harness therefore covers the exact parent-task path:

`DeploymentPlan` on VM-1 -> `RuntimeBinding` on VM-2 -> telemetry return to VM-1 ->
kill-switch / rollback initiated from VM-1 and executed by VM-2.

### 3.2 Acceptance record template

`docs/deployment/dual-vm-acceptance-results.md` provides:

- the accepted scope for a real dual-VM run
- preconditions for VM-1 and VM-2
- the canonical smoke command
- the evidence files that must be retained from a real pass
- a fill-in result template for later operator execution
- an explicit limitation note that the runtime is still a bootstrap stub

### 3.3 Operator failover runbook

`docs/deployment/operator-failover-guide.md` turns the same contract into an operator path for:

- execution-plane health confirmation
- emergency kill-switch
- rollback to fallback artifact
- telemetry proof back to VM-1
- safe-mode progression back toward normal operation

This keeps DEPLOY-009 useful even when the full scripted smoke is not the preferred operational
entrypoint.

---

## 4. Acceptance Checklist

This checklist reflects the delivered parent task and the repo snapshot as of this sidecar pass.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Dual-VM smoke harness exists | Met | `scripts/smoke_test_dual_vm.sh` present |
| 2 | Harness covers VM-1 `DeploymentPlan` dispatch | Met | script validates, creates, and dispatches a plan on VM-1 |
| 3 | Harness covers VM-2 `RuntimeBinding` creation | Met | script calls `/api/runtimes/deploy` and captures `binding_id` |
| 4 | Harness covers telemetry backflow to VM-1 | Met | script posts deploy/rollback telemetry and verifies stats increments |
| 5 | Harness covers VM-1 initiated kill-switch on VM-2 | Met | script calls `/api/kill-switch/dispatch` and verifies paused state |
| 6 | Harness covers rollback on VM-2 | Met | script calls `/api/rollback` and verifies retired -> active binding transition |
| 7 | Acceptance evidence template exists | Met | `docs/deployment/dual-vm-acceptance-results.md` present |
| 8 | Operator failover path exists | Met | `docs/deployment/operator-failover-guide.md` present |
| 9 | Boundary note forbids overstating execution proof | Met | both docs state VM-2 paper runtime is a bootstrap stub |
| 10 | Real two-host pass evidence recorded in this workspace | Not claimed | archived parent review notes explicitly say no real dual-VM run occurred locally |
| 11 | Sidecar stayed support-only | Met | this packet adds support material only |

### Acceptance interpretation

The parent task is already closed as a delivered **acceptance harness**. The current repo truth is
therefore:

- the harness and operator documentation are implemented
- the intended runtime boundary is documented honestly
- a future real dual-VM run should attach `summary.json` and companion JSON outputs as operational
  evidence

This packet should not be read as disputing the parent closeout. It makes the accepted boundary
explicit so later reviewers do not overread the task as full execution-loop proof.

---

## 5. Dependency Map

### 5.1 Hard upstream task dependencies

| Task | Status | Relevance |
|---|---|---|
| `DEPLOY-007` | `done` | supplies the VM-1 control-plane split that DEPLOY-009 targets as the control side of the dual-VM flow |
| `DEPLOY-008` | `done` | supplies the VM-2 execution-plane slice and bootstrap runtime that DEPLOY-009 exercises |

### 5.2 Repo-local implementation dependencies

| Input | Why it matters |
|---|---|
| `services/deployment/service.py` | receives plan validation, creation, dispatch, and saga updates on VM-1 |
| `services/runtime-manager/main.py` | owns execution-side binding creation, kill-switch, rollback, and safe-mode state on VM-2 |
| `services/telemetry/*` | accepts telemetry events and proves VM-1 can resolve VM-2 binding identity |
| `services/execution/lean_runtime/runtime_bootstrap.py` | defines the current paper-runtime bootstrap boundary used by the smoke |
| `docker-compose.control.yml` | represents the VM-1 service surface established by `DEPLOY-007` |
| `docker-compose.exec.yml` | represents the VM-2 service surface established by `DEPLOY-008` |

### 5.3 Operational evidence outputs

If a later operator runs the smoke against two reachable hosts, the following become the primary
runtime evidence set:

- `summary.json`
- `plan-dispatch-response.json`
- `saga-detail-response.json`
- `runtime-deploy-response.json`
- `kill-switch-response.json`
- `safe-mode-response.json`
- `kill-switch-audit-response.json`
- `rollback-response.json`
- `telemetry-stats-before.json`
- `telemetry-stats-after-deploy.json`
- `telemetry-stats-after-rollback.json`

### 5.4 Sequencing summary

```text
DEPLOY-007 (VM-1 control split) ──┐
                                  ├──► DEPLOY-009 (dual-VM acceptance harness)
DEPLOY-008 (VM-2 exec split)   ───┘
```

Practical meaning:

- `DEPLOY-009` only makes sense because the VM-1 and VM-2 surfaces were already separated
- the harness consumes those split surfaces; it does not redefine them

---

## 6. Reviewer Handoff Notes

For `Codex` as the assigned sidecar reviewer:

1. confirm this packet accurately reflects the archived parent closeout: delivered harness, no
   local two-host execution, explicit bootstrap boundary
2. confirm the dependency map is support-only and does not try to rewrite deployment semantics
3. confirm the acceptance checklist distinguishes `artifact implemented` from `runtime pass
   evidence attached`
4. approve the sidecar if it is a useful audit packet for future reruns or parent absorption

Suggested review disposition:

- approve if the packet is factually aligned with the archived parent task and current repo files
- do not treat the absence of a local dual-VM run in this workspace as a new blocker; it is already
  part of the accepted parent boundary note

---

## 7. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no L1 or L2 canonical document was modified
- no runtime, deployment, registry, governance, or telemetry implementation file was modified
- no global summary file was edited manually
- parent-task ownership and closeout remain unchanged
- whether to absorb this packet into parent review history remains a parent-owner decision

---

*Generated by `Codex2` as a sidecar `acceptance_packet` helper for `DEPLOY-009`. This file is a support artifact and does not modify canonical truth.*
