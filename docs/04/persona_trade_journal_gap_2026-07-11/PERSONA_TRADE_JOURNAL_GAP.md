# Persona Trade Journal 與逐筆交易反思 Gap

日期：2026-07-11
狀態：execution-ready design；尚未宣稱實作完成
範圍：Pantheon BFF、telemetry/lineage、persona OODA/memory、`execute-plans` 管理前端

## 1. 問題與目標

目前交易成交、Persona 決策日誌、績效歸因、Learn writeback 與 memory/evaluation
各自存在，但 operator 無法從一筆買進一路回答：誰決定、依據是什麼、送出哪些
orders、實際如何 fills、何時平倉、損益如何、Persona 如何反思、lesson 是否被採用。

目標是建立 Persona-scoped Trade Journal，讓每個 Persona 的交易可重播、可解釋、
可反思、可治理，同時不建立第二套 order/fill 或 P&L 真相來源。

## 2. 現況 Gap

| Gap | 現況 | 目標 |
|---|---|---|
| G1 identity | `trace_id` 可追服務，但沒有穩定的完整交易單位 | `trade_episode_id` 串 entry、加減碼、exit；每個 event 仍保留 `trace_id` |
| G2 execution truth | fills/positions 在 telemetry/runtime projection | journal 只引用 canonical order/fill/position/P&L，不複製 authority |
| G3 rationale | decision journal 不保證逐筆對上 execution | 每個 intent/proposal/order 帶 decision、evidence、risk snapshot refs |
| G4 reflection | Learn/memory 存在，但不是逐筆結構化反思 | fill review、episode reflection、periodic pattern review 三層 |
| G5 lifecycle | 不清楚何時算一筆交易結束 | 明確 episode state machine 與 partial-fill/scale-in/scale-out/reversal 規則 |
| G6 governance | lesson 可能被誤讀為已修改 persona | reflection 只產生候選 lesson；endorse/merge/quarantine 有審核與 receipt |
| G7 UI | 紀錄、理由、反思分散 | Persona → Trade Journal 列表、episode detail、reflection inbox、pattern view |
| G8 honesty | 缺資料時可能呈現推測內容 | `complete/partial/degraded/unavailable` coverage，缺失欄位不得由 LLM 補造 |

## 3. 邊界與真相來源

- Runtime Manager / execution telemetry：order、ack、reject、cancel、fill、position。
- Canonical valuation/attribution：realized/unrealized P&L、fees、slippage、benchmark。
- Decision/lineage evidence：intent、market snapshot、research evidence、risk decision。
- Persona domain：reflection、lesson candidate、memory review state。
- BFF：唯讀聚合與 governed commands；不得成為 execution 或 memory 的 shadow store。
- 前端：呈現聚合結果；不得自行推導正式 P&L 或假裝缺失 reflection 已存在。

## 4. 核心識別與關聯

`trade_episode_id` 是一個 Persona 對一個 instrument/strategy direction 的完整經濟意圖，
從第一個 approved intent 到曝險回到零或被明確轉向為止。

```text
persona_id
  └─ trade_episode_id
       ├─ decision_id / proposal_id / evidence_refs[]
       ├─ trace_ids[]
       ├─ order_ids[]
       │    └─ fill_ids[]
       ├─ position_snapshot_refs[]
       ├─ attribution_ref
       └─ reflection_id
            └─ lesson_candidate_ids[]
```

必要欄位：`environment`、`persona_id`、`strategy_id`、`artifact_id/version`、
`runtime_binding_id`、`capital_pool_id`、`instrument_id`、`side/direction`、時間與來源版本。

關聯規則：

1. 同一 thesis 的 partial fills、加碼與減碼留在同一 episode。
2. 曝險歸零後 episode 關閉；下一次 entry 建新 episode。
3. long 直接反轉 short 時，先關閉 long episode，再開 short episode；不可共用 P&L。
4. cancel/reject-only intent 仍保留 `aborted` episode，供決策品質反思，但不算成交交易。
5. 人工 intervention、risk liquidation、kill-switch 必須標記 `exit_actor` 與 cause。
6. 無法可靠 join 的歷史資料標為 `unresolved`，不得用時間接近度默默硬配。

