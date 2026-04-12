# Pantheon Roadmap

Last updated: 2026-04-09
Status: canonical phased program roadmap aligned to `TARGET_ARCHITECTURE.md`
Tier: L2 Planning & Execution
Scope: phase sequencing, critical path, workstream activation order, and delivery outcomes
Conflict rule: this file sequences work but does not override L1 architecture or policy semantics; use `DEVELOPMENT_WORKBREAKDOWN.md` for detailed task definitions

## 1. Delivery Principle

Build Pantheon in layers that preserve governance at every handoff:

1. canonicalize architecture and policy truth
2. split artifact governance from deployment stage
3. make deployment planning and runtime binding explicit
4. align telemetry, lineage, and evolution around the same truth model
5. add persona/application surfaces and OSS hardening on top of stable governance

## 2. Activation Order

Workstreams should activate in this order:

1. `DOC-*`
2. `REG/GOV/DEP-*`
3. `CAP/RUN/EX-*`
4. `TEL/LIN/INC-*`
5. `EVO-*`
6. `PER/APP-*`
7. `OSS-*`

This order is mandatory unless a later workstream task explicitly depends only on already-finished earlier tasks.

## 3. Phase Plan

### Phase 0: Canonical Cutover

Outcome:

- canonical tiers are published
- architecture and roadmap reflect `artifact_state` / `deployment_stage` split
- onboarding, status generation, and dashboard-facing snapshots all read the same truth

Primary tasks:

- `DOC-001` through `DOC-006`

### Phase 1: Registry and Governance Split

Outcome:

- registry and promotion semantics no longer mix artifact approval with runtime stage
- `ApprovalDecision` and `DeploymentPlan` become explicit write-owned objects
- deployment transitions remain consistent across service boundaries through outbox/inbox and orchestration-first saga rules

Primary tasks:

- `REG-004`
- `GOV-001`
- `DEP-001`
- `DEP-002` (Consistency Backbone)

### Phase 2: Capital, Runtime, and Execution Control

Outcome:

- capital pool ownership is explicit
- persona-to-capital binding is governed
- shared-pool multi-persona conflicts have a governed synthesis path inside `optimizer-svc`
- runtime-manager writes canonical `RuntimeBinding`
- rollback execution semantics line up with deployment and position policy

Primary tasks:

- `CAP-001`
- `RUN-001`
- `EX-002`
- `CAP-002` (Internal Conflict Resolution Module)

### Phase 3: Telemetry, Lineage, and Incident Backbone

Outcome:

- telemetry events carry deployment-stage and runtime-binding truth
- high-volume telemetry ingest is buffered and backpressure-aware
- lineage is normalized on write and aggregated on read
- lineage read paths have a defined performance target and assembly strategy
- incident and postmortem records can point to the same cross-plane identifiers

Primary tasks:

- `TEL-001`
- `LIN-001`
- `INC-001`
- `TEL-002` (Ingest Resilience)
- `LIN-002` (Assembly Performance)

### Phase 4: Evolution Governance

Outcome:

- `EvolutionDecision` becomes first-class
- thresholds, review owners, and auto-execution boundaries are wired into platform truth
- emergency fast-path actions and cooldown/convergence rules are explicit

Primary tasks:

- `EVO-003`
- `EVO-004`
- `EVO-005` (Emergency Fast-Path)

### Phase 5: Persona and Application Surfaces

Outcome:

- persona registry/session/runtime model is reflected in platform contracts
- BFF/read aggregation and consultation surfaces have a governed path
- operator-facing surfaces stop inventing parallel state

Primary tasks:

- `PER-001`
- `APP-001`
- `APP-002`

### Phase 6: OSS Integration Hardening

Outcome:

- upstream OpenClaw integration is pinned and evidenced
- key learning integrations are regraded against the new canonical model
- deferred OSS paths have clear entry criteria rather than ambiguous placeholders

Primary tasks:

- `OSS-001`
- `OSS-002`
- `OSS-003`

## 4. Critical Path

The critical path for the next platform increment is:

1. `DOC-001` -> `DOC-006`
2. `REG-004`
3. `GOV-001`
4. `DEP-001`
5. `DEP-002`
6. `RUN-001`
7. `TEL-001`
8. `LIN-001`
9. `EVO-003`

Production-readiness resilience tracks that should advance in parallel once dependencies are ready:

- `CAP-002`
- `TEL-002`
- `LIN-002`
- `EVO-005`

`CAP-001`, `EX-002`, `PER-001`, and `OSS-001` should start as soon as their direct dependencies are clear, but they must not redefine the upstream semantics already locked by the critical path.

## 5. Ownership Guidance by Lane

- `Codex`: canonical docs, registry semantics, lineage model, status/onboarding alignment
- `Claude`: governance, runtime, execution, rollback semantics, persona/runtime boundary review
- `Gemini`: deployment workflow, runtime packaging, telemetry plumbing, OSS packaging/pinning
- `Copilot`: consultation/app surface planning, research/OSS evidence gathering, critique and review support

## 6. Relationship to Detailed Work

Use `DEVELOPMENT_WORKBREAKDOWN.md` for:

- per-task dependencies
- canonical references
- owner/reviewer defaults
- acceptance criteria

Use `OSS_INTEGRATION_CHECKLIST.md` as the evidence bar for any task that names an upstream framework.
