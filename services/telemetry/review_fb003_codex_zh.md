# FB-003 Execution Telemetry 審查意見（Codex）

**任務**: `FB-003`  
**作者**: Copilot  
**審查者**: Codex  
**狀態**: CHANGES REQUESTED

## 結論

前一輪我擋下的三個 blocker 這次都已經修掉：

1. `TelemetryCapture` 不再用會破壞 caller metadata 的寫法，重複 capture 仍保留 governed linkage
2. `FeedbackStoreAdapter` 已支援 shared store recovery，duplicate `event_id` 也不會再在 adapter query 結果裡重複出現
3. `smoke_test.py` 已改成真的使用 configured `feedback_store_path`，並驗證 cross-process recovery + idempotency

我重新跑了現有驗證：

- `python3 -m unittest test_feedback_adapter test_capture`（在 `services/telemetry/` 目錄）: `35` tests pass
- `python3 smoke_test.py`（在 `services/telemetry/` 目錄）: pass

但這一輪仍然不能通過，原因是 shared store query semantics 還有一個新的 contract 缺口：`FeedbackStoreAdapter` 會把 **trader feedback event** 和 **execution telemetry event** 混在一起回傳。這違反了 contract 明寫的「兩者是分開的 event family，只共享 linkage surface」，會直接污染 downstream evaluator / registry 對 execution telemetry 的查詢結果。

## Blocking Finding

### 1. shared feedback store 查詢沒有把 telemetry event family 與 trader feedback event family 分開

contract 明確要求 trader feedback 與 execution telemetry 是兩種不同 event family：

- `services/feedback/schema/contract.md:152`
- `services/feedback/schema/contract.md:165`
- `services/feedback/schema/contract.md:226`

但 `FeedbackStoreAdapter` 在 shared-store mode 下，實際上只靠 linkage filters 查資料：

- `_recover_from_store()` 直接把 `iter_events()` 的所有 event 全部載入 `self.telemetry_log`
- `get_telemetry_for_strategy()` 用 `build_query_filters(strategy_id=..., promotion_state=..., event_type=...)` 後直接回傳 `self.feedback_store.list(filters)`；若呼叫端沒有指定 `event_type`，任何同 `strategy_id` 的 feedback event 也會被算進來
- `get_telemetry_by_promotion_state()` 只用 `promotion_state` 過濾，一樣會把 feedback event 撈回來
- `query_telemetry()` 若只用 `strategy_id` / `registry_id` / `promotion_state` / `created_at` 查詢，也會混入非 telemetry 事件

- `services/telemetry/feedback_adapter.py:66`
- `services/telemetry/feedback_adapter.py:77`
- `services/telemetry/feedback_adapter.py:167`
- `services/telemetry/feedback_adapter.py:174`
- `services/telemetry/feedback_adapter.py:219`
- `services/telemetry/feedback_adapter.py:264`
- `services/telemetry/feedback_adapter.py:273`

最小重現如下：

```python
telemetry = {
    "event_id": "evt-t1",
    "event_type": "pnl_snapshot",
    "created_at": "2026-04-07T00:00:00Z",
    "execution_mode": "paper",
    "target": {"strategy_id": "strat-1", "promotion_state": "paper"},
    "metrics": {"pnl": 1.0},
}
feedback = {
    "event_id": "fb-1",
    "event_type": "approve",
    "created_at": "2026-04-07T00:05:00Z",
    "actor_id": "u1",
    "actor_role": "approver",
    "channel": "console",
    "target": {"strategy_id": "strat-1", "promotion_state": "paper"},
}

adapter = FeedbackStoreAdapter(feedback_store_path=store_path)
results = adapter.get_telemetry_for_strategy("strat-1")
```

實際結果：

- `results` 長度是 `2`
- event types 是 `['pnl_snapshot', 'approve']`
- `get_telemetry_by_promotion_state("paper")` 也會回 `['pnl_snapshot', 'approve']`

這代表 execution telemetry adapter 現在查到的是「shared store 裡同 linkage 的所有事件」，不是「shared store 裡屬於 telemetry family 的事件」。對 evaluator 來說，`approve`/`edit`/`reject`/`rationale` 這些 feedback event 混進 pnl / drawdown / fill / slippage query，是明顯的語義錯誤。

### 2. 現有測試與 smoke test 沒有覆蓋 mixed event family 情境

目前測試確實驗到了 shared store recovery 與 duplicate idempotency，但 coverage 仍缺一塊：

