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
- `docs/reviews/Pantheon_Response_to_Architecture_Blockers_Decision_Package.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Team_Design_Input_List.md`
- repo 現況中的 `WORKBENCH_DELIVERY_BACKLOG.md`、BFF contract、screen spec、packet family、frontend SA

---

## 先講結論

目前已整合後的系統設計藍圖結論是：

1. `Pantheon_Response_to_Architecture_Blockers_Decision_Package.md` 已在 substance 上回答先前的 architecture blockers 問題。
2. 需要保留在 architecture bucket 的，主要只剩 cross-cutting canonical docs 與其 downstream rebaseline，不再是整批 module contract 本身。
3. module readiness 一律以 `MODULE_READINESS_RATIFICATION_2026-04-20.md`、對應 `docs/bff/*.md`、以及 current repo truth 為準；不能把較早 snapshot 中的 `blocked` wording 直接覆蓋回來。
4. 大多數 route-live 或 contract-ready 模組，應直接進 implementation / hardening / UI activation，而不是繼續在 architecture lane 空轉。

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

現已落文件：

- `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- `docs/conventions/DEGRADATION_DICTIONARY.md`
- `docs/conventions/MODULE_READINESS_LADDER.md`

### A2. LIN-002 Lineage Ownership

已整合結論：

- `services/lineage-read/` 是 UI-facing canonical lineage read owner。
- BFF lineage / evolution surfaces 只能接 `lineage-read`，不能同時吃第二條 telemetry truth path。
- telemetry lineage engine 可以作 internal substrate，但不能變成第二個 UI truth owner。

現已落文件：

- `docs/decisions/LIN-002-lineage-ownership.md`

### A3. Control Plane Persona Boundary

已整合結論：

- persona service 應擁有 canonical persona object，而不是只讓 BFF 聚合結果充當 upstream truth。
- canonical persona truth 至少包含：`Persona`、`PersonaLifecycle`、`RoutePolicyRef`、`ConsultPolicyRef`、`PersonaCapabilityProfile`、`PersonaCapitalEligibility`、`PersonaSession` metadata。
- BFF 只可做 operator-facing aggregation，不可反客為主取代 persona canonical object。

現已落文件：

- `docs/decisions/control-plane-persona-boundary.md`

### A4. Control Plane Router Enforcement Ownership

已整合結論：

- gateway / edge 只負責 ingress / auth / transport / coarse throttling。
- router 擁有 routing decision 與 intent capture，但不擁有 governance approval authority。
- approval authority 屬於 governance / promotion / relevant control surface。
- TTL 必須分 transport TTL 與 domain TTL 兩層定義 owner。
- local intent classifier 可以保留 degraded fallback 身分，但不得作 production canonical truth。

現已落文件：

- `docs/decisions/control-plane-router-enforcement-ownership.md`

---

## B. 已整合的 Ratification 結論

### B1. 對 `Pantheon_Response_to_Architecture_Blockers_Decision_Package.md` 的整合判讀

已整合結論：

- 該回覆已回答先前卡住的 global conventions、ownership / authority decisions、以及 `CW-03` partial activation wording。
- Pantheon 接受它的 cross-cutting decision 內容。
- 但 module-level final classification 不直接照抄；凡是 repo 已完成 ratification 或實作已前進的模組，仍以 `MODULE_READINESS_RATIFICATION_2026-04-20.md`、對應 `docs/bff/*.md`、以及 current repo truth 為準。

工作規則：

1. 若 response 與 conventions / decision docs 一致，直接以現有 canonical docs 為藍圖依據。
2. 若 response 把某模組仍寫成 `blocked`，但 ratification + contract + code truth 已經把它往前推，則 current repo truth 優先。
3. 不得因為讀到較早 snapshot 的 `blocked` wording，就把已 ratified / route-live 的模組退回 architecture lane。

### B2. 目前模組整合後的 working truth

- `RW-05`：已不再只是 `contract_ready`，目前 repo truth 是 route-live。
- `CW-02`：response 中的 transcript schema / ordering / actor / evidence 問題已被回答並吸收進 ratified contract；目前 repo truth 是 route-live，不再屬 architecture lane。
- `CW-04`：response 中的 memo lifecycle / mapping / governance gate 問題已被回答；目前 working truth 是 ratified contract + pending BFF implementation。
- `TW-02`：response 中的 patch semantics / rejected shape / diff rule 已被回答；目前 working truth 是 ratified contract + implementation in progress。
- `KW-02` / `KW-03` / `KW-04`：已不只是 pending BFF 的 contract-ready wording；目前 repo truth 已進到 route-live。
- `KW-05`：response 中的 version identity / lifecycle / compare semantics 已被回答並吸收進 ratified contract；目前 repo truth 是 route-live，不再屬 architecture lane。

### B3. 對 readiness 文件的正式依據

正式依據：

- `MODULE_READINESS_RATIFICATION_2026-04-20.md`
- `docs/bff/CW-02-debate-transcript.md`
- `docs/bff/CW-04-redteam-memo.md`
- `docs/bff/TW-02-parameter-controls.md`
- `docs/bff/KW-05-strategy-spec.md`
- `WORKBENCH_DELIVERY_BACKLOG.md`

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
- `CW-02`
- `CW-03` 的 route / authority contract 本身
- `CW-04`
- `KW-01`
- `KW-02`
- `KW-03`
- `KW-04`
- `KW-05`
- `TW-02`
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

## F. 接下來要維護的 canonical 文件與實作 task

### 全域規範

這些文件現已存在，後續工作是持續把 derived docs 對齊到它們：

- `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- `docs/conventions/DEGRADATION_DICTIONARY.md`
- `docs/conventions/MODULE_READINESS_LADDER.md`

### Ownership decisions

這些文件現已存在，後續工作是把 control-plane / packet / SA wording 對齊到它們：

- `docs/decisions/LIN-002-lineage-ownership.md`
- `docs/decisions/control-plane-persona-boundary.md`
- `docs/decisions/control-plane-router-enforcement-ownership.md`

### Readiness / ratification outputs

- `MODULE_READINESS_RATIFICATION_2026-04-20.md`
- backlog / SA / packet family / BFF overview 的 readiness truth 同步

### 仍在 implementation / hardening lane 的 task

這一段在 2026-04-24 之後不應再把前一波 Pantheon task id 清單直接當成 active implementation lane。

先前列在這裡的 `APP-003-CW04-IMPL-001`、`APP-003-TW02-IMPL-001`、`APP-003-RW01-HARDEN-001`、`APP-003-RW03-HARDEN-001`、`PER-001-RUNTIME-INTEGRATION-001`、`APP-003-TRUTH-SYNC-001`、`APP-003-PKT001-BFF-ALIGN-001`、`APP-003-ROUTE-LIVE-FRONTEND-001`、`APP-003-ROUTE-LIVE-FRONTEND-002`、`APP-003-CW04-FRONTEND-HANDOFF-001`、`APP-003-PKT001-PUBLICATION-REPLAY-001`、`APP-003-CW04-PUBLICATION-REPLAY-001`、`APP-003-PKT001-SURFACE-VALIDATION-001`、`APP-003-TRUTH-SYNC-002`、`APP-003-TRUTH-SYNC-003`、`OSS-003-DOC-SYNC-001` 都已 archive-done、review-complete、或轉入 closeout / history，不應再被讀成現在的主要實作缺口。

截至目前，真正仍在執行面的剩餘工作是：

- `EP5-002` 的 human-gated canary / live proof
- 將 Lovable coordination 的 runtime proof 覆蓋從目前 board 顯示的 `32/46` 持續補齊
- `TRL` / `Qlib` 從 smoke-tested 朝 activation-ready 收尾，但不應提早宣稱 production-governed activation
- `FinRL` / `RLlib` / `Ray Tune` / `W&B` 仍依 entry criteria 維持 deferred，不屬於本輪未完成主線
- 跨 repo backlog / SA / coordination summary / feature row metadata 的 rebaseline

補充 truth：

- `front-ai-trading-system` 的 GitHub-visible default branch 現在已經掛上 `EW-04`、`EW-05`、`CW-01`、`CW-03`、`CW-04` 的 live route，不應再把這批讀成 blocked-shell realignment 主線
- `PKT-001-deployment-review` 與 `PKT-003-post-incident-review` 的 follow-up code 和 request pair 也已上 front default branch；剩餘若有未收斂者，應視為 review / closeout / truth-sync residual，而不是新的前端基礎實作缺口
- `Settings` surface 已切到 Pantheon BFF-backed `/api/v1/settings*` 路由，不再是 demo-only page；後續若有工作，應視為 config-domain product refinement，而不是「尚未接 Pantheon」的整合缺口

目前 active lane 請以 `current-work.md`、reopened execution tasks、以及各 repo 的 current default-branch truth 一起判讀，而不是只以上一波 closeout response 或已完成 task id 回推現況。

### 已被回答、但不應重開為新 implementation task 的模組

- `docs/bff/CW-02-debate-transcript.md`
- `docs/bff/CW-04-redteam-memo.md`
- `docs/bff/TW-02-parameter-controls.md`
- `docs/bff/KW-05-strategy-spec.md`

對應判讀：

- `CW-02`：已 route-live，不重開新 implementation task
- `KW-05`：已 route-live，不重開新 implementation task
- `CW-04` / `TW-02`：保留既有 implementation task，不另開 duplicate task

---

## 一句話總結

現在 Pantheon 需要的，不再是重畫 architecture blueprint；`Pantheon_Response_to_Architecture_Blockers_Decision_Package.md` 已把最後的 cross-cutting 問題回答清楚，剩下的是把這些決策維持在 canonical docs 裡，並讓仍未完成的模組繼續留在 implementation / hardening / closeout lane，而不是退回 architecture lane。
