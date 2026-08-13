#!/usr/bin/env python3
"""Verify that a deployed frontend serves every referenced JS/CSS asset.

This catches the common SPA failure where ``index.html`` points at an asset
that is missing and the catch-all route returns HTML with status 200 instead.
The verifier is deliberately independent from the BFF health surface.
"""

from __future__ import annotations

import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable


@dataclass(frozen=True)
class FetchResult:
    status: int
    content_type: str
    body: bytes
    final_url: str


@dataclass(frozen=True)
class AssetResult:
    url: str
    ok: bool
    detail: str


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        candidate = values.get("src") if tag == "script" else values.get("href")
        if tag not in {"script", "link"} or not candidate:
            return
        path = urllib.parse.urlsplit(candidate).path.lower()
        if path.endswith((".js", ".mjs", ".css")):
            self.references.append(candidate)


def referenced_asset_urls(index_url: str, html: str) -> list[str]:
    parser = _AssetParser()
    parser.feed(html)
    urls = [urllib.parse.urljoin(index_url, ref) for ref in parser.references]
    return list(dict.fromkeys(urls))


def _looks_like_html(body: bytes) -> bool:
    prefix = body[:512].lstrip().lower()
    return prefix.startswith(b"<!doctype html") or b"<html" in prefix


def _asset_kind(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    return "css" if path.endswith(".css") else "javascript"


def validate_asset(result: FetchResult, *, requested_url: str) -> AssetResult:
    kind = _asset_kind(requested_url)
    content_type = result.content_type.partition(";")[0].strip().lower()
    allowed = (
        {"text/css"}
        if kind == "css"
        else {
            "application/javascript",
            "application/ecmascript",
            "text/javascript",
            "text/ecmascript",
        }
    )
    reasons: list[str] = []
    if result.status != 200:
        reasons.append(f"status={result.status}")
    if not result.body:
        reasons.append("empty body")
    if _looks_like_html(result.body):
        reasons.append("SPA HTML fallback")
    if content_type not in allowed:
        reasons.append(f"content-type={content_type or '<missing>'}")
    return AssetResult(
        url=requested_url,
        ok=not reasons,
        detail="ok" if not reasons else ", ".join(reasons),
    )


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if os.getenv("FE_INSECURE", "").strip().lower() in {"1", "true", "yes"}:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def fetch_url(url: str) -> FetchResult:
    request = urllib.request.Request(url, headers={"User-Agent": "pantheon-fe-verifier/1"})
    try:
        with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:
            return FetchResult(
                status=int(response.status),
                content_type=str(response.headers.get("content-type") or ""),
                body=response.read(),
                final_url=str(response.geturl()),
            )
    except urllib.error.HTTPError as exc:
        return FetchResult(
            status=int(exc.code),
            content_type=str(exc.headers.get("content-type") or ""),
            body=exc.read(),
            final_url=str(exc.geturl()),
        )


def verify_frontend(
    base_url: str,
    *,
    fetcher: Callable[[str], FetchResult] = fetch_url,
) -> list[AssetResult]:
    index_url = base_url.rstrip("/") + "/"
    index = fetcher(index_url)
    if index.status != 200 or not index.body or not _looks_like_html(index.body):
        return [
            AssetResult(
                url=index_url,
                ok=False,
                detail=(
                    f"index status={index.status}, bytes={len(index.body)}, "
                    f"content-type={index.content_type or '<missing>'}"
                ),
            )
        ]
    assets = referenced_asset_urls(index.final_url or index_url, index.body.decode("utf-8"))
    if not assets:
        return [AssetResult(url=index_url, ok=False, detail="index references no JS/CSS assets")]
    return [validate_asset(fetcher(url), requested_url=url) for url in assets]


def main() -> int:
    base_url = os.getenv("FE_BASE", "").strip()
    if not base_url:
        print("ERROR: set FE_BASE to the deployed frontend origin", file=sys.stderr)
        return 2
    try:
        results = verify_frontend(base_url)
    except (OSError, UnicodeError, urllib.error.URLError) as exc:
        print(f"FAIL: frontend verification could not complete: {exc}")
        return 1
    for result in results:
        print(f"{'PASS' if result.ok else 'FAIL'}  {result.url}: {result.detail}")
    failures = [result for result in results if not result.ok]
    if failures:
        print(f"FAIL: {len(failures)} frontend asset check(s) failed")
        return 1
    print(f"OK: {len(results)} referenced frontend asset(s) are directly servable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
