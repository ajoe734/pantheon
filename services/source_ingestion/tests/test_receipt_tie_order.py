from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from services.source_ingestion.connectors.base import IngestRun
from services.source_ingestion.scheduler import IngestReceipt, JsonlIngestScheduleStore

TS = "2026-11-14T23:55:10Z"
EARLY = "ingest-ffff00000001"  # sorts last, inserted first
LATE = "ingest-000000000002"  # sorts first, inserted last


def _run(run_id: str, connector_id: str = "tw-twse-tpex-official-market", started: str = TS) -> IngestRun:
    return IngestRun(
        ingest_run_id=run_id,
        connector_id=connector_id,
        source_type="market",
        trigger_type="manual_one_shot",
        trace_id="trace-tie",
        started_at=started,
    )


def _receipt(run_id: str, count: int, connector_id: str = "tw-twse-tpex-official-market",
             started: str = TS, finished: str | None = TS) -> IngestReceipt:
    return IngestReceipt(
        ingest_run_id=run_id,
        connector_id=connector_id,
        status="completed",
        trigger_type="manual_one_shot",
        trace_id="trace-tie",
        started_at=started,
        finished_at=finished,
        raw_count=count,
        normalized_count=count,
        rejected_count=0,
        watermark=None,
        source_timestamp=TS,
        source_timestamp_status="valid",
    )


def _populate(store: JsonlIngestScheduleStore) -> None:
    store.upsert_run(_run(EARLY))
    store.upsert_receipt(_receipt(EARLY, 256))
    store.upsert_run(_run(LATE))
    store.upsert_receipt(_receipt(LATE, 60))


def _ids(receipts) -> list[str]:
    return [r.ingest_run_id for r in receipts]


def test_same_second_receipts_ordered_by_run_insertion(tmp_path: Path) -> None:
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl")
    _populate(store)
    assert store.read_freshness_snapshot()["receipts"][-1].normalized_count == 60
    assert _ids(store.list_receipts()) == [EARLY, LATE]
    assert _ids(store.list_receipts(connector_id="tw-twse-tpex-official-market")) == [EARLY, LATE]
    assert store.list_receipts(connector_id="other") == []


def test_run_position_wins_over_receipt_write_order(tmp_path: Path) -> None:
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl")
    store.upsert_run(_run(EARLY))
    store.upsert_run(_run(LATE))
    store.upsert_receipt(_receipt(LATE, 60))
    store.upsert_receipt(_receipt(EARLY, 256))
    assert _ids(store.list_receipts()) == [EARLY, LATE]


def test_receipts_without_run_fall_back_to_receipt_order(tmp_path: Path) -> None:
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl")
    store.upsert_receipt(_receipt(EARLY, 256))
    store.upsert_receipt(_receipt(LATE, 60))
    assert _ids(store.list_receipts()) == [EARLY, LATE]


def test_runs_page_newest_inserted_first(tmp_path: Path) -> None:
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl")
    _populate(store)
    page, _receipts, total = store.list_runs_page(limit=10)
    assert total == 2
    assert [r.ingest_run_id for r in page] == [LATE, EARLY]


def test_order_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _populate(JsonlIngestScheduleStore(path))
    reopened = JsonlIngestScheduleStore(path)
    assert _ids(reopened.list_receipts()) == [EARLY, LATE]
    assert reopened.read_freshness_snapshot()["receipts"][-1].normalized_count == 60
    assert [r.ingest_run_id for r in reopened.list_runs_page(limit=10)[0]] == [LATE, EARLY]


def test_order_survives_incremental_reload_after_other_process_append(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    reader = JsonlIngestScheduleStore(path)
    reader.upsert_run(_run(EARLY))
    reader.upsert_receipt(_receipt(EARLY, 256))
    assert _ids(reader.list_receipts()) == [EARLY]
    script = (
        "import sys\n"
        "from services.source_ingestion.tests.test_receipt_tie_order import LATE, JsonlIngestScheduleStore, _receipt, _run\n"
        "s = JsonlIngestScheduleStore(sys.argv[1])\n"
        "s.upsert_run(_run(LATE))\n"
        "s.upsert_receipt(_receipt(LATE, 60))\n"
    )
    repo_root = Path(__file__).resolve().parents[3]
    subprocess.run([sys.executable, "-c", script, str(path)], cwd=repo_root, check=True)
    assert _ids(reader.list_receipts()) == [EARLY, LATE]
    assert reader.read_freshness_snapshot()["receipts"][-1].normalized_count == 60
    assert [r.ingest_run_id for r in reader.list_runs_page(limit=10)[0]] == [LATE, EARLY]


def test_different_timestamps_keep_timestamp_order(tmp_path: Path) -> None:
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl")
    # Inserted newest first; timestamps must still dominate insertion order.
    store.upsert_run(_run("ingest-bbbb", started="2026-11-14T23:55:12Z"))
    store.upsert_receipt(_receipt("ingest-bbbb", 1, started="2026-11-14T23:55:12Z", finished="2026-11-14T23:55:12Z"))
    store.upsert_run(_run("ingest-aaaa", started="2026-11-14T23:55:10Z"))
    store.upsert_receipt(_receipt("ingest-aaaa", 2, started="2026-11-14T23:55:10Z", finished=None))
    assert _ids(store.list_receipts()) == ["ingest-aaaa", "ingest-bbbb"]
    assert [r.ingest_run_id for r in store.list_runs_page(limit=10)[0]] == ["ingest-bbbb", "ingest-aaaa"]


def test_journal_format_unchanged_and_legacy_journal_replays(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _populate(JsonlIngestScheduleStore(path))
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["record_type"] for e in entries] == ["ingest_run", "ingest_receipt", "ingest_run", "ingest_receipt"]
    assert all(set(e) >= {"record_type", "payload"} for e in entries)
    # A journal produced by the current dev code is exactly these rows; replay it fresh.
    copy = tmp_path / "legacy.jsonl"
    copy.write_text(path.read_text())
    replayed = JsonlIngestScheduleStore(copy)
    assert _ids(replayed.list_receipts()) == [EARLY, LATE]
    assert replayed.get_receipt(LATE).normalized_count == 60
