"""SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118: the read only pin source (acceptance 5 and 6)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.source_ingestion.connectors.base import IngestRun
from services.source_ingestion.pin_source import PIN_PATHS_ENV, PinSource, PinSourceUnavailable, main
from services.source_ingestion.scheduler import IngestReceipt, JsonlIngestScheduleStore

REPO = Path(__file__).resolve().parents[3]
RECORDED = REPO / "docs" / "deployment" / "evidence" / "S5-LOOPS-001" / "source-readbacks"
NOW = datetime.now(timezone.utc)


# ---- reproduce first: what recorded evidence really references --------------------------------------------


def test_recorded_paper_job_evidence_references_ingest_run_ids_and_trade_days():
    """S5-LOOPS-001 recorded readbacks (a paper job and its market snapshots): lineage.ingest_run_ids, the
    ingest_run_id of each source record and the snapshot point times are what the evidence references."""
    raw = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in RECORDED.glob("*.json")}
    assert {"job-post.json", "snapshot-reload.json"} <= set(raw)
    pins = PinSource([RECORDED]).read()
    text = "\n".join(json.dumps(v) for v in raw.values())
    assert pins.files_read == len(raw)
    assert pins.run_ids >= {"ingest-65d1dd0d00d5", "ingest-3d71219942fd", "ingest-e276cb8baffe"}
    assert all(r in text for r in pins.run_ids)  # nothing invented
    # The recording names runs and storage refs but no trade date field: dates come from the run files below.
    assert pins.trade_dates == frozenset()


def test_dates_of_a_pinned_runs_files_are_pinned_dates(tmp_path):
    store = tmp_path / "market_data_store"
    for day, run in (("2026-09-04", "ingest-f8c3db055740"), ("2026-09-05", "ingest-other")):
        d = store / "normalized" / "tw_price_daily" / f"date={day}"
        d.mkdir(parents=True)
        (d / f"{run}.jsonl").write_text("{}")
    raw = store / "raw" / "src" / "tw_price_daily" / "date=2026-09-06"
    raw.mkdir(parents=True)
    (raw / "ingest-f8c3db055740.jsonl.gz").write_bytes(b"")
    pins = PinSource([RECORDED], run_files_root=store).read()
    assert "ingest-f8c3db055740" in pins.run_ids
    assert pins.trade_dates == {"2026-09-04", "2026-09-06"}  # not 09-05: that run is not pinned


def test_each_reference_field_is_read(tmp_path):
    (tmp_path / "gate_proof.json").write_text(json.dumps({"audit": {"ingest_run_id": "ingest-gate", "evidence_bundle_ref": "e"},
                                                         "storage": {"checksum": "c"}}))
    (tmp_path / "session.json").write_text(json.dumps({"lineage": {"ingest_run_ids": ["ingest-l1", "ingest-l2"]},
                                                      "points": [{"event_time": "2026-11-13T05:30:00Z"}]}))
    (tmp_path / "paper.jsonl").write_text("\n".join(json.dumps(x) for x in (
        {"metadata": {"ingest_run_id": "ingest-sig", "trade_date": "2026-11-12"}},
        {"provenance": {"source_ingest_run_id": "ingest-prov", "trade_dates": ["2026-11-10", "2026-11-11"]}})))
    pins = PinSource([tmp_path]).read()
    assert pins.run_ids == {"ingest-gate", "ingest-l1", "ingest-l2", "ingest-sig", "ingest-prov"}
    assert pins.trade_dates == {"2026-11-13", "2026-11-12", "2026-11-10", "2026-11-11"}


def test_it_is_read_only_and_adds_no_store(tmp_path):
    (tmp_path / "p.json").write_text(json.dumps({"ingest_run_id": "ingest-x"}))
    before = sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    PinSource([tmp_path]).read()
    assert sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")) == before


# ---- fail closed ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("setup", ["not_configured", "missing", "bad_json", "too_big"])
def test_unresolvable_pins_raise_with_a_readable_reason(tmp_path, monkeypatch, setup):
    if setup == "not_configured":
        monkeypatch.delenv(PIN_PATHS_ENV, raising=False)
        source = PinSource.from_env()
        match = "pin_source_not_configured"
    elif setup == "missing":
        source, match = PinSource([tmp_path / "nowhere"]), "not found"
    elif setup == "bad_json":
        (tmp_path / "x.json").write_text("{oops")
        source, match = PinSource([tmp_path]), "not readable JSON"
    else:
        import services.source_ingestion.pin_source as ps

        monkeypatch.setattr(ps, "MAX_FILE_BYTES", 5)
        (tmp_path / "x.json").write_text(json.dumps({"ingest_run_id": "ingest-x"}))
        source, match = PinSource([tmp_path]), "over 5 bytes"
    with pytest.raises(PinSourceUnavailable, match=match):
        source.read()
    assert source.health()["resolved"] is False and match in source.health()["reason"]


def test_cli_writes_the_pinned_dates_for_the_cleanup_script(tmp_path, capsys):
    (tmp_path / "p.json").write_text(json.dumps({"ingest_run_id": "ingest-x", "trade_date": "2026-11-12"}))
    out = tmp_path / "dates.json"
    assert main(["--path", str(tmp_path / "p.json"), "--dates-out", str(out)]) == 0
    assert json.loads(out.read_text()) == ["2026-11-12"]  # the shape of cleanup_market_run_files --pinned-dates
    assert json.loads(capsys.readouterr().out)["pinned_run_ids"] == ["ingest-x"]
    assert main(["--path", str(tmp_path / "gone")]) == 2


# ---- receipt retention: the caller of pinned_run_ids -------------------------------------------------------------


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(record_type: str, record_id: str, payload: dict) -> bytes:
    return (json.dumps({"schema_version": "ingest_schedule_store.v1", "record_type": record_type,
                        "record_id": record_id, "payload": payload}, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _journal(path: Path) -> None:
    """Two receipts past retention (60 days) of different connectors, each followed by a newer run so that
    neither is the newest run of its connector; one newer receipt."""
    with path.open("wb") as handle:
        for run_id, connector, age in (("ingest-pinned", "a", 60), ("ingest-unpinned", "b", 60),
                                       ("ingest-newer-a", "a", 1), ("ingest-newer-b", "b", 1)):
            receipt = IngestReceipt(
                ingest_run_id=run_id, connector_id=connector, status="completed", trigger_type="scheduled", trace_id="t",
                started_at=_ts(age), finished_at=_ts(age), raw_count=3, normalized_count=3, rejected_count=0,
                watermark="w", source_timestamp=_ts(age), source_timestamp_status="valid", created_at=_ts(age))
            run = IngestRun(ingest_run_id=run_id, connector_id=connector, source_type="market", trigger_type="scheduled",
                            trace_id="t", started_at=_ts(age), raw_count=3, normalized_count=3, status="completed")
            handle.write(_row("ingest_run", run_id, run.to_dict()))
            handle.write(_row("ingest_receipt", run_id, receipt.to_dict()))


def test_a_receipt_past_retention_that_a_pin_references_is_kept_and_one_without_a_pin_is_dropped(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "gate_proof.json").write_text(json.dumps({"audit": {"ingest_run_id": "ingest-pinned"}}))
    path = tmp_path / "j.jsonl"
    _journal(path)
    store = JsonlIngestScheduleStore(path, retention_days=30, pinned_run_ids=PinSource([evidence]).run_ids)
    assert "ingest-pinned" in store._receipts  # referenced by a proof: kept
    assert "ingest-unpinned" not in store._receipts  # no pin: dropped
    assert {"ingest-newer-a", "ingest-newer-b"} <= set(store._receipts)
    assert store.journal_stats()["receipt_pin_source"] == {"resolved": True, "pinned_run_id_count": 1}


def test_when_the_pins_cannot_be_resolved_retention_drops_nothing_and_health_says_why(tmp_path):
    path = tmp_path / "j.jsonl"
    _journal(path)
    store = JsonlIngestScheduleStore(path, retention_days=30, pinned_run_ids=PinSource([tmp_path / "evidence-not-mounted"]).run_ids)
    assert {"ingest-pinned", "ingest-unpinned", "ingest-newer-a", "ingest-newer-b"} <= set(store._receipts)
    pin = store.journal_stats()["receipt_pin_source"]
    assert pin["resolved"] is False and pin["retention_dropped_nothing"] is True
    assert "pin evidence path not found" in pin["reason"]
    # the same journal with resolvable evidence drops the unpinned receipt: the difference is the pin source
    (tmp_path / "evidence").mkdir()
    again = JsonlIngestScheduleStore(path, retention_days=30, pinned_run_ids=PinSource([tmp_path / "evidence"]).run_ids)
    assert "ingest-unpinned" not in again._receipts and "ingest-pinned" not in again._receipts


def test_runtime_wires_the_pin_source_and_reports_an_unset_path(tmp_path, monkeypatch):
    from services.source_ingestion.runtime import SourceIngestionRuntime

    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(PIN_PATHS_ENV, raising=False)
    _journal(SourceIngestionRuntime().SCHEDULE_STORE_PATH)  # a journal with receipts past retention
    runtime = SourceIngestionRuntime()
    assert runtime.store._pinned_run_ids == runtime.pin_source.run_ids
    assert {"ingest-pinned", "ingest-unpinned"} <= set(runtime.store._receipts)  # unset: nothing is dropped
    pin = runtime._source_runtime_metrics()["ingest_schedule_journal"]["receipt_pin_source"]
    assert pin["resolved"] is False and "pin_source_not_configured" in pin["reason"]
