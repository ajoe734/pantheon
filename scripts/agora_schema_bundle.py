#!/usr/bin/env python3
"""Compute the Agora v1 index and verify every Agora bundle's sha256 digests.

Usage:
    python3 scripts/agora_schema_bundle.py [--verify]

Without --verify: writes services/control-plane/specs/agora/bundle_index.json.
With --verify: checks every bundle_index*.json, including parent and OpenAPI hashes.
"""

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
AGORA_SPECS = REPO_ROOT / "services" / "control-plane" / "specs" / "agora"
OPENAPI_DIR = REPO_ROOT / "services" / "control-plane" / "openapi"

SCHEMA_FILES = [
    "agora_user_scope.schema.json",
    "servant_profile.schema.json",
    "strategy_workshop.schema.json",
    "strategy_completeness.schema.json",
    "research_plan.schema.json",
    "research_run_summary.schema.json",
    "candidate_pool.schema.json",
    "dashboard_recipe.schema.json",
    "widget_spec.schema.json",
    "trading_event.schema.json",
    "trading_intent.schema.json",
    "shadow_decision.schema.json",
    "personalization_event.schema.json",
    "capability_manifest.json",
]

OPENAPI_FILES = [
    "agora_v1.openapi.yaml",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_index() -> dict:
    entries = {}
    for name in SCHEMA_FILES:
        path = AGORA_SPECS / name
        if not path.exists():
            print(f"ERROR: missing schema file: {path}", file=sys.stderr)
            sys.exit(1)
        entries[f"specs/agora/{name}"] = sha256_file(path)

    for name in OPENAPI_FILES:
        path = OPENAPI_DIR / name
        if not path.exists():
            print(f"ERROR: missing openapi file: {path}", file=sys.stderr)
            sys.exit(1)
        entries[f"openapi/{name}"] = sha256_file(path)

    return {
        "bundle_version": "1.0",
        "frozen_by": "AG-XR-001",
        "note": "Run 'python3 scripts/agora_schema_bundle.py --verify' to confirm digest integrity.",
        "files": entries,
    }


def verify_index(index: dict) -> bool:
    ok = True
    entries = {f"services/control-plane/{p}": h for p, h in index["files"].items()}
    if parent := index.get("extends"):
        entries[parent["bundle_path"]] = parent["bundle_index_sha256"]
    if openapi := index.get("openapi"):
        entries[openapi["path"]] = openapi["sha256"]
    for rel_path, expected in entries.items():
        path = REPO_ROOT / rel_path
        if not path.exists():
            print(f"MISSING: {rel_path}", file=sys.stderr)
            ok = False
            continue
        actual = sha256_file(path)
        if actual != expected:
            print(f"DIGEST MISMATCH: {rel_path}", file=sys.stderr)
            print(f"  expected: {expected}", file=sys.stderr)
            print(f"  actual:   {actual}", file=sys.stderr)
            ok = False
        else:
            print(f"OK: {rel_path}")
    return ok


def verify_all_indices() -> bool:
    paths = sorted(AGORA_SPECS.glob("bundle_index*.json"))
    if not paths:
        print(f"ERROR: no bundle indices found at {AGORA_SPECS}", file=sys.stderr)
        return False
    results = []
    for path in paths:
        print(f"Verifying {path.name}")
        results.append(verify_index(json.loads(path.read_text())))
    return all(results)


def main() -> None:
    verify = "--verify" in sys.argv

    if verify:
        sys.exit(0 if verify_all_indices() else 1)
    else:
        index = build_index()
        out_path = AGORA_SPECS / "bundle_index.json"
        with open(out_path, "w") as f:
            json.dump(index, f, indent=2)
            f.write("\n")
        print(f"Written: {out_path}")
        for rel, digest in index["files"].items():
            print(f"  {digest[:12]}...  {rel}")


if __name__ == "__main__":
    main()
