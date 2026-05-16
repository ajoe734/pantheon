# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.
Absolute times below use 台灣時間 (UTC+8).

Last updated: 2026-05-16 15:02:12

## Objective

跨進開發團隊 GAP master rebaseline (docs/04/pantheon_sa_supplemental_2026-05-15/GAP_dev_team_master_rebaseline_2026-05-15.md)，以 pantheon@master + execute-plans@main 為基準。並行 6 條 EPIC，按 P0→P3 階梯推進：(I) EPIC-BFF-P0 (P0 10 task / Sprint 1) — session trio (/bff/me, auth/refresh, logout) + /openapi.json + canonical action endpoint + approval decide + registry reads (strategies/personas/capital-pools/audit)，讓 execute-plans@main 在 VITE_BFF_FALLBACK=strict 下可 bootstrap 核心 Management flow 不再 fallback mock；(II) EPIC-GOV-DEPLOY (P1 5 task / Sprint 2) — ApprovalDecision first-class + DeploymentPlan contract/service + stage planner + deployment projection + pool/runtime compatibility 檢查；(III) EPIC-RUNTIME (P1 6 task / Sprint 3) — RuntimeBinding schema + Runtime Manager skeleton + /bff/runtimes + deploy/pause/replace/rollback actions + loader metadata migration (promotion_state → artifact_state + deployment_stage) + LEAN algorithm-level smoke；(IV) EPIC-TELEMETRY (P2 7 task / Sprint 4) — TelemetryEvent canonical schema + RuntimeHeartbeat ingest + AuditAction backend + /bff/alerts + /bff/incidents + reconciliation record + Postmortem schema/endpoint；(V) EPIC-RESEARCH (P3 28 task / Sprint 5) — Source Ingest (SRC) + StrategySpec (STRAT) + Experiment orchestrator (EXP) + Qlib/vectorbt adapters + Persona/Trainer (PER/TRN) + Imitation dataset (IMT) + Consult/Committee (ASK)；(VI) EPIC-EVOLUTION (P3 3 task / Sprint 6) — EvolutionDecision service + /bff/v5/loop-runs + /bff/v5/sentinel/findings。GAP § 10 最大阻塞：BFF live endpoints 不足 → EPIC-BFF-P0 必須最先收斂；Registry/Promotion canonical 已 implemented，DeploymentPlan/RuntimeBinding 是 governance→execution 缺口；Artifact Loader 仍寫 legacy promotion_state，EX-002 metadata migration 是 execution-side 技術債。fail-closed 鐵律延續：broker production live、capital binding live 仍禁止；canary 需 risk-owner + operator 雙閘；evidence 走 support/evidence/<epic>-<task>/。Track E 收尾備註：46 個 MGMT-* task 中 45 個 done+archive，僅 MGMT-BROKER-002 仍 blocked 等 Shioaji credentials (commit 22e5ca3b 已備 sidecar acceptance packet)；M7 canary readiness 因此未閉合；Track E objective 不在本 sprint 推進範圍，僅 carry-over 記錄。

## Current Sprint

- Sprint: `2026-05-16-pantheon-bff-p0-foundation`
- Canonical files: `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `ai-activity-log.jsonl`, `current-work.md`, `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`, `CANONICAL_DOCUMENT_MAP.md`, `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`, `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `WORKBENCH_DELIVERY_BACKLOG.md`, `DELIVERY_CLOSURE_AND_LOOP_STATES.md`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`, `CANONICAL_CONTRACT_MIGRATION_DECISION.md`, `WORK_REBASELINE.md`, `Pantheon_總索引版系統分析文件.md`, `Pantheon_資料表_Schema_設計版.md`, `Pantheon_API_Service_Contract_設計版.md`
- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Platform Architecture & Policy`, `L2 Planning & Execution`, `L3 Supporting Design & Migration`
- Planning mode: `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/README.md`
- Canonical map: `CANONICAL_DOCUMENT_MAP.md`
- Document boundary: `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`
- Full backlog: `DEVELOPMENT_WORKBREAKDOWN.md`
- Workbench backlog: `WORKBENCH_DELIVERY_BACKLOG.md`
- Loop closure: `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- Execution proof: `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- Dashboard: `docs-site/index.html`

