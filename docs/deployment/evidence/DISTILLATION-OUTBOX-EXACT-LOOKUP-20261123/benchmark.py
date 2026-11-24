#!/usr/bin/env python3
"""Isolated near-hosted benchmark for the distillation outbox exact lookup.

Usage: SOURCE_INGEST_TEST_POSTGRES_DSN=postgresql://... python3 benchmark.py [N_ADMITTED] [N_NEW]

Builds, in a throwaway PG schema and temp SQLite file only (no hosted access):
 * N_ADMITTED (default 37461) tenant-a paper SourceRecords whose versions are
   already receipts in the SQLite queue (written via the real
   DistillationJobQueue bootstrap/enqueue_source_record), then N_NEW new ones;
 * the index is dropped to reproduce the legacy (pre-fix) schema, then rebuilt
   with the real _bootstrap(), measuring build time and exact index bytes (dbstat).
Measures scans with read_source_records_for_tenant(limit=100,
already_admitted=queue.get(...) is not None) exactly as the controller does.
"""
from __future__ import annotations

import json, os, sqlite3, sys, tempfile, time, uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
import psycopg  # noqa: E402

from services.source_ingestion.connectors.base import SourceRecord  # noqa: E402
from services.source_ingestion.distillation_worker import DistillationJobQueue, source_version_digest  # noqa: E402
from services.source_ingestion.pg_store import read_source_records_for_tenant  # noqa: E402

IDX = "distillation_outbox_source_version_idx"
N_ADMITTED = int(sys.argv[1]) if len(sys.argv) > 1 else 37461
N_NEW = int(sys.argv[2]) if len(sys.argv) > 2 else 205
dsn = os.environ["SOURCE_INGEST_TEST_POSTGRES_DSN"]
schema = f"bench_{uuid.uuid4().hex[:12]}"
table = f'"{schema}"."source_evidence"'
os.environ.update(SOURCE_INGEST_EVIDENCE_BACKEND="postgres", SOURCE_INGEST_EVIDENCE_DSN=dsn,
                  SOURCE_INGEST_EVIDENCE_TABLE=f"{schema}.source_evidence")


def rec(i: int) -> dict:
    return SourceRecord(source_id=f"paper-{i}", connector_id="conn", source_type="paper",
                        title=f"Paper {i}", content_ref=f"ref://paper/{i}",
                        metadata={"tenant_id": "tenant-a", "body": "x" * 1500}).to_dict()


def dbstat_bytes(path: str, name: str) -> int | None:
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT SUM(pgsize) FROM dbstat WHERE name=?", (name,)).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        c.close()


def timed(fn):
    t = time.perf_counter(); r = fn(); return r, round(time.perf_counter() - t, 4)


