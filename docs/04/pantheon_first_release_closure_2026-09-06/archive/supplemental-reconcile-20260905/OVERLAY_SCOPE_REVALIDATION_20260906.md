# Overlay 活躍工作：contract 必要性與執行程序複驗

取證 2026-09-06 00:43–00:48 UTC；worker HEAD bff2dec5636967b096fcc1c23f65c3b702fca65c，
原始 dev base471dc5391a0f9cbde54d51730891583043708e42；61 tracked unstaged files。
這是 advisory review，不是新 scope 授權、canonical reviewer approval 或完成證據。

**01:12 更新（以下00:48內容保留為歷史取證）：** main任意namespace forwarding已由worker撤回；
ControlLoopsService已改明確callbacks，不再用main蓋掉注入依賴。Strategy局部overlay dict已移除、
無writer改503，但optional read-store writer/私有_data仍存在。最新66個tracked WIP檔案，
新增control_loops/router.py也不在原contract。另查明Registry固定用memory store，Strategy adapter
沒有真正write/readback；[來源、負向probe與正式prerequisite邊界](STRATEGY_REGISTRY_OWNER_REAUDIT_20260906.md)。
先前回退相容層finding不能在已撤回後仍當成當前main缺陷；Persona內其他反向lookup仍需審查。

## 1. 不可整包擴充的 10 個來源檔案

獨立 agent 複驗以下10檔：base與committed Overlay HEAD均相同，base也沒有五個指定 retired symbols。
因此不能說其 WIP 都是「因 Overlay 刪除而必需的機械修改」。

| BFF 下的相對路徑 | WIP 實際行為 | 處置 |
| --- | --- | --- |
| action_catalog.py | 重新加入 namespace ImportError fallback | 不因 Overlay 擴充；保留 canonical package 方向 |
| command_queue.py | 同上 | 同上 |
| command_executor.py | 同上 | 同上 |
| control_loops/service.py | 載入 main 時，main globals 蓋過明確注入的 store/health monitor；亦改 backfill provenance | 非機械退休；反向依賴不接受 |
| governance/router.py | 透過 main 找 helper，所有讀取 exception 當 timeout/degraded-empty | 需原 owner 行為契約，不可只為 green tests 接回 main |
| management_read_models/router.py | main helper forwarding、exception-name matching | 原 MGMT ownership；不可順帶擴充 |
| management_read_models/service.py | 複製 helper、改 metrics/counts/provenance、per-request executor | `_audit_datetime` 與仍存在 main helper AST 相同，新增重複而非移動 |
| capital/router.py | dry-run accepted 提前到 pool_id 與 existence 檢查之前 | P1 驗證順序退化；不授權資金或新控制行為 |
| research/routes/common.py | 增加 limit 對 page_size 的優先權 | 與 Overlay 無關的 API 行為修改 |
| events/router.py | 為 baseline 既有未定義 resolver 增補函式 | 唯一可另作最小 prerequisite 評估的檔案；非 Overlay 造成 |

若事件 resolver 確實阻擋必要驗證，可提出 events/router.py + existing tests/test_events_router.py 的
精確 prerequisite/contract 理由；目前沒有代 owner 提交 authenticated blocker，也未正式批准這兩檔。
所有 worker 變更原樣保留，root 不覆寫、不刪除或幫忙混入 commit。

## 2. 已在 contract 內仍必須修的 Strategy authority

Current `strategies/routes/collection.py:110–130` 先寫 ctx.strategy_overlay，再嘗試 optional writer
或修改 rs._data，捕捉所有 exception 後照常回成功／cache idempotency。
main 傳 strategy_overlay=None，而 strategies/router.py:78–80 把 None 換成新的 process-local {}。
因此刪掉 _STRATEGY_BFF_OVERLAY 的符號，不等於刪掉 process-local writer 或 fallback acknowledgement。

必須沿 selected owner command/query 收斂；實際 owner failure 不得回成功；fresh reader/restart
證據不能只把同一個 FakeCanonicalReadStore 先設 None 再還原。這些已屬原 Overlay acceptance，
不能推遲到無關 task、也不需要新增第二套保存機制。

### 同在原 contract 內的 main 相容層倒退

WIP `main.py:23065–23070` 新增 `_BffMainModule.__getattr__` 任意轉查 personas.service
namespace，exception一律吞掉。Committed HEAD原本只有retired-symbol拒絕，沒有此forwarding。
WIP亦把governance的read_surface由明確app_deps.read_surface改為lambda:read_store，
並重匯入backward helperaliases。這不是消除舊依賴，而是重新建立動態namespace／global耦合。
main在artifacts內僅表示可修改，不能推論新相容機制符合SA/SD。需移除或按原正式
compatibility owner/removal契約處理，不能拿隨後舊global-mocking tests通過當架構驗收。

## 2A. Test artifact 擴充候選：只有必要部分，不授權整包43檔

