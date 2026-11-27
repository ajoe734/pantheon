"""Governed whole-market dataset from the daily_bars store (DISCOVERY-GOVERNED-MARKET-DATASET-20261125)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from services.research import governed_market_dataset as gmd
from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.daily_bars_store import DailyBarsStore

END = "2026-11-24"
NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
SMALL = gmd.MarketPolicy(window_days=30, min_history_days=10, liquidity_days=5, min_liquidity_observations=3,
                         min_median_traded_value=1_000_000.0)


def trading_days(n: int, end: str = END) -> list[str]:
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def row(symbol: str, day: str, close: float, *, traded_value: float | None = 9_000_000.0,
        available: datetime | None = None) -> dict:
    ohlcv = {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0, "adjusted": False,
             "traded_value": traded_value,
             "available_time": available or datetime.fromisoformat(day + "T11:00:00+00:00"),
             "trading_status": "traded"}
    return {"market": "TW", "symbol": symbol, "trade_date": day, **ohlcv, "source_id": "s", "ingest_run_id": "r",
            "lineage_ref": "r", "content_hash": dbs._sha({k: (v.isoformat() if isinstance(v, datetime) else v)
                                                         for k, v in ohlcv.items()})}


def build_store(root: Path, days: int = 30, *, symbols=("2330.TW", "2317.TW"), skip: dict | None = None,
                thin: tuple = (), short: tuple = (), low_tv: tuple = ()) -> list[str]:
    store = DailyBarsStore(root)
    ds = trading_days(days)
    for i, d in enumerate(ds):
        rows = []
        for s in list(symbols) + list(thin) + list(short) + list(low_tv):
            if s in (skip or {}) and d >= skip[s]:
                continue
            if s in short and i < days - 4:
                continue
            tv = 100.0 if s in low_tv else 9_000_000.0
            rows.append(row(s, d, 100.0 + i, traded_value=tv))
        store.write_day(d, rows, ingest_run_id=f"r-{d}")
    return ds


def store_from_records(root: Path, records: list[dict]) -> Path:
    """A daily_bars store written from recorded official OHLCV records (fixture only: traded_value is
    close*volume and available_time is 08:00Z of the day; the boundary reads both, nothing else does)."""
    by_day: dict[str, list[dict]] = {}
    for r in records:
        by_day.setdefault(r["date"], []).append(r)
    store = DailyBarsStore(root)
    for day, rows in sorted(by_day.items()):
        out = []
        for r in rows:
            built = row(r["instrument"], day, r["close"], traded_value=r["close"] * r["volume"],
                        available=datetime.fromisoformat(day + "T08:00:00+00:00"))
            built.update(open=r["open"], high=r["high"], low=r["low"], volume=r["volume"])
            out.append(built)
        store.write_day(day, out, ingest_run_id="fixture-" + day)
    return root


def test_boundary_includes_market_and_names_every_exclusion(tmp_path):
    build_store(tmp_path, symbols=("2330.TW", "2317.TW", "2412.TW"), skip={"2412.TW": trading_days(30)[-3]},
                short=("9999.TW",), low_tv=("1101.TW",))
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    assert boundary.symbols == ("2317.TW", "2330.TW")
    assert boundary.exclusions["2412.TW"].startswith("suspended:")
    assert boundary.exclusions["9999.TW"].startswith("too_short_history:")
    assert boundary.exclusions["1101.TW"].startswith("illiquid:")
    assert boundary.store_symbol_count == 5 and boundary.trade_days == 30


def test_empty_authority_directory_reaches_a_dataset(tmp_path, monkeypatch):
    """No persona candidate output, no policy file: the dataset comes from the store alone."""
    store_root = tmp_path / "market_data_store"
    build_store(store_root)
    empty_authority = tmp_path / "persona-pool-dataset-authority"
    empty_authority.mkdir()
    monkeypatch.setenv(gmd.STORE_ROOT_ENV, str(store_root))
    for name in ("RESEARCH_RETRAIN_CANONICAL_DATASET_PATH", "RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH"):
        monkeypatch.delenv(name, raising=False)
    dataset = gmd.resolve_governed_dataset(symbols=["2330"], now=NOW, policy=SMALL)
    assert list(empty_authority.iterdir()) == []
    assert dataset["data_frequency"] == "daily" and dataset["dataset_id"].startswith("gmd-" + END)
    assert {r["instrument"] for r in dataset["records"]} == {"2330.TW", "2317.TW"}
    assert dataset["companion_symbols"] == ["2317.TW"]  # a one-symbol rule still gets two instruments
    first = dataset["records"][0]
    assert set(first) == {"instrument", "date", "open", "high", "low", "close", "volume", "traded_value", "available_time"}
    assert first["available_time"].endswith("Z")


def test_point_in_time_by_stored_available_time_and_nothing_imputed(tmp_path):
    store = DailyBarsStore(tmp_path)
    ds = trading_days(30)
    for i, d in enumerate(ds):
        late = datetime(2026, 11, 26, tzinfo=timezone.utc) if d == ds[-1] else None
        store.write_day(d, [row("2330.TW", d, 100.0 + i, available=late), row("2317.TW", d, 50.0 + i)], ingest_run_id=d)
    dataset = gmd.resolve_governed_dataset(symbols=["2330.TW"], root=tmp_path, now=NOW, policy=SMALL)
    assert [r["date"] for r in dataset["records"] if r["instrument"] == "2330.TW"] == ds[:-1]  # the bar not yet available is left out, not filled
    assert dataset["bars_not_point_in_time"] == 1


def test_snapshot_id_reverifies_and_changes_with_the_store(tmp_path):
    ds = build_store(tmp_path)
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    assert gmd.verify_snapshot(boundary.snapshot_id, tmp_path, policy=SMALL)
    pinned = gmd.resolve_governed_dataset(snapshot_id=boundary.snapshot_id, symbols=["2330"], root=tmp_path, now=NOW, policy=SMALL)
    assert pinned["dataset_id"] == boundary.snapshot_id
    DailyBarsStore(tmp_path).write_day(ds[-5], [row("2330.TW", ds[-5], 777.0)], ingest_run_id="rewrite")
    assert not gmd.verify_snapshot(boundary.snapshot_id, tmp_path, policy=SMALL)
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.resolve_governed_dataset(snapshot_id=boundary.snapshot_id, root=tmp_path, now=NOW, policy=SMALL)
    assert exc.value.code == "snapshot_not_reproducible"
    assert not gmd.verify_snapshot("gmd-2026-11-24-" + "0" * 40, tmp_path, policy=SMALL)
    assert not gmd.verify_snapshot("not-ours", tmp_path, policy=SMALL)


def test_missing_and_stale_store_fail_closed_with_a_reason(tmp_path):
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.build_market_boundary(tmp_path / "absent", policy=SMALL, now=NOW)
    assert exc.value.code == "daily_bars_store_missing" and "no stored trade day" in str(exc.value)
    build_store(tmp_path)
    with pytest.raises(gmd.GovernedMarketDatasetError) as stale:
        gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW + timedelta(days=9))
    assert stale.value.code == "daily_bars_store_stale" and END in str(stale.value)


@pytest.mark.parametrize("damage,code", [
    ("truncate", "daily_bars_day_incomplete"),
    ("delete", "daily_bars_day_incomplete"),
    ("bad_json", "daily_bars_day_corrupt"),
    ("no_digest", "daily_bars_day_unverifiable"),
])
def test_corrupt_incomplete_or_unverifiable_day_blocks(tmp_path, damage, code):
    ds = build_store(tmp_path)
    manifest_path = tmp_path / "daily_bars" / "_manifest" / f"date={ds[-3]}.json"
    manifest = json.loads(manifest_path.read_text())
    part = tmp_path / "daily_bars" / manifest["files"][0]["path"]
    if damage == "truncate":
        part.write_bytes(part.read_bytes()[:-10])
    elif damage == "delete":
        part.unlink()
    elif damage == "bad_json":
        manifest_path.write_text("{not json")
    else:
        manifest.pop("content_digest")
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    assert exc.value.code == code and ds[-3] in str(exc.value)


def test_dead_letters_of_other_connectors_do_not_block(tmp_path, monkeypatch):
    """Hosted shape: 4,742 pending dead letters on other connectors, 0 on the price connector. The builder
    reads the store only and never calls the source API, so the count cannot reach it."""
    build_store(tmp_path)
    import urllib.request

    def refuse(*args, **kwargs):
        raise AssertionError("the builder must not call any HTTP API")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setenv("SOURCE_INGEST_API_URL", "http://source-ingest:8097")
    hosted = {"pending_dlq_count_other_connectors": 4742, "pending_dlq_count_price_connector": 0}
    assert hosted["pending_dlq_count_price_connector"] == 0
    assert gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW).symbols
    source = Path(gmd.__file__).read_text()
    assert "urllib" not in source and "requests" not in source and "source-ingest/candidate-pool-market" not in source


def test_whole_market_is_not_materialised_and_symbol_limit_fails_closed(tmp_path):
    build_store(tmp_path)
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.dataset_for(boundary, ["2330.TW"] * 1 + [f"S{i}" for i in range(gmd.MAX_DATASET_SYMBOLS)], root=tmp_path)
    assert exc.value.code == "dataset_symbols_over_limit"
    with pytest.raises(gmd.GovernedMarketDatasetError) as outside:
        gmd.dataset_for(boundary, ["0000.TW"], root=tmp_path)
    assert outside.value.code == "symbols_outside_market_boundary"


def test_refuses_to_run_on_an_event_loop(tmp_path):
    build_store(tmp_path)

    async def call():
        gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)

    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        asyncio.run(call())
    assert exc.value.code == "governed_dataset_must_run_in_worker"


def test_symbol_session_slice_reads_only_requested_symbols_and_sessions(tmp_path, monkeypatch):
    from services.source_ingestion.daily_bars_reader import DailyBarsReader

    ds = build_store(tmp_path, symbols=("2330.TW", "2317.TW", "0050.TW"))
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    sessions = [d for d in ds if "2026-11-09" <= d <= "2026-11-18"]
    warmup = [d for d in ds if d < sessions[0]][-2:]
    read_calls: list[tuple[tuple[str, ...], str, str]] = []
    orig_read_range = DailyBarsReader.read_range

    def spy_list_symbols(self, *args, **kwargs):
        raise AssertionError("targeted symbol/session fill must not call list_symbols")

    def spy_read_range(self, symbols, start, end, **kwargs):
        asked = ( symbols,) if isinstance(symbols, str) else tuple(symbols)
        read_calls.append((asked, start, end))
        return orig_read_range(self, symbols, start, end, **kwargs)

    monkeypatch.setattr(DailyBarsReader, "list_symbols", spy_list_symbols)
    monkeypatch.setattr(DailyBarsReader, "read_range", spy_read_range)

    dataset = gmd.resolve_governed_dataset(
        symbols=["2330"], sessions=sessions, warmup_days=2, min_instruments=1,
        root=tmp_path, now=NOW, policy=SMALL,
    )
    assert dataset["dataset_id"] == boundary.snapshot_id
    assert "companion_symbols" not in dataset
    assert {r["instrument"] for r in dataset["records"]} == {"2330.TW"}
    assert [r["date"] for r in dataset["records"]] == warmup + sessions
    assert [c[0] for c in read_calls] == [("2330",)] * len(warmup + sessions)
    assert [c[1] for c in read_calls] == warmup + sessions
    assert [c[2] for c in read_calls] == warmup + sessions


def test_reconciler_lock_carries_pyarrow_and_reads_governed_daily_bars_with_only_lock_packages(tmp_path):
    import re
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[3]
    req_text = (repo_root / "services/paper_fleet_reconciler/requirements.txt").read_text(encoding="utf-8")
    lock_text = (repo_root / "dependencies/locks/services-paper_fleet_reconciler.txt").read_text(encoding="utf-8")
    persona_lock = (repo_root / "dependencies/locks/services-control-plane-persona.txt").read_text(encoding="utf-8")
    assert re.search(r"^pyarrow>=15,<30\b", req_text, re.M)
    pinned = re.search(r"^pyarrow==(\S+)$", persona_lock, re.M).group(1)
    assert re.search(rf"^pyarrow=={re.escape(pinned)}$", lock_text, re.M)

    build_store(tmp_path, symbols=("2330.TW", "2317.TW", "0050.TW"))
    script = """
