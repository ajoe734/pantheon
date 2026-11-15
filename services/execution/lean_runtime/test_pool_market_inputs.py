import pytest

from services.execution.lean_runtime import pool_market_inputs as pmi

NOW = "2026-11-13T06:00:00Z"


def snap(symbol, event="2026-11-13T05:30:00Z"):
    return {"symbol": symbol, "event_time": event, "observed_at": "2026-11-13T05:45:00Z",
            "closes": [100.0, 102.0], "source_ref": "test-only:bar", "snapshot_id": "x"}


def _admit(snapshot, **_):
    # Admission rule itself is exercised against the real owner in the integration test.
    from services.execution.market_snapshot_admission import admitted, rejected
    if snapshot is None:
        return rejected("market_input_missing", "market snapshot is missing")
    return admitted(snapshot["snapshot_id"], snapshot["event_time"], 1.0)


def obtain(symbols, fetch, **kw):
    return pmi.obtain_envelope_market_inputs(
        symbols, source_ingest_url="http://source", max_age_seconds=86400, minimum_closes=2,
        now_iso=NOW, fetch=fetch, admit=_admit, **kw)


def test_envelope_symbols_requires_distinct_nonblank_bounded():
    assert pmi.envelope_symbols({"parameters": {"symbols": ["2330.TW", "2317.TW"]}}) == ["2330.TW", "2317.TW"]
    for bad in ([], ["2330.TW", "2330.TW"], ["", "2317.TW"], [f"{i}.TW" for i in range(51)]):
        with pytest.raises(pmi.PoolMarketInputsUnavailable):
            pmi.envelope_symbols({"parameters": {"symbols": bad}})


def test_common_bar_one_request_per_symbol():
    calls = []
    out = obtain(["A.TW", "B.TW"], lambda url, s: (calls.append(s), snap(s))[1])
    assert set(out) == {"A.TW", "B.TW"} and calls == ["A.TW", "B.TW"]


def test_different_bars_refuse_whole_pool():
    with pytest.raises(pmi.PoolMarketInputsUnavailable) as err:
        obtain(["A.TW", "B.TW"], lambda url, s: snap(s, "2026-11-13T05:30:00Z" if s == "A.TW" else "2026-11-13T05:31:00Z"))
    assert err.value.code == "market_input_bar_mismatch"


def test_missing_symbol_refuses_whole_pool_and_names_it():
    with pytest.raises(pmi.PoolMarketInputsUnavailable) as err:
        obtain(["A.TW", "B.TW"], lambda url, s: snap(s) if s == "A.TW" else None)
    assert err.value.code == "market_input_missing" and "B.TW" in err.value.detail


def test_missing_source_url_fails_closed():
    with pytest.raises(pmi.PoolMarketInputsUnavailable) as err:
        pmi.obtain_envelope_market_inputs(["A.TW"], source_ingest_url="", max_age_seconds=1,
                                          minimum_closes=2, now_iso=NOW, fetch=lambda *_: None)
    assert err.value.code == "market_input_unavailable"
