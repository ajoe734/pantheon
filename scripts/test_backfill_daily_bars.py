from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from scripts import backfill_daily_bars as bf
from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.daily_bars_reader import DailyBarsReader
from services.source_ingestion.daily_bars_store import DailyBarsStore
from services.source_ingestion.security_listing import ListingState

TODAY = "2026-11-17"


def _state(today=TODAY):
    st = ListingState(last_success_date=today)

    def add(sym, cfi, warrant, last_seen=today):
        st.allowlist[sym] = {"venue": "TWSE", "category": "x", "cfi": cfi, "is_warrant": warrant,
                             "first_seen": "2026-01-01", "last_seen": last_seen}

    for sym in ("2330", "2317", "1101"):
        add(sym, "ESVUFR", False)
    add("030001", "RWSCCA", True)  # listed warrant: carried
    add("030002", "RWSCCA", True, last_seen="2026-11-10")  # expired warrant: not carried
    st.ordinary_share_registry["1234"] = {"venue": "TWSE", "first_seen": "2020-01-01"}  # delisted share: carried
    return st


def _bf(root, state, **kw):
    kw.setdefault("today", TODAY)
    # the fixture re-ingests 2330 on 11-13 with another close: these tests exercise the newest-run rule, which
    # is now the named flag (the default blocks such a date, see the conflict tests below)
    kw.setdefault("newest_run_on_conflict", True)
    return bf.backfill(root, state, **kw)


def _line(symbol, trade_date, close, run="r", key="trade_date", **extra):
    body = {"symbol": symbol, "close": close, "open": close, "high": close, "low": close, "volume": 1000.0, **extra}
    if trade_date is not None:
        body[key] = trade_date
    return json.dumps({"source_id": f"{run}-{symbol}", "metadata": {"market": "TW", "normalized_row": body}})


def _write(root, write_date, run, lines):
    d = root / "normalized" / "tw_price_daily" / f"date={write_date}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build(root):
    # write date 11-15 holds trade dates 11-12 and 11-13 (partition name is NOT the trade date)
    _write(root, "2026-11-15", "run-a", [
        _line("2330", "2026-11-12", 100.0), _line("2317", "2026-11-12", 50.0),
        _line("2330", "2026-11-13", 101.0), _line("030002", "2026-11-13", 1.0),
        _line("030001", "2026-11-13", 2.0), _line("1234", "2026-11-12", 7.0),
        _line("1101", None, 9.0),  # no trade date: counted, not written
    ])
    # a later re-ingest of 11-13 with a corrected 2330 close: the newest copy wins
    _write(root, "2026-11-16", "run-b", [_line("2330", "2026-11-13", 111.0, run="b"), _line("2317", "2026-11-13", 51.0, run="b")])


def _bytes(root):
    out = {}
    for p in sorted((root / "daily_bars").rglob("*")):
        if p.is_file() and p.name != ".lock":
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_dry_run_reports_and_writes_nothing(tmp_path):
    _build(tmp_path)
    report = _bf(tmp_path, _state())
    assert not (tmp_path / "daily_bars").exists()
    assert report["source"]["rows_without_trade_date"] == 1
    d13 = report["days"]["2026-11-13"]
    assert (d13["rows"], d13["unique"], d13["duplicates"]) == (5, 4, 1)
    assert d13["classes"]["delete"] == 1  # expired warrant
    assert d13["expected_rows"] == 3  # 2330, 2317, listed warrant 030001


def test_trade_date_from_row_and_newest_wins_and_classes(tmp_path):
    _build(tmp_path)
    report = _bf(tmp_path, _state(), apply=True)
    assert sorted(report["days"]) == ["2026-11-12", "2026-11-13"]  # never 11-15 / 11-16 (write dates)
    assert report["mismatches"] == []
    rows = DailyBarsStore(tmp_path)._read_day_rows(DailyBarsStore(tmp_path).read_manifest("2026-11-13"))
    by_symbol = {k[1]: v for k, v in rows.items()}
    assert by_symbol["2330"]["close"] == 111.0
    assert set(by_symbol) == {"2330", "2317", "030001"}  # expired 030002 not carried
    d12 = DailyBarsStore(tmp_path)._read_day_rows(DailyBarsStore(tmp_path).read_manifest("2026-11-12"))
    assert {k[1] for k in d12} == {"2330", "2317", "1234"}  # delisted share carried
    assert report["classes"]["keep_delisted_share"] == 1


