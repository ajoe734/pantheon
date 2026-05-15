# Codex2 Readout

## Lane

- Agent: Codex2
- Capability focus: schema audit, object boundaries, runtime contract formalization, and P0/P1 slicing risks.

## Canonical Sources Read

- L0:
  - `planning-session.json`
  - `starter-draft.md`
  - `consensus-packet.md`
  - `docs/04/SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md`
- L1:
  - `docs/04/pantheon_p0_sd/README_P0_SD_INDEX.md`
  - `docs/04/pantheon_p0_sd/SD-P0-02_DeploymentPlan_to_RuntimeBootstrap_Contract.md`
  - `docs/04/pantheon_p0_sd/SD-P0-03_RuntimeBinding_Context_Propagation.md`
  - `docs/04/pantheon_p0_sd/SD-P0-04_Paper_Runtime_TelemetryEvent_Contract.md`
  - `docs/04/pantheon_p0_sd/SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md`
  - `docs/04/pantheon_p0_sd/SD-P0-06_Submodule_Compose_Health_CI_Verification.md`
- L2:
  - `docs/04/pantheon_sa/SA-11_operating_loop_gap_analysis.md`
  - `docs/04/pantheon_sa/SA-13_contract_schema_gap_analysis.md`
  - `docs/04/pantheon_sa/SA-17_telemetry_reconciliation_evolution_gap_analysis.md`
  - `docs/04/pantheon_sa/SA-19_v2_gap_matrix_master_corrected.md`
  - `docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md`

## Working Interpretation

- Architecture summary: P0 should treat `RuntimeBinding` as the runtime identity pivot and keep the execution boundary paper-only: `DeploymentPlan -> RuntimeBinding -> RuntimeBootstrapRequest -> PantheonRuntimeContext -> PantheonAlgoBase -> paper TelemetryEvent -> ingest/projection`, with live/canary fail-closed and `pantheon/lean` as the current bridge target. [SD-P0-02] [SD-P0-03] [SD-P0-04] [SA-20]
- Delivery order: I agree with the starter/supervisor order, with one schema-driven constraint: `RuntimeBootstrapRequest` and `PantheonRuntimeContext` should be accepted before any telemetry producer task is allowed to count as complete, because telemetry validation depends on binding, plan, artifact, capital, stage, and bridge identity. [SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK] [SD-P0-03] [SD-P0-04]
- Ownership boundaries: Pantheon owns canonical objects, command/event contracts, telemetry ingest/projection, and CI guardrails; `pantheon/lean` owns the bridge attachment point through `PantheonAlgoBase`; BFF/front can expose source mode and command surfaces but must not become canonical runtime truth. [README_P0_SD_INDEX] [SD-P0-03] [SD-P0-05] [SD-P0-06]

## Risks / Contradictions

- Risk 1: P0/P1 slicing is not fully reconciled for BFF command/read split and reconciliation. `SA-19` lists BFF command/read cleanup and paper reconciliation baseline in P1, while the supervisor plan, starter draft, and consensus packet include `P0-BFF-CMD-001` and `P0-REC-001` in P0. My recommendation is to keep both in P0 but narrow them: command/read split only for runtime-affecting commands, and reconciliation only as one paper `ReconciliationRecord` plus optional `IncidentCase` threshold behavior with no evolution dispatch. [SA-19] [SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK] [starter-draft.md] [consensus-packet.md]
- Risk 2: Contract examples are close but field naming should be normalized before execution tasks are materialized. Current docs use `runtime_binding_id` in `RuntimeBinding` and `RuntimeBootstrapRequest`, but telemetry uses `binding_id`; `deployment_plan_id` and `plan_id` both appear; `deployment_stage`, `target_stage`, `runtime_role`, and `execution_mode` are all present. This is acceptable if explicitly mapped, but unsafe if implementers infer equivalence ad hoc. [SD-P0-02] [SD-P0-03] [SD-P0-04] [SA-13]
- Risk 3: `lean-platform` status needs a crisp object-boundary rule in the ADR/CI docs. Current sources agree it is not the current P0 execution target, but unresolved language still asks whether to archive it or keep it migration-only. Without a formal `migration_only + adr_override` rule, task packets and CI scans can disagree. [SA-19] [SA-20] [SD-P0-06] [starter-draft.md]
- Risk 4: Runtime context source modes are powerful but need environment-specific acceptance in the task definitions. `local_dev_seed` and env vars are acceptable for dev/paper smoke, while staging/prod require launch manifest behavior; task acceptance should name the environment posture so a dev fallback cannot satisfy a staging/prod contract. [SD-P0-03] [SD-P0-04]

## Suggested Task Slices