import importlib.abc, importlib.machinery, json, pathlib, re, sys
from datetime import datetime, timezone
import pytest

repo_root = pathlib.Path(sys.argv[1])
store_root = pathlib.Path(sys.argv[2])
lock_text = (repo_root / "dependencies/locks/services-paper_fleet_reconciler.txt").read_text(encoding="utf-8")
locked = {m.group(1).lower().replace("-", "_") for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", lock_text, re.M)}
locked |= {"attr", "rpds"}
stdlib = set(sys.stdlib_module_names)

class ReconcilerLockOnlyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        top = fullname.split(".")[0]
        if top in stdlib or top.lower() in locked or top.startswith("_"):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.origin and str(spec.origin).startswith(str(repo_root)) and ".venv" not in str(spec.origin):
            return None
        if spec and spec.submodule_search_locations:
            if any(str(loc).startswith(str(repo_root)) and ".venv" not in str(loc) for loc in spec.submodule_search_locations):
                return None
        raise ModuleNotFoundError(f"blocked import outside reconciler lock: {fullname}")

sys.meta_path.insert(0, ReconcilerLockOnlyFinder())

for forbidden in ("pandas", "numpy", "scipy", "vectorbt"):
    try:
        __import__(forbidden)
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError(f"{forbidden} must not be importable under reconciler lock guard")

from services.research import governed_market_dataset as gmd
from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks, governed_admission_replay_source
from services.registry.strategy_artifact import strategy_artifact_checksum
from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact

now = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
policy = gmd.MarketPolicy(window_days=30, min_history_days=10, liquidity_days=5,
                          min_liquidity_observations=3, min_median_traded_value=1_000_000.0)
artifact = build_canonical_tw_strategy_artifact(
    strategy_id="strat-hosted-2330", symbols=("2330.TW",), lookback_bars=3, momentum_threshold=0.0
)
checksum = strategy_artifact_checksum(artifact)
aid = artifact["artifact_id"]
entry = {"registry_id": aid, "checksum": checksum, "metadata": {"strategy_artifact": artifact}}
env = {gmd.STORE_ROOT_ENV: str(store_root)}

source = governed_admission_replay_source(
    entry_reader=lambda ref, tenant: entry, tenant_id="tenant-dev", env=env, now=now
)
book_path = store_root / "member-books" / "persona.json"
books = MemberVirtualBooks(book_path, history_source=source)
member_meta = {"artifact_id": aid, "version": "1.0.0", "checksum": checksum, "risk_weight": 1.0,
               "virtual_only": False, "underlying_count": 1, "view": {"direction": "LONG", "strength": 1}}
for i, day in enumerate(("2026-11-20", "2026-11-23", "2026-11-24")):
    books.observe(
        binding_id="rb-eb74118dd3ca4b169496b1c239087512", envelope_checksum="sha256:env1", symbol="2330.TW",
        event_time=f"{day}T05:30:00Z", price=120.0 + i, open_price=119.5 + i,
        source_ref=f"recorded:{day}", bar_frequency="1d", members={aid: member_meta},
    )
[summary] = books.observe(
    binding_id="rb-new", envelope_checksum="sha256:env2", symbol="2330.TW",
    event_time="2026-11-25T05:30:00Z", price=124.0, open_price=123.5,
    source_ref="recorded:2026-11-25", bar_frequency="1d",
    members={aid: {**member_meta, "joined_at": "2026-11-08T18:10:02Z"}},
)
assert summary["admitted_at"] == "2026-11-08T18:10:02Z"
assert summary["since_inception"] is True
assert summary["since_inception_reason"] is None
print("LOCK_ONLY_OK", len(summary["dates"]))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, str(repo_root), str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "LOCK_ONLY_OK" in proc.stdout
