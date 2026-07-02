# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.
Absolute times below use 台灣時間 (UTC+8).

Last updated: 2026-07-02 23:41:44

## Objective

Implement the paper-first persona lifecycle: create persona directly into paper runtime, evaluate paper cohorts, require human approval for canary, live, and quarterly allocation changes, and enforce automatic risk guardrails that can pause, reduce, risk-off, or freeze immediately.

## Current Sprint

- Sprint: `2026-07-02-persona-paper-live-gap`
- Canonical files: `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `ai-activity-log.jsonl`, `current-work.md`, `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`, `CANONICAL_DOCUMENT_MAP.md`, `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`, `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `WORKBENCH_DELIVERY_BACKLOG.md`, `DELIVERY_CLOSURE_AND_LOOP_STATES.md`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`, `CANONICAL_CONTRACT_MIGRATION_DECISION.md`, `WORK_REBASELINE.md`, `Pantheon_總索引版系統分析文件.md`, `Pantheon_資料表_Schema_設計版.md`, `Pantheon_API_Service_Contract_設計版.md`
- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Platform Architecture & Policy`, `L2 Planning & Execution`, `L3 Supporting Design & Migration`
- Canonical map: `CANONICAL_DOCUMENT_MAP.md`
- Document boundary: `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`
- Full backlog: `DEVELOPMENT_WORKBREAKDOWN.md`
- Workbench backlog: `WORKBENCH_DELIVERY_BACKLOG.md`
- Loop closure: `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- Execution proof: `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- Dashboard: `docs-site/index.html`

## Active Slices

- `Claude`: execution, control-plane, governance-review; next: Assignment created
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: No active assignment
- `Codex`: integration, status-system, schema, acceptance; next: Risk guardrail evaluator now emits pause/reduce/risk_off/frozen events with incident evidence, no promotion/allocation authority, and human-review resume markers for risk_off/frozen. Validation: pytest capital risk policy, persona paper/live schema, fleet contract regressions.
- `Codex2`: integration, status-system, schema, acceptance; next: Assignment created
- `Copilot`: research-ingest, external-search, spec-review, critique; next: No active assignment
- `Claude2`: execution, control-plane, governance-review; next: Assignment created
- `Gemini2`: gcp, ci-cd, runtime-packaging, worker-ops; next: Assignment created
- `Human/Ops`: human-gate, operations, signoff; next: No active assignment
- `Antigravity`: gcp, ci-cd, runtime-packaging, worker-ops; next: No active assignment
- `Antigravity2`: gcp, ci-cd, runtime-packaging, worker-ops; next: No active assignment

## Delivery Layers

### Primary Project Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `PPLG-001` | EPIC PPLG / contracts | Canonical persona paper/live state and contract alignment | Codex | review | - | 鎖定 paper-first persona lifecycle, schema, endpoint contract, 舊 onboarding spec supersession。建立完成必須是 paper runtime 或 setup_failed。 |
| `PPLG-002` | EPIC PPLG / paper launch | Idempotent create-to-paper persona launch workflow | Claude | todo | `PPLG-001` | 實作 POST /bff/management/personas/paper-launch，一次完成 persona、paper pool binding、paper plan、paper approval、RuntimeBinding、paper runtime startup。 |
| `PPLG-003` | EPIC PPLG / fleet read model | Persona Fleet readiness projection and payload cleanup | Codex2 | todo | `PPLG-001` | 補 Fleet readiness/competition projection 並移除重複大 payload，讓 row 在同一 cohort 顯示 paper challengers、canary challengers、live incumbents。 |
| `PPLG-004` | EPIC PPLG / evaluation ranking | Paper eligibility and unified competition ranking engine | Claude2 | todo | `PPLG-001` | 實作 paper hard gates、promotion_score、paper/canary/live 同 cohort ranking 與 recommendation packet；系統只推薦，不批准實盤。 |
| `PPLG-005` | EPIC PPLG / human review | Human review workflows for canary live and quarterly ranking | Claude | todo | `PPLG-004` | 實作 promotion/canary/live/quarterly/replacement/resume human review，所有真錢資金進出與季度重排都需人審。 |
| `PPLG-006` | EPIC PPLG / risk guardrails | Automatic risk guardrails and incident review evidence | Codex | review | `PPLG-001` | 實作虧損、drawdown、exposure、slippage、order/data/runtime/policy/correlation guardrails，可自動 pause/reduce/risk_off/freeze 並建立事件審核。 |
| `PPLG-007` | EPIC PPLG / frontend UX | Frontend Create Paper Persona and unified Fleet UX | Codex2 | todo | `PPLG-002`, `PPLG-003`, `PPLG-005` | 更新 Persona Registry/Fleet：主要 CTA 是建立 Paper Persona，row action 依狀態顯示，研究/模擬/正式只控制命令上下文，不拆開 paper/live 競爭視圖。 |
| `PPLG-008` | EPIC PPLG / verification | End-to-end release gate and fleet closeout | Gemini2 | todo | `PPLG-002`, `PPLG-003`, `PPLG-004`, `PPLG-005`, `PPLG-006`, `PPLG-007` | 建立完整驗證包：create->paper runtime->evaluation->human review->canary/live/quarterly/risk-off 全流程證據。 |

