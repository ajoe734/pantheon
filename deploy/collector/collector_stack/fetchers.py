"""Fetcher adapters for Scrapling (HTTP/Browser), APIs, RSSHub, and Docling (SD §6.2, §6.3)."""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import CollectorConfigError, EXCHANGE_WEBSITE_SOURCE_IDS, SourceFetchPolicy
from .state import classify_access_block


class FetchAccessBlockedError(RuntimeError):
    """Raised when a fetch encounters HTTP 429, 403, or a login redirect."""

    def __init__(self, block_reason: str, response: "FetchResponse") -> None:
        super().__init__(f"fetch blocked ({block_reason}) for {response.requested_url}")
        self.block_reason = block_reason
        self.response = response


@dataclass(frozen=True)
class FetchResponse:
    requested_url: str
    final_url: str
    http_status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class FetchTransport:
    """Pluggable transport interface; tests inject FixtureTransport so no network call is made."""

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        fetcher_kind: str = "http",
    ) -> FetchResponse:  # pragma: no cover - abstract
        raise NotImplementedError


class FixtureTransport(FetchTransport):
    """Deterministic offline transport backed by captured fixtures."""

    def __init__(
        self,
        routes: Mapping[str, FetchResponse | bytes | str | Callable[[str], FetchResponse]] | None = None,
    ) -> None:
        self._routes: dict[str, Any] = dict(routes or {})
        self.calls: list[dict[str, Any]] = []

    def register(
        self,
        url_or_prefix: str,
        payload: FetchResponse | bytes | str | Callable[[str], FetchResponse],
        *,
        http_status: int = 200,
        final_url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if isinstance(payload, (bytes, str)):
            raw = payload.encode("utf-8") if isinstance(payload, str) else payload
            self._routes[url_or_prefix] = FetchResponse(
                requested_url=url_or_prefix,
                final_url=final_url or url_or_prefix,
                http_status=http_status,
                body=raw,
                headers=dict(headers or {}),
            )
        else:
            self._routes[url_or_prefix] = payload

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        fetcher_kind: str = "http",
    ) -> FetchResponse:
        self.calls.append(
            {
                "url": url,
                "method": method,
                "headers": dict(headers or {}),
                "body": body,
                "fetcher_kind": fetcher_kind,
            }
        )
        target = self._routes.get(url)
        if target is None:
            for prefix, candidate in self._routes.items():
                if url.startswith(prefix):
                    target = candidate
                    break
        if target is None:
            raise KeyError(f"no offline fixture registered for URL: {url!r}")
        if callable(target):
            return target(url)
        if isinstance(target, FetchResponse):
            return FetchResponse(
                requested_url=url,
                final_url=target.final_url,
                http_status=target.http_status,
                body=target.body,
                headers=target.headers,
            )
        raw = target.encode("utf-8") if isinstance(target, str) else bytes(target)
        return FetchResponse(requested_url=url, final_url=url, http_status=200, body=raw)


