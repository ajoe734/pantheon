"""Yahoo Taiwan market-data adapters.

The adapters intentionally model Yahoo Taiwan as a low-cost public-web summary
source. They preserve provenance and entitlement metadata for research use, but
do not treat Yahoo as the official truth source for filings or exchange data.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceMetadata,
    SourceRecord,
)


YAHOO_TW_BROKER_TRADING_URL_TEMPLATE = "https://tw.stock.yahoo.com/quote/{symbol}/broker-trading"
YAHOO_TW_STOCK_RSS_URL = "https://tw.stock.yahoo.com/rss"
BROKER_TOP_SCHEMA_HASH = "tw_broker_top.v1"
RSS_NEWS_SCHEMA_HASH = "tw_yahoo_rss_news.v1"

_DATE_RE = re.compile(r"(?:資料日期|日期)\s*[:：]?\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
_INT_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)$")
_TW_SYMBOL_RE = re.compile(r"(?<!\d)(\d{4})(?:\.(?:TW|TWO))?(?!\d)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _parse_int(value: Any) -> int:
    text = _clean_text(value).replace(",", "")
    if text in {"", "-", "--"}:
        return 0
    return int(text)


def _is_int_token(value: Any) -> bool:
    return bool(_INT_RE.match(_clean_text(value)))


def _iso_date(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _date_from_available_time(value: str | None) -> str:
    text = _clean_text(value)
    if not text:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return text[:10]


class _TextTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_data(self, data: str) -> None:
        text = _clean_text(data)
        if text:
            self.tokens.append(text)


def _html_text_tokens(html_text: str) -> list[str]:
    parser = _TextTokenParser()
    parser.feed(html_text)
    return parser.tokens


def _extract_trade_date(tokens: Sequence[str]) -> str | None:
    joined = " ".join(tokens)
    match = _DATE_RE.search(joined)
    if match:
        return _iso_date(match.group(1), match.group(2), match.group(3))
    for index, token in enumerate(tokens[:-1]):
        if token in {"資料日期", "日期"}:
            next_token = tokens[index + 1]
            match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", next_token)
            if match:
                return _iso_date(match.group(1), match.group(2), match.group(3))
    return None


def _find_token(tokens: Sequence[str], needle: str) -> int | None:
    for index, token in enumerate(tokens):
        if token == needle or needle in token:
            return index
    return None


def _parse_broker_section(
    tokens: Sequence[str],
    *,
    start_at: int,
    side: str,
    trade_date: str,
    symbol: str,
    source_url: str,
    available_time: str,
    max_rank: int,
    stop_tokens: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    headers = {"買進", "賣出", "買超張數", "賣超張數", "券商", "買超券商", "賣超券商"}
    index = start_at + 1
    rank = 1
    while index < len(tokens) - 3 and rank <= max_rank:
        token = tokens[index]
        if token in stop_tokens:
            break
        if token in headers or _is_int_token(token):
            index += 1
            continue

        buy_token = tokens[index + 1]
        sell_token = tokens[index + 2]
        net_token = tokens[index + 3]
        if not (_is_int_token(buy_token) and _is_int_token(sell_token) and _is_int_token(net_token)):
            index += 1
            continue

        buy_qty = _parse_int(buy_token)
        sell_qty = _parse_int(sell_token)
        net_qty = buy_qty - sell_qty
        rows.append(
            {
                "date": trade_date,
                "symbol": symbol,
                "source": "Yahoo Taiwan Stock",
                "side": side,
                "rank": rank,
                "broker": token,
                "buy_qty": buy_qty,
                "sell_qty": sell_qty,
                "net_qty": net_qty,
                "reported_net_qty": abs(_parse_int(net_token)),
                "available_time": available_time,
                "source_url": source_url,
            }
        )
        index += 4
        rank += 1
    return rows


def parse_yahoo_broker_trading_html(
    symbol: str,
    html_text: str,
    *,
    source_url: str | None = None,
    available_time: str | None = None,
    max_rank: int = 15,
) -> tuple[dict[str, Any], ...]:
    """Parse Yahoo Taiwan broker-trading HTML into normalized top broker rows."""

    symbol_text = _clean_text(symbol).upper()
    if not symbol_text:
        raise ValueError("symbol is required")
    available = available_time or _utc_now()
    tokens = _html_text_tokens(html_text)
    trade_date = _extract_trade_date(tokens) or _date_from_available_time(available)
    url = source_url or YAHOO_TW_BROKER_TRADING_URL_TEMPLATE.format(symbol=symbol_text)

    buy_index = _find_token(tokens, "買超券商")
    sell_index = _find_token(tokens, "賣超券商")
    if buy_index is None or sell_index is None:
        raise ValueError("Yahoo broker-trading page missing buy/sell broker sections")

    buy_rows = _parse_broker_section(
        tokens,
        start_at=buy_index,
        side="buy",
        trade_date=trade_date,
        symbol=symbol_text,
        source_url=url,
        available_time=available,
        max_rank=max_rank,
        stop_tokens={"賣超券商"},
    )
    sell_rows = _parse_broker_section(
        tokens,
        start_at=sell_index,
        side="sell",
        trade_date=trade_date,
        symbol=symbol_text,
        source_url=url,
        available_time=available,
        max_rank=max_rank,
        stop_tokens={"相關新聞", "熱門搜尋", "台股排行", "主力進出排行", "主力買超", "主力賣超"},
    )
    return tuple(buy_rows + sell_rows)


@dataclass(frozen=True)
class YahooTaiwanBrokerTopAdapter(SourceConnectorProvider):
    """Provider-owned adapter for Yahoo Taiwan top broker buy/sell summaries."""

    connector_id: str = "tw-yahoo-broker-top15"
    max_rank: int = 15
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="Yahoo Taiwan Stock",
            license_scope="public_web_summary",
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="public_web_summary",
                allowed_use=("research", "search_index", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/yahoo-tw-public-web-summary",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=20,
                burst=2,
                retry_after_seconds=90,
                concurrency=1,
                policy_ref="source-ingest://policy/yahoo-tw-courteous-web",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Yahoo Taiwan top broker trading",
                homepage_url="https://tw.stock.yahoo.com/",
                owner="Yahoo Taiwan Stock",
                tags=("taiwan", "yahoo", "broker_top", "public_web_summary"),
            ),
            metadata={
                "source_class": "public_web_summary",
                "dataset": "tw_broker_top",
                "source_profile": "broker_top15",
                "source_priority": "fallback",
                "fallback_for_connector_id": "tw-finmind-broker-daily-report",
                "history_depth": "latest_trading_day_only",
                "completeness": "top15_buy_sell_only",
                "active_universe_tiers": ["core_universe", "candidate_universe"],
                "expected_rows_per_symbol": self.max_rank * 2,
                "schema_hash": BROKER_TOP_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "YahooTaiwanBrokerTopAdapter.records_from_html",
            "url_template": YAHOO_TW_BROKER_TRADING_URL_TEMPLATE,
            "max_rank": self.max_rank,
        }

    def records_from_html(
        self,
        symbol: str,
        html_text: str,
        *,
        source_url: str | None = None,
        available_time: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        rows = parse_yahoo_broker_trading_html(
            symbol,
            html_text,
            source_url=source_url,
            available_time=available_time,
            max_rank=self.max_rank,
        )
        records: list[SourceRecord] = []
        for row in rows:
            key_payload = {
                "symbol": row["symbol"],
                "date": row["date"],
                "side": row["side"],
                "rank": row["rank"],
                "broker": row["broker"],
            }
            row_hash = _stable_hash(key_payload)
            content_ref = f"yahoo-tw://broker-top/{row['symbol']}/{row['date']}/{row['side']}/{row['rank']}"
            records.append(
                SourceRecord(
                    source_id=f"yahoo-tw-broker-top:{row['symbol']}:{row['date']}:{row['side']}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=(
                        f"Yahoo Taiwan broker top {row['side']} "
                        f"{row['symbol']} {row['date']} #{row['rank']} {row['broker']}"
                    ),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "public_web_summary",
                        "provider": "Yahoo Taiwan Stock",
                        "dataset": "tw_broker_top",
                        "source_profile": "broker_top15",
                        "source_priority": "fallback",
                        "fallback_for_connector_id": "tw-finmind-broker-daily-report",
                        "history_depth": "latest_trading_day_only",
                        "completeness": "top15_buy_sell_only",
                        "symbol": row["symbol"],
                        "trade_date": row["date"],
                        "event_time": row["date"],
                        "available_time": row["available_time"],
                        "side": row["side"],
                        "rank": row["rank"],
                        "broker": row["broker"],
                        "buy_qty": row["buy_qty"],
                        "sell_qty": row["sell_qty"],
                        "net_qty": row["net_qty"],
                        "source_url": row["source_url"],
                        "raw_row": dict(row),
                        "body": json.dumps(row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_web_summary",
                        "active_universe_tiers": ["core_universe", "candidate_universe"],
                        "schema_hash": BROKER_TOP_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)


def _rss_text(item: ET.Element, name: str) -> str:
    value = item.findtext(name)
    if value is None:
        return ""
    return _clean_text(value)


def _parse_rss_time(value: str | None) -> tuple[str, bool]:
    text = _clean_text(value)
    if not text:
        return _utc_now(), True
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), False
    except (TypeError, ValueError):
        return _utc_now(), True


def _symbols_from_text(*parts: str) -> list[str]:
    symbols: list[str] = []
    for part in parts:
        for match in _TW_SYMBOL_RE.finditer(part):
            symbol = match.group(1)
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


@dataclass(frozen=True)
class YahooTaiwanRssAdapter(SourceConnectorProvider):
    """Provider-owned adapter for Yahoo Taiwan stock RSS metadata."""

    connector_id: str = "tw-yahoo-stock-rss"
    feed_url: str = YAHOO_TW_STOCK_RSS_URL
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="news",
            provider="Yahoo Taiwan Stock RSS",
            license_scope="rss_metadata",
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="rss_metadata",
                allowed_use=("research", "search_index", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/yahoo-tw-rss-metadata",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/yahoo-tw-rss-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Yahoo Taiwan stock RSS",
                homepage_url="https://tw.stock.yahoo.com/",
                owner="Yahoo Taiwan Stock",
                tags=("taiwan", "yahoo", "rss", "news"),
            ),
            metadata={
                "source_class": "public_news_metadata",
                "dataset": "tw_news_metadata",
                "source_profile": "rss_metadata",
                "entitlement_tags": ["yahoo-tw-rss-public-metadata"],
                "access_scope": ["public", "research"],
                "active_universe_tiers": ["core_universe", "candidate_universe"],
                "schema_hash": RSS_NEWS_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "YahooTaiwanRssAdapter.records_from_rss",
            "feed_url": self.feed_url,
            "max_records": self.max_records,
        }

    def records_from_rss(
        self,
        rss_xml: str,
        *,
        feed_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        root = ET.fromstring(rss_xml)
        items = list(root.findall(".//item"))[: self.max_records]
        records: list[SourceRecord] = []
        for item in items:
            title = _rss_text(item, "title")
            link = _rss_text(item, "link")
            description = _rss_text(item, "description")
            guid = _rss_text(item, "guid") or link or title
            published_at, inferred_time = _parse_rss_time(_rss_text(item, "pubDate"))
            body = f"{title}\n{description}".strip()
            content_hash = _content_hash(body or guid)
            source_id = f"yahoo-tw-rss:{_stable_hash({'guid': guid, 'published_at': published_at})}"
            records.append(
                SourceRecord(
                    source_id=source_id,
                    connector_id=self.connector_id,
                    source_type="news",
                    title=title or "Yahoo Taiwan RSS item",
                    content_ref=link or f"rss://yahoo-tw/{source_id}",
                    metadata={
                        "source_class": "public_news_metadata",
                        "provider": "Yahoo Taiwan Stock RSS",
                        "publisher": "Yahoo Taiwan Stock",
                        "dataset": "tw_news_metadata",
                        "feed_url": feed_url or self.feed_url,
                        "source_url": link or None,
                        "guid": guid,
                        "published_at": published_at,
                        "event_time": published_at,
                        "available_time": published_at,
                        "published_at_inferred": inferred_time,
                        "symbols": _symbols_from_text(title, description, link),
                        "summary": description,
                        "body": body,
                        "content_hash": content_hash,
                        "body_hash": content_hash,
                        "license_scope": "rss_metadata",
                        "entitlement_tags": ["yahoo-tw-rss-public-metadata"],
                        "access_scope": ["public", "research"],
                        "schema_hash": RSS_NEWS_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)
