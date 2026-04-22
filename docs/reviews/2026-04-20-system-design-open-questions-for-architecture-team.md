# 2026-04-20 System Design Open Questions For Architecture Team

## 目的

本文件原本只整理尚未定案的 system design 問題；在
`Pantheon_Response_to_System_Design_Open_Questions.md` 回覆後，現在改為記錄：

- 原問題
- architecture / system design team 已正式回覆的結論
- Pantheon 端整合後的最終判讀
- 仍需保留的異議與後續文件工作

在正式 canonical 文件落到 `docs/conventions/` 與 `docs/decisions/` 之前，
本文件作為目前 system design 藍圖整合版的 working source。

## 整合來源

- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Team_Design_Input_List.md`
- repo 現況中的 `WORKBENCH_DELIVERY_BACKLOG.md`、BFF contract、screen spec、packet family、frontend SA

---

## 先講結論

目前已整合後的系統設計藍圖結論是：

1. 真正仍屬 architecture bucket 的，只剩：
   - 全域 canonical conventions
   - `LIN-002` lineage ownership
   - persona boundary
   - router / gateway / governance enforcement ownership
2. 需要 ratification 的模組結論已大致拍板，不必再重畫高階藍圖。
3. 大多數 route-live 或 contract-ready 模組，應直接進 implementation / hardening / UI activation，而不是繼續在 architecture lane 空轉。

---

## A. 已整合的 Architecture 決策

### A1. Global Canonical Conventions Pack

已整合結論：

- `module-level canonical contract != new deployable service` 正式成立。
- `allowedActions` 應是 CTA 的唯一權威來源。
- `meta.snapshot_at` 應為快照時間固定欄位。
- `meta.surfaces.*` 必須共享一份全域 degradation dictionary。
- lifecycle / state naming 採「全域框架 + 領域子集」模式。
- readiness ladder 正式需要收斂，不可再讓 backlog / SA / packet family 各自命名。

正式期待交付：

- `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- `docs/conventions/DEGRADATION_DICTIONARY.md`
- `docs/conventions/MODULE_READINESS_LADDER.md`

### A2. LIN-002 Lineage Ownership

已整合結論：

- `services/lineage-read/` 是 UI-facing canonical lineage read owner。
- BFF lineage / evolution surfaces 只能接 `lineage-read`，不能同時吃第二條 telemetry truth path。
- telemetry lineage engine 可以作 internal substrate，但不能變成第二個 UI truth owner。

正式期待交付：

- `docs/decisions/LIN-002-lineage-ownership.md`

### A3. Control Plane Persona Boundary

已整合結論：

- persona service 應擁有 canonical persona object，而不是只讓 BFF 聚合結果充當 upstream truth。
- canonical persona truth 至少包含：`Persona`、`PersonaLifecycle`、`RoutePolicyRef`、`ConsultPolicyRef`、`PersonaCapabilityProfile`、`PersonaCapitalEligibility`、`PersonaSession` metadata。
- BFF 只可做 operator-facing aggregation，不可反客為主取代 persona canonical object。

正式期待交付：

- `docs/decisions/control-plane-persona-boundary.md`

### A4. Control Plane Router Enforcement Ownership

已整合結論：

- gateway / edge 只負責 ingress / auth / transport / coarse throttling。
- router 擁有 routing decision 與 intent capture，但不擁有 governance approval authority。
- approval authority 屬於 governance / promotion / relevant control surface。
- TTL 必須分 transport TTL 與 domain TTL 兩層定義 owner。
- local intent classifier 可以保留 degraded fallback 身分，但不得作 production canonical truth。

正式期待交付：

- `docs/decisions/control-plane-router-enforcement-ownership.md`

---

## B. 已整合的 Ratification 結論

### B1. RW-05 Artifact Compare

已整合結論：

- `docs/bff/RW-05-artifact-compare.md` 應視為 canonical contract。
- 正確 canonical status 是 `contract_ready`。
- implementation lane 可以前進。
- backlog / packet family / overview 應從「缺 contract」改為「contract published / pending BFF implementation」。

### B2. CW-02 Debate Transcript

已整合結論：

- 仍未達 fully locked。
- append-only `TranscriptEvent` schema、ordering semantics、actor labeling、inline evidence-link semantics 都還沒正式 ratify。
- canonical status 仍應為 `blocked`。
- 只允許 shell / non-authoritative scaffolding，不允許正式 implementation lane 接手。

### B3. CW-04 Red-team Memo

已整合結論：

- `ConsultMemo` read model 接近，但未正式 ratify 完成。
- `session_to_memo_mapping` 與 `allowedActions.canInitiateGovernanceReview` 都尚未鎖。
- canonical status 仍應為 `blocked`。
- 只允許 shell / non-authoritative scaffolding。

### B4. TW-02 Parameter Controls

已整合結論：

- read contract、patch semantics、validation contract、diff shape 尚未 fully locked。
- canonical status 仍應為 `blocked`。
- 只允許 shell / form scaffolding，不允許正式 implementation lane 接手。

### B5. KW-02 / KW-03 / KW-04 / KW-05

已整合結論：

- `KW-02`：`contract_ready`，pending BFF
- `KW-03`：`contract_ready`，pending BFF
- `KW-04`：`contract_ready`，pending BFF
- `KW-05`：`blocked`

補充判讀：

- `KW-006` packet family 先前把 `KW-02` 到 `KW-05` 寫成 `ready / implemented / resolved`，屬於 overclaim。
- `KW-02` 到 `KW-04` 不應再被一律視為 shell-only。
- `KW-05` 仍不可離開 architecture bucket。

