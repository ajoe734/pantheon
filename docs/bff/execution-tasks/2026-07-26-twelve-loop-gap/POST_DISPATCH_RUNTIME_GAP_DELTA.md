# Twelve-Loop Post-Dispatch Runtime Gap Delta

Document Version: `9.0.0`
Date: `2026-07-27`
Task ID: `OPS-L12-RUNTIME-GAP-DELTA-001`
Owner: `Claude`
Reviewer: `Codex2`
Program: `pantheon-twelve-loop-gap-2026-07-26`

---

## 1. Purpose And Boundary

本文件是 Twelve-Loop Gap Remediation Program 的第四層（Layer 4）delta 歸檔：只補記
**三輪 gap baseline 盤點與 25-task catalog 派工「之後」才出現的 runtime 缺口**。

不修改、不重述、不取代下列既有基線：

- `docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/ROUND1_SPEC_RUNTIME_AUDIT.md`
- `docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/ROUND2_IMPLEMENTATION_FAILURE_AUDIT.md`
- `docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/ROUND3_ACCEPTANCE_EVIDENCE_AUDIT.md`
- `docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/tasks.json`（25-task catalog）

本版（v9.0.0）取代 v1.0.0（已由 PR #4200 合併進 `dev`）、v2.0.0、v3.0.0、v4.0.0、
v5.0.0、v6.0.0、v7.0.0 與 v8.0.0 的內容主張。v1–v3 由 owner `Antigravity` 撰寫：v1.0.0 於 journal seq 1685 由
`Human/Ops` 獨立否決，v2.0.0 與 v3.0.0 由當時的 reviewer `Claude` 判為事實錯誤且未進入
PR。journal seq 1943（`2026-07-26T21:12:08Z`，`Human/Ops`）將本 task 改派為 owner
`Claude` / reviewer `Codex2`。v4.0.0 由新 owner 交付於 PR `#4221`，被 `Human/Ops` 否決
（過期 canonical 快照、`L12-CAP-001` 事實錯誤、未來時間戳、head 綁定不涵蓋交付 bytes）。
v5.0.0 修掉上述四項並改以**內容摘要（content digest）**綁定交付 bytes，但在 head
`5a9ed0c9957529467fce0b7afa0338546987ee4b` 上被 `Human/Ops` 第三次否決，理由為兩項**尚未
被任何規則擋下的結構性缺陷**：

1. **可變 PR 事實被當成 cut 當下的真值**：v5.0.0 宣告的 cut 為 `2026-07-26T22:26:48Z`，
   但其引用的 `#4211`、`#4203` head 與檢查顏色在該時點之前就已經變動。
2. **required check 只覆蓋已被否決的舊 head**：`checks_bound_to_commits` 只檢查 check 的
   head 是否出現在 `anchor_commits` 之中，因此一份「完全沒有為交付 bytes 跑過任何檢查」
   的 manifest 仍會回報零拒絕。

v6.0.0 的修正是結構性的，不只是改字：所有可變表面的事實一律降格為**綁定 head 與觀測
時點的時點觀測**（§2、§5.0），並新增兩條 fail-closed 規則
（`current_delivery_checks`、`mutable_observation_binding`）與一份**非循環交付收據契約**
（§7.5）。

v6.0.0 交付於 PR `#4221` head `b3f8edad0b5ac078ada3dd791b8166dbaf58cf9e`，被 reviewer
`Codex2` 第四次否決，理由是**七條規則全部只讀 manifest 對收據的自述，沒有一條去讀收據
本身**：把 `receipt_role` 與 `bound_content_digest` 搬到已被否決的 v4 commit
`5c39428dda1d3c1e42fa926aa5f320467e1b8324`、將其 `delivery_state` 改寫成 live、再重封
`evidence.sha256`，七條規則依然零拒絕——即使 git object 顯示該 commit 的本文件 sha256 為
`9ac925e0…` 而非被綁定的 `4f2f7735…`，且該 commit 完全不含兩支 validator script。
v7.0.0 新增第八條規則 `receipt_commit_artifacts`：直接以 `git ls-tree` / `git cat-file` 離線讀出
收據 commit 的 tree，逐一比對每個被綁定 artifact 的存在與 blob sha256，並在 git 不可用、
commit 未知、路徑缺失或摘要不符時 fail closed（§7.6）。

v7.0.0 交付於 PR `#4221` head `04332822e44922d64a4a403cfe6223f311e9954b`，被 reviewer
`Codex2` 第五次否決。這次**收據證明本身成立**——三個 blob 在收據 commit 上與綁定值相符、
最終 head 未更動任一 blob、必要檢查全綠、八條規則零拒絕。被否決的是**未隨版本重切的
敘述**：`evidence.json` 仍以 seq 2046 與 seq 2014 描述本次 cut 的 canonical 快照、仍稱
文件為 v6 與「七條規則」、仍把 `#4203` 寫成 trailers 失敗且 `BEHIND`，而同一份 manifest
的觀測表已記錄該 PR 為綠。這些全部是**關於當前 cut 的主張**，而既有八條規則只讀結構化
欄位、不讀敘述，於是 schema、checksum 與八條規則同時放行了一份自相矛盾的 manifest。
v8.0.0 (historical) 新增第九條規則 `current_cut_consistency`：它**不**新增
宣告區塊，而是把當前 cut 的身分從 manifest 既有結構導出——版本取自
`task.evidence_cut_semantics` 的開頭句、canonical 快照序號取自
`authorities.actual_state[0]`、交付收據取自帶 `receipt_role` 的那一個 anchor、規則數取自
驗證器自身——再要求被列管的敘述欄位與該身分一致；確實屬於舊版的字樣必須在其後緊接字面
標記 `(historical)`，否則即為拒絕（§7.7）。

v8.0.0 (historical) 交付於 PR `#4221` head
`a5de47447b607a2f561b852fc40bf33035ffcba0`，被 reviewer `Codex2` 第六次否決。schema、
checksum、九條 (historical) 規則、77 個測試、dispatch valid/25、收據檢查與 baseline /
catalog diff 全數通過，被否決的是**同一種缺陷換了一個 artifact**：出問題的不是 manifest，
而是 manifest 用 content digest 綁住的**本文件**。當時的 §1 寫著 cut 身分宣告於
`evidence.json` 的一個名為 `current_cut` 的欄位——那個欄位不存在、也從未存在，同一份文件
的 §7.7 反而正確地寫著身分是從既有結構導出的；§3.4 則仍稱本版只掃描到 seq 2014
(historical)，與本 cut 的邊界 seq 2191 及 manifest 自己的掃描命令矛盾。九條規則沒有一條
會發現這件事，因為**九條規則全部只讀 manifest，沒有一條打開 manifest 綁定的文件**。
v9.0.0 新增第十條規則 `bound_document_consistency`：直接讀取被 content digest 綁定的
delta 文件，拒絕「宣告於某個不存在的 manifest 欄位」與「與本 cut 宣告不符的掃描邊界」
兩類主張，並要求文件至少明載一次本 cut 的邊界（§7.8）。v1–v8 中被推翻的具體主張逐條
列於 §7。

本文件**不宣稱**十二循環已完成、已 hosted 啟用、或本 task 已完成。

---

## 2. Authoritative Sources And Verification Method

每一項事實都可由下列來源重新驗證；本文件不引用未經查證的 PR、run 或事件序號。

| 來源 | 路徑 / 位置 | 用途 |
| :--- | :--- | :--- |
| Task-state journal | `/home/lupin/pantheon-ci-deploy/runtime/task-state-events.jsonl`（append-only，本版查證截止於 seq 2191，`2026-07-27T01:18:27Z`） | 非終態任務消失／復原事實 |
| Loop catalog registry | `docs/deployment/loop-catalog.registry.json`（`global-loop-catalog-2026-07-13`, `loop_catalog.v2`） | 十二循環 controller / maturity / evidence 現況 |
| BFF loop read model | `services/control-plane/bff/loop_inventory.py` | 何種 evidence 才會被接受為 live proof |
| Runtime manifest | `docker-compose.yml`（66 services） | worker 是否被 default 啟動、restart/health/volume 設定 |
| Hosted deploy path | `scripts/deploy_nonprod_vm.sh` | `COMPOSE_PROFILES` 預設集合 |
| Hosted acceptance evidence | `ajoe734/execute-plans` run `30192435033` artifact `controller/accepted-deployment.json`、`controller/evidence.jsonl` | 現役 FE/BFF 身分與 accept 時間 |
| Canonical task rows | `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh show <task>`；journal seq 2191 state | owner / reviewer / status |
| Archived task rows | `/home/lupin/pantheon/ai-task-archive/tasks/<TASK-ID>.json` | 已歸檔 `done` 任務的 owner / reviewer |
| PR 狀態 | `gh pr view <n> --repo ajoe734/pantheon`、`gh api repos/ajoe734/pantheon/commits/<head>/check-runs` | 只引用查得到的 PR，且一律以「head + 觀測時點」記錄 |
| Live command runtime | `$PANTHEON_COMMAND_ROOT`（`PANTHEON_COMMAND_RUNTIME_SHA=bdbd0a99bf68e6a635d9bd936782c659298b7bb7`） | merged 與 installed 的落差 |

### 2.1 可變表面的觀測規則（v6.0.0 新增）

`journal sequence`、`PR head`、`mergeStateStatus` 與 check 顏色都是**會在觀測之後繼續變動
的表面**。v5.0.0 被否決的第一項理由，就是把這類事實寫成「evidence cut 當下的真值」——
`#4211` 與 `#4203` 在宣告的 cut（`2026-07-26T22:26:48Z`）之前就已經前進。

本版因此改用單一規則，貫穿全文與 `evidence.json`：

- 任何可變事實都必須寫成 **`<事實> @ head <40-hex> @ <觀測時點 UTC>`**；
- 沒有 head 與觀測時點的可變事實不得出現在本文件或 manifest 中；
- 這類事實只主張「在該 head、該時點為真」，**不主張此後未再變動**；審查者應以
  §2 的命令對自己的觀測時點重新查核，head 不同即視為事實已前進，而非本文件錯誤；
- 這條規則由驗證器規則 `mutable_observation_binding` 對 `evidence.json` 強制執行
  （§7.3）：讀取 `gh pr` / `gh run` / `gh api` 的每一筆 `validation.commands` 都必須帶
  `observed_at` 與 `observations[]`，且每筆觀測都要有 40-hex 的 `head_sha`。

---

## 3. Journal Sequence Audit（更正版）

以下計數由 journal 每筆 `state.tasks` 直接統計：`total` = 任務總數，`nonterminal` =
狀態不在 {`done`,`supersede`,`superseded`} 者，`L12` = id 以 `L12-` 開頭者。

### 3.1 第一次非終態清空：1592 → 1593 → 1594–1595

| Seq | committed_at (UTC) | source | total | nonterminal | L12 |
| ---: | :--- | :--- | ---: | ---: | ---: |
| 1592 | 2026-07-26T15:25:36Z | `Ops` | 22 | 19 | 17 |
| 1593 | 2026-07-26T15:26:05Z | `codex-20260726T151724Z-097aa5af` | **0** | **0** | **0** |
| 1594 | 2026-07-26T15:29:41Z | `temporary-live-repair-root-restore-seq1592-after-empty-snapshot` | 22 | 19 | 17 |
| 1595 | 2026-07-26T15:30:51Z | `Human/Ops` | 22 | 19 | 17 |

seq 1593 由一個 `codex-*` worker session 提交了空快照，22 個任務（19 非終態、17 個
L12）一次歸零。seq 1594 以 append-only 方式把 seq 1592 的 state 重新提交（`state.updated_at`
仍為 `2026-07-26T15:25:33Z`，即 1592 的時間戳），seq 1595 由 `Human/Ops` 確認。整段
復原沒有改寫或刪除任何既有 journal 事件。

### 3.2 復發：1606、1610

| Seq | committed_at (UTC) | source | total |
| ---: | :--- | :--- | ---: |
| 1606 | 2026-07-26T15:41:09Z | `codex-20260726T153521Z-4d574bea` | **0** |
| 1607 | 2026-07-26T15:41:17Z | `claude1-2-20260726T153449Z-c8cf034d` | 22 |
| 1610 | 2026-07-26T15:41:56Z | `codex-20260726T153521Z-4d574bea` | **0** |
| 1611 | 2026-07-26T15:42:05Z | `Human/Ops` | 23 |

同一個 `codex-*` session 在 1594–1595 復原後 11 分鐘內又提交了兩次空快照。這兩次沒有
專門的復原事件，而是被下一筆正常 worker/Human 提交覆蓋。這代表 1593 不是單一意外。

### 3.3 第二次非終態清空：1645 → 1646–1650 → 1651

| Seq | committed_at (UTC) | source | total | nonterminal | L12 |
| ---: | :--- | :--- | ---: | ---: | ---: |
| 1645 | 2026-07-26T16:07:10Z | `Ops` | 23 | 20 | 16 |
| 1646 | 2026-07-26T16:07:30Z | `codex-20260726T160349Z-f490a02c` | **0** | 0 | 0 |
| 1647 | 2026-07-26T16:07:58Z | `Ops` | **0** | 0 | 0 |
| 1648 | 2026-07-26T16:08:34Z | `codex-20260726T160349Z-f490a02c` | **0** | 0 | 0 |
| 1649 | 2026-07-26T16:08:43Z | `Ops` | **0** | 0 | 0 |
| 1650 | 2026-07-26T16:09:03Z | `codex-20260726T160349Z-f490a02c` | **0** | 0 | 0 |
| 1651 | 2026-07-26T16:09:32Z | `Ops-Recovery-Antigravity` | 23 | 20 | 16 |

