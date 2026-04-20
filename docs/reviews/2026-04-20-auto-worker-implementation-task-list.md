# 2026-04-20 Auto Worker Implementation Task List

## 目的

這份文件列出目前 **不必再等系統規畫團隊補高階設計**，而可以直接切成 auto worker implementation task 的項目。

判斷原則：

- contract 已發布
- 或 BFF route 已 live
- 或差距只剩 implementation / wiring / tests / doc rebaseline

---

## 先講結論

現在最適合直接派工的，不是再寫抽象藍圖，而是：

1. BFF route implementation
2. local snapshot / example payload -> service-owned truth 的 wiring
3. live route 對應的 UI / handoff activation
4. backlog / SA / code truth rebaseline
5. missing tests 與技術債 cleanup

---

## A. 直接可派的 BFF Implementation Tasks

### A1. EW-04-BFF-IMPLEMENT-001

目標：

實作 `GET /api/v1/lineage/inspiration/{artifact_id}`，把已發布的 EW-04 contract 落到 BFF。

原因：

- `EW-04` contract 已發布
- 目前缺的是 route 落地，不是 abstract design

主要產出：

- BFF route
- read-store wiring
- route tests
- backlog / handoff 狀態同步

參考：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/bff` 對應 EW-04 contract
- `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md`

---

### A2. RW-02-BFF-IMPLEMENT-001

目標：

實作 Research Search route 與 index adapter wiring。

原因：

- `RW-02` contract 已發布
- 目前缺 live route / adapter implementation

主要產出：

- search route
- query/filter/pagination wiring
- route tests
- readiness update

參考：

- `docs/bff/RW-02-search.md`
- `docs/screens/RW-02-search.md`
- `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md`

---

### A3. RW-04-BFF-IMPLEMENT-001

目標：

實作 experiment launch / history / detail / cancel route family。

原因：

- `RW-04` contract 已發布
- backlog 也明確寫的是 implementation pending

主要產出：

- launch route
- history/detail routes
- cancel route
- state-machine-aligned tests

參考：

- `docs/bff/RW-04-experiment-launch.md`

---

### A4. CW-01-BFF-IMPLEMENT-001

目標：

把 Consult Request 的 create / list / detail / cancel routes 做成 live BFF truth。

原因：

- `CW-01` contract、screen spec、handoff 都已存在
- 目前差距是 BFF implementation

主要產出：

- four-route family
- lifecycle wiring
- route tests
- workbench overview readiness update

參考：

- `docs/bff/CW-01-consult-request.md`
- `docs/screens/CW-01-consult-request.md`
- `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md`

---

### A5. TW-01-BFF-IMPLEMENT-001

目標：

把 teaching dialog 的 session create / list / detail / message routes 做成 live BFF truth。

原因：

- `TW-01` contract 已發布
- 目前缺 BFF implementation，不缺 abstract design

主要產出：

- route family
- lifecycle wiring
- dialog event tests

參考：

- `docs/bff/TW-01-teaching-dialog.md`
- `docs/screens/TW-01-teaching-dialog.md`
- `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md`

---

### A6. TW-03-BFF-IMPLEMENT-001

目標：

把 before/after preview route family 做成 live BFF truth。

原因：

- `TW-03` contract 已發布
- `preview_unavailable` degraded semantics 已定義

主要產出：

- preview route
- refresh / polling semantics
- degraded branch tests

參考：

- `docs/bff/TW-03-before-after-compare.md`
- `docs/screens/TW-03-before-after-compare.md`

---

### A7. TW-04-BFF-IMPLEMENT-001

目標：

把 teaching replay list / detail / commit / discard 相關 route family 做成 live truth。

原因：

- `TW-04` contract 已發布
- 目前差距是 BFF implementation

主要產出：

- replay routes
- authority wiring
- evidence-link wiring
- route tests

參考：

- `docs/bff/TW-04-teaching-replay.md`
- `docs/screens/TW-04-teaching-replay.md`

---

## B. 可直接派的 Truth-Hardening / Wiring Tasks

### B1. RW-01-TRUTH-HARDENING-001

目標：

把已 live 的 research ticket routes 從 local fallback 推進到 service-owned truth。

原因：

- route 已 live
- 測試已存在
- 真正差距是 read-store fallback / snapshot 依賴

主要產出：

- fallback usage audit
- service-owned read path
- regression tests

關鍵程式碼：

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`

