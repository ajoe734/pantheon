# Development Work Breakdown

Last updated: 2026-04-19
Status: full platform backlog and workstream breakdown for Pantheon
Tier: L2 Planning & Execution
Scope: detailed task inventory, dependencies, owner/reviewer defaults, and acceptance criteria for future work
Conflict rule: this file is the detailed backlog truth, but it must follow L1 platform architecture and policy documents

## 1. Working Rule

This file is the full backlog truth.
`ai-status.json` tracks only the currently activated sprint tasks and should not be used as the long-range backlog.

This file defines canonical workstream scope, not the live execution status of
every row. Use archived task snapshots, current review packets, and
`ai-status.json` to determine whether a row is already closed, rebaselined, or
materialized as a newer follow-up slice.

If a row's original scope is already archived `done`, do not reactivate the
same task id just because it still appears here as part of the long-range
backlog map. Create a follow-up or closeout slice that cites the closed row.

Every task here cites at least one L1 canonical policy document.
L3 documents may be used as background only.

## 2. Workstream Order

Activate workstreams in this order:

1. `DOC-*`
2. `REG/GOV/DEP-*`
3. `CAP/RUN/EX-*`
4. `TEL/LIN/INC-*`
5. `EVO-*`
6. `PER/APP-*`
7. `OSS-*`

## 3. DOC Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `DOC-001` | Canonical adoption | Promote the new L1/L3 files into the canonical tier model and update cross-references. | - | `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md` | Codex | Claude | canonical tiers published; L1/L3 file headers normalized; guide and map agree |
| `DOC-002` | Canonical adoption | Publish the canonical document map and conflict-resolution rules. | `DOC-001` | `TARGET_ARCHITECTURE.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Codex | Claude | question routing exists; tier precedence documented; onboarding files point to the map |
| `DOC-003` | Architecture rewrite | Rewrite the architecture overview around `artifact_state` and `deployment_stage` separation. | `DOC-001` | `TARGET_ARCHITECTURE.md`, `PAPER_CANARY_LIVE_POLICY.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | Codex | Claude | overview no longer treats `paper/live` as artifact state; ownership split is explicit |
| `DOC-004` | Roadmap rewrite | Replace the old epic roadmap with phased platform/workstream sequencing. | `DOC-003` | `TARGET_ARCHITECTURE.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Codex | Gemini | roadmap uses phases and workstreams; critical path points at detailed backlog |
| `DOC-005` | Backlog publication | Publish the full platform backlog with dependencies and acceptance criteria. | `DOC-004` | `TARGET_ARCHITECTURE.md`, `PERSONA_RUNTIME_MODEL.md`, `OPENCLAW_RUNTIME_CONTRACT.md` | Codex | Gemini | every task has owner/reviewer defaults, dependencies, canonical refs, and acceptance |
| `DOC-006` | State/onboarding sync | Align `ai-status`, generator scripts, onboarding briefs, and prompt prefixes with the new canonical order. | `DOC-002`, `DOC-004` | `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md` | Codex | Gemini | state files, generated snapshot, onboarding docs, and setup scripts show the same read order |
| `DOC-007` | Workbench backlog publication | Publish the canonical remaining module-level workbench and product backlog. | `DOC-005` | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Codex | Claude | `WORKBENCH_DELIVERY_BACKLOG.md` is published; remaining operator/workbench modules are explicitly tracked with truthful next gates |
| `DOC-008` | Delivery loop semantics | Publish canonical status and closure semantics for `.coordination` loops. | `DOC-006` | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` | Codex | Claude | `DELIVERY_CLOSURE_AND_LOOP_STATES.md` is published; packet closure no longer depends on ambiguous local convention |
| `DOC-009` | Execution proof ladder | Publish the canonical maturity ladder for execution evidence. | `DOC-004`, `DOC-005` | `OPENCLAW_RUNTIME_CONTRACT.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md` | Codex | Gemini | `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` is published; roadmap and reviews can cite stable proof levels instead of ad hoc wording |

