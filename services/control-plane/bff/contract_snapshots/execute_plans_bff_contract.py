from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SNAPSHOT_PATH = Path(__file__).with_name("execute_plans_bff_routes.json")
ROUTE_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{[^/{}]+\}")
IGNORED_METHODS = {"HEAD", "OPTIONS"}


def load_registry(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_path(path: str) -> str:
    cleaned = str(path or "").strip()
    cleaned = cleaned.split("?", 1)[0].rstrip("/")
    if not cleaned:
        cleaned = "/"
    cleaned = ROUTE_PARAM_RE.sub("{param}", cleaned)
    return cleaned


def route_key(method: str, path: str) -> str:
    return f"{method.upper()} {normalize_path(path)}"


def entry_key(entry: dict[str, Any]) -> str:
    return route_key(str(entry["method"]), str(entry["path"]))


def app_route_index(app: Any) -> set[str]:
    routes: set[str] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            method = str(method).upper()
            if method not in IGNORED_METHODS:
                routes.add(route_key(method, path))
    return routes


def implemented_entry_is_live(entry: dict[str, Any], live_routes: set[str]) -> bool:
    status = entry.get("status")
    if status == "implemented":
        return entry_key(entry) in live_routes
    if status == "implemented_by_alias":
        covered_by = str(entry.get("covered_by") or "")
        if not covered_by:
            return False
        method, _, path = covered_by.partition(" ")
        return bool(method and path and route_key(method, path) in live_routes)
    return True


def coverage_rows(registry: dict[str, Any], live_routes: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in registry.get("entries", []):
        status = str(entry.get("status"))
        key = entry_key(entry)
        live = key in live_routes
        alias_live = implemented_entry_is_live(entry, live_routes) if status == "implemented_by_alias" else False
        rows.append(
            {
                "family": str(entry.get("family") or "unknown"),
                "method": str(entry.get("method")),
                "path": str(entry.get("path")),
                "status": status,
                "task_id": str(entry.get("task_id") or ""),
                "covered_by": str(entry.get("covered_by") or ""),
                "live": live or alias_live,
            }
        )
    return rows


def summarize_by_family(rows: Iterable[dict[str, Any]]) -> dict[str, Counter[str]]:
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        summary[str(row["family"])][str(row["status"])] += 1
    return dict(sorted(summary.items()))


def format_coverage_report(registry: dict[str, Any], live_routes: set[str]) -> str:
    rows = coverage_rows(registry, live_routes)
    summary = summarize_by_family(rows)
    lines = [
        "Execute-Plans BFF Route Coverage",
        f"snapshot_date: {registry.get('metadata', {}).get('snapshot_date')}",
        f"entries: {len(rows)}",
        "",
        "Family Coverage",
        "family | implemented | alias | missing | deferred | superseded",
        "--- | ---: | ---: | ---: | ---: | ---:",
    ]
    for family, counts in summary.items():
        lines.append(
            " | ".join(
                [
                    family,
                    str(counts.get("implemented", 0)),
                    str(counts.get("implemented_by_alias", 0)),
                    str(counts.get("missing", 0)),
                    str(counts.get("deferred_with_task", 0)),
                    str(counts.get("superseded_with_reason", 0)),
                ]
            )
        )

    outstanding = [
        row
        for row in rows
        if row["status"] in {"missing", "deferred_with_task"}
    ]
    if outstanding:
        lines.extend(["", "Outstanding Routes"])
        for row in outstanding:
            lines.append(
                f"- {row['status']}: {row['method']} {row['path']} "
                f"({row['family']}, {row['task_id']})"
            )

    implemented_gaps = [
        row
        for row in rows
        if row["status"] in {"implemented", "implemented_by_alias"} and not row["live"]
    ]
    if implemented_gaps:
        lines.extend(["", "Implemented Rows Not Live"])
        for row in implemented_gaps:
            covered = f" via {row['covered_by']}" if row["covered_by"] else ""
            lines.append(f"- {row['method']} {row['path']}{covered}")

    return "\n".join(lines)
