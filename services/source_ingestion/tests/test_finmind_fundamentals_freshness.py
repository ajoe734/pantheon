"""FinMind monthly revenue / quarterly statements freshness is decided by publication."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.source_ingestion import runtime as runtime_module
from services.source_ingestion.configured import ConnectorScheduleConfig
from services.source_ingestion.connector_definitions import list_connector_definitions
from services.source_ingestion.runtime import SourceIngestionRuntime
from services.source_ingestion.scheduler import IngestReceipt

MONTHLY = "tw-finmind-monthly-revenue"
QUARTERLY = "tw-finmind-quarterly-statements"


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _summary(connector_id: str, source_ts: str, now: datetime, *, interval: int = 86400):
    finished = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    receipt = IngestReceipt(
        ingest_run_id="ingest-test",
        connector_id=connector_id,
        status="completed",
        trigger_type="manual_one_shot",
        trace_id="trace-test",
        started_at=finished,
        finished_at=finished,
        raw_count=1,
        normalized_count=1,
        rejected_count=0,
        watermark=None,
        source_timestamp=source_ts,
        source_timestamp_status="valid",
    )
    schedule = ConnectorScheduleConfig(
        connector_id=connector_id, enabled=True, interval_seconds=interval, updated_at=finished
    )
    return SourceIngestionRuntime()._connector_freshness_summary_from_snapshot(
        connector_id,
        connector_metadata={},
        schedule=schedule,
        watermark=None,
        runs=[],
        receipts=[receipt],
        now=now,
    )


# Hosted readback shapes of 2026-11-19 ~14:55Z (failed on origin/dev: stale).
def test_hosted_monthly_revenue_shape_is_fresh() -> None:
    s = _summary(MONTHLY, "2026-11-10T10:00:00Z", _utc(2026, 11, 19, 14, 55))
    assert s["status"] == "fresh" and s["stale"] is False
    assert s["age_seconds"] > s["stale_threshold_seconds"]  # the generic rule rejected it


def test_hosted_quarterly_shape_is_fresh() -> None:
    s = _summary(QUARTERLY, "2026-11-14T10:00:00Z", _utc(2026, 11, 19, 14, 55))
    assert s["status"] == "fresh" and s["stale"] is False


@pytest.mark.parametrize(
    "connector,ts,now,stale",
    [
        # monthly: Oct revenue due 2026-11-10; grace 2 days
        (MONTHLY, "2026-10-10T10:00:00Z", _utc(2026, 11, 9, 4), False),   # day before deadline
        (MONTHLY, "2026-10-10T10:00:00Z", _utc(2026, 11, 10, 4), False),  # deadline day
        (MONTHLY, "2026-10-10T10:00:00Z", _utc(2026, 11, 11, 4), False),  # inside grace
        (MONTHLY, "2026-10-10T10:00:00Z", _utc(2026, 11, 13, 4), True),   # missing past grace
        (MONTHLY, "2026-11-10T10:00:00Z", _utc(2026, 11, 13, 4), False),  # published
        # month boundary Nov -> Dec
        (MONTHLY, "2026-11-10T10:00:00Z", _utc(2026, 12, 9, 4), False),
        (MONTHLY, "2026-11-10T10:00:00Z", _utc(2026, 12, 13, 4), True),
        # quarterly: Q3 due 2026-11-14
        (QUARTERLY, "2026-08-14T10:00:00Z", _utc(2026, 11, 13, 4), False),
        (QUARTERLY, "2026-08-14T10:00:00Z", _utc(2026, 11, 15, 4), False),
        (QUARTERLY, "2026-08-14T10:00:00Z", _utc(2026, 11, 17, 4), True),
        (QUARTERLY, "2026-11-14T10:00:00Z", _utc(2026, 11, 17, 4), False),
        # quarter boundary into Q4 annual (due 2027-03-31)
        (QUARTERLY, "2026-11-14T10:00:00Z", _utc(2027, 3, 30, 4), False),
        (QUARTERLY, "2026-11-14T10:00:00Z", _utc(2027, 4, 3, 4), True),
        (QUARTERLY, "2027-03-31T10:00:00Z", _utc(2027, 4, 3, 4), False),
    ],
)
def test_publication_boundaries(connector, ts, now, stale) -> None:
    s = _summary(connector, ts, now)
    assert s["stale"] is stale
    assert (s["status"] == "stale") is stale


def test_other_connectors_unchanged(monkeypatch) -> None:
    now = _utc(2026, 11, 19, 14, 55)
    ids = [d.definition_id for d in list_connector_definitions()]
    assert "tw-finmind-valuation" in ids

    def snapshot() -> dict:
        out = {}
        for cid in ids:
            if cid in (MONTHLY, QUARTERLY):
                continue
            for ts in ("2026-11-19T10:00:00Z", "2026-11-14T10:00:00Z", "2026-10-01T10:00:00Z"):
                try:
                    out[(cid, ts)] = {
                        k: v for k, v in _summary(cid, ts, now).items() if k != "latest_receipt"
                    }
                except Exception as exc:  # identical behaviour required either way
                    out[(cid, ts)] = repr(exc)
        return out

    after = snapshot()
    monkeypatch.setattr(runtime_module, "FINMIND_FUNDAMENTAL_PUBLICATION_DATASETS", {})
    before = snapshot()
    assert after == before
