# 正式執行任務複驗：2026-09-06

取證範圍：00:24–00:31 UTC，Pantheon `origin/dev@471dc5391a0f9cbde54d51730891583043708e42`。
獨立唯讀 checkout：`/tmp/pantheon-closure-audit-20260906.HKFCSg`。
本報告補充原始 INDEX / SA / SD，不修改其簽章來源或已封存任務。

## 01:30 執行更新（下文來源盤點仍以00:31為基準）

Overlay已於01:19:26提交checkpoint98a295700與真正Registry-owner blocker，canonical blocked，
不是已完成；其停止worktree有64files/0skipped封存可恢復。兩個必要regression artifacts已在
blocked狀態依Human/Ops正式增列，沒有放寬其他來源檔案或原驗收。
新[Registry owner V2 SA/SD](REGISTRY_STRATEGY_PREREQUISITE_SA_SD_20260906_V2.md)經查重及二次review，
01:28:53正式admitted/authoritative readback verified，01:29:31真實Claude worker已開工，Antigravityreview。
原20項現在為10done/1blocked/5todo/4未admitted；另五項correctives已admitted、此owner prerequisite已開工。

最新[Registry／Strategy來源複驗](STRATEGY_REGISTRY_OWNER_REAUDIT_20260906.md)與
[Overlay checkpoint／實際migration負例](OVERLAY_CHECKPOINT_REVIEW_20260906.md)確認：
Registry是memoryowner、Strategyadapter假readback；Overlay新harness仍是dict假restart，
backfill可在未寫入時報成功、將foreign tenantId資料加上另一tenant_id。這些均不因focusedgreen而通過。
V1因archive-only依賴拒收、V2修正且保留全要求的過程與兩次signed文件均保留，未手改canonicalJSON。

## 結論

正式 contract 修訂與 cron/runtime prerequisite 已恢復派工鏈；不是全面產品驗收。
原始 20 項 canonical 分布為 **10 done、1 in_progress、5 todo、4 尚未 admitted**。
其中新 done 的 source delivery 有下述實證缺口，不能把 10 done 解讀為 10 項完整需求均已滿足。
12 個業務循環、Management、Agora 與 hosted exact-version acceptance **仍未證實全部閉環**。

## 1. 今天重新核對的派工與交付

Supervisor PID 1801618，qualified runtime `dd3f0563a6a3f9ca2976a354de29221d91665a73`；
00:25:20 runtime healthy，TaskStore authoritative/caught_up，無 loop error。
OVERLAY-RETIRE-001 generation 4 有真實 worker PID 2801974 / child 2802134，
00:28:48 持續有事件。昨日 BFFTEST lease 卡住的觀察已過時，不再作為今日 blocker。
本次未重啟 supervisor、未新建 cron、未調整認證或 live deployment。

| 原始任務 | 最新 canonical | 本次證據／限制 |
| --- | --- | --- |
| PLAN-ADMIT-001 | done | PR #5551，規畫接收不是產品驗收 |
| STRUCT-OWNERSHIP-001 | done | PR #5557，ownership inventory |
| ENV-STAGING-PROD-PLAN-001 | done | PR #5556，環境規畫不是 hosted deployment |
| BFF-PACKAGE-001 | done | PR #5575；後續 package corrective #5604 已交付 |
| BFF-COMPOSITION-001 | done | PR #5587；保留後續 dependency/authority 驗收 |
| BFF-DEADCODE-001 | done | PR #5597，只證明已處理的 17 unreachable tails |
| BFF-TEST-ARCH-001 | done | PR #5600；完整 SD §8.5 未達成，見 §2 |
| JOURNAL-OWNER-001 | done | PR #5613；consumer/retirement 缺口，見 §4 |
| BFF-ROUTER-STRUCT-001 | done | PR #5615；父 factory 已拆，application boundary 未完整落實，見 §5 |
| DOMAIN-WRITERS-001 | done | PR #5616；持久命令与 missing-method 缺口，見 §3 |
| OVERLAY-RETIRE-001 | in_progress | PR #5618 open；範圍與驗證問題，見 §6 |
| AGORA-CHAIN-001 | todo | 依賴 Overlay；不得把上述 source done 當業務已可用 |
| LOOP-TRUTH-001 | todo | 仍待 Agora / Overlay |
| MGMT-READ-001 | todo | persona projection 雙重組裝 finding 已保留於 canonical note |
| FE-STRICTLIVE-001 | todo | 獨立 execute-plans repo，不在 Pantheon 放 FE |
| DEV-DELIVERY-001 | todo | exact artifact paired rollback finding 已保留於 canonical note |
| DEV-RELEASE-HOSTED-001 | unknown/not admitted | hosted 類正式授權 prerequisite 尚未完成 |
| L12-HOSTED-001 | unknown/not admitted | 同上，不以 local tests 代替 |
| MGMT-AGORA-E2E-001 | unknown/not admitted | 同上，不以 supervisor health 代替 |
| STRUCT-RETIRE-001 | unknown/not admitted | retirement 與 hosted 相依仍未落地 |

