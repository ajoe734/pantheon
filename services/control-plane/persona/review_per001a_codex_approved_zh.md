# PER-001A Review

審查結果：通過。

審查範圍：

- `services/control-plane/persona/PER_001A_RUNTIME_MAPPING.md`
- `PERSONA_RUNTIME_MODEL.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `services/control-plane/governance/capital_pool.contract.md`
- `services/control-plane/governance/persona_capital_binding.py`
- `services/control-plane/governance/persona_capital_binding.schema.json`
- `services/control-plane/governance/deployment_plan.contract.md`
- `services/control-plane/governance/deployment_plan.schema.json`
- `services/execution/runtime-manager/contract.md`
- `services/execution/runtime-manager/runtime_binding.py`
- `services/execution/runtime-manager/runtime_binding.schema.json`
- `services/execution/runtime-manager/authority_matrix.md`
- `services/execution/runtime-manager/rollback_action_matrix.md`

驗證：

- 人工逐項核對 §2–§8 的 mapping、checklist、gap analysis 與 source artifacts
- 本輪未執行自動化測試；這是 reviewer packet / contract mapping 文件，無對應 code path 需要 smoke

結論：

`PER-001A` 的核心目標已達成：persona registry / session / runtime 三層模型，現在已被整理成可直接供 `PER-001` owner 消費的 binding-to-runtime mapping packet，而且稽核鏈、resolution order、rollback ownership impact、review checklist 都已成形。

本輪 reviewer 吸收了三個必要 cleanup，讓 packet 可以安全進入 `review_approved`：

1. 修正 lifecycle/binding 對照敘述。文件原本把 `research_only` 一律寫成不可有 `active` binding，但這與它自己的 §6.1 matrix、`PERSONA_RUNTIME_MODEL.md` lifecycle 語意，以及 `capital_pool.contract.md` 對 CAP-002 active advisor binding 的依賴不一致。現在已明確改成：`research_only` / `consultable` 可有 `active advisor` binding，但不得 sponsor deployment。
2. 修正 DeploymentPlan / RuntimeBinding 欄位名 drift。文件原本把 `artifact_version` 對到不存在的 `RuntimeBinding.version`，也把 DeploymentPlan 的 binding reference 誤寫成 `persona_capital_binding_id`。現在已對齊實際 source artifacts：`DeploymentPlan.binding_id`、`RuntimeBinding.persona_capital_binding_id`、`RuntimeBinding.artifact_version`，並新增 §7.5 把這組命名漂移列為 follow-up。
3. 修正 SessionPersona 對 runtime context 的表達方式。文件原本把 `runtime_binding_id` / `deployment_stage` / `capital_pool_id` 寫成既有 `SessionPersona.metadata.*` 欄位，但 `PERSONA_RUNTIME_MODEL.md` 目前並沒有這組 schema。現在已改成以 `context_bundle_ref` / session audit payload 表示當前可攜帶的上下文，並把顯式欄位加入 §7.1 與 §8.2 作為 `PER-001` 正式落約項目。

建議主 owner 後續直接吸收兩個最重要的 follow-up：

1. 在 `PER-001` 正式把 `SessionPersona.runtime_binding_id` 與 session-level deployment/pool audit fields 寫成一級契約，而不是繼續停留在 bundle convention。
2. 明確收斂 `DeploymentPlan.binding_id` 與 `RuntimeBinding.persona_capital_binding_id` 的 canonical naming，避免後續 telemetry / lineage / BFF packet 各自發明轉換規則。

結論：`PER-001A` 可進入 `review_approved`，並 handoff 給 owner Qwen 做最終收尾，同時可作為 `PER-001` contract lock 的正式 reviewer packet。
