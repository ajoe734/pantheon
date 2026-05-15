# Pantheon Product-Facing Glossary and Stage/Status Language Pack

Last updated: 2026-04-13
Status: canonical product-facing terminology reference
Tier: L2 Planning & Execution
Scope: glossary, action→object map, stage/status wording (internal governance vs. external product-facing)
Conflict rule: this document does not redefine semantics; it translates L1 canonical terms into product-facing language

## Purpose

This document is the **single source of truth for product-facing terminology** in Pantheon: the labels, short descriptions, and status phrases that should appear on operator-facing surfaces such as BFF dashboards, review queues, notifications, and runbooks.

It serves three functions:

1. **Glossary** — key terms in EN and ZH with traceable canonical sources
2. **Action → Object Map** — which user actions touch which canonical object(s)
3. **Stage/Status Language Pack** — the approved wording for governance, deployment, persona, binding, and evolution states

> Rule: when semantic meaning and UI copy disagree, the canonical source wins. This file translates; it does not redefine.
>
> Source discipline: the `Canonical Source` column may cite L1 policy docs or the concrete service contract that owns the object. It intentionally does **not** cite task IDs.

---

## Part 1: Glossary

### 1.1 Core System Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Pantheon | 萬神殿系統 | The multi-persona AI trading platform we are building | `TARGET_ARCHITECTURE.md` |
| OpenClaw | OpenClaw | Upstream runtime substrate Pantheon integrates for agent/session/workflow execution | `OPENCLAW_RUNTIME_CONTRACT.md` |
| Plane | 功能平面 | A logical subsystem such as Data, Research, Persona, Governance, Execution, or Evolution | `TARGET_ARCHITECTURE.md` |
| Artifact | 工件 | Product-facing name for a governed registry artifact represented canonically by an `ArtifactRecord` / registry entry | `TARGET_ARCHITECTURE.md`, `services/registry/contract.md` |
| StrategySpec | 策略規格書 | Canonical normalized strategy description before registry admission | `services/control-plane/specs/contract.md` |
| Strategy Registry | 策略登錄中心 | System of record for governed artifact versions, lineage, and approval linkage | `services/registry/contract.md` |
| Capital Pool | 資金池 | Governance object for deployable capital, budget, and single-runtime rules | `services/control-plane/governance/capital_pool.contract.md` |
| Persona | 人格 | Registered AI trading persona with mandate, lifecycle state, and policy references | `PERSONA_RUNTIME_MODEL.md`, `services/control-plane/persona/contract.md` |
| Session | 工作階段 | Runtime-bounded unit of persona execution | `PERSONA_RUNTIME_MODEL.md`, `OPENCLAW_RUNTIME_CONTRACT.md` |
| Runtime | 執行環境 | OpenClaw-compatible substrate that runs Pantheon persona sessions and workflows | `OPENCLAW_RUNTIME_CONTRACT.md` |
| LEAN | LEAN | Quantitative trading engine Pantheon uses for backtest, paper, and live execution | `TARGET_ARCHITECTURE.md` |
| BFF | 操作台整合層 | Backend-for-Frontend layer that aggregates operator-facing control and read surfaces | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `OPERATOR_ACCEPTANCE_MATRIX.md` |
| CLI | 管理命令列 | Fallback admin command surface used when BFF is degraded or unavailable | `OPERATOR_ACCEPTANCE_MATRIX.md` |

