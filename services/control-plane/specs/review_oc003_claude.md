# OC-003 StrategySpec & WorkflowHandoff Contract — Review (Claude)

**Task:** OC-003  
**Author:** Codex  
**Reviewer:** Claude  
**Status:** APPROVED with two minor open items

---

## 結論

OC-003 contract 可以通過。StrategySpec 和 WorkflowHandoff 的邊界定義清楚，governance_context 足以和 OC-001 / P4-001 對齊，registry_hints 也不會過度耦合到 storage 細節。

以下列出兩個 minor 問題，不阻擋 v1 鎖定。

---

## Findings

### 1. APPROVED: StrategySpec 邊界正確，沒有洩漏執行細節

- `execution_profile` 只記錄 `signal_schema_version`、`quantity_type`、`rebalance_cadence`、`execution_mode_hint`（research/paper/live），沒有 broker order 細節或 LEAN 特定欄位。
- 這個邊界是對的：StrategySpec 描述「意圖」，executor.py 負責「怎麼打」。
- `execution_mode_hint` 的 enum `["research", "paper", "live"]` 和 OC-001 `execution_context` 一致。

**確認：StrategySpec 不洩漏執行細節。**

---

### 2. APPROVED: WorkflowHandoff governance_context 足夠

- `approval_required`（必填）+ `execution_context`（必填）讓下游系統知道要不要走 approval flow。
- 這和 P4-001 router 的 `ALLOW_WITH_APPROVAL` 機制對齊：router 先判斷 `allow_with_approval`，下游 Governance service 攔截持有等待審批。
- `policy_id`（選填）允許連結到 OC-001 具體 policy object，但不強制，對 v1 合理。

**確認：governance_context 和 OC-001 / P4-001 對齊。**

---

### 3. APPROVED: registry_hints 不過度耦合 storage 細節

- `artifact_type` enum：`strategy_spec | model_artifact | feature_set | prompt_bundle | signal_snapshot | execution_bundle` — 是邏輯分類，不是 storage 路徑或 DB 表名。
- `initial_lifecycle_state` 只允許 `draft | candidate`，禁止 `paper`/`live` 從 handoff 層直接注入 — 正確，REG-002 promotion gate 負責之後的狀態躍遷。
- `lineage_ref` 和 `producer_run_id` 都是 optional，讓 RS-002 可以填入，RS-001 raw ingestion 也可以不填。

**確認：registry_hints 對 REG-001 / REG-002 是足夠且不過度耦合的。**

---

### 4. MINOR: `strategy_spec` 的 inline vs ref 二選一可能產生驗證歧義

- `workflow_handoff.schema.json` 的 `strategy_spec` 欄位用 `oneOf`：要麼是完整 StrategySpec inline，要麼是 `{strategy_id, spec_ref}` 的 ref 形式。
- **問題：** 兩個 branch 沒有 discriminator。一個帶了 `spec_version` 的物件可以匹配第一個 branch，但如果同時帶了 `spec_ref`，jsonschema validator 行為可能不一致（additionalProperties: false 在 branch 1 會拒絕 `spec_ref`，但 oneOf 的錯誤訊息可能讓人困惑）。
- **這不是 blocker：** 兩個 branch 結構差異夠大（branch 1 有 spec_version，branch 2 有 spec_ref），實際上不會衝突。只是錯誤訊息不清晰。
- **建議：** 可以加 `$comment` 說明兩個 branch 的使用情境，或在 RS-002 實作時確認 validator 行為。

---

### 5. MINOR: `from_stage` / `to_stage` 是 free-form string，可能導致 stage 名稱不一致

- Contract §3.2 定義了 `handoff_type` enum，但 `from_stage` / `to_stage` 是 `type: string`，沒有 enum 限制。
- **問題：** 不同 workflow 可能用不同字串表示同一個 stage（`"research_ingestion"` vs `"rs-001"` vs `"ingest"`）。
- **這不是 blocker：** v1 只有一個 pipeline，一致性可以用 convention 維持。
- **建議：** OC-003 follow-up 或 RS-002 落地時，考慮把 stage 名稱收斂成一個 canonical enum 或至少一份 stage registry。

---

## 不要求這輪做完的事

- `from_stage` / `to_stage` enum 化（後續 RS-002 或 OC-003 follow-up）
- `strategy_spec` oneOf 加 `$comment` 說明（低優先）
- `execution_mode_hint` 和 OC-001 `execution_context_allowlist` 的語意對齊確認（看起來一致，但沒有機器可讀的 cross-reference）

---

## 結論

**OC-003 APPROVED for v1 lock。**

StrategySpec 邊界正確，WorkflowHandoff 帶足夠的治理上下文，registry_hints 不過度耦合，兩個 minor open items 不阻擋落地。
