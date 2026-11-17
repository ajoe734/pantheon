from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from scripts import backfill_daily_bars as bf
from scripts import cleanup_market_run_files as cl
from scripts.test_backfill_daily_bars import _build, _line, _state, _write, TODAY

OK_READERS = {"trading_days": 5, "mismatches": 0}
LATER = "2026-12-30"  # more than 30 days after the 11-15/11-16 write dates


def _prepared(tmp_path):
    _build(tmp_path)
    backfill = bf.backfill(tmp_path, _state(), today=TODAY, apply=True, newest_run_on_conflict=True)
    # the one row without a trade date blocks its file: remove it so the fixture is deletable
    for p in (tmp_path / "normalized" / "tw_price_daily" / "date=2026-11-15").glob("*.jsonl"):
        p.write_text("\n".join(l for l in p.read_text().splitlines() if '"trade_date"' in l) + "\n")
    raw = tmp_path / "raw" / "c" / "tw_price_daily" / "date=2026-11-15"
    raw.mkdir(parents=True)
    with gzip.open(raw / "run-a.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(_line("2330", "2026-11-12", 100.0) + "\n")
    return backfill


def _report(tmp_path, backfill, **kw):
    kw.setdefault("today", LATER)
    kw.setdefault("reader_evidence", OK_READERS)
    kw.setdefault("known_readers", ())
    return cl.build_report(tmp_path, backfill_report=backfill, **kw)


def _reasons(report):
    return {e["path"]: e["refused"] for p in report["partitions"].values() for e in p["files"]}


def test_dry_run_deletes_nothing_and_reports_dates_files_bytes(tmp_path):
    backfill = _prepared(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*.jsonl*"))
    rc = cl.main(["--root", str(tmp_path), "--today", LATER])
    assert rc == 0 and sorted(tmp_path.rglob("*.jsonl*")) == before
    report = _report(tmp_path, backfill)
    assert set(report["partitions"]) == {"2026-11-15", "2026-11-16"}
    assert report["totals"]["files"] == 3 and report["totals"]["eligible_files"] == 3
    assert report["totals"]["eligible_bytes"] == report["totals"]["bytes"] > 0


@pytest.mark.parametrize("evidence,reason", [
    (None, "no_reader_evidence"),
    ({"trading_days": 4, "mismatches": 0}, "readers_on_daily_bars_under_5_trading_days"),
    ({"trading_days": 9, "mismatches": 1}, "reader_mismatches"),
])
def test_refuses_without_five_clean_reader_days(tmp_path, evidence, reason):
    backfill = _prepared(tmp_path)
    report = _report(tmp_path, backfill, reader_evidence=evidence)
    assert report["totals"]["eligible_files"] == 0
    assert all(reason in r for r in _reasons(report).values())


def test_refuses_inside_the_verification_window(tmp_path):
    backfill = _prepared(tmp_path)
    report = _report(tmp_path, backfill, today="2026-11-20")
    assert all("younger_than_30_day_window" in r for r in _reasons(report).values())


def test_refuses_when_daily_bars_not_verified(tmp_path):
    backfill = _prepared(tmp_path)
    manifest = tmp_path / "daily_bars" / "_manifest" / "date=2026-11-13.json"
    data = json.loads(manifest.read_text())
    (tmp_path / "daily_bars" / data["files"][0]["path"]).write_bytes(b"corrupt")
    report = _report(tmp_path, backfill)
    refused = [r for r in _reasons(report).values() if any("daily_bars_not_verified:2026-11-13" in x for x in r)]
    assert refused  # the files that hold 11-13 are kept
    ok = [e for p in report["partitions"].values() for e in p["files"] if e["eligible"]]
    assert all("2026-11-13" not in e["trade_dates"] for e in ok)


def test_refuses_when_backfill_report_missing_or_stale(tmp_path):
    backfill = _prepared(tmp_path)
    assert all("no_backfill_report" in r for r in _reasons(_report(tmp_path, None)).values())
    stale = json.loads(json.dumps(backfill))
    stale["days"]["2026-11-12"]["content_digest"] = "0" * 64
    reasons = _reasons(_report(tmp_path, stale))
    assert any("backfill_report_digest_differs:2026-11-12" in x for r in reasons.values() for x in r)


def test_refuses_file_with_rows_that_have_no_trade_date(tmp_path):
    _build(tmp_path)  # run-a keeps its no-trade-date row
    backfill = bf.backfill(tmp_path, _state(), today=TODAY, apply=True, newest_run_on_conflict=True)
    reasons = _reasons(_report(tmp_path, backfill))
    assert any(x.startswith("rows_without_trade_date:1") for r in reasons.values() for x in r)


def test_pinned_dates_are_never_deleted(tmp_path):
    backfill = _prepared(tmp_path)
    report = _report(tmp_path, backfill, pinned_dates=["2026-11-13"])
    for p in report["partitions"].values():
        for e in p["files"]:
            if "2026-11-13" in e["trade_dates"]:
                assert not e["eligible"] and "pinned:2026-11-13" in e["refused"]


def test_real_run_needs_flag_and_the_report_digest(tmp_path):
    backfill = _prepared(tmp_path)
    report = _report(tmp_path, backfill)
    with pytest.raises(cl.CleanupRefused, match="needs --report-digest"):
        cl.execute(tmp_path, report, expected_digest=None)
    with pytest.raises(cl.CleanupRefused, match="digest does not match"):
        cl.execute(tmp_path, report, expected_digest="f" * 64)
    assert len(list(tmp_path.rglob("*.jsonl*"))) == 3
    log = tmp_path / "deleted.log"
    result = cl.execute(tmp_path, report, expected_digest=report["digest"], log=log)
    assert result["deleted_files"] == 3 and not list(tmp_path.rglob("*.jsonl*"))
    assert len(log.read_text().splitlines()) == 3  # each deletion logged
    assert (tmp_path / "daily_bars" / "_manifest").is_dir()  # daily bars untouched


def test_other_datasets_are_untouched(tmp_path):
    backfill = _prepared(tmp_path)
    other = tmp_path / "normalized" / "tw_institutional" / "date=2026-11-01"
    other.mkdir(parents=True)
    (other / "r.jsonl").write_text("{}\n")
    report = _report(tmp_path, backfill)
    cl.execute(tmp_path, report, expected_digest=report["digest"])
    assert (other / "r.jsonl").exists()


def test_cli_execute_requires_digest_and_then_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "KNOWN_RUN_FILE_READERS", ())
    backfill = _prepared(tmp_path)
    (tmp_path / "bf.json").write_text(json.dumps(backfill))
    (tmp_path / "ev.json").write_text(json.dumps(OK_READERS))
    base = ["--root", str(tmp_path), "--today", LATER, "--backfill-report", str(tmp_path / "bf.json"),
            "--reader-evidence", str(tmp_path / "ev.json")]
    assert cl.main(base + ["--execute"]) == 2
    report = _report(tmp_path, backfill)
    assert cl.main(base + ["--execute", "--report-digest", report["digest"]]) == 0
    assert not list(tmp_path.rglob("*.jsonl*"))


def test_refuses_every_file_while_a_known_reader_is_not_switched(tmp_path):
    backfill = _prepared(tmp_path)
    assert _report(tmp_path, backfill)["totals"]["eligible_files"] == 3  # all else satisfied
    readers = [dict(r) for r in cl.KNOWN_RUN_FILE_READERS]
    assert readers and any(not r["switched"] and r.get("blocks_cleanup", True) for r in readers)
    report = _report(tmp_path, backfill, known_readers=readers)  # the checked in list
    assert report["totals"]["eligible_files"] == 0 and report["totals"]["eligible_bytes"] == 0
    for r in readers:
        if not r["switched"] and r.get("blocks_cleanup", True):
            assert all(f"unswitched_reader:{_rid(r)}" in x for x in _reasons(report).values())
    assert sorted(report["unswitched_readers"]) == sorted(
        _rid(r) for r in readers if not r["switched"] and r.get("blocks_cleanup", True))
    log = tmp_path / "deleted.log"
    result = cl.execute(tmp_path, report, expected_digest=report["digest"], log=log)
    assert result["deleted_files"] == 0 and len(list(tmp_path.rglob("*.jsonl*"))) == 3
    # the real CLI with the checked in list refuses too
    (tmp_path / "bf.json").write_text(json.dumps(backfill))
    (tmp_path / "ev.json").write_text(json.dumps(OK_READERS))
    base = ["--root", str(tmp_path), "--today", LATER, "--backfill-report", str(tmp_path / "bf.json"),
            "--reader-evidence", str(tmp_path / "ev.json")]
    dry = _report(tmp_path, backfill, known_readers=None)
    assert cl.main(base + ["--execute", "--report-digest", dry["digest"]]) == 0
    assert len(list(tmp_path.rglob("*.jsonl*"))) == 3  # nothing deleted


def test_switching_the_last_reader_lifts_the_refusal(tmp_path):
    backfill = _prepared(tmp_path)
    switched = [dict(r, switched=True) for r in cl.KNOWN_RUN_FILE_READERS]
    assert _report(tmp_path, backfill, known_readers=switched)["totals"]["eligible_files"] == 3


def _rid(r):
    return f"{r['file']}::{r['part']}" if r.get("part") else str(r["file"])


def test_known_readers_list_matches_the_code():
    root = Path(__file__).resolve().parents[1]
    for r in cl.KNOWN_RUN_FILE_READERS:
        text = (root / r["file"]).read_text(encoding="utf-8")
        assert r["marker"] in text, r["file"]  # the old read stays in the file for rollback
        if r["switched"]:
            assert r["switched_marker"] and r["switched_marker"] in text, r["file"]
        elif r["switched_marker"]:
            assert r["switched_marker"] not in text, r["file"]  # a part is switched only when its new read exists
    for r in cl.KNOWN_KEYED_READERS:
        assert r["marker"] in (root / r["file"]).read_text(encoding="utf-8"), r["file"]
        assert r["switched"] is True  # routes through normalized_dataset_base: keyed under the reader setting


def test_every_run_file_reader_in_the_code_is_listed(tmp_path):
    root = Path(__file__).resolve().parents[1]
    listed = {r["file"] for r in cl.KNOWN_RUN_FILE_READERS}
    unlisted = [f for f in cl.scan_run_file_readers(root) if f not in listed]
    assert unlisted == [], f"files that open tw_price_daily run files and are not in KNOWN_RUN_FILE_READERS: {unlisted}"
    # the allowlist of false positives must not rot: each named file exists and still looks like a reader
    for rel, reason in cl.SCAN_FALSE_POSITIVES.items():
        assert reason and cl.looks_like_run_file_reader((root / rel).read_text(encoding="utf-8")), rel


def test_the_scan_fails_for_a_new_unlisted_reader(tmp_path):
    (tmp_path / "services" / "x").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "services" / "x" / "new_reader.py").write_text(
        'from pathlib import Path\n\ndef rows(root):\n    return sorted((Path(root) / "normalized" / "tw_price_daily").glob("date=*/*.jsonl"))\n')
    (tmp_path / "services" / "x" / "fine.py").write_text("def f():\n    return 1\n")
    found = cl.scan_run_file_readers(tmp_path)
    assert found == ["services/x/new_reader.py"]
    assert found[0] not in {r["file"] for r in cl.KNOWN_RUN_FILE_READERS}