def test_second_run_writes_nothing_bytes_and_digests(tmp_path):
    _build(tmp_path)
    first = _bf(tmp_path, _state(), apply=True)
    before = _bytes(tmp_path)
    second = _bf(tmp_path, _state(), apply=True)
    assert _bytes(tmp_path) == before
    assert all(not d["written"] for d in second["days"].values())
    assert {k: v["content_digest"] for k, v in first["days"].items()} == {k: v["content_digest"] for k, v in second["days"].items()}


def test_kill_in_the_middle_then_resume(tmp_path):
    _build(tmp_path)
    calls = {"n": 0}

    def kill(step):
        if step == "before_manifest_rename":
            calls["n"] += 1
            if calls["n"] == 2:  # first date published, second date killed before its manifest
                raise KeyboardInterrupt("killed")

    with pytest.raises(KeyboardInterrupt):
        _bf(tmp_path, _state(), apply=True, store=DailyBarsStore(tmp_path, fault=kill))
    store = DailyBarsStore(tmp_path)
    published = sorted(p.name for p in store.manifest_dir.glob("date=*.json"))
    assert published == ["date=2026-11-12.json"]
    assert store.verify_day("2026-11-12")["verified"]  # every published date is complete
    residue = [p for p in (tmp_path / "daily_bars").rglob("*")
               if p.is_file() and p.name != ".lock" and "date=2026-11-12" not in p.as_posix()]
    assert residue and all("date=2026-11-13" in p.as_posix() for p in residue)  # one trade date at most

    report = _bf(tmp_path, _state(), apply=True)
    assert report["swept"]["temp_files"] >= 1 and report["swept"]["orphan_parts"] >= 1
    assert not [p for p in (tmp_path / "daily_bars").rglob("*") if p.is_file() and ".tmp" in p.name]
    assert report["mismatches"] == [] and len(report["days"]) == 2
    assert DailyBarsStore(tmp_path).verify_day("2026-11-13")["verified"]


def test_mismatch_fails_and_names_the_date(tmp_path, monkeypatch):
    _build(tmp_path)
    real = DailyBarsStore.verify_day

    def bad(self, trade_date):
        out = real(self, trade_date)
        return {**out, "verified": False} if trade_date == "2026-11-13" else out

    monkeypatch.setattr(DailyBarsStore, "verify_day", bad)
    with pytest.raises(bf.BackfillMismatch) as exc:
        _bf(tmp_path, _state(), apply=True)
    assert exc.value.dates == ["2026-11-13"] and "2026-11-13" in str(exc.value)


@pytest.mark.parametrize("last", [None, "2026-11-01"])
def test_unreadable_or_stale_listing_stops_before_any_write(tmp_path, last):
    _build(tmp_path)
    state = _state()
    state.last_success_date = last
    with pytest.raises(bf.BackfillRefused, match="listing is not fresh"):
        _bf(tmp_path, state, apply=True)
    assert not (tmp_path / "daily_bars").exists()