新封存來源精確綁定：

| 任務 | approved head | merge |
| --- | --- | --- |
| BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001 / PR #5612 | dcff559aedc07bf899098beca060bc8ed7a9495e | a86c8ff9619c3504451e4dc98cbfa6bd3ae0b689 |
| BFF-TEST-ARCH-001 / PR #5600 | 1392c4f299207617944b0728bd5fc0b36b97b654 | 1db964e248e850c735ad6f7cc5d99e76284b66cc |
| JOURNAL-OWNER-001 / PR #5613 | 04a71328146f647e11007cd5f7d6a99d8d06313c | dbca7faea54b6612332a52688529b13e4b0117a9 |
| BFF-ROUTER-STRUCT-001 / PR #5615 | 6c574b2c780e8d0224e7227d21bc64c6a788bbb0 | 8e1e4cfb0fd769675ebbc0e29331f88e768f5c3d |
| DOMAIN-WRITERS-001 / PR #5616 | e0afd7197f644c4418f7c94fdbe3623d8389c828 | 471dc5391a0f9cbde54d51730891583043708e42 |

PR #5612 required canonical status / branch checks success；早先 non-required signed-review
audit 曾 failure，不能描述為「所有 checks 全綠」。Canonical 的獨立審查、merge 與 archive
均真實存在，但不能取代逐條需求複驗。

## 2. 測試架構：完整遷移被縮成五項示範與計畫清單

原 SD §8.5 要求 categories 2–4 脫離 main / sys.path mutation / global monkeypatch，
per-domain typed fixtures，並記錄 collection/import 與 per-file runtime。
原始 218 cases 是 216 unique files，不把兩筆重複計成缺檔。

獨立 AST 複驗 approved head 與 current dev：216 檔仍全存在，**212 檔仍 import main**，
其中 **204 在目前 composition allowlist 之外**。原始集合只有 CW01、CW02、CW04、
development_route_boundary 四檔脫離 main。結案 inventory 的 211 只涵蓋所列 341 檔，
沒有完整涵蓋原始集合與 importing helper；不能拿不同分母當進度改善。

`tests/test_bff_test_architecture.py:100–103` 只要求 migrated >= 5；`:162–168`
比較 JSON metadata <= 211，而不是當前 source import graph。這是 planned migration
trend gate，不是原 task 的完整驗收。昨日 f167 的 164 importers 也不是這個最終 head 的事實。

修復方向：沿原分類與 fixture 架構完成全部實際非 composition importers；掃描真實檔案，
不能靠改 JSON、擴大 allowlist、刪斷言或只搬五檔通過。此缺口需引用 terminal task 的正式
收尾任務；不重開 archive、不另建測試框架。

獨立複查也確認 ASK001/003/004 最終版本仍交換 main globals 並清除共用 idempotency；
nested fixture 的內層離開後，外層 replay cache 沒有恢復。昨日中途 f167 的 context fixes
沒有保留到 final head，不把它們列為今日已修。相反，cookie suite 已恢復 real main.app
production extractor coverage，舊「複製 extractor」finding 已解決，不應再次照舊開工。

## 3. Domain writers：仍有非持久 accepted 與兩套寫入選擇

已交付有效部分：governance approval authority fields 的 anti-forgery readback、
部分 Persona / Research / Runtime 的 owner 接線；不回退這些修復。

但 current `governance/service.py:266–333` 的 create_approval_decision 只寫
`_created_approvals` / `_idempotency`。00:30:36 guarded actual-source probe：
returned_status=accepted、same_instance_count=1、fresh_instance_same_provider_count=0。
沒有 domain transaction / durable command/outbox receipt，不符 SD §4.4。

`main.py:18869` 的 RecordSponsorDecision 仍呼叫 `read_store.record_sponsor_decision`；
actual ReadSurfacePorts class 沒有該方法。這一 SD §4.5 明列項目仍未接到治理 command owner。
不能再加 mutation 到 read facade 來遮掩問題。

