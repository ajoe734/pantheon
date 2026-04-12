# OSS-003 Codex Review

狀態：`review_approved`
Reviewer：`Codex`
日期：2026-04-10

## 結論

`OSS-003` 可進入 `review_approved`。

這輪 reviewer cleanup 已吸收三個原本會造成後續漂移的 blocker：

1. `Qlib` / `TRL` activation docs 不再把 `paper` / `live` 當成 registry lifecycle，而是回到 shared truth 的 `artifact_state` + `deployment_stage` 分離語義。
2. `Qlib` artifact 範例不再發明 `alpha_model` / string `storage_ref` 這類不在目前 registry contract 的欄位形狀；`TRL` artifact 也同步改成 canonical target shape。
3. `W&B` activation doc 不再引用 repo 中不存在的 `ExperimentAdapter` / `build_adapter()` / `WandBExperimentAdapter` 現成 API，改成明確承認目前只有 MLflow-first `RegistryExperimentAdapter`，並把 backend generalization 列為啟用前門檻。

## 驗證

- 文件交叉對齊：
  - `TARGET_ARCHITECTURE.md`
  - `services/registry/contract.md`
  - `services/registry/experiments/adapter.py`
  - `services/execution/artifact-loader/contract.md`
- 相關文件 metadata / checklist 已同步：
  - `services/learning/qlib/ACTIVATION_CRITERIA.md`
  - `services/learning/trl/ACTIVATION_CRITERIA.md`
  - `services/registry/experiments/WANDB_ACTIVATION.md`
  - `services/learning/OSS_003_REVIEW_PACKET.md`
  - `OSS_INTEGRATION_CHECKLIST.md`

## 備註

- 這次是文件與契約對齊工作，未執行 runtime / unit tests。
- parent task 已可由 owner 正式 finalize；sidecar review packet 可後續吸收，不阻塞主線 `OSS-003`。
