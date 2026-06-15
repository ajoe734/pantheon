#!/usr/bin/env python3
"""E2E auth-boundary verifier for the BFF.

Even in stub/permissive auth, protected BFF endpoints must reject an unauthenticated
request (no / empty Authorization header) — otherwise an endpoint has silently
become an open door (an auth-bypass regression). This verifier asserts that a
curated set of protected endpoints:
  * reject a request with NO Authorization header (401/403), and
  * reject a request with an EMPTY bearer (401/403), and
  * accept a valid bearer (not 401/403).

Failure semantics (CI-safe):
  * FAIL (exit 1) on any protected endpoint that returns a non-auth status
    (i.e. serves data, 2xx/4xx-non-auth) WITHOUT credentials — an auth bypass.
  * REPORT (exit 0) otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_auth_boundary.py
"""
from __future__ import annotations

import os
import ssl
import sys
import urllib.request

PROTECTED = [
    "/bff/runtimes", "/bff/strategies", "/bff/personas", "/bff/capital-pools",
    "/bff/deployments", "/bff/approvals", "/bff/incidents", "/bff/v5/loop-runs",
    "/bff/v5/sentinel/findings", "/api/v1/operator/runtime-state", "/bff/artifacts",
]
AUTH_REJECT = {401, 403}


def _ctx():
    ctx = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _status(base, ctx, path, headers):
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    bypass = []
    for path in PROTECTED:
        no_auth = _status(base, ctx, path, {})
        empty = _status(base, ctx, path, {"Authorization": "Bearer "})
        with_auth = _status(base, ctx, path, {"Authorization": f"Bearer {token}"})
        ok_noauth = no_auth in AUTH_REJECT
        ok_empty = empty in AUTH_REJECT
        if not ok_noauth:
            bypass.append(f"{path}: no-auth -> {no_auth} (should be 401/403)")
        if not ok_empty:
            bypass.append(f"{path}: empty-bearer -> {empty} (should be 401/403)")
        marker = "" if (ok_noauth and ok_empty) else "  <-- BYPASS"
        print(f"  {path}: no-auth={no_auth} empty={empty} bearer={with_auth}{marker}")

    print(f"\n== auth boundary over {len(PROTECTED)} protected endpoints ==")
    if bypass:
        print(f"FAIL: {len(bypass)} auth-bypass(es) — endpoint served without credentials:")
        for b in bypass:
            print(f"   {b}")
        return 1
    print("OK: every protected endpoint rejects missing/empty credentials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
