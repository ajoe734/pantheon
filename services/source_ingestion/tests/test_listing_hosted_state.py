"""SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118: hosted gets the dated security listing.

Generated data in the hosted shape: fixed seed, no hosted access. Acceptance 2, 3, 4, 7, 8.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion.connectors import SourceConnector, SourceRecord
from services.source_ingestion.connectors.collector_bundle import LocalDirectoryBundleTransport
from services.source_ingestion.market_data_storage import MarketDataStorageWriter
from services.source_ingestion.security_listing import (
    BUNDLE_STATE_PATH,
    HOSTED_STATE_FILENAME,
    LISTING_MODE_ENV,
    MAX_STATE_BYTES,
    HostedListing,
    ListingParseError,
    ListingState,
    listing_mode,
    write_state_bytes,
)

LISTING_DAY = "2026-11-16"  # Monday
NEXT_DAY = "2026-11-17"


def _live_shaped_state(date: str = LISTING_DAY, seed: int = 20261117) -> ListingState:
    """57,531 rows like the live listing of 2026-11-17: 41,965 TWSE warrants, 13,143 TPEx warrants (here the
    remaining rows are ordinary shares). Symbols and dates come from a fixed seed."""
    rng = random.Random(seed)
    s = ListingState(last_success_date=date)
    first_seen = ["2026-09-01", "2026-09-15", "2026-10-05", "2026-10-20", "2026-11-03"]

    def put(sym: str, venue: str, category: str, cfi: str, warrant: bool) -> None:
        s.allowlist[sym] = {"venue": venue, "category": category, "cfi": cfi, "is_warrant": warrant,
                            "first_seen": rng.choice(first_seen), "last_seen": date}

    for i in range(41965):
        put(f"{i + 30000:06d}", "TWSE", "上市認購(售)權證", "RWSCCA", True)
    for i in range(13143):
        put(f"7{i:05d}", "TPEX", "上櫃認購(售)權證", "RWSCCA", True)
    n_shares = 57531 - len(s.allowlist)
    for i in range(n_shares):
        sym = f"{1000 + i}"
        put(sym, "TWSE", "股票", "ESVUFR", False)
        s.ordinary_share_registry[sym] = {"venue": "TWSE", "first_seen": rng.choice(first_seen)}
    assert len(s.allowlist) == 57531
    return s


def _small_state(date: str = LISTING_DAY) -> ListingState:
    s = ListingState(last_success_date=date)
    for sym, warrant in (("2330", False), ("030001", True), ("030002", True)):
        s.allowlist[sym] = {"venue": "TWSE", "category": "w" if warrant else "s", "cfi": "RWSCCA" if warrant else "ESVUFR",
                            "is_warrant": warrant, "first_seen": "2026-10-01", "last_seen": date}
    s.ordinary_share_registry["2330"] = {"venue": "TWSE", "first_seen": "2026-10-01"}
    return s


def _publish(collector_root: Path, state: ListingState) -> bytes:
    """What CollectorRunner.publish_security_listing does: the state file's bytes, under bundles/."""
    src = collector_root / "state" / "security-listing.json"
    state.save(src)
    data = src.read_bytes()
    write_state_bytes(collector_root / BUNDLE_STATE_PATH, data)
    return data


# ---- acceptance 2: one dated listing; byte test at the live shape ---------------------------------------


def test_live_shape_state_size_is_measured_and_far_below_the_20mb_cap(tmp_path):
    path = tmp_path / HOSTED_STATE_FILENAME
    state = _live_shaped_state()
    state.save(path)
    size = path.stat().st_size
    print("live_shape_rows=57531 state_bytes=", size)
    assert size < 5 * 1024 * 1024, size  # about 3.2 MB at the live shape (measured)
    assert size < MAX_STATE_BYTES
    back = ListingState.load(path)
    assert back.allowlist == state.allowlist and back.ordinary_share_registry == state.ordinary_share_registry


def test_state_over_the_cap_is_refused_on_save_and_on_load(tmp_path, monkeypatch):
    import services.source_ingestion.security_listing as sl

    monkeypatch.setattr(sl, "MAX_STATE_BYTES", 100)
    with pytest.raises(ListingParseError, match="byte cap"):
        _small_state().save(tmp_path / "x.jsonl")
    big = tmp_path / "big.jsonl"
    big.write_bytes(b"x" * 101)
    with pytest.raises(ListingParseError, match="byte cap"):
        ListingState.load(big)