---

### B2. RW-03-TRUTH-HARDENING-001

目標：

把已 live 的 analysis list/detail 路徑從 local fallback 推進到 service-owned truth。

原因：

- route 已 live
- contract 已發布
- 目前主要是 truth source 仍偏 local snapshot

---

### B3. CW-03-TRUTH-HARDENING-001

目標：

把 committee board / detail 路徑做成更完整的 service-owned truth，並補 readiness closure。

原因：

- route 已 live
- command path 已有
- 現在更像是 cleanup / hardening，而不是還缺設計

關鍵程式碼：

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`

---

### B4. KW-01-WIRING-001

目標：

把 institutional memory 從 example / hardcoded payload 推進到真正 read-store 或 service-backed path。

原因：

- route 已 live
- contract 已發布
- 目前最大的缺口是 payload truth，不是缺頁面設計

關鍵程式碼：

- `services/control-plane/bff/main.py`

---

## C. 可直接派的 UI / Handoff Activation Tasks

### C1. EW-05-UI-ACTIVATION-001

目標：

基於已 live 的 mutation review route 與 command vocabulary，啟動 production UI / Lovable implementation。

原因：

- `GET /api/v1/operator/mutation-review/{decision_id}` 已 live
- command vocabulary 已 live
- 這一塊不該再卡在 architecture lane

參考：

- `docs/bff/EW-05-mutation-review.md`
- `docs/screens/EW-05-mutation-review.md`
- `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md`

---

### C2. CW-03-UI-ACTIVATION-001

目標：

把 committee board / detail 從「文件上仍 blocked」改成真實可交付的 frontend handoff。

原因：

- code 已有 route
- 目前主要是 docs / handoff 沒 rebaseline

---

## D. 可直接派的 Doc Rebaseline Tasks

### D1. DOC-REBASE-WORKBENCH-001

目標：

把 `WORKBENCH_DELIVERY_BACKLOG.md` 與 code truth 對齊。

至少要修正：

- `EW-05`
- `RW-01`
- `RW-03`
- `CW-03`

原因：

- 這幾項目前文件仍把它們寫成 route-missing 或 module-not-ready
- 但 code 已有 live route

---

### D2. DOC-REBASE-LOVABLE-SA-001

目標：

把 `docs/lovable/PANTHEON_FRONTEND_SA.md` 與 BFF truth 對齊。

至少要修正：

- `KW-02~04` readiness 漂移
- `CW-03` readiness 漂移
- `EW-05` / `RW-01` / `RW-03` 的 route-live truth

---

## E. 可直接派的 Test / Technical Debt Tasks

### E1. TEST-PROMOTION-001

目標：

為 `services/promotion/` 補 service-path tests。

---

### E2. TEST-LINEAGE-READ-001

目標：

為 `services/lineage-read/` 補 tests。

注意：

- 這個 task 只補 coverage
- 不處理 lineage ownership 決策

---

### E3. TEST-ROUTER-001

目標：

為 `services/control-plane/router/` 補 service-path tests。

注意：

- 若碰到 ownership / enforcement 邊界問題，回拋 architecture lane

---

### E4. TEST-PERSONA-MAIN-001

目標：

為 `services/control-plane/persona/main.py` 補目前 stub 行為的 service-path tests。

注意：

- 這不是 productization task
- 只是把現況測試化，讓後續 refactor 有保護

---

### E5. TECHDEBT-PYDANTIC-V2-001

目標：

把 BFF 內仍在用的 `.dict()` 改成 `model_dump()`。

原因：

- 目前 targeted tests 已出現 Pydantic v2 deprecation warning

關鍵程式碼：

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/command_queue.py`

---

## 不該直接派給 Auto Worker 的項目

以下先不要直接 materialize 成 implementation task：

- global canonical conventions pack
- `LIN-002` lineage ownership decision
- `control-plane/persona` boundary decision
- `control-plane/router` enforcement ownership decision
- `RW-05`
- `CW-02`
- `CW-04`
- `TW-02`
- `KW-05`
- `KW-02~04` readiness ratification

這些先等 architecture input，否則 worker 很容易把 draft 當 truth。

---

## 一句話結論

現在最適合給 auto worker 的，是 **implementation、wiring、truth-hardening、doc rebaseline、tests**。

真正缺 canonical decision 的少數模組與全域規則，先不要混進 implementation board。