seq 1645 的基線是 **23 total / 20 nonterminal / 16 L12**（不是 22，也不是 17 個 L12）。
1646 起連續 5 筆為 0；其中 **1647 與 1649 是 `Ops` 來源**，代表空狀態已經被正常治理路徑
讀入並再次提交，污染面比「單一 worker 寫壞」更廣。seq 1651 由
`Ops-Recovery-Antigravity` 以 append-only 方式復原到 seq 1645 的 state
（`state.updated_at` 回到 `2026-07-26T16:07:01Z`）。

### 3.4 全 journal 空快照普查

在 seq 1..2191 的全量掃描中，`state.tasks` 為空的提交共 **9 筆**。這個掃描邊界與 §2、
§3.6 宣告的 canonical 快照邊界是同一個 seq 2191，不是另一次較早的掃描：v4.0.0
(historical) 掃描到 seq 1952 (historical) 時為 9 筆，v5.0.0 (historical) 推進到
seq 2014 (historical)、v6.0.0 (historical) 推進到 seq 2046 (historical)、v7.0.0
(historical) 推進到 seq 2142 (historical)、本版推進到 seq 2191，計數自始未變——9 筆全部
落在 `2026-07-26T16:09:03Z` 之前，其後至 seq 2191 的 541 筆事件沒有再出現空快照：

| Seq | committed_at (UTC) | source |
| ---: | :--- | :--- |
| 715 | 2026-07-24T00:23:01Z | `antigravity-20260724T002224Z-4f8dd7ba` |
| 1593 | 2026-07-26T15:26:05Z | `codex-20260726T151724Z-097aa5af` |
| 1606 | 2026-07-26T15:41:09Z | `codex-20260726T153521Z-4d574bea` |
| 1610 | 2026-07-26T15:41:56Z | `codex-20260726T153521Z-4d574bea` |
| 1646 | 2026-07-26T16:07:30Z | `codex-20260726T160349Z-f490a02c` |
| 1647 | 2026-07-26T16:07:58Z | `Ops` |
| 1648 | 2026-07-26T16:08:34Z | `codex-20260726T160349Z-f490a02c` |
| 1649 | 2026-07-26T16:08:43Z | `Ops` |
| 1650 | 2026-07-26T16:09:03Z | `codex-20260726T160349Z-f490a02c` |

派工後 44 分鐘內（15:26–16:09）發生 8 筆，橫跨 3 個不同的 worker session 與 2 筆 `Ops`
提交。這是 §5 Gap 10「recurrence」的量化依據。

### 3.5 拒絕與 handoff：1685、1687

| Seq | committed_at (UTC) | source | total / nonterminal / L12 |
| ---: | :--- | :--- | :--- |
| 1685 | 2026-07-26T16:21:08Z | `Human/Ops` | 23 / 20 / 16 |
| 1687 | 2026-07-26T16:21:34Z | `antigravity1-3-20260726T161118Z-636c9135` | 23 / 20 / 16 |

seq 1685 是 Human/Ops 對本 task v1.0.0 交付的獨立否決；seq 1687 是在該否決之後 26 秒
由 worker session 發出的 handoff。1687 沒有推翻 1685：本 task 於本文件寫作時仍為
`in_progress`，並已改派 owner `Claude` / reviewer `Codex2`。

### 3.6 查證邊界（seq 2191 的時點快照）

seq 2191，`2026-07-27T01:18:27Z`，source `claude1-2-20260727T005150Z-a4a383cb`：
**30 total / 27 nonterminal / 15 L12**（`15 L12` 指 25-task catalog 中仍在板上、id 以
`L12-` 起首的 task；`OPS-L12-*` 為派工後新增的 ops task，計入 total 而不計入這 15）。

這是**時點快照**，不是「現況」。v4.0.0 (historical) 引用 seq 1952 (historical)
（`2026-07-26T21:19:49Z`，31 / 28 / 15）而在送審時已過期；v5.0.0 (historical) 引用
seq 2014 (historical)（`2026-07-26T22:13:08Z`，30 / 26 / 15）並在該版審查期間又被 32 筆
事件超前；v6.0.0 (historical) 引用 seq 2046 (historical)（`2026-07-26T22:50:08Z`，
29 / 26 / 15）並在其審查期間又被 96 筆事件超前；v7.0.0 (historical) 引用
seq 2142 (historical)（`2026-07-27T00:13:26Z`，31 / 28 / 15）並在其審查期間又被 49 筆
事件超前。本版把所有 canonical owner / reviewer / status 一律重新取自 seq 2191，並依
§2.1 明載該序號與時間戳；審查者在更高的 sequence 上讀到不同的值，屬於 journal 正常前進，
應以 §2 命令自行重讀。

這個「引用即過期」的模式本身就是證據，不是本文件的失誤：canonical journal 是 append-only
且持續前進的，任何快照都會在審查完成前被超前。因此本文件的主張一律綁定序號，而**不**
主張「此後未再變動」（§8 最後一項）。**但「快照會過期」不是「敘述可以不重切」的理由**：
v7.0.0 (historical) 正是因為文件已推進到 seq 2142 (historical)、`evidence.json` 的敘述卻
仍停在更早的序號而被否決（§7.7）。本版把這個界線寫成可執行規則
`current_cut_consistency`：快照序號在 `evidence.json` 只宣告一次，所有列管敘述必須引用
同一個序號，舊序號必須標記 `(historical)`。

seq 2142 (historical) → 2191 之間實際發生變動、且本版已據以更正的六筆：

| Task | seq 2142 (historical)（v7.0.0 (historical)） | seq 2191（本版） |
| :--- | :--- | :--- |
| `L12-CAP-001` | `in_progress` | **`review_approved`**（`last_update` `2026-07-27T01:15:57Z`；§5 Gap 6、§6 已更正） |
| `L12-IMIT-001` | `todo` | **`in_progress`**（`last_update` `2026-07-27T01:18:07Z`；§5 Gap 3、§6 已更正） |
| `OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001` | `in_progress` | **`review_approved`**（`last_update` `2026-07-27T01:13:59Z`；§5 Gap 10、§6 已更正） |
| `OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001` | `review` | 已歸檔離板（PR `#4211` 於 `8c4d727296d575cf49b9d3a6e1b7a222396063e3` 合併；§5 Gap 8、§6 已更正） |
| `OPS-CI-PR-TRAILER-RANGE-001` | `review` | `todo`（`last_update` `2026-07-27T01:03:58Z`） |
| `OPS-L12-PYTHON-PACKAGING-PROVISION-001` | `in_progress` | `review`（`last_update` `2026-07-27T01:16:41Z`） |

`L12-CAP-001` 與 `OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001` 進入 `review_approved`
是**治理層**的推進：兩者的「仍缺證據」欄（§5 Gap 6、Gap 10）皆由產品證據與 runtime 安裝
決定，不由 canonical status 決定，因此本版不因狀態改變而刪除任一項。

25-task catalog 中已歸檔為 `done` 的 10 個（狀態逐一由 `ai-task-archive/tasks/*.json`
複核）：`L12-FLEET-001`（`Codex`/`Codex2`）、`L12-CTRL-001`（`Claude`/`Codex2`）、
`L12-TEL-001`（`Antigravity`/`Codex`）、`L12-REC-001`（`Codex`/`Codex2`）、
`L12-SRC-001`（`Codex2`/`Codex`）、`L12-ALPHA-001`（`Codex`/`Codex2`）、
`L12-AGORA-001`（`Antigravity`/`Codex`）、`L12-CONS-001`（`Codex`/`Codex2`）、
`L12-DEP-001`（`Codex2`/`Codex`）、`L12-TEACH-001`（`Claude`/`Codex`）。
仍在板上的 15 個見 §5 / §6。

---

## 4. Hosted Identity Record（現役身分）

以下取自 `ajoe734/execute-plans` deploy run `30192435033` 的 sealed evidence
artifact（`controller/accepted-deployment.json`、`controller/evidence.jsonl`）。

| 欄位 | 值 |
| :--- | :--- |
| Frontend repository | `ajoe734/execute-plans` |
| Served FE commit | `6a8d2d9b4f725056735eefd7165ef47b52cda53d` |
| BFF repository | `ajoe734/pantheon` |
| Served BFF runtime commit | `be956c07aca889043ef301389412b6744452f20b` |
| BFF base URL | `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io` |
| Pair ID | `c05fc6b0abea92ceb1805cde8c2f3f4d7bcfab12fb77ac45be0a4241ea5874cf` |
| `deploymentState` | `accepted` |
| **`acceptedAt`** | **`2026-07-26T07:23:44Z`** |
| Release | `20260726T072219Z-6a8d2d9b4f72-gate-30192097967-30192435033-1-887536` |
| FE integration gate run | `30192097967`（success，head `6a8d2d9b4f72`） |
| Deploy run | `30192435033`（success，job 07:14:15Z → 07:23:57Z） |
| Safety build flags | `VITE_BFF_MODE=live`、`VITE_BFF_FALLBACK=strict`、`VITE_BFF_REAL_WRITES=false`、`VITE_BFF_ALLOW_DEV_STUB_WRITES=false`、`VITE_BFF_EMBEDDED_BEARER_TOKEN=false` |

evidence chain 內 `release.accepted` 事件 `at=2026-07-26T07:23:44.271Z`、
`release.completed` `at=2026-07-26T07:23:44.335Z`，兩者的 hash 鏈連續。

### 4.1 Hosted 十二循環證明：0 / 12

依 `docs/deployment/loop-catalog.registry.json`（12 個 canonical loop）：

- `controller_contract.status = not_implemented`：**12 / 12**
- `evidence_profile.reconciled_live_proof.status = present`：**0 / 12**（11 `planned`、1 `historical`）
- `evidence_profile.proven_live_evidence.status = present`：**0 / 12**（11 `planned`、
  `capital_pool_execution` 為 `historical`）
- `maturity.current`：11 個 `api-only`、`capital_pool_execution` 為 `manual`；
  12 / 12 的 `target` 為 `reconciled`

依 `services/control-plane/bff/loop_inventory.py`，只有 `reconciled_live_proof` 與
`proven_live_evidence` 會被接受為 live 證據（`_LIVE_EVIDENCE_LEVELS`），且 controller
record 超過 900 秒即視為過期。因此目前 hosted 端可被接受的十二循環 live proof 為
**0 of 12**。

`docs/deployment/evidence/twelve-loop-gap/` 下亦不存在 `L12-HOSTED-001`、
`L12-MANIFEST-001`、`L12-TRUTH-001`、`L12-FE-TRUTH-001` 或任一 `L12-VERIFY-*` 的
evidence 目錄。上述現役 FE/BFF pair 是 **PPL-ALLOC-009 的 hosted acceptance 身分**，
不是十二循環的 hosted proof；本文件只把它記錄為「後續修復所依據的現役身分」。

---

## 5. Post-Dispatch Runtime Gaps

每個 gap 都列出：現象與可驗證證據、影響、canonical task、owner、reviewer、acceptance
條文、PR、tests、仍缺證據。`PR: none` 表示該 task 目前確實沒有對應 PR，不以任何未查證
的 PR 編號填補。所有 owner/reviewer/status 取自 journal seq 2191 的 canonical row
（`2026-07-27T01:18:27Z`）；已歸檔任務取自 `ai-task-archive/tasks/*.json`。

### 5.0 本節引用的 PR 時點觀測（依 §2.1）

下表是本節所有 PR 事實的唯一來源。每一列都綁定 head 與觀測時點，**只主張該 head 在該
時點為真**；`#4203` 與 `#4211` 在 v5.0.0 (historical) 宣告的 cut 之前就已前進，
v5.0.0 (historical) 卻仍以舊 head 敘述，這正是該版被否決的第一項理由。

| PR | branch | head @ 觀測時點 | state / mergeStateStatus | 該 head 的必要檢查 |
| :--- | :--- | :--- | :--- | :--- |
| `#4193` | `task/L12-DIST-001` | `192c1fbabc9574e6bb59ba23be6b0c145354764b` @ `2026-07-27T01:18:37Z` | OPEN / `BEHIND`（`01:18:58Z` 重讀） | merge-ref 的 `Commit trailers` **failure**（`2026-07-26T15:14:22Z`，run `30207761345`）；branch-ref 的三項為 success（run `30207760447`，`15:14:17Z`–`15:15:10Z`） |
| `#4203` | `task/L12-CAP-001` | `2cc1a2e6af3cca1d274c8a2ef87648c13e2affa8` @ `2026-07-27T01:18:38Z` | OPEN / `BEHIND`（`01:18:58Z` 重讀） | `Runtime mirror guard` / `Commit trailers` / `Smoke acceptance` 全 success（`2026-07-27T00:36:41Z`–`00:37:41Z`，run `30227826889` / `30227828295`） |
| `#4211` | `task/OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001` | `8c4d727296d575cf49b9d3a6e1b7a222396063e3` @ `2026-07-27T01:18:39Z` | **MERGED** | 合併前三項全 success（`2026-07-27T00:01:40Z`–`00:02:37Z`） |
| `#4221` | `task/OPS-L12-RUNTIME-GAP-DELTA-001` | `04332822e44922d64a4a403cfe6223f311e9954b` @ `2026-07-27T01:18:40Z` | OPEN / `BEHIND`、`autoMergeRequest` 為 `null` | 此為 v7.0.0 (historical) 被 `Codex2` 否決的最終 head（§7.7） |
| `#4221` | `task/OPS-L12-RUNTIME-GAP-DELTA-001` | `a5de47447b607a2f561b852fc40bf33035ffcba0` @ `2026-07-27T02:51:22Z` | OPEN / `BEHIND`（`02:51:31Z` 重讀）、`autoMergeRequest` 為 `null` | 此為 v8.0.0 (historical) 被 `Codex2` 否決的最終 head（§7.8）；該 head 的三項必要檢查於 `2026-07-27T01:50:38Z`–`01:51:32Z` 全 success |

