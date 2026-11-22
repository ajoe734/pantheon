"""Independent operator probe: raw owner evidence versus deliberately corrupted DTOs."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

PATH = Path(__file__).parent / "probes/trading-pulse-operator/__main__.py"
SPEC = importlib.util.spec_from_file_location("trading_pulse_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

AT = "2026-11-22T12:00:00Z"
START = "2026-11-01T00:00:00Z"


def fixture():
    people = [{"id": "p1", "name": "青禾", "tenant_id": "tenant-a"},
              {"id": "p2", "name": "澄川", "tenant_id": "tenant-a"},
              {"id": "p-probe", "name": "fixture", "tenant_id": "tenant-a", "metadata": {"is_probe": True}},
              {"id": "foreign", "name": "other", "tenant_id": "tenant-b"}]
    bindings = [{"runtime_id": "r-" + p["id"], "binding_id": "b-" + p["id"], "persona_id": p["id"],
                 "tenant_id": p["tenant_id"], "deployment_stage": "paper", "status": "active"} for p in people]
    raw = {"r-p1": {"runtime_id": "r-p1", "binding_id": "b-p1", "tenant_id": "tenant-a", "last_event_at": AT,
                      "pnl_at": AT, "pnl": -168.085, "pnl_currency": "TWD", "pnl_period_start_at": START,
                      "pnl_definition": "net_ledger_pnl", "drawdown_at": AT, "window": "1h", "drawdown": 0, "sharpe_ratio": None, "avg_slippage_bps": 0, "ledger_fill_count": 1},
           "r-p2": {"runtime_id": "r-p2", "binding_id": "b-p2", "tenant_id": "tenant-a", "last_event_at": AT,
                      "pnl_at": None, "pnl": None, "drawdown": None, "sharpe_ratio": None,
                      "avg_slippage_bps": 0, "ledger_fill_count": 0,
                      "fill_rate_details": {"status": "known", "numerator": 0, "denominator": 452, "unit": "shares",
                                            "basis": "filled_quantity_over_submitted_quantity", "window": {"start_at": START, "end_exclusive_at": AT}}}}
    scope = {"status": "verified", "tenant_id": "tenant-a", "persona_count": 2, "runtime_count": 2,
             "by_stage": {"paper": {"persona_count": 2, "runtime_count": 2}}}
    rows = []
    for person in people[:2]:
        r = raw["r-" + person["id"]]
        rows.append({"runtime_id": r["runtime_id"], "runtime_binding_id": r["binding_id"], "persona_id": person["id"], "persona_name": person["name"],
            "telemetry_summary": {"metrics": {k: r.get(k) for k in ("pnl", "drawdown", "sharpe_ratio", "avg_slippage_bps")}},
            "performance_context": {"latest_report_at": AT, "snapshot_at": AT, "pnl_at": r.get("pnl_at"), "fill_rate_details": r.get("fill_rate_details")},
            "latest_outcome": {}, "links": {"persona": "/management/personas/" + person["id"]}})
        rows[-1]["telemetry_summary"]["metrics"].update(total_trades=r["ledger_fill_count"], fill_rate=0 if person["id"] == "p2" else None)
    rows[1]["latest_outcome"] = {"journey_id": "tj-refused", "reason_code": "insufficient_cash", "source_event_id": "immutable-event",
        "required": 100034.3461, "available": 100000, "requested_quantity": 452, "currency": "TWD", "submitted_to_broker": False}
    rows[1]["links"]["journey"] = "/management/trade-journeys/tj-refused?tenant_id=tenant-a&environment=paper"
    summary = {"operator_scope": scope, "runtime_count": 2, "total_trades": 1, "pnl_groups": [
        {"stage": "paper", "currency": "TWD", "pnl_definition": "net_ledger_pnl", "period_start_at": START, "period_end_at": AT, "as_of": AT,
         "value": -168.085, "covered_persona_count": 1, "eligible_persona_count": 2}]}
    pulse = {"summary": summary, "runtime_rows": rows}
    blocks = []
    for metric in ("pnl", "drawdown", "fill_rate", "sharpe_ratio"):
        items = []
        for row in rows:
            value = row["telemetry_summary"]["metrics"].get(metric)
            if value is not None:
                items.append({"runtime_id": row["runtime_id"], "runtime_binding_id": row["runtime_binding_id"],
                    "persona_name": row["persona_name"], "links": row["links"], "ranking_metric_value": value})
        blocks.append({"metric": metric, "items": items, "eligible_item_count": len(items), "missing_metric_count": 2 - len(items)})
    rankings = {"summary": {"operator_scope": copy.deepcopy(scope)}, "items": blocks}
    journey = {"journey_id": "tj-refused", "quantity": 452, "outcome": {"kind": "rejected", "reason_code": "insufficient_cash", "source_event_id": "immutable-event", "submitted_to_broker": False,
        "reason_detail": {"required": 100034.3461, "available": 100000, "requested_quantity": 452, "currency": "TWD"}}}
    return {"people": people, "bindings": bindings, "raw": raw, "pulse": pulse, "rankings": rankings, "journey": journey}


def fetcher(world):
    def fetch(path):
        parsed = urlsplit(path)
        if parsed.path == "/api/v1/personas":
            return {"data": world["people"]}
        if parsed.path == "/api/v1/runtime-bindings":
            return {"data": world["bindings"]}
        if parsed.path == "/bff/management/trading-pulse":
            return {"data": world["pulse"]}
        if parsed.path.endswith("/rankings"):
            return {"data": world["rankings"]}
        if parsed.path.startswith("/api/v1/telemetry/"):
            return {"data": world["raw"][parsed.path.split("/")[-2]]}
        if parsed.path == "/bff/management/trade-journeys":
            q = parse_qs(parsed.query)
            assert q["to"] == [AT]
            return {"data": {"items": [world["journey"]] if q["q"] == ["b-p2"] else []}}
        raise AssertionError(path)
    return fetch


def test_probe_passes_independent_sources_with_zero_and_unknown():
    assert probe.check(fetcher(fixture()), "tenant-a")["passed"]


@pytest.mark.parametrize("mutation", ["probe_row", "denominator", "zero_to_null", "unknown_to_zero", "remove_refusal", "wrong_amount", "wrong_unit", "wrong_name", "wrong_pnl_group", "ranking_probe"])
def test_probe_rejects_corrupted_aggregate(mutation):
    world = fixture()
    rows = world["pulse"]["runtime_rows"]
    if mutation == "probe_row":
        rows.append({**rows[0], "runtime_id": "r-p-probe", "runtime_binding_id": "b-p-probe"})
    elif mutation == "denominator":
        world["pulse"]["summary"]["operator_scope"]["persona_count"] = 3
    elif mutation == "zero_to_null":
        rows[1]["telemetry_summary"]["metrics"]["total_trades"] = None
    elif mutation == "unknown_to_zero":
        rows[1]["telemetry_summary"]["metrics"]["pnl"] = 0
    elif mutation == "remove_refusal":
        rows[1]["latest_outcome"] = {}
    elif mutation == "wrong_amount":
        rows[1]["latest_outcome"]["required"] = 99892
    elif mutation == "wrong_unit":
        rows[1]["performance_context"]["fill_rate_details"] = {**rows[1]["performance_context"]["fill_rate_details"], "unit": "orders"}
    elif mutation == "wrong_name":
        rows[1]["persona_name"] = "fake"
    elif mutation == "wrong_pnl_group":
        world["pulse"]["summary"]["pnl_groups"][0]["value"] = 168.085
    elif mutation == "ranking_probe":
        world["rankings"]["items"][0]["items"].append({"runtime_id": "r-p-probe", "runtime_binding_id": "b-p-probe"})
    report = probe.check(fetcher(world), "tenant-a")
    assert not report["passed"] and report["failed"]


@pytest.mark.parametrize("change", ["source_failure", "old_binding", "source_advanced", "missing_identity", "missing_refusal_evidence"])
def test_lost_or_moving_sources_are_unverifiable(change):
    world = fixture()
    fetch = fetcher(world)
    if change == "source_failure":
        base = fetch
        def fetch(path):
            return {"data": [], "meta": {"status": "unavailable"}} if path == "/api/v1/personas" else base(path)
    elif change == "old_binding":
        world["raw"]["r-p2"]["binding_id"] = "old"
    elif change == "source_advanced":
        world["raw"]["r-p2"]["last_event_at"] = "2026-11-22T13:00:00Z"
    elif change == "missing_identity":
        world["people"] = world["people"][1:]
    elif change == "missing_refusal_evidence":
        world["journey"]["outcome"].pop("source_event_id")
    with pytest.raises(probe.Unverifiable):
        probe.check(fetch, "tenant-a")


def test_missing_environment_is_skip_not_pass(monkeypatch):
    for key in ("ACCEPTANCE_BFF_URL", "ACCEPTANCE_VIEWER_CLIENT_ID", "ACCEPTANCE_VIEWER_CLIENT_SECRET", "ACCEPTANCE_TENANT_ID"):
        monkeypatch.delenv(key, raising=False)
    assert probe.main() == 77
