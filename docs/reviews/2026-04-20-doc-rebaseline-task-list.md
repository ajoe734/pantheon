# 2026-04-20 Doc Rebaseline Task List

## 目的

把目前 repo 中已確認的 truth drift，整理成文件 rebaseline 清單。

這份清單只處理「文件沒跟上真實實作或 ratification」的項目，不處理真正缺 implementation 的模組。

---

## A. 必改：會直接誤導 execution 派工的文件

### A1. `WORKBENCH_DELIVERY_BACKLOG.md`

需要修正：

1. `RW-05`
   - 從 `not ready` 改為 `contract_ready / pending BFF implementation`

2. `KW-02`、`KW-03`、`KW-04`
   - 從 `module not ready` 改為 `contract_ready / pending BFF`

3. `KW-05`
   - 保持 `blocked`，不要和 `KW-02~04` 混寫

4. `CW-03`
   - 改成 partial activation rule
   - 不能再寫成必須完全等 `CW-01` / `CW-02` live 才能開始任何前端 lane

5. `TW-03`、`TW-04`
   - 不可再寫成 `BFF implementation pending`
   - 應改為「route live；handoff / frontend activation pending」

### A2. `docs/lovable/PANTHEON_FRONTEND_SA.md`

需要修正：

1. workbench summary
   - `RW-05` 不應再被視為純 blocked 缺 contract
   - `KW-02~04` 不應再一律寫成 blocked / shell-only
   - `CW-03` 應改為 partial activation allowed

2. route map
   - `/research/experiments` 不應再寫成 pending BFF
   - `/knowledge/notes`、`/knowledge/evidence`、`/knowledge/insights` 需要改成 `contract_ready / pending BFF`
   - `/consultation/committees*` 需要改成 partial activation rule

3. implementation order
   - 補上已 route-live modules 的 canonical front-end lane priority

---

## B. 必改：packet family truth drift

### B1. `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`

需要修正：

1. header 的 `all ready`
2. module inventory 中 `KW-02~05` 的 `ready`
3. `KW-02~05` sections 內的 `implemented / resolved`
4. backend gap matrix 中 `resolved` overclaim

改後應對齊：

- `KW-02`：`contract_ready / pending BFF`
- `KW-03`：`contract_ready / pending BFF`
- `KW-04`：`contract_ready / pending BFF`
- `KW-05`：`blocked`

### B2. `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`

需要修正：

1. `CW-03` inventory 不可再寫 `contract-published; pending-bff`
2. `CW-03` backend gaps 不可再寫 `GET /api/v1/committees*` missing
3. `CW-03` promotion rule 需改成 partial activation

### B3. `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`

需要確認：

1. `RW-05` wording 是否已完全改成 `contract published / pending BFF implementation`
2. 不可再出現「缺 contract」類描述

---

## C. 必改：overview example JSON 與 BFF overview truth

### C1. `docs/examples/PKT-consultation-workbench.json`

需要修正：

1. `CW-01` 不可再是 `not_ready`
2. `CW-03` 不可再寫成 missing route / missing committee projection
3. `packet_family.note` 不可再說四個模組全部 blocked

### C2. `docs/examples/PKT-knowledge-workbench.json`

需要修正：

1. `KW-02~04` 不可再一律 `not_ready`
2. `packet_family.note` 需改成 `KW-02~04 contract_ready / pending BFF`
3. `KW-05` 才是仍 blocked 的那個

### C3. `services/control-plane/bff/main.py` overview builders

需要修正：

1. consultation overview builder
   - `CW-01` / `CW-03` 的 summary / note 要對齊目前 route-live truth

2. knowledge overview builder
   - `KW-02~04` 不可再維持 `not_ready`
   - `KW-05` 需保留 blocked

---

## D. 必改：coordination / handoff truth

### D1. `EW-04`

需要修正：

1. `.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml`
2. `.coordination/responses/PKT-003-inspiration-graph-contract-ready.yaml`
3. 相關 lovable prompt / ui-task

目標：

- 全部改成 route-live truth
- 不再讓前端讀到「BFF route 尚未 live」

### D2. `RW-04`

需要修正：

1. lovable task 引用但不存在的 example templates
2. handoff bundle completeness

### D3. `TW-03`

需要補齊：

1. `FRONTEND_CHANGE_SPEC.md`
2. lovable ui-task
3. example templates

### D4. `TW-04`

需要補齊：

1. `FRONTEND_CHANGE_SPEC.md`
2. bff-gap / ui-done templates
3. `screen_id` 一致性
4. backlog / packet family truth sync

---

## E. 最佳執行順序

1. `WORKBENCH_DELIVERY_BACKLOG.md`
2. `docs/lovable/PANTHEON_FRONTEND_SA.md`
3. `KW-006` / `CW-008` packet family
4. overview example JSON
5. BFF overview builder wording
6. coordination / handoff bundle truth
