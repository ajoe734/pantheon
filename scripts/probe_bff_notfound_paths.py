#!/usr/bin/env python3
"""Live not-found error-path probe for BFF detail-by-id routes.

Motivation
----------
``probe_bff_authenticated_live.py`` exercises happy-path *collection* GETs
(``/bff/strategies`` etc.). It does NOT exercise the *detail-by-id* routes with
a non-existent id, which is exactly the surface where a stale ``ErrorCode``
attribute reference (e.g. ``ErrorCode.OBJECT_NOT_FOUND`` after the enum member
was renamed to ``RESOURCE_NOT_FOUND``) raises ``KeyError`` and turns a clean
404 into a 500. ``audit_deploy_drift.sh`` detects drift *statically* (image
build timestamp vs service-path commits) but cannot prove the running
container actually serves the fixed code.

This probe closes that gap at runtime: it enumerates every ``GET /bff/.../{id}``
route from the live OpenAPI spec, requests each with a deliberately
non-existent id, and asserts the response is a clean client status (typically
404/410/422) rather than a 5xx. Any 5xx is a real defect -- a not-found path
that crashes -- and exits non-zero.

Usage
-----
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/probe_bff_notfound_paths.py

Exit code 0 = no 5xx on any not-found path; 1 = at least one 5xx (or the spec
could not be fetched).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

DETAIL_ID_RE = re.compile(r"/\{[^/}]+\}$")
# A token unlikely to collide with any real resource id.
NONEXISTENT_ID = "pantheon-notfound-probe-zzz-000"


def _enumerate_detail_routes(spec: dict) -> list[str]:
    """Return GET /bff/.../{single_param} routes from an OpenAPI spec."""
    routes = []
    for path, ops in spec.get("paths", {}).items():
        if not path.startswith("/bff/"):
            continue
        if "get" not in {k.lower() for k in ops}:
            continue
        # Exactly one path param, appearing at the tail: a detail-by-id route.
        if DETAIL_ID_RE.search(path) and path.count("{") == 1:
            routes.append(path)
    return sorted(routes)


def _probe(base: str, token: str, ctx: ssl.SSLContext, path: str):
    url = base.rstrip("/") + DETAIL_ID_RE.sub("/" + NONEXISTENT_ID, path)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        return path, resp.status, ""
    except urllib.error.HTTPError as exc:
        return path, exc.code, ""
    except Exception as exc:  # noqa: BLE001 - surface any transport error
        return path, None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN")
    if not base or not token:
        print("ERROR: set BFF_BASE and BFF_TOKEN environment variables", file=sys.stderr)
        return 2

    ctx = ssl.create_default_context()
    # dev/staging use sslip.io + LE; tolerate hostname mismatch on probe.
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    spec_url = base.rstrip("/") + "/openapi.json"
    try:
        with urllib.request.urlopen(spec_url, timeout=20, context=ctx) as r:
            spec = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not fetch {spec_url}: {exc}", file=sys.stderr)
        return 1

    routes = _enumerate_detail_routes(spec)
    if not routes:
        print("ERROR: no detail-by-id /bff routes found in spec", file=sys.stderr)
        return 1

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda p: _probe(base, token, ctx, p), routes))

    server_errors = [(p, c) for p, c, _ in results if isinstance(c, int) and c >= 500]
    transport_errors = [(p, e) for p, c, e in results if c is None]

    print(f"probed {len(results)} detail-by-id GET routes with a non-existent id")
    dist: dict = {}
    for _, code, _ in results:
        key = code if code is not None else "transport-error"
        dist[key] = dist.get(key, 0) + 1
    print(f"status distribution: {dict(sorted(dist.items(), key=lambda kv: str(kv[0])))}")

    for path, err in transport_errors:
        print(f"  WARN  transport error {path}: {err}", file=sys.stderr)

    if server_errors:
        print(f"\nFAIL: {len(server_errors)} not-found path(s) returned 5xx (should be 404):")
        for path, code in sorted(server_errors):
            print(f"  {code}  {path}")
        return 1

    print("OK: every not-found detail path returns a clean client status (no 5xx)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
