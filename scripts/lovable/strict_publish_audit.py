#!/usr/bin/env python3
"""Generate the final Lovable strict-publish audit evidence packet.

This orchestrates the LSP-002 browser probe, LSP-003 hosted bundle hash
capture, and LSP-004 forbidden runtime path scan against one hosted Lovable
deployment URL. The combined result is fail-closed: every component must pass
for the final packet to pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping, TypedDict


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "support" / "evidence" / "lsp-final-audit"
DEFAULT_JSON_PATH = DEFAULT_OUTPUT_DIR / "strict-publish-audit.json"
DEFAULT_REPORT_PATH = DEFAULT_OUTPUT_DIR / "strict-publish-audit.md"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from browser_probe import run_browser_probe  # noqa: E402
from capture_bundle_hashes import capture_bundle_hashes  # noqa: E402
from forbidden_path_scanner import scan_deployment  # noqa: E402


TASK_ID = "LSP-005-V2"

ComponentRunner = Callable[[str], Mapping[str, Any]]


class StrictPublishAuditResult(TypedDict):
    task_id: str
    deployment_url: str
    browser_probe_base_url: str
    checked_at: str
    inputs: dict[str, object]
    components: dict[str, dict[str, Any]]
    component_status: dict[str, bool]
    summary: dict[str, int]
    errors: list[str]
    passed: bool


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _deployment_origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"deployment URL must be absolute: {url}")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _as_dict(payload: Mapping[str, Any]) -> dict[str, Any]:
    return dict(payload)


def _passed(payload: Mapping[str, Any], field: str) -> bool:
    return bool(payload.get(field))


def _run_component(
    name: str,
    task_id: str,
    runner: ComponentRunner,
    target_url: str,
    pass_field: str,
) -> tuple[dict[str, Any], bool, list[str]]:
    try:
        result = _as_dict(runner(target_url))
    except Exception as exc:  # pragma: no cover - defensive operator path
        message = f"{task_id} runner failed: {type(exc).__name__}: {exc}"
        return (
            {
                "task_id": task_id,
                "component": name,
                "target_url": target_url,
                "error": f"{type(exc).__name__}: {exc}",
                pass_field: False,
            },
            False,
            [message],
        )

    ok = _passed(result, pass_field)
    if ok:
        return result, True, []

    component_errors = result.get("errors")
    error_count = len(component_errors) if isinstance(component_errors, list) else 0
    if task_id == "LSP-004-V2":
        signal_count = len(result.get("forbidden_signals") or [])
        return result, False, [f"{task_id} failed: forbidden_signals={signal_count} errors={error_count}"]
    return result, False, [f"{task_id} failed: errors={error_count}"]


def _default_browser_runner(timeout: float, token: str | None) -> ComponentRunner:
    def runner(base_url: str) -> Mapping[str, Any]:
        return run_browser_probe(base_url, timeout=timeout, bearer_token=token)

    return runner


def _default_bundle_runner(timeout: float, max_js_assets: int) -> ComponentRunner:
    def runner(deployment_url: str) -> Mapping[str, Any]:
        return capture_bundle_hashes(deployment_url, timeout=timeout, max_js_assets=max_js_assets)

    return runner


def _default_forbidden_runner(timeout: float) -> ComponentRunner:
    def runner(deployment_url: str) -> Mapping[str, Any]:
        return scan_deployment(deployment_url, timeout=timeout)

    return runner


def run_strict_publish_audit(
    deployment_url: str,
    *,
    browser_probe_base_url: str | None = None,
    token: str | None = None,
    timeout: float = 15.0,
    max_js_assets: int = 200,
    checked_at: str | None = None,
    browser_probe_runner: ComponentRunner | None = None,
    bundle_capture_runner: ComponentRunner | None = None,
    forbidden_scan_runner: ComponentRunner | None = None,
) -> StrictPublishAuditResult:
    """Run the final audit and return a JSON-serializable evidence packet."""

    probe_base_url = (browser_probe_base_url or _deployment_origin(deployment_url)).rstrip("/")
    timestamp = checked_at or _utc_now()

    browser_result, browser_ok, browser_errors = _run_component(
        "browser_probe",
        "LSP-002-V2",
        browser_probe_runner or _default_browser_runner(timeout, token),
        probe_base_url,
        "all_passed",
    )
    bundle_result, bundle_ok, bundle_errors = _run_component(
        "bundle_hash_capture",
        "LSP-003-V2",
        bundle_capture_runner or _default_bundle_runner(timeout, max_js_assets),
        deployment_url,
        "passed",
    )
    forbidden_result, forbidden_ok, forbidden_errors = _run_component(
        "forbidden_path_scan",
        "LSP-004-V2",
        forbidden_scan_runner or _default_forbidden_runner(timeout),
        deployment_url,
        "passed",
    )

    bundle_urls = bundle_result.get("js_bundle_urls") or []
    forbidden_signals = forbidden_result.get("forbidden_signals") or []
    scanned_urls = forbidden_result.get("scanned_urls") or []
    browser_probe_errors = browser_result.get("errors") or []
    bundle_capture_errors = bundle_result.get("errors") or []
    forbidden_scan_errors = forbidden_result.get("errors") or []
    errors = browser_errors + bundle_errors + forbidden_errors

    return {
        "task_id": TASK_ID,
        "deployment_url": deployment_url,
        "browser_probe_base_url": probe_base_url,
        "checked_at": timestamp,
        "inputs": {
            "deployment_url": deployment_url,
            "browser_probe_base_url": probe_base_url,
            "timeout_seconds": timeout,
            "max_js_assets": max_js_assets,
            "browser_probe_token_provided": bool(token),
        },
        "components": {
            "browser_probe": browser_result,
            "bundle_hash_capture": bundle_result,
            "forbidden_path_scan": forbidden_result,
        },
        "component_status": {
            "LSP-002-V2": browser_ok,
            "LSP-003-V2": bundle_ok,
            "LSP-004-V2": forbidden_ok,
        },
        "summary": {
            "js_bundle_count": len(bundle_urls) if isinstance(bundle_urls, list) else 0,
            "scanned_url_count": len(scanned_urls) if isinstance(scanned_urls, list) else 0,
            "forbidden_signal_count": len(forbidden_signals) if isinstance(forbidden_signals, list) else 0,
            "browser_probe_error_count": len(browser_probe_errors) if isinstance(browser_probe_errors, list) else 0,
            "bundle_capture_error_count": len(bundle_capture_errors) if isinstance(bundle_capture_errors, list) else 0,
            "forbidden_scan_error_count": len(forbidden_scan_errors) if isinstance(forbidden_scan_errors, list) else 0,
        },
        "errors": errors,
        "passed": browser_ok and bundle_ok and forbidden_ok,
    }


def _status(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _table_cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= 180 else text[:177] + "..."


def render_markdown_report(result: StrictPublishAuditResult) -> str:
    status = _status(result["passed"])
    component_status = result["component_status"]
    components = result["components"]
    browser = components["browser_probe"]
    bundle = components["bundle_hash_capture"]
    forbidden = components["forbidden_path_scan"]

    bundle_hashes = bundle.get("js_bundles") or {}
    if isinstance(bundle_hashes, dict) and bundle_hashes:
        bundle_rows = "\n".join(
            f"| `{_table_cell(url)}` | `{_table_cell((meta or {}).get('sha256', ''))}` | {_table_cell((meta or {}).get('bytes', ''))} |"
            for url, meta in sorted(bundle_hashes.items())
            if isinstance(meta, dict)
        )
    else:
        bundle_rows = "| _none_ | _n/a_ | _n/a_ |"

    forbidden_signals = forbidden.get("forbidden_signals") or []
    if isinstance(forbidden_signals, list) and forbidden_signals:
        signal_rows = "\n".join(
            "| {signal} | `{source}` | {line} | {column} | `{snippet}` |".format(
                signal=_table_cell(signal.get("signal", "")),
                source=_table_cell(signal.get("source_url", "")),
                line=_table_cell(signal.get("line", "")),
                column=_table_cell(signal.get("column", "")),
                snippet=_table_cell(signal.get("snippet", "")),
            )
            for signal in forbidden_signals[:50]
            if isinstance(signal, dict)
        )
        if len(forbidden_signals) > 50:
            signal_rows += f"\n| _truncated_ | _see JSON for remaining {len(forbidden_signals) - 50} signal(s)_ |  |  |  |"
    else:
        signal_rows = "| _none_ | _n/a_ | _n/a_ | _n/a_ | _n/a_ |"

    error_lines = "\n".join(f"- {error}" for error in result["errors"]) or "- none"
    browser_health = browser.get("health") if isinstance(browser.get("health"), dict) else {}
    browser_bff_me = browser.get("bff_me") if isinstance(browser.get("bff_me"), dict) else {}

    return f"""# LSP-005-V2 Strict Publish Audit