`#4203` 與 `#4211` 的 head 在前一版之後又各自前進（v7.0.0 (historical) 分別記為
`945f47dce…` 與同一個 `8c4d72729…`，觀測於 `2026-07-27T00:19:47Z` / `00:19:48Z`）。
這**不是**本版的更正對象，而是 §2.1 規則正常運作的結果：舊版的那兩列在其自己載明的
觀測時點為真，本表則另立 `2026-07-27T01:18:3xZ` 的觀測。v5.0.0 (historical) 之所以被
否決，是因為它把已過期的值寫成 cut 當下的真值，而不是因為值會變。

本表 `#4193` / `#4203` / `#4211` 三列綁定的觀測時點為 `2026-07-27T01:18:3xZ`，`#4221`
另有 `2026-07-27T02:51:22Z` 的一列。`evidence.json` 另記兩組 cut 時點的觀測：v8.0.0
(historical) cut 的 `2026-07-27T01:37:46Z`（其中 `#4203` 已為 `MERGED`，`mergedAt`
`2026-07-27T01:25:19Z`），以及本版 cut 的觀測。各組觀測都只主張各自時點為真，依 §2.1
不互相取代；本文件不為每一次 PR 前進重切一版，否則 §7.5 終止的遞迴會從另一端回來。
`#4203` 的合併不改變 Gap 6 的「仍缺證據」：合併後的產品證據仍未取得。

`#4221` 自身在兩次觀測時點都是 `BEHIND`：v7 (historical) 那次是 `dev` 前進到
`7fedefb281dd416e0412e935c48e866438f56e6d`，v8 (historical) 那次是 `dev` 在其
`a5de47447…` 之後又再前進。每一版都在收據 commit 之前先合入當期 `dev` tip，因此
`BEHIND` 是**該次觀測時點的事實**，不是最終 head 的狀態；最終 head 的
`mergeStateStatus` 由 reviewer 於 §7.5 所述的 PR 留言時點自行重讀。

這些觀測**不改變任一 gap 的「仍缺證據」**：PR 轉綠代表 CI 檢查通過，不代表該 task 的
產品證據已取得（§8「Merged ≠ Installed」的同型推論）。

### Gap 1 — Scheduler：必要排程 worker 不在預設啟動集合（`scheduler`）

- **現象與證據**：`docker-compose.yml` 共 66 個 service，48 個無 `profiles:`（預設啟動），
  18 個被 profile 隔離。其中 `source-ingest-scheduler`（profile `source-ingest-scheduler`）
  與 `policy-learning-shadow-eval-scheduler`（同名 profile）皆非預設啟動。
  `scripts/deploy_nonprod_vm.sh:111` 的 `DEV_COMPOSE_PROFILES` 預設為空字串
  （日誌記為 `<default-safe>`）；該腳本內最寬的 profile 集合（`:1973`）為
  `activation-ready-smoke,dormant-smoke,openclaw,openclaw-activation-ready-e2e,search-index-scheduler,smoke,source-search-bounded`，
  **不含** `source-ingest-scheduler`，也不含 `policy-learning-shadow-eval-scheduler`。
  腳本註解明確記載 `source-ingest-scheduler is deliberately NOT in the default set`，
  理由是每 60 秒對第三方 provider 抓取會形成單一雲端出口的持續爬取。
- **影響**：`L12-MANIFEST-001` acceptance 第 1 條（每個必要 scheduled/async worker 由
  intended default 啟動）與 acceptance 第 3 條（source egress 維持 deny-by-default）目前
  互相牴觸，現行解法是犧牲第 1 條。連帶使 `source_ingestion` 與
  `human_imitation_shadow_evaluation` 兩個 loop 的 `scheduled_tick` 證據無法產生。
- **Canonical task**：`L12-MANIFEST-001`（wave 3, lane `runtime-manifest`）
- **Owner / Reviewer**：`Antigravity` / `Claude`（status `todo`）
- **Acceptance**：`Every required scheduled or async worker is represented and started by the intended default`；`Source egress remains bounded and deny-by-default`
- **PR**：none（task 尚未開工）
- **Tests**：`scripts/test_source_ingest_deploy_diagnostics_contract.py`、
  `scripts/test_evolution_daily_sweep_deploy_contract.py`
- **仍缺證據**：一份明確裁決「bounded egress 下 source scheduler 的預設啟動形態」的
  compose config readback；`policy-learning-shadow-eval-scheduler` 納入預設集合的決定與
  local-stack health 證據。

### Gap 2 — Projector：projector 不產出可被接受的 controller record（`projector`）

- **現象與證據**：`loop-run-projector-scheduler` 為預設啟動且有 healthcheck，但
  `source-ingest-agora-projector` 與 `source-ingest-scheduler` 共用同一個 profile，因此
  同樣不會被預設啟動。更關鍵的是 `docs/deployment/loop-catalog.registry.json` 中
  **12 / 12 loop 的 `controller_contract.status` 為 `not_implemented`、
  `controller_name` 為 `null`**；`services/control-plane/bff/loop_inventory.py` 只接受
  來源為 `controller_store` / `service_store` / `target_runtime` 且 900 秒內的 record。
- **影響**：`L12-TRUTH-001` acceptance 第 1 條（十二個 loop 都送出當期 tenant-scoped
  canonical controller record）與第 4 條（catalog 的 controller 名稱／查詢／restart／
  liveness 欄位與實作一致）目前全數未達成；operator truth 只能落在 registry metadata 或
  snapshot fallback 等級。
- **Canonical task**：`L12-TRUTH-001`（wave 3, lane `operator-truth`）
- **Owner / Reviewer**：`Claude` / `Antigravity`（status `todo`）
- **Acceptance**：`All twelve loops emit current tenant-scoped canonical controller records`；`Catalog controller names queries restart and liveness fields match implementation`
- **PR**：none
- **Tests**：`services/control-plane/bff/test_loop_health_read_model_contract.py`、
  `services/control-plane/bff/test_loop_inventory_read_model_contract.py`
- **仍缺證據**：12 個 loop 的 `controller_name` 與 desired/actual query 定案；一次
  非 snapshot 來源的 controller record readback。

### Gap 3 — Imitation：shadow evaluation 排程未進入 runtime（`imitation`）

- **現象與證據**：`policy-learning-svc` 為預設啟動，但真正驅動 shadow evaluation 的
  `policy-learning-shadow-eval-scheduler` 被 profile 隔離且不在任何 deploy 預設集合中
  （見 Gap 1）。registry 中 `human_imitation_shadow_evaluation` 的
  `scheduled_tick` / `reconciled_live_proof` / `proven_live_evidence` 皆為 `planned`。
- **影響**：`L12-VERIFY-LEARN-001` acceptance 第 3 條（真實 dataset 產生一個 gated
  imitation candidate 且不得使用 seed fallback）無法在現行 runtime 上取得證據。
- **Canonical task**：`L12-VERIFY-LEARN-001`（wave 4）；前置為 `L12-IMIT-001`
- **Owner / Reviewer**：`L12-VERIFY-LEARN-001` = `Antigravity` / `Claude`（`todo`）；
  `L12-IMIT-001` = `Claude` / `Codex2`（**`in_progress`**，`last_update`
  `2026-07-27T01:18:07Z`）。**本版更正 v7.0.0 (historical)**：該版依
  seq 2142 (historical) 記為 `todo`，此後轉入 `in_progress`（§3.6）；改派本身則發生在
  更早的 `2026-07-27T00:10:47Z`，v6.0.0 (historical) 依 seq 2046 (historical) 誤記為
  `Antigravity` / `Claude`。狀態推進不減少下列「仍缺證據」。
- **Acceptance**：`Real dataset creates a gated imitation candidate without seed fallback`
- **PR**：none
- **Tests**：`services/policy-learning/` 之 shadow-eval 契約測試（由 `L12-IMIT-001` 指定）
- **仍缺證據**：一次 real-dataset（非 seed）的 gated candidate 產生紀錄；shadow-eval
  scheduler 的 restart / liveness readback。

### Gap 4 — Alpha persona：persona → ExperimentRun 的權威鏈未驗證（`alpha persona`）

- **現象與證據**：`L12-ALPHA-001` 已 `done`（owner `Codex`、reviewer `Codex2`、
  review_file `docs/deployment/evidence/twelve-loop-gap/L12-ALPHA-001/evidence.json`），
  `alpha-replication-worker` 為預設啟動；但該 worker **沒有 healthcheck**
  （見 Gap 8 清單），且 registry 中 `alpha_replication` 的 controller 仍為
  `not_implemented`、`proven_live_evidence` 為 `planned`。
- **影響**：`L12-VERIFY-KNOW-001` acceptance 第 2 條（approved StrategySpec 產生
  authoritative ExperimentRun）與第 5 條（BFF 與 controller 的終態真值與所有權威一致）
  尚無 hosted 證據；`L12-ALPHA-001` 的 done 只涵蓋實作交付，不涵蓋產品流程證明。
- **Canonical task**：`L12-VERIFY-KNOW-001`（wave 4）
- **Owner / Reviewer**：`Claude` / `Antigravity`（status `todo`）
- **Acceptance**：`Approved StrategySpec produces authoritative ExperimentRun`；`BFF and controller terminal truth match every authority`
- **PR**：none（`L12-ALPHA-001` 的既有交付 PR 不作為本 gap 的證據）
- **Tests**：由 `L12-VERIFY-KNOW-001` 指定的 persona→spec→ExperimentRun 端對端測試
- **仍缺證據**：真實 Persona requirement 起始的一條完整鏈；unapproved spec 與 immutable
  approved artifact 的負向 gate 結果。

### Gap 5 — Consultation：有 API service，沒有 durable workflow executor（`consultation`）

- **現象與證據**：`consultation-svc` 為預設啟動、具 healthcheck 與 `consultation-data`
  volume；但 `docker-compose.yml` 中**沒有任何 consultation workflow executor / worker
  service**（66 個 service 內無對應項）。registry 中 `consultation` 的 controller 為
  `not_implemented`、`proven_live_evidence` 為 `planned`。`L12-CONS-001` 已 `done`
  （`Codex` / `Codex2`）。
- **影響**：`L12-VERIFY-LEARN-001` acceptance 第 4 條（真實 consultation workflow 產生
  一份 memo 與一次 governance handoff）缺少可執行的 runtime 載體。
- **Canonical task**：`L12-VERIFY-LEARN-001`（wave 4）；runtime 納管由 `L12-MANIFEST-001` 承擔
- **Owner / Reviewer**：`Antigravity` / `Claude`（status `todo`）
- **Acceptance**：`Real consultation workflow creates one memo and one governance handoff`
- **PR**：none
- **Tests**：`services/consultation/` 之 workflow executor 契約測試
- **仍缺證據**：executor 是否應為獨立 compose service 的裁決；一次真實 workflow 的
  memo + handoff readback。

### Gap 6 — Capital reconciliation：對帳鏈可跑但 CAP 任務被擋（`capital reconciliation`）

- **現象與證據**：`capital`、`paper-fleet-reconciler`、`paper-signal-producer`、
  `reconciliation-drift-svc` / `-consumer` / `-scheduler` / `-incident-listener` 皆為預設
  啟動，但 `paper-signal-producer`、`reconciliation-drift-consumer`、
  `reconciliation-drift-scheduler`、`reconciliation-drift-incident-listener` **均無
  healthcheck**；實際執行 paper 訂單的 `pantheon-paper-runtime` 位於 profile
  `static-paper-runtime`，不在任何 deploy 預設集合中。canonical row（seq 2191，
  `2026-07-27T01:18:27Z`）顯示 `L12-CAP-001` 為 owner `Codex` / reviewer `Claude` /
  status **`review_approved`**（`last_update` `2026-07-27T01:15:57Z`）；registry 中
  `capital_pool_execution` 的
  `maturity.current` 為 `manual`、`proven_live_evidence` 為 `historical`（非 `present`）。
- **Evidence-only closeout 的圍堵結果**：稍早的 canonical row（`last_update`
  `2026-07-26T18:40:07Z`，當時 status 為 `blocked`）記載對 PR `#4203` 當時 head
  `5dbc95673c4390f7ae140a89b8fe88b95cf81059` 的獨立稽核結論（該 head 為**歷史觀測**，
  在 `2026-07-26T22:50:22Z` 已被 `7cec6f7cb0929a34024eb67c27bb299d2a2c6d62` 取代）：
  production `get_reconciler()` 以 `leader_store=None` 建構 `PaperFleetReconciler`，
  等於每個 replica 都預設為 leader；file lease 是無鎖的 read/overwrite，其所謂
  cross-process 測試實際上是同一 process 內依序呼叫兩個物件；Redis lease 使用
  GET 後 SET 並無條件續約，可在過期後覆蓋後繼者；Redis signal claim 的
  LMOVE/RPOPLPUSH 與 HSET(timestamp) 分離，移動後時間戳失敗會讓 inflight item 永久
  被 reclaim 略過；ack / nack-requeue / DLQ 皆為多命令非原子路徑；six-binding drill
  只用 `InMemoryPendingSignalStore` 與 mock execution，未證明 Redis 或
  process/container restart。這證實 CAP 先前的 evidence-only closeout 主張不成立，
  圍堵有效。
- **圍堵之後的進展（本版更正）**：`L12-CAP-001` 已不再是 `blocked`。canonical row 於
  `2026-07-26T22:50:02Z` 記為 owner `Codex` / reviewer `Claude` / `in_progress`，其
  `next` 欄位記載 anchor `baf573278` 使 Redis claim/ack/nack/DLQ/reclaim 成為單一命令
  原子操作，並加入 fail-closed、token-fenced 的 Redis/file reconciler lease production
  wiring；真實 Redis 多 process crash/restart 證明、capital actor/tenant 授權與 evidence
  recut 仍在進行中，且未變更 config 或 live-capital 設定。**狀態改變的是治理層，不是
  證據層**：下列「仍缺證據」在 `#4203` 收斂並取得獨立審查前依然全部成立。
- **影響**：`L12-VERIFY-RUNTIME-001` acceptance 第 1 條（immutable approved artifact →
  一個 RuntimeBinding 與一個 paper worker）與第 5 條（order/fill/position/heartbeat 與
  BFF stage 真值共用權威 correlation）在 `L12-CAP-001` 取得上述缺漏證據之前無法取證。
