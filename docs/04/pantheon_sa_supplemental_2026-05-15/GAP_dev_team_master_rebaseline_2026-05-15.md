# Pantheon 開發團隊 GAP 開發文件 (master 基準重盤)

> 文件版本：v2.0 — master 基準重盤底稿
> 日期：2026-05-15
> 基準 repo：`ajoe734/pantheon@master`、`ajoe734/execute-plans@main`
> 文件層級：L2/L3 — 不覆蓋 L1 canonical (TARGET_ARCHITECTURE.md 等)
> 衝突規則：若與 L1 衝突，以 L1 為準
> 配對文件：
>   - `SA_management_console_multi_persona_ooda.md` — Management OODA 補充 SA（同日）
>   - `SD_management_console_multi_persona_ooda.md` — Management OODA 補充 SD（同日）

本文件比上述兩份 supplemental SA/SD **覆蓋面更廣**：SA/SD 只看 Management Console + OODA layer；本 GAP 文件對 9 條完整循環（research → governance → execution → telemetry → evolution）逐一盤點，並以 P0/P1/P2/P3 路線與 6 個 Sprint 候選整理開發排程。

---

## 0. 文件摘要

本文件根據最新指定基準重新盤點：

- 後端：`pantheon@master`
- 前端 / Management Console：`execute-plans@main`

重新校準後結論如下：

1. **Management Console / Agora Workbench 的前端、spec、state machine、mock / fallback、BFF boundary 已高度成熟。**
2. **`pantheon@master` 的 Registry / Promotion 主幹不是 blueprint，已具有 implemented service semantics。**
3. **Artifact Loader 不是純 contract，已是 `IN PROGRESS`，具備 service-local loader、Object Store adapter / materialization helper、smoke path。**
4. **尚未完成的核心 GAP 是 live BFF backend routes、ApprovalDecision、DeploymentPlan、RuntimeBinding、Runtime Manager、metadata migration、Telemetry / Evolution backend、Research / Source Ingest / Imitation backend。**
5. **目前 Pantheon 已是 OODA-ready architecture，但尚未持續完整 production OODA closed-loop system。**

本文件不再對 `pantheon` 原本寫成 `backend-dev-publish-20260429`，而把後端判斷改以 `master` 為基準。

---

## 1. Repo 事實基準

### 1.1 `execute-plans@main`

`execute-plans` 是目前 Pantheon 的前端主線。它不是舊 `front-ai-trading-system`，而是新的 Lovable app，定位為集團內部操作介面：

1. **Management Console**
2. **Agora Workbench**

目前 README 已定義：

- 支援 mock mode
- 支援 Pantheon BFF live mode
- `VITE_BFF_FALLBACK=auto`：dev / hybrid fallback
- `VITE_BFF_FALLBACK=strict`：real integration，不允許隱性 fallback
- `VITE_BFF_REAL_WRITES=false`：預設禁止真寫入，直到 operator auth、approval evidence、confirm-token、two-man signing 驗證後才開放

前端結構已經很成熟：

```text
src/platform/      共用 Shell / TopBar / SideNav / HighRiskConfirm / AuditTimeline / LifecycleStepper / PermissionAwareButton
src/management/    Strategy / Persona / Capital Pool / Rebalance / Approvals / Runtimes / Risk / Incidents / Capabilities
src/agora/         DailyBrief / Signals / Notebook / AskPersonas / Committee / DecisionJournal / AlertTriage / InsightInbox / Trainer / MemoryReview / SkillCoaching
src/lib/bff-v1/    Pantheon BFF v1 live/mock transport
src/lib/bff/       legacy mock seed / mutations / realtime / v5 UI facade
src/lib/stateMachines/ 18 個實體的狀態機
src/lib/permissions.ts RBAC 權限矩陣 + filterActions
src/lib/handoff.ts Agora -> Management handoff store
src/i18n/          zh-TW / en-US 字典
src/mocks/seed.ts  mock 資料
```

#### 設計原則已落地於前端

`execute-plans` 已明確定義：

1. **BFF boundary only**：頁面不可直接 `fetch()` 真 API，所有讀資料、寫入、事件都走 BFF facade。
2. **狀態機驅動 UI**：所有可執行動作由 `nextTransitions(machine, state)` 產生，再經 `filterActions(role, …)` 過濾。
3. **高風險動作三重閘門**：`PermissionAwareButton`、`HighRiskConfirm`、`auditEvents`。
4. **Agora 完全隔離**：Agora 永遠不直接 deploy / rollback / 動資金，只能透過 handoff 推到 Management 工作佇列。
5. **i18n 強制對齊**：兩語系 key 數量必須相等。

#### FE / spec 現況

Current spec snapshot 已經設為可一次更新到多 SoT：

```text
v5 IA / closed-loop OS
  > v4 + Pack D normative core
  > 2026-05-07 Final BFF Contract
  > v3 legacy shim
  > v2 / v1 historical
```

