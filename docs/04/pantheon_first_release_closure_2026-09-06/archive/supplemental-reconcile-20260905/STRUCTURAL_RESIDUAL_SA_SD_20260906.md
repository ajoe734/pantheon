# SA/SD：已封存來源交付的結構性收尾

日期 2026-09-06。Revalidated dev `471dc5391a0f9cbde54d51730891583043708e42`。
簽章後本文件不可修改。原始 baseline675a488d 的 SA / SD / TRACEABILITY / EXECUTION_TASKS
仍適用，尤其 SD §4.2A、4.4、4.5、5.1–5.3、8.5–8.6；沒有 functional scope reduction。
取證報告 CURRENT_SOURCE_REAUDIT_20260906.md 為旁證，規範要求在本文件完整列出。

## SA：共通根因與不得重建的機制

目前 source delivery 多以 focused tests 成功取代完整 acceptance；有的把未遷移項標為
PLANNED，有的在 artifact 缺項時自行推遲，有的只改 service default 卻保留 router 第二份 default。
這些是結案覆蓋與 ownership 邊界未落實，不是需要新框架。

五個收尾 task 分別引用已 terminal 的來源任務，不修改 archive/status/evidence，不重做有效部分。
沿現有 owner/module/test gate 演進；禁第二套 façade、journal store/replication、policy engine、
test framework、cron、TaskStore。所有新增依賴必須是真正 typed domain 邊界，不能是全能 service locator。

這些是 functional source tasks。無 hosted、VM deployment、認證／秘密／帳號修改或 real-capital
操作授權。測試使用合成 tenant/data、隔離 database/temp dir，不觸碰正式產品資料。

## 共同執行與驗收

1. supervisor owns dispatch，worker 用 clean current-dev task worktree，保留其他 workers 的內容。
2. Overlay 活躍且有重疊修改，因此以下鏈先等待 OVERLAY-RETIRE-001，再按 CW → Journal →
   Test migration → Router use cases → Domain commands 執行，避免同一路徑並行重做。
   若 Overlay 確實需要某後續 owner 修復才可通過，必須先 owner checkpoint / authenticated
   blocker 與正式順序重核；不得私改依賴、做出循環、或繞過 lease/fence。
3. 原 AGORA/LOOP/MGMT/FE/DELIVERY 仍保留各自完整 ownership，source done 不作其驗收替代。
   後續任務若需要本文未完成能力，報真實 prerequisite，不可再補第二份實作。
4. 每條 acceptance 對應 actual production path、具體 regression、command、exit code、executed
   count、code hash。負例必須先重現既有缺口，再在同一入口通過；測試不自己複製被測 policy。
5. Bounded foreground batches，收齊 terminal output；collection-only、timeout、killed、skip/xfail、
   blanket green-count、改 metadata baseline 或只設常數的 test 不等於完成。
6. 只 stage artifacts 中的必要檔案，正確真實作者／task／reviewer trailers，push/PR，獨立
   exact-head canonical review，required CI，existing integrator merge/archive。
7. Existing evidence/terminal artifacts 不重寫；每項新 task 各有 exact evidence.json。
   額外檔案在編輯前以 scoped checkpoint/authenticated blocker 申請 formal artifact-contract。

## A. BFF-CW-POLICY-OWNER-CORRECTIVE-002

Predecessor BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001，PR5612 mergea86c8ff9。
原兩份 CW normative SA/SD 仍適用。已修的 6×4 mapping、context validation、真實 create/envelope、
committee empty/missing semantics、committee unavailable precedence 均保留。

### SD

- 現有 GovernanceService 成為唯一 CW availability/content/action owner；router 不再維持另一份
  healthy-default / pass-through redaction policy。不得另建 shadow service 或第三份 mapper。
- any explicitly unavailable source 優先於另一來源 degraded/ok。缺 source/callback 不宣稱 fresh/
  healthy/actionable；保留真正有 provenance 的 degraded/stale contract，不擅自全禁有效 stale。
- 沿現有 canonical evidence/capability owner，service 及實際 router omission paths 均 fail closed。
  unknown/empty/insufficient capability、lookup error、redactor error 有明確契約，不能漏 evidence。
- 實際 main 正常 wiring 已存在；唯讀驗證其 composition，不需變更 main 或 auth 機制。
- 使用 existing scripts/test_bff_cw_contract_prerequisite.py 擴矩陣；不要另建測試 runner。

### 必須逐列驗證

