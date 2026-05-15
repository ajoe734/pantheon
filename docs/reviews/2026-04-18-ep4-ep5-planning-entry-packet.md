# 2026-04-18 EP4/EP5 Planning Entry Packet

Record layer document.
Do not treat this file as canonical blueprint truth.
This packet is a planning-entry aid for a future `discussion_planning` session.

## Purpose

Prepare the repo to enter planning mode for execution-proof work beyond the current `EP3` ceiling.

This packet inventories:

- what the repo already proves for `EP4` and `EP5`
- what is still missing
- whether canonical document reconciliation is needed before planning
- what task slices should be proposed once planning mode starts

## Suggested Planning Session Metadata

- Suggested session id: `phase7-2026-04-18-ep4-ep5-execution-proof`
- Planning mode: `discussion_planning`
- Runtime mode: `supervisor_managed_execution`
- Objective: turn the current `EP3`-bounded deployment evidence into a dependency-aware plan for stable `EP4`, while explicitly separating later `EP5` canary/live proof from paper-runtime proof
- Facilitator: `Claude`
- Starter draft owner: `Codex`
- Recommended reviewer order: `Gemini -> Claude -> Codex`

## Recommended Brief Files

### Canonical proof and policy inputs

- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `ROADMAP.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`

### Current evidence inputs

- `docs/deployment/single-vm-smoke-results.md`
- `docs/deployment/dual-vm-acceptance-results.md`
- `integrations/openclaw/evidence_pack.md`
- `docs/reviews/2026-04-17-oss-next-008-governed-regression-refresh.md`
- `docs/reviews/2026-04-18-current-state-reconciliation.md`

## Document Reconciliation Preflight

This section is written to match the intent of planning mode's `document_reconciliation` stage.

### Canonical Inputs Reviewed

- Canonical planning docs:
  - `ROADMAP.md`
  - `DEVELOPMENT_WORKBREAKDOWN.md`
  - `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- Canonical architecture or policy docs:
  - `OPENCLAW_RUNTIME_CONTRACT.md`
  - `PAPER_CANARY_LIVE_POLICY.md`
  - `ROLLBACK_AND_POSITION_SEMANTICS.md`
  - `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
  - `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`

### Insufficiencies Found

1. No blocking canonical semantic gap was found for entering EP4/EP5 planning.
   The repo already defines:
   - what `EP4` and `EP5` mean
   - what `paper`, `canary`, and `live` mean
   - what the runtime boundary is
   - what rollback is required to preserve

2. The main deficiency is not missing semantic policy.
   The main deficiency is missing runtime evidence and execution packaging needed to satisfy the already-published semantics.

3. There is one planning caveat:
   the repo does not yet publish one canonical EP4/EP5 execution checklist beyond the maturity ladder itself.
   That is a planning and execution gap, but it is not severe enough to block planning entry.

### Canonical Updates Required

- None required before entering execution planning.
- Recommended `document_reconciliation_status` for the future planning session: `not_needed`

## Current Evidence Baseline

### What the repo already proves

1. `EP1`
   Route, schema, and contract proof exists across the BFF and service surfaces.

2. `EP2`
   Local composed proof exists for several service slices and smoke paths.

3. `EP3`
   The repo has:
   - single-VM system smoke evidence
   - dual-VM cross-plane acceptance harness evidence

### What the repo explicitly does not yet prove

1. Stable `EP4`
   `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` says the repo does not yet have stable governed paper execution proof.

2. Any `EP5`
   The same canonical ladder says the repo does not yet have canary/live proof.

## EP4 Inventory

`EP4` requires the governed paper loop to run with real authority, runtime state, telemetry, governance, and rollback semantics together.

### EP4-01: Cross-plane plan-to-binding path

- Current evidence:
  - dual-VM acceptance proves `DeploymentPlan -> RuntimeBinding` crosses the VM boundary
  - runtime-manager is already the authoritative writer for `RuntimeBinding`
- Current gap:
  - the VM-2 paper runtime is still a bootstrap stub
  - this is not yet the final execution package

### EP4-02: Truthful paper-runtime execution path

- Current evidence:
  - policy says `paper` must use real market data, real artifact/config/runtime path, simulated fills, and canary/live-compatible telemetry schema
  - dual-VM acceptance proves only paper-runtime process health and adjacency
- Current gap:
  - no final runtime package replaces the bootstrap wrapper yet
  - no real execution-side signal consumer proof exists yet

### EP4-03: Runtime auth and authority path

- Current evidence:
  - runtime-manager write routes require Bearer auth
  - dual-VM smoke already uses a runtime-manager token path
  - OpenClaw gateway smoke exists as governed OSS evidence
- Current gap:
  - runtime-manager auth remains stub-level, not production-grade token validation
  - there is not yet one integrated EP4 run proving the final authority path from approved plan to paper execution runtime

### EP4-04: Governance + telemetry + rollback together

- Current evidence:
  - single-VM smoke proves governance approval, runtime binding creation, telemetry ingest, incident creation, and BFF reads
  - dual-VM harness proves kill-switch and rollback commands can cross VM boundary
  - rollback semantics and runtime-manager authority boundary are formally defined
- Current gap:
  - these proofs are still split across smoke/harness layers
  - there is no one accepted governed paper-runtime run that combines approval, runtime activation, telemetry, incident/health observation, and rollback evidence into one EP4 packet

### EP4-05: Execution evidence packaging

- Current evidence:
  - deployment docs clearly define what current smoke artifacts prove
- Current gap:
  - there is no record-layer EP4 evidence packet yet with operator-facing acceptance wording, runbook, and archived evidence references

## EP5 Inventory

`EP5` requires real canary or live execution under the same governance and rollback model.