- `services/telemetry/test_feedback_adapter.py:61`
- `services/telemetry/test_feedback_adapter.py:105`
- `services/telemetry/smoke_test.py:190`
- `services/telemetry/smoke_test.py:197`

這些測試都只把 telemetry event 放進 store，沒有把 feedback event 與 telemetry event 混存在同一個 store 裡。因此現有 `35` 個 tests 全綠，仍無法證明 adapter 在真實 shared-store 場景下會維持 event family 邊界。

## Reviewer Decision

`FB-003` 需要再次退回修正。至少補齊以下兩點後再送 review：

1. 讓 `FeedbackStoreAdapter` 的 shared-store query semantics 只回傳 telemetry event family
2. 補一組 regression tests，明確驗證 shared store 同時含有 feedback + telemetry event 時，`get_telemetry_for_strategy()`、`get_telemetry_by_promotion_state()`、`query_telemetry()` 都不會混入 `approve` / `edit` / `reject` / `rationale`

前一輪的 recovery / idempotency / smoke test blocker 這次已確認修復，不再是目前的阻塞點。

---

## Update 2026-04-07: 第二輪 re-review 結果

Copilot 這一輪補上的 event family filter 與 mixed-family regression tests，本身方向是對的：

- `_recover_from_store()` 現在只會載入 telemetry family
- `get_telemetry_for_strategy()`、`get_telemetry_by_promotion_state()`、`query_telemetry()` 都會在 shared-store 查詢結果上再過濾一次 `TELEMETRY_EVENT_TYPES`
- `python3 -m unittest test_feedback_adapter test_capture` 現在是 `38` tests pass
- `python3 smoke_test.py` 仍然 pass

但 re-review 後發現 shared-store query semantics 仍然還沒真正符合 contract，因為 **`limit` 是在 event family 過濾之前就先被 feedback store 吃掉**。這會讓 telemetry query 在 mixed-family store 裡被截斷，甚至直接回空。

### New Blocking Finding

`TraderFeedbackStore.list()` 目前是這樣工作：

- 逐行讀 shared store
- 先用 linkage filter (`strategy_id` / `registry_id` / `promotion_state` / `created_at` / `event_type`) 做 `_matches()`
- 一旦累積到 `filters.limit` 就直接 `break`

見：

- `services/control-plane/feedback/store.py:110`
- `services/control-plane/feedback/store.py:116`

而 `FeedbackStoreAdapter` 的 telemetry family 過濾，是在 `list()` 回傳之後才做：

- `services/telemetry/feedback_adapter.py:196`
- `services/telemetry/feedback_adapter.py:204`
- `services/telemetry/feedback_adapter.py:254`
- `services/telemetry/feedback_adapter.py:258`
- `services/telemetry/feedback_adapter.py:311`
- `services/telemetry/feedback_adapter.py:323`

這代表只要 shared store 中有大量符合 linkage 的 feedback events 先出現，telemetry events 就算存在，也可能永遠到不了 adapter 的 family filter。

最小重現：

```python
adapter = FeedbackStoreAdapter(feedback_store_path=store_path)

for i in range(120):
    adapter.feedback_store.append(
        {
            "event_id": f"fb-{i}",
            "event_type": "approve",
            "created_at": f"2026-04-07T00:{i % 60:02d}:00Z",
            "actor_id": "u",
            "actor_role": "approver",
            "channel": "console",
            "target": {"strategy_id": "strat-x", "promotion_state": "paper"},
        }
    )

adapter.ingest_telemetry_event(
    {
        "event_id": "evt-1",
        "event_type": "pnl_snapshot",
        "created_at": "2026-04-07T02:00:00Z",
        "execution_mode": "paper",
        "target": {"strategy_id": "strat-x", "promotion_state": "paper"},
        "metrics": {"pnl": 1.0},
    },
    "strat-x",
    "paper",
)

results = adapter.get_telemetry_for_strategy("strat-x")
```

實測結果：

- `len(results) == 0`
- 不是因為 telemetry event 不存在，而是因為前 100 筆都被 `approve` events 先佔滿，`TraderFeedbackStore.list()` 在碰到 telemetry event 之前就停止了

這個問題同樣會影響：

- `get_telemetry_by_promotion_state()`（預設 `limit=100`）
- `query_telemetry(..., limit=N)`（會 under-return，甚至回 `0`，即使 store 裡還有符合條件的 telemetry events）

### 修正要求

`FB-003` 仍需再次退回。至少要補齊以下內容：