- **Canonical task**：`L12-VERIFY-RUNTIME-001`（wave 4）；前置為 `L12-CAP-001`
- **Owner / Reviewer**：`L12-VERIFY-RUNTIME-001` = `Claude` / `Antigravity`（`todo`）；
  `L12-CAP-001` = `Codex` / `Claude`（**`review_approved`**）。**本版更正
  v7.0.0 (historical)**：該版依 seq 2142 (historical) 記為 `in_progress`，該 task 於
  `2026-07-27T01:15:57Z` 進入 `review_approved`。這是**治理層**的推進，不是產品證據；
  下列「仍缺證據」在 `#4203` 合併並取得產品證據前依然全部成立
- **Acceptance**：`Immutable approved artifact reaches one RuntimeBinding and one paper worker`；`Order fill position heartbeat and BFF stage truth share authoritative correlation`
- **PR**：`#4194`（merged 2026-07-26T15:18:50Z）、`#4196`（merged 2026-07-26T15:53:05Z）
  為 `L12-CAP-001` 的已合併 evidence anchor；`#4203`
  `L12-CAP-001: make governed-paper execution lossless and isolated`
  是目前的收斂路徑。依 §5.0 的時點觀測：head
  `2cc1a2e6af3cca1d274c8a2ef87648c13e2affa8` @ `2026-07-27T01:18:38Z` 為 **open /
  `BEHIND`**，該 head 的三項必要檢查全 success（`2026-07-27T00:36:41Z`–`00:37:41Z`）。
  **本版更正 v5.0.0 (historical)**：該版寫「head `5dbc956`、Commit trailers fail、
  `BEHIND`」，該敘述在其自稱的 cut（`22:26:48Z`）之前就已不成立（`#4203` 於
  `22:25:34Z`、`22:25:44Z` 連續前進）。v6.0.0 (historical) 記的 `7cec6f7cb…` @
  `22:50:22Z`、v7.0.0 (historical) 記的 `945f47dce…` @ `2026-07-27T00:19:47Z` 在其各自
  時點為真，之後該 PR 又前進到本列的 head——依 §2.1，這些都只是時點觀測，並非互相矛盾。
  **`BEHIND` 與檢查全綠可以同時為真**：前者是與 `dev` tip 的相對關係，後者是該 head 上
  的 check 結論。**CI 轉綠不等於本 gap 收斂**：下列「仍缺證據」由產品證據決定，不由
  check 顏色決定。
- **Tests**：`services/control-plane/governance/test_product_closeout_verdict.py`
- **仍缺證據**：原子化的 claim / timestamp / ack / nack / DLQ（Lua 或等價 durable
  consumer-group 交易）；接入 production constructor 的 fenced leader 取得與續約；
  真實 Redis + 雙 process + six-binding restart drill 與各命令邊界的故障注入；
  `pantheon-paper-runtime` 的部署形態裁決；`#4203`（head
  `2cc1a2e6af3cca1d274c8a2ef87648c13e2affa8`）的合併與合併後的產品證據——`L12-CAP-001`
  已進入 `review_approved`，但 §5.0 的觀測顯示該 PR 於觀測時點尚未合併。

### Gap 7 — Evolution：dispatch 與 sweep worker 無健康契約（`evolution`）

- **現象與證據**：`evolution`、`evolution-dispatch-worker`、
  `evolution-daily-sweep-scheduler`、`evolution-threshold-sweep-producer` 皆為預設啟動
  且 `restart: unless-stopped`；但 `evolution-daily-sweep-scheduler` 與
  `evolution-threshold-sweep-producer` **無 healthcheck**，四者皆未設
  `stop_grace_period`。registry 中 `evolution` 的 controller 為 `not_implemented`。
- **已重現的 split-brain outbox**：canonical row（`last_update` `2026-07-26T19:10:52Z`）
  記載對 `L12-EVO-001` head `f7f81a9ff` 的獨立稽核：compose 把 evolution API 掛在
  `/data/evolution`，但 `evolution-dispatch-worker` **沒有 `EVOLUTION_DATA_DIR`、共享
  volume、`DATABASE_URL` 或 `EVOLUTION_STORE_BACKEND`**，兩側 `build_dispatch_outbox_store`
  都退回 JSON，重現結果為 API 側 `api_records=1` 而 worker 側 `worker_records=0`。
  另記 `EvolutionDecisionStore` 即使有 `DATABASE_URL` 仍固定使用 `decisions.json`；
  governance / deployment / runtime plane 在 action matrix 中可見，但 worker 一律標為
  unsupported 並 dead-letter。該 task 目前**沒有 evidence 目錄也沒有 PR**。
- **影響**：`L12-VERIFY-OBS-001` acceptance 第 3、4 條（已解決 incident 產生一份
  postmortem 與一個 governed EvolutionDecision；approved action 抵達真實下游終態並具
  retry / compensation）缺少 liveness 與 graceful-stop 的支撐證據。
- **Canonical task**：`L12-VERIFY-OBS-001`（wave 4）；前置為 `L12-EVO-001`
- **Owner / Reviewer**：`L12-VERIFY-OBS-001` = `Antigravity` / `Claude`（`todo`）；
  `L12-EVO-001` = `Claude` / `Antigravity`（`in_progress`）
- **Acceptance**：`Resolved incident produces one postmortem and one governed EvolutionDecision`；`Approved action reaches real downstream terminal state with retry and compensation`
- **PR**：none（`L12-EVO-001` 目前既無 PR 也無 evidence 目錄）
- **Tests**：`scripts/test_evolution_daily_sweep_deploy_contract.py`；稽核記錄的重跑結果為
  256 passed / 1 failed（telemetry duplicate retry），未設定時的初次執行 collection 失敗
- **仍缺證據**：API 與 worker 共用單一權威 durable backend（建議 Postgres 並顯式指定
  backend/DSN）且缺少 production persistence 設定時 fail closed；一次跨 process 的
  compose restart 測試證明 approve → outbox → worker claim → 下游終態 receipt →
  executed，含 retry / DLQ / replay / compensation 與 tenant 隔離；四個 evolution
  worker 的 health / heartbeat / graceful-stop 設定。

### Gap 8 — Storage healthcheck：儲存層健康未成為被接受的基礎設施遙測（`storage healthcheck`）

- **現象與證據**：`postgres`、`minio`、`nats` 皆有 container healthcheck 與具名
  volume（`postgres-data`、`minio-data`、`nats-data`，共 25 個具名 volume）；但預設啟動的
  48 個 service 中有 **11 個沒有 healthcheck**：`alpha-replication-worker`、
  `deployment-outbox-consumer`、`evolution-daily-sweep-scheduler`、
  `evolution-threshold-sweep-producer`、`minio-init`、`paper-signal-producer`、
  `reconciliation-drift-consumer`、`reconciliation-drift-incident-listener`、
  `reconciliation-drift-scheduler`、`source-ingest-controller-migrate`、
  `strategy-distillation-worker`（其中 `minio-init` 與 `source-ingest-controller-migrate`
  為一次性 init job）。registry 中 `bff_health_monitoring` 的 controller 為
  `not_implemented`。container 層級的健康狀態目前沒有被轉成 BFF 可接受的
  infrastructure telemetry。
- **已重現的遙測授權缺口**：canonical row（`last_update` `2026-07-26T18:44:40Z`）記載對
  `L12-BFF-001` branch head `25f3c131` 的獨立稽核：focused monitor 與 sentinel 套件為
  **56 passed / 2 failed**——strict telemetry ingest 以 **401** 拒絕 monitor POST，因為
  `_post_json` 未帶 service JWT 或 tenant authority；real incident create 透過
  runtime-manager 驗證假 sentinel binding 而失敗。monitor 只把 probe 計數、incident id
  與投遞狀態放在 process 記憶體；`_emit_telemetry_sync` 吞掉失敗且無 durable retry/DLQ；
  recovery 在 resolve 成功前就 pop 掉 incident 對應；event id 每個 replica 隨機；
  無 error-rate spike 觸發；target registry 只涵蓋 5 個環境變數而非完整的 BFF 下游集合。
  另記 telemetry binding bypass 可被偽造：`runtime_health` 或 `infrastructure_health`
  加上任一 `infrastructure_probe`/`bff_health_probe` dict 即可跳過權威 binding 驗證而
  無需可信 producer principal。
- **影響**：`L12-VERIFY-OBS-001` acceptance 第 5 條（BFF 下游停止與復原產生被接受的
  infrastructure telemetry 並收斂一個 incident）與 `L12-MANIFEST-001` acceptance 第 2 條
  （worker 具備 restart / health / heartbeat / durable volume / auth / graceful-stop）
  皆未達成。
- **Canonical task**：`L12-VERIFY-OBS-001`（wave 4）；前置為 `L12-BFF-001`
- **Owner / Reviewer**：`L12-VERIFY-OBS-001` = `Antigravity` / `Claude`（`todo`）；
  `L12-BFF-001` = `Antigravity` / `Claude`（`in_progress`）
- **Acceptance**：`BFF downstream stop and recovery emits accepted infrastructure telemetry and resolves one incident`
- **PR**：`#4211`
  `OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001: prove infra health authority`
  是相關的遙測授權交付。依 §5.0 的時點觀測：head
  `8c4d727296d575cf49b9d3a6e1b7a222396063e3` @ `2026-07-27T01:18:39Z` 已為
  **MERGED**，合併前三項必要檢查全 success（`2026-07-27T00:01:40Z`–`00:02:37Z`）。
  **本版更正 v7.0.0 (historical)**：該版依 `2026-07-27T00:19:48Z` 的觀測記為
  open / `CLEAN`，該 PR 此後已合併，對應 task
  `OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001` 也已離開 canonical 板面（§3.6）。
  更早的 v5.0.0 (historical) 寫「head `4e24e895…`、`BEHIND`、merge-ref 的
  Commit trailers fail」，但 `c1686aae` 的 committer 時間為 `2026-07-26T22:24:26Z`，
  早於該版自稱的 cut `22:26:48Z`，該敘述在 cut 當下即已過期。
  **合併不等於本 gap 收斂**：`#4211` 交付的是遙測授權面，下列「仍缺證據」由
  `L12-VERIFY-OBS-001` 的端對端證據決定（§8「Merged ≠ Installed」的同型推論）。
- **Tests**：`services/control-plane/bff/test_loop_health_read_model_contract.py`；
  `services/control-plane/bff/test_bff_downstream_health_monitor.py`（稽核記錄 56 passed / 2 failed）
- **仍缺證據**：9 個常駐 worker 的 healthcheck 補齊；durable 共享的 probe/outbox/incident
  狀態與具穩定 event id 的 retry/DLQ/replay；完整 target registry 與 error-rate 觸發；
  restart / 雙 replica / 真實服務 stop-recovery 證明；strict-auth 非交易 schema
  （`OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001` 已由 `#4211` 合併交付，但其產品證據
  仍須由 `L12-VERIFY-OBS-001` 端對端取得；incident authority 由 `L12-EVO-001` 擁有的
  `services/incidents` 契約提供）。

### Gap 9 — Revision：派工後的 catalog / assignment 修訂需在 manifest 端對齊（`revision`）

- **現象與證據**：25-task catalog 派工後發生兩次治理修訂：`assignment-revision-1.json`
  把所有未完成 task 的實作 owner 改為 Antigravity / Claude 系（`INDEX.md` 記載
  Codex-family 僅保留既有獨立審查），以及 catalog owner lock 與 revision replay。
  已合併 PR：`#4187`（`OPS-L12-CATALOG-OWNER-LOCK-002`, 13:51:20Z）、
  `#4188`（`OPS-L12-FLEET-REASSIGNMENT-001`, 14:15:36Z）、
  `#4189`（`OPS-L12-CATALOG-REVISION-REPLAY-001: preserve admitted task scope`, 14:20:19Z）、
  `#4184`（`OPS-L12-PROOF-OWNERSHIP-001: govern deferred proof`, 13:16:42Z）。
  這些修訂改變了 owner/reviewer 與 proof 產出歸屬，但 `L12-MANIFEST-001` 及其下游
  wave 3–5 task 的 runtime 交付內容尚未依修訂後的擁有權重新排程。
- **影響**：wave 3–5 的 12 個 dependency（`L12-MANIFEST-001` 的 `depends_on` 涵蓋全部
  12 個 loop 實作 task）中已有 10 個 `done`、2 個未完成（`L12-DIST-001` `in_progress`、
  `L12-CAP-001` `in_progress`），`L12-MANIFEST-001` 仍為 `todo` 而未被啟動。
- **Canonical task**：`L12-MANIFEST-001`（wave 3）
- **Owner / Reviewer**：`Antigravity` / `Claude`（status `todo`）
- **Acceptance**：`Compose config and local stack health pass with no duplicate legacy workers`
- **PR**：`#4184`、`#4187`、`#4188`、`#4189`（皆 merged，屬治理修訂本身；
  `L12-MANIFEST-001` 本身無 PR）
- **Tests**：`scripts/test_dispatch_twelve_loop_gap_2026_07_26.py`
- **仍缺證據**：修訂後 owner 對 `L12-MANIFEST-001` 的啟動紀錄；`L12-DIST-001` 與
  `L12-CAP-001` 兩個未完成 dependency 的收斂路徑。另註：`assignment-revision-1.json`
  把未完成 task 的實作 owner 收攏到 Antigravity / Claude 系，但 `L12-CAP-001` 於
  `2026-07-26T22:50:02Z` 的 canonical owner 為 `Codex`，代表修訂後的擁有權在
  runtime 端已再度分歧，需由 `L12-MANIFEST-001` 一併對齊。

### Gap 10 — Recurrence：非終態清空的防護已合併但未安裝（`recurrence`）

