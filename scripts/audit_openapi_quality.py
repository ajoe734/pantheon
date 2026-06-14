#!/usr/bin/env python3
"""audit_openapi_quality.py — structural quality gate for the BFF OpenAPI spec.

Checks: duplicate operationIds, operations missing operationId, paths/operations
missing a `responses` block, and orphan component schemas (defined but never $ref'd).
Reads a local openapi.json path or fetches from a base URL.

Usage:
  python3 scripts/audit_openapi_quality.py path/to/openapi.json
  python3 scripts/audit_openapi_quality.py --url https://<bff>/openapi.json
Exit 1 if any structural defect is found.
"""
from __future__ import annotations
import argparse, json, re, sys, urllib.request
from collections import Counter

_METHODS = {"get", "post", "put", "patch", "delete"}


def load(src: str, is_url: bool) -> dict:
    if is_url:
        return json.loads(urllib.request.urlopen(src, timeout=20).read().decode())
    return json.loads(open(src, encoding="utf-8").read())


def audit(spec: dict) -> list[str]:
    paths = spec.get("paths", {})
    schemas = spec.get("components", {}).get("schemas", {})
    problems = []
    opids, missing_opid, missing_resp = [], [], []
    for p, methods in paths.items():
        for m, op in (methods or {}).items():
            if m not in _METHODS:
                continue
            oid = op.get("operationId")
            (opids.append(oid) if oid else missing_opid.append(f"{m.upper()} {p}"))
            if not op.get("responses"):
                missing_resp.append(f"{m.upper()} {p}")
    dups = [o for o, c in Counter(opids).items() if c > 1]
    refs = set(re.findall(r"#/components/schemas/([A-Za-z0-9_.]+)", json.dumps(spec)))
    orphans = [s for s in schemas if s not in refs]
    if dups: problems.append(f"duplicate operationIds: {dups}")
    if missing_opid: problems.append(f"operations missing operationId: {len(missing_opid)} (e.g. {missing_opid[:3]})")
    if missing_resp: problems.append(f"operations missing responses: {len(missing_resp)} (e.g. {missing_resp[:3]})")
    if orphans: problems.append(f"orphan schemas: {len(orphans)} (e.g. {orphans[:5]})")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="-")
    ap.add_argument("--url")
    a = ap.parse_args()
    spec = load(a.url, True) if a.url else load(a.source, False)
    paths = spec.get("paths", {}); schemas = spec.get("components", {}).get("schemas", {})
    print(f"openapi: paths={len(paths)} schemas={len(schemas)}")
    problems = audit(spec)
    if not problems:
        print("OK: no structural OpenAPI defects (operationIds unique+present, all ops have responses, no orphan schemas).")
        return 0
    print(f"FOUND {len(problems)} OpenAPI defect class(es):")
    for p in problems: print("  -", p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
