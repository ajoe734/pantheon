"""Owner-unavailable detail reads return 503 DEPENDENCY_UNAVAILABLE (capital convention, ed2951d76).

A known-missing id with an available source keeps returning 404.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BFF_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BFF_DIR))

from test_execute_plans_final_live_wiring_contract import HEADERS, _isolated_final_read_models  # noqa: E402
from services.control_plane.bff.personas.service import PersonaService  # noqa: E402
from services.control_plane.bff.ports.read_surface_ports import (  # noqa: E402
    PaperReconcilerUnavailableError,
    ReadSurfacePorts,
)
from services.control_plane.bff.ports.research_knowledge_source import (  # noqa: E402
    ResearchWriteOwnerUnavailableError,
)


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


def _assert_503(response):
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["retryable"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/bff/research-analyses/analysis-x",
        "/bff/personas/persona-x",
        "/api/v1/operator/persona-management/persona-x",
    ],
)
def test_detail_reads_return_503_when_source_unavailable(path):
    with _isolated_final_read_models(fallback=False) as client:
        _assert_503(client.get(path, headers=HEADERS))


@pytest.mark.parametrize(
    "path",
    [
        "/bff/research-analyses/analysis-x",
        "/bff/personas/persona-x",
        "/api/v1/operator/persona-management/persona-x",
    ],
)
def test_detail_reads_keep_404_for_unknown_id_when_source_available(path):
    with _isolated_final_read_models() as client:
        response = client.get(path, headers=HEADERS)
    assert response.status_code == 404, response.text


def test_strategy_experiments_return_503_when_research_write_owner_unavailable():
    with _isolated_final_read_models() as client:
        import main as bff_main

        def _owner_down(**_kw):
            raise ResearchWriteOwnerUnavailableError("Research write owner is not configured")

        bff_main.read_store.list_research_experiments = _owner_down
        _assert_503(client.get("/bff/strategies/stg_001/experiments", headers=HEADERS))


def test_quarterly_ranking_returns_503_when_paper_reconciler_down(monkeypatch):
    def _reconciler_down(self, **_kw):
        raise PaperReconcilerUnavailableError("PANTHEON_PAPER_FLEET_RECONCILER_URL is not configured")

    monkeypatch.setattr(PersonaService, "_compose_quarterly_ranking_context", _reconciler_down)
    with _isolated_final_read_models() as client:
        _assert_503(client.get("/bff/management/quarterly-ranking", headers=HEADERS))


def test_unconfigured_reconciler_raises_dedicated_unavailable_error(monkeypatch):
    monkeypatch.delenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", raising=False)
    ports = ReadSurfacePorts.__new__(ReadSurfacePorts)
    with pytest.raises(PaperReconcilerUnavailableError):
        ports.list_authoritative_paper_runtime_monitoring_sessions()
