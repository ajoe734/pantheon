"""Scrapling web spiders for Threads, Instagram popular tags, CMoney, PTT, DGBAS, and TWSE/TPEx exchange website datasets (SD §5.1, §5.3, §6.2)."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlparse

from .real_parsers import (
    parse_cmoney_forum,
    parse_instagram_tag,
    parse_ptt_index,
    parse_threads_search,
)
from .bundle import CollectedItem, utc_iso
from .config import CollectorConfigError, SourceFetchPolicy
from .fetchers import (
    FetchTransport,
    ScraplingBrowserFetcher,
    ScraplingHttpFetcher,
    extract_html_elements,
)
from .privacy import AuthorHasher, scrub_raw_html_or_json


def _detect_tw_symbols(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:00\d{2,3}|[1-9]\d{3})\b", text or "")))


def _social_item(
    *,
    hasher: AuthorHasher,
    platform: str,
    item_id: str,
    url: str,
    fetched_iso: str,
    published_at: str,
    text: str,
    handle: str,
    display_name: str,
    record: dict[str, Any],
    metrics: dict[str, Any],
    http_status: int,
) -> CollectedItem:
    """Build a de-identified item from a parsed real-site record (author hashed, literals scrubbed)."""
    for literal in (display_name, handle):
        if literal:
            text = text.replace(f"@{literal}", "").replace(literal, "").strip()
    safe_record = {k: v for k, v in record.items() if k not in {"handle", "author", "display_name", "member_id"}}
    if "text" in safe_record:
        safe_record["text"] = text
    raw = scrub_raw_html_or_json(
        json.dumps(safe_record, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        platform=platform,
        hasher=hasher,
        handles=[handle],
        display_names=[display_name] if display_name else (),
    )
    return CollectedItem(
        item_id=item_id,
        url=url,
        fetched_at=fetched_iso,
        published_at=published_at,
        text=text,
        lang="zh-TW",
        hashed_author=hasher.hash_author(platform, handle),
        metrics={**metrics, "platform": platform},
        raw_bytes=raw,
        raw_extension="json",
        http_status=http_status,
        extra_fields={"symbols": _detect_tw_symbols(text)},
        forbidden_literals=tuple(x for x in (handle, display_name) if x),
        require_hashed_author=True,
    )


class ThreadsWebSpider:
    """Scrapling browser spider for logged-out Threads search first page (SD §5.3)."""

    source_id = "collector-tw-threads"
    skipped_count = 0

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        hasher: AuthorHasher,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingBrowserFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.hasher = hasher

    def collect(
        self,
        *,
        query: str = "台股",
        url: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        target_url = (
            url
            or f"https://www.threads.com/search?q={quote(query, safe='')}&serp_type=default"
        )
        if "page=" in target_url or "after=" in target_url or "cursor=" in target_url:
            raise CollectorConfigError(
                "Threads logged-out spider permits first page only; pagination cursors are forbidden"
            )
        response = self.fetcher.fetch(target_url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)

        records, skipped = parse_threads_search(response.text)
        self.skipped_count += skipped
        items: list[CollectedItem] = []
        for rec in records:
            items.append(
                _social_item(
                    hasher=self.hasher,
                    platform="threads",
                    item_id=f"threads-{rec['post_id']}",
                    url=f"https://www.threads.com/t/{rec['post_id']}",
                    fetched_iso=fetched_iso,
                    published_at=rec["published_at"],
                    text=rec["text"],
                    handle=rec["handle"],
                    display_name="",
                    record=rec,
                    metrics={"like_count": 0, "reply_count": 0},
                    http_status=response.http_status,
                )
            )
        return items


class InstagramPopularTagSpider:
    """Scrapling browser spider restricted strictly to logged-out Instagram popular tag pages (SD §5.3, O-3)."""

    source_id = "collector-tw-instagram-popular"
    skipped_count = 0

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        hasher: AuthorHasher,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingBrowserFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.hasher = hasher

    @staticmethod
    def validate_popular_tag_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.netloc not in {"www.instagram.com", "instagram.com"}:
            raise CollectorConfigError(f"Instagram spider refused non-Instagram host: {url!r}")
        path = parsed.path or ""
        if any(
            path.startswith(bad)
            for bad in ("/explore/search/", "/accounts/login", "/p/", "/reel/", "/ stories/")
        ):
            raise CollectorConfigError(
                f"Instagram spider permits only logged-out popular tag pages, refused: {url!r}"
            )
        if not (path.startswith("/explore/tags/") or path.startswith("/popular/")):
            raise CollectorConfigError(
                f"Instagram spider permits only /explore/tags/<tag>/ or /popular/<tag>/, refused profile/other URL: {url!r}"
            )

    def collect(
        self,
        *,
        tag: str = "台股",
        url: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        target_url = url or f"https://www.instagram.com/explore/tags/{quote(tag, safe='')}/"
        self.validate_popular_tag_url(target_url)
        response = self.fetcher.fetch(target_url)
        self.validate_popular_tag_url(response.final_url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)

        records, skipped = parse_instagram_tag(response.text)
        self.skipped_count += skipped
        items: list[CollectedItem] = []
        for rec in records:
            items.append(
                _social_item(
                    hasher=self.hasher,
                    platform="instagram",
                    item_id=f"ig-reel-{rec['post_id']}",
                    url=f"https://www.instagram.com/popular/{quote(tag, safe='')}/#reel-{rec['post_id']}",
                    fetched_iso=fetched_iso,
                    published_at=fetched_iso,
                    text=rec["text"],
                    handle=rec["handle"],
                    display_name="",
                    record=rec,
                    metrics={"view_count": rec["play_count"], "like_count": 0, "tag": tag},
                    http_status=response.http_status,
                )
            )
        return items


class CmoneyForumSpider:
    """Scrapling browser spider for 股市爆料同學會 (CMoney forum, disabled by default per O-4)."""

    source_id = "collector-tw-cmoney-forum"
    skipped_count = 0

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        hasher: AuthorHasher,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingBrowserFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.hasher = hasher

    def collect(
        self,
        *,
        stock_code: str = "2330",
        url: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        target_url = url or f"https://www.cmoney.tw/forum/stock/{stock_code}"
        response = self.fetcher.fetch(target_url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)

        records, skipped = parse_cmoney_forum(response.text)
        self.skipped_count += skipped
        items: list[CollectedItem] = []
        for rec in records:
            item = _social_item(
                hasher=self.hasher,
                platform="cmoney",
                item_id=f"cmoney-{rec['article_id']}",
                url=f"https://www.cmoney.tw/forum/stock/{stock_code}#article-{rec['article_id']}",
                fetched_iso=fetched_iso,
                published_at=fetched_iso,  # the page only shows relative times ("1小時前")
                text=rec["text"],
                handle=rec["member_id"],
                display_name=rec["display_name"],
                record=rec,
                metrics={"like_count": 0, "sentiment_tag": rec["sentiment"]},
                http_status=response.http_status,
            )
            item.extra_fields["symbols"] = sorted(set([stock_code] + _detect_tw_symbols(item.text)))
            items.append(item)
        return items


class PttStockSpider:
    """Scrapling HTTP spider for PTT Stock board official web front (SD §5.3)."""

    source_id = "collector-tw-ptt-stock"
    skipped_count = 0

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        hasher: AuthorHasher,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.hasher = hasher

    def collect(
        self,
        *,
        url: str = "https://www.ptt.cc/bbs/Stock/index.html",
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        response = self.fetcher.fetch(url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)

        records, skipped = parse_ptt_index(response.text)
        self.skipped_count += skipped
        items: list[CollectedItem] = []
        for rec in records:
            item = _social_item(
                hasher=self.hasher,
                platform="ptt",
                item_id=f"ptt-stock-{rec['article_id']}",
                url=f"https://www.ptt.cc{rec['href']}",
                fetched_iso=fetched_iso,
                published_at=fetched_iso,  # the index only shows "MM/DD"
                text=rec["title"],
                handle=rec["author"],
                display_name="",
                record=rec,
                metrics={"push_count": rec["push_count"], "ai_train_allowed": False},
                http_status=response.http_status,
            )
            item.extra_fields["title"] = item.text
            items.append(item)
        return items


class PttMirrorSpider:
    """Scrapling HTTP spider for PTT mirrors (disp.cc RSS and pttweb.cc; disabled by default per O-11)."""

    source_id = "collector-tw-ptt-mirrors"

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        hasher: AuthorHasher,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.hasher = hasher

    def collect(
        self,
        *,
        url: str = "https://rss.disp.cc/Stock.xml",
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        response = self.fetcher.fetch(url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        root = ET.fromstring(response.text)
        items: list[CollectedItem] = []

        for item_el in root.findall(".//item"):
            guid = (item_el.findtext("guid") or "").strip()
            title = (item_el.findtext("title") or "").strip()
            link = (item_el.findtext("link") or url).strip()
            desc = (item_el.findtext("description") or "").strip()
            pub_date = (item_el.findtext("pubDate") or fetched_iso).strip()
            raw_author = (item_el.findtext("author") or "anonymous_mirror_user").strip()
            raw_handle, _, display_name = raw_author.partition(" ")
            raw_handle = raw_handle.strip("()@")
            display_name = display_name.strip("()")

            hashed_author = self.hasher.hash_author("ptt_mirror", raw_handle)
            clean_text = f"{title} — {desc}".strip()
            if display_name:
                clean_text = clean_text.replace(display_name, "").strip()
            if raw_handle:
                clean_text = clean_text.replace(raw_handle, "").strip()

            raw_item_xml = ET.tostring(item_el, encoding="utf-8")
            scrubbed_raw = scrub_raw_html_or_json(
                raw_item_xml,
                platform="ptt_mirror",
                hasher=self.hasher,
                handles=[raw_handle],
                display_names=[display_name] if display_name else (),
            )
            forbidden = tuple(x for x in (raw_handle, display_name) if x)
            items.append(
                CollectedItem(
                    item_id=f"ptt-mirror-{guid}",
                    url=link,
                    fetched_at=fetched_iso,
                    published_at=pub_date,
                    text=clean_text,
                    lang="zh-TW",
                    hashed_author=hashed_author,
                    metrics={"mirror": "disp.cc", "platform": "ptt_mirror"},
                    raw_bytes=scrubbed_raw,
                    raw_extension="xml",
                    http_status=response.http_status,
                    extra_fields={
                        "title": title,
                        "symbols": _detect_tw_symbols(clean_text),
                    },
                    forbidden_literals=forbidden,
                    require_hashed_author=True,
                )
            )
        return items


class DgbasMacroSpider:
    """Scrapling HTTP spider for DGBAS CPI downloads and GDP/unemployment statistical tables (SD §5.1, §6.2)."""

    source_id = "collector-tw-macro-dgbas"
    skipped_count = 0

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    def collect(
        self,
        *,
        urls: Sequence[str] = (
            "https://www.stat.gov.tw/Point.aspx?sid=t.2&n=3581",
            "https://www.stat.gov.tw/Point.aspx?sid=t.1&n=3580",
        ),
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        items: list[CollectedItem] = []
        seen_item_ids: set[str] = set()

        for url in urls:
            response = self.fetcher.fetch(url)
            rows = extract_html_elements(response.text, "tr", class_name="dgbas-obs")
            for row in rows:
                series_id = row.attrs.get("data-series-id") or "DGBAS_SERIES"
                period = row.attrs.get("data-period") or ""
                if not period:
                    self.skipped_count += 1
                    continue
                item_id = f"dgbas-{series_id.lower()}-{period}"
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                published_at = row.attrs.get("data-release-date") or fetched_iso
                value = float(row.attrs.get("data-value") or 0.0)
                yoy = float(row.attrs.get("data-yoy") or 0.0)
                unit = row.attrs.get("data-unit") or "index"
                label = row.text or f"{series_id} {period}"

                row_html = f'<tr class="dgbas-obs" data-series-id="{series_id}" data-period="{period}">{row.inner_html}</tr>'
                items.append(
                    CollectedItem(
                        item_id=item_id,
                        url=url,
                        fetched_at=fetched_iso,
                        published_at=published_at,
                        text=f"DGBAS {series_id} ({period}): value={value} {unit}, YoY={yoy}%",
                        lang="zh-TW",
                        hashed_author=None,
                        metrics={"value": value, "yoy_pct": yoy, "period": period},
                        raw_bytes=row_html.encode("utf-8"),
                        raw_extension="html",
                        http_status=response.http_status,
                        extra_fields={
                            "title": label,
                            "series_id": series_id,
                            "value": value,
                            "unit": unit,
                        },
                    )
                )
        return items


EXCHANGE_TRADING_DAYS_PER_YEAR = 244
TWO_YEAR_BACKFILL_TRADING_DAYS = EXCHANGE_TRADING_DAYS_PER_YEAR * 2

TWSE_T86_WEBSITE_URL_TEMPLATE = (
    "https://www.twse.com.tw/rwd/zh/fund/T86?date={yyyymmdd}&selectType=ALLBUT0999&response=json"
)
TWSE_MI_INDEX_WEBSITE_URL_TEMPLATE = (
    "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={yyyymmdd}&type=ALLBUT0999&response=json"
)
TPEX_DAILY_QUOTES_WEBSITE_URL_TEMPLATE = (
    "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={yyyy_mm_dd}&id=&response=json"
)


def estimate_two_year_backfill_requests(
    *,
    trading_days_per_year: int = EXCHANGE_TRADING_DAYS_PER_YEAR,
    years: int = 2,
    min_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    """Return the 2-year per-date all-stock backfill request counts and wall-clock budget."""

    trading_days = int(trading_days_per_year) * int(years)
    per_endpoint_seconds = trading_days * float(min_interval_seconds)
    endpoints = {
        "collector-tw-twse-t86": {
            "source_id": "collector-tw-twse-t86",
            "exchange": "TWSE",
            "dataset": "tw_institutional_flow",
            "endpoint_template": TWSE_T86_WEBSITE_URL_TEMPLATE,
            "page_granularity": "per_date_all_stocks",
            "requests_for_two_years": trading_days,
            "max_connections": 1,
            "min_interval_seconds": float(min_interval_seconds),
            "estimated_wall_seconds": per_endpoint_seconds,
        },
        "collector-tw-twse-price-history": {
            "source_id": "collector-tw-twse-price-history",
            "exchange": "TWSE",
            "dataset": "tw_price_daily",
            "endpoint_template": TWSE_MI_INDEX_WEBSITE_URL_TEMPLATE,
            "page_granularity": "per_date_all_stocks",
            "requests_for_two_years": trading_days,
            "max_connections": 1,
            "min_interval_seconds": float(min_interval_seconds),
            "estimated_wall_seconds": per_endpoint_seconds,
        },
        "collector-tw-tpex-price-history": {
            "source_id": "collector-tw-tpex-price-history",
            "exchange": "TPEX",
            "dataset": "tw_price_daily",
            "endpoint_template": TPEX_DAILY_QUOTES_WEBSITE_URL_TEMPLATE,
            "page_granularity": "per_date_all_stocks",
            "requests_for_two_years": trading_days,
            "max_connections": 1,
            "min_interval_seconds": float(min_interval_seconds),
            "estimated_wall_seconds": per_endpoint_seconds,
        },
    }
    return {
        "years": int(years),
        "trading_days_per_year": int(trading_days_per_year),
        "trading_days_total": trading_days,
        "price_history_requests_for_two_years": trading_days * 2,
        "all_exchange_website_requests_for_two_years": trading_days * 3,
        "endpoints": endpoints,
    }


def _load_builtin_holidays() -> frozenset[str]:
    raw = json.loads((Path(__file__).with_name("tw_exchange_holidays.json")).read_text(encoding="utf-8"))
    return frozenset(raw["holidays"])


# Only certain closures are listed (see the JSON note); unlisted ones are handled by the "no data" response.
TW_EXCHANGE_HOLIDAYS: frozenset[str] = _load_builtin_holidays()


def recent_weekday_dates(
    reference_iso: str, *, count: int, holidays: Collection[str] = TW_EXCHANGE_HOLIDAYS
) -> list[str]:
    """Most recent `count` trading dates (weekdays minus built-in closures), newest first."""
    day = datetime.strptime(reference_iso[:10], "%Y-%m-%d").date()
    out: list[str] = []
    while len(out) < count:
        if day.weekday() < 5 and day.isoformat() not in holidays:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return out


TAIWAN_UTC_OFFSET_HOURS = 8
T86_PUBLISH_HOUR_TW = 15


def latest_published_trading_date(reference_iso: str, *, holidays: Collection[str] = TW_EXCHANGE_HOLIDAYS) -> str:
    """Latest non-holiday weekday whose TWSE daily data is published: before 15:00 Taiwan time that is the previous weekday."""
    ref = datetime.fromisoformat(reference_iso.replace("Z", "+00:00"))
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    local = ref.astimezone(timezone(timedelta(hours=TAIWAN_UTC_OFFSET_HOURS)))
    day = local.date()
    if local.hour < T86_PUBLISH_HOUR_TW:
        day -= timedelta(days=1)
    while day.weekday() >= 5 or day.isoformat() in holidays:
        day -= timedelta(days=1)
    return day.isoformat()


def _payload_confirms_no_data(payload: Mapping[str, Any]) -> bool:
    """True when an exchange page positively says there is no data for the date (holiday / closure)."""
    stat = str(payload.get("stat") or "")
    if "沒有符合條件" in stat or "no data" in stat.lower():
        return True
    if stat.lower() != "ok":
        return False
    tables = payload.get("tables")
    if isinstance(tables, list):
        return not any(isinstance(t, Mapping) and t.get("data") for t in tables)
    return not payload.get("data")


def _normalize_iso_date(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise CollectorConfigError("missing trade date in exchange website payload")
    cleaned = value.replace("/", "-").replace(".", "-")
    if re.fullmatch(r"\d{8}", cleaned):
        return f"{cleaned[0:4]}-{cleaned[4:6]}-{cleaned[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned
    if re.fullmatch(r"\d{7}", cleaned):
        year = int(cleaned[:3]) + 1911
        return f"{year:04d}-{int(cleaned[3:5]):02d}-{int(cleaned[5:7]):02d}"
    roc_match = re.fullmatch(r"(\d{2,3})-(\d{1,2})-(\d{1,2})", cleaned)
    if roc_match:
        year = int(roc_match.group(1)) + 1911
        return f"{year:04d}-{int(roc_match.group(2)):02d}-{int(roc_match.group(3)):02d}"
    raise CollectorConfigError(f"unsupported trade date format: {raw_value!r}")


def _clean_num(value: Any, *, allow_zero: bool = True) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not allow_zero and numeric == 0.0:
            return None
        return numeric
    text = str(value).strip()
    if not text or text in {"--", "---", "N/A", "null", "None", "除權息", "除息", "除權"}:
        return None
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace(",", "").replace("+", "").replace("X", "").strip()
    if not text or text in {"--", "---", "-"}:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    if not allow_zero and numeric == 0.0:
        return None
    return numeric


def _clean_int(value: Any) -> int:
    numeric = _clean_num(value, allow_zero=True)
    if numeric is None:
        return 0
    return int(round(numeric))


class TwseT86DailySpider:
    skipped_not_listed_count = 0

    """Scrapling HTTP spider for TWSE T86 daily institutional buy/sell flows per-date all-stock page (SD §5.1, O-1)."""

    source_id = "collector-tw-twse-t86"
    DEFAULT_RECENT_DATES = 1

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy,
            transport=transport,
            contact_user_agent=contact_user_agent,
            sleep_fn=sleep_fn,
        )

    @staticmethod
    def build_date_url(trade_date: str) -> str:
        iso_date = _normalize_iso_date(trade_date)
        compact = iso_date.replace("-", "")
        return TWSE_T86_WEBSITE_URL_TEMPLATE.format(yyyymmdd=compact)

    @staticmethod
    def validate_all_stock_url(url: str) -> None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "stockNo" in query or "stkNo" in query:
            raise CollectorConfigError(
                f"TWSE T86 spider requires per-date all-stock pages (selectType=ALLBUT0999), got per-stock URL: {url!r}"
            )
        select_types = query.get("selectType") or []
        if select_types and select_types[0] not in {"ALLBUT0999", "ALL"}:
            raise CollectorConfigError(
                f"TWSE T86 spider requires selectType=ALLBUT0999 per-date all-stock query, got: {url!r}"
            )

    def collect(
        self,
        *,
        dates: Sequence[str] | None = None,
        urls: Sequence[str] | None = None,
        symbols_filter: Sequence[str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        allowed_symbols = (
            {str(s).strip() for s in symbols_filter if str(s).strip()}
            if symbols_filter is not None
            else None
        )
        self.skipped_not_listed_count = 0
        if dates is None:
            latest = latest_published_trading_date(fetched_iso)
            dates = recent_weekday_dates(latest, count=self.DEFAULT_RECENT_DATES)
        target_urls = list(urls) if urls is not None else [self.build_date_url(d) for d in dates]
        items: list[CollectedItem] = []
        seen_ids: set[str] = set()

        empty_pages = 0
        self.no_data_confirmed = False
        for target_url in target_urls:
            self.validate_all_stock_url(target_url)
            response = self.fetcher.fetch(target_url)
            payload = json.loads(response.text)
            if isinstance(payload, Mapping) and _payload_confirms_no_data(payload):
                empty_pages += 1
            if not isinstance(payload, Mapping):
                continue
            query_dates = parse_qs(urlparse(target_url).query).get("date") or []
            raw_date = payload.get("date") or (query_dates[0] if query_dates else None)
            if not raw_date:
                continue
            trade_date = _normalize_iso_date(raw_date)
            fields = [str(f).strip() for f in (payload.get("fields") or [])]
            rows = payload.get("data") or []
            if not rows and isinstance(payload.get("tables"), list):
                for table in payload["tables"]:
                    if isinstance(table, Mapping) and table.get("data"):
                        fields = [str(f).strip() for f in (table.get("fields") or fields)]
                        rows = table.get("data") or []
                        break

            for row in rows:
                if isinstance(row, Mapping):
                    row_map = {str(k).strip(): v for k, v in row.items()}
                elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
                    row_map = {
                        fields[idx]: row[idx]
                        for idx in range(min(len(fields), len(row)))
                    }
                else:
                    continue

                symbol = str(
                    row_map.get("證券代號")
                    or row_map.get("Code")
                    or row_map.get("symbol")
                    or ""
                ).strip()
                if not symbol or not re.fullmatch(r"[0-9A-Za-z]{4,6}", symbol):
                    continue
                if allowed_symbols is not None and symbol not in allowed_symbols:
                    self.skipped_not_listed_count += 1
                    continue
                security_name = str(
                    row_map.get("證券名稱")
                    or row_map.get("Name")
                    or row_map.get("security_name")
                    or symbol
                ).strip()

                foreign_primary = _clean_int(
                    row_map.get("外陸資買賣超股數(不含外資自營商)")
                    if "外陸資買賣超股數(不含外資自營商)" in row_map
                    else row_map.get("外資及陸資買賣超股數")
                )
                foreign_dealer = _clean_int(row_map.get("外資自營商買賣超股數"))
                foreign_net = (
                    _clean_int(row_map["foreign_net_shares"])
                    if "foreign_net_shares" in row_map
                    else (foreign_primary + foreign_dealer)
                )
                trust_net = _clean_int(
                    row_map.get("investment_trust_net_shares")
                    if "investment_trust_net_shares" in row_map
                    else row_map.get("投信買賣超股數")
                )
                if "dealer_net_shares" in row_map:
                    dealer_net = _clean_int(row_map["dealer_net_shares"])
                elif "自營商買賣超股數" in row_map:
                    dealer_net = _clean_int(row_map.get("自營商買賣超股數"))
                else:
                    dealer_net = _clean_int(
                        row_map.get("自營商買賣超股數(自行買賣)")
                    ) + _clean_int(row_map.get("自營商買賣超股數(避險)"))

                total_net = (
                    _clean_int(row_map["total_institutional_net_shares"])
                    if "total_institutional_net_shares" in row_map
                    else (
                        _clean_int(row_map["三大法人買賣超股數"])
                        if "三大法人買賣超股數" in row_map
                        else (foreign_net + trust_net + dealer_net)
                    )
                )

                item_id = f"twse-t86-{trade_date}-{symbol}"
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                normalized_record = {
                    "dataset": "tw_institutional_flow",
                    "exchange": "TWSE",
                    "source_dataset": "T86",
                    "symbol": symbol,
                    "security_name": security_name,
                    "trade_date": trade_date,
                    "foreign_net_shares": foreign_net,
                    "investment_trust_net_shares": trust_net,
                    "dealer_net_shares": dealer_net,
                    "total_institutional_net_shares": total_net,
                }
                raw_row = json.dumps(
                    {
                        "date": trade_date.replace("-", ""),
                        "fields": fields,
                        "data": [list(row) if isinstance(row, (list, tuple)) else row],
                        "record": normalized_record,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")

                items.append(
                    CollectedItem(
                        item_id=item_id,
                        url=target_url,
                        fetched_at=fetched_iso,
                        published_at=f"{trade_date}T08:30:00Z",
                        text=(
                            f"TWSE T86 {symbol} {security_name} ({trade_date}): "
                            f"foreign={foreign_net}, trust={trust_net}, dealer={dealer_net}, total={total_net}"
                        ),
                        lang="zh-TW",
                        hashed_author=None,
                        metrics={
                            "foreign_net_shares": foreign_net,
                            "investment_trust_net_shares": trust_net,
                            "dealer_net_shares": dealer_net,
                            "total_institutional_net_shares": total_net,
                        },
                        raw_bytes=raw_row,
                        raw_extension="json",
                        http_status=response.http_status,
                        extra_fields={
                            **normalized_record,
                            "symbols": [symbol],
                            "title": f"TWSE T86 {symbol} {trade_date}",
                        },
                    )
                )
        self.no_data_confirmed = bool(target_urls) and empty_pages == len(target_urls) and not items
        return items


def _price_published_at(trade_date: str, fetched_iso: str) -> str:
    """Bound the conventional 14:30 Taipei label by the actual observation."""
    conventional = f"{trade_date}T06:30:00Z"
    fetched = datetime.fromisoformat(fetched_iso.replace("Z", "+00:00"))
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    published = datetime.fromisoformat(conventional.replace("Z", "+00:00"))
    return conventional if published <= fetched else fetched_iso


class TwseDailyPriceHistorySpider:
    skipped_not_listed_count = 0

    """Scrapling HTTP spider for TWSE daily price history using per-date all-stock MI_INDEX pages (SD §5.1, O-1)."""

    source_id = "collector-tw-twse-price-history"
    DEFAULT_RECENT_DATES = 1

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy,
            transport=transport,
            contact_user_agent=contact_user_agent,
            sleep_fn=sleep_fn,
        )

    @staticmethod
    def build_date_url(trade_date: str) -> str:
        iso_date = _normalize_iso_date(trade_date)
        compact = iso_date.replace("-", "")
        return TWSE_MI_INDEX_WEBSITE_URL_TEMPLATE.format(yyyymmdd=compact)

    @staticmethod
    def validate_all_stock_url(url: str) -> None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "stockNo" in query or "stkNo" in query or "/STOCK_DAY" in (parsed.path or ""):
            raise CollectorConfigError(
                f"TWSE price history backfill must use per-date all-stock MI_INDEX pages, refused per-stock URL: {url!r}"
            )
        types = query.get("type") or []
        if types and types[0] not in {"ALLBUT0999", "ALL"}:
            raise CollectorConfigError(
                f"TWSE MI_INDEX spider requires type=ALLBUT0999 per-date all-stock query, got: {url!r}"
            )

    def collect(
        self,
        *,
        dates: Sequence[str] | None = None,
        urls: Sequence[str] | None = None,
        symbols_filter: Sequence[str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        if dates is None:
            latest = latest_published_trading_date(fetched_iso)
            dates = recent_weekday_dates(latest, count=self.DEFAULT_RECENT_DATES)
        target_urls = list(urls) if urls is not None else [self.build_date_url(d) for d in dates]
        allowed_symbols = (
            {str(s).strip() for s in symbols_filter if str(s).strip()}
            if symbols_filter is not None
            else None
        )
        self.skipped_not_listed_count = 0
        items: list[CollectedItem] = []
        seen_ids: set[str] = set()

        empty_pages = 0
        self.no_data_confirmed = False
        for target_url in target_urls:
            self.validate_all_stock_url(target_url)
            response = self.fetcher.fetch(target_url)
            payload = json.loads(response.text)
            if isinstance(payload, Mapping) and _payload_confirms_no_data(payload):
                empty_pages += 1
            if not isinstance(payload, Mapping):
                continue
            query_dates = parse_qs(urlparse(target_url).query).get("date") or []
            raw_date = payload.get("date") or (query_dates[0] if query_dates else None)
            if not raw_date:
                continue
            default_trade_date = _normalize_iso_date(raw_date)

            tables: list[Mapping[str, Any]] = []
            if isinstance(payload.get("tables"), list):
                for table in payload["tables"]:
                    if isinstance(table, Mapping) and table.get("data"):
                        fields = [str(f).strip() for f in (table.get("fields") or [])]
                        if not fields or any("收盤價" in f or f.lower() == "close" for f in fields):
                            tables.append(table)
            if not tables and payload.get("data"):
                tables.append(payload)

            for table in tables:
                fields = [str(f).strip() for f in (table.get("fields") or [])]
                for row in table.get("data") or ():
                    if isinstance(row, Mapping):
                        row_map = {str(k).strip(): v for k, v in row.items()}
                    elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
                        row_map = {
                            fields[idx]: row[idx]
                            for idx in range(min(len(fields), len(row)))
                        }
                    else:
                        continue

                    symbol = str(
                        row_map.get("證券代號")
                        or row_map.get("Code")
                        or row_map.get("symbol")
                        or ""
                    ).strip()
                    if not symbol or not re.fullmatch(r"[0-9A-Za-z]{4,6}", symbol):
                        continue
                    if allowed_symbols is not None and symbol not in allowed_symbols:
                        self.skipped_not_listed_count += 1
                        continue
                    if "收盤價" not in row_map and "close" not in row_map:
                        continue
                    raw_close_cell = (
                        row_map.get("收盤價")
                        if "收盤價" in row_map
                        else row_map.get("close")
                    )
                    if raw_close_cell is None or str(raw_close_cell).strip() == "":
                        continue

                    security_name = str(
                        row_map.get("證券名稱")
                        or row_map.get("Name")
                        or row_map.get("security_name")
                        or symbol
                    ).strip()
                    trade_date = (
                        _normalize_iso_date(row_map["日期"])
                        if "日期" in row_map and row_map["日期"]
                        else default_trade_date
                    )
                    close_price = _clean_num(
                        raw_close_cell,
                        allow_zero=False,
                    )
                    not_traded = close_price is None
                    trading_status = "not_traded" if not_traded else "traded"
                    raw_close = str(raw_close_cell).strip()
                    open_price = _clean_num(
                        row_map.get("開盤價")
                        if "開盤價" in row_map
                        else row_map.get("open"),
                        allow_zero=False,
                    )
                    if open_price is None and close_price is not None:
                        open_price = close_price
                    high_price = _clean_num(
                        row_map.get("最高價")
                        if "最高價" in row_map
                        else row_map.get("high"),
                        allow_zero=False,
                    )
                    if high_price is None and close_price is not None:
                        high_price = close_price
                    low_price = _clean_num(
                        row_map.get("最低價")
                        if "最低價" in row_map
                        else row_map.get("low"),
                        allow_zero=False,
                    )
                    if low_price is None and close_price is not None:
                        low_price = close_price
                    sign_raw = str(row_map.get("漲跌(+/-)") or "").strip()
                    diff_val = (
                        None
                        if not_traded
                        else _clean_num(
                            row_map.get("漲跌價差")
                            if "漲跌價差" in row_map
                            else row_map.get("change"),
                            allow_zero=True,
                        )
                    )
                    if diff_val is not None and ("-" in sign_raw or "green" in sign_raw.lower()) and diff_val > 0:
                        change_val: float | None = -diff_val
                    else:
                        change_val = diff_val

                    volume_shares = _clean_int(
                        row_map.get("成交股數")
                        if "成交股數" in row_map
                        else row_map.get("volume_shares")
                    )
                    turnover_twd = _clean_int(
                        row_map.get("成交金額")
                        if "成交金額" in row_map
                        else row_map.get("turnover_twd")
                    )
                    transactions = _clean_int(
                        row_map.get("成交筆數")
                        if "成交筆數" in row_map
                        else row_map.get("transactions")
                    )

                    item_id = f"twse-price-history-{trade_date}-{symbol}"
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)

                    normalized_record = {
                        "dataset": "tw_price_daily",
                        "exchange": "TWSE",
                        "source_dataset": "STOCK_DAY",
                        "upstream_endpoint": "MI_INDEX",
                        "symbol": symbol,
                        "security_name": security_name,
                        "trade_date": trade_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "change": change_val,
                        "volume_shares": volume_shares,
                        "turnover_twd": turnover_twd,
                        "transactions": transactions,
                        "not_traded": not_traded,
                        "suspended_or_not_traded": not_traded,
                        "trading_status": trading_status,
                        "raw_close": raw_close,
                    }
                    raw_row = json.dumps(
                        {
                            "date": trade_date.replace("-", ""),
                            "fields": fields,
                            "data": [list(row) if isinstance(row, (list, tuple)) else row],
                            "record": normalized_record,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")

                    items.append(
                        CollectedItem(
                            item_id=item_id,
                            url=target_url,
                            fetched_at=fetched_iso,
                            published_at=_price_published_at(trade_date, fetched_iso),
                            text=(
                                f"TWSE MI_INDEX {symbol} {security_name} ({trade_date}): "
                                f"close={close_price}, trading_status={trading_status}, volume_shares={volume_shares}"
                            ),
                            lang="zh-TW",
                            hashed_author=None,
                            metrics={
                                "open": open_price,
                                "high": high_price,
                                "low": low_price,
                                "close": close_price,
                                "volume_shares": volume_shares,
                                "turnover_twd": turnover_twd,
                                "transactions": transactions,
                                "not_traded": not_traded,
                                "suspended_or_not_traded": not_traded,
                                "trading_status": trading_status,
                                "raw_close": raw_close,
                            },
                            raw_bytes=raw_row,
                            raw_extension="json",
                            http_status=response.http_status,
                            extra_fields={
                                **normalized_record,
                                "symbols": [symbol],
                                "title": f"TWSE MI_INDEX {symbol} {trade_date}",
                            },
                        )
                    )
        self.no_data_confirmed = bool(target_urls) and empty_pages == len(target_urls) and not items
        return items


class TwseRegimeParseError(ValueError):
    """A regime page cannot be published atomically with the expected schema."""


def _regime_rows(table: Mapping[str, Any], fields: list[str], label: str) -> list[list[str]]:
    if table.get("fields") != fields:
        raise TwseRegimeParseError(f"{label}: changed fields {table.get('fields')!r}")
    rows = table.get("data")
    if not isinstance(rows, list) or not rows:
        raise TwseRegimeParseError(f"{label}: missing data rows")
    if any(not isinstance(row, list) or len(row) != len(fields)
           or any(not isinstance(cell, str) for cell in row) for row in rows):
        raise TwseRegimeParseError(f"{label}: malformed row")
    return rows


def _regime_number(raw: str) -> float:
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", raw):
        raise TwseRegimeParseError(f"invalid numeric value {raw!r}")
    value = float(raw.replace(",", ""))
    if not math.isfinite(value):
        raise TwseRegimeParseError(f"non-finite numeric value {raw!r}")
    return value


class TwseIndexBreadthSpider(TwseDailyPriceHistorySpider):
    """Separate MI_INDEX source: TWSE price indices and market/stock breadth.

    Build the entire result before returning it so one malformed table/row
    invalidates the run, including any earlier successfully parsed pages.
    """

    source_id = "collector-tw-twse-index-breadth"
    INDEX_FIELDS = ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]
    BREADTH_FIELDS = ["類型", "整體市場", "股票"]
    BREADTH_LABELS = ["上漲(漲停)", "下跌(跌停)", "持平", "未成交", "無比價"]

    def __init__(self, policy: SourceFetchPolicy, **kwargs: Any) -> None:
        # These new sources enforce website spacing before issuing any request.
        if policy.max_connections != 1 or policy.min_interval_seconds < 5.0:
            raise CollectorConfigError(f"{policy.source_id}: requires max_connections=1 and min_interval_seconds >= 5.0")
        super().__init__(policy, **kwargs)

    def _item(self, *, record: dict[str, Any], raw: dict[str, Any], item_id: str,
              url: str, fetched_iso: str, http_status: int) -> CollectedItem:
        return CollectedItem(
            item_id=item_id, url=url, fetched_at=fetched_iso,
            published_at=f"{record['trade_date']}T06:30:00Z",
            text=f"TWSE {record['source_dataset']} {record.get('index_name', 'market breadth')} {record['trade_date']}",
            lang="zh-TW", hashed_author=None,
            raw_bytes=json.dumps(raw, ensure_ascii=False).encode("utf-8"), raw_extension="json",
            http_status=http_status, extra_fields={**record, "raw_values": raw},
        )

    def collect(self, *, dates: Sequence[str] | None = None,
                fetched_at: datetime | str | None = None) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        targets = dates if dates is not None else [latest_published_trading_date(fetched_iso)]
        items: list[CollectedItem] = []
        self.no_data_confirmed = False
        for target in dict.fromkeys(targets):
            trade_date = _normalize_iso_date(target)
            url = self.build_date_url(trade_date)
            response = self.fetcher.fetch(url)
            payload = json.loads(response.text)
            if not isinstance(payload, dict) or payload.get("stat") != "OK":
                raise TwseRegimeParseError("MI_INDEX: expected stat=OK")
            if payload.get("date") != trade_date.replace("-", ""):
                raise TwseRegimeParseError("MI_INDEX: response date does not match request")
            tables = payload.get("tables")
            if not isinstance(tables, list):
                raise TwseRegimeParseError("MI_INDEX: missing tables")
            indices = [t for t in tables if isinstance(t, dict)
                       and str(t.get("title", "")).endswith("價格指數(臺灣證券交易所)")]
            breadth = [t for t in tables if isinstance(t, dict) and t.get("title") == "漲跌證券數合計"]
            if len(indices) != 1 or len(breadth) != 1:
                raise TwseRegimeParseError("MI_INDEX: expected one TWSE price index table and one breadth table")
            index_rows = _regime_rows(indices[0], self.INDEX_FIELDS, "MI_INDEX price indices")
            names = [r[0] for r in index_rows]
            if ("發行量加權股價指數" not in names or not any("類指數" in n for n in names)
                    or len(set(names)) != len(names) or any(not n for n in names)):
                raise TwseRegimeParseError("MI_INDEX: missing TAIEX/sector indices or duplicate index names")
            base = {"exchange": "TWSE", "source_dataset": "MI_INDEX", "trade_date": trade_date}
            for row in index_rows:
                name, close, sign_raw, points, percent, _ = row
                sign = re.sub(r"<[^>]*>", "", sign_raw).strip()
                if sign not in {"+", "-", ""}:
                    raise TwseRegimeParseError(f"MI_INDEX: unexpected change sign {sign_raw!r}")
                points_value = _regime_number(points)
                percent_value = _regime_number(percent)
                if points_value < 0 or (sign == "" and (points_value != 0 or percent_value != 0)):
                    raise TwseRegimeParseError("MI_INDEX: inconsistent change sign/points")
                change = -points_value if sign == "-" else points_value
                if (change < 0 < percent_value) or (percent_value < 0 < change):
                    raise TwseRegimeParseError("MI_INDEX: inconsistent change percent")
                record = {**base, "dataset": "tw_index_daily", "index_name": name,
                          "close": _regime_number(close), "change_points": change,
                          "change_percent": percent_value}
                raw = {"fields": self.INDEX_FIELDS, "data": [row], "title": indices[0]["title"]}
                items.append(self._item(record=record, raw=raw, item_id=f"twse-index-{trade_date}-{name}",
                                        url=url, fetched_iso=fetched_iso, http_status=response.http_status))
            breadth_rows = _regime_rows(breadth[0], self.BREADTH_FIELDS, "MI_INDEX breadth")
            by_label = {row[0]: row for row in breadth_rows}
            if set(by_label) != set(self.BREADTH_LABELS) or len(breadth_rows) != 5:
                raise TwseRegimeParseError("MI_INDEX: changed breadth row labels")
            counts: dict[str, Any] = {}
            for col, group in ((1, "whole_market"), (2, "stocks")):
                group_counts: dict[str, int] = {}
                for label, total_key, limit_key in (("上漲(漲停)", "advancers", "limit_up"),
                                                    ("下跌(跌停)", "decliners", "limit_down")):
                    match = re.fullmatch(r"([\d,]+)\(([\d,]+)\)", by_label[label][col])
                    if not match:
                        raise TwseRegimeParseError(f"MI_INDEX: malformed breadth count {by_label[label][col]!r}")
                    total, limit = (self._count(v) for v in match.groups())
                    if limit > total:
                        raise TwseRegimeParseError("MI_INDEX: limit count exceeds total")
                    group_counts.update({total_key: total, limit_key: limit})
                for label, key in (("持平", "unchanged"), ("未成交", "untraded"), ("無比價", "no_comparison")):
                    group_counts[key] = self._count(by_label[label][col])
                counts[group] = group_counts
            items.append(self._item(
                record={**base, "dataset": "tw_market_breadth_daily", **counts},
                raw={"fields": self.BREADTH_FIELDS, "data": breadth_rows, "title": breadth[0]["title"]},
                item_id=f"twse-breadth-{trade_date}", url=url, fetched_iso=fetched_iso,
                http_status=response.http_status,
            ))
        return items

    @staticmethod
    def _count(raw: str) -> int:
        if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)", raw):
            raise TwseRegimeParseError(f"MI_INDEX: invalid integer count {raw!r}")
        return int(raw.replace(",", ""))


class TwseTaiexHistorySpider(TwseIndexBreadthSpider):
    """One MI_5MINS_HIST request per calendar month; never fills absent sessions."""

    source_id = "collector-tw-twse-taiex-history"
    MONTH_FIELDS = ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"]
    URL_TEMPLATE = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={yyyymmdd}&response=json"

    @staticmethod
    def build_date_url(trade_date: str) -> str:
        month = trade_date[:7]
        datetime.strptime(month + "-01", "%Y-%m-%d")
        return TwseTaiexHistorySpider.URL_TEMPLATE.format(yyyymmdd=month.replace("-", "") + "01")

    def collect(self, *, dates: Sequence[str] | None = None,
                fetched_at: datetime | str | None = None) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        targets = dates if dates is not None else [latest_published_trading_date(fetched_iso)[:7]]
        items: list[CollectedItem] = []
        for month in dict.fromkeys(d[:7] for d in targets):
            url = self.build_date_url(month)
            response = self.fetcher.fetch(url)
            payload = json.loads(response.text)
            if not isinstance(payload, dict) or payload.get("stat") != "OK":
                raise TwseRegimeParseError("MI_5MINS_HIST: expected stat=OK")
            if payload.get("date") != month.replace("-", "") + "01":
                raise TwseRegimeParseError("MI_5MINS_HIST: response month does not match request")
            rows = _regime_rows(payload, self.MONTH_FIELDS, "MI_5MINS_HIST")
            seen: set[str] = set()
            for row in rows:
                trade_date = _normalize_iso_date(row[0])
                datetime.strptime(trade_date, "%Y-%m-%d")
                if trade_date[:7] != month or trade_date in seen:
                    raise TwseRegimeParseError("MI_5MINS_HIST: duplicate or out-of-month session")
                seen.add(trade_date)
                ohlc = dict(zip(("open", "high", "low", "close"), map(_regime_number, row[1:])))
                if not (0 < ohlc["low"] <= min(ohlc["open"], ohlc["close"])
                        <= max(ohlc["open"], ohlc["close"]) <= ohlc["high"]):
                    raise TwseRegimeParseError("MI_5MINS_HIST: inconsistent OHLC")
                items.append(self._item(
                    record={"dataset": "tw_index_ohlc_daily", "exchange": "TWSE",
                            "source_dataset": "MI_5MINS_HIST", "index_name": "發行量加權股價指數",
                            "trade_date": trade_date, **ohlc},
                    raw={"fields": self.MONTH_FIELDS, "data": [row], "title": payload.get("title")},
                    item_id=f"twse-taiex-{trade_date}", url=url, fetched_iso=fetched_iso,
                    http_status=response.http_status,
                ))
        return items


class TpexDailyPriceHistorySpider:
    skipped_not_listed_count = 0

    """Scrapling HTTP spider for TPEx daily price history using per-date all-stock dailyQuotes pages (SD §5.1, O-1)."""

    source_id = "collector-tw-tpex-price-history"
    DEFAULT_RECENT_DATES = 1

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy,
            transport=transport,
            contact_user_agent=contact_user_agent,
            sleep_fn=sleep_fn,
        )

    @staticmethod
    def build_date_url(trade_date: str) -> str:
        iso_date = _normalize_iso_date(trade_date)
        slash_date = iso_date.replace("-", "/")
        return TPEX_DAILY_QUOTES_WEBSITE_URL_TEMPLATE.format(yyyy_mm_dd=slash_date)

    @staticmethod
    def validate_all_stock_url(url: str) -> None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if "/tradingStock" in (parsed.path or "") or "stkNo" in query or "code" in query:
            raise CollectorConfigError(
                f"TPEx price history backfill must use per-date all-stock dailyQuotes pages, refused per-stock URL: {url!r}"
            )
        ids = [val.strip() for val in (query.get("id") or []) if val.strip()]
        if ids:
            raise CollectorConfigError(
                f"TPEx dailyQuotes spider requires id='' per-date all-stock query, got per-stock id={ids!r} in {url!r}"
            )

    def collect(
        self,
        *,
        dates: Sequence[str] | None = None,
        urls: Sequence[str] | None = None,
        symbols_filter: Sequence[str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        if dates is None:
            latest = latest_published_trading_date(fetched_iso)
            dates = recent_weekday_dates(latest, count=self.DEFAULT_RECENT_DATES)
        target_urls = list(urls) if urls is not None else [self.build_date_url(d) for d in dates]
        allowed_symbols = (
            {str(s).strip() for s in symbols_filter if str(s).strip()}
            if symbols_filter is not None
            else None
        )
        self.skipped_not_listed_count = 0
        items: list[CollectedItem] = []
        seen_ids: set[str] = set()

        empty_pages = 0
        self.no_data_confirmed = False
        for target_url in target_urls:
            self.validate_all_stock_url(target_url)
            response = self.fetcher.fetch(target_url)
            payload = json.loads(response.text)
            if isinstance(payload, Mapping) and _payload_confirms_no_data(payload):
                empty_pages += 1
            if not isinstance(payload, Mapping):
                continue
            query_dates = parse_qs(urlparse(target_url).query).get("date") or []
            raw_date = payload.get("date") or (query_dates[0] if query_dates else None)
            if not raw_date:
                continue
            default_trade_date = _normalize_iso_date(raw_date)

            tables: list[Mapping[str, Any]] = []
            if isinstance(payload.get("tables"), list):
                for table in payload["tables"]:
                    if isinstance(table, Mapping) and (table.get("data") or table.get("aaData")):
                        tables.append(table)
            if not tables and (payload.get("data") or payload.get("aaData")):
                tables.append(payload)

            for table in tables:
                fields = [str(f).strip() for f in (table.get("fields") or [])]
                rows = table.get("data") or table.get("aaData") or ()
                for row in rows:
                    if isinstance(row, Mapping):
                        row_map = {str(k).strip(): v for k, v in row.items()}
                    elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
                        if fields:
                            row_map = {
                                fields[idx]: row[idx]
                                for idx in range(min(len(fields), len(row)))
                            }
                        else:
                            row_map = {
                                "代號": row[0] if len(row) > 0 else "",
                                "名稱": row[1] if len(row) > 1 else "",
                                "收盤": row[2] if len(row) > 2 else None,
                                "漲跌": row[3] if len(row) > 3 else None,
                                "開盤": row[4] if len(row) > 4 else None,
                                "最高": row[5] if len(row) > 5 else None,
                                "最低": row[6] if len(row) > 6 else None,
                                "成交股數": row[8] if len(row) > 8 else (row[7] if len(row) > 7 else 0),
                                "成交金額(元)": row[9] if len(row) > 9 else 0,
                                "成交筆數": row[10] if len(row) > 10 else 0,
                            }
                    else:
                        continue

                    symbol = str(
                        row_map.get("代號")
                        or row_map.get("證券代號")
                        or row_map.get("SecuritiesCompanyCode")
                        or row_map.get("symbol")
                        or ""
                    ).strip()
                    if not symbol or not re.fullmatch(r"[0-9A-Za-z]{4,6}", symbol):
                        continue
                    if allowed_symbols is not None and symbol not in allowed_symbols:
                        self.skipped_not_listed_count += 1
                        continue
                    if "收盤" not in row_map and "收盤價" not in row_map and "close" not in row_map:
                        continue
                    raw_close_cell = (
                        row_map.get("收盤")
                        if "收盤" in row_map
                        else (row_map.get("收盤價") if "收盤價" in row_map else row_map.get("close"))
                    )
                    if raw_close_cell is None or str(raw_close_cell).strip() == "":
                        continue

                    security_name = str(
                        row_map.get("名稱")
                        or row_map.get("證券名稱")
                        or row_map.get("CompanyName")
                        or row_map.get("security_name")
                        or symbol
                    ).strip()
                    trade_date = (
                        _normalize_iso_date(row_map["日 期"] if "日 期" in row_map else row_map["日期"])
                        if ("日 期" in row_map or "日期" in row_map)
                        else default_trade_date
                    )
                    close_price = _clean_num(
                        raw_close_cell,
                        allow_zero=False,
                    )
                    not_traded = close_price is None
                    trading_status = "not_traded" if not_traded else "traded"
                    raw_close = str(raw_close_cell).strip()
                    open_price = _clean_num(
                        row_map.get("開盤")
                        if "開盤" in row_map
                        else (row_map.get("開盤價") if "開盤價" in row_map else row_map.get("open")),
                        allow_zero=False,
                    )
                    if open_price is None and close_price is not None:
                        open_price = close_price
                    high_price = _clean_num(
                        row_map.get("最高")
                        if "最高" in row_map
                        else (row_map.get("最高價") if "最高價" in row_map else row_map.get("high")),
                        allow_zero=False,
                    )
                    if high_price is None and close_price is not None:
                        high_price = close_price
                    low_price = _clean_num(
                        row_map.get("最低")
                        if "最低" in row_map
                        else (row_map.get("最低價") if "最低價" in row_map else row_map.get("low")),
                        allow_zero=False,
                    )
                    if low_price is None and close_price is not None:
                        low_price = close_price
                    change_val = (
                        None
                        if not_traded
                        else _clean_num(
                            row_map.get("漲跌")
                            if "漲跌" in row_map
                            else row_map.get("change"),
                            allow_zero=True,
                        )
                    )
                    volume_shares = _clean_int(
                        row_map.get("成交股數")
                        if "成交股數" in row_map
                        else row_map.get("volume_shares")
                    )
                    turnover_twd = _clean_int(
                        row_map.get("成交金額(元)")
                        if "成交金額(元)" in row_map
                        else (row_map.get("成交金額") if "成交金額" in row_map else row_map.get("turnover_twd"))
                    )
                    transactions = _clean_int(
                        row_map.get("成交筆數")
                        if "成交筆數" in row_map
                        else row_map.get("transactions")
                    )

                    item_id = f"tpex-price-history-{trade_date}-{symbol}"
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)

                    normalized_record = {
                        "dataset": "tw_price_daily",
                        "exchange": "TPEX",
                        "source_dataset": "tpex_individual_stock_monthly_history",
                        "upstream_endpoint": "dailyQuotes",
                        "symbol": symbol,
                        "security_name": security_name,
                        "trade_date": trade_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "change": change_val,
                        "volume_shares": volume_shares,
                        "turnover_twd": turnover_twd,
                        "transactions": transactions,
                        "not_traded": not_traded,
                        "suspended_or_not_traded": not_traded,
                        "trading_status": trading_status,
                        "raw_close": raw_close,
                    }
                    raw_row = json.dumps(
                        {
                            "date": trade_date.replace("-", "/"),
                            "fields": fields,
                            "data": [list(row) if isinstance(row, (list, tuple)) else row],
                            "record": normalized_record,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")

                    items.append(
                        CollectedItem(
                            item_id=item_id,
                            url=target_url,
                            fetched_at=fetched_iso,
                            published_at=_price_published_at(trade_date, fetched_iso),
                            text=(
                                f"TPEx dailyQuotes {symbol} {security_name} ({trade_date}): "
                                f"close={close_price}, trading_status={trading_status}, volume_shares={volume_shares}"
                            ),
                            lang="zh-TW",
                            hashed_author=None,
                            metrics={
                                "open": open_price,
                                "high": high_price,
                                "low": low_price,
                                "close": close_price,
                                "volume_shares": volume_shares,
                                "turnover_twd": turnover_twd,
                                "transactions": transactions,
                                "not_traded": not_traded,
                                "suspended_or_not_traded": not_traded,
                                "trading_status": trading_status,
                                "raw_close": raw_close,
                            },
                            raw_bytes=raw_row,
                            raw_extension="json",
                            http_status=response.http_status,
                            extra_fields={
                                **normalized_record,
                                "symbols": [symbol],
                                "title": f"TPEx dailyQuotes {symbol} {trade_date}",
                            },
                        )
                    )
        self.no_data_confirmed = bool(target_urls) and empty_pages == len(target_urls) and not items
        return items


class TaifexVixParseError(ValueError):
    """A TAIFEX VIX page or file changed shape; the whole run is rejected."""


class TaifexVixSpider:
    """TAIFEX Taiwan VIX archive: daily close files plus per-day intraday files.

    Confirmed against the live site (2026-11-14), no browser needed:
    - /cht/7/vixDaily3MNew lists ``Dailydownload/vix/log2data/YYYYMMnew.txt`` (daily, current + 3 months)
    - /cht/7/vixMinNew lists ``getVixData?filesname=YYYYMMDD`` (intraday, current month, one file per day)
    - /cht/7/vixMin3MNew lists ``Dailydownload/vix/logdata/YYYYMM...zip`` (intraday, prior 3 months)
    Files are Big5 text. Everything is parsed before returning, so any change raises and emits nothing.
    A ledger (only advanced by ``acknowledge_bundle`` after the bundle is published) skips finished files.
    """

    source_id = "collector-tw-taifex-vix"
    PAGE_BASE = "https://www.taifex.com.tw/cht/7/"
    DAILY_PAGE = PAGE_BASE + "vixDaily3MNew"
    INTRADAY_PAGE = PAGE_BASE + "vixMinNew"
    INTRADAY_3M_PAGE = PAGE_BASE + "vixMin3MNew"
    DAY_URL = PAGE_BASE + "getVixData?filesname={yyyymmdd}"
    FILE_BASE = "https://www.taifex.com.tw/file/taifex/Dailydownload/vix/"
    DAILY_URL = FILE_BASE + "log2data/{yyyymm}new.txt"
    DAILY_HEADER = ["交易日期", "時間(時/分/秒/毫秒)", "臺指選擇權波動率指數", "收盤前1分鐘平均指數"]
    INTRADAY_HEADER = ["交易日期", "時間(時/分/秒/毫秒)", "臺指選擇權波動率指數"]
    LAST_AVG_LABEL = "Last 1 min AVG"

    def __init__(self, policy: SourceFetchPolicy, *, transport: FetchTransport,
                 contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
                 sleep_fn: Callable[[float], None] | None = None,
                 state_path: Path | str | None = None) -> None:
        if policy.max_connections != 1 or policy.min_interval_seconds < 5.0:
            raise CollectorConfigError(f"{policy.source_id}: requires max_connections=1 and min_interval_seconds >= 5.0")
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(policy, transport=transport,
                                            contact_user_agent=contact_user_agent, sleep_fn=sleep_fn)
        self.state_path = Path(state_path) if state_path is not None else None
        self._pending: set[str] = set()
        self.skipped_count = 0  # malformed items skipped; always 0 here (a bad file fails the run)
        self.ledger_skipped = 0  # files already archived, not requested again

    # -- ledger -----------------------------------------------------------------
    def _ledger(self) -> set[str]:
        if self.state_path is None or not self.state_path.exists():
            return set()
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        return set(data.get("done", []))

    def acknowledge_bundle(self) -> None:
        """Called only after the bundle was published; marks finished files as archived."""
        if self.state_path is None or not self._pending:
            return
        done = sorted(self._ledger() | self._pending)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"done": done}, indent=0), encoding="utf-8")
        tmp.replace(self.state_path)
        self._pending = set()

    # -- parsing ----------------------------------------------------------------
    @staticmethod
    def _decode(body: bytes, label: str) -> list[str]:
        if body.lstrip()[:1] == b"<":
            raise TaifexVixParseError(f"{label}: got HTML instead of a data file")
        try:
            text = body.decode("cp950")
        except UnicodeDecodeError as exc:
            raise TaifexVixParseError(f"{label}: not Big5 text") from exc
        return [line for line in text.replace("\r", "").split("\n") if line.strip()]

    @staticmethod
    def _time(raw: str, label: str) -> str:
        """``H[H]MMSSff``: observed 7/8 digits, e.g. 9001500 = 09:00:15.00, 13450000 = 13:45:00.00."""
        if not re.fullmatch(r"\d{7,8}", raw):
            raise TaifexVixParseError(f"{label}: bad time {raw!r}")
        padded = raw.rjust(8, "0")
        hh, mm, ss, frac = int(padded[:2]), int(padded[2:4]), int(padded[4:6]), padded[6:]
        if hh > 23 or mm > 59 or ss > 59:
            raise TaifexVixParseError(f"{label}: bad time {raw!r}")
        return f"{hh:02d}:{mm:02d}:{ss:02d}.{frac}"

    @staticmethod
    def _value(raw: str, label: str) -> float:
        if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
            raise TaifexVixParseError(f"{label}: bad VIX value {raw!r}")
        value = float(raw)
        if not 0 < value < 500:
            raise TaifexVixParseError(f"{label}: implausible VIX value {raw!r}")
        return value

    @staticmethod
    def _date(raw: str, label: str) -> str:
        try:
            return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise TaifexVixParseError(f"{label}: bad date {raw!r}") from exc

    def _table(self, lines: list[str], header: list[str], label: str) -> list[list[str]]:
        if len(lines) < 3 or [p.strip() for p in lines[0].split("\t") if p.strip()] != header:
            raise TaifexVixParseError(f"{label}: changed header")
        if not re.fullmatch(r"[-\t ]+", lines[1]):
            raise TaifexVixParseError(f"{label}: missing header rule")
        return [[p.strip() for p in line.split("\t") if p.strip()] for line in lines[2:]]

    def parse_daily(self, body: bytes, month: str) -> list[dict[str, Any]]:
        label = f"daily {month}"
        rows, seen = [], set()
        for parts in self._table(self._decode(body, label), self.DAILY_HEADER, label):
            if len(parts) != 4:
                raise TaifexVixParseError(f"{label}: malformed row {parts!r}")
            trade_date = self._date(parts[0], label)
            if trade_date[:7].replace("-", "") != month or trade_date in seen:
                raise TaifexVixParseError(f"{label}: out-of-month or duplicate session {parts[0]}")
            seen.add(trade_date)
            rows.append({"trade_date": trade_date, "time": self._time(parts[1], label), "time_raw": parts[1],
                         "close": self._value(parts[2], label),
                         "last_1min_avg": self._value(parts[3], label)})
        if not rows or [r["trade_date"] for r in rows] != sorted(seen):
            raise TaifexVixParseError(f"{label}: no rows or unordered sessions")
        return rows

    def parse_intraday(self, body: bytes, yyyymmdd: str) -> dict[str, Any]:
        label = f"intraday {yyyymmdd}"
        trade_date = self._date(yyyymmdd, label)
        rows: list[dict[str, Any]] = []
        last_avg: float | None = None
        for parts in self._table(self._decode(body, label), self.INTRADAY_HEADER, label):
            if len(parts) != 3 or parts[0] != yyyymmdd or last_avg is not None:
                raise TaifexVixParseError(f"{label}: malformed row {parts!r}")
            if parts[1] == self.LAST_AVG_LABEL:
                last_avg = self._value(parts[2], label)
                continue
            clock = self._time(parts[1], label)
            if rows and clock <= rows[-1]["time"]:
                raise TaifexVixParseError(f"{label}: times not increasing at {clock}")
            rows.append({"trade_date": trade_date, "time": clock, "time_raw": parts[1], "value": self._value(parts[2], label)})
        if not rows:
            raise TaifexVixParseError(f"{label}: no rows")
        return {"trade_date": trade_date, "rows": rows, "last_1min_avg": last_avg,
                "complete": last_avg is not None}

    # -- items ------------------------------------------------------------------
    @staticmethod
    def _sha(body: bytes) -> str:
        import hashlib
        return hashlib.sha256(body).hexdigest()

    def _item(self, *, item_id: str, url: str, fetched_iso: str, published: str, text: str,
              body: bytes, ext: str, record: dict[str, Any], status: int) -> CollectedItem:
        return CollectedItem(
            item_id=item_id, url=url, fetched_at=fetched_iso, published_at=f"{published}T06:30:00Z",
            text=text, lang="zh-TW", hashed_author=None, raw_bytes=body, raw_extension=ext,
            http_status=status,
            extra_fields={"exchange": "TAIFEX", "raw_sha256": self._sha(body), **record},
        )

    def _intraday_item(self, parsed: dict[str, Any], body: bytes, url: str, fetched_iso: str,
                       status: int, via: str) -> CollectedItem:
        day = parsed["trade_date"]
        return self._item(
            item_id=f"taifex-vix-intraday-{day}-{self._sha(body)[:12]}", url=url, fetched_iso=fetched_iso,
            published=day, text=f"TAIFEX Taiwan VIX intraday {day}", body=body, ext="txt", status=status,
            record={"dataset": "tw_vix_intraday", "trade_date": day, "delivery": via,
                    "row_count": len(parsed["rows"]), "complete_session": parsed["complete"],
                    "last_1min_avg": parsed["last_1min_avg"], "rows": parsed["rows"]},
        )

    def _links(self, html_text: str, pattern: str, page: str) -> list[str]:
        found = list(dict.fromkeys(re.findall(pattern, html_text)))
        if not found:
            raise TaifexVixParseError(f"{page}: no download links found (page changed?)")
        return found

    def collect(self, *, fetched_at: datetime | str | None = None) -> list[CollectedItem]:
        import io
        import zipfile
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        taipei = datetime.fromisoformat(fetched_iso.replace("Z", "+00:00")) + timedelta(hours=8)
        current_month = taipei.strftime("%Y%m")
        done = self._ledger()
        pending: set[str] = set()
        items: list[CollectedItem] = []
        seen_days: set[str] = set()
        self.ledger_skipped = 0

        page = self.fetcher.fetch(self.DAILY_PAGE)
        for month in self._links(page.text, r"log2data/(\d{6})new\.txt", "vixDaily3MNew"):
            key = f"daily:{month}"
            if key in done:
                self.ledger_skipped += 1
                continue
            url = self.DAILY_URL.format(yyyymm=month)
            response = self.fetcher.fetch(url)
            rows = self.parse_daily(response.body, month)
            items.append(self._item(
                item_id=f"taifex-vix-daily-{month}-{self._sha(response.body)[:12]}", url=url,
                fetched_iso=fetched_iso, published=rows[-1]["trade_date"], body=response.body, ext="txt",
                text=f"TAIFEX Taiwan VIX daily close {month}", status=response.http_status,
                record={"dataset": "tw_vix_daily", "month": month, "row_count": len(rows),
                        "first_date": rows[0]["trade_date"], "last_date": rows[-1]["trade_date"],
                        "rows": rows}))
            if month < current_month:
                pending.add(key)

        page = self.fetcher.fetch(self.INTRADAY_PAGE)
        for day in self._links(page.text, r"getVixData\?filesname=(\d{8})", "vixMinNew"):
            seen_days.add(day)
            key = f"intraday:{day}"
            if key in done:
                self.ledger_skipped += 1
                continue
            url = self.DAY_URL.format(yyyymmdd=day)
            response = self.fetcher.fetch(url)
            parsed = self.parse_intraday(response.body, day)
            items.append(self._intraday_item(parsed, response.body, url, fetched_iso,
                                             response.http_status, "daily_file"))
            if parsed["complete"]:
                pending.add(key)

        page = self.fetcher.fetch(self.INTRADAY_3M_PAGE)
        zips = self._links(page.text, r"logdata/(\d{6}[^'\"/]*\.zip)", "vixMin3MNew")
        for name in zips:
            month = name[:6]
            key = f"zip:{month}"
            if key in done:
                self.ledger_skipped += 1
                continue
            url = self.FILE_BASE + "logdata/" + quote(name)
            response = self.fetcher.fetch(url)
            try:
                archive = zipfile.ZipFile(io.BytesIO(response.body))
                members = [(i.filename, archive.read(i)) for i in archive.infolist() if not i.is_dir()]
            except zipfile.BadZipFile as exc:
                raise TaifexVixParseError(f"zip {month}: not a zip archive") from exc
            if not members:
                raise TaifexVixParseError(f"zip {month}: empty archive")
            day_items, member_days = [], []
            for member, body in members:
                match = re.fullmatch(r"tvix_(\d{8})", member)
                if not match or not match.group(1).startswith(month):
                    raise TaifexVixParseError(f"zip {month}: unexpected member {member!r}")
                day = match.group(1)
                if day in member_days:
                    raise TaifexVixParseError(f"zip {month}: duplicate member {member!r}")
                member_days.append(day)
                parsed = self.parse_intraday(body, day)
                if not parsed["complete"]:
                    raise TaifexVixParseError(f"zip {month}: incomplete session {day}")
                if day not in seen_days and f"intraday:{day}" not in done:
                    day_items.append(self._intraday_item(parsed, body, url + "#" + member, fetched_iso,
                                                         response.http_status, "monthly_zip"))
                pending.add(f"intraday:{day}")
            items.append(self._item(
                item_id=f"taifex-vix-intraday-zip-{month}-{self._sha(response.body)[:12]}", url=url,
                fetched_iso=fetched_iso, published=f"{month[:4]}-{month[4:]}-01", body=response.body,
                ext="zip", text=f"TAIFEX Taiwan VIX intraday monthly archive {month}",
                status=response.http_status,
                record={"dataset": "tw_vix_intraday_archive", "month": month, "member_days": sorted(member_days)}))
            items.extend(day_items)
            pending.add(key)
        self._pending = pending
        return items


class TwTradingHolidayScheduleSpider(TwseIndexBreadthSpider):
    """Official TWSE and TPEx trading holiday and market opening/closing schedules (2024-2026)."""

    source_id = "collector-tw-trading-holidays"
    dataset = "tw_trading_holidays"
    DEFAULT_YEARS: tuple[int, ...] = (2024, 2025, 2026)
    TWSE_URL_TEMPLATE = (
        "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date={yyyy}0101&response=json"
    )
    TPEX_URL_TEMPLATE = "https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate?date={yyyy}&response=json"
    TWSE_PUBLICATION_REF = "https://www.twse.com.tw/zh/trading/holiday.html"
    TPEX_PUBLICATION_REF = "https://www.tpex.org.tw/zh-tw/announce/market/holiday.html"
    TWSE_FIELDS = ["日期", "名稱", "說明"]
    TPEX_ALLOWED_HEADERS = (
        ["紀念節日名稱", "日期", "星期", "說明"],
        ["紀念節日名稱", "日期", "星期", "休市/開市說明"],
    )

    @classmethod
    def build_twse_url(cls, year: int) -> str:
        return cls.TWSE_URL_TEMPLATE.format(yyyy=int(year))

    @classmethod
    def build_tpex_url(cls, year: int) -> str:
        return cls.TPEX_URL_TEMPLATE.format(yyyy=int(year))

    @classmethod
    def _parse_tpex_html_table(cls, html: str, year: int) -> list[dict[str, Any]]:
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        pending: dict[tuple[int, int], str] = {}
        grid: list[list[str | None]] = []
        for r_idx, tr in enumerate(trs):
            cells = re.findall(r"<(t[hd])([^>]*)>(.*?)</\1>", tr, re.S)
            if not cells:
                continue
            row_vals: list[str | None] = [None] * 4
            for col in range(4):
                if (r_idx, col) in pending:
                    row_vals[col] = pending.pop((r_idx, col))
            c_idx = 0
            for _tag, attrs, inner in cells:
                while c_idx < 4 and row_vals[c_idx] is not None:
                    c_idx += 1
                text = re.sub(r"<[^>]+>", "", inner).replace("&nbsp;", " ").strip()
                rs_match = re.search(r"rowspan=[\"']?(\d+)", attrs, re.I)
                cs_match = re.search(r"colspan=[\"']?(\d+)", attrs, re.I)
                rs = int(rs_match.group(1)) if rs_match else 1
                cs = int(cs_match.group(1)) if cs_match else 1
                for dr in range(rs):
                    for dc in range(cs):
                        if c_idx + dc < 4:
                            if dr == 0:
                                row_vals[c_idx + dc] = text
                            else:
                                pending[(r_idx + dr, c_idx + dc)] = text
                c_idx += cs
            grid.append(row_vals)

        if len(grid) < 3:
            raise TwseRegimeParseError(f"TPEx tradingDate {year}: missing holiday schedule table")
        roc_year = int(year) - 1911
        title_cell = str(grid[0][0] or "").strip()
        if f"中華民國{roc_year}年" not in title_cell:
            raise TwseRegimeParseError(f"TPEx tradingDate {year}: unexpected table title {title_cell!r}")
        header = [str(x or "").strip() for x in grid[1]]
        if header not in cls.TPEX_ALLOWED_HEADERS:
            raise TwseRegimeParseError(f"TPEx tradingDate {year}: changed table headers {header!r}")

        rows_out: list[dict[str, Any]] = []
        for raw_row in grid[2:]:
            name, date_cell, weekday_cell, description = [str(x or "").strip() for x in raw_row]
            if not name or not date_cell:
                raise TwseRegimeParseError(f"TPEx tradingDate {year}: malformed row {raw_row!r}")
            month_days = re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", date_cell)
            if not month_days:
                raise TwseRegimeParseError(f"TPEx tradingDate {year}: unparseable date cell {date_cell!r}")
            weekdays = [w.strip() for w in re.split(r"\s+", weekday_cell) if w.strip()]
            for idx, (m_str, d_str) in enumerate(month_days):
                iso_date = f"{int(year):04d}-{int(m_str):02d}-{int(d_str):02d}"
                try:
                    datetime.strptime(iso_date, "%Y-%m-%d")
                except ValueError as exc:
                    raise TwseRegimeParseError(f"TPEx tradingDate {year}: invalid date {iso_date!r}") from exc
                weekday = weekdays[idx] if idx < len(weekdays) else (weekdays[-1] if weekdays else "")
                is_closed = not (
                    "開始交易" in name
                    or "最後交易" in name
                    or "開始交易" in description
                )
                rows_out.append(
                    {
                        "date": iso_date,
                        "holiday_name": name,
                        "weekday": weekday,
                        "description": description,
                        "is_closed": is_closed,
                        "raw_row": {
                            "title": title_cell,
                            "headers": header,
                            "cells": [name, date_cell, weekday_cell, description],
                        },
                    }
                )
        if not rows_out:
            raise TwseRegimeParseError(f"TPEx tradingDate {year}: zero schedule rows parsed")
        return rows_out

    def collect(
        self,
        *,
        years: Sequence[int] | None = None,
        venues: Sequence[str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        target_years = tuple(int(y) for y in (years if years is not None else self.DEFAULT_YEARS))
        target_venues = tuple(v.upper() for v in (venues if venues is not None else ("TWSE", "TPEX")))
        if not target_years or not target_venues:
            raise TwseRegimeParseError("holiday schedule collection requires non-empty years and venues")

        items: list[CollectedItem] = []
        self.no_data_confirmed = False
        for year in dict.fromkeys(target_years):
            if "TWSE" in target_venues:
                url = self.build_twse_url(year)
                response = self.fetcher.fetch(url)
                payload = json.loads(response.text)
                if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
                    raise TwseRegimeParseError(f"TWSE holidaySchedule {year}: expected stat=ok")
                rows = _regime_rows(payload, self.TWSE_FIELDS, f"TWSE holidaySchedule {year}")
                for idx, row in enumerate(rows):
                    raw_date, raw_name, raw_desc = [str(c).strip() for c in row]
                    iso_date = _normalize_iso_date(raw_date)
                    datetime.strptime(iso_date, "%Y-%m-%d")
                    if not iso_date.startswith(f"{year:04d}-") or not raw_name:
                        raise TwseRegimeParseError(
                            f"TWSE holidaySchedule {year}: out-of-year or unnamed row {row!r}"
                        )
                    is_closed = not ("開始交易" in raw_name or "最後交易" in raw_name)
                    market_status = "closed" if is_closed else "open_session_marker"
                    raw_values = {
                        "title": payload.get("title"),
                        "fields": list(self.TWSE_FIELDS),
                        "row": list(row),
                    }
                    record = {
                        "dataset": self.dataset,
                        "venue": "TWSE",
                        "exchange": "TWSE",
                        "source_dataset": "holidaySchedule",
                        "year": year,
                        "date": iso_date,
                        "trade_date": iso_date,
                        "holiday_name": raw_name,
                        "weekday": None,
                        "description": raw_desc,
                        "is_closed": is_closed,
                        "market_status": market_status,
                        "official_publication_ref": self.TWSE_PUBLICATION_REF,
                        "api_endpoint": url,
                        "raw_values": raw_values,
                    }
                    items.append(
                        CollectedItem(
                            item_id=f"twse-holiday-{iso_date}-{idx}",
                            url=url,
                            fetched_at=fetched_iso,
                            published_at=f"{year:04d}-01-01T00:00:00Z",
                            text=f"TWSE holidaySchedule {iso_date} {raw_name} ({market_status})",
                            lang="zh-TW",
                            hashed_author=None,
                            metrics={"year": year, "is_closed": is_closed},
                            raw_bytes=json.dumps(raw_values, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                            raw_extension="json",
                            http_status=response.http_status,
                            extra_fields=record,
                        )
                    )

            if "TPEX" in target_venues:
                url = self.build_tpex_url(year)
                response = self.fetcher.fetch(url)
                payload = json.loads(response.text)
                if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
                    raise TwseRegimeParseError(f"TPEx tradingDate {year}: expected stat=ok")
                data_obj = payload.get("data")
                if not isinstance(data_obj, Mapping) or not isinstance(data_obj.get("html"), str):
                    raise TwseRegimeParseError(f"TPEx tradingDate {year}: missing data.html table")
                parsed_rows = self._parse_tpex_html_table(data_obj["html"], year)
                for idx, entry in enumerate(parsed_rows):
                    iso_date = entry["date"]
                    is_closed = bool(entry["is_closed"])
                    market_status = "closed" if is_closed else "open_session_marker"
                    raw_values = entry["raw_row"]
                    record = {
                        "dataset": self.dataset,
                        "venue": "TPEx",
                        "exchange": "TPEX",
                        "source_dataset": "tradingDate",
                        "year": year,
                        "date": iso_date,
                        "trade_date": iso_date,
                        "holiday_name": entry["holiday_name"],
                        "weekday": entry["weekday"],
                        "description": entry["description"],
                        "is_closed": is_closed,
                        "market_status": market_status,
                        "official_publication_ref": self.TPEX_PUBLICATION_REF,
                        "api_endpoint": url,
                        "raw_values": raw_values,
                    }
                    items.append(
                        CollectedItem(
                            item_id=f"tpex-holiday-{iso_date}-{idx}",
                            url=url,
                            fetched_at=fetched_iso,
                            published_at=f"{year:04d}-01-01T00:00:00Z",
                            text=f"TPEx tradingDate {iso_date} {entry['holiday_name']} ({market_status})",
                            lang="zh-TW",
                            hashed_author=None,
                            metrics={"year": year, "is_closed": is_closed},
                            raw_bytes=json.dumps(raw_values, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                            raw_extension="json",
                            http_status=response.http_status,
                            extra_fields=record,
                        )
                    )
        return items


class TwTradingSuspensionSpider(TwseIndexBreadthSpider):
    """Official TWSE (TWTAWU) and TPEx (sprcHis) trading suspension and resumption records (2024-2026)."""

    source_id = "collector-tw-trading-suspension"
    dataset = "tw_trading_suspension"
    DEFAULT_YEARS: tuple[int, ...] = (2024, 2025, 2026)
    TWSE_URL_TEMPLATE = (
        "https://www.twse.com.tw/rwd/zh/afterTrading/TWTAWU"
        "?startDate={start_yyyymmdd}&endDate={end_yyyymmdd}&response=json"
    )
    TPEX_URL_TEMPLATE = (
        "https://www.tpex.org.tw/www/zh-tw/bulletin/sprcHis?date={yyyy}&cate=1&response=json"
    )
    TWSE_PUBLICATION_REF = "https://www.twse.com.tw/zh/trading/historical/twtawu.html"
    TPEX_PUBLICATION_REF = "https://www.tpex.org.tw/zh-tw/announce/market/halt/historical.html"
    TWSE_FIELDS = ["編號", "證券代號", "證券名稱", "暫停交易日期", "暫停交易時間", "恢復交易日期", "恢復交易時間"]
    TPEX_FIELDS = [
        "編號",
        "有價證券類別",
        "有價證券代號",
        "有價證券名稱",
        "暫停交易日期",
        "暫停交易時間",
        "恢復交易日期",
        "恢復交易時間",
    ]
    skipped_not_listed_count = 0

    @classmethod
    def build_twse_url(cls, *, start_yyyymmdd: str = "20240101", end_yyyymmdd: str) -> str:
        return cls.TWSE_URL_TEMPLATE.format(
            start_yyyymmdd=str(start_yyyymmdd).replace("-", ""),
            end_yyyymmdd=str(end_yyyymmdd).replace("-", ""),
        )

    @classmethod
    def build_tpex_url(cls, year: int) -> str:
        return cls.TPEX_URL_TEMPLATE.format(yyyy=int(year))

    @staticmethod
    def _parse_optional_roc_date(raw_value: Any, *, label: str) -> str | None:
        text = str(raw_value or "").strip()
        if not text or text in {"-", "--", "---"}:
            return None
        try:
            iso = _normalize_iso_date(text)
            datetime.strptime(iso, "%Y-%m-%d")
            return iso
        except Exception as exc:
            raise TwseRegimeParseError(f"{label}: invalid date {raw_value!r}") from exc

    def collect(
        self,
        *,
        years: Sequence[int] | None = None,
        venues: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        symbols_filter: Sequence[str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        target_years = tuple(int(y) for y in (years if years is not None else self.DEFAULT_YEARS))
        target_venues = tuple(v.upper() for v in (venues if venues is not None else ("TWSE", "TPEX")))
        if not target_years or not target_venues:
            raise TwseRegimeParseError("trading suspension collection requires non-empty years and venues")

        start_compact = (
            _normalize_iso_date(start_date).replace("-", "")
            if start_date is not None
            else f"{min(target_years):04d}0101"
        )
        end_compact = (
            _normalize_iso_date(end_date).replace("-", "")
            if end_date is not None
            else latest_published_trading_date(fetched_iso).replace("-", "")
        )

        # SOURCE-WARRANT-LISTING-EXPIRY: the exchange listing (not a symbol pattern) decides which codes
        # are collected; None keeps every code (filter disabled / rollback).
        allowed_symbols = (
            {str(s).strip() for s in symbols_filter if str(s).strip()}
            if symbols_filter is not None
            else None
        )
        self.skipped_not_listed_count = 0
        items: list[CollectedItem] = []
        self.no_data_confirmed = False

        if "TWSE" in target_venues:
            url = self.build_twse_url(start_yyyymmdd=start_compact, end_yyyymmdd=end_compact)
            response = self.fetcher.fetch(url)
            payload = json.loads(response.text)
            if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().upper() != "OK":
                raise TwseRegimeParseError(f"TWSE TWTAWU: expected stat=OK, got {payload.get('stat')!r}")
            if payload.get("fields") != self.TWSE_FIELDS:
                raise TwseRegimeParseError(f"TWSE TWTAWU: changed fields {payload.get('fields')!r}")
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                raise TwseRegimeParseError("TWSE TWTAWU: missing data rows")
            for row in rows:
                if not isinstance(row, list) or len(row) != len(self.TWSE_FIELDS):
                    raise TwseRegimeParseError(f"TWSE TWTAWU: malformed row {row!r}")
                serial_no, raw_code, raw_name, susp_date_raw, susp_time_raw, res_date_raw, res_time_raw = row
                symbol = str(raw_code or "").strip()
                security_name = str(raw_name or "").strip()
                if not symbol or not security_name:
                    raise TwseRegimeParseError(f"TWSE TWTAWU: missing symbol/name in row {row!r}")
                susp_iso = self._parse_optional_roc_date(susp_date_raw, label="TWSE TWTAWU")
                res_iso = self._parse_optional_roc_date(res_date_raw, label="TWSE TWTAWU")
                if susp_iso is None and res_iso is None:
                    raise TwseRegimeParseError(f"TWSE TWTAWU: row lacks both suspension and resumption dates {row!r}")
                if allowed_symbols is not None and symbol not in allowed_symbols:
                    self.skipped_not_listed_count += 1
                    continue
                event_date = susp_iso or res_iso or f"{min(target_years):04d}-01-01"
                raw_values = {
                    "title": payload.get("title"),
                    "fields": list(self.TWSE_FIELDS),
                    "row": list(row),
                }
                record = {
                    "dataset": self.dataset,
                    "venue": "TWSE",
                    "exchange": "TWSE",
                    "source_dataset": "TWTAWU",
                    "serial_no": serial_no,
                    "symbol": symbol,
                    "security_name": security_name,
                    "security_category": "上市證券",
                    "suspended_date": susp_iso,
                    "suspended_date_raw": str(susp_date_raw).strip(),
                    "suspended_time_raw": str(susp_time_raw).strip(),
                    "resumed_date": res_iso,
                    "resumed_date_raw": str(res_date_raw).strip(),
                    "resumed_time_raw": str(res_time_raw).strip(),
                    "trade_date": event_date,
                    "official_publication_ref": self.TWSE_PUBLICATION_REF,
                    "api_endpoint": url,
                    "raw_values": raw_values,
                }
                items.append(
                    CollectedItem(
                        item_id=f"twse-suspension-{symbol}-{susp_iso or 'none'}-{res_iso or 'none'}-{serial_no}",
                        url=url,
                        fetched_at=fetched_iso,
                        published_at=_price_published_at(event_date, fetched_iso),
                        text=(
                            f"TWSE TWTAWU {symbol} {security_name}: "
                            f"suspended={susp_iso} ({susp_time_raw}), resumed={res_iso} ({res_time_raw})"
                        ),
                        lang="zh-TW",
                        hashed_author=None,
                        metrics={"suspended": susp_iso is not None, "resumed": res_iso is not None},
                        raw_bytes=json.dumps(raw_values, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                        raw_extension="json",
                        http_status=response.http_status,
                        extra_fields={**record, "symbols": [symbol]},
                    )
                )

        if "TPEX" in target_venues:
            for year in dict.fromkeys(target_years):
                url = self.build_tpex_url(year)
                response = self.fetcher.fetch(url)
                payload = json.loads(response.text)
                if not isinstance(payload, dict) or str(payload.get("stat") or "").strip().lower() != "ok":
                    raise TwseRegimeParseError(f"TPEx sprcHis {year}: expected stat=ok")
                tables = payload.get("tables")
                if not isinstance(tables, list) or not tables or not isinstance(tables[0], Mapping):
                    raise TwseRegimeParseError(f"TPEx sprcHis {year}: missing tables[0]")
                t0 = tables[0]
                if t0.get("fields") != self.TPEX_FIELDS:
                    raise TwseRegimeParseError(f"TPEx sprcHis {year}: changed fields {t0.get('fields')!r}")
                rows = t0.get("data")
                if not isinstance(rows, list) or not rows:
                    raise TwseRegimeParseError(f"TPEx sprcHis {year}: missing data rows")
                for row in rows:
                    if not isinstance(row, list) or len(row) != len(self.TPEX_FIELDS):
                        raise TwseRegimeParseError(f"TPEx sprcHis {year}: malformed row {row!r}")
                    (
                        serial_no,
                        raw_cat,
                        raw_code,
                        raw_name,
                        susp_date_raw,
                        susp_time_raw,
                        res_date_raw,
                        res_time_raw,
                    ) = row
                    symbol = str(raw_code or "").strip()
                    security_name = str(raw_name or "").strip()
                    category = str(raw_cat or "").strip()
                    if not symbol or not security_name:
                        raise TwseRegimeParseError(f"TPEx sprcHis {year}: missing symbol/name in row {row!r}")
                    susp_iso = self._parse_optional_roc_date(susp_date_raw, label=f"TPEx sprcHis {year}")
                    res_iso = self._parse_optional_roc_date(res_date_raw, label=f"TPEx sprcHis {year}")
                    if susp_iso is None and res_iso is None:
                        raise TwseRegimeParseError(
                            f"TPEx sprcHis {year}: row lacks both suspension and resumption dates {row!r}"
                        )
                    if allowed_symbols is not None and symbol not in allowed_symbols:
                        self.skipped_not_listed_count += 1
                        continue
                    event_date = susp_iso or res_iso or f"{year:04d}-01-01"
                    raw_values = {
                        "date": payload.get("date"),
                        "fields": list(self.TPEX_FIELDS),
                        "row": list(row),
                    }
                    record = {
                        "dataset": self.dataset,
                        "venue": "TPEx",
                        "exchange": "TPEX",
                        "source_dataset": "sprcHis",
                        "year": year,
                        "serial_no": serial_no,
                        "symbol": symbol,
                        "security_name": security_name,
                        "security_category": category,
                        "suspended_date": susp_iso,
                        "suspended_date_raw": str(susp_date_raw).strip(),
                        "suspended_time_raw": str(susp_time_raw).strip(),
                        "resumed_date": res_iso,
                        "resumed_date_raw": str(res_date_raw).strip(),
                        "resumed_time_raw": str(res_time_raw).strip(),
                        "trade_date": event_date,
                        "official_publication_ref": self.TPEX_PUBLICATION_REF,
                        "api_endpoint": url,
                        "raw_values": raw_values,
                    }
                    items.append(
                        CollectedItem(
                            item_id=f"tpex-suspension-{year}-{symbol}-{susp_iso or 'none'}-{res_iso or 'none'}-{serial_no}",
                            url=url,
                            fetched_at=fetched_iso,
                            published_at=_price_published_at(event_date, fetched_iso),
                            text=(
                                f"TPEx sprcHis {symbol} {security_name}: "
                                f"suspended={susp_iso} ({susp_time_raw}), resumed={res_iso} ({res_time_raw})"
                            ),
                            lang="zh-TW",
                            hashed_author=None,
                            metrics={"year": year, "suspended": susp_iso is not None, "resumed": res_iso is not None},
                            raw_bytes=json.dumps(raw_values, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                            raw_extension="json",
                            http_status=response.http_status,
                            extra_fields={**record, "symbols": [symbol]},
                        )
                    )
        return items


def estimate_two_year_survivorship_recovery_plan(
    *,
    retained_full_page_dates_twse: int = 0,
    retained_full_page_dates_tpex: int = 0,
    trading_days_per_year: int = EXCHANGE_TRADING_DAYS_PER_YEAR,
    years: int = 2,
    min_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    """Return the exact 2-year recovery analysis and bounded re-collection plan (AC 4).

    Nothing is re-collected by this function or by COLLECTOR-TW-SUSPENSION-HOLIDAYS-20261115.
    """
    trading_days = int(trading_days_per_year) * int(years)
    twse_retained = max(0, min(trading_days, int(retained_full_page_dates_twse)))
    tpex_retained = max(0, min(trading_days, int(retained_full_page_dates_tpex)))
    twse_recollect = trading_days - twse_retained
    tpex_recollect = trading_days - tpex_retained
    holiday_requests = 6  # TWSE (2024, 2025, 2026) + TPEx (2024, 2025, 2026)
    suspension_requests = 4  # TWSE TWTAWU (2024..2026 range) + TPEx sprcHis (2024, 2025, 2026)
    interval = float(min_interval_seconds)
    total_requests = twse_recollect + tpex_recollect + holiday_requests + suspension_requests
    return {
        "recollected_in_this_task": False,
        "requests_executed_for_backfill": 0,
        "years": int(years),
        "trading_days_per_year": int(trading_days_per_year),
        "trading_days_total": trading_days,
        "raw_retention_days": 90,
        "retained_raw_capture_recovery": {
            "per_item_price_bundle_raw_files_recoverable_dates": 0,
            "per_item_price_bundle_raw_files_reason": (
                "Prior TwseDailyPriceHistorySpider (lines 993-994) and TpexDailyPriceHistorySpider (lines 1447-1448) "
                "dropped rows with close_price is None before constructing CollectedItem or writing raw/<item_id>.json, "
                "and each raw/<item_id>.json stores only a single security row rather than the full upstream table."
            ),
            "retained_full_page_dates_twse": twse_retained,
            "retained_full_page_dates_tpex": tpex_retained,
            "retained_full_page_new_requests": 0,
        },
        "bounded_recollection_plan": {
            "published_rate_limit": {
                "max_connections": 1,
                "min_interval_seconds": interval,
            },
            "sources": {
                "collector-tw-twse-price-history": {
                    "endpoint_template": TWSE_MI_INDEX_WEBSITE_URL_TEMPLATE,
                    "page_granularity": "per_date_all_stocks",
                    "dates_recoverable_without_requests": twse_retained,
                    "requests_required": twse_recollect,
                    "estimated_wall_seconds": twse_recollect * interval,
                },
                "collector-tw-tpex-price-history": {
                    "endpoint_template": TPEX_DAILY_QUOTES_WEBSITE_URL_TEMPLATE,
                    "page_granularity": "per_date_all_stocks",
                    "dates_recoverable_without_requests": tpex_retained,
                    "requests_required": tpex_recollect,
                    "estimated_wall_seconds": tpex_recollect * interval,
                },
                "collector-tw-trading-holidays": {
                    "twse_endpoint_template": TwTradingHolidayScheduleSpider.TWSE_URL_TEMPLATE,
                    "tpex_endpoint_template": TwTradingHolidayScheduleSpider.TPEX_URL_TEMPLATE,
                    "covered_years": list(TwTradingHolidayScheduleSpider.DEFAULT_YEARS),
                    "requests_required": holiday_requests,
                    "estimated_wall_seconds": holiday_requests * interval,
                },
                "collector-tw-trading-suspension": {
                    "twse_endpoint_template": TwTradingSuspensionSpider.TWSE_URL_TEMPLATE,
                    "tpex_endpoint_template": TwTradingSuspensionSpider.TPEX_URL_TEMPLATE,
                    "covered_years": list(TwTradingSuspensionSpider.DEFAULT_YEARS),
                    "requests_required": suspension_requests,
                    "estimated_wall_seconds": suspension_requests * interval,
                },
            },
            "price_history_recollection_requests": twse_recollect + tpex_recollect,
            "holiday_and_suspension_requests": holiday_requests + suspension_requests,
            "total_requests": total_requests,
            "total_estimated_wall_seconds": total_requests * interval,
        },
    }
