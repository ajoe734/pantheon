# OSS-004B Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `OSS-004B-SIDECAR-REVIEW`
**Helper parent:** `OSS-004B` — replace bootstrap paper runtime with truthful paper execution package
**Parent owner:** `Codex`
**Parent reviewer:** `Claude`
**Prepared by:** `Claude`
**Date:** `2026-04-19`
**Packet status:** `ready for Codex sidecar review against the archived OSS-004B snapshot`

> Scope constraint: support artifact only. This packet does not edit canonical truth, runtime
> contracts, deployment contracts, or the parent implementation. It packages reviewer-focused
> guidance, evidence mapping, risk areas, and a checklist for `OSS-004B`.

---

## 1. Purpose

This review packet supports `Codex` as the assigned reviewer for the
`OSS-004B-SIDECAR-REVIEW` helper slice. It does three things:

1. restates the parent task's acceptance criteria and intended proof surface
2. maps current repo evidence to each criterion and flags partial or missing coverage
3. records risk areas most likely to surface disagreements during the review pass

This packet is complementary to the acceptance sidecar at
`support/sidecars/OSS-004B/OSS-004B-SIDECAR-ACCEPTANCE.md`, which expands the parent acceptance
into seven reviewer-facing gates (AC-1 through AC-7) and ran low-cost verification. This packet
translates those gates into review-time checklist items and names the failure modes to watch for.

---

## 2. Parent Task Summary

From the archived parent snapshot at `ai-task-archive/tasks/OSS-004B.json` and the accepted Phase
7 planning session:

- **Task ID:** `OSS-004B`
- **Owner:** `Codex`
- **Reviewer:** `Claude`
- **Phase:** Phase 7 — EP4 Proof Raising
- **Status at sidecar prep:** `done` (archived; commit `20c902d7cad521ef64d5ee61042c75aec27bfa8a`)
- **Formal depends-on:** `OSS-004A` (done)

**Stated acceptance criteria:**

1. Bootstrap wrapper replaced or formally retired
2. Paper execution package is the truthful EP4 substrate

**Intended proof surface (from Phase 7 planning):**

- VM-2 bootstrap role is retired for the paper runtime role
- A concrete signal-consumer/binding/telemetry package exists in the repo
- VM-2 packaging (compose + env) and deployment docs reflect the new runtime truthfully
- OSS-004C remains the owner of the first integrated governed paper acceptance run

**Upstream prerequisite:** `OSS-004A` (done) supplies the explicit authority path, workspace and
auth-profile refs, and adapter-boundary clarity that this task consumes.

**Downstream dependency:** `OSS-004B` must be closed before `OSS-004C` runs the first integrated
governed paper acceptance against the truthful VM-2 runtime.

---

## 3. What the Parent Must Deliver

For the reviewer to approve `OSS-004B`, the delivery should include at minimum:

| Deliverable | Rationale |
|---|---|
| Retirement of bootstrap paper-runtime role | `runtime_bootstrap.py` must no longer route the paper-runtime role into a bootstrap-only stub |
| Concrete paper execution runtime package | `paper_runtime.py` or equivalent must provide a signal-consumer, binding-resolver, and telemetry-emitter surface |
| VM-2 packaging alignment | `docker-compose.exec.yml` and `env/prod-exec.env.example` must wire the new package with execution-only secret boundary |
| Deployment docs and smoke harness updated | Deployment docs must stop describing the VM-2 service as "bootstrap-only"; smoke harness must reflect the new package surface |
| Explicit OSS-004C deferral | Parent must not claim the integrated governed paper run; that boundary must stay with `OSS-004C` |

The parent is **not** required to:

- run the first integrated governed paper execution acceptance (`OSS-004C`)
- publish the EP4 evidence packet (`OSS-004D`)
- implement production-grade live-trading packaging
- introduce full JWT validation on the paper runtime path

---

## 4. Evidence Map

### 4.1 Bootstrap paper-runtime role retirement — met

| File | What it proves | What it does not prove |
|---|---|---|
| `services/execution/lean_runtime/runtime_bootstrap.py` | Role `pantheon-paper-execution-runtime` (and its backward-compatible alias `pantheon-lean-paper-runtime`) now calls `paper_runtime.main()`, not a stub | Does not prove a live-trading LEAN execution session |
| `docs/deployment/exec-vm-secrets-guide.md` | Prior version stated the service was a bootstrap wrapper; acceptance packet confirms this was updated | — |

**Reviewer verdict guidance:** Accept if the role-dispatch path no longer terminates in a
health-only bootstrap stub. Reject if `stub_mode = True` is still surfaced without a deprecation
path or if the paper-runtime role still falls through to a bootstrap-only import.

### 4.2 Concrete paper execution runtime package — met