Status: {status}
Task: `LSP-005-V2`
Checked: {result["checked_at"]}
Deployment URL: `{result["deployment_url"]}`
Browser probe base URL: `{result["browser_probe_base_url"]}`

## Component Results

| Task | Component | Status | Evidence |
|---|---|---:|---|
| LSP-002-V2 | Browser /health and /bff/me probe | {_status(component_status["LSP-002-V2"])} | `/health` status `{_table_cell(browser_health.get("status", ""))}`, `/bff/me` status `{_table_cell(browser_bff_me.get("status", ""))}`, mock-seed-free `{_table_cell(browser.get("mock_seed_free", ""))}` |
| LSP-003-V2 | Hosted bundle hash capture | {_status(component_status["LSP-003-V2"])} | `{result["summary"]["js_bundle_count"]}` JS bundle(s), `{_table_cell(bundle.get("asset_count", 0))}` total asset(s) |
| LSP-004-V2 | Forbidden runtime path scan | {_status(component_status["LSP-004-V2"])} | `{result["summary"]["forbidden_signal_count"]}` forbidden signal(s), `{result["summary"]["scanned_url_count"]}` scanned URL(s) |

## Bundle Hashes

| Asset URL | sha256 | bytes |
|---|---:|---:|
{bundle_rows}

