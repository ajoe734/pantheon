# 2026-04-20 Execution Task List From Gap Audit

## 目的

把 [current implementation vs blueprint gap audit](/home/edna/code/pantheon/docs/reviews/2026-04-20-current-implementation-vs-blueprint-gap-audit.md:1) 轉成可直接派工的 execution 清單。

原則：

- `route-live` / `contract-live` 模組優先進 front-end implementation 或 handoff activation。
- `contract_ready` 但 route 未 live 的模組進 BFF implementation。
- `blocked` 模組不直接丟 implementation，先等 system design / contract lock。

---

## A. 最高優先級：已 live backend 的前端接線

### A1. 直接接 live BFF route 的 production UI

1. `EXEC-FRONT-EW04-001`
   - 任務：把 `EW-04` 從 placeholder 換成真正的 inspiration graph page。
   - 依據：`GET /api/v1/lineage/inspiration/{artifact_id}` 已 live。
   - 目前狀態：已在 board 上，但尚未完成。

2. `EXEC-FRONT-RW01-001`
   - 任務：實作 `/research/tickets`、`/research/tickets/:ticket_id`。
   - 依據：`RW-01` create/list/detail/patch routes 已 live。
   - 目前狀態：已在 board 上，待開始。

3. `EXEC-FRONT-RW02-001`
   - 任務：實作 `/research/search`。
   - 依據：`RW-02` search route 已 live。
   - 目前狀態：已在 board 上，待開始。

4. `EXEC-FRONT-RW03-001`
   - 任務：新增 `/research/analyze` 對應 canonical UI。
   - 依據：`RW-03` analysis list/detail routes 已 live。
   - 目前狀態：尚未 materialize 成 execution task。

5. `EXEC-FRONT-RW04-001`
   - 任務：實作 `/research/experiments` 與 detail flow。
   - 依據：`RW-04` launch/history/detail/cancel routes 已 live。
   - 目前狀態：先等 handoff refresh 完成。

6. `EXEC-FRONT-TW01-001`
   - 任務：實作 `/trainer/sessions`、`/trainer/sessions/:session_id`。
   - 依據：`TW-01` trainer dialog routes 已 live。
   - 目前狀態：已在 board 上，待開始。

7. `EXEC-FRONT-CW03-PARTIAL-001`
   - 任務：依 partial activation rule 實作 `/consultation/committees`、`/consultation/committees/:committee_id` 的 read-only / sponsor-status / outcome-summary 版本。
   - 依據：`CW-03` list/detail routes 已 live，但 full handoff 仍受 `CW-02` gate 影響。
   - 目前狀態：尚未 materialize。

### A2. 已回傳前端成果，需要 review / finalize

1. `EXEC-FRONT-EW05-001`
   - 任務：完成 review disposition，收斂 `EW-05` UI return。
   - 目前狀態：`review`

2. `EXEC-FRONT-KW01-001`
   - 任務：完成 review disposition，收斂 `KW-01` UI return。
   - 目前狀態：`review`

---

## B. 高優先級：handoff / coordination 補齊

1. `EXEC-REBASE-EW04-001`
   - 任務：把 `EW-04` / `PKT-003` coordination bundle 全面 rebaseline 到 route-live truth。
   - 補齊後才能避免前端繼續依舊 placeholder gate 工作。

2. `EXEC-REBASE-RW04-001`
   - 任務：補齊 `RW-04` frontend handoff bundle。
   - 已知缺口：缺 lovable task 參考的 example templates。

3. `EXEC-REBASE-TW03-001`
   - 任務：建立 `TW-03` frontend handoff bundle、lovable task、必要模板。
   - 現況：route live，但 handoff 缺失。

4. `EXEC-REBASE-TW04-001`
   - 任務：建立 `TW-04` frontend handoff bundle、修正 `screen_id` 漂移、同步 packet / backlog truth。
   - 現況：route live，但 handoff 缺失且 review 已退回。

5. `EXEC-REBASE-BACKLOG-SA-001`
   - 任務：把 backlog / frontend SA / packet family 裡仍停在 pending-BFF 的路線更新成實際 truth。

---

## C. 中優先級：真實缺失的 backend implementation

### C1. 可直接進 implementation 的 `contract_ready` 模組

1. `EXEC-BFF-RW05-001`
   - 任務：實作 `RW-05` artifact registry / detail / compare / version ancestry routes。
   - 條件：不再需要 architecture clarification。

2. `EXEC-BFF-KW02-001`
   - 任務：實作 `KW-02` notes create/list/detail。

3. `EXEC-BFF-KW03-001`
   - 任務：實作 `KW-03` evidence list/detail。

4. `EXEC-BFF-KW04-001`
   - 任務：實作 `KW-04` insight aggregation/detail。

### C2. 目前仍 blocked，不直接派 implementation

以下不建議現在切成一般 implementation task：

- `CW-02`
- `CW-04`
- `TW-02`
- `KW-05`

這些先等 system design / contract follow-up 回覆後再 materialize。

---

## D. 中優先級：前端 closeout / record sync

1. `EXEC-CLOSEOUT-FRONTEND-001`
   - 任務：批次 finalize `frontend_feedback_reviewed` 與 `ui_done_reviewed` loops。

建議先收斂：

- `F-042`
- `PKT-001` 到 `PKT-014`
- `PKT-consultation-workbench`
- `PKT-knowledge-workbench`
- `EW-05`
- `KW-01`

---

## E. 較低優先級：OSS next-wave

1. `EXEC-OSS-RL-001`
2. `EXEC-OSS-WANDB-001`
3. `EXEC-OSS-VECTORBT-001`
4. `EXEC-OSS-STATSMODELS-001`
5. `EXEC-OSS-QUANTLIB-001`

這一層不屬於主 workbench gap 閉環關鍵路徑，可在 productization 主線穩住後持續推進。

---

## 建議執行順序

1. `EW-04` / `RW-04` / `TW-03` / `TW-04` handoff rebaseline
2. `EW-04`、`RW-01`、`RW-02`、`RW-03`、`RW-04`、`TW-01` 前端 canonical route 接線
3. `CW-03` partial activation UI
4. `RW-05`、`KW-02`、`KW-03`、`KW-04` backend implementation
5. frontend closeout
6. OSS next-wave
