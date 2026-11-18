"""SOURCE-RECEIPT-REFS-ONLY-20261117-R2: receipt journal migration, retention and alarms.

Also a generator of a hosted shaped journal: ``python3 scripts/test_migrate_ingest_receipts.py
--synth PATH --gib 2.4`` writes legacy rows (500 record batches with every per record list,
snapshot bodies embedded) so the migration and the replay can be measured at hosted size.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_ingest_receipts as mig  # noqa: E402
from services.source_ingestion.connectors.base import IngestRun  # noqa: E402
from services.source_ingestion.scheduler import (  # noqa: E402
    RECEIPT_LINE_BUDGET_BYTES,
    CrawlFrontierItem,
    IngestReceipt,
    JsonlIngestScheduleStore,
    SourceWatermark,
    compact_receipt_payload,
)

DEPLOYED_SHA = "e7ec353d6"
NOW = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def legacy_receipt(index: int, *, records: int = 500, days_ago: float = 1, status: str = "completed",
                   connector: str = "collector-tw-twse-price-history") -> IngestReceipt:
    """A receipt in the shape the hosted writer produced: every per record list embedded."""
    run_id = f"ingest-{index:06d}"
    bodies = [{"schema_version": "x", "snapshot_id": f"snap-{index}-{n}", "symbol": f"{n:05d}.TWSE",
               "dataset": "tw_price_daily", "event_time": "2026-11-16T00:00:00Z",
               "observed_at": "2026-11-16T01:00:00Z", "closes": [100.0 + i for i in range(60)],
               "lineage": {"content_refs": [f"ref://{n}/{i}" for i in range(60)]}} for n in range(records)]
    return IngestReceipt(
        ingest_run_id=run_id, connector_id=connector, status=status, trigger_type="scheduled", trace_id="t",
        started_at=_ts(days_ago), finished_at=_ts(days_ago), raw_count=records, normalized_count=records,
        rejected_count=0, watermark="w", source_timestamp="2026-11-16T00:00:00Z", source_timestamp_status="valid",
        created_at=_ts(days_ago),
        evidence_refs={
            "source_ids": [f"src-{index}-{n}" for n in range(records)],
            "evidence_item_ids": [f"evi-{index}-{n}" for n in range(records)],
            "evidence_bundle_id": f"evbundle-{index}",
            "knowledge_object_ids": [f"ko-{index}-{n}" for n in range(records)],
            "distillation_admissions": {f"src-{index}-{n}": {"status": "admitted", "job_id": f"job-{n}",
                                                             "source_digest": "d" * 64} for n in range(records)},
            "market_snapshots": {"schema_version": "b.v1", "ingest_run_id": run_id,
                                 "accepted_record_count": records, "updated_snapshot_count": records,
                                 "snapshots": bodies}},
        storage_refs={"raw_refs": [{"uri": "raw://a"}], "normalized_refs": [{"uri": "norm://a"}]})


def _row(record_type: str, record_id: str, payload: dict) -> bytes:
    return (json.dumps({"schema_version": "ingest_schedule_store.v1", "record_type": record_type,
                        "record_id": record_id, "payload": payload}, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _run(receipt: IngestReceipt, status: str = "completed") -> IngestRun:
    return IngestRun(ingest_run_id=receipt.ingest_run_id, connector_id=receipt.connector_id, source_type="market",
                     trigger_type="scheduled", trace_id="t", started_at=receipt.started_at,
                     raw_count=receipt.raw_count, normalized_count=receipt.normalized_count,
                     status=status)


def write_legacy_journal(path: Path, receipts: list[IngestReceipt], *, with_runs: bool = True) -> None:
    with path.open("wb") as handle:
        for receipt in receipts:
            if with_runs:
                handle.write(_row("ingest_run", receipt.ingest_run_id, _run(receipt).to_dict()))
            handle.write(_row("ingest_receipt", receipt.ingest_run_id, receipt.to_dict()))


def state_of(path: Path) -> dict:
    """Replay a COPY of the journal (opening a store rewrites an over-budget journal in place)."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.jsonl"
        shutil.copyfile(path, copy)
        store = JsonlIngestScheduleStore(copy, retention_days=0)
        return {name: {k: v.to_dict() for k, v in getattr(store, name).items()}
                for name in ("_runs", "_watermarks", "_frontier")} | {"receipt_ids": sorted(store._receipts)}


