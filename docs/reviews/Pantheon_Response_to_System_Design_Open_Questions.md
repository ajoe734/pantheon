# Pantheon 對 System Design Open Questions 的正式回覆

## 文件目的
本文件是對 `2026-04-20-system-design-open-questions-for-architecture-team.md` 的正式架構回覆。
目標不是重畫整個 Pantheon 高階藍圖，而是把目前仍未定案、仍需 ratification、或仍需明確 architecture 決策的問題，**逐題定案**，讓：

- implementation lane
- BFF lane
- frontend / Lovable lane
- backlog / SA / packet family

之間不再各說各話。該文件本身也已明確指出，當前真正待解的不是高階藍圖，而是三類問題：**全域 canonical conventions、少數 cross-service ownership / authority boundary、以及若干 contract 已存在但 readiness / status 互相衝突的模組 ratification**。fileciteturn24file13

---

# 1. 總結論

## 1.1 我方整體判斷
我同意開發團隊的主判斷：

> 目前 Pantheon 真正需要 architecture team 回答的，不是重畫高階藍圖，而是把共通規範、少數 ownership decision、以及若干 module readiness truth 正式鎖定。fileciteturn24file13turn24file2

因此，本回覆做三件事：

1. 把 A 類未定案 architecture 問題逐條定案
2. 把 B 類 ratification 問題逐條拍板 canonical status
3. 把 C 類 module gate rule 與 D 類 non-architecture 項目一併明文化

## 1.2 本回覆採用的原則
- **module-level canonical contract ≠ new deployable service**。模組契約是 BFF / packet / UI / implementation 的真相來源，不等於要新增一個獨立服務。這也是 design input list 先前要求 architecture team 補齊的全域規則之一。fileciteturn24file12
- **frontend 不得自行綜合真相**。任何 CTA、state、degradation、readiness 都不能由前端自行推導，必須由 canonical contract 或 canonical readiness truth 提供。
- **若 contract 已鎖、route 是否 live 可由 code 驗證、剩下只是 implementation / wiring / UI activation，就不應再送回 architecture**；這一點與 open questions 文件的判準一致。fileciteturn24file13turn24file2

---

# 2. A 類：真正未定案的 Architecture 問題（逐題定案）

## A1. Global Canonical Conventions Pack
開發團隊提出 8 個尚未正式文件化的全域規範問題，包括：
- `module-level canonical contract != new deployable service`
- detail / list response 最小 envelope
- `allowedActions` 是否是 CTA 的唯一權威來源
- `meta.snapshot_at` 放置規則
- `meta.surfaces.*` 的命名、枚舉值與語義
- lifecycle / state naming 的全域 enum / naming 規範
- pagination / cursor / ordering / filter naming 的統一規格
- readiness ladder 的正式 enum 與提升條件。fileciteturn24file1turn24file7

### 我方正式回答

#### A1-1. `module-level canonical contract != new deployable service`
**正式列為全域原則。**

- `module-level canonical contract` 的作用：定義某個 workbench module 的 read model、authority、lifecycle、degradation semantics、example payload、handoff packet truth。
- `deployable service` 的作用：定義 runtime boundary、failure domain、scaling profile、write authority。
- 兩者不能混為一談。某模組需要 canonical contract，並不代表它要獨立部署成新的 service。

#### A1-2. Detail / List response 的最小固定 envelope
**正式統一。**

所有 BFF-facing detail response，預設至少包含：

```json
{
  "id": "...",
  "title": "...",
  "status": "...",
  "lifecycle_state": "...",
  "allowedActions": [],
  "meta": {
    "snapshot_at": "2026-04-20T00:00:00Z",
    "surfaces": {}
  },
  "links": {}
}
```

所有 list response，預設至少包含：

```json
{
  "items": [],
  "page_info": {
    "page_size": 50,
    "next_cursor": null,
    "sort_by": "updated_at",
    "sort_order": "desc"
  },
  "meta": {
    "snapshot_at": "2026-04-20T00:00:00Z",
    "surfaces": {}
  }
}
```

#### A1-3. `allowedActions` 是否為 CTA 的唯一權威來源
**是，正式列為全域硬規則。**

前端不得依：
- actor role
- object state
- route availability
- 自己的 heuristic

去推 CTA。所有可執行動作都以 backend 回傳的 `allowedActions` 為準。

#### A1-4. `meta.snapshot_at` 的放置規則
**統一放在 `meta.snapshot_at`。**

