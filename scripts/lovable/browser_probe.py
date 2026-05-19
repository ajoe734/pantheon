#!/usr/bin/env python3
"""Browser-style probe for a deployed Lovable/BFF URL.

Probes /health and /bff/me, asserting no silent fallback to mock seed data.
Uses stdlib only — no external browser runtime or third-party packages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, TypedDict


TASK_ID = "LSP-002-V2"

# Response body patterns that indicate a silent mock-seed fallback.
_MOCK_SEED_RE = re.compile(
    r"""(?:
        "__mock__"
        |"mock_seed"
        |"__seed__"
        |"isMockData"\s*:\s*true
        |"is_mock_data"\s*:\s*true
        |"fallback_source"\s*:\s*"seed"
        |"_mock_fallback"
        |"SEED_FALLBACK"
        |"mock-user-\d"
        |"seed-user-\d"
    )""",
    re.VERBOSE,
)

_AUTH_GATE_STATUSES = frozenset({401, 403})

HttpGet = Callable[[str, float], tuple[int, bytes]]


class EndpointResult(TypedDict):
    path: str
    status: int
    ok: bool
    duration_ms: int
    mock_seed_detected: bool
    mock_seed_matches: list[str]
    body_prefix: str | None
    error: str | None


class ProbeResult(TypedDict):
    task_id: str
    base_url: str
    checked_at: str
    app_page_reachable: bool
    health: EndpointResult
    bff_me: EndpointResult
    mock_seed_free: bool
    all_passed: bool
    errors: list[str]


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stdlib_get(url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/html,*/*",
            "User-Agent": "pantheon-browser-probe/1.0 (LSP-002-V2)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def _make_auth_get(bearer_token: str) -> HttpGet:
    def get(url: str, timeout: float) -> tuple[int, bytes]:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {bearer_token}",
                "User-Agent": "pantheon-browser-probe/1.0 (LSP-002-V2)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read()

    return get


def detect_mock_seed(body: str) -> list[str]:
    """Return list of mock-seed pattern matches found in body."""
    return [m.group(0) for m in _MOCK_SEED_RE.finditer(body)]


def probe_endpoint(
    base_url: str,
    path: str,
    *,
    http_get: HttpGet,
    timeout: float,
    require_200: bool,
) -> EndpointResult:
    """Fetch one endpoint and assess whether a mock-seed fallback occurred."""
    url = base_url.rstrip("/") + path
    started = time.time()
    try:
        status, raw = http_get(url, timeout)
    except Exception as exc:
        return {
            "path": path,
            "status": 0,
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "mock_seed_detected": False,
            "mock_seed_matches": [],
            "body_prefix": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    body = raw.decode("utf-8", errors="replace")
    elapsed = round((time.time() - started) * 1000)
    mock_matches = detect_mock_seed(body)
    mock_detected = bool(mock_matches)

    if require_200:
        # Authenticated probe: must return 200 and carry no mock markers.
        ok = status == 200 and not mock_detected
    else:
        # Unauthenticated probe: 401/403 means the real BFF gate is active (good).
        # 200 is only acceptable without mock seed data.
        ok = (status in _AUTH_GATE_STATUSES) or (status == 200 and not mock_detected)

    return {
        "path": path,
        "status": status,
        "ok": ok,
        "duration_ms": elapsed,
        "mock_seed_detected": mock_detected,
        "mock_seed_matches": mock_matches,
        "body_prefix": body[:300] if (not ok or mock_detected) else None,
        "error": None,
    }


def run_browser_probe(
    base_url: str,
    *,
    http_get: HttpGet | None = None,
    timeout: float = 10.0,
    bearer_token: str | None = None,
) -> ProbeResult:
    """Run the browser probe sequence against base_url."""
    errors: list[str] = []

    # Resolve http_get callables per endpoint.
    # When http_get is injected (tests), use it for every call.
    # In production, optionally apply auth headers only to /bff/me.
    if http_get is not None:
        plain_get: HttpGet = http_get
        me_get: HttpGet = http_get
    else:
        plain_get = _stdlib_get
        me_get = _make_auth_get(bearer_token) if bearer_token else _stdlib_get

    # Step 1: hit the root page as a headless browser would.
    app_reachable = False
    try:
        status, _ = plain_get(base_url.rstrip("/") + "/", timeout)
        app_reachable = status < 500
    except Exception as exc:
        errors.append(f"app page fetch failed: {exc}")

    # Step 2: probe /health (no auth required, must return 200).
    health = probe_endpoint(
        base_url,
        "/health",
        http_get=plain_get,
        timeout=timeout,
        require_200=True,
    )
    if not health["ok"]:
        errors.append(
            f"/health probe failed: status={health['status']} mock={health['mock_seed_detected']}"
        )

    # Step 3: probe /bff/me — assert no mock-seed fallback.
    bff_me = probe_endpoint(
        base_url,
        "/bff/me",
        http_get=me_get,
        timeout=timeout,
        require_200=bool(bearer_token),
    )
    if not bff_me["ok"]:
        errors.append(
            f"/bff/me probe failed: status={bff_me['status']} mock={bff_me['mock_seed_detected']}"
        )

    mock_seed_free = not health["mock_seed_detected"] and not bff_me["mock_seed_detected"]
    all_passed = health["ok"] and bff_me["ok"] and mock_seed_free and not errors

    return {
        "task_id": TASK_ID,
        "base_url": base_url,
        "checked_at": _utc_now(),
        "app_page_reachable": app_reachable,
        "health": health,
        "bff_me": bff_me,
        "mock_seed_free": mock_seed_free,
        "all_passed": all_passed,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "base_url",
        help="BFF base URL, e.g. https://pantheon-dev-bff.example.com",
    )
    parser.add_argument("--token", default="", help="Optional Bearer token for /bff/me")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", default="", help="Write JSON result to this path")
    args = parser.parse_args(argv)

    result = run_browser_probe(
        args.base_url,
        bearer_token=args.token or None,
        timeout=args.timeout,
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")

    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