def test_hosted_state_equals_collector_state_for_a_recorded_listing_day(tmp_path):
    collector = tmp_path / "collector"
    published = _publish(collector, _live_shaped_state())
    hosted = HostedListing(tmp_path / "data" / HOSTED_STATE_FILENAME, transport=LocalDirectoryBundleTransport(collector),
                           mode="shadow", today=lambda: NEXT_DAY)
    result = hosted.sync()
    assert result["ok"] and result["replaced"] and result["listing_date"] == LISTING_DAY
    assert (tmp_path / "data" / HOSTED_STATE_FILENAME).read_bytes() == published  # byte for byte
    assert ListingState.load(tmp_path / "data" / HOSTED_STATE_FILENAME).to_dict() == _live_shaped_state().to_dict()


def test_one_file_replaced_per_listing_day_and_one_fetch_per_day(tmp_path):
    collector = tmp_path / "collector"
    _publish(collector, _small_state(LISTING_DAY))
    clock = {"day": LISTING_DAY, "t": 0.0}
    path = tmp_path / "data" / HOSTED_STATE_FILENAME
    hosted = HostedListing(path, transport=LocalDirectoryBundleTransport(collector), mode="shadow",
                           today=lambda: clock["day"], monotonic=lambda: clock["t"])
    assert hosted.sync()["replaced"] and hosted.fetch_count == 1
    for _ in range(5):  # up to date: no fetch
        assert hosted.sync() == {"attempted": False, "reason": "up_to_date", "listing_date": LISTING_DAY}
    assert hosted.fetch_count == 1
    clock["day"] = NEXT_DAY  # new listing day, collector has not published yet: retry is spaced out
    hosted.sync()
    assert hosted.fetch_count == 2
    clock["t"] += 60
    for _ in range(5):
        assert hosted.sync()["reason"] == "retry_later"
    assert hosted.fetch_count == 2
    _publish(collector, _small_state(NEXT_DAY))
    clock["t"] += 3600
    out = hosted.sync()
    assert out["ok"] and out["replaced"] and out["listing_date"] == NEXT_DAY and hosted.fetch_count == 3
    assert sorted(p.name for p in path.parent.iterdir()) == [HOSTED_STATE_FILENAME]  # one file, no temp left over
    # weekend: the last weekday's listing is current, nothing is fetched
    _publish(collector, _small_state("2026-11-20"))
    clock["day"] = "2026-11-20"
    clock["t"] += 10**6
    assert hosted.sync()["replaced"]
    count = hosted.fetch_count
    clock["day"] = "2026-11-21"
    clock["t"] += 10**6
    assert hosted.sync()["reason"] == "up_to_date" and hosted.fetch_count == count


def test_an_older_remote_state_never_replaces_a_newer_local_one(tmp_path):
    collector = tmp_path / "collector"
    _publish(collector, _small_state(LISTING_DAY))
    path = tmp_path / "data" / HOSTED_STATE_FILENAME
    _small_state(NEXT_DAY).save(path)
    before = path.read_bytes()
    hosted = HostedListing(path, transport=LocalDirectoryBundleTransport(collector), mode="shadow",
                           today=lambda: "2026-11-18")
    out = hosted.sync()
    assert out["ok"] is False and out["reason"] == "remote_older_than_local"
    assert path.read_bytes() == before


def test_a_failed_fetch_keeps_the_previous_state_and_is_reported(tmp_path):
    path = tmp_path / "data" / HOSTED_STATE_FILENAME
    _small_state(LISTING_DAY).save(path)
    before = path.read_bytes()
    hosted = HostedListing(path, transport=LocalDirectoryBundleTransport(tmp_path / "nowhere"), mode="shadow",
                           today=lambda: NEXT_DAY)
    out = hosted.sync()
    assert out["ok"] is False and "not found" in out["reason"]
    assert path.read_bytes() == before
    assert hosted.health()["last_sync"]["ok"] is False


# ---- acceptance 3: the named setting off | shadow | enforce ----------------------------------------------