## 4. REG/GOV/DEP Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `REG-004` | Registry semantics | Split `artifact_state` from `deployment_stage` across registry and promotion contracts. | `DOC-003` | `TARGET_ARCHITECTURE.md`, `PAPER_CANARY_LIVE_POLICY.md` | Codex | Claude | registry contracts use `draft/candidate/approved/retired`; stage handled separately |
| `GOV-001` | Approval governance | Define canonical `ApprovalDecision` contract, write owner, and audit requirements. | `REG-004` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Claude | Codex | approval object exists; owner matrix is explicit; promotion and evolution can cite it |
| `DEP-001` | Deployment planning | Define `DeploymentPlan` contract and stage transition planner behavior. | `GOV-001` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md` | Gemini | Claude | deployment plan supports paper/canary/live/frozen transitions and rollback linkage |
| `DEP-002` | Deployment Orchestration Saga | Implement orchestration-first saga with transactional outbox/inbox for cross-service deployment consistency. | `DEP-001` | `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | Gemini | Claude | atomic write of business data plus event outbox is verified; per-aggregate ordering and idempotent consumer behavior are verified; compensation and owner-scoped write boundaries are documented and tested |

## 5. CAP/RUN/EX Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `CAP-001` | Capital control | Define `capital_pool` and `PersonaCapitalBinding` as governed platform objects. | `DEP-001` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PERSONA_RUNTIME_MODEL.md` | Claude | Codex | pool and binding ownership are explicit; single-pool runtime rule is documented |
| `RUN-001` | Runtime control | Define `RuntimeBinding` and runtime-manager write authority. | `CAP-001`, `DEP-001` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md` | Claude | Gemini | runtime binding references deployment plan, binding, and stage; write owner is execution plane |
| `EX-002` | Rollback execution | Align runtime-manager execution actions with `replace`, `pause_then_replace`, and `liquidate_then_replace`. | `RUN-001` | `ROLLBACK_AND_POSITION_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md` | Gemini | Claude | action mapping is explicit; position handling and cutover semantics are preserved |
| `CAP-002` | Multi-Persona Synthesis | Implement the `optimizer-svc` internal synthesis module for resolving multi-advisor conflicts for one capital pool. | `CAP-001`, `GOV-001` | `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Claude | Copilot | weighted fusion, sponsor selection, and committee override logic are implemented inside `optimizer-svc`; one canonical synthesis artifact is produced per scope; `conflict_resolution_log` is generated |

## 6. TEL/LIN/INC Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `TEL-001` | Telemetry alignment | Add deployment-stage and runtime-binding references to telemetry truth. | `RUN-001` | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `PAPER_CANARY_LIVE_POLICY.md` | Gemini | Codex | telemetry schema carries stage and binding refs; canonical store strategy is documented |
| `LIN-001` | Lineage model | Normalize cross-plane lineage edges and define the read-model aggregation contract. | `TEL-001` | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `TARGET_ARCHITECTURE.md` | Codex | Claude | normalized edges are enumerated; read model is marked derived-only |
| `INC-001` | Incident backbone | Define incident and postmortem records that attach to runtime bindings and telemetry evidence. | `TEL-001`, `LIN-001` | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Claude | Copilot | incident/postmortem objects can point to stage, binding, and lineage references |
| `TEL-002` | Ingest Shock Absorption | Implement durable buffer and async batch writers for telemetry ingest shock absorption. | `TEL-001` | `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | Gemini | Codex | high-volume ingest does not block canonical Postgres writes; backpressure, replay, and idempotent batch-write behavior are verified; durable buffer choice is documented with operational tradeoffs |
| `LIN-002` | Lineage Read Service | Implement high-performance lineage assembly using materialized paths or optimized joins. | `LIN-001` | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | Codex | Claude | benchmark corpus and p95 latency target are documented and validated; deep graph traversal is supported without recursive timeout failure |

## 7. EVO Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `EVO-003` | Evolution adoption | Adopt `EvolutionDecision` as a first-class governed object in platform contracts. | `LIN-001` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `TARGET_ARCHITECTURE.md` | Codex | Claude | decision lifecycle, actor roles, evidence links, and cooldown/convergence fields are formalized |
| `EVO-004` | Operational evolution | Wire freeze, rollback, retrain, and redeploy orchestration boundaries. | `EVO-003`, `EX-002`, `INC-001` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md` | Claude | Gemini | each action path has owner, threshold, cooldown, and execution boundary |
| `EVO-005` | Kill Switch Fast-Path | Implement prioritized emergency fast path for Kill Switch and Safe Mode actions through runtime-manager. | `EVO-004` | `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Claude | Gemini | emergency actions bypass normal governance review queues but still flow through runtime-manager fast path; audit trail is preserved; kill-switch latency target is validated under benchmark scenario |

## 8. PER/APP Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `PER-001` | Persona platform | Adopt the registry/session/runtime persona model in platform contracts and service boundaries. | `CAP-001`, `RUN-001` | `PERSONA_RUNTIME_MODEL.md`, `OPENCLAW_RUNTIME_CONTRACT.md` | Claude | Codex | persona identity, session, and runtime instance boundaries are explicit |
| `APP-001` | App surfaces | Define BFF/read-aggregation and consultation surfaces without creating parallel truth sources. | `PER-001`, `LIN-001` | `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | Copilot | Codex | BFF is read-oriented; consultation surfaces cite canonical objects instead of shadow copies; degraded operator path and control-plane resilience assumptions are documented |
| `APP-002` | Operator surfaces | Define operator-facing views for deployment, incident, and evolution control. | `APP-001`, `EVO-004` | `PAPER_CANARY_LIVE_POLICY.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | Copilot | Claude | operator actions map to approval, deployment plan, runtime binding, and evolution decision objects; operator fallback path is defined when the primary BFF path is degraded |
| `APP-003` | Workbench productization | Deliver the remaining operator, governance follow-on, evolution, research, knowledge, consultation, and trainer workbench modules tracked in `WORKBENCH_DELIVERY_BACKLOG.md`. | `APP-001`, `APP-002`, `DOC-007`, `DOC-008` | `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Copilot | Codex | each remaining module exits the backlog through a live route, truthful packet artifacts, and loop closure semantics that match `DELIVERY_CLOSURE_AND_LOOP_STATES.md` |

