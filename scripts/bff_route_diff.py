#!/usr/bin/env python3
"""Diff the execute-plans frontend route manifest against the BFF backend manifest."""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_MANIFEST = REPO_ROOT / "services/control-plane/bff/contract_snapshots/backend_routes_manifest.json"
FRONTEND_MANIFEST = REPO_ROOT / "services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json"
BASELINE_PATH = REPO_ROOT / "docs/bff/contract_snapshots/route-diff-baseline.json"

ROUTE_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{[^/{}]+\}")

NON_BLOCKING_ROUTE_STATUSES = {
    "deferred",
    "deferred_with_task",
    "deprecated",
    "mock_only",
    "mock_only_dev",
    "superseded",
    "superseded_with_reason",
}
MOCK_ONLY_MARKERS = {"mock_only", "mock-only", "mock_only_dev", "mock-only-dev"}


def normalize_path(path: str) -> str:
    cleaned = str(path or "").strip().split("?", 1)[0].rstrip("/")
    if not cleaned:
        cleaned = "/"
    return ROUTE_PARAM_RE.sub("{param}", cleaned)


def route_key(method: str, path: str) -> str:
    return f"{str(method or '').upper()} {normalize_path(path)}"


def entry_key(entry: dict[str, Any]) -> str:
    return route_key(str(entry.get("method") or ""), str(entry.get("path") or ""))


