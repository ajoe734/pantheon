"""Tests for GET /api/source-ingest/registry read cost and consumer field parity.

Verifies without wall-clock threshold assertions (per CI non-flakiness policy):
1. Complete response field parity for every consumer of GET /api/source-ingest/registry:
   - BFF research_knowledge_source.get_source_connector_registry & get_source_ops_snapshot
   - BFF console_gap.datasources.bff_management_data_sources (legacy fallback & catalog)
   - BFF personas.service._read_source_ingest_truth_locked
   - BFF research.service.get_source_connectors
   - source_ingestion.controller_worker.read_actual_state_with_fetch_bindings
   - smoke_source_search_bounded & run_dev_paired_readonly_qa
2. Elimination of full-table PostgresSourceEvidenceRepository.list_source_records()
   scans on GET /api/source-ingest/registry (0 full-table payload scans on steady
   reads; incremental append_id > last_append_id delta fetch when new rows arrive).
3. Elimination of repeated schedule journal receipt sorting and run timestamp
   parsing when ingest_schedule.jsonl is unchanged across registry reads.
4. Immediate freshness update when a new tw-twse-tpex-official-market SourceRecord
   is ingested.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.source_ingestion.connectors import (
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
from services.source_ingestion.routers.catalog_controller import create_catalog_controller_router
from services.source_ingestion.runtime import SourceIngestionRuntime, _ordered_run_receipts
from services.source_ingestion.scheduler import (
    IngestReceipt,
    IngestRun,
    IngestRunStatus,
    SourceWatermark,
)
from services.source_ingestion.tests.test_source_management_import import (
    _populate_hosted_39_configured_connectors,
)


REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "connectors",
    "provider_examples",
    "policy_registry",
    "financial_data_source_catalog",
    "active_universe_policy",
}

REQUIRED_CONNECTOR_ENTRY_KEYS = {
    "schema_version",
    "connector_id",
    "provider",
    "source_type",
    "status",
    "supported_modes",
    "policy",
    "metadata",
    "fetch_policy",
    "schedule",
    "freshness",
    "health_metrics",
    "state",
    "crawler_policy",
}

REQUIRED_FRESHNESS_KEYS = {
    "schema_version",
    "status",
    "stale",
    "is_due",
    "schedule_enabled",
    "last_success_at",
    "source_timestamp",
    "source_timestamp_status",
    "age_seconds",
    "stale_threshold_seconds",
    "last_failure_at",
    "last_typed_failure",
    "last_watermark",
    "last_ingest_run_id",
    "latest_run",
    "latest_receipt",
    "staleness_seconds",
    "next_due_at",
    "next_run_at",
    "seconds_until_due",
}


class _FakePostgresCursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakePostgresEvidenceHarness:
    """In-memory SQL harness emulating PostgresSourceEvidenceRepository append_id table."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str, str]] = []  # (append_id, record_id, record_type, payload_json)
        self.full_table_payload_scans = 0
        self.delta_payload_queries = 0
        self.meta_count_queries = 0

    def append_record(self, record: SourceRecord) -> int:
        append_id = len(self.rows) + 1
        payload_json = json.dumps(record.to_dict(), sort_keys=True)
        self.rows.append((append_id, record.source_id, "source_record", payload_json))
        return append_id

    @contextlib.contextmanager
    def connection(self) -> Iterator["_FakePostgresEvidenceHarness"]:
        yield self

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakePostgresCursor:
        normalized = " ".join(sql.split())
        if "SELECT COALESCE(MAX(append_id), 0), COUNT(*)" in normalized:
            self.meta_count_queries += 1
            src_rows = [r for r in self.rows if r[2] == "source_record"]
            max_id = max((r[0] for r in src_rows), default=0)
            return _FakePostgresCursor([(max_id, len(src_rows))])
        if "WHERE record_type = %s AND append_id > %s" in normalized:
            self.delta_payload_queries += 1
            r_type, after_id = params[0], int(params[1])
            matched = [(r[0], r[3]) for r in self.rows if r[2] == r_type and r[0] > after_id]
            return _FakePostgresCursor(matched)
        if "SELECT append_id, payload FROM" in normalized and "WHERE record_type = %s" in normalized:
            self.full_table_payload_scans += 1
            r_type = params[0]
            matched = [(r[0], r[3]) for r in self.rows if r[2] == r_type]
            return _FakePostgresCursor(matched)
        if "SELECT payload FROM" in normalized and "WHERE record_type = %s" in normalized:
            self.full_table_payload_scans += 1
            r_type = params[0]
            matched = [(r[3],) for r in self.rows if r[2] == r_type]
            return _FakePostgresCursor(matched)
        if "INSERT INTO" in normalized:
            record_id, record_type, payload_json = str(params[0]), str(params[1]), str(params[2])
            append_id = len(self.rows) + 1
            self.rows = [
                r for r in self.rows if not (r[2] == record_type and r[1] == record_id)
            ]
            self.rows.append((append_id, record_id, record_type, payload_json))
            return _FakePostgresCursor([])
        return _FakePostgresCursor([])


