# SA/SD：還原 CW 公開契約，收斂 Governance 投影與行動政策

Task：BFF-GOVERNANCE-CW-CONTRACT-CORRECTIVE-PREREQUISITE-001。
日期：2026-09-05。基準：dev merge 62ecea34b5ae51bb35f484f1a7a78d39fecd9c08。
簽章後本文件不可修改；追加發現另記報告。這是 functional source corrective，
不是 hosted、production、real capital 或部署授權。

## SA：正式依據與重新盤點

已發布文件是本修復的規範，所有 required fields／invariants 都必須追溯：

- docs/bff/CW-01-consult-request.md 與 docs/examples/CW-01-consult-request.json
- docs/bff/CW-03-committee-board.md 與 docs/examples/CW-03-committee-board.json
- docs/bff/CW-04-redteam-memo.md 與 docs/examples/CW-04-redteam-memo.json

先前以為新 characterization tests 可能代表新版格式，因此曾向操作員非阻塞
詢問。05:14 的正式來源複查消除了歧義：

1. docs/04/pantheon_full_product_operation_audit_2026-08-29/
   EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json:161–165 明定治理 V1→V2
   supersede_reason=bind_v2_bootstrap、functional_scope_change=none。
   Blob a6afdb1cae15dd78919f00cb94d730e25e38eb79。
2. 同目錄 EXECUTION_TASK_CATALOG_2026-08-30.json:69644–69800 明定只搬移
   governance routes、保留既有 lifecycle governance predecessor；搬移清單
   包括 CW01 validation 與 CW04 projection helpers，不是授權刪掉語意。
   Blob a419068a322181685d4d294b4284c5c4dbcb31c7。
3. PR #5467 只新增 router/service/focused tests，未變更 published docs，
   未找到正式 API supersession。Issues #36/#43/#47 的 closeout 是 tracker
   hygiene，明言沒有 code/deployment 變更，不能當作格式改版授權。

因此恢復已發布契約是修正超出路由搬移範圍的 regression，不是選擇新的 API。
不得同時回傳 data/items 或 root/data 別名以令兩組相衝突測試都通過。
現行文件的歷史 route-live 標籤不構成今天的 hosted 驗收。

已重現：

- CW01 合法 risk_review 被錯套 request_type enum 而 422；critical priority、
  type/id context refs 與公開 envelope 也須完整驗證。Consultation domain 的
  request_type 與 consultation_type 是不同欄位，既有 operations port 已負責
  映射與 lifecycle/session linkage，不能在 BFF 再寫一個生命週期引擎。
- CW03 GET 回 raw record 缺 allowedActions，但 main command validator 仍跑
  _cw03_committee_projection／allowed-actions policy；讀／寫決策分叉。
- CW04 GET 缺 memo projection、mapping/action/surface metadata；detail 是
  raw data wrapper。DI seam 已存在，不是必須再造 application factory。
- GovernanceService.create_consult_request 在 port 回 None/缺失時合成
  created+UUID+canCancel，不能以未持久化資料宣稱成功；此 exact branch 應移除。

三組先前 direct production-router probes 為隔離、無 downstream side effects；
未宣稱全 CW suite 或 hosted 已通過。錯誤來源 blame eaa1cfea5459efbed16c8c7203e4dafcb480f92f。

05:20 另用 actual DomainConsultationPort + memory I/O capture 複驗，六種
published subtype 加 critical 的公開回應雖保留原字面，送入 service 的
request_type 全變 strategy_review、priority 全變 normal。這是 port mapping
回歸，不能只修 router。已找回原 service adoption 的完整正式映射：
commit aba0cd0087f297dadfff5769d5a97f4bdc3215e8（2026-04-28，SD-CONSULT-003），
services/control-plane/bff/read_store.py:88–105。現在在既有 extracted port
還原此表，不恢復已退役 read_store owner，也不自行制定新業務分類。
原 adoption blob 80c0994f85f09e491b29aabd0427325ebbc988d9；extraction 前
parent dcb14231d29f08f1646a4ee962b83fd2d4b67560 的 read_store:1842–1854
仍保留映射（blob bac21b19755efc58d7031471a7cf6fe9deefc6ff）。
cc0325090ee77ce4a11a642284f7c33ea1747e37 的 typed-port extraction
遺失 subtype/critical keys；PR #5318 未授權改業務分類。

## 結構責任與不重複開發

沿用 GovernanceService 作 public validation／projection／action-policy owner，
沿用 create_governance_router 作 HTTP adapter，沿用 existing typed read ports
與 domain command/store 作事實和 persistence owner。Router 不自行重建政策。
Main 只組裝 dependencies／呼叫既有 command validator；不得把 domain 邏輯搬回。

