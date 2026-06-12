from __future__ import annotations

from services.source_ingestion.connectors.yahoo_taiwan import (
    AnueTaiwanRssAdapter,
    YahooTaiwanBrokerTopAdapter,
    YahooTaiwanRssAdapter,
    parse_yahoo_broker_trading_html,
)
from services.source_ingestion.external_sources import validate_external_source_record


BROKER_HTML = """
<html>
  <body>
    <section>
      <span>資料日期:2026/06/08</span>
      <h2>買超券商</h2>
      <span>買進</span><span>賣出</span><span>買超張數</span>
      <span>國泰綜合</span><span>4,354</span><span>335</span><span>4,019</span>
      <span>凱基台北</span><span>1,200</span><span>200</span><span>1,000</span>
      <h2>賣超券商</h2>
      <span>買進</span><span>賣出</span><span>賣超張數</span>
      <span>摩根大通</span><span>200</span><span>2,000</span><span>1,800</span>
      <span>美林</span><span>100</span><span>900</span><span>800</span>
    </section>
  </body>
</html>
"""


RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Yahoo Taiwan Stock</title>
    <item>
      <title>2330 台積電主力買超擴大</title>
      <link>https://tw.stock.yahoo.com/news/2330-test</link>
      <guid>2330-test-guid</guid>
      <pubDate>Mon, 08 Jun 2026 06:30:00 GMT</pubDate>
      <description>台股新聞摘要。</description>
    </item>
  </channel>
</rss>
"""


ANUE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Anue Cnyes</title>
    <item>
      <title>台積電 (2330-TW) 盤中買盤升溫</title>
      <link>https://news.cnyes.com/news/id/2330-test</link>
      <guid>anue-2330-test-guid</guid>
      <dc:date>2026-06-08T14:40:00+08:00</dc:date>
      <description>台股即時新聞摘要。</description>
    </item>
  </channel>
</rss>
"""


def test_parse_yahoo_broker_trading_html_returns_buy_and_sell_top_rows() -> None:
    rows = parse_yahoo_broker_trading_html(
        "2330",
        BROKER_HTML,
        source_url="https://tw.stock.yahoo.com/quote/2330/broker-trading",
        available_time="2026-06-08T06:00:00Z",
        max_rank=2,
    )

    assert len(rows) == 4
    assert rows[0]["date"] == "2026-06-08"
    assert rows[0]["side"] == "buy"
    assert rows[0]["rank"] == 1
    assert rows[0]["broker"] == "國泰綜合"
    assert rows[0]["net_qty"] == 4019
    assert rows[2]["side"] == "sell"
    assert rows[2]["net_qty"] == -1800


def test_yahoo_broker_adapter_emits_market_source_records() -> None:
    adapter = YahooTaiwanBrokerTopAdapter(max_rank=2)
    connector = adapter.connector()
    records = adapter.records_from_html(
        "2330",
        BROKER_HTML,
        source_url="https://tw.stock.yahoo.com/quote/2330/broker-trading",
        available_time="2026-06-08T06:00:00Z",
        trace_id="trace-yahoo-broker-test",
    )

    assert connector.source_type.value == "market"
    assert connector.metadata["dataset"] == "tw_broker_top"
    assert connector.metadata["source_priority"] == "fallback"
    assert connector.metadata["fallback_for_connector_id"] == "tw-finmind-broker-daily-report"
    assert connector.metadata["active_universe_tiers"] == ["core_universe", "candidate_universe"]
    assert connector.metadata["archive_behavior"] == "skip"
    assert connector.metadata["max_rank_policy"]["default_max_rank"] == 2
    assert connector.metadata["max_rank_policy"]["full_branch_storage_allowed_by_default"] is False
    assert len(records) == 4
    assert records[0].connector_id == "tw-yahoo-broker-top15"
    assert records[0].metadata["dataset"] == "tw_broker_top"
    assert records[0].metadata["schema_hash"] == "tw_broker_top.v1"


def test_yahoo_rss_adapter_emits_pit_valid_news_records() -> None:
    adapter = YahooTaiwanRssAdapter()
    connector = adapter.connector()
    records = adapter.records_from_rss(RSS_XML, feed_url="https://tw.stock.yahoo.com/rss", trace_id="trace-rss-test")

    assert connector.source_type.value == "news"
    assert connector.metadata["entitlement_tags"] == ["yahoo-tw-rss-public-metadata"]
    assert connector.metadata["archive_behavior"] == "skip"
    assert connector.metadata["raw_storage_policy"]["compression"] == "gzip"
    assert connector.metadata["raw_storage_policy"]["retention_days"] == 730
    assert len(records) == 1
    record = validate_external_source_record(records[0], connector=connector)
    assert record.metadata["event_time"] == "2026-06-08T06:30:00Z"
    assert record.metadata["available_time"] == "2026-06-08T06:30:00Z"
    assert record.metadata["symbols"] == ["2330"]
    assert record.metadata["external_source_policy"] == "news_social_alpha_pit_v1"
    assert record.metadata["full_text_allowed_by_default"] is False
    assert record.metadata["full_text_stored"] is False
    assert record.metadata["dedupe_fields"] == ["provider", "source_url", "title_hash", "published_at"]


def test_anue_rss_adapter_emits_summary_only_news_metadata_records() -> None:
    adapter = AnueTaiwanRssAdapter(feed_url="https://feeds.example.test/anue.xml")
    connector = adapter.connector()
    records = adapter.records_from_rss(
        ANUE_RSS_XML,
        feed_url="https://feeds.example.test/anue.xml",
        trace_id="trace-anue-rss-test",
    )

    assert connector.source_type.value == "news"
    assert connector.metadata["entitlement_tags"] == ["anue-tw-rss-public-metadata"]
    assert connector.metadata["summary_only_default"] is True
    assert connector.metadata["archive_behavior"] == "skip"
    assert connector.metadata["raw_storage_policy"]["retention_policy_ref"] == (
        "market-data://raw-retention/tw-news-metadata-2y"
    )
    assert len(records) == 1
    record = validate_external_source_record(records[0], connector=connector)
    assert record.connector_id == "tw-anue-news-rss"
    assert record.metadata["event_time"] == "2026-06-08T06:40:00Z"
    assert record.metadata["available_time"] == "2026-06-08T06:40:00Z"
    assert record.metadata["symbols"] == ["2330"]
    assert record.metadata["summary"] == "台股即時新聞摘要。"
    assert record.metadata["full_text_allowed_by_default"] is False
    assert record.metadata["full_text_stored"] is False
    assert record.metadata["dedupe_key"]
    assert record.metadata["external_source_policy"] == "news_social_alpha_pit_v1"
