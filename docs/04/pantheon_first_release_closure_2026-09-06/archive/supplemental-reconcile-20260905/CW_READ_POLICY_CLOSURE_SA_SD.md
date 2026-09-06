# SA/SD：CW 真實讀取層與政策驗收收尾

Task: BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001。
日期：2026-09-05。Revalidated base dev@f797244b7905d722072180505f8ee348dacf8618。
本文件簽章後不可修改；只授權 functional source delivery，沒有 hosted/live/
real-capital 變更授權。原始 20 項產品架構與 12-loop 驗收不縮減。

14:20 canonical active-scope 回讀：僅 BFF-TEST-ARCH in_progress，與以下八檔
沒有重疊；無 review/review_approved owner。Inactive JOURNAL/DOMAIN/OVERLAY/
MGMT/PPL 所有權與相依保留，不把本後續修復當成上述任務完成。

## SA：為何是 terminal follow-up，不是平行重做

BFF-GOVERNANCE-CW-CONTRACT-CORRECTIVE-PREREQUISITE-001 已 archive/done，
PR #5610 merged 06:15:24，canonical archived 06:17:02；reviewed head
a91100e860cb35eb0cd98527de543e1084bc6870。其七檔合約沒有包含
ports/read_surface_ports.py，也沒有 artifact revision。Terminal evidence
不修改、不在原 task 上 reopen；此 task 依賴並引用該已完成來源交付。

保留原修復有效部分：public six-subtype/four-priority mapping、type/id refs、
synthetic-create removal、公開 envelopes、command validator 單一 projection。
不得全部推倒另寫，或聲稱 14 tests 通過即滿足完整原驗收。原 immutable
GOVERNANCE_CW_CONTRACT_SA_SD.md（SHA256
00e36a3742b5fdf8dd331988d92044c6600f860e880160062fc7f0ecc8c2d1b3）仍為
published CW01/CW03/CW04 與 ownership/negative matrix 的規範。
其 formal V2 ledger functional_scope_change=none 已證明這不是 API 改版。

14:18 在 current-dev clean checkout 的 actual-code guarded probe 重現：

1. DomainConsultationPort 回 [] 後，ReadSurfacePorts.list_committees 把 falsey
   result 轉去 list_workflow_templates；GovernanceService 傳 quorum_states/
   consensus_states，Composite workflow method 不接受，拋 TypeError。
   無 filter kwargs 時則錯回 workflow row。get_committee None 又改查同 ID
   consult request，造成錯誤 object type／缺 committee_id 而非 404。
2. Committee record unavailable、dataset ok，projection 得 ok 且 sponsor CTA
   true；memo record degraded、dataset unavailable，保留應 suppress 的內容。
3. 沒有 dataset provenance/default callbacks 得 healthy/fresh 與可寫 CTA。
   有 healthy source、capabilities=[] 但省略 redactor 時，strategy evidence
   refs 未 redacted。正常 main/router 已接 real callbacks，未宣稱 production
   route 現已洩漏；這是新 default path fail-open 與矩陣缺測。

Exact source SHA256：service337f9799b52e65ac5f046c3dad24ed4bf4621f1d788cb74812a573e332c07ede；
operations port b9f3dd7db477aeb83c11c28266f2a5756315c789e19646a90045cf029f90e485；
read facade6c4c8920162b961eae0316a4d060e09d19d684f7bec9836c81be42efd801c093。
Probe /tmp/pantheon-cw-merged-negative-probe-20260905.py 未 import main、禁
network/process/filesystem writes，terminal exit0、hashes unchanged；不當作
full-app/hosted 驗收。先前完整 real-router chain 也已重現相同缺口。

## SD：既有單一責任修復

### 1. 保留 read port 的 empty/missing truth

只在現有 ReadSurfacePorts.list_committees/get_committee 去除跨 domain 的
workflow/request fallback；typed operations/consultation owner 的 []/None
是有效結果，必須原樣傳遞。Missing required provider/capability 明確符合
原 unavailable contract，不能當健康空集合，也不能用其他 dataset 補成功。
不得建新 facade、truthy empty list subclass、exception-as-success、繞過
facade 的 service special case、或把委員會 identity 塞進 request 當別名。

