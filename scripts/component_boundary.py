#!/usr/bin/env python3
"""Classify a change against the one component-boundary manifest.

The classifier is deliberately small: it does not decide approval, deployment,
or task routing.  It only tells CI whether a diff touches product runtime, so a
development-tooling change is not made to run unrelated product tests.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "02-architecture" / "component-boundary.yaml"


class BoundaryError(ValueError):
    """The manifest or requested path has no unambiguous component boundary."""


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BoundaryError(f"cannot read boundary manifest: {exc}") from exc
    if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
        raise BoundaryError("component boundary manifest must use schema_version 1")
    components = parsed.get("components")
    domains = parsed.get("domains")
    if not isinstance(components, list) or not isinstance(domains, dict):
        raise BoundaryError("component boundary manifest is missing domains or components")
    for component in components:
        if not isinstance(component, dict):
            raise BoundaryError("component entry must be an object")
        component_id = component.get("id")
        domain = component.get("domain")
        paths = component.get("paths")
        if not isinstance(component_id, str) or not component_id:
            raise BoundaryError("component entry requires an id")
        if domain not in domains:
            raise BoundaryError(f"component {component_id} uses an unknown domain")
        if paths is None and isinstance(component.get("repository"), str):
            component["paths"] = []
            paths = []
        if not isinstance(paths, list) or not all(isinstance(item, str) and item for item in paths):
            raise BoundaryError(f"component {component_id} requires paths")
    return parsed


def pattern_matches(path: str, pattern: str) -> bool:
    normalized_path = path.lstrip("./")
    normalized_pattern = pattern.lstrip("./")
    if normalized_pattern.endswith("/"):
        return normalized_path.startswith(normalized_pattern)
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def classify_paths(manifest: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    components = manifest["components"]
    classified: list[dict[str, Any]] = []
    unknown: list[str] = []
    for path in paths:
        matches = [
            {"id": component["id"], "domain": component["domain"]}
            for component in components
            if any(pattern_matches(path, pattern) for pattern in component["paths"])
        ]
        if not matches:
            unknown.append(path)
        classified.append({"path": path, "components": matches})
    domains = sorted({match["domain"] for item in classified for match in item["components"]})
    product_touched = "product_runtime" in domains
    return {
        "paths": classified,
        "unknown_paths": unknown,
        "domains": domains,
        "product_touched": product_touched,
        "development_tooling_touched": "development_tooling" in domains,
        "delivery_touched": "delivery" in domains,
        "tooling_only": bool(paths) and not product_touched,
    }


def changed_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only", base, head],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise BoundaryError(result.stderr.strip() or "unable to read git diff")
    return [line for line in result.stdout.splitlines() if line]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--base", help="base git revision")
    parser.add_argument("--head", help="head git revision")
    parser.add_argument("--path", action="append", default=[], help="explicit changed path (repeatable)")
    parser.add_argument("--json", action="store_true", help="print machine-readable classification")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if bool(args.base) != bool(args.head):
        raise SystemExit("--base and --head must be supplied together")
    if not args.path and not args.base:
        raise SystemExit("supply --path or both --base and --head")
    paths = list(args.path)
    if args.base:
        paths.extend(changed_paths(args.base, args.head))
    result = classify_paths(load_manifest(args.manifest), paths)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("tooling_only=" + str(result["tooling_only"]).lower())
        print("domains=" + ",".join(result["domains"]))
        if result["unknown_paths"]:
            print("unknown=" + ",".join(result["unknown_paths"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
