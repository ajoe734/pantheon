"""Scheduled allocation -> real BFF persona create route + provisioning saga -> real evolution scope.

Only owner HTTP is faked (Registry/Capital/Governance/Deployment via the saga's
``FakeOwnerTransport``, Persona owner via an in-memory writer).  The allocator,
the BFF ``POST /bff/personas`` route, the real ``PersonaProvisioningCoordinator``
and the evolution ``DiscoveryBindingScope`` are all real.
"""
from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.persona_provisioning_coordinator import deterministic_provisioning_ids
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _admitted_artifact_view,
)
from services.control_plane.bff.tests import test_bff_persona_provisioning_baseline_500 as bff_harness
from services.evolution.auto_consumer_runtime import DiscoveryBindingScope
from services.registry.strategy_artifact import strategy_artifact_checksum
from services.research import discovery_allocator as da

SPEC_ID = "spec-gate-1"
ARTIFACT_ID = "artifact-gate-1"
RUN_ID = "bt-erun-1"
UNIVERSE = ["2317", "2330", "2454"]
MANDATE = {"persona_id": "discovery-mandate", "mandate": "Taiwan equity momentum", "strategy_family": "momentum"}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    transport, _store = bff_harness._setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        client = bff_harness._build_test_client(read_store, CommandStore(str(Path(td) / "commands.jsonl")))
        writer = personas_service._current_persona_service.get()._write_owner
        yield {"transport": transport, "client": client, "writer": writer, "tmp": Path(td)}


def _bundle_view(state="approved"):
    view = _admitted_artifact_view(ARTIFACT_ID, state=state)
    view["entry"]["owner_tenant"] = "pantheon-dev"
    params = view["entry"]["metadata"]["strategy_artifact"]["parameters"]
    params.update(market="TW", symbols=["2330"])
    surface = view["entry"]["metadata"]["strategy_artifact"]["mutation_surface"]
    if "market" not in surface["immutable_parameters"]:
        surface["immutable_parameters"] = [*surface["immutable_parameters"], "market"]
    return view


def _spec_view(artifact):
    return {"entry": {
        "registry_id": SPEC_ID, "artifact_type": "strategy_spec", "artifact_state": "approved",
        "owner_tenant": "pantheon-dev", "checksum": "sha256:spec",
        "metadata": {"tenant_id": "pantheon-dev", "strategy_spec": {
            "strategy_id": "discovered-strategy", "title": "tw momentum", "strategy_artifact": artifact}},
    }}


def _wire(world, *, bundle_state="approved", persona_rows=None, scorer=None):
    transport, client, writer = world["transport"], world["client"], world["writer"]
    bundle = _bundle_view(bundle_state)
    artifact = bundle["entry"]["metadata"]["strategy_artifact"]
    transport.objects[("registry", f"/api/registry/strategy-artifacts/{ARTIFACT_ID}")] = bundle
    seen: list[tuple[str, str, str]] = []

    def persona_owner_rows():
        return [
            {**p, "tenant_id": p["metadata"].get("tenant_id"), "status": "active"}
            for p in writer.personas.values()
        ]

    def call(service, method, url, body, headers=None):
        seen.append((service, method, url))
        if service == "registry" and method == "GET" and "/strategy-specs/" in url:
            return _spec_view(artifact)
        if service == "registry" and method == "GET":
            got = transport.get("registry", url.split("http://r")[1])
            if got is None:
                raise da.OwnerRejected("registry", 404, "nf")
            return got
        if service == "persona":
            return persona_rows() if persona_rows else persona_owner_rows()
        if service == "provisioning":
            resp = client.post(url.split("http://b")[1], json=body, headers={**bff_harness.OPERATOR_HEADERS, **(headers or {})})
            if resp.status_code >= 400:
                raise da.OwnerRejected("provisioning", resp.status_code, resp.text[:1200])
            return resp.json()
        raise AssertionError((service, method, url))

    persona_rows = None
    ports = da.make_production_ports(
        call=call, persona_url="http://p", provisioning_url="http://b", registry_url="http://r",
        tenant_id="pantheon-dev", paper_capital_cap=50_000, per_persona_capital=10_000, mandate_profile=MANDATE,
    )
    if scorer is not None:
        real_provision = ports["allocator_factory"]().provision
        ports["allocator_factory"] = lambda: da.DiscoveryAllocator(
            scorer=scorer, provision=real_provision, paper_capital_cap=50_000, per_persona_capital=10_000)
    return ports, seen, artifact


