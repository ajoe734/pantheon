"""DISCOVERY-OWNER-SUMMARY-READ-20261125: bounded, tenant-scoped discovery summary."""

from __future__ import annotations

import builtins
import json
import sqlite3
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.source_ingestion import discovery_summary as ds
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.distillation_worker import (
    TERMINAL_COUNTS_TABLE,
    DistillationJobQueue,
    _canonical_json,
)
from services.source_ingestion.hypothesis_card import ExtractionLedger
from services.source_ingestion.routers.management import create_management_router

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc).timestamp()
HOUR = 3600.0
SECRET = "discovery-summary-test-secret"
URL = "/api/source-ingest/management/discovery-summary"


def _source(sid: str, stype: str = "news", tenant: str = "tenant-dev", title: str | None = None) -> SourceRecord:
    return SourceRecord(
        source_id=sid, connector_id="conn", source_type=stype, title=title or f"title {sid}",
        content_ref=f"https://example.test/{sid}", status="normalized",
        metadata={"tenant_id": tenant, "body": "台積電月營收創高"},
    )


def _brute_counts(path: Path) -> dict[tuple[str, str, str, str], int]:
    with sqlite3.connect(path) as c:
        rows = c.execute(
            "SELECT COALESCE(tenant_id,''), COALESCE(source_type,''), status,"
            " CASE WHEN status='done' THEN '' ELSE skip_code END, COUNT(*) FROM distillation_outbox"
            " WHERE status IN ('done','skipped') GROUP BY 1,2,3,4"
        ).fetchall()
    return {tuple(r[:4]): int(r[4]) for r in rows}


def _counter(path: Path) -> dict[tuple[str, str, str, str], int]:
    with sqlite3.connect(path) as c:
        rows = c.execute(f"SELECT tenant_id, source_type, status, reason, n FROM {TERMINAL_COUNTS_TABLE} WHERE n <> 0").fetchall()
    return {tuple(r[:4]): int(r[4]) for r in rows}


def _queue_with_jobs(tmp_path: Path) -> DistillationJobQueue:
    queue = DistillationJobQueue(tmp_path / "distill_job_queue.jsonl", now=lambda: NOW)
    plan = [
        ("n1", "news", "tenant-dev", "skip", "l2_irrelevant: no_finance_signal", NOW - 2 * HOUR),
        ("n2", "news", "tenant-dev", "skip", "l2_irrelevant: no_finance_signal", NOW - 30 * HOUR),
        ("p1", "paper", "tenant-dev", "skip", "l2_extraction_disabled", NOW - 1 * HOUR),
        ("s1", "social", "tenant-dev", "skip", "source s1 is rejected", NOW - 1 * HOUR),
        ("n3", "news", "tenant-dev", "done", None, NOW - 1 * HOUR),
        ("f1", "filing", "tenant-dev", "done", None, NOW - 40 * HOUR),
        ("m1", "market", "tenant-dev", "skip", "source_type=market does not produce Registry strategy drafts", NOW - HOUR),
        ("o1", "news", "tenant-other", "skip", "l2_irrelevant: lottery", NOW - HOUR),
        ("o2", "news", "tenant-other", "done", None, NOW - HOUR),
        ("q1", "news", "tenant-dev", "pending", None, None),
    ]
    for sid, stype, tenant, action, reason, at in plan:
        job = queue.enqueue_source_record(_source(sid, stype, tenant), enqueued_at=(at or NOW) - 60)
        if action == "skip":
            queue.mark_skipped(job.job_id, reason=reason, processed_at=at)
        elif action == "done":
            queue.mark_done(job.job_id, seed_id=f"seed-{sid}", processed_at=at)
    return queue


# ---------------------------------------------------------------------------
# One counter system: trigger-kept counts equal the outbox on every transition
# ---------------------------------------------------------------------------


