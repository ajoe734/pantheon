from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from services.research import discovery_allocator as da
from services.research.tests.test_research_orchestrator_http_service import _load_service_module


PROFILE = {"persona_id": "discovery-mandate", "mandate": "tw swing"}


def _wire(module, calls):
    def factory():
        return da.DiscoveryAllocator(
            scorer=lambda p, a: {"score": 90, "match_id": f"m-{a['strategy_spec_id']}"},
            provision=lambda req: calls.append(req) or {"persona_id": f"persona-{len(calls)}"},
            paper_capital_cap=10_000,
            per_persona_capital=100,
            max_personas=99,  # must still be clamped to 5
        )

    def resolve(passed):
        return [
            {"registry_id": f"art-{p['strategy_spec_id']}", "strategy_spec_id": p["strategy_spec_id"],
             "backtest_run_id": p["backtest_run_id"], "symbols": p["symbols"]}
            for p in passed
        ], []

    module.discovery_allocator_ports.update(
        read_marked=lambda: ([], []), resolve_candidates=resolve,
        allocator_factory=factory, mandate_profile=PROFILE,
        list_gate_evidence=_evidence,
    )


def _evidence(specs=None):
    specs = specs if specs is not None else [f"spec-{i}" for i in range(8)]
    findings = [
        {"source_event_type": "research_finding_published",
         "metadata": {"event_type": "research_finding_published", "tenant_id": "default",
                      "strategy_spec_id": s, "metrics": {"backtest_run_id": f"bt-{s}"}}}
        for s in specs
    ]
    runs = {f"bt-{s}": {"status": "completed", "artifact_checksum": "c", "dataset_symbols": ["2330"]} for s in specs}
    return findings, runs.get


def _records(module):
    path = module.store.data_dir / "discovery_allocator_runs.jsonl"
    deadline = time.time() + 5
    while time.time() < deadline and not (path.exists() and path.read_text().strip()):
        time.sleep(0.05)
    return [json.loads(line) for line in path.read_text().splitlines()]


def _admitted(n):
    return [{"strategy_spec_id": f"spec-{i}", "registry_id": f"spec-{i}", "status": "admitted"} for i in range(n)]


def _run_startup(module, admitted, env="true"):
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": env}):
        with TestClient(module.app):  # runs startup handlers -> scheduler
            return _records(module)[0]


def test_startup_schedule_creates_personas_for_gate_passed_only_cap_five() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    rec = _run_startup(module, _admitted(8))
    assert rec["enabled"] is True and rec["admitted_candidates_seen"] == 8
    assert len(rec["bound"]) == 5 == len(calls) == da.MAX_DISCOVERY_PERSONAS
    assert {c["execution_context"] for c in calls} == {"paper"}
    assert all(c["discovery_allocator"]["marker"] is True and c["symbols"] == ["2330"] for c in calls)


def test_admission_without_gate_pass_is_skipped_with_reason() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    module.discovery_allocator_ports["list_gate_evidence"] = lambda: _evidence(["spec-0"])
    rec = _run_startup(module, _admitted(3))
    assert len(rec["bound"]) == 1 and len(calls) == 1
    assert {s["artifact_ref"]: s["reason"] for s in rec["skipped"]} == {
        "spec-1": "no_gate_pass", "spec-2": "no_gate_pass"}


def test_setting_off_records_disabled_and_allocates_nothing() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    rec = _run_startup(module, _admitted(1), env="false")
    assert rec["reason"] == "disabled" and rec["bound"] == [] and calls == []


def test_missing_mandate_profile_records_reason_and_binds_nothing() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    module.discovery_allocator_ports["mandate_profile"] = None
    rec = _run_startup(module, _admitted(1))
    assert rec["reason"] == "mandate_profile_missing" and calls == []