# ---- the 16 KB byte test -------------------------------------------------------------------

def test_500_record_batch_receipt_line_is_at_most_16kb_counts_and_references_only(tmp_path):
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl", retention_days=0)
    store.upsert_receipt(legacy_receipt(1, records=500))
    line = max(len(row) for row in (tmp_path / "j.jsonl").read_bytes().splitlines(keepends=True))
    print("RECEIPT_LINE_BYTES_500=" + str(line))
    assert line <= RECEIPT_LINE_BUDGET_BYTES
    refs = store.get_receipt("ingest-000001").evidence_refs
    assert refs["source_id_count"] == refs["evidence_item_id_count"] == refs["knowledge_object_id_count"] == 500
    assert refs["distillation_admission_count"] == 500
    assert refs["distillation_admission_status_counts"] == {"admitted": 500}
    assert refs["market_snapshots"]["snapshot_count"] == 500 and "snapshots" not in refs["market_snapshots"]
    assert refs["evidence_bundle_id"] == "evbundle-1"
    assert not any(isinstance(v, (list, dict)) and len(v) > 8 for v in refs.values() if v is not refs["market_snapshots"])


def test_a_receipt_that_stays_over_16kb_is_counted_and_alarmed(tmp_path):
    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl", retention_days=0)
    store.upsert_receipt(legacy_receipt(1, records=3))
    assert store.journal_stats()["alarms"] == []
    fat = IngestReceipt(**{**legacy_receipt(2, records=3).__dict__,
                           "evidence_refs": {"operator_note": "x" * 20000}})
    store.upsert_receipt(fat)
    stats = store.journal_stats()
    assert stats["receipt_lines_over_budget"] == 1 and stats["max_receipt_line_bytes"] > RECEIPT_LINE_BUDGET_BYTES
    assert "ingest_receipt_line_over_16kb" in stats["alarms"]
    assert "ingest_receipt_line_over_16kb" in JsonlIngestScheduleStore(tmp_path / "j.jsonl", retention_days=0).journal_stats()["alarms"]


def test_growth_over_5mb_per_hour_alarm(tmp_path, monkeypatch):
    from services.source_ingestion import scheduler as sch

    store = JsonlIngestScheduleStore(tmp_path / "j.jsonl", retention_days=0)
    store.upsert_receipt(legacy_receipt(1, records=3))
    assert store.journal_stats()["alarms"] == []
    store._growth_samples.clear()
    store._growth_samples.append((sch.time.monotonic() - 1800, 0))
    monkeypatch.setattr(sch, "JOURNAL_GROWTH_ALARM_BYTES_PER_HOUR", 100)
    stats = store.journal_stats()
    assert stats["growth_bytes_last_hour"] > 100
    assert "ingest_schedule_journal_growth_over_5mb_per_hour" in stats["alarms"]


# ---- retention in the store -----------------------------------------------------------------

