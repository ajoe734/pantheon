# OSS-004C Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-004C-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-004C` - run integrated governed paper execution acceptance for EP4
**Parent owner:** `Codex`
**Parent reviewer:** `Claude`
**Prepared by:** `Claude`
**Date:** `2026-04-19`
**Packet status:** `prepared from current repo snapshot; ready for Codex review`

> Scope constraint: support artifact only. This packet does not modify canonical truth, L1 policy,
> runtime/deployment contracts, registry or governance truth, or the parent implementation. It
> packages the current OSS-004C acceptance surface, dependency map, and reviewer handoff context.

---

## 1. Purpose

This sidecar exists to reduce archival/review lookup cost for `OSS-004C` by doing four things:

1. restate the active parent-task truth from planning/state without reopening global history
2. map the upstream completed substrates (`OSS-004A`, `OSS-004B`) to the EP4 integrated acceptance
   boundary
3. expand the short parent acceptance criteria into a reviewer-facing checklist covering each
   acceptance plane: approval, deployment, runtime binding, paper execution, telemetry,
   incident/health, kill-switch, and rollback
4. hand `Codex` a compact acceptance surface that does not require re-reading all prior sidecar
   packets

This is intentionally narrower than the implementation work `OSS-004C` itself does. It is meant to
support the reviewer without prescribing implementation steps.

---

## 2. Parent Task Truth

From the current `ai-status.json` snapshot used for this review:

- owner: `Codex`
- reviewer: `Claude`
- phase: `Phase 7: EP4 Proof Run`
- status: `review`
- formal dependencies: `OSS-004A` (done), `OSS-004B` (done)
- recorded acceptance:
  - `one integrated EP4 acceptance run is archived`
  - `evidence covers approval, runtime, telemetry, incident, and rollback together`
- current archived evidence packet:
  - `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z`

The accepted phase-7 planning session framed `OSS-004C` as:

> Run and archive one governed paper execution acceptance proving approval → deployment → runtime
> binding → paper execution → telemetry → incident/health → kill-switch/rollback as one EP4
> packet.

`OSS-004C` is therefore the first task allowed to claim the integrated governed paper execution
proof for EP4. The previous two tasks provided the substrate:

- `OSS-004A` made the runtime auth/authority path explicit
- `OSS-004B` replaced the VM-2 bootstrap stub with the truthful paper execution package

---

## 3. Sidecar Scope Boundary

In scope for this sidecar:

- restate the parent proof boundary and acceptance surface from planning/state
- map each named EP4 acceptance plane to the current repo substrate
- expand the short parent acceptance into a multi-plane checklist
- record what `OSS-004C` must produce as evidence and what it must defer to `OSS-004D`
- hand the packet to `Codex` as sidecar reviewer without modifying parent task state

Out of scope:

- executing a real dual-VM governed paper run
- modifying `services/execution/lean_runtime/`, `services/governance/`, `services/runtime-manager/`,
  telemetry, deployment, or any canonical truth file
- claiming `EP4` completion on behalf of `OSS-004C`; that is a parent-owner decision
- advancing `OSS-004D` or publishing the EP4 evidence packet

---

## 4. Upstream Dependency Status

### 4.1 OSS-004A: runtime auth/authority path (done, archived)

Commit `3891348` (`OSS-004A-SIDECAR-ACCEPTANCE`) and prior parent work confirmed:

- runtime-manager write routes require a non-empty bearer token
- execution-only token is scoped to VM-2 via `docker-compose.exec.yml`
- paper-runtime identity is explicit: role `pantheon-lean-paper-runtime`, mode `paper`, with
  `runtime_manager_url` and `workspace_ref` / `auth_profile_ref`
- telemetry authority refs are explicit in `services/telemetry/telemetry_event.schema.json`:
  `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`,
  `deployment_stage`, `plan_id`, `persona_capital_binding_id`
- OpenClaw adapter boundary locks OpenClaw as a runtime substrate without governance authority

The OSS-004C integrated run should consume this substrate without reopening it.

### 4.2 OSS-004B: truthful paper execution package (done, archived)

Commit `20c902d` (`OSS-004B`) confirmed:

- `services/execution/lean_runtime/paper_runtime.py` provides the concrete paper execution runtime
  package: `PendingSignalStore`, `SignalConsumer`, `RuntimeBindingResolver`,
  `RuntimeTelemetryEmitter`, and in-process paper execution with position tracking
- `services/execution/lean_runtime/runtime_bootstrap.py` dispatches the paper runtime role into
  `paper_runtime.main()` — bootstrap-only behavior is retired for the paper role
- `docker-compose.exec.yml` packages the VM-2 stack as `runtime-manager` + truthful paper
  execution runtime + mock broker/exchange sidecars with an execution-only secret boundary
