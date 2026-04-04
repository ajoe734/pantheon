# 本文件已退役 (Deprecated)
所有協作規範已收斂至 `AI_COLLABORATION_GUIDE.md`。
當前任務看板請讀取 `current-work.md`。
機器狀態請見 `ai-status.json`。

---

## 3. PR 描述模板 (可直接複製)

```markdown
### [OpenClaw] 任務更新
**負責 LLM**: [Claude / Gemini / Codex]
**所屬 Phase**: [1 / 2 / 3 / 4]
**更新元件**: `services/path/to/component`

**變更內容**:
- [x] 完成了 XXX 功能實作
- [x] 已同步更新 PROGRESS.md 的 History Log

**待後續 LLM 配合事項**:
- @Gemini 請確認 Redis 連接配置
- @Codex 請確認 Signal Schema 是否相容
```

---

## 4. 工作流規範
1.  **讀取**: 每次啟動前讀取 `COLLAB.md` 與 `PROGRESS.md`。
2.  **執行**: 只修改自己責任範圍內的目錄。
3.  **記錄**: 完工後將 `⬜` 改為 `✅`，並在 `PROGRESS.md` 的 `History Log` 底部**追加 (Append)** 記錄。
4.  **溝通**: 若有阻塞 (Blocker)，立即填寫在 `PROGRESS.md` 的 Blocker 表格。

> 在這些問題被確認前，所有 LLM 應使用環境變數佔位（`${BROKER}` 等），不要硬編碼。

---

## 2. 工作分配

### 原則
- 每個 LLM 負責的 Phase 是**主要負責人**，其他 LLM 可審查但不主動修改。
- 介面合約（Interface Contract）一旦確定，**不得在未通知其他 LLM 的情況下更改**。
- 所有產出放在對應的 `services/<name>/` 或 `infra/` 目錄下。

---

### Codex 負責 — Phase 1：基礎設施 + Channels

**目標**：讓本地環境和 GCP 基礎能跑起來，Channels 接通。

| 元件 | 輸出路徑 | 說明 |
|------|---------|------|
| Dockerfile（所有服務通用模板） | `docker/base/` | Python 3.11-slim base image |
| docker-compose.yml（完整本地環境） | `docker-compose.yml` | 包含所有服務 |
| Terraform — GCP 網路 / IAM / Secret Manager | `infra/terraform/base/` | VPC、SA、基本 IAM |
| Terraform — Artifact Registry / GKE cluster | `infra/terraform/gke/` | |
| Terraform — GCS buckets（Signal Store / Feature Store） | `infra/terraform/storage/` | |
| Terraform — Pub/Sub topics | `infra/terraform/pubsub/` | |
| GitHub Actions — CI（lint + test） | `.github/workflows/ci.yml` | |
| GitHub Actions — CD（build → push → deploy） | `.github/workflows/deploy.yml` | |
| Telegram bot service | `services/channels/telegram/` | |
| Discord bot service | `services/channels/discord/` | |
| Web API（FastAPI） | `services/channels/web/` | REST + WebSocket |
| Operator Console（簡單 HTML/React） | `services/channels/console/` | |
| Signal Store 讀寫介面（GCS + Redis） | `services/signal-store/` | 只實作 interface，不含業務邏輯 |

**依賴**：不依賴其他 LLM，可第一個開始。
**產出介面**：
- `SignalStoreClient`：`write_signal(strategy_id, signal: dict)` / `read_signal(strategy_id) -> dict`
- Pub/Sub topic 名稱規範（寫入 `PROGRESS.md` > Interfaces 區塊）
- 所有 GCP 資源命名規則（寫入 `PROGRESS.md`）

---

### Gemini 負責 — Phase 2：Research Plane

**目標**：各研究工具容器化，能產出信號並寫入 Signal Store。

**前置條件**：等待 Codex 完成 `SignalStoreClient` interface 定義。

| 元件 | 輸出路徑 | 說明 |
|------|---------|------|
| Market Data ingestion pipeline | `services/research/data-ingestion/` | 支援 Yahoo Finance / Polygon（可擴充）|
| Feature Store（讀寫 GCS Parquet） | `services/research/feature-store/` | |
| Qlib worker service | `services/research/qlib-worker/` | 包含 Dockerfile、rolling backtest、signal 輸出 |
| vectorbt worker service | `services/research/vectorbt-worker/` | param sweep、signal sanity check |
| FinRL worker service | `services/research/finrl-worker/` | RL train/test/trade pipeline |
| Experiment Store（Vertex AI MLflow wrapper） | `services/research/experiment-store/` | 統一 log metrics / artifacts |
| Cron Job 定義（Cloud Scheduler + Pub/Sub） | `infra/terraform/scheduler/` | 定時觸發 ingestion / retrain |

**依賴**：
- 需要 Codex 的 `SignalStoreClient` interface（`services/signal-store/client.py`）
- 需要 Codex 的 GCS bucket 名稱（從 `PROGRESS.md` 讀取）

**產出介面**：
- 每個 worker 暴露統一 HTTP endpoint：`POST /run { strategy_id, params } → { run_id }`
- 信號格式（JSON schema）寫入 `PROGRESS.md` > Interfaces 區塊

---

### Claude 負責 — Phase 3 & 4：Execution Plane + Control Plane

**目標**：LEAN 跑起來能消費信號，Persona Agent 能接受指令並協調研究與執行。

**前置條件**：等待 Codex Phase 1 完成（Signal Store），Gemini Phase 2 完成（信號格式確定）。

**Phase 3 — Execution Plane**