def test_retention_exact_set(tmp_path):
    path = tmp_path / "j.jsonl"
    receipts = []
    # distinct connectors so "newest run of its connector" pinning does not mask retention
    for i, (age, conn) in enumerate([(90, "a"), (60, "b"), (45, "c-watermark"), (40, "c-frontier"),
                                     (35, "c-lineage"), (2, "f"), (33, "c-open"), (31, "g")], start=1):
        receipts.append(legacy_receipt(i, records=3, days_ago=age, connector=conn))
    with path.open("wb") as handle:
        for r in receipts:
            handle.write(_row("ingest_run", r.ingest_run_id,
                              _run(r, "fetching" if r.connector_id == "c-open" else "completed").to_dict()))
            handle.write(_row("ingest_receipt", r.ingest_run_id, r.to_dict()))
        handle.write(_row("source_watermark", "c-watermark", SourceWatermark(
            connector_id="c-watermark", source_type="market", value="v", updated_at=_ts(1),
            last_ingest_run_id="ingest-000003").to_dict()))
        handle.write(_row("crawl_frontier_item", "f1", CrawlFrontierItem(
            frontier_id="f1", connector_id="c-frontier", status="done", ingest_run_id="ingest-000004",
            created_at=_ts(40), updated_at=_ts(40)).to_dict()))
    # every connector's newest run is pinned, so make the old ones non-newest with a later run each
    for conn in ("a", "b", "g"):
        later = legacy_receipt(100 + ord(conn), records=3, days_ago=0.5, connector=conn)
        with path.open("ab") as handle:
            handle.write(_row("ingest_run", later.ingest_run_id, _run(later).to_dict()))
            handle.write(_row("ingest_receipt", later.ingest_run_id, later.to_dict()))
    before_runs = state_of(path)["_runs"]
    store = JsonlIngestScheduleStore(path, retention_days=30, pinned_run_ids=lambda: ["ingest-000005"], )
    gone = {"ingest-000001", "ingest-000002", "ingest-000008"}
    assert gone.isdisjoint(store._receipts)
    assert {"ingest-000003", "ingest-000004", "ingest-000005", "ingest-000006", "ingest-000007"} <= set(store._receipts)
    assert state_of(path)["_runs"] == before_runs  # runs are not retention targets
    # retention disabled keeps everything
    path2 = tmp_path / "k.jsonl"
    write_legacy_journal(path2, [legacy_receipt(1, records=3, days_ago=400), legacy_receipt(2, records=3, days_ago=1)])
    assert len(JsonlIngestScheduleStore(path2, retention_days=0)._receipts) == 2


def test_retention_runs_on_the_write_path_once_a_day(tmp_path, monkeypatch):
    from services.source_ingestion import scheduler as sch

    path = tmp_path / "j.jsonl"
    write_legacy_journal(path, [legacy_receipt(1, records=3, days_ago=90, connector="a"),
                                legacy_receipt(2, records=3, days_ago=1, connector="a")])
    store = JsonlIngestScheduleStore(path, retention_days=30)
    assert "ingest-000001" not in store._receipts  # enforced at start
    store.upsert_receipt(legacy_receipt(3, records=3, days_ago=0.1, connector="a"))
    store._last_retention_check -= 86500
    store.upsert_receipt(legacy_receipt(4, records=3, days_ago=80, connector="z"))  # no run row: not pinned
    assert store.get_receipt("ingest-000004") is None
    assert store.get_receipt("ingest-000003") is not None


# ---- migration script ----------------------------------------------------------------------

def _hosted_like(path: Path, n: int = 6) -> list[IngestReceipt]:
    receipts = [legacy_receipt(i, records=500, days_ago=1 + (i % 3), connector=f"c{i % 2}") for i in range(n)]
    write_legacy_journal(path, receipts)
    return receipts


def test_migration_is_lossless_for_runs_watermarks_frontier_and_receipt_ids(tmp_path):
    path = tmp_path / "j.jsonl"
    receipts = _hosted_like(path)
    with path.open("ab") as handle:
        handle.write(_row("source_watermark", "c0", SourceWatermark(
            connector_id="c0", source_type="market", value="v", updated_at=_ts(1),
            last_ingest_run_id="ingest-000004").to_dict()))
    before_bytes = path.stat().st_size
    before = state_of(path)
    report = mig.migrate(path, retention_days=30, now=NOW)
    assert report["changed"] and report["compacted_rows"] == len(receipts)
    assert path.stat().st_size < before_bytes // 50
    assert report["after"]["max_receipt_line_bytes"] <= RECEIPT_LINE_BUDGET_BYTES
    after = state_of(path)
    assert after == before  # runs, watermarks, frontier, receipt ids identical
    assert path.with_name(path.name + ".pre-migrate.bak").stat().st_size == before_bytes
    assert not path.with_name(path.name + ".migrate.tmp").exists()
    assert not path.with_name(path.name + ".migrate.state.json").exists()
    # receipt content: compaction only touches evidence_refs
    old_store = JsonlIngestScheduleStore(path.with_name(path.name + ".pre-migrate.bak"), retention_days=0)
    new_store = JsonlIngestScheduleStore(path, retention_days=0)
    for rid in before["receipt_ids"]:
        assert new_store._receipts[rid].to_dict() == old_store._receipts[rid].to_dict()
    # idempotent: a second run has nothing to do
    assert mig.migrate(path, retention_days=30, now=NOW)["changed"] is False