FE audit 顯示：

- 366 / 366 tests green
- FE coverage 約 98%
- B 組 spec backport 已完成
- C 組 FE tail 已完成
- D 組 optional enhancement 已完成
- G 組 spec-conflict 已完成
- H 組 version backlog 已 FE closed
- A 組 Backend P0 endpoints 仍 pending

### 1.2 `execute-plans@main` 的 BFF live probe 現況

目前 live BFF probe 顯示：43 個 canonical endpoint 中約 4 個可確認：

- `GET /bff/events/stream` -> 200
- `GET /bff/approvals` -> 401（route exists + auth gate active）
- `POST /bff/mcp-servers/{id}/import-tools` -> 401
- `GET /bff/v5/interventions` -> 401

主要 pending / 404 類型：

```text
Session trio:
  GET  /bff/me
  POST /bff/auth/refresh
  POST /bff/logout

Canonical action:
  POST /bff/actions/{entityType}/{entityId}/{actionId}

Decision endpoints:
  POST /bff/approvals/{id}/decide
  POST /bff/v5/interventions/{id}/decide

Entity registries:
  strategies / personas / capital-pools / rebalances / deployments / jobs
  alerts / incidents / audit / artifacts / runtimes / mcp-tools / skills
  channels / tools / ranking-formulas / research-experiments

Agora routes:
  /bff/agora/signals
  /bff/agora/inbox
  /bff/agora/journal
  /bff/agora/postmortems
  /bff/agora/ask/sessions

v5 routes:
  /bff/v5/loop-runs
  /bff/v5/sentinel/findings
  /bff/v5/execution/persona-health
```

結論：

> FE/spec/mock/state-machine 已高度成熟，但 live backend routes 尚未完成。因此 Management 是 production-shaped，但不是 production-closed-loop。

### 1.3 `pantheon@master`

`pantheon@master` 是本文件的後端基準。

Root README 是 legacy compatibility note，並明確指出 canonical REG-002 files 位於：

```text
services/registry/promotion/README.md
services/registry/promotion/gate.py
services/registry/promotion/cli.py
```

Root wrappers 仍保留：

```text
gate.py
cli.py
```

### 1.4 Registry / Promotion 現況

`services/registry/promotion/README.md` 已明確說：

> The split artifact_state / deployment_stage service is now implemented.

並列出目前實作路徑：

```text
models.py       Pydantic models: ArtifactState, DeploymentStage, RegistryEntry, DeploymentSummary
storage.py      In-memory store with deployment-view projection and strategy indexing
split_api.py    Core operations: register, get, list_by_strategy, advance_artifact_state, resolve_latest_approved, resolve_deployment_view
service.py      FastAPI service exposing the split API
test_service.py Smoke tests covering split model, transitions, deployment view, FastAPI endpoints
```

Canonical semantics：

```text
Registry artifact_state:
  draft -> candidate
  candidate -> approved
  draft|candidate|approved -> retired

Deployment/runtime deployment_stage:
  none / paper / canary / live / frozen
```

Canonical flow：

```text
1. registry admits artifact as candidate
2. governance advances artifact to approved
3. DeploymentPlan chooses paper / canary / live / frozen
4. RuntimeBinding records what is actually running
```

結論：Registry / Promotion 不再是 contract-only，而是 implemented service semantics + legacy compatibility path。

### 1.5 Registry Contract 現況

`services/registry/contract.md` 定義 registry 是所有可能影響 execution 的 artifact 的 governed source of truth。目的包含：strategy evolution versioned / lineage traceable / artifact governance maturity explicit / rollback defaults explicit / LEAN execution never loads non-approved artifacts。

Artifact types 包含：`strategy_spec / model_artifact / feature_set / prompt_bundle / signal_snapshot / execution_bundle / evaluation_result / critique_result / optimizer_result`。

核心規則：`artifact_state` = registry lifecycle；`deployment_stage` = separate deployment/runtime concern。

Execution projection 讓 loader 能檢查：

```text
runtime loading requires artifact_state=approved
paper mode requires deployment_stage=paper
canary mode requires deployment_stage=canary
live mode requires deployment_stage=live
candidate / retired / none / frozen rejected for new execution loads
```

### 1.6 Artifact Loader 現況

`services/execution/artifact-loader/contract.md` 顯示狀態為：

```text
IN PROGRESS — contract locked;
service-local loader, Object Store adapter/materialization helper, and smoke path now exist;
algorithm-level LEAN run coverage is still deferred
```

仍有 compatibility gap：

```text
canonical registry state = artifact_state
canonical deployment placement = deployment_stage
current loader contract still consumes legacy execution envelope with promotion_state
metadata migration is not complete
```

結論：Artifact Loader 應視為 partial implemented / in-progress，而非 blueprint。

---

## 2. 成熟度定義

