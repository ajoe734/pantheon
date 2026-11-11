from __future__ import annotations

import json
import time
from unittest import mock

from fastapi.testclient import TestClient

from services.research import discovery_allocator as da
from services.research.tests.test_research_orchestrator_http_service import _load_service_module


def _wire(module, calls):
    def factory():
        real = da.DiscoveryAllocator(
            scorer=lambda p, a: {"score": 90, "match_id": f"m-{p['persona_id']}-{a['strategy_spec_id']}"},
            provision=calls.append,
            paper_capital_cap=10_000,
            per_persona_capital=100,
            max_personas=99,  # must still be clamped to 5
        )
        return real

    module.discovery_allocator_ports.update(
        list_personas=lambda: [{"persona_id": f"p{i}"} for i in range(8)],
        list_active_bindings=lambda: [],
        resolve_admitted=lambda adm: (list(adm), []),
        allocator_factory=factory,
    )


def _records(module):
    path = module.store.data_dir / "discovery_allocator_runs.jsonl"
    deadline = time.time() + 5
    while time.time() < deadline and not (path.exists() and path.read_text().strip()):
        time.sleep(0.05)
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_startup_schedule_runs_allocator_paper_only_cap_five() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    admitted = [{"strategy_spec_id": f"spec-{i}", "registry_id": f"spec-{i}", "status": "admitted"} for i in range(8)]
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "true"}):
        with TestClient(module.app):  # runs startup handlers -> scheduler
            rec = _records(module)[0]
    assert rec["enabled"] is True
    assert rec["admitted_candidates_seen"] == 8
    assert len(rec["bound"]) == 5 == len(calls) == da.MAX_DISCOVERY_PERSONAS
    assert {c["execution_context"] for c in calls} == {"paper"}
    assert all(b["execution_context"] == "paper" for b in rec["bound"])


def test_setting_off_records_disabled_and_allocates_nothing() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    admitted = [{"strategy_spec_id": "s", "registry_id": "s", "status": "admitted"}]
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "false"}):
        with TestClient(module.app):
            rec = _records(module)[0]
    assert rec["reason"] == "disabled"
    assert rec["bound"] == [] and calls == []


def test_no_personas_records_reason() -> None:
    module = _load_service_module()
    _wire(module, [])
    module.discovery_allocator_ports.update(list_personas=lambda: [])
    admitted = [{"strategy_spec_id": "s", "registry_id": "s", "status": "admitted"}]
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "true"}):
        with TestClient(module.app):
            rec = _records(module)[0]
    assert rec["admitted_candidates_seen"] == 1
    assert rec["reason"] == "no_discovery_personas"


def _spec_view(spec_id, *, state="approved", tenant="tenant-x", checksum="sha-1"):
    return {"entry": {
        "registry_id": spec_id, "artifact_type": "strategy_spec", "artifact_state": state,
        "owner_tenant": tenant, "checksum": checksum,
        "metadata": {"tenant_id": tenant, "strategy_spec": {"strategy_id": f"strat-{spec_id}", "title": "momentum"}},
    }}


def _real_admission(spec_id, checksum="sha-1"):
    from services.research.alpha_replication.admission import ReplicationAdmissionStore
    import tempfile
    store = ReplicationAdmissionStore(tempfile.mkdtemp())
    return store.create_admission({
        "tenant_id": "tenant-x", "strategy_spec_id": spec_id, "strategy_id": f"strat-{spec_id}",
        "spec_version": "1", "checksum": checksum, "approval_decision_id": "d1", "approver": "a",
    })


def _ports(calls, *, bindings=(), personas=None, specs=None):
    def call(service, method, url, body):
        calls.append((service, method, url, body))
        if service == "persona":
            return personas if personas is not None else [{"persona_id": "p1", "tenant_id": "tenant-x", "status": "active", "lifecycle_state": "paper_owner"}]
        if service == "capital":
            return list(bindings)
        if service == "registry":
            sid = url.rsplit("/", 1)[-1]
            if specs is None or sid not in specs:
                raise da.OwnerRejected("registry", 404, "not found")
            return specs[sid]
        return {"data": {}}
    return da.make_production_ports(
        call=call, persona_url="http://p", provisioning_url="http://b", registry_url="http://r",
        capital_url="http://c", tenant_id="tenant-x", paper_capital_cap=50_000, per_persona_capital=10_000,
    )


