# OPENCLAW_RUNTIME_CONTRACT.md

Last updated: 2026-04-30
Status: canonical runtime-boundary contract for upstream OpenClaw-compatible runtimes
Tier: L1 Platform Architecture & Policy
Scope: upstream runtime dependency, adapter boundary, and ownership split between Pantheon and runtime substrate
Conflict rule: this document overrides broader OpenClaw mentions in overview/planning docs, but does not override narrower implementation contracts under a future dedicated adapter package

## 1. 文件目的

本文件定義 Pantheon 與 **OpenClaw-compatible runtime** 之間的正式契約。

目標不是描述 OpenClaw 內部如何實作，而是明確界定：

- Pantheon 依賴 OpenClaw 的哪些能力
- 哪些能力屬於必要 contract
- 哪些能力由 Pantheon adapter 負責補足
- OpenClaw 與 Pantheon 的 ownership 邊界

> 核心決議：**OpenClaw 在 Pantheon 中被視為外部 agent runtime / control-plane substrate。**  
> Pantheon 不重寫 OpenClaw，本文件描述的是 **Pantheon 對 OpenClaw-compatible runtime 的最小依賴契約**。

> 實作邊界：OpenClaw 相關 adapter / facade / runtime-adoption scaffold 可以先開發並以
> fail-closed 方式驗證；不得因此啟用 paper / canary / live execution、broker session、
> capital binding，或讓 OpenClaw 成為 execution kernel。
>
> 目前 repo truth（2026-04-30）：OpenClaw 的 fail-closed runtime-adoption scaffold 已落地，
> `openclaw-gateway-adapter` 已具備 typed upstream client（capabilities、session lifecycle）、
> Pantheon-owned session lifecycle store（durable state machine、idempotent create、operator
> audit trail）、以及 tool/workflow bridge（allowed-tool policy、deny-by-default、operator
> context mapping、request/response audit trail、broker/live/paper/capital 永久拒絕）。
> 所有 invocation 均須提供 `X-Operator-Id`；broker session、paper/canary/live route、
> capital binding 與 execution-kernel 角色仍然關閉，必須等未來明確 activation gate。

---

## 2. 定義與範圍

### 2.1 OpenClaw 在 Pantheon 裡的角色

OpenClaw 只負責：

- agent runtime
- session lifecycle
- tools / skills / plugins 載入
- multi-agent / consultation runtime
- workflow / cron / hooks 的執行

OpenClaw **不負責**：

- Strategy Registry
- Experiment Registry
- Artifact Registry
- Approval / Promotion
- Capital Pool / Runtime binding
- LEAN deployment
- canonical telemetry / lineage
- paper / canary / live execution kernel

### 2.2 Pantheon Adapter 的角色

`openclaw-gateway-adapter` 是 Pantheon 的封裝層，負責把 Pantheon domain model 映射到 OpenClaw runtime contract。

它不重寫 OpenClaw 本體，只負責：

1. agent provisioning
2. session create / resume / end
3. tool / skill / workflow mapping
4. persona context 與 OpenClaw session context 的橋接
5. consult bus / sub-agent orchestration
6. cron / hooks 到 Pantheon jobs 的橋接
7. auth / capability resolution / policy filtering

目前 adapter 的 upstream client surface：

**Session / capability surfaces（SVC-OPENCLAW-UPSTREAM-CLIENT + SVC-OPENCLAW-SESSION-LIFECYCLE）：**
- `GET /api/openclaw-adapter/capabilities`：回傳 Pantheon fail-closed capability snapshot，若 upstream 可達則附帶 upstream capabilities，否則維持 degraded。
- `GET /api/openclaw-adapter/sessions`：呼叫 upstream session list 並正規化 session metadata。
- `GET /api/openclaw-adapter/sessions/{session_id}`：呼叫 upstream session get。
- `POST /api/openclaw-adapter/sessions`：呼叫 upstream session create，但不啟用 broker/paper/live/capital binding。
- `POST /api/openclaw-adapter/sessions/{session_id}/cancel`：呼叫 upstream cancel。
- `GET /api/openclaw-adapter/lifecycle/sessions`：Pantheon-owned 持久化 session list，可依 operator_id 與 state 篩選。
- `GET /api/openclaw-adapter/lifecycle/sessions/{id}`：Pantheon-owned session record，active 時從 upstream 同步狀態。
- `POST /api/openclaw-adapter/lifecycle/sessions`：idempotent create；記錄 operator identity、idempotency key、審計軌跡。
- `POST /api/openclaw-adapter/lifecycle/sessions/{id}/cancel`：operator-owned cancel；upstream 不可達時保留 local record。
- `GET /api/openclaw-adapter/lifecycle/sessions/{id}/audit`：append-only 審計軌跡。