def test_hosted_shaped_backfill_stays_inside_the_byte_limits(tmp_path):
    """3400 symbols per day, 3 trade dates through 2 write dates; day bytes stay under the section 6 bound."""
    state = _state()
    for i in range(3400):
        state.allowlist[f"{2000 + i}"] = {"venue": "TWSE", "category": "x", "cfi": "ESVUFR", "is_warrant": False,
                                          "first_seen": "2026-01-01", "last_seen": TODAY}
    lines = lambda day, c: [_line(f"{2000 + i}", day, c + i % 50) for i in range(3400)]  # noqa: E731
    _write(tmp_path, "2026-11-15", "a", lines("2026-11-11", 10.0) + lines("2026-11-12", 11.0))
    _write(tmp_path, "2026-11-16", "b", lines("2026-11-13", 12.0) + lines("2026-11-12", 11.0))  # duplicates of 11-12
    report = _bf(tmp_path, state, apply=True)
    assert report["days"]["2026-11-12"]["duplicates"] == 3400
    assert all(d["manifest_rows"] == 3400 and d["verified"] for d in report["days"].values())
    assert report["budget"]["daily_bars_max_day_bytes"] <= dbs.DAILY_BARS_DAY_TARGET_BYTES


def test_day_over_the_limit_fails_on_bytes(tmp_path, monkeypatch):
    _build(tmp_path)
    monkeypatch.setattr(bf, "DAILY_BARS_DAY_ALARM_BYTES", 1)
    with pytest.raises(dbs.DailyBarsBudgetExceeded):
        _bf(tmp_path, _state(), apply=True)


def test_cli_dry_run_writes_report(tmp_path):
    _build(tmp_path)
    state_path = tmp_path / "listing.json"
    _state().save(state_path)
    out = tmp_path / "report.json"
    assert bf.main(["--root", str(tmp_path), "--listing-state", str(state_path), "--today", TODAY, "--report", str(out)]) == 0
    assert json.loads(out.read_text())["apply"] is False
    assert bf.main(["--root", str(tmp_path), "--listing-state", str(state_path), "--today", "2026-12-30"]) == 2


# --- SOURCE-DAILY-BARS-READERS-COMPLETE-20261118: duplicate conflicts (gap G5) ---------------------------------
def _conflict_root(root):
    _write(root, "2026-11-15", "run-a", [
        _line("2330", "2026-11-12", 100.0), _line("2317", "2026-11-12", 50.0),   # 11-12: clean
        _line("2330", "2026-11-13", 101.0), _line("2317", "2026-11-13", 51.0),   # 11-13: price conflict (2330)
        _line("2330", "2026-11-16", 120.0), _line("2317", "2026-11-16", 60.0),   # 11-16: volume only (2317)
    ])
    _write(root, "2026-11-16", "run-b", [
        _line("2330", "2026-11-12", 100.0), _line("2330", "2026-11-13", 111.0, run="b"),
        _line("2317", "2026-11-16", 60.0, volume=2000.0)])


def test_conflicting_copies_are_counted_listed_and_the_date_is_not_published(tmp_path):
    _conflict_root(tmp_path)
    report = _bf(tmp_path, _state(), apply=True, newest_run_on_conflict=False)
    assert report["conflicts"]["price_keys"] == 1 and report["conflicts"]["volume_only_keys"] == 1
    assert report["conflicts"]["blocked_dates"] == ["2026-11-13", "2026-11-16"]
    day = report["days"]["2026-11-13"]
    assert day["conflicts"]["price"] == ["2330"] and day["conflicts"]["volume_only"] == []
    assert day["published_blocked"] is True and day["written"] is False
    assert report["days"]["2026-11-16"]["conflicts"]["volume_only"] == ["2317"]
    assert report["days"]["2026-11-12"]["conflicts"]["price_keys"] == 0 and report["days"]["2026-11-12"]["written"] is True
    store = DailyBarsStore(tmp_path)
    assert store.read_manifest("2026-11-12") is not None
    assert store.read_manifest("2026-11-13") is None and store.read_manifest("2026-11-16") is None
    assert report["mismatches"] == []  # a blocked date is not a mismatch, it is not published


def test_the_named_flag_publishes_the_newest_run_and_still_reports_the_keys(tmp_path):
    _conflict_root(tmp_path)
    report = _bf(tmp_path, _state(), apply=True, newest_run_on_conflict=True)
    assert report["conflicts"]["blocked_dates"] == []
    assert report["conflicts"]["published_with_flag"] == ["2026-11-13", "2026-11-16"]
    assert report["days"]["2026-11-13"]["conflicts"]["price"] == ["2330"] and report["days"]["2026-11-13"]["written"] is True
    bars = DailyBarsReader(tmp_path).read_range("2330", "2026-11-13", "2026-11-13")
    assert [b["close"] for b in bars] == [111.0]  # the newest copy


