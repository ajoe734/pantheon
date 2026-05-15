# 2026-04-21 Architecture-Blocked Decision Package For System Design Team

## 目的

這份文件是給 system design / architecture team 的正式決策包。

目的不是重畫 Pantheon 高階藍圖，而是把截至 `2026-04-21` UTC 仍真正卡在
architecture / system design lane 的項目收斂成可回覆、可 ratify、可落文件的清單。

本文特別區分三種狀態：

- `architecture-blocked`：缺 canonical decision，implementation 不應先行。
- `implementation-gap`：架構已足夠，缺的是 BFF / frontend / runtime work。
- `delivery-closeout`：code 與 route 大致已有，剩 review、runtime refresh、handoff、
  或 doc rebaseline。

## 本文採用的快照基準

- `current-work.md`
- `MODULE_READINESS_RATIFICATION_2026-04-20.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Team_Design_Input_List.md`
- `docs/reviews/2026-04-20-system-design-follow-up-question-list.md`
- `docs/bff/CW-02-debate-transcript.md`
- `docs/bff/CW-04-redteam-memo.md`
- `docs/bff/TW-02-parameter-controls.md`
- `docs/bff/KW-05-strategy-spec.md`

## 一頁結論

截至 `2026-04-21` UTC，真正仍屬 `blocked 等 architecture` 的，不是整體藍圖，而是：

1. 四個 blocked modules
   - `CW-02 Debate Transcript`
   - `CW-04 Red-team Memo`
   - `TW-02 Parameter Controls`
   - `KW-05 Strategy Spec`
2. 三組 ownership / authority 決策
   - `LIN-002` lineage ownership
   - persona canonical owner boundary
   - router / gateway / governance enforcement matrix
3. 六組 global conventions
   - readiness ladder crosswalk
   - degradation dictionary
   - `stale` vs `meta.staleness`
   - pagination naming migration rule
   - shared response envelope minimum rule
   - object-shaped `allowedActions`
4. 一條 module gate rule
   - `CW-03` partial activation / promotion wording

除此之外，多數 workbench 模組已不應再送回 architecture。

## 重要澄清

不是所有 `blocked` 都是 architecture blocker。

例如：

- `EXEC-FRONT-RW01-001` 在 `current-work.md` 顯示為 `blocked`，但那是 runtime refresh blocker，不是 system design blocker。
- `KW-04` 目前仍未 live，但性質是 implementation gap，不是 architecture blocker。
- `RW-05`、`KW-02`、`KW-03` 目前的主要問題是 frontend activation 與文件 rebaseline，不是 architecture blocker。

## A. 真正 blocked 等 architecture 的模組

### A1. 模組總表

| Module | Canonical status | 為什麼仍 blocked | 現有草稿是否存在 | implementation 可否先行 |
|---|---|---|---|---|
| `CW-02` | `blocked` | transcript append-only schema、ordering、actor labeling、inline evidence semantics 尚未 ratify | yes | no，僅可 shell / scaffolding |
| `CW-04` | `blocked` | memo lifecycle、governance handoff、`session_to_memo_mapping`、`allowedActions.canInitiateGovernanceReview` 未鎖 | yes | no，僅可 shell / scaffolding |
| `TW-02` | `blocked` | patch semantics、validation contract、invalid / reject behavior、diff shape 未鎖 | yes | no，僅可 shell / scaffolding |
| `KW-05` | `blocked` | versioned strategy spec semantics 仍 architecture-sensitive | yes | no，應維持 blocked |

### A2. 共同判讀原則

上述四個模組都有「文件骨架已存在」的表象，但目前不應把這些草稿直接當成已解鎖。

正式判讀應為：

- 這些 `docs/bff/*.md` 是 candidate contract draft
- 尚未完成 architecture ratification
- 在 ratified version 落地前，不應把它們視為 `contract_ready`

換句話說，這四個模組不是「完全沒想法」，而是「已有草稿，但仍缺 architecture lock」

### A3. `CW-02 Debate Transcript`

目前仍未閉合的點：

- append-only `TranscriptEvent` canonical schema
- ordering / stable cursor rule
- actor labeling contract
- inline evidence-link semantics
- transcript projection 與 replay boundary
- `partial transcript` 是否為正式 degradation mode

為什麼不能先做 implementation：

- transcript 是 `CW-03` full production handoff 的前置真相
- 一旦 ordering、actor labeling、evidence embedding 先被實作者寫死，後續返工成本很高