record × dataset source：ok/degraded/unavailable/missing，各組以 actual service 與 real router
覆核 list/detail、content hidden/retained、all CTA。特別固定三個 red cases：
(1) memo degraded × dataset unavailable → unavailable/summary suppressed；
(2) omitted provenance → not ok/fresh、CTA false；
(3) actual router omitted redactor + capabilities=[] → no visible strategy evidence。
authorized real-redactor positive、normal wiring、CW03 fresh command validation 與先前6×4/empty/missing
regressions一併保留。Command persistence 由 E 負責，不在 A 補第三套 writer。

Artifacts：governance/service.py、governance/router.py（位於 services/control-plane/bff）；
scripts/test_bff_cw_contract_prerequisite.py；docs/operations/governance-cw-contract-owner.md；
docs/deployment/evidence/BFF-CW-POLICY-OWNER-CORRECTIVE-002/evidence.json。

## B. JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001

Predecessor JOURNAL-OWNER-001，PR5613 mergedbca7faea。保留選定 governance durable owner、
已接線 Agora create/patch、fail-closed 無 writer 的修正；不回退為 fake success。

### SD

- 完成 SD5.3 scorecard：transaction/CAS、append-only history、tenant/user isolation、outbox/event、
  Agora/Governance consumers、fresh-process readback。維持既有 selected owner，僅補缺的能力。
- 用明確 typed query/command ports 連接所有 journal consumers。修 global read facade 把 research
  notes 當 journal 的錯路徑；移除 adapter 全域 __getattr__ forwarding，不把寫入塞回 ReadSurfacePorts。
- create supplied-ID collision 不可回其他 actor/tenant private record。tenant/actor scope 同時適用
  create/list/detail/patch/audit/idempotency；缺 scope 的 legacy rows 不默認全域可見，須有受治理遷移。
- 在原 services/agora/store.py/service.py 精確退休 journal bootstrap/create/patch/audit/replay slice。
  保留非 journal aggregates。先記錄 existing rows/source IDs/checksums/conflicts、dry-run 与 resumable
  tenant-scoped migration，再驗 fresh query parity，最後零舊 writer/bootstrap/caller；unwired 不等於刪除。
- ownership inventory 更新為實際 selected owner，不留下兩種權威名稱；無 journal-to-journal bridge。

### 必須逐列驗證

actor A/B、tenant A/B（含相同 operator ID）、相同 supplied entry ID 與 idempotency key 的 authorized
positive / unauthorized negative；real selected owner against isolated durable backend；fresh-process restart、
duplicate retry/conflict、optimistic conflict、all consumer read parity、舊 bootstrap/writer forbidden checks。
舊7tests只證fresh adapter，不足以支持全 scorecard。不得暴露實際私人產品資料作 evidence。

Artifacts：services/governance/decision_journal.py、services/governance/test_decision_journal.py；
services/control-plane/bff/agora/service.py、agora/router.py；
services/control-plane/bff/governance/decision_journal_write_owner.py、governance/test_decision_journal_write_owner.py；
services/control-plane/bff/ports/read_surface_ports.py、ports/operations_consultation.py、main.py；
services/agora/store.py、services/agora/service.py、tests/agora_write_owner/**；
docs/02-architecture/product-aggregate-ownership.yaml；services/governance/migrations/**；
docs/deployment/evidence/JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001/evidence.json。

## C. BFF-TEST-FULL-MIGRATION-CORRECTIVE-001

Predecessor BFF-TEST-ARCH-001，PR5600 merge1db964e2。保留正確 CW 分類與 real cookie/bearer
production classification tests，不再次實作已消除的 copied cookie extractor 問題。

### SD

- 以 immutable original 218 cases /216 unique files 為 traceability universe，亦掃描新 tests/helpers/smoke。
  起始 current-dev212 original importers，204在allowlist外。所有非 composition categories2–4實際遷移；
  PLANNED inventory不是 closure，沒有 owner-approved縮成5files的授權。
- 現有 inventory/gate改驗actual AST/import graph，覆蓋 helpers/conftest/smoke/subprocess；不要只檢JSON211。
  small justified composition allowlist，每項有 sole purpose；不得重新分類來容納全部遺留 tests。
- per-domain common typed fixtures，router薄mock I/O、不複製application/authbusinesspolicy。adapter層用真實
  隔離依賴；hosted層只HTTP。禁止universal fake store與另外一套gate/runner。
- 原 ASK001/003/004 global store/replay swapping需真正instance-bound；保留原斷言/body contract。
  嵌套A/B及並行client、outer replay在inner exit後保持409；不能只 finally還原store卻丟cache。
- 移除 owned BFF sys.path insertion，包括 conftest.py與knowledge_read_port_fixtures.py；canonical package imports。
  每檔 collection/import/runtime bounded evidence，不以metadata/table數量當實際執行。

Artifacts：services/control-plane/bff/test_*.py、smoke_test*.py、tests/**、*/test*.py；
docs/deployment/evidence/BFF-TEST-FULL-MIGRATION-CORRECTIVE-001/evidence.json。
不修改 main 或 runtime modules來配合fake test；必要product seam先正式報prerequisite。
原BFF-TEST evidence不可改，把實際分類與驗收放現有tests inventory及新task evidence。