### 1.2 Lifecycle and State Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Artifact State | 工件治理狀態 | Governance maturity of an artifact: `draft` → `candidate` → `approved` → `retired` | `TARGET_ARCHITECTURE.md`, `services/registry/contract.md` |
| Deployment Stage | 部署階段 | Actual runtime stage: `none` → `paper` → `canary` → `live` → `frozen` | `TARGET_ARCHITECTURE.md`, `PAPER_CANARY_LIVE_POLICY.md` |
| Persona Lifecycle | 人格生命週期 | Persona admissibility ladder: `draft` → `research_only` → `consultable` → `paper_owner` → `live_owner` → `frozen` / `retired` | `PERSONA_RUNTIME_MODEL.md`, `services/control-plane/persona/contract.md` |
| Binding Governance Status | 綁定治理狀態 | Fine-grained `PersonaCapitalBinding` status: `pending`, `active`, `suspended`, `revoked`, `expired` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `services/control-plane/governance/capital_pool.contract.md` |
| Binding Read-Model Status | 綁定讀模型狀態 | Coarse execution/read-model projection: `active` / `inactive` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` |
| `draft` | 草稿 | Authored but not yet in governance review | `TARGET_ARCHITECTURE.md`, `services/registry/contract.md` |
| `candidate` | 候選 | Submitted for governance review and promotion-gate checks | `TARGET_ARCHITECTURE.md`, `services/registry/promotion/README.md` |
| `approved` | 已核准 | Approved for deployment planning; not necessarily running yet | `TARGET_ARCHITECTURE.md`, `services/control-plane/governance/contract.md` |
| `retired` | 已退役 | No longer promotable and not valid for new deployment planning | `TARGET_ARCHITECTURE.md`, `services/registry/contract.md` |
| `paper` | 模擬盤 | Real data, governed artifact path, simulated execution, no real capital at risk | `PAPER_CANARY_LIVE_POLICY.md` |
| `canary` | 金絲雀盤 | Real orders on scaled capital under strict monitoring | `PAPER_CANARY_LIVE_POLICY.md`, `services/control-plane/governance/deployment_plan.contract.md` |
| `live` | 實盤 | Full production deployment with real capital and full exposure | `PAPER_CANARY_LIVE_POLICY.md` |
| `frozen` | 凍結中 | Deployment-stage quarantine; no new entries, and runtime treatment follows freeze / rollback policy | `TARGET_ARCHITECTURE.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `services/control-plane/governance/deployment_plan.contract.md` |
| Promotion | 升版 | Governed advancement of an artifact through registry maturity, separate from deployment stage | `services/registry/promotion/README.md` |
| Rollback | 回退 | Operational mitigation that rebinds runtime to a safer approved artifact or stage | `ROLLBACK_AND_POSITION_SEMANTICS.md`, `services/execution/runtime-manager/contract.md` |
| Lineage | 血緣鏈 | Provenance chain linking artifacts, decisions, datasets, and telemetry evidence | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `services/registry/lineage/contract.md` |

### 1.3 Binding and Deployment Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Binding | 綁定 | Governance association between a persona and a capital pool; it authorizes but does not deploy | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `services/control-plane/governance/capital_pool.contract.md` |
| PersonaCapitalBinding | 人格資金綁定 | Governing object linking a persona to a capital pool with role, status, and scope ceiling | `services/control-plane/governance/capital_pool.contract.md` |
| Deployment Plan | 部署計畫 | Governed stage-transition intent for an already-approved artifact | `services/control-plane/governance/deployment_plan.contract.md` |
| Runtime Binding | 執行綁定 | Execution-plane record of what approved artifact is actually running for a pool | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `services/execution/runtime-manager/contract.md` |
| Allowed Deployment Scope | 允許部署範圍 | Permission ceiling on a binding: `none` / `paper` / `canary` / `live` | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `services/control-plane/governance/capital_pool.contract.md` |
| Runtime Status | 執行狀態 | Runtime-manager lifecycle such as `active`, `pending_pause`, `paused`, `retired`, or `failed` | `services/execution/runtime-manager/contract.md` |
| Cutover | 切換 | Runtime moment when a new binding replaces the old one | `ROLLBACK_AND_POSITION_SEMANTICS.md`, `services/execution/runtime-manager/contract.md` |

