"""create_plan accepts pool envelopes for paper, including multi-symbol (binding.metadata.symbols)."""
import copy
import json

import pytest

from services.deployment.test_inline_paper_execution_projection import _inline_registry
from services.deployment.test_service import _plan_payload, client  # noqa: F401
from services.registry.portfolio_envelope import build_portfolio_envelope
from services.registry.strategy_artifact import strategy_artifact_checksum
from services.governance.test_approval_authority import approval_snapshot


def _install_envelope(test_client, governance_dir, member_specs):
    """member_specs: list of (symbols, lookback, weight) built from the real momentum artifact."""
    base, entry = _inline_registry(test_client, governance_dir)
    members = []
    for i, (symbols, lookback, weight) in enumerate(member_specs):
        child = copy.deepcopy(base)
        child.update(artifact_id=f"child-{i}", strategy_id=f"child-s-{i}")
        child["parameters"]["symbols"] = symbols
        key = child["strategy_logic"]["lookback_parameter"]
        child["parameters"][key] = lookback
        for control in child["mutation_surface"]["controls"]:
            if control["parameter_key"] == key:
                control["current_value"] = lookback
                control["allowed_range"]["max"] = max(control["allowed_range"]["max"], lookback)
        members.append({"member_id": f"m{i}", "artifact": child, "risk_weight": weight})
    env = build_portfolio_envelope(
        artifact_id=entry["registry_id"], strategy_id=entry["strategy_id"],
        version=entry["version"], members=members)
    entries = json.loads(test_client.registry_snapshot.read_text())
    reg = entries[entry["registry_id"]]
    reg["checksum"] = strategy_artifact_checksum(env)
    reg["metadata"] = {"strategy_artifact": env}
    test_client.registry_snapshot.write_text(json.dumps(entries))
    approval = approval_snapshot(
        decision_id="approval-001", target_id=reg["registry_id"], target_version=reg["version"],
        target_type="registry_entry", candidate_digest=reg["checksum"],
        capital_pool_id="pool-001", persona_id="persona-ops", tenant_id="tenant-deployment-test",
    )
    (governance_dir / "approval_decisions.json").write_text(json.dumps({"approval-001": approval}))
    return env


def test_singleton_envelope_plan_is_created(client):
    test_client, governance_dir = client
    env = _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-1"))
    assert response.status_code == 201, response.text
    md = response.json()["metadata"]
    assert md["symbol"] == "2330.TW"
    assert md["market_data_policy"]["minimum_closes"] == 5
    assert md["artifact_checksum"] == strategy_artifact_checksum(env)


