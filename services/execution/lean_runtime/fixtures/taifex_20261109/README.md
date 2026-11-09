# TAIFEX 2026-11-09 DailyMarketReportFut CSV fixture

Captured from `https://openapi.taifex.com.tw/v1/DailyMarketReportFut` on
`2026-11-09` before the Monday close. The live endpoint serves `text/csv`
prefixed with a UTF-8 BOM (`\xef\xbb\xbf`) and Chinese column headers:

`日期,契約代號,到期月份(週別),開盤價,最高價,最低價,最後成交價,漲跌價,漲跌%,合計成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量`

| File | Source URL | Captured (UTC) | Trade date in payload | Trimming applied |
|---|---|---|---|---|
| `DailyMarketReportFut.csv` | https://openapi.taifex.com.tw/v1/DailyMarketReportFut | 2026-11-09 | `日期=20261106` | UTF-8 BOM preserved; rows trimmed to contracts `SRF`, `QFF`, `NYF`, `DHF`, and `CDF` for underlyings `0050`, `2330`, and `2317`, keeping regular-session (`一般`), after-hours (`盤後` with `結算價=NULL`), and calendar spread (`202611/202612`) rows as served |

Pair this file with the margin (`SingleStockFuturesMargining.json`,
`SingleStockFuturesETFMargining.json`), `FinalSettlementPriceSSF.json`, and
`stockLists.html` fixtures under `../taifex_20261106/`.
