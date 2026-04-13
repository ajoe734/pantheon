# Discussion Planning Mode

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase2-2026-04-12-blueprint-gap-convergence`
- Objective: Use the blueprint gap review, market data scope/source plan, current execution state, and canonical L1/L2 truth to converge on the next Pantheon delivery wave. Required outputs are gap-response-matrix.md, execution-materialization.md, and consensus-packet.md.
- Shared draft owner: `Codex`

## Brief Files

- `Pantheon_Blueprint_Gap_Review_v1.md`
- `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md`
- `ai-status.json`
- `current-work.md`
- `TARGET_ARCHITECTURE.md`
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
- `CANONICAL_DOCUMENT_MAP.md`
- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `OSS_INTEGRATION_CHECKLIST.md`

## Expected Outputs

- `docs/02-architecture/consensus/phase2/gap-response-matrix.md` (owner: `Claude`)
- `docs/02-architecture/consensus/phase2/execution-materialization.md` (owner: `Codex`)
- `docs/02-architecture/consensus/phase2/consensus-packet.md` (owner: `Claude`)

## Baton Loop

1. every lane reads the session brief and writes an independent readout using `LLM_READOUT_TEMPLATE.md`
2. only `Codex` seeds `starter-draft.md`
3. cited cross-review happens round by round
4. unresolved disagreements become explicit `human_required` or `tracking` items
5. the facilitator drafts `consensus-packet.md`
6. after human acceptance, convert `proposed_execution_tasks` into execution tasks through `scripts/planning-state.sh materialize`

## Rules

- only the shared draft owner edits `starter-draft.md`
- reviewers do not directly rewrite the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is the derived dashboard state
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early