維持 DomainConsultationPort → Composite → ReadSurfacePorts 的同一邊界；
在已存在 ConsultationReaderPort declaration 補上實際 committee methods，
保留 current client/local-store 的 read owner；沒有新 store 或生命週期引擎。
原 metadata projection 可用的欄位須保留；依 dcb14231d29f08f1646a4ee962b83fd2d4b67560
read_store:18499–18643 原 lineage 保持 started_at descending 和可取得的
persona label，不能複製整份退休 read_store 或假造 display data。

### 2. 單一 availability policy，不同來源的 unavailable 必須優先

既有 GovernanceService 收斂 record 與 dataset/source state，明確 unavailable
優先於另一來源的 ok/degraded/stale。缺 source/callback 不默認 healthy。
保留有證據的 fresh/stale/degraded 與原允許條件，不擅自把 stale 全禁用。
CW03 GET 和 actual sponsor command validator 仍共用同一 owner、command
重讀 fresh board。CW04 unavailable 才 suppress content；真正 degraded
但可用時保留完整 mapping/summary/evidence 並禁 CTA。List/detail 均符合
各自 published contract；不得只修被 probe 點到的一條 helper。

### 3. 明確 redaction/capability 邊界

沿現有 models/production redaction owner。不新增 pass-through default 或
第二份 capability 判斷。採明確 required dependency 或同一既有 canonical
default policy，缺失依赖/exception 時 fail closed；不能為未使用 CW 的其他
governance routes 製造不必要破壞。Normal main/router wiring 要實際驗證，
測試缺 redactor、empty/insufficient capabilities 與 callback failure。

## 精確八項 artifacts

- services/control-plane/bff/governance/service.py
- services/control-plane/bff/governance/router.py
- services/control-plane/bff/main.py
- services/control-plane/bff/ports/operations_consultation.py
- services/control-plane/bff/ports/read_surface_ports.py
- scripts/test_bff_cw_contract_prerequisite.py
- docs/operations/governance-cw-contract-owner.md
- docs/deployment/evidence/BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001/evidence.json

修改既有 regression script/runbook，不另建一套 CW 測試框架；不要改原已封存
evidence.json。Existing BFF tests 為 BFF-TEST-ARCH owner，source 調整不得
削弱其 published assertions。通用 approval/idempotency/sponsor persistence
仍在 DOMAIN-WRITERS，Persona globals 在 BFF-ROUTER，overlay retirement
在 OVERLAY-RETIRE，不能平行接管。若再需額外 artifact，先 checkpoint 與
authenticated blocker，再正式擴同一 active task；不可默改 scope。

## 驗收：補足原完整要求並證明真正 integration

1. 原六 subtype×四 priority 的真實 client/local typed-store mapping tests
   保留並通過，不把 public metadata 正確當作 actual I/O 正確。
2. Real DomainConsultationPort → Composite → ReadSurfacePorts →
   GovernanceService → actual router：非空、空、filter 無命中、missing ID、
   同 ID unrelated request/workflow collision、missing provider；精確驗證
   []/None/404/unavailable 與 total/filter/order，禁止不相干 source 讀取。
3. Record×dataset ok/degraded/unavailable/missing provenance 的矩陣，同時
   驗證 public metadata、CW03 GET/action 與 actual validator fresh-state 重讀，
   CW04 retained/hidden content + 全 CTA 否定條件。Typed doubles 只供 I/O
   與資料，不自己寫 endpoint、projection 或 policy。
4. Redaction absent/exception/insufficient caps/empty caps 的負例及 authorized
   正例，production real wiring；不得只證明 normal callback 接線有字串。
5. 原公開 create/list/detail/cancel 與 pagination/handoff/role/error contracts
   保留；完成 scoped regression、ports/smoke，逐項列出真正 residual failures。
   既有 conflicting characterization 調整仍由 test owner 按正式 lineage 接手。
6. Tests 必須有 bounded foreground terminal output/exit/counts；不能用
   collection、skip/xfail、timeout/killed、自製 fake endpoint 當成功。Exact
   head/code hash/evidence，要能證明上述每列，不用 14/14 作 blanket acceptance。
7. Current-dev clean task worktree，正常 subject/trailers、push/PR、獨立
   canonical review、required CI 與 existing integrator merge/archive。
   此任務不需要 supervisor runtime promotion、cron 或 hosted deployment。

回退走同一正常 revert/forward-repair workflow，不復原跨 domain fallback、
不隱藏缺 source、也不重建舊 duplicated authority。