def _make_source_record(
    *,
    source_id: str,
    connector_id: str,
    run_id: str,
    trade_date: str = "2026-11-20",
    symbol: str = "2330.TW",
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id=connector_id,
        source_type=SourceType.MARKET,
        title=f"{symbol} daily close {trade_date}",
        content_ref=f"evidence://market/{connector_id}/{source_id}",
        created_at=datetime(2026, 11, 20, 6, 5, 0, tzinfo=timezone.utc),
        status=SourceRecordStatus.NORMALIZED,
        trace_id=f"trace-{run_id}",
        metadata={
            "source_ingest_run_id": run_id,
            "dataset": "tw_price_daily",
            "symbol": symbol,
            "trade_date": trade_date,
            "available_time": f"{trade_date}T06:05:00Z",
            "calendar_evidence": {
                "market": "TW",
                "last_trading_day": trade_date,
                "is_trading_day": True,
            },
            "normalized_row": {
                "symbol": symbol,
                "dataset": "tw_price_daily",
                "trade_date": trade_date,
                "close": 1050.0,
            },
        },
    )


def _attach_fake_postgres_evidence_repo(
    runtime: SourceIngestionRuntime,
    records: list[SourceRecord],
) -> _FakePostgresEvidenceHarness:
    harness = _FakePostgresEvidenceHarness()
    pg_repo = object.__new__(PostgresSourceEvidenceRepository)
    from services.knowledge.evidence import InMemoryEvidenceRepository

    InMemoryEvidenceRepository.__init__(pg_repo)
    pg_repo.dsn = "postgresql://fake/test"
    pg_repo.table = "source_ingest.source_evidence"
    pg_repo._replaying = False
    pg_repo._get_connection = harness.connection  # type: ignore[method-assign]

    for rec in records:
        InMemoryEvidenceRepository.add_source_record(pg_repo, rec)
        harness.append_record(rec)

    runtime.evidence_repository = pg_repo
    return harness


