# BFF_HA_AND_CONTROL_PLANE_RESILIENCE

Last updated: 2026-04-29
Status: canonical BFF high availability and control plane resilience policy for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: BFF high availability, control-plane isolation from execution, degradation strategies, and secondary operator control paths
Conflict rule: this document defines control-plane resilience; it overrides general HA mentions in planning docs

## 0. 2026-04-29 scope disposition

The multi-replica plus load-balancer BFF production topology is explicitly deferred
and must not be materialized as current execution work.

Reason: the operator frontend is expected to have low concurrent human usage, so a
dedicated HA topology for the UI aggregation layer is not worth pulling into the
current service-layer implementation wave. The current single-VM baseline may run
one `operator-bff` replica as long as the non-BFF runtime/control safety paths
remain reachable and active runtimes do not depend on BFF availability.

Re-entry gate: reopen BFF HA topology only if operator concurrency, availability
SLOs, external customer access, or audit requirements make BFF outage a material
business or safety risk.

## 1. 目的

本文件定義 Pantheon BFF 作為唯一 frontend aggregation point 時的高可用與降級策略。

目標是回答：

- BFF 是否是單點
- BFF 掛掉時會不會連 live runtime 一起死
- 需要什麼 HA 與 fallback
- 什麼算可接受的 control-plane risk

---

## 2. 結論摘要

### 2.1 BFF 是唯一 aggregation point，但不是單 instance
Pantheon 可接受：
- BFF 作為唯一 frontend aggregation point

Pantheon 不可接受：
- 單機單實例 BFF

### 2.2 BFF 掛掉不應影響 active runtime
BFF 屬 control plane / UI plane。
其故障可影響：
- Console
- Workbench
- operator interaction

其故障不應影響：
- 已 active 的 paper/canary/live runtime
- 已啟動的 runtime-manager 內部控制流
- broker connectivity
- runtime safety path

---

## 3. HA 原則

## 3.1 Stateless
BFF 必須盡量 stateless。  
不得把 canonical state 放在本機記憶體。

## 3.2 Multi-replica
至少 2 replicas。

## 3.3 LB 前置
必須放在 load balancer 後。

## 3.4 Shared backing store
session / notification cursor / cache 若需要共享，必須外置。

---

## 4. BFF 責任邊界

BFF 負責：
- auth / RBAC facade
- read model composition
- command facade
- realtime feed to UI
- view model aggregation

BFF 不負責：
- canonical domain writes（除 façade）
- long-running workflows
- runtime control truth
- deployment state truth
- telemetry truth

---

## 5. 降級策略

## 5.1 BFF partial degradation
若部分 downstream service 不可用：
- UI 顯示 degraded panel
- 只禁用受影響工作台
- 其他工作台可繼續服務
- degraded 狀態必須由 backend / BFF 明確標示來源與可信度，不能由 UI 自行發明 snapshot/default fallback 當正常資料
- BFF 正常整合路徑不得以本地 seed、snapshot、或隱性 localhost backend 預設假裝 backend 已就緒
- command-submission path 也必須指向明確配置的 backend API；不得以環境別名或隱性 fallback 假裝 governance/control backend 已可用

## 5.2 BFF total outage
若 BFF 全部不可用：
- UI 無法操作
- 但 runtime-manager / telemetry / kill-switch 不得受影響
- operator 可用後備管理接口（CLI / admin API）

## 5.3 consultation / workbench degradation
consultation、trainer preview、knowledge search 可降級為：
- async
- delayed
- read-only
- unavailable banner

但不得影響 emergency control chain。

---

## 6. 後備控制路徑

Pantheon 必須保留非 BFF 路徑給高權限 operator：

- admin CLI
- control-plane internal API
- runtime-manager protected admin endpoint

用途：
- pause
- rollback
- kill switch
- health diagnostics

此路徑需：
- 強 RBAC
- audit
- 非一般 researcher 可用

---

## 7. 監控

BFF 自身必須輸出：
- request rate
- error rate
- downstream dependency error rate
- render/viewmodel latency
- SSE / stream disconnect rate
- auth error rate

並進入第四包 telemetry / incident。

---

## 8. v1 決策

1. BFF 可作唯一 frontend aggregation point
2. BFF 不得單實例
3. BFF 故障屬 control-plane 風險，不得影響 active runtimes
4. 必須有 operator 後備控制路徑
5. BFF 不可成為 kill-switch 唯一路徑
6. degraded mode 必須能局部失效，而非全站一起死

---

## 9. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下項目屬於後續 HA / degraded-mode 細化，不是本文件目前生效的前置條件。

- BFF deployment topology
- cache strategy
- SSE / websocket fallback policy
- admin CLI / protected internal API spec
