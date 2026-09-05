from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent

from services.control_plane.bff import main as bff_main


def _configure_dependencies(monkeypatch, projection_root: Path) -> None:
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime-manager:8081")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance:8082")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", "http://deployment:8095")
    monkeypatch.setenv("LIFECYCLE_PROJECTION_ROOT", str(projection_root))
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres")
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_HEALTH_ENVIRONMENT", "paper")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", "30")


def _valid_controller(overrides=None):
    base = {
        "controller_id": "canonical-lifecycle-projector",
        "checkpoint": 19,
        "source_high_watermark": 19,
        "backlog": 0,
        "generation": 7,
        "deployment_sha": "deadbeef",
        "mode": "live",
        "status": "ready",
        "accepted_live": True,
        "last_poll_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "last_error": None,
        "quarantine_count": 0,
    }
    if overrides:
        base.update(overrides)
    return base


def test_bff_readyz_fails_closed_when_legacy_json_backend_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "json")
    client = TestClient(bff_main.app)

    response = client.get("/readyz")
    assert response.status_code == 503
    dep = response.json()["dependencies"]["lifecycle_projector"]
    assert dep["ready"] is False
    assert dep["status"] == "degraded"
    assert "legacy_reader_retired:json" in dep["reasons"]


def test_bff_readyz_fails_closed_when_projector_is_stale_and_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    monkeypatch.setenv("GIT_SHA", "deadbeef")

    controller_state = _valid_controller({"last_poll_at": "2020-01-01T00:00:00Z"})

    class Reader:
        def controller_freshness(self, **kwargs):
            return dict(controller_state)

    monkeypatch.setattr(
        bff_main.read_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )

    client = TestClient(bff_main.app)
    stale = client.get("/readyz")
    assert stale.status_code == 503
    stale_dep = stale.json()["dependencies"]["lifecycle_projector"]
    assert stale_dep["ready"] is False
    assert any("last_poll_stale:" in r for r in stale_dep["reasons"])

    controller_state["last_poll_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    recovered = client.get("/bff/readyz")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["dependencies"]["lifecycle_projector"]["ready"] is True


def test_bff_readyz_exposes_projector_error_reason(tmp_path: Path, monkeypatch) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    monkeypatch.setenv("GIT_SHA", "deadbeef")

    controller_state = _valid_controller({
        "status": "degraded",
        "last_error": "postgres connection reset",
        "backlog": 5,
    })

    class Reader:
        def controller_freshness(self, **kwargs):
            return dict(controller_state)

    monkeypatch.setattr(
        bff_main.read_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )

    response = TestClient(bff_main.app).get("/readyz")
    assert response.status_code == 503
    dep = response.json()["dependencies"]["lifecycle_projector"]
    assert dep["ready"] is False
    assert "last_error:postgres connection reset" in dep["reasons"]


def test_bff_readyz_uses_exact_relational_controller_after_reader_cutover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    monkeypatch.setenv("GIT_SHA", "deadbeef")

    controller = _valid_controller()

    class Reader:
        def controller_freshness(self, **kwargs):
            assert kwargs == {"tenant_id": "default", "environment": "paper"}
            return dict(controller)

    monkeypatch.setattr(
        bff_main.read_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )

    response = TestClient(bff_main.app).get("/readyz")
    assert response.status_code == 200, response.text
    dependency = response.json()["dependencies"]["lifecycle_projector"]
    assert dependency["reader_backend"] == "postgres"
    assert dependency["deployment_sha"] == "deadbeef"
    assert dependency["checkpoint"] == dependency["source_high_watermark"] == 19
    assert dependency["legacy_recovery_stores"]["preserved"] is True
    assert dependency["legacy_recovery_stores"]["accepted_reader"] is False

    controller["deployment_sha"] = "stale-sha"
    rejected = TestClient(bff_main.app).get("/readyz")
    assert rejected.status_code == 503
    rejected_dependency = rejected.json()["dependencies"]["lifecycle_projector"]
    assert rejected_dependency["ready"] is False
    assert rejected_dependency["error_reason"] == (
        "deployment_sha_mismatch:stale-sha!=deadbeef"
    )
