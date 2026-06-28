# Task Brief: SRCLIVE-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: US 研究源真實抓取 driver (Yahoo替Stooq + SEC + FINRA + FRED-keyed)
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Helper-claimed by Codex2 while Claude is dispatch-paused.

## Summary
US 研究源做不到 read_ok 的真因已逐一在 dev VM 實測查清(非接線問題):(1) Stooq 把下載端點藏在 JavaScript anti-bot 牆後,curl/urllib 只拿到 JS 挑戰頁或 404,永遠拿不到 CSV→必須換源,已驗證 Yahoo chart API(https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=...)從本 VM 正常回真資料、免 key;(2) FRED 無 key 的 fredgraph.csv 路徑從本 VM timeout,但 keyed API host api.stlouisfed.org 反應快可用→需配 FRED API key(orchestrator 另外提供 secret);(3) SEC EDGAR adapter 需要 CIK 逐家解析 driver(fetch_company_tickers→fetch_submissions→records_from_payload),目前 request 空→failed;(4) FINRA adapter 需要先抓當日 short-volume 檔的 driver(fetch_short_volume_text(trade_date)),要挑最近有效交易日。工作:A. 新增 us-yahoo 每日 OHLCV connector(取代 disabled 的 stooq,provider_owned_adapter,正規化到 us_price_daily schema)並接 BFF _SOURCE_PROVIDER_CONNECTOR_CANDIDATES['stooq' 或新 key];B. 寫 SEC、FINRA 的真實多步抓取 driver,讓 /api/source-ingest/jobs 能跑出真 rows;C. 把 FRED adapter 接 keyed API(adapter_config.secret_ref 指到 GCP secret FRED_API_KEY),orchestrator 配 key 後即可活化;D. 每個連接器附單元測試 + 更新 docs/05/srclive/us-activation-runbook.md 用正確端點/參數(現有 runbook 寫了不存在的 /run 端點要修正)。Polygon/AlphaVantage 維持 credential_unavailable(無付費 key)。完成定義:live curl persona-us-equity 顯示 ibkr + yahoo + sec + finra + fred = read_ok(FRED 待 key),polygon/alphavantage = credential_unavailable;不得假綠。