- **現象與證據**：§3.4 顯示派工後 44 分鐘內發生 8 筆空快照提交。防護
  `validate_state_transition()`（`prev_nonterminal > 0 且 new_nonterminal == 0` 即
  `TaskStateStoreError: task-state nonterminal drop rejected`）位於
  `.orchestrator/rewrite/task_state_store.py`，由 PR **`#4199`**
  `OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001: reject nonterminal task-state collapse to empty snapshot`
  於 **2026-07-26T16:47:57Z** 合併進 `dev`（變更檔：
  `.orchestrator/rewrite/task_state_store.py`、`.orchestrator/rewrite/test_task_state_store.py`、
  `conftest.py`、`scripts/test_verify_task_state_store.py`）。
  **但目前實際執行狀態命令的 command root
  `PANTHEON_COMMAND_RUNTIME_SHA=bdbd0a99bf68e6a635d9bd936782c659298b7bb7`
  （= PR #4179 的 merge，2026-07-26T12:48:52Z）落後 `dev` tip
  `7fedefb281dd416e0412e935c48e866438f56e6d` **160 個 commit**
  （`git rev-list --count` @ `2026-07-27T01:23:24Z`），
  其 `.orchestrator/rewrite/task_state_store.py` 內
  `grep -c "nonterminal drop rejected"` 為 0**，亦即該防護尚未安裝到執行中的 runtime。
  （v5.0.0 (historical) 在此處誤寫為 119，與同版 `evidence.json` 記載的 129 自相矛盾；
  v6.0.0 (historical) 起改記 129 並對齊；本版依當期 `dev` tip 重算為 160，落後只增不減。）
  同理，lock-order 修復 PR `#4197`（merged 15:59:10Z）也晚於安裝點。
- **影響**：`L12-HOSTED-001` acceptance 第 3 條（full stack restart 保存或復原在途工作
  且不產生重複效果）所依賴的狀態層不變式，目前在 live runtime 上仍未生效；十二循環
  hosted drill 期間若再發生一次空快照，仍會直接落盤。
- **Canonical task**：`L12-HOSTED-001`（wave 5）；防護安裝由
  `SUP-COMMAND-RUNTIME-REFRESH-001` 承擔
- **Owner / Reviewer**：`L12-HOSTED-001` = `Claude` / `Antigravity`（`todo`）；
  `OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001` = `Claude` / `Codex2`
  （seq 2191 為 **`review_approved`**，`last_update` `2026-07-27T01:13:59Z`；
  v7.0.0 (historical) 依 seq 2142 (historical) 記為 `in_progress`）；
  `SUP-COMMAND-RUNTIME-REFRESH-001` = `Claude` / `Codex2`（seq 2191 仍為 **`blocked`**，
  `last_update` `2026-07-26T23:32:20Z`；v6.0.0 (historical) 依 seq 2046 (historical)
  記為 `in_progress`、v5.0.0 (historical) 依 seq 2014 (historical) 記為 `todo`。這個
  task 正是 §5 Gap 10「已合併但未安裝」的收斂路徑，其 `blocked` 屬於該 gap 的現況，
  不改變 gap 結論）
- **Acceptance**：`Full stack restart preserves or recovers in-flight work without duplicate effects`
- **PR**：`#4199`（merged）為防護本身；`#4197`（merged）為 task-brief lock-order
  正規化，兩者是**不同的修復**，不得合併敘述。
- **Tests**：`.orchestrator/rewrite/test_task_state_store.py`、
  `scripts/test_verify_task_state_store.py`
- **仍缺證據**：command runtime 升級到含 `#4197` 與 `#4199` 的 sha 之後的重新驗證；
  升級後一次刻意的空快照被拒絕的 readback。

### Gap 11 — Hosted：現役 pair 已被接受，但十二循環 hosted proof 仍為 0 / 12（`hosted`）

- **現象與證據**：§4 的現役 FE/BFF pair 於 `2026-07-26T07:23:44Z` 被接受
  （`deploymentState=accepted`），但該接受是 PPL-ALLOC-009 的 hosted acceptance；
  §4.1 顯示十二循環的 `reconciled_live_proof` 與 `proven_live_evidence` 皆為 0 / 12，
  且 `L12-HOSTED-001`、`L12-FE-TRUTH-001`、四個 `L12-VERIFY-*` 均為 `todo`、皆無
  evidence 目錄。
- **影響**：程式閉環授權（`tasks.json.completion_authority`）要求
  `L12-CLOSE-001` 直接依賴 `L12-HOSTED-001`、`L12-TRUTH-001`、`L12-SIGNOFF-001`，
  且需 Human/Ops 受保護裁決；目前三者皆未完成。
- **Canonical task**：`L12-HOSTED-001`（wave 5）
- **Owner / Reviewer**：`Claude` / `Antigravity`（status `todo`）
- **Acceptance**：`Hosted manifest identifies exact merged Pantheon and execute-plans commits and images`；`All required workers are healthy with twelve current accepted controller records`
- **PR**：none
- **Tests**：由 `L12-HOSTED-001` 指定的 hosted drill 套件
- **仍缺證據**：一份把上述 FE `6a8d2d9b…` / BFF `be956c07…`（或其後繼）綁定到十二循環
  controller record 的 hosted manifest；12 筆當期且被接受的 controller record。

### Gap 12 — Distillation：materialization identity 不穩定且 PR trailer 檢查為紅（`revision` 的實作面）

- **現象與證據**：canonical row（`last_update` `2026-07-26T18:38:14Z`）記載對
  PR `#4193` head `192c1fb` 的獨立稽核，在 60 個 focused test 全綠的情況下仍重現兩個
  阻斷缺陷：(1) `lease_expires_at` 已過期的 claimed job 仍能用原 token 呼叫
  `mark_done`——探測在 lease 於 t=101 過期的情況下於 t=102 settle 成 `status=done`；
  (2) `_distill_one` 由可變的 `queue.version_count` 推導 `version_key`，當 version 1
  已 materialize seed、Registry 投遞失敗、version 2 先被admit 而 version 1 才重試時，
  version-1 的重試會改變 bundle identity——重現結果為 **2 個 source version 產生 3 個
  seed**。依 §5.0 的時點觀測，`#4193` head
  `192c1fbabc9574e6bb59ba23be6b0c145354764b` @ `2026-07-27T01:18:37Z` 仍為 open /
  **`BEHIND`**，且 merge-ref 的 **`Commit trailers` 為 failure**
  （`2026-07-26T15:14:22Z`，run `30207761345`）。這是四個被引用 PR 中唯一自
  v5.0.0 (historical) 以來 head 未變動的一個。
- **影響**：`L12-MANIFEST-001` 的 12 個 dependency 中 `L12-DIST-001` 尚未收斂；
  `L12-VERIFY-KNOW-001` acceptance 第 1 條（真實 Persona requirement 產生 SourceRecord
  與一份可變 StrategySpec draft）所需的 exactly-once 語意目前不成立。
- **Canonical task**：`L12-DIST-001`（wave 2, lane `source`）
- **Owner / Reviewer**：`Claude` / `Antigravity`（status `in_progress`）
- **Acceptance**：`Committed normalized SourceRecord transactionally enqueues one versioned distillation job`；`Crash before or after Registry write replays to one terminal draft`
- **PR**：`#4193`（head `192c1fbabc9574e6bb59ba23be6b0c145354764b` @
  `2026-07-27T01:18:37Z`：open、merge-ref 的 Commit trailers failure、`BEHIND`）
- **Tests**：`services/source_ingestion/` 與 Registry 套件（稽核記錄 60 focused tests 通過但
  未涵蓋上述兩個路徑）
- **仍缺證據**：terminal 與 retry/DLQ 轉移拒絕過期 claim 的 exact expiry-boundary 迴歸；
  materialization identity 於 admission 時固化或僅由不可變 job 欄位推導；一次
  crash/outage + 中間版本插入的迴歸證明「每個 committed version 恰好一個 seed 與一份
  Registry draft」；Commit trailers 修復與 rebase。

---

## 6. Gap → Task Mapping Matrix

| Gap | 主題 | 目標 task | Owner | Reviewer | 現況 | 已驗證 PR | 仍缺證據（摘要） |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | scheduler | `L12-MANIFEST-001` | Antigravity | Claude | todo | none | 預設啟動裁決 + compose readback |
| 2 | projector | `L12-TRUTH-001` | Claude | Antigravity | todo | none | 12 個 controller_name + 非 snapshot readback |
| 3 | imitation | `L12-VERIFY-LEARN-001` | Antigravity | Claude | todo | none | real-dataset gated candidate |
| 4 | alpha persona | `L12-VERIFY-KNOW-001` | Claude | Antigravity | todo | none | persona→ExperimentRun 完整鏈 |
| 5 | consultation | `L12-VERIFY-LEARN-001` | Antigravity | Claude | todo | none | executor 形態裁決 + memo/handoff |
| 6 | capital reconciliation | `L12-VERIFY-RUNTIME-001` | Claude | Antigravity | todo | #4194, #4196 (CAP, merged); #4203 (open / BEHIND、三項檢查全綠 @ `2cc1a2e6af3cca1d274c8a2ef87648c13e2affa8` @ `2026-07-27T01:18:38Z`) | paper sleeve 實測 + CAP 原子化／fenced lease 的真實 Redis 證明 |
| 7 | evolution | `L12-VERIFY-OBS-001` | Antigravity | Claude | todo | none | worker health/graceful-stop + 下游終態 |
| 8 | storage healthcheck | `L12-VERIFY-OBS-001` | Antigravity | Claude | todo | #4211 (**MERGED** @ `8c4d727296d575cf49b9d3a6e1b7a222396063e3` @ `2026-07-27T01:18:39Z`) | 9 個 worker healthcheck + incident 收斂 |
| 9 | revision | `L12-MANIFEST-001` | Antigravity | Claude | todo | #4184, #4187, #4188, #4189 (merged) | 修訂後啟動紀錄 + 2 個未完成 dependency |
| 10 | recurrence | `L12-HOSTED-001` | Claude | Antigravity | todo | #4197, #4199 (merged) | command runtime 安裝後重驗 |
| 11 | hosted | `L12-HOSTED-001` | Claude | Antigravity | todo | none | 十二循環 hosted manifest + 12 筆 controller record |
| 12 | distillation identity / trailer | `L12-DIST-001` → `L12-MANIFEST-001` | Claude | Antigravity | in_progress | #4193 (open / BEHIND、trailers failure @ `192c1fbabc9574e6bb59ba23be6b0c145354764b` @ `2026-07-27T01:18:37Z`) | expiry-boundary 迴歸 + 穩定 materialization identity |

支援型 task（非本文件擁有，僅記錄依存關係；狀態取自 journal seq 2191，
`2026-07-27T01:18:27Z`）：
`L12-IMIT-001`（`Claude`/`Codex2`，**in_progress**）、
`L12-CAP-001`（`Codex`/`Claude`，**review_approved**）、
`L12-EVO-001`（`Claude`/`Antigravity`，in_progress）、
`L12-BFF-001`（`Antigravity`/`Claude`，in_progress）、
`L12-DIST-001`（`Claude`/`Antigravity`，in_progress）、
`L12-SIGNOFF-001`（`Claude`/`Codex2`，in_progress）、
`OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001`（`Claude`/`Codex2`，**review_approved**）、
`SUP-COMMAND-RUNTIME-REFRESH-001`（`Claude`/`Codex2`，**blocked**）。
`OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001` 已隨 `#4211` 合併而離開 canonical 板面。
其中 `L12-CAP-001`、`L12-IMIT-001` 與 `OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001` 的
狀態推進，以及該筆離板，是 seq 2142 (historical) → 2191 之間變動的項目（§3.6）。

---

## 7. Corrections To v1.0.0 – v7.0.0

### 7.1 對 v1.0.0 – v3.0.0 的更正

本版明確推翻前三版下列主張（v1.0.0 已由 PR #4200 合併進 `dev`，故必須在此更正）：

1. **seq 1645 的計數**：前版寫「23 任務 / 20 非終態 / **17** 個 L12」。實際為
   **16 個 L12**（§3.3）。
2. **第二次清空的復原點**：v2.0.0 曾寫「1648 完成復原」。實際 1646–1650 連續為 0，
   復原發生在 **1651**（§3.3）。
3. **1647 / 1649 的性質**：前版未記錄這兩筆 `Ops` 來源的 0-task 提交（§3.3）。
4. **復發次數**：前版只記錄 1593 與 1646 兩次。實際全 journal 有 9 筆空快照、
   派工後 8 筆（§3.4）。
5. **PR 對應**：前版把 `#4140/#4141/#4142` 對應 `L12-FLEET-001`、`#2690` 對應
   `L12-FLEET-001`、`#2695` 對應 `L12-DIST-001`、`#4195` 對應 `L12-CTRL-001`、
   `#4172` 對應「1646→1651 復原」，並把 `#4193` 描述為「`L12-DIST-001` 的 trailer
   已補齊、re-assignment 完成」。經 `gh` 查核：`#4195` 不存在；`#4193` 確實是
   `task/L12-DIST-001` 的 PR（head `192c1fbabc9574e6bb59ba23be6b0c145354764b`），但它
   **仍為 open、Commit trailers 檢查為 fail、狀態 `BEHIND`**，trailer 問題並未解決
   （§5 Gap 12）。本版不再引用任何未經 `gh` 查核的 PR。
6. **lock-order 與 nonterminal-drop guard 混寫**：前版把 seq 1650/1651 一併歸因於
   lock-order 正規化。兩者是不同修復：lock-order = PR `#4197`；nonterminal-drop
   guard = PR `#4199`（§5 Gap 10）。
7. **Owner / Reviewer**：前版標示 owner `Antigravity` / reviewer `Claude`。canonical row
   目前為 owner `Claude` / reviewer `Codex2`。

### 7.2 對 v4.0.0 的更正

