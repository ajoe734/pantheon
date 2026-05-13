#!/usr/bin/env python3
"""
BFF backend route manifest extractor.

Walks the live FastAPI app route table in services/control-plane/bff/main.py
and writes a JSON manifest of every registered route (method, path, family,
status).  Use this to diff what the backend actually serves against the
frontend-facing contract manifest (execute_plans_bff_routes.json).

Usage:
    python scripts/bff_route_manifest_backend.py            # write manifest JSON
    python scripts/bff_route_manifest_backend.py --dump     # print JSON to stdout
    python scripts/bff_route_manifest_backend.py --check    # exit 1 if routes changed vs snapshot
    python scripts/bff_route_manifest_backend.py --out PATH # write to custom path
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
SNAPSHOT_PATH = BFF_DIR / "contract_snapshots" / "backend_routes_manifest.json"

IGNORED_METHODS = {"HEAD", "OPTIONS"}
ROUTE_PARAM_RE = re.compile(r"\{[^/{}]+\}")

# Ordered prefix-to-family rules.  First match wins.
# More-specific prefixes must appear before broader ones.
# Status overrides for routes that are compat aliases, superseded, or deferred.
# Keys are "METHOD /exact-path" matching the FastAPI route path string.
# Canonical target (covered_by) uses the same path format for clarity.
ROUTE_STATUS_OVERRIDES: dict[str, dict] = {
    # /bff/mcp-servers* and /bff/mcp-tools* are execute-plans compat aliases
    # for the canonical /bff/mcp/* routes in the tools-mcp-skills family.
    "GET /bff/mcp-servers": {"status": "alias", "covered_by": "GET /bff/mcp/servers"},
    "GET /bff/mcp-servers/{id}": {"status": "alias", "covered_by": "GET /bff/mcp/servers/{server_id}"},
    "GET /bff/mcp-tools": {"status": "alias", "covered_by": "GET /bff/mcp/servers/{server_id}/tools"},
    "GET /bff/mcp-tools/{id}": {"status": "alias", "covered_by": "GET /bff/mcp/servers/{server_id}/tools"},
    "POST /bff/mcp-servers/{server_id}/import-tools": {
        "status": "alias",
        "covered_by": "POST /bff/v1/mcp/servers/{server_id}/import-tools",
    },
    # /bff/ranking-formulas* are compat aliases for canonical /bff/ranking/formulas*
    "GET /bff/ranking-formulas": {"status": "alias", "covered_by": "GET /bff/ranking/formulas"},
    "GET /bff/ranking-formulas/{id}": {"status": "alias", "covered_by": "GET /bff/ranking/formulas/{formula_id}"},
    "PATCH /bff/ranking-formulas/{id}": {
        "status": "alias",
        "covered_by": "PATCH /bff/ranking/formulas/{formula_id}",
    },
    "POST /bff/ranking-formulas": {"status": "alias", "covered_by": "POST /bff/ranking/formulas"},
}

FAMILY_RULES: list[tuple[str, str]] = [
    # ── health / infra ──────────────────────────────────────────────────────
    ("/health", "health"),
    ("/healthz", "health"),
    ("/livez", "health"),
    ("/readyz", "health"),
    ("/metrics", "health"),
    ("/docs", "health"),
    ("/redoc", "health"),
    ("/openapi.json", "health"),
    ("/bff/healthz", "health"),
    ("/bff/readyz", "health"),
    ("/bff/capabilities", "health"),
    ("/bff/feature-flags", "health"),
    # ── final-contract (execute-plans BFF envelope surface) ──────────────────
    ("/bff/actions", "final-contract"),
    ("/bff/v1/commands", "final-contract"),
    # ── session / auth ───────────────────────────────────────────────────────
    ("/bff/me", "session-auth-me"),
    ("/bff/auth", "session-auth-me"),
    ("/bff/logout", "session-auth-me"),
    ("/bff/switch-tenant", "session-auth-me"),
    # ── MCP (execute-plans compat aliases) ──────────────────────────────────
    ("/bff/v1/mcp", "mcp-final"),
    ("/bff/mcp-servers", "mcp-final"),
    ("/bff/mcp-tools", "mcp-final"),
    # ── v5 interventions ─────────────────────────────────────────────────────
    ("/bff/v5/interventions", "v5-interventions"),
    # ── execute-plans cutover / v5 smoke ─────────────────────────────────────
    ("/bff/v5/sentinel", "execute-plans-cutover-smoke"),
    ("/bff/v5/loop-runs", "execute-plans-cutover-smoke"),
    ("/bff/v5/execution", "execute-plans-cutover-smoke"),
    ("/bff/v5/control-room", "execute-plans-cutover-smoke"),
    ("/bff/v5", "execute-plans-cutover-smoke"),
    # ── strategy / persona ───────────────────────────────────────────────────
    ("/bff/strategies", "strategy-persona"),
    ("/bff/personas", "strategy-persona"),
    ("/bff/search", "strategy-persona"),
    ("/bff/types", "strategy-persona"),
    # ── capital / ranking / rebalance ────────────────────────────────────────
    ("/bff/capital-pools", "capital-ranking-rebalance"),
    ("/bff/ranking-formulas", "capital-ranking-rebalance"),
    ("/bff/ranking", "capital-ranking-rebalance"),
    ("/bff/rebalances", "capital-ranking-rebalance"),
    ("/bff/rankings", "capital-ranking-rebalance"),
    # ── SSE compatibility (must precede /bff/events) ─────────────────────────
    ("/bff/events/stream", "sse-compatibility"),
    ("/bff/sse", "sse-compatibility"),
    ("/bff/realtime", "sse-compatibility"),
    # ── evolution / experiments / jobs / events ──────────────────────────────
    ("/bff/evolution-programs", "evolution-experiment-jobs-events"),
    ("/bff/experiments", "evolution-experiment-jobs-events"),
    ("/bff/research-experiments", "evolution-experiment-jobs-events"),
    ("/bff/jobs", "evolution-experiment-jobs-events"),
    ("/bff/events", "evolution-experiment-jobs-events"),
    ("/bff/artifacts", "evolution-experiment-jobs-events"),
    # ── governance / runtime / risk / audit ──────────────────────────────────
    ("/bff/reviews", "governance-runtime-risk-audit"),
    ("/bff/approvals", "governance-runtime-risk-audit"),
    ("/bff/deployments", "governance-runtime-risk-audit"),
    ("/bff/runtimes", "governance-runtime-risk-audit"),
    ("/bff/risk", "governance-runtime-risk-audit"),
    ("/bff/incidents", "governance-runtime-risk-audit"),
    ("/bff/alerts", "governance-runtime-risk-audit"),
    ("/bff/audit", "governance-runtime-risk-audit"),
    ("/bff/command-confirmations", "governance-runtime-risk-audit"),
    ("/bff/confirm-tokens", "governance-runtime-risk-audit"),
    # ── agora extended (must precede generic /bff/agora) ─────────────────────
    ("/bff/agora/committee", "agora-extended"),
    ("/bff/agora/persona-lab", "agora-extended"),
    ("/bff/agora/handoffs", "agora-extended"),
    ("/bff/agora/channels", "agora-extended"),
    # ── agora core ───────────────────────────────────────────────────────────
    ("/bff/agora", "agora-core"),
    ("/bff/research/tasks", "agora-core"),
    ("/bff/memory", "agora-core"),
    ("/bff/insights", "agora-core"),
    # ── tools / MCP / skills ─────────────────────────────────────────────────
    ("/bff/tools", "tools-mcp-skills"),
    ("/bff/mcp", "tools-mcp-skills"),
    ("/bff/skills", "tools-mcp-skills"),
    # ── channels ─────────────────────────────────────────────────────────────
    ("/bff/channels", "agora-extended"),
    # ── SSE substrate (api/v1 streams) ───────────────────────────────────────
    ("/api/v1/stream", "sse-substrate"),
    ("/api/v1/approvals/stream", "sse-substrate"),
    ("/api/v1/agora/ask/stream", "sse-substrate"),
    ("/api/v1/runtime", "sse-substrate"),
    ("/api/v1/incidents/stream", "sse-substrate"),
    ("/api/v1/kill-switch/updates", "sse-substrate"),
    ("/api/v1/internal/sse", "sse-substrate"),
    # ── operator surfaces ────────────────────────────────────────────────────
    ("/api/v1/operator", "operator"),
    # ── api/v1 read surfaces ─────────────────────────────────────────────────
    ("/api/v1", "api-v1"),
]


def _path_matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "{")


def infer_family(path: str) -> str:
    for prefix, family in FAMILY_RULES:
        if _path_matches_prefix(path, prefix):
            return family
    return "unknown"


def normalize_path(path: str) -> str:
    cleaned = str(path or "").strip().split("?", 1)[0].rstrip("/")
    if not cleaned:
        cleaned = "/"
    return ROUTE_PARAM_RE.sub("{param}", cleaned)


def route_key(method: str, path: str) -> str:
    return f"{method.upper()} {normalize_path(path)}"


def app_route_index(app: object) -> list[dict]:
    """Walk the FastAPI route table and return structured route entries."""
    entries: list[dict] = []
    seen: set[str] = set()
    for route in getattr(app, "routes", []):
        path = str(getattr(route, "path", "") or "")
        methods = getattr(route, "methods", set()) or set()
        for method in sorted(methods):
            method = str(method).upper()
            if method in IGNORED_METHODS:
                continue
            key = route_key(method, path)
            if key in seen:
                continue
            seen.add(key)
            override = ROUTE_STATUS_OVERRIDES.get(f"{method} {path}", {})
            entry: dict = {
                "method": method,
                "path": path,
                "family": infer_family(path),
                "status": override.get("status", "implemented"),
            }
            if "covered_by" in override:
                entry["covered_by"] = override["covered_by"]
            entries.append(entry)
    entries.sort(key=lambda e: (e["family"], e["method"], e["path"]))
    return entries


def _load_bff_app() -> object:
    """Import and return the BFF FastAPI app object."""
    os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
    if str(BFF_DIR) not in sys.path:
        sys.path.insert(0, str(BFF_DIR))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from main import app  # type: ignore[import]  # noqa: PLC0415

    return app


def build_manifest(app: object) -> dict:
    entries = app_route_index(app)
    return {
        "metadata": {
            "snapshot_date": str(date.today()),
            "source": "services/control-plane/bff/main.py",
            "generator": "scripts/bff_route_manifest_backend.py",
            "total_routes": len(entries),
            "status_values": ["implemented", "alias", "superseded", "deferred"],
            "notes": [
                "All routes in this manifest are directly registered in the FastAPI app.",
                "HEAD and OPTIONS routes are excluded.",
                "Duplicate method+path pairs (e.g. stacked decorators on one handler) are deduped.",
                "Diff this against execute_plans_bff_routes.json to find backend/frontend gaps.",
            ],
        },
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a JSON manifest of all routes registered in the BFF FastAPI app."
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print JSON to stdout; do not write the snapshot file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the live routes differ from the current snapshot.",
    )
    parser.add_argument(
        "--out",
        default=str(SNAPSHOT_PATH),
        help="Output path (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    app = _load_bff_app()
    manifest = build_manifest(app)
    text = json.dumps(manifest, indent=2, ensure_ascii=False)

    if args.dump:
        print(text)
        return 0

    out_path = Path(args.out)

    if args.check:
        if not out_path.exists():
            print(f"[check] snapshot not found: {out_path}", file=sys.stderr)
            return 1
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        existing_keys = {
            route_key(e["method"], e["path"]) for e in existing.get("entries", [])
        }
        new_keys = {
            route_key(e["method"], e["path"]) for e in manifest.get("entries", [])
        }
        added = sorted(new_keys - existing_keys)
        removed = sorted(existing_keys - new_keys)
        if added or removed:
            if added:
                print("[check] new routes:", file=sys.stderr)
                for k in added:
                    print(f"  + {k}", file=sys.stderr)
            if removed:
                print("[check] removed routes:", file=sys.stderr)
                for k in removed:
                    print(f"  - {k}", file=sys.stderr)
            return 1
        print("[check] routes match snapshot.", file=sys.stderr)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {len(manifest['entries'])} routes to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