| 成熟度 | 定義 |
|---|---|
| implemented | repo 內已有可執行碼、模組、測試、狀態機映射到實作 |
| partial implemented | 有 service-local / smoke / adapter / tests，但尚未完成 production closed-loop |
| contract | 語義、接口、文件、狀態、契約明確，但尚未證明 runtime closed-loop |
| spike | demo / mock / preview / local smoke path 已有，但不是 production path |
| blueprint | 主要仍在書面架構規劃，無實作位置 |
| pending | 尚未鎖到可開發路徑或 BFF route 仍 404 |

---

## 3. Management 系統 GAP 總結

Management 系統的完整目標 flow：

```text
投資研究 -> AI persona 協作 -> 治理決策 -> 部署執行
       -> telemetry 反饋 -> evolution 修正 -> 反向到研究 / 人格 / 治理 / 部署
```

### 3.1 Flow 完成度總表

| Flow | FE/spec/mock | Backend / live | 整體判斷 |
|---|---|---|---|
| 投資研究 | Agora / Research shell 已存在 | research routes 多數 404 | FE ready / backend pending |
| AI persona 協作 | AskPersonas / Committee / Trainer / SkillCoaching 已存在 | personas / ask / skills routes 多數 404 | FE/spec ready / backend pending |
| 治理決策 | state machine / approval / high-risk gate 成熟 | action / decide endpoints pending | FE/spec mature + backend partial |
| 部署執行 | Capital Pool / Runtimes / Risk UI 有 | runtimes / deployments / capital-pools 404 | FE ready / backend pending |
| telemetry 反饋 | Alerts / Incidents / Audit / realtime UI 有 | alerts / incidents / audit 404 | FE ready / telemetry backend pending |
| evolution 修正 | v5 loop/sentinel/evolution spec 有 | loop/sentinel/persona-health 404 | spec ready / backend pending |

### 3.2 Management 結論

```text
FE/spec/mock OODA: 成熟
BFF live routes: 不完整
Backend closed-loop: 未完成
```

→ Management 可以被視為「production-shaped FE / spec / control surface」，但不能視為「production-closed-loop management system」。

---

## 4. Pantheon OODA 能力 GAP 總結

Pantheon 的投資管理 OODA：`Observe -> Orient -> Decide -> Act -> Observe ...`

### 4.1 OODA 現況

| OODA | 目標能力 | 目前現況 | GAP |
|---|---|---|---|
| Observe | market / runtime / research / persona / action events | FE/spec 有；telemetry backend 未完整 | telemetry ingest、audit、heartbeat、events store |
| Orient | registry、lineage、evidence、consult、drift、postmortem | registry/governance 強；research/drift 弱 | source ingest、experiment lineage、drift engine |
| Decide | approval、promotion、rollback、freeze、mutate、retire | promotion core 強；decision endpoints 未完整 | ApprovalDecision、action endpoint、decide endpoints |
| Act | deploy、pause、rollback、rebind、liquidate、retrain | loader partial；runtime manager 未實現 | DeploymentPlan、RuntimeBinding、Runtime Manager、LEAN run |

### 4.2 OODA 結論

Pantheon 目前是：

```text
OODA-ready architecture
+ governance loop partial implemented
+ management FE mature
- backend closed-loop incomplete
```

尚不能說已具備完整的自主投資管理自我演化能力。

---

## 5. 9 個完整循環 GAP 盤點

### 5.1 循環 1：研究素材接路

```text
OpenClaw cron -> OpenAlex / GitHub allowlist -> Source Registry -> normalize -> StrategySpec seed
```

成熟度：**blueprint / backend pending**

已有：Agora Workbench 有 DailyBrief、Signals、Notebook、InsightInbox 等研究操作介面；FE / spec 已保留研究與知識入口。

缺口：`source-ingest-service`、paper / repo / internal ingest workers、source registry backend、normalize -> StrategySpec seed、evidence / trust / dedupe logic、OpenClaw cron -> ingest pipeline 實際路徑。

開發目標：建立初步 source ingest pipeline，使外部 paper、repo、內部 memo 能形成 `SourceRecord` 與 `StrategySpecSeed`。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| SRC-001 | 建立 SourceRecord schema + API | P2 |
| SRC-002 | 建立 paper ingest adapter skeleton | P2 |
| SRC-003 | 建立 repo allowlist ingest skeleton | P2 |
| SRC-004 | 建立 StrategySpecSeed builder | P2 |
| SRC-005 | 接 OpenClaw cron / job trigger | P3 |

驗收條件：`POST /bff/sources/ingest/internal` 可建立內部 source；Source 可被 normalize 為 `StrategySpecSeed`；Source 與 seed 有 lineage；不允許 persona 自由抓外部任意 URL。

### 5.2 循環 2：策略蒸餾接路

```text
discovered material -> scaffolded spec/data/template/baseline
```

成熟度：**contract**

已有：Registry contract 支援 `strategy_spec` artifact type；Registry 是 governed source of truth；Artifact lifecycle 已有 canonical `artifact_state`。