v4.0.0 由本 task 的新 owner 交付於 PR `#4221`，並被 `Human/Ops` 於
`2026-07-26T21:49Z` 的稽核再次否決。以下四項為被否決的具體缺陷與其修正（第 1 項的修正
在 v5.0.0 之後又被推進了一次，見 §7.4 第 1 項）：

1. **過期的 canonical 快照**：v4.0.0 的所有 owner / reviewer / status 取自 journal
   seq 1952 (historical)（`2026-07-26T21:19:49Z`）。該快照在送審時已被後續事件取代。
   v5.0.0 改以 seq 2014 (historical)（`2026-07-26T22:13:08Z`）為準；v6.0.0 (historical)
   推進到 seq 2046 (historical)（`2026-07-26T22:50:08Z`）、v7.0.0 (historical) 推進到
   seq 2142 (historical)（`2026-07-27T00:13:26Z`）、v8.0.0 (historical) 推進到
   seq 2191（`2026-07-27T01:18:27Z`），本版**刻意停在同一個 seq 2191**（理由見 §7.8
   末段），並在 §2 / §2.1 / §3.4 / §3.6 / §5 前言明載查證邊界與時點語意。
2. **`L12-CAP-001` 事實錯誤**：v4.0.0 記為 owner `Antigravity` / reviewer `Claude` /
   status `blocked`。canonical row 實為 owner `Codex` / reviewer `Claude`，status 於
   seq 2191 為 `review_approved`（`last_update` `2026-07-27T01:15:57Z`；
   §5 Gap 6、§5 Gap 9、§6）。
3. **未來時間戳**：v4.0.0 的 evidence `record_log` sequence 7 記載
   `recorded_at 2026-07-26T22:00:00Z`，晚於當時的稽核時點，屬於尚未發生的事實主張。
   本版把所有 `record_log` 時間戳改為可由 git commit 時間或 GitHub check run
   `completed_at` 逐筆核對的實測值，並由 §7.3 的驗證器拒絕任何未來時間戳。
4. **head 綁定不涵蓋交付 bytes**：v4.0.0 送出的 PR head 為
   `0bb6d7ffbef956a57f3a3b300056f77088837f2a`，但 `validation.validated_head_sha`
   只寫到 `5c39428dda1d3c1e42fa926aa5f320467e1b8324`。由於 `0bb6d7f` 本身又改動了
   `evidence.json` 與 checksum，該綁定無法涵蓋當時實際交付的 bytes。這是 commit sha
   綁定的結構性缺陷，不是筆誤——任何「先寫 sha、再改檔」的順序都會複現它。

### 7.3 交付 bytes 的非循環綁定規則（本版新增）

commit sha 無法在 commit 之前得知，因此把交付 bytes 綁在 commit sha 上必然循環：
要嘛 sha 過期（v4.0.0 的情形），要嘛需要再一個 commit 去追記，而那個 commit 又會使
綁定再次過期。本版改用**內容摘要鏈**：

1. `POST_DISPATCH_RUNTIME_GAP_DELTA.md`（本文件）、
   `scripts/validate_twelve_loop_gap_evidence.py`、
   `scripts/test_validate_twelve_loop_gap_evidence.py` 三個檔案在 evidence 產出**之前**
   定稿，其 sha256 逐一記入 `evidence.json` 的
   `integrity.source_artifact_sha256_by_epoch`。
2. `validation.validated_head_sha` 記錄的不是 commit sha，而是
   `content-digest:sha256:<digest>`；`<digest>` 為上述檔案清單
   （`"<sha256>  <path>\n"` 依 path 排序後串接）的 sha256。
3. `evidence.json` 自身的 bytes 由 companion `evidence.sha256` 封存——manifest 不能包含
   自己的雜湊，因此這一環由外部檔案承擔，鏈路仍不循環。
4. `.orchestrator/task-briefs/ops_l12_runtime_gap_delta_001.md` **刻意排除**於綁定之外：
   它由 supervisor 於每次派工重新產生，納入綁定會產生與交付無關的失效。

驗證器 `scripts/validate_twelve_loop_gap_evidence.py` 對這份 evidence 執行**十條
fail-closed 拒絕規則**（v5.0.0 (historical) 為五條，v6.0.0 (historical) 新增
`current_delivery_checks` 與 `mutable_observation_binding`，v7.0.0 (historical) 新增
`receipt_commit_artifacts`，v8.0.0 (historical) 新增 `current_cut_consistency`，本版新增
`bound_document_consistency`），並由
`scripts/test_validate_twelve_loop_gap_evidence.py` 以迴歸測試逐條覆蓋：

| 規則 | 拒絕條件 | 對應缺陷 |
| :--- | :--- | :--- |
| `future_timestamp` | `task.evidence_cut_at`、`validation.validated_at`、`hosted_readback.pre_deploy.observed_at`、任一 `record_log[].recorded_at`、任一 `required_checks[].completed_at`、或任一 `validation.commands[].observed_at` / `observations[].observed_at` 晚於檢查時點 | §7.2 第 3 項 |
| `head_binding` | `validated_head_sha` 為裸 40-hex commit sha，或 `content-digest` 摘要與重算結果不符，或被綁定檔案的 sha256 與 `source_artifact_sha256_by_epoch` 不符 | §7.2 第 4 項 |
| `record_log_ordering` | `record_log` 的 `sequence` 非嚴格遞增，或 `recorded_at` 相對前一筆倒退 | §7.2 第 3 項的一般化 |
| `checks_bound_to_commits` | `implementation_delivery.required_checks[].head_sha` 未出現在 `anchor_commits[].sha` | §7.2 第 4 項的一般化 |
| **`current_delivery_checks`** | 沒有任何 `anchor_commits[]` 標記 `receipt_role: current_delivery_receipt`；或該收據 commit 被自身 `delivery_state` 標為 superseded / squashed / rejected / merged；或其 `bound_content_digest` 不等於 `validation.validated_head_sha`；或該 head 缺少 `Commit trailers`、`Runtime mirror guard`、`Smoke acceptance` 任一項的 success；或所有成功的 required check 都落在被標為 superseded 的 head 上 | §7.4 第 2 項 |
| **`receipt_commit_artifacts`** | 收據 commit 的 sha 不是 40-hex 小寫；或該 commit 不在本地 object store；或 `source_artifact_sha256_by_epoch` 為空；或該 commit 的 tree 缺少任一被綁定 artifact；或任一 blob 的 sha256 與 manifest 記載不符；或以該 commit 的 blob 重算出的 content digest 不等於 `validated_head_sha`；或 git 本身不可用（例如 `--git-root` 不是 git repository） | §7.6 |
| **`mutable_observation_binding`** | 任一讀取 `gh pr` / `gh run` / `gh api` / `gh search` 的 `validation.commands[]` 缺少 `observed_at` 或 `observations[]`，或任一觀測缺少 `subject`、缺少可解析的 `observed_at`、或其 `head_sha` 不是 40-hex 小寫 commit sha | §7.4 第 1 項 |
| `companion_checksum` | `evidence.json` 的實際 sha256 與 `evidence.sha256` 記載不符，或 companion 檔案缺少該筆記錄 | 封存 §7.3 第 3 點的那一環 |
| **`current_cut_consistency`** | `task.evidence_cut_semantics` 未以 `Owner evidence cut vX.Y.Z.` 開頭；或 `authorities.actual_state[0]` 未剛好載明一個未標記的 journal 序號；或帶有 `receipt_role` 的 anchor 不是剛好一個；或任一被列管的敘述欄位不存在；或任一 `validation.commands[]` 未宣告 `claim_scope`（`historical` 者未附 `historical_note`）；或被列管敘述中出現與該宣告不符且未標記 `(historical)` 的版本字樣、journal 序號、規則數、已被 superseded 的 commit sha；或引用了本 cut 觀測過的 PR 卻未同時引用該次觀測的 head | §7.7 |
| **`bound_document_consistency`** | content digest 綁定的 Markdown 文件不是剛好一份、不在樹上、或 `authorities.actual_state[0]` 無法導出唯一序號；或該文件中出現「宣告於 `evidence.json` 的 `<欄位>`」而該欄位在 manifest 中無法解析；或任一載有掃描邊界字樣（`查證截止` / `查證邊界` / `掃描邊界` / `全量掃描` / `推進到` 及其英文對應）的句子引用了與宣告不符且未標記 `(historical)` 的序號；或全文從未以宣告的序號載明本 cut 的邊界 | §7.8 |

前八條規則中，除 `receipt_commit_artifacts` 之外都只讀 manifest 自身的**結構化斷言**，
因此都可以用「改寫 manifest」來滿足；`receipt_commit_artifacts` 是唯一一條把 manifest 的
主張拿去對**外部不可竄改來源**（git object store）核對的規則。
`current_cut_consistency` 補的是另一個維度：它是第一條讀**敘述文字**的規則。前八條
規則能證明收據為真，卻證明不了 manifest 的散文有跟著這次交付重切——v7.0.0 (historical)
就是在「收據可驗證、敘述未重切」的狀態下被否決的（§7.7）。
`bound_document_consistency` 補的是第三個維度：**取材面**。前九條規則的輸入全部是
`evidence.json`，而 content digest 綁定的三個 artifact 裡有兩支 script 與一份本文件；
被交付、被審查、被引用的散文主要在文件裡，卻沒有任何一條規則打開它。v8.0.0
(historical) 就是在「manifest 內部完全一致、文件自相矛盾」的狀態下被否決的（§7.8）。

驗證器只讀不寫：它不修改工作樹，也不觸碰任何 status plane，且**完全離線**——唯一的外部
呼叫是 `git --no-optional-locks -C <git-root> ls-tree` / `cat-file`，不存取網路，也不需要 GitHub
憑證。`--repo-root` 指定被雜湊的 bytes 所在的樹，`--git-root` 指定收據 commit 所在的
object store；正常使用時兩者是同一個 repository，未指定 `--git-root` 時預設等於
`--repo-root`（因此把 `--repo-root` 指向沒有 object store 的暫存目錄會 fail closed，
而不是悄悄改去讀別的 repository 的歷史）。以 `--now` 指定檢查時點，即可對已提交的舊
evidence 重放稽核；對 `0bb6d7f` 的 v4 bytes 以 `--now 2026-07-26T21:49:00Z` 重放，會精確
重現 `Human/Ops` 當時的三筆拒絕（兩筆 `future_timestamp`、一筆 `head_binding`）並以
exit 1 結束。

### 7.4 對 v5.0.0 的更正

v5.0.0 交付於 PR `#4221` head `5a9ed0c9957529467fce0b7afa0338546987ee4b`，於
`2026-07-26T22:38Z` 被 `Human/Ops` 第三次否決。兩項缺陷與本版的修正：

1. **可變 PR 事實在宣告的 cut 當下即已過期**：v5.0.0 宣告 cut 為
   `2026-07-26T22:26:48Z`，卻把 `#4211` 記為 head `4e24e895…` / `BEHIND` / trailers
   fail——但取代它的 `c1686aaecd393e57648a06d8aa593fd71a1f9a7b` committer 時間為
   `22:24:26Z`，早於該 cut；`#4203` 亦於 `22:25:34Z`、`22:25:44Z` 連續前進，而非文中的
   `5dbc956…`。因此 v5.0.0「三個 PR 皆 BEHIND」的命令結論在其自稱時點不成立。
   **本版的修正不是重抄一次新值**——新值同樣會過期——而是 §2.1 的規則：所有可變事實
   一律降格為綁定 head 與觀測時點的時點觀測，並由 `mutable_observation_binding` 在
   `evidence.json` 上強制執行。§5.0 是本文件唯一的 PR 事實來源。
2. **required check 只覆蓋已被否決的舊 head**：v5.0.0 的
   `implementation_delivery.required_checks` 只有 `5c39428…` 與 `0bb6d7f…` 兩個**已被
   `Human/Ops` 否決**的 v4 head 的綠燈。`checks_bound_to_commits` 只問「這個 head 有沒有
   出現在 `anchor_commits`」，兩者都出現了，於是「五條規則、零拒絕」對一份**完全沒有為
   交付 bytes 跑過任何檢查**的 manifest 依然成立。本版新增 `current_delivery_checks`，
   並以 §7.5 的交付收據契約給出可被檢查的定義；迴歸測試
   `test_checks_covering_only_superseded_heads_are_rejected` 以本 manifest 為輸入，把
   `required_checks` 削到只剩 superseded head，斷言 `checks_bound_to_commits` **不會**
   拒絕而 `current_delivery_checks` **會**拒絕。

### 7.5 非循環交付收據契約（Delivery Receipt Contract）

「證明交付 bytes 通過 CI」有一個結構性障礙：檢查結果只有在 commit 之後才存在，而把結果
寫進 `evidence.json` 又會產生新的 commit。v4.0.0 就是在這個迴圈裡失敗的。本契約用
**收據 head 與 PR head 分離**來終止遞迴：

1. **收據 commit（receipt head）**只包含被綁定的三個 artifact：本文件、
   `scripts/validate_twelve_loop_gap_evidence.py`、
   `scripts/test_validate_twelve_loop_gap_evidence.py`。它先推送，先跑完三項必要檢查。
2. **evidence commit** 只改 `evidence.json` 與 `evidence.sha256`。這兩個檔案**不在**
   content digest 的綁定集合內（§7.3 第 1 點），因此 evidence commit **不會改變**
   `validated_head_sha`：收據 head 的 tree 與 PR 最終 head 的 tree 對這三個 artifact
   逐 byte 相同。
3. `evidence.json` 於 `anchor_commits` 中以 `receipt_role: current_delivery_receipt`
   標記收據 commit，並記下 `bound_content_digest`（必須等於 `validated_head_sha`）與該
   head 上三項必要檢查的 run id、conclusion、`completed_at`。