architecture team 需要拍板的最小交付：

- ratified `docs/bff/CW-02-debate-transcript.md`
- 一個 detail example
- 一個 pagination / cursor example
- 一句明確的 append-only / ordering rule

### A4. `CW-04 Red-team Memo`

目前仍未閉合的點：

- `ConsultMemo` lifecycle 是否嚴格維持 `draft -> published`
- `session_to_memo_mapping` 是否為一等欄位
- governance handoff contract
- `allowedActions.canInitiateGovernanceReview` 的 authority rule
- memo publish 與 downstream review 的 boundary

為什麼不能先做 implementation：

- 這是直接連到 governance handoff 的模組
- 若 authority semantics 未鎖，實作者會被迫自己定義 CTA 與 escalation boundary

architecture team 需要拍板的最小交付：

- ratified `docs/bff/CW-04-redteam-memo.md`
- governance handoff example payload
- 一句明確 authority rule

### A5. `TW-02 Parameter Controls`

目前仍未閉合的點：

- patch semantics 是 partial patch 還是 replace-style
- invalid patch response shape
- reject / partial-apply / noop policy
- `updated_controls[]` 是否為 canonical diff shape
- preview / replay / commit / discard boundary 與本模組的切分

為什麼不能先做 implementation：

- `TW-02` 直接影響 `TW-03` compare 與 `TW-04` replay/commit/discard 的上游真相
- 若 patch / reject / diff 規則先亂落地，下游 trainer family 會整串污染

architecture team 需要拍板的最小交付：

- ratified `docs/bff/TW-02-parameter-controls.md`
- 一份 read contract
- 一份 write / patch contract
- 三個 example：valid patch、invalid patch、rejected patch

### A6. `KW-05 Strategy Spec`

目前仍未閉合的點：

- canonical version identifier
- `parent` / `ancestor` / `superseded` relationship model
- lifecycle state
- compare semantics 與 diff granularity
- 哪些 write path 可建立新 version
- 哪些 write path 只能 mutate draft

為什麼不能先做 implementation：

- 這不是單頁 UI contract，而是整個 versioned strategy spec truth
- 若這層沒鎖，`KW-05` 後續極可能整包重寫，且會連動 `KW-02` / `KW-03` / `KW-04`

architecture team 需要拍板的最小交付：

- version model decision
- ratified `docs/bff/KW-05-strategy-spec.md`
- 一份 compare / version-history canonical example

## B. 仍待 architecture 拍板的 cross-cutting 決策

### B1. Global conventions

下列不是單一模組問題，而是整個 BFF / frontend / packet / backlog 共同依賴的規則：

| Topic | 為什麼仍未閉合 | 需要 architecture team 決策 |
|---|---|---|
| readiness ladder crosswalk | repo 仍混用 `contract-published`、`pending-bff`、`route-live`、`ready`、`shell-only`、`blocked` | 正式 enum 與現有 vocabulary mapping |
| degradation dictionary | repo 同時存在 `ok` / `stale` / `degraded` / `unavailable` / `partial` | 全域 status set 與使用規則 |
| `stale` vs `meta.staleness` | 有些模組把 `stale` 當 surface enum，有些當衍生語義 | `stale` 的正式位置與優先語義 |
| pagination naming | current repo 大量使用 `page_info.next_page_token`，但 architecture 回覆曾提 `next_cursor` | 是否維持現狀，或提供 migration / alias policy |
| shared response envelope | generic `id` / `title` 與 domain-specific identity 存在衝突 | minimum envelope 與 domain identity rule |
| `allowedActions` rule | repo truth 幾乎全面使用 object-shaped flags | 是否正式鎖為 object-shaped canonical truth |

### B2. Ownership / authority boundaries

| Topic | 需要拍板什麼 | 為什麼會卡 implementation |
|---|---|---|
| `LIN-002` lineage ownership | `lineage-read`、telemetry lineage engine、BFF consume path 的正式邊界 | evolution / lineage surfaces 會出現第二條 truth path 風險 |
| persona boundary | 哪些 object 屬 persona service canonical truth，哪些僅屬 BFF composed read model | persona-facing screens 容易把 convenience rollup 誤當 authority truth |
| router / gateway / governance matrix | transport TTL、domain TTL、rate limit、approval authority、fallback classifier 的 owner | incident / approval / routing 類模組容易各自補判斷，產生 authority drift |