## 5. Episode 狀態機

```text
proposed → approved → submitted → partially_filled → open
   └──────────────→ rejected/cancelled/aborted
open → reducing → closed → reflection_pending → reflected
open/reducing → force_closed → reflection_pending
reflection_pending → reflection_failed → reflection_pending (audited retry)
```

事件採 append-only；late/out-of-order event 依 canonical event time、sequence 與 ingestion
cursor 重建 projection。更正使用 correction event，不覆寫原始事實。

## 6. 資料契約

### 6.1 `TradeEpisodeProjection`

- identity 與 refs：上述必要關聯欄位。
- lifecycle：`status`、`opened_at`、`closed_at`、`entry/exit_actor`、`exit_reason`。
- execution summary：requested/filled/remaining quantity、VWAP、fees、slippage、rejects。
- outcome：realized/unrealized P&L、return、MAE、MFE、holding duration、benchmark delta。
- rationale：thesis、expected catalyst、invalidation conditions、time horizon、confidence；皆附 source ref。
- risk snapshot：limits、expected loss、stop/exit plan、approval refs。
- coverage：每一區塊的 `state`、`missing_refs[]`、`as_of`、`source_system`。
- reflection summary 與 memory governance refs。

Projection 是可重建 read model，不是 canonical execution record。

### 6.2 `PersonaTradeReflection`

- `reflection_id`、`trade_episode_id`、`persona_id`、`reflection_version`。
- `trigger`: `fill_review | episode_closed | scheduled_pattern | manual_retry`。
- `facts_snapshot_ref` 與內容 hash；反思不得改寫輸入事實。
- `expected_vs_actual`: thesis、entry quality、exit quality、sizing、timing、risk adherence。
- `counterfactuals[]`: 替代行動、估計影響、假設與不確定性；必須標為 counterfactual。
- `attribution`: process / market / execution / risk / data quality，不把結果好壞等同決策好壞。
- `mistakes[]`、`what_worked[]`、`unknowns[]`、`followups[]`。
- `lesson_candidates[]`: scope、proposed change、supporting episode ids、confidence、expiry。
- `model/provider/prompt/template/version`、生成時間、evidence coverage、review state。

### 6.3 Memory governance

lesson state：`proposed → pending_review → endorsed → merged`，或 `quarantined/rejected/expired`。
單一交易只能提出 candidate。涉及 route policy、risk limit、capital allocation、artifact 或 live
行為的變更，必須走既有 evaluation、approval、deployment 流程；reflection 無直接 mutation authority。

## 7. 反思機制

### A. Fill review（近即時、低成本）

每次重要 fill/reject/cancel 後記錄 execution variance、risk adherence 與資料缺口；不急著得出
策略 lesson，也不觸發 mutation。

### B. Episode reflection（平倉後）

等待 canonical attribution watermark 完成後，以 immutable facts snapshot 產生完整反思。
若 valuation 或 joins 未齊，進 `reflection_pending`；超時只能產生明確標示 partial 的反思。

### C. Pattern review（每日/每週或樣本門檻）

跨 episode 比較相同 regime、strategy、instrument 與 mistake taxonomy。只有達到最低樣本、
跨時段支持與 evaluation 通過，才可把 lesson 候選送入 governance review。

### 品質防線

- LLM 只能解釋提供的 facts/evidence；缺失即寫 unknown。
- reflection 與 outcome 分離評分，避免 hindsight bias。
- 保留 prompt/model/version 以便 replay；禁止靜默覆寫。
- paper/canary/live 分層，不以 paper lesson 自動改 live。
- 敏感 broker/account 欄位依角色遮罩；所有讀取與 review command 可稽核。

