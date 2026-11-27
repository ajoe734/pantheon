"""Independent operator probe: raw owner evidence versus deliberately corrupted DTOs."""
from __future__ import annotations

import copy
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

PATH = Path(__file__).parent / "probes/trading-pulse-operator"
SPEC = importlib.util.spec_from_loader("trading_pulse_probe", SourceFileLoader("trading_pulse_probe", str(PATH)))
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
                 "metadata": {"tenant_id": p["tenant_id"]}, "deployment_stage": "paper", "status": "active"} for p in people]
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


@pytest.mark.parametrize("wrong_excluded_count", [False, True])
def test_probe_three_trading_personas_pass_with_servant_telemetry_404(wrong_excluded_count):
    world = fixture()
    world["people"] += [
        {"id": "p3", "name": "衡岳", "tenant_id": "tenant-a"},
        {"id": "agora-servant", "name": "Agora Servant", "tenant_id": "tenant-a",
         "metadata": {"persona_class": "agora_servant"}},
    ]
    world["bindings"] += [
        {**world["bindings"][0], "persona_id": "p3", "runtime_id": "r-p3", "binding_id": "b-p3"},
        {"persona_id": "agora-servant", "runtime_id": "rt-persona-paper-servant",
         "runtime_binding_id": "rb-servant", "status": "active", "deployment_mode": "paper",
         "metadata": {"tenant_id": "tenant-a", "book_kind": "trader_servant_paper_book", "paper_only": True}},
    ]
    world["raw"]["r-p3"] = {**world["raw"]["r-p2"], "runtime_id": "r-p3", "binding_id": "b-p3"}
    world["pulse"]["runtime_rows"].append({**copy.deepcopy(world["pulse"]["runtime_rows"][1]),
        "runtime_id": "r-p3", "runtime_binding_id": "b-p3", "persona_id": "p3", "persona_name": "衡岳",
        "latest_outcome": {}, "links": {"persona": "/management/personas/p3"}})
    scope = world["pulse"]["summary"]["operator_scope"]
    scope.update(persona_count=3, runtime_count=3, excluded_non_trading_runtime_count=0 if wrong_excluded_count else 1,
                 by_stage={"paper": {"persona_count": 3, "runtime_count": 3}})
    world["rankings"]["summary"]["operator_scope"] = copy.deepcopy(scope)
    world["pulse"]["summary"]["runtime_count"] = 3
    world["pulse"]["summary"]["pnl_groups"][0]["eligible_persona_count"] = 3
    for block in world["rankings"]["items"]:
        if block["metric"] == "fill_rate":
            block["items"].append({**copy.deepcopy(block["items"][0]), "runtime_id": "r-p3", "runtime_binding_id": "b-p3",
                                   "persona_name": "衡岳", "links": {"persona": "/management/personas/p3"}})
            block["eligible_item_count"] += 1
        else:
            block["missing_metric_count"] += 1
    base, reads = fetcher(world), []

    def fetch(path):
        reads.append(path)
        if path == "/api/v1/telemetry/rt-persona-paper-servant/summary":
            raise probe.Unverifiable("source_http_404")
        return base(path)

    report = probe.check(fetch, "tenant-a")
    assert report["runtime_count"] == report["persona_count"] == 3
    assert report["passed"] is not wrong_excluded_count
    assert report["failed"] == (["excluded_non_trading_denominator_mismatch"] if wrong_excluded_count else [])
    assert not any("rt-persona-paper-servant" in path for path in reads)


@pytest.mark.parametrize("location", ["metadata", "top_level", "both"])
def test_probe_binding_tenant_wire_locations(location):
    # Viewer witness 2026-11-22T13:15Z: all four active owner bindings
    # omit top-level tenant_id and carry metadata.tenant_id instead.
    world = fixture()
    for binding in world["bindings"]:
        if location in {"top_level", "both"}:
            binding["tenant_id"] = binding["metadata"]["tenant_id"]
        if location == "top_level":
            binding.pop("metadata")
    assert probe.check(fetcher(world), "tenant-a") == {
        "passed": True, "failed": [], "runtime_count": 2, "persona_count": 2}