| File | What it proves | What it does not prove |
|---|---|---|
| `services/execution/lean_runtime/paper_runtime.py` | Provides `PendingSignalStore` integration, `SignalConsumer` wiring, `RuntimeBindingResolver`, `RuntimeTelemetryEmitter`, in-process paper execution algorithm, and HTTP health/admin surface | Does not constitute a production governed execution run |
| `services/execution/lean_runtime/pending_signal_store.py` | Provides the signal retrieval surface consumed by the runtime | — |
| `services/execution/lean_runtime/signal_consumer.py` | Provides the concrete signal-consumer path used by the runtime | — |
| `services/runtime-manager/runtime_manager_client.py` | Supports binding resolution against runtime-manager state from inside the paper runtime | — |

**Reviewer verdict guidance:** Accept if the runtime package has binding-resolver, telemetry, and
signal-consumer surfaces. Reject if any of these surfaces are empty stubs with no real wiring.

### 4.3 VM-2 packaging alignment — met

| File | What it proves | What it does not prove |
|---|---|---|
| `docker-compose.exec.yml` | Describes VM-2 stack as `runtime-manager` + truthful paper execution runtime + mock broker/exchange sidecars behind execution-only secret boundary | Does not prove a real broker integration or live trade path |
| `env/prod-exec.env.example` | Documents `PANTHEON_PAPER_RUNTIME_ID`, signal queue key, broker/exchange runtime ids, and execution-only secret boundary on VM-2 | — |

**Reviewer verdict guidance:** Accept if compose and env align with the paper execution package
story and use execution-only secret boundary inherited from `OSS-004A`. Reject if VM-1 secrets are
mixed into the VM-2 compose or env surface.

### 4.4 Deployment docs and smoke harness updated — met

| File | What it proves | What it does not prove |
|---|---|---|
| `docs/deployment/dual-vm-acceptance-results.md` | States repo ships dedicated VM-2 paper execution runtime package on top of DEPLOY-008 split; explicitly says `OSS-004C` still owns the first integrated governed paper run | Does not prove the integrated governed paper run has been completed |
| `scripts/smoke_test_dual_vm.sh` | Records `"paper_runtime_bootstrap_stub": false`; smoke note repeats `OSS-004C` ownership | — |

**Reviewer verdict guidance:** Accept if the deployment doc and smoke harness truthfully describe
the new package surface without absorbing `OSS-004C` scope. Reject if either file implies the
integrated governed paper run is complete.

### 4.5 Verification evidence — met (per acceptance packet rerun)

The acceptance sidecar reran three low-cost verification passes on the current repo snapshot:

- `python3 -m unittest services.execution.lean_runtime.test_signal_consumer services.execution.lean_runtime.test_runtime_identity services.execution.lean_runtime.test_paper_runtime` — exited `0`, 12 tests passed
- `python3 -m py_compile` on the four core runtime files — exited `0`
- `timeout 2s` bootstrap launch with paper-runtime role env — exited `124` (expected; runtime startup confirmed before intentional kill)

**Reviewer verdict guidance:** Confirm the unit test count and compile exit code match the
acceptance sidecar note. Reject if there are unexplained test failures or compilation errors on the
core runtime files.

---

## 5. Review Checklist

Use this checklist when evaluating the parent delivery.

| # | Check | Pass condition | Flag if |
|---|---|---|---|
| R-1 | Bootstrap paper-runtime role retired | `runtime_bootstrap.py` routes paper-runtime role into `paper_runtime.main()`, not a health-only stub | Role dispatch still terminates in bootstrap-only behavior or `stub_mode = True` without a formal deprecation note |
| R-2 | Concrete signal-consumer surface exists | `paper_runtime.py` imports and wires `SignalConsumer` from a real consumer module | The runtime is a wrapper that only polls for health; no real signal consumption path |
| R-3 | Binding resolver is present | Runtime resolves its active `RuntimeBinding` from `runtime-manager` via `RuntimeBindingResolver` or equivalent | Binding is hardcoded or missing; runtime cannot identify itself from runtime-manager state |
| R-4 | Telemetry emitter is present | Runtime emits telemetry envelopes that include binding/runtime/plan authority refs | Telemetry is absent or emits without authority chain; emitter uses deprecated field aliases (e.g., `runtime_binding_id`) |
| R-5 | VM-2 compose uses execution-only secret boundary | `docker-compose.exec.yml` keeps execution token on VM-2 and does not mix in VM-1 governance secrets | VM-1 token or approval-bus secrets appear in VM-2 execution service environment |
| R-6 | OSS-004C boundary is explicit | Deployment doc and smoke harness state explicitly that `OSS-004C` owns the first integrated governed paper run | Parent delivery absorbs or conflates the integrated governed paper run into `OSS-004B` scope |
| R-7 | No EP4 overclaim | Parent does not claim `EP4` execution proof is complete | Parent implies the integrated governed paper acceptance is done based on `OSS-004B` substrate work alone |
| R-8 | Evidence anchored to existing files | All cited files and verification results exist in the current repo state | Parent cites files that do not exist or rerun results that do not match the acceptance sidecar note |

---

## 6. Known Risk Areas

### 6.1 Bootstrap/EP4 conflation

The principal risk is that `dual-vm-acceptance-results.md` is miscited as integrated governed paper
execution proof. That document records the VM-2 runtime package substrate, not the first full
EP4 acceptance run. The reviewer should confirm the parent does not use the smoke result as EP4
completion evidence.

