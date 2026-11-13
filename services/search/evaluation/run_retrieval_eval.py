#!/usr/bin/env python3
"""Score recorded source-labelled retrieval runs; never generate queries or data.

Default exits 2 until genuine gold and measurements exist. Run files contain
model_name, mode, host, model_manifest_sha256, and requests with query_id,
latency_ms (full authorized request), and source_scores mapping source_event_id
to retrieval score. Scoring is evidence, never authority to activate a model.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MODES = {"keyword_fallback", "lexical", "dense", "hybrid", "hybrid_reranker"}
GOLD_PATH = Path(__file__).with_name("zh_tw_gold_set.json")


def validate_local_dsn(dsn: str) -> None:
    """Retained admission helper for isolated historical regression probes."""
    parsed = urlsplit(dsn)
    if (parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != 25432 or parsed.path != "/pantheon_search"
        or parsed.query or parsed.fragment):
        raise ValueError("Evaluation requires the isolated loopback test database without DSN options")


def load_gold(path: Path) -> dict[str, Any]:
    gold = json.loads(path.read_text(encoding="utf-8"))
    queries = gold.get("queries", [])
    if not 150 <= len(queries) <= 300:
        raise ValueError("Need 150–300 recorded, independently source-labelled real queries")
    if {q.get("language") for q in queries} != {"zh-TW", "en"}:
        raise ValueError("Gold set must contain both zh-TW and English queries")
    if len({q["query_id"] for q in queries}) != len(queries):
        raise ValueError("Duplicate query ids")
    for query in queries:
        if not all(query.get(key) for key in ("query", "recorded_ref", "recorded_at", "labelled_by", "source_event_ids")):
            raise ValueError("Query lacks recorded provenance or source_event_id labels")
        if not isinstance(query["source_event_ids"], list) or not all(isinstance(s, str) and s for s in query["source_event_ids"]):
            raise ValueError("Invalid source_event_id labels")
    return gold


def score_runs(gold: dict[str, Any], paths: list[Path]) -> list[dict[str, Any]]:
    from ranx import Qrels, Run, evaluate
    labels = {q["query_id"]: {source: 1 for source in q["source_event_ids"]} for q in gold["queries"]}
    qrels = Qrels(labels)
    results = []
    identities = set()
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("mode") not in MODES or not all(record.get(k) for k in ("host", "model_name", "model_manifest_sha256")):
            raise ValueError(f"Incomplete measured run identity: {path}")
        identity = (record["model_name"], record["mode"])
        if identity in identities:
            raise ValueError("Duplicate model/mode measurements")
        identities.add(identity)
        requests = record["requests"]
        if len(requests) != len(labels) or {r["query_id"] for r in requests} != set(labels):
            raise ValueError("Every measured run must cover every gold query exactly once")
        times = sorted(float(r["latency_ms"]) for r in requests)
        if any(not math.isfinite(t) or t < 0 for t in times):
            raise ValueError("Invalid measured request latency")
        run = Run({r["query_id"]: r["source_scores"] for r in requests})
        metrics = evaluate(qrels, run, ["recall@10", "ndcg@10"])
        results.append({
            "model_name": record["model_name"], "mode": record["mode"],
            "host": record["host"], "model_manifest_sha256": record["model_manifest_sha256"],
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            **{key: float(value) for key, value in metrics.items()},
            "p95_latency_ms": times[math.ceil(len(times) * 0.95) - 1],
        })
    if not results or any(
        {r["mode"] for r in results if r["model_name"] == model} != MODES
        for model in {r["model_name"] for r in results}
    ):
        raise ValueError("Each model needs keyword fallback, lexical, dense, hybrid and reranker measurements")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-set", type=Path, default=GOLD_PATH)
    parser.add_argument("--run", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report: dict[str, Any] = {"task_id": "RETRIEVAL-BACKEND-ACTIVATION-20261113", "accepted": False}
    try:
        gold = load_gold(args.gold_set)
        report["metrics"] = score_runs(gold, args.run)
        report["gold_sha256"] = hashlib.sha256(args.gold_set.read_bytes()).hexdigest()
        report["status"] = "measured_pending_independent_review"
        code = 0
    except (ValueError, OSError, KeyError, ImportError) as exc:
        report.update(status="unavailable", reason=str(exc))
        code = 2
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