def _evidence(artifact, *, symbols=UNIVERSE, status="completed"):
    checksum = strategy_artifact_checksum(artifact)
    finding = {"source_event_type": "research_finding_published", "metadata": {
        "event_type": "research_finding_published", "tenant_id": "pantheon-dev", "strategy_spec_id": SPEC_ID,
        "metrics": {"backtest_run_id": RUN_ID}}}
    run = {"status": status, "artifact_checksum": checksum, "dataset_id": "tw-ds-1", "dataset_symbols": symbols}
    return [finding], {RUN_ID: run}.get


def _run(world, ports, seen, artifact, *, evidence=None, now=None):
    findings, lookup = evidence or _evidence(artifact)
    return da.run_scheduled_allocation(
        enabled=True, allocator_factory=ports["allocator_factory"],
        list_admitted=lambda: [{"strategy_spec_id": SPEC_ID, "checksum": "sha256:spec", "status": "admitted"}],
        list_gate_evidence=lambda: (findings, lookup), read_marked=ports["read_marked"],
        resolve_candidates=ports["resolve_candidates"], mandate_profile=MANDATE, tenant_id="pantheon-dev",
        record_path=world["tmp"] / "runs.jsonl", now=now or datetime(2026, 11, 12, tzinfo=timezone.utc),
    )


def _always(score=90):
    return lambda profile, candidate: {"score": score, "match_id": f"match-{candidate['strategy_spec_id']}", "blockers": ()}


