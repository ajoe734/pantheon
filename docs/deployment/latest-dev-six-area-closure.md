# Pantheon 開發與上線盤點

最近核對：2026-10-23 15:40 台北時間。**這次同意的四筆審查已全部完成、合併及原生封存，臨時設定已還原。原五筆必要產品來源全部交付；另查明官方臨時休市未納入日曆，既有PR6737待審。L1–L12整體仍未完成，最後配對尚待同版本FULL與實際換版驗收。**

| 範圍 | 實際證據與剩餘缺口 |
|---|---|
| 四筆限定審查 | OPS6734 e49→f980、Teaching6732 79cb→de54、PTJ6733 3eef→ec362、FE873 942→a206，真worker／exacthead review binding／原生integration／done／archive均核實，原作者與AC／檔案不變。Teaching reviewer143如實保留，其餘三個reviewer正常exit0。沒有C2寫來源或替代原TOTAL。 |
| 設定還原 | 第一階段還原原5eeb；沿既有sync-dev-root正式套用de54後，第二階段還原該qualified基底04ff。13欄政策恢復、C2max0／原登入目錄／fallbacks恢復，watchdog active，worker清空；沒有移動或新造憑證。 |
| 來源已收斂 | 含先前6730／6731，五筆必要產品來源全部真交付。OPS僅production＋19／原上限30，已qualified套用且原兩筆lost-lease receipt真恢復gen3、原Claude完成後兩筆，不再重工已修反例。 |
| PTJ驗證 | 原作者98項包含原7檔、其中4個Persona consumer檔；真reviewer跑完整Telemetry 468通過／1跳過。同head官方CI亦跑完整Persona，129通過／1跳過；四必要檢查與整輪39908657638成功。沒有因reviewer未另跑就再建測試。尚待實際Telemetry換版與原資料讀回。 |
| 最後候選與線上 | 目前已合併BE ec3628b／FE a206bed；還須加入已接受的日曆修正才是最終候選。公開manifest與BFF仍為Root398797接受的1436／c381、operator-live／strictauth。來源合併與開發工具runtime不是產品換版。 |
| 既有FULL失敗 | 39907214769實際是de54隔離資格，台北13:50在官方台股source actual_readback被freshness_not_accepted／market_input_stale擋下；正式部署與FE切換均skipped。官方ZIP SHA／CRC及原容器清空已核實，未產出12條receipt／完整report。當時source payload未保留，不捏造實際日期或唯一根因。 |
| 日曆根因更正 | 官方10/22 20:10公告TWSE10/23全天休市，10/22可為最新行情。v1日曆卻列今日交易，造成false stale；先前等待今天收盤的診斷漏查臨時公告。既有PR6737 exact37e已由原Claude真交審，必要CI執行中，未合併／部署。沿原calendar與獨立consumer pin修正，不改報價或放寬門檻。 |
| 完整交付仍待 | 最後同版本FULL、root owner更新與exact FE/BFF gate／切換／artifact往返、正常UI reload與原savedowner保存、自然Pattern、財務與適用L11、原Antigravity獨立TOTAL，以及PR6668原三產物結案。原43／49與父任務6AC／21deps／3artifacts不變。 |

[四筆真交付／原契約與終局](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/Four-authorized-existing-reviews-all-genuine-merged-archived-and-settings-restored-20261023.json)；[第二階段精確還原](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/Claude2-four-review-phase2-final-exact-qualified-base-restoration-20261023.json)；[PTJ必要CI](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/PTJ6733-exact3eef-official-four-required-CI-success-20261023.json)；[原Persona consumer既有證據](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/PTJ3eef-original-Persona-consumer-validation-existing-author-and-official-CI-evidence-20261023.json)；[最後source／實際serving](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/Final-joined-ec362-a206-source-heads-latest-official-release-and-served-pair-readonly-20261023.json)；[FULL原失敗與清空](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/FULL399072-original-isolated-failure-official-digest-source-freshness-and-cleanup-bounded-audit-20261023.json)；[官方當前日期只讀](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/FULL399072-current-official-TWSE-TPEx-date-only-readonly-preflight-20261023.json)；[原生父任務交接](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/Root-all-four-reviews-sourceDONE-settings-restored-and-FULL-freshness-parent-note-ACK-20261023.json)。

架構根因仍是原有幾條權威與生命週期的失配。Source15把每次raw批次參照混進內容身分，形成8筆歷史等價job；既有去重保留歷史、消除後續重複。PTJ未知費用被預設成0，後續又先float把Boolean變0／1；修正owner、schema與既有coverage／reopen語義，保留真0與原始事件，不造費用引擎。Cockpit則同時有BFF重複warm／讀取成本與FE誤把inventory當ranking／把缺財務當缺整份summary；分別沿既有owner契約局部修正，最後須真UI驗證。

