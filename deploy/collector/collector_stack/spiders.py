"""Scrapling web spiders for Threads, Instagram popular tags, CMoney, PTT, and DGBAS (SD §5.1, §5.3, §6.2)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import urlparse

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


class ThreadsWebSpider:
    """Scrapling browser spider for logged-out Threads search first page (SD §5.3)."""

    source_id = "collector-tw-threads"

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
            or f"https://www.threads.com/search?q={query}&serp_type=default"
        )
        if "page=" in target_url or "after=" in target_url or "cursor=" in target_url:
            raise CollectorConfigError(
                "Threads logged-out spider permits first page only; pagination cursors are forbidden"
            )
        response = self.fetcher.fetch(target_url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)

        articles = extract_html_elements(response.text, "article", class_name="thread-post")
        items: list[CollectedItem] = []

        for article in articles:
            post_id = article.attrs.get("data-post-id") or ""
            raw_handle = (article.attrs.get("data-author-handle") or "").lstrip("@")
            display_name = article.attrs.get("data-display-name") or ""
            published_at = article.attrs.get("data-published-at") or fetched_iso
            post_url = article.attrs.get("data-url") or target_url
            likes = int(article.attrs.get("data-likes") or 0)
            replies = int(article.attrs.get("data-replies") or 0)

            body_elems = extract_html_elements(article.inner_html, "p", class_name="post-text")
            post_text = body_elems[0].text if body_elems else article.text
            if display_name:
                post_text = post_text.replace(display_name, "").strip()
            if raw_handle:
                post_text = post_text.replace(f"@{raw_handle}", "").replace(raw_handle, "").strip()

            hashed_author = self.hasher.hash_author("threads", raw_handle)
            if raw_handle and f"@{raw_handle}" in post_url:
                post_url = post_url.replace(f"@{raw_handle}/", "")
            elif raw_handle and raw_handle in post_url:
                post_url = f"https://www.threads.com/t/{post_id}"
            article_html = f'<article class="thread-post" data-post-id="{post_id}">{article.inner_html}</article>'
            scrubbed_raw = scrub_raw_html_or_json(
                article_html.encode("utf-8"),
                platform="threads",
                hasher=self.hasher,
                handles=[raw_handle],
                display_names=[display_name] if display_name else (),
            )
            forbidden = tuple(x for x in (raw_handle, display_name) if x)
            items.append(
                CollectedItem(
                    item_id=f"threads-{post_id}",
                    url=post_url,
                    fetched_at=fetched_iso,
                    published_at=published_at,
                    text=post_text,
                    lang="zh-TW",
                    hashed_author=hashed_author,
                    metrics={
                        "like_count": likes,
                        "reply_count": replies,
                        "platform": "threads",
                    },
                    raw_bytes=scrubbed_raw,
                    raw_extension="html",
                    http_status=response.http_status,
                    extra_fields={"symbols": _detect_tw_symbols(post_text)},
                    forbidden_literals=forbidden,
                    require_hashed_author=True,
                )
            )
        return items


class InstagramPopularTagSpider:
    """Scrapling browser spider restricted strictly to logged-out Instagram popular tag pages (SD §5.3, O-3)."""

    source_id = "collector-tw-instagram-popular"

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
        target_url = url or f"https://www.instagram.com/explore/tags/{tag}/"
        self.validate_popular_tag_url(target_url)
        response = self.fetcher.fetch(target_url)
        self.validate_popular_tag_url(response.final_url)
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)

        reels = extract_html_elements(response.text, "article", class_name="ig-popular-reel")
        items: list[CollectedItem] = []

        for reel in reels:
            reel_id = reel.attrs.get("data-reel-id") or ""
            raw_handle = (reel.attrs.get("data-author-handle") or "").lstrip("@")
            display_name = reel.attrs.get("data-display-name") or ""
            published_at = reel.attrs.get("data-published-at") or fetched_iso
            view_count = int(reel.attrs.get("data-view-count") or 0)
            like_count = int(reel.attrs.get("data-like-count") or 0)

            caption_elems = extract_html_elements(reel.inner_html, "p", class_name="reel-caption")
            caption = caption_elems[0].text if caption_elems else reel.text
            if display_name:
                caption = caption.replace(display_name, "").strip()
            if raw_handle:
                caption = caption.replace(f"@{raw_handle}", "").replace(raw_handle, "").strip()

            hashed_author = self.hasher.hash_author("instagram", raw_handle)
            reel_html = f'<article class="ig-popular-reel" data-reel-id="{reel_id}">{reel.inner_html}</article>'
            scrubbed_raw = scrub_raw_html_or_json(
                reel_html.encode("utf-8"),
                platform="instagram",
                hasher=self.hasher,
                handles=[raw_handle],
                display_names=[display_name] if display_name else (),
            )
            forbidden = tuple(x for x in (raw_handle, display_name) if x)
            items.append(
                CollectedItem(
                    item_id=f"ig-reel-{reel_id}",
                    url=f"https://www.instagram.com/popular/{tag}/#reel-{reel_id}",
                    fetched_at=fetched_iso,
                    published_at=published_at,
                    text=caption,
                    lang="zh-TW",
                    hashed_author=hashed_author,
                    metrics={
                        "view_count": view_count,
                        "like_count": like_count,
                        "tag": tag,
                        "platform": "instagram",
                    },
                    raw_bytes=scrubbed_raw,
                    raw_extension="html",
                    http_status=response.http_status,
                    extra_fields={"symbols": _detect_tw_symbols(caption)},
                    forbidden_literals=forbidden,
                    require_hashed_author=True,
                )
            )
        return items


class CmoneyForumSpider:
    """Scrapling browser spider for 股市爆料同學會 (CMoney forum, disabled by default per O-4)."""

    source_id = "collector-tw-cmoney-forum"

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

        posts = extract_html_elements(response.text, "article", class_name="cmoney-post")
        items: list[CollectedItem] = []

        for post in posts:
            post_id = post.attrs.get("data-article-id") or ""
            raw_handle = (post.attrs.get("data-member-id") or "").strip()
            display_name = (post.attrs.get("data-member-name") or "").strip()
            published_at = post.attrs.get("data-published-at") or fetched_iso
            sentiment_tag = post.attrs.get("data-sentiment") or "neutral"
            likes = int(post.attrs.get("data-likes") or 0)

            body_elems = extract_html_elements(post.inner_html, "div", class_name="post-content")
            post_text = body_elems[0].text if body_elems else post.text
            if display_name:
                post_text = post_text.replace(display_name, "").strip()
            if raw_handle:
                post_text = post_text.replace(raw_handle, "").strip()

            hashed_author = self.hasher.hash_author("cmoney", raw_handle)
            post_html = f'<article class="cmoney-post" data-article-id="{post_id}">{post.inner_html}</article>'
            scrubbed_raw = scrub_raw_html_or_json(
                post_html.encode("utf-8"),
                platform="cmoney",
                hasher=self.hasher,
                handles=[raw_handle],
                display_names=[display_name] if display_name else (),
            )
            symbols = sorted(set([stock_code] + _detect_tw_symbols(post_text)))
            forbidden = tuple(x for x in (raw_handle, display_name) if x)
            items.append(
                CollectedItem(
                    item_id=f"cmoney-{post_id}",
                    url=f"https://www.cmoney.tw/forum/stock/{stock_code}#article-{post_id}",
                    fetched_at=fetched_iso,
                    published_at=published_at,
                    text=post_text,
                    lang="zh-TW",
                    hashed_author=hashed_author,
                    metrics={
                        "like_count": likes,
                        "sentiment_tag": sentiment_tag,
                        "platform": "cmoney",
                    },
                    raw_bytes=scrubbed_raw,
                    raw_extension="html",
                    http_status=response.http_status,
                    extra_fields={"symbols": symbols},
                    forbidden_literals=forbidden,
                    require_hashed_author=True,
                )
            )
        return items


class PttStockSpider:
    """Scrapling HTTP spider for PTT Stock board official web front (SD §5.3)."""

    source_id = "collector-tw-ptt-stock"

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

        entries = extract_html_elements(response.text, "div", class_name="r-ent")
        items: list[CollectedItem] = []

        for entry in entries:
            article_id = entry.attrs.get("data-article-id") or ""
            raw_userid = (entry.attrs.get("data-author-id") or "").strip()
            nickname = (entry.attrs.get("data-author-nickname") or "").strip()
            published_at = entry.attrs.get("data-published-at") or fetched_iso
            push_count = int(entry.attrs.get("data-push-count") or 0)
            href = entry.attrs.get("data-href") or f"/bbs/Stock/{article_id}.html"

            title_elems = extract_html_elements(entry.inner_html, "div", class_name="title")
            summary_elems = extract_html_elements(entry.inner_html, "div", class_name="excerpt")
            title_text = title_elems[0].text if title_elems else entry.text
            summary_text = summary_elems[0].text if summary_elems else ""
            combined_text = f"{title_text} {summary_text}".strip()
            if nickname:
                combined_text = combined_text.replace(nickname, "").strip()
            if raw_userid:
                combined_text = combined_text.replace(raw_userid, "").strip()

            hashed_author = self.hasher.hash_author("ptt", raw_userid)
            entry_html = f'<div class="r-ent" data-article-id="{article_id}">{entry.inner_html}</div>'
            scrubbed_raw = scrub_raw_html_or_json(
                entry_html.encode("utf-8"),
                platform="ptt",
                hasher=self.hasher,
                handles=[raw_userid],
                display_names=[nickname] if nickname else (),
            )
            forbidden = tuple(x for x in (raw_userid, nickname) if x)
            items.append(
                CollectedItem(
                    item_id=f"ptt-stock-{article_id}",
                    url=f"https://www.ptt.cc{href}" if href.startswith("/") else href,
                    fetched_at=fetched_iso,
                    published_at=published_at,
                    text=combined_text,
                    lang="zh-TW",
                    hashed_author=hashed_author,
                    metrics={
                        "push_count": push_count,
                        "ai_train_allowed": False,
                        "platform": "ptt",
                    },
                    raw_bytes=scrubbed_raw,
                    raw_extension="html",
                    http_status=response.http_status,
                    extra_fields={
                        "title": title_text,
                        "symbols": _detect_tw_symbols(combined_text),
                    },
                    forbidden_literals=forbidden,
                    require_hashed_author=True,
                )
            )
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
                period = row.attrs.get("data-period") or "2026-10"
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