1. shared-store telemetry query 必須在 **telemetry family boundary 內** 套用 `limit`，不能讓 feedback events 先消耗查詢上限
2. 補一組 regression tests，明確驗證 mixed-family store 在大量 feedback events 先出現時：
   - `get_telemetry_for_strategy()` 不會因預設 limit 而回空
   - `get_telemetry_by_promotion_state()` 不會被 feedback events 截斷
   - `query_telemetry(limit=...)` 的 `limit` 是針對 telemetry results，而不是 shared store raw matches

結論不變：`FB-003` 這一輪仍然不能核准。

---

## Update 2026-04-07: 第三輪 re-review 結果

Copilot 這一輪新增的 regression tests 確實把我上次指出的 `limit=100` 問題測出來了，而且目前測試與 smoke test 也都是綠的：

- `python3 -m unittest test_feedback_adapter test_capture`: `41` tests pass
- `python3 smoke_test.py`: pass

但實作本身還是沒有真正把 shared-store query semantics 拉回 contract，而是把原本的截斷點從 `100` 改成一個新的任意常數 `1000000`：

- `services/telemetry/feedback_adapter.py:195`
- `services/telemetry/feedback_adapter.py:255`
- `services/telemetry/feedback_adapter.py:319`

三個 shared-store query path 現在都直接呼叫：

```python
build_query_filters(..., limit=1000000)
```

可是在底層 store，`TraderFeedbackStore.list()` 仍然是先對 raw matches 累積，再在碰到 `filters.limit` 時直接停止：

- `services/control-plane/feedback/store.py:110`
- `services/control-plane/feedback/store.py:116`

這表示目前的行為其實是：

1. 先在 shared store 中找出 linkage 上符合的所有 raw events
2. 最多只讀到第 `1000000` 個 raw match
3. 然後才在 adapter 端把 feedback family 過濾掉
4. 最後才把 caller 的 `limit` 套到 telemetry results

所以 contract 問題其實沒有被關掉，只是被往後推到第 `1000000` 筆。只要 shared store 中有超過 `1000000` 筆符合 linkage 的 feedback events 先出現，後面的 telemetry events 仍然永遠到不了 adapter 的 telemetry-family filter。

這會直接影響三條查詢路徑：

- `get_telemetry_for_strategy()`：沒有 `limit` 參數，但現在實際上最多只會看前 `1000000` 個 raw matches，不能保證回傳所有 matching telemetry events
- `get_telemetry_by_promotion_state()`：同樣被隱含硬上限截斷
- `query_telemetry(limit=N)`：只有當 telemetry event 出現在前 `1000000` 個 raw matches 內時，才真的能保證 `N` 是套在 telemetry family 上

### Blocking Finding

### 1. `limit=1000000` 只是更大的 magic number，shared-store telemetry query 仍然可能在 telemetry-family filter 之前被截斷

目前註解與 docstring 都聲稱「limit 是在 telemetry family filter 之後套用」，但程式邏輯其實不是這樣：

- `services/telemetry/feedback_adapter.py:196`
- `services/telemetry/feedback_adapter.py:202`
- `services/telemetry/feedback_adapter.py:256`
- `services/telemetry/feedback_adapter.py:260`
- `services/telemetry/feedback_adapter.py:320`
- `services/telemetry/feedback_adapter.py:329`
- `services/control-plane/feedback/store.py:110`
- `services/control-plane/feedback/store.py:116`

目前只是把底層 `TraderFeedbackStore.list()` 的 raw-match 上限改成一個比較大的數字，並沒有改變「先 raw truncate、再 family filter」這個事實。換句話說：

- 先前 bug 的 trigger condition 是「前 `100` 筆 raw matches 都是 feedback」
- 現在只是變成「前 `1000000` 筆 raw matches 都是 feedback」

這對 contract 來說仍然是 blocker，因為 query semantics 仍然不是在 telemetry family boundary 內套用，而是依賴 store 目前剛好還沒有長到那個數量。

### 修正要求

`FB-003` 需要再次退回。下一版至少要做到以下任一種真正關閉問題的做法：

1. 在 adapter 自己掃 `iter_events()`，先套 linkage + telemetry family filter，再對 telemetry results 套用 `limit`
2. 或把 store 查詢能力擴充到能在 storage/query layer 直接表達 event family 邊界，而不是靠 caller 傳一個巨大的 magic number

另外，測試也要補上對這個語義本身的覆蓋，而不是只證明 `100` 改成 `1000000` 後現有樣例會過。

結論更新：

- event family separation 本身沒有再回退
- 但 shared-store query contract 仍未完全成立
- `FB-003` 這一輪仍然不能核准
