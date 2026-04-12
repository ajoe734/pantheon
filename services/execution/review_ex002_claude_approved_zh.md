# EX-002 Review — Claude (Approved)

**Task:** EX-002 — Align rollback execution actions with runtime-manager semantics  
**Owner:** Codex  
**Reviewer:** Claude  
**Review date:** 2026-04-10  
**Verdict:** APPROVED

---

## 驗證範圍

EX-002 的核心聲明：

1. `rollback_action_type` 在整個 deployment_saga compensation 流程中維持 `replace` / `pause_then_replace` / `liquidate_then_replace` 正式詞彙
2. 舊有 `replace_binding` 只在 compatibility boundary 正規化
3. rollback_action_matrix 與 ROLLBACK_AND_POSITION_SEMANTICS 對齊到 RuntimeBinding creation/retire（而非 in-place 覆寫）語意
4. artifact-loader contract 明確限定只驗 fallback metadata，不自行決定 rollback execution 語意

---

## 測試驗證（全部通過）

| 測試指令 | 結果 |
|---|---|
| `test_deployment_saga.py` (9 tests) | 9/9 PASS |
| `test_deployment_plan.py` (24 tests) | 24/24 PASS |
| `smoke_test_deployment_saga.py` | 13/13 PASS |
| `smoke_test_runtime_binding.py` | 10/10 groups PASS |
| `test_artifact_loader.py` (9 tests) | 9/9 PASS |

---

## 驗收標準確認

### AC1：action mapping is explicit

`rollback_action_matrix.md` 有清楚三列對應表，涵蓋：
- 觸發情境（Scenario）
- Runtime Manager 的每步執行動作
- Position treatment（Preserve & Inherit / Drain & Inherit / Flatten）
- Telemetry cutover 邊界

matrix §4 也明訂了 Loader Boundary：loader 不決定 action type，不 mutate binding。

**✅ 滿足**

### AC2：position handling and cutover semantics are preserved

`ROLLBACK_AND_POSITION_SEMANTICS.md` 正式定義：
- `opened_by_artifact_id`：永遠不可被 rollback 改寫
- `current_managed_by_binding_id`：只在 replacement binding 成為 active owner 後才更新
- telemetry cutover 邊界：由 Runtime Manager retire 舊 binding 的時點決定，不以 loader load 完成時間決定
- `liquidate_then_replace` 的清倉 telemetry 仍歸舊 binding/artifact

`rollback_action_matrix.md` §3 再次明訂 `liquidate_then_replace` 的 guard：positions 未全 flat 前不可轉移 ownership。

**✅ 滿足**

---

## 邊界責任確認

### `_normalize_rollback_action_type()`（deployment_plan.py:786）

```python
if value == RuntimeAction.REPLACE_BINDING.value:
    return RollbackActionType.REPLACE
return RollbackActionType(value)
```

- 只在 compatibility boundary 正規化 `replace_binding` → `replace`
- 正式詞彙直接用 `RollbackActionType(value)` 驗證，不做額外轉換
- deployment_saga.py 的 `_parse_rollback_action()` 在 bootstrap 入口正規化，之後整個 saga 生命週期維持 canonical 詞彙

### `runtime_binding.py` RollbackActionType enum

```python
class RollbackActionType(str, Enum):
    REPLACE = "replace"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"
```

- 不含 `replace_binding` — 舊詞彙不可能穿透到 RuntimeBinding 層
- `rollback_parent` 設定時 `rollback_action_type` 必填（`runtime_binding.py:233-234`）

### artifact-loader/contract.md §4.1

明確聲明：

> The loader is **not** responsible for: deciding whether the runtime should `replace`, `pause_then_replace`, or `liquidate_then_replace`; mutating `RuntimeBinding`; timing the telemetry cutover.

責任分界乾淨，loader 只驗 fallback metadata 存在性。

---

## 結論

EX-002 的三個核心成果完整到位：

1. **ROLLBACK_AND_POSITION_SEMANTICS.md** — 新 L1 文件，正式定義三種 rollback 策略、position lineage 欄位、telemetry cutover 語意
2. **rollback_action_matrix.md** — 明確 action mapping，含 position treatment 與 telemetry cutover 邊界
3. **artifact-loader/contract.md** — 責任邊界乾淨，loader 不越俎代庖

所有驗證測試通過，邊界責任清楚，無殘餘 workaround。

**EX-002 APPROVED — 標記為 done。**