## 8. API / Event 設計

### BFF read APIs

```http
GET /bff/personas/{persona_id}/trade-journal
GET /bff/personas/{persona_id}/trade-journal/{trade_episode_id}
GET /bff/personas/{persona_id}/trade-reflections
GET /bff/personas/{persona_id}/trade-patterns
```

列表支援 cursor、期間、environment、strategy、instrument、side、status、outcome、reflection
state、coverage state。detail 回傳 timeline、canonical refs、rationale、execution、attribution、
reflection 與 governance receipts。

### Governed commands

```http
POST /bff/personas/{persona_id}/trade-journal/{episode_id}/reflection:retry
POST /bff/personas/{persona_id}/trade-lessons/{lesson_id}:submit-review
POST /bff/personas/{persona_id}/trade-lessons/{lesson_id}:decide
```

所有 POST 需要 RBAC、`Idempotency-Key`、reason、audit receipt；retry 不得改變 facts snapshot。

### Events

`trade_episode.opened|updated|closed|unresolved`、`trade_reflection.requested|completed|failed`、
`trade_lesson.proposed|reviewed|merged|quarantined`。Envelope 必須含 event id、schema version、
occurred/ingested time、trace、episode、persona、environment、producer 與 causation/correlation ids。

## 9. 前端資訊架構

Persona detail 新增 `Trade Journal`：

- Summary：交易數、勝率只能作描述；另顯示 expectancy、P&L、fees/slippage、coverage、pending reflection。
- Episode table：時間、商品、方向、entry/exit、quantity、P&L、decision quality、reflection state。
- Episode detail drawer/page：Why → Timeline → Execution → Outcome → Reflection → Lessons/Audit。
- Timeline 清楚區分 intent、approval、order、fill、risk intervention、close、reflection。
- Reflection inbox：pending/failed/partial/candidate review，不把 LLM 文字當 canonical fact。
- Pattern view：依 mistake/process/regime 聚合，顯示 sample size 與 uncertainty。
- Deep links：Performance Attribution、Decision Journal、Lineage Explorer、Memory Review、Human Review。

預設 desktop/mobile 都要可用；live 與 paper 必須有持續可見的 environment 標籤。

## 10. SLO、保留與可觀測性

- fill projection freshness：paper/canary/live 分別量測，目標 p95 ≤ 60s（paper）與 ≤ 15s（canary/live readback）。
- closed episode 在 attribution watermark 後 5 分鐘內建立 reflection request。
- journal coverage、unresolved joins、reflection queue age/failure、lesson review age 都有 metrics/alerts。
- retention 依 canonical telemetry/memory policy；projection 可刪除重建，reflection version 與 receipt 不可失真。

## 11. 驗收場景

1. 一筆完整 paper long trade：decision → partial fills → scale-out → close → reflection → lesson review。
2. rejected/cancelled、partial fill、force close、manual intervention、reversal。
3. late/duplicate/out-of-order events 可 idempotent replay。
4. 缺 decision、P&L 或 reflection 時明確 partial/degraded，不生成假理由。
5. Persona 與期間 filter 不洩漏其他 persona/account 資料。
6. reflection retry 可重播且保留版本；lesson 不可繞過 governance 改 policy/capital/live。
7. hosted FE 能從 Persona Journal 深連到原始 order/fill、attribution、decision、memory receipt。

## 12. 非目標

- 本 wave 不讓 Persona 自主改 live policy、risk 或資金。
- 不用 BFF/前端取代 broker/runtime/telemetry 的 canonical truth。
- 不以生成式文字填補缺失 execution facts。
- 不在本設計中執行任何 live order。

## 13. 完成定義

只有當 canonical joins、reflection worker、BFF、frontend、governance、replay/hosted acceptance
全部通過，才可稱 Persona Trade Journal 完成。僅有 UI mock、LLM summary 或 local test 都不算完成。

