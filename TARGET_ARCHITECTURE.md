# Pantheon Target Architecture

Last updated: 2026-04-09
Status: canonical platform architecture for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: cross-plane architecture, lifecycle model, object ownership, and end-to-end governed flow
Conflict rule: this overview defers to narrower L1 policy files for runtime, persona, deployment, rollback, lineage, telemetry, and evolution details

## 1. Identity and Top-Level Rule

Pantheon is the system we are building.
`OpenClaw` is an upstream OSS runtime substrate that Pantheon integrates through governed adapters.

The platform must never allow a persona, worker, or runtime to mutate live behavior directly from short-term market feedback.
Every change must pass through governed research, approval, deployment planning, runtime binding, telemetry, and evolution review.

## 2. Canonical Lifecycle Model

Pantheon separates governance maturity from runtime deployment.

### Artifact State

Canonical `artifact_state` values are:

- `draft`
- `candidate`
- `approved`
- `retired`

### Deployment Stage

Canonical `deployment_stage` values are:

- `none`
- `paper`
- `canary`
- `live`
- `frozen`

Rules:

- `artifact_state` describes whether an artifact is governable and promotable.
- `deployment_stage` describes where an approved artifact is actually bound and running.
- `canary` is a first-class deployment stage, not a replacement artifact state.
- `lineage read model` is derived only; normalized edges remain the source of truth.

## 3. Responsibility Split

### OpenClaw-Compatible Runtime

The upstream runtime owns:

- persona/session execution substrate
- tool invocation runtime
- workflow/cron execution substrate
- agent checkpointing or session persistence when delegated to upstream runtime

Pantheon owns:

- permissions and deny-first policy
- workflow handoff shape
- governance checks before deployment-capable actions
- mapping runtime outputs into Pantheon objects and lineage

### Research and Learning Plane

This plane discovers and refines governed candidates.

It owns:

- source ingestion
- normalization into `StrategySpec` or governed proposals
- replication gates
- evaluator and optimizer inputs/outputs
- OSS learning-framework adapters such as DSPy, imitation, Qlib, TRL, and RL paths

### Registry and Governance Plane

This plane decides what may progress.

It owns:

- governed artifact registration
- artifact lineage edges
- `ApprovalDecision`
- `DeploymentPlan`
- paper/canary/live gate semantics
- rollback target metadata and approval records

### Capital, Runtime, and Execution Plane

This plane decides where approved artifacts run.

It owns:

- `capital_pool`
- `PersonaCapitalBinding`
- `RuntimeBinding`
- runtime-manager semantics
- artifact loading into LEAN
- broker and position-affecting execution behavior

### Telemetry, Incident, and Evolution Plane

This plane turns operating evidence into controlled follow-up actions.

It owns:

- execution telemetry and trader feedback capture
- normalized lineage edges from runtime to telemetry
- incident/postmortem records
- `EvolutionDecision`
- threshold-based freeze, rollback, retrain, and review triggers

## 4. Core Canonical Objects

These objects form the minimum cross-plane backbone:

**Decision-front objects (front-half provenance chain):**
- `RegimeState`
- `UniverseSelection`
- `SignalInference`
- `AllocationDecision`
- `RiskAdjudication`

**Governance and runtime objects (back-half):**
- `StrategySpec`
- `ArtifactRecord`
- `ApprovalDecision`
- `DeploymentPlan`
- `PersonaCapitalBinding`
- `RuntimeBinding`
- `TelemetryEvent`
- `EvolutionDecision`

Ownership rules:

- decision-front objects are owned by the decision plane (`services/registry-core/decision-domain/`) and feed the back-half governance gate
- registry/governance own `ArtifactRecord` and `ApprovalDecision`
- governance/promotion own `DeploymentPlan`
- capital/runtime own `PersonaCapitalBinding` and `RuntimeBinding`
- telemetry services own normalized event writes
- lineage read model aggregates but does not own truth

## 5. End-to-End Governed Flow

1. Approved ingest workflows collect papers, repos, notes, and operator input.
2. Research services normalize material into `StrategySpec`, dataset references, or experiment proposals.
3. Replication and evaluation determine whether outputs become `candidate` artifacts.
4. Governance review may advance candidates into `approved`.
5. Deployment planning chooses `paper`, `canary`, `live`, or `frozen` stage transitions.
6. Runtime manager writes `RuntimeBinding` and LEAN loads only the approved artifact projection.
7. Telemetry and feedback write normalized operational evidence with deployment-stage and binding references.
8. Evolution review decides whether to freeze, rollback, retrain, mutate, or retire.

## 6. Preferred Framework Roles

- `OpenClaw`: upstream runtime substrate for persona/workflow execution
- `DSPy`: persona policy optimization
- `imitation`: trader behavior cloning
- `MLflow`: experiment tracking backbone
- `Qlib`: first deferred learning path to activate for supervised alpha research, gated by `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `TRL`: governed preference-learning workflows only after FB-002 volume, imitation baseline, and a downstream consumer exist, gated by `services/learning/trl/ACTIVATION_CRITERIA.md`
- `FinRL` / `RLlib` / `Ray Tune`: optional RL path only after Qlib has plateaued and the problem is genuinely sequential, gated by `services/learning/rl/PATH_DEFINITION.md`

Named OSS projects always mean real integrations unless a local replacement is explicitly stated.

## 7. Current Repo Interpretation

This repo already contains substantial contract and adapter work, but the architecture cutover in this document is primarily semantic and planning-level.

Already established:

- collaboration operating system and dashboard
- research ingest / normalize / replicate chain
- registry, promotion, and artifact-loader contracts
- feedback, telemetry, evaluator, optimizer, and several OSS adapters

Still to be completed as platform truth:

- artifact-state / deployment-stage split throughout contracts
- explicit `ApprovalDecision`, `DeploymentPlan`, `PersonaCapitalBinding`, and `RuntimeBinding` ownership
- telemetry and lineage edge alignment
- runtime-manager and capital-pool control surface
- incident, postmortem, BFF, and consultation surfaces

## 8. Detailed Policy References

Use these L1 files when working below architecture-overview level:

- `OPENCLAW_RUNTIME_CONTRACT.md`
- `PERSONA_RUNTIME_MODEL.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
- `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`
- `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`
- `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`