### 6.2 Stub mode retained without deprecation

If `runtime_bootstrap.py` still emits `stub_mode = True` through the paper-runtime dispatch path
(even as a default), and the parent does not formally retire or annotate it, the reviewer should
flag this as an incomplete retirement of the bootstrap behavior.

### 6.3 Secret boundary drift from OSS-004A

`OSS-004A` established that execution-only tokens stay on VM-2. If `docker-compose.exec.yml` or
`env/prod-exec.env.example` inadvertently reference VM-1 secrets (approval bus, governance
registry), that is a cross-plane boundary violation in the packaging.

### 6.4 Telemetry naming drift

`OSS-004A` confirmed `binding_id` as the canonical telemetry authority anchor. If `paper_runtime.py`
introduces `runtime_binding_id` or other legacy aliases in the emitted envelope, it conflicts with
the schema canonical truth in `services/telemetry/telemetry_event.schema.json`.

### 6.5 Scope bleed into OSS-004C

If the parent delivery includes any artifact that claims to complete the first integrated governed
paper run — e.g., a production run log, an end-to-end acceptance checkpoint, or a status update
that says EP4 is proven — that is scope bleed into `OSS-004C`.

---

## 7. Scoping Boundary Summary

| In scope for `OSS-004B` | Out of scope — belongs to `OSS-004C` or later |
|---|---|
| Retiring the bootstrap paper-runtime role | Running the first integrated governed paper acceptance |
| Adding a concrete signal-consumer + binding + telemetry package | Claiming EP4 proof is complete |
| Aligning VM-2 compose and env with the new runtime story | Implementing production live-trading packaging |
| Updating deployment docs and smoke harness truthfully | Publishing the EP4 evidence packet (`OSS-004D`) |
| Consuming the auth/authority substrate from `OSS-004A` | Introducing full JWT validation or RBAC |

---

## 8. Reviewer Disposition Guide

For the sidecar task now in `review`, `Codex` should evaluate the delivery against the checklist
above and choose one of:

- **Approve** (`approve` command): all R-1 through R-8 pass; bootstrap role retired, concrete
  runtime package wired, VM-2 packaging aligned, OSS-004C boundary explicit, no EP4 overclaim
- **Reopen** (`reopen` + required changes): one or more checklist items fail; state the specific
  failing item and what concrete change is required before re-review
- **Blocker** (`blocker` command): a required canonical file or verification result referenced by
  the parent is missing from the repo and cannot be verified

After approval, return the sidecar task to `Claude` (sidecar owner) for final `done` close. The
archived parent owner remains `Codex`.

Note: since `OSS-004B` is already archived as `done` in the current repo snapshot, the primary
reviewer action is to confirm the acceptance sidecar and this review packet together give an
accurate retrospective picture of the completed work, and that no scope overclaim exists in the
archived state.

---

## 9. Related Artifacts

| Artifact | Role |
|---|---|
| `support/sidecars/OSS-004B/OSS-004B-SIDECAR-ACCEPTANCE.md` | Acceptance checklist (AC-1 through AC-7), dependency map, and sidecar verification rerun; primary complementary artifact |
| `services/execution/lean_runtime/runtime_bootstrap.py` | Role dispatch surface — proves bootstrap retirement |
| `services/execution/lean_runtime/paper_runtime.py` | Concrete paper execution runtime package |
| `services/execution/lean_runtime/pending_signal_store.py` | Signal retrieval surface consumed by the runtime |
| `services/execution/lean_runtime/signal_consumer.py` | Signal-consumer path |
| `services/runtime-manager/runtime_manager_client.py` | Binding resolution client used by the runtime |
| `docker-compose.exec.yml` | VM-2 execution-plane packaging |
| `env/prod-exec.env.example` | VM-2 execution-only env boundary |
| `scripts/smoke_test_dual_vm.sh` | Smoke harness with updated runtime proof surface |
| `docs/deployment/dual-vm-acceptance-results.md` | Deployment-facing evidence (substrate, not EP4 proof) |
| `services/telemetry/telemetry_event.schema.json` | Telemetry authority schema (canonical) |
| `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | EP3/EP4 evidence bar definitions |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Canonical deployment chain policy |
| `support/sidecars/OSS-004A/OSS-004A-SIDECAR-REVIEW.md` | Upstream auth-substrate review packet (context for boundary checks) |

---

## 10. Handoff

This packet is ready for `Codex` as the sidecar reviewer.

**Reviewer next action:** review this packet against the archived `OSS-004B` record, then use
sections 5 and 6 of this document as the primary retrospective review guide.

**Sidecar owner next action:** after `Codex` approves the helper slice, `Claude` should finalize
`OSS-004B-SIDECAR-REVIEW` to `done`.

**Parent owner next action:** `Codex` may still use section 3 (what to deliver) and section 8
(reviewer disposition guide) to confirm the archived closeout shape matches the review gate before
deciding whether to absorb this support packet into the main review trail.