- detail response：反映該 read model 的快照時間
- list response：反映該列表資料的快照時間
- 不得各模組自行改到 `snapshotAt`、`fetched_at`、`generated_at` 等不同位置，除非是額外欄位而非取代 `meta.snapshot_at`

#### A1-5. `meta.surfaces.*` 是否全域統一
**是，必須全域統一。**

每個 module 都可以有自己的 surface key，例如：
- `meta.surfaces.compare`
- `meta.surfaces.transcript`
- `meta.surfaces.mutation_review`

但 surface status 的枚舉值與語義必須共享同一份 degradation dictionary。

#### A1-6. lifecycle / state naming 的全域規範
**採「全域框架 + 領域子集」模式，而不是單一巨型 enum。**

全域框架：
- `status`：偏運行/呈現狀態
- `lifecycle_state`：偏領域工作流狀態
- `readiness_state`：偏交付 readiness

領域子集：
- Artifact：`draft -> candidate -> approved -> retired`
- Deployment stage：`paper -> canary -> live -> frozen`
- Evolution：`proposed -> reviewed -> approved -> executed -> superseded`
- Incident：`new -> triaged -> active -> mitigated -> closed`

也就是說：
- 有全域 naming framework
- 各 domain 再定自己的 canonical enum
- 不強求所有物件共用一個 enum

#### A1-7. pagination / cursor / ordering / filter naming
**正式統一。**

- 分頁：cursor-based only
- 欄位：`page_size`, `next_cursor`
- 排序：`sort_by`, `sort_order`
- 篩選：`filters` 物件，內含標準命名 key

預設規則：
- 不再使用 offset/limit 作為 BFF-facing default
- `page_size` 要有 service-defined upper bound
- `sort_order` 只允許 `asc` / `desc`
- filter key 要以 canonical field 名稱為準，不允許 UI 自造別名

#### A1-8. module readiness ladder 的正式 enum 與提升條件
**正式定為：**

```text
blocked
contract_ready
screen_ready
handoff_ready
implementation_ready
production_ui_ready
```

提升條件：
- `blocked`：缺 canonical contract 或缺 owner / authority / lifecycle 定義
- `contract_ready`：`docs/bff/<module>.md` + example payload + basic authority / lifecycle 已鎖
- `screen_ready`：`docs/screens/<module>.md` 已存在，且 contract 足以做 honest screen
- `handoff_ready`：frontend / Lovable packet 可正式開工
- `implementation_ready`：BFF / backend lane 可實作，不再需 architecture clarification
- `production_ui_ready`：route live、truth hardened、前端不需自行 synthesize truth、關鍵 state 已覆蓋