## Discussion Planning

- Session: `phase6-2026-05-01-pantheon-p0-paper-loop`
- Status: `accepted`
- Baton owner: `Codex`
- Current round: `0`
- Consensus: `accepted`
- Human gate: `approved`
- Ready for human: `True`
- Ready to materialize execution: `True`

## Active Slices

- `Claude`: execution, control-plane, governance-review; next: Codex review in progress: checking EX-002-RB artifact metadata migration diff, evidence, and focused verification.
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: No active assignment
- `Codex`: integration, status-system, schema, acceptance; next: Implementing StrategySpec schema/model with focused contract tests; keeping changes scoped to services/research/strategy_spec and schema docs.
- `Codex2`: integration, status-system, schema, acceptance; next: No active assignment
- `Copilot`: research-ingest, external-search, spec-review, critique; next: No active assignment
- `Claude2`: execution, control-plane, governance-review; next: EXP-002 implementation complete. Contract test file: services/control-plane/bff/test_exp002_bff_research_experiments_contract.py (17 tests, all pass). Commit: 7960c3c5. Covers: GET /bff/research-experiments list envelope+surface+seeded-data+field invariants+auth; GET /bff/research-experiments/{id} detail envelope+surface+completed/running/failed status+analysis_links+404+auth; POST /api/v1/experiments/launch → BFF round-trip. Please review and approve.
- `Gemini2`: gcp, ci-cd, runtime-packaging, worker-ops; next: Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check.

## Delivery Layers

### Primary Project Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | Gemini2 | blocked | - | - |
| `EX-002-RB` | Sprint 3 / EPIC-RUNTIME | Loader metadata migration promotion_state -> artifact_state + deployment_stage (rebaseline) | Claude | review | - | - |
| `TEL-002-RB` | Sprint 4 / EPIC-TELEMETRY | RuntimeHeartbeat ingest endpoint (rebaseline) | Codex | review_approved | - | - |
| `SRC-001` | Sprint 5 / EPIC-RESEARCH | SourceRecord schema + ingest API | Claude | todo | - | - |
| `SRC-002` | Sprint 5 / EPIC-RESEARCH | paper ingest adapter skeleton | Codex | review | - | - |
| `SRC-003` | Sprint 5 / EPIC-RESEARCH | repo allowlist ingest skeleton | Codex | review | - | - |
| `SRC-004` | Sprint 5 / EPIC-RESEARCH | StrategySpecSeed builder | Codex | in_progress | - | - |
| `STRAT-001` | Sprint 5 / EPIC-RESEARCH | StrategySpec schema / model | Codex | in_progress | - | - |
| `STRAT-002` | Sprint 5 / EPIC-RESEARCH | StrategySpec registry endpoints | Codex | in_progress | - | - |
| `STRAT-003` | Sprint 5 / EPIC-RESEARCH | Source -> StrategySpec conversion service | Claude | todo | - | - |
| `STRAT-004` | Sprint 5 / EPIC-RESEARCH | evidence / code refs lineage | Claude | todo | - | - |
| `EXP-001` | Sprint 5 / EPIC-RESEARCH | ExperimentTask / ExperimentRun schema | Claude | todo | - | - |
| `EXP-002` | Sprint 5 / EPIC-RESEARCH | /bff/research-experiments list/detail | Claude2 | review | - | - |
| `EXP-005` | Sprint 5 / EPIC-RESEARCH | ExperimentRun -> Artifact registry writeback | Claude | todo | - | - |
| `PER-002` | Sprint 5 / EPIC-RESEARCH | skills/tools/capabilities read API | Claude2 | todo | - | - |
| `TRN-001` | Sprint 5 / EPIC-RESEARCH | TeachingSession / TeachingEvent schema | Claude | todo | - | - |
| `TRN-002` | Sprint 5 / EPIC-RESEARCH | trainer session endpoints | Claude | todo | - | - |
| `TRN-003` | Sprint 5 / EPIC-RESEARCH | rapid-eval request / response | Claude2 | todo | - | - |
| `TRN-004` | Sprint 5 / EPIC-RESEARCH | trainer commit / discard / replay | Claude | todo | - | - |
| `IMT-001` | Sprint 5 / EPIC-RESEARCH | TraderTrajectory schema | Claude | todo | - | - |
| `IMT-002` | Sprint 5 / EPIC-RESEARCH | PreferenceExample / CorrectionTrace schema | Claude | todo | - | - |
| `IMT-003` | Sprint 5 / EPIC-RESEARCH | imitation dataset builder skeleton | Claude2 | todo | - | - |
| `IMT-004` | Sprint 5 / EPIC-RESEARCH | behavior policy artifact type registration | Claude | todo | - | - |
| `ASK-001` | Sprint 5 / EPIC-RESEARCH | /bff/agora/ask/sessions | Claude2 | todo | - | - |
| `ASK-002` | Sprint 5 / EPIC-RESEARCH | ConsultRequest / ConsultMemo schema | Claude | todo | - | - |
| `ASK-003` | Sprint 5 / EPIC-RESEARCH | ask / committee session lifecycle | Claude2 | todo | - | - |
| `ASK-004` | Sprint 5 / EPIC-RESEARCH | memo publish to registry / review | Claude | todo | - | - |
| `ASK-005` | Sprint 5 / EPIC-RESEARCH | approval / ask SSE event publishing | Claude | todo | - | - |
| `EVO-001` | Sprint 6 / EPIC-EVOLUTION | EvolutionDecision service | Claude | todo | - | - |
| `LOOP-001-RB` | Sprint 6 / EPIC-EVOLUTION | /bff/v5/loop-runs endpoint (rebaseline) | Claude2 | todo | - | - |
| `SENT-001` | Sprint 6 / EPIC-EVOLUTION | /bff/v5/sentinel/findings endpoint | Claude2 | todo | - | - |