### 1.4 Governance and Evolution Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Approval Decision | 核准決策 | Formal approval authority record for approve / reject / conditional approval | `services/control-plane/governance/contract.md` |
| Promotion Gate | 升版門 | Registry-side governance checks that move an artifact to `candidate` or `approved` without collapsing deployment stage | `services/registry/promotion/README.md` |
| Evolution Decision | 演化決策 | Formal governed follow-up action record triggered by telemetry, incidents, or review | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `services/control-plane/governance/evolution_decision.contract.md` |
| Evolution Controller | 演化控制器 | System component that proposes evolution actions from evidence and thresholds | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `services/control-plane/governance/evolution_controller_contract.md` |
| Reviewer on Duty | 值班審查者 | Low-risk review / approval owner for routine evolution actions | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` |
| Risk Owner | 風險負責人 | Medium/high-risk review authority for deployment/evolution follow-up | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` |
| Governance Committee | 治理委員會 | High-risk approval body for freezes, retirements, and other structural actions | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` |
| Cooldown Window | 冷卻視窗 | Minimum wait period after an executed evolution decision before another structural mutation | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `services/control-plane/governance/evolution_decision.contract.md` |
| Observation Window | 觀察視窗 | Post-execution monitoring window used to confirm convergence before further action | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `services/control-plane/governance/evolution_decision.contract.md` |
| Deny-First Policy | 拒絕優先原則 | Permission model where actions are denied unless explicitly allowed | `services/control-plane/permissions/contract.md` |
| Kill Switch | 緊急開關 | Emergency fast path that routes through runtime-manager rather than directly mutating LEAN | `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `OPERATOR_ACCEPTANCE_MATRIX.md` |
| Safe Mode | 安全模式 | Restricted emergency execution state entered through kill-switch policy | `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` |

### 1.5 Research and Learning Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Research Ingestion | 研究素材匯入 | Governed discovery of papers, repos, and notes from approved structured sources | `services/research/ingest/INGESTION_WORKFLOW.md`, `services/research/grok_source_catalog.md` |
| Replication Gate | 複現門 | Validation gate between research handoffs and registry admission | `services/research/replication/GATE_CONTRACT.md` |
| Evaluator | 評估器 | Component that scores governed artifacts and emits `evaluation_result` | `services/evaluation/contracts/README.md` |
| Critic | 審查器 | Component that analyzes evaluator outputs and emits `critique_result` rationale | `services/evaluation/contracts/README.md` |
| Optimizer | 優化器 | Component that produces new candidate artifacts plus `optimizer_result` provenance | `services/evaluation/optimizers/contract.md` |
| Imitation Learning | 模仿學習 | Trader-behavior cloning path using governed trajectories and BC-first workflow | `services/learning/imitation/README.md` |
| Preference Learning | 偏好學習 | Training preference models from governed FB-002 feedback only | `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md` |
| Experiment Registry | 實驗登錄 | MLflow-first metadata bridge for experiment lineage inspection | `services/registry/experiments/README.md`, `OPENCLAW_RUNTIME_CONTRACT.md` |

### 1.6 Feedback and Telemetry Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Trajectory | 操作軌跡 | Governed sequence of operator decisions and step-level observations used for imitation or review | `services/feedback/schema/contract.md`, `services/learning/imitation/README.md` |
| Preference | 偏好事件 | Explicit approve / edit / reject / rationale feedback captured in the governed feedback store | `services/feedback/schema/contract.md`, `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md` |
| Telemetry Event | 遙測事件 | Canonical execution observation such as PnL, drawdown, fills, or slippage | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `services/feedback/schema/contract.md` |
| Incident | 事故 | Operational event requiring incident response and possible governed follow-up | `services/incident/contract.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` |
| Postmortem | 事後檢討 | Structured analysis of an incident used as evidence for later learning and control changes | `services/incident/contract.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` |
| Institutional Memory | 機構記憶 | Reusable knowledge accumulated from incidents, postmortems, and review loops | `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` |

