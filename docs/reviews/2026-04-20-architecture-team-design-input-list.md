# 2026-04-20 Architecture Team Design Input List

## 目的

這份文件只回答一件事：

哪些差異現在 **不能直接丟給 auto worker 實作**，而必須先由系統規畫 / 架構團隊補齊 canonical design input。

判斷原則：

- 若缺的是 canonical route / read model / lifecycle / authority / degradation / ownership decision，先進 architecture bucket
- 若 contract 已發布，只差 BFF route、service wiring、truth-hardening、tests、UI activation，進 implementation bucket

---

## 先講結論

現在真正需要架構團隊先補的，不是整個 Pantheon 高階藍圖。

高階藍圖大致已經存在。真正缺的是：

1. 全域 canonical conventions 文件化
2. 少數模組仍未鎖定 module-level contract
3. 少數跨 service ownership 還沒拍板
4. 少數文件與 code truth 漂移，必須先做 architecture ratification

---

## A. 全域 Canonical 規則

### A1. Global Canonical Conventions Pack

這包一定要由架構團隊補正式文件，不能只停留在 review 回覆。

需要補的內容：

- `module-level canonical contract != new deployable service`
- 共通 response envelope
- `allowedActions` 全域規則
- `meta.snapshot_at` 放置規則
- `meta.surfaces.*` 命名與語義
- lifecycle / state 命名規範
- list route pagination / cursor / ordering / filter naming 規範
- module readiness ladder

最少要產出的正式內容：

- 一份 global contract conventions 文件
- 一份 degradation dictionary
- 一份 readiness classification 文件

原因：

- 這 6 點是架構團隊已經明確要求補的共通層
- 目前它們還散落在 review 與 gap matrix 中，沒有收斂成 canonical doc

來源：

- `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`

---

## B. 需要 Ownership Decision 的項目

### B1. LIN-002 Lineage Ownership

這件事不能直接派 implementation worker，因為現在 lineage 真相分裂成三條：

- telemetry 內有高性能 lineage engine
- `services/lineage-read/` 是獨立 deploy service
- BFF lineage UI path 走自己的聚合 projection

需要架構團隊補的決策：

- 哪個 service 才是 canonical lineage read owner
- BFF lineage UI 應該對接哪條 canonical path
- `lineage-read` 是保留、包裝 telemetry engine，還是被 telemetry path 吸收

為什麼先要 design：

- 這不是單純 route 缺工
- 若 ownership 不先拍板，auto worker 可能會把錯的 path 做更深

關鍵程式碼：

- `services/telemetry/lineage_read/service.py`
- `services/telemetry/main.py`
- `services/lineage-read/main.py`
- `services/control-plane/bff/read_store.py`

---

### B2. Control Plane Persona Boundary

`services/control-plane/persona/main.py` 目前仍是 stub / deferred 狀態。

需要架構團隊補的決策：

- persona plane 的正式責任邊界
- upstream schema 何者為 locked truth
- 哪些 persona-facing capabilities 屬於 BFF composed read model
- 哪些必須是 persona service 自己擁有的 canonical contract

為什麼先要 design：

- 現在 service 自己就明寫 `system not ready`
- 這不是單靠實作者補 route 可以解的問題

關鍵程式碼：

- `services/control-plane/persona/main.py`

---

### B3. Control Plane Router Enforcement Ownership

`router` 目前仍把部分 enforcement defer 給 gateway，approval workflow 也還有 stub surrogate。

需要架構團隊補的決策：

- TTL enforcement owner
- rate-limit enforcement owner
- approval / routing authority 由 router 還是 gateway / other control surface 擁有
- local intent classifier 在 production 是否仍保留 fallback 身分

為什麼先要 design：

- 這是 system boundary 問題，不是單一函式修補

關鍵程式碼：

- `services/control-plane/router/main.py`
- `services/control-plane/router/contract.md`

---

## C. 仍缺 Module-Level Canonical Contract 的模組

### C1. RW-05 Artifact Compare

目前仍缺：

- artifact identity rules
- versioning semantics
- backend-owned compare contract
- compare response shape
- evidence rail contract

為什麼先要 architecture：

- 這個模組本質就是 canonical compare truth
- 沒鎖 contract 就不該讓前端或 BFF 自己猜 diff semantics