def test_two_member_same_symbol_envelope_uses_member_maximum(client):
    test_client, governance_dir = client
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 0.5), (["2330.TW"], 12, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-2"))
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["market_data_policy"]["minimum_closes"] == 12


def test_multi_symbol_envelope_plan_is_accepted_and_declares_every_symbol(client):
    test_client, governance_dir = client
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 0.5), (["2317.TW"], 8, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-3"))
    assert response.status_code == 201, response.text
    md = response.json()["metadata"]
    assert md["symbols"] == ["2317.TW", "2330.TW"]  # the envelope declares them sorted
    assert "symbol" not in md  # no single symbol is invented
    assert md["market_data_policy"]["minimum_closes"] == 8
    assert test_client.get("/api/deployment/plans/plan-env-3").status_code == 200


def test_single_symbol_plan_keeps_symbol_and_never_emits_symbols(client):
    test_client, governance_dir = client
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    md = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-4")).json()["metadata"]
    assert md["symbol"] == "2330.TW" and "symbols" not in md


def test_duplicate_symbols_are_refused_and_not_persisted(client):
    test_client, governance_dir = client
    env = _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    entries = json.loads(test_client.registry_snapshot.read_text())
    reg = next(iter(entries.values()))
    env["parameters"]["symbols"] = ["2330.TW", "2330.TW"]
    env2 = json.loads(json.dumps(env))
    reg["metadata"] = {"strategy_artifact": env2}
    reg["checksum"] = strategy_artifact_checksum(env2)
    test_client.registry_snapshot.write_text(json.dumps(entries))
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-5"))
    assert response.status_code == 422, response.text
    assert test_client.get("/api/deployment/plans/plan-env-5").status_code == 404


def test_envelope_member_needing_more_than_snapshot_is_refused_naming_member(client):
    test_client, governance_dir = client
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 0.5), (["2317.TW"], 120, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-long"))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "'m1'" in detail and "120" in detail and "60" in detail
    assert test_client.get("/api/deployment/plans/plan-env-long").status_code == 404


# --- DEPLOY-PLAN-HISTORY-AVAILABILITY-20261120: refuse a plan whose symbols lack governed history ---
import sys  # noqa: E402


def _planner():
    return sys.modules["services.deployment.service"].planner_service


def _report(counts):
    return lambda names: {n: {"count": counts.get(n, 0), "first_date": "2025-01-01", "last_date": "2026-11-13"} for n in names}


def test_phase1_refusal_stays_when_daily_bars_setting_is_off(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.delenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", raising=False)
    _planner().history_reader = _report({"2330.TW": 500})  # must not matter while the setting is off
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 120, 1)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-off"))
    assert response.status_code == 422 and "market snapshot serves 60" in response.json()["detail"]


def test_setting_on_lifts_snapshot_rule_and_accepts_when_history_is_enough(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    seen = []
    counts = {"2330.TW": 120, "2317.TW": 300}
    _planner().history_reader = lambda names: seen.append(list(names)) or _report(counts)(names)
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 120, 0.5), (["2317.TW"], 200, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-ok"))
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["market_data_policy"]["minimum_closes"] == 200
    assert seen == [["2317.TW", "2330.TW"]]


def test_setting_on_refuses_short_symbol_naming_symbol_available_needed_kind(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    _planner().history_reader = _report({"2330.TW": 500, "2317.TW": 150})
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 120, 0.5), (["2317.TW"], 200, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-short"))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "2317.TW" in detail and "150" in detail and "200" in detail and "short by 50" in detail
    assert "persona_portfolio_v1/" in detail
    assert test_client.get("/api/deployment/plans/plan-av-short").status_code == 404


def test_setting_on_checks_short_history_even_below_snapshot_size(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    _planner().history_reader = _report({"2330.TW": 3})
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-small"))
    assert response.status_code == 422 and "has 3 governed closes" in response.json()["detail"]


def test_source_unreachable_refuses_and_does_not_build(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    monkeypatch.setenv("DEPLOYMENT_SOURCE_INGEST_BASE_URL", "http://127.0.0.1:9")
    _planner().history_reader = None  # the real HTTP reader
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-down"))
    assert response.status_code == 422 and "history_unavailable" in response.json()["detail"]
    assert test_client.get("/api/deployment/plans/plan-av-down").status_code == 404


def test_source_url_missing_and_malformed_reply_refuse(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    monkeypatch.delenv("DEPLOYMENT_SOURCE_INGEST_BASE_URL", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_URL", raising=False)
    _planner().history_reader = None
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    r = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-nourl"))
    assert r.status_code == 422 and "not configured" in r.json()["detail"]
    _planner().history_reader = lambda names: {"2330.TW": {"count": "many"}}
    r = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-bad"))
    assert r.status_code == 422 and "reported no history" in r.json()["detail"]


def test_explicit_market_data_policy_keeps_existing_behaviour(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    _planner().history_reader = lambda names: (_ for _ in ()).throw(AssertionError("Source must not be asked"))
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    payload = _plan_payload(plan_id="plan-av-explicit")
    payload["metadata"] = {"market_data_policy": {"owner": "source-ingest", "contract": "latest_stored_normalized",
                                                  "minimum_closes": 5, "max_age_seconds": 86400}}
    assert test_client.post("/api/deployment/plans", json=payload).status_code == 201


def test_fifty_symbol_pool_refused_when_one_is_short_and_accepted_otherwise(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("DEPLOYMENT_HISTORY_FROM_DAILY_BARS", "true")
    symbols = [f"{1000 + i}.TW" for i in range(50)]
    _install_envelope(test_client, governance_dir, [(symbols, 200, 1)])
    counts = {s: 500 for s in symbols}
    _planner().history_reader = _report(counts)
    assert test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-50ok")).status_code == 201
    counts[symbols[37]] = 199
    r = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-av-50short"))
    assert r.status_code == 422 and symbols[37] in r.json()["detail"] and "199" in r.json()["detail"]