### 1.7 Event and Messaging Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| Aggregate | 聚合體 | Unit of event ordering, such as a strategy, artifact, or deployment plan | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |
| At-Least-Once Delivery | 至少一次送達 | Delivery guarantee where retries may happen but loss is not allowed | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |
| Idempotency | 冪等性 | Consumer-side guarantee that duplicates do not create duplicate effects | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |
| Outbox Pattern | 發件箱模式 | Reliable publish pattern coupled to canonical writes | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |
| Causal Parent | 因果父事件 | The event that causally precedes the current event within an aggregate | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |

### 1.8 Data Plane Terms

| Term (EN) | Term (ZH) | Definition | Canonical Source |
|---|---|---|---|
| SecurityMaster | 商品主檔 | Canonical identity record for tradeable cash instruments | `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` |
| ContractMaster | 契約主檔 | Canonical contract record for derivatives and underlying linkage | `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` |
| MarketCalendarSession | 交易時段日曆 | Canonical market session / holiday object per market and trade date | `MARKET_CALENDAR_AND_SESSION_POLICY.md` |
| Corporate Action | 公司行動 | Identity-affecting event such as dividends, splits, delistings, or local corporate actions | `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md`, `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` |
| Symbol Mapping | 代碼映射 | Native-to-canonical symbol resolution across venues, brokers, and local code systems | `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` |
| Source Class | 資料來源類別 | Normalized classification of ingest sources such as `official_reference` or `internal_can` | `DATA_SOURCE_SCOPE_MATRIX.md` |
| DatasetVersion | 資料版本包 | Immutable replay-ready package of datasets, masters, and calendars | `DATASET_VERSION_AND_REPLAY_POLICY.md` |
| Dataset Lineage | 資料集血緣 | Traceable refs from dataset packages back to raw, normalized, and feature datasets | `DATASET_VERSION_AND_REPLAY_POLICY.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` |

---

## Part 2: Action → Object Map

This table maps operator-facing actions to the canonical object(s) they really touch. The goal is to remove ambiguity about whether a user action is changing governance truth, deployment intent, runtime reality, or a read-only summary.

| User Action (EN) | User Action (ZH) | Target Canonical Object(s) | Authoritative Write Path | Notes |
|---|---|---|---|---|
| Create Persona | 建立人格 | `Persona` | Persona Plane registry write | Creates identity and policy anchor only; no runtime session or deployment |
| Advance Persona Lifecycle | 調整人格生命週期 | `Persona.lifecycle_state` | Governance Plane | Changes admissibility; does not create bindings |
| Bind Persona to Pool | 綁定人格到資金池 | `PersonaCapitalBinding` | Governance + Capital Pool Plane | Authorizes scope only; binding does not deploy |
| Submit Artifact | 提交工件 | `ArtifactRecord` / registry entry with `artifact_state=candidate` | Registry intake / promotion path | Makes the artifact reviewable, not runnable |
| Approve Artifact | 核准工件 | `ApprovalDecision` | Promotion review service / Governance Plane | Formal approval authority; required before deployment planning |
| Reject Artifact | 駁回工件 | `ApprovalDecision` | Promotion review service / Governance Plane | Records rejection with rationale; does not mutate runtime |
| Plan Paper Deployment | 規劃模擬盤部署 | `DeploymentPlan` | Governance / Promotion Plane | Explicit `none -> paper` stage intent with rollback linkage |
| Plan Canary Promotion | 規劃金絲雀盤升級 | `DeploymentPlan` | Governance / Promotion Plane | Explicit `paper -> canary` stage intent |
| Plan Live Promotion | 規劃實盤升級 | `DeploymentPlan` | Governance / Promotion Plane | Explicit `canary -> live` stage intent |
| Activate / Replace Runtime | 啟動或替換執行綁定 | `RuntimeBinding` | Runtime Manager | Execution-plane realization of an approved `DeploymentPlan` |
| Pause Runtime | 暫停執行環境 | `RuntimeBinding.status`, `RuntimeStatus` | Runtime Manager | Operational control only; does not change artifact_state |
| Freeze Deployment | 凍結部署 | `EvolutionDecision` plus optional `DeploymentPlan(current_stage -> frozen)` | Governance decision with deployment/runtime follow-through | Freeze is governance quarantine; runtime mitigation may be separate |
| Roll Back Deployment | 回退部署 | `DeploymentPlan.rollback`, `RuntimeBinding` | Rollback Controller -> Runtime Manager | Rebinds runtime to a safer approved target; does not change artifact_state |
| Retire Artifact | 退役工件 | `ArtifactRecord` with `artifact_state=retired` | Registry / Governance Plane | Ends promotability; runtime exit is a separate path |
| Request Evolution | 提出演化請求 | `EvolutionDecision` with `decision_state=proposed` | Evolution Controller / operator | Creates governed follow-up request only |
| Execute Approved Evolution | 執行演化決策 | `EvolutionDecision.execution_result` plus downstream work item | Authoritative downstream plane | May create governance, research, deployment, or runtime work; does not bypass owners |
| Execute Kill Switch | 啟動緊急開關 | `RuntimeStatus` with `RuntimeBinding` refs | Kill-switch controller -> Runtime Manager fast path | Emergency-only path; bypasses normal queue, not authority boundaries |
| View Telemetry | 查看遙測 | `TelemetryEvent` read models | Telemetry / BFF read surface | Read-only query path |
| Provide Feedback | 提供回饋 | `TraderFeedbackEvent` | Feedback store | Append-only governed feedback, not in-place mutation of the artifact |
| Open Incident / Postmortem | 建立事故或事後檢討 | `IncidentCase`, `Postmortem` | Incident Plane | Feeds institutional memory and evolution evidence |

