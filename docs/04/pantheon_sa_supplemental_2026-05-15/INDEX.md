# Pantheon Management Console & Multi-Persona OODA — Supplemental Index

Date archived: 2026-05-15
Source: 系統設計團隊現況盤點，L2/L3 補充文件
Conflict rule: 本目錄不覆蓋 L1 canonical (`TARGET_ARCHITECTURE.md` 等)；衝突以 L1 為準。

## 文件

| 檔案 | 角色 | 重點 |
|---|---|---|
| [SA_management_console_multi_persona_ooda.md](SA_management_console_multi_persona_ooda.md) | Supplemental System Analysis（Management OODA 切面） | 12 節，含 Gap Register、Delivery Milestones M0–M7、Definition of Done |
| [SD_management_console_multi_persona_ooda.md](SD_management_console_multi_persona_ooda.md) | Supplemental System Design（Management OODA 切面） | 16 節，含 7 個 EPIC、46 個 task、資料 contract、API mapping、Rollback plan |
| [GAP_dev_team_master_rebaseline_2026-05-15.md](GAP_dev_team_master_rebaseline_2026-05-15.md) | 開發團隊 GAP 文件（master 全系統重盤底稿） | 13 節 / 9 個循環 / 3 個大循環 / P0–P3 路線 / 6 個候選 Sprint 切分 |

**三份文件關係**：SA + SD 是 Management Console + OODA layer 的 L2/L3 補充；GAP 是 master 基準下 Pantheon 全系統（research / governance / execution / telemetry / evolution）的盤點底稿，涵蓋面更廣。當下 sprint 的 Track E（46 個 MGMT-* task）對齊 SA/SD 的 EPIC-01..07；GAP 文件提出的 P0/P1/P2/P3 EPIC（BFF-P0 / GOV-DEPLOY / RUNTIME / TELEMETRY / RESEARCH / EVOLUTION）屬於 Track E 之後的下一輪規劃候選。

## 對齊現況 (2026-05-15)

- 已驗收基線：`BFF-CONSOL-027`（Management Console + BFF live/strict 整合通過閘門）
- 仍 fail-closed：broker production live、capital binding live、canary 需 risk-owner + operator 雙閘
- 進行中活動 track：A=Shioaji sandbox、B=Qlib admission、C=services namespace normalization、D=BFF 收尾

本 supplemental 對應的新工作以 **Track E** 形式接到當前 sprint `2026-05-13-ep5-qlib-bff-consolidation`：管理面 OODA layer + paper-loop 證明 + 多 persona 合成 + Qlib admission + Shioaji sandbox + evolution follow-through + fail-closed regression。

## EPIC × Task × Owner / Reviewer 對照表

下表是 ai-status.json 上 Track E 的 46 個 task 派工結果。每個 task 的 `phase` 欄位會記載 EPIC 編號。

### EPIC-01 OODA Packet Foundation (7 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-OODA-001 | OodaLoopPacket schema | Codex | Codex2 |
| MGMT-OODA-002 | OODA JSONL append store | Codex | Codex2 |
| MGMT-OODA-003 | OODA stage transition validation | Codex2 | Codex |
| MGMT-OODA-004 | BFF read routes for OODA packets | Claude | Codex |
| MGMT-OODA-005 | Control Room OODA status card | Claude2 | Claude |
| MGMT-OODA-006 | OODA packet drawer component | Claude2 | Claude |
| MGMT-OODA-007 | OODA packet unit / integration tests | Codex2 | Claude |

### EPIC-02 Management Paper Loop Proof (7 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-PAPER-001 | paper candidate StrategySpec | Copilot | Codex |
| MGMT-PAPER-002 | paper ApprovalDecision packet | Claude | Codex2 |
| MGMT-PAPER-003 | paper DeploymentPlan packet | Claude | Codex2 |
| MGMT-PAPER-004 | paper RuntimeBinding packet | Claude | Gemini |
| MGMT-PAPER-005 | paper telemetry packet | Codex | Codex2 |
| MGMT-PAPER-006 | paper EvolutionDecision review packet | Claude2 | Copilot |
| MGMT-PAPER-007 | complete paper OODA packet | Codex2 | Claude |

