#!/usr/bin/env python3
"""Scan Lovable-hosted frontend assets for forbidden runtime fallback signals."""

from __future__ import annotations

import argparse
import datetime as dt
import html.parser
import json
import re
import sys
import urllib.parse
import urllib.request
from typing import Callable, Pattern, TypedDict


TASK_ID = "LSP-004-V2"
HttpGet = Callable[[str, float], bytes | str]


class ForbiddenSignal(TypedDict):
    signal: str
    source_url: str
    line: int
    column: int
    snippet: str


class ScanResult(TypedDict):
    task_id: str
    deployment_url: str
    checked_at: str
    scanned_urls: list[str]
    bundle_urls: list[str]
    forbidden_signals: list[ForbiddenSignal]
    errors: list[str]
    passed: bool


class _AssetParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        tag_name = tag.lower()

        if tag_name == "script" and attr_map.get("src"):
            self.assets.append(attr_map["src"])
            return

        if tag_name != "link":
            return

        href = attr_map.get("href")
        if not href:
            return
        rel = {part.strip().lower() for part in attr_map.get("rel", "").split()}
        as_attr = attr_map.get("as", "").lower()
        if "modulepreload" in rel or ("preload" in rel and as_attr in {"script", "worker"}):
            self.assets.append(href)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _urllib_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*",
            "User-Agent": "pantheon-lovable-forbidden-path-scanner/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _decode(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    return content.decode("utf-8", errors="replace")


def _looks_like_js_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith((".js", ".mjs"))


def _extract_js_asset_urls(document_url: str, html_text: str) -> list[str]:
    parser = _AssetParser()
    parser.feed(html_text)
    urls: set[str] = set()

    for asset in parser.assets:
        absolute = urllib.parse.urljoin(document_url, asset)
        if _looks_like_js_url(absolute):
            urls.add(absolute)

    # Vite/Lovable can place module asset paths inside inline bootstrap code.
    inline_asset = re.compile(r"""["'](?P<asset>/assets/[^"']+\.(?:js|mjs))["']""")
    for match in inline_asset.finditer(html_text):
        urls.add(urllib.parse.urljoin(document_url, match.group("asset")))

    return sorted(urls)


FORBIDDEN_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("/mocks/", re.compile(r"/mocks/", re.IGNORECASE)),
    ("seed.", re.compile(r"\bseed\.", re.IGNORECASE)),
    ("mockSeed", re.compile(r"\bmockSeed\b")),
    (
        "silent fallback marker",
        re.compile(r"(?<![A-Za-z0-9])(?:silent[\s_-]?fallback|fallback[\s_-]?silent)", re.IGNORECASE),
    ),
    (
        "VITE_BFF_FALLBACK=auto",
        re.compile(
            r"""VITE_BFF_FALLBACK\s*(?::|=)\s*["']?auto["']?""",
            re.IGNORECASE,
        ),
    ),
    (
        "local seed hydration",
        re.compile(
            r"(?<![A-Za-z0-9])(?:local[\s_-]?seed[\s_-]?hydration|hydrate(?:s|d)?[\s_-]?local[\s_-]?seed)",
            re.IGNORECASE,
        ),
    ),
    ("Pathreon brand typo", re.compile(r"\bPathreon\b")),
]


def _line_column(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset + 1 if last_newline == -1 else offset - last_newline
    return line, column


def _snippet(text: str, start: int, end: int, *, window: int = 90) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = text[left:right].replace("\n", "\\n")
    if left > 0:
        snippet = "..." + snippet
    if right < len(text):
        snippet += "..."
    return snippet


def _scan_text(source_url: str, text: str) -> list[ForbiddenSignal]:
    signals: list[ForbiddenSignal] = []
    seen: set[tuple[str, int, int]] = set()

    for signal_name, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            key = (signal_name, match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            line, column = _line_column(text, match.start())
            signals.append(
                {
                    "signal": signal_name,
                    "source_url": source_url,
                    "line": line,
                    "column": column,
                    "snippet": _snippet(text, match.start(), match.end()),
                }
            )

    return sorted(signals, key=lambda item: (item["source_url"], item["line"], item["column"], item["signal"]))


def scan_deployment(
    deployment_url: str,
    *,
    http_get: HttpGet | None = None,
    timeout: float = 15.0,
) -> ScanResult:
    """Fetch a Lovable page or JS bundle and fail closed on forbidden fallback signals."""

    fetch = http_get or _urllib_get
    errors: list[str] = []
    fetched: dict[str, str] = {}
    bundle_urls: list[str] = []

    try:
        document = fetch(deployment_url, timeout)
        fetched[deployment_url] = _decode(document)
    except Exception as exc:  # pragma: no cover - exercised by operators, not unit tests
        errors.append(f"fetch failed: {deployment_url}: {exc}")
        fetched[deployment_url] = ""

    if _looks_like_js_url(deployment_url):
        bundle_urls = [deployment_url] if fetched.get(deployment_url) else []
    else:
        document_text = fetched.get(deployment_url, "")
        if document_text:
            bundle_urls = _extract_js_asset_urls(deployment_url, document_text)
        if not bundle_urls:
            errors.append(f"no hosted JS bundles discovered from {deployment_url}")

    for bundle_url in bundle_urls:
        if bundle_url == deployment_url:
            continue
        try:
            fetched[bundle_url] = _decode(fetch(bundle_url, timeout))
        except Exception as exc:  # pragma: no cover - exercised by operators, not unit tests
            errors.append(f"fetch failed: {bundle_url}: {exc}")

    forbidden_signals: list[ForbiddenSignal] = []
    for source_url, text in fetched.items():
        if text:
            forbidden_signals.extend(_scan_text(source_url, text))

    scanned_urls = sorted(fetched)
    passed = not errors and not forbidden_signals
    return {
        "task_id": TASK_ID,
        "deployment_url": deployment_url,
        "checked_at": _utc_now(),
        "scanned_urls": scanned_urls,
        "bundle_urls": bundle_urls,
        "forbidden_signals": forbidden_signals,
        "errors": errors,
        "passed": passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_url", help="Lovable page URL or hosted JS bundle URL")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output", help="Write the JSON scan result to this path")
    args = parser.parse_args(argv)

    result = scan_deployment(args.deployment_url, timeout=args.timeout)
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