class LiveScraplingTransport(FetchTransport):
    """Production transport using Scrapling (Fetcher / DynamicFetcher) and urllib for APIs."""

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        fetcher_kind: str = "http",
    ) -> FetchResponse:  # pragma: no cover - never called in unit tests
        req_headers = dict(headers or {})
        if fetcher_kind == "browser" and method == "GET":
            try:
                from scrapling.fetchers import DynamicFetcher  # type: ignore[import-not-found]

                page = DynamicFetcher.fetch(
                    url,
                    headless=True,
                    stealth=False,
                    disable_resources=True,
                    network_idle=True,
                )
                raw = (
                    page.body
                    if isinstance(getattr(page, "body", None), bytes)
                    else str(getattr(page, "html_content", "")).encode("utf-8")
                )
                return FetchResponse(
                    requested_url=url,
                    final_url=str(getattr(page, "url", url)),
                    http_status=int(getattr(page, "status", 200)),
                    body=raw,
                    headers={},
                )
            except ImportError:
                pass
        elif fetcher_kind == "http" and method == "GET":
            try:
                from scrapling.fetchers import Fetcher  # type: ignore[import-not-found]

                page = Fetcher(auto_match=False).get(url, headers=req_headers, stealthy_headers=False)
                raw = (
                    page.body
                    if isinstance(getattr(page, "body", None), bytes)
                    else str(getattr(page, "html_content", "")).encode("utf-8")
                )
                return FetchResponse(
                    requested_url=url,
                    final_url=str(getattr(page, "url", url)),
                    http_status=int(getattr(page, "status", 200)),
                    body=raw,
                    headers={},
                )
            except ImportError:
                pass

        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return FetchResponse(
                    requested_url=url,
                    final_url=resp.geturl(),
                    http_status=resp.status,
                    body=resp.read(),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            return FetchResponse(
                requested_url=url,
                final_url=exc.geturl() or url,
                http_status=exc.code,
                body=exc.read() if exc.fp else b"",
                headers={k.lower(): v for k, v in (exc.headers.items() if exc.headers else ())},
            )


class PolicyEnforcedFetcher:
    """Enforces per-source fetch policy, user-agent rules, rate limits, and 429/403/login detection."""

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
        sleep_fn: Callable[[float], None] = lambda _: None,
        allow_browser: bool = False,
    ) -> None:
        if policy.logged_in:
            raise CollectorConfigError(f"source {policy.source_id} cannot run with logged_in=True")
        if allow_browser and policy.fetcher != "browser":
            raise CollectorConfigError(
                f"ScraplingBrowserFetcher requires fetcher='browser', got {policy.fetcher!r} for {policy.source_id}"
            )
        if not allow_browser and policy.fetcher == "browser":
            raise CollectorConfigError(
                f"source {policy.source_id} requires ScraplingBrowserFetcher (fetcher='browser')"
            )
        if policy.max_connections <= 0:
            raise CollectorConfigError(
                f"source {policy.source_id} max_connections must be >= 1, got {policy.max_connections}"
            )
        if policy.source_id in EXCHANGE_WEBSITE_SOURCE_IDS:
            if policy.max_connections != 1:
                raise CollectorConfigError(
                    f"source {policy.source_id} requires max_connections=1 (single connection)"
                )
            if policy.min_interval_seconds < 5.0:
                raise CollectorConfigError(
                    f"source {policy.source_id} requires min_interval_seconds >= 5.0"
                )
        self.policy = policy
        self.transport = transport
        self.contact_user_agent = contact_user_agent
        self.sleep_fn = sleep_fn
        self.request_count = 0
        self.max_connections = policy.max_connections
        self._active_connections = 0
        self.sleep_calls: list[float] = []

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> FetchResponse:
        self.policy.validate_target_url(url)
        if self.request_count >= self.policy.max_requests_per_run:
            raise CollectorConfigError(
                f"source {self.policy.source_id} exceeded max_requests_per_run={self.policy.max_requests_per_run}"
            )
        if self._active_connections >= self.max_connections:
            raise CollectorConfigError(
                f"source {self.policy.source_id} exceeded max_connections={self.max_connections}"
            )
        if self.request_count > 0 and self.policy.min_interval_seconds > 0:
            self.sleep_calls.append(self.policy.min_interval_seconds)
            self.sleep_fn(self.policy.min_interval_seconds)

        req_headers = dict(headers or {})
        for forbidden_hdr in ("cookie", "x-proxy", "proxy-authorization"):
            if forbidden_hdr in {k.lower() for k in req_headers}:
                raise CollectorConfigError(
                    f"forbidden header {forbidden_hdr!r} in request for {self.policy.source_id}"
                )

        if self.policy.fetcher == "browser":
            req_headers.setdefault(
                "User-Agent",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/130.0.0.0 Safari/537.36",
            )
        else:
            req_headers.setdefault("User-Agent", self.contact_user_agent)
        if self.max_connections == 1:
            req_headers.setdefault("Connection", "close")

        self.request_count += 1
        self._active_connections += 1
        try:
            response = self.transport.request(
                url,
                method=method,
                headers=req_headers,
                body=body,
                fetcher_kind=self.policy.fetcher,
            )
        finally:
            self._active_connections -= 1

        block_reason = classify_access_block(
            http_status=response.http_status,
            requested_url=response.requested_url,
            final_url=response.final_url,
            location_header=response.headers.get("location") or response.headers.get("Location"),
        )
        if block_reason is not None:
            raise FetchAccessBlockedError(block_reason, response)

        return response


class ScraplingHttpFetcher(PolicyEnforcedFetcher):
    """Scrapling HTTP fetcher (used first whenever a page does not require JS)."""

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
        sleep_fn: Callable[[float], None] = lambda _: None,
    ) -> None:
        if policy.fetcher != "http":
            raise CollectorConfigError(
                f"ScraplingHttpFetcher requires fetcher='http', got {policy.fetcher!r} for {policy.source_id}"
            )
        super().__init__(
            policy,
            transport=transport,
            contact_user_agent=contact_user_agent,
            sleep_fn=sleep_fn,
            allow_browser=False,
        )


class ScraplingBrowserFetcher(PolicyEnforcedFetcher):
    """Scrapling browser fetcher (used only for JS pages: Threads, Instagram, CMoney)."""

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
        sleep_fn: Callable[[float], None] = lambda _: None,
    ) -> None:
        super().__init__(
            policy,
            transport=transport,
            contact_user_agent=contact_user_agent,
            sleep_fn=sleep_fn,
            allow_browser=True,
        )


@dataclass(frozen=True)
class HtmlElement:
    tag: str
    attrs: Mapping[str, str]
    inner_html: str
    text: str