## 9. OSS Workstream

| ID | Workstream | Goal | Depends on | Canonical refs | Default owner lane | Reviewer lane | Acceptance |
|---|---|---|---|---|---|---|---|
| `OSS-001` | OpenClaw integration | Select and pin the upstream OpenClaw source and document the governed adapter boundary. | `DOC-003` | `OPENCLAW_RUNTIME_CONTRACT.md`, `TARGET_ARCHITECTURE.md` | Gemini | Codex | source selected; version pinned; adapter boundary and smoke-test plan recorded |
| `OSS-002` | Integration regrade | Regrade DSPy, imitation, and MLflow against the new canonical model and update evidence status. | `DOC-005`, `OSS-001` | `TARGET_ARCHITECTURE.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | Copilot | Codex | checklist status reflects real maturity; missing evidence and follow-ups are explicit |
| `OSS-003` | Deferred path criteria | Define hard entry criteria for Qlib, TRL, and RL stack activation under the new governance model. | `EVO-003`, `OSS-002` | `TARGET_ARCHITECTURE.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Gemini | Copilot | deferred frameworks have explicit prerequisites, not placeholder boxes |
| `OSS-004` | Governed execution proof | Raise runtime evidence from local or harness smoke toward governed paper execution proof. | `OSS-001`, `OSS-002`, `OSS-003`, `RUN-001`, `EVO-004` | `OPENCLAW_RUNTIME_CONTRACT.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Gemini | Codex | execution evidence is explicitly classified with `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`; repo reaches stable `EP4` before any `EP5` claim |

## 10. Activation Guidance for `ai-status.json`

Only copy tasks from this file into `ai-status.json` when they are part of the currently active sprint.
Recommended first activation set after the canonical cutover:

- `REG-004`
- `GOV-001`
- `DEP-001`
- `CAP-001`
- `RUN-001`
- `TEL-001`
- `LIN-001`
- `OSS-001`

Recommended second-wave activation set once the first dependencies land:

- `DEP-002`
- `CAP-002`
- `TEL-002`
- `LIN-002`
- `EVO-003`
- `EVO-004`
- `EVO-005`

Historical note: the activation sets above describe sequencing after the
canonical cutover. They are not the same thing as the current open-task truth.

As of the 2026-04-19 deep-task rebaseline:

- closed rebaseline / archived `done`: `DEP-002`, `CAP-002`, `TEL-002`,
  `LIN-002`, `EVO-004`, `EVO-005`, `OSS-004`
- active remaining productization gap: `APP-003`, now split between canonical
  closeout bookkeeping (`APP-003-CLOSEOUT-001`) and the genuinely unfinished
  module backlog that remains in `WORKBENCH_DELIVERY_BACKLOG.md`

If a new defect or extension is found inside one of the closed rows above,
materialize a follow-up task instead of treating the historical row as still
unimplemented.
