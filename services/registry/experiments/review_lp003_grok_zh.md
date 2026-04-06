# LP-003 審查意見（Grok）

**任務**: `LP-003`  
**作者**: Codex  
**審查者**: Grok  
**狀態**: APPROVED ✓

## 審查結論

LP-003 實現完整並通過驗收。Codex 在 `services/registry/experiments/` 中提供了完整的 MLflow-first registry adapter，達成了所有驗收標準：

1. ✓ experiment lineage 正確對應到 registry metadata
2. ✓ aliases 與 promotion metadata 文檔完整
3. ✓ artifact version handoff 定義清楚
4. ✓ 所有單位測試通過
5. ✓ smoke test 通過

## 實現亮點

### 1. 正確的治理語義

- `RegistryExperimentAdapter` 正確映射 registry entry → MLflow run
- experiment name 格式 `pantheon/{artifact_type}/{strategy_id}` 保證按 artifact 類別和策略族分組
- run name 格式 `{version}:{lifecycle_state}` 使 lifecycle 進展清晰可見
- **关键**: MLflow 只作為鏡像，不是 promotion 的真相源

### 2. 完整的 lifecycle state 對應

adapter 實現了 REG-001 / EX-001 要求的完整生命週期：

```python
_PROMOTION_ALIASES = {
    "draft": (),           # draft 不產生別名或 promoted metadata
    "candidate": ("candidate",),
    "paper": ("paper",),
    "live": ("live",),
    "retired": ("retired",),
}
```

- ✓ `draft` 狀態下根本不產生 `promoted_metadata`
- ✓ `candidate/paper/live/retired` 都映射到對應 aliases
- ✓ 與 EX-001 loader 的 allow/reject 邏輯直接相容

### 3. 充分的 lineage 與 rollback 支援

必需的 mirrored tags:

- `pantheon.registry_id`, `pantheon.strategy_id`, `pantheon.version`, `pantheon.artifact_type`
- `pantheon.lifecycle_state`, `pantheon.checksum`
- `pantheon.storage_backend`, `pantheon.storage_path`
- `pantheon.lineage` 及其所有 subfields
- `pantheon.aliases`, `pantheon.mlflow.version_pin`

Optional tags 正確處理：

- `pantheon.producer_run_id`, `pantheon.promoted_at`, `pantheon.approver`
- `pantheon.rollback_target`, `pantheon.evaluation_summary`

**Live 條目 rollback 驗證**:

- 提供了兩種可接受形式：
  1. `metadata.rollback` 包含 `target_registry_id` + `target_version`
  2. `metadata.rollback_target_registry_id` + 頂級 `rollback_target`
- Live 條目沒有 rollback 時會正確拒絕並提示錯誤

### 4. Artifact Handoff 正確實現

`artifact_handoff.json` 提供了完整的 MLflow → governed store 的反向對應：

```python
"execution_projection": {
    "metadata_path": f"openclaw/registry/{strategy_id}/{version}/metadata.json",
    "artifact_path": f"openclaw/registry/{strategy_id}/{version}/artifact.bin",
}
```

- ✓ 保留了 canonical `storage_ref`
- ✓ 保留了 `checksum`
- ✓ 提供了 execution projection 路徑給 EX-001 loader
- ✓ 只帶 descriptive aliases（非決策別名）

### 5. Promoted Metadata 形狀適於下游

`ExperimentSyncResult.promoted_metadata` 返回的結構：

```python
{
    "registry_id": "...",
    "strategy_id": "...",
    "version": "...",
    "artifact_type": "...",
    "promotion_state": "paper|live|...",
    "checksum": "...",
    "lineage": {...},
    "created_at": "ISO8601",
    "experiment_refs": [...],
    "approved_at": "...",  # if promoted
    "approver": "...",      # if promoted
    "rollback": {...}       # if live
}
```

這個形狀與 REG-003 執行投影的要求相容，LP-005 可以直接使用。

### 6. 測試覆蓋完整

- ✓ `test_build_record_maps_tags_metrics_and_handoff` — 驗證 tag/metric/handoff 映射
- ✓ `test_sync_registry_entry_returns_promoted_metadata_with_experiment_ref` — 驗證 end-to-end sync
- ✓ `test_live_entry_requires_rollback_registry_metadata` — 驗證 live 條目需要 rollback
- ✓ `test_live_entry_builds_reg003_compatible_rollback_object` — 驗證 rollback 形狀

所有測試通過，smoke test 也通過。

## 與 LP-005 的相容性

LP-005 review 中列出的下游要求都已滿足：

1. **Lifecycle state vocabulary** — 正確使用 `draft/candidate/paper/live/retired`（已解決 LP-005 審查的問題#1）
2. **Artifact model + handoff** — 完整的 governance metadata 和 Object Store projection（已解決 LP-005 審查的問題#2）
3. **Experiment refs shape** — 包含 backend, run_id, artifact_uri, project, aliases 等完整欄位
4. **REG-003 相容性** — promoted_metadata 形狀與 registry 最終執行投影契約相容

**結論**: LP-005 現在可以安心使用 `promoted_metadata["experiment_refs"]` 來記錄實驗執行追蹤，無需修改。

## W&B Deferral

適當地將 W&B 延後。MLflow 優先確保：

- 本地控制 lineage 和 storage metadata
- promotion path 仍在穩定中，可逐步調整
- 無需即刻依賴 SaaS 追蹤後端

後續若要添加 W&B，只需在同一 adapter 框架中實現另一個 `ExperimentBackend` 實現。

## 建議

無。實現完整、測試充分、文檔清晰。可直接合併。

## Reviewer Decision

✅ **APPROVED** — LP-003 通過審查，實現達到並超過驗收標準。

---

**審查者**: Grok  
**審查時間**: 2026-04-06T09:45:00Z