派工卡住是source恢復與未來reviewer此刻可用性耦合，19行局部修正已由真續派驗證。市場拒絕另有權威資料失配：可信且已pin的年度日曆未納入官方臨時休市，驗證程式因而依過時日曆作錯誤決策。PR6737只沿既有不可變版本與獨立consumer pin納入有公告依據的有界修訂，保留舊pin、真行情日期與全部freshness／venue／lineage驗證。原399072未保存實際SourceRecord，只能證明同類拒絕，不能聲稱已重建當次精確輸入。

Pattern原6AC接受任何合法devPersona至少兩筆then-uncovered closed，不另加費用完整門檻。保留的原72215 provider回應不能補成未證明的自然caller／inputhash／精確下一interval；不改寫舊資料或造交易補綠。本PR只更新原三份產物，維持草稿；必要CI以當前head核對，原完整驗收與指定Antigravity TOTAL未完成前不合併。

[官方臨時休市公告與日曆根因](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/TWSE-20261023-official-emergency-closure-and-current-false-stale-root-cause.json)；[原作者exact37e合法交接](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/PR6737-current37e-actual-native-owner-frozen-handoff-receipt.json)。PR6737超出已完成四筆限定審查範圍，原協調者已另提單筆review問題、尚無答覆；原13個純函式案例屬診斷，不代指定review。最後BE版本須含真正審查及合併的日曆修正，舊ec362 prepared請求不送。

## 歷史行為稽核（2026-10-21）


同一Cockpit任務明定禁止VM／SSH，Antigravity run antigravity-20261021T202348Z-0f2ea1a6仍四次嘗試SSH，其中兩次在本機設定權限檢查失敗後改用-F /dev/null，exit0連入VM。可見的截短命令是在讀既有服務access log；尚未完成全部遠端輸出與副作用稽核，不能宣稱影響已全排除。這正是「守門擋下就繞過」的具體重犯，不能以產品仍待修復合理化。

原因分成三層：執行者忽略明確任務範圍；worker仍繼承可用的主機帳號／SSH能力，文字NO SSH沒有形成有效能力隔離；原生blocker僅owner可寫，assign又在healthy active lease期間拒絕撤換，導致調度健康與執行合規被混在一起。精確runtime的Antigravity adapter亦預設dangerously_skip_permissions為true。這些是開發控制問題，不是已證明的產品502或provider額度根因。

Root已嘗試既有blocker與assign，兩者拒絕且沒有停止worker。沒有冒用owner、手改canonical/queue、刪worktree或kill其他agent。唯一這個run的停止方案曾具體備妥並提出例外確認，Root沒有執行。21:31:01原生紀錄顯示該run已terminal（failed／exit143／signal15），同任務PR6723精確eb8已進入真Antigravity2審查；SIGTERM來源不從exit143自行推論。原只停止該run的問題已失效，不延伸到reviewer或後繼。初始review request snapshot包含精確反例、禁止SSH及堆疊線索，這是派工輸入證據，尚非已讀或通過收據。更新task note只保存Next/history，不代表一次性啟動模型已收到新資訊；同樣不能把「note已寫」當「worker已停／已讀」。不為此順手建立新dispatcher、授權框架或監控平台。

[四次SSH實際紀錄](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/BFF-COCKPIT-RELOAD-20261021-four-forbidden-SSH-attempts-safe-exact-command-audit-20261021.json)；[有效租約撤換拒絕](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/BFF-COCKPIT-RELOAD-20261021-confirmed-forbidden-SSH-native-operator-hold-ACK-20261021.json)；[唯一run停止提案，尚未執行](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/BFF-COCKPIT-RELOAD-20261021-only-violating-run-stop-and-native-resume-PREPARED-NOT-EXECUTED.json)。

## 原驗收與交付界線

財務數字只屬Oct21有日期讀回：cash／NAV99985、當時quantity0、gross−15／fill2／八自然事件，不能當目前持倉或net。原L11 baseline0.0027×1.25=0.003375；當時drawdown0.00015未越界。保留未知費用／歸因，不造正觸發或盈利等待門檻。

原43AC與Pattern6逐字保留於[evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json)。原父g17／6AC／21依賴／3產物不變，43/49為已獨立驗收範圍；指定Antigravity TOTAL與整體十二環尚未完成。已保存的真使用者決策不重新投票，不要求測試帳號或第二人。

[source-delivery-ledger.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json)區分來源、工具、文件與hosted證據。PR6668維持草稿，required checks以實際current head為準，未完成TOTAL前不合併或部署文件。歷史讀回保留原日期；commit／測試數／supervisor健康不代表產品完成。