### EPIC-03 Multi-Persona Synthesis (7 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-SYN-001 | PersonaAllocationProposal schema | Codex | Codex2 |
| MGMT-SYN-002 | PersonaAllocationProposal store | Codex | Codex2 |
| MGMT-SYN-003 | allocation conflict classifier | Copilot | Claude |
| MGMT-SYN-004 | allocation synthesis method v1 | Claude | Copilot |
| MGMT-SYN-005 | AllocationPolicyArtifact output | Claude | Codex |
| MGMT-SYN-006 | Management UI conflict log view | Claude2 | Codex2 |
| MGMT-SYN-007 | multi-persona synthesis proof evidence | Codex2 | Claude |

### EPIC-04 Qlib Admission (6 tasks) — 與 Track B 共用 TW market dataset

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-QLIB-001 | Qlib dataset manifest | Copilot | Codex |
| MGMT-QLIB-002 | Qlib StrategySpec builder | Copilot | Codex2 |
| MGMT-QLIB-003 | Qlib LightGBM smoke | Gemini2 | Copilot |
| MGMT-QLIB-004 | Qlib model / eval artifact refs | Codex | Codex2 |
| MGMT-QLIB-005 | Qlib registry admission packet | Claude | Codex |
| MGMT-QLIB-006 | Management artifact / research linkage | Claude2 | Codex2 |

### EPIC-05 Shioaji Sandbox (6 tasks) — 與 Track A 共用 broker sandbox

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-BROKER-001 | Shioaji sandbox adapter facade | Gemini | Gemini2 |
| MGMT-BROKER-002 | Shioaji account readiness check | Gemini2 | Gemini |
| MGMT-BROKER-003 | Shioaji place / cancel / readback / reconcile smoke | Gemini | Gemini2 |
| MGMT-BROKER-004 | Shioaji evidence packet | Codex | Codex2 |
| MGMT-BROKER-005 | Shioaji fail-closed tests | Codex2 | Codex |
| MGMT-BROKER-006 | Shioaji canary readiness packet integration | Claude | Codex2 |

### EPIC-06 Evolution Follow-Through (7 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-EVO-001 | telemetry-to-evolution packet link | Codex | Codex2 |
| MGMT-EVO-002 | EvolutionDecision proposal from incident / postmortem | Copilot | Claude |
| MGMT-EVO-003 | evolution review / approval UI linkage | Claude2 | Codex2 |
| MGMT-EVO-004 | retrain / revalidate dispatch | Gemini | Copilot |
| MGMT-EVO-005 | rollback / freeze follow-through | Claude | Codex2 |
| MGMT-EVO-006 | evolution observation window report | Codex2 | Claude |
| MGMT-EVO-007 | evolution OODA loop closure | Claude | Codex2 |

### EPIC-07 Safety / Fail-Closed Regression (6 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| MGMT-SAFE-001 | live broker disabled smoke | Gemini2 | Codex |
| MGMT-SAFE-002 | capital binding disabled smoke | Gemini2 | Codex |
| MGMT-SAFE-003 | OpenClaw broker tool denial smoke | Codex2 | Copilot |
| MGMT-SAFE-004 | canary human gate smoke | Codex2 | Claude |
| MGMT-SAFE-005 | no live side effects assertion | Copilot | Codex2 |
| MGMT-SAFE-006 | command idempotency regression | Codex | Codex2 |

## Owner 工作量分佈

| Agent | Owns | 主要 lane 對應 |
|---|---:|---|
| Claude | 10 | execution + control-plane + governance-review |
| Codex2 | 10 | acceptance + schema |
| Codex | 9 | schema + integration |
| Claude2 | 6 | execution + control-plane (UI 為主) |
| Copilot | 6 | research-ingest + spec-review + critique |
| Gemini2 | 4 | runtime-packaging + ci-cd |
| Gemini | 3 | runtime-packaging + worker-ops |

## 依賴關係 (粗略)

- EPIC-02 依賴 EPIC-01 的 packet schema
- EPIC-04 / EPIC-05 可與 EPIC-01 並行（皆是 paper loop 的 observe/act 來源）
- EPIC-03 依賴 EPIC-01 schema，但 synthesis 模組本身可獨立
- EPIC-06 依賴 EPIC-01 + EPIC-02 完成
- EPIC-07 是橫向 regression，任何 EPIC 有 PR 都應觸發

## 接下來

1. 各 owner 從 ai-status.json 的 task board 認領自己的 task，按 `start → progress → done` lifecycle 推進
2. 高風險 task（broker activation / capital binding / canary）保持 fail-closed，evidence 走 `support/evidence/MGMT-OODA-M*.json`
3. M1 (OODA Packet Foundation) 是第一個收斂點；驗證後再開後續 milestone 的 e2e

