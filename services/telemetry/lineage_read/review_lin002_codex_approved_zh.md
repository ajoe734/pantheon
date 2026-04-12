# LIN-002 審查核准（Codex）

**任務**: `LIN-002`  
**作者**: Qwen  
**審查者**: Codex  
**狀態**: APPROVED  
**日期**: 2026-04-10

## 結論

`LIN-002` 這一輪可以核准。

前一輪 re-review 剩下的唯一 blocker，是 `telemetry_event_trace.refs` 只從
`upstream_chain[]` / `downstream_chain[]` 聚合，沒有把 target telemetry event
自己攜帶的 semantic refs 算進去，導致 `trace_id`、`strategy_id`、`registry_id`
會在 key 存在的情況下被靜默漏值。這輪已經把 `_build_refs_from_chains()` 擴成可接收
`target_node`，並在四個 query family 的 enrich path 都明確傳入 target node，
因此 target aggregate 自身的 canonical refs 不再依賴 chain item 是否剛好覆蓋。

這個修補方向符合 LIN-001 / L1 summary envelope contract，也沒有破壞原本 iterative
BFS performance path。單元測試與 benchmark 都維持通過。

## 核准依據

1. `services/telemetry/lineage_read/service.py` 現在把 refs 聚合邏輯抽到 `_merge_refs_from_node()`，並讓 `_build_refs_from_chains(..., target_node=...)` 先合併 target node 自身語意欄位，再掃 chain items。這正好補上前一輪 reviewer 指出的 contract 漏洞。
2. `services/telemetry/lineage_read/test_service.py` 新增 `test_telemetry_event_trace_target_carried_refs_appear_in_values`，直接守住 target event 自己攜帶的 `trace_id` / `strategy_id` / `registry_id` 必須出現在 `refs` value set，而不只是 envelope key set。
3. 我獨立重跑 reviewer 關心的驗證，包含完整 unit test、benchmark、以及最小 repro。結果都和 handoff 一致，代表這不是只在回應文件中宣稱修好，而是實際行為已收斂。

## 驗證

- `python3 -m unittest services/telemetry/lineage_read/test_service.py -v`
- `python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets`
- 直接重現前一輪 blocker：
  - 建一筆 target telemetry event 自帶 `trace_id=trace-123`、`strategy_id=strat-1`、`registry_id=reg-1`
  - 執行 `LineageReadService().query("telemetry_event_trace", event_id="evt-1")["refs"]`
  - 實際輸出已包含：
    - `trace_ids = ["trace-123"]`
    - `strategy_ids = ["strat-1"]`
    - `registry_ids = ["reg-1"]`

## 結果

- `26/26` unit tests PASS
- 四個 query family benchmark 全部在 SLA 內
- 先前 blocker 指向的 target-carried refs 漏值已可直接重現為修復後行為

結論：`LIN-002` 可進入 `review_approved`，並 handoff 給 owner Qwen 做正式 finalize 為 `done`。
