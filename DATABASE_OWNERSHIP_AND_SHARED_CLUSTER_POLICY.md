# DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY

Last updated: 2026-04-09
Status: canonical database ownership and cluster policy for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: shared PostgreSQL cluster policy, schema ownership, write boundaries, and cross-service read/write rules
Conflict rule: this document defines data persistence ownership; it overrides general microservice isolation mentions in planning docs

## 1. 目的

本文件定義 Pantheon 在 PostgreSQL 上的共享叢集策略、schema ownership、寫入邊界與跨服務讀寫規則。

本文件要解決的問題：

- 多個服務是否共享同一個 Postgres cluster
- 是否採 DB-per-service
- 多個服務碰同一 schema 時，誰有寫權
- shared schema 是否違反微服務 isolation
- v1 先求什麼、捨什麼

---

## 2. 結論摘要

### 2.1 v1 架構
Pantheon v1 採：

> **shared Postgres cluster + strict write ownership + API/read-only sharing**

### 2.2 不採 DB-per-service
v1 不強制每個服務獨立 DB instance。  
原因：
- domain 關聯性強
- 需要高效 lineage / governance / reconciliation join
- 團隊初期運維成本較低

### 2.3 不允許共享寫權限
即使共用 cluster / schema namespace，**每個主資料表仍必須有唯一 write owner**。

### 2.4 非 owner 只能：
- read
- 讀 replica / read role
- 或透過 owner service API 寫

---

## 3. ownership 原則

## 3.1 單表單 owner
每個核心表只能有一個 service 為 write owner。

## 3.2 cross-domain read allowed
為了降低 v1 複雜度，允許 cross-service 讀共享 cluster，但：
- 必須走 read role
- 不可寫別人主表
- 不能依賴未承諾的隱含欄位

## 3.3 owner service API 優先
若是會改變狀態或需要 invariant 檢查：
- 應優先呼叫 owner service API
- 不直接碰資料表

---

## 4. 建議 ownership 映射

| Domain / Schema | Write Owner |
|---|---|
| iam | iam-svc |
| persona | persona-svc |
| consult | consultation-svc |
| registry.source / strategy / alpha / experiment / artifact | registry-core-svc |
| governance | governance-svc / promotion-svc |
| capital | capital-pool-svc |
| runtime | runtime-manager-svc |
| telemetry | telemetry-svc |
| incident / postmortem / evolution | telemetry-evolution-svc |

---

## 5. 典型案例

## 5.1 registry-core 與 research-orchestrator
- registry-core：寫 `StrategySpec` / `ArtifactRegistry`
- research-orchestrator：寫 experiment task/run domain
- 若 research-orchestrator 需要推動 strategy state change，應呼叫 registry-core API，而不是直接 update registry 主表

## 5.2 runtime-manager 與 governance
- governance 決定 deploy plan / approval
- runtime-manager 寫 `RuntimeBinding` / `RuntimeStatus`
- governance 不直接 update runtime tables

## 5.3 telemetry 與 runtime-manager
- runtime-manager 發 runtime actions
- telemetry-svc 寫 telemetry canonical tables
- runtime-manager 不直接插 telemetry canonical event_raw

---

## 6. schema 共享原則

### 6.1 可共享 cluster
可以

### 6.2 可共享 schema namespace
可以，但僅作邏輯 grouping，不代表共享寫權

### 6.3 可共享 migration repository
可以，但 migration must preserve ownership documentation

### 6.4 不可共享 write responsibility
不可

---

## 7. 權限模型

建議至少分：

- owner role
- read role
- migration/admin role

每個 service 帳號僅拿：
- 自己 schema/table 的寫權
- 需要的 read 權
- 不拿跨域主表寫權

---

## 8. 與 lineage / BFF 的關係

shared cluster 的主要收益之一，是：
- lineage read model 可更容易跨域查詢
- BFF read model 可較容易組裝

但注意：
- 這只是 read convenience
- 不能演變成 everyone writes everything

---

## 9. v1 決策

1. 採 shared Postgres cluster
2. 不採 DB-per-service
3. 單表單 owner
4. 非 owner 只讀或走 owner API
5. 寫權限必須有明確 mapping
6. schema 共享不等於 ownership 共享

---

## 10. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下項目屬於後續 DB ownership 與 shared-cluster 細化，不是本文件目前生效的前置條件。

- 具體 GRANT / ROLE matrix
- migration ownership rules
- read replica strategy
- backup / restore per schema policy