---

## Part 3: Stage/Status Language Pack

### 3.1 Usage Rules

Use these rules on all operator-facing surfaces:

1. Keep the canonical enum visible in API payloads, debug drawers, audit views, or tooltips.
2. Use the friendly label only on dashboards, notifications, runbooks, and summary chips.
3. Never collapse `artifact_state`, `deployment_stage`, persona lifecycle, binding governance status, and runtime status into one generic `status`.
4. If a screen shows the coarse `active` / `inactive` binding projection, label it as a **binding summary** or **read-model projection**, not as the full governance truth.

### 3.2 Artifact Lifecycle Wording

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `draft` | Draft | 草稿 | This artifact is being authored and has not entered governance review | 此工件仍在撰寫，尚未進入治理審查 |
| `candidate` | Under Review | 審查中 | This artifact is in governance review and promotion-gate validation | 此工件正在接受治理審查與升版門驗證 |
| `approved` | Approved | 已核准 | This artifact is approved for deployment planning, but may still be undeployed | 此工件已獲核准，可進入部署規劃，但未必已上線 |
| `retired` | Retired | 已退役 | This artifact is no longer valid for new promotion or deployment planning | 此工件已退役，不再用於新的升版或部署規劃 |

### 3.3 Deployment Stage Wording

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `none` | Not Deployed | 未部署 | No runtime deployment has been initiated for this approved artifact | 這個已核准工件尚未啟動任何執行部署 |
| `paper` | Paper Trading | 模擬盤 | Running on real market data with simulated execution and no real capital at risk | 使用真實市場資料、模擬執行，沒有真實資金風險 |
| `canary` | Canary | 金絲雀盤 | Running on real orders with scaled capital and heightened monitoring | 使用真實委託、縮量資金，並接受加強監控 |
| `live` | Live | 實盤 | Running in full production with real capital and full exposure | 以真實資金正式運行，承擔完整市場曝險 |
| `frozen` | Frozen | 凍結中 | The deployment stage is quarantined; no new entries are allowed, and runtime treatment follows freeze / rollback policy | 目前處於部署凍結；不可開新倉，執行處置依 freeze / rollback 政策處理 |

