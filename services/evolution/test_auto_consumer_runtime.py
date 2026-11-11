"""Runtime wiring: scope from real allocator records, decay telemetry -> retire, through the real evolution service."""

import json

from fastapi.testclient import TestClient

import services.evolution.auto_consumer_runtime as rt
from services.evolution.test_governed_persona_lifecycle import FakeWorld

TENANT = "default"
ENV = {
    "EVOLUTION_AUTO_CONSUMER_ENABLED": "true", "EVOLUTION_API_URL": "http://evo",
    "EVOLUTION_SCHEDULER_TENANT_ID": TENANT,
    "AUTO_CONSUMER_PERSONA_URL": "http://per", "AUTO_CONSUMER_GOVERNANCE_URL": "http://gov",
    "AUTO_CONSUMER_CAPITAL_URL": "http://cap", "AUTO_CONSUMER_REGISTRY_URL": "http://reg",
    "AUTO_CONSUMER_PROPOSER_TOKEN": "tp", "AUTO_CONSUMER_PROPOSER_ID": "proposer",
    "AUTO_CONSUMER_GATE_TOKEN": "tg", "AUTO_CONSUMER_GATE_ID": "gate", "AUTO_CONSUMER_PERSONA_TOKEN": "tper",
    "AUTO_CONSUMER_CAPITAL_TOKEN": "tcap", "AUTO_CONSUMER_REGISTRY_TOKEN": "treg",
}

SPEC_ID = "spec-disc-1"
ARTIFACT_ID = "art-disc-1"
BINDING_ID = "pcb-discovery-abc123"


def _binding(**over):
    # Exactly the shape the discovery allocator's provision() creates: no persona metadata markers.
    row = {"binding_id": BINDING_ID, "tenant_id": TENANT, "persona_id": "p1", "status": "active", "budget": 10000,
           "metadata": {"strategy_spec_registry_id": SPEC_ID, "tenant_id": TENANT}}
    row.update(over)
    return row


def _owners(bindings=None, persona_state="paper_owner", kind="factor_rule_v1", persona_tenant=TENANT):
    bindings = [_binding()] if bindings is None else bindings
    persona = {"persona_id": "p1", "tenant_id": persona_tenant, "lifecycle_state": persona_state, "metadata": {}}

    def call(method, url, headers, body):
        if url == "http://cap/api/bindings":
            return 200, bindings
        if url == f"http://reg/api/registry/strategy-artifacts/{ARTIFACT_ID}":
            return 200, {"entry": {"registry_id": ARTIFACT_ID, "owner_tenant": TENANT, "metadata": {
                "strategy_artifact": {"strategy_logic": {"kind": kind}, "lineage": {"source_strategy_spec_id": SPEC_ID}}}}}
        if url.startswith("http://reg/"):
            return 404, {"detail": "nf"}
        if url == "http://per/api/personas/p1":
            return 200, persona
        raise AssertionError(url)

    return call, persona


def _scope(call):
    return rt.DiscoveryBindingScope(capital_url="http://cap", registry_url="http://reg", persona_url="http://per",
                                    capital_token=lambda: "c", registry_token=lambda: "r", persona_token=lambda: "p",
                                    transport=call)


def test_disabled_by_default():
    assert not rt.enabled({}) and rt.enabled(ENV)


def test_allocated_persona_resolves_without_any_persona_metadata_injection():
    call, _ = _owners()
    got = _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID})
    assert got["persona_id"] == "p1" and got["stage"] == "paper" and got["strategy_kind"] == "factor_rule_v1"
    # the admitted spec itself is also an in-scope target
    assert _scope(call).resolve({"tenant_id": TENANT, "target_id": SPEC_ID})["persona_id"] == "p1"


def test_scope_fails_closed():
    for kw in ({"bindings": []},
               {"bindings": [_binding(status="retired")]},
               {"bindings": [_binding(binding_id="pcb-manual-1")]},
               {"bindings": [_binding(), _binding(binding_id="pcb-discovery-zzz", persona_id="p2")]},
               {"persona_tenant": "other"}):
        call, _ = _owners(**kw)
        assert _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID}) is None, kw
    call, _ = _owners()
    assert _scope(call).resolve({"tenant_id": "other", "target_id": ARTIFACT_ID}) is None


# ------------------------------------------------------------ decay telemetry -> governed retire

def _summary(sharpe, dd, at):
    return {"runtime_id": "rt1", "deployment_stage": "paper", "persona_capital_binding_id": BINDING_ID,
            "binding_id": "rb1", "artifact_id": ARTIFACT_ID, "artifact_version": "1.0.0",
            "sharpe_ratio": sharpe, "sharpe_ratio_at": at, "sharpe_ratio_binding_id": "rb1", "drawdown": dd}