### A1 期待交付（正式要求）
開發文件裡已列出四份期待交付，我方同意直接照此落地：
- `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- `docs/conventions/DEGRADATION_DICTIONARY.md`
- `docs/conventions/MODULE_READINESS_LADDER.md` fileciteturn24file1turn24file6

---

## A2. LIN-002 Lineage Ownership
文件指出 lineage truth 目前分散在：
- `services/telemetry/lineage_read/service.py`
- `services/lineage-read/main.py`
- `services/control-plane/bff/read_store.py`
並要求 architecture team 決定 UI-facing canonical lineage read owner 與 BFF consumption rule。fileciteturn24file1turn24file3

### 我方正式回答

#### A2-1. UI-facing canonical lineage read owner
**正式指定：`services/lineage-read/` 是 UI-facing canonical lineage read owner。**

#### A2-2. BFF lineage / evolution surfaces 是否只能接一條 canonical path
**是。BFF lineage / evolution surfaces 只能接 `lineage-read`。**

不允許：
- 一部分頁面打 `telemetry lineage engine`
- 另一部分頁面打 `lineage-read`
- BFF 自己再拼一套 lineage summary truth

#### A2-3. `services/lineage-read/` 的定位
**定位為：canonical read owner + façade。**

- 它可以在內部使用 telemetry lineage engine
- 可以包裝高性能 lineage substrate
- 但對外仍必須是唯一 canonical read façade

#### A2-4. telemetry lineage engine 的角色
**只保留為 internal substrate，不得成為第二條 UI truth path。**

### A2 期待交付（正式要求）
- 一份 LIN-002 ownership decision 文件
- 明確 BFF consumption rule
- 明確寫出 telemetry lineage path 與 `lineage-read` 的角色關係

---

## A3. Control Plane Persona Boundary
文件指出 `services/control-plane/persona/main.py` 仍是 stub / deferred，因此 architecture 需回答：persona service canonical object 邊界、哪些 persona-facing capability 必須由 persona service 擁有、哪些只能存在於 BFF composed read model、upstream schema 何者已鎖。fileciteturn24file1turn24file3turn24file19

### 我方正式回答

#### A3-1. Persona service canonical object 邊界
persona service 的 canonical truth 應至少包含：
- `Persona`
- `PersonaLifecycle`
- `RoutePolicyRef`
- `ConsultPolicyRef`
- `PersonaCapabilityProfile`
- `PersonaCapitalEligibility`
- `PersonaSession`（至少 metadata / status 層）

#### A3-2. 必須由 persona service 自己擁有的 persona-facing capabilities
以下不得只存在於 BFF composed read model：
- persona identity / id
- lifecycle state
- route policy binding
- consult policy binding
- capability profile
- formal persona eligibility / ownership boundary
- session metadata

#### A3-3. 只允許存在於 BFF composed read model 的內容
BFF 可以擁有：
- latest deployment rollup
- latest incident summary
- review summary / chips / badges
- operator convenience aggregation
- cross-domain stitched presentation model

但 BFF 不得取代 persona service 擁有 canonical persona object。

#### A3-4. upstream schema 何者已是 locked truth
**凡屬於 persona object 本身、policy reference、本體 lifecycle、session metadata，都應視為 upstream locked truth，由 persona service 所屬 schema 擁有。**

BFF 只可做 read aggregation，不可成為 upstream truth。

### A3 期待交付（正式要求）
- persona canonical object boundary 文件
- BFF aggregation boundary 文件
- persona readiness 條件（何時從 stub/deferred 進到 contract_ready）

---

## A4. Control Plane Router Enforcement Ownership
文件要求 architecture team 回答：
- TTL enforcement owner
- rate-limit enforcement owner
- approval / routing authority 誰擁有
- local intent classifier 是否保留 degraded fallback
- gateway 是否只負責 ingress / transport concerns。fileciteturn24file3turn24file19

### 我方正式回答

#### A4-1. TTL enforcement owner
分兩層：

- **Transport / request timeout 類 TTL**：由 gateway / edge 層負責
- **Domain / command validity / routing-related TTL**：由 router 或 downstream domain owner 負責

也就是說，TTL 不能只說一個 owner，必須區分 transport TTL 與 domain TTL。

#### A4-2. rate-limit enforcement owner
**正式 owner：gateway / edge 層。**

router 不應成為通用流量節流器。

#### A4-3. approval / routing authority 誰擁有
- router：擁有 routing decision 與 intent capture
- gateway：不擁有 approval authority
- approval authority：屬於 governance / promotion / relevant control surface

所以：
> router 可以決定流向，但不能取代 governance authority；gateway 更不應擁有 business approval authority。

#### A4-4. local intent classifier 是否保留 degraded fallback 身分
**可以保留，但只能作 degraded fallback，不得作 production canonical truth source。**

#### A4-5. gateway 是否只負責 ingress / transport concerns
**是。**

gateway 僅負責：
- authn / authz gatekeeping
- transport-level shaping
- coarse throttling
- request admission

不得擁有 business authority。

### A4 期待交付（正式要求）
- enforcement ownership decision 文件
- gateway / router / governance 三者邊界圖
- fallback classifier policy

---

# 3. B 類：需要 Architecture Ratification 的模組（逐題拍板）

這一類文件的核心判斷是：
- contract 文檔已存在
- packet / screen / example payload 有時也存在
- 但 backlog、SA、packet family、BFF overview 對 readiness / status 說法互相衝突
因此 architecture team 要做的是 **ratify canonical truth**，不是重畫模組。fileciteturn24file15turn24file17

---

## B1. RW-05 Artifact Compare
文件指出：
- `docs/bff/RW-05-artifact-compare.md` 已存在
- packet family 把 RW-05 寫成 `contract-published — pending BFF implementation`
- backlog 卻仍寫 `missing artifact registry/detail/compare routes and versioning semantics`。fileciteturn24file15turn24file17

### 我方正式回答

#### B1-1. `docs/bff/RW-05-artifact-compare.md` 是否已是 canonical contract？
**是，但僅限於 contract 層。**

也就是說：
- `RW-05` 不應再被視為「完全沒 contract」
- 但也**不能**被描述成已 implemented

#### B1-2. backlog 是否應改成 `contract published / pending BFF implementation`？
**是。**

#### B1-3. 是否仍有未鎖定 semantics？
我方判定：**比較核心的 compare semantics 應視為已鎖；剩餘差距主要在 BFF route 與 implementation truth-hardening，而不是 architecture semantics。**

### B1 Canonical status
**`contract_ready`**

### B1 implementation lane may proceed?
**Yes.**

### B1 需同步更新的文件
- `WORKBENCH_DELIVERY_BACKLOG.md`
- 相關 packet family
- BFF overview（若仍標 not_ready）

---

## B2. CW-02 Debate Transcript
文件指出：
- `docs/bff/CW-02-debate-transcript.md` 與 example payload 已存在
- packet family 寫成 `contract-published; pending-bff`
- backlog 仍寫 `missing transcript route, append-only event schema, and actor-label contract`
- consultation overview 仍把它視為 `not_ready`。fileciteturn24file8turn24file9

### 我方正式回答

#### B2-1. `TranscriptEvent` schema 是否已 canonical locked？
**尚未正式 ratified 為 fully locked。**

#### B2-2. actor labeling contract 是否已 canonical locked？
**尚未正式 ratified。**

#### B2-3. inline evidence-link semantics 是否已 canonical locked？
**尚未正式 ratified。**

#### B2-4. backlog / overview 是否應改成 `contract-published; pending implementation`？
**現階段不應直接這樣改。**

因為這個模組的 append-only event schema、ordering、actor labeling、inline evidence semantics 都還是 architecture-sensitive 的部分。

### B2 Canonical status
**`blocked`（或至少尚未達 `contract_ready`）**

### B2 implementation lane may proceed?
**No，僅可做 shell / non-authoritative scaffolding。**

### B2 需補哪些 exact undecided fields
- append-only `TranscriptEvent` canonical schema
- ordering semantics
- actor labeling contract
- inline evidence-link semantics
- transcript projection / replay boundary

---

## B3. CW-04 Red-team Memo
文件指出：
- `docs/bff/CW-04-redteam-memo.md` 與 example payload 已存在
- packet family 將其描述為 contract-published / pending-bff
- backlog 仍把它寫成 `missing memo list/detail/publish flow and downstream handoff contract`。fileciteturn24file8turn24file9

### 我方正式回答

#### B3-1. `ConsultMemo` read model 是否已 canonical locked？
**部分接近，但未正式 ratified 完成。**

#### B3-2. `session_to_memo_mapping` 是否已 canonical locked？
**未正式 ratified。**

#### B3-3. `allowedActions.canInitiateGovernanceReview` 是否已 canonical locked？
**未正式 ratified。這是高風險 authority 欄位，不可由實作者自行補。**

#### B3-4. recommendation list 是否確定只採用 plain string list，且 per-recommendation severity / workflow status 不在本輪 scope？
**是，暫定維持 plain string list；per-recommendation severity / workflow status 明確不在本輪 scope。**

#### B3-5. backlog 是否應改為 `contract-published; pending BFF implementation`？
**現階段不應直接這樣改。**

因為 governance handoff 與 authority semantics 尚未正式拍板。

### B3 Canonical status
**`blocked`（尚未達 `contract_ready`）**

### B3 implementation lane may proceed?
**No，僅可做 shell / non-authoritative UI scaffolding。**

### B3 需補哪些 exact undecided fields
- memo lifecycle
- publish / review semantics
- session-to-memo mapping
- governance handoff contract
- `allowedActions.canInitiateGovernanceReview`

---

## B4. TW-02 Parameter Controls
文件指出：
- `docs/bff/TW-02-parameter-controls.md`、screen spec、example payload 已存在
- `docs/lovable/PANTHEON_FRONTEND_SA.md` 把它寫成 `contract-published`
- backlog 仍寫 `missing controls read route, patch route, validation contract, and diff response shape`
- BFF main 內未見 live route。fileciteturn24file8turn24file9

### 我方正式回答

#### B4-1. `TW-02` 的 read contract、patch semantics、validation contract、diff shape 是否已 fully locked？
**尚未 fully locked。**

#### B4-2. backlog 是否應改為 `contract-published; pending BFF implementation`？
**目前不應直接改。**

#### B4-3. 哪些 patch / reject / invalid behavior 仍待拍板？
至少包含：
- patch validation scope
- invalid patch response shape
- reject / partial-apply policy
- preview 失敗時的 degraded semantics
- diff response shape

### B4 Canonical status
**`blocked`（尚未達 `contract_ready`）**

### B4 implementation lane may proceed?
**No，僅可實作無權威 shell 與 form scaffolding。**

---

## B5. KW-02 / KW-03 / KW-04 / KW-05 Knowledge Family Ratification
文件指出 knowledge family 的 repo truth 嚴重漂移：
- contract docs 已存在
- packet family 把 `KW-02` 到 `KW-05` 寫成 `ready / implemented / resolved`
- `PANTHEON_FRONTEND_SA.md` 仍寫成 `blocked / shell-only`
- backlog 仍寫 `module not ready`
- BFF knowledge overview 仍視為 `not_ready`
- 而 `main.py` 內未見對應 live route decorator，表示 packet family 至少存在過度宣稱。fileciteturn24file5turn24file10turn24file11

### 我方正式回答

#### B5-1. `KW-02` 到 `KW-05` 的 contract 文檔是否都已 canonical locked？
- `KW-02`：**可視為 contract 文檔已基本存在，待 ratification**
- `KW-03`：**可視為 contract 文檔已基本存在，待 ratification**
- `KW-04`：**可視為 contract 文檔已基本存在，待 ratification**
- `KW-05`：**不視為 fully locked；仍需 architecture 補 versioned strategy spec semantics**

#### B5-2. `KW-006` packet family 中的 `ready / implemented / resolved` 是否屬於過度宣稱？
**是。至少對 `KW-02`~`KW-05` 整體打包寫成 implemented/resolved，與目前 code truth 不一致。**

#### B5-3. 正確 canonical readiness 應該是什麼？
我方建議：
- `KW-02`：`contract_ready`（pending BFF）
- `KW-03`：`contract_ready`（pending BFF）
- `KW-04`：`contract_ready`（pending BFF）
- `KW-05`：`blocked` 或至多 `draft_contract`（若 ladder 不接受 `draft_contract`，則維持 `blocked`）

#### B5-4. frontend lane 目前是否仍應維持 shell-only？
- `KW-02`~`KW-04`：**不應再一律 shell-only；可進入 pending BFF / screen work，但不得假裝已 implemented**
- `KW-05`：**仍應維持 shell-only，直到 strategy spec contract fully ratified**

#### B5-5. backlog / SA / knowledge overview / packet family 四者，哪一個才代表 canonical readiness truth？
**正式規則：**
1. 若有 `MODULE_READINESS_RATIFICATION` 文件，該文件為 readiness truth 第一順位
2. 若尚無 ratification 文件，則以 `WORKBENCH_DELIVERY_BACKLOG.md + code truth` 為第一順位
3. `SA` 與 `packet family` 屬衍生文件，不得覆蓋 canonical readiness truth

### B5 期待交付（正式要求）
- `MODULE_READINESS_RATIFICATION_2026-04-20.md`
- 明確逐模組標示 `KW-02`、`KW-03`、`KW-04`、`KW-05` 的 canonical status
- 回收或修正 packet family 的 overclaim

---

# 4. C 類：需要 Architecture Clarification 的 Module-Gating 規則

## C1. CW-03 Committee Board 的正式解鎖條件
文件指出：
- `CW-03` route 與 sponsor-decision authority 已 live
- backlog 與 BFF overview 都把它視為 module-gated
- `CW-01` 已 live，但 `CW-02` 仍未 live
因此要求 architecture team 正式拍板：`CW-03` 是否必須等 `CW-02` live 才能開啟，或可 partial activation。fileciteturn24file4turn24file5

### 我方正式回答

#### C1-1. `CW-03` production handoff 是否必須等到 `CW-02` live？
**不必完全等到 `CW-02` live，允許 partial module activation。**

#### C1-2. 允許的 partial module activation 範圍
在 `CW-02` 尚未 live 時，`CW-03` 可以開放：
- board summary
- sponsor decision status
- committee outcome summary
- board-level read-only overview

但不得把自己宣稱為完整 consultation workbench，因為 transcript drill-down 與完整 debate replay 還未到位。

#### C1-3. 何時可升到完整 production handoff
當 `CW-02` transcript layer live 且可提供：
- transcript drill-down
- append-only event projection
- actor labeling truth
- inline evidence link truth

之後 `CW-03` 才能從 `partial activation` 升到完整 handoff-ready / production-ui-ready。

### C1 正式 gate rule
請將下列規則寫入 readiness ladder / packet promotion rule：

> `CW-03` route-live ≠ full module-ready。
> `CW-03` 在 `CW-02` 未 live 前可 partial activate；
> 但完整 production handoff 必須以 `CW-02` transcript layer live 為前提。

---

# 5. D 類：不應再送回 Architecture 的項目（正式確認）

文件列出以下項目不該再當成未定案 system design：
- `EW-04`
- `EW-05`
- `RW-01`
- `RW-02`
- `RW-03`
- `RW-04`
- `CW-01`
- `CW-03` 的 route / authority contract 本身
- `KW-01`
- `TW-01`
- `TW-03`
- `TW-04`。fileciteturn24file4turn24file18

### 我方正式回答
**同意。**

這些項目的主要差距已經是：
- implementation
- truth-hardening
- UI activation
- wiring
- 文件 rebaseline

而不是缺抽象 architecture 設計。

因此，這些項目應轉入：
- implementation lane
- BFF hardening lane
- frontend activation lane
- docs alignment lane

不應再回送 architecture。

---

# 6. 建議 Architecture Team 的正式回答格式（我方同意）
文件建議 architecture team 對每題用同一格式回答：
1. `Canonical status`
2. `Still open? yes/no`
3. `If open, exact undecided fields`
4. `If closed, implementation lane may proceed? yes/no`
5. `Docs that must be updated after this answer`。fileciteturn24file2turn24file4

### 我方補充
請再加第 6 項：
6. `Resulting readiness state`

這樣每題回答完，就能直接回寫到：
- `WORKBENCH_DELIVERY_BACKLOG.md`
- BFF overview
- SA
- packet family

---

# 7. 我方要求 Architecture Team 本輪必交付的文件

## 7.1 全域規範
- `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- `docs/conventions/DEGRADATION_DICTIONARY.md`
- `docs/conventions/MODULE_READINESS_LADDER.md`