out: dict = {"admitted_receipts": N_ADMITTED, "new_sources": N_NEW, "sqlite": sqlite3.sqlite_version}
with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / "queue.sqlite3")
    q = DistillationJobQueue(path)
    t = time.perf_counter()
    with psycopg.connect(dsn) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f"CREATE TABLE {table} (append_id bigserial PRIMARY KEY, record_type text, payload jsonb)")
        conn.execute(
            f"INSERT INTO {table} (record_type, payload) SELECT 'source_record', %s::jsonb || "
            "jsonb_build_object('source_id','paper-'||n,'title','Paper '||n,'content_ref','ref://paper/'||n) "
            "FROM generate_series(1, %s) n", (json.dumps(rec(0)), N_ADMITTED + N_NEW))
    out["pg_seed_seconds"] = round(time.perf_counter() - t, 3)
    t = time.perf_counter()
    for i in range(1, N_ADMITTED + 1):
        d = rec(i)
        d["source_id"], d["title"], d["content_ref"] = f"paper-{i}", f"Paper {i}", f"ref://paper/{i}"
        q.enqueue_source_record(SourceRecord.from_dict(d))
    out["sqlite_receipt_seed_seconds"] = round(time.perf_counter() - t, 3)
    with sqlite3.connect(path) as c:
        out["outbox_rows"] = c.execute("SELECT COUNT(*) FROM distillation_outbox").fetchone()[0]
        out["source_version_rows"] = c.execute("SELECT COUNT(*) FROM distillation_source_versions").fetchone()[0]
        c.execute(f"DROP INDEX IF EXISTS {IDX}")
        c.execute("VACUUM")
    out["db_bytes_legacy_schema"] = os.path.getsize(path)
    out["wal_or_shm_present"] = [p.name for p in Path(tmp).glob("queue.sqlite3-*")]

    def scan(queue):
        pages, got, t0, per_page = 0, 0, time.perf_counter(), []
        while True:
            s = {}
            tp = time.perf_counter()
            batch = read_source_records_for_tenant(
                jsonl_path=Path(tmp) / "unused", tenant_id="tenant-a", limit=100, summary=s,
                already_admitted=lambda src: queue.get(src.source_id, source_version_digest(src)) is not None)
            per_page.append(round(time.perf_counter() - tp, 3))
            if not batch:
                break
            pages += 1; got += len(batch)
            for src in batch:  # admit the page so the next page advances (receipt writes, cap 100)
                queue.enqueue_source_record(SourceRecord.from_dict(src.to_dict()))
        return {"pages_with_data": pages, "admitted_new": got, "total_seconds": round(time.perf_counter() - t0, 3),
                "max_page_seconds": max(per_page), "eligible_source_count": s.get("eligible_source_count")}

    def one_scan():  # one tick: single page of 100 new sources behind all admitted receipts
        tp = time.perf_counter()
        b = read_source_records_for_tenant(
            jsonl_path=Path(tmp) / "unused", tenant_id="tenant-a", limit=100, summary={},
            already_admitted=lambda src: q.get(src.source_id, source_version_digest(src)) is not None)
        return len(b), round(time.perf_counter() - tp, 3)

    # Legacy (no index): a full scan needs one outbox scan per receipt, so it is
    # extrapolated from 100 measured keys instead of run to completion.
    keys = [(f"paper-{i}", source_version_digest(SourceRecord.from_dict({**rec(i), "source_id": f"paper-{i}", "title": f"Paper {i}", "content_ref": f"ref://paper/{i}"}))) for i in range(1, 101)]
    out["legacy_no_index_100_keys_seconds"] = timed(lambda: [q.get(*k) for k in keys])[1]
    out["legacy_no_index_extrapolated_full_receipt_scan_seconds"] = round(
        out["legacy_no_index_100_keys_seconds"] * N_ADMITTED / 100, 1)

    # Re-bootstrap with the real code = index build on the existing legacy file.
    _, out["index_build_via_bootstrap_seconds"] = timed(lambda: DistillationJobQueue(path))
    c = sqlite3.connect(path); out["plan_after"] = [r[3] for r in c.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM distillation_outbox WHERE source_id=? AND source_digest=?", keys[0])]; c.close()
    out["index_bytes_dbstat"] = dbstat_bytes(path, IDX)
    c = sqlite3.connect(path); c.execute("VACUUM"); c.close()
    out["db_bytes_with_index_vacuumed"] = os.path.getsize(path)
    out["index_bytes_by_file_delta"] = out["db_bytes_with_index_vacuumed"] - out["db_bytes_legacy_schema"]
    out["index_bytes_per_row"] = round((out["index_bytes_by_file_delta"]) / max(1, out["outbox_rows"]), 1)

    q = DistillationJobQueue(path)
    out["indexed_first_scan_page_cold"] = one_scan()
    out["indexed_repeat_scan_page_warm"] = one_scan()
    out["indexed_100_keys_seconds"] = timed(lambda: [q.get(*k) for k in keys])[1]
    out["indexed_multipage_full_drain"] = scan(q)
    out["indexed_multipage_within_300s"] = out["indexed_multipage_full_drain"]["total_seconds"] <= 300
with psycopg.connect(dsn) as conn:
    conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
print(json.dumps(out, indent=2))
