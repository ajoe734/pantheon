# Canonical Document Map

Last updated: 2026-05-18
Status: canonical lookup map for Pantheon document authority
Tier: L2 Planning & Execution
Scope: reading order, tier definitions, conflict resolution, and question-to-document routing
Conflict rule: this file routes you to the right document; it does not override the document it points to

This file is intentionally `L2`, not `L1`, because it coordinates reading and execution flow rather than defining product/runtime semantics.

## 1. Fast Read Order

Start here for almost every task:

1. `AI_COLLABORATION_GUIDE.md`
2. `ai-status.json`
3. `current-work.md` as a human summary only
4. `TARGET_ARCHITECTURE.md`
5. this file
6. `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`
7. `ROADMAP.md`
8. `DEVELOPMENT_WORKBREAKDOWN.md`
9. `WORKBENCH_DELIVERY_BACKLOG.md`
10. `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
11. `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
12. the L1 policy file closest to your task

## 2. Tier Map

| Tier | Purpose | Documents | Override rule |
|---|---|---|---|
| `L0` | collaboration state and operator coordination | `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `ai-activity-log.jsonl` | machine-readable state beats summaries |
| `L0.5` | derived narrative only | `current-work.md` | derived views never outrank `L0` |
| `L1` | current platform architecture and policy semantics | `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`, `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`, `docs/conventions/BFF_RESPONSE_ENVELOPE.md`, `docs/conventions/DEGRADATION_DICTIONARY.md`, `docs/conventions/MODULE_READINESS_LADDER.md`, `docs/decisions/LIN-002-lineage-ownership.md`, `docs/decisions/control-plane-persona-boundary.md`, `docs/decisions/control-plane-router-enforcement-ownership.md` | narrower L1 policy beats broader L1 overview |
| `L2` | canonical planning and document-governance rules | `CANONICAL_DOCUMENT_MAP.md`, `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`, `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `WORKBENCH_DELIVERY_BACKLOG.md`, `DELIVERY_CLOSURE_AND_LOOP_STATES.md`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`, `OSS_INTEGRATION_CHECKLIST.md`, `docs/04/SUPERVISOR_IDLE_EFFICIENCY_CONTROL_AND_MEASUREMENT.md` | planning cannot override L1 semantics |
| `L3` | rationale, migration notes, and future-state blueprints | `CANONICAL_CONTRACT_MIGRATION_DECISION.md`, `WORK_REBASELINE.md`, `Pantheon_總索引版系統分析文件.md`, `Pantheon_資料表_Schema_設計版.md`, `Pantheon_API_Service_Contract_設計版.md` | background only unless explicitly promoted |

Planning sessions, review writeups, and execution loops are not canonical blueprint files. They are working records unless explicitly promoted.

## 3. Conflict Resolution

Apply these rules in order:

1. Generated files never outrank their machine-readable source. `ai-status.json` outranks `current-work.md`.
2. Execution records do not redefine blueprint truth.
3. L1 policy documents outrank L2 planning documents.
4. Within L1, the more specific scope wins.
5. L3 documents may explain or anticipate future work, but they do not redefine current truth.
6. If two documents still appear to conflict, update the broader one to cite the narrower one instead of inventing a third interpretation.

## 4. Question Routing

Use this map when you need an answer quickly:

| Question | Canonical document |
|---|---|
| What is Pantheon vs OpenClaw? | `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md` |
| What is a persona in runtime terms? | `PERSONA_RUNTIME_MODEL.md` |
| Is binding the same as deployment? | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` |
| What are `paper`, `canary`, `live`, and `frozen`? | `PAPER_CANARY_LIVE_POLICY.md` |
| How does rollback affect positions and lineage? | `ROLLBACK_AND_POSITION_SEMANTICS.md` |
| What is the truth model for telemetry and lineage? | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` |
| How does telemetry ingest handle high-frequency writes and backpressure? | `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` |
| Who reviews or auto-executes evolution actions? | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` |
| How does the kill switch bypass governance queues in an emergency? | `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` |
| What happens when a deployment write succeeds but a downstream write fails? | `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` |
| How are conflicting multi-persona proposals resolved for one pool? | `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` |
| Do services share a database or cluster? | `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` |
| How are event order and delivery guaranteed? | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |
| How does the system prevent evolution loops or oscillation? | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` |
| What happens if the BFF or UI goes down? | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` |
| How are loops triggered, scheduled, and race-resolved? | `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` |
| What is the canonical BFF envelope, freshness model, and action-authority wrapper? | `docs/conventions/BFF_RESPONSE_ENVELOPE.md`, `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md` |
| What are the canonical degradation and staleness rules? | `docs/conventions/DEGRADATION_DICTIONARY.md` |
| What readiness states are canonical and how do old labels map to them? | `docs/conventions/MODULE_READINESS_LADDER.md` |
| Who owns UI-facing lineage read truth? | `docs/decisions/LIN-002-lineage-ownership.md` |
| What belongs to persona service truth vs BFF composed persona views? | `docs/decisions/control-plane-persona-boundary.md` |
| Who owns gateway, router, governance, TTL, and command approval concerns? | `docs/decisions/control-plane-router-enforcement-ownership.md` |
| Which external execution, market-data, disclosure, and research vendors are canonical by market? | `DATA_SOURCE_SCOPE_MATRIX.md`, `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` |
| Which files are immutable blueprint vs working records? | `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md` |
| How should supervisor avoid idle token churn and measure auto-worker efficiency? | `docs/04/SUPERVISOR_IDLE_EFFICIENCY_CONTROL_AND_MEASUREMENT.md` |
| How should work be sequenced? | `ROADMAP.md` |
| What exact foundational tasks exist next? | `DEVELOPMENT_WORKBREAKDOWN.md` |
| What product or workbench modules are still open? | `WORKBENCH_DELIVERY_BACKLOG.md` |
| When is a frontend or backend loop actually closed? | `DELIVERY_CLOSURE_AND_LOOP_STATES.md` |
| What does current smoke or acceptance evidence really prove? | `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` |
| Why was the contract model migrated this way? | `CANONICAL_CONTRACT_MIGRATION_DECISION.md` |
| Where is the broader v2 blueprint? | the three `Pantheon_*設計版.md` files |

## 5. Working Rules

- Every new detailed design document must declare its `Tier`, `Scope`, and `Conflict rule`.
- A backlog item in `DEVELOPMENT_WORKBREAKDOWN.md` must cite at least one L1 document.
- Planning sessions and review files must say near the top whether they are active, historical, or execution-facing records.
- L3 future-state docs may inform backlog creation, but acceptance criteria must resolve against L1/L2.
- If a document is historical or supporting, say so near the top of the file.