獨立static AST複驗43個test/support變更，未刪test function。以下7檔目前僅作retired overlay fixture
清理或test-double欄位更名，可在owner正式checkpoint/blocker後作最小artifact-contract候選：

- services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py
- services/control-plane/bff/test_datastrat_persona_strategy_discovery_bff.py
- services/control-plane/bff/test_inc001_rebaseline_incidents_contract.py
- services/control-plane/bff/test_mgmt_load_002_shell_summary.py
- services/control-plane/bff/test_per002_bff_persona_skills_tools_capabilities_contract.py
- services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py
- services/control-plane/bff/tests/rebalance_authority_test_support.py

另3檔有可保留的局部retarget hunks，但須先分離其他變更：dry_run_rbac_contract的overlay absence
改read absence、evolution_experiment_jobs_events的injected jobs double、b2_004_research_search的
injected strategy read double。它們保留HTTP/side-effects/pagination assertions；不代表canonical durability。

以下問題不因把tests列入artifacts就合理：

1. control_loops_router / training_router將實際single-owner composition assertions縮成name occurrence；
   只出現factory字樣但沒mount也可過。原 reverse-import check也抓不到新增的sys.modules反向依賴。
2. baseline_500的tenantless-overlay負例造了canary卻不再注入，最後assert absent/404變成沒有hostile input。
   另一tenantless-registry負例仍有效，不能錯說全部隔離coverage都刪掉；obsolete overlay case要明確
   替換為old-path拒絕與真實canonical scope衝突測試。
3. b2_list_detail_facade新增in-memory strategy persistence可作router double，不能佐證production
   canonical writer/restart/replica；它恰配合新的optional-write fallback，容易掩蓋退休未完成。
4. 新canonical→bare import fallback、多namespace stores、同時patch main/persona service會擴大相容層。
   Canonical-import-only與command-store finally還原有有效改善，但屬package/test architecture scope，
   不能因此批准伴隨的product behavior patches。

以上是待正式owner回應的範圍候選與reject理由；截至取證並未直接更改active task artifacts。

## 3. 暫時 live repair：只中斷無界的 collection 子程序

00:43 起確認 supervisor1801618、worker2801974、AGY2802201皆活著；last log step931 是
`.venv-pantheon/bin/pytest --collect-only -q services/control-plane/bff/`，tool參數只有CommandLine，
沒有timeout。子程序PID3164057持續CPU99.6%、RSS5007864KiB，並非真實驗證已通過。

00:47:51以PPID2802201、exact工作目錄、exact argv三項再次核對後，僅向PID3164057發送一次SIGINT。
當時elapsed13:32、CPU13:30。目的為讓owner取得中止結果並回到有界驗證／正式checkpoint流程；
不是重啟supervisor/worker，不是因單次觀察timeout就當機重啟，也沒有宣告測試成功。
未改source/config/credentials/cron；未刪檔、未動worker未提交內容。
信號是否已收齊terminal結果須另行回讀，不能把signal送出當作程序已結束。

00:49後已回讀：原PID3164057不存在，原worker2801974與AGY2802201仍是相同程序；
log step931轉DONE，duration813.718秒，後續step935先列changed test files，step937
改執行列明test files的pytest，真實新子程序PID3230457仍在跑。這證明worker已恢復向前執行，
不證明collection或新批次passed；尚未取得新批次terminal exit/counts。

00:51 補核 step931 實際 output：前3927行被provider截斷，尾端顯示
`3750 tests collected in 742.69s (0:12:22)`，沒有可見 exit code 或 KeyboardInterrupt。
因此精確事實是「root已送SIGINT、原程序之後退出、輸出有完成collection摘要」；
不能把信號送出推論成已證實 interrupted exit，也不能把3750 collected記為3750 passed。
先前canonical note/PR把它稱 interrupted 的用字已另行更正，保留操作紀錄不偽造終止碼。

00:57 再回讀step937：provider terminal output明載 **92 failed / 524 passed / 1846 warnings，379.64秒**；
tool duration413.766秒，PID3230457已退出。Worker同一程序繼續逐檔複驗，例如
test_loop_prod_per_001_provisioning.py有6passed/10.13s；不得將isolated pass當成整批92fail都解決。
需逐项baseline/current与隔離／combined差異分辨，不能為迎合遺留global污染而加入反向依賴。

另一個交付防錯已記入既有DEV-DELIVERY-001：current-dev/source worker docs仍有retired dev identity，
shared root的正確environment文件只是未提交user變更。該文件已在既有task artifacts內，無需另造task；
保留exact artifact rollback要求，禁止使用退休VM或自行假定hostname，部署前以現行operator規則和
真實hosted/GitHub identity重核。本次未探測任何退休host、未部署。

後續同一正式 Overlay task / PR5618 交付與 review流程保持；本操作不構成 repo change 完成交付。
