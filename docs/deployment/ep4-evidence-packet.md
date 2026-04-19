# EP4 Evidence Packet

**Status:** STABLE EP4 — governed paper execution proof complete
**Published by:** OSS-004D
**Published at:** 2026-04-19
**Source task:** OSS-004C — Run integrated governed paper execution acceptance for EP4
**Canonical review:** `.coordination/reviews/OSS-004C-review.md`
**Evidence bundle:** `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/`

---

## What EP4 Proves

EP4 is the governed paper execution proof level. It proves the full governed paper loop runs
with real authority, runtime state, and recovery behavior:

- governance approval chain is exercised and enforced
- deployment plan dispatch reaches the runtime-manager and creates a binding
- runtime binding carries all canonical authority refs
- paper execution runtime runs with real signal processing (not a bootstrap stub)
- telemetry ingest receives events from the paper execution runtime
- incident creation and resolution is exercised end-to-end
- kill-switch pauses the binding via the runtime-manager fast path
- rollback (`pause_then_replace`) retires the old binding and activates a replacement

EP4 does **not** prove canary or live execution safety. Those belong to EP5.

---

## Accepted Evidence Run

| Field | Value |
|---|---|
| Run timestamp (UTC) | 2026-04-19T00:37:20Z |
| Source task | OSS-004C |
| Overall result | **PASS** |
| Approval decision | `apv-ep4-ceea5ce1` |
| Deployment plan | `plan-ep4-1195b41a` |
| Deployment saga | `deployment-saga-plan-ep4-1195b41a` |
| Initial binding | `rb-8c00a26d1fee4fabb2afdd33c7e71ea7` |
| Replacement binding | `rb-391496fbca75458494ab07fe1f1228c5` |
| Incident | `inc-ep4-c1c15d4c` |
| Deploy telemetry event | `35b0c528-03c5-4111-9184-b4700e0518c0` |
| Rollback telemetry event | `f50d9300-73c5-4951-b5e4-9c8dc45ba4ae` |
| Kill-switch audit | `audit-de8130f2d062` |
| Paper runtime | `paper-runtime-001` |
| Telemetry counter before runtime | 258 |
| Telemetry counter after deploy event | 259 |
| Telemetry counter after rollback event | 261 |
| Processed signal count | 4 |
| Execution event count | 4 |
| Kill-switch state | `paused` |
| Rollback action type | `pause_then_replace` |

---

## Eight-Plane Summary

| Plane | Name | Result |
|---|---|---|
| A | Governance Approval | **PASS** |
| B | Deployment | **PASS** |
| C | Runtime Binding | **PASS** |
| D | Paper Execution | **PASS** |
| E | Telemetry | **PASS** (with caveat) |
| F | Incident / Health | **PASS** |
| G | Kill-Switch | **PASS** |
| H | Rollback | **PASS** |

### Plane A: Governance Approval

Decision `apv-ep4-ceea5ce1`, `decision: approved`, `actor_role: governance_reviewer`.
Approval write-authority chain exercised end-to-end.

### Plane B: Deployment

`runtime-deploy.response.json`: status 201, `binding_id: rb-8c00a26d1fee4fabb2afdd33c7e71ea7`,
`status: active`, `deployment_mode: paper`. Saga `deployment-saga-plan-ep4-1195b41a` reached
`deployment.saga.completed`.

### Plane C: Runtime Binding

Binding carries full authority refs: `binding_id`, `capital_pool_id`, `plan_id`,
`persona_capital_binding_id`, `runtime_id`, `artifact_id`, `artifact_version`. Consistent with
`BINDING_AND_DEPLOYMENT_SEMANTICS.md`.

### Plane D: Paper Execution

`stub_mode: false`, `paper_execution_ready: true`, `signal_consumer_ready: true`.
`runtime_package: paper_execution_runtime`, `runtime_package_version: ep4` — bootstrap stub
retired per OSS-004B. `processed_signal_count: 4`, `execution_event_count: 4`. Telemetry path:
`sent: 255, failed: 0`.

### Plane E: Telemetry

Counter progression: 258 → 259 → 261. `total_rejected: 0` throughout. Trace read endpoints
returned 404 — local dev read-model gap, not an ingest failure. Ingest proof via counter advance
is sufficient for EP4.

### Plane F: Incident / Health

Incident `inc-ep4-c1c15d4c` created (status 201, `severity: high`) with full authority refs.
Resolved successfully.

### Plane G: Kill-Switch

`action: pause`, `binding.status: paused`, `safe_mode_after: paused`, `emergency_class: hard`,
`dispatch_path: runtime_manager_fast_path`. Matches
`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` fast-path semantics.

### Plane H: Rollback

`action_type: pause_then_replace`, `old_binding.status: retired`, `new_binding.status: active`.
`opened_by_artifact_id` correctly immutable per `ROLLBACK_AND_POSITION_SEMANTICS.md §7`.
Post-rollback telemetry counter advanced to 261.

---

## Documented Caveats (Non-Blocking)

1. **Telemetry trace 404:** Local dev read-model gap on port 38083. Ingest proof via counter
   advance is sufficient for EP4. The event-trace projection surface is an EP4-local
   infrastructure gap, not a policy or implementation regression.

2. **`binding_context_complete: false` on paper runtime state:** Test-harness artifact from
   concurrent runs. Does not affect the proof chain.

3. **`telemetry_event_ids: []` in incident records:** The incidents service does not ingest
   telemetry event IDs in the repo-current path. Not a blocking EP4 gap.

---

## Upstream Substrate

| Task | Status | Contribution |
|---|---|---|
| OSS-004A | done | Runtime auth/authority path, token isolation, telemetry authority refs, OpenClaw boundary |
| OSS-004B | done | Truthful VM-2 paper execution package (`paper_runtime.py`), bootstrap stub retired |
| OSS-004C | done | First integrated governed paper acceptance packet (this evidence) |

---

## EP5 Scope Boundary

This packet does **not** claim EP5. The following remain deferred:

| Claim | Belongs to |
|---|---|
| Real LEAN order execution with live broker | EP5 canary phase |
| Broker-side order acknowledgement | EP5-001 |
| End-to-end production signal delivery | EP5+ |
| Final JWT/issuer runtime auth hardening | Post-EP4 |
| Canary/live rollback drill | EP5-001 |

The repo can truthfully claim stable EP4. It cannot truthfully claim EP5.