### 3.4 Persona Lifecycle Wording

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `draft` | Setup in Progress | 設定中 | Persona exists but is not fully configured for governed work | 人格已建立，但尚未完成受治理配置 |
| `research_only` | Research Only | 僅研究 | Persona may run research or trainer workflows only | 只能執行研究或訓練工作流 |
| `consultable` | Consultable | 可諮詢 | Persona may join consult workflows but may not sponsor deployments | 可參與 consult，但不可主責部署 |
| `paper_owner` | Paper Sponsor | 可主責模擬盤 | Persona may sponsor paper-stage deployments | 可主責模擬盤部署 |
| `live_owner` | Live Sponsor | 可主責實盤 | Persona may sponsor paper, canary, and live deployments | 可主責模擬盤、金絲雀盤與實盤部署 |
| `frozen` | Frozen | 已凍結 | Persona may not expand capability or sponsor new deployments until revalidated | 在重新驗證前，不可擴權或主責新部署 |
| `retired` | Retired | 已退役 | Persona remains for history or replay only | 人格僅保留歷史或 replay 用途 |

### 3.5 Binding Wording

`PersonaCapitalBinding` has two valid UI views. They must not be mixed.

#### Governance Truth

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `pending` | Pending Approval | 待核准 | The binding request exists but is not yet active for admissibility checks | 綁定申請已建立，但尚未生效於准入判定 |
| `active` | Authorized | 已授權 | The binding is valid and may be used for deployment admissibility calculations | 綁定已生效，可用於部署准入計算 |
| `suspended` | Suspended | 已暫停 | The binding remains on record but is temporarily excluded from admissibility | 綁定仍存在，但暫時不納入准入計算 |
| `revoked` | Revoked | 已撤銷 | The binding has been formally withdrawn and is terminal | 綁定已正式撤銷，屬終態 |
| `expired` | Expired | 已到期 | The binding validity window ended and it no longer counts for admissibility | 綁定已過期，不再計入准入 |

#### Read-Model Projection

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `active` | Bound | 已綁定 | Coarse projection meaning the binding is currently usable in execution-facing summaries | 粗粒度投影，表示此綁定目前可在執行摘要中視為可用 |
| `inactive` | Not Bound | 未綁定 | Coarse projection covering `pending`, `suspended`, `revoked`, or `expired` | 粗粒度投影，涵蓋 `pending`、`suspended`、`revoked`、`expired` |

### 3.6 Approval Decision Wording

| Internal State / Outcome | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `proposed` | Pending Review | 待受理 | A decision record exists, but no reviewer has accepted it yet | 決策記錄已建立，但尚未被 reviewer 受理 |
| `under_review` | In Review | 審核中 | The reviewer has accepted the case and is evaluating it | reviewer 已受理，正在評估 |
| `decided: approved` | Approved | 已核准 | The decision authorizes the target to proceed | 此決策已允許目標進入下一步 |
| `decided: approved_with_conditions` | Conditionally Approved | 附條件核准 | The decision is approved, but stated conditions must be met first | 已附條件核准，需先滿足條件 |
| `decided: rejected` | Rejected | 已駁回 | The decision does not authorize the target to proceed | 此決策不允許目標繼續 |
| `superseded` | Superseded | 已被取代 | A newer decision replaced this one for the same target | 同一目標已有較新的決策取代它 |
| `revoked` | Revoked | 已撤銷 | A previously decided approval has been formally revoked | 既有決策已被正式撤銷 |

