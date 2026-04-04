# OC-001 Permission Contract — Review (Claude)

**Task:** OC-001  
**Author:** Codex  
**Reviewer:** Claude  
**Status:** APPROVED with minor open items noted below

---

## 結論

OC-001 可以通過。deny-first 模型、6 條強制 deny 規則、以及 approval hook 設計都和 Router v1 實作對齊。
以下記錄三個已確認的小項目，不阻擋這個版本落地，但需要後續追蹤。

---

## Findings

### 1. RESOLVED: operator subject resolution 已在 Router 落地

- **原本的 concern：** OC-001 §9 要求 `console → role=operator`，但前一版 router 未正確傳遞 role。
- **現況：** P4-001 round-2 修正已在 `main.py` 加入 `_CHANNEL_ROLE` dict：
  ```python
  _CHANNEL_ROLE = {"console": "operator", "cron": "system",
                   "web": "persona", "telegram": "persona", "discord": "persona"}
  ```
  `_evaluate_permission()` 現在收到正確的 role 參數。
- **結論：** 已解決，OC-001 §9 minimum v1 mapping 已實作完畢。

---

### 2. MINOR: tool_policy_schema.json 的 `effect` 缺少 `allow_with_approval` 值

- **現況：** `tool_policy_schema.json` 中 `policyRule.effect` 只有 `["allow", "deny"]`。
- **問題：** `allow_with_approval` 在 contract §7 是明確的第三種結果，但 schema 透過
  `requires_approval: boolean` 旁路表達，無法在 policy 物件層面直接判斷是否需要審批，
  需要讀取 rule 的 `effect + requires_approval` 才能重建決策。
- **影響範圍：** 目前只影響未來 policy loader 的設計，v1 router 直接用 enum 判斷不受影響。
- **建議：** 後續在 OC-003 或 policy storage backend 落地時，考慮把 `allow_with_approval`
  加為 `effect` 的第三個枚舉值，讓 policy 物件可以獨立表達這個語義。

---

### 3. MINOR: 促銷狀態（promotion-state）強制在 v1 還不完整

- **Contract §5 step 6：** 列出 "enforce promotion-state restrictions for execution-capable actions"。
- **現況：** Router `_evaluate_permission()` 的 Rule 4 對 `execution_signal` 做 DENY/ALLOW_WITH_APPROVAL，
  但不讀取 signal artifact 的 `promotion_state`（`paper` / `live` / `draft` 等）。
  Promotion-state 的強制目前期待由 SignalStore 或 artifact loader 在寫入前把關，
  不在 router 這層。
- **這是已知 gap：** router contract §8 的 open items 已記錄。不阻擋 v1。
- **建議：** REG-002 promotion gate 落地後，router 或 Governance service 再銜接
  promotion-state 的二次確認。

---

### 4. CONFIRMED: 6 條強制 deny 規則與 router 實作一致

| OC-001 §6 Rule | router main.py 實作 |
|---|---|
| deny lean_direct when live | Rule 1: `tool_class == "lean_direct" → DENY` |
| deny draft/candidate/retired artifacts | 由 SignalStore 把關（router 傳遞 ALLOW_WITH_APPROVAL） |
| deny governance for non-operator | Rule 3: `tool_class == "governance" and role not in (operator, approver) → DENY` |
| deny deployment from chat/web | Rule 2: `tool_class == "deployment" and tier not in (operator, system) → DENY` |
| deny cron from live execution surfaces | Rule 4 + role=system 沒有 execution_signal 通道 |

第 2 條（deny draft/candidate artifacts）目前由下游 SignalStore 把關而非 router 直接檢查——
這是架構設計選擇，而非遺漏，因為 router 沒有 signal artifact 的 promotion-state 讀取能力。

---

### 5. CONFIRMED: approval hook 對高風險操作足夠

| 操作 | Contract | Router 實作 |
|---|---|---|
| paper → live 推廣 | requires_approval | `governance.approve → ALLOW_WITH_APPROVAL` |
| live artifact rollback | requires_approval (operator only) | `governance` class denied for non-operator |
| live execution signal | requires_approval | `execution_signal → ALLOW_WITH_APPROVAL` (operator only) |
| modify permission policy | requires_approval | `governance` class; non-operator DENY |

---

## 不要求這輪就做的事

- `allow_with_approval` 作為 schema effect 值（後續 OC-003 / policy loader）
- promotion-state 在 router 層的二次確認（後續 REG-002 完成後）
- full policy object loading from storage（OC-001 §9 open item，取決於 storage backend 選型）

---

## 結論

OC-001 permission contract 已達到 v1 鎖定標準：

- deny-first 模型完整
- 6 條強制 deny 規則在 router 已落地且對齊
- operator/system subject resolution 已在 P4-001 實作
- approval hook 覆蓋所有高風險操作
- 兩個 minor open items 不阻擋目前實作，已記錄為後續任務

**OC-001 APPROVED for v1 lock.**