- 12 unit tests pass across `test_signal_consumer`, `test_runtime_identity`, and `test_paper_runtime`

The OSS-004C integrated run therefore has a concrete signal-consumer/runtime package to run against.

---

## 5. Acceptance Checklist

This checklist expands the short parent acceptance into reviewer-facing gates across all named EP4
proof planes.

### Plane A: Governance Approval

| Check | What "done" means | Current substrate |
|---|---|---|
| A-1 Approval route exists and enforces write authority | `services/governance/main.py` accepts approval decisions behind an auth boundary | Exists — `services/governance/main.py` provides approval routes with write-authority checks |
| A-2 Approval decision writes authoritative state | `ApprovalDecision` is persisted and returns a stable `approval_id` | `services/governance/models.py` + `audit_log.py` define the state surface |
| A-3 Governed transition: approved plan can enter deployment | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` chain — `ApprovalDecision -> DeploymentPlan -> RuntimeBinding` — is traversable in the integrated run | Canonical chain is locked in L1 policy |

### Plane B: Deployment

| Check | What "done" means | Current substrate |
|---|---|---|
| B-1 DeploymentPlan dispatches from VM-1 | Deployment service creates and dispatches a plan tied to the approval | Deployment service at `services/deployment/` |
| B-2 Deployment saga records binding creation | VM-1 saga records `binding_created` and `runtime_active` events | Saga pattern documented in `dual-vm-acceptance-results.md` |
| B-3 Plan dispatch response is archived as evidence | `deployment-plan-dispatch.response.json` and `deployment-saga-detail.response.json` are recorded | Evidence template defined in `dual-vm-acceptance-results.md` |

### Plane C: Runtime Binding

| Check | What "done" means | Current substrate |
|---|---|---|
| C-1 RuntimeBinding is created on VM-2 runtime-manager | `runtime-deploy.response.json` shows binding created | VM-2 runtime-manager at `services/runtime-manager/main.py` |
| C-2 Binding carries truthful authority refs | `binding_id`, `runtime_id`, `plan_id`, `persona_capital_binding_id` are all present | Locked by `services/execution/runtime-manager/contract.md` |
| C-3 Binding identity crosses VM boundary cleanly | VM-1 telemetry can join on the binding created by VM-2 runtime-manager | Proven by `dual-vm-acceptance-results.md` acceptance item |

### Plane D: Paper Execution

| Check | What "done" means | Current substrate |
|---|---|---|
| D-1 Paper runtime starts on VM-2 with correct role | `paper_runtime.py` starts as `pantheon-paper-execution-runtime` with runtime-manager binding resolved | `docker-compose.exec.yml` + `runtime_bootstrap.py` |
| D-2 Signal consumer pulls pending signals | `PendingSignalStore` + `SignalConsumer` path is exercised | `services/execution/lean_runtime/pending_signal_store.py` + `signal_consumer.py` |
| D-3 In-process paper execution runs at least one order cycle | Position changes reflect simulated fills | `PaperExecutionAlgorithm` in `paper_runtime.py` |
| D-4 Paper runtime emits telemetry-ready envelopes | `RuntimeTelemetryEmitter` fires at least one event with binding/runtime/plan refs | `paper_runtime.py` telemetry path |

### Plane E: Telemetry

| Check | What "done" means | Current substrate |
|---|---|---|
| E-1 Telemetry ingest receives events from VM-2 | VM-1 ingest counter increments after paper execution cycle | `telemetry-stats-before-runtime.response.json` vs `telemetry-stats-after-deploy.response.json` |
| E-2 Events carry canonical authority refs | Each event has `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `deployment_stage` | Schema locked in `services/telemetry/telemetry_event.schema.json` |
| E-3 Telemetry authority refs join the deployed binding | VM-1 can resolve the `binding_id` from VM-2 via runtime-manager lookup | Already tested in dual-VM harness baseline |

### Plane F: Incident / Health

| Check | What "done" means | Current substrate |
|---|---|---|
| F-1 Paper runtime health endpoint is responsive | `GET /health` returns `200` while runtime is active | `paper_runtime.py` HTTP server |
| F-2 Kill-switch health check reflects paused state | After kill-switch, health endpoint or binding status reflects `paused` | `kill-switch-dispatch.response.json` acceptance item |
| F-3 Incident signal is observable from VM-1 | VM-1 can query binding status or telemetry to detect the paused state | Runtime-manager route on VM-2 + VM-1 telemetry lookthrough |

### Plane G: Kill-Switch

