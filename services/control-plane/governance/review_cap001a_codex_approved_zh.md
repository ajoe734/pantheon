# CAP-001A Review

審查結果：通過。

審查範圍：

- `services/control-plane/governance/review_cap001a_qwen.md`
- `services/control-plane/governance/capital_pool.contract.md`
- `services/control-plane/governance/capital_pool.py`
- `services/control-plane/governance/persona_capital_binding.py`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `Pantheon_資料表_Schema_設計版.md`

驗證：

- `python3 services/control-plane/governance/smoke_test_capital_pool.py`
  - `64/64 checks passed`
- `python3 -m py_compile services/control-plane/governance/capital_pool.py services/control-plane/governance/persona_capital_binding.py`
  - passed

結論：

`CAP-001A` 的三個 acceptance criteria 已滿足。Qwen 的 packet 已經把 pool ownership、single-runtime / single-live-owner 規則、governance file map，以及 CAP-001 目前最重要的 schema drift 整理成可直接交給主 owner 的支援材料。

本輪 reviewer 只補一個非阻塞更正：

1. `review_cap001a_qwen.md` §7 的第 7 點把 smoke test 視為待補項，但實際上 `services/control-plane/governance/smoke_test_capital_pool.py` 已存在，且已可直接用來做 CAP-001 的 end-to-end smoke verification。這不影響 packet 的主要結論，也不阻塞本 task 通過。

建議主 owner 直接吸收 packet 中兩個最有價值的 follow-up：

1. 優先處理 `deployment_mode` → `allowed_deployment_scope` 的 DB schema rename drift。
2. 在 contract 或 L1 文件中補一段 governance view 與 execution/runtime view 的 status mapping 說明，避免後續 RUN-001 / EX-002 誤把不同層的 status 當成同一語意。

結論：`CAP-001A` 可進入 `review_approved`，並 handoff 給 `CAP-001` owner 作為正式 acceptance / compatibility 參考包。