def test_default_boot_uses_production_ports_and_never_the_capital_route() -> None:
    module = _load_service_module()  # no injection
    ports = module.discovery_allocator_ports
    for name in ("read_marked", "resolve_candidates", "allocator_factory"):
        assert callable(ports[name])
    assert ports["read_marked"].__module__ == da.__name__
    assert "list_active_bindings" not in ports
    alloc = ports["allocator_factory"]()
    assert isinstance(alloc, da.DiscoveryAllocator) and alloc.max_personas == 5
    with mock.patch.object(module, "_retrain_call", return_value=[]) as owner:
        ports["read_marked"]()
    assert owner.call_args.kwargs["url"].endswith("/api/personas")
    assert owner.call_args.kwargs["token_variable"] == "RESEARCH_PERSONA_SERVICE_TOKEN"
    src = Path(da.__file__).read_text()
    assert "paper-capital" not in src.replace("never the capital-only", "")  # no call to the capital-only route


def test_provision_posts_persona_create_with_idempotency_key() -> None:
    calls: list = []

    def call(service, method, url, body, headers=None):
        calls.append((service, method, url, body, headers))
        return {"data": {"id": "persona-x"}}

    ports = da.make_production_ports(
        call=call, persona_url="http://p", provisioning_url="http://b", registry_url="http://r",
        tenant_id="t", paper_capital_cap=50_000, per_persona_capital=10_000, mandate_profile=PROFILE)
    receipt = ports["allocator_factory"]().provision({
        "tenant_id": "t", "idempotency_key": "k1", "strategy_artifact_ref": "art-1", "budget": 10_000,
        "symbols": ["2330"], "discovery_allocator": {"marker": True, "match_id": "m", "match_score": 70.0}})
    service, method, url, body, headers = calls[-1]
    assert (service, method, url) == ("provisioning", "POST", "http://b/bff/personas")
    assert headers == {"Idempotency-Key": "k1"} and receipt["persona_id"] == "persona-x"
    assert body["strategy_artifact_ref"] == "art-1" and body["symbols"] == ["2330"] and body["market"] == "TW"
    assert body["discovery_allocator"]["match_id"] == "m" and body["name"].startswith("discovery-")


def test_partial_provision_failure_halts_and_keeps_earlier_bindings() -> None:
    attempts: list = []

    def provision(req):
        attempts.append(req)
        if len(attempts) == 2:
            raise RuntimeError("boom")
        return {"persona_id": "p"}

    alloc = da.DiscoveryAllocator(
        scorer=lambda p, a: {"score": 90, "match_id": "m"}, provision=provision,
        paper_capital_cap=100_000, per_persona_capital=1_000)
    arts = [{"registry_id": f"a{i}", "backtest_run_id": "r", "symbols": ["2330"]} for i in range(3)]
    res = alloc.allocate(candidates=arts, mandate_profile=PROFILE, tenant_id="t", date="d")
    assert len(attempts) == 2 and len(res.bindings) == 1
    assert res.error == "provision_failed_halted:RuntimeError"


def test_unreachable_owner_recorded_per_run() -> None:
    module = _load_service_module()
    failure = module._RetrainFailure("x", "persona request failed: boom")
    module.discovery_allocator_ports.update(
        list_gate_evidence=_evidence, mandate_profile=PROFILE)
    with mock.patch.object(module, "_retrain_call", side_effect=failure):
        rec = _run_startup(module, _admitted(1))
    assert rec["reason"].startswith("owner_unreachable:") and rec["bound"] == []


def test_compose_mounts_credentials_sets_dev_tenant_and_mandate_setting() -> None:
    import yaml
    root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    env = compose["services"]["research-orchestrator-svc"]["environment"]
    assert env["DISCOVERY_ALLOCATOR_TENANT_ID"].endswith(":-tenant-dev}")
    assert "DISCOVERY_ALLOCATOR_MANDATE_PROFILE" in env
    for var in ("RESEARCH_BFF_SERVICE_TOKEN", "RESEARCH_PERSONA_SERVICE_TOKEN", "RESEARCH_REGISTRY_SERVICE_TOKEN"):
        assert env[var + "_FILE"].endswith("/run/pantheon-principals/" + var + "}")