### EP5-01: Canary/live runtime path

- Current evidence:
  - stage semantics for `paper`, `canary`, `live`, and `frozen` are canonical
  - runtime-manager and registry models already represent `canary` and `live`
- Current gap:
  - no accepted canary or live runtime proof exists
  - no final signal-consumer/runtime package has been shown to operate under real-order conditions

### EP5-02: Real broker and venue behavior

- Current evidence:
  - policy requires canary/live to prove real orders, real capital, slippage, rejects, fills, and rollback readiness
- Current gap:
  - no repo evidence currently proves real broker acknowledgement, partial fills, real slippage, or live/canary order-loop behavior

### EP5-03: Operator approval and capital gating

- Current evidence:
  - policy already defines paper-to-canary and canary-to-live thresholds plus reviewer/risk/operator approvals
- Current gap:
  - no execution evidence currently shows those approvals being exercised through a real canary/live drill
  - no run packet currently proves scaled canary capital and ramp semantics in practice

### EP5-04: Rollback drill under real execution

- Current evidence:
  - rollback semantics and runtime-manager fast-path boundaries are defined
  - dual-VM harness proves rollback command flow at harness level
- Current gap:
  - there is no canary/live rollback drill under real execution conditions
  - there is no accepted operator signoff packet demonstrating rollback readiness under canary/live conditions

## Recommended Scope Boundary

The planning session should not treat `EP4` and `EP5` as one undifferentiated wave.

Recommended boundary:

1. First raise the repo to stable `EP4`
2. Only then open `EP5` planning for canary/live proof
3. Allow the same planning session to prepare `EP5` prerequisites, but do not let `EP5` proof claims hide inside `EP4` acceptance

## Human-Gated Questions For Planning Mode

These should be explicit unresolved items in the planning session.

1. Should the next proof program stop at stable `EP4`, or should it also materialize the infrastructure prerequisites for `EP5` in the same wave?

2. What is the final execution substrate for the first truthful paper-runtime proof?
   Options to resolve in planning:
   - LEAN final runtime package
   - OpenClaw-mediated execution wrapper
   - hybrid path with Pantheon-owned adapter + LEAN runner

3. Are nonprod secrets and broker/exchange credentials available for any real canary drill, or is `EP5` necessarily deferred behind infrastructure readiness?

4. Does the team want `EP5` to mean:
   - canary proof only
   - or canary plus live proof in one later wave

## Proposed Wave Order

### Wave P0: Planning and reconciliation

- confirm `document_reconciliation_status = not_needed`
- lock the EP4/EP5 scope split
- decide whether `EP5` is merely prereq planning or real execution work in the next wave

### Wave P1: EP4 prerequisite closure

- replace bootstrap paper runtime with the final truthful paper execution package
- stabilize runtime auth and authority path
- confirm integrated signal-consumer/runtime path

### Wave P2: EP4 proof run

- execute one governed paper-runtime acceptance run
- archive evidence for deployment, telemetry, operator surfaces, incident handling, kill-switch, and rollback
- publish an EP4 evidence packet

### Wave P3: EP5 prerequisite closure

- only if human gate approves
- prepare canary/live broker path, capital gating, and operator approval artifacts

### Wave P4: EP5 proof run

- real canary or live proof
- rollback drill
- operator signoff

## Proposed Execution Slices

These are candidate tasks for planning discussion, not active execution tasks yet.

### P0

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| `OSS-004A` | Gemini | Codex | - | P1 | Stabilize the runtime auth/authority path for EP4: runtime-manager token flow, paper-runtime identity, telemetry authority references, and OpenClaw/Pantheon adapter boundary needed for a truthful governed paper run. |
| `OSS-004B` | Claude | Gemini | `OSS-004A` | P1 | Replace the VM-2 bootstrap paper runtime with the final paper execution package or final signal-consumer path so `DEPLOY-009` no longer stops at bootstrap health. |

### P1

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| `OSS-004C` | Gemini | Codex | `OSS-004A`, `OSS-004B` | P2 | Run and archive one governed paper execution acceptance proving approval -> deployment -> runtime binding -> paper execution -> telemetry -> incident/health -> kill-switch/rollback as one EP4 packet. |
| `OSS-004D` | Codex | Claude | `OSS-004C` | P2 | Publish the EP4 evidence packet and reconcile status/tracking layers so the repo can truthfully claim stable `EP4` and nothing higher. |

### P2

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| `EP5-001` | Gemini | Claude | `OSS-004C` | P3 | Prepare canary-ready execution path: real broker/venue config, scaled capital gate, operator approval checklist, and rollback drill harness. |
| `EP5-002` | Claude | Codex | `EP5-001` | P4 | Execute and archive the first canary/live proof packet, including rollback drill and operator signoff, if human gate and infrastructure prerequisites are satisfied. |

## Planning-Mode Success Criteria

The planning session should be considered successful if it leaves with:

1. a documented `document_reconciliation` outcome
2. a clear `EP4` vs `EP5` scope split
3. explicit human-gated unresolved items for broker/capital/live readiness
4. agreed execution slices with owners, reviewers, and dependency order
5. a decision on whether the next wave claims only `EP4` or also prepares `EP5`

## Ready-To-Use Planning Commands

If you want to activate this as the next planning session, the minimal command sequence should be:

```bash
python3 scripts/planning_state.py start phase7-2026-04-18-ep4-ep5-execution-proof \
  "Turn EP3 deployment evidence into a plan for stable EP4 and explicitly scoped EP5 prerequisites."

python3 scripts/planning_state.py reconcile-docs not_needed \
  "Canonical EP4/EP5 semantics are already published; planning can proceed without a blueprint patch."
```
