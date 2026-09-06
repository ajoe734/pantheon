#!/usr/bin/env python3
"""Fail-closed retrieval admission report and bounded, read-only local probe.

The previous generator mixed 9,790 random vectors with 210 embedded fixtures
and reported cached searches as full inference latency. Its results cannot
admit a backend. This runner never changes an index or claims task acceptance.
The optional local probe is validation diagnostics, not a replacement holdout.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


TASK_ID = "SIMPLIFY-RETRIEVAL-MEMORY-001"
SEARCH_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).with_name("retrieval_manifest.schema.json")
UNRESOLVED = {
    "owner_authorization": "Authenticated server scope and durable owner ACL/version/expiry contract are missing; request body and invented defaults are not authority.",
    "read_role": "The prior evaluation used postgres superuser/BYPASSRLS; no nonowner, nonbypass runtime query role was proved.",
    "holdout": "No source-family split, prior-runtime baseline, independent critical-case adjudication or immutable all-real-embedding holdout was supplied.",
    "comparison": "Qdrant dense-only search is not the required dense/sparse/native-RRF comparison; total operations cost and latest-compatible identities remain unverified.",
    "consumer_lifecycle": "Owner hydration/version/retention, write receipts, restart/rebuild and failure recovery were not proved through the actual consumers.",
    "performance": "Prior timing reused query vectors and omitted authorized owner hydration; full 1000-replay concurrency-4 acceptance remains unmeasured.",
    "privacy": "Zero external inference was asserted without complete transport observation; result/count/citation/snippet nonleakage remains unproved.",
}


def validate_local_dsn(dsn: str) -> None:
    """Only the task's loopback test database is eligible for this probe."""
    parsed = urlsplit(dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != 25432
        or parsed.path != "/pantheon_search"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Probe requires the task loopback database on port 25432 without DSN options")


def admission_report() -> dict:
    return {
        "schema_version": "retrieval_admission.v2",
        "task_id": TASK_ID,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "accepted": False,
        "disposition": "candidate_withdrawn_pending_valid_evaluation",
        "chosen_backend": None,
        "unresolved": dict(UNRESOLVED),
        "legacy_manifest": {
            "path": "services/search/evaluation/retrieval_manifest.json",
            "accepted": False,
            "reason": "Historical v1 report invalidated; its numerical results are not acceptance evidence.",
        },
        "local_probe": None,
    }


def probe_local(dsn: str, sample_count: int = 12) -> dict:
    validate_local_dsn(dsn)
    if not 4 <= sample_count <= 24:
        raise ValueError("Probe sample count must be between 4 and 24")

    # Imports occur only after target admission. The default command cannot
    # load a model, contact a database, truncate a table or download artifacts.
    import psycopg
    from services.search.filters import SearchAccessContext
    from services.search.local_embeddings import LocalEmbeddingEngine
    from services.search.pg_retrieval import PostgresRetrievalBackend

    bounded_dsn = psycopg.conninfo.make_conninfo(
        dsn, connect_timeout=5,
        options="-c statement_timeout=5000 -c default_transaction_read_only=on",
    )
    with psycopg.connect(bounded_dsn) as conn:
        role, superuser, bypass, owner = conn.execute(
            "SELECT current_user, r.rolsuper, r.rolbypassrls, "
            "c.relowner = r.oid FROM pg_roles r, pg_class c "
            "WHERE r.rolname = current_user AND c.oid = 'search_retrieval_index'::regclass"
        ).fetchone()
        corpus_count = conn.execute("SELECT count(*) FROM search_retrieval_index").fetchone()[0]

    engine = LocalEmbeddingEngine(local_files_only=True)
    start = time.perf_counter()
    engine._ensure_loaded()
    cold_model_seconds = time.perf_counter() - start
    engine._query_cache.clear()
    backend = PostgresRetrievalBackend(dsn=bounded_dsn, embedding_engine=engine)
    context = SearchAccessContext(environment="paper", access_scopes=["public"], license_scopes=["open"])
    queries = (
        "繁體中文來源授權與事件記憶檢索驗證",
        "Governed incident memory and source availability validation",
        "跨語查詢 market liquidity and memory retention",
    )

    def measure(index: int) -> float:
        # Unique validation-only inputs prevent any reused query-vector hit.
        # Include embedding explicitly; backend then consumes that same vector.
        query = f"{queries[index % len(queries)]} validation sample {index}"
        begin = time.perf_counter()
        engine.embed_query(query)
        backend.search(query=query, context=context, top_k=10, mode="hybrid")
        return (time.perf_counter() - begin) * 1000

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        latencies = list(executor.map(measure, range(sample_count)))
    elapsed = time.perf_counter() - started
    latencies.sort()
    return {
        "kind": "validation_diagnostics_only",
        "accepted": False,
        "role": role,
        "role_superuser": superuser,
        "role_bypassrls": bypass,
        "role_table_owner": owner,
        "corpus_count_observed": corpus_count,
        "corpus_provenance_verified": False,
        "model_manifest_sha256": hashlib.sha256(engine.manifest_path.read_bytes()).hexdigest(),
        "cold_model_load_seconds": round(cold_model_seconds, 3),
        "concurrency": 4,
        "requests_completed": len(latencies),
        "latency_ms": [round(value, 3) for value in latencies],
        "embedding_and_search_p95_ms": round(latencies[math.ceil(len(latencies) * .95) - 1], 3),
        "elapsed_seconds": round(elapsed, 3),
        "includes_authorized_hydration": False,
        "external_inference_calls_observed": None,
        "limitation": "Unadjudicated diagnostic inputs against the historical mixed-vector index; neither relevance nor full-runtime performance acceptance.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-local", action="store_true")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = admission_report()
    if args.probe_local:
        dsn = os.getenv("PANTHEON_SEARCH_POSTGRES_DSN", "postgresql://postgres:postgres@127.0.0.1:25432/pantheon_search")
        try:
            report["local_probe"] = probe_local(dsn, args.samples)
        except Exception as exc:
            # Exception messages may include credentials or query data.
            report["local_probe"] = {"accepted": False, "error_type": type(exc).__name__}
    import jsonschema
    jsonschema.validate(report, json.loads(SCHEMA_PATH.read_text()))
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 2  # Missing/failed acceptance is never exit-zero evidence.


if __name__ == "__main__":
    raise SystemExit(main())