def test_real_admission_shape_is_resolved_to_registry_spec_and_scored() -> None:
    adm = _real_admission("spec-1")
    assert "registry_id" not in adm and "strategy_artifact_ref" not in adm  # raw admission shape
    calls: list = []
    ports = _ports(calls, specs={"spec-1": _spec_view("spec-1")})
    specs, unresolved = ports["resolve_admitted"]([adm])
    assert unresolved == [] and specs[0]["strategy_artifact_ref"] == "spec-1"
    assert specs[0]["registry_id"] == "spec-1" and specs[0]["title"] == "momentum"
    rec = da.run_scheduled_allocation(
        enabled=True, allocator_factory=lambda: da.DiscoveryAllocator(
            scorer=lambda p, a: {"score": 90, "match_id": "m"},
            provision=ports["allocator_factory"]().provision,
            paper_capital_cap=50_000, per_persona_capital=10_000),
        list_admitted=lambda: [adm], list_personas=ports["list_personas"],
        list_active_bindings=ports["list_active_bindings"], resolve_admitted=ports["resolve_admitted"],
        tenant_id="tenant-x",
    )
    assert rec["bound"][0]["artifact_ref"] == "spec-1"
    post = [c for c in calls if c[1] == "POST"][0]
    assert post[3]["strategy_spec_registry_id"] == "spec-1"


def test_unapproved_foreign_or_changed_spec_is_skipped_with_reason() -> None:
    admissions = [_real_admission(i) for i in ("a", "b", "c", "d")]
    ports = _ports([], specs={
        "a": _spec_view("a", state="draft"), "b": _spec_view("b", tenant="other"),
        "c": _spec_view("c", checksum="changed"),
    })
    specs, unresolved = ports["resolve_admitted"](admissions)
    assert specs == []
    assert {u["artifact_ref"]: u["reason"] for u in unresolved} == {
        "a": "spec_not_approved", "b": "spec_tenant_mismatch", "c": "spec_checksum_changed",
        "d": "registry_rejected:404",
    }


def test_provision_body_satisfies_real_bff_contract_and_coordinator() -> None:
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "control-plane" / "bff"
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root.parent))
    try:
        from services.control_plane.bff.personas.routes.provisioning import ChainPaperCapitalRequest  # type: ignore
    except Exception:  # noqa: BLE001
        import importlib.util
        spec = importlib.util.spec_from_file_location("_prov_routes", root / "personas" / "routes" / "provisioning.py")
        ChainPaperCapitalRequest = None
    calls: list = []
    ports = _ports(calls)
    ports["allocator_factory"]().provision({
        "tenant_id": "tenant-x", "idempotency_key": "k", "persona_id": "p1",
        "strategy_artifact_ref": "spec-1", "execution_context": "paper", "budget": 10_000,
        "metadata": {"discovery_match_id": "m", "discovery_match_score": 90},
    })
    body = calls[-1][3]
    assert body["currency"] == "USD"
    if ChainPaperCapitalRequest is not None:
        ChainPaperCapitalRequest(**body)  # extra=forbid: raises on any unknown/invalid field
    # Same persona/artifact on another day reuses the exact same pool and binding ids.
    ports["allocator_factory"]().provision({
        "tenant_id": "tenant-x", "idempotency_key": "k2", "persona_id": "p1",
        "strategy_artifact_ref": "spec-1", "execution_context": "paper", "budget": 10_000, "metadata": {},
    })
    assert calls[-1][3]["binding_id"] == body["binding_id"] == calls[-2][3]["binding_id"]


def test_active_bindings_come_from_capital_not_persona_labels() -> None:
    bindings = [
        {"binding_id": "pcb-discovery-aa", "persona_id": "p9", "tenant_id": "tenant-x", "status": "active",
         "budget": 10_000.0, "metadata": {"strategy_spec_registry_id": "spec-9"}},
        {"binding_id": "pcb-discovery-bb", "persona_id": "p8", "tenant_id": "tenant-x", "status": "retired",
         "budget": 10_000.0, "metadata": {"strategy_spec_registry_id": "spec-8"}},
        {"binding_id": "other-1", "persona_id": "p7", "tenant_id": "tenant-x", "status": "active", "budget": 5.0},
        {"binding_id": "pcb-discovery-cc", "persona_id": "p6", "tenant_id": "tenant-y", "status": "active", "budget": 1.0},
    ]
    ports = _ports([], bindings=bindings)
    assert ports["list_active_bindings"]() == [
        {"persona_id": "p9", "artifact_ref": "spec-9", "paper_capital": 10_000.0}
    ]