來源：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`
- `docs/bff/RW-05-artifact-compare.md`

---

### C2. CW-02 Debate Transcript

目前仍缺：

- append-only `TranscriptEvent` canonical schema
- ordering semantics
- actor labeling contract
- inline evidence-link semantics
- replay / transcript projection boundary

為什麼先要 architecture：

- transcript 一旦 schema 鎖錯，後面的 BFF、UI、replay 全部會跟著歪

來源：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`
- `docs/bff/CW-02-debate-transcript.md`

---

### C3. CW-04 Red-team Memo

目前仍缺：

- memo lifecycle
- publish / review semantics
- session-to-memo mapping
- governance handoff contract
- `allowedActions.canInitiateGovernanceReview`

為什麼先要 architecture：

- 這牽涉到 downstream governance handoff，不該讓 implementation lane 自行拼流程

來源：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`
- `docs/bff/CW-04-redteam-memo.md`

---

### C4. TW-02 Parameter Controls

目前仍缺：

- controls read contract
- patch semantics
- validation contract
- diff response shape
- invalid / rejected patch behavior

為什麼先要 architecture：

- controls patch 一旦 contract 沒鎖，preview、replay、commit/discard 都會不穩

來源：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`
- `docs/bff/TW-02-parameter-controls.md`

---

### C5. KW-05 Strategy Spec

目前仍缺：

- versioned strategy-spec browse contract
- detail projection
- compare projection
- citation / evidence contract
- version identity rules

為什麼先要 architecture：

- 這個模組的核心就是 versioned spec truth
- 先寫 UI 或 route 會把 compare semantics 做死

來源：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`
- `docs/bff/KW-05-strategy-spec.md`

---

## D. 需要 Architecture Ratification 的項目

### D1. KW-02 / KW-03 / KW-04

這三個模組現在最大的問題不是「完全沒設計」，而是：

- `docs/bff/*.md` 已存在
- `docs/lovable/PANTHEON_FRONTEND_SA.md` 把它們寫成 contract-ready
- 但 BFF knowledge overview 仍把它們視為 `not_ready`

所以現在需要架構團隊先確認：

- 這些 docs 是否已經升格為 canonical truth
- 若已升格，為什麼 BFF overview / backlog 還沒同步
- 若未升格，哪些內容仍只是 draft，不可派 production implementation

為什麼先要 architecture：

- 這是 readiness classification 問題
- 若不先 ratify，task board 會一直把同一模組同時列為 ready 與 not-ready

關鍵文件 / 程式碼：

- `docs/lovable/PANTHEON_FRONTEND_SA.md`
- `services/control-plane/bff/main.py`
- `WORKBENCH_DELIVERY_BACKLOG.md`

---

## 不是 Architecture Bucket 的項目

以下雖然還沒完成，但 **不應再回送架構團隊**：

- `EW-04`：contract 已發布，主要缺 BFF route 實作
- `RW-02`：contract 已發布，主要缺 BFF implementation
- `RW-04`：contract 已發布，主要缺 BFF implementation
- `CW-01`：contract 已發布，主要缺 BFF implementation
- `TW-01`：contract 已發布，主要缺 BFF implementation
- `TW-03`：contract 已發布，主要缺 BFF implementation
- `TW-04`：contract 已發布，主要缺 BFF implementation
- `RW-01` / `RW-03` / `CW-03` / `KW-01`：主要缺 truth-hardening / wiring，不是缺 abstract design
- `EW-05`：BFF route 與 command vocabulary 已 live，主要缺 UI / handoff activation

---

## 建議架構團隊交付包

對每個需要 architecture input 的項目，至少交付：

1. `docs/bff/<module>.md`
2. `docs/screens/<module>.md`，若該模組已足夠 screen-ready
3. `docs/examples/<module>.json`
4. readiness classification update
5. `WORKBENCH_DELIVERY_BACKLOG.md` 對應 row update

對全域規則，至少交付：

1. global envelope / naming conventions
2. degradation dictionary
3. readiness ladder
4. `allowedActions` global rule

---

## 一句話結論

現在真正要先回送架構團隊的，是 **global canonical conventions、cross-service ownership decisions、以及少數仍未鎖定 module-level contract 的模組**。

其他 contract-published 或 route-live 的項目，不該再卡在 architecture lane。