def test_attestation_and_other_unswitched_readers_block_the_real_cleanup(tmp_path):
    """G2: the survivorship attestation still opens run files, so the checked in list keeps blocking."""
    names = {_rid(r) for r in cl.KNOWN_RUN_FILE_READERS if not r["switched"] and r.get("blocks_cleanup", True)}
    assert any("survivorship attestation" in n for n in names)
    assert any(n.startswith("services/source_ingestion/snapshot_universe.py") for n in names)
    assert any(n.startswith("services/source_ingestion/pin_source.py") for n in names)
    backfill = _prepared(tmp_path)
    report = _report(tmp_path, backfill, known_readers=None)
    assert report["totals"]["eligible_files"] == 0 and set(report["unswitched_readers"]) == names


def test_rows_without_trade_date_are_counted_and_deleted_only_under_the_second_flag(tmp_path):
    _build(tmp_path)  # the 11-15 run file holds one row without a trade date
    backfill = bf.backfill(tmp_path, _state(), today=TODAY, apply=True, newest_run_on_conflict=True)
    refused = _report(tmp_path, backfill)
    assert refused["rows_without_trade_date"] == {"files": 1, "rows": 1}
    entry = next(e for p in refused["partitions"].values() for e in p["files"] if e["undated_rows"])
    assert entry["undated_rows"] == 1 and "rows_without_trade_date:1" in entry["refused"] and not entry["eligible"]
    allowed = _report(tmp_path, backfill, delete_files_with_undated_rows=True)
    entry = next(e for p in allowed["partitions"].values() for e in p["files"] if e["undated_rows"])
    assert entry["eligible"] and allowed["delete_files_with_undated_rows"] is True
    assert allowed["digest"] != refused["digest"]  # the flag is part of what the operator read
    res = cl.execute(tmp_path, allowed, expected_digest=allowed["digest"])
    assert entry["path"] in res["deleted"]


