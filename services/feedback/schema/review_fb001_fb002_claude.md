# FB-001 + FB-002 Feedback Schema Review (Claude)

**Tasks:** FB-001, FB-002  
**Author:** Codex  
**Reviewer:** Claude  
**Status:** APPROVED with minor items noted

---

## 結論

FB-001 trajectory & preference store contract 以及 FB-002 trader feedback event schema 可以通過。
governance 邊界清楚，event family 分離正確，linkage 物件對後續 evaluator/optimizer 工作足夠。
以下列出三個 minor 項目，不阻擋 v1 鎖定。

---

## FB-001 Review

### 1. APPROVED: governance 邊界清楚，feedback 不能直接觸發 live 操作

- Contract §2.2 明確列出 store **可以影響**：evaluators、critics、preference-learning pipelines、optimizer inputs
- **不可觸發**：live promotion、direct LEAN mutation、direct policy replacement
- 這和 OC-001 deny-first 模型一致 — feedback 是 learning input，不是執行觸發器
- **確認：governance 邊界正確且明確。**

---

### 2. APPROVED: event family 分離正確

- `trader_feedback` 和 `execution_telemetry` 是分開的 schema 檔案，不共用 base type
- 設計原則說得清楚：「不混淆人類 preference signal 和市場執行觀察」
- 兩個 family 共用 `target` linkage object shape（§4），但各自有不同的必填欄位——這個設計合理，共享結構但不強迫 merge
- **確認：family 分離正確。**

---

### 3. APPROVED: linkage 物件對 evaluator/optimizer 工作足夠

- `strategy_id`（必填）是最小錨點，後續 join 到 REG-001 有路徑
- `registry_id`（選填）允許精確 join，但不強制——適合 v1 尚未有完整 registry 的情境
- §4 說明：「如果任何欄位在事件時間不確定，應明確省略而非靜默推斷」—— 正確的設計原則
- **一個觀察：** 如果 `registry_id` 缺失，`strategy_id + artifact_version + promotion_state` 的組合通常可以重建，但多版本並存時可能不唯一。建議 FB-003/EV-001 落地時重新評估是否把 `registry_id` 升級為必填。

---

## FB-002 Review

### 4. APPROVED: trader feedback event schema 正確覆蓋 approve/edit/reject/rationale

- `event_type` enum：`["approve", "edit", "reject", "rationale"]` — 完整
- `actor_role` enum：`["operator", "approver", "reviewer", "system"]` — 合理
- `edits` 陣列：支援 `replace / append / remove / annotate` 操作 — 彈性適中
- **重要邊界（contract §5.3）：** `edit` 不是靜默覆寫，是「人類修正的事件記錄」。任何實質化更新的 artifact 仍需經過 registry 和 promotion gate——這個邊界正確且必要。

---

## Minor Items

### M-1: `actor_role` 包含 "reviewer" 但 OC-001 role enum 沒有這個值

- OC-001 `_CHANNEL_ROLE` 定義的角色：`persona | operator | approver | system`
- trader_feedback schema 的 `actor_role` 加了 `"reviewer"` 不在上面的清單
- **影響：** 如果 router 或 governance service 試圖從 channel role 推斷 actor_role，這個不一致可能導致 reject
- **建議：** 在 FB-002 follow-up 或 OC-001 update 時，確認 `reviewer` 是否是 `approver` 的子角色，或加入 OC-001 allowed role list

### M-2: execution_telemetry `promotion_state` 不含 "draft"，trader_feedback 含

- `execution_telemetry_event.schema.json`：`promotion_state` enum = `["candidate", "paper", "live", "retired"]`
- `trader_feedback_event.schema.json`：`promotion_state` enum = `["draft", "candidate", "paper", "live", "retired"]`
- **這個設計是有意的**（不會對 draft artifact 做 execution telemetry），但值得明文說明，避免後來的讀者誤以為是遺漏
- **建議：** 在 contract §6 加一行說明「telemetry 不記錄 draft state，因為 draft artifact 不可執行」

### M-3: `edits.operation` 的 "annotate" 不是標準 JSON Patch 操作

- `replace / append / remove` 接近 JSON Patch RFC 6902 概念
- `annotate` 是自定義操作（附加說明而不修改值）
- **這不是 bug** — 對交易員 UX 來說 annotate 是有用的操作
- **建議：** 在 schema 的 `operation` property 加 `$comment` 說明 annotate 的語意，避免實作端誤解

---

## 不要求這輪做完的事

- `actor_role` 和 OC-001 role 對齊（建議在 FB-002 follow-up 處理）
- `registry_id` 是否升必填（建議 EV-001 落地前評估）
- `annotate` operation 的 `$comment` 文件（低優先）
- storage backend 選型（FB-001 §7 明確說「backend 是 open 的」，合理延後）

---

## 結論

**FB-001 APPROVED for v1 lock。**  
**FB-002 APPROVED for v1 lock。**

governance 邊界正確，event 家族分離清楚，linkage 對 REG-001 / EV-001 後續工作足夠。
三個 minor items 不阻擋落地。