### External / Upstream Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| _(none)_ | - | - | - | - | - | - |

## Recently Executed Tasks

- Archive updated: 2026-05-16 14:51:26
- Terminal tasks archived: `1129` total, `1111` completed, `18` superseded

| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |
|---|---|---|---|---|---|---|
| `INC-001-RB` | Sprint 4 / EPIC-TELEMETRY | Claude2 reclaiming as original task owner for review_approved closeout; Codex implementation reviewed and approved. | Claude2 | completed | 2026-05-16 14:51:26 | `ai-task-archive/tasks/INC-001-RB.json` |
| `DEP-002-RB` | Sprint 2 / EPIC-GOV-DEPLOY | DeploymentPlan stage planner (rebaseline) | Codex | completed | 2026-05-16 14:38:02 | `ai-task-archive/tasks/DEP-002-RB.json` |
| `TEL-001-RB` | Sprint 4 / EPIC-TELEMETRY | TelemetryEvent canonical schema (rebaseline) | Codex | completed | 2026-05-16 14:37:31 | `ai-task-archive/tasks/TEL-001-RB.json` |
| `CAP-002-RB` | Sprint 2 / EPIC-GOV-DEPLOY | Pool/runtime compatibility checks (rebaseline) | Codex | completed | 2026-05-16 14:30:13 | `ai-task-archive/tasks/CAP-002-RB.json` |
| `DEP-001-RB` | Sprint 2 / EPIC-GOV-DEPLOY | DeploymentPlan contract + service (rebaseline) | Codex | completed | 2026-05-16 14:27:01 | `ai-task-archive/tasks/DEP-001-RB.json` |
| `RT-003` | Sprint 3 / EPIC-RUNTIME | /bff/runtimes list/detail | Codex | completed | 2026-05-16 14:19:33 | `ai-task-archive/tasks/RT-003.json` |
| `QLIB-001` | Sprint 5 / EPIC-RESEARCH | Qlib adapter skeleton | Claude2 | completed | 2026-05-16 14:13:28 | `ai-task-archive/tasks/QLIB-001.json` |
| `DEP-003` | Sprint 2 / EPIC-GOV-DEPLOY | deployment projection read model | Claude2 | completed | 2026-05-16 14:03:32 | `ai-task-archive/tasks/DEP-003.json` |
| `ALT-001` | Sprint 4 / EPIC-TELEMETRY | /bff/alerts endpoint | Codex | completed | 2026-05-16 14:02:57 | `ai-task-archive/tasks/ALT-001.json` |
| `P0-AUD-001` | Sprint 1 / EPIC-BFF-P0 | /bff/audit read endpoint | Claude2 | completed | 2026-05-16 13:53:57 | `ai-task-archive/tasks/P0-AUD-001.json` |
| `POST-001` | Sprint 4 / EPIC-TELEMETRY | Postmortem schema + endpoint | Codex | completed | 2026-05-16 13:51:19 | `ai-task-archive/tasks/POST-001.json` |
| `AUD-002` | Sprint 4 / EPIC-TELEMETRY | AuditAction backend (write engine) | Codex | completed | 2026-05-16 13:47:22 | `ai-task-archive/tasks/AUD-002.json` |
| `RT-004` | Sprint 3 / EPIC-RUNTIME | Runtime deploy/pause/replace/rollback actions | Codex | completed | 2026-05-16 13:44:24 | `ai-task-archive/tasks/RT-004.json` |
| `RT-002` | Sprint 3 / EPIC-RUNTIME | Runtime Manager skeleton | Codex | completed | 2026-05-16 13:43:58 | `ai-task-archive/tasks/RT-002.json` |
| `P0-PER-001` | Sprint 1 / EPIC-BFF-P0 | /bff/personas list/detail | Claude2 | completed | 2026-05-16 13:43:09 | `ai-task-archive/tasks/P0-PER-001.json` |
| `P0-REG-001` | Sprint 1 / EPIC-BFF-P0 | /bff/strategies list/detail | Claude2 | completed | 2026-05-16 13:38:43 | `ai-task-archive/tasks/P0-REG-001.json` |
| `GOV-001-RB` | Sprint 2 / EPIC-GOV-DEPLOY | ApprovalDecision schema + write authority (rebaseline) | Codex | completed | 2026-05-16 13:34:27 | `ai-task-archive/tasks/GOV-001-RB.json` |
| `REC-001` | Sprint 4 / EPIC-TELEMETRY | Basic reconciliation record | Codex | completed | 2026-05-16 13:17:28 | `ai-task-archive/tasks/REC-001.json` |
| `RT-001` | Sprint 3 / EPIC-RUNTIME | RuntimeBinding schema | Claude | completed | 2026-05-16 09:53:42 | `ai-task-archive/tasks/RT-001.json` |
| `P0-APP-001` | Sprint 1 / EPIC-BFF-P0 | approval decide endpoint /bff/approvals/{id}/decide | Claude | completed | 2026-05-16 09:30:31 | `ai-task-archive/tasks/P0-APP-001.json` |

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | - | Gemini2 | Gemini | blocked | - | 2026-05-15 23:15:06 | Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check. |
| `EX-002-RB` | Sprint 3 / EPIC-RUNTIME | Loader metadata migration promotion_state -> artifact_state + deployment_stage (rebaseline) | - | Claude | Codex | review | - | 2026-05-16 14:59:27 | Codex review in progress: checking EX-002-RB artifact metadata migration diff, evidence, and focused verification. |
| `TEL-002-RB` | Sprint 4 / EPIC-TELEMETRY | RuntimeHeartbeat ingest endpoint (rebaseline) | - | Codex | Claude | review_approved | - | 2026-05-16 15:01:02 | Review approved: all 24 telemetry tests pass; heartbeat adapter, binding guard, projection fields, and staleness handling are correct. Returning to Codex for finalization. |
| `SRC-001` | Sprint 5 / EPIC-RESEARCH | SourceRecord schema + ingest API | - | Claude | Codex | todo | - | 2026-05-16 14:51:28 | Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `SRC-002` | Sprint 5 / EPIC-RESEARCH | paper ingest adapter skeleton | - | Codex | Claude | review | - | 2026-05-16 14:55:59 | SRC-002 ready for review. Implemented OpenAlex paper ingest adapter skeleton: bounded connector/fetch policy, pure OpenAlex work-to-SourceRecord normalization preserving DOI/authors/publication/event/available time/license/access/governance metadata, execution-route denial, provider catalog export, and source connector doc note. Evidence: support/evidence/SRC-002/README.md. Verification: py_compile paper/examples/__init__; pytest test_paper_ingest_adapter.py 3 passed; pytest connector_framework + registry provider example 4 passed; pytest services/source_ingestion 68 passed. Note: __init__.py/examples.py also contain adjacent SRC-003 repo allowlist hunks already present; SRC-002-owned hunks are OpenAlexPaperIngestAdapter export/catalog swap. |
| `SRC-003` | Sprint 5 / EPIC-RESEARCH | repo allowlist ingest skeleton | - | Codex | Claude | review | - | 2026-05-16 14:50:37 | Repo allowlist ingest skeleton ready for review. Added RepoAllowlistProvider/Entry, exposed provider catalog example, and covered allowlist/static-record fetch behavior plus URL/path/duplicate rejection. Verification: python3 -m py_compile services/source_ingestion/connectors/repo_allowlist.py services/source_ingestion/connectors/examples.py services/source_ingestion/connectors/__init__.py; python3 -m pytest services/source_ingestion/tests/test_repo_allowlist.py -q; python3 -m pytest services/source_ingestion/tests -q. |
| `SRC-004` | Sprint 5 / EPIC-RESEARCH | StrategySpecSeed builder | - | Codex | Claude | in_progress | - | 2026-05-16 14:59:18 | Picked up SRC-004 StrategySpecSeed builder implementation in the background worker. |
| `STRAT-001` | Sprint 5 / EPIC-RESEARCH | StrategySpec schema / model | - | Codex | Claude | in_progress | - | 2026-05-16 15:01:04 | Implementing StrategySpec schema/model with focused contract tests; keeping changes scoped to services/research/strategy_spec and schema docs. |
| `STRAT-002` | Sprint 5 / EPIC-RESEARCH | StrategySpec registry endpoints | - | Codex | Claude | in_progress | - | 2026-05-16 15:00:00 | Supervisor auto-started STRAT-002 after successful dispatch. |
| `STRAT-003` | Sprint 5 / EPIC-RESEARCH | Source -> StrategySpec conversion service | - | Claude | Claude2 | todo | - | 2026-05-16 09:29:33 | Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `STRAT-004` | Sprint 5 / EPIC-RESEARCH | evidence / code refs lineage | - | Claude | Copilot | todo | - | 2026-05-16 08:53:41 | Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `EXP-001` | Sprint 5 / EPIC-RESEARCH | ExperimentTask / ExperimentRun schema | - | Claude | Claude2 | todo | - | 2026-05-16 08:53:53 | Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `EXP-002` | Sprint 5 / EPIC-RESEARCH | /bff/research-experiments list/detail | - | Claude2 | Codex2 | review | - | 2026-05-16 15:01:46 | EXP-002 implementation complete. Contract test file: services/control-plane/bff/test_exp002_bff_research_experiments_contract.py (17 tests, all pass). Commit: 7960c3c5. Covers: GET /bff/research-experiments list envelope+surface+seeded-data+field invariants+auth; GET /bff/research-experiments/{id} detail envelope+surface+completed/running/failed status+analysis_links+404+auth; POST /api/v1/experiments/launch → BFF round-trip. Please review and approve. |
| `EXP-005` | Sprint 5 / EPIC-RESEARCH | ExperimentRun -> Artifact registry writeback | - | Claude | Claude2 | todo | - | 2026-05-16 08:18:44 | Auto-reassigned ownership from Codex to Claude after repeated Codex terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `PER-002` | Sprint 5 / EPIC-RESEARCH | skills/tools/capabilities read API | - | Claude2 | Codex | todo | - | 2026-05-16 07:30:18 | Assignment created |
| `TRN-001` | Sprint 5 / EPIC-RESEARCH | TeachingSession / TeachingEvent schema | - | Claude | Claude2 | todo | - | 2026-05-16 08:54:05 | Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `TRN-002` | Sprint 5 / EPIC-RESEARCH | trainer session endpoints | - | Claude | Codex2 | todo | - | 2026-05-16 07:31:25 | Assignment created |
| `TRN-003` | Sprint 5 / EPIC-RESEARCH | rapid-eval request / response | - | Claude2 | Copilot | todo | - | 2026-05-16 07:31:56 | Assignment created |
| `TRN-004` | Sprint 5 / EPIC-RESEARCH | trainer commit / discard / replay | - | Claude | Codex2 | todo | - | 2026-05-16 07:32:26 | Assignment created |
| `IMT-001` | Sprint 5 / EPIC-RESEARCH | TraderTrajectory schema | - | Claude | Claude2 | todo | - | 2026-05-16 08:36:13 | Auto-reassigned ownership from Codex to Claude after repeated Codex terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `IMT-002` | Sprint 5 / EPIC-RESEARCH | PreferenceExample / CorrectionTrace schema | - | Claude | Claude2 | todo | - | 2026-05-16 09:11:18 | Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `IMT-003` | Sprint 5 / EPIC-RESEARCH | imitation dataset builder skeleton | - | Claude2 | Claude | todo | - | 2026-05-16 09:46:44 | Auto-reassigned ownership from Codex2 to Claude2 after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude2 starts a fresh run. |
| `IMT-004` | Sprint 5 / EPIC-RESEARCH | behavior policy artifact type registration | - | Claude | Claude2 | todo | - | 2026-05-16 08:36:24 | Auto-reassigned ownership from Codex to Claude after repeated Codex terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `ASK-001` | Sprint 5 / EPIC-RESEARCH | /bff/agora/ask/sessions | - | Claude2 | Codex2 | todo | - | 2026-05-16 07:35:24 | Assignment created |
| `ASK-002` | Sprint 5 / EPIC-RESEARCH | ConsultRequest / ConsultMemo schema | - | Claude | Claude2 | todo | - | 2026-05-16 08:36:34 | Auto-reassigned ownership from Codex to Claude after repeated Codex terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `ASK-003` | Sprint 5 / EPIC-RESEARCH | ask / committee session lifecycle | - | Claude2 | Codex | todo | - | 2026-05-16 07:36:12 | Assignment created |
| `ASK-004` | Sprint 5 / EPIC-RESEARCH | memo publish to registry / review | - | Claude | Codex2 | todo | - | 2026-05-16 07:36:31 | Assignment created |
| `ASK-005` | Sprint 5 / EPIC-RESEARCH | approval / ask SSE event publishing | - | Claude | Codex2 | todo | - | 2026-05-16 08:53:22 | Auto-reassigned ownership from Codex to Claude after repeated Codex terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run. |
| `EVO-001` | Sprint 6 / EPIC-EVOLUTION | EvolutionDecision service | - | Claude | Codex | todo | - | 2026-05-16 07:37:02 | Assignment created |
| `LOOP-001-RB` | Sprint 6 / EPIC-EVOLUTION | /bff/v5/loop-runs endpoint (rebaseline) | - | Claude2 | Codex2 | todo | - | 2026-05-16 07:37:18 | Assignment created |
| `SENT-001` | Sprint 6 / EPIC-EVOLUTION | /bff/v5/sentinel/findings endpoint | - | Claude2 | Codex | todo | - | 2026-05-16 07:37:33 | Assignment created |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| `EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE` | Gemini | Codex2 | Acceptance packet and dependency map for EP5-BROKER-TW-002 prepared at support/sidecars/EP5-BROKER-TW-002/EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE.md. Ready for review and incorporation into parent closeout. | pending | 2026-05-12 22:50:00 |
| `EX-002-RB` | Claude | Codex | EX-002-RB ready for review. Migration: PromotionGate.build_execution_projection() now emits artifact_state+deployment_stage (with promotion_state retained during transition). ArtifactLoader._validate_metadata() reads deployment_stage first (fallback promotion_state). Schema: artifact_state+deployment_stage added as optional properties; allOf condition migrated to deployment_stage==live. 6 new migration tests + original 9 pass (15 total). 65 registry tests pass. Evidence: support/evidence/EX-002-RB/README.md. Verification: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/test_artifact_loader.py -v; PYTHONDONTWRITEBYTECODE=1 python3 services/execution/smoke_test_artifact_loader.py; PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/ -v | pending | 2026-05-16 14:46:03 |
| `SRC-003` | Codex | Claude | Repo allowlist ingest skeleton ready for review. Added RepoAllowlistProvider/Entry, exposed provider catalog example, and covered allowlist/static-record fetch behavior plus URL/path/duplicate rejection. Verification: python3 -m py_compile services/source_ingestion/connectors/repo_allowlist.py services/source_ingestion/connectors/examples.py services/source_ingestion/connectors/__init__.py; python3 -m pytest services/source_ingestion/tests/test_repo_allowlist.py -q; python3 -m pytest services/source_ingestion/tests -q. | pending | 2026-05-16 14:50:37 |
| `SRC-002` | Codex | Claude | SRC-002 ready for review. Implemented OpenAlex paper ingest adapter skeleton: bounded connector/fetch policy, pure OpenAlex work-to-SourceRecord normalization preserving DOI/authors/publication/event/available time/license/access/governance metadata, execution-route denial, provider catalog export, and source connector doc note. Evidence: support/evidence/SRC-002/README.md. Verification: py_compile paper/examples/__init__; pytest test_paper_ingest_adapter.py 3 passed; pytest connector_framework + registry provider example 4 passed; pytest services/source_ingestion 68 passed. Note: __init__.py/examples.py also contain adjacent SRC-003 repo allowlist hunks already present; SRC-002-owned hunks are OpenAlexPaperIngestAdapter export/catalog swap. | pending | 2026-05-16 14:55:59 |
| `TEL-002-RB` | Claude | Codex | Review approved: all 24 telemetry tests pass; heartbeat adapter, binding guard, projection fields, and staleness handling are correct. Returning to Codex for finalization. | pending | 2026-05-16 15:01:02 |
| `EXP-002` | Claude2 | Codex2 | EXP-002 implementation complete. Contract test file: services/control-plane/bff/test_exp002_bff_research_experiments_contract.py (17 tests, all pass). Commit: 7960c3c5. Covers: GET /bff/research-experiments list envelope+surface+seeded-data+field invariants+auth; GET /bff/research-experiments/{id} detail envelope+surface+completed/running/failed status+analysis_links+404+auth; POST /api/v1/experiments/launch → BFF round-trip. Please review and approve. | pending | 2026-05-16 15:01:46 |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| `MGMT-BROKER-002` | Gemini2 | Gemini | Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check. | open |

