# Pantheon GAP Remediation — System Analysis (SA)

## 1. 問題陳述

Pantheon 的主要風險不是「沒有功能」，而是演進後同一責任存在多種入口、owner 與完成證據：

- BFF `main.py` 同時承擔 app composition、441 個 inline route decorators、dependency lookup 與部分 domain logic。
- typed domain command 與 central internal command plane 並存。
- 前端 typed clients 與 overlay/mock/seed/fallback 並存。
- source test、hosted effect、deployment identity 與 canonical task state 被混成同一種「完成」。
- 跨 repo task 的 `target_repo` 在 bridge pipeline 中遺失，JSON 正確不代表 TaskStore readback 正確。
- `services/control-plane/bff/ports` 與 `domain_ports` 同時被 caller 直接依賴，六組同名 domain contract 形成雙重 namespace。

局部改 import、改 assertion 或新增 facade 只會把重複路徑延後。目標是移除重複 authority，讓每個功能、route、handler、artifact、deployment 與 task 都能回到唯一 owner。

## 2. 分析方法與證據層

本次採八層交叉驗證，而非只沿用原四層：

1. source/entrypoint/caller；
2. mutation authority 與 read lineage；
3. durable same-ID effect；
4. failure/multi-replica/retry semantics；
5. test execution result；
6. governed safety proof；
7. CI/deployment/exact hosted identity；
8. task/git/caller/retirement consistency。

證據優先順序是 live/canonical readback > exact commit/runtime manifest > executable config/tests > current source > historical prose。缺任一必要層只能標為 partial/unknown，不能往上推論「正常」。

## 3. 根因樹

| 根因 | 症狀 | 為何不能局部修 | 架構處置 |
|---|---|---|---|
| Composition root 吸收 domain 行為 | 441 inline routes、`import main`、多副本載入失敗、operation-ID collision | 修一個 import 仍保留反向依賴與 shared globals | route/handler 原子 ownership；domain routers 先完成，main 最後只組裝 |
| URL 世代被誤當 domain | `/api/v1`、`/bff` 容易形成 legacy/new 雙 router tree | 同一 capability 會被切成兩個 owner | 逐 method+normalized-path 指派到 cohesive bounded context，不建立 legacy catch-all |
| 一個 handler 橫跨 domains | approvals/artifacts/research 共用 generic handler | 複製 handler 會產生兩份邏輯；塞進 generic router 又成第二 hierarchy | 唯一 typed shared service + domain thin wrappers；implementation owner 只有一個 |
| 第二命令面未退役 | central URL、internal API、runtime mount、shims、Stage0/tests | 先刪會斷 caller，只改 env 會留 resurrection path | caller cutover → main cutover → zero proof → implementation/shim/mount/test delete |
| UI 缺 durable owner contract | generic CRUD、overlay/fallback/seed/mock 可達 production | 加 generic backend 會創造第二 truth store | typed owner actions；無 owner control disabled/removed；bundle graph 阻擋復活 |
| source-level pass 被當 hosted effect | Source/12-loop/UI 有測試但 current pair 無 evidence | 再加 unit test 不證明 deployed behavior | exact pair 上 natural stimulus、receipt、terminal、same-ID readback |
| Task routing 只存在於規劃 JSON | execute-plans artifact 有 prefix，但 bridge 丟失 `target_repo` | dry-run resolver 不能代表 canonical TaskStore | 先修 immutable target_repo pipeline，再 materialize/read back |
| Shared port 有兩個可直接 import 的 namespace | 150 files import `ports`、22 files import `domain_ports`、5 files 同時 import | 再加 facade 或第三個 compat package 只會延長雙 owner | `ports` 成為唯一 public + implementation namespace；搬六組實作、遷移 callers、刪除 `domain_ports` |
| Delivery/safety 證據分裂 | deploy skip 可放行、rollback 依遠端、EP5 fixture 被 gate 擋 | 測試 bypass 或 prose 無法提供 operation proof | deploy fail-closed；F22 另由安全治理 scope，不混入本 functional plan |

## 4. 目標權威模型