**Tool / workflow bridge surfaces（SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE）：**
- `GET /api/openclaw-adapter/tools/policy`：查詢目前 allowed_tools / allowed_workflows policy；預設 deny-all。
- `GET /api/openclaw-adapter/tools`：列出對目前 operator/session 有效的 tool set（policy allowlist ∩ upstream reported tools）；需 `X-Operator-Id`。
- `POST /api/openclaw-adapter/tools/invoke`：invoked a named tool within a session；須通過 policy check；需 `X-Operator-Id` 與 session_id；audit trail 必寫；broker/live/paper tools 永遠拒絕。
- `POST /api/openclaw-adapter/workflows/trigger`：觸發 workflow ref；須通過 policy check；需 `X-Operator-Id`；audit trail 必寫；broker/live/paper/capital workflow 前綴永遠拒絕。
- `GET /api/openclaw-adapter/workflows/jobs/{job_id}`：查詢 workflow job 狀態（呼叫 upstream）。
- `GET /api/openclaw-adapter/audit/invocations`：讀取 tool/workflow invocation audit log；可依 session_id / operator_id 篩選。

**Policy 環境變數：**
- `OPENCLAW_ALLOWED_TOOLS`：comma-separated 允許呼叫的 tool names；預設空 = deny all。
- `OPENCLAW_ALLOWED_WORKFLOWS`：comma-separated 允許觸發的 workflow refs；預設空 = deny all。
- `OPENCLAW_UPSTREAM_TIMEOUT` 與 `OPENCLAW_UPSTREAM_RETRIES` 控制 adapter 對 upstream 的 timeout/retry；未設定 upstream 或 upstream 不健康時，adapter 必須回 degraded/error envelope，不得自動啟用 execution path。

---

## 3. 契約原則

### 3.1 外部依賴原則
Pantheon 只依賴 OpenClaw-compatible runtime 的外部契約，不依賴其內部資料結構或私有實作。

### 3.2 session-first 原則
Pantheon 的 persona 執行態，不是固定 process，而是 **session-bound runtime instance**。

### 3.3 capability filtering 原則
Pantheon 的 route policy / consult policy / RBAC 先決定 effective capability，再交給 OpenClaw runtime 執行。

### 3.4 no implicit secret sharing 原則
persona 間不得因為共用 runtime 就隱式共享 auth / secrets / credentials。

### 3.5 adapter owns mapping 原則
所有 Pantheon domain object 與 OpenClaw runtime object 的對應關係，由 adapter 層負責。

---

## 4. 最小能力契約（Minimum Runtime Contract）

Pantheon 對 OpenClaw-compatible runtime 的最小依賴如下。

### 4.1 Agent Provisioning
必須支援：

- 建立 agent
- 指定 agent identity / label
- 指定 workspace 或 cwd
- 綁定 tool profile / skill set
- 載入 shared 與 per-agent skills
- 更新 agent capability allowlist

**Required API semantics**

- `create_agent(persona_ref, effective_capability_set, workspace_ref)`
- `update_agent(agent_id, capability_delta, metadata_delta)`
- `deactivate_agent(agent_id)`

### 4.2 Session Lifecycle
必須支援：

- 建立 session
- 恢復 session
- 結束 session
- 查詢 session 狀態
- 以 session 為邊界執行 task / consult / training interaction

**Required API semantics**

- `create_session(agent_id, session_type, context_bundle)`
- `resume_session(session_id)`
- `terminate_session(session_id)`
- `get_session_status(session_id)`

**Session types**

- `interactive`
- `trainer`
- `research_task`
- `consult`
- `committee`
- `red_team`
- `background_job`

### 4.3 Tool Resolution
必須支援：

- 查詢可用 tool
- 依 allowlist / denylist 決定是否可呼叫
- 執行 typed tool
- 返回結構化結果

**Required API semantics**

- `resolve_tools(agent_id, session_id)`
- `invoke_tool(session_id, tool_name, args)`
- `list_tools(agent_id)`

### 4.4 Skill Resolution
必須支援：

- 載入 shared skills
- 載入 agent-specific skills
- precedence / override
- allowlist

**Required API semantics**

- `resolve_skills(agent_id)`
- `attach_shared_skill(agent_id, skill_ref)`
- `attach_local_skill(agent_id, skill_ref)`
- `compute_effective_skill_set(agent_id)`

### 4.5 Multi-Agent Consultation
必須支援：

- 啟動 sub-agent
- 跨 session 發送 consult request
- 聚合多個 agent 回覆
- 保存 consult session references

**Required API semantics**

