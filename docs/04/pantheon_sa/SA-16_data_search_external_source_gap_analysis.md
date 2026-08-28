---
project: Pantheon
document_type: System Analysis Gap Report
batch: SA-16 to SA-20
language: zh-TW
assumption: >
  本批 SA 文件採用最新校正：目前實際在 VS Code 中被修改、用於 execution substrate 判讀的是 `ajoe734/Lean`；
  `ajoe734/lean-platform` 暫列為幾乎未動、歷史分支或待決 execution repo。
baseline: >
  以 Pantheon 總索引版系統分析文件為主準繩。該母文件定義 Pantheon 是多人格量化 operating system，
  由 Console / BFF / Shared Capability / Source Ingestion / Persona / Capital Pool / Knowledge & Registry /
  Consultation / Research / Policy Learning / Optimizer / Governance / Execution / Telemetry-Evolution 等 plane 組成，
  並要求 paper / canary / live、telemetry、reconciliation、postmortem、evolution 形成閉環。
---

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.





# SA-16 — Data / Search / External Source 差異分析

## 1. 本章目的

本章分析 Pantheon 在 **外部資料源、Data Gateway、Source Ingestion、Evidence Store、Search Gateway、OpenClaw 搜尋能力** 上的實作差異。

使用者先前已明確指出，外部資料不只包括 market data / fundamentals / macro，也包括：

```text
news data
social media
external alpha database
OpenClaw-connected LLM search capability
```

因此本章的分析重點不是「要不要多接幾個 vendor API」，而是：

```text
外部來源如何進入 Pantheon canonical truth？
如何帶 source / license / available_time / entitlement？
如何被 evidence store / search gateway / StrategySpec Seed Builder 消費？
如何避免 OpenClaw / LLM 直接越權查資料或觸碰 execution？
```

---

## 2. Blueprint Requirement

Pantheon 母文件中的 Source Ingestion Plane 包含：

```text
Paper Ingest
Repo Ingest
Internal Research Ingest
Source Normalizer
Source Registry
StrategySpec Seed Builder
```

Knowledge & Registry Plane 包含：

```text
Source Registry
Strategy Registry
Alpha Registry
Experiment Registry
Artifact Registry
Insight Bus / Research Notes
Evidence Store
Approval Registry
Model / Artifact Lineage
```

Shared Capability Plane 則應承接：

```text
Plugin Tools
Shared Skills Pack
Workflow Templates
Hooks / Cron / Background Jobs
Agent Router / Session Binder
```

其中 OpenClaw / LLM 主要在研究、控制、治理，不直接作 execution kernel。

因此外部資料源正確落點是：

```text
External Source
→ Data / Source Gateway
→ Source Normalizer
→ Source Registry
→ Evidence Store / Search Index
→ StrategySpec Seed Builder / Research / Review / Postmortem
```

而不是：

```text
External Source
→ OpenClaw free search
→ strategy / runtime direct action
```

---

## 3. 現況與演進摘要

### 3.1 架構訊號

目前架構定位：

```text
execute-plans (Active Canonical Frontend):
  - /management/data-sources Data Source Control Center
  - Agora Research Workbench / Evidence / Insight / StrategySpec UI surface
  - BFF client 連接 canonical management read/write routes

pantheon (Active Canonical Backend):
  - Source Ingestion Plane: connector definitions, source management store & commands, provider adapters, financial catalog
  - Search Plane: as-of/time filters, hybrid retriever, structured alpha rules
  - Research / Memory: reviewed memory writeback outbox & worker
  - OpenClaw: Phase 2 governed search client only, zero product write authority

Lean (Execution Submodule):
  - 標準 LEAN engine，專注 backtest / paper / live execution substrate
  - 不做 canonical external source management 權威

[Historical Legacy Reference]
front-ai-trading-system:
  - 舊版前端名稱，已全數由 execute-plans 取代 (歷史參考用)
```

### 3.2 主要判斷

目前狀態不是完全沒有資料概念，而是：

```text
research ingest / data-plane / evidence UI 已有局部骨架；
但完整的 Data Gateway / Search Gateway / Source Entitlement / External Alpha DB / News / Social canonical ingestion 尚未被證明落地。
```

---

## 4. 外部資料類別完整盤點

Pantheon 至少要支援以下來源族群。