4. `evidence.json` 自身的 bytes 由 companion `evidence.sha256` 封存。
5. **（v7.0.0 新增）**收據 commit 不只被「宣告」，還要能被**離線核對**：
   `receipt_commit_artifacts` 以 `git ls-tree` / `git cat-file` 讀出該 commit 的 tree，要求每個被綁定
   artifact 都存在於該 tree 且 blob sha256 等於 manifest 記載值，並以這些 blob 重算
   content digest 與 `validated_head_sha` 比對。少了這一步，第 3 點只是 manifest 的
   自述，可以被改寫（§7.6）。

因此本 manifest 主張的是：**被綁定的那三個 artifact，其 byte 內容在收據 head 上通過了
全部三項必要檢查**。這個主張不需要知道自己所在 commit 的 sha，遞迴到此終止。

契約的兩個代價，本版明確揭露而不掩飾：

- 在收據 commit 與 evidence commit **之間**的那一個 commit 上，`evidence.json` 仍是舊
  版，`python3 -m pytest scripts/test_validate_twelve_loop_gap_evidence.py` 會失敗。
  這是契約要求的順序造成的，且該中間狀態不是 PR 的最終 head；分支 CI 的 smoke gate
  （`scripts/run-acceptance.sh smoke`）不執行本測試，所以中間 commit 的三項必要檢查
  仍為綠。審查請以 PR 最終 head 為準。
- evidence commit 自身的三項檢查結果**不在** manifest 內（寫進去就再次遞迴）。它們由
  owner 在 handoff 訊息與 PR 留言中以 run id 揭露，由 reviewer 於 `#4221` 上直接核對。

### 7.6 對 v6.0.0 的更正：收據自述 vs. 收據本身

v6.0.0 交付於 PR `#4221` head `b3f8edad0b5ac078ada3dd791b8166dbaf58cf9e`，由 reviewer
`Codex2` 否決。這次的缺陷不在任何單一欄位，而在**七條規則的取材面**：它們讀的全是
manifest 對收據的自述，沒有一條去讀收據 commit 本身。因此以下這組編輯可以在**不觸碰任何
規則**的情況下把收據換成一個不曾承載交付 bytes 的舊 commit：

1. 把 `receipt_role: current_delivery_receipt` 從真正的收據 head 搬到已被 `Human/Ops`
   否決的 v4 commit `5c39428dda1d3c1e42fa926aa5f320467e1b8324`；
2. 把當前的 `bound_content_digest` 原值複製到該 anchor 上（於是
   `current_delivery_checks` 的等值檢查通過）；
3. 把它的 `delivery_state` 從 `superseded_v4_delivery_commit_rejected_by_human_ops`
   改寫成看起來 live 的字串（於是 superseded 標記檢查通過）；
4. 重封 `evidence.sha256`（於是 `companion_checksum` 通過）。

`5c39428` 上三項必要檢查本來就是全綠（run 30221671216），所以
`current_delivery_checks` 的綠燈完整性檢查也通過。**七條規則、零拒絕。**

而 git object 對同一個 commit 的說法完全相反，且不可竄改：

| 被綁定 artifact | `5c39428` 的 blob sha256 | manifest 綁定值 |
| :--- | :--- | :--- |
| `POST_DISPATCH_RUNTIME_GAP_DELTA.md` | `9ac925e0c66a3c48a916b7b3c97fbac580034ee96f7e3f53e1e0a42c4093a5d3` | v6.0.0 為 `4f2f7735513f52fabc6a5679202be6f1c1dd521b6ad639b99dfc618a3b7944bc` |
| `scripts/validate_twelve_loop_gap_evidence.py` | 該 commit 的 tree 中**不存在** | 有綁定值 |
| `scripts/test_validate_twelve_loop_gap_evidence.py` | 該 commit 的 tree 中**不存在** | 有綁定值 |

v7.0.0 的修正是新增第八條規則 `receipt_commit_artifacts`（§7.3 表格最後一列），
把收據從「manifest 說的」變成「object store 能證明的」：

- 以 `git --no-optional-locks -C <git-root> ls-tree` 與 `cat-file` 離線讀取，不觸網、
  不需憑證；
- 收據 sha 必須是 40-hex 小寫且解析為 commit object，否則 fail closed；
- `source_artifact_sha256_by_epoch` 中每一個路徑都必須存在於收據 commit 的 tree，
  缺任一路徑即 fail closed（這是上表第 2、3 列的形狀）。「路徑不存在」以
  `ls-tree -r --name-only -z` 的成員關係判定，而非以 `cat-file` 的錯誤訊息字串推測——
  後者會隨 git 版本改變措辭，把「無法核對」誤判成「路徑不存在」；
- 每個 blob 的 sha256 必須等於 manifest 記載值，不符即 fail closed（第 1 列的形狀）；
- 以收據 commit 的 blob 重算 content digest，必須等於 `validated_head_sha`；
- git 不可用、`--git-root` 不是 git repository、`ls-tree` 失敗、或已知存在的路徑
  `cat-file` 仍失敗時，一律 fail closed——「無法核對」不等於「通過」。

迴歸測試以**這次否決的精確形狀**覆蓋，而非近似形狀：

- `test_v4_receipt_substitution_with_resealed_checksum_is_rejected` 以本 manifest 為輸入，
  執行上述四步編輯（含重封 checksum），斷言拒絕的規則**恰為** `receipt_commit_artifacts`
  與 `current_cut_consistency` 兩條——亦即 `receipt_commit_artifacts` 之前既有的七條規則
  確實全部通過——且 `receipt_commit_artifacts` 的訊息同時含有 `9ac925e0…` 的摘要衝突與
  兩支 validator script 的 `does not contain bound artifact` 缺失。第九條規則從相反方向
  攔住同一個替換：第 3 步把真正的收據改寫成 superseded，而 manifest 的敘述仍指名它
  （§7.7 第 4 點），於是敘述與被宣告的 cut 立刻不一致。這是**兩條規則、兩個取材面**，
  不是同一條規則報兩次。
- `test_receipt_verification_reads_blobs_not_the_working_tree` 更進一步：把工作樹的本文件
  換成 `5c39428` 的精確 v4 bytes 並完整 rebind，使 `head_binding` 與
  `companion_checksum` 都通過，仍然被 `receipt_commit_artifacts` 拒絕——因為該 commit
  根本沒有兩支 validator script，任何 rebind 都追不上。
- `test_receipt_naming_an_unknown_commit_is_rejected`、
  `test_receipt_with_an_abbreviated_sha_is_rejected`、
  `test_cli_without_git_root_falls_back_to_repo_root_and_fails_closed` 覆蓋 commit 未知、
  縮寫 sha、以及 object store 不可用時的 fail-closed 行為。

### 7.7 對 v7.0.0 的更正：可被驗證的收據 vs. 未被重切的敘述

v7.0.0 (historical) 交付於 PR `#4221` 最終 head
`04332822e44922d64a4a403cfe6223f311e9954b`，由 reviewer `Codex2` 否決。與前四次不同，
**這次的收據證明本身成立**：三個被綁定 blob 在收據 commit
`63d4d60372741af77943f24c2833db0c15e2e051` 上與綁定值相符、最終 head 未更動任一 blob、
該 head 的三項必要檢查全綠、schema / checksum / 八條規則全部零拒絕、迴歸與 dispatcher
共 60 個測試通過、baseline 與 25-task catalog 的 diff 為空。

被否決的是 `evidence.json` 的**敘述面**：它沒有跟著 v7 的 cut 一起重切，於是同一份
manifest 內部自相矛盾。`Codex2` 逐項列出的缺陷：

| 欄位 | v7.0.0 (historical) 寫的 | 同版其他欄位／文件記載的 |
| :--- | :--- | :--- |
| `authorities.actual_state[0]` | 查證截止於 seq 2046 (historical) | 該 cut 的查證邊界為 seq 2142 (historical) |
| `deployment.identity_admission`、`security_and_safety.two_person_approval` | canonical row 取自 seq 2014 (historical) | 同上 |
| `behavioral_proof.duplicate_safety` | 文件版本為 6.0.0 (historical) | 文件版本為 7.0.0 (historical) |
| `behavioral_proof.failure_and_degraded_behavior` | 「七條 fail-closed 規則」 | 驗證器已有八條 |
| `acceptance` AC4 | `#4203` open、Commit trailers 失敗、`BEHIND` | §5.0 記該 PR 於 `945f47dce…` 為 open / `CLEAN`、三項全綠 |
| `acceptance` AC5 / AC9 | owner / reviewer / status 取自 seq 2014 (historical) | 查證邊界為 seq 2142 (historical) |
| `acceptance` AC7 | 「七條 fail-closed 拒絕規則」 | 驗證器已有八條 |
| `residual_risks.independent_review` | 「v6.0.0 (historical) 尚未被獨立審查」 | 送審的是 v7.0.0 (historical) |
| `residual_risks.canonical_snapshot_age` | seq 2046 (historical) 為本次 cut 的 tip | 查證邊界為 seq 2142 (historical) |
| `residual_risks.delivery_receipt_intermediate_state` | 中間態收據為 `20ba1af…` | 本次收據為 `63d4d603…` |
| `validation.commands[2, 6-12]` | 以「當前 pass」語氣記載 v6 (historical) 時期的命令結論 | v7 的命令另行附加在其後 |

這一類缺陷有一個共同形狀：**它們全部是關於「當前 cut」的主張，而前八條規則一條都不讀
敘述文字**。schema 只檢查鍵的存在與型別，checksum 只檢查 bytes 是否被動過，八條規則只
檢查結構化欄位之間的關係——三者對「散文說的是上一版」完全無感。

v8.0.0 (historical) 新增第九條規則 `current_cut_consistency`，其設計取向是**只宣告一次、
再逐句核對**（本版沿用，未改動）：

1. **不新增宣告區塊**。`schemas/product-evidence.schema.json` 的
   `additionalProperties` 為 `false`，而本 task 明訂不修改該 schema（§1、
   `scope.not_changing`）；更重要的是，多一個可自由填寫的宣告欄位，就是多一個可以填錯
   的地方。因此本 cut 的身分一律**從 manifest 既有結構導出**：版本取自
   `task.evidence_cut_semantics` 的開頭句 `Owner evidence cut vX.Y.Z.`；canonical 快照
   序號取自 `authorities.actual_state[0]`（必須剛好一個未標記序號）；交付收據取自帶有
   `receipt_role: current_delivery_receipt` 的那一個 anchor（必須剛好一個）；規則數取自
   驗證器自身的 `RULES`。四者只要無法唯一導出即 fail closed。
2. 規則數不由 manifest 宣告，因此**不存在「宣告錯數字」這個選項**：敘述中的規則數直接
   與驗證器實際規則數對帳，規則增減而敘述未更新即 fail closed。同理，
   `(historical)` 標記的字面值寫死在驗證器內，被列管的文件無法自行放寬自己的例外機制。
3. 被列管的敘述欄位清單**寫在驗證器裡，不由 manifest 指定**，且任一路徑無法解析即為
   拒絕——刪掉欄位不是規避規則的辦法。清單涵蓋 `schema_status.formalization_trigger`、
   `evidence_policy.mutation_rule`、`task.evidence_cut_semantics`、
   `authorities.actual_state[0]`、`behavioral_proof` 的 `duplicate_safety` 與
   `failure_and_degraded_behavior`、`deployment.identity_admission.proof`、
   `security_and_safety.two_person_approval.proof`、全部 `acceptance[].statement`、
   `residual_risks` 的 `independent_review` / `canonical_snapshot_age` /
   `delivery_receipt_intermediate_state`、`integrity.self_hash_reason`，以及每一個宣告
   `claim_scope: current` 的 `validation.commands[]` 的 `command` / `conclusion` /
   `note`。
4. 在這些文字裡，下列字樣一律與導出的宣告對帳：版本字樣（`v9.0.0` 與 `v9` 兩種
   寫法）、journal 序號（`sequence NNNN` / `seq NNNN`）、規則數（`ten rules` 一類）、
   已被 `delivery_state` 標為 superseded 的 commit sha（含縮寫前綴）。`loop_catalog.v2`
   這類以點號起首的 schema 名稱不視為版本字樣，四段式數字（如 IP）不視為三段版本號。
5. **PR 主張必須綁 head**：本 cut 觀測過的 PR（由 `claim_scope: current` 命令項的
   `observations[].pull_request` 導出，不由作者自行列舉），只要在列管敘述中被提及，
   同一段文字就必須引用該次觀測的 head，否則拒絕。這正是 AC4 的形狀——它談 `#4203` 的
   狀態卻沒有任何 head 可供對照，於是與觀測表脫節而無人察覺。
6. **確實屬於舊版的字樣不是不能寫，而是必須標記**：在該字樣後緊接字面
   `(historical)` 即為合法。這使 §7.1–§7.7 這類更正段落仍然可寫，同時讓「歷史觀測」
   與「當前主張」在機器與人眼中都可區分——這也是本次否決要求的「clearly mark
   historical observations as historical」。
7. `validation.commands[]` 每一項都必須宣告 `claim_scope` 為 `current` 或
   `historical`，且 `historical` 者必須附 `historical_note` 說明它屬於哪一版。未宣告
   即 fail closed，因此「舊版命令結論混在新版命令旁邊、語氣都是當前 pass」這個
   v7.0.0 (historical) 的具體形狀不可能再通過。

迴歸測試以**這次否決的精確形狀**覆蓋，而非近似形狀：

- `test_stale_version_token_in_a_current_claim_is_rejected` 與
  `test_short_form_stale_cut_name_is_rejected` 重現 `behavioral_proof` 與
  `residual_risks.independent_review` 停在上一版的形狀；
- `test_stale_journal_sequence_in_a_current_claim_is_rejected` 把
  `authorities.actual_state[0]` 改回 seq 2046 (historical) 的寫法，
  `test_dropping_the_journal_sequence_is_rejected` 則證明「把序號刪掉」同樣不通過；
- `test_superseded_receipt_sha_in_a_current_claim_is_rejected` 重現
  `delivery_receipt_intermediate_state` 指向舊收據的形狀；
