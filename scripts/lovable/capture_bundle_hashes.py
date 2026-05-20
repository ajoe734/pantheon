#!/usr/bin/env python3
"""Capture SHA-256 hashes for a hosted Lovable index page and JS chunks."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html.parser
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, TypedDict


HttpGet = Callable[[str, float], bytes | str]


class AssetHash(TypedDict):
    kind: str
    url: str
    sha256: str
    bytes: int


class BundleCaptureResult(TypedDict):
    task_id: str
    deployment_url: str
    checked_at: str
    index: AssetHash | None
    js_bundle_urls: list[str]
    js_bundles: dict[str, AssetHash]
    assets: list[AssetHash]
    asset_count: int
    errors: list[str]
    passed: bool


class _HtmlAssetParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()
        if tag == "script" and attr_map.get("src"):
            self.candidates.append(attr_map["src"])
            return

        if tag != "link":
            return

        rels = {part.strip().lower() for part in attr_map.get("rel", "").split()}
        href = attr_map.get("href")
        as_attr = attr_map.get("as", "").lower()
        if href and ("modulepreload" in rels or ("preload" in rels and as_attr == "script")):
            self.candidates.append(href)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _urllib_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*",
            "User-Agent": "pantheon-lovable-bundle-hash-recorder/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _to_bytes(content: bytes | str) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def _decode(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    return content.decode("utf-8", errors="replace")


def _hash_asset(kind: str, url: str, content: bytes | str) -> AssetHash:
    raw = _to_bytes(content)
    return {
        "kind": kind,
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _is_js_asset(candidate: str) -> bool:
    path = urllib.parse.urlparse(candidate).path.lower()
    return path.endswith(".js") or path.endswith(".mjs")


def _resolve_js_url(base_url: str, candidate: str) -> str | None:
    if not candidate or not _is_js_asset(candidate):
        return None
    return urllib.parse.urljoin(base_url, candidate)


def _extract_js_urls_from_html(document_url: str, html_text: str) -> list[str]:
    parser = _HtmlAssetParser()
    parser.feed(html_text)
    urls = {
        resolved
        for candidate in parser.candidates
        if (resolved := _resolve_js_url(document_url, candidate))
    }

    # Vite/Lovable can also include asset paths inside inline bootstrap code.
    for match in re.finditer(r"""["'`](?P<asset>/assets/[^"'`\s)]+\.m?js(?:\?[^"'`\s)]*)?)["'`]""", html_text):
        if resolved := _resolve_js_url(document_url, match.group("asset")):
            urls.add(resolved)
    return sorted(urls)


_JS_STRING_LITERAL = re.compile(
    r"""["'`](?P<asset>(?:(?:https?:)?//|/|\.{1,2}/|assets/)[^"'`\s),]+\.m?js(?:\?[^"'`\s),]*)?)["'`]"""
)


def _extract_js_urls_from_js(bundle_url: str, js_text: str) -> list[str]:
    urls: set[str] = set()
    for match in _JS_STRING_LITERAL.finditer(js_text):
        candidate = match.group("asset")
        if resolved := _resolve_js_url(bundle_url, candidate):
            urls.add(resolved)
    return sorted(urls)


def capture_bundle_hashes(
    deployment_url: str,
    *,
    http_get: HttpGet | None = None,
    timeout: float = 15.0,
    checked_at: str | None = None,
    max_js_assets: int = 200,
) -> BundleCaptureResult:
    """Fetch an index page and reachable JS chunks, returning deterministic hashes."""

    fetch = http_get or _urllib_get
    errors: list[str] = []
    index_hash: AssetHash | None = None
    js_bundles: dict[str, AssetHash] = {}
    queue: list[str] = []
    seen: set[str] = set()

    try:
        document = fetch(deployment_url, timeout)
        index_hash = _hash_asset("index_html", deployment_url, document)
        queue.extend(_extract_js_urls_from_html(deployment_url, _decode(document)))
    except Exception as exc:  # pragma: no cover - operator/network path
        errors.append(f"fetch index failed: {deployment_url}: {exc}")

    while queue and len(seen) < max_js_assets:
        js_url = queue.pop(0)
        if js_url in seen:
            continue
        seen.add(js_url)
        try:
            content = fetch(js_url, timeout)
        except Exception as exc:  # pragma: no cover - unit tests exercise with injected fetcher
            errors.append(f"fetch js failed: {js_url}: {exc}")
            continue

        js_bundles[js_url] = _hash_asset("js_bundle", js_url, content)
        for discovered in _extract_js_urls_from_js(js_url, _decode(content)):
            if discovered not in seen and discovered not in queue:
                queue.append(discovered)

    if queue:
        errors.append(f"js asset discovery exceeded max_js_assets={max_js_assets}")

    js_bundle_urls = sorted(js_bundles)
    assets = ([index_hash] if index_hash else []) + [js_bundles[url] for url in js_bundle_urls]
    return {
        "task_id": "LSP-003-V2",
        "deployment_url": deployment_url,
        "checked_at": checked_at or _utc_now(),
        "index": index_hash,
        "js_bundle_urls": js_bundle_urls,
        "js_bundles": {url: js_bundles[url] for url in js_bundle_urls},
        "assets": assets,
        "asset_count": len(assets),
        "errors": errors,
        "passed": not errors and index_hash is not None,
    }


def write_audit_packet(result: BundleCaptureResult, audit_packet_path: str | Path) -> dict[str, object]:
    """Merge a bundle hash capture result into a JSON audit packet."""

    path = Path(audit_packet_path)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path} must contain a JSON object")
    else:
        payload = {}

    payload["bundle_hash_capture"] = result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_url", help="Hosted Lovable URL, for example https://.../management")
    parser.add_argument("--audit-packet", help="JSON audit packet path to create or update")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-js-assets", type=int, default=200)
    args = parser.parse_args(argv)

    result = capture_bundle_hashes(
        args.deployment_url,
        timeout=args.timeout,
        max_js_assets=args.max_js_assets,
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.audit_packet:
        write_audit_packet(result, args.audit_packet)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
