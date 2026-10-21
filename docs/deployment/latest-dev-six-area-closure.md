# Pantheon 開發與上線盤點

最近核對：2026-10-21T15:36:26.966206+00:00。**唯一正式Root39619749022失敗，新版368b84／1cfa未接受。BFF雖已回舊23f，實際readyz仍503；補償未完整成功。原Publisher已恢復ACTIVE、既有整合鎖已釋放。原49／all12／TOTAL仍未完成。**

## 這次發布失敗的架構根因

同一筆市場輸入在既有producer產生相同signal／decision／run ID，卻在不同tick產生新的隨機journey envelope。PostgreSQL投影要求一個decision只能歸屬一個journey，於checkpoint7511939拒絕第二個歸屬，controller失敗並令BFF `/readyz` 回503。15:32實讀與正式log一致；最近一次live success為12:47，早於14:46本次發布。舊23f與候選368b的producer及projector檔案SHA完全相同，不能把這個缺陷歸因於6716的平倉顯示修正。

這是producer重試身分與持久投影唯一性契約不一致。已寫入的衝突retry不會因回舊image消失，故延長readiness等待、重啟、放寬gate或另加健康寫入者不能完成恢復。原生任務LIFECYCLE-DECISION-JOURNEY-CONFLICT-20261021／PR6719已由Claude處理，Antigravity2獨審；修正須同時保持新retry身分穩定與既有衝突紀錄的誠實恢復，保留真正內容衝突拒絕，不刪事件、不重綁原業務ID、不偽造accepted-live。本對話不重複改worker的源碼。

## 發布、補償與證據的界線

同版本隔離FULL39615739197已核對14checks／10joins／12ordered receipts與清理0。隔離新環境通過，不等於長時間運行且累積重試的真實資料能通過。正式run於後端readiness失敗，FE gate／switch及精確往返未執行；old-leg觀測0次，不主張本次三階段資料保存已驗。

補償step的GitHub結論是success，原因是既有continue-on-error；原始log實際exit75並明載rollback incomplete，沒有成功補償artifact。BFF版本讀回23f，公開FE仍a701／舊Root395649接受manifest，但readyz仍503。歷史accepted標籤不等於當前可用。Publisher已於15:31:04恢復ACTIVE，原整合鎖於15:31:53釋放；沒有第二次Root POST。[官方失敗與持久身分根因核對](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/Root39619749022-official-failure-and-persistent-identity-root-cause-bounded-audit-20261021.json)。

## 原任務交付與剩餘驗收

Capital5、Research5（PR6715）、FE審批6（PR864）已由原指定reviewer核對並原生結案。Persona6的PR6717已真獨審、必要CI通過、合併0bb5cf2e並原生結案；四個原checkpoint共22條已獨立核對。原Agora8與Evaluator13已由兩個supervisor worker各自只備原evidence文件，仍待真正接受版本與同一資源UI／owner補證，未標完成。原角色、條文、依賴與產物不改。

6716的具體缺陷是Telemetry明確positions=[]／position_count=0時，BFF把「已知零持倉」誤當缺資料，製造unknown holding及MISSING_HOLDING_ROW。原局部修正已合併368b、61 tests與真獨審通過，保留coverage／filter／stale診斷，產品淨增30行；尚未線上交付。既有6710估值修正在此前23f已交付，沒有新增endpoint、store或第二套財務引擎。

原0040已自然SELL1@2405、BUY1@2420平倉；8真事件／7因果邊、官方Source ingest與snapshot、6對帳0drift和持久ledger已對上。signed Decimal gross−15／cash99985／drawdown0.00015，不能偷換canonical episode仍為null的淨績效；coverage partial、missing opened_event與attribution保留。當前同Persona僅1 closed episode，原Pattern minimum2仍不足。L11原基準0.0027×1.25＝0.003375，尚未自然觸發，不製造breach或新交易。

目前Evaluator原自然14:30保存建議與原Management a785（實際OpenAI/gpt-5.6-sol）、Research／Agora／L6／L7／Persona／F12保存結果都沿既有讀回驗，不重跑耗用的許可。原父6AC／21依賴／3文件、43AC＋Pattern6、指定Antigravity TOTAL與PR6668均保留；來源修改、測試、真審查、合併、部署、使用者流程驗收分開記錄。

## 原驗收與證據

原43AC與Pattern6逐字保留於 [evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json)。其中原Capital5／Research5／FE6／Persona6，共22條已有指定獨立checkpoint驗收及正式結案；其餘仍未標完成，這不是原指定TOTAL總驗收。父g17六AC／21依賴／三產物保持，原PR6668仍為草稿。

[source-delivery-ledger.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json) 分開記錄source交付、hosted與工具／文件範圍。原歷史完整保留於Git，不將commit數、測試數或清理返工當功能交付。
