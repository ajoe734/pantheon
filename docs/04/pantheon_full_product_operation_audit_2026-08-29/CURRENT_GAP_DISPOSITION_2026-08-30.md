# Pantheon 全系統盤點結果與 GAP 處置 — 2026-08-30

## 0. 結論

原稽核的主結論成立：Pantheon 不是空殼，但「有大量實作」不等於「全系統正常」。重新對照 `pantheon@1095c55b...`、`execute-plans@bd03c863...`、accepted hosted pair 與目前 canonical task state 後：

- 20 個 canonical GAP 中，OP-G03 已由 accepted pair 在規劃基線關閉；其餘 19 個仍 active。
- F21/F24 分別併入 OP-G08/OP-G10；F22/F23/F25 保留為 unresolved excluded findings。
- 規劃另發現一個派工基礎設施 blocker：development bridge 尚未端到端保留 `target_repo`。它不是新的產品 GAP，但不先修就不能相信跨 repo 任務 readback。
- BFF shared ports 同時存在 `domain_ports/` implementation 與 `ports/` forwarding/composition 路徑。這是 ACG-RS residual cleanup，不先決定 canonical namespace就會在 route extraction 時長出第三套 seam。
- 30 個 execution/support tasks 由 bounded context、hot file、repo 與不可逆退役邊界導出，不是配額；OP-G14 沿用既有 canonical task，不重複 materialize。
- Source 沒有新的 source-code GAP 證據，不建立 alias-cleanup 任務。

## 1. 原稽核內容哪些正確

| 原敘述 | 判定 | 必要限縮 |
|---|---|---|
| 48 個後端 service 目錄；兩個 `NotImplementedError` 是抽象 Port | 正確（稽核基線） | source inventory 不等於 production operation；抽象方法本身不是未完成功能。 |
| BFF 在 `services/control-plane/bff`，有大量 routes/tests | 正確 | 本基線 AST 是 441 個 HTTP/websocket decorators、441 個唯一 method+normalized-path、421 handlers，另有 12 個 framework decorators；「440」是舊快照/口徑差。 |
| Agora write matrix、Postgres persistence、33/33 測試 | 正確（該批次） | 尚不能證明 suggestion 有自然 production caller、hosted receipt 與 same-ID durable readback。 |
| Management 在 execute-plans，不是本 repo legacy app；0 mock import | 方向正確 | import grep 不能證明 production bundle 不可達 seed/overlay/fallback，需 bundle graph gate。 |
| 核心系統是真的做出來 | 正確 | 只能證明「非空殼」；唯一 authority、failure semantics、hosted effect、安全、delivery gate、retirement 仍需證明。 |
| 646 pass / 20 fail / 16 skip | 正確但只是一個時間點 | timeout、缺 DB/網路的 suite 不算 pass；數字不能當目前 HEAD 的永久結論。 |
| BFF 多副本 `from main import X` 是 8 個 failover 類測試根因 | 正確 | 根因是 domain router 反向依賴 composition root，不應只修單一 import。 |
| EP5 gate 卡住 kill/rollback fixture | 正確且未解 | 不能以 bypass 讓測試變綠。F22 保留，另由安全治理 scope 處理。 |
| Evolution tenant-prefix assertions 落後 | 正確、偏測試債 | 先確認 canonical contract，再更新測試；不得為舊 literal 逆改產品。 |
| Promote-to-master 六項 workflow 0-job | 正確（所查期間） | 代表 merge-enforcement 證據缺失。產品 deploy fail-closed 與組織 branch security 是不同責任面。 |
| 看板與 git ancestor 不一致 | 原稽核時點正確 | 不能用舊 `ai-status.json` 當今日真相，也不能另建產品 TaskStore；本規劃只修新發現的跨 repo immutable routing blocker。 |
| `.audit-report-tmp.html` 是未追蹤暫存檔 | 不屬產品 GAP | workspace hygiene 不生成 SA/SD implementation task。 |

## 2. Canonical GAP 唯一處置