## Forbidden Runtime Signals

| Signal | Source URL | line | column | snippet |
|---|---|---:|---:|---|
{signal_rows}

## Errors

{error_lines}

## Packet Notes

- This packet preserves the raw LSP-002, LSP-003, and LSP-004 component payloads in the JSON artifact.
- The final verdict is fail-closed: every component must pass for `passed` to be true.
"""


def write_audit_outputs(
    result: StrictPublishAuditResult,
    output_json: str | Path,
    report_md: str | Path,
) -> None:
    json_path = Path(output_json)
    report_path = Path(report_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown_report(result), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_url", help="Hosted Lovable URL, for example https://pantheon-dev.lovable.app/management")
    parser.add_argument(
        "--browser-probe-base-url",
        default="",
        help="Override the base URL used by LSP-002 /health and /bff/me probes. Defaults to the deployment URL origin.",
    )
    parser.add_argument("--token", default="", help="Optional Bearer token for the LSP-002 /bff/me probe")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-js-assets", type=int, default=200)
    parser.add_argument("--output", default=str(DEFAULT_JSON_PATH), help="Write combined JSON evidence to this path")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Write Markdown evidence report to this path")
    args = parser.parse_args(argv)

    result = run_strict_publish_audit(
        args.deployment_url,
        browser_probe_base_url=args.browser_probe_base_url or None,
        token=args.token or None,
        timeout=args.timeout,
        max_js_assets=args.max_js_assets,
    )
    write_audit_outputs(result, args.output, args.report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