- `spawn_subagent(parent_session_id, target_agent_id, consult_context)`
- `send_session_message(from_session_id, to_session_id, message)`
- `collect_replies(consult_session_group_id)`

### 4.6 Workflow / Cron / Hooks
必須支援：

- 註冊 workflow job
- 定時喚醒 agent / workflow
- 對事件觸發 hook
- 查詢 job status

**Required API semantics**

- `schedule_job(job_type, cron_expr, payload)`
- `trigger_workflow(workflow_ref, context)`
- `get_job_status(job_id)`
- `cancel_job(job_id)`

---

## 5. Pantheon 與 OpenClaw 的責任邊界

| 領域 | OpenClaw-compatible runtime | Pantheon |
|---|---|---|
| Agent session 執行 | 是 | 否 |
| Shared tools / skills 裝載 | 部分 | 主責定義與授權 |
| Persona registry | 否 | 是 |
| Consult session 執行 | 是 | 協調與審核 |
| Strategy / Experiment / Artifact registry | 否 | 是 |
| Approval / Promotion | 否 | 是 |
| Capital pool / LEAN deployment | 否 | 是 |
| Canonical telemetry / lineage | 否 | 是 |

---

## 6. Runtime Object Mapping

| Pantheon object | Runtime object | 說明 |
|---|---|---|
| Persona | Agent definition | 靜態 persona 在 runtime 中映射為 agent |
| CapabilitySnapshot | Effective tool/skill set | session 啟動前計算 |
| TeachingSession | Trainer session | runtime session type = trainer |
| ConsultRequest | Consult / sub-agent session | 由 adapter 建立 consult group |
| WorkflowTemplate | Workflow invocation | runtime 只看 workflow ref 與 context |

---

## 7. 安全與隔離要求

### 7.1 必要隔離
- per-agent workspace
- per-agent auth profile
- no implicit credential sharing
- 高風險 persona 必須搭配 sandbox

### 7.2 禁止事項
- 同一 agentDir 給多 persona 共用
- 直接把 broker secret 掛到 shared runtime global scope
- 讓 runtime 自由繞過 Pantheon capability filtering

### 7.3 安全審計
任何 runtime session 都必須有：

- `persona_id`
- `session_id`
- `trace_id`
- `request_id`
- `actor_type`
- `environment`

---

## 8. Sync / Async 契約

### 同步命令
- create agent
- create session
- resolve capability
- accept workflow trigger
- accept consult request

### 非同步執行
- session inference / dialogue
- committee / consult completion
- workflow completion
- cron jobs

### Retry owner
- command retry：BFF / caller
- job retry：workflow orchestrator / adapter
- runtime exec retry：OpenClaw-compatible runtime，僅在安全語義內

---

## 9. 錯誤模型

OpenClaw adapter 的錯誤分類採 **三層模型**。

### 9.1 第一層：known typed errors

這些錯誤具有明確語義，可預測、可映射、可補償。

包括：

- `AGENT_NOT_FOUND`
- `SESSION_NOT_FOUND`
- `CAPABILITY_DENIED`
- `SKILL_RESOLUTION_FAILED`
- `TOOL_INVOCATION_FAILED`
- `SUBAGENT_SPAWN_FAILED`
- `WORKFLOW_TRIGGER_FAILED`
- `RUNTIME_UNAVAILABLE`
- `TIMEOUT`
- `UPSTREAM_UNAVAILABLE`

### 9.2 第二層：transport / system errors

這些錯誤來自傳輸層或 runtime 系統層，通常是基礎設施問題。

包括：

- `NETWORK_PARTITION`
- `SERIALIZATION_FAILURE`
- `PROCESS_KILLED` / `OOM_KILL`
- `UNEXPECTED_STATUS_CODE`
- `CONNECTION_REFUSED`
- `DNS_FAILURE`

### 9.3 第三層：unknown_upstream_error

任何 **不能映射到第一層或第二層** 的錯誤，全部落入 `unknown_upstream_error`。

這是最終 fallback 分類，不是例外。
若錯誤無法被理解，就必須假設 agent runtime 行為不可預期。

### 9.4 錯誤欄位要求

每個錯誤回應必須至少包含：

- `error_code`
- `message`
- `session_id`（若適用）
- `trace_id`
- `retryable`
- `owner_plane`
- `error_layer`（`known` / `transport` / `unknown`）

### 9.5 unknown_upstream_error fallback 行為

當 adapter 遇到 `unknown_upstream_error` 時，必須執行下列四件事：

#### A. 保留 raw envelope

必須完整記錄：

- raw payload
- stderr / stdout
- upstream response code（若有）
- session / agent / tool context
- 發生時間與 worker ID

