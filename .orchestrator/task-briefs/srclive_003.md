# Task Brief: SRCLIVE-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Crypto CoinGecko 連接器新建 + 接線
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Helper-claimed by Codex2 while Claude is dispatch-paused.

## Summary
persona-crypto 目前 1/2(kraken=datasource_smoke_ok),coingecko=read_unavailable 釘在離線 smoke。source_ingestion 完全沒有 CoinGecko 連接器→必須新建。工作:(1) 新增 connectors/crypto_coingecko.py:CoinGeckoSpotMarketAdapter(connector_id 'crypto-coingecko-spot',CoinGecko public API /api/v3,免 key,正規化日 OHLC/價格到 source record schema,遵循 SourceConnectorProvider 介面與既有 us_public 連接器同風格);(2) 在 connectors/__init__.py 匯出註冊;(3) active_universe 加 SourceUpdateRule;(4) BFF _SOURCE_PROVIDER_CONNECTOR_CANDIDATES 加 coingecko→crypto-coingecko-spot;(5) 觸發一輪真實 ingest run 並確認 snapshot status:ok;(6) 連接器單元測試(正規化+evidence packet)。kraken 不動。

[設計規則] 唯讀疊加層 _overlay_source_health_truth 是真相來源:provider 翻 read_ok 的唯一合法路徑是(1) BFF _SOURCE_PROVIDER_CONNECTOR_CANDIDATES 有 provider_key→connector_id 對照,且(2) source-ingest /api/source-ingest/health-usage-snapshot 回報該 connector status:ok。嚴禁硬寫 read_ok 或假綠;沒有即時健康就誠實顯示 credential_unavailable / read_unavailable 並附 reason。