| Check | What "done" means | Current substrate |
|---|---|---|
| G-1 VM-1 kill-switch stops the VM-2 binding | `kill-switch-dispatch.response.json` shows `binding.status = paused` | VM-2 runtime-manager kill-switch route |
| G-2 Safe mode is set and persisted | `kill-switch-safe-mode.response.json` shows the runtime-manager safe-mode flag is set | Safe-mode implementation via `DEPTH-EVO005` (commit `d0eb7ec`) |
| G-3 Audit log records the kill-switch event | `kill-switch-audit-log.response.json` shows an audit entry | `services/governance/audit_log.py` |

### Plane H: Rollback

| Check | What "done" means | Current substrate |
|---|---|---|
| H-1 VM-1 rollback executes on VM-2 | `rollback-execute.response.json` shows `old_binding.status = retired` and `new_binding.status = active` | VM-2 runtime-manager rollback route |
| H-2 Telemetry reflects the post-rollback binding | `telemetry-stats-after-rollback.response.json` shows counter updates tied to the new binding | VM-1 ingest + runtime-manager lookup |
| H-3 Rollback evidence stays within EP4 scope | Rollback is proven for paper execution; live rollback semantics remain deferred to EP5 | `ROLLBACK_AND_POSITION_SEMANTICS.md` boundary |

---

## 6. Evidence Packet Shape

The archived `OSS-004C` packet follows the evidence categories from
`docs/deployment/dual-vm-acceptance-results.md`, but the repo-current EP4 packet uses richer
per-step `*.request.json` / `*.response.json` filenames so every proof plane can be audited
directly. Reviewer should expect at least the following evidence categories in the archived packet:

```text
ep4-acceptance-run-<timestamp>/
  summary.json                                             # overall pass/fail + run metadata
  approval-*.request.json / approval-*.response.json       # Plane A
  deployment-plan-dispatch.response.json                   # Plane B
  deployment-saga-detail.response.json                     # Plane B
  runtime-deploy.response.json                             # Plane C
  paper-runtime-health.response.json                       # Plane D
  paper-runtime-state-after-signal.response.json           # Plane D
  telemetry-stats-before-runtime.response.json             # Plane E baseline
  telemetry-stats-after-deploy.response.json               # Plane E after paper execution
  incident-create.response.json                            # Plane F
  incident-operator-payload.response.json                  # Plane F
  incident-resolve.response.json                           # Plane F
  kill-switch-dispatch.response.json                       # Plane G
  kill-switch-safe-mode.response.json                      # Plane G
  kill-switch-audit-log.response.json                      # Plane G
  rollback-execute.response.json                           # Plane H
  telemetry-stats-after-rollback.response.json             # Plane H
```

The current archived `summary.json` records the proof run in repo-current field names:

| Field | Value |
|---|---|
| `run_timestamp_utc` | ISO-8601 UTC |
| `source_task_id` | `OSS-004C` |
| `approval_decision_id` | generated approval decision ID |
| `plan_id` | generated plan ID |
| `saga_id` | generated saga ID |
| `initial_binding_id` | binding ID after deploy |
| `replacement_binding_id` | binding ID after rollback |
| `incident_id` | generated incident ID |
| `deploy_event_id` | deploy-side telemetry event ID |
| `rollback_event_id` | rollback-side telemetry event ID |
| `signal_id` | generated paper signal ID |
| `paper_runtime_id` | paper runtime identity |
| `telemetry_counter_before_runtime` | integer count before runtime activity |
| `telemetry_counter_after_deploy_event` | integer count after deploy-side telemetry |
| `telemetry_counter_after_rollback_event` | integer count after rollback-side telemetry |
| `telemetry_trace_after_deploy_status` | HTTP status for deploy trace lookup |
| `telemetry_trace_after_rollback_status` | HTTP status for rollback trace lookup |
| `runtime_emitted_telemetry_sent` | runtime-emitted telemetry counter |
| `processed_signal_count` | processed signal count |
| `execution_event_count` | paper execution event count |
| `kill_switch_state` | expected paused state before rollback |
| `rollback_action_type` | expected `pause_then_replace` |
| `overall_result` | `"pass"` or `"fail"` |
| `output_dir` | archived packet directory |

The run-local service URLs and the telemetry trace caveat are recorded in the packet `README.md`
rather than in `summary.json`. The support requirement here is that the packet remain clearly
bounded to `EP4` and not silently claim `EP5`.

---

## 7. Scope Boundary — What OSS-004C Must Not Claim

`OSS-004C` closes the EP4 integrated paper execution loop. It must not silently absorb claims that
belong to later EP5 or production phases:

| Claim | Belongs to | Why not OSS-004C |
|---|---|---|
| Real LEAN order execution with live broker | EP5 canary phase | Requires non-mock broker/venue config |
| Broker-side order acknowledgement | EP5-001 | Needs real account and venue |
| End-to-end production signal delivery | EP5+ | Production `SignalStore` requires live infra |
| Final JWT/issuer runtime auth | Post-EP4 hardening | OSS-004A explicitly scoped bearer presence only |
| Canary/live rollback drill | EP5-001 | ROLLBACK_AND_POSITION_SEMANTICS.md defers to EP5 |

---

## 8. Dependency Map

### 8.1 Hard upstream task dependencies

| Task | Status | Relevance |
|---|---|---|
| `OSS-004A` | `done` (archived) | runtime auth/authority path, token isolation, telemetry authority refs, OpenClaw boundary |
| `OSS-004B` | `done` (archived) | truthful VM-2 paper execution package, `paper_runtime.py`, compose/env/docs updated |

### 8.2 Repo-local implementation dependencies

| Input | Why it matters |
|---|---|
| `services/governance/main.py` | approval decision routing and write-authority enforcement |
| `services/governance/models.py` | `ApprovalDecision` persistence |
| `services/governance/audit_log.py` | audit event recording for governance plane |
| `services/runtime-manager/main.py` | authoritative `RuntimeBinding` write surface; kill-switch, rollback, and safe-mode routes |
| `services/execution/lean_runtime/paper_runtime.py` | concrete paper execution runtime package |
| `services/execution/lean_runtime/pending_signal_store.py` | signal retrieval surface |
| `services/execution/lean_runtime/signal_consumer.py` | signal-consumer path |
| `services/execution/lean_runtime/runtime_bootstrap.py` | role dispatch into paper runtime |
| `services/telemetry/telemetry_event.schema.json` | canonical authority refs in every telemetry event |
| `docker-compose.exec.yml` | VM-2 execution-plane packaging |
| `docker-compose.control.yml` | VM-1 control-plane packaging |
| `scripts/smoke_test_dual_vm.sh` | canonical dual-VM acceptance command; OSS-004C extends this for the full EP4 loop |
| `docs/deployment/dual-vm-acceptance-results.md` | evidence template and acceptance mapping |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | locks `ApprovalDecision -> DeploymentPlan -> RuntimeBinding` chain |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | locks EP4 rollback semantics boundary |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | kill-switch fast-path policy from DEPTH-EVO005 |

### 8.3 Downstream tasks unblocked by OSS-004C completion

| Task | Relation | Why it matters |
|---|---|---|
| `OSS-004D` | hard downstream | publishes the EP4 evidence packet and reconciles status layers; requires an archived `OSS-004C` run |
| `EP5-001` | transitive downstream | canary-ready execution path work; can only begin after stable EP4 |

### 8.4 Sequencing summary

```text
OSS-004A (authority path explicit, done)
        |
        v
OSS-004B (truthful VM-2 paper execution package, done)
        |
        v
OSS-004C (first integrated governed paper acceptance packet) ← this task
        |
        v
OSS-004D (publish EP4 evidence packet and reconcile status truth)
        |
        v
EP5-001 (canary-ready execution path preparation)
```

---

## 9. Reviewer Handoff Notes

For `Codex` as the assigned sidecar reviewer:

1. Confirm this packet accurately restates the `OSS-004C` parent scope without overclaiming any
   single upstream substrate as EP4 completion.
2. Confirm the checklist planes (A–H) cover the parent acceptance boundary:
   `approval → deployment → runtime binding → paper execution → telemetry → incident/health →
   kill-switch → rollback`.
3. Confirm the evidence packet shape maps to the existing `dual-vm-acceptance-results.md` template
   without creating new canonical obligations.
4. Confirm the scope-boundary table correctly keeps EP5 claims deferred.
5. Approve or request corrections on the packet as a support artifact.

Suggested review commands:

```bash
AI_NAME=Codex python3 scripts/ai_status.py approve OSS-004C-SIDECAR-ACCEPTANCE \
  "Acceptance packet approved: OSS-004C sidecar accurately restates the eight EP4 acceptance planes, upstream dependency status, evidence bundle shape, and EP5 scope boundary without modifying canonical truth."
```

If corrections are needed:

```bash
AI_NAME=Codex python3 scripts/ai_status.py reopen OSS-004C-SIDECAR-ACCEPTANCE \
  "Describe the specific corrections needed."
```

---

## 10. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no L1 or L2 canonical document was modified
- no runtime, deployment, registry, governance, or telemetry implementation file was modified by
  this sidecar
- no global summary file was edited manually
- parent-task ownership and review state remain unchanged
- whether to absorb this packet into the parent review trail remains a parent-owner decision

*Prepared by Claude for the `OSS-004C-SIDECAR-ACCEPTANCE` sidecar slice. This file is
intentionally support-only and does not modify canonical truth.*