| Source Family | Examples | 用途 | 優先級 | 是否應直接進 Lean |
|---|---|---|---|---|
| Reference / Master | security master, contract master, symbol map, calendars | 所有資料對齊與回放 | P0 | 否，應由 Pantheon canonical 層供應 |
| Market Data | OHLCV, trades, quotes, tick, L2 | backtest / live comparison / execution | P0 | live feed 可進 Lean，但 research truth 應 canonical |
| Corporate Actions | splits, dividends, mergers | PIT adjustment / backtest truth | P0 | 否 |
| Filings / Fundamentals | SEC, MOPS, financial statements, revenue | fundamental alpha / evidence | P0/P1 | 否 |
| Macro / Rates / Calendar | FRED, central bank, rates, CPI, market calendar | regime / risk overlay | P0/P1 | 否 |
| News | Benzinga, RSS, vendor news, company IR | event-driven alpha / evidence | P0/P1 | 否；execution alerts 可以由 Pantheon 推送 |
| Social Media | X, Reddit, Discord, Telegram, StockTwits | sentiment / anomaly / signal candidate | P1 | 否 |
| External Alpha DB | vendor factor DB, curated signals, alt-data panels | alpha candidate / factor source | P1 | 否 |
| Research Corpus | papers, GitHub repos, internal notes, PDFs | strategy ideation / RAG | P0/P1 | 否 |
| Broker / Execution Data | orders, fills, positions, cash, broker status | reconciliation / incident | P0 | Lean 產生，但 Pantheon canonical telemetry 接收 |
| Telemetry / Runtime Data | heartbeat, runtime health, latency | monitoring / incident | P0 | Lean 產生，Pantheon 接收 |
| Alternative Data | web traffic, job postings, app ranking, satellite | specialized alpha | P2 | 否 |

---

## 5. SourceRecord / EvidenceBundle 必要 contract

### 5.1 SourceRecord

```json
{
  "source_id": "src-...",
  "source_type": "news|social|filing|paper|repo|market_data|macro|broker|alpha_vendor",
  "provider": "provider-name",
  "provider_record_id": "...",
  "source_uri": "...",
  "title": "...",
  "authors_or_owner": [],
  "published_at": "RFC3339",
  "event_time": "RFC3339",
  "available_time": "RFC3339",
  "ingest_time": "RFC3339",
  "license_scope": "internal|public|vendor|restricted",
  "entitlement_tags": [],
  "trust_score": 0.0,
  "normalized_status": "pending|normalized|rejected|archived",
  "body_hash": "sha256:...",
  "metadata": {},
  "evidence_refs": []
}
```

### 5.2 EvidenceBundle

```json
{
  "evidence_id": "ev-...",
  "source_ids": [],
  "summary": "...",
  "claims": [],
  "citations": [],
  "available_time": "RFC3339",
  "license_scope": "...",
  "visibility_scope": {
    "workspaces": [],
    "personas": [],
    "roles": []
  },
  "lineage_refs": [],
  "created_by": "system|operator|persona",
  "created_at": "RFC3339"
}
```

### 5.3 關鍵規則

```text
available_time 是硬性欄位。
沒有 available_time 的來源不可用於 backtest / historical decision。
license_scope 是硬性欄位。
沒有 entitlement 的資料不可被 OpenClaw / persona 搜尋。
body_hash / checksum 是硬性欄位。
source → evidence → strategy → artifact 必須能追。
```

---

## 6. Data Gateway 差異分析

### 6.1 藍圖要求

Data Gateway 應是 Source Ingestion Plane 的 adapter/runtime 實作，不是 Pantheon 的新控制中樞。它只負責：

```text
connect
fetch
validate
normalize
version
persist
emit events
```

不負責：

```text
approve strategy
deploy runtime
grant persona authority
override risk
```

### 6.2 目前缺口

| Gap ID | Gap | Type | Severity |
|---|---|---|---|
| DATA-GAP-001 | 缺正式 `services/data-gateway/` bounded context | Structural | High |
| DATA-GAP-002 | 市場資料、新聞、社群、filings、macro、alpha DB 未統一 registry | Structural | High |
| DATA-GAP-003 | 來源資料未必有 PIT 欄位 | Contract | Critical |
| DATA-GAP-004 | Lean data feed 可能被誤認為 research canonical data | Boundary | High |
| DATA-GAP-005 | provider license / entitlement 未統一 | Governance | High |
| DATA-GAP-006 | source health / ingest lag / DLQ 未建立 | Operational | Medium |
| DATA-GAP-007 | Data Gateway 與 StrategySpec Seed Builder 未驗證 | Behavioral | High |