def test_migration_drops_expired_unpinned_receipts_and_dry_run_changes_nothing(tmp_path):
    path = tmp_path / "j.jsonl"
    write_legacy_journal(path, [legacy_receipt(1, records=3, days_ago=90, connector="a"),
                                legacy_receipt(2, records=3, days_ago=1, connector="a")])
    snapshot = path.read_bytes()
    dry = mig.migrate(path, retention_days=30, now=NOW, dry_run=True)
    assert dry["would_drop_receipts"] == 1 and path.read_bytes() == snapshot
    report = mig.migrate(path, retention_days=30, now=NOW)
    assert report["expired_rows"] == 1
    assert state_of(path)["receipt_ids"] == ["ingest-000002"]
    assert set(state_of(path)["_runs"]) == {"ingest-000001", "ingest-000002"}


def test_migration_resumes_after_a_crash_and_every_state_is_readable(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    _hosted_like(path, n=8)
    before = state_of(path)
    original = path.read_bytes()
    monkeypatch.setattr(mig, "CHECKPOINT_BYTES", 1)  # checkpoint after every row
    with pytest.raises(KeyboardInterrupt):
        mig.migrate(path, retention_days=30, now=NOW, stop_after_checkpoints=5)
    tmp = path.with_name(path.name + ".migrate.tmp")
    state = path.with_name(path.name + ".migrate.state.json")
    assert tmp.exists() and state.exists()
    assert path.read_bytes() == original  # journal untouched and readable at the crash
    assert state_of(path) == before
    # a torn partial row after the last checkpoint is discarded on resume
    with tmp.open("ab") as handle:
        handle.write(b'{"torn":')
    report = mig.migrate(path, retention_days=30, now=NOW)
    assert report["resumed"] is True
    assert state_of(path) == before
    assert max(len(line) for line in path.read_bytes().splitlines()) <= RECEIPT_LINE_BUDGET_BYTES


def test_migration_restarts_when_the_journal_was_replaced_since_the_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    _hosted_like(path, n=4)
    monkeypatch.setattr(mig, "CHECKPOINT_BYTES", 1)
    with pytest.raises(KeyboardInterrupt):
        mig.migrate(path, retention_days=30, now=NOW, stop_after_checkpoints=3)
    replacement = tmp_path / "other.jsonl"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)  # new inode
    report = mig.migrate(path, retention_days=30, now=NOW)
    assert report["resumed"] is False and report["changed"]


