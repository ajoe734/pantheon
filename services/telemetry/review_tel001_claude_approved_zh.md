# TEL-001 Review — Claude

日期：2026-04-10
結論：`review_approved`

## 複查項目與結果

### Blocker 1：deploy/rollback/kill-switch/heartbeat helpers 使用空 metrics 無法通過 schema

**狀態：已修復 ✅**

所有 lifecycle helpers 現在攜帶非空的 canonical metric payload：
- `deploy_started` / `deploy_completed`：`metrics={"action": "deploy_started/completed"}`
- `rollback_started` / `rollback_completed`：`metrics={"action": "rollback_started/completed"}`
- `kill_switch_action`：`metrics={"action": "kill_switch_action"}`
- `heartbeat`：`metrics={"heartbeat": 1}`

schema 在 `metrics` 定義了 `action` 和 `heartbeat` 這兩個明確欄位，讓生命週期事件能以語義正確的方式通過 `minProperties=1` 要求。驗證：`capture_deploy_started`、`capture_rollback_started`、`capture_heartbeat` 均在 smoke test 回傳 `True`。

### Blocker 2：evidence 欄位改為嚴格拒絕，不再補假值

**狀態：已修復 ✅**

`_build_event()` (capture.py:626-642) 現在在 `binding_context` 存在但有缺失欄位時，立即 `return None`，並記錄明確的拒絕原因。`_store_event()` (capture.py:697) 對 `None` 立即 `return False`。

測試涵蓋：`test_partial_binding_context_rejected` 和 smoke test step 13 均確認部分 binding_context 被拒絕，`get_paper_events()` 為空。

### Blocker 3：`deployment_stage='frozen'` 正確映射至 `execution_mode='paper'`

**狀態：已修復 ✅**

`_build_event()` (capture.py:592-598) 加入了明確的 `frozen` 分支：
```python
elif deployment_stage == "frozen":
    execution_mode_alias = "paper"
```
binding_context 路徑 (capture.py:655-658) 同步加入相同邏輯。

測試涵蓋：`test_frozen_deployment_stage_maps_to_paper_mode` 和 smoke test step 14 均確認 `frozen` → `execution_mode=paper`，`deployment_stage=frozen`，`environment=frozen`，schema validation 通過。

### Blocker 4：tests/smoke 統一為 canonical telemetry_event.schema.json

**狀態：已修復 ✅**

- `test_capture.py` 第 17 行：`CANONICAL_SCHEMA = str(Path(__file__).parent / "telemetry_event.schema.json")`
- `smoke_test.py` 第 22 行：同上
- 所有新增 `TestBindingStageEvidence` 測試均以 `schema_path=CANONICAL_SCHEMA` 建立 `TelemetryCapture`

結果：33/33 unit tests pass，smoke test 14/14 steps pass，全部對 canonical schema 驗證。

### Canonical Drift：`runtime_binding_id` → `binding_id` naming

**狀態：已修復 ✅**

`LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` 中已無 `runtime_binding_id` 殘留。schema、capture.py、測試、lineage doc 均使用 `binding_id` 作為唯一欄位名。

## 驗證執行

```
cd services/telemetry
python3 -m unittest test_capture -v
# 結果：33 tests, OK
python3 smoke_test.py
# 結果：14/14 steps passed
```

## 備注

1. `rollback_parent` schema 宣告了 `"format": "uuid"`，但測試使用了非 UUID 格式字串（如 `"old-binding-id-uuid"`）。由於 jsonschema draft-07 預設不強制 format 校驗，這不影響現有測試，但建議 downstream ingest-svc 在插入 Postgres 前加 UUID 格式驗證。這屬 post-landing 改進，不阻擋本次 merge。
2. `execution_mode` 欄位保留作 backward-compat alias，schema 描述已說明將來 deprecation 路徑。

## 結論

TEL-001 所有 4 個 Codex 阻礙項已全部解決，canonical drift 已收斂。**approved — 可合併並設 done。**