def test_the_setting_has_three_values_and_shadow_is_the_default(monkeypatch):
    monkeypatch.delenv(LISTING_MODE_ENV, raising=False)
    assert listing_mode() == ("shadow", None)
    for value in ("off", "shadow", "enforce", " ENFORCE "):
        assert listing_mode({LISTING_MODE_ENV: value})[0] == value.strip().lower()
    mode, problem = listing_mode({LISTING_MODE_ENV: "enforced"})
    assert mode == "shadow" and "not one of" in problem  # never a silent enforce


def _write_run(tmp_path, hosted, symbols, run_id="run-1"):
    conn = SourceConnector(connector_id="c", source_type="market", provider="t", license_scope="internal", metadata={"market": "TW"})
    recs = [SourceRecord(source_id=f"s-{s}", connector_id="c", source_type="market", title="t", content_ref=f"r-{s}",
                         metadata={"symbol": s, "close": 10.0, "dataset": "tw_price_daily",
                                   "event_time": "2026-11-16T05:30:00Z"}) for s in symbols]
    stub = SimpleNamespace(write_records=lambda *a, **k: None, record_dual_write_error=lambda e: None, health=lambda: {})
    writer = MarketDataStorageWriter(tmp_path / "store", daily_bars_store=stub, listing_provider=hosted)
    res = SimpleNamespace(records=recs, run=SimpleNamespace(ingest_run_id=run_id))
    return writer.write_run(result=res, connector=conn).to_dict()["summary"]


def _hosted(tmp_path, mode, state=None, today=NEXT_DAY):
    path = tmp_path / "data" / HOSTED_STATE_FILENAME
    (state or _small_state()).save(path)
    return HostedListing(path, transport=None, mode=mode, today=lambda: today)


SYMBOLS = ["2330", "030001", "999991", "999992", "999993"]  # three codes are in no listing


def test_shadow_counts_what_would_be_skipped_and_skips_nothing(tmp_path):
    hosted = _hosted(tmp_path, "shadow")
    summary = _write_run(tmp_path, hosted, SYMBOLS)
    assert summary["skipped_not_listed_count"] == 0
    assert hosted.last_run["would_skip_count"] == 3 and hosted.last_run["skipped_not_listed_count"] == 0
    assert hosted.last_run["ingest_run_id"] == "run-1" and hosted.last_run["listing_mode"] == "shadow"
    assert hosted.health()["day_totals"][NEXT_DAY]["writer_would_skip_count"] == 3
    # the normalized file holds all five rows
    rows = [json.loads(l) for p in (tmp_path / "store" / "normalized").rglob("run-1.jsonl") for l in p.read_text().splitlines()]
    assert len(rows) == 5


def test_enforce_skips_what_the_listing_does_not_list(tmp_path):
    hosted = _hosted(tmp_path, "enforce")
    summary = _write_run(tmp_path, hosted, SYMBOLS)
    assert summary["skipped_not_listed_count"] == 3 and summary["normalized_row_count"] == 2
    assert hosted.last_run["skipped_not_listed_count"] == 3 and hosted.last_run["would_skip_count"] == 0


def test_off_does_not_read_the_listing(tmp_path):
    hosted = _hosted(tmp_path, "off")
    (tmp_path / "data" / HOSTED_STATE_FILENAME).write_text("{not json")  # would fail if it were read
    summary = _write_run(tmp_path, hosted, SYMBOLS)
    assert summary["normalized_row_count"] == 5 and summary["skipped_not_listed_count"] == 0
    assert hosted.filter_for_run() is None
    assert hosted.last_run["filter_active"] is False and hosted.last_run["would_skip_count"] == 0 and hosted.last_run["listing_mode"] == "off"
    assert hosted.sync() == {"attempted": False, "reason": "mode_off"}


# ---- acceptance 4: fail closed and visible ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_unreadable_listing_is_reported_and_nothing_is_skipped_or_expired(tmp_path, mode):
    hosted = _hosted(tmp_path, mode)
    (tmp_path / "data" / HOSTED_STATE_FILENAME).write_text("{not json")
    summary = _write_run(tmp_path, hosted, SYMBOLS)
    assert summary["normalized_row_count"] == 5 and summary["skipped_not_listed_count"] == 0
    h = hosted.health()
    assert h["readable"] is False and h["status"] == "listing_unavailable" and h["reason"].startswith("listing_unreadable")
    assert h["expired_warrant_count"] is None and h["last_run"]["filter_active"] is False


