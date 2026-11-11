"""Scrapling web spiders for Threads, Instagram popular tags, CMoney, PTT, DGBAS, and TWSE/TPEx exchange website datasets (SD §5.1, §5.3, §6.2)."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
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


def recent_weekday_dates(reference_iso: str, *, count: int) -> list[str]:
    """Most recent `count` weekday dates (YYYY-MM-DD), newest first, derived from the run time."""
    day = datetime.strptime(reference_iso[:10], "%Y-%m-%d").date()
    out: list[str] = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return out


TAIWAN_UTC_OFFSET_HOURS = 8
T86_PUBLISH_HOUR_TW = 15


def latest_published_trading_date(reference_iso: str) -> str:
    """Latest weekday whose TWSE daily data is published: before 15:00 Taiwan time that is the previous weekday."""
    ref = datetime.fromisoformat(reference_iso.replace("Z", "+00:00"))
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    local = ref.astimezone(timezone(timedelta(hours=TAIWAN_UTC_OFFSET_HOURS)))
    day = local.date()
    if local.hour < T86_PUBLISH_HOUR_TW:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
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
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
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


class TwseDailyPriceHistorySpider:
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
                        if not fields or any("證券代號" in f or "收盤價" in f for f in fields):
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
                        row_map.get("收盤價")
                        if "收盤價" in row_map
                        else row_map.get("close"),
                        allow_zero=False,
                    )
                    if close_price is None:
                        continue
                    open_price = (
                        _clean_num(
                            row_map.get("開盤價")
                            if "開盤價" in row_map
                            else row_map.get("open"),
                            allow_zero=False,
                        )
                        or close_price
                    )
                    high_price = (
                        _clean_num(
                            row_map.get("最高價")
                            if "最高價" in row_map
                            else row_map.get("high"),
                            allow_zero=False,
                        )
                        or close_price
                    )
                    low_price = (
                        _clean_num(
                            row_map.get("最低價")
                            if "最低價" in row_map
                            else row_map.get("low"),
                            allow_zero=False,
                        )
                        or close_price
                    )
                    sign_raw = str(row_map.get("漲跌(+/-)") or "").strip()
                    diff_val = _clean_num(
                        row_map.get("漲跌價差")
                        if "漲跌價差" in row_map
                        else row_map.get("change"),
                        allow_zero=True,
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
                            published_at=f"{trade_date}T06:30:00Z",
                            text=(
                                f"TWSE MI_INDEX {symbol} {security_name} ({trade_date}): "
                                f"close={close_price}, volume_shares={volume_shares}"
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


class TpexDailyPriceHistorySpider:
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
                        row_map.get("收盤")
                        if "收盤" in row_map
                        else (row_map.get("收盤價") if "收盤價" in row_map else row_map.get("close")),
                        allow_zero=False,
                    )
                    if close_price is None:
                        continue
                    open_price = (
                        _clean_num(
                            row_map.get("開盤")
                            if "開盤" in row_map
                            else (row_map.get("開盤價") if "開盤價" in row_map else row_map.get("open")),
                            allow_zero=False,
                        )
                        or close_price
                    )
                    high_price = (
                        _clean_num(
                            row_map.get("最高")
                            if "最高" in row_map
                            else (row_map.get("最高價") if "最高價" in row_map else row_map.get("high")),
                            allow_zero=False,
                        )
                        or close_price
                    )
                    low_price = (
                        _clean_num(
                            row_map.get("最低")
                            if "最低" in row_map
                            else (row_map.get("最低價") if "最低價" in row_map else row_map.get("low")),
                            allow_zero=False,
                        )
                        or close_price
                    )
                    change_val = _clean_num(
                        row_map.get("漲跌")
                        if "漲跌" in row_map
                        else row_map.get("change"),
                        allow_zero=True,
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
                            published_at=f"{trade_date}T06:30:00Z",
                            text=(
                                f"TPEx dailyQuotes {symbol} {security_name} ({trade_date}): "
                                f"close={close_price}, volume_shares={volume_shares}"
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
