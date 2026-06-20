#!/usr/bin/env python3
"""Compute and verify the Agora v1 schema bundle sha256 index.

Usage:
    python3 scripts/agora_schema_bundle.py [--verify]

Without --verify: writes services/control-plane/specs/agora/bundle_index.json.
With --verify: reads the existing bundle_index.json and checks all digests.
"""

import hashlib
import json
import os
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
    for rel_path, expected in index.get("files", {}).items():
        path = REPO_ROOT / "services" / "control-plane" / rel_path
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


def main() -> None:
    verify = "--verify" in sys.argv

    if verify:
        index_path = AGORA_SPECS / "bundle_index.json"
        if not index_path.exists():
            print(f"ERROR: bundle_index.json not found at {index_path}", file=sys.stderr)
            sys.exit(1)
        with open(index_path) as f:
            index = json.load(f)
        ok = verify_index(index)
        sys.exit(0 if ok else 1)
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