# --- keyed datasets (SOURCE-DATASETS-KEYED-WRITE-20261119: the 13 datasets) -----------------------------------
KEYED = "tw_institutional_flow"


def _flow(symbol, day, cls, net, run="r"):
    return {"source_id": f"{run}-{symbol}-{cls}", "metadata": {"market": "TW", "normalized_row": {
        "symbol": symbol, "trade_date": day, "investor_class": cls, "net": net}}}


def _keyed_prepared(tmp_path, undated=False, skip_keyed=()):
    from services.source_ingestion.daily_bars_store import KEYED_DATASETS, KeyedDatasetStore

    rows = [_flow("2330", "2026-11-12", "foreign", 5), _flow("2330", "2026-11-12", "trust", 1),
            _flow("2317", "2026-11-13", "foreign", -2)]
    d = tmp_path / "normalized" / KEYED / "date=2026-11-14"
    d.mkdir(parents=True)
    lines = [json.dumps(r) for r in rows]
    if undated:
        lines.append(json.dumps({"source_id": "x", "metadata": {"normalized_row": {"symbol": "1101", "investor_class": "foreign"}}}))
    (d / "run-a.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw = tmp_path / "raw" / "c" / KEYED / "date=2026-11-14"
    raw.mkdir(parents=True)
    with gzip.open(raw / "run-a.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    KeyedDatasetStore(tmp_path, KEYED_DATASETS[KEYED]).write_records([r for r in rows if r["source_id"] not in skip_keyed])
    return rows


def _keyed_report(tmp_path, **kw):
    kw.setdefault("today", LATER)
    kw.setdefault("reader_evidence", OK_READERS)
    kw.setdefault("known_readers", ())
    kw.setdefault("dataset", KEYED)
    return cl.build_report(tmp_path, **kw)


def test_keyed_dataset_dry_run_reports_and_deletes_only_that_dataset(tmp_path):
    _keyed_prepared(tmp_path)
    other = tmp_path / "normalized" / "tw_margin_short_balance" / "date=2026-11-14"
    other.mkdir(parents=True)
    (other / "run-a.jsonl").write_text(json.dumps(_flow("2330", "2026-11-12", "x", 1)) + "\n")
    price = tmp_path / "normalized" / "tw_price_daily" / "date=2026-11-14"
    price.mkdir(parents=True)
    (price / "run-a.jsonl").write_text(_line("2330", "2026-11-12", 1.0) + "\n")
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    report = _keyed_report(tmp_path)
    assert report["dataset"] == KEYED and report["totals"]["files"] == 2 and report["totals"]["eligible_files"] == 2
    assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == before  # dry run
    res = cl.execute(tmp_path, report, expected_digest=report["digest"])
    assert res["deleted_files"] == 2 and all(f"/{KEYED}/" in x for x in res["deleted"])
    assert other.is_dir() and price.is_dir() and (tmp_path / "keyed" / KEYED).is_dir()


@pytest.mark.parametrize("dataset", sorted(__import__("services.source_ingestion.daily_bars_store", fromlist=["K"]).KEYED_DATASETS))
def test_every_keyed_dataset_is_accepted_and_the_default_list_blocks_nothing_it_should_not(tmp_path, dataset):
    report = cl.build_report(tmp_path, today=LATER, dataset=dataset, reader_evidence=OK_READERS)
    assert report["dataset"] == dataset and report["totals"]["files"] == 0
    assert report["unswitched_readers"] == []  # the three base routed readers are switched


def test_unknown_dataset_is_refused_with_the_reason(tmp_path):
    with pytest.raises(cl.CleanupRefused, match="not tw_price_daily or one of the keyed datasets"):
        cl.build_report(tmp_path, today=LATER, dataset="tw_price_1m")
    assert cl.main(["--root", str(tmp_path), "--today", LATER, "--dataset", "tw_price_1m"]) == 2


def test_keyed_refuses_when_the_keyed_store_does_not_hold_the_keys(tmp_path):
    _keyed_prepared(tmp_path, skip_keyed=("r-2330-trust",))
    report = _keyed_report(tmp_path)
    reasons = _reasons(report)
    assert report["totals"]["eligible_files"] == 0
    assert all(any(x.startswith("keys_not_in_keyed_store:1") for x in v) for v in reasons.values())


def test_keyed_refuses_unverified_partition_young_files_pins_and_missing_evidence(tmp_path):
    _keyed_prepared(tmp_path)
    manifest = tmp_path / "keyed" / KEYED / "_manifest" / "date=2026-11-12.json"
    good = manifest.read_text()
    manifest.write_text(good.replace('"row_count":2', '"row_count":9'))
    assert any(x.startswith("keyed_partition_not_verified:2026-11-12") for v in _reasons(_keyed_report(tmp_path)).values() for x in v)
    manifest.write_text(good)
    assert _keyed_report(tmp_path)["totals"]["eligible_files"] == 2
    assert _keyed_report(tmp_path, today="2026-11-20")["totals"]["eligible_files"] == 0  # inside the window
    assert any(x == "pinned:2026-11-12" for v in _reasons(_keyed_report(tmp_path, pinned_dates=["2026-11-12"])).values() for x in v)
    assert any(x == "no_reader_evidence" for v in _reasons(_keyed_report(tmp_path, reader_evidence=None)).values() for x in v)
    (tmp_path / "keyed" / KEYED / "_manifest" / "date=2026-11-13.json").unlink()
    assert any("no_manifest" in x for v in _reasons(_keyed_report(tmp_path)).values() for x in v)


def test_keyed_undated_rows_are_counted_and_need_the_second_flag(tmp_path):
    _keyed_prepared(tmp_path, undated=True)
    refused = _keyed_report(tmp_path)
    assert refused["rows_without_trade_date"] == {"files": 1, "rows": 1}
    assert refused["totals"]["eligible_files"] == 1  # the raw copy has none
    allowed = _keyed_report(tmp_path, delete_files_with_undated_rows=True)
    assert allowed["totals"]["eligible_files"] == 2


def test_keyed_cli_needs_the_digest_and_never_deletes_by_default(tmp_path):
    _keyed_prepared(tmp_path)
    (tmp_path / "ev.json").write_text(json.dumps(OK_READERS))
    base = ["--root", str(tmp_path), "--today", LATER, "--dataset", KEYED, "--reader-evidence", str(tmp_path / "ev.json")]
    files = sorted(tmp_path.rglob("*.jsonl*"))
    assert cl.main(base) == 0 and sorted(tmp_path.rglob("*.jsonl*")) == files
    assert cl.main(base + ["--execute"]) == 2 and sorted(tmp_path.rglob("*.jsonl*")) == files
    assert cl.main(base + ["--execute", "--report-digest", "0" * 64]) == 2
    # the checked in reader list is satisfied for keyed datasets, the evidence gate is what the operator provides
    report = _keyed_report(tmp_path, known_readers=None)
    assert cl.main(base + ["--execute", "--report-digest", report["digest"]]) == 0
    assert not [p for p in tmp_path.rglob("*.jsonl*") if "keyed" not in p.parts and KEYED in str(p)]


def test_execute_refuses_a_path_outside_the_chosen_dataset(tmp_path):
    _keyed_prepared(tmp_path)
    report = _keyed_report(tmp_path)
    victim = tmp_path / "normalized" / "tw_price_daily" / "date=2026-11-14"
    victim.mkdir(parents=True)
    (victim / "run-a.jsonl").write_text("x\n")
    entry = next(iter(report["partitions"].values()))["files"][0]
    entry["path"] = "normalized/tw_price_daily/date=2026-11-14/run-a.jsonl"
    entry["bytes"] = 2
    report["digest"] = cl._digest(report)
    with pytest.raises(cl.CleanupRefused, match="is not a run file of tw_tw|is not a run file of tw_institutional_flow"):
        cl.execute(tmp_path, report, expected_digest=report["digest"])
    assert (victim / "run-a.jsonl").exists()


@pytest.mark.parametrize("dataset", sorted(__import__("services.source_ingestion.daily_bars_store", fromlist=["K"]).KEYED_DATASETS))
def test_cleanup_of_each_of_the_13_datasets_after_a_real_dual_write(tmp_path, dataset, monkeypatch):
    """The generated rows go through the real writer (dual write is the default), the dry run finds the run files of
    that dataset only, the real run deletes exactly them, and the keyed store still gives every reader the rows."""
    from services.source_ingestion.daily_bars_store import KeyedDatasetStore, KEYED_DATASETS, keyed_dataset_dir
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter, normalized_dataset_base
    from services.source_ingestion.tests.test_datasets_keyed_write import _connector, _rec, _result

    monkeypatch.delenv("PANTHEON_KEYED_DATASETS_WRITE", raising=False)  # default: dual
    writer = MarketDataStorageWriter(tmp_path)
    writer.write_run(result=_result([_rec(dataset, i) for i in range(6)], "run-1"), connector=_connector())
    for other in sorted(KEYED_DATASETS):
        if other != dataset:  # a neighbour that must stay untouched
            writer.write_run(result=_result([_rec(other, 0)], "run-n"), connector=_connector())
            break
    run_files = [p for p in tmp_path.rglob("*.jsonl*") if dataset in p.parts and "keyed" not in p.parts]
    assert run_files and any("raw" in p.parts for p in run_files) and any("normalized" in p.parts for p in run_files)
    report = cl.build_report(tmp_path, today=LATER, dataset=dataset, reader_evidence=OK_READERS)
    assert report["totals"]["files"] == len(run_files) and report["totals"]["eligible_files"] == len(run_files), _reasons(report)
    assert report["unswitched_readers"] == [] and report["rows_without_trade_date"] == {"files": 0, "rows": 0}
    before_keyed = sorted(p.read_bytes() for p in keyed_dataset_dir(tmp_path, dataset).rglob("part-*"))
    res = cl.execute(tmp_path, report, expected_digest=report["digest"])
    assert sorted(res["deleted"]) == sorted(p.relative_to(tmp_path).as_posix() for p in run_files)
    assert not [p for p in tmp_path.rglob("*.jsonl*") if dataset in p.parts and "keyed" not in p.parts]
    assert [p for p in tmp_path.rglob("*.jsonl*") if "normalized" in p.parts and dataset not in p.parts]  # neighbour kept
    assert sorted(p.read_bytes() for p in keyed_dataset_dir(tmp_path, dataset).rglob("part-*")) == before_keyed
    store = KeyedDatasetStore(tmp_path, KEYED_DATASETS[dataset])
    assert sum(len(store.records(p)) for p in store.partitions()) == 6
    assert normalized_dataset_base(tmp_path, dataset, env={"PANTHEON_KEYED_DATASET_READER": "keyed"}).is_dir()