def test_partial_provision_failure_retains_earlier_bindings() -> None:
    attempts: list = []

    def provision(req):
        attempts.append(req["persona_id"])
        if len(attempts) == 2:
            raise RuntimeError("boom")

    alloc = da.DiscoveryAllocator(
        scorer=lambda p, a: {"score": 90, "match_id": "m"}, provision=provision,
        paper_capital_cap=100_000, per_persona_capital=1_000,
    )
    rec = da.run_scheduled_allocation(
        enabled=True, allocator_factory=lambda: alloc,
        list_admitted=lambda: [{"registry_id": f"s{i}"} for i in range(3)],
        list_personas=lambda: [{"persona_id": f"p{i}"} for i in range(3)],
        tenant_id="t",
    )
    assert len(attempts) == 4 and len(rec["bound"]) == 3 and rec["reason"] is None
    assert any(k["reason"].startswith("provision_failed:RuntimeError") for k in rec["skipped"])


def test_owner_unreachable_mid_run_keeps_bound_and_records_reason() -> None:
    n = []

    def provision(req):
        n.append(1)
        if len(n) == 2:
            raise da.OwnerUnreachable("provisioning", "down")

    alloc = da.DiscoveryAllocator(
        scorer=lambda p, a: {"score": 90, "match_id": "m"}, provision=provision,
        paper_capital_cap=100_000, per_persona_capital=1_000,
    )
    rec = da.run_scheduled_allocation(
        enabled=True, allocator_factory=lambda: alloc,
        list_admitted=lambda: [{"registry_id": f"s{i}"} for i in range(3)],
        list_personas=lambda: [{"persona_id": f"p{i}"} for i in range(3)],
        tenant_id="t",
    )
    assert len(rec["bound"]) == 1
    assert rec["reason"].startswith("owner_unreachable:provisioning")


def test_default_boot_uses_production_ports_and_compose_tenant_credentials() -> None:
    module = _load_service_module()  # no injection
    ports = module.discovery_allocator_ports
    assert callable(ports["allocator_factory"]) and callable(ports["resolve_admitted"])
    for name in ("list_personas", "list_active_bindings", "resolve_admitted"):
        assert ports[name].__module__ == da.__name__
    alloc = ports["allocator_factory"]()
    assert isinstance(alloc, da.DiscoveryAllocator) and alloc.max_personas == 5
    with mock.patch.object(module, "_retrain_call", return_value=[
        {"persona_id": "p1", "tenant_id": module.DISCOVERY_ALLOCATOR_TENANT, "status": "active",
         "lifecycle_state": "paper_owner", "strategy_family": "momentum"},
    ]) as owner:
        assert [p["persona_id"] for p in ports["list_personas"]()] == ["p1"]
    assert owner.call_args.kwargs["url"].endswith("/api/personas")
    assert owner.call_args.kwargs["token_variable"] == "RESEARCH_PERSONA_SERVICE_TOKEN"
    with mock.patch.object(module, "_retrain_call", return_value=[]) as owner:
        ports["list_active_bindings"]()
    assert owner.call_args.kwargs["token_variable"] == "RESEARCH_CAPITAL_SERVICE_TOKEN"
    assert owner.call_args.kwargs["url"].endswith("/api/bindings")


def test_compose_mounts_credentials_and_sets_dev_tenant() -> None:
    import yaml
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    env = compose["services"]["research-orchestrator-svc"]["environment"]
    assert env["DISCOVERY_ALLOCATOR_TENANT_ID"].endswith(":-tenant-dev}")
    for var in ("RESEARCH_BFF_SERVICE_TOKEN", "RESEARCH_PERSONA_SERVICE_TOKEN", "RESEARCH_CAPITAL_SERVICE_TOKEN"):
        assert env[var + "_FILE"].endswith("/run/pantheon-principals/" + var + "}")


def test_unreachable_owner_recorded_per_run() -> None:
    module = _load_service_module()
    admitted = [{"strategy_spec_id": "s", "registry_id": "s", "status": "admitted"}]
    failure = module._RetrainFailure("x", "persona request failed: boom")
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.object(module, "_retrain_call", side_effect=failure), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "true"}):
        with TestClient(module.app):
            rec = _records(module)[0]
    assert rec["reason"].startswith("owner_unreachable:persona:")
    assert rec["bound"] == []