### External / Upstream Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| _(none)_ | - | - | - | - | - | - |

## Recently Executed Tasks

- Archive updated: 2026-06-22 16:33:56
- Terminal tasks archived: `1697` total, `1668` completed, `29` superseded

| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |
|---|---|---|---|---|---|---|
| `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | EPIC AGORA-FE / Phase 2 | Prepare AG-FE-SW-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 16:33:56 | `ai-task-archive/tasks/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.json` |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | EPIC AGORA-FE / Phase 3 | Prepare AG-FE-RS-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 16:15:20 | `ai-task-archive/tasks/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-43` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 16:02:31 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-43.json` |
| `AG-BE-TR-002` | EPIC AGORA-TR / Phase 4 | Governed TradingIntent / handoff | Codex | completed | 2026-06-22 15:42:08 | `ai-task-archive/tasks/AG-BE-TR-002.json` |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | EPIC AGORA-TR / Phase 4 | Prepare AG-BE-TR-002 BFF and frontend handoff packet | Claude | completed | 2026-06-22 15:39:38 | `ai-task-archive/tasks/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.json` |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | EPIC AGORA-FE / Phase 3 | Prepare AG-FE-RS-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 15:20:01 | `ai-task-archive/tasks/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.json` |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | EPIC AGORA-TR / Phase 4 | Prepare AG-BE-TR-002 BFF and frontend handoff packet | Claude | superseded | 2026-06-22 15:19:03 | `ai-task-archive/tasks/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.json` |
| `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | EPIC AGORA-SW / Phase 2 | Prepare AG-BE-SW-001 BFF and frontend handoff packet | Claude2 | superseded | 2026-06-22 15:18:40 | `ai-task-archive/tasks/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 15:10:33 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-41` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 14:52:05 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-41.json` |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | EPIC AGORA-FE / Phase 3 | Prepare AG-FE-RS-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 14:31:56 | `ai-task-archive/tasks/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.json` |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | EPIC AGORA-FE / Phase 3 | Prepare AG-FE-RS-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 14:17:17 | `ai-task-archive/tasks/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-40` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 14:06:33 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-40.json` |
| `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | EPIC AGORA-FE / Phase 2 | Prepare AG-FE-SW-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 13:44:25 | `ai-task-archive/tasks/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.json` |
| `AG-FE-SW-001-SIDECAR-BFF-HANDOFF` | EPIC AGORA-FE / Phase 2 | Prepare AG-FE-SW-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 13:31:28 | `ai-task-archive/tasks/AG-FE-SW-001-SIDECAR-BFF-HANDOFF.json` |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | EPIC AGORA-FE / Phase 3 | Prepare AG-FE-RS-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 13:10:34 | `ai-task-archive/tasks/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 13:03:48 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-38` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 12:42:28 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-38.json` |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | EPIC AGORA-FE / Phase 3 | Prepare AG-FE-RS-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 12:23:44 | `ai-task-archive/tasks/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.json` |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-37` | EPIC AGORA-FE / Phase 1 | Prepare AG-FE-ID-001 BFF and frontend handoff packet | Codex | completed | 2026-06-22 12:13:45 | `ai-task-archive/tasks/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-37.json` |

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `PPLG-001` | EPIC PPLG / contracts | Canonical persona paper/live state and contract alignment | 鎖定 paper-first persona lifecycle, schema, endpoint contract, 舊 onboarding spec supersession。建立完成必須是 paper runtime 或 setup_failed。 | Codex | Claude | review | - | 2026-07-02 23:29:04 | Canonical persona paper/live schema, BFF endpoint contract, old wizard supersession, and schema regression tests are ready for review. |
| `PPLG-002` | EPIC PPLG / paper launch | Idempotent create-to-paper persona launch workflow | 實作 POST /bff/management/personas/paper-launch，一次完成 persona、paper pool binding、paper plan、paper approval、RuntimeBinding、paper runtime startup。 | Claude | Codex | todo | `PPLG-001` | 2026-07-02 21:47:50 | Assignment created |
| `PPLG-003` | EPIC PPLG / fleet read model | Persona Fleet readiness projection and payload cleanup | 補 Fleet readiness/competition projection 並移除重複大 payload，讓 row 在同一 cohort 顯示 paper challengers、canary challengers、live incumbents。 | Codex2 | Claude2 | todo | `PPLG-001` | 2026-07-02 21:48:00 | Assignment created |
| `PPLG-004` | EPIC PPLG / evaluation ranking | Paper eligibility and unified competition ranking engine | 實作 paper hard gates、promotion_score、paper/canary/live 同 cohort ranking 與 recommendation packet；系統只推薦，不批准實盤。 | Claude2 | Codex | todo | `PPLG-001` | 2026-07-02 21:48:10 | Assignment created |
| `PPLG-005` | EPIC PPLG / human review | Human review workflows for canary live and quarterly ranking | 實作 promotion/canary/live/quarterly/replacement/resume human review，所有真錢資金進出與季度重排都需人審。 | Claude | Codex2 | todo | `PPLG-004` | 2026-07-02 21:48:22 | Assignment created |
| `PPLG-006` | EPIC PPLG / risk guardrails | Automatic risk guardrails and incident review evidence | 實作虧損、drawdown、exposure、slippage、order/data/runtime/policy/correlation guardrails，可自動 pause/reduce/risk_off/freeze 並建立事件審核。 | Codex | Claude2 | review | `PPLG-001` | 2026-07-02 23:41:44 | Risk guardrail evaluator now emits pause/reduce/risk_off/frozen events with incident evidence, no promotion/allocation authority, and human-review resume markers for risk_off/frozen. Validation: pytest capital risk policy, persona paper/live schema, fleet contract regressions. |
| `PPLG-007` | EPIC PPLG / frontend UX | Frontend Create Paper Persona and unified Fleet UX | 更新 Persona Registry/Fleet：主要 CTA 是建立 Paper Persona，row action 依狀態顯示，研究/模擬/正式只控制命令上下文，不拆開 paper/live 競爭視圖。 | Codex2 | Claude | todo | `PPLG-002`, `PPLG-003`, `PPLG-005` | 2026-07-02 21:48:46 | Assignment created |
| `PPLG-008` | EPIC PPLG / verification | End-to-end release gate and fleet closeout | 建立完整驗證包：create->paper runtime->evaluation->human review->canary/live/quarterly/risk-off 全流程證據。 | Gemini2 | Codex | todo | `PPLG-002`, `PPLG-003`, `PPLG-004`, `PPLG-005`, `PPLG-006`, `PPLG-007` | 2026-07-02 21:48:57 | Assignment created |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| `PPLG-001` | Codex | Claude | Canonical persona paper/live schema, BFF endpoint contract, old wizard supersession, and schema regression tests are ready for review. | pending | 2026-07-02 23:29:04 |
| `PPLG-006` | Codex | Claude2 | Risk guardrail evaluator now emits pause/reduce/risk_off/frozen events with incident evidence, no promotion/allocation authority, and human-review resume markers for risk_off/frozen. Validation: pytest capital risk policy, persona paper/live schema, fleet contract regressions. | pending | 2026-07-02 23:41:44 |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| _(none)_ | - | - | - | - |

