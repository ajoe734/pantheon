# INC-001 審查核准（Codex）

**任務**: `INC-001`  
**作者**: Claude  
**審查者**: Codex  
**狀態**: APPROVED  
**日期**: 2026-04-10

## 結論

這版 `INC-001` 現在可以核准，但不是原樣核准。review 過程中補了兩個會讓 incident/postmortem backbone 名義上存在、實際上卻無法穩定承載 forensic truth 的問題：

1. `IncidentStore.create_postmortem()` 原本只檢查 `incident_id` 是否存在，沒有驗證 `Postmortem` 上 propagated evidence 是否真的和 parent `IncidentCase` 一致。這會讓 `postmortem.incident_case` formal edge 存在，但 `binding_id` / `artifact_id` / `trace_id` 等 evidence 可以靜默漂移。這輪已改成強制比對完整 propagated evidence 集合，不一致就拒絕寫入。
2. `services/incident/smoke_test_incident.py` 直接執行會因 `sys.path` 指到 repo root 上一層而失敗，和 handoff 中「smoke checks PASS」的敘述不一致。這輪已修正路徑，讓 `python3 services/incident/smoke_test_incident.py` 與 `python3 -m services.incident.smoke_test_incident` 都能正常跑完。

另外，`Postmortem` 現在已補齊和 `IncidentCase` 同一組核心 evidence refs：`deployment_plan_id`、`persona_capital_binding_id`、`runtime_id`，使它和交接內容中「Postmortem propagates the same evidence refs」一致，而不是只帶部分欄位。

## 核准依據

1. `services/incident/incident.py` 現在把 `Postmortem` 明確收斂成 incident evidence 的完整 propagated snapshot，而不是只有 formal edge `incident_id` 與零散欄位。這讓後續 `EVO-003` / `EVO-004` / forensic read path 不必再猜哪些 evidence 要回頭 join incident 才拿得到。
2. `services/incident/postmortem.schema.json`、`services/incident/contract.md`、`services/incident/test_incident.py`、`services/incident/smoke_test_incident.py` 已同步到同一份 truth：Postmortem 必須帶完整 propagated evidence，store 也必須拒絕和 parent incident 不一致的 snapshot。
3. 本輪不是只補文件。負面案例已被鎖進測試：如果 postmortem 對同一個 `incident_id` 帶入不同的 `binding_id` / `deployment_plan_id` / `persona_capital_binding_id` / `artifact_id` / `runtime_id` / `trace_id`，store 會直接丟錯，不再接受錯鏈資料。

## 驗證

- `python3 -m unittest services.incident.test_incident`
- `python3 services/incident/smoke_test_incident.py`
- `python3 -m services.incident.smoke_test_incident`

結果：

- unit tests: `75/75` PASS
- smoke checks: `59/59` PASS

## 結果

`INC-001` 已滿足本輪 reviewer 期待：`IncidentCase` / `Postmortem` 現在不只是有 formal edges，也真的守住 evidence propagation 的一致性；schema、contract、unit test、smoke test 都對齊，而且 smoke test 的直接執行路徑已可用。

結論：`INC-001` 可進入 `review_approved`，並 handoff 給 owner Claude 做最終收尾為 `done`。