## Track E 執行收尾盤點（2026-05-16 02:51 補記）

依 `ai-task-archive/tasks/MGMT-*.json` 統計，46 個 MGMT task 中 **45 個已歸檔完成**，唯一例外：

- `MGMT-BROKER-002` Shioaji account readiness check — **blocked**，等 broker credentials (API_KEY/SECRET_KEY)，下游 sidecar acceptance packet 已備妥（commit 22e5ca3b）

evidence 已落入 `support/evidence/MGMT-OODA-M*.json`、`support/evidence/MGMT-PAPER-*`、`support/evidence/MGMT-EVO-*`、`support/evidence/MGMT-QLIB-*`、`support/evidence/MGMT-BROKER-*`、`support/evidence/MGMT-SAFE-*`、`support/evidence/MGMT-SYN-*`。M1–M6 OODA packet 主要證據齊全；M7 canary readiness 的人工閘門證據因 BROKER-002 未解而未閉合。

→ Track E 已進入收斂尾段，下一輪 sprint 規劃應改採 `GAP_dev_team_master_rebaseline_2026-05-15.md` 提出的 EPIC-BFF-P0 / GOV-DEPLOY / RUNTIME / TELEMETRY / RESEARCH / EVOLUTION 為主線。

---

## 下一輪 Sprint：`2026-05-16-pantheon-bff-p0-foundation` 派工盤點

sprint 已切換（2026-05-16T00:00:00Z 起），objective 描述跨 6 個 EPIC 按 P0→P3 階梯推進。本次依 [GAP § 5 + § 7 去重後共 59 個 task 全部 assign 進 ai-status.json](GAP_dev_team_master_rebaseline_2026-05-15.md)。Track E 殘留 `MGMT-BROKER-002` 仍 blocked 等 Shioaji credentials，並列保留不在本 sprint 推進範圍。

**ID 衝突備註**：9 個 GAP 原始 ID 與 2026-04 Phase 1 已歸檔 task 撞名（GOV-001 / DEP-001 / DEP-002 / CAP-002 / EX-002 / TEL-001 / TEL-002 / INC-001 / LOOP-001），全部加 `-RB` (rebaseline) 後綴重派。

### EPIC-BFF-P0 (Sprint 1, 10 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| P0-BFF-001 | GET /bff/me session bootstrap | Claude | Codex |
| P0-BFF-002 | POST /bff/auth/refresh | Claude | Codex |
| P0-BFF-003 | POST /bff/logout | Claude2 | Codex2 |
| P0-BFF-004 | Fix /openapi.json 500 | Codex | Codex2 |
| P0-ACT-001 | canonical action endpoint /bff/actions/{type}/{id}/{action} | Claude | Codex2 |
| P0-APP-001 | approval decide endpoint /bff/approvals/{id}/decide | Claude | Codex2 |
| P0-REG-001 | /bff/strategies list/detail | Codex2 | Claude |
| P0-PER-001 | /bff/personas list/detail | Codex2 | Claude |
| P0-CAP-001 | /bff/capital-pools list/detail | Codex | Claude2 |
| P0-AUD-001 | /bff/audit read endpoint | Claude2 | Codex |

### EPIC-GOV-DEPLOY (Sprint 2, 5 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| GOV-001-RB | ApprovalDecision schema + write authority | Claude | Codex |
| DEP-001-RB | DeploymentPlan contract + service | Claude | Codex2 |
| DEP-002-RB | DeploymentPlan stage planner (paper/canary/live/frozen) | Claude2 | Codex |
| DEP-003 | deployment projection read model | Codex | Codex2 |
| CAP-002-RB | Pool/runtime compatibility checks | Claude | Codex2 |

### EPIC-RUNTIME (Sprint 3, 6 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| RT-001 | RuntimeBinding schema | Codex | Codex2 |
| RT-002 | Runtime Manager skeleton | Gemini | Claude |
| RT-003 | /bff/runtimes list/detail | Claude2 | Codex2 |
| RT-004 | Runtime deploy/pause/replace/rollback actions | Claude | Codex2 |
| EX-002-RB | Loader metadata migration promotion_state → artifact_state + deployment_stage | Codex2 | Codex |
| EX-003 | LEAN algorithm-level smoke test | Gemini2 | Gemini |

