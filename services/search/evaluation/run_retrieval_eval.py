#!/usr/bin/env python3
"""Score retrieval from the recorded query log; never generate or load query sets.

Reads only retrieval_query_log (rows written by the live search request path)
and retrieval_query_labels (human source_event_id labels) from retrieval-postgres.
Persona-private rows are read only through an explicit, repeatable --persona
scope: each scope runs in its own RLS context (pantheon.current_persona set to
that persona), so the owner's rows are visible and every other persona's are
not. No RLS bypass exists. Without --persona only non-private rows are read.
Run: python -m services.search.evaluation.run_retrieval_eval --persona <id> ...
(needs ranx; install services/search/requirements-eval.txt).
Reports status=unavailable with a reason while fewer than MIN_QUERIES real
labelled queries exist or when the set looks templated. Exit code 2 = unavailable.
Scoring is evidence only, never authority to switch a model.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MIN_QUERIES = 150
MAX_TEMPLATE_SHARE = 0.5  # refuse if >50% of queries collapse into few shapes


def validate_local_dsn(dsn: str) -> None:
    """Retained admission helper for isolated loopback regression probes."""
    parsed = urlsplit(dsn)
    if (parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != 25432 or parsed.path != "/pantheon_search"
        or parsed.query or parsed.fragment):
        raise ValueError("Evaluation requires the isolated loopback test database without DSN options")


def _shape(query: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"[A-Za-z]+", "a", query.strip().lower()))


def refuse_templated(queries: list[str]) -> None:
    shapes = {_shape(q) for q in queries}
    texts = set(queries)
    if len(texts) < len(queries) * MAX_TEMPLATE_SHARE or len(shapes) < len(queries) * 0.2:
        raise ValueError("Recorded queries look templated or synthetic (too few distinct queries/shapes)")


def load_recorded(dsn: str, tenant_id: str,
                  personas: list[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    import psycopg
    from psycopg.rows import dict_row

    rows: dict[str, dict[str, Any]] = {}
    labels: dict[str, set[str]] = defaultdict(set)
    # "" = no persona context (non-private rows only); each named persona is its own RLS scope.
    scopes = [""] + [p for p in dict.fromkeys(personas or []) if p]
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
        for persona in scopes:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE pantheon_retrieval")
                cur.execute("SELECT set_config('pantheon.current_tenant', %s, true)", (tenant_id,))
                cur.execute("SELECT set_config('pantheon.current_persona', %s, true)", (persona,))
                cur.execute("SELECT * FROM retrieval_query_log WHERE origin = 'live_request'")
                for r in cur.fetchall():
                    rows[str(r["query_log_id"])] = r
                cur.execute("SELECT query_log_id, source_event_id FROM retrieval_query_labels")
                for lab in cur.fetchall():
                    labels[str(lab["query_log_id"])].add(lab["source_event_id"])
    return list(rows.values()), labels


def score(rows: list[dict[str, Any]], labels: dict[str, set[str]]) -> dict[str, Any]:
    labelled = [r for r in rows if labels.get(str(r["query_log_id"]))]
    if len(labelled) < MIN_QUERIES:
        raise ValueError(f"Only {len(labelled)} real labelled queries recorded; need at least {MIN_QUERIES}")
    refuse_templated([r["query_text"] for r in labelled])
    from ranx import Qrels, Run, evaluate

    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in labelled:
        by_group[(r["model_name"], r["retrieval_mode"])].append(r)
    metrics = []
    for (model, mode), group in sorted(by_group.items()):
        qrels = Qrels({str(r["query_log_id"]): {s: 1 for s in labels[str(r["query_log_id"])]} for r in group})
        run = Run({
            str(r["query_log_id"]): {sid: float(len(r["returned_source_event_ids"]) - i)
                                      for i, sid in enumerate(r["returned_source_event_ids"])} or {"": 0.0}
            for r in group
        })
        result = evaluate(qrels, run, ["recall@10", "ndcg@10"])
        times = sorted(float(r["latency_ms"]) for r in group)
        metrics.append({
            "model_name": model, "mode": mode, "queries": len(group),
            **{k: float(v) for k, v in result.items()},
            "p95_latency_ms": times[math.ceil(len(times) * 0.95) - 1],
        })
    return {"labelled_queries": len(labelled), "metrics": metrics}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("PANTHEON_SEARCH_POSTGRES_DSN"))
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--persona", action="append", default=[],
                        help="authorized persona scope whose private queries to include (repeatable)")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report: dict[str, Any] = {"task_id": "RETRIEVAL-BACKEND-ACTIVATION-R2-20261113", "accepted": False}
    try:
        if not args.dsn:
            raise ValueError("No retrieval database DSN configured")
        rows, labels = load_recorded(args.dsn, args.tenant, args.persona)
        report.update(score(rows, labels), status="measured_pending_independent_review")
        code = 0
    except Exception as exc:  # unavailable is a recorded reason, never a silent pass
        report.update(status="unavailable", reason=str(exc))
        code = 2
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
