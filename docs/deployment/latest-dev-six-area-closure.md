# Pantheon 開發與上線盤點

更新：2026-10-21T01:53:58.102500+00:00（UTC）。原交付任務仍未結案，本文件及 PR6668 保持草稿。

最後正式接受的是 BE `525bb5c`／FE `a7012154`。精確換版／回滾往返及原資料保存已完成；Cockpit 的追加修復已合併為 `3f91b1c`，唯一正式發布 `39527250552` 正在執行。原自然 Pattern、四筆 hosted 驗收、適用十二環、指定獨立總驗收與文件交付未全部完成。

| 範圍 | 已證實 | 尚缺 |
|---|---|---|
| 正式軟體接受 | BFF395179／FE395206成功，18項舊684e→同一525 artifact往返；原Publisher恢復 | 新3f91的整次正式接受、精確往返及Publisher恢復 |
| 當前換版 | 唯一BFF395272，後端118984703180成功；FEgate395279 workflow成功，原raw範圍待核；FEdeploy395297執行中。沒有第二次POST或新paper | 沿同一run終局；不因觀察逾時重開 |
| Cockpit | 原A2確已檢查current60及46項回歸／39架構測試，四requiredCI通過並合併；候選真正常頁25項診斷通過，active runtime=1、0040保留 | 診斷早於正式接受，不能充最終pair畫面驗收 |
| 原保存資料 | 原25=21HTTP200+4 SDK成功（HTTP null）；完整22業務內容在前／實際舊leg／返回後保留，只差精確ISO觀察時間。原Incident20owner欄、Capital16／PCB19及持倉真畫面重載一致 | 新3f91最終同ID讀回、原衝突／auth／owner持久性條件及獨立覆核 |
| Management | 原412及另一執行者a785實際回覆保存／重載／重新登入／登出結果保留；a785實際為OpenAI/gpt-5.6-sol | 只讀回同一保存session，不再次呼叫模型；不把曾經額度問題當當前原因 |
| L11 | 原0.0027提案與當前0040真策略／參數／規模對齊；PR6699由既有ReviewerOnDuty明確批准並合併，原設定啟用，僅重啟原producer。01:15:50自然tick coverage1/1、errors0 | 真drawdown0產生candidate0/incident0正確；適用breach／冷卻／下游尚未證，不強造正向 |
| 自然Pattern | 全tenant真GET完整：78 Persona、32 episodes（30open／2closed）；兩closed已覆蓋，沒有滿足原minimum2的pending set | 真自然正向及其緊接下一interval去重。歷史13:37回覆語意已找回，inputhash／自然caller仍缺證據 |
| paper財務 | 保留原0040：SELL1@2405、cash102405、qty−1、NAV100000／grossPnL0，8因果事件、6對帳且0drift；Capital／PCB／UI已對齊 | 一個績效點仍有範圍限制。API mark／market value／unrealized PnL／currency為null、UI為—；不虛填零、費用或淨報酬，也不加20天／獲利門檻 |
| 舊案清理 | 原失敗補償及DLQ已結清；85退役、1個原0040保持current | 不重播或退役目前案例 |
| 原總驗收 | 原43AC+Pattern6、父g17六AC／21依賴／3文件完整保留 | 四個hosted checkpoints、指定Antigravity獨立TOTAL與原PR6668完成交付 |

## 架構根因與本輪處理

**交付真相曾被不同層的狀態混用。** Git commit、worker/native DONE、隔離測試、BFF版本、前端manifest與真人可操作流程是不同證據。真正的業務狀態由Research／Persona／Capital等owner保存，BFF讀模型與UI是投影；TaskStore／supervisor負責開發派工，發布控制器負責精確artifact切換。本輪保留這些邊界，以正式manifest、同ID完整owner內容及實際瀏覽器結果判定，不以工具健康或測試數結案。

**Cockpit原問題在聚合邊界。** 外層逾時會丟掉已完成的owner區段；另一個狀態名稱錯配只認running而忽略canonical active，將實際運行中案例顯示為0。原修復重用現有格式化與單次request資料，保留真已完成區段、明示未完成區段及partial狀態，並對齊既有狀態語意。沒有加長timeout、跨request快取、另一個健康寫入者或第二個服務。候選的正常頁實測已改善，正式接受後仍須驗收。

**反覆重建驗收案例妨礙跨時間閉環。** 新Root建立新artifact／binding／runtime後，又退役前案，會中斷同一案例累積自然closed episodes，也使artifact-specific基準重新缺失。目前實際32episodes僅2closed且已覆蓋，無法用重跑測試製造新的自然前提。本輪BFF-only修復保留原0040，讓同一真paper案例繼續累積；不另造episode、假交易或重播模型。

**L11缺的是政策設定接入，當時不是Telemetry數值。** 真輸入的numeric drawdown、binding歸屬及freshness皆有效，原baseline map卻缺當前artifact。研究提案不等於生效政策；完成原ReviewerOnDuty明確決策後，只將同artifact的0.0027加入既有map，按原producer retune啟用。真startup tick已可評估；零breach不冒充正向下游完成。

**記錄不完整又造成了本助手的返工。** 我只看見舊b9審查文字，漏讀原reviewer對current60的實際46PASS與最終結論，先留下疑慮，之後又在PR已合併後重開工單。現在已用既有supersede撤出這次重複工作，完整保留真審查與merge；原生status done搭配terminal_outcome superseded表示撤銷，不能當產品完成。剩餘驗收仍歸原父工單，沒有另加proof／reviewer／派工平台來掩蓋錯誤。

## 原驗收範圍與執行邊界

[evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json) 保留原43AC及Pattern6逐字條件，全49項獨立驗收完成0/49，原條件按原工單分組與AC順序保留；本稿沒有代替指定審查者下結論。L1–L12、原父工單六AC／21依賴與三份文件均未縮減。

有限Research／Agora／L6／L7／Persona／F12許可已耗用，保留原真結果，後續以同ID讀回驗證。一次真正使用者決策不擴張成其他操作許可，也不要求測試帳號登入或第二位真人。失敗、缺資料、null、逾時與partial原樣呈現；不把人工拼接、fixture或程式成功碼冒充provider或owner結果。

## 接續順序

1. 完成唯一BFF395272、舊525 artifact回復及返回同一3f91，整次終局先恢復Publisher原ACTIVE。
2. 在正式接受pair核正常Cockpit、同ID UI／owner資料及原四hosted條件；依原保存證據驗衝突／auth／tenant／重啟範圍，不再執行耗用的業務動作。
3. 保持原0040與已批准設定；保留自然Pattern及適用L11正向缺口。原最終同版本FULL義務仍在；歷史525 FULL不改標3f91，也不把額外provider測試當這次BFF修復的新前置gate。
4. 逐項對齊原49／L1–L12、交指定Antigravity獨立TOTAL，再完成原三份文件與PR6668。文件清理是返工，不算產品功能交付。

## 證據與歷史

主要證據路徑、原件SHA256與source交付列於[evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json)及[source-delivery-ledger.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json)。原32ee15b報告完整保留於Git歷史；本稿移除重複巢狀的過時current描述，不刪原驗收條件或把歷史採樣當現況。