def test_identical_copies_are_not_conflicts(tmp_path):
    _write(tmp_path, "2026-11-15", "a", [_line("2330", "2026-11-12", 100.0), _line("2317", "2026-11-12", 50.0)])
    _write(tmp_path, "2026-11-16", "b", [_line("2330", "2026-11-12", 100.0)])
    report = _bf(tmp_path, _state(), apply=True, newest_run_on_conflict=False)
    assert report["days"]["2026-11-12"]["duplicates"] == 1
    assert report["conflicts"] == {"price_keys": 0, "volume_only_keys": 0, "blocked_dates": [], "published_with_flag": []}


def test_cli_reports_blocked_dates_and_exits_3_on_apply(tmp_path, capsys):
    _conflict_root(tmp_path)
    state_path = tmp_path / "listing.json"
    _state().save(state_path)
    base = ["--root", str(tmp_path), "--listing-state", str(state_path), "--today", TODAY]
    assert bf.main(base + ["--report", str(tmp_path / "r.json")]) == 0  # dry run: report only
    assert json.loads((tmp_path / "r.json").read_text())["conflicts"]["blocked_dates"] == ["2026-11-13", "2026-11-16"]
    assert bf.main(base + ["--apply"]) == 3
    assert "conflicting duplicates and were not published" in capsys.readouterr().err
    assert bf.main(base + ["--apply", "--newest-run-on-conflict"]) == 0
    assert DailyBarsStore(tmp_path).read_manifest("2026-11-13") is not None


# ---------------------------------------------------------------------------------------------
# Locally generated, deterministic shaped copy of the normalized tw_price_daily run files (NOT taken
# from the hosted machine). ``python -m scripts.test_backfill_daily_bars generate --root R`` writes it;
# ``measure`` runs backfill, comparison, reader equivalence, latency and size on it. Not a pytest test.
# ---------------------------------------------------------------------------------------------
SHAPE_SEED = 20261118
SHAPE_CLASSES = (  # (class, count per date, symbol base, listing registration)
    ("ordinary_listed", 1500, 1000), ("delisted_ordinary", 60, 5000),
    ("warrant_listed", 1000, 30000), ("warrant_expired", 840, 60000))


def shaped_listing(today: str = TODAY) -> ListingState:
    st = ListingState(last_success_date=today)
    for name, count, base in SHAPE_CLASSES:
        for i in range(count):
            code = f"{base + i:04d}" if base < 10000 else f"{base + i:06d}"
            if name == "delisted_ordinary":
                st.ordinary_share_registry[code] = {"venue": "TWSE", "first_seen": "2015-01-01"}
            else:
                warrant = name.startswith("warrant")
                st.allowlist[code] = {"venue": "TWSE", "category": "x", "cfi": "RWSCCA" if warrant else "ESVUFR",
                                      "is_warrant": warrant, "first_seen": "2025-01-01",
                                      "last_seen": "2026-06-01" if name == "warrant_expired" else today}
    return st


def _shape_codes():
    out = []
    for name, count, base in SHAPE_CLASSES:
        out += [f"{base + i:04d}" if base < 10000 else f"{base + i:06d}" for i in range(count)]
    return out


def trading_days(n: int, last: str = "2026-11-13"):
    from datetime import date, timedelta

    day, out = date.fromisoformat(last), []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(out)


