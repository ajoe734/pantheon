# CAP-001 Review

審查結果：通過。

審查範圍：

- `services/control-plane/governance/review_cap001_claude_zh.md`
- `services/control-plane/governance/capital_pool.contract.md`
- `services/control-plane/governance/capital_pool.py`
- `services/control-plane/governance/capital_pool.schema.json`
- `services/control-plane/governance/persona_capital_binding.py`
- `services/control-plane/governance/persona_capital_binding.schema.json`
- `services/control-plane/governance/smoke_test_capital_pool.py`
- `services/control-plane/governance/test_capital_pool.py`
- `services/control-plane/governance/test_persona_capital_binding.py`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `PERSONA_RUNTIME_MODEL.md`

本輪 reviewer 額外補了一個測試一致性修正：

1. `services/control-plane/governance/test_capital_pool.py` 原先把合法的 `active -> archived` 轉移誤寫成應該丟錯，與 `capital_pool.py` 的 `_ALLOWED_STATUS_TRANSITIONS` 以及 `capital_pool.contract.md` §2.4 不一致。現已改成：
   - `test_update_status_archive_from_active()` 驗證合法封存路徑
   - `test_update_status_invalid_transition()` 保留真正非法的 `archived -> active`

驗證：

- `python3 services/control-plane/governance/smoke_test_capital_pool.py`
  - `64/64 checks passed`
- `python3 -m py_compile services/control-plane/governance/capital_pool.py services/control-plane/governance/persona_capital_binding.py services/control-plane/governance/smoke_test_capital_pool.py services/control-plane/governance/test_capital_pool.py services/control-plane/governance/test_persona_capital_binding.py`
  - passed
- `python3 -m pytest -q services/control-plane/governance/test_capital_pool.py services/control-plane/governance/test_persona_capital_binding.py`
  - 本機環境缺少 `pytest` module，無法在此 session 直接執行

結論：

`CAP-001` 的兩個 acceptance criteria 已滿足：

1. pool 與 binding 的 ownership 已在 `capital_pool.contract.md`、`capital_pool.py`、`persona_capital_binding.py` 以及 `BINDING_AND_DEPLOYMENT_SEMANTICS.md` 中明確收斂。
2. single-pool runtime rule 已在 `CapitalPool.single_runtime_enforced`、`PersonaCapitalBindingStore._check_single_live_owner()`、`capital_pool.contract.md` §2.5 / §3.8，以及 `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §9 / §16 中完整落地。

另，CAP-001A reviewer 要求補齊的兩個 follow-up 也已收斂：

- `allowed_deployment_scope` rename drift 已和 schema / contract 對齊
- governance / execution status mapping 已在 `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §16 補齊，可直接供 RUN-001 參考

結論：`CAP-001` 可進入 `review_approved`，並 handoff 給 owner Claude 做最終收尾為 `done`。