def test_scheduled_run_creates_persona_through_saga_and_requests_runtime(world) -> None:
    ports, seen, artifact = _wire(world, scorer=_always())
    rec = _run(world, ports, seen, artifact)
    assert rec["reason"] is None and len(rec["bound"]) == 1, rec["skipped"]
    bound = rec["bound"][0]
    assert bound["artifact_ref"] == ARTIFACT_ID and bound["symbols"] == UNIVERSE and bound["backtest_run_id"] == RUN_ID

    # never the capital-only route
    assert not any("paper-capital" in url for _, _, url in seen)
    assert ("provisioning", "POST", "http://b/bff/personas") in seen

    transport = world["transport"]
    persona = next(iter(world["writer"].personas.values()))
    meta = persona["metadata"]
    ids = deterministic_provisioning_ids(type("R", (), {"tenant_id": meta["tenant_id"], "persona_id": persona["persona_id"]})())
    assert meta["discovery_allocator"]["marker"] is True
    assert meta["discovery_allocator"]["match_id"] == f"match-{SPEC_ID}" and meta["discovery_allocator"]["match_score"] == 90
    assert meta["source_strategy_artifact_ref"] == ARTIFACT_ID

    # saga artifact carries the judged artifact's logic/parameters unchanged and the source ref
    entry = deepcopy(transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")]["entry"])
    saga_artifact = entry["metadata"]["strategy_artifact"]
    assert saga_artifact["strategy_logic"] == artifact["strategy_logic"]
    assert saga_artifact["parameters"] == artifact["parameters"]
    assert entry["metadata"]["source_strategy_artifact_ref"] == ARTIFACT_ID

    # the paper universe is the gate dataset's universe
    spec_entry = transport.objects[("registry", f"/api/registry/strategy-specs/{ids.registry_id}")]["entry"]
    assert json.dumps(spec_entry["metadata"], sort_keys=True).count("2330") >= 1

    # a runtime is requested through the Deployment saga for that very artifact
    plan_posts = [b for m, o, p, b in transport.calls if m == "POST" and p == "/api/deployment/plans"]
    dispatch = [p for m, o, p, b in transport.calls if m == "POST" and p.endswith("/dispatch")]
    assert plan_posts and plan_posts[0]["registry_id"] == ids.strategy_artifact_id and dispatch
    binding = transport.objects[("capital", f"/api/bindings/{ids.persona_capital_binding_id}")]
    assert binding["metadata"]["discovery_allocator"]["marker"] is True
    assert ids.persona_capital_binding_id.startswith("pcb-persona-paper-")

    # the real evolution scope resolves the persona from owner records
    def owners(method, url, headers, body):
        if url == "http://cap/api/bindings":
            return 200, [v for (o, p), v in transport.objects.items() if o == "capital" and p.startswith("/api/bindings/")]
        if url.startswith("http://reg/api/registry/strategy-artifacts/"):
            got = transport.get("registry", url.split("http://reg")[1])
            return (200, got) if got else (404, {})
        if url == f"http://per/api/personas/{persona['persona_id']}":
            return 200, {**persona, "tenant_id": "pantheon-dev"}
        raise AssertionError(url)

    scope = DiscoveryBindingScope(capital_url="http://cap", registry_url="http://reg", persona_url="http://per",
                                  capital_token=lambda: "c", registry_token=lambda: "r", persona_token=lambda: "p", transport=owners)
    for target in (ids.strategy_artifact_id, ARTIFACT_ID):
        got = scope.resolve({"tenant_id": "pantheon-dev", "target_id": target})
        assert got and got["persona_id"] == persona["persona_id"] and got["binding_id"] == ids.persona_capital_binding_id


def test_second_run_adopts_existing_persona_and_retired_frees_slot(world) -> None:
    ports, seen, artifact = _wire(world, scorer=_always())
    assert len(_run(world, ports, seen, artifact)["bound"]) == 1
    again = _run(world, ports, seen, artifact)
    assert again["bound"] == [] and len(world["writer"].personas) == 1  # artifact already taken; no second persona
    persona = next(iter(world["writer"].personas.values()))
    persona["lifecycle_state"] = "retired"
    slots, taken = ports["read_marked"]()
    assert slots == [] and taken == [ARTIFACT_ID]  # slot freed, artifact never re-bound


def test_unapproved_bundle_and_missing_universe_fail_closed(world) -> None:
    ports, seen, artifact = _wire(world, bundle_state="candidate", scorer=_always())
    rec = _run(world, ports, seen, artifact)
    assert rec["bound"] == [] and {"artifact_ref": SPEC_ID, "reason": "execution_bundle_not_approved"} in rec["skipped"]
    ports, seen, artifact = _wire(world, scorer=_always())
    rec = _run(world, ports, seen, artifact, evidence=_evidence(artifact, symbols=[]))
    assert rec["bound"] == [] and {"artifact_ref": SPEC_ID, "reason": "universe_missing"} in rec["skipped"]
    assert not any(m == "POST" and o == "provisioning" for o, m, _ in seen) and not world["writer"].personas


def test_real_persona_strategy_match_scores_the_candidate_and_records_it(world) -> None:
    ports, seen, artifact = _wire(world)  # real persona_strategy_match scorer, mandate profile from settings
    rec = _run(world, ports, seen, artifact)
    assert len(rec["bound"]) == 1 and rec["bound"][0]["match_id"].startswith("psm-")
    marker = next(iter(world["writer"].personas.values()))["metadata"]["discovery_allocator"]
    assert marker["match_id"] == rec["bound"][0]["match_id"] and marker["match_score"] == rec["bound"][0]["score"] >= da.DEFAULT_MIN_SCORE
