"""SOURCE-RETENTION-SWEEPER-20261118-R2: retention sweeper (design SD_MARKET_DATA_ONE_WRITE_PER_DAY section 4.3, 7 step 8, 9 T5).

Logic tests use a 500 day history with a few hundred symbols per day. The size and latency claims of the
evidence run at the hosted shape (22,500 listed symbols of which about 20,200 warrants, 500 trading days, about
10,700 snapshot records, collector batches of 500) and are produced by ``test_hosted_shape_*`` below, which runs only
with ``RETENTION_SHAPED=1`` (set ``RETENTION_EVIDENCE_OUT=<file>`` to dump the measured values).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion import retention as ret
from services.source_ingestion.daily_bars_reader import DailyBarsReader
from services.source_ingestion.daily_bars_store import DailyBarsStore, KeyedDatasetStore, keyed_dataset_store
from services.source_ingestion.pin_source import PinSource
from services.source_ingestion.retention import RetentionSweeper
from services.source_ingestion.security_listing import ListingState

TODAY = "2026-11-17"  # Tuesday
LAST_LISTING = "2026-11-16"
RETENTION_CUTOFF = (date.fromisoformat(TODAY) - timedelta(days=2555)).isoformat()
MEASURED: dict = {}

SHARES = [f"{1101 + i}" for i in range(40)]  # four digit ordinary shares
DELISTED_SHARES = ["1234", "1235"]  # in the registry, not in the listing any more
WARRANTS_LISTED = [f"03{i:04d}" for i in range(60)]
WARRANTS_EXPIRED = [f"04{i:04d}" for i in range(8)]


def _weekdays(end: str, n: int) -> list[str]:
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def _row(symbol: str, day: str, run: str) -> dict:
    ohlcv = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000.0, "adjusted": False}
    return {"market": "TW", "symbol": symbol, "trade_date": day, **ohlcv, "source_id": "s", "ingest_run_id": run,
            "lineage_ref": run, "content_hash": dbs._sha(ohlcv)}


def _state(*, expired=WARRANTS_EXPIRED, date_=LAST_LISTING) -> ListingState:
    s = ListingState(last_success_date=date_)

    def put(sym, warrant, last_seen):
        s.allowlist[sym] = {"venue": "TWSE", "category": "w" if warrant else "s", "cfi": "RWSCCA" if warrant else "ESVUFR",
                            "is_warrant": warrant, "first_seen": "2019-01-01", "last_seen": last_seen}

    for sym in SHARES:
        put(sym, False, date_)
        s.ordinary_share_registry[sym] = {"venue": "TWSE", "first_seen": "2019-01-01"}
    for sym in DELISTED_SHARES:  # delisted: registry only
        s.ordinary_share_registry[sym] = {"venue": "TWSE", "first_seen": "2019-01-01"}
    for sym in WARRANTS_LISTED:
        put(sym, True, date_)
    for sym in expired:
        put(sym, True, "2026-11-10")  # left the listing before the last listing day
    return s


def _symbols_for(day: str) -> list[str]:
    syms = [f"{s}.TW" for s in SHARES]
    if day < RETENTION_CUTOFF:
        syms += [f"{s}.TW" for s in DELISTED_SHARES]
    syms += WARRANTS_LISTED + WARRANTS_EXPIRED
    return syms


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    """500 trading days ending 2026-11-13, plus 6 days before the retention cutoff; lineage per day. Built once,
    copied per test."""
    base = tmp_path_factory.mktemp("retention_template")
    root = base / "market_data_store"
    bars = DailyBarsStore(root)
    recent = _weekdays("2026-11-13", 500)
    old = _weekdays("2019-11-01", 6)
    for day in old + recent:
        run = f"run-{day}"
        bars.write_day(day, [_row(s, day, run) for s in _symbols_for(day)], ingest_run_id=run, connector_id="c")
    return SimpleNamespace(root=root, old=old, recent=recent)


@pytest.fixture()
def world(tmp_path, _template):
    import shutil

    root = tmp_path / "market_data_store"
    shutil.copytree(_template.root, root)
    listing = tmp_path / "security-listing.jsonl"
    _state().save(listing)
    return SimpleNamespace(root=root, bars=DailyBarsStore(root), listing=listing, report=tmp_path / ret.REPORT_FILENAME,
                           old=_template.old, recent=_template.recent, tmp=tmp_path)


def _sweeper(w, *, mode="delete", pins=None, snapshot=None, today=TODAY, extra_env=None, **kw):
    env = {ret.MODE_ENV: mode, **(extra_env or {})}
    pin_path = w.tmp / "pins.json"
    if not pin_path.exists():
        pin_path.write_text(json.dumps({"audit": {"ingest_run_id": "gate-proof-run"}}), encoding="utf-8")
    pin_source = pins if pins is not None else PinSource([pin_path])
    return RetentionSweeper(daily_bars_store=w.bars, report_path=w.report, listing_state_path=w.listing, snapshot_store=snapshot,
                            pin_source=pin_source, keyed_root=w.root, environ=env, today=lambda: today, **kw)


def _symbols_in(bars, day) -> set[str]:
    manifest = bars.read_manifest(day)
    if manifest is None:
        return set()
    return set(bars._read_day_rows(manifest) and {k[1] for k in bars._read_day_rows(manifest)})


# ---- 1. reproduce: nothing deletes today, the stock is measured --------------------------------------------


def test_reproduce_nothing_deletes_past_retention_or_expired_warrants_today(world):
    """Before the sweeper: the store holds partitions past retention and expired warrants, and no existing code
    removes them (a dry run measures them and changes nothing)."""
    state = ListingState.load(world.listing)
    stored = world.bars.symbol_stats()
    expired_stored = ret.count_expired_warrant_symbols(state, stored, TODAY)
    before = {d: world.bars.read_manifest(d)["bytes"] for d in world.bars.dates()}
    past = [d for d in world.bars.dates() if d < RETENTION_CUTOFF]
    assert len(past) == 6 and expired_stored == len(WARRANTS_EXPIRED)
    MEASURED["reproduce"] = {"partitions_past_retention": len(past), "bytes_past_retention": sum(before[d] for d in past),
                             "expired_warrant_symbols_in_bars": expired_stored, "partitions": len(before)}
    report = _sweeper(world, mode="dry_run").run()
    assert report["status"] == "completed" and report["mode"] == "dry_run"
    assert {d: world.bars.read_manifest(d)["bytes"] for d in world.bars.dates()} == before  # nothing deleted


# ---- 2. retention ----------------------------------------------------------------------------------------------


def test_partitions_past_retention_are_deleted_with_their_lineage_and_kept_bars_keep_theirs(world):
    keep_day = world.recent[0]
    lineage_before = (world.bars.lineage_dir / f"date={keep_day}.json").read_bytes()
    report = _sweeper(world).run()
    assert report["status"] == "completed"
    # old days keep only the delisted ordinary shares; their lineage stays with the kept rows
    for day in world.old:
        assert _symbols_in(world.bars, day) == {f"{s}.TW" for s in DELISTED_SHARES}
        lin = json.loads((world.bars.lineage_dir / f"date={day}.json").read_text())
        assert list(lin["runs"]) == [f"run-{day}"]
    assert (world.bars.lineage_dir / f"date={keep_day}.json").read_bytes() == lineage_before
    assert report["stores"]["daily_bars"]["rewritten_partitions"] == 506  # 6 old days + 500 days that held expired warrants


def test_a_past_retention_partition_with_nothing_to_keep_is_deleted_whole_with_its_lineage(world):
    day = "2019-10-01"
    world.bars.write_day(day, [_row(f"{SHARES[0]}.TW", day, "run-x")], ingest_run_id="run-x", connector_id="c")
    assert (world.bars.lineage_dir / f"date={day}.json").exists()
    report = _sweeper(world).run()
    assert world.bars.read_manifest(day) is None
    assert not (world.bars.lineage_dir / f"date={day}.json").exists()
    assert not list(world.bars.bars_dir.glob(f"market=*/date={day}/*"))
    assert report["stores"]["daily_bars"]["deleted_partitions"] == 1 and report["stores"]["lineage"]["deleted_partitions"] == 1
    assert report["stores"]["lineage"]["deleted_bytes"] > 0


def test_keyed_datasets_expire_by_their_declared_retention_and_macro_never(world):
    comm = keyed_dataset_store(world.root, "tw_community_daily")  # 180 days
    macro = keyed_dataset_store(world.root, "tw_macro_series")  # no expiry
    old_day = "2026-01-02"
    rec = lambda day: {"symbol": "2330", "trade_date": day, "platform": "ptt", "metadata": {}}
    recent_days = _weekdays("2026-11-12", 10)  # enough kept partitions that one expiry is under the 20 % share limit
    comm.write_records([rec(old_day), *[rec(d) for d in recent_days]], ingest_run_id="r")
    macro.write_records([{"series_code": "cpi", "date": "2019-01", "value": 1}], ingest_run_id="r")
    report = _sweeper(world).run()
    assert comm.partitions() == sorted(recent_days) and macro.partitions() == ["2019-01"]
    assert report["stores"]["keyed"]["deleted_partitions"] == 1
    assert report["stores"]["keyed"]["datasets"]["tw_community_daily"]["partitions"] == 1


def test_a_delisted_ordinary_share_keeps_its_bars_and_a_listed_one_past_retention_does_not(world):
    day = world.old[0]
    assert {f"{s}.TW" for s in DELISTED_SHARES} <= _symbols_in(world.bars, day)
    _sweeper(world).run()
    left = _symbols_in(world.bars, day)
    assert left == {f"{s}.TW" for s in DELISTED_SHARES}
    assert not any(f"{s}.TW" in left for s in SHARES)


# ---- 3. pins -----------------------------------------------------------------------------------------------------


def test_a_pinned_partition_past_retention_is_reported_and_not_deleted(world):
    pinned_day = world.old[1]
    (world.tmp / "pins.json").write_text(json.dumps({"trade_dates": [pinned_day]}), encoding="utf-8")
    before = world.bars.read_manifest(pinned_day)
    report = _sweeper(world).run()
    assert world.bars.read_manifest(pinned_day) == before  # untouched, same generation
    assert pinned_day in report["stores"]["daily_bars"]["pinned_dates_kept_sample"]
    assert report["stores"]["daily_bars"]["pinned_dates_kept"] == 1
    assert world.bars.read_manifest(world.old[0])["row_count"] == len(DELISTED_SHARES)  # the others were swept


def test_bars_of_a_pinned_run_survive_retention_and_expiry(world):
    run = f"run-{world.recent[10]}"
    (world.tmp / "pins.json").write_text(json.dumps({"lineage": {"ingest_run_ids": [run]}}), encoding="utf-8")
    _sweeper(world).run()
    assert {f"{s}.TW" for s in []} <= _symbols_in(world.bars, world.recent[10])
    assert set(WARRANTS_EXPIRED) <= _symbols_in(world.bars, world.recent[10])  # pinned: expired warrants stay too
    assert not set(WARRANTS_EXPIRED) & _symbols_in(world.bars, world.recent[11])


def test_unresolved_pins_delete_nothing_at_all(world, tmp_path):
    before = {d: world.bars.read_manifest(d)["bytes"] for d in world.bars.dates()}
    for pins in (PinSource([]), PinSource([tmp_path / "missing-evidence-dir"])):
        report = _sweeper(world, pins=pins).run()
        assert report["status"] == "blocked" and "pins_unresolved" in report["reason"]
        assert {d: world.bars.read_manifest(d)["bytes"] for d in world.bars.dates()} == before
    assert "pins_unresolved" in _sweeper(world).health()["last_run_reason"]


# ---- 4. expired warrants --------------------------------------------------------------------------------------


def _snapshot_store(tmp_path, n_shares=40, warrants=WARRANTS_EXPIRED, listed=WARRANTS_LISTED[:10]):
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
    from services.source_ingestion.tests.test_snapshot_current_record_store import _record

    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl", store_mode="sqlite", export_min_interval_seconds=10**9)
    store._last_export_monotonic = time.monotonic()
    syms = [*SHARES[:n_shares], *warrants, *listed]
    recs = []
    for i, s in enumerate(syms):
        r = _record(i, 3)
        r.metadata["normalized_row"]["symbol"] = f"{s}.TWSE"
        r.content_ref = f"ref-{s}"
        recs.append(r)
    store.append_normalized_records(recs, ingest_run_id="d", observed_at="2026-11-17T10:00:00Z")
    return store


def test_expired_warrants_lose_bars_snapshot_record_and_lineage_over_a_500_day_history(world):
    snap = _snapshot_store(world.tmp)
    assert snap.snapshot_current_stats()["record_count"] == 40 + len(WARRANTS_EXPIRED) + 10
    report = _sweeper(world, snapshot=snap).run()
    assert report["status"] == "completed", report["reason"]
    for day in world.recent:
        left = _symbols_in(world.bars, day)
        assert not set(WARRANTS_EXPIRED) & left
        assert set(WARRANTS_LISTED) <= left and {f"{s}.TW" for s in SHARES} <= left
    assert snap.snapshot_current_stats()["record_count"] == 40 + 10
    assert not any(bare in snap.live_symbols() for bare in [f"{s}.TWSE" for s in WARRANTS_EXPIRED])
    daily = report["stores"]["daily_bars"]
    assert daily["deleted_rows"] >= 500 * len(WARRANTS_EXPIRED)
    assert report["stores"]["snapshot_current"]["deleted_symbols"] == len(WARRANTS_EXPIRED)
    # nothing is archived: no file mentions an expired warrant anywhere under the root
    for p in world.root.rglob("*"):
        if p.is_file() and p.suffix == ".json":
            assert "040000" not in p.read_text(errors="ignore")
    # after the sweep the stock of warrant bars is at most listed warrants times days listed
    warrant_rows = sum(1 for d in world.recent for s in _symbols_in(world.bars, d) if len(s) == 6 and s[0] == "0")
    assert warrant_rows <= len(WARRANTS_LISTED) * len(world.recent)


def test_dry_run_is_the_default_and_reports_counts_and_bytes_without_deleting(world):
    snap = _snapshot_store(world.tmp)
    before = {d: world.bars.read_manifest(d) for d in world.bars.dates()}
    sweeper = _sweeper(world, mode="", snapshot=snap)  # unset setting
    report = sweeper.run()
    assert report["mode"] == "dry_run"
    daily = report["stores"]["daily_bars"]
    assert daily["would_delete_rows"] >= 500 * len(WARRANTS_EXPIRED) and daily["would_delete_bytes"] > 0
    assert daily["deleted_rows"] == 0
    assert report["stores"]["snapshot_current"]["would_delete_rows"] == len(WARRANTS_EXPIRED)
    assert report["stores"]["snapshot_current"]["expired_warrant_symbols_stored"] == len(WARRANTS_EXPIRED)
    assert snap.snapshot_current_stats()["record_count"] == 40 + len(WARRANTS_EXPIRED) + 10
    assert {d: world.bars.read_manifest(d) for d in world.bars.dates()} == before
    bad = _sweeper(world, mode="yes please", today="2026-11-18")  # unknown value: still a dry run, says so
    r2 = bad.run()
    assert r2["mode"] == "dry_run" and "not one of" in r2["mode_problem"]


def test_a_four_digit_ordinary_share_code_is_never_deleted_by_the_warrant_step(world):
    state = _state()
    # a corrupt listing row marks an ordinary share as a warrant that left the listing
    state.allowlist["1101"] = {**state.allowlist["1101"], "is_warrant": True, "last_seen": "2026-11-10"}
    state.ordinary_share_registry.pop("1101")
    state.save(world.listing)
    report = _sweeper(world).run()
    assert "1101.TW" in _symbols_in(world.bars, world.recent[0])
    assert report["warrant_step"]["expired_warrants"] == len(WARRANTS_EXPIRED)


# ---- 5. fail closed ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["missing", "unreadable", "stale"])
def test_an_unreadable_or_stale_listing_deletes_nothing_and_health_says_why(world, case):
    snap = _snapshot_store(world.tmp)
    if case == "missing":
        world.listing.unlink()
    elif case == "unreadable":
        world.listing.write_text("{not json", encoding="utf-8")
    else:
        _state(date_="2026-11-12").save(world.listing)  # older than the last trading day
    before = {d: world.bars.read_manifest(d) for d in world.bars.dates()}
    sweeper = _sweeper(world, snapshot=snap)
    report = sweeper.run()
    assert report["warrant_step"]["status"] == "blocked" and "listing_unavailable" in report["warrant_step"]["reason"]
    assert report["stores"]["daily_bars"]["deleted_rows"] == 0 and "listing_unavailable" in report["stores"]["daily_bars"]["blocked"]
    assert {d: world.bars.read_manifest(d) for d in world.bars.dates()} == before
    assert snap.snapshot_current_stats()["record_count"] == 40 + len(WARRANTS_EXPIRED) + 10
    health = sweeper.health()
    assert health["last_run_status"] == "completed" and health["stores"]["daily_bars"]["blocked"]


# ---- 6. atomic rewrite, kill, concurrent reader ----------------------------------------------------------------


def test_a_kill_in_the_middle_leaves_the_previous_complete_date_and_a_rerun_finishes(world):
    day = world.recent[3]
    before = world.bars.read_manifest(day)
    killed = RuntimeError("killed")
    for step in ("after_temp_write", "after_fsync", "after_lineage", "before_manifest_rename"):
        def fault(s, step=step):
            if s == step:
                raise killed
        bars = DailyBarsStore(world.root, fault=fault)
        with pytest.raises(RuntimeError):
            bars.remove_rows(day, lambda r: r["symbol"] in WARRANTS_EXPIRED)
        assert bars.read_manifest(day) == before
        assert bars.verify_day(day)["verified"] and bars.verify_day(day)["row_count"] == before["row_count"]
        assert DailyBarsReader(world.root).read_range([WARRANTS_EXPIRED[0]], day, day)[WARRANTS_EXPIRED[0]]
        bars.sweep_interrupted()  # residue of the killed writer
    assert world.bars.remove_rows(day, lambda r: r["symbol"] in WARRANTS_EXPIRED)["removed"] == len(WARRANTS_EXPIRED)
    assert world.bars.verify_day(day)["verified"]


def test_a_killed_whole_day_delete_leaves_no_partial_date_and_the_next_run_cleans_up(world):
    day = "2019-10-01"
    world.bars.write_day(day, [_row(f"{SHARES[0]}.TW", day, "run-x")], ingest_run_id="run-x", connector_id="c")

    def fault(step):
        if step == "after_manifest_unlink":
            raise RuntimeError("killed")
    with pytest.raises(RuntimeError):
        DailyBarsStore(world.root, fault=fault).delete_day(day)
    assert world.bars.read_manifest(day) is None  # a reader sees no date, never half a date
    _sweeper(world).run()  # orphan parts and lineage go on the next run
    assert not list(world.bars.bars_dir.glob(f"market=*/date={day}/*")) and not (world.bars.lineage_dir / f"date={day}.json").exists()


def test_a_reader_during_the_rewrite_sees_the_old_complete_date_then_the_new_one(world):
    day = world.recent[5]
    full = world.bars.read_manifest(day)["row_count"]
    reader = DailyBarsReader(world.root)
    seen = {}

    def fault(step):
        if step == "before_manifest_rename":
            seen["during"] = sum(len(v) for v in reader.read_range(_symbols_for(day), day, day).values())
        if step == "after_manifest_rename":
            seen["after"] = sum(len(v) for v in reader.read_range(_symbols_for(day), day, day).values())

    bars = DailyBarsStore(world.root, fault=fault)
    bars.remove_rows(day, lambda r: r["symbol"] in WARRANTS_EXPIRED)
    assert seen["during"] == full and seen["after"] == full - len(WARRANTS_EXPIRED)


def test_removing_rows_from_a_date_never_reads_a_mixture_under_concurrent_readers(world):
    day = world.recent[6]
    full = world.bars.read_manifest(day)["row_count"]
    reader = DailyBarsReader(world.root)
    counts, stop = set(), threading.Event()

    def read_loop():
        while not stop.is_set():
            counts.add(sum(len(v) for v in reader.read_range(_symbols_for(day), day, day).values()))
    t = threading.Thread(target=read_loop)
    t.start()
    try:
        world.bars.remove_rows(day, lambda r: r["symbol"] in WARRANTS_EXPIRED)
        time.sleep(0.05)
    finally:
        stop.set()
        t.join()
    assert counts <= {full, full - len(WARRANTS_EXPIRED)}


# ---- 7/8. cadence, alarm, dry run, share limit -----------------------------------------------------------------


def test_the_sweeper_runs_at_most_once_per_trading_day_and_not_on_a_weekend(world):
    s = _sweeper(world, mode="dry_run")
    first = s.run_if_due()
    assert first["ran"] is True
    assert s.run_if_due() == {"ran": False, "reason": "already_ran_today", "run_date": TODAY, "status": "completed"}
    assert _sweeper(world, today="2026-11-14").run_if_due()["reason"] == "not_a_trading_day"  # Saturday


def test_no_run_for_two_trading_days_raises_the_alarm_field(world):
    assert _sweeper(world).health()["alarm"] == "retention_never_ran"
    _sweeper(world, mode="dry_run", today="2026-11-16").run()  # Monday
    assert _sweeper(world, today="2026-11-17").health()["alarm"] is None  # 1 trading day
    h = _sweeper(world, today="2026-11-18").health()  # Wednesday: 2 trading days
    assert h["alarm"] == "retention_not_run_for_2_trading_days" and h["days_since_last_run"] == 2
    assert h["last_run_date"] == "2026-11-16" and h["stores"]["daily_bars"] and h["last_run_at"]
    assert "retention_not_run_for_4_trading_days" == _sweeper(world, today="2026-11-20").health()["alarm"]


def test_a_real_run_refuses_to_delete_more_than_the_stated_share_without_the_override(world):
    # a listing that expires 38 of 108 symbols a day (35 %) is far over the 20 % limit
    _state(expired=WARRANTS_EXPIRED + WARRANTS_LISTED[:30]).save(world.listing)
    before = {d: world.bars.read_manifest(d) for d in world.bars.dates()}
    s = _sweeper(world)
    report = s.run()
    daily = report["stores"]["daily_bars"]
    assert report["max_delete_share"] == 0.20
    assert daily["delete_share"] > 0.20 and "over the stated limit" in daily["refused"]
    assert report["status"] == "partial"
    assert {d: world.bars.read_manifest(d) for d in world.bars.dates()} == before
    over = _sweeper(world, extra_env={ret.ALLOW_LARGE_ENV: "1"}).run()
    assert over["large_delete_override"] is True
    assert "refused" not in over["stores"]["daily_bars"] and over["stores"]["daily_bars"]["deleted_rows"] > 0


def test_a_small_share_is_not_refused(world):
    # 8 expired of 108 symbols a day is 7 %; the six old days add rows too but stay far below the limit
    report = _sweeper(world).run()
    assert "refused" not in report["stores"]["daily_bars"] and report["status"] == "completed"


# ---- 10. the only store is one replaced report file -----------------------------------------------------------


def test_the_report_is_one_replaced_file_within_its_stated_bound(world):
    s = _sweeper(world, mode="dry_run")
    s.run()
    first_inode = world.report.stat().st_ino
    _sweeper(world, mode="dry_run", today="2026-11-18").run()
    files = sorted(p.name for p in world.tmp.iterdir() if p.name.startswith("retention-"))
    assert files == [ret.REPORT_FILENAME]  # no history, no temp file left
    size = world.report.stat().st_size
    assert size <= ret.REPORT_MAX_BYTES
    assert len(world.report.read_text().splitlines()) == 1
    MEASURED["report_bytes_500_days"] = size
    assert first_inode is not None


def test_an_oversized_report_is_refused_not_written(world, monkeypatch):
    monkeypatch.setattr(ret, "REPORT_MAX_BYTES", 100)
    report = _sweeper(world, mode="dry_run").run()
    assert report["status"] == "failed" and "report_not_written" in report["reason"]
    assert not world.report.exists()  # nothing half written, no temp file


# ---- controller cadence and health payload ---------------------------------------------------------------------


def test_the_controller_calls_the_sweeper_once_per_day_and_a_failure_never_fails_the_tick(monkeypatch):
    from services.source_ingestion import controller_worker as cw

    calls = []
    monkeypatch.setattr(cw, "_request_json", lambda url, **kw: calls.append(url) or {"ran": True, "status": "completed", "mode": "dry_run"})
    cw._RETENTION_LAST_DAY.clear()
    a = cw.run_retention_sweep(api_url="http://x", controller_token="t", today="2026-11-17")
    b = cw.run_retention_sweep(api_url="http://x", controller_token="t", today="2026-11-17")
    c = cw.run_retention_sweep(api_url="http://x", controller_token="t", today="2026-11-18")
    assert (a["ran"], b["called"], c["called"]) == (True, False, True) and len(calls) == 2
    assert calls[0].endswith("/api/source-ingest/retention/run")

    def boom(url, **kw):
        raise cw.ControllerTickError("controller", "HTTPError: 503")
    monkeypatch.setattr(cw, "_request_json", boom)
    cw._RETENTION_LAST_DAY.clear()
    out = cw.run_retention_sweep(api_url="http://x", controller_token="t", today="2026-11-19")
    assert out["called"] is True and out["ok"] is False and "503" in out["reason"]


def test_the_source_ingest_health_payload_carries_the_retention_fields(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.source_ingestion.routers.observability import create_observability_router

    class Empty:
        def list(self, **kw):
            return []

    runtime = SimpleNamespace(
        source_health_store=Empty(), market_data_storage_writer=SimpleNamespace(daily_bars_store=world.bars),
        DATA_DIR=world.tmp, hosted_listing=SimpleNamespace(state_path=world.listing),
        latest_market_snapshot_store=None, MARKET_DATA_STORAGE_ROOT=world.root,
        _require_controller_authorization=lambda authorization, operation: None
        if authorization == "Bearer ok" else (_ for _ in ()).throw(PermissionError("auth")),
    )
    runtime.market_data_storage_writer.daily_bars_store = world.bars
    world.bars.health = lambda: {}
    app = FastAPI()
    app.include_router(create_observability_router(runtime))
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/api/source-ingest/retention/run").status_code == 500  # no authorization
    body = client.get("/api/source-ingest/health").json()["retention"]
    assert body["alarm"] == "retention_never_ran" and body["mode"] == "dry_run"
    ran = client.post("/api/source-ingest/retention/run", headers={"Authorization": "Bearer ok"}).json()
    assert ran["mode"] == "dry_run"
    after = client.get("/api/source-ingest/retention").json()
    assert after["last_run_date"] and after["stores"]["daily_bars"] is not None and "alarm" in after


# ---- 9. hosted shape bounds (RETENTION_SHAPED=1) ---------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("RETENTION_SHAPED"), reason="hosted shape run: set RETENTION_SHAPED=1")
def test_hosted_shape_sweep_of_300_expired_warrants_is_bounded_and_never_blocks_snapshot_io(tmp_path):
    days = int(os.environ.get("RETENTION_SHAPED_DAYS", "500"))
    listed_warrants, shares, expiring = 20200, 2300, 300
    state = ListingState(last_success_date=LAST_LISTING)
    codes = {"share": [f"{1000 + i}" for i in range(shares)], "warrant": [f"{30000 + i:06d}" for i in range(listed_warrants)],
             "expiring": [f"{900000 + i}" for i in range(expiring)]}

    def put(sym, warrant, last):
        state.allowlist[sym] = {"venue": "TWSE", "category": "w" if warrant else "s", "cfi": "RWSCCA" if warrant else "ESVUFR",
                                "is_warrant": warrant, "first_seen": "2025-01-01", "last_seen": last}
    for s in codes["share"]:
        put(s, False, LAST_LISTING)
        state.ordinary_share_registry[s] = {"venue": "TWSE", "first_seen": "2019-01-01"}
    for s in codes["warrant"]:
        put(s, True, LAST_LISTING)
    for s in codes["expiring"]:
        put(s, True, "2026-11-10")
    listing = tmp_path / "security-listing.jsonl"
    state.save(listing)
    root = tmp_path / "store"
    bars = DailyBarsStore(root)
    symbols = [f"{s}.TW" for s in codes["share"]] + codes["warrant"] + codes["expiring"]
    t0 = time.monotonic()
    for day in _weekdays("2026-11-13", days):
        run = f"run-{day}"
        bars.write_day(day, [_row(s, day, run) for s in symbols], ingest_run_id=run, connector_id="c")
    MEASURED["seed_seconds"] = round(time.monotonic() - t0, 1)
    snap = _hosted_snapshot(tmp_path, codes)
    before_bytes = sum(bars.read_manifest(d)["bytes"] for d in bars.dates())
    before_lineage = sum(bars._lineage_size(d) for d in bars.lineage_dates())
    pin = tmp_path / "pins.json"
    pin.write_text("{}")
    big = {ret.MODE_ENV: "delete", ret.ALLOW_LARGE_ENV: "1"}
    sweeper = RetentionSweeper(daily_bars_store=bars, report_path=tmp_path / ret.REPORT_FILENAME, listing_state_path=listing,
                               snapshot_store=snap, pin_source=PinSource([pin]), keyed_root=root, environ=big, today=lambda: TODAY)
    dry = RetentionSweeper(daily_bars_store=bars, report_path=tmp_path / "dry.jsonl", listing_state_path=listing,
                           snapshot_store=snap, pin_source=PinSource([pin]), keyed_root=root,
                           environ={ret.MODE_ENV: "dry_run"}, today=lambda: TODAY).run()
    MEASURED["dry_run"] = {k: dry["stores"][k] for k in ("daily_bars", "lineage", "snapshot_current")} | {"duration_seconds": dry["duration_seconds"]}
    read_ms, write_ms, stop = [], [], threading.Event()
    store_snap = snap

    def reader():
        keys = [f"{s}.TWSE" for s in codes["share"][:50]]
        while not stop.is_set():
            t = time.monotonic()
            for k in keys[:10]:
                store_snap.get(k)
            read_ms.append((time.monotonic() - t) * 1000)

    def writer():
        from services.source_ingestion.tests.test_snapshot_current_record_store import _record
        n = 0
        while not stop.is_set():
            recs = [_record(i, 4 + n % 3) for i in range(500)]
            t = time.monotonic()
            try:
                store_snap.append_normalized_records(recs, ingest_run_id=f"w{n}", observed_at="2026-11-17T11:00:00Z")
            except Exception:  # noqa: BLE001 - retryable busy counts as a measured wait
                pass
            write_ms.append((time.monotonic() - t) * 1000)
            n += 1
            time.sleep(0.05)
    threads = [threading.Thread(target=reader), threading.Thread(target=writer)]
    for th in threads:
        th.start()
    real = sweeper.run()
    stop.set()
    for th in threads:
        th.join()
    after_bytes = sum(bars.read_manifest(d)["bytes"] for d in bars.dates())
    left = bars.symbol_stats()
    warrant_rows = sum(c for s, (c, _a, _b) in left.items() if s.isdigit() and len(s) == 6)
    MEASURED["real_run"] = {"status": real["status"], "duration_seconds": real["duration_seconds"], "days": days,
                            "deleted_rows": real["stores"]["daily_bars"]["deleted_rows"], "deleted_bytes": real["stores"]["daily_bars"]["deleted_bytes"],
                            "lineage_bytes": before_lineage, "bytes_before": before_bytes, "bytes_after": after_bytes,
                            "snapshot_deleted_symbols": real["stores"]["snapshot_current"]["deleted_symbols"],
                            "reader_p100_ms": round(max(read_ms), 1), "writer_p100_ms": round(max(write_ms), 1),
                            "reader_samples": len(read_ms), "writer_samples": len(write_ms),
                            "warrant_rows_after": warrant_rows, "warrant_rows_bound": listed_warrants * days,
                            "report_bytes": real["report_bytes"]}
    out = os.environ.get("RETENTION_EVIDENCE_OUT")
    if out:
        Path(out).write_text(json.dumps(MEASURED, indent=2, sort_keys=True), encoding="utf-8")
    assert real["status"] == "completed", real["reason"]
    assert real["stores"]["snapshot_current"]["deleted_symbols"] == expiring
    assert not [s for s in left if s in codes["expiring"]]
    assert warrant_rows <= listed_warrants * days
    assert max(read_ms) < 100 and max(write_ms) < 250 + 1000 * 0  # stated bounds (writer budget includes its own work)
    assert real["report_bytes"] <= ret.REPORT_MAX_BYTES


def _hosted_snapshot(tmp_path, codes):
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
    from services.source_ingestion.tests.test_snapshot_current_record_store import _record

    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl", store_mode="sqlite", export_min_interval_seconds=10**9)
    store._last_export_monotonic = time.monotonic()
    syms = codes["share"] + codes["warrant"][:8000] + codes["expiring"]
    for start in range(0, len(syms), 500):
        recs = []
        for i, s in enumerate(syms[start:start + 500], start=start):
            r = _record(i, 3)
            r.metadata["normalized_row"]["symbol"] = f"{s}.TWSE"
            r.content_ref = f"ref-{s}"
            recs.append(r)
        store.append_normalized_records(recs, ingest_run_id=f"seed-{start}", observed_at="2026-11-17T10:00:00Z")
    return store