依賴 BFF-PACKAGE-BOUNDARY-CORRECTIVE-PREREQUISITE-001 與 BFF-COMPOSITION-001
完成，避免碰仍在做 package normalization 的 main。新的 regression 放 scripts，
不平行編輯 BFF-TEST-ARCH 所有權內的 existing test files。

DOMAIN-WRITERS-001 仍負責後續 canonical mutations、approval/idempotency/state
convergence。此 corrective 只還原 CW contract 與其既有 read/command policy，
不重作一般 approvals 或另建治理 store。既有 task 的依賴鏈不刪改、不形成
BFF-TEST → writers → 本修復 → BFF-TEST 循環依賴。

05:16 canonical snapshot event 2273／Human-Ops show 再核對：無 active source
overlap。Inactive overlaps 另含 JOURNAL-OWNER-001 的 governance/**、
OVERLAY-RETIRE-001／MGMT-READ-001 的 main.py，以及 PPL-ALLOC-009 廣泛
BFF contract。保留各自 durable journal、overlay retirement、Management
read-model／PPL closeout 所有權，交接此修復基線，不重開相同 CW 工作。
不將本任務反向依賴歷史 audit 缺失的 PPL-ALLOC-007。
05:23:30 event 2285 再核對新增 operations_consultation.py：無 active
owner overlap；只匹配 done MGMT-GAP-003、todo PPL-ALLOC-009 與
todo OVERLAY-RETIRE-001，後兩者仍保留原 dependency/owner 邊界。

## SD：CW01

- Public consultation_type 接受已發布六種 subtype，priority 接受四種含 critical；
  context_refs 驗證 type/id 與 published type set，不接受錯名 key 充作 canonical。
  保留 required-field／target validation；從原 lineage helper 還原，不移植整個 main。
- 將驗證後的欄位交給既有 operations consultation port；request_type 映射、
  request/session identity、pending/running/completed/canceled 語意由該 owner
  提供。不得從時間、queue age 或 fixture 假推進。Create 只在真實 port 成功時
  回傳 persisted identity；missing/None provider 明確 fail closed，禁止 synthetic
  success、local UUID authority 或 second persistence fallback。
- 在既有 ports/operations_consultation.py 修復同一 mapping owner：
  pre_deployment→STRATEGY_REVIEW、risk_review→EXECUTION_RISK、
  macro_regime_shift→STRATEGY_REVIEW、incident_response→INCIDENT、
  policy_change→PERSONA_POLICY、general→STRATEGY_REVIEW；priority 的
  low/normal/high 對應同名、critical→URGENT。這是上述歷史正式表，不是
  新推論；公開 consultation_type／priority 與 service 的 enum 欄位須各自
  保留，不拿 metadata 的正確字面掩飾傳送錯誤。Client 與 local typed-store
  兩個既有 I/O 分支都共用同一 table/policy，禁止各自複製另一份映射。
  不改 domain enum/schema，不移植退役的 read_store 模組；unknown input
  必須沿 validation 拒絕，不能把 fallback 當成六種合法輸入的路由決策。
- Create/cancel 為 published root envelope；list 是 data/page_info/meta，detail
  為 root fields + links + meta。保持 request_id、linked_session_id、
  request_to_session_status、session_handoff 來源一致，不用雙格式包裝。
- 既有 port 的 canCancel lifecycle policy 保留；公開 action 另依同一 read-surface
  availability 與實際 write guard 決策，不能使 readonly viewer 獲得假的可寫 signal。
  不放寬現在的 create/cancel operator guard；blocked terminal/unavailable 不寫入。
- meta 使用 consult_request_list／consult_request_detail，保留 fresh/stale/
  degraded/unavailable 區分及 unavailable suppress-content 語意。

## SD：CW03

- List 回 data，接受 quorum_state／consensus_state filters，page total 是過濾後
  全數而非 page length；保留 published route_href 與 canonical board fields。
- Detail 使用單一 governance-owned projection，保持 roster、sponsor、synthesis、
  evidence 等 backend 結果；不得在 browser、route 或 test double 重新算 verdict。
- 將 main 的 _cw03_committee_surface_state、_cw03_allowed_actions、
  _cw03_committee_projection 三個 implementation 收斂至 governance/service.py。
  其需要的 record、identity、surface/source、snapshot metadata 都用明確參數，
  不 import main、globals forwarding、namespace proxy 或隱性 store callback。
- GET 與 _validate_record_sponsor_decision 必須呼叫相同 policy owner，command
  執行前仍重新讀當下 board，不信任客户端傳來的 allowedActions 或先前 GET。
  保留既有必填/decision enum/rationale/404/409/403 semantics。
- Sponsor assignment、operator/approver/admin、sponsor_required、未有 decision、
  surface 非 unavailable 才可能允許。明確 unavailable 必須優先；missing provenance
  不可無條件 default healthy。保留 published stale/degraded 與 unavailable 區分，
  不擅自新增或取消其他業務條件。Transcript-gated surfaces 仍受 CW02 truth 管控。

## SD：CW04

- List 保留 items，不改成 CW01 的 data；detail 是 published root fields，不包 raw
  data。使用已由 domain port 提供的 memo/mapping/lifecycle 內容作公共投影，
  不從 transcript 猜 mapping、不再造 lifecycle、recommendations 保持 string list。
- meta.surfaces.redteam_memo.state 是 ok/degraded/unavailable；freshness 在
  meta.staleness。degraded 保留完整 published detail+mapping，只禁止 CTA；
  unavailable 才 suppress summary/recommendations/evidence content。
- canInitiateGovernanceReview 只由 governance owner 計算：published、合法 target、
  actor reviewer/governance authority、無 active review、未 suppressed/withdrawn、
  可用 evidence/service/target type；degraded/unavailable 均 false。缺失 state/
  provenance 不可變成健康成功。不得在 fixture 或 frontend 寫第二份 gate。
- 原 evidence redaction/capability callback 要繼續使用，不能用空 stub 關掉。

## 精確 artifacts

- services/control-plane/bff/governance/service.py
- services/control-plane/bff/governance/router.py
- services/control-plane/bff/main.py
- services/control-plane/bff/ports/operations_consultation.py
- scripts/test_bff_cw_contract_prerequisite.py
- docs/operations/governance-cw-contract-owner.md
- docs/deployment/evidence/BFF-GOVERNANCE-CW-CONTRACT-CORRECTIVE-PREREQUISITE-001/evidence.json

Existing CW tests、tests/test_governance_router.py、domain schema/model、其他 ports、FE
只讀參考，不屬本任務可寫 artifacts。上述唯一 port 只改 CW translation 範圍。
若真正需改其他 owner，先 checkpoint 與
authenticated blocker，正式擴 contract 或協調原 owner；不可暗中擴張。
現行 `_created_approvals`／通用 idempotency 等其他 writers 缺口保留給原 DOMAIN-WRITERS。

## 驗收矩陣與交付

1. 專屬 script 測試 actual production router/service，typed per-domain doubles
   只提供資料／I/O，不自己實作 endpoint、projection 或 policy。含 CW01 subtype/
   priority/context refs 正負例、persisted-create／missing-port rejection、完整
   root/list/detail/cancel shapes、pagination/filter 與 session handoff。
   額外跑 actual DomainConsultationPort 的 6×4 subtype/priority table，client
   capture 及 local typed-store 分支均驗證真正 canonical I/O payload 和讀回，
   不只檢查公開 metadata；typed doubles 不得自己實作 mapping。
2. CW03 用 action truth table 覆蓋 missing sponsor、role、consensus、already
   decided、unavailable、允許案例；驗證讀取 signal 與 actual command validator
   同一 owner 且重讀 fresh state。不可用只看字串存在的 gate 代替 behavior。
3. CW04 驗證完整欄位／mapping／redaction／target與actor條件，三種surface
   state、degraded content retained/unavailable hidden；各個否定條件都須阻止 CTA。
4. 小範圍 composition/wiring probe 證明 production DI 真的接到相同 owner，
   route uniqueness、zero governance→main reverse imports、main 三個舊 policy
   implementations 已刪，無 compatibility forwarding／second store／雙格式。
5. BFF-TEST owner 同步依正式 contract lineage 調整衝突 characterization 並 mount
   真實 routes；不得更改原 published assertions/payload 來避開缺口。此任務不得
   因未收齊整合 evidence 宣稱所有 old/new tests 原封不動都綠。
6. 記錄 exact head、source checksums、完成的 commands/exit codes/counts、真正
   residual failures。Bounded foreground 執行，不用 collection、skip/xfail、
   timeout/killed、偽造測試 endpoint 當成功。
7. Current dev clean task branch、required trailers、push/PR、獨立 canonical
   review、required CI、既有 integrator merge/archive。根據原 user scope 交付
   source，不新增 product route/dev bridge、cron、dispatcher 或部署。

回退為正常 revert/forward repair 經同一 review/integrator，不重開 bare aliases、
雙格式、另一份 policy 或 in-memory success。Hosted readiness 留待原任務鏈。