- Slice 1: Add a contract reconciliation patch before materialization. Output should be a compact field map for `DeploymentPlan`, `RuntimeBinding`, `RuntimeBootstrapRequest`, `PantheonRuntimeContext`, `TelemetryEvent`, `RuntimeProjection`, and `ReconciliationRecord`, including canonical names and allowed aliases. This should resolve `binding_id` vs `runtime_binding_id`, `plan_id` vs `deployment_plan_id`, and stage/mode terminology before implementation begins. [SA-13] [SD-P0-02] [SD-P0-03] [SD-P0-04]
- Slice 2: Keep `P0-CTX-001` ahead of `P0-CTX-002`, `P0-LEAN-CTX-001`, and `P0-TEL-001`, and require validators for missing binding, stage mismatch, artifact mismatch, capital mismatch, raw secret detection, and bridge identity. [SD-P0-03] [SD-P0-04]
- Slice 3: Define `P0-TEL-001` and `P0-TEL-PROJ-001` as two separate contracts: producer event shape/dedupe/stage rejection first, projection update second. Projection acceptance should require a non-mock runtime summary with bridge repo/commit and last heartbeat. [SD-P0-04] [SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK]
- Slice 4: Keep `P0-REC-001` minimal and downstream of the paper loop smoke: one paper run creates one `ReconciliationRecord`; threshold breach may open `IncidentCase`; evolution remains proposed-only and no dispatcher work is included. [SA-17] [SA-20] [SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK]
- Slice 5: Keep `P0-BFF-CMD-001` in P0 only for runtime/deployment/approval/incident commands, because those commands require actor, trace, idempotency, policy/RBAC, and audit. Non-runtime console cleanup can remain frontend demo/source-mode work or P1. [SA-13] [SD-P0-05] [README_P0_SD_INDEX]
- Slice 6: Treat CI guardrails as a prerequisite lane, not a polish lane: submodule authority, no `lean-platform` P0 target without override, paper bootstrap, live fail-closed, source/search bounded, OpenClaw/research fail-closed, frontend demo production guard, and health endpoint cleanup scan all protect the contract boundary. [SD-P0-06] [SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK]

## Citations

- [planning-session.json] Codex2 lane focus is schemas, object boundaries, and contract formalization gaps; session objective is paper-only operating loop execution work.
- [starter-draft.md] Starter scope is paper-only operating loop and repo authority; proposed wave order includes repo guardrails, runtime contract/context, telemetry, projection/reconciliation, and BFF/front honesty cleanup.
- [consensus-packet.md] Draft packet accepts `pantheon/lean`, paper-only runtime, live fail-closed, `RuntimeBinding` identity, and BFF/front non-authority; it includes `P0-BFF-CMD-001` and `P0-REC-001`.
- [SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md] Hard invariants require `pantheon/lean`, deployment stages separate from artifact states, every deployment-managed runtime having `RuntimeBinding`, paper telemetry carrying runtime identity, no broker secrets, BFF/front non-authority, OpenClaw non-operation, and live disabled pending later human approval.
- [README_P0_SD_INDEX.md] P0 package purpose is to lock execution repo authority, turn `DeploymentPlan -> RuntimeBinding -> runtime_bootstrap.py -> pantheon/lean -> TelemetryEvent` into contract, keep paper baseline/live fail-closed, and require actor/trace/idempotency for runtime-affecting commands.
- [SD-P0-02_DeploymentPlan_to_RuntimeBootstrap_Contract.md] Defines minimum `DeploymentPlan`, `RuntimeBinding`, `RuntimeBootstrapRequest`, command envelope, bootstrap invariants, role behavior, failure behavior, and acceptance for paper bootstrap/live health-only.
- [SD-P0-03_RuntimeBinding_Context_Propagation.md] Defines `PantheonRuntimeContext`, context source modes, `PantheonAlgoBase` methods, context invariants, failure behavior by environment, and tests for context loading and telemetry attachment.
- [SD-P0-04_Paper_Runtime_TelemetryEvent_Contract.md] Defines paper telemetry event types, required fields, bridge metadata, projection shape, ingest requirements, hard invariants, failure behavior, and producer/ingest/projection tests.
- [SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md] Defines frontend route source modes, staging/prod demo/auth prohibitions, command client fields, source mode badges, failure behavior, and acceptance criteria that BFF unavailable cannot silently become demo success.
- [SD-P0-06_Submodule_Compose_Health_CI_Verification.md] Defines CI checks for submodule authority, no wrong repo target, paper bootstrap, live fail-closed, compose/health, source/search bounded baseline, research/OpenClaw fail-closed, and frontend demo production guard.
- [SA-11_operating_loop_gap_analysis.md] Operating loop completion requires canonical objects, commands, events, stores, policy, integration, audit, replay, and tests.
- [SA-13_contract_schema_gap_analysis.md] Identifies `RuntimeBinding` as the pivot for deployment/runtime/telemetry/reconciliation attribution; also identifies command contract, launch manifest, telemetry, event envelope, error, idempotency, RBAC, and entitlement gaps.
- [SA-17_telemetry_reconciliation_evolution_gap_analysis.md] Telemetry/reconciliation loop requires producer context, ingest validation, runtime projection, `ReconciliationRecord`, `IncidentCase`, and later evolution/postmortem dispatch; minimum telemetry loop includes simple reconciliation and incident threshold behavior.
- [SA-19_v2_gap_matrix_master_corrected.md] Corrects repo mapping to `pantheon/lean` and lists P0 gaps for ADR, CI, bootstrap, context, telemetry, live guard, frontend demo cleanup, and health cleanup, while listing BFF command/read cleanup and paper reconciliation baseline in P1.
- [SA-20_v2_risk_register_corrected.md] Top risks include repo mapping drift, live health-only mistaken as live-ready, RuntimeBinding propagation unverified, paper telemetry producer unverified, bracket order log-only, frontend demo/auth issues, and reconciliation not tied to paper runtime.