def test_terminal_counter_tracks_every_transition_and_feeds_closure_counts(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    assert _counter(queue.path) == _brute_counts(queue.path)
    closures = queue.external_text_closure_counts()
    assert closures["news"] == {"l2_irrelevant": 3}
    assert closures["paper"] == {"l2_extraction_disabled": 1}
    assert closures["social"] == {"source_rejected": 1}
    assert closures["market"] == {"market_source": 1}

    # skipped -> pending (redispatch) and back with another reason, and a done row re-skipped.
    queue.reset_to_pending("n1")
    assert _counter(queue.path) == _brute_counts(queue.path)
    job = queue.get("n1")
    queue.mark_skipped(job.job_id, reason="hypothesis_rejected: weak", processed_at=NOW)
    assert _counter(queue.path) == _brute_counts(queue.path)
    assert queue.external_text_closure_counts()["news"] == {"l2_irrelevant": 2, "hypothesis_rejected": 1}
    with sqlite3.connect(queue.path) as c:
        c.execute("DELETE FROM distillation_outbox WHERE source_id = 'o2'")
    assert _counter(queue.path) == _brute_counts(queue.path)


def test_migration_backfills_scope_and_seeds_counter_once(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    expected = _brute_counts(queue.path)
    with sqlite3.connect(queue.path) as c:  # a queue written before this migration
        for trig in ("stamp_scope", "terminal_count_insert", "terminal_count_update", "terminal_count_delete"):
            c.execute(f"DROP TRIGGER distillation_outbox_{trig}")
        c.execute(f"DROP TABLE {TERMINAL_COUNTS_TABLE}")
        c.execute("UPDATE distillation_outbox SET tenant_id = NULL, source_type = NULL")
    reopened = DistillationJobQueue(queue.path, now=lambda: NOW)
    assert _brute_counts(reopened.path) == expected
    assert _counter(reopened.path) == expected
    DistillationJobQueue(queue.path, now=lambda: NOW)  # idempotent: no double seeding
    assert _counter(queue.path) == expected


# ---------------------------------------------------------------------------
# Summary content
# ---------------------------------------------------------------------------


def _ledger(tmp_path: Path, cards: dict[str, str], calls: int = 0, cap: int = 400) -> tuple[Path, ExtractionLedger]:
    path = tmp_path / "l2_extraction_ledger.sqlite"
    ledger = ExtractionLedger(path, daily_cap=cap)
    for _ in range(calls):
        assert ledger.reserve_call()
    return path, ledger


def test_summary_reports_windows_totals_cards_and_llm_calls(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    path, ledger = _ledger(tmp_path, {}, calls=3, cap=250)
    digest = queue.get("n3").source_digest
    ledger.put_card("n3", digest, {"claim": "Monthly revenue surprise drifts", "testability": "testable"})
    today = datetime.now(timezone.utc).timestamp()
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW)

    collected = out["collected_text_records"]
    assert collected["status"] == "ok"
    # enqueued within 24 h: n1, p1, s1, n3, q1 (news x3, paper, social); market and other tenant excluded
    assert collected["last_24h"] == {"total": 5, "by_source_family": {"news": 3, "paper": 1, "social": 1}}

    closed = out["closed_text_records"]
    assert closed["last_24h"]["by_reason"] == {"l2_irrelevant": 1, "l2_extraction_disabled": 1, "source_rejected": 1}
    assert closed["total"]["by_reason"] == {"l2_irrelevant": 2, "l2_extraction_disabled": 1, "source_rejected": 1}
    assert closed["total"]["by_source_family"]["news"] == {"l2_irrelevant": 2}
    assert "market_source" not in json.dumps(closed)
    assert closed["unattributed_terminal_rows"] == 0

    cards = out["hypothesis_cards"]
    assert (cards["today"], cards["total"]) == (1, 2)
    assert [c["source_id"] for c in cards["newest"]] == ["n3", "f1"]
    newest = cards["newest"][0]
    assert newest["title"] == "title n3" and newest["source_family"] == "news"
    assert newest["claim"] == "Monthly revenue surprise drifts" and newest["claim_status"] == "ok"
    assert cards["newest"][1]["claim_status"] == "card_not_in_ledger"

    llm = out["llm_calls"]
    if datetime.fromtimestamp(today, tz=timezone.utc).date() == datetime.fromtimestamp(NOW, tz=timezone.utc).date():
        assert llm["calls"] == 3
    assert llm["status"] == "ok" and llm["scope"] == "service"
    assert out["bounds"]["max_response_bytes"] == ds.MAX_RESPONSE_BYTES
    assert out["bounds"]["elapsed_ms"] >= 0


def test_llm_calls_report_distinct_sources_and_calls_per_source(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    _, ledger = _ledger(tmp_path, {})
    for source_id, digest in (("a", "v1"), ("a", "v2"), ("b", "v1")):
        assert ledger.reserve_attempt(source_id, digest) == "reserved"
    llm = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev")["llm_calls"]
    assert (llm["calls"], llm["distinct_sources"], llm["calls_per_source"]) == (3, 2, 1.5)


def test_llm_cap_comes_from_the_writer_ledger(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    _, ledger = _ledger(tmp_path, {}, calls=2, cap=250)
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", daily_cap=999)
    assert out["llm_calls"]["calls"] == 2
    assert (out["llm_calls"]["daily_cap"], out["llm_calls"]["cap_source"]) == (250, "ledger")


def test_other_tenant_sees_only_its_rows(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-other", now=NOW)
    assert out["collected_text_records"]["last_24h"]["total"] == 2
    assert out["closed_text_records"]["total"]["by_reason"] == {"l2_irrelevant": 1}
    assert [c["source_id"] for c in out["hypothesis_cards"]["newest"]] == ["o2"]


def test_missing_queue_or_ledger_is_unavailable_never_zero(tmp_path):
    out = ds.read_discovery_summary(queue_path=tmp_path / "absent.sqlite3", tenant_id="tenant-dev", now=NOW)
    for key in ("collected_text_records", "closed_text_records", "hypothesis_cards"):
        assert out[key]["status"] == "unavailable"
        assert out[key]["reason"] == "distillation_queue_missing"
        assert "total" not in out[key] and "last_24h" not in out[key]
    assert out["llm_calls"] == {
        "status": "unavailable", "source": ds.LEDGER_SOURCE, "reason": "extraction_ledger_missing",
    }
    queue = _queue_with_jobs(tmp_path)
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW)
    assert out["llm_calls"]["reason"] == "extraction_ledger_missing"
    assert all(c["claim"] is None and c["claim_status"] == "ledger_unavailable" for c in out["hypothesis_cards"]["newest"])
    assert out["hypothesis_cards"]["status"] == "ok"


def test_unmigrated_queue_and_missing_tenant_are_unavailable(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE distillation_outbox (job_id TEXT)")
    out = ds.read_discovery_summary(queue_path=path, tenant_id="tenant-dev", now=NOW)
    assert out["collected_text_records"]["reason"] == "distillation_queue_not_migrated"
    out = ds.read_discovery_summary(queue_path=path, tenant_id="  ", now=NOW)
    assert out["hypothesis_cards"]["reason"] == "missing_tenant_scope"


def test_read_is_read_only_and_time_bounded(tmp_path):
    queue = _queue_with_jobs(tmp_path)
    before = queue.path.read_bytes()
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW)
    assert out["collected_text_records"]["status"] == "ok"
    assert queue.path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        with ds._open_read_only(queue.path, time.monotonic() + 5) as c:
            c.execute("DELETE FROM distillation_outbox")
    expired = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW, time_budget_seconds=0)
    # A zero budget is floored at 50 ms; this small queue still answers inside it.
    assert expired["bounds"]["read_time_budget_ms"] == 0


def test_time_budget_interrupt_is_unavailable(tmp_path, monkeypatch):
    queue = _queue_with_jobs(tmp_path)
    real = ds._open_read_only
    monkeypatch.setattr(ds, "_open_read_only", lambda path, deadline: real(path, time.monotonic() - 1))
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW)
    assert out["collected_text_records"] == {
        "status": "unavailable", "source": ds.SUMMARY_SOURCE, "reason": "read_time_budget_exceeded",
    }


def test_response_size_stays_inside_the_stated_bound(tmp_path):
    queue = DistillationJobQueue(tmp_path / "q.sqlite3", now=lambda: NOW)
    path, ledger = _ledger(tmp_path, {})
    for i in range(80):
        sid = f"{'界' * 150}-{i}"
        job = queue.enqueue_source_record(_source(sid, "news", title="標" * 900), enqueued_at=NOW - 60)
        queue.mark_done(job.job_id, seed_id="s" * 300, processed_at=NOW - i)
        ledger.put_card(sid, job.source_digest, {"claim": "主張" * 900})
    for i in range(300):
        job = queue.enqueue_source_record(_source(f"r{i}", ["news", "paper", "social"][i % 3]), enqueued_at=NOW - 60)
        queue.mark_skipped(job.job_id, reason=f"code_{i:03d}_{'x' * 50}: detail", processed_at=NOW - 10)
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW)
    assert len(out["hypothesis_cards"]["newest"]) == ds.NEWEST_CARD_LIMIT
    assert len(out["closed_text_records"]["total"]["by_reason"]) == ds.MAX_REASONS + 1
    size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    assert size <= ds.MAX_RESPONSE_BYTES, size


# ---------------------------------------------------------------------------
# Scale: 40,000 queue rows and 13,500 seeds; the read never touches seeds or evidence
# ---------------------------------------------------------------------------

QUEUE_ROWS = 40_000
SEED_ROWS = 13_500
FAMILIES = ("news", "paper", "social", "filing", "macro", "market")
REASONS = ("l2_irrelevant: no_finance_signal", "l2_extraction_disabled", "no_hypothesis: model_abstained",
           "hypothesis_needs_data: tw_flow", "l2_admission_denied: secret")


def _bulk_queue(path: Path) -> DistillationJobQueue:
    queue = DistillationJobQueue(path, now=lambda: NOW)
    versions, outbox = [], []
    for i in range(QUEUE_ROWS):
        sid, digest = f"src-{i:06d}", f"d{i:06d}"
        tenant = "tenant-dev" if i % 5 else "tenant-other"
        family = FAMILIES[i % len(FAMILIES)]
        enqueued = NOW - (i % (30 * 24)) * HOUR - 30
        payload = _canonical_json({"source_id": sid, "source_type": family, "title": f"t{i}",
                                   "metadata": {"tenant_id": tenant, "body": "x" * 200}})
        versions.append((sid, digest, "v1", payload, enqueued))
        status = ("done", "skipped", "skipped", "pending")[i % 4]
        processed = enqueued + 10 if status != "pending" else None
        outbox.append((f"job-{i}", f"ev-{i}", sid, digest, "v1", f"idem-{i}", status, enqueued, processed,
                       enqueued, 3, enqueued, REASONS[i % len(REASONS)] if status == "skipped" else None))
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO distillation_source_versions VALUES (?,?,?,?,?)", versions)
        c.executemany(
            "INSERT INTO distillation_outbox (job_id, event_id, source_id, source_digest, event_version,"
            " idempotency_key, status, enqueued_at, processed_at, available_at, max_attempts, updated_at, skip_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            outbox,
        )
    return queue


@pytest.fixture
def scale_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    queue = _bulk_queue(tmp_path / "distill_job_queue.jsonl")
    ExtractionLedger(tmp_path / "l2_extraction_ledger.sqlite", daily_cap=400)
    seeds = tmp_path / "distill_seeds.jsonl"
    with seeds.open("w") as fh:
        for i in range(SEED_ROWS):
            fh.write(json.dumps({"seed_id": f"seed-{i}", "source_id": f"src-{i:06d}", "metadata": {"tenant_id": "tenant-dev"}}) + "\n")
    evidence = tmp_path / "source_evidence.jsonl"
    evidence.write_text("{}\n")
    forbidden = {str(seeds), str(evidence)}
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if str(file) in forbidden:
            raise AssertionError(f"discovery summary opened {file}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    return queue, forbidden


class _Forbidden:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"discovery summary touched {self._name}.{attr}")


def _plans(path: Path, statements: list[str]) -> list[str]:
    lines = []
    with sqlite3.connect(path) as c:
        for sql in statements:
            if not sql.lstrip().upper().startswith("SELECT"):
                continue
            lines.extend(str(r[-1]) for r in c.execute("EXPLAIN QUERY PLAN " + sql.replace("?", "NULL")))
    return lines


def test_scale_read_uses_indexes_and_counters_only(scale_env, monkeypatch):
    queue, _ = scale_env
    traced: list[str] = []
    ledger_traced: list[str] = []
    real = ds._open_read_only

    def tracing(path, deadline):
        connection = real(path, deadline)
        if Path(path) == queue.path:
            connection.set_trace_callback(traced.append)
        else:
            connection.set_trace_callback(ledger_traced.append)
        return connection

    monkeypatch.setattr(ds, "_open_read_only", tracing)
    tracemalloc.start()
    started = time.perf_counter()
    out = ds.read_discovery_summary(queue_path=queue.path, tenant_id="tenant-dev", now=NOW)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"discovery_summary rows={QUEUE_ROWS} seeds={SEED_ROWS} elapsed_s={elapsed:.4f} peak_bytes={peak}")

    assert out["collected_text_records"]["status"] == "ok"
    assert out["hypothesis_cards"]["total"] > 0 and len(out["hypothesis_cards"]["newest"]) == ds.NEWEST_CARD_LIMIT
    touched = {t for sql in traced + ledger_traced for t in ("source_evidence", "strategy_seed", "seeds") if t in sql}
    assert traced and touched == set(), touched
    full_scans = [line for line in _plans(queue.path, traced)
                  if line.startswith("SCAN") and ("distillation_outbox" in line or "distillation_source_versions" in line)]
    assert full_scans == [], full_scans
    assert elapsed < 2.0, elapsed
    assert peak < 16 * 1024 * 1024, peak

    # Totals equal a brute-force aggregate over the same rows.
    with sqlite3.connect(queue.path) as c:
        brute_cards = c.execute(
            "SELECT COUNT(*) FROM distillation_outbox WHERE tenant_id='tenant-dev' AND status='done'"
            " AND source_type IN ('filing','macro','news','paper','social')"
        ).fetchone()[0]
        brute_closed = c.execute(
            "SELECT COUNT(*) FROM distillation_outbox WHERE tenant_id='tenant-dev' AND status='skipped'"
            " AND source_type IN ('filing','macro','news','paper','social')"
        ).fetchone()[0]
    assert out["hypothesis_cards"]["total"] == brute_cards
    assert out["closed_text_records"]["total"]["total"] == brute_closed