## 7.2 Ownership decisions
- `docs/decisions/LIN-002-lineage-ownership.md`
- `docs/decisions/control-plane-persona-boundary.md`
- `docs/decisions/control-plane-router-enforcement-ownership.md`

## 7.3 Ratification outputs
- `MODULE_READINESS_RATIFICATION_2026-04-20.md`
- 逐模組回填 `KW-02`、`KW-03`、`KW-04`、`KW-05`
- 回收 overclaim 的 packet family / SA 文本

## 7.4 仍缺 contract 的模組
- `docs/bff/CW-02-debate-transcript.md`（ratified version）
- `docs/bff/CW-04-redteam-memo.md`（ratified version）
- `docs/bff/TW-02-parameter-controls.md`（ratified version）
- `docs/bff/KW-05-strategy-spec.md`（ratified version）
- `docs/bff/RW-05-artifact-compare.md`（status-confirmed version）

---

# 8. 最終結論

我對這份 open questions 文件的正式回答是：

1. 我同意它的總判斷：Pantheon 現在真正未定案的 system design，主要集中在 **全域 canonical conventions、lineage / persona / router 三個 cross-service boundary、以及若干 contract 已存在但 readiness truth 漂移的模組 ratification**。fileciteturn24file13turn24file2
2. A1~A4 這四類確實屬於 architecture bucket，我方已在本文件中逐題定案。
3. B 類 ratification 模組中：
   - `RW-05`：應視為 `contract_ready`，implementation 可前進
   - `CW-02`、`CW-04`、`TW-02`：仍屬 blocked，需 architecture 先補 lock
   - `KW-02`~`KW-04`：應 ratify 為 `contract_ready / pending_bff`
   - `KW-05`：仍應保留在 blocked
4. `CW-03` 的 route / authority contract 不需重畫，但 module gate 規則必須補：允許 partial activation，不得直接冒充 full module-ready。fileciteturn24file4turn24file5
5. D 類列出的項目，原則上不應再回送 architecture，應轉 implementation / hardening / UI activation lane。fileciteturn24file2turn24file18

### 一句話收斂
> 現在需要 architecture team 補的，不是 Pantheon 高階藍圖，而是把「全域規範、少數 ownership decision、少數尚未 ratify 的 module contract 與 readiness truth」正式鎖定；鎖定後，其餘多數 workbench 模組都應立即轉入 implementation 與 UI handoff，而不是繼續在 architecture lane 反覆空轉。fileciteturn24file13turn24file2