def generate_shaped_copy(root, *, write_dates=150, trade_dates=150, seed=SHAPE_SEED, row_pad=580,
                         no_date_per_run=25, dup_fraction=0.1):
    """Normalized run files named by write date. Trade dates are spread over the write dates (a write date
    holds ``trade_dates / write_dates`` of them, as a monthly capture does); about ``dup_fraction`` of the
    rows are re-ingested by a later run (copies of one symbol and trade date, newer close); a few rows per
    run carry no trade date. Returns the facts of what was written."""
    import random
    from datetime import date, timedelta

    rng = random.Random(seed)
    root = Path(root)
    codes = _shape_codes()
    days = trading_days(trade_dates)
    per_write = max(1, -(-trade_dates // write_dates))
    first = date(2026, 11, 14) - timedelta(days=write_dates)
    facts = {"seed": seed, "write_dates": write_dates, "trade_dates": trade_dates, "symbols_per_date": len(codes),
             "rows": 0, "bytes": 0, "rows_without_trade_date": 0, "duplicate_rows": 0, "run_files": 0}
    pad = "x" * row_pad

    def line(code, day, close, run, avail, volume=None):
        body = {"symbol": code, "date": day, "open": close, "high": round(close * 1.01, 2), "low": round(close * 0.99, 2),
                "close": close, "volume": volume if volume is not None else float(rng.randrange(1000, 90000000)), "value": round(close * 1000.0, 2),
                "available_time": avail}
        meta = {"market": "TW", "symbol": code, "date": day, "available_time": avail, "body": json.dumps(body),
                "normalized_row": {**body, "symbol_canonical": f"{code}.TW", "venue": "TWSE", "trade_date": day},
                "collector": {"source_id": "twse_stock_day_all", "upstream_endpoint": pad[:60], "run": run},
                "raw_payload": pad}
        return json.dumps({"source_id": f"{run}-{code}", "metadata": meta})

    later: list[tuple[str, str, float, float]] = []
    for w in range(write_dates):
        wd = (first + timedelta(days=w)).isoformat()
        run = f"run-{w:03d}"
        lines = []
        for day in days[w * per_write:(w + 1) * per_write]:
            avail = f"{day}T14:30:00+08:00"
            for code in codes:
                close = round(10 + rng.random() * 900, 2)
                volume = float(rng.randrange(1000, 90000000))
                lines.append(line(code, day, close, run, avail, volume))
                if rng.random() < dup_fraction:
                    later.append((code, day, close, volume))
        redo, later = later[: len(later) // 2], later[len(later) // 2:]
        for code, day, close, volume in redo:  # a later run re-captures rows of earlier trade dates: same
            # OHLC, a larger (final) volume, so the old read (largest volume) and newest-wins agree
            lines.append(line(code, day, close, run, f"{day}T14:30:00+08:00", volume + 1000.0))
            facts["duplicate_rows"] += 1
        for code in rng.sample(codes, no_date_per_run):
            lines.append(line(code, None, 1.0, run, f"{wd}T14:30:00+08:00"))
            facts["rows_without_trade_date"] += 1
        d = root / "normalized" / "tw_price_daily" / f"date={wd}"
        d.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines) + "\n"
        (d / f"{run}.jsonl").write_text(text, encoding="utf-8")
        facts["rows"] += len(lines)
        facts["bytes"] += len(text.encode())
        facts["run_files"] += 1
    return facts


def _percentile(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]


def _bare(symbol):
    return str(symbol).split(".")[0]


def _timed(fn):
    import time

    t = time.perf_counter()
    value = fn()
    return value, round(time.perf_counter() - t, 3)


def measure_equivalence(root):  # pragma: no cover - measurement driver
    """Old (run file) read against the daily bars read, both settings, on the same root."""
    import os

    from services.research.alpha_templates import evaluator as ev
    from services.source_ingestion import candidate_pool_market_feed as feed
    from services.source_ingestion import market_data_storage as mds

    env = mds.DAILY_PRICE_READER_ENV
    inputs = [{"name": "price", "dataset": "tw_price_daily", "field": "close"},
              {"name": "turnover", "dataset": "tw_price_daily", "field": "volume"}]
    out = {"root_kind": "locally generated shaped copy", "evaluator": {}, "feed": {}, "depth": {}}
    got = {}
    for mode in ("run_files", "daily_bars"):
        os.environ[env] = mode
        uni, sec = _timed(lambda: ev.load_tw_price_daily_snapshot(root, min_bars=1))
        mkt, fsec = _timed(lambda: feed.build_candidate_pool_market(root, inputs))
        depth, dsec = _timed(lambda: mds.measure_stored_history_depth(root))
        got[mode] = (uni, mkt, depth)
        out["evaluator"][f"{mode}_seconds"] = sec
        out["feed"][f"{mode}_build_seconds"] = fsec
        out["depth"][f"{mode}_seconds"] = dsec
    os.environ.pop(env, None)
    old_u, new_u = ({_bare(k): v for k, v in got[m][0].items()} for m in ("run_files", "daily_bars"))
    fields = ("open", "high", "low", "close", "volume")

    def cmp_eval(last):
        bad, compared, vwap_diff, old_only = [], 0, 0, 0
        for sym, new in new_u.items():
            old = old_u.get(sym)
            if old is None:
                bad.append((sym, "missing_in_old"))
                continue
            # the old read also turns a row WITHOUT a trade date into a bar (key "None", stamped by its
            # available_time); daily bars never carry it. Those bars are counted, not compared.
            # such a bar has the day key "None", which sorts after every date: it is the last element, and
            # there is at most one per symbol (the key collapses them)
            extra = len(old["decision_times"]) - len(new["decision_times"])
            if extra not in (0, 1):
                bad.append((sym, f"series_length_differs_by_{extra}"))
                continue
            keep = list(range(len(old["decision_times"]) - extra))
            old_only += extra
            sl = slice(-last, None) if last else slice(None)
            o_times = [old["decision_times"][i] for i in keep]
            if o_times[sl] != new["decision_times"][sl]:
                bad.append((sym, "decision_times"))
            for f in fields:
                compared += 1
                if [old["features"][f][i] for i in keep][sl] != new["features"][f][sl]:
                    bad.append((sym, f))
            vwap_diff += [old["features"]["vwap"][i] for i in keep][sl] != new["features"]["vwap"][sl]
        return {"symbols_compared": len(new_u), "field_series_compared": compared, "mismatches": len(bad),
                "first_mismatches": bad[:5], "old_only_bars_from_rows_without_trade_date": old_only,
                "vwap_series_differing_known_gap_G1": vwap_diff}

    out["evaluator"].update({
        "symbols_run_files": len(old_u), "symbols_daily_bars": len(new_u),
        "only_in_run_files": len(set(old_u) - set(new_u)),
        "only_in_run_files_are_expired_warrants_only": all(
            s in {f"{60000 + i:06d}" for i in range(840)} for s in set(old_u) - set(new_u)),
        "compare_5_trading_days": cmp_eval(5), "compare_full_range": cmp_eval(0)})

    def feed_syms(mode):
        return {_bare(k): v["bars"] for k, v in got[mode][1]["symbols"].items()}

    old_f, new_f = feed_syms("run_files"), feed_syms("daily_bars")

    def cmp_feed(last):
        bad = 0
        first = []
        for sym, nb in new_f.items():
            ob = old_f.get(sym)
            if ob is None:
                bad += 1
                continue
            sl = slice(-last, None) if last else slice(None)
            for a_, b_ in zip(ob[sl], nb[sl]):
                for f in ("event_time", "close", "open", "high", "low", "volume", "suspended"):
                    if a_.get(f) != b_.get(f):
                        bad += 1
                        first.append((sym, f))
            if len(ob[sl]) != len(nb[sl]):
                bad += 1
        return {"symbols_compared": len(new_f), "mismatches": bad, "first_mismatches": first[:5]}

    out["feed"].update({
        "symbols_run_files": len(old_f), "symbols_daily_bars": len(new_f),
        "sessions_equal": got["run_files"][1]["trading_sessions"] == got["daily_bars"][1]["trading_sessions"],
        "compare_5_trading_days": cmp_feed(5), "compare_full_range": cmp_feed(0)})
    do, dn = got["run_files"][2], got["daily_bars"][2]
    keys = [k for k in do.get("datasets", {}) if k == "tw_price_daily"]
    out["depth"]["tw_price_daily_run_files"] = {k: do["datasets"][k] for k in keys}
    out["depth"]["tw_price_daily_daily_bars"] = {k: dn["datasets"][k] for k in keys}
    return out


def measure_latency(root, reps=21):  # pragma: no cover - measurement driver
    import os

    from services.research.alpha_templates import evaluator as ev
    from services.source_ingestion.daily_bars_reader import DailyBarsReader
    from services.source_ingestion.daily_bars_store import DailyBarsStore

    root = Path(root)
    dates = DailyBarsReader(root).trade_dates()
    symbols = sorted(DailyBarsStore(root).symbol_stats())
    out = {"trade_dates": len(dates), "symbols": len(symbols), "reps": reps, "loadavg_before": os.getloadavg()}

    def run(label, fn):
        fn()  # first call opens parquet metadata: reported separately, not part of the warm figures
        times = [_timed(fn)[1] for _ in range(reps - 1)]
        out[label] = {"warm_runs": len(times), "p50_s": _percentile(times, 0.5), "p95_s": _percentile(times, 0.95),
                      "max_s": max(times), "loadavg_after": os.getloadavg()}

    reader = DailyBarsReader(root)
    one, two_years = symbols[len(symbols) // 2], (dates[-504], dates[-1])
    run("one_symbol_2_years_reader", lambda: reader.read_range(one, *two_years))
    run("one_symbol_2_years_switched_evaluator_path", lambda: ev._read_daily_bars_rows(root, [one]))
    many = symbols[:500]
    run("500_symbols_250_days_reader", lambda: reader.read_range(many, dates[-250], dates[-1]))
    run("500_symbols_250_days_switched_evaluator_path", lambda: ev._read_daily_bars_rows(root, many))
    # breakdown per partition for the 1 symbol x 2 years request
    manifest_files = {}
    import time

    t = time.perf_counter()
    for d in dates[-504:]:
        reader._read_one_date(d, [one])
    out["breakdown_1_symbol_2_years"] = {
        "serial_all_dates_s": round(time.perf_counter() - t, 3),
        "per_partition_ms_p50": None, "per_partition_ms_p95": None}
    per = []
    for d in dates[-504:]:
        t = time.perf_counter()
        reader._read_one_date(d, [one])
        per.append((time.perf_counter() - t) * 1000)
    out["breakdown_1_symbol_2_years"]["per_partition_ms_p50"] = round(_percentile(per, 0.5), 3)
    out["breakdown_1_symbol_2_years"]["per_partition_ms_p95"] = round(_percentile(per, 0.95), 3)
    m = reader._manifest(dates[-1])
    out["breakdown_1_symbol_2_years"]["files_per_partition"] = len(m["files"])
    out["breakdown_1_symbol_2_years"]["partition_bytes"] = sum(f["bytes"] for f in m["files"])
    return out


def main(argv=None) -> int:  # pragma: no cover - measurement driver
    import argparse
    import os
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("generate", "backfill", "equiv", "latency"))
    ap.add_argument("--root", required=True)
    ap.add_argument("--write-dates", type=int, default=150)
    ap.add_argument("--trade-dates", type=int, default=150)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.step == "generate":
        out = generate_shaped_copy(a.root, write_dates=a.write_dates, trade_dates=a.trade_dates)
    elif a.step == "backfill":
        out = bf.backfill(a.root, shaped_listing(), today=TODAY, apply=True)
    elif a.step == "equiv":
        out = measure_equivalence(a.root)
    else:
        out = measure_latency(a.root)
    out["seconds"] = round(time.time() - t0, 1)
    out["loadavg"] = os.getloadavg()
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: v for k, v in out.items() if k not in ("days",)}, default=str)[:1500])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