| GAP | 狀態與根因處置 | Primary owner |
|---|---|---|
| OP-G01 | active；只有 admitted adapter receipt 可導出 Agora `real`。 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G02 | active；Suggestion 接既有 durable event/outbox 並 same-ID readback，否則刪除功能宣稱。 | 同上 |
| OP-G03 | **closed**；accepted manifest 綁 FE `bd03c863...` + BFF `e7f010dc...`，pre/post-switch passed。 | 無 |
| OP-G04 | active；required acceptance 的 fail/skip/missing 一律 non-zero。 | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| OP-G05 | active；auth path 改為 local JWT/tenant/RBAC，provider readiness 只做背景診斷。 | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G06 | active；無 domain owner 的 generic CRUD 禁用/刪除，只留 typed command。 | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` |
| OP-G07 | active；production-reachable mock/seed/overlay/fallback 刪除並加 bundle depgraph gate。 | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| OP-G08 | active；composition root 無邊界造成 inline routes、循環 import、多副本與 operation-ID 問題。 | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G09 | active；Agora 私有 cross-router import 與第二 Workshop store 改 typed port 並刪除 duplicate store。 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G10 | active；第二中央命令面依 caller→main→delete 順序退役。 | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G11 | active；12 loops 必須 natural stimulus→receipt→terminal→same-ID readback。 | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| OP-G12 | active（hosted proof only）；只證明 bounded Source effect，不重寫 Source code。 | 同上 |
| OP-G13 | active；同步 TestClient/AnyIO portal 改 async ASGI + deadline。 | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G14 | active；exact-pair authenticated desktop DOM/network/readback evidence；沿用既有 blocked row，不複製 scope。 | `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`（既有 execute-plans artifacts） |
| OP-G15 | active；UI 只依 backend contract 顯示 Real/Simulation/Unavailable。 | `OPGAP-FE-AGORA-WORKSHOP-20260830` |
| OP-G16 | active；lease bounded retry/grace + sealed local rollback baseline。 | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| OP-G17 | active；Registry 產 immutable RuntimeBinding projection，Runtime 驗 digest。 | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| OP-G18 | active；Incident bounded context 透過 `services/postmortems` 使用 canonical `postmortem_id`。 | `OPGAP-BE-INCIDENT-POSTMORTEM-20260830` |
| OP-G19 | active；promotion 綁 exact `connectorId + ingestRunId + sourceId`。 | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G20 | active；同一 correlation ID 證明 `signal→order→fill→position→heartbeat`。 | 同上 |

## 3. 後續 finding 歸併

| Finding | 處置 |
|---|---|
| F21 multi-replica import/operation ID | 併入 OP-G08；以 router ownership + pure composition root 解決。 |
| F22 EP5 governed activation proof | unresolved、排除本 functional-first catalog；不加入 MFA/security acceptance 或 bypass。 |
| F23 tenant-prefix assertion drift | unresolved test debt；需另行凍結 canonical tenant contract。 |
| F24 central internal command plane | 併入 OP-G10；完整 caller cutover/retirement。 |
| F25 zero-job merge workflows | unresolved governance risk；OP-G04/16 只處理產品部署 gate。 |

## 4. BFF 路由與 handler 盤點

URL generation 不是 ownership boundary。Catalog 對基線 `main.py` 的 441 個 decorators 逐條保存 `method`、normalized path、source handler/line、target owner module、preparation task 與 handler implementation owner。

| Bounded-context route owner | Count |
|---|---:|
| Persona lifecycle | 76 |
| Management projections | 63 |
| Agora | 40 |
| Research/Knowledge/Artifact/Lineage | 37 |
| Operator projections | 33 |
| Governance | 28 |
| Runtime/Deployment/Binding/Telemetry | 27 |
| Incident/Alert/Postmortem | 27 |
| Tools/MCP/Skills | 25 |
| BFF v5 | 24 |
| Capital/Ranking/Rebalance | 23 |
| Core/Auth | 14 |
| Evolution/Experiment/OODA | 10 |
| Generic Events/SSE/Channels | 5 |
| Settings | 4 |
| Command adapters | 4 |
| Assistant | 1 |
| **總計** | **441** |

Handler atomicity 另獨立檢查。421 個 source handlers 各只有一個 implementation owner。唯一需要跨 route owners 的 `sem_final_generic_read_alias`（approvals/artifacts/research-analyses）由 `OPGAP-BE-SEMANTIC-ALIAS-SERVICE-20260830` 擁有一次實作，Governance/Research 只做薄 wrapper；不建立 shared router 或第二 store。Agora research alias、Persona league alias、Incident 五個 command aliases 均整組歸同一 owner。

## 5. Shared-port namespace disposition

目前可執行/import/test scope 有 167 個唯一 import files：150 個 import `ports`、22 個 import `domain_ports`，其中 5 個兩者都 import。`domain_ports/` 有六個實作檔；`ports/` 有六個同名 forwarding/fallback modules，另有 `read_surface_ports.py` composition與 `__init__.py` public API。`ports` 並非純 facade，因為部分 factories/composition logic 已經存在其中，所以不能只宣稱它「無語意」。

唯一處置是：

- 選 `services/control-plane/bff/ports` 為 public **兼 implementation** canonical namespace。
- 將六個 `domain_ports/*.py` implementations 與同名 `ports/*.py` factories合併成各一份；不保留 re-export/fallback duplicate。
- 22 個 baseline `domain_ports` import files全部遷移；其中 7 個是 ports tree自身，15 個是 tests/callers。完成後 executable/import/test scan必須為 0。
- 150 個 baseline `ports` import files保留 public symbol contract；5 個 dual-import files收斂成 ports-only。
- `read_surface_ports.py` 只允許 composition、delegation與 test factories，不得持久化或做 domain mutation決策。
- 六個 `domain_ports` files同一 delivery刪除，boundary test禁止 `domain_ports` 或第三個 shared-port namespace復活。
- 歷史 ACG-RS ownership maps與 `ports/REVIEW_EVIDENCE.md` 可留在 non-executable allowlist。

Transition owner是 `OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-20260830`，源自 catalog 明列的八個 canonical ACG-RS terminal tasks（foundation、六個 domain ownership tasks、final delete）之 current residual。未完成的 Persona task同時擁有一個 direct-import test，故 port transition必須等它 canonical completion、rebase後才遷移該 test；17 個 route preparation tasks全部依賴 canonical port task，不得各自創造新 port seam。

## 6. Central command plane 完整 reference inventory

機器掃描 expression 為 `PANTHEON_INTERNAL_API_URL|/api/internal/v1|control[_-]plane.*internal_api|internal_api_min`。目前掃到 51 個直接 reference files，另有 1 個間接 drill test：

| Disposition | 數量 | Owner |
|---|---:|---|
| active env/Compose/CLI/drill/deploy/BFF/test caller cutover | 29 direct + 1 indirect | `OPGAP-BE-COMMAND-CALLER-CUTOVER-20260830` |
| `main.py` composition cutover | 1 | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| implementation/shim/runtime mount/Stage0/test inventory delete/update | 17 | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| historical non-executable markdown | 4 | explicit allowlist；不執行、不 import、不進 CI/test/runtime |

Retirement 另外擁有兩個沒有命中字串但隨 package 一起退役的 hyphen-path `__init__.py` / `internal_api_min.py`。完整逐檔清單在 catalog。BFF `command_executor.py` 明確保留。

## 7. 不重做與必刪除

- 不建立 `legacy_api/router.py`、generic governance catch-all 或第二 router hierarchy。
- 不保留 `domain_ports` forwarding/implementation樹，也不新增 `shared_ports`、`router_ports` 等第三 namespace。
- Source 既有 controller/connectors/calendar/scheduler/storage 與 reconcile-only/manual bounded one-shot 不重寫；沒有 Source cleanup task。
- 前端只有 `bff-v1` production transport；production-reachable overlay/fallback/seed/mock 與無 owner generic CRUD 刪除。
- Central command implementation、兩套 import shims、runtime mount、Stage0 entry、legacy-only tests 在 zero-caller proof 後同批刪除。
- 歷史文件可留，但只能位於 catalog allowlist，且不得被 runtime/CI/test/deploy 消費。
- 任務狀態不透過產品 BFF 寫入，也不新增第二 TaskStore。

## 8. Terminal／既有 task reconciliation 與 materialization blocker

中央 archive 逐筆確認 28 個 ACG 與 4 個 PFG rows 都已 terminal；其中 `PFG-HOSTED-RUNTIME-CLOSEOUT-20260828` 的 terminal outcome 是 `superseded`，其餘 31 個是 `completed`。這些 terminal facts 不 reopening、不再 supersede，也不計入本 catalog 新任務；machine-readable residual owner mapping 在 catalog `prior_terminal_reconciliation`。

另外四個既有 nonterminal rows 保持原 canonical scope：

- `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` blocked，仍是 OP-G14 唯一 owner；其 paper-baseline HTTP 500 blocker 改變且 program 有 accepted pair 後才 resume。
- `AGORA-SOURCE-FRONTIER-RECOVERY-V1-20260829` blocked、`OPS-SOURCE-FRONTIER-SCOPE-RECOVERY-20260829` review_approved；兩者的 Source implementation 不複製到 catalog。
- `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` 現為 blocked；clean PR #5432 head `be3e90463...` 等 canonical branch identity reconciliation。Port consolidation 與 main assembly 都等待其 canonical completion。

目前 `BridgeTask`、dispatcher task spec/hash 與 canonical materialize readback 沒有端到端保留 `target_repo`，所以先前「20 records readback verified」的說法不正確。新基礎任務 `OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830` 必須先完成；在現行 bridge 下只 bootstrap 此 pantheon-default task，完成後才 materialize 其餘 records，逐筆 read back `targetRepo` 與唯一 `artifactRepoIds`。

同時 `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` 仍是未完成的 `main.py` 與一個 Persona port test owner（blocked；clean PR #5432，觀察 head `be3e90463...`）。BFF assembly與 port consolidation都必須等待其 canonical completion並 rebase；main route AST若漂移先做 reviewed catalog amendment。

## 9. 規劃驗證與限制

本文件包能證明的是規劃一致性：20 GAP disposition 唯一、441 route rows 唯一、421 handlers atomic、artifact 無重複、DAG closed/acyclic、52 筆 command references有 disposition、167 筆 port imports有 namespace處置、32 個 prior terminal rows與4個既有 nonterminal rows都有唯一處置、31 個 materialization records可由兩 repo resolver preflight解析。

它尚不能證明 implementation、canonical materialization、Docker/Postgres/NATS、hosted 12-loop、authenticated desktop、安全 activation 或新 candidate promotion 已完成。這些證據只能由對應 execution task 的 exact head 與 hosted evidence 關閉。