- `test_unbound_pull_request_state_claim_is_rejected` 以 v7.0.0 (historical) 的 AC4
  原文為輸入，`test_pull_request_claim_bound_to_the_observed_head_is_accepted` 證明
  綁定 head 之後即合法（非空泛拒絕）；
- `test_stale_rule_count_in_a_current_claim_is_rejected` 覆蓋敘述側的規則數漂移
  （宣告側不存在，見上文第 2 點）；
- `test_command_entry_without_a_claim_scope_is_rejected`、
  `test_historical_command_entry_must_say_which_cut_it_belongs_to`、
  `test_stale_claim_hidden_in_a_current_command_entry_is_rejected` 覆蓋
  `validation.commands` 這一面；
- `test_removing_a_current_claim_field_is_rejected`、
  `test_missing_version_declaration_is_rejected`、
  `test_ambiguous_snapshot_declaration_is_rejected`、
  `test_two_delivery_receipts_leave_the_cut_undeclared` 覆蓋四種規避路徑：刪欄位、
  拿掉版本宣告、讓快照序號變得不唯一、讓收據變得不唯一；
- `test_historical_marker_keeps_a_superseded_version_legal` 與
  `test_schema_version_names_are_not_read_as_cut_names` 是**非誤報**測試：標記後的舊版
  字樣、以及 `loop_catalog.v2` 這類 schema 名稱都不得被誤判。

**這條規則的邊界**：它比對的是 manifest 導出的 cut 身分與 manifest 自己的敘述，屬於
內部一致性，不是外部真值。`authorities.actual_state[0]` 寫的是哪個序號，規則就以哪個為
準——它能保證「所有敘述指向同一個序號」，不能保證「那個序號是最新的」。序號的新鮮度仍由
§2 的重讀命令與 reviewer 判斷（§8「Snapshot Freshness」）。同理，它讀的是散文而非
外部系統：它能證明敘述與本 cut 一致，不能證明敘述描述的事件真的發生過——那由前八條規則
與 §5.0 的時點觀測承擔。

### 7.8 對 v8.0.0 的更正：被綁定卻沒有被讀過的文件

v8.0.0 (historical) 交付於 PR `#4221` 最終 head
`a5de47447b607a2f561b852fc40bf33035ffcba0`，由 reviewer `Codex2` 否決。這一次連
**敘述面**都是成立的——但只在 `evidence.json` 之內成立：schema 通過、`sha256sum -c`
通過、九條 (historical) 規則零拒絕、77 個迴歸與 dispatcher 測試通過、
`--validate-only` 回報 valid/25、收據 commit 的三個 blob 逐一相符、baseline 與 25-task
catalog 的 diff 為空。

被否決的是**本文件**，也就是 `validation.validated_head_sha` 這個 content digest 綁定的
三個 artifact 之一。`Codex2` 列出的兩項：

| 位置 | v8.0.0 (historical) 寫的 | 同一份交付其他地方記載的 |
| :--- | :--- | :--- |
| §1 | 本 cut 的身分「集中宣告於 `evidence.json` 的 `current_cut` (historical)」 | manifest 沒有 `current_cut` 這個鍵，schema 的 `additionalProperties` 為 `false` 也不允許它存在；同一份文件的 §7.7 第 1 點正確地寫著身分**從既有結構導出** |
| §3.4 | 「在 seq 1..2014 (historical) 的全量掃描中」「本版把掃描邊界推進到 2014 (historical) 後計數不變」 | 本 cut 的邊界為 seq 2191（§2、§3.6），`validation.commands` 的掃描命令也載明 `bounded at journal sequence 2191` |

這兩項的形狀與 §7.7 是同一種——**關於當前 cut 的主張沒有跟著重切**——只是換了一個
artifact。第一項尤其說明問題不在筆誤：它指向一個**從未存在的欄位**，讀者若照著去找會
一無所獲，而文件自己在 60 行之外把正確的機制寫對了。九條規則沒有一條能發現，因為
**九條規則的輸入全部是 `evidence.json`**。

本版新增第十條規則 `bound_document_consistency`，其設計取向是**讀被綁的那份文件，並且
只問 manifest 答得出來的問題**：

1. **讀哪一份不由作者指定**。文件路徑從 `integrity.source_artifact_sha256_by_epoch`
   導出：被綁定 artifact 中副檔名為 `.md` 的那一份，必須剛好一份，否則 fail closed。
   換言之，被檢查的一定是**被 digest 綁住、且會進收據 commit** 的那份文件，不是另一份
   未被綁定的副本。
2. **只列管兩類主張**，因為只有這兩類能由 manifest 判定真偽：
   - 「宣告於 `evidence.json` 的 `<欄位>`」這種**指路**主張——該欄位必須能在 manifest 中
     解析（用的是 §7.7 同一支 `resolve_claim_path`）。指向不存在的鍵即為拒絕；本節上表
     那種**逐字引用舊版錯誤**的寫法，與 §7.7 一樣以緊接的 `(historical)` 標記保持合法。
   - **掃描／查證邊界**主張——句中未標記的序號必須等於 `authorities.actual_state[0]`
     導出的那一個序號，也就是 §7.7 用的同一個宣告。兩條規則因此不可能各自對到不同的 cut。
3. **只有帶邊界字樣的句子會被檢查**。§3 各節逐筆列出的 715、1593、1606…… 是
   **事件**序號而非邊界，不受列管；把它們一律標記 `(historical)` 既不正確也無意義。判定字樣
   （`查證截止` / `查證邊界` / `掃描邊界` / `全量掃描` / `推進到` / `推進至` 及
   `scan boundary` / `bounded at` / `scanned through` / `advanced to`）寫死在驗證器內。
4. **刪號碼不是解法**：全文必須至少有一句以宣告的序號載明本 cut 的邊界，否則拒絕。
   §7.7 的教訓是「刪欄位不能規避規則」，這裡是它在文件面的對應。
5. **句子的邊界依文件結構切分**：表格列與標題各自成句，其餘連續非空行視為一個被硬換行
   斷開的段落並還原。文件以中文硬換行，`掃描` 與 `邊界` 常被拆到兩行；若照一般作法用
   空白 join，被檢查的字樣本身就會被拆散。同理，序號比對在文件面另用一組樣式：CJK 在
   Unicode 屬於 word character，`seq NNNN時` 這種換行還原結果在 `\b` 下不成立，
   manifest 面的英文樣式會漏掉它。
6. **`(historical)` 標記沿用同一個字面值**，因此 §7.1–§7.8 這類逐版更正段落照樣可寫。

迴歸測試以**這次否決的精確形狀**覆蓋：

- `test_nonexistent_manifest_field_named_as_the_cut_declaration_is_rejected` 以 §1 的
  `current_cut` 原文為輸入；`test_resolvable_manifest_field_reference_is_accepted` 證明
  指向真實欄位（`integrity.source_artifact_sha256_by_epoch`）不會被誤判；
- `test_stale_scan_boundary_in_the_bound_document_is_rejected` 重現 §3.4 的
  `seq 1..2014 (historical)` 與 `推進到 2014 (historical)`；
  `test_historically_marked_scan_boundary_is_accepted` 證明標記後即合法；
- `test_journal_event_sequences_are_not_read_as_scan_boundaries` 是非誤報測試：§3.1–§3.4
  的事件序號不得被當成邊界；
- `test_dropping_every_scan_boundary_is_rejected` 覆蓋「刪號碼」這條規避路徑；
- `test_bound_document_missing_from_the_tree_is_rejected` 與
  `test_two_bound_documents_leave_the_document_undefined` 覆蓋兩種 fail-closed 前提；
- `test_wrapped_boundary_phrase_is_still_matched` 與
  `test_cjk_terminated_sequence_token_is_still_matched` 鎖住第 5 點的兩個切分細節，
  避免日後有人把它們「簡化」回會漏判的寫法。

**本版為何不推進 canonical 快照序號**：v9.0.0 是對 v8.0.0 (historical) 的**純敘述更正**，
交付的事實集合與 v8.0.0 (historical) 相同。若同時把邊界推到新的序號，reviewer 就必須在
「敘述是否已重切」與「事實是否已改變」兩件事混在一起的 diff 上判斷，而這正是前幾版反覆
出錯的地方。因此本版把 canonical 快照固定在 seq 2191，讓 v8 → v9 的 diff 只包含被否決的
兩處敘述、新規則與其迴歸測試。這是**刻意**的選擇，不是遺漏；seq 2191 的時點語意與
其後 journal 仍在前進的事實，依 §2.1 與 §8「Snapshot Freshness」照舊成立。

**這條規則的邊界**：與 §7.7 相同，它比對的是文件敘述與 manifest 導出的宣告，屬於**跨
artifact 的內部一致性**，不是外部真值。它能保證「文件與 manifest 指向同一個 cut、且不會
叫人去找不存在的欄位」，不能保證那個 cut 是最新的，也不能檢查文件裡未被列管的其餘散文
——那仍由 reviewer 承擔。把它讀成「文件已被全面驗證」會是新的過度宣稱。

---

## 8. Operational Boundaries

> [!CAUTION]
> **No Premature Verification Claim Policy**
> - **Hosted**：本文件不宣稱 Pantheon 十二循環已達 hosted / production 啟用。§4 記錄的
>   `accepted` 狀態屬於 PPL-ALLOC-009 的 FE/BFF pair，不是十二循環的 hosted proof。
> - **Twelve-Loop Completion**：在 `L12-HOSTED-001` 與 `L12-CLOSE-001` 通過並取得
>   Human/Ops 受保護裁決前，不得宣稱 12-loop remediation 已完成。
> - **Merged ≠ Installed**：PR 合併進 `dev` 不等於已安裝到執行中的 command runtime
>   （§5 Gap 10 為具體反例）。
> - **Task Completion**：本文件本身不構成 `OPS-L12-RUNTIME-GAP-DELTA-001` 的完成宣告；
>   該 task 需經 reviewer `Codex2` 獨立審查後由 owner 收尾。
> - **Auto-Merge**：PR `#4221` 的 auto-merge 由 `Human/Ops` 關閉，本版**不重新啟用**。
>   合併時機由 reviewer `Codex2` 的獨立審查決定，不由 owner 端的自動化決定。
> - **Snapshot Freshness**：§3.6 的 canonical 計數是 seq 2191 的時點快照。journal 持續
>   append，審查時的最新 sequence 只會更大；本文件的主張綁定在載明的 sequence 與時間戳
>   上，不宣稱「此後未再變動」。
> - **Mutable Surface**：PR head、`mergeStateStatus` 與 check 顏色同屬會前進的表面。
>   本文件的所有 PR 事實集中在 §5.0 並各自綁定 head 與觀測時點，只主張「該 head 在該
>   時點為真」。審查者讀到不同 head 屬正常前進，不構成本文件的事實錯誤；反之，任何
>   未綁定 head 與時點的可變事實都應被視為缺陷（`mutable_observation_binding`）。
> - **Green CI ≠ Gap Closed**：`#4203` 與 `#4211` 在 §5.0 的觀測時點為 `CLEAN` 且三項
>   檢查全綠。這只表示分支 CI 通過，Gap 6 與 Gap 8 的「仍缺證據」一項未減。
> - **Self-Declared Receipt ≠ Verified Receipt**：`evidence.json` 對交付收據的描述是
>   manifest 的自述，可被改寫；只有 `receipt_commit_artifacts` 以 git object 核對過的
>   部分才是被證明的（§7.6）。同理，本文件對十條規則的敘述不取代規則本身——請以
>   `python3 scripts/validate_twelve_loop_gap_evidence.py … --json` 的實際輸出為準。
> - **Internal Consistency ≠ External Truth**：`current_cut_consistency` 保證 manifest
>   的敘述全部指向它自己宣告的那一個 cut（版本、收據、序號、規則數、PR head），
>   `bound_document_consistency` 把同一個宣告延伸到被 digest 綁定的本文件（指路欄位、
>   掃描邊界），兩者**都不**保證那個宣告是最新的外部事實。快照新鮮度仍由 §2 的重讀命令
>   與 reviewer 判斷（§7.7、§7.8 末段）。
> - **Bound Document ≠ Fully Checked Document**：`bound_document_consistency` 只列管本
>   文件的兩類主張（指向 manifest 欄位、掃描邊界）。本文件其餘散文——gap 敘述、根因、
>   §7 各節的歷史更正——沒有任何規則在讀，仍完全依賴 reviewer（§7.8 末段）。
> - **Validator Scope**：這十條規則只覆蓋**本 task 的 evidence manifest 與它綁定的
>   delta 文件**，不是全站 evidence gate；分支 CI 不執行它（§7.5），因此它是審查工具，
>   不是自動化防護。把它誤讀為「所有 evidence 都已被此規則保護」會是新的過度宣稱。

---

## 9. Conclusion

派工後出現的 runtime 缺口可歸為三類：

1. **runtime manifest 缺口**（Gap 1, 2, 3, 5, 9, 12）——必要的 scheduler / projector /
   executor 不在預設啟動路徑，或在治理修訂後未重新排程、未收斂；收斂於
   `L12-MANIFEST-001` 與 `L12-TRUTH-001`。
2. **產品流程證據缺口**（Gap 4, 6, 7, 8）——實作 task 已交付或在飛，但四個
   `L12-VERIFY-*` 尚未取得端對端證據；CAP、EVO、BFF、DIST 四份獨立稽核都在 focused
   測試全綠的情況下重現了阻斷缺陷，顯示 local test 綠燈不可作為產品流程證據。
3. **狀態層與 hosted 缺口**（Gap 10, 11）——非終態清空防護已合併但未安裝；十二循環
   hosted proof 仍為 0 / 12。

三類缺口的最終對帳點為 `L12-CLOSE-001`，其直接依賴為 `L12-HOSTED-001`、
`L12-TRUTH-001`、`L12-SIGNOFF-001`，並需 Human/Ops 受保護裁決。本文件為該對帳提供
可重新驗證的 delta 基準，等待 reviewer `Codex2` 獨立審查。