正式期待交付：

- `MODULE_READINESS_RATIFICATION_2026-04-20.md`

---

## C. 已整合的 Module Gate Rule

### C1. CW-03 Committee Board

已整合結論：

- `CW-03` 不必完全等到 `CW-02` live 才能開始。
- 允許 partial activation：
  - board summary
  - sponsor decision status
  - committee outcome summary
  - read-only overview
- 但完整 production handoff 仍必須等 `CW-02` transcript layer live，且提供 transcript drill-down、append-only event projection、actor labeling truth、inline evidence-link truth。

需要正式寫進 readiness ladder / promotion rule 的句子：

> `CW-03` route-live != full module-ready。  
> `CW-03` 在 `CW-02` 未 live 前可 partial activate；完整 production handoff 仍以 `CW-02` live 為前提。

---

## D. 已確認不該再送回 Architecture 的項目

以下項目已不應再被當成抽象 system design 未定案：

- `EW-04`
- `EW-05`
- `RW-01`
- `RW-02`
- `RW-03`
- `RW-04`
- `RW-05`
- `CW-01`
- `CW-03` 的 route / authority contract 本身
- `KW-01`
- `TW-01`
- `TW-03`
- `TW-04`

這些項目剩下的主問題是 implementation、truth-hardening、UI activation、wiring、或 docs rebaseline。

---

## E. 我方異議與整合註記

這些不是否定整份回覆，而是我認為必須在正式 canonical 文件裡補清楚的地方。

### E1. `allowedActions` 不應被寫成 array

系統設計回覆中的 shared envelope example 把 `allowedActions` 寫成陣列，但目前 repo truth、BFF contract、frontend types 幾乎全面採用 object-shaped flags，例如：

- `allowedActions.canApproveMutation`
- `allowedActions.canCancel`
- `allowedActions.canCommit`

因此正式 conventions 應維持 object-shaped `allowedActions`，不能直接改成 array。

### E2. detail envelope 不應強迫所有模組都有通用 `id` / `title`

系統設計回覆提出 detail response 預設至少包含 `id`、`title`、`status` 等欄位；我同意需要 shared envelope，但不同模組現在的 canonical identity 明顯是：

- `decision_id`
- `request_id`
- `session_id`
- `artifact_id`

不是所有 detail read model 都天然有 generic `id` / `title`。  
因此正式 envelope 應定義「最小公共外殼 + domain-specific primary identity」，而不是硬把所有 detail view 壓成同一個 object shape。

### E3. pagination key 不應直接改成 `next_cursor`

系統設計回覆建議統一成 cursor-based naming，這個方向我同意；但目前 repo truth、contract、tests、frontend 都廣泛使用：

- `page_info.next_page_token`

而不是 `next_cursor`。  
所以若要統一 naming，必須明確定義 migration / alias policy；在那之前，不應直接把 canonical example 改寫成 `next_cursor`。

### E4. `meta.surfaces.*` 的 shared dictionary 仍未完全閉合

「surface status 必須全域統一」這個方向我同意，但目前 repo 其實同時存在：

- `ok | degraded | unavailable`
- `fresh | stale | degraded | unavailable`
- `partial`

甚至 `PKT-005` 又把 stale 視為 `meta.staleness` 與 degraded surface 的組合結果。  
所以正式 `DEGRADATION_DICTIONARY.md` 還必須補：

- 哪些 surface 可使用 `fresh/stale`
- 哪些 surface 必須只用 `ok/degraded/unavailable`
- `partial` 是否保留
- `stale` 是 surface enum 還是由 `meta.staleness` 推導

### E5. readiness ladder 需要明確對映現有 repo vocabulary

新的 ladder enum 方向正確，但 repo 現在實際上還在使用：

- `contract-published`
- `pending-bff`
- `route-live`
- `ready`
- `shell-only`

此外，`CW-03` 又新增了 partial activation 的 promotion rule。  
因此正式 `MODULE_READINESS_LADDER.md` 必須補：

- 舊 vocabulary 對新 ladder 的 mapping
- `partial activation` 在 ladder 裡如何表示
- backlog / SA / packet family / lovable-ui-task 各自要用哪一層字彙

---

## F. 接下來要回寫的 canonical 文件

### 全域規範

- `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- `docs/conventions/DEGRADATION_DICTIONARY.md`
- `docs/conventions/MODULE_READINESS_LADDER.md`

### Ownership decisions

- `docs/decisions/LIN-002-lineage-ownership.md`
- `docs/decisions/control-plane-persona-boundary.md`
- `docs/decisions/control-plane-router-enforcement-ownership.md`

### Readiness / ratification outputs

- `MODULE_READINESS_RATIFICATION_2026-04-20.md`
- backlog / SA / packet family / BFF overview 的 readiness truth 同步

### 仍需 ratified contract 的模組

- `docs/bff/CW-02-debate-transcript.md`
- `docs/bff/CW-04-redteam-memo.md`
- `docs/bff/TW-02-parameter-controls.md`
- `docs/bff/KW-05-strategy-spec.md`

---

## 一句話總結

現在 Pantheon 需要 architecture team 補的，已不再是整張高階藍圖，而是把全域規範、少數 ownership decision、少數 blocked 模組 contract、以及 readiness truth 的最終映射正式落成；除這些之外，多數 workbench 模組都應直接轉入 implementation 與 UI handoff。