缺口：Source material -> StrategySpec 的後端 service、distillation service、evidence / code refs extraction、`discovered -> scaffolded` state transition。

開發目標：將 source material 轉換為可研究的 `StrategySpec`，並存入 registry。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| STRAT-001 | 建立 StrategySpec schema / model | P1 |
| STRAT-002 | 建立 StrategySpec registry endpoints | P1 |
| STRAT-003 | 建立 Source -> StrategySpec conversion service | P2 |
| STRAT-004 | 建立 evidence / code refs link | P2 |

驗收條件：可由 source seed 產生 `strategy_spec` artifact；`strategy_spec` 有 lineage、storage_ref、checksum；`strategy_spec` 可進入 `draft -> candidate`。

### 5.3 循環 3：Alpha 複製 / 研究接路

```text
scaffolded -> backend selection -> experiment_run -> replicated
```

成熟度：**blueprint / contract**

已有：Registry 支援 `producer_run_id`、`evaluation_summary`、`lineage`；Artifact types 支援 `model_artifact`、`feature_set`、`signal_snapshot`、`optimizer_result`；FE 有 Research / Signals / Notebook 類工作台。

缺口：experiment orchestrator、`/bff/research-experiments`、Qlib / vectorbt / statsmodels / QuantLib / RL adapters、experiment_run 寫回 registry、replicated gate。

開發目標：建立最小 research backend closed-loop：`StrategySpec -> ExperimentRun -> CandidateArtifact`。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| EXP-001 | 建立 ExperimentTask / ExperimentRun schema | P2 |
| EXP-002 | 建立 `/bff/research-experiments` list/detail | P2 |
| EXP-003 | 建立 vectorbt rapid prototype adapter | P2 |
| EXP-004 | 建立 Qlib adapter skeleton | P3 |
| EXP-005 | ExperimentRun -> Artifact registry writeback | P2 |

驗收條件：一個 `strategy_spec` 可提交 experiment task；任務完成後產生 `experiment_run`；run 可產生 `candidate artifact`；artifact lineage 指向 strategy spec 與 run。

### 5.4 循環 4：Persona 行為接路

```text
Researcher -> Trainer UI -> teaching trace -> persona patch -> rapid eval -> persona registry
```

成熟度：**FE implemented / backend pending**

已有：Agora Workbench 有 Trainer、MemoryReview、SkillCoaching；FE 有 state machine、RBAC、audit/realtime pattern。

缺口：`/bff/personas`、`/bff/personas/{id}`、`/bff/tools`、`/bff/skills`、teaching session backend、teaching event store、rapid eval service、persona patch persistence。

開發目標：將 Trainer 從 UI/mock flow 變成真實 persona teaching pipeline。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| PER-001 | 實作 `/bff/personas` list/detail | P0 |
| PER-002 | 實作 skills/tools/capabilities read API | P0 |
| TRN-001 | 建立 TeachingSession / TeachingEvent schema | P1 |
| TRN-002 | 實作 trainer session endpoints | P1 |
| TRN-003 | 實作 rapid-eval request / response | P2 |
| TRN-004 | 實作 commit/discard/replay | P2 |

驗收條件：Trainer 可連 live BFF 讀 persona；teaching message / patch 有寫入後端；patch 可產生 preview result；commit 後 persona policy / route policy 有可追 lineage 的變更紀錄。

### 5.5 循環 5：Human Trader 模仿接路

```text
trainer traces / trader trajectories -> BC -> DAgger/GAIL/AIRL -> candidate behavior policy
```

成熟度：**blueprint / data-source prepared**

已有：Trainer、SkillCoaching、DecisionJournal、MemoryReview 可作為資料來源位置。

缺口：trader trajectory schema、imitation dataset builder、behavior policy artifact、imitation evaluation contract、imitation / TRL training path。

開發目標：先建立資料合約，下期才訓練模型。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| IMT-001 | 定義 TraderTrajectory schema | P3 |
| IMT-002 | 定義 PreferenceExample / CorrectionTrace | P3 |
| IMT-003 | 建立 dataset builder skeleton | P3 |
| IMT-004 | 註冊 behavior policy artifact type | P3 |

驗收條件：teaching traces 可轉成 dataset；dataset 有 storage_ref、schema_version、lineage；不直接影響 live execution。

### 5.6 循環 6：Consultation / Committee 接路

```text
persona -> consult.request -> committee/red-team -> memo -> review gate
```

成熟度：**FE/spec implemented / backend pending**

已有：AskPersonas、Committee、DecisionJournal、Agora -> Management handoff、approval / ask SSE spec、EvidenceKind capability map。

缺口：`/bff/agora/ask/sessions`、consult request backend、committee orchestration service、consult memo store、memo -> review gate writeback。

