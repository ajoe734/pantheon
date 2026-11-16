"""Held identities come from live paper bindings at pull time, never a deploy-time snapshot."""
from __future__ import annotations

import json
import time

import pytest

from services.source_ingestion import provider_adapters as pa
from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport
from services.source_ingestion.tests.test_collector_newest_session_progress import TPEX, TWSE, recorded_run
from services.source_ingestion.tests.test_scheduled_connector import admission_stack  # noqa: F401

CAP = 150
EVIDENCE = {}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", raising=False)
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-a")
    monkeypatch.setenv("PANTHEON_RUNTIME_BINDING_STORE_PATH", str(tmp_path / "runtime_bindings.json"))


def _store(path, *bindings):
    path.write_text(json.dumps(list(bindings)), encoding="utf-8")


def _binding(symbol, *, tenant="tenant-a", status="active", mode="paper"):
    return {"binding_id": f"b-{symbol}", "symbol": symbol, "status": status, "deployment_mode": mode,
            "metadata": {"tenant_id": tenant}}


def _codes(result):
    return [str(r.metadata.get("symbol_canonical") or r.metadata.get("symbol") or "").split(".")[0]
            for r in result.records]


def _pull(a):
    return pa._collector_bundle(a, {pa.EFFECTIVE_MAX_RECORDS_KEY: CAP}, "trace-held")


def _setup(tmp_path, source, count):
    files, entry = recorded_run(source, count)
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    files["status.json"] = json.dumps({"sources": {source: {
        "status": "ok", "last_run_at": entry["finished_at"], "cadence_seconds": 21600}}}).encode()
    return cb.CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market",
        dataset="tw_price_daily", cadence_seconds=600, max_records=CAP, transport=FakeTransport(files),
        raw_root=str(tmp_path), attribution_required=True)


def test_binding_activated_after_start_is_held_on_next_pull(tmp_path):
    store = tmp_path / "runtime_bindings.json"
    _store(store)
    a = _setup(tmp_path / "a", TWSE, 400)
    first = _pull(a)
    assert "10390" not in _codes(first)[:5]
    _store(store, _binding("10390.TWSE"), _binding("10391.TWO"), _binding("10392.TW"))
    b = _setup(tmp_path / "b", TWSE, 400)
    second = _pull(b)
    assert set(_codes(second)[:3]) == {"10390", "10391", "10392"}


@pytest.mark.parametrize("source,count,held", [
    (TPEX, 5268, ("15200.TWO", "15250.TPEX", "15260.TW")),
    (TWSE, 1376, ("11300.TWSE", "11350.TW", "11370.TWSE")),
])
def test_recorded_size_close_delivers_all_held_rows_in_first_pull(tmp_path, source, count, held):
    _store(tmp_path / "runtime_bindings.json", *(_binding(s) for s in held))
    a = _setup(tmp_path, source, count)
    start = time.perf_counter()
    result = _pull(a)
    seconds = time.perf_counter() - start
    codes = _codes(result)
    wanted = {s.split(".")[0] for s in held}
    assert set(codes[: len(wanted)]) == wanted
    assert wanted <= set(codes)
    EVIDENCE[source] = {"rows_in_bundle": count, "held_codes": sorted(wanted), "pull_number": 1,
                        "pull_seconds": round(seconds, 3), "rows_in_first_pull": len(codes)}


def test_unreadable_store_keeps_order_with_typed_reason(tmp_path):
    (tmp_path / "runtime_bindings.json").write_text("{not json", encoding="utf-8")
    result = _pull(_setup(tmp_path, TWSE, 300))
    assert len(result.records) > 0
    assert result.observed["held_symbols_unavailable"] == "binding_store_unreadable"
    assert pa._live_paper_held_symbols({}) == ((), "binding_store_unreadable")


def test_missing_store_is_typed_and_non_stalling(tmp_path):
    (tmp_path / "runtime_bindings.json").unlink(missing_ok=True)
    result = _pull(_setup(tmp_path, TWSE, 300))
    assert result.observed["held_symbols_unavailable"] == "binding_store_missing"
    assert len(result.records) > 0


def test_other_tenant_binding_never_selects_held_rows(tmp_path):
    _store(tmp_path / "runtime_bindings.json",
           _binding("10200.TWSE", tenant="tenant-b"), _binding("10100.TWSE"),
           _binding("10150.TWSE", status="retired"), _binding("10160.TWSE", mode="live"))
    symbols, reason = pa._live_paper_held_symbols({})
    assert (symbols, reason) == (("10100.TWSE",), None)
    assert pa._live_paper_held_symbols({"tenant_id": "tenant-b"})[0] == ("10200.TWSE",)
    codes = _codes(_pull(_setup(tmp_path, TWSE, 300)))
    assert codes[0] == "10100" and "10200" not in codes[:5]