| 元件 | 輸出路徑 | 說明 |
|------|---------|------|
| LEAN Runtime 容器封裝 | `services/execution/lean-runtime/` | 讀取 Signal Store 驅動 LEAN alpha |
| Capital Pool / Sleeve 管理 | `services/execution/capital-pool/` | 帳戶 / 子帳戶 / 限額 |
| Broker plugin 設定層 | `services/execution/broker-config/` | 依券商決定載入哪個 LEAN plugin |
| Monitoring service | `services/execution/monitoring/` | 讀 LEAN 倉位/委託/PnL → Pub/Sub → Cloud Monitoring |

**Phase 4 — Control Plane**

| 元件 | 輸出路徑 | 說明 |
|------|---------|------|
| Multi-agent Router | `services/control-plane/router/` | persona → agent dispatch |
| Persona Agent | `services/control-plane/persona/` | mandate / style / memory / policy |
| Skills registry | `services/control-plane/skills/` | how-to 文件 + skill loader |
| Plugin Agent Tools（typed tool layer） | `services/control-plane/tools/` | 呼叫 Qlib/vectorbt/FinRL/QuantLib worker |
| Alpha / Strategy Registry（Firestore） | `services/control-plane/strategy-registry/` | strategy spec + template + score |
| Governance（approvals / audit / secrets refs） | `services/control-plane/governance/` | |

**產出介面**：
- `AgentRouter`：接受 channel message，回傳 agent response
- Monitoring 事件格式（寫入 `PROGRESS.md`）

---

## 3. Phase 依賴圖

```
Phase 1 (Codex)
  ├── docker-compose ✓（Codex 完成即可本地跑）
  ├── Terraform base ✓
  ├── SignalStoreClient interface ✓
  └── Channels ✓
        ↓
Phase 2 (Gemini)  ←── 需要 SignalStoreClient interface
  ├── Data ingestion ✓
  ├── Feature Store ✓
  ├── Qlib/vectorbt/FinRL workers ✓
  └── Experiment Store ✓
        ↓
Phase 3 (Claude)  ←── 需要信號格式 + Signal Store
  ├── LEAN Runtime wrapper ✓
  ├── Capital Pool ✓
  └── Monitoring ✓
        ↓
Phase 4 (Claude)  ←── 需要所有 worker endpoints
  ├── Router ✓
  ├── Persona Agent ✓
  ├── Tools ✓
  └── Governance ✓
        ↓
Phase 5：整合測試（三方共同）
  └── end-to-end smoke test
```

---

## 4. 目錄結構（最終）

```
openclaw/
├── COLLAB.md               ← 本文件（不要修改）
├── PROGRESS.md             ← 進度 + 歷史（每次工作後更新）
├── .github/
│   └── workflows/
│       ├── ci.yml          (Codex)
│       └── deploy.yml      (Codex)
├── services/
│   ├── channels/           (Codex)
│   │   ├── telegram/
│   │   ├── discord/
│   │   └── web/
│   ├── signal-store/       (Codex)
│   ├── research/           (Gemini)
│   │   ├── data-ingestion/
│   │   ├── feature-store/
│   │   ├── qlib-worker/
│   │   ├── vectorbt-worker/
│   │   ├── finrl-worker/
│   │   └── experiment-store/
│   ├── execution/          (Claude)
│   │   ├── lean-runtime/
│   │   ├── capital-pool/
│   │   ├── broker-config/
│   │   └── monitoring/
│   └── control-plane/      (Claude)
│       ├── router/
│       ├── persona/
│       ├── skills/
│       ├── tools/
│       ├── strategy-registry/
│       └── governance/
├── infra/
│   └── terraform/          (Codex)
│       ├── base/
│       ├── gke/
│       ├── storage/
│       ├── pubsub/
│       └── scheduler/      (Gemini 定義 cron spec，Codex 實作 Terraform)
├── docker/
│   └── base/               (Codex)
├── docker-compose.yml      (Codex，所有服務)
└── Makefile                (Codex)
```

---

## 5. 共用規範（所有 LLM 必須遵守）

### 環境變數命名
```
GCP_PROJECT_ID
GCP_REGION
BROKER_TYPE          # alpaca | ibkr | paper
LLM_BACKEND          # anthropic | openai | ollama
DATA_SOURCE          # yfinance | polygon | custom
GCS_BUCKET_SIGNALS
GCS_BUCKET_FEATURES
PUBSUB_TOPIC_SIGNALS
PUBSUB_TOPIC_MONITORING
FIRESTORE_COLLECTION_STRATEGIES
```

### Docker image 命名
```
{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT_ID}/openclaw/{service-name}:{git-sha}
```

### Pub/Sub topic 命名
```
openclaw.signals.{strategy_id}
openclaw.monitoring.{account_id}
openclaw.commands.{agent_id}
```

### GCS bucket 命名
```
{GCP_PROJECT_ID}-openclaw-signals
{GCP_PROJECT_ID}-openclaw-features
{GCP_PROJECT_ID}-openclaw-artifacts
```

### API 服務埠號（本地）
| 服務 | 埠號 |
|------|------|
| Web channel | 8000 |
| Router | 8001 |
| Persona Agent | 8002 |
| Qlib worker | 8010 |
| vectorbt worker | 8011 |
| FinRL worker | 8012 |
| QuantLib worker | 8013 |
| Monitoring | 8020 |
| LEAN (gRPC) | 50051 |

---

## 6. 如何更新進度

每次工作結束後，更新 `PROGRESS.md`：
1. 將完成的元件改為 `✅`
2. 如有新的 interface 定義，加入 **Interfaces** 區塊
3. 在 **History Log** 底部 append 一條記錄（不要修改既有記錄）
4. 如有 blocker，加入 **Blockers** 區塊

格式見 `PROGRESS.md`。
