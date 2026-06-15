#!/usr/bin/env python3
"""E2E frontend static-serving verifier (catches the blank-console class of bug).

A single-page app is blank if its index.html references a JS/CSS asset that is
not actually served — an SPA catch-all (`try_files {path} /index.html`) then
returns index.html for the missing asset, the browser receives HTML where it
expected JavaScript, the module fails to execute, and `<div id=root>` stays
empty. The API can be perfectly healthy while the console shows nothing.

This verifier fetches the FE index.html, extracts every referenced
`/assets/*.js` and `*.css`, fetches each, and asserts: status 200, a matching
content-type (js -> javascript, css -> css), and that the body is NOT the SPA
fallback HTML (i.e. the asset really exists on disk).

Failure semantics:
  * FAIL (exit 1) if any referenced asset is missing / served as HTML / wrong MIME.
  * Also FAIL if index.html itself is unreachable.

Usage:
    FE_BASE=https://...sslip.io python3 scripts/verify_e2e_fe_serving.py
"""
from __future__ import annotations

import os
import re
import ssl
import sys
import urllib.request

ASSET_RE = re.compile(r'(?:src|href)="(/assets/[^"]+\.(?:js|css))"')


def _ctx():
    ctx = ssl.create_default_context()
    if os.environ.get("FE_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch(base, ctx, path):
    req = urllib.request.Request(base.rstrip("/") + path)
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, "", b""
    except Exception as e:  # noqa: BLE001
        return None, f"ERR:{e}", b""


def main() -> int:
    base = os.environ.get("FE_BASE")
    if not base:
        print("ERROR: set FE_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    code, _ctype, body = _fetch(base, ctx, "/")
    if code != 200 or not body:
        print(f"FAIL: FE index unreachable -> {code}")
        return 1
    html = body.decode("utf-8", "ignore")
    assets = ASSET_RE.findall(html)
    if not assets:
        print("FAIL: index.html references no /assets/*.js|css (unexpected build)")
        return 1

    bad = []
    for path in assets:
        st, ctype, b = _fetch(base, ctx, path)
        is_html = b[:15].lstrip().lower().startswith(b"<!doctype") or b"<html" in b[:200].lower()
        want = "javascript" if path.endswith(".js") else "css"
        ok = st == 200 and want in ctype.lower() and not is_html
        flag = "" if ok else "  <-- BROKEN"
        print(f"  {path}: {st} type='{ctype}' bytes={len(b)} html_fallback={is_html}{flag}")
        if not ok:
            reason = "missing/served-as-index.html" if is_html else f"status={st} type={ctype}"
            bad.append(f"{path}: {reason}")

    print(f"\n== FE static serving: {len(assets)} referenced assets ==")
    if bad:
        print(f"FAIL: {len(bad)} asset(s) not served correctly (the console will be blank):")
        for b in bad:
            print(f"   {b}")
        return 1
    print("OK: every index-referenced asset is served with the correct type (app can boot)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
