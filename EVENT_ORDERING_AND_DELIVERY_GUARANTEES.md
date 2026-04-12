# EVENT_ORDERING_AND_DELIVERY_GUARANTEES

Last updated: 2026-04-09
Status: canonical event ordering and delivery policy for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: event ordering, delivery guarantees (at-least-once), idempotency, and replay principles for the Pantheon event model
Conflict rule: this document defines messaging semantics; it overrides generic "event driven" mentions in planning docs

## 1. 目的

本文件定義 Pantheon 事件模型中的 ordering、delivery guarantees、idempotency 與 replay 原則。

目標是回答：

- 是否需要全域事件排序
- `strategy.approved` 是否必須晚於 `strategy.submitted`
- 用什麼單位保證順序
- event loss / duplicate 該怎麼處理

---

## 2. 結論摘要

### 2.1 不追求全域 ordering
Pantheon **不要求 global total ordering**。

### 2.2 必須有 per-aggregate ordering
Pantheon 對關鍵 aggregate 採：

- `aggregate_type`
- `aggregate_id`
- `sequence_no`
- `causal_parent_id`

來保證同一 aggregate 內的事件順序。

### 2.3 採 at-least-once delivery
delivery guarantee 採：
- at-least-once
- consumer idempotency
- outbox pattern
- replay support

### 2.4 關鍵事件不能依賴「碰巧按時間先後到」
順序語義不能只靠 `event_time` 或 broker 順序「看起來正確」。

---

## 3. aggregate 定義

下列對象視為關鍵 aggregate：

- strategy
- artifact
- approval decision
- deployment plan
- runtime binding
- runtime
- incident case
- evolution decision
- trainer session
- consult request

---

## 4. 事件必備欄位

```text
event_id
event_type
aggregate_type
aggregate_id
sequence_no
causal_parent_id
event_time
emitted_at
trace_id
idempotency_key
payload
```

### sequence_no
同一 aggregate 單調遞增。

### causal_parent_id
用來表示：
- 由哪個事件導出
- 可支持局部因果鏈重建

---

## 5. 順序規則

## 5.1 同 aggregate 必須保序
例：
- `strategy.submitted`
- `strategy.reviewed`
- `strategy.approved`
- `strategy.retired`

這些對同一 `strategy_id` 必須保序。

## 5.2 跨 aggregate 不保證全域排序
例：
- 某個 runtime 事件與某個 incident 事件  
不要求全域 total order，只要求能由 trace / causal chain 連回。

---

## 6. delivery guarantee

## 6.1 outbox pattern
所有重要 domain event 都必須由 write owner service 以 outbox 發送。

## 6.2 at-least-once
consumer 必須容忍重複投遞。

## 6.3 idempotent consumer
消費者必須用：
- event_id
- idempotency_key
- aggregate sequence

來去重 / 保證不重複副作用。

---

## 7. replay 規則

必須支援：
- by aggregate replay
- by time window replay
- by event type replay

replay 時：
- 不重複創造 side effect
- consumer 要有 rebuild mode / dry-run mode

---

## 8. queue / stream 原則

### 8.1 keyed ordering
對同一 aggregate key，message transport 應盡量保序。

### 8.2 no dependence on broker-wide order
不得假設整個 queue topic 內所有事件天然有全域順序。

---

## 9. 典型例子

## 9.1 strategy lifecycle
以 `strategy_id` 為 aggregate：
- submitted -> reviewed -> approved -> retired

## 9.2 deployment lifecycle
以 `deployment_plan_id` 為 aggregate：
- planned -> approved -> dispatched -> completed / failed

## 9.3 runtime lifecycle
以 `runtime_id` / `binding_id` 為 aggregate：
- binding_created -> loading -> active -> paused -> replaced

---

## 10. v1 決策

1. 不做 global total ordering
2. 強制 per-aggregate ordering
3. 關鍵事件走 outbox
4. delivery = at-least-once
5. consumer 必須 idempotent
6. replay 是第一級能力

---

## 11. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下項目屬於後續 delivery / ordering 細化，不是本文件目前生效的前置條件。

- event naming convention
- aggregate-specific sequence allocation
- replay operational runbook
- dead-letter / poison message policy

---

## 12. DEP-002 Implementation Anchor

`DEP-002` 以 `deployment_saga` aggregate 實作本文件的 ordering / idempotency 規則：

- `services/control-plane/governance/deployment_saga.py`
- `services/control-plane/governance/deployment_saga.contract.md`

實作重點：

- 只保證 `deployment_saga` 內的 `sequence_no` 單調遞增
- consumer 以 `event_id` + `idempotency_key` + per-aggregate sequence 做 dedupe
- 缺序事件標記為 `out_of_order`，不直接套用 side effect