開發目標：將 Agora 的協作介面接成真實 consult / memo / handoff pipeline。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| ASK-001 | 實作 `/bff/agora/ask/sessions` | P1 |
| ASK-002 | 建立 ConsultRequest / ConsultMemo schema | P1 |
| ASK-003 | 實作 ask / committee session lifecycle | P2 |
| ASK-004 | 實作 memo publish to registry/review | P2 |
| ASK-005 | 實作 approval / ask SSE event publishing | P2 |

驗收條件：Agora 可建立 ask session；Committee memo 可保存；Memo 可 handoff 到 Management review queue；Agora 仍不能直接 deploy / rollback / 動資金。

### 5.7 循環 7：Promotion / Deployment 接路

```text
replicated -> validators -> review gates -> approved -> deployment plan -> paper/canary/live
```

成熟度：**registry / promotion implemented; deployment runtime pending**

已有：`services/registry/promotion/` canonical service path；split `artifact_state / deployment_stage` service implemented；`draft -> candidate -> approved -> retired` registry lifecycle；legacy promotion gate compatibility；registry contract。

缺口：ApprovalDecision first-class backend (`GOV-001`)、DeploymentPlan (`DEP-001`)、stage planner、RuntimeBinding、action / decide endpoints、canonical metadata migration。

開發目標：將 registry approval 接到 DeploymentPlan 與 RuntimeBinding，形成可執行部署鏈。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| GOV-001 | 建立 ApprovalDecision schema + write authority | P0 |
| GOV-002 | 實作 `/bff/approvals/{id}/decide` | P0 |
| GOV-003 | 實作 `/bff/actions/{entityType}/{id}/{actionId}` | P0 |
| DEP-001 | 建立 DeploymentPlan contract + service | P0 |
| DEP-002 | 建立 paper/canary/live/frozen stage planner | P1 |
| DEP-003 | 實作 deployment projection read model | P1 |

驗收條件：`artifact_state=approved` 可建立 DeploymentPlan；`DeploymentPlan` 可指定 `paper / canary / live / frozen`；ApprovalDecision 是 approved 的 canonical authority；UI action endpoint 能驅動 approval / deployment state machine。

### 5.8 循環 8：Capital Pool Execution 接路

```text
approved artifact -> pool binding -> LEAN runtime -> broker/subaccounts -> fills/positions
```

成熟度：**FE management implemented + artifact loader partial implemented + runtime pending**

已有：Management Console 已有 Capital Pool / Rebalance / Approvals / Runtimes / Risk；Artifact Loader contract locked；service-local loader / Object Store adapter / materialization helper / smoke path exists。

缺口：`/bff/capital-pools`、`/bff/deployments`、`/bff/runtimes`、Runtime Manager、RuntimeBinding store、LEAN algorithm-level smoke coverage、actual broker/subaccount loop、metadata migration from `promotion_state` to `artifact_state + deployment_stage`。

開發目標：讓 approved artifact 可透過 DeploymentPlan 綁定到 runtime，並由 loader 安全載入。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| CAP-001 | 實作 `/bff/capital-pools` list/detail | P0 |
| RT-001 | 建立 RuntimeBinding schema | P0 |
| RT-002 | 建立 Runtime Manager skeleton | P1 |
| RT-003 | 實作 `/bff/runtimes` list/detail | P1 |
| RT-004 | 實作 deploy / pause / replace / rollback actions | P1 |
| EX-002 | Loader metadata migration：promotion_state -> artifact_state + deployment_stage | P1 |
| EX-003 | LEAN algorithm-level smoke test | P2 |

驗收條件：Runtime loader 只接受 `artifact_state=approved`；Runtime mode 與 `deployment_stage` 必須一致；`candidate / retired / none / frozen` 不得新載入 execution；RuntimeBinding 可揭露當前 runtime 實際載入 artifact。

### 5.9 循環 9：Telemetry / Postmortem / Evolution 接路

```text
telemetry / trader edits / research ingest -> postmortem -> discovered / replicated / live / retired
```

成熟度：**FE/spec implemented / backend pending**

已有：Alerts / Incidents / AuditTimeline / JobProgressDrawer / realtime / auditEvents；v5 loop / sentinel / current spec。

缺口：`/bff/alerts`、`/bff/incidents`、`/bff/audit`、`/bff/agora/postmortems`、`/bff/v5/loop-runs`、`/bff/v5/sentinel/findings`、`/bff/v5/execution/persona-health`、telemetry event store、reconciliation / drift backend、incident case manager、postmortem builder、evolution decision engine。

開發目標：先建立 telemetry / incident / audit 的最小閉環，再裝 evolution。

建議任務：

| ID | 任務 | 優先級 |
|---|---|---|
| TEL-001 | 建立 canonical telemetry event schema | P2 |
| TEL-002 | 實作 `/bff/audit` | P1 |
| TEL-003 | 實作 `/bff/alerts` | P1 |
| TEL-004 | 實作 `/bff/incidents` | P1 |
| TEL-005 | Runtime heartbeat ingest | P2 |
| POST-001 | Postmortem schema + endpoint | P2 |
| EVO-001 | EvolutionDecision schema | P3 |
| EVO-002 | LoopRun / Sentinel endpoints | P3 |