## C. `CW-03` 的特殊 gate rule

`CW-03` 不是新的 blocked module，但它仍需要 architecture team 給正式 wording。

目前整合後的判讀應是：

- `CW-03` 不必完全等 `CW-02` live 才能開始
- `CW-03` 可 partial activate
- 但 `CW-03 route-live` 不等於 full module-ready
- full production handoff 仍以 `CW-02` transcript truth live 為前提

需要 system design team 正式寫進文件的句子：

> `CW-03` route-live != full module-ready.
> `CW-03` may partial-activate before `CW-02` is fully live.
> Full production handoff still requires `CW-02` transcript truth.

## D. 明確不應再送回 architecture 的項目

以下模組不應再被當成 architecture blocker：

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
- `KW-02`
- `KW-03`
- `KW-04`
- `TW-01`
- `TW-03`
- `TW-04`

這些項目的主問題分別是：

- implementation
- runtime refresh
- frontend activation
- review closeout
- handoff activation
- doc rebaseline

不是 architecture 重畫。

## E. 要求 system design team 交付的文件

### E1. 必要文件

| Priority | File | 用途 |
|---|---|---|
| P0 | `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md` | 鎖 global contract naming 與 minimum rules |
| P0 | `docs/conventions/BFF_RESPONSE_ENVELOPE.md` | 鎖 list / detail minimum envelope |
| P0 | `docs/conventions/DEGRADATION_DICTIONARY.md` | 鎖 `ok/stale/degraded/unavailable/partial` 規則 |
| P0 | `docs/conventions/MODULE_READINESS_LADDER.md` | 鎖 readiness enum 與 crosswalk |
| P0 | `docs/decisions/LIN-002-lineage-ownership.md` | 鎖 lineage ownership |
| P0 | `docs/decisions/control-plane-persona-boundary.md` | 鎖 persona canonical owner boundary |
| P0 | `docs/decisions/control-plane-router-enforcement-ownership.md` | 鎖 command / approval / TTL / throttle owner |
| P0 | ratified `docs/bff/CW-02-debate-transcript.md` | 解鎖 `CW-02` |
| P0 | ratified `docs/bff/CW-04-redteam-memo.md` | 解鎖 `CW-04` |
| P0 | ratified `docs/bff/TW-02-parameter-controls.md` | 解鎖 `TW-02` |
| P0 | ratified `docs/bff/KW-05-strategy-spec.md` | 解鎖 `KW-05` |

### E2. 次要但必要的同步文件

以下文件需在 ratification 後同步回寫：

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `MODULE_READINESS_RATIFICATION_2026-04-20.md` 或更新版 ratification record
- relevant packet family docs
- relevant frontend SA sections
- relevant BFF overview / examples

## F. 建議 system design team 回覆格式

每一題請至少回覆：

- `decision`
- `canonical wording`
- `affected modules`
- `affected files`
- `implementation impact`
- `migration impact`
- `may implementation proceed before final ratified doc? yes/no`

若該題需要兩階段落地，請額外標註：

- `interim rule`
- `final rule`
- `what may proceed under the interim rule`

## G. 建議回覆優先順序

1. global conventions
2. ownership / authority
3. blocked modules
4. `CW-03` promotion wording

原因：

- conventions 不先鎖，後面所有 contract wording 都會漂
- ownership 不先鎖，BFF 與 service boundary 會持續歪
- blocked modules 的解鎖要依賴前兩層規則
- `CW-03` promotion rule 則是 readiness / handoff wording 的最後拼圖

## H. 最終判讀

如果只回答「有哪些是 blocked 等 architecture」：

- 真正 blocked 的模組只有四個：
  - `CW-02`
  - `CW-04`
  - `TW-02`
  - `KW-05`
- 但若把 cross-cutting decision 也算進去，system design team 本輪仍需補：
  - 6 個 conventions decision
  - 3 個 ownership / authority decision
  - 1 個 `CW-03` promotion rule

也就是說，本輪要 system design team 回答的，不是整個 Pantheon 藍圖，
而是：

- `4` 個 blocked modules
- `10` 個 cross-cutting decisions

一旦這些項目完成 ratification，Pantheon 剩餘的大部分差距就可以乾淨地下放到：

- BFF implementation
- frontend implementation
- runtime refresh
- handoff activation
- delivery closeout

而不必再停在 architecture lane 反覆打轉。