def test_allocator_body_is_accepted_by_real_coordinator_for_new_pool() -> None:
    from services.control_plane.bff import test_persona_provisioning_coordinator as t

    calls: list = []
    ports = _ports(calls)
    ports["allocator_factory"]().provision({
        "tenant_id": "tenant-a", "idempotency_key": "k", "persona_id": "persona-chain",
        "strategy_artifact_ref": "reg-spec-frozen", "execution_context": "paper", "budget": 10_000,
        "metadata": {},
    })
    body = dict(calls[-1][3])
    transport = t.FakeOwnerTransport()
    t._seed_approved_registry(transport)
    coordinator = t._chain_coordinator(transport)
    result = coordinator.ensure_chain_paper_capital(
        tenant_id="tenant-a", persona_id="persona-chain", requested_by="research-orchestrator", **body,
    )
    assert result["persona_capital_binding"]["status"] == "active"
    posted = {p: b for m, _, p, b in transport.calls if m == "POST"}
    assert posted["/api/capital-pools"]["currency"] == "USD"
    assert posted["/api/bindings"]["metadata"]["strategy_spec_registry_id"] == "reg-spec-frozen"
    assert posted["/api/bindings"]["binding_id"].startswith("pcb-discovery-")


def _compose_capital_reader_default() -> str:
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text()
    m = re.search(r"CAPITAL_ALLOWED_READER_SERVICES: \$\{CAPITAL_ALLOWED_READER_SERVICES:-([^}]*)\}", text)
    assert m, "compose must declare the Capital reader allowlist"
    return m.group(1)


def test_production_binding_read_passes_real_capital_auth(monkeypatch) -> None:
    from services.capital.test_reader_authority import (  # noqa: F401
        _auth_headers,
        capital_reader_env,
    )

    gen = capital_reader_env.__wrapped__() if hasattr(capital_reader_env, "__wrapped__") else None
    assert gen is not None
    client, _module, _ = next(gen)
    try:
        # Real strict auth with the deployed (compose) reader allowlist, not a hand-set one.
        allowed = _compose_capital_reader_default()
        monkeypatch.setenv("CAPITAL_ALLOWED_READER_SERVICES", allowed)
        assert "research-orchestrator" in allowed.split(",")
        headers = _auth_headers(
            "tenant-alpha", service="research-orchestrator", roles=["capital-reader"], actor_id="ro",
        )

        def call(service, method, url, body):
            assert service == "capital" and method == "GET"
            res = client.get(url.replace("http://c", ""), headers=headers)
            assert res.status_code == 200, res.text  # was 403 ACTOR_SERVICE_FORBIDDEN
            return res.json()

        ports = da.make_production_ports(
            call=call, persona_url="http://p", provisioning_url="http://v", registry_url="http://r",
            capital_url="http://c", tenant_id="tenant-alpha", paper_capital_cap=1.0, per_persona_capital=1.0,
        )
        assert ports["list_active_bindings"]() == []

        # Write restriction preserved: research-orchestrator may not mutate Capital.
        denied = client.post(
            "/api/capital-pools",
            json={"actor_id": "ro", "actor_role": "capital.admin", "pool_id": "x", "name": "x",
                  "owner_id": "f", "owner_type": "fund", "approval_decision_id": "a"},
            headers=_auth_headers("tenant-alpha", service="research-orchestrator",
                                  roles=["capital-reader", "capital.admin"]),
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "ACTOR_SERVICE_FORBIDDEN"
    finally:
        gen.close()


def test_persona_eligibility_uses_real_owner_record_shape() -> None:
    base = {"tenant_id": "tenant-x", "status": "active", "lifecycle_state": "paper_owner"}
    rows = [
        {**base, "persona_id": "ok", "strategy_family": "momentum"},
        {**base, "persona_id": "baseline", "strategy_family": "dev_paper_baseline"},
        {**base, "persona_id": "meta-baseline", "metadata": {"strategy_family": "dev_paper_baseline"}},
        {**base, "persona_id": "suspended", "status": "suspended"},
        {**base, "persona_id": "archived", "status": "archived"},
        {**base, "persona_id": "frozen", "lifecycle_state": "frozen"},
        {**base, "persona_id": "retired", "lifecycle_state": "retired"},
        {**base, "persona_id": "other-tenant", "tenant_id": "tenant-y"},
        {"persona_id": "no-tenant", "status": "active", "lifecycle_state": "paper_owner"},
    ]
    ports = da.make_production_ports(
        call=lambda service, method, url, body: rows,
        persona_url="http://p", provisioning_url="http://v", registry_url="http://r",
        capital_url="http://c", tenant_id="tenant-x", paper_capital_cap=1.0, per_persona_capital=1.0,
    )
    assert [p["persona_id"] for p in ports["list_personas"]()] == ["ok"]
