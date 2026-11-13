"""Rebuild the retrieval index from canonical stores.

    python -m services.search.rebuild_index

Applies the idempotent schema, then force-fully re-indexes every knowledge
object from the durable evidence repository (jieba-segmented, embedded with the
checksum-verified model). The index is disposable derived data; the query log
and labels are not touched. Exits non-zero if the model or database is unavailable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .index_pipeline import IncrementalIndexPipeline, JsonlIndexPipelineStore
from .pg_retrieval import PostgresRetrievalBackend
from .pg_store import build_search_evidence_repository


def rebuild(dsn: str | None = None, evidence_path: Path | None = None, pipeline_path: Path | None = None) -> dict:
    backend = PostgresRetrievalBackend(dsn=dsn)
    health = backend.check_health()
    if health.get("status") != "ok":
        raise RuntimeError(f"retrieval backend unavailable: {health.get('reason')}")
    backend.setup_schema()
    repository = build_search_evidence_repository(
        evidence_path or Path(os.getenv("SEARCH_EVIDENCE_STORE_PATH", "/data/source-ingest/source_evidence.jsonl")))
    store = JsonlIndexPipelineStore(
        pipeline_path or Path(os.getenv("SEARCH_PIPELINE_STORE_PATH", "/data/search/search-pipeline.jsonl")))
    snapshot = IncrementalIndexPipeline(repository, store, retrieval_backend=backend).run(
        triggered_by="rebuild_index", force_full=True)
    return snapshot.to_dict()


if __name__ == "__main__":
    try:
        print(rebuild())
    except Exception as exc:
        print(f"rebuild failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