@pytest.mark.parametrize("binding_tenant,metadata_tenant", [
    ("tenant-a", "tenant-b"), ("tenant-b", "tenant-a"), (None, None),
])
def test_probe_cannot_verify_missing_or_conflicting_binding_tenant(binding_tenant, metadata_tenant):
    world = fixture()
    world["bindings"][0].update(tenant_id=binding_tenant, metadata={"tenant_id": metadata_tenant})
    with pytest.raises(probe.Unverifiable, match="independent_binding_tenant_unverified"):
        probe.check(fetcher(world), "tenant-a")


@pytest.mark.parametrize("inactive", [{"status": "retired"}, {"status": "FAILED"}, {"retired_at": AT}])
def test_probe_excludes_old_and_failed_bindings(inactive):
    world = fixture()
    old = {**world["bindings"][0], "runtime_id": "r-old", "binding_id": "b-old", **inactive}
    world["bindings"].append(old)
    assert probe.check(fetcher(world), "tenant-a")["passed"]


def test_probe_rejects_empty_aggregate_when_metadata_scoped_bindings_exist():
    world = fixture()
    world["pulse"]["runtime_rows"] = []
    report = probe.check(fetcher(world), "tenant-a")
    assert not report["passed"]
    assert "operator_roster_mismatch" in report["failed"]
    assert report["runtime_count"] == 2


def test_viewer_witness_wire_roster_has_three_operators_not_zero():
    # Minimal wire projection of the independent viewer sample captured at
    # 2026-11-22T13:15:36Z (owner-witness-20261122.json). Four active
    # metadata-scoped bindings; the release persona is marked data_scope=test.
    sample = [
        ("persona-d64835a203ce276b8fbb", "rt-49c1c3d1", "rb-23c9d71a91c5420dae77ca14eea9f988", "test"),
        ("persona-6507750312870d03a241", "rt-3252fa75", "rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4", None),
        ("persona-2448c7053e33ea48da47", "rt-ad81501b", "rb-eb74118dd3ca4b169496b1c239087512", None),
        ("persona-ee7112a829d38b9135ac", "rt-3b65a843", "rb-2b0df30559154a93902c209b0318d95c", None),
    ]
    world = fixture()
    world["people"] = [{"id": pid, "tenant_id": "tenant-dev", "data_scope": scope} for pid, _, _, scope in sample]
    world["bindings"] = [{"persona_id": pid, "runtime_id": rid, "binding_id": bid,
        "metadata": {"tenant_id": "tenant-dev"}, "deployment_mode": "paper", "status": "active"}
        for pid, rid, bid, _ in sample]
    world["pulse"]["runtime_rows"] = []
    report = probe.check(fetcher(world), "tenant-dev")
    assert report["runtime_count"] == report["persona_count"] == 3
    assert not report["passed"] and "operator_roster_mismatch" in report["failed"]


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


@pytest.mark.parametrize("change", ["source_failure", "old_binding", "missing_identity", "missing_refusal_evidence"])
def test_lost_or_moving_sources_are_unverifiable(change):
    world = fixture()
    fetch = fetcher(world)
    if change == "source_failure":
        base = fetch
        def fetch(path):
            return {"data": [], "meta": {"status": "unavailable"}} if path == "/api/v1/personas" else base(path)
    elif change == "old_binding":
        world["raw"]["r-p2"]["binding_id"] = "old"
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