def test_registry_response_field_parity_across_all_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 5: GET /api/source-ingest/registry preserves every field any consumer reads."""
    _populate_hosted_39_configured_connectors(tmp_path, monkeypatch)
    runtime = SourceIngestionRuntime(data_dir=tmp_path)

    tw_rec = _make_source_record(
        source_id="tw-official:2330:2026-11-20",
        connector_id="tw-twse-tpex-official-market",
        run_id="run-tw-01",
    )
    other_rec = _make_source_record(
        source_id="finmind:2330:2026-11-20",
        connector_id="tw-finmind-market-data",
        run_id="run-fm-01",
    )
    _attach_fake_postgres_evidence_repo(runtime, [tw_rec, other_rec])

    app = FastAPI()
    app.include_router(create_catalog_controller_router(runtime))
    client = TestClient(app)

    resp = client.get("/api/source-ingest/registry")
    assert resp.status_code == 200
    payload = resp.json()

    assert set(payload.keys()) == REQUIRED_TOP_LEVEL_KEYS
    assert payload["schema_version"] == "source_connector_registry.v1"
    assert isinstance(payload["connectors"], list)
    assert len(payload["connectors"]) >= 39
    assert isinstance(payload["provider_examples"], list)
    assert len(payload["provider_examples"]) > 0
    assert isinstance(payload["policy_registry"], dict)
    assert isinstance(payload["financial_data_source_catalog"], dict)
    assert payload["active_universe_policy"] == payload["financial_data_source_catalog"]["active_universe_policy"]

    for entry in payload["connectors"]:
        assert REQUIRED_CONNECTOR_ENTRY_KEYS.issubset(entry.keys())
        assert REQUIRED_FRESHNESS_KEYS.issubset(entry["freshness"].keys())
        assert "mode" in entry["fetch_policy"]
        if entry["fetch_policy"]["mode"] == "provider_owned_adapter":
            assert "adapter" in entry["fetch_policy"]

    # Compare every connector's freshness summary against an uncached reference calculation
    snapshot = runtime.store.read_freshness_snapshot()
    runs_by_cid: dict[str, list[Any]] = {}
    for run in snapshot["runs"]:
        runs_by_cid.setdefault(str(getattr(run, "connector_id", "")), []).append(run)
    rcps_by_cid: dict[str, list[IngestReceipt]] = {}
    for rcp in snapshot["receipts"]:
        rcps_by_cid.setdefault(rcp.connector_id, []).append(rcp)
    schedules_by_cid = {s.connector_id: s for s in runtime.schedule_config_store.list_schedules()}
    configs, _ = runtime.connector_store.read_snapshot()
    cfg_by_cid = {c.connector.connector_id: c for c in configs}

    now_dt = datetime.now(timezone.utc)
    for entry in payload["connectors"]:
        cid = entry["connector_id"]
        cfg = cfg_by_cid.get(cid)
        conn = cfg.connector if cfg else runtime.manager.get_connector(cid)
        assert conn is not None
        ref_freshness = runtime._connector_freshness_summary_from_snapshot(
            cid,
            connector_metadata=conn.metadata,
            schedule=schedules_by_cid.get(cid),
            watermark=snapshot["watermarks"].get(cid),
            runs=runs_by_cid.get(cid, ()),
            receipts=_ordered_run_receipts(rcps_by_cid.get(cid, ()), runs_by_cid.get(cid, ())),
            now=now_dt,
            latest_record=tw_rec if cid == "tw-twse-tpex-official-market" else other_rec if cid == "tw-finmind-market-data" else None,
        )
        # Non-time-delta structural fields match identically
        for key in (
            "schema_version",
            "status",
            "stale",
            "is_due",
            "schedule_enabled",
            "last_success_at",
            "source_timestamp",
            "source_timestamp_status",
            "stale_threshold_seconds",
            "last_failure_at",
            "last_typed_failure",
            "last_watermark",
            "last_ingest_run_id",
            "latest_run",
            "latest_receipt",
            "next_due_at",
            "next_run_at",
        ):
            assert entry["freshness"][key] == ref_freshness[key], f"Mismatch on {cid}.freshness.{key}"


def test_registry_reads_eliminate_full_table_postgres_evidence_scans_and_resorts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 5: steady registry reads perform 0 full-table Postgres payload scans and 0 redundant receipt sorts."""
    _populate_hosted_39_configured_connectors(tmp_path, monkeypatch)
    runtime = SourceIngestionRuntime(data_dir=tmp_path)

    records = [
        _make_source_record(
            source_id=f"finmind:2330:2026-11-{day:02d}:{idx}",
            connector_id="tw-finmind-market-data",
            run_id=f"run-fm-{idx}",
            trade_date=f"2026-11-{day:02d}",
        )
        for idx, day in enumerate([(i % 20) + 1 for i in range(200)])
    ]
    records.append(
        _make_source_record(
            source_id="tw-official:2330:2026-11-19",
            connector_id="tw-twse-tpex-official-market",
            run_id="run-tw-20261119",
            trade_date="2026-11-19",
        )
    )
    harness = _attach_fake_postgres_evidence_repo(runtime, records)

    app = FastAPI()
    app.include_router(create_catalog_controller_router(runtime))
    client = TestClient(app)

    # First read attaches to already-loaded PostgresSourceEvidenceRepository with 0 full-table payload scans
    resp1 = client.get("/api/source-ingest/registry")
    assert resp1.status_code == 200
    assert harness.full_table_payload_scans == 0
    assert harness.delta_payload_queries == 0
    assert harness.meta_count_queries == 1

    # Count schedule receipt sorts on subsequent steady registry reads
    sort_calls = 0
    orig_sort = runtime.store._sort_receipts_unlocked

    def _counting_sort(receipts: Any) -> list[IngestReceipt]:
        nonlocal sort_calls
        sort_calls += 1
        return orig_sort(receipts)

    monkeypatch.setattr(runtime.store, "_sort_receipts_unlocked", _counting_sort)

    for _ in range(4):
        resp_n = client.get("/api/source-ingest/registry")
        assert resp_n.status_code == 200

    # 0 full-table payload scans and 0 receipt re-sorts across all 4 steady registry reads
    assert harness.full_table_payload_scans == 0
    assert harness.delta_payload_queries == 0
    assert sort_calls == 0

    # Now simulate an external worker appending a newer tw-twse-tpex-official-market record in Postgres
    newer_tw = _make_source_record(
        source_id="tw-official:2330:2026-11-20",
        connector_id="tw-twse-tpex-official-market",
        run_id="run-tw-20261120",
        trade_date="2026-11-20",
    )
    harness.append_record(newer_tw)

    resp_after_append = client.get("/api/source-ingest/registry")
    assert resp_after_append.status_code == 200
    # Fetched via delta query (append_id > last_append_id), still 0 full-table scans!
    assert harness.full_table_payload_scans == 0
    assert harness.delta_payload_queries == 1
    latest_tw = runtime._latest_source_record_by_connector(
        connector_ids=("tw-twse-tpex-official-market",)
    )["tw-twse-tpex-official-market"]
    assert latest_tw.source_id == "tw-official:2330:2026-11-20"