驗收條件：Management 可看到 live audit / alerts / incidents；每個 deploy / approve / rollback / runtime action 都能看到 audit event；Incident 能連到 artifact、runtime、pool、deployment plan。

---

## 6. 三個大循環 GAP

### 6.1 大循環 A：Learning / Discovery Loop

涵蓋：循環 1、2、3、4、5。

現況：FE/spec/data-source surface ready；backend research/learning factory pending。

最需要缺口：source ingest、StrategySpec seed / scaffold、experiment orchestrator、research backend adapters、trainer backend、imitation dataset builder。

目標：在 P2/P3 建立至少一條最小研究閉環：`source/internal note -> StrategySpec -> experiment run -> candidate artifact -> registry`。

### 6.2 大循環 B：Governance Loop

涵蓋：循環 6、7、9 治理部分。

現況：目前最成熟；FE/spec mature；registry/promotion implemented；backend action/decision/deployment still pending。

最需要缺口：action endpoint、approval decide endpoint、ApprovalDecision、DeploymentPlan、RuntimeBinding、metadata migration。

目標：在 P0/P1 建立第一條真實治理閉環：`approved artifact -> deployment plan -> runtime binding -> audit event`。

### 6.3 大循環 C：Capital / Execution Loop

涵蓋：循環 7、8、9 runtime 部分。

現況：Management surface mature；artifact loader partial implemented；runtime manager / live loop pending。

最需要缺口：capital pool endpoints、runtime endpoints、deployment endpoint、runtime manager、LEAN smoke run、telemetry / runtime heartbeat。

目標：在 P1/P2 建立 paper runtime minimal loop：`DeploymentPlan(paper) -> RuntimeBinding -> Loader -> Paper Runtime Smoke -> Telemetry/Audit`。

---

## 7. P0 / P1 / P2 / P3 開發路線

### 7.1 P0：讓 Management 能真實接 BFF，而不只是 mock

| ID | 任務 | Repo | 驗收 |
|---|---|---|---|
| P0-BFF-001 | 實作 `GET /bff/me` | pantheon / BFF | FE 可 bootstrap session / roles / tenant / capabilities |
| P0-BFF-002 | 實作 `POST /bff/auth/refresh` | pantheon / BFF | session refresh 不再 fallback mock |
| P0-BFF-003 | 實作 `POST /bff/logout` | pantheon / BFF | logout 可清 live session |
| P0-BFF-004 | 修復 `/openapi.json` 500 | pantheon / BFF | OpenAPI 可供 FE / QA discover |
| P0-ACT-001 | 實作 canonical action endpoint | pantheon / BFF | `POST /bff/actions/{entityType}/{entityId}/{actionId}` 不再 404 |
| P0-APP-001 | 實作 approval decide endpoint | pantheon / BFF | approval list -> decide flow 可用 |
| P0-REG-001 | 實作 strategies list/detail | pantheon | Management Strategy 可 live read |
| P0-PER-001 | 實作 personas list/detail | pantheon | Management / Agora persona 可 live read |
| P0-CAP-001 | 實作 capital-pools list/detail | pantheon | Management Capital Pool 可 live read |
| P0-AUD-001 | 實作 audit read endpoint | pantheon | AuditTimeline 有 live data |

P0 完成標準：live probe 43 endpoints 中至少核心 12 個不再 404；`execute-plans` 可以在 `VITE_BFF_FALLBACK=strict` 下啟動核心 Management flow；FE 不再需要對 session / actions / approval decide fallback mock。

### 7.2 P1：接通 Governance -> Deployment -> Runtime 最小鏈

| ID | 任務 | Repo | 驗收 |
|---|---|---|---|
| GOV-001 | ApprovalDecision schema + service | pantheon | approved state 有 first-class approval authority |
| DEP-001 | DeploymentPlan schema + service | pantheon | approved artifact 可建立 paper/canary/live/frozen plan |
| DEP-002 | Stage planner | pantheon | stage-specific rules 可檢查 |
| RT-001 | RuntimeBinding schema | pantheon | 可記錄 artifact 實際跑在哪個 runtime |
| RT-002 | Runtime Manager skeleton | pantheon | runtime inventory / bind / status API 可用 |
| EX-002 | metadata migration | pantheon | loader 使用 `artifact_state + deployment_stage` canonical metadata |
| CAP-002 | Pool compatibility checks | pantheon | DeploymentPlan approval 前可檢查 pool/runtime compatibility |

P1 完成標準：`approved artifact -> DeploymentPlan(paper) -> RuntimeBinding` 可完整走通；Loader 不再依賴 legacy `promotion_state` 作為新入約的真相；Management 可以看到 deployment plan、runtime binding、approval decision。