class _BlockExtractor(HTMLParser):
    """Lightweight HTML element extractor used alongside or as fallback for Scrapling Adaptor."""

    def __init__(self, target_tag: str, required_class: str | None = None) -> None:
        super().__init__()
        self.target_tag = target_tag.lower()
        self.required_class = required_class
        self.elements: list[HtmlElement] = []
        self._active_attrs: dict[str, str] | None = None
        self._depth = 0
        self._chunks: list[str] = []
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if self._depth == 0:
            classes = attr_map.get("class", "").split()
            if tag_l == self.target_tag and (
                self.required_class is None or self.required_class in classes
            ):
                self._active_attrs = attr_map
                self._depth = 1
                self._chunks = []
                self._text_chunks = []
                return
        if self._depth > 0:
            if tag_l == self.target_tag:
                self._depth += 1
            attr_str = "".join(f' {k}="{html.escape(v)}"' for k, v in attr_map.items())
            self._chunks.append(f"<{tag_l}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if self._depth > 0:
            if tag_l == self.target_tag:
                self._depth -= 1
                if self._depth == 0 and self._active_attrs is not None:
                    self.elements.append(
                        HtmlElement(
                            tag=self.target_tag,
                            attrs=self._active_attrs,
                            inner_html="".join(self._chunks).strip(),
                            text=" ".join("".join(self._text_chunks).split()),
                        )
                    )
                    self._active_attrs = None
                    return
            self._chunks.append(f"</{tag_l}>")

    def handle_data(self, data: str) -> None:
        if self._depth > 0:
            self._chunks.append(data)
            self._text_chunks.append(data)


def extract_html_elements(
    html_text: str, tag: str, *, class_name: str | None = None
) -> list[HtmlElement]:
    parser = _BlockExtractor(tag, required_class=class_name)
    parser.feed(html_text)
    return parser.elements


@dataclass(frozen=True)
class ParsedBookSection:
    book_id: str
    section_index: int
    section_title: str
    page_start: int
    page_end: int
    text: str


class DoclingBookAdapter:
    """Parses operator-owned PDF/EPUB/Markdown books in the collector library into sections with page refs."""

    def parse_book_file(self, path: Path) -> tuple[bytes, list[ParsedBookSection]]:
        raw_bytes = path.read_bytes()
        book_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", path.stem).strip("-").lower() or "book"

        # 1. If Docling is installed on the collector host and file is PDF/EPUB, try Docling first
        if path.suffix.lower() in {".pdf", ".epub"}:
            try:
                from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]

                converter = DocumentConverter()
                result = converter.convert(str(path))
                md_text = result.document.export_to_markdown()
                return raw_bytes, self._sections_from_marked_text(book_id, md_text)
            except Exception:
                pass

        # 2. Deterministic offline extraction for EPUB (ZIP of XHTML) or PDF/text with page markers
        if path.suffix.lower() == ".epub" and zipfile.is_zipfile(BytesIO(raw_bytes)):
            with zipfile.ZipFile(BytesIO(raw_bytes), "r") as zf:
                combined: list[str] = []
                for name in sorted(zf.namelist()):
                    if name.endswith((".xhtml", ".html", ".xml", ".md", ".txt")):
                        combined.append(zf.read(name).decode("utf-8", errors="replace"))
                return raw_bytes, self._sections_from_marked_text(book_id, "\n".join(combined))

        decoded = raw_bytes.decode("utf-8", errors="replace")
        return raw_bytes, self._sections_from_marked_text(book_id, decoded)

    def _sections_from_marked_text(
        self, book_id: str, text: str
    ) -> list[ParsedBookSection]:
        sections: list[ParsedBookSection] = []
        pattern = re.compile(
            r"(?:^|\n)#+\s*(?P<title>[^\n]+?)(?:\s*\[pages?\s*(?P<pstart>\d+)(?:-(?P<pend>\d+))?\])?\s*\n(?P<body>.*?)(?=(?:\n#+\s)|\Z)",
            re.DOTALL,
        )
        matches = list(pattern.finditer(text))
        if not matches:
            clean = " ".join(re.sub(r"<[^>]+>", " ", text).split()).strip()
            if clean:
                sections.append(
                    ParsedBookSection(
                        book_id=book_id,
                        section_index=1,
                        section_title=book_id,
                        page_start=1,
                        page_end=1,
                        text=clean,
                    )
                )
            return sections

        for idx, match in enumerate(matches, start=1):
            title = match.group("title").strip()
            pstart = int(match.group("pstart") or idx)
            pend = int(match.group("pend") or pstart)
            body = " ".join(re.sub(r"<[^>]+>", " ", match.group("body")).split()).strip()
            if body:
                sections.append(
                    ParsedBookSection(
                        book_id=book_id,
                        section_index=idx,
                        section_title=title,
                        page_start=pstart,
                        page_end=pend,
                        text=body,
                    )
                )
        return sections