`runtime/router.py:1048–1080` 仍以 hasattr 分支保留 read_store.create_runtime_binding，
另一支臨時建立 RuntimeManagerClient(allow_local=True)；不是單一明確 command port。
其他原 §4.5 列項需逐列 owner/caller/receipt 查核，不能以 ReadSurfacePorts 本身零 mutation
方法，就宣稱所有 callers 已遷移。

## 4. Journal：有效 durable owner 接線，但 consumer 與舊實作尚未收斂

Agora 已接 `services.governance.decision_journal`，移除該局部 create fake-success，
現有測試證明 fresh adapter 對相同磁碟檔讀回；這些有效成果保留。

但新 DecisionJournalOwnerAdapter 用 `__getattr__` 代理整個 read facade，並在同一形狀加寫入；
只在 `agora/router.py` 包裝。全域 `ReadSurfacePorts.list_decision_journal_entries:1016`
仍回 `research_knowledge_source.list_research_notes`，`main.py:22721` 仍消費這個路徑。
00:30:36 actual-facade probe 確認回 research_note，不是 journal。兩個 consumer 不是同一 truth。

原 SD §5.3 要求選定 owner 後兩邊 consumer、tenant/user isolation、rows disposition、
移除 unselected implementation/schema/tests。結案 evidence 自承 `services/agora/store.py`
與 `services/agora/service.py` 的另一套 journal 留待後續，理由是 artifacts 未含 services/agora/**。
正確處理是正式擴 scope／terminal corrective，只退休 journal slice，不能刪掉其他 Agora aggregate，
不能宣稱「unwired」就等同「程式已刪除」。

獨立 readonly owner-function probe 另確認 private ID collision：actor A 建立 private entry 後，
actor B 以相同 caller-supplied ID create，`decision_journal.py:185–186` 的 insert_if_absent
直接回 A 的既有 record/body，沒有 ownership check。owner record 沒有 tenant scope；
Agora list/patch 僅比較 operator ID。這不是已證實 hosted exploit，但足以否定目前 evidence
所稱 tenant/user isolation；formal corrective 必須含 cross-user / cross-tenant collision、
scope-bound idempotency、negative create/list/patch 與 durable replay。

## 5. Router：不能只把巨型 closure 搬去子檔

五個 parent factories 已縮至 71–190 行；Persona metadata 的 instance-bound context 也有改進。
但 evidence 自列 subrouter ranking 1694 行、knowledge 1684 行、workspaces 1197 行。
`agora/trading_room/routes/workspaces.py` 的 handler 仍直接 `ctx.store`，
計算 queue/proposal/layout 等 business branching；不是只做 HTTP mapping 並委派一個 use case。
依 SD §4.2A 的 application 邊界、typed domain dependencies、例外 cohesion scorecard 繼續整頓；
不為湊行數再無意義拆檔，也不把 Any-filled context dataclass 當完整 typed port。

## 6. Overlay 活躍工作：正式擴 contract 與真實持久證據尚待 owner 回應

00:28 exact worker tree 為 bff2dec5636967b096fcc1c23f65c3b702fca65c，61 個 tracked unstaged files。
其中 governance/router、management_read_models、capital/router、command queue/executor、
action catalog、control_loops、events、research common、BFF root/tests 等不在現行 artifacts。
已用 qualified Human/Ops note 要求保留 worker 變更、checkpoint、authenticated blocker
列明最小增補理由，才修訂同一 active task。沒有為 owner 冒報 blocker，沒有手改 scope。

`tests/test_overlay_retirement.py:221–244` 所稱 multi-replica/restart 只是把同一個
FakeCanonicalReadStore 暫設 None 再放回；不是 fresh process 或 durable writer。
rollback test 只 assert 本地字典常數，不能證明 actual release rollback。
需保留 mandatory symbol retirement，同時補 SD §5.1/5.2 的 owner資料處置／dry-run
counts/checksums/conflicts、restart/replica parity；測試替身不等於持久性實證。

## 7. CW：四項已修，三項仍有 actual-code 負例

PR #5612 已修：empty list 不改查 workflow、missing committee 不改查 request、
committee explicit unavailable 壓過 dataset ok、service 省略 redactor 會隱藏 refs。

仍未修：

1. memo record degraded + dataset unavailable 被投影為 degraded，summary 仍存在；應 unavailable 並 suppress。
2. 缺 dataset provenance/default callback 被投影 ok/fresh，canInitiateGovernanceReview=true。
3. service default 雖改安全，router 仍有另一個 pass-through redactor default；
   actual router endpoint body + capabilities=[] 回傳 strategy ref、redacted_count=0。
   正常 production main 已提供真實 callback，這不是已證實 hosted disclosure。

這正是「同一 policy 有兩套 default，僅改一層症狀」；需沿既有 GovernanceService／router
收斂成單一 owner，強制 actual service + router 負向矩陣，不能又只驗 service。

## 8. 可重跑的獨立取證與界線

`/tmp/pantheon-closure-negative-probe-20260906.py` 沿用原 CW guarded probe：禁止 network、
subprocess、filesystem writes，不 import main；只在記憶體操作合成資料，直接執行實際 async
endpoint body，不宣稱 HTTP/ASGI/hosted 全程測試。00:30:36 root .venv Python exit 0，
所有 before/after hashes unchanged。先前 supervisor-only Python 缺 fastapi 的 attempt exit 1，
已記錄為環境不適用，不算測試成功；沒有安裝或修改 live Python。

| Source | SHA256 |
| --- | --- |
| governance/service.py | 7c02337a34019c0a097e8ecf7554b48dbd2462e8974d1a6b234ccf93b1ab0f2d |
| governance/router.py | 9cc56ba5a1b761fb6a9980afe71ecc0307606be0caa2d51b5d677e84f134f1ac |
| ports/read_surface_ports.py | ce91b6b760edb7c9213482ce1465bbbeee9f84c6b422eb14d72ce7d79e95b03c |
| ports/operations_consultation.py | 0cd9cfb3bf8e9ea7e04993ee302ff55907228966adf4f3ca0cb34989f5fc3e54 |

後續以同一 canonical task transport / supervisor / independent reviewer / integrator 執行。
不建第二套 task store、cron、產品寫入 facade、journal replication 或測試框架。

## 9. 已正式接收的執行收尾 tasks（00:40–00:42 UTC）

不是僅寫規畫或只放 inbox：packet `pkt-structural-source-residuals-20260906-v1`
於 00:39:57 queued、00:40:23 supervisor admitted；receipt processed / admitted，errors=[]，
authoritative materialization readback=verified；隨後逐項 qualified Human/Ops show 全部 active/todo。

| Task | Owner / reviewer | 執行前相依 |
| --- | --- | --- |
| BFF-CW-POLICY-OWNER-CORRECTIVE-002 | Claude / Antigravity | 原 CW 已 done；等待 Overlay |
| JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001 | Claude / Antigravity | 原 Journal 已 done；等待 CW corrective |
| BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 | Antigravity2 / Claude | 原 BFFTEST 已 done；等待 Journal corrective |
| BFF-ROUTER-USECASE-CORRECTIVE-001 | Antigravity / Claude | 原 Router 已 done；等待完整 test migration |
| DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 | Antigravity / Claude | 原 Writers 已 done；等待 router use-case corrective |

上述為 configured assignment，不推論不同 identity 的額度／帳號等價。
順序避免與 Overlay 的同路徑活躍修改平行重做；若實際出現反向 prerequisite，需 owner
authenticated checkpoint/blocker 後正式重核順序，不能等待循環、私改 task JSON 或另做一套。
AGORA-CHAIN 的 canonical note 已交接新缺口與收尾 owner，保留其原本工作及驗收。

規範文件：[STRUCTURAL_RESIDUAL_SA_SD_20260906.md](STRUCTURAL_RESIDUAL_SA_SD_20260906.md)，
SHA256 `6ec3b02f78435f26e48f491f8f86d593771cab9c115e7fb5745d94930bd064aa`，簽章後未改。
Packet digest `89e001322d6a244a8d86aaaf114fbd582ab89998eeb0af3c5deb716b4056f3b5`。

Overlay 的契約／持久性驗證問題也已交付
[PR #5618 advisory comment](https://github.com/ajoe734/pantheon/pull/5618#issuecomment-5555850008)，
這不是 canonical reviewer approval。現行 Overlay artifacts 尚未擴充，仍待 owner checkpoint/blocker。

00:40:51 再查現有 schedule：auto-integrator 恰一筆 `*/5 * * * *`；
pantheon-supervisor-watchdog.timer 持續每分鐘觸發。沒有新增重複 cron 或 supervisor。
五項 task 是已接收待執行，不是已實作合併；所有 product/hosted 未閉環結論保留。