## D. BFF-ROUTER-USECASE-CORRECTIVE-001

Predecessor BFF-ROUTER-STRUCT-001，PR5615 merge8e1e4cfb。保留5domain subrouter分工、route signatures、
Persona data/metadata instance隔離；不把父factory縮小等同application boundary完成。

### SD

- 逐一5domains route→application→typedowner traceability：personas/strategies/research/agora-research/
  trading-room。HTTP handler只parsing/authinvocation/DTO/status；business branch/store transaction/retry移至
  該domain existing application use case，一個command/transaction objective，不做universaldispatcher。
- 覆核現有1694-line ranking、1684-lineknowledge、1197-lineworkspace等cohesion；實際router factory
  和business-heavyhandler都有reviewedboundedsize，合理例外需說明，不為300行再碎切 meaningless files。
- domain-specific typed deps，不用Any-filled context冒充typed contract；去service locators/dynamicnamespace。
  existingfunctiononly合理委派，不複製相同行為到service後保留原handlerbody。
- actualnormalizedroute/headers/status/errors/CAS/idempotency/tenant/source-confidence保持；測試新舊實際path，
  dual-instance、negative data/provenance與no-direct-store/no-duplicate-AST gate均需通過。

Artifacts沿原5domain目錄，加既有services/control-plane/bff/tests/test_bff_test_architecture.py、
docs/deployment/evidence/BFF-ROUTER-USECASE-CORRECTIVE-001/evidence.json。

## E. DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001

Predecessor DOMAIN-WRITERS-001，PR5616 merge471dc5391。保留authority metadataanti-forgery与已有正確
接線；不把同一 missing-method 建第二個conditionalfallback。

### SD

- SD4.5每一列有canonicalowner與mountedcallers清單：runtimebinding/deploymentplan/persona update/
  experiment query-command/joblogs/researchticket/agora audit/sponsor decision/rankingdeprecated。
  每個mountedmutation經typedcommandport，readquerymethods不變成writer。
- approval create目前accepted但freshinstance0records。改用既有governancecontroller/durablecommandowner，
  不保留 BFF _created_approvals/_idempotency 當第二source；accepted需durablecommand/outboxrow。
- sponsor executor目前main18869呼叫missingread_store.record_sponsor_decision；接selectedgovernanceowner，
  与CWreadpolicyfreshvalidator一致，commandresult由ownerreadback取得，非假downstream_verified。
- runtime移除hasattr/read_storewriter與臨時allow_localclient雙implementation；由composition注入單一port。
  其他列同樣不保留oldfallback以掩飾owner不可用。
- receipts至少command_id/aggregate_type/id/version/status/event_id/correlation_id/owner/committed_at，
  preservepublicenvelopes。Idempotency tenant+actor+command+requestbound，重啟後retryparity；optimisticconflict、
  negativepermissions、unavailableowner與transaction/outboxfailure均有fail-closed路徑。
- session_id/candidate_digest/proof_digest/controller_record_ref/recorded_at/authority_status保持controller
  真實authoritative來源，不能採用不可信requestmetadata填補。

Artifacts沿原八BFFdomain及四domainservice目錄，精確增加 services/control-plane/bff/main.py、
services/control-plane/bff/ports/**、services/control-plane/governance/approval_decision.py、
services/control-plane/governance/approval_decision.schema.json、
services/control-plane/bff/tests/test_read_surface_caller_migration.py、
docs/deployment/evidence/DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001/evidence.json。
Runtime Manager 的實際來源在 services/runtime-manager/**，package-qualified alias 不是第二份來源。

## 回退與完成

用同一repo workflow修正或revert exact source；migration保留相容schema與sourceID證據。
不重啟dualwrite、不還原processlocalauthority、不把未遷移項改PLANNED後關閉。
完成這五項也不是12loop/hosted完成，原AGORA/LOOP/MGMT/FE/DELIVERY/HOSTED/RETIRE任務仍需完整驗收。
