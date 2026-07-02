#!/usr/bin/env python3
"""Audit Management BFF list endpoints for oversized-list contract smells.

The audit is intentionally static and conservative. It does not prove an
endpoint is slow; it catches source patterns that have repeatedly produced slow
management tables: duplicate envelopes, duplicated camel/snake keys, raw source
records in list DTOs, and aggregate endpoints that embed complete child payloads.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_SOURCE = Path("services/control-plane/bff/main.py")

LIST_ALIAS_KEYS = {
    "items",
    "rows",
    "data",
    "pools",
    "holdings",
    "positions",
    "exposures",
    "rankings",
    "rankingBlocks",
    "ranking_blocks",
    "recommendations",
    "sections",
    "movers",
    "tiers",
    "assignments",
    "cells",
    "buckets",
    "attributions",
    "persona_fleet",
    "personaFleet",
}

TOP_LEVEL_LIST_ALIASES = LIST_ALIAS_KEYS - {"data"}

SOURCE_RECORD_KEYS = {
    "sourceRecord",
    "source_record",
    "sourceDocument",
    "source_document",
}

EMBEDDED_AGGREGATE_KEYS = {
    "persona_league",
    "personaLeague",
    "human_inbox",
    "humanInbox",
    "portfolioBook",
    "portfolio_book",
    "portfolioBookExposure",
    "portfolio_book_exposure",
    "portfolioBookPositions",
    "portfolio_book_positions",
    "strategyAllocation",
    "strategy_allocation",
    "performanceAttribution",
    "performance_attribution",
}

HEAVY_HELPER_HINTS = {
    "routePolicy",
    "capabilities",
    "bindings",
    "sessions",
    "evaluations",
    "memory",
    "allowedActions",
    "allowed_actions",
    "dataSourceStatus",
    "data_source_status",
    "dataSources",
    "data_sources",
    "currentResearchProjects",
    "current_research_projects",
    "researchStatus",
    "research_status",
}

PROJECT_BEFORE_PAGE_FUNCTIONS = {
    "bff_management_persona_fleet": (
        "Builds full persona health plus related collections before page slicing."
    ),
    "_human_inbox_payload": (
        "Collects every inbox source and persona readiness row before filters and page slicing."
    ),
    "_pm12_persona_league_rows": (
        "Projects route policy, capabilities, sessions, evaluations, memory, and health before route pagination."
    ),
    "_pm12_performance_attribution_response": (
        "Builds all attribution rows from runtime telemetry before page slicing."
    ),
    "_management_cost_attribution_response": (
        "Builds all cost-attribution rows from runtime telemetry before page slicing."
    ),
    "bff_management_portfolio_book_holdings": (
        "Builds all holdings from every runtime and telemetry record before page slicing."
    ),
    "bff_management_portfolio_book_exposure": (
        "Builds all exposure rows before page slicing."
    ),
}


@dataclass(frozen=True)
class Issue:
    fingerprint: str
    severity: str
    category: str
    function: str
    route: str
    line: int
    title: str
    evidence: str
    recommendation: str


def camel_to_snake(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def stable_fingerprint(
    *,
    category: str,
    function_name: str,
    route: str,
    evidence_key: str,
) -> str:
    payload = "|".join(
        [
            category,
            function_name,
            route,
            re.sub(r"\s+", " ", evidence_key.strip()),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def string_key(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def route_from_decorator(decorator: ast.AST) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr.lower() not in {"get", "post", "put", "patch", "delete", "options"}:
        return None
    if not decorator.args:
        return None
    first_arg = decorator.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value
    return None


def function_routes(function: ast.AsyncFunctionDef | ast.FunctionDef) -> list[str]:
    routes: list[str] = []
    for decorator in function.decorator_list:
        route = route_from_decorator(decorator)
        if route:
            routes.append(route)
    return routes


def is_management_function(function: ast.AsyncFunctionDef | ast.FunctionDef, routes: Sequence[str]) -> bool:
    if any(route.startswith("/bff/management") for route in routes):
        return True
    if function.name in {"bff_persona_league", "_persona_league_payload"}:
        return True
    return function.name.startswith(
        (
            "_management_",
            "_pm12_",
            "_project_persona_league",
            "_human_inbox",
            "_governance_ledger",
            "_hiq_backlog",
            "_intervention_stream",
            "bff_management_",
        )
    )


def iter_dicts(function: ast.AsyncFunctionDef | ast.FunctionDef) -> Iterable[ast.Dict]:
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            yield node


def keys_for_dict(node: ast.Dict) -> list[str]:
    return [key for key in (string_key(key_node) for key_node in node.keys) if key]


def issue(
    *,
    severity: str,
    category: str,
    function_name: str,
    route: str,
    line: int,
    title: str,
    evidence: str,
    recommendation: str,
    evidence_key: str | None = None,
) -> Issue:
    fingerprint = stable_fingerprint(
        category=category,
        function_name=function_name,
        route=route,
        evidence_key=evidence_key or evidence,
    )
    return Issue(
        fingerprint=fingerprint,
        severity=severity,
        category=category,
        function=function_name,
        route=route,
        line=line,
        title=title,
        evidence=evidence,
        recommendation=recommendation,
    )


def duplicate_value_groups(node: ast.Dict) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for key_node, value_node in zip(node.keys, node.values):
        key = string_key(key_node)
        if not key:
            continue
        if key not in LIST_ALIAS_KEYS:
            continue
        value_dump = ast.dump(value_node, annotate_fields=False, include_attributes=False)
        groups.setdefault(value_dump, []).append(key)
    return [sorted(keys) for keys in groups.values() if len(keys) > 1]


def audit_dict(
    *,
    node: ast.Dict,
    function_name: str,
    route: str,
) -> list[Issue]:
    found: list[Issue] = []
    keys = keys_for_dict(node)
    key_set = set(keys)
    line = getattr(node, "lineno", 0)

    if "data" in key_set:
        duplicated_top_keys = sorted(key_set & TOP_LEVEL_LIST_ALIASES)
        if duplicated_top_keys:
            found.append(issue(
                severity="P0",
                category="duplicate-envelope",
                function_name=function_name,
                route=route,
                line=line,
                title="List response exposes both data and top-level list aliases",
                evidence=f"keys={['data', *duplicated_top_keys]}",
                recommendation=(
                    "Return one canonical envelope: data.items plus page_info/meta. "
                    "Do not repeat the same list at top level."
                ),
                evidence_key="data+" + ",".join(duplicated_top_keys),
            ))

    for group in duplicate_value_groups(node):
        if any(key in TOP_LEVEL_LIST_ALIASES for key in group):
            found.append(issue(
                severity="P0",
                category="duplicate-list-alias",
                function_name=function_name,
                route=route,
                line=line,
                title="Same list value is returned under multiple names",
                evidence=f"aliases={group}",
                recommendation=(
                    "Pick the semantic list field once. Backward-compatible aliases "
                    "must be versioned or moved behind a temporary adapter, not returned forever."
                ),
                evidence_key="aliases=" + ",".join(group),
            ))

    casing_pairs: list[tuple[str, str]] = []
    for key in sorted(key_set):
        if not any(ch.isupper() for ch in key):
            continue
        snake = camel_to_snake(key)
        if snake in key_set and snake != key:
            casing_pairs.append((key, snake))
    if casing_pairs:
        examples = ", ".join(f"{camel}+{snake}" for camel, snake in casing_pairs[:12])
        suffix = "" if len(casing_pairs) <= 12 else f", ... ({len(casing_pairs)} pairs)"
        found.append(issue(
            severity="P1",
            category="camel-snake-duplicate",
            function_name=function_name,
            route=route,
            line=line,
            title="DTO returns camelCase and snake_case copies of fields",
            evidence=f"{examples}{suffix}",
            recommendation=(
                "Use one wire casing for Management BFF DTOs. If the frontend "
                "needs another casing, convert in its adapter."
            ),
            evidence_key=";".join(f"{camel}:{snake}" for camel, snake in casing_pairs),
        ))

    source_record_keys = sorted(key_set & SOURCE_RECORD_KEYS)
    if source_record_keys:
        found.append(issue(
            severity="P0",
            category="source-record-in-list-dto",
            function_name=function_name,
            route=route,
            line=line,
            title="List DTO includes raw source record/document fields",
            evidence=f"keys={source_record_keys}",
            recommendation=(
                "List rows must be slim summaries. Move raw source records to a "
                "detail endpoint or a privileged debug endpoint."
            ),
            evidence_key="source=" + ",".join(source_record_keys),
        ))

    embedded_keys = sorted(key_set & EMBEDDED_AGGREGATE_KEYS)
    if len(embedded_keys) >= 2:
        found.append(issue(
            severity="P0",
            category="embedded-aggregate-payload",
            function_name=function_name,
            route=route,
            line=line,
            title="List or board payload embeds related aggregate collections",
            evidence=f"keys={embedded_keys}",
            recommendation=(
                "Return summary counts, health, and hrefs. Fetch related aggregates "
                "from dedicated endpoints only when the user opens the detail/section."
            ),
            evidence_key="embedded=" + ",".join(embedded_keys),
        ))

    heavy_keys = sorted(key_set & HEAVY_HELPER_HINTS)
    if len(heavy_keys) >= 4:
        found.append(issue(
            severity="P1",
            category="heavy-row-helper",
            function_name=function_name,
            route=route,
            line=line,
            title="Row projection contains detail-grade nested helper data",
            evidence=f"keys={heavy_keys}",
            recommendation=(
                "List rows should expose counts, latest status, and links. Move nested "
                "policy/capability/session/memory/source detail to detail endpoints."
            ),
            evidence_key="heavy=" + ",".join(heavy_keys),
        ))

    return found


def source_for_node(source_lines: Sequence[str], node: ast.AST) -> str:
    start = max(getattr(node, "lineno", 1) - 1, 0)
    end = getattr(node, "end_lineno", start + 1)
    return "\n".join(source_lines[start:end])


def audit_function(
    *,
    function: ast.AsyncFunctionDef | ast.FunctionDef,
    routes: Sequence[str],
    source_lines: Sequence[str],
) -> list[Issue]:
    route = next((candidate for candidate in routes if candidate.startswith("/bff/management")), "")
    found: list[Issue] = []
    for dict_node in iter_dicts(function):
        found.extend(audit_dict(node=dict_node, function_name=function.name, route=route))
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = string_key(target.slice)
            if key not in SOURCE_RECORD_KEYS:
                continue
            found.append(issue(
                severity="P0",
                category="source-record-in-list-dto",
                function_name=function.name,
                route=route,
                line=getattr(node, "lineno", 0),
                title="List DTO assigns raw source record/document fields",
                evidence=f"assignment to {key}",
                recommendation=(
                    "List rows must be slim summaries. Move raw source records to a "
                    "detail endpoint or a privileged debug endpoint."
                ),
                evidence_key=f"assignment:{key}",
            ))

    function_source = source_for_node(source_lines, function)
    if function.name in PROJECT_BEFORE_PAGE_FUNCTIONS and "_page_slice(" in function_source:
        found.append(issue(
            severity="P1",
            category="project-before-page",
            function_name=function.name,
            route=route,
            line=getattr(function, "lineno", 0),
            title="Endpoint/helper projects broad aggregates before page slicing",
            evidence=PROJECT_BEFORE_PAGE_FUNCTIONS[function.name],
            recommendation=(
                "Apply query filters before expensive fanout/projection and page before "
                "detail-grade hydration. Use a detail endpoint for row expansion."
            ),
            evidence_key=function.name,
        ))

    if function.name == "_management_board_pack_response":
        found.append(issue(
            severity="P0",
            category="board-pack-full-child-payloads",
            function_name=function.name,
            route=route,
            line=getattr(function, "lineno", 0),
            title="Board-pack composes complete child endpoint payloads",
            evidence=(
                "Calls portfolio-book, persona-league, movers, and attribution endpoints, "
                "then embeds their full payloads under data."
            ),
            recommendation=(
                "Board-pack should be summary-only: section status, counts, deltas, and hrefs. "
                "Do not nest child list payloads."
            ),
            evidence_key="_management_board_pack_response",
        ))

    return found


def audit_source(path: Path) -> list[Issue]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = function_routes(node)
        if not is_management_function(node, routes):
            continue
        issues.extend(audit_function(function=node, routes=routes, source_lines=source_lines))
    return sorted(
        dedupe_issues(issues),
        key=lambda item: (item.severity, item.category, item.function, item.line, item.fingerprint),
    )


def dedupe_issues(issues: Iterable[Issue]) -> list[Issue]:
    seen: set[str] = set()
    output: list[Issue] = []
    for item in issues:
        if item.fingerprint in seen:
            continue
        seen.add(item.fingerprint)
        output.append(item)
    return output


def baseline_payload(path: Path, issues: Sequence[Issue]) -> dict[str, object]:
    return {
        "schema": "pantheon.management-list-contract-baseline.v1",
        "source": str(path),
        "issue_count": len(issues),
        "fingerprints": [item.fingerprint for item in issues],
        "issues": [
            {
                "fingerprint": item.fingerprint,
                "severity": item.severity,
                "category": item.category,
                "function": item.function,
                "route": item.route,
                "title": item.title,
                "evidence": item.evidence,
                "recommendation": item.recommendation,
            }
            for item in issues
        ],
    }


def load_baseline(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, list):
        raise ValueError(f"{path} does not contain a fingerprints list")
    return {str(item) for item in fingerprints}


def markdown_table(issues: Sequence[Issue], *, new_fingerprints: set[str] | None = None) -> str:
    new_fingerprints = new_fingerprints or set()
    lines = [
        "# Management List Contract Audit",
        "",
        f"Total issues: {len(issues)}",
        "",
        "| New | Severity | Category | Function | Route | Line | Evidence |",
        "|---|---|---|---|---|---:|---|",
    ]
    for item in issues:
        marker = "yes" if item.fingerprint in new_fingerprints else ""
        evidence = item.evidence.replace("|", "\\|")
        route = item.route or ""
        lines.append(
            f"| {marker} | {item.severity} | {item.category} | `{item.function}` | "
            f"`{route}` | {item.line} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def json_report(
    issues: Sequence[Issue],
    *,
    source: Path,
    baseline: Path | None,
    new_fingerprints: set[str],
    missing_fingerprints: set[str],
) -> str:
    payload = {
        "source": str(source),
        "baseline": str(baseline) if baseline else None,
        "issue_count": len(issues),
        "new_issue_count": len(new_fingerprints),
        "retired_baseline_issue_count": len(missing_fingerprints),
        "new_fingerprints": sorted(new_fingerprints),
        "retired_baseline_fingerprints": sorted(missing_fingerprints),
        "issues": [asdict(item) for item in issues],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def summary_report(
    issues: Sequence[Issue],
    *,
    source: Path,
    baseline: Path | None,
    new_fingerprints: set[str],
    missing_fingerprints: set[str],
) -> str:
    return (
        f"source={source} baseline={baseline or ''} issues={len(issues)} "
        f"new={len(new_fingerprints)} retired={len(missing_fingerprints)}\n"
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--fail-on-new", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json", "summary"), default="markdown")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    issues = audit_source(args.source)
    current_fingerprints = {item.fingerprint for item in issues}
    baseline_fingerprints: set[str] = set()
    if args.baseline:
        baseline_fingerprints = load_baseline(args.baseline)
    new_fingerprints = current_fingerprints - baseline_fingerprints if baseline_fingerprints else set()
    missing_fingerprints = baseline_fingerprints - current_fingerprints

    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(baseline_payload(args.source, issues), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.format == "json":
        sys.stdout.write(json_report(
            issues,
            source=args.source,
            baseline=args.baseline,
            new_fingerprints=new_fingerprints,
            missing_fingerprints=missing_fingerprints,
        ))
    elif args.format == "summary":
        sys.stdout.write(summary_report(
            issues,
            source=args.source,
            baseline=args.baseline,
            new_fingerprints=new_fingerprints,
            missing_fingerprints=missing_fingerprints,
        ))
    else:
        sys.stdout.write(markdown_table(issues, new_fingerprints=new_fingerprints))
        if args.baseline:
            sys.stdout.write(
                f"\nBaseline: {args.baseline}; new={len(new_fingerprints)}; "
                f"retired={len(missing_fingerprints)}\n"
            )

    if args.fail_on_new and new_fingerprints:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