### 6.3 建議模組

```text
pantheon/services/data-gateway/
  connectors/
    market/
    news/
    filings/
    macro/
    social/
    alpha_vendor/
    broker/
  normalizers/
  validators/
  source_registry.py
  evidence_writer.py
  dataset_version_writer.py
  entitlement.py
  health.py
  dead_letter_queue.py
```

---

## 7. Search Gateway 差異分析

### 7.1 為什麼需要 Search Gateway

OpenClaw / LLM 應具備搜尋能力，但這個搜尋不能是 unrestricted internet search。正確路徑：

```text
OpenClaw / LLM
→ governed Search Gateway
→ ACL / entitlement / license / environment filter
→ hybrid retrieval
→ EvidenceBundle / citation pack
→ audit log
```

### 7.2 必要功能

```text
Hybrid search:
  full-text + vector + metadata filters

Mandatory filters:
  workspace_id
  persona_id
  role
  environment
  license_scope
  available_time
  source_type
  sensitivity
  capital_pool_scope

Output:
  EvidenceBundle
  citations
  source ids
  retrieval audit
```

### 7.3 差異

| Gap ID | Gap | Type | Severity |
|---|---|---|---|
| SEARCH-GAP-001 | Search Gateway 未驗證 | Structural | High |
| SEARCH-GAP-002 | Vector index / embedding store 未驗證 | Structural | Medium |
| SEARCH-GAP-003 | ACL-before-retrieval 未驗證 | Governance | Critical |
| SEARCH-GAP-004 | OpenClaw search tool 未接 EvidenceStore | Integration | High |
| SEARCH-GAP-005 | Citation pack / evidence bundle 未標準化 | Contract | Medium |
| SEARCH-GAP-006 | Search audit log 未驗證 | Audit | Medium |
| SEARCH-GAP-007 | social / news / alpha DB results 的 trust scoring 未定義 | Data Quality | Medium |

### 7.4 Search Query Contract

```json
{
  "query_id": "uuid",
  "query": "...",
  "actor_ref": "...",
  "persona_id": "...",
  "workspace_id": "...",
  "purpose": "research|review|postmortem|operator",
  "filters": {
    "source_type": [],
    "available_time_lte": "RFC3339",
    "license_scope": [],
    "asset_class": [],
    "strategy_id": null,
    "capital_pool_id": null
  },
  "top_k": 10,
  "trace_id": "uuid"
}
```

### 7.5 Search Result Contract

```json
{
  "query_id": "uuid",
  "results": [
    {
      "evidence_id": "ev-...",
      "source_id": "src-...",
      "title": "...",
      "snippet": "...",
      "score": 0.91,
      "available_time": "RFC3339",
      "license_scope": "...",
      "trust_score": 0.74,
      "citations": [],
      "metadata": {}
    }
  ],
  "audit_ref": "aud-..."
}
```

---

## 8. News Data 差異

### 8.1 Required

News data 應該被視為 evidence source，而非直接 trading signal。流程：

```text
news article
→ SourceRecord
→ EvidenceBundle
→ event / entity / asset mapping
→ StrategySpecSeed or risk flag
→ review / research / postmortem
```

### 8.2 Current Risk

如果 Benzinga / vendor news connector 存在於 Lean toolbox 或 execution repo，它仍不是 Pantheon canonical news ingestion。

### 8.3 Required fields

```text
publisher
published_at
available_time
symbols / entities
headline
body_hash
license_scope
event_tags
sentiment / classification metadata
source_url / vendor_id
```

### 8.4 Gap

```text
NEWS-GAP-001: news connector 可能存在於 execution/toolbox，但未 canonicalize。
NEWS-GAP-002: news not searchable through governed Search Gateway.
NEWS-GAP-003: news evidence not linked to StrategySpec / Incident / Postmortem.
```

---

## 9. Social Media 差異

### 9.1 Required

Social media source 應更嚴格：

```text
platform
author_id_hash
post_id
thread_id
published_at
available_time
engagement metrics
language
entity mapping
spam / bot / trust score
moderation flag
license / platform policy
```

### 9.2 Special Rules

```text
social signals must not directly trigger runtime actions.
social results require trust / noise filter.
social source must be clearly marked lower confidence unless curated.
```

### 9.3 Gap

