# APP-001C 審查核准（Codex）

**任務**: `APP-001C`  
**作者**: Qwen  
**審查者**: Codex  
**狀態**: APPROVED  
**日期**: 2026-04-10

## 結論

這版 `APP-001C` 可以核准。三份 artifacts 都已寫齊，而且有守住 task 邊界：它們把 `APP-001A` 的 surface inventory 與 `APP-001B` 的 critique / risk packet 收斂成可直接交給 `APP-001` owner 消費的設計骨架、query contract outline 與 open questions list，但沒有越界把尚未定案的 RBAC、transport、cache topology、PER/LIN field schema 假裝成既成 canonical truth。

## 核准依據

1. `APP_001C_DESIGN_SKELETON.md` 已把 BFF 拆成 gateway、composition、service adapter、external state 四層，並補上 surface-to-adapter mapping、degradation architecture、4 條 composed view、secondary control path 與 observability 要求。這讓 `APP-001` owner 可以直接從 component boundary 開始，而不是再從 inventory 重組一次。
2. `APP_001C_QUERY_CONTRACT_OUTLINE.md` 已完整吸收 `APP-001B` 的 7 個 contract gaps：query envelope、response envelope、error shape、staleness/degradation、composed views、versioning、realtime feed placeholder 都有對應章節，足以作為後續 OpenAPI / JSON Schema formalization 的前置稿。
3. `APP_001C_OPEN_QUESTIONS.md` 已把尚未鎖定的決策集中成 decider-ready 清單，並明確標出 critical / important / deferred 三層，包括 RBAC matrix、SSE vs WebSocket、cache TTL、PER-001 / LIN-001 dependency timing、composed-view consistency 與 secondary control path ownership。這讓 `APP-001` 後續不會把未決事項散落在正文裡。
4. reviewer 做了三個必要 cleanup，讓文件內部語義與 `APP-001A/B` 對齊：
   - 修正 degraded-path 自相矛盾：原稿一邊說 downstream failure 不能回 `data: []`，一邊又把 list failure 寫成空陣列；現已收斂成「有 stale/replica payload 就回 degraded data，沒有 verifiable payload 就回 `DOWNSTREAM_UNAVAILABLE`」。
   - 修正 `RT-03` 型別漂移：query contract 原本寫成未定義的 `RuntimeStatus`，現已回到 `APP-001A` 定義的 `RuntimeBinding`，避免發明新的 pseudo-canonical object。
   - 統一 composed-view error/staleness 命名：`_surfaces` 已統一成 `meta.surfaces`，避免 skeleton、query outline、open questions 三份文件各講各的。

## 驗證

- 人工逐份核對：
  - `services/control-plane/bff/APP_001C_DESIGN_SKELETON.md`
  - `services/control-plane/bff/APP_001C_QUERY_CONTRACT_OUTLINE.md`
  - `services/control-plane/bff/APP_001C_OPEN_QUESTIONS.md`
- 交叉比對來源：
  - `services/control-plane/bff/BFF_SURFACE_INVENTORY.md`
  - `services/control-plane/bff/APP_001B_OWNER_PACKET.md`
  - `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
  - `PERSONA_RUNTIME_MODEL.md`
  - `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
  - `TARGET_ARCHITECTURE.md`
- 本輪未執行自動化測試；此 task 為 design packet / reviewer artifact，沒有對應 runtime code path 需要 smoke。

## 結果

`APP-001C` 已達成本輪目標：把 governed BFF 的設計骨架、query contract outline、以及 formal contract 前必須解的 open questions 收斂成一組可直接 handoff 的 packet。後續 `APP-001` owner 可以在不重做 inventory / critique 的前提下，直接進入 API contract、RBAC、composed-view、realtime feed 與 secondary control path 的正式設計。

結論：`APP-001C` 可進入 `review_approved`，並 handoff 給 owner Qwen 做最終收尾。