def test_missing_listing_is_reported(tmp_path):
    hosted = HostedListing(tmp_path / "data" / HOSTED_STATE_FILENAME, transport=None, mode="enforce", today=lambda: NEXT_DAY)
    assert hosted.filter_for_run() is None
    h = hosted.health()
    assert h["status"] == "listing_unavailable" and h["reason"] == "listing_missing" and h["age_trading_days"] is None


def test_listing_older_than_the_last_trading_day_is_reported_with_its_age(tmp_path):
    state = _small_state(LISTING_DAY)
    state.allowlist["030002"]["last_seen"] = "2026-11-02"  # an expired warrant on a readable list
    hosted = _hosted(tmp_path, "enforce", state, today="2026-11-18")  # two trading days after the listing
    h = hosted.health()
    assert h["status"] == "stale_ok" and h["age_trading_days"] == 2 and h["listing_date"] == LISTING_DAY
    assert "2 trading days old" in h["reason"]
    assert h["expired_warrant_count"] == 0  # not fresh: nothing is treated as expired
    assert hosted.filter_for_run() is not None  # three trading day rule: still usable


def test_enforce_applies_the_three_trading_day_rule(tmp_path):
    hosted = _hosted(tmp_path, "enforce", today="2026-11-19")  # 3 trading days
    assert hosted.health()["status"] == "stale_ok" and hosted.filter_for_run() is not None
    too_old = _hosted(tmp_path / "old", "enforce", today="2026-11-20")  # 4 trading days
    h = too_old.health()
    assert h["status"] == "listing_unavailable" and h["age_trading_days"] == 4 and "limit 3" in h["reason"]
    assert too_old.filter_for_run() is None  # no symbol is skipped or treated as expired
    summary = _write_run(tmp_path / "old", too_old, SYMBOLS)
    assert summary["normalized_row_count"] == 5


def test_runtime_health_carries_the_listing_state(tmp_path, monkeypatch):
    from services.source_ingestion.runtime import SourceIngestionRuntime

    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(LISTING_MODE_ENV, raising=False)
    metrics = SourceIngestionRuntime()._source_runtime_metrics()
    listing = metrics["security_listing"]
    assert listing["mode"] == "shadow" and listing["status"] == "listing_unavailable"
    assert listing["reason"] == "listing_missing" and listing["state_bytes_cap"] == MAX_STATE_BYTES


# ---- acceptance 8: the previous release on a volume that holds the new state ------------------------------

PREVIOUS_RELEASE = "fe72f0f76"  # dev head this task branched from: the release that is deployed before it

_DRIVER = '''
import json, os, sys
from pathlib import Path
data_dir = Path(sys.argv[1])
os.environ["SOURCE_INGEST_DATA_DIR"] = str(data_dir)
from services.source_ingestion.runtime import SourceIngestionRuntime
runtime = SourceIngestionRuntime()
metrics = runtime._source_runtime_metrics()
files = sorted(p.name for p in data_dir.iterdir())
print(json.dumps({"started": True, "journal_keys": sorted(metrics["ingest_schedule_journal"])[:3],
                  "has_listing_metric": "security_listing" in metrics, "files": files}))
'''


def test_previous_release_starts_on_a_volume_that_holds_the_new_listing_state(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts import previous_release_reader as harness

    volume = tmp_path / "volume"
    volume.mkdir()
    collector = tmp_path / "collector"
    _publish(collector, _small_state())
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(volume))
    from services.source_ingestion.runtime import SourceIngestionRuntime

    runtime = SourceIngestionRuntime()  # this release writes the new state onto the volume
    runtime.hosted_listing._transport = LocalDirectoryBundleTransport(collector)
    runtime.hosted_listing._today = lambda: NEXT_DAY
    assert runtime.hosted_listing.sync()["replaced"]
    state_file = volume / HOSTED_STATE_FILENAME
    before = state_file.read_bytes()
    assert state_file.exists()

    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    result = harness.run_driver(PREVIOUS_RELEASE, driver, modules=["services.source_ingestion.runtime"],
                                driver_args=[str(volume)], check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["started"] is True and seen["has_listing_metric"] is False  # the previous release does not know it
    assert HOSTED_STATE_FILENAME in seen["files"]  # present on the volume ...
    assert state_file.read_bytes() == before  # ... and left untouched (ignored) by the previous release
