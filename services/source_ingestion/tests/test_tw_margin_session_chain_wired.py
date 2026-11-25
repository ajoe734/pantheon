"""Margin session chain check wired into every production margin path (TW-MARGIN-SESSION-CHAIN-CHECK-WIRED)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from services.source_ingestion import chip_publication_schedule as cps
from services.source_ingestion.chip_publication_schedule import (
    MARGIN_CHAIN_MIN_MATCH_RATIO,
    ChipSessionUndecidable,
    margin_chain_wiring,
    stored_margin_session_lookup,
    verify_margin_session_chain,
)
from services.source_ingestion.connectors import TaiwanOfficialMarketDatasetAdapter
from services.source_ingestion.persona_source_reconciler import SourceProvisioningReconciler, chip_profile_provider

TWSE = "tw-official-twse-margin-balances"
TPEX = "tw-official-tpex-margin-balances"


def _write(root: Path, session: str, connector: str, rows: list[dict], name: str = "run.jsonl") -> None:
    part = root / "normalized" / "tw_margin_short_balance" / f"date={session}"
    part.mkdir(parents=True, exist_ok=True)
    with (part / name).open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({"connector_id": connector, "metadata": {"date": r.get("date", session), "normalized_row": r}}) + "\n")


def _rows(n: int, session: str | None, prefix: str = "") -> list[dict]:
    return [{"symbol": f"{prefix}{1000 + i}", "date": session or "", "margin_balance": 100 + i, "short_balance": 50 + i}
            for i in range(n)]


def _payload(n: int, mismatches: int = 0) -> list[dict]:
    return [{"symbol": f"{1000 + i}", "margin_previous_balance": 0 if i < mismatches else 100 + i,
             "short_previous_balance": 50 + i} for i in range(n)]


def _check(rows, stored):
    return verify_margin_session_chain(TWSE, date(2026, 11, 19), rows, stored)


def test_tolerance_shapes_from_hosted_measurement() -> None:
    def stored(n):
        return {f"{1000 + i}": {"margin_balance": 100 + i, "short_balance": 50 + i} for i in range(n)}

    assert MARGIN_CHAIN_MIN_MATCH_RATIO == 0.90
    assert _check(_payload(500), stored(500))["matched_count"] == 500
    for n, bad in ((1311, 39), (1311, 7)):  # 11/24 (1272 matched) and 11/23 (1304 matched) shapes are accepted
        ok = _check(_payload(n, mismatches=bad), stored(n))
        assert ok["matched_count"] == n - bad and ok["mismatched_count"] == bad
        assert len(ok["mismatched_symbols"]) == min(bad, 10)
    for n, good in ((1311, 186), (1311, 185), (500, 102)):  # measured wrong sessions are refused
        with pytest.raises(ChipSessionUndecidable, match=f"matched {good}, mismatched {n - good}"):
            _check(_payload(n, mismatches=n - good), stored(n))


def test_stored_earlier_lookup_is_bounded_and_newest_first(tmp_path: Path) -> None:
    earlier = cps.stored_margin_earlier_session_lookup(tmp_path)
    for day in ("2026-11-12", "2026-11-05"):
        _write(tmp_path, day, TWSE, _rows(3, day))
    assert earlier(TWSE, date(2026, 11, 17))[0] == date(2026, 11, 12)
    assert earlier(TWSE, date(2026, 11, 17), max_sessions=2) is None  # 11/17 and 11/16 only (weekday calendar)
    assert earlier(TPEX, date(2026, 11, 17)) is None  # other connector's rows are not used
    assert cps.MARGIN_CHAIN_MAX_MISMATCH_SYMBOLS == 10 and cps.MARGIN_CHAIN_GAP_LOOKBACK_SESSIONS == 10


def _margin_payload(previous: int, own: int, n: int = 20) -> list[dict]:
    return [{"股票代號": f"{1000 + i}", "股票名稱": f"S{i}", "融資前日餘額": str(previous + i),
             "融資今日餘額": str(own + i), "融券前日餘額": str(50 + i), "融券今日餘額": str(50 + i)} for i in range(n)]


@pytest.mark.parametrize("connector,venue", [(TWSE, "TWSE"), (TPEX, "TPEx")])
def test_gap_bridge_through_adapter_path(tmp_path: Path, connector: str, venue: str) -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.connectors import SourceEvidenceError

    fetch = datetime(2026, 11, 19, 10, 50, tzinfo=timezone.utc)  # inferred session 2026-11-18, previous 11/17
    _write(tmp_path, "2026-11-12", connector, [{"symbol": f"{1000 + i}", "date": "2026-11-12", "margin_balance": 100 + i,
                                               "short_balance": 50 + i} for i in range(20)])
    adapter = TaiwanOfficialMarketDatasetAdapter(connector_id=connector, **margin_chain_wiring(connector, root=tmp_path))

    def run(payload):
        return adapter.records_from_payload("tw_margin_short_balance", venue, payload, fetch_time=fetch, trace_id="t")

    recs = run(_margin_payload(900, 950))  # unrelated to the compared session: bridged, nothing invented
    chain = recs[0].metadata["normalized_row"]["session_chain"]
    assert chain["status"] == "gap_bridged" and chain["compared_session"] == "2026-11-12"
    assert chain["missing_sessions"] == ["2026-11-13", "2026-11-16", "2026-11-17"]
    assert {r.metadata["date"] for r in recs} == {"2026-11-18"}
    with pytest.raises(SourceEvidenceError, match="earlier session"):  # payload previous == compared session
        run(_margin_payload(100, 950))
    with pytest.raises(SourceEvidenceError, match="repeats"):  # payload own balances == compared session
        run(_margin_payload(900, 100))
    # nothing stored within 10 sessions: fails closed as before
    far = TaiwanOfficialMarketDatasetAdapter(connector_id=connector, **margin_chain_wiring(connector, root=tmp_path))
    _write(tmp_path, "2026-10-01", TPEX if connector == TWSE else TWSE, _rows(2, "2026-10-01"))
    import shutil
    shutil.rmtree(tmp_path / "normalized" / "tw_margin_short_balance" / "date=2026-11-12")
    with pytest.raises(SourceEvidenceError, match="gap"):
        far.records_from_payload("tw_margin_short_balance", venue, _margin_payload(900, 950), fetch_time=fetch, trace_id="t")


def test_lookup_reads_one_session_one_connector_placeholder() -> None:
    assert cps.MARGIN_CHAIN_MAX_MISMATCH_SYMBOLS == 10 and cps.MARGIN_CHAIN_GAP_LOOKBACK_SESSIONS == 10


def test_lookup_reads_one_session_one_connector(tmp_path: Path) -> None:
    _write(tmp_path, "2026-11-18", TWSE, _rows(3, "2026-11-18"))
    _write(tmp_path, "2026-11-18", TPEX, _rows(2, "2026-11-18", prefix="9"))
    _write(tmp_path, "2026-11-17", TWSE, _rows(4, "2026-11-17"))
    out = stored_margin_session_lookup(tmp_path)(TWSE, date(2026, 11, 18))
    assert sorted(out) == ["1000", "1001", "1002"]
    assert out["1001"] == {"margin_balance": 101, "short_balance": 51}


def test_first_load_only_when_previous_holds_legacy_rows_and_gap_fails_closed(tmp_path: Path) -> None:
    lookup = stored_margin_session_lookup(tmp_path)
    _write(tmp_path, "2026-11-18", TWSE, _rows(3, None))  # stored before PR 7655: fetch-date partition, date ''
    assert lookup(TWSE, date(2026, 11, 18)) is None
    adapter_kwargs = margin_chain_wiring(TWSE, root=tmp_path)
    assert verify_margin_session_chain(TWSE, date(2026, 11, 19), _payload(3), lookup(TWSE, date(2026, 11, 18)),
                                       accept_first_load=adapter_kwargs["accept_first_load"])["first_load_accepted"] is True
    with pytest.raises(ChipSessionUndecidable, match="gap"):  # later missing previous session
        lookup(TWSE, date(2026, 11, 17))
    with pytest.raises(ChipSessionUndecidable, match="gap"):  # partition exists but only another connector
        _write(tmp_path, "2026-11-16", TPEX, _rows(2, "2026-11-16"))
        lookup(TWSE, date(2026, 11, 16))


def test_each_production_path_passes_the_lookup() -> None:
    from services.source_ingestion import provider_adapters as pa

    for connector_id in (TWSE, TPEX):
        # bounded refresh profile provider (persona reconciler)
        a = chip_profile_provider(connector_id)
        assert callable(a.previous_session_lookup) and a.accept_first_load
        # persona reconciler's own provider factory for the base official connector id
        rec = SourceProvisioningReconciler.__init__  # noqa: F841 - factory lambdas are built in __init__
        # steady scheduler: the provider adapter spec build
        spec = pa.ALLOWED_PROVIDER_ADAPTERS["TaiwanOfficialMarketDatasetAdapter.records_from_payload"]
        built = spec.build(type("C", (), {"connector_id": connector_id, "secret_ref_id": None})(), {})
        assert callable(built.previous_session_lookup) and built.accept_first_load
    # not margin connectors: untouched
    assert margin_chain_wiring("tw-official-tpex-institutional-flow") == {}
    assert TaiwanOfficialMarketDatasetAdapter(connector_id="x").previous_session_lookup is None


@pytest.mark.parametrize("connector_id", [TWSE, TPEX])
def test_handler_wires_bare_adapter_before_fetch(monkeypatch: pytest.MonkeyPatch, connector_id: str) -> None:
    from services.source_ingestion import provider_adapters as pa

    captured = []

    def fake(self, dataset, venue, payload, **kw):
        captured.append(self)
        return ()

    monkeypatch.setattr(TaiwanOfficialMarketDatasetAdapter, "records_from_payload", fake)
    monkeypatch.setattr(TaiwanOfficialMarketDatasetAdapter, "fetch_payload", lambda self, *a, **k: [])
    pa._taiwan_official_records(
        TaiwanOfficialMarketDatasetAdapter(connector_id=connector_id),
        {"dataset": "tw_margin_short_balance", "venues": ["TWSE"], "payloads": {"TWSE": []}}, "t",
    )
    assert captured and callable(captured[0].previous_session_lookup) and captured[0].accept_first_load


def test_fresh_store_is_first_load_but_missing_partition_in_populated_store_is_gap(tmp_path: Path) -> None:
    lookup = stored_margin_session_lookup(tmp_path)
    assert lookup(TWSE, date(2026, 11, 18)) is None
    _write(tmp_path, "2026-11-16", TWSE, _rows(2, "2026-11-16"))
    with pytest.raises(ChipSessionUndecidable, match="gap"):
        lookup(TWSE, date(2026, 11, 18))