def covered_by_key(entry: dict[str, Any]) -> str:
    covered_by = str(entry.get("covered_by") or "").strip()
    method, separator, path = covered_by.partition(" ")
    if not separator:
        return ""
    return route_key(method, path)


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def route_summary(entry: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {"key": key or entry_key(entry)}
    for field in (
        "method",
        "path",
        "family",
        "status",
        "task_id",
        "covered_by",
        "reason",
        "proof",
        "source_mode",
    ):
        value = entry.get(field)
        if value not in (None, ""):
            summary[field] = value
    return summary


def index_entries(entries: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    index: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for entry in entries:
        key = entry_key(entry)
        if key in index:
            duplicates.append(
                {
                    "key": key,
                    "first": route_summary(index[key], key),
                    "duplicate": route_summary(entry, key),
                }
            )
            continue
        index[key] = entry
    return index, duplicates


def has_mock_only_marker(entry: dict[str, Any]) -> bool:
    if entry.get("mock_only") is True or entry.get("mockOnly") is True:
        return True
    for field in ("status", "source_mode", "mode", "classification"):
        marker = str(entry.get(field) or "").strip().lower()
        if marker in MOCK_ONLY_MARKERS:
            return True
    tags = entry.get("tags")
    if isinstance(tags, list):
        return any(str(tag).strip().lower() in MOCK_ONLY_MARKERS for tag in tags)
    return False


def route_requires_counterpart(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or "").strip().lower()
    return status not in NON_BLOCKING_ROUTE_STATUSES and not has_mock_only_marker(entry)


def frontend_requires_backend(entry: dict[str, Any]) -> bool:
    return route_requires_counterpart(entry)


def backend_requires_frontend(entry: dict[str, Any]) -> bool:
    return route_requires_counterpart(entry)


def frontend_referenced_backend_keys(entries: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for entry in entries:
        keys.add(entry_key(entry))
        covered = covered_by_key(entry)
        if covered:
            keys.add(covered)
    return keys


def _empty_failure_map() -> dict[str, list[dict[str, Any]]]:
    return {
        "backend_missing_frontend": [],
        "frontend_missing_backend": [],
        "naming_mismatches": [],
        "duplicate_backend_keys": [],
        "duplicate_frontend_keys": [],
    }


def build_route_diff(
    backend_manifest: dict[str, Any],
    frontend_manifest: dict[str, Any],
    *,
    backend_path: Path = BACKEND_MANIFEST,
    frontend_path: Path = FRONTEND_MANIFEST,
    mode: str = "fail-hard",
) -> dict[str, Any]:
    backend_entries = list(backend_manifest.get("entries") or [])
    frontend_entries = list(frontend_manifest.get("entries") or [])
    backend_index, duplicate_backend = index_entries(backend_entries)
    frontend_index, duplicate_frontend = index_entries(frontend_entries)

    failures = _empty_failure_map()
    failures["duplicate_backend_keys"] = duplicate_backend
    failures["duplicate_frontend_keys"] = duplicate_frontend

    for key in sorted(frontend_index):
        frontend_entry = frontend_index[key]
        if not frontend_requires_backend(frontend_entry):
            continue

        covered_key = covered_by_key(frontend_entry)
        direct_backend = backend_index.get(key)
        covered_backend = backend_index.get(covered_key) if covered_key else None
        if direct_backend is None and covered_backend is None:
            row = route_summary(frontend_entry, key)
            row["expected_backend_key"] = covered_key or key
            failures["frontend_missing_backend"].append(row)
            continue

        if direct_backend is None:
            continue

        frontend_family = str(frontend_entry.get("family") or "")
        backend_family = str(direct_backend.get("family") or "")
        if frontend_family and backend_family and frontend_family != backend_family:
            failures["naming_mismatches"].append(
                {
                    "key": key,
                    "frontend_family": frontend_family,
                    "backend_family": backend_family,
                    "frontend": route_summary(frontend_entry, key),
                    "backend": route_summary(direct_backend, key),
                }
            )

    frontend_backend_keys = frontend_referenced_backend_keys(frontend_entries)
    backend_missing_frontend = [
        route_summary(backend_index[key], key)
        for key in sorted(backend_index)
        if key not in frontend_backend_keys and backend_requires_frontend(backend_index[key])
    ]
    warnings = {"backend_missing_frontend": backend_missing_frontend}
    if mode == "fail-hard":
        failures["backend_missing_frontend"] = backend_missing_frontend
        warnings = {"backend_missing_frontend": []}

    failure_counts = {name: len(rows) for name, rows in failures.items()}
    warning_counts = {name: len(rows) for name, rows in warnings.items()}
    failure_count = sum(failure_counts.values())
    warning_count = sum(warning_counts.values())
    mode_failure_count = failure_count

    return {
        "metadata": {
            "task_id": "BFF-CONSOL-026",
            "mode": mode,
            "generator": "scripts/bff_route_diff.py",
            "backend_manifest": repo_relative(backend_path),
            "frontend_manifest": repo_relative(frontend_path),
            "backend_snapshot_date": backend_manifest.get("metadata", {}).get("snapshot_date"),
            "frontend_snapshot_date": frontend_manifest.get("metadata", {}).get("snapshot_date"),
            "non_blocking_route_statuses": sorted(NON_BLOCKING_ROUTE_STATUSES),
            "rules": [
                "Frontend active routes absent from the backend are failures unless marked mock-only, deferred, superseded, or covered_by a backend route.",
                "Backend active routes absent from the frontend manifest are failures in fail-hard mode unless marked mock-only, deferred, or superseded.",
                "Shared active method/path routes with mismatched manifest family names are failures.",
            ],
        },
        "summary": {
            "status": "pass" if mode_failure_count == 0 else "fail",
            "backend_routes": len(backend_entries),
            "frontend_routes": len(frontend_entries),
            "failures": failure_counts,
            "warnings": warning_counts,
            "failure_count": failure_count,
            "warning_count": warning_count,
            "mode_failure_count": mode_failure_count,
        },
        "failures": failures,
        "warnings": warnings,
    }


def failure_surface(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {
            "failures": diff.get("summary", {}).get("failures", {}),
            "failure_count": diff.get("summary", {}).get("failure_count", 0),
        },
        "failures": diff.get("failures", {}),
    }


def warning_surface(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {
            "warnings": diff.get("summary", {}).get("warnings", {}),
            "warning_count": diff.get("summary", {}).get("warning_count", 0),
        },
        "warnings": diff.get("warnings", {}),
    }


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def baseline_drift_lines(current: dict[str, Any], baseline: dict[str, Any], *, strict_warnings: bool) -> list[str]:
    lines: list[str] = []
    current_failure = canonical_json(failure_surface(current)).splitlines(keepends=True)
    baseline_failure = canonical_json(failure_surface(baseline)).splitlines(keepends=True)
    if current_failure != baseline_failure:
        lines.extend(
            difflib.unified_diff(
                baseline_failure,
                current_failure,
                fromfile="baseline failure surface",
                tofile="current failure surface",
            )
        )

    current_warning = canonical_json(warning_surface(current)).splitlines(keepends=True)
    baseline_warning = canonical_json(warning_surface(baseline)).splitlines(keepends=True)
    if strict_warnings and current_warning != baseline_warning:
        lines.extend(
            difflib.unified_diff(
                baseline_warning,
                current_warning,
                fromfile="baseline warning surface",
                tofile="current warning surface",
            )
        )
    return lines


def render_summary(diff: dict[str, Any]) -> str:
    summary = diff["summary"]
    failures = summary["failures"]
    warnings = summary["warnings"]
    lines = [
        f"BFF route diff: {summary['status']}",
        f"backend routes: {summary['backend_routes']}",
        f"frontend routes: {summary['frontend_routes']}",
        f"failures: {summary['failure_count']} {failures}",
        f"warnings: {summary['warning_count']} {warnings}",
    ]

    missing = diff["failures"]["frontend_missing_backend"]
    mismatches = diff["failures"]["naming_mismatches"]
    backend_only_failures = diff["failures"].get("backend_missing_frontend", [])
    backend_only_warnings = diff["warnings"].get("backend_missing_frontend", [])
    if missing:
        lines.append("")
        lines.append("Frontend routes missing in backend:")
        for row in missing[:25]:
            lines.append(f"- {row['key']} status={row.get('status', '')} family={row.get('family', '')}")
        if len(missing) > 25:
            lines.append(f"- ... {len(missing) - 25} more")
    if mismatches:
        lines.append("")
        lines.append("Route family/name mismatches:")
        for row in mismatches[:25]:
            lines.append(f"- {row['key']}: frontend={row['frontend_family']} backend={row['backend_family']}")
        if len(mismatches) > 25:
            lines.append(f"- ... {len(mismatches) - 25} more")
    if backend_only_failures:
        lines.append("")
        lines.append("Backend routes missing in frontend:")
        for row in backend_only_failures[:25]:
            lines.append(f"- {row['key']} family={row.get('family', '')} status={row.get('status', '')}")
        if len(backend_only_failures) > 25:
            lines.append(f"- ... {len(backend_only_failures) - 25} more")
    if backend_only_warnings:
        lines.append("")
        lines.append("Backend-only warning sample:")
        for row in backend_only_warnings[:25]:
            lines.append(f"- {row['key']} family={row.get('family', '')} status={row.get('status', '')}")
        if len(backend_only_warnings) > 25:
            lines.append(f"- ... {len(backend_only_warnings) - 25} more")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff backend and frontend BFF route manifests.")
    parser.add_argument("--backend", default=str(BACKEND_MANIFEST), help="Backend route manifest JSON path.")
    parser.add_argument("--frontend", default=str(FRONTEND_MANIFEST), help="Frontend route manifest JSON path.")
    parser.add_argument("--baseline", default=str(BASELINE_PATH), help="Checked-in route diff baseline JSON path.")
    parser.add_argument("--mode", choices=["fail-but-warn", "fail-hard"], default="fail-hard")
    parser.add_argument("--dump", action="store_true", help="Print current diff JSON instead of a text summary.")
    parser.add_argument("--write-baseline", action="store_true", help="Write the current diff to the baseline path.")
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="Fail if the hard-failure surface drifts from the checked-in baseline.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="When checking a fail-but-warn baseline, treat backend-only warning drift as a failure.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    backend_path = Path(args.backend)
    frontend_path = Path(args.frontend)
    baseline_path = Path(args.baseline)
    diff = build_route_diff(
        load_manifest(backend_path),
        load_manifest(frontend_path),
        backend_path=backend_path,
        frontend_path=frontend_path,
        mode=args.mode,
    )

    if args.write_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(canonical_json(diff), encoding="utf-8")
        print(f"Wrote route diff baseline to {baseline_path}")
        if not args.check_baseline:
            return 0

    if args.check_baseline:
        if not baseline_path.exists():
            print(f"Route diff baseline not found: {baseline_path}", file=sys.stderr)
            return 1
        baseline = load_manifest(baseline_path)
        drift = baseline_drift_lines(diff, baseline, strict_warnings=args.strict_warnings)
        if drift:
            print("Route diff baseline drift detected:", file=sys.stderr)
            print("".join(drift), file=sys.stderr)
            return 1
        if args.dump:
            print(canonical_json(diff), end="")
        else:
            warning_note = " with strict warnings" if args.strict_warnings else " fail-hard surface"
            print(f"Route diff baseline matches current{warning_note}.")
            if args.mode == "fail-hard":
                grandfathered = diff["summary"]["failures"].get("backend_missing_frontend", 0)
                if grandfathered:
                    print(f"Grandfathered backend-only routes locked by baseline: {grandfathered}")
            else:
                print(render_summary(diff))
        return 0

    if args.dump:
        print(canonical_json(diff), end="")
    elif not args.write_baseline and not args.check_baseline:
        print(render_summary(diff))

    return 1 if diff["summary"]["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