def test_scale_route_never_reads_seed_store_or_evidence(scale_env, monkeypatch):
    queue, _ = scale_env
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "permissive")

    class _Runtime:
        DISTILLATION_JOB_QUEUE_PATH = queue.path
        strategy_seed_store = _Forbidden("strategy_seed_store")
        evidence_repository = _Forbidden("evidence_repository")

    app = FastAPI()
    app.include_router(create_management_router(_Runtime()))
    client = TestClient(app)
    token = encode_jwt_hs256({"sub": "op", "roles": ["operator"], "tenant_id": "tenant-dev",
                              "exp": int(time.time()) + 600}, secret=SECRET)
    tracemalloc.start()
    started = time.perf_counter()
    resp = client.get(URL, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"})
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"discovery_summary_route elapsed_s={elapsed:.4f} peak_bytes={peak} bytes={len(resp.content)}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == "tenant-dev"
    assert body["hypothesis_cards"]["status"] == "ok"
    assert len(resp.content) <= ds.MAX_RESPONSE_BYTES
    assert elapsed < 3.0 and peak < 32 * 1024 * 1024


def test_route_requires_auth(scale_env, monkeypatch):
    queue, _ = scale_env
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")

    class _Runtime:
        DISTILLATION_JOB_QUEUE_PATH = queue.path

    app = FastAPI()
    app.include_router(create_management_router(_Runtime()))
    resp = TestClient(app).get(URL)
    assert resp.status_code in (401, 403)