def test_stage_discovers_and_executes_probe_without_pythonpath_or_tenant(tmp_path):
    from scripts.dev_acceptance_stage import discover_probes
    found = next(p for p in discover_probes(PATH.parent) if p["id"] == "probe:trading-pulse-operator")
    assert found["owner"] == "BFF-TRADING-PULSE-OPERATOR-FACTS-20261122"
    assert found["executable"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_") and k != "PYTHONPATH"}
    done = subprocess.run([str(PATH.resolve())], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 77, done.stderr
    assert json.loads(done.stdout)["unverifiable"] == "missing_environment"


@pytest.mark.parametrize("tenant_state", ["verified", "missing", "not_allowed"])
def test_stage_viewer_contract_verifies_tenant_without_extra_environment(monkeypatch, tenant_state, capsys):
    world = fixture()
    requests = []
    for k in ("ACCEPTANCE_TENANT_ID", "PYTHONPATH"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {"ACCEPTANCE_BFF_URL": "https://bff.example", "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer",
                 "ACCEPTANCE_VIEWER_CLIENT_SECRET": "private-client-secret"}.items():
        monkeypatch.setenv(k, v)
    def urlopen(req, timeout):
        assert timeout == 30
        requests.append(req)
        path = req.full_url.removeprefix("https://bff.example")
        if path == "/bff/auth/dev-login":
            assert req.method == "POST"
            body = {"access_token": "private-access-token"}
        else:
            assert req.method == "GET" and req.get_header("Authorization") == "Bearer private-access-token"
            assert req.get_header("X-tenant-id") is None
            if path == "/bff/me":
                body = {"data": {"tenant_id": None if tenant_state == "missing" else "tenant-a",
                                 "allowed_tenants": [] if tenant_state == "not_allowed" else ["tenant-a"]}}
            else:
                body = fetcher(world)(path)
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(probe.urllib.request, "urlopen", urlopen)
    assert probe.main() == (0 if tenant_state == "verified" else 77)
    output = capsys.readouterr().out
    assert "private-client-secret" not in output and "private-access-token" not in output
    if tenant_state != "verified":
        assert len(requests) == 2
        assert json.loads(output)["unverifiable"] == "viewer_tenant_unverified"


def test_rankings_keep_navigation_without_loading_journey_evidence():
    world = fixture()
    for block in world["rankings"]["items"]:
        for item in block["items"]:
            item["links"] = {k: v for k, v in item["links"].items() if k != "journey"}
    assert probe.check(fetcher(world), "tenant-a")["passed"]


@pytest.mark.parametrize("quantity", [452, None])
def test_live_journey_uses_viewer_allowed_binding_resolver(quantity):
    world = fixture()
    for b in world["bindings"]:
        b["deployment_stage"] = "live"
    scope = world["pulse"]["summary"]["operator_scope"]
    scope["by_stage"] = {"live": scope["by_stage"]["paper"]}
    world["rankings"]["summary"]["operator_scope"] = copy.deepcopy(scope)
    world["pulse"]["summary"]["pnl_groups"][0]["stage"] = "live"
    world["pulse"]["runtime_rows"][1]["latest_outcome"]["requested_quantity"] = quantity
    world["journey"].update(runtime_id="r-p2", quantity=quantity if quantity is not None else "***")
    def fetch(path):
        parsed = urlsplit(path)
        q = parse_qs(parsed.query)
        if parsed.path.endswith("/resolve"):
            assert q["environment"] == ["live"] and q["identifier_type"] == ["binding_id"]
            return {"data": {"journey_ids": ["tj-refused"] if q["q"] == ["b-p2"] else []}}
        if parsed.path == "/bff/management/trade-journeys":
            assert "q" not in q and q["page_size"] == ["200"] and q["to"] == [AT]
            return {"data": {"items": [world["journey"]] if q["persona_id"] == ["p2"] else []}}
        return fetcher(world)(path)
    assert probe.check(fetch, "tenant-a")["passed"]


def heartbeat_fetcher(world, advances):
    """Fake source: the heartbeat moves raw timestamps `advances` times, each
    right after the pulse was read (before the raw summaries), like the 15 s
    heartbeat; the pulse always reports the raw value of its own read."""
    base = fetcher(world)
    state = {"left": advances, "ticks": 0, "pulse_calls": 0}

    def fetch(path):
        parsed = urlsplit(path)
        if parsed.path == "/bff/management/trading-pulse":
            state["pulse_calls"] += 1
            snap = copy.deepcopy(world["pulse"])
            for row in snap["runtime_rows"]:
                raw = world["raw"][row["runtime_id"]]
                row["performance_context"]["latest_report_at"] = raw["last_event_at"]
            if state["left"] > 0:
                state["left"] -= 1
                state["ticks"] += 1
                for raw in world["raw"].values():
                    raw["last_event_at"] = f"2026-11-22T12:00:{state['ticks']:02d}Z"
            return {"data": snap}
        return copy.deepcopy(base(path))  # a real source returns fresh objects
    fetch.state = state
    return fetch


def test_consistent_on_first_read():
    world = fixture()
    fetch = heartbeat_fetcher(world, 0)
    report = probe.check(fetch, "tenant-a")
    assert report["passed"] and fetch.state["pulse_calls"] == 1


def test_consistent_on_a_later_attempt():
    world = fixture()
    fetch = heartbeat_fetcher(world, 3)
    report = probe.check(fetch, "tenant-a")
    assert report["passed"] and fetch.state["pulse_calls"] == 4


def test_never_consistent_is_unverifiable_with_attempt_count():
    world = fixture()
    fetch = heartbeat_fetcher(world, 100)
    with pytest.raises(probe.Unverifiable, match=r"source_changed_during_read:attempts=6"):
        probe.check(fetch, "tenant-a")
    assert fetch.state["pulse_calls"] == 6


def test_stale_pulse_against_unchanged_source_fails():
    world = fixture()
    world["raw"]["r-p2"]["last_event_at"] = "2026-11-22T13:00:00Z"
    fetch = fetcher(world)
    calls = []
    report = probe.check(lambda path: calls.append(path) or fetch(path), "tenant-a")
    assert not report["passed"] and "pulse_stale_against_unchanged_source" in report["failed"]
    assert sum(1 for c in calls if c == "/bff/management/trading-pulse") == 2


def test_time_budget_stops_retries_without_sleeping():
    world = fixture()
    ticks = iter(range(0, 1000, 100))
    fetch = heartbeat_fetcher(world, 100)
    with pytest.raises(probe.Unverifiable, match="time_budget"):
        probe.check(fetch, "tenant-a", clock=lambda: next(ticks), budget=240)


# Hosted shape seen on dev 2026-11-26 (BFF 8e67d240e, after #7872): raw
# telemetry pnl_currency is null; the ledger's positions, fills and marks are
# all TWD; paper runtimes emit pnl only on fills, so two runtimes have
# different pnl_at (two groups); one runtime has no PnL at all.
PNL3_AT = "2026-11-23T23:34:52Z"
TWD_FACTS = {"positions": [{"symbol": "2330.TW", "quantity": 1000, "currency": "TWD"}],
             "last_fill": {"symbol": "2330.TW", "currency": "TWD"}, "_currency_by_symbol": {"2330.TW": "TWD"}}


def ranking_blocks(rows):
    blocks = []
    for metric in ("pnl", "drawdown", "fill_rate", "sharpe_ratio"):
        items = [{"runtime_id": r["runtime_id"], "runtime_binding_id": r["runtime_binding_id"], "persona_name": r["persona_name"],
                  "links": r["links"], "ranking_metric_value": r["telemetry_summary"]["metrics"][metric]}
                 for r in rows if r["telemetry_summary"]["metrics"].get(metric) is not None]
        blocks.append({"metric": metric, "items": items, "eligible_item_count": len(items), "missing_metric_count": len(rows) - len(items)})
    return blocks


def hosted_fixture():
    world = fixture()
    person = {"id": "p3", "name": "遠山", "tenant_id": "tenant-a"}
    world["people"].insert(2, person)
    world["bindings"].append({"runtime_id": "r-p3", "binding_id": "b-p3", "persona_id": "p3",
                              "metadata": {"tenant_id": "tenant-a"}, "deployment_stage": "paper", "status": "active"})
    world["raw"]["r-p3"] = {**copy.deepcopy(world["raw"]["r-p1"]), "runtime_id": "r-p3", "binding_id": "b-p3",
                            "pnl_at": PNL3_AT, "pnl": 25.5, "drawdown_at": PNL3_AT, "ledger_fill_count": 2}
    for rid in ("r-p1", "r-p3"):
        world["raw"][rid].update(pnl_currency=None, **copy.deepcopy(TWD_FACTS))
    world["raw"]["r-p2"]["pnl_currency"] = None
    rows = world["pulse"]["runtime_rows"]
    r = world["raw"]["r-p3"]
    rows.append({**copy.deepcopy(rows[0]), "runtime_id": "r-p3", "runtime_binding_id": "b-p3", "persona_id": "p3", "persona_name": "遠山",
                 "links": {"persona": "/management/personas/p3"}})
    rows[2]["performance_context"]["pnl_at"] = PNL3_AT
    rows[2]["telemetry_summary"]["metrics"].update(pnl=25.5, total_trades=2)
    summary = world["pulse"]["summary"]
    summary["operator_scope"].update(persona_count=3, runtime_count=3, by_stage={"paper": {"persona_count": 3, "runtime_count": 3}})
    summary.update(runtime_count=3, total_trades=3)
    first = summary["pnl_groups"][0]
    first["eligible_persona_count"] = 3
    summary["pnl_groups"].append({**first, "period_end_at": PNL3_AT, "as_of": PNL3_AT, "value": 25.5})
    world["rankings"] = {"summary": {"operator_scope": copy.deepcopy(summary["operator_scope"])}, "items": ranking_blocks(rows)}
    return world


def test_hosted_shape_resolves_currency_from_ledger_not_raw_claim():
    world = hosted_fixture()
    assert probe.check(fetcher(world), "tenant-a") == {"passed": True, "failed": [], "runtime_count": 3, "persona_count": 3}


@pytest.mark.parametrize("mutation,failure", [
    ("group_currency", "pnl_group_scope_mismatch"),
    ("value_off_by_one", "pnl_group_value_mismatch"),
    ("covered", "pnl_group_denominator_mismatch"),
    ("eligible", "pnl_group_denominator_mismatch"),
    ("raw_claim_disagrees", "raw_pnl_currency_ledger_mismatch"),
])
def test_hosted_shape_still_rejects_wrong_groups(mutation, failure):
    world = hosted_fixture()
    group = world["pulse"]["summary"]["pnl_groups"][1]
    if mutation == "group_currency":
        group["currency"] = "USD"
    elif mutation == "value_off_by_one":
        group["value"] = 26.5
    elif mutation == "covered":
        group["covered_persona_count"] = 2
    elif mutation == "eligible":
        group["eligible_persona_count"] = 2
    elif mutation == "raw_claim_disagrees":
        world["raw"]["r-p3"]["pnl_currency"] = "USD"
    report = probe.check(fetcher(world), "tenant-a")
    assert not report["passed"] and failure in report["failed"]


@pytest.mark.parametrize("facts", [
    {"positions": [{"symbol": "2330.TW", "quantity": 1000, "currency": "TWD"}, {"symbol": "AAPL", "quantity": 5, "currency": "USD"}]},
    {"positions": [{"symbol": "2330.TW", "quantity": 1000}], "last_fill": None, "_currency_by_symbol": {}},
    {"ledgers": [{"pnl_currency_status": "mixed"}]},
], ids=["mixed", "unknown_active_position", "mixed_constituent_ledger"])
def test_mixed_or_unknown_currency_stays_out_of_expected_groups(facts):
    world = hosted_fixture()
    world["raw"]["r-p3"].update(copy.deepcopy(facts))
    report = probe.check(fetcher(world), "tenant-a")
    assert not report["passed"] and "pnl_group_scope_mismatch" in report["failed"]
    # #7872 keeps pnl_currency null there: no group and no pnl ranking for it.
    world["pulse"]["summary"]["pnl_groups"].pop()
    pnl = world["rankings"]["items"][0]
    pnl["items"] = [i for i in pnl["items"] if i["runtime_id"] != "r-p3"]
    pnl.update(eligible_item_count=1, missing_metric_count=2)
    assert probe.check(fetcher(world), "tenant-a")["passed"]


def test_raw_claim_agreeing_with_ledger_passes():
    world = hosted_fixture()
    for rid in ("r-p1", "r-p3"):
        world["raw"][rid]["pnl_currency"] = "twd"
    assert probe.check(fetcher(world), "tenant-a")["passed"]


def test_currency_needs_no_extra_request():
    world = hosted_fixture()
    calls = []
    fetch = fetcher(world)
    probe.check(lambda path: calls.append(path) or fetch(path), "tenant-a")
    assert sum(1 for c in calls if c.startswith("/api/v1/telemetry/")) == 3
    assert not any("position" in c or "ledger" in c for c in calls)