```text
SOCIAL-GAP-001: social connector 未驗證。
SOCIAL-GAP-002: spam / trust scoring 未定義。
SOCIAL-GAP-003: social evidence-to-alpha pipeline 未定義。
SOCIAL-GAP-004: platform policy / retention / deletion policy 未定義。
```

---

## 10. External Alpha DB 差異

### 10.1 Required

External alpha DB 必須以 vendor entitlement 方式接入：

```text
alpha_vendor_id
signal_id
signal_version
field_schema
universe
available_time
license_scope
allowed_use
PIT semantics
vendor confidence / provenance
```

### 10.2 不能做的事

```text
不能把 vendor signal 直接餵 Lean 下單。
不能跳過 research / experiment / review。
不能忽略 survivorship / lookahead / PIT。
```

### 10.3 Gap

```text
ALPHA-GAP-001: external alpha vendor registry 未定義。
ALPHA-GAP-002: signal schema 未定義。
ALPHA-GAP-003: license / entitlement / PIT 未定義。
ALPHA-GAP-004: vendor signal → ExperimentRun / CandidateArtifact pipeline 未定義。
```

---

## 11. Broker / Execution Data as Source

### 11.1 Required

Broker / execution data 是 Source / Telemetry 的橋：

```text
orders
fills
positions
fees
cash
margin
borrow
broker status
reject reason
latency
disconnect
```

它由 Lean runtime 產生，但 canonical truth 應由 Pantheon telemetry / reconciliation store 接收。

### 11.2 Gap

```text
EXEC-DATA-GAP-001: Lean exporter 未驗證。
EXEC-DATA-GAP-002: broker event 是否轉 TelemetryEvent 未驗證。
EXEC-DATA-GAP-003: positions / fills 是否可 reconcile 未驗證。
EXEC-DATA-GAP-004: broker account_ref / capital_pool_id mapping 未驗證。
```

---

## 12. Data Source Priority Roadmap

### P0 — Backbone

```text
SourceRecord schema
EvidenceBundle schema
DataGateway skeleton
SearchGateway skeleton
PIT fields
license / entitlement
source health
OpenClaw governed search
```

### P0 — Core Sources

```text
market bars / calendars
filings / fundamentals
macro / rates
broker / execution telemetry
internal notes / PDFs / repos
news basic connector
```

### P1 — Enhancement

```text
social media
external alpha DB
options / futures / chain data
news sentiment / event classification
research paper ranking
```

### P2 — Advanced Alternative Data

```text
web traffic
job postings
app ranking
satellite
supply chain
credit-card style panels
```

---

## 13. Required Acceptance Tests

```text
test_source_record_requires_available_time
test_source_record_requires_license_scope
test_evidence_bundle_links_to_source
test_search_filters_acl_before_ranking
test_openclaw_search_returns_evidence_bundle_only
test_news_ingest_creates_source_and_evidence
test_social_ingest_requires_trust_score
test_alpha_vendor_signal_requires_entitlement
test_market_data_version_is_replayable
test_broker_event_links_to_runtime_binding
```

### 13.1 P1-SOURCE-001 implementation update — 2026-05-01

P1-SOURCE-001 將 bounded source-ingest baseline 擴充到 news / social / alpha DB connector family：

```text
services/source_ingestion/external_sources.py
  - validates news, social, and alpha_db connector policy
  - requires connector-level entitlement_tags or entitlement_ref
  - rejects direct Lean / broker / runtime / execution targets
  - normalizes SourceRecord metadata for license_scope, access_scope, entitlement_tags,
    event_time, available_time, PIT validation, content_hash/body_hash, and governance sink

services/knowledge/evidence/EvidenceBundle
  - now preserves bundle-level available_time
  - now preserves bundle-level entitlement_tags
```

Implemented source-family rules:

```text
News:
  publisher, published_at/event_time, available_time, source URI, license, ACL, entitlement

Social:
  platform, author_id_hash, post_id, platform_policy_ref, trust_score, available_time,
  license, ACL, entitlement

Alpha DB:
  alpha_vendor_id, signal_id, signal_version, field_schema, universe, as_of_time/as_of_date,
  available_time, allowed_use, license, ACL, entitlement
```

Boundary remains unchanged:

```text
news / social / alpha_db
→ SourceRecord
→ EvidenceItem / EvidenceBundle
→ governed search / research / review

No connector may target Lean, broker, runtime-manager, order router, or execution directly.
```

Focused verification:

```bash
python3 -m pytest services/source_ingestion -q
python3 -m pytest services/knowledge/evidence services/search/tests/test_governed_search.py services/search/test_index_pipeline.py services/search/tests/test_retrieval_rank_filter_cutoff_contract.py -q
```

### 13.2 SRCM-P1 Full Delivery and Hosted Acceptance — 2026-08-24

在 `SRCM-PHASE1-20260824`（SD-SRCM-01 至 SD-SRCM-08）中，Pantheon 完整落實了外部資料源生命週期管理：

1. **Source Connector Definitions & Schema Authority**:
   - `services/source_ingestion/connector_definitions.py` 定義 deployed adapter allowlist。
   - `source_management_store.py` 提供 Postgres/JSONL 雙後端 desired/observed 狀態儲存。
   - 支援 `create_disabled`、`validate`、`canary`、`enable`、`disable`、`degrade`、`resume`、`change_schedule`、`replace`、`retire` 等生命週期命令與 durable receipts。

2. **BFF Management Facade & UI Control Center**:
   - `services/control-plane/bff/console_gap/datasources.py` 提供 `/bff/management/data-sources/v2` 與受控寫入 endpoints（需 Operator/Admin 權限與 idempotency key）。
   - `execute-plans` 前端 `/management/data-sources` 擴充為完整 Control Center。

3. **Governed Search & Structured Alpha Rules**:
   - `services/search/filters.py` 與 `services/search/gateway.py` 實作 available-time as-of cutoff 與授權過濾。
   - `services/search/structured_alpha.py` 支援 AST 驗證、PIT、survivorship 及 query snapshot。

4. **Reviewed Research Memory Lineage**:
   - `services/research/research_memory_outbox.py` 與 `services/memory/learn_feedback_writeback.py` 保證只有經審查的 research findings 能寫入 persona / institutional memory。

5. **Canonical Terminology**:
   - `target`: 設計/意圖能力
   - `supported`: 已部署 adapter 定義存在
   - `configured`: 資料源實例存在於持久化 store
   - `credentialed`: secret ref 成功解析所需權限
   - `validated`: 配置與 policy 驗證通過
   - `canary-passed`: 有界 downstream 驗證通過（無下單/資金路徑）
   - `enabled`: 操作者授權之期望執行狀態
   - `fresh`: 觀察水位在 declared SLA 內
   - `live`: 當前環境經 exact FE/BFF/source 部署驗證通過

6. **Hosted Acceptance Status**:
   - `scripts/verify_external_source_management_acceptance.py` 嚴格把關 10 個 hosted journeys、實際 Playwright/HAR、非 placeholder 截圖、負向安全控制、exact-pair identity drift rejection、migration idempotency、read-only rollback 與 zero secret leak。
   - 2026-08-28 Human/Ops 授權以 functional closure 優先：三筆 DLQ 經兩次 deduplicated execution replay，一次 exclusive TWSE/TPEx manual pull 成功（`ran=1`, `failed=0`, `excluded=92`），最後 pending/unresolved DLQ 都是 0。Normal Source 已恢復 `egress=deny`、`reconcile_only`、`MAX_TICKS=0`、restart `unless-stopped`；這是持續內部 reconcile owner，不是 recurring provider pull。
   - 公開 dev pair 觀察為 execute-plans `c230fc76` 與 BFF `dcb14231`，FE manifest 無 BFF drift、frontend 保持 `VITE_BFF_REAL_WRITES=false`，未授權與錯誤登入皆回 `401 AUTH_REQUIRED`。`--functional-only` 可驗證此範圍；十個 journey 的獨立 browser/HAR、authenticated Source definition identity、credentialed journey、strict BFF auth 與 full exact-pair authorization 都明列為 non-blocking follow-up，完整版 verifier 仍 fail-closed，故不得把 functional closure 寫成完整 hosted/live 證明。
   - OpenClaw 維持 Phase 2，不具備 product write 權限。

---

## 14. 本章結論

目前 Pantheon 外部資料整合的主要問題不是缺少單一 API vendor，而是缺少一個完整的 canonical source/search/evidence backbone。

SA 判斷：

> News、social media、external alpha DB、OpenClaw search 都應該接回 Pantheon 的 Source Registry / Evidence Store / Search Gateway，而不是散落在 Lean、front、OpenClaw 或個別 research script。Data Gateway 只能是 Source Ingestion 的 adapter/runtime，不可變成新的控制中樞；OpenClaw search 必須受 ACL、license、available_time 與 evidence bundle 約束。