def _stack(tmp_path, monkeypatch, summaries):
    from services.evolution import threshold_sweep_worker as tsw
    import services.evolution.main as evo_main
    from services.evolution.main import app

    evo_main.store._decisions.clear()
    if evo_main.store._storage_path and evo_main.store._storage_path.exists():
        evo_main.store._storage_path.unlink()
    client = TestClient(app)
    owners, persona = _owners()
    world = FakeWorld()
    world.persona = persona  # one persona record shared by lifecycle port and scope
    persona.setdefault("lifecycle_state", "paper_owner")

    def transport(method, url, headers, body):
        if url.startswith("http://evo"):
            path = url[len("http://evo"):]
            r = client.request(method, path, json=body, headers={"X-Tenant-Id": TENANT})
            return r.status_code, (r.json() if r.content else {})
        if url.startswith("http://tel"):
            return 200, {"summaries": summaries()}
        if url.startswith("http://gov") or url == "http://per/api/personas/p1/lifecycle":
            return world(method, url, headers, body)
        return owners(method, url, headers, body)

    monkeypatch.setattr(tsw, "load_baselines", lambda path=None: {
        ARTIFACT_ID: {"expected_sharpe_ratio": 1.0, "expected_drawdown": 0.10, "policy_source": "docs/policy.md#decision"}})
    monkeypatch.setattr(tsw, "default_fetch_summaries",
                        lambda url, timeout, tenant_id=None, service_token=None: transport("GET", "http://tel/x", {}, None)[1]["summaries"])
    env = dict(ENV, AUTO_CONSUMER_EVIDENCE_PATH=str(tmp_path / "ev.jsonl"),
               AUTO_CONSUMER_DECAY_LEDGER_PATH=str(tmp_path / "windows.jsonl"))
    return client, transport, env, persona


def test_paper_decay_telemetry_drives_the_governed_retire_through_the_scheduler_tick(tmp_path, monkeypatch):
    series = [_summary(0.8, 0.1, "2026-11-01T00:00:00Z")]
    client, transport, env, persona = _stack(tmp_path, monkeypatch, lambda: series)
    first = rt.run_consumer_tick(env, transport)
    assert first["decay"]["proposed"] == [] and persona["lifecycle_state"] == "paper_owner"  # one window: no decay
    series[:] = [_summary(0.6, 0.1, "2026-11-02T00:00:00Z")]
    rt.run_consumer_tick(env, transport)
    series[:] = [_summary(0.3, 0.1, "2026-11-03T00:00:00Z")]
    third = rt.run_consumer_tick(env, transport)
    assert len(third["decay"]["proposed"]) == 1 and third["decay"]["errors"] == {}, third
    decision_id = third["decay"]["proposed"][0]
    assert third["approved"] == [decision_id] and third["retired"] == [decision_id], third
    assert persona["lifecycle_state"] == "retired"
    final = client.get(f"/api/evolution/proposals/{decision_id}", headers={"X-Tenant-Id": TENANT}).json()
    assert final["decision_state"] == "approved" and final["action_type"] == "retire" and final["risk_level"] == "low"
    events = [json.loads(line)["event"] for line in (tmp_path / "ev.jsonl").read_text().splitlines()]
    assert {"paper_decay_detected", "retire_proposed", "auto_gate_approved", "persona_retired"} <= set(events)

    # Replay is idempotent: nothing new is proposed, approved or retired.
    again = rt.run_consumer_tick(env, transport)
    assert again["decay"]["proposed"] == [] and again["retired"] == [] and again["errors"] == {}


def test_failed_proposal_is_retried_next_tick(tmp_path, monkeypatch):
    series = [_summary(0.8, 0.1, "2026-11-01T00:00:00Z")]
    client, transport, env, persona = _stack(tmp_path, monkeypatch, lambda: series)
    rt.run_consumer_tick(env, transport)
    series[:] = [_summary(0.6, 0.1, "2026-11-02T00:00:00Z")]
    rt.run_consumer_tick(env, transport)
    series[:] = [_summary(0.3, 0.1, "2026-11-03T00:00:00Z")]
    down = {"on": True}

    def flaky(method, url, headers, body):
        if down["on"] and method == "POST" and url.endswith("/api/evolution/proposals"):
            return 503, {"detail": "down"}
        return transport(method, url, headers, body)

    failed = rt.run_consumer_tick(env, flaky)
    assert failed["decay"]["errors"] and persona["lifecycle_state"] == "paper_owner"
    down["on"] = False
    ok = rt.run_consumer_tick(env, flaky)
    assert len(ok["decay"]["proposed"]) == 1 and persona["lifecycle_state"] == "retired"


def test_missing_approved_baseline_never_retires(tmp_path, monkeypatch):
    from services.evolution import threshold_sweep_worker as tsw

    series = [_summary(0.1, 0.9, f"2026-11-0{i}T00:00:00Z") for i in (1,)]
    client, transport, env, persona = _stack(tmp_path, monkeypatch, lambda: series)
    monkeypatch.setattr(tsw, "load_baselines", lambda path=None: {})
    for i in (1, 2, 3):
        series[:] = [_summary(0.1, 0.9, f"2026-11-0{i}T00:00:00Z")]
        out = rt.run_consumer_tick(env, transport)
    assert out["decay"]["proposed"] == [] and persona["lifecycle_state"] == "paper_owner"