### 7.3 P2：建立 telemetry / incident / audit 最小閉環

| ID | 任務 | Repo | 驗收 |
|---|---|---|---|
| TEL-001 | TelemetryEvent schema | pantheon | 所有 runtime/action/event 可 canonical ingest |
| TEL-002 | RuntimeHeartbeat endpoint | pantheon | runtime health 可查 |
| AUD-001 | AuditAction backend | pantheon | 所有高風險 action 寫 audit |
| ALT-001 | Alerts endpoint | pantheon | Management Alerts live data |
| INC-001 | IncidentCase endpoint | pantheon | Management Incidents live data |
| REC-001 | Basic reconciliation record | pantheon | deploy/runtime/action 可對帳 |
| POST-001 | Postmortem endpoint | pantheon | incident 可產生 postmortem record |

P2 完成標準：deploy / approve / runtime action 有 audit trail；runtime health 可進 Management Health/Runtimes；incident / alert 不再只是 FE mock。

### 7.4 P3：補 Research / Learning / Evolution 後端

| ID | 任務 | Repo | 驗收 |
|---|---|---|---|
| SRC-001 | source ingest service | pantheon | source 可產生 StrategySpecSeed |
| EXP-001 | experiment orchestrator | pantheon | StrategySpec 可跑 experiment |
| QLIB-001 | Qlib adapter skeleton | pantheon | formal research path 可接 Qlib |
| VBT-001 | vectorbt rapid eval adapter | pantheon | Trainer preview 可接 rapid eval |
| IMT-001 | imitation dataset schema | pantheon | teaching / trader traces 可 build dataset |
| EVO-001 | EvolutionDecision service | pantheon | postmortem 可產生 evolution decision |
| LOOP-001 | `/bff/v5/loop-runs` | pantheon | v5 OODA loop UI 有 live backend |
| SENT-001 | `/bff/v5/sentinel/findings` | pantheon | Sentinel UI 有 live backend |

P3 完成標準：至少一條 research loop 可從 StrategySpec 走到 CandidateArtifact；Trainer preview 從 demo 能正式 rapid eval；Evolution decision 可由 incident/postmortem 觸發。

---

## 8. Repo 分工

### 8.1 `execute-plans@main`

繼續責任：Management Console、Agora Workbench、state machine UI、high-risk modal、audit/realtime display、BFF live/mock/strict transport、FE contract tests、integration gate。

不應責任：真實 business logic、registry source of truth、approval write authority、runtime state truth、telemetry truth。

近期重點：等待 backend P0 routes 後，做 strict integration 驗證；擴充 live BFF contract tests；把 mock overlay 行為逐步替換為 live backend。

### 8.2 `pantheon@master`

繼續責任：registry-core、promotion gate、artifact governance、approval decision、deployment plan、runtime binding、artifact loader、BFF backend、telemetry / incident / evolution backend。

近期重點：從 registry implemented 往 DeploymentPlan / RuntimeBinding 推進；完成 BFF P0 endpoints；完成 metadata migration；建立 runtime manager skeleton。

### 8.3 `lean-platform`

未來接入角色：LEAN paper/canary/live runtime substrate、runtime smoke test、broker/subaccount execution、orders/fills/positions telemetry source。

近期暫不要求：在 P0/P1 不需要做 full live broker，僅要求 paper runtime smoke + loader projection。

---

## 9. 最小可交付閉環

### 9.1 閉環 A：Management Live Read / Action Loop

```text
GET /bff/me
  -> GET /bff/personas / strategies / capital-pools
  -> POST /bff/actions/{entity}/{id}/{action}
  -> audit event
  -> FE state update
```

驗收：strict BFF mode 下 fallback mock；action 有 audit；RBAC / permission / high-risk confirm 不被繞過。

### 9.2 閉環 B：Artifact Governance Loop

```text
RegistryEntry(candidate)
  -> ApprovalDecision
  -> artifact_state=approved
  -> DeploymentPlan(paper)
```

驗收：`approved` 不是用 legacy paper/live 卡 registry lifecycle；ApprovalDecision 是 first-class；DeploymentPlan 可接。

### 9.3 閉環 C：Paper Runtime Binding Loop

```text
DeploymentPlan(paper)
  -> RuntimeBinding
  -> Artifact Loader
  -> paper runtime smoke
  -> audit / heartbeat
```

驗收：loader 使用 canonical `artifact_state + deployment_stage`；runtime binding 可查；heartbeat / audit 產生。

### 9.4 閉環 D：Incident / Evolution Seed Loop

```text
runtime event
  -> alert
  -> incident
  -> postmortem
  -> evolution decision proposal
```

驗收：incident 可連到 runtime / artifact / pool；postmortem 可產生 corrective action；evolution decision 不直接改 live，須走 governance。

---

## 10. 風險與阻塞