### EPIC-TELEMETRY (Sprint 4, 7 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| TEL-001-RB | TelemetryEvent canonical schema | Codex2 | Codex |
| TEL-002-RB | RuntimeHeartbeat ingest endpoint | Gemini | Codex |
| AUD-002 | AuditAction backend (write engine) | Codex2 | Claude |
| ALT-001 | /bff/alerts endpoint | Claude2 | Codex2 |
| INC-001-RB | /bff/incidents (IncidentCase) | Claude2 | Codex |
| REC-001 | Basic reconciliation record | Gemini | Codex |
| POST-001 | Postmortem schema + endpoint | Copilot | Codex2 |

### EPIC-RESEARCH (Sprint 5, 28 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| SRC-001 | SourceRecord schema + ingest API | Copilot | Codex |
| SRC-002 | paper ingest adapter skeleton | Copilot | Gemini2 |
| SRC-003 | repo allowlist ingest skeleton | Copilot | Gemini2 |
| SRC-004 | StrategySpecSeed builder | Copilot | Codex2 |
| SRC-005 | OpenClaw cron / ingest job trigger | Gemini2 | Copilot |
| STRAT-001 | StrategySpec schema / model | Codex2 | Codex |
| STRAT-002 | StrategySpec registry endpoints | Codex | Codex2 |
| STRAT-003 | Source → StrategySpec conversion service | Copilot | Codex |
| STRAT-004 | evidence / code refs lineage | Codex2 | Copilot |
| EXP-001 | ExperimentTask / ExperimentRun schema | Codex2 | Codex |
| EXP-002 | /bff/research-experiments list/detail | Claude2 | Codex2 |
| EXP-005 | ExperimentRun → Artifact registry writeback | Codex | Copilot |
| QLIB-001 | Qlib adapter skeleton | Gemini | Copilot |
| VBT-001 | vectorbt rapid eval adapter | Gemini2 | Copilot |
| PER-002 | skills/tools/capabilities read API | Claude2 | Codex |
| TRN-001 | TeachingSession / TeachingEvent schema | Codex2 | Codex |
| TRN-002 | trainer session endpoints | Claude | Codex2 |
| TRN-003 | rapid-eval request / response | Claude2 | Copilot |
| TRN-004 | trainer commit / discard / replay | Claude | Codex2 |
| IMT-001 | TraderTrajectory schema | Codex | Copilot |
| IMT-002 | PreferenceExample / CorrectionTrace schema | Codex2 | Copilot |
| IMT-003 | imitation dataset builder skeleton | Copilot | Codex2 |
| IMT-004 | behavior policy artifact type registration | Codex | Codex2 |
| ASK-001 | /bff/agora/ask/sessions | Claude2 | Codex2 |
| ASK-002 | ConsultRequest / ConsultMemo schema | Codex | Codex2 |
| ASK-003 | ask / committee session lifecycle | Claude2 | Codex |
| ASK-004 | memo publish to registry / review | Claude | Codex2 |
| ASK-005 | approval / ask SSE event publishing | Codex | Codex2 |

### EPIC-EVOLUTION (Sprint 6, 3 tasks)

| Task ID | Title | Owner | Reviewer |
|---|---|---|---|
| EVO-001 | EvolutionDecision service | Claude | Codex |
| LOOP-001-RB | /bff/v5/loop-runs endpoint | Claude2 | Codex2 |
| SENT-001 | /bff/v5/sentinel/findings endpoint | Claude2 | Codex |

### Sprint 1 Owner 工作量分佈

| Agent | Owns |
|---|---:|
| Claude2 | 14 |
| Claude | 11 |
| Codex2 | 11 |
| Codex | 10 |
| Copilot | 7 |
| Gemini2 | 3 |
| Gemini | 3 |

### 依賴與滾動規則

- **EPIC-BFF-P0 必須先收斂**（GAP § 10 最大阻塞），完成後 P1 才有意義
- **EPIC-GOV-DEPLOY 與 EPIC-RUNTIME 接續**：ApprovalDecision → DeploymentPlan → RuntimeBinding → loader migration 是一條鏈
- **EPIC-TELEMETRY 可與 RUNTIME 並行**：但 TEL/AUD 等 backend 須等 runtime 有事件來源
- **EPIC-RESEARCH 與 EPIC-EVOLUTION 是 P3**：可在 P0/P1 過閘後再開動，不阻塞 governance loop
- **fail-closed 鐵律**：broker production live、capital binding live 仍禁；canary 需 risk-owner + operator 雙閘；evidence 走 `support/evidence/<task-id>/`

