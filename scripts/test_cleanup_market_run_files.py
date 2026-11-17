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
    backfill = bf.backfill(tmp_path, _state(), today=TODAY, apply=True)
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
    backfill = bf.backfill(tmp_path, _state(), today=TODAY, apply=True)
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
    assert readers and any(not r["switched"] for r in readers)
    report = _report(tmp_path, backfill, known_readers=readers)  # the checked in list
    assert report["totals"]["eligible_files"] == 0 and report["totals"]["eligible_bytes"] == 0
    for r in readers:
        if not r["switched"]:
            assert all(f"unswitched_reader:{r['file']}" in x for x in _reasons(report).values())
    assert sorted(report["unswitched_readers"]) == sorted(r["file"] for r in readers if not r["switched"])
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


def test_known_readers_list_matches_the_code():
    root = Path(__file__).resolve().parents[1]
    for r in cl.KNOWN_RUN_FILE_READERS:
        text = (root / r["file"]).read_text(encoding="utf-8")
        # an unswitched reader still reads run files; a switched one must have dropped the marker
        assert (r["marker"] in text) is (not r["switched"]), r["file"]