1. **最大阻塞：BFF live endpoints 不足。**FE/spec 已成熟，但 backend route 仍多數 404，導致 Management 無法從 mock 進 strict live integration。
2. **Registry canonical 與 loader legacy 不一致。**Registry canonical 是 `artifact_state + deployment_stage`，但 loader 仍寫 legacy `promotion_state`。這是最需要的 execution-side 技術債。
3. **ApprovalDecision / DeploymentPlan / RuntimeBinding 尚未 first-class。**這三個是 governance 接到 execution 的最重要缺口。
4. **Telemetry backend 缺失會阻塞 OODA。**沒有 telemetry / incident / postmortem，Pantheon 只能 governance-ready，不能 evolution-ready。
5. **Research backend 缺失會阻塞自我演化。**沒有 source ingest / experiment orchestrator / trainer dataset，Pantheon 不能形成真正的 investment learning loop。

---

## 11. 建議 Sprint 切分

### Sprint 1：BFF P0 foundation

目標：讓 Management 能 strict live bootstrap。任務：`/bff/me` / `/bff/auth/refresh` / `/bff/logout` / `/openapi.json` / `/bff/actions/{entityType}/{entityId}/{actionId}` / `/bff/approvals/{id}/decide` / strategies / personas / capital-pools read APIs。

### Sprint 2：Governance -> Deployment handoff

目標：把 implemented registry 接到 deployment plan。任務：ApprovalDecision / DeploymentPlan / stage planner / deployment projection / pool/runtime compatibility checks。

### Sprint 3：Runtime binding + loader migration

目標：讓 approved artifact 可以進 paper runtime smoke。任務：RuntimeBinding / Runtime Manager skeleton / loader metadata migration / `/bff/runtimes` / paper runtime smoke。

### Sprint 4：Telemetry / audit / incident foundation

目標：所有 high-risk actions 與 runtime events 都能看到真實 audit / telemetry。任務：telemetry event store / audit endpoint / alerts endpoint / incidents endpoint / runtime heartbeat / basic postmortem。

### Sprint 5：Research / Trainer backend MVP

目標：把 Agora / Trainer 的資料鏈開始寫入 backend。任務：TeachingSession / TeachingEvent / rapid eval skeleton / StrategySpec seed builder / ExperimentTask / ExperimentRun skeleton。

### Sprint 6：Evolution seed

目標：建立最小 OODA correction loop。任務：EvolutionDecision / postmortem -> evolution proposal / loop-runs endpoint / sentinel findings endpoint。

---

## 12. 開發協同立即行動清單

### 本週需完成的決策

1. 確認 `pantheon@master` 為正式後端基準。
2. 確認 `execute-plans@main` 為正式前端基準。
3. 確認 BFF canonical path catalog。
4. 確認 ApprovalDecision schema owner。
5. 確認 DeploymentPlan schema owner。
6. 確認 RuntimeBinding schema owner。
7. 確認 loader metadata migration owner。

### 本週需建立的 issue epics

```text
EPIC-BFF-P0：Session / Action / Approval / Entity Registry live endpoints
EPIC-GOV-DEPLOY：ApprovalDecision + DeploymentPlan + Stage Planner
EPIC-RUNTIME：RuntimeBinding + Runtime Manager + Artifact Loader migration
EPIC-TELEMETRY：Audit + Alert + Incident + Runtime Heartbeat
EPIC-RESEARCH：Source Ingest + Experiment Orchestrator + Rapid Eval
EPIC-EVOLUTION：Postmortem -> EvolutionDecision -> Loop/Sentinel endpoints
```

### 本週需產出的技術文件

1. BFF Route Implementation Matrix
2. ApprovalDecision Schema
3. DeploymentPlan Schema
4. RuntimeBinding Schema
5. Loader Metadata Migration Plan
6. TelemetryEvent Schema
7. Management Strict Integration Test Plan

---

## 13. 最終結論

以 `pantheon@master` 和 `execute-plans@main` 重新校準後，Pantheon 的開發現況最精確描述為：

```text
FE / Management / Agora / spec / state machine: mature
Registry / Promotion: implemented core
Artifact Loader: partial implemented / in progress
BFF live endpoints: major gap
DeploymentPlan / RuntimeBinding / Runtime Manager: major gap
Telemetry / Postmortem / Evolution backend: major gap
Research / Source Ingest / Imitation backend: major gap
```

因此下一階段開發團隊不應分散把所有缺口同時開寫，而應按本文件提出以下工作主線：

1. **BFF P0 live foundation**
2. **Governance -> Deployment canonical handoff**
3. **Runtime binding + loader metadata migration**
4. **Telemetry / audit / incident foundation**
5. **Research / trainer backend foundation**
6. **Evolution OODA seed loop**

完成前 3 條後，Management 可以從 mock/spec 成熟的狀態進入真實 governed deployment 的狀態。完成後 3 條後，Pantheon 才能開始接近真正的投資管理 OODA 自我演化系統。
