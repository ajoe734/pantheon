"""Default owner readers preserve auth and distinguish empty from unavailable."""
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports import create_read_surface_ports


@pytest.mark.parametrize("dataset,method,path,key", [
    ("capital_pools", "list_capital_pools", "/api/capital-pools", None),
    ("bindings", "list_bindings", "/api/bindings", None),
    ("deployment_plans", "list_deployment_plans", "/api/deployment/plans", None),
    ("runtime_bindings", "list_runtime_bindings", "/api/runtime-bindings", "bindings"),
    ("evolution_programs", "list_evolution_programs", "/api/evolution/programs", "items"),
    ("evolution_decisions", "list_evolution_decisions", "/api/evolution/proposals", None),
])
def test_default_readers_preserve_auth_and_availability(monkeypatch, dataset, method, path, key):
    for env in ("PANTHEON_CAPITAL_API_URL", "PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_RUNTIME_MANAGER_URL", "PANTHEON_EVOLUTION_API_URL"):
        monkeypatch.setenv(env, "http://owner")
    calls = []
    row = {"pool_id": "p1", "binding_id": "b1", "plan_id": "d1", "program_id": "e1", "decision_id": "v1"}
    rows = [row]

    def transport(url, **kwargs):
        assert kwargs["auth_token"] == "caller-jwt"
        calls.append(urlsplit(url).path)
        if urlsplit(url).path != path:
            return []
        return {key: rows} if key else rows

    monkeypatch.setattr(owner_reads, "http_request_json", transport)
    ports = create_read_surface_ports()
    read = getattr(ports, method)
    assert read() == []
    assert ports.dataset_source(dataset) == "missing"
    token = owner_reads.authorization.set("Bearer caller-jwt")
    try:
        assert read()[0].items() >= row.items()
        assert path in calls
        assert ports.dataset_source(dataset) != "missing"
        rows.clear()
        assert read() == []
        assert ports.dataset_source(dataset) != "missing"
        monkeypatch.setattr(owner_reads, "http_request_json", lambda *a, **k: {"error": "unavailable"})
        assert read() == []
        assert ports.dataset_source(dataset) == "missing"
    finally:
        owner_reads.authorization.reset(token)


def test_rankings_read_the_same_injected_owner_store():
    rows = [SimpleNamespace(to_dict=lambda: {"ranking_id": "r1", "status": "published"})]
    ports = create_read_surface_ports(ranking_store=SimpleNamespace(list_rankings=lambda: rows))
    assert ports.get_ranking("r1")["status"] == "published"
    rows.clear()
    assert ports.list_rankings() == []
    assert ports.dataset_source("rankings") != "missing"


@pytest.mark.parametrize("missing_reader", [True, False])
def test_lifecycle_readiness_reports_unavailable_projection(monkeypatch, missing_reader):
    from services.control_plane.bff import main
    from services.control_plane.bff.trade_journey_projection_store import ProjectionReadUnavailable

    def unavailable(**kwargs):
        raise ProjectionReadUnavailable("owner unavailable")

    reader = None if missing_reader else SimpleNamespace(controller_freshness=unavailable)
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres")
    monkeypatch.setattr(main, "read_store", SimpleNamespace(trade_journey_projection_reader=lambda: reader))
    result = main._lifecycle_projector_dependency()
    assert result["ready"] is False
    assert any(reason.startswith("projection_reader_unavailable:") for reason in result["reasons"])
