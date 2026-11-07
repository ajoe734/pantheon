# TAIFEX single-stock-futures fixtures, Data Date 20261106

Captured by TAIFEX-SSF-CONTRACT-CANDIDATES-20261107 by public HTTPS GET on
2026-11-07 (read time about 15:47 UTC). Every OpenAPI row carries `Date` 20261106
(the Friday regular session). No credentials, no broker, no VM involved.

| File | Source URL | Capture |
| --- | --- | --- |
| `SingleStockFuturesMargining.json` | https://openapi.taifex.com.tw/v1/SingleStockFuturesMargining | rows filtered to contracts CAF CDF DHF QFF (JSON as served, `Accept: application/json`) |
| `SingleStockFuturesETFMargining.json` | https://openapi.taifex.com.tw/v1/SingleStockFuturesETFMargining | complete response. Today it was served as JSON without BOM; the connector also decodes the CSV-with-BOM form (tested from a derived CSV built in the test) |
| `DailyMarketReportFut.json` | https://openapi.taifex.com.tw/v1/DailyMarketReportFut | rows filtered to contracts CAF CDF DHF NYF QFF SRF, all sessions and spread rows kept as served |
| `FinalSettlementPriceSSF.json` | https://openapi.taifex.com.tw/v1/FinalSettlementPriceSSF | rows filtered to the same contracts |
| `stockLists.html` | https://www.taifex.com.tw/cht/2/stockLists | page table header plus the rows for families CA CC CD DH NY QF SR, markup unchanged |
| `feeSchedules.txt` | https://www.taifex.com.tw/cht/4/feeSchedules | page text of section 一 (交易及結算相關費率), tags stripped; used only by the drift test |

Filtering only removes rows; no value was edited. The openapi
`FuturesAndOptionsFeeSchedule` row is deliberately not captured: its STF and STO
tax columns are swapped relative to the page.

Values the tests rely on: QFF 202611 settlement 2133 volume 126386; DHF 202611
settlement 225 volume 14260; SRF 202611 settlement 92.35 (regular session);
margin tier 1 10.00 / 10.35 / 13.50 percent; SRF fixed 6400/6700/8700; NYF fixed
64000/67000/87000; units CD 2000, QF 100, DH 2000, NY 10000, SR 1000.