def test_rows_appended_during_the_copy_are_kept(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    _hosted_like(path, n=3)
    late = legacy_receipt(50, records=500, days_ago=0)
    real_copy = mig._copy_range
    calls = {"n": 0}

    def appending_copy(src, dst, start, end, drop_ids, stats, on_checkpoint=None):
        calls["n"] += 1
        result = real_copy(src, dst, start, end, drop_ids, stats, on_checkpoint)
        if calls["n"] == 1:  # the writer appends after the bulk copy, before the catch-up
            with path.open("ab") as handle:
                handle.write(_row("ingest_run", late.ingest_run_id, _run(late).to_dict()))
                handle.write(_row("ingest_receipt", late.ingest_run_id, late.to_dict()))
        return result

    monkeypatch.setattr(mig, "_copy_range", appending_copy)
    mig.migrate(path, retention_days=30, now=NOW)
    assert "ingest-000050" in state_of(path)["receipt_ids"]
    assert max(len(line) for line in path.read_bytes().splitlines()) <= RECEIPT_LINE_BUDGET_BYTES


def test_migration_refuses_a_torn_tail_row(tmp_path):
    path = tmp_path / "j.jsonl"
    _hosted_like(path, n=2)
    with path.open("ab") as handle:
        handle.write(b'{"payload":{')
    with pytest.raises(mig.MigrationError, match="torn"):
        mig.migrate(path, retention_days=30, now=NOW)
    assert not path.with_name(path.name + ".pre-migrate.bak").exists()


# ---- previous reader (hosted version) both directions ----------------------------------------

def _previous_scheduler_module():
    source = subprocess.run(["git", "show", f"{DEPLOYED_SHA}:services/source_ingestion/scheduler.py"],
                            capture_output=True, text=True, check=False, cwd=ROOT)
    if source.returncode != 0:
        pytest.fail(f"deployed reader {DEPLOYED_SHA} is not available from git: {source.stderr.strip()}")
    name = "services.source_ingestion._deployed_scheduler_refs_only"
    module = types.ModuleType(name)
    module.__package__ = "services.source_ingestion"
    module.__file__ = "<deployed scheduler>"
    sys.modules[name] = module
    exec(compile(source.stdout, module.__file__, "exec"), module.__dict__)
    return module


def test_previous_reader_reads_the_migrated_journal_and_new_reader_reads_the_old_one(tmp_path):
    previous = _previous_scheduler_module()
    path = tmp_path / "j.jsonl"
    receipts = _hosted_like(path, n=4)
    old_reader = previous.JsonlIngestScheduleStore(path)  # old journal, old reader
    old_view = {k: {i: v.to_dict() for i, v in getattr(old_reader, k).items()} for k in ("_runs", "_receipts")}
    new_on_old = JsonlIngestScheduleStore(tmp_path / "copy.jsonl", retention_days=0)
    (tmp_path / "copy.jsonl").write_bytes(path.read_bytes())
    new_on_old = JsonlIngestScheduleStore(tmp_path / "copy.jsonl", retention_days=0)  # new reader, old journal
    assert set(new_on_old._receipts) == set(old_view["_receipts"]) == {r.ingest_run_id for r in receipts}
    mig.migrate(path, retention_days=30, now=NOW)
    migrated = previous.JsonlIngestScheduleStore(path)  # previous reader, migrated journal
    assert set(migrated._receipts) == {r.ingest_run_id for r in receipts}
    assert {k: v.to_dict() for k, v in migrated._runs.items()} == old_view["_runs"]
    for rid, receipt in migrated._receipts.items():
        data = receipt.to_dict()
        assert data["raw_count"] == 500 and data["evidence_refs"]["market_snapshots"]["updated_snapshot_count"] == 500
    # previous writer appends to the migrated journal; the new reader still reads it
    migrated.upsert_receipt(legacy_receipt(77, records=3))
    assert "ingest-000077" in JsonlIngestScheduleStore(path, retention_days=0)._receipts


def test_replay_of_a_compact_journal_equals_replay_of_the_compacted_old_journal(tmp_path):
    path = tmp_path / "j.jsonl"
    _hosted_like(path, n=3)
    old = JsonlIngestScheduleStore(tmp_path / "x.jsonl", retention_days=0)
    (tmp_path / "x.jsonl").write_bytes(path.read_bytes())
    old = JsonlIngestScheduleStore(tmp_path / "x.jsonl", retention_days=0)
    mig.migrate(path, retention_days=0)
    new = JsonlIngestScheduleStore(path, retention_days=0)
    assert {k: v.to_dict() for k, v in old._receipts.items()} == {k: v.to_dict() for k, v in new._receipts.items()}


def test_compaction_is_idempotent():
    payload = legacy_receipt(1, records=10).to_dict()
    once, changed = compact_receipt_payload(payload)
    twice, again = compact_receipt_payload(once)
    assert changed and not again and twice is once


# ---- hosted shaped generator ----------------------------------------------------------------

def synthesize(path: Path, target_bytes: int, per_receipt_records: int = 500) -> dict:
    """Legacy hosted-shaped journal: ~5800 rows of receipts, big ones carry every per record list."""
    big = legacy_receipt(0, records=per_receipt_records)
    big_len = len(_row("ingest_receipt", "x", big.to_dict()))
    written = rows = receipts = 0
    with path.open("wb") as handle:
        i = 0
        while written < target_bytes:
            age = 0.05 + (i % 4000) * 0.01  # about 40 days of history; some past retention
            conn = f"collector-{i % 40}"
            if i % 21 == 0:
                receipt = legacy_receipt(i, records=per_receipt_records, days_ago=age, connector=conn)
            else:
                receipt = legacy_receipt(i, records=2, days_ago=age, connector=conn)
            for row in (_row("ingest_run", receipt.ingest_run_id, _run(receipt).to_dict()),
                        _row("ingest_receipt", receipt.ingest_run_id, receipt.to_dict())):
                handle.write(row)
                written += len(row)
                rows += 1
            receipts += 1
            i += 1
    return {"bytes": written, "rows": rows, "receipts": receipts, "big_receipt_line_bytes": big_len}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--synth", type=Path, required=True)
    ap.add_argument("--gib", type=float, default=2.4)
    args = ap.parse_args()
    print(json.dumps(synthesize(args.synth, int(args.gib * 1024**3))))


def test_script_and_service_share_one_backup_path_and_keep_the_oldest_copy(tmp_path):
    from services.source_ingestion import scheduler as sched

    path = tmp_path / "j.jsonl"
    _hosted_like(path)
    original = path.read_bytes()
    assert mig.journal_backup_path is sched.journal_backup_path
    assert mig.keep_journal_backup is sched.keep_journal_backup
    assert mig.migrate(path, retention_days=30, now=NOW)["changed"]
    backup = path.with_name(path.name + sched.RECEIPT_BACKUP_SUFFIX)
    assert backup.read_bytes() == original
    # A second rewrite inside the window does not replace the oldest complete copy.
    write_legacy_journal(path.with_name("extra.jsonl"), [legacy_receipt(50, records=500, days_ago=1, connector="c9")])
    with path.open("ab") as handle:
        handle.write(path.with_name("extra.jsonl").read_bytes())
    assert mig.migrate(path, retention_days=30, now=NOW)["changed"]
    assert backup.read_bytes() == original


# ---- SOURCE-RECEIPT-STORAGE-REFS-BOUND-20261118: the script compacts storage_refs rows too ----------

def test_script_compacts_oversize_storage_refs_rows_and_keeps_the_backup(tmp_path):
    dates = [f"2025-{1 + n // 28:02d}-{1 + n % 28:02d}" for n in range(400)]
    refs = {"schema_version": "market_data_storage_manifest.v1", "ingest_run_id": "ingest-000007",
            "raw_refs": [], "feature_refs": [], "summary": {"normalized_row_count": 400},
            "normalized_refs": [{"ref_type": "normalized_rows", "dataset": "tw_index_daily", "date": d,
                                 "uri": f"/data/normalized/tw_index_daily/date={d}/ingest-000007.jsonl",
                                 "row_count": 1, "schema_hash": "h" * 32} for d in dates]}
    receipt = IngestReceipt(**{**legacy_receipt(7, records=3).__dict__, "evidence_refs": {"evidence_bundle_id": "e"},
                               "storage_refs": refs})
    path = tmp_path / "j.jsonl"
    write_legacy_journal(path, [receipt])
    assert max(len(r) for r in path.read_bytes().splitlines()) > RECEIPT_LINE_BUDGET_BYTES
    before = path.read_bytes()
    report = mig.migrate(path, retention_days=0)
    assert report["changed"] is True
    assert max(len(r) for r in path.read_bytes().splitlines()) <= RECEIPT_LINE_BUDGET_BYTES
    assert path.with_name(path.name + ".pre-migrate.bak").read_bytes() == before
    stored = json.loads([r for r in path.read_bytes().splitlines() if b"ingest_receipt" in r][0])["payload"]["storage_refs"]
    assert stored["ref_counts"]["normalized"] == 400 and stored["stores"]["normalized"][0]["first_date"] == "2025-01-01"