## Review Notes

| Task | Reviewer | 修正重點 | Review File |
|---|---|---|---|
| _(none)_ | - | - | - |

## Lovable Coordination

- Last coordination scan: -
- Tracked features: `0`
- Lovable-ready packets: `0`
- Waiting for Lovable/front-end: `0`
- UI-done returned: `0`
- Frontend feedback returned: `0`
- Open BFF gaps: `0`
- Backend route live: `0`
- Pantheon handoff published: `0`
- Mirrored to front default branch: `0`
- Dispatch recorded in coordinator state: `0`
- Receiver-visible payload on front default branch: `0`
- Lovable consumed packet: `0`
- UI activated: `0`
- Runtime verified: `0`

| Feature | Screen | Stage | Lovable Ready | Mirrored | UI Done | Feedback | Next Action |
|---|---|---|---|---|---|---|---|
| _(none)_ | - | - | - | - | - | - | - |

Tracked-feature note: the table above only lists modules that currently have coordination feature records.
Archive-done route-live activation publication lanes that remain outside explicit feature rows: `CW-02`, `KW-04`, `KW-05`, `RW-02`, `RW-04`, `RW-05`, `KW-02`, `KW-03`, `TW-01`, `TW-02`, `TW-04`.
Do not read those omitted modules as open Pantheon backlog purely because they are absent from the coordination feature table.

## Latest Checkpoints

- 2026-05-16 01:52:28 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:31 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:32 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:52:37 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:38 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:52:42 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:43 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:52:47 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:48 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:52:52 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:53 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:52:57 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:52:57 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:53:02 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:53:02 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:53:08 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:53:08 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:53:13 Orchestrator: PreToolUse: Bash
- 2026-07-02 23:41:38 Codex: `PPLG-006` Implementing automatic risk guardrails with incident evidence and human-review resume semantics.
- 2026-07-02 23:41:44 Codex: `PPLG-006` Handoff to Claude2: Risk guardrail evaluator now emits pause/reduce/risk_off/frozen events with incident evidence, no promotion/allocation authority, and human-review resume markers for risk_off/frozen. Validation: pytest capital risk policy, persona paper/live schema, fleet contract regressions.