#### B. 回傳安全降級結果

絕不能把 unknown error 當作成功處理。
對不同下游必須回傳對應的 unavailable 狀態：

| 下游用途 | 降級結果 |
|---|---|
| consultation | `consult_unavailable` |
| trainer preview | `preview_unavailable` |
| governance assist | `review_assist_unavailable` |
| capability resolution | deny-by-default |

#### C. 隔離 agent runtime

- 將該 session 標記為 `degraded`
- repeated unknown error 時，trip circuit breaker
- 暫停此 agent 的新 consult / teaching workload
- degraded session 不參與新的 consultation group

#### D. 升級為 incident

若同一 agent / session / adapter worker 在定義時間窗內（v1 建議 5 分鐘）連續出現 `unknown_upstream_error`（v1 建議閾值：3 次），直接開立 incident。

### 9.6 circuit breaker policy

每個 agent / session 維度獨立計數 circuit breaker：

- **closed**：正常執行
- **open**：連續 unknown error 達閾值，暫停新 workload
- **half-open**：cooldown 後允許一次 probe，成功則 closed，失敗則重新 open

v1 建議參數：
- failure threshold: 3
- cooldown: 5 分鐘
- probe timeout: 30 秒

---

## 10. Degraded Mode & Session Quarantine

### 10.1 Degraded Mode 定義

當 agent / session 進入 degraded mode 時：

- 不接收新 consult request
- 不啟動新 teaching session
- 不觸發新 workflow / cron job
- 既有 session 若可安全終止，則終止；若不能安全終止，則標記並監控
- 不影響已 active 的 paper / canary / live runtime

### 10.2 Session Quarantine

當 session 被標記為 `quarantined` 時：

- 禁止一切 tool invocation
- 禁止 sub-agent spawn
- 僅允許 `terminate_session` 與 `get_session_status`
- quarantine 解除需經 operator 手動確認或 circuit breaker half-open probe 成功

### 10.3 安全邊界

最重要的一條規則：

> **OpenClaw 的 unknown error 不得直接影響 live execution。**

OpenClaw 在 Pantheon 中是 **control plane / consultation / teaching substrate**，不是 **execution kernel**。
即使 agent runtime 行為不可預期，kill switch 也應該透過 runtime-manager fast path 生效，而不是依賴 OpenClaw。

這與 Pantheon 的核心架構原則一致：
- LLM / agent 主要用於研究與治理，不直接做 execution
- execution kernel（LEAN）的控制路徑與 agent runtime 完全隔離
- kill switch 的安全路徑不依賴任何 LLM / agent 組件

---

## 11. 事件與審計

adapter 必須輸出至少以下事件：

- `agent.created`
- `agent.updated`
- `session.created`
- `session.terminated`
- `session.degraded`
- `session.quarantined`
- `circuit_breaker.opened`
- `circuit_breaker.half_open`
- `circuit_breaker.closed`
- `consult.spawned`
- `workflow.triggered`
- `cron.job.started`
- `cron.job.failed`
- `tool.invoked`
- `tool.denied`
- `error.unknown_upstream`

這些事件會送到 Telemetry / Audit Plane，而不是只留在 runtime 內。

---

## 12. 向上層暴露的 API 草案

### Adapter facade
- `POST /control/personas/{persona_id}/sessions`
- `POST /control/sessions/{session_id}/invoke`
- `POST /control/consult/spawn`
- `GET /control/sessions/{session_id}`
- `GET /control/personas/{persona_id}/capabilities`
- `POST /control/jobs`
- `GET /control/jobs/{job_id}`

---

## 13. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下文件屬於後續拆解與實作細化，能補強執行細節，但不是本文件作為目前 canonical runtime boundary 的生效前提。

1. `PERSONA_RUNTIME_MODEL.md`
2. `SERVICE_OWNERSHIP_AND_TRIGGER_MATRIX.md`
3. `CAPABILITY_RESOLUTION_SPEC.md`
4. `CONSULTATION_SESSION_PROTOCOL.md`
5. `OPENCLAW_ERROR_MAPPING_SPEC.md`（詳列三層錯誤的完整映射表與 circuit breaker 參數）

---

## 14. 結論

Pantheon 對 OpenClaw 的正式立場是：

- OpenClaw 是外部 compatible runtime
- Pantheon 不重寫它
- Pantheon 透過 adapter 接它
- 所有 persona / consult / cron / workflow 執行都靠這個 contract
- registry / governance / execution / telemetry 仍由 Pantheon 自己擁有

這樣做的目的，是把控制平面與交易平面明確切開，讓 OpenClaw 成為可靠的 agent substrate，而不是未定義黑盒。