### 3.7 Evolution Decision Wording

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `proposed` | Proposed | 提案中 | A governed evolution action has been proposed from evidence or thresholds | 依據證據或門檻，已提出受治理的演化動作 |
| `reviewed` | Reviewed | 已受理審查 | The proposal has entered the formal review chain | 提案已進入正式審查鏈 |
| `approved` | Approved for Execution | 已核准執行 | The action is approved and waiting for the authoritative downstream plane | 動作已核准，等待權威下游平面執行 |
| `rejected` | Rejected | 已駁回 | The proposal was reviewed and not accepted | 提案經審查後未被採納 |
| `canceled` | Canceled | 已取消 | The proposal or approved action was canceled before execution | 提案或已核准動作在執行前被取消 |
| `executed` | Executed | 已執行 | The authoritative downstream plane accepted the work and recorded an execution result | 權威下游平面已受理並留下執行結果 |
| `superseded` | Superseded | 已被取代 | A newer evolution decision replaced this one | 此決策已被較新的演化決策取代 |

### 3.8 Runtime Binding Status Wording

Runtime status and deployment stage are separate. A screen may need to show both.

| Internal Enum | Short (EN) | Short (ZH) | Full Description (EN) | Full Description (ZH) |
|---|---|---|---|---|
| `active` | Running | 運行中 | The runtime binding is active and currently managing the pool | 執行綁定已啟用，正在管理資金池 |
| `pending_pause` | Pausing | 暫停中 | Orders or positions are being drained before pause / replace completes | 正在排空委託或部位，以完成暫停或替換 |
| `paused` | Paused | 已暫停 | The binding still exists but new runtime activity is paused | 綁定仍存在，但新的執行活動已暫停 |
| `retired` | Retired | 已退場 | Historical binding only; it no longer owns the pool | 僅保留歷史紀錄，不再管理該資金池 |
| `failed` | Failed | 失敗 | The binding failed during load or runtime operation and needs intervention | 綁定在載入或運行時失敗，需要介入處理 |

### 3.9 Error and Alert Messages

| Event | Alert (EN) | Alert (ZH) | Recommended Action |
|---|---|---|---|
| Promotion gate failure | "Artifact approval blocked: {reason}" | "工件核准受阻：{reason}" | Review the failed governance check and correct the artifact or evidence |
| Deployment freeze | "Deployment frozen for {artifact_id}" | "{artifact_id} 的部署已凍結" | Confirm whether runtime follow-through is also required |
| Rollback triggered | "Rollback activated for {artifact_id}" | "{artifact_id} 已啟動回退" | Monitor cutover and verify fallback binding health |
| Kill switch activated | "Kill switch engaged - safe mode active" | "緊急開關已啟動，進入安全模式" | Investigate root cause and prepare recovery plan |
| Approval expired | "Approval decision expired for {target_id}" | "{target_id} 的核准已過期" | Re-open review or issue a fresh approval decision before deployment planning |
| Conditional approval outstanding | "Approval conditions still open for {decision_id}" | "{decision_id} 的核准條件尚未完成" | Resolve the listed conditions before creating a deployment plan |
| Binding pending approval | "Binding pending approval for persona {persona_id}" | "人格 {persona_id} 的綁定仍待核准" | Complete binding governance review before deployment planning |
| Runtime failure | "Runtime binding failed for pool {pool_id}" | "資金池 {pool_id} 的執行綁定失敗" | Inspect runtime-manager evidence, then decide whether to retry, freeze, or roll back |
| Evolution cooldown active | "Evolution cooldown in effect until {time}" | "演化冷卻中，至 {time} 為止" | Wait until cooldown expires before proposing another structural change |
| Binding conflict | "Multiple active bindings detected for pool {id}" | "偵測到資金池 {id} 存在多個有效綁定" | Review admissibility and single-runtime rules |
| Telemetry gap | "Telemetry gap detected for {window}" | "偵測到 {window} 的遙測缺口" | Check telemetry pipeline and evidence completeness |
| Incident opened | "Incident {id} opened: {severity}" | "事故 {id} 已開啟：{severity}" | Start incident response and capture evidence refs |

### 3.10 Help Text and Tooltips