| Authority plane | 唯一責任 | 明確不做 |
|---|---|---|
| Domain authority | mutation、durable state、domain event/receipt | 不由 UI/BFF overlay 產真值 |
| Transport/router authority | 一個 bounded context 的 route contract 與 DTO mapping | 不擁有第二 store，不反向 import main |
| Composition authority | FastAPI app/FE shell 的 wiring、middleware、lifespan、router mounts | 不含 domain handler |
| Delivery authority | immutable candidate、pre-switch gate、atomic switch、rollback、hosted evidence | 不把 remote commit 當 served version |
| Development-task authority | canonical TaskStore、dependencies、owner/reviewer、repo routing | 不透過產品 BFF 寫 repo/task |
| Shared-port authority | `services/control-plane/bff/ports` 內唯一的 domain contracts、factories 與 composition ports | 不允許 `domain_ports`、第三 namespace 或 package 外直接 implementation import |
| Safety/governance authority | MFA、dual approval、kill/rollback governed proof | 不用 test bypass 取代正式證據 |

## 5. BFF bounded contexts

基線是 441 個唯一 method+normalized-path 與 421 handlers。路由分為 17 個 cohesive owners：Core、Agora、Management、BFF v5、Research、Evolution、Persona、Capital、Governance、Runtime、Incident、Tools、Events、Operator、Settings、Assistant、Command。

分界原則：

- domain identity 優先於 transport。Agora stream 歸 Agora、Incident stream 歸 Incident；Events 只擁有 generic SSE/channel substrate。
- aliases 不能拆 handler。Agora research alias 與 Persona league alias整組移動。
- `sem_final_generic_read_alias` 是唯一跨 domain 特例：shared service 擁有一次 implementation，Governance/Research 各自擁有 route wrapper。
- `/api/v1` 不形成一個 `legacy_api` owner；每條 route 回到其 domain。
- 現有 router/tree 優先使用；沒有 canonical route owner 才新增具名 domain router。
- 所有 preparation tasks 禁止修改 `main.py`；assembly 在最後一次刪 inline handlers 並 include routers。

所有 route preparation tasks 都必須等待 shared-port namespace consolidation。如此 route 搬移期間只會依賴一個 port owner，不會把 `domain_ports` 複製進新的 router tree。

## 6. Canonical shared-port namespace

caller inventory 對 `services`、`tests`、`scripts`、`.orchestrator` 做 exact import scan，共 167 個唯一 files：150 import `ports`、22 import `domain_ports`，其中 5 個同時 import；`domain_ports` 有六組實作 module，六個同名 `ports` modules 直接 forwarding/fallback，`read_surface_ports.py` 另直接做 composition。

目標不是把 `ports` 宣稱成純 facade。現況的 `ports` 已包含 semantic factories、composition 與 test factory，因此唯一可維護模型是：

1. `services/control-plane/bff/ports` 同時是 stable public API 與唯一 implementation namespace；
2. 六組 `domain_ports` implementation 搬入同名 `ports` modules，保留既有 public symbols 與 contract semantics；
3. 22 個直接 `domain_ports` callers 全部遷移，5 個 dual import files 收斂為單一 import；既有 150 個 `ports` callers 不得被迫走另一層 compatibility path；
4. `ports/read_surface_ports.py` 只保留 composition/delegation/test factory，不擁有 persistence、mutation 或第二 domain implementation；
5. zero-caller 與 import-boundary gate 通過後，同批刪除六個 `domain_ports` files；禁止新增第三個 `*_ports`/compat namespace；
6. 歷史 ACG-RS ownership maps 只保留為 non-executable evidence，不是執行期 namespace。

transition owner 是 `OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-20260830`，由八個 ACG-RS ownership/caller/delete follow-ups 合併導出。它等待目前活動的 Persona task，因該 task 正在修改一個 direct-domain caller test；rebase 後只接管 namespace migration，不重做 Persona 行為。

## 7. Command authority 與退役分析

目前 scoped scan 有 51 個直接 references + 1 個間接 test caller，分成四類：

- 29 direct + 1 indirect：env/Compose/CLI/drill/deploy/BFF/test callers，先切 typed adapters。
- 1：`main.py`，由 composition owner 移除 central capability/config。
- 17：central implementation、shims、runtime mount、Stage0、legacy tests/inventory，由 retirement task 刪除/更新。
- 4：歷史 markdown，只在 non-executable allowlist 保留。

Retirement invariant：

```text
active caller/import/config/test count == 0
AND runtime mount count == 0
AND main central capability count == 0
THEN delete implementation + both shim trees + Stage0 entry + exclusive tests
```

BFF `command_executor.py` 是保留的 canonical dispatcher，不是廢碼。禁止以同名新 facade 取代 central plane。

## 8. Frontend authority 分析

execute-plans 的 target state：

