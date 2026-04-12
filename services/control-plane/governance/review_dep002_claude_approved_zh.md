# DEP-002 Review Decision — Claude

Reviewer: Claude  
Date: 2026-04-10  
Status: **approved**

---

## 驗證結果

```
python3 -m unittest discover -s services/control-plane/governance -p 'test_deployment_saga.py'
→ 7 tests, OK

python3 services/control-plane/governance/smoke_test_deployment_saga.py
→ 12/12 checks passed

python3 -m unittest discover -s services/control-plane/cron -p 'test_*.py'
→ 9 tests, OK
```

---

## 逐項驗收

### AC-1: business write + event outbox is atomic

**通過。**

`DeploymentSagaStore._transaction()` 實作了 copy-on-write draft 機制：
- 進入 mutate 之前深拷貝 sagas / outbox / inbox
- mutate 在 draft 上同時操作 saga 狀態與 outbox append
- 只有 `_validate_state()` + `_before_commit()` 都通過後，才把 draft 寫回 live state 並 persist
- `_persist_draft()` 使用 `.tmp` → `replace()` 的 atomic rename，符合 local ACID 的意圖

`test_commit_failure_rolls_back_saga_and_outbox` 以注入 `before_commit` 拋出例外來驗證：
commit 失敗時 saga 與 outbox 都不落地，重新載入亦為空。

### AC-2: ordering and idempotent consumer behavior are verified

**通過，且符合 EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md。**

`_build_receipt()` 按以下順序 dedupe：
1. `event_id` 或 `idempotency_key` 重複 → `DUPLICATE`（不呼叫 apply_fn）
2. `sequence_no <= last_applied_sequence` → `DUPLICATE`
3. `sequence_no != last_applied_sequence + 1` → `OUT_OF_ORDER`（gap）
4. 符合條件 → `APPLIED`

`test_consumer_is_idempotent_and_preserves_per_aggregate_order` 驗證了完整的 gap → close 場景：
- seq3 先到 → `OUT_OF_ORDER`
- seq1 重送 → `DUPLICATE`
- seq2 到 → `APPLIED`
- seq3 重送 → `APPLIED`（gap 已填補）

`causal_parent_id` 鏈正確：`seq2.causal_parent_id == seq1.event_id`，`seq3.causal_parent_id == seq2.event_id`。

### AC-3: compensation boundaries are documented

**通過，write-owner 邊界對齊 DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md。**

`determine_compensation()` 的四條 branch：

| 失敗點 | command | owner_service | 符合 write-owner |
|---|---|---|---|
| `BINDING_REQUESTED` | `abort_plan` | `governance-svc` | ✓ DeploymentPlan 的 owner |
| `RUNTIME_LOAD_REQUESTED` | `mark_binding_failed_inactive` | `runtime-manager-svc` | ✓ RuntimeBinding 的 owner |
| `RUNTIME_ACTIVE` | `request_rollback` | `rollback-controller` | ✓ issues command; runtime-manager applies |
| `COMPENSATION_REQUESTED` | `enter_safe_mode_and_raise_incident` | `runtime-manager-svc` | ✓ incident-owner 附加，不跨越 write boundary |

`rollback_action_type` 正確從 `DeploymentPlan.rollback.action_type` 繼承至 `DeploymentSaga.rollback_action_type`（via `from_plan()`），不重新發明 rollback 規則。  
`test_post_activation_failure_uses_plan_rollback_action` 以不同的 `rollback_action=RuntimeAction.PAUSE_THEN_REPLACE` 驗證此行為。

---

## 額外觀察（非阻斷）

1. **Reference implementation 邊界**：`_transaction()` 的 atomicity 是 in-process。生產環境需要真正的 local ACID DB transaction，但作為 policy 層的 reference implementation 是適合的表達方式。

2. **`finalize_compensation()` 的 event owner**：final event 的 `owner_service` 從 `saga.compensation.owner_service` 取出，attribution 正確。

3. **`record_runtime_active()` 的前置狀態寬鬆**：目前允許從 `AWAITING_BINDING` 直接跳到 `RUNTIME_ACTIVE`（不一定要先過 `AWAITING_RUNTIME_LOAD`）。在 EX-002 實作時，若 runtime-manager 有更嚴格的狀態機需求，可在那層加限制；此處的寬鬆是合理的 reference design。

4. **CAP-001 衝突**：`test_*.py` discover 仍被 `test_capital_pool.py` 的 pytest import 卡住，這不是 DEP-002 引入的問題，已知並在 next task 中追蹤。

---

## 決定

**approve**

三個 acceptance criteria 全部通過，L1 policy 對齊確認，write-owner 邊界維持，EX-002 接口預留合理。
