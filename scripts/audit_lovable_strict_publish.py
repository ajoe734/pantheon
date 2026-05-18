#!/usr/bin/env python3
"""Audit a Lovable deployment for strict BFF build-time publish settings."""

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
from typing import Callable, Mapping, TypedDict


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIRED_ENV_PATH = (
    ROOT / "support" / "evidence" / "LOVABLE-STRICT-PUBLISH" / "required_build_env.json"
)

HttpGet = Callable[[str, float], bytes | str]


class AuditResult(TypedDict):
    task_id: str
    deployment_url: str
    checked_at: str
    required_flags: dict[str, str]
    observed_flags: dict[str, dict[str, object]]
    strict_env_confirmed: bool
    missing_flags: list[str]
    bundle_urls: list[str]
    bundle_hashes: dict[str, dict[str, object]]
    forbidden_runtime_paths: list[str]
    errors: list[str]
    passed: bool


class _AssetParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "script" and attr_map.get("src"):
            self.assets.append(attr_map["src"])
            return
        if tag.lower() != "link":
            return

        rel = {part.strip().lower() for part in attr_map.get("rel", "").split()}
        href = attr_map.get("href")
        if href and ({"modulepreload", "preload", "stylesheet"} & rel):
            self.assets.append(href)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _urllib_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,text/css,*/*",
            "User-Agent": "pantheon-lovable-strict-publish-audit/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _decode(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    return content.decode("utf-8", errors="replace")


def _load_required_flags(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    flags = payload.get("required_build_env", payload)
    if not isinstance(flags, dict):
        raise ValueError(f"{path} must contain a JSON object of required flags")
    return {str(key): str(value) for key, value in flags.items() if str(key).startswith("VITE_")}


def _normalize_required_flags(
    required_env: Mapping[str, str] | None,
    required_env_path: str | Path,
) -> dict[str, str]:
    if required_env is not None:
        return {str(key): str(value) for key, value in required_env.items()}
    return _load_required_flags(Path(required_env_path))


def _extract_asset_urls(document_url: str, html_text: str) -> list[str]:
    parser = _AssetParser()
    parser.feed(html_text)
    urls = {urllib.parse.urljoin(document_url, asset) for asset in parser.assets}

    # Some Vite/Lovable pages include asset URLs inside inline bootstrap code.
    for match in re.finditer(r"""["'](?P<asset>/assets/[^"']+\.(?:js|mjs|css))["']""", html_text):
        urls.add(urllib.parse.urljoin(document_url, match.group("asset")))

    return sorted(urls)


def _flag_match(text: str, key: str, required_value: str) -> tuple[bool, str | None]:
    key_re = re.escape(key)
    value_re = re.escape(required_value)
    direct_patterns = [
        re.compile(rf"""["']{key_re}["']\s*[:=]\s*["']{value_re}["']"""),
        re.compile(rf"""{key_re}\s*[:=]\s*["']?{value_re}["']?"""),
    ]
    for pattern in direct_patterns:
        match = pattern.search(text)
        if match:
            return True, match.group(0)

    for match in re.finditer(key_re, text):
        start = max(match.start() - 160, 0)
        end = min(match.end() + 160, len(text))
        window = text[start:end]
        if required_value in window:
            return True, window
    return False, None


def _path_from_candidate(candidate: str) -> str:
    if candidate.startswith(("http://", "https://", "//")):
        url = "https:" + candidate if candidate.startswith("//") else candidate
        return urllib.parse.urlparse(url).path
    return candidate


def _is_forbidden_runtime_path(candidate: str) -> bool:
    path = _path_from_candidate(candidate).lower()
    if "/mocks/" in path:
        return True
    return any(segment.startswith("seed") for segment in path.strip("/").split("/") if segment)


def _extract_forbidden_runtime_paths(text: str) -> list[str]:
    candidates: set[str] = set()
    string_path = re.compile(r"""["'`](?P<value>(?:(?:https?:)?//|/)[^"'`\s)]+)["'`]""")
    for match in string_path.finditer(text):
        value = match.group("value")
        if _is_forbidden_runtime_path(value):
            candidates.add(value)
    return sorted(candidates)


def _sha256(content: bytes | str) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def verify_strict_publish(
    deployment_url: str,
    *,
    required_env: Mapping[str, str] | None = None,
    required_env_path: str | Path = DEFAULT_REQUIRED_ENV_PATH,
    http_get: HttpGet | None = None,
    timeout: float = 15.0,
) -> AuditResult:
    """Verify strict build flags and seed/mock request-path absence for a deployment."""

    fetch = http_get or _urllib_get
    required_flags = _normalize_required_flags(required_env, required_env_path)
    errors: list[str] = []
    fetched: dict[str, bytes | str] = {}

    try:
        document = fetch(deployment_url, timeout)
        fetched[deployment_url] = document
    except Exception as exc:  # pragma: no cover - exercised by operators, not unit tests
        errors.append(f"fetch document failed: {deployment_url}: {exc}")
        document = b""

    document_text = _decode(document)
    bundle_urls = _extract_asset_urls(deployment_url, document_text) if document_text else []
    bundle_hashes: dict[str, dict[str, object]] = {}

    for asset_url in bundle_urls:
        try:
            content = fetch(asset_url, timeout)
            fetched[asset_url] = content
            raw = content.encode("utf-8") if isinstance(content, str) else content
            bundle_hashes[asset_url] = {
                "sha256": _sha256(raw),
                "bytes": len(raw),
            }
        except Exception as exc:  # pragma: no cover - exercised by operators, not unit tests
            errors.append(f"fetch asset failed: {asset_url}: {exc}")

    searchable_parts = [_decode(content) for content in fetched.values()]
    searchable_text = "\n".join(searchable_parts)

    observed_flags: dict[str, dict[str, object]] = {}
    missing_flags: list[str] = []
    for key, value in required_flags.items():
        confirmed, evidence = _flag_match(searchable_text, key, value)
        observed_flags[key] = {
            "required": value,
            "confirmed": confirmed,
            "evidence": evidence[:240] if evidence else None,
        }
        if not confirmed:
            missing_flags.append(key)

    forbidden_runtime_paths = _extract_forbidden_runtime_paths(searchable_text)
    strict_env_confirmed = not missing_flags
    passed = strict_env_confirmed and not forbidden_runtime_paths and not errors

    return {
        "task_id": "LOVABLE-STRICT-PUBLISH",
        "deployment_url": deployment_url,
        "checked_at": _utc_now(),
        "required_flags": required_flags,
        "observed_flags": observed_flags,
        "strict_env_confirmed": strict_env_confirmed,
        "missing_flags": missing_flags,
        "bundle_urls": bundle_urls,
        "bundle_hashes": bundle_hashes,
        "forbidden_runtime_paths": forbidden_runtime_paths,
        "errors": errors,
        "passed": passed,
    }


def render_markdown_report(result: AuditResult) -> str:
    status = "PASS" if result["passed"] else "FAIL"
    bundle_rows = "\n".join(
        f"| `{url}` | `{meta['sha256']}` | {meta['bytes']} |"
        for url, meta in result["bundle_hashes"].items()
    )
    if not bundle_rows:
        bundle_rows = "| _none fetched_ | _n/a_ | _n/a_ |"

    flag_rows = "\n".join(
        f"| `{key}` | `{meta['required']}` | {meta['confirmed']} |"
        for key, meta in result["observed_flags"].items()
    )
    forbidden = "\n".join(f"- `{path}`" for path in result["forbidden_runtime_paths"]) or "- none"
    errors = "\n".join(f"- {error}" for error in result["errors"]) or "- none"

    return f"""# LOVABLE-STRICT-PUBLISH Audit Result

Status: {status}
Task: `LOVABLE-STRICT-PUBLISH`
Checked: {result["checked_at"]}
Deployment URL: `{result["deployment_url"]}`

## Strict Build Env

| Flag | Required value | Confirmed in deployed assets |
|---|---|---|
{flag_rows}

Missing flags: `{", ".join(result["missing_flags"]) or "none"}`

## Bundle Hashes

| Asset URL | sha256 | bytes |
|---|---:|---:|
{bundle_rows}

## Runtime Path Probe

Forbidden request paths matching `/mocks/` or `seed.*`:

{forbidden}

## Fetch Errors

{errors}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_url", help="Lovable deployment URL, for example https://.../management")
    parser.add_argument("--required-env", default=str(DEFAULT_REQUIRED_ENV_PATH))
    parser.add_argument("--output", help="Write JSON audit result to this path")
    parser.add_argument("--report", help="Write Markdown audit result to this path")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    result = verify_strict_publish(
        args.deployment_url,
        required_env_path=args.required_env,
        timeout=args.timeout,
    )
    json_payload = json.dumps(result, indent=2, sort_keys=True)
    print(json_payload)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_payload + "\n", encoding="utf-8")
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown_report(result), encoding="utf-8")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