- `src/lib/bff-v1` 是唯一 production transport。
- v5 只做 DTO/view transform，不自行 network/mutate。
- write overlay/fallback、production seed/mock/stub graph 由 reachability 判斷並刪除，不用 source-name grep 假裝完成。
- generic CRUD 沒有 canonical owner 時顯示 unavailable/disabled，不補一個 generic CRUD backend。
- `App.tsx`、`ManagementLayout.tsx`、`bff-v1/index.ts` 由唯一 FE assembly task 擁有。
- OP-G14 沿用既有 `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` 的 execute-plans evidence artifacts；不新增第二個 hosted acceptance task，也不與 Pantheon artifact 混用。

## 9. Source 與「不重做」判定

目前沒有 direct evidence 支持新的 Source source-code remediation。已完成的 reconcile-only、manual bounded one-shot、controller、connectors、Taiwan calendar、scheduler、frontier、storage 保持原 owner。

OP-G12 的缺口只是 hosted effect proof：

```text
create test source
→ validate
→ bounded one-shot
→ read exact snapshot/effect
→ automatically return to reconcile-only
```

若未來出現具體 executable caller/duplicate implementation 證據，需另建新的 reviewed GAP；本計畫不預先授權 alias cleanup。

## 10. Task materialization 根因

現在的 `BridgeTask` model、dispatcher `task_spec`/hash 與 canonical materialize readback 沒有完整保存 `target_repo`。因此 catalog 中的 execute-plans prefix 只能證明 resolver 能解析，不能證明 canonical task 被派到正確 repo。

bootstrap sequence：

1. plan-freeze 合併；
2. 在現行 pantheon default 下只 materialize `OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830`；
3. bridge 讓 `target_repo` 進入 packet、signature/hash、admission、assignment、archive、TaskStore readback；
4. tamper/mismatch fail-closed，legacy omission 只使用明文規定的 pantheon default；
5. 再 materialize 其餘 records；
6. 每筆 read back exact `targetRepo` + exactly one matching `artifactRepoId` 後才 dispatch。

既有 canonical tasks 不走這次 materialization。28 個 ACG 與 4 個 PFG terminal rows 保留 terminal fact，只把 current residual 指向本 catalog owner；既有 AGC-14、兩個 Source recovery 與 Persona durable-list rows 保持原 scope/status。特別是 OP-G14 由既有 blocked AGC-14 row 關閉，catalog 不得藉 bridge 修復複製一筆替代 task。

## 11. Hot-file concurrency 分析

`services/control-plane/bff/main.py` 目前另有未完成 task `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830`；canonical row 已 blocked，clean PR #5432 head `be3e90463...` 等 branch-identity reconciliation，且同一 task 也修改一個 direct `domain_ports` caller test。BFF assembly 與 port consolidation 都必須把它列為 external dependency，等 canonical completion、rebase；前者重建 AST parity，後者只遷移 import namespace。不能用 catalog owner 覆蓋既有 owner。

其餘 hot files 同樣單一 owner：three Compose/env 在 command caller cutover；deploy script 在 deploy reliability；FE shell/barrel 在 FE assembly。Shared hosted VM 用 capacity=1 resource，而不是靠任意 dependency 製造假序列。

## 12. 架構不變量

1. 每個 mutation 一個 write authority。
2. 每個 method+normalized-path 一個 route owner。
3. 每個 source handler 一個 implementation owner。
4. 每個 catalog artifact 一個 task owner。
5. shared domain port 只允許 `services/control-plane/bff/ports`；`domain_ports` 與第三 namespace 必須為 0。
6. preparation routers 不 import/edit `main.py`。
7. 沒有 `legacy_api`、generic governance/runtime catch-all 或第二 route hierarchy。
8. 沒有 production-reachable mock/overlay/fallback。
9. 退役同批完成 caller cutover、delete 與 resurrection gate。
10. hosted success 綁 exact FE/BFF pair 與 same-ID evidence。
11. task target repo 必須 canonical readback，不接受 dry-run 代替。
12. F22/F25 未關閉前不得宣稱全系統正常。

## 13. 成功判定

本 SA/SD 合併只代表規劃凍結。Program 的 source success、hosted success 與 full-system success 是三個不同 terminal facts：

- source success：相應 task exact head 通過 contract/integration tests 並合併。
- hosted success：accepted candidate 上 exact-pair evidence 通過，skip/missing/fail 均阻擋。
- full-system success：另需 F22 安全 proof 與 F25 merge-enforcement proof；本 catalog 不虛構這兩項。