| Concept | Tooltip (EN) | Tooltip (ZH) |
|---|---|---|
| Paper Trading | "Paper trading uses real market data with simulated execution. No real capital is at risk." | "模擬盤使用真實市場資料與模擬執行，不涉及真實資金。" |
| Canary Deployment | "Canary runs on real orders with a small capital slice and stricter monitoring than live." | "金絲雀盤使用真實委託與較小資金切片，監控要求比實盤更嚴格。" |
| Frozen Stage | "Frozen is a deployment-stage quarantine. It is not the same thing as retiring the artifact." | "凍結是部署階段的隔離狀態，不等於工件退役。" |
| Binding | "A binding authorizes a persona for a pool. It does not create a runtime by itself." | "綁定只授權人格使用資金池，不會自行建立執行環境。" |
| Binding Summary | "Bound / Not Bound is a coarse read-model summary. Open details for the full governance status." | "已綁定 / 未綁定 是粗粒度讀模型摘要；完整治理狀態請查看詳細資訊。" |
| Approval Decision | "ApprovalDecision is the formal approval authority. A registry entry's old approver field is only a compatibility hint." | "ApprovalDecision 才是正式核准權威；registry 舊的 approver 欄位只是相容提示。" |
| Rollback | "Rollback changes runtime state or binding lineage. It does not rewrite the artifact's governance history." | "回退只改變執行狀態或 binding 血緣，不會重寫工件的治理歷史。" |
| Evolution Cooldown | "Cooldown prevents rapid back-to-back structural changes to the same target." | "冷卻期用來避免對同一目標連續快速做結構性變更。" |

---

## Appendix A: Document Reference Map

| Glossary Section | Canonical Sources |
|---|---|
| Core System Terms | `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `services/control-plane/specs/contract.md`, `services/registry/contract.md`, `OPERATOR_ACCEPTANCE_MATRIX.md` |
| Lifecycle and State Terms | `TARGET_ARCHITECTURE.md`, `PERSONA_RUNTIME_MODEL.md`, `PAPER_CANARY_LIVE_POLICY.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `services/registry/contract.md`, `services/registry/promotion/README.md` |
| Binding and Deployment Terms | `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `services/control-plane/governance/capital_pool.contract.md`, `services/control-plane/governance/deployment_plan.contract.md`, `services/execution/runtime-manager/contract.md` |
| Governance and Evolution Terms | `services/control-plane/governance/contract.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `services/control-plane/governance/evolution_decision.contract.md`, `services/control-plane/permissions/contract.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` |
| Research and Learning Terms | `services/research/ingest/INGESTION_WORKFLOW.md`, `services/research/grok_source_catalog.md`, `services/research/replication/GATE_CONTRACT.md`, `services/evaluation/contracts/README.md`, `services/evaluation/optimizers/contract.md`, `services/learning/imitation/README.md`, `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md`, `services/registry/experiments/README.md` |
| Feedback and Telemetry Terms | `services/feedback/schema/contract.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `services/incident/contract.md` |
| Event and Messaging Terms | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` |
| Data Plane Terms | `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`, `MARKET_CALENDAR_AND_SESSION_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`, `DATASET_VERSION_AND_REPLAY_POLICY.md`, `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` |

## Appendix B: Translation Notes

- "Paper" uses "模擬盤" rather than a literal translation because it matches trading-industry usage.
- "Canary" stays "金絲雀盤" because it is already an established deployment term.
- "Artifact" stays "工件" and should be understood as the product-facing name for the canonical governed artifact.
- "Binding" stays "綁定" to emphasize authorization / association rather than deployment.
- "Promotion" uses "升版" to distinguish governed advancement from a generic increase.
- "Rollback" uses "回退" to distinguish operational mitigation from database rollback semantics.
- "Frozen" is translated as "凍結中" for deployment-stage quarantine and should not be silently replaced with "paused" on summary screens.
- "Retired" uses "退役" for canonical lifecycle wording; summary chips may use "停用" only when the UI clearly refers to a user-facing deactivation label.