## Review Notes

| Task | Reviewer | 修正重點 | Review File |
|---|---|---|---|
| `TEL-002-RB` | Claude | 審查通過。heartbeat adapter 正確轉換 SD-09 RuntimeHeartbeat → TelemetryEvent；binding fail-closed；projection 保留 connectivity/broker/lag/health 欄位；24 個測試全通過。 | support/evidence/TEL-002-RB/review-claude.md |

## Lovable Coordination

- Last coordination scan: 2026-05-03 18:57:30
- Tracked features: `46`
- Lovable-ready packets: `45`
- Waiting for Lovable/front-end: `0`
- UI-done returned: `46`
- Frontend feedback returned: `46`
- Open BFF gaps: `0`
- Backend route live: `45`
- Pantheon handoff published: `45`
- Mirrored to front default branch: `45`
- Dispatch recorded in coordinator state: `46`
- Receiver-visible payload on front default branch: `45`
- Lovable consumed packet: `46`
- UI activated: `46`
- Runtime verified: `46`

| Feature | Screen | Stage | Lovable Ready | Mirrored | UI Done | Feedback | Next Action |
|---|---|---|---|---|---|---|---|
| `CW-01-consult-request` | consult-request | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `CW-02-debate-transcript` | consultation-debate-transcript | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `CW-03-committee-board` | consultation-committee-board | `loop_complete` | no | no | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `CW-04-redteam-memo` | redteam-memo | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `EW-05-mutation-review` | mutation-review | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `F-042` | promotion-review | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-01-institutional-memory` | institutional-memory | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-02-research-notes` | knowledge-research-notes | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-03-evidence-refs` | knowledge-evidence-refs | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-04-insight-cards` | knowledge-insight-cards | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-05-strategy-spec` | knowledge-strategy-spec | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-001-deployment-review` | deployment-review-console | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-001-governance-review-queue` | governance-review-queue | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-002-incident-action-drawer` | incident-action-drawer | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-002-incident-detail` | incident-detail | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-002-incident-home` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-evolution-center` | evolution-center | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-inspiration-graph` | inspiration-graph | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-lineage-view` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-post-incident-review` | post-incident-review-console | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-capital-binding-drilldowns` | capital-binding-drilldowns | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-deployment-approval-drilldowns` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-persona-drilldowns` | persona-drilldowns | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-persona-management` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-005-degradation-banner` | global-degradation-banner | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-005-sse-substrate` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-006-approval-queue` | governance-approval-queue | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-007-deployment-diff` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-008-rollback-review` | governance-rollback-review | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-009-governance-audit-rail` | governance-audit-rail | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-010-runtime-state-board` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-011-health-status-board` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-012-alerts-rail` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-013-operator-home` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-014-paper-live-drift` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-consultation-workbench` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-knowledge-workbench` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-01-research-ticket` | research-ticket | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-02-search` | research-search | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-03-analyze` | research-analyze | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-04-experiment-launch` | experiment-launch | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-05-artifact-compare` | artifact-compare | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-01-teaching-dialog` | teaching-dialog | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-02-parameter-controls` | parameter-controls | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-03-before-after-compare` | before-after-compare | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-04-teaching-replay` | teaching-replay | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |

## Latest Checkpoints

- 2026-05-16 15:00:53 Orchestrator: PostToolUse: Bash
- 2026-05-16 15:00:53 Orchestrator: PreToolUse: Write
- 2026-05-16 15:00:53 Orchestrator: PostToolUse: Write
- 2026-05-16 15:00:58 Orchestrator: PreToolUse: Bash
- 2026-05-16 15:01:00 Orchestrator: PreToolUse: Bash
- 2026-05-16 15:01:01 Orchestrator: PostToolUse: Bash
- 2026-05-16 15:01:02 Claude: `TEL-002-RB` Review approved: all 24 telemetry tests pass; heartbeat adapter, binding guard, projection fields, and staleness handling are correct. Returning to Codex for finalization.
- 2026-05-16 15:01:04 Codex: `STRAT-001` Implementing StrategySpec schema/model with focused contract tests; keeping changes scoped to services/research/strategy_spec and schema docs.
- 2026-05-16 15:01:15 Orchestrator: PreToolUse: Bash
- 2026-05-16 15:01:17 Claude2: `EXP-002` All 17 EXP-002 contract tests pass: list shape, detail shape, surface status, seeded-data presence, field invariants, 404, auth gate, launch round-trip. Contract test committed at 7960c3c5. Verification: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_exp002_bff_research_experiments_contract.py -v → 17 passed.
- 2026-05-16 15:01:28 Orchestrator: PostToolUse: Bash
- 2026-05-16 15:01:34 Orchestrator: PreToolUse: Bash
- 2026-05-16 15:01:36 Orchestrator: PostToolUse: Bash
- 2026-05-16 15:01:40 Orchestrator: PostToolUse: Bash
- 2026-05-16 15:01:41 Orchestrator: Stop: Stop
- 2026-05-16 15:01:42 Orchestrator: SessionEnd: SessionEnd
- 2026-05-16 15:01:45 Orchestrator: PreToolUse: Bash
- 2026-05-16 15:01:46 Claude2: `EXP-002` Handoff to Codex2: EXP-002 implementation complete. Contract test file: services/control-plane/bff/test_exp002_bff_research_experiments_contract.py (17 tests, all pass). Commit: 7960c3c5. Covers: GET /bff/research-experiments list envelope+surface+seeded-data+field invariants+auth; GET /bff/research-experiments/{id} detail envelope+surface+completed/running/failed status+analysis_links+404+auth; POST /api/v1/experiments/launch → BFF round-trip. Please review and approve.
- 2026-05-16 15:02:08 Orchestrator: PostToolUse: Bash
- 2026-05-16 15:02:12 Orchestrator: PreToolUse: Bash
