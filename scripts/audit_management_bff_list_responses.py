#!/usr/bin/env python3
"""Dynamically audit Management BFF list/table responses.

The static Management list contract audit catches source smells. This audit
executes the registered FastAPI routes with a local TestClient and checks the
actual JSON sent to browsers: response size, canonical envelopes, duplicate
top-level list aliases, row size, and duplicated snake/camel keys.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_RESPONSE_HARD_LIMIT_BYTES = 1_000_000
DEFAULT_ROW_HARD_LIMIT_BYTES = 16_000
DEFAULT_PAGE_SIZE_LIMIT = 50

TOP_LEVEL_LIST_ALIASES = {
    "items",
    "rows",
    "summary",
    "checks",
    "evidence_refs",
    "rankings",
    "pools",
    "holdings",
    "positions",
    "exposures",
    "sections",
    "movers",
    "tiers",
    "recommendations",
}

EXCLUDED_PREFIXES = (
    "/bff/management/readiness/",
)

EXCLUDED_PATHS = {
    "/bff/management/cockpit",
    "/bff/management/shell-summary",
    "/bff/management/quarterly-ranking/formula",
    "/bff/management/quarterly-ranking/drilldown",
}


@dataclass(frozen=True)
class Issue:
    severity: str
    category: str
    path: str
    title: str
    evidence: str


def camel_to_snake(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def duplicate_casing_pairs(value: Any, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        keys = {key for key in value if isinstance(key, str)}
        for key in sorted(keys):
            snake = camel_to_snake(key)
            if snake != key and snake in keys:
                findings.append(f"{path}.{key}/{snake}")
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                findings.extend(duplicate_casing_pairs(nested, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, nested in enumerate(value[:5]):
            if isinstance(nested, (dict, list)):
                findings.extend(duplicate_casing_pairs(nested, path=f"{path}[{index}]"))
    return findings


def iter_management_get_paths(app: Any) -> Iterable[str]:
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/bff/management"):
            continue
        if "GET" not in methods:
            continue
        if "{" in path or "}" in path:
            continue
        if path in EXCLUDED_PATHS:
            continue
        if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            continue
        yield path


def should_audit_body(path: str, body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    if "page_info" in body:
        return True
    return path in {"/bff/management/strategy-seeds"}


def list_items(body: dict[str, Any]) -> list[Any]:
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(body.get("items"), list):
        return body["items"]
    return []


def audit_body(
    *,
    path: str,
    body: Any,
    raw_size: int,
    response_hard_limit: int,
    row_hard_limit: int,
    page_size_limit: int,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(body, dict):
        return [
            Issue(
                severity="high",
                category="non_object_response",
                path=path,
                title="Management list response is not a JSON object",
                evidence=f"type={type(body).__name__}",
            )
        ]

    if raw_size > response_hard_limit:
        issues.append(
            Issue(
                severity="high",
                category="response_size",
                path=path,
                title="Management list response exceeds hard payload budget",
                evidence=f"{raw_size} bytes > {response_hard_limit} bytes",
            )
        )

    top_aliases = sorted(TOP_LEVEL_LIST_ALIASES.intersection(body.keys()))
    if top_aliases:
        issues.append(
            Issue(
                severity="high",
                category="top_level_alias",
                path=path,
                title="Management list response duplicates list data at the top level",
                evidence=", ".join(top_aliases),
            )
        )

    data = body.get("data")
    if not isinstance(data, dict):
        issues.append(
            Issue(
                severity="high",
                category="data_shape",
                path=path,
                title="Management list response must wrap rows under data.items",
                evidence=f"data type is {type(data).__name__}",
            )
        )

    page_info = body.get("page_info")
    if not isinstance(page_info, dict):
        issues.append(
            Issue(
                severity="high",
                category="missing_page_info",
                path=path,
                title="Management list response is missing page_info",
                evidence="page_info is absent or not an object",
            )
        )
    else:
        page_size = page_info.get("page_size")
        if isinstance(page_size, int) and page_size > page_size_limit:
            issues.append(
                Issue(
                    severity="medium",
                    category="page_size",
                    path=path,
                    title="Default Management list page size exceeds the table budget",
                    evidence=f"page_size={page_size} > {page_size_limit}",
                )
            )

    items = list_items(body)
    for index, item in enumerate(items[:10]):
        row_size = len(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if row_size > row_hard_limit:
            issues.append(
                Issue(
                    severity="high",
                    category="row_size",
                    path=path,
                    title="Management list row exceeds hard row-size budget",
                    evidence=f"items[{index}] is {row_size} bytes > {row_hard_limit} bytes",
                )
            )
        casing = duplicate_casing_pairs(item)
        if casing:
            issues.append(
                Issue(
                    severity="high",
                    category="duplicate_casing",
                    path=path,
                    title="Management list row contains duplicated snake/camel fields",
                    evidence=", ".join(casing[:12]),
                )
            )

    summary = data.get("summary") if isinstance(data, dict) else None
    casing = duplicate_casing_pairs(summary) if isinstance(summary, dict) else []
    if casing:
        issues.append(
            Issue(
                severity="high",
                category="duplicate_summary_casing",
                path=path,
                title="Management list summary contains duplicated snake/camel fields",
                evidence=", ".join(casing[:12]),
            )
        )
    return issues


def audit(
    *,
    repo_root: Path,
    response_hard_limit: int,
    row_hard_limit: int,
    page_size_limit: int,
) -> tuple[list[dict[str, Any]], list[Issue]]:
    os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
    os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")
    bff_dir = repo_root / "services" / "control-plane" / "bff"
    sys.path.insert(0, str(bff_dir))

    from fastapi.testclient import TestClient  # type: ignore
    import main as bff_main  # type: ignore
    from ports import ReadSurfacePorts, create_in_memory_read_surface_ports  # type: ignore

    class AuditTestStore(ReadSurfacePorts):
        def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_ooda_packets(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def list_governance_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def put_ranking_snapshot(self, snapshot: dict[str, Any]) -> None:
            pass

    rows: list[dict[str, Any]] = []
    issues: list[Issue] = []
    original_store = bff_main.read_store
    original_audit_events = list(getattr(bff_main, "_MGMT_AI_AUDIT_EVENTS", []))
    try:
        with tempfile.TemporaryDirectory() as td:
            bff_main.read_store = AuditTestStore()
            if hasattr(bff_main, "_MGMT_AI_AUDIT_EVENTS"):
                bff_main._MGMT_AI_AUDIT_EVENTS.clear()
                bff_main._MGMT_AI_AUDIT_EVENTS.append(
                    {
                        "event_id": "audit-management-list-contract-ui-alias",
                        "event_type": "management_ai.context.sample",
                        "recorded_at": "2026-07-03T00:00:00Z",
                        "ui": {
                            "currentRoute": "/management/personas",
                            "current_route": "/management/personas",
                        },
                    }
                )
            client = TestClient(bff_main.app)
            headers = {"Authorization": "Bearer op-management-list-audit:operator,reviewer,approver:mfa"}
            for path in sorted(set(iter_management_get_paths(bff_main.app))):
                response = client.get(path, headers=headers)
                row: dict[str, Any] = {
                    "path": path,
                    "status": response.status_code,
                    "size_bytes": len(response.content),
                }
                if response.status_code != 200:
                    row["audited"] = False
                    rows.append(row)
                    continue
                try:
                    body = response.json()
                except ValueError:
                    row["audited"] = False
                    rows.append(row)
                    continue
                if not should_audit_body(path, body):
                    row["audited"] = False
                    rows.append(row)
                    continue
                row["audited"] = True
                row["item_count"] = len(list_items(body)) if isinstance(body, dict) else 0
                rows.append(row)
                issues.extend(
                    audit_body(
                        path=path,
                        body=body,
                        raw_size=len(response.content),
                        response_hard_limit=response_hard_limit,
                        row_hard_limit=row_hard_limit,
                        page_size_limit=page_size_limit,
                    )
                )
    finally:
        bff_main.read_store = original_store
        if hasattr(bff_main, "_MGMT_AI_AUDIT_EVENTS"):
            bff_main._MGMT_AI_AUDIT_EVENTS.clear()
            bff_main._MGMT_AI_AUDIT_EVENTS.extend(original_audit_events)
    return rows, issues


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--format", choices={"summary", "json"}, default="summary")
    parser.add_argument("--fail-on-issues", action="store_true")
    parser.add_argument("--response-hard-limit", type=int, default=DEFAULT_RESPONSE_HARD_LIMIT_BYTES)
    parser.add_argument("--row-hard-limit", type=int, default=DEFAULT_ROW_HARD_LIMIT_BYTES)
    parser.add_argument("--page-size-limit", type=int, default=DEFAULT_PAGE_SIZE_LIMIT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows, issues = audit(
        repo_root=args.repo_root.resolve(),
        response_hard_limit=args.response_hard_limit,
        row_hard_limit=args.row_hard_limit,
        page_size_limit=args.page_size_limit,
    )
    payload = {
        "schema": "pantheon.management-bff-list-response-audit.v1",
        "audited_routes": len([row for row in rows if row.get("audited")]),
        "route_count": len(rows),
        "issue_count": len(issues),
        "issues": [asdict(issue) for issue in issues],
        "routes": rows,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "source=fastapi-testclient "
            f"routes={payload['route_count']} audited={payload['audited_routes']} "
            f"issues={payload['issue_count']}"
        )
        for issue in issues:
            print(f"{issue.severity}\t{issue.category}\t{issue.path}\t{issue.evidence}")
    return 1 if issues and args.fail_on_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
