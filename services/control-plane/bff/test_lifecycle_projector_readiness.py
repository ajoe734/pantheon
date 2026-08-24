from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402

from services.trade_journey.lifecycle_projector import LifecycleProjector  # noqa: E402
from services.trade_journey.test_lifecycle_projector import lifecycle_rows  # noqa: E402


def _configure_dependencies(monkeypatch, projection_root: Path) -> None:
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime-manager:8081")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance:8082")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", "http://deployment:8095")
    monkeypatch.setenv("LIFECYCLE_PROJECTION_ROOT", str(projection_root))
    monkeypatch.setenv(
        "LIFECYCLE_PROJECTOR_STATE_PATH",
        str(projection_root / "controller_state.json"),
    )
    monkeypatch.setenv(
        "LIFECYCLE_PROJECTOR_HEALTH_STATE_PATH",
        str(projection_root / "health_state.json"),
    )
    monkeypatch.setenv(
        "PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE",
        str(projection_root / "current" / "trade_journey_events.json"),
    )
    monkeypatch.setenv(
        "PANTHEON_BFF_LOOP_RUN_STORE",
        str(projection_root / "current" / "loop_runs.json"),
    )
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", "30")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_BACKLOG", "0")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_PERCENT", "0")


def test_bff_readyz_fails_closed_when_projector_is_stale_and_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
    )
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    client = TestClient(bff_main.app)

    healthy = client.get("/readyz")
    assert healthy.status_code == 200, healthy.text
    dependency = healthy.json()["dependencies"]["lifecycle_projector"]
    assert dependency["worker_status"] == "ready"
    assert dependency["current_generation"] == 1
    assert dependency["source_high_watermark"] == 1
    assert dependency["last_successful_publish_at"]

    state_path = tmp_path / "health_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["controller"]["last_poll_at"] = "2020-01-01T00:00:00Z"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    stale = client.get("/readyz")
    assert stale.status_code == 503
    stale_dependency = stale.json()["dependencies"]["lifecycle_projector"]
    assert stale_dependency["worker_status"] == "stale"
    assert stale_dependency["stale_reason"].startswith("last_poll_stale:")

    stale_alias = client.get("/bff/readyz")
    assert stale_alias.status_code == 503
    assert (
        stale_alias.json()["dependencies"]["lifecycle_projector"]["worker_status"]
        == "stale"
    )

    projector.record_poll(source_high_watermark=1, backlog=0, mode="live")
    recovered = client.get("/bff/readyz")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["dependencies"]["lifecycle_projector"]["ready"] is True


def test_bff_readyz_exposes_projector_error_reason(tmp_path: Path, monkeypatch) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
    )
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    projector.record_source_failure("postgres unavailable", backlog=1)

    response = TestClient(bff_main.app).get("/readyz")
    assert response.status_code == 503
    dependency = response.json()["dependencies"]["lifecycle_projector"]
    assert dependency["worker_status"] == "error"
    assert dependency["error_reason"] == "last_error:postgres unavailable"
    assert dependency["backlog"] == 1


def test_bff_readyz_fails_closed_when_read_store_is_not_projector_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
    )
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    monkeypatch.setenv(
        "PANTHEON_BFF_LOOP_RUN_STORE",
        str(tmp_path / "loop_runs.json"),
    )

    response = TestClient(bff_main.app).get("/readyz")

    assert response.status_code == 503
    dependency = response.json()["dependencies"]["lifecycle_projector"]
    assert dependency["worker_status"] == "ready"
    assert dependency["ready"] is False
    assert dependency["status"] == "degraded"
    assert dependency["error_reason"] == "read_surface_store_mismatch:loop_runs"
    assert dependency["read_surface_stores"]["loop_runs"] == {
        "configured_path": str(tmp_path / "loop_runs.json"),
        "expected_path": str(tmp_path / "current" / "loop_runs.json"),
        "aligned": False,
    }


def test_bff_readyz_uses_exact_relational_controller_after_reader_cutover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "postgres")
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_HEALTH_ENVIRONMENT", "paper")
    monkeypatch.setenv("GIT_SHA", "deadbeef")

    controller = {
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
    assert dependency["writer_backend"] == "postgres"
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


def test_bff_readyz_relational_negative_conditions(tmp_path: Path, monkeypatch) -> None:
    _configure_dependencies(monkeypatch, tmp_path)
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "postgres")
    monkeypatch.setenv("GIT_SHA", "current-sha")

    base_controller = {
        "controller_id": "canonical-lifecycle-projector",
        "checkpoint": 50,
        "source_high_watermark": 50,
        "backlog": 0,
        "generation": 12,
        "deployment_sha": "current-sha",
        "mode": "live",
        "status": "ready",
        "accepted_live": True,
        "last_poll_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "last_error": None,
        "quarantine_count": 0,
    }

    controller_ref = [dict(base_controller)]

    class Reader:
        def controller_freshness(self, **kwargs):
            return dict(controller_ref[0]) if controller_ref[0] is not None else None

    monkeypatch.setattr(
        bff_main.read_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )
    client = TestClient(bff_main.app)

    # 1. Controller missing
    controller_ref[0] = None
    res = client.get("/readyz")
    assert res.status_code == 503
    assert "controller_missing" in res.json()["dependencies"]["lifecycle_projector"]["reasons"]

    # 2. Controller status not ready
    c = dict(base_controller)
    c["status"] = "degraded"
    controller_ref[0] = c
    res = client.get("/readyz")
    assert res.status_code == 503
    assert "controller_not_ready:degraded" in res.json()["dependencies"]["lifecycle_projector"]["reasons"]

    # 3. Mode not live or accepted_live False
    c = dict(base_controller)
    c["mode"] = "recovery"
    c["accepted_live"] = False
    controller_ref[0] = c
    res = client.get("/readyz")
    assert res.status_code == 503
    assert any(r.startswith("live_truth_not_accepted:") for r in res.json()["dependencies"]["lifecycle_projector"]["reasons"])

    # 4. Checkpoint mismatch / backlog > 0
    c = dict(base_controller)
    c["checkpoint"] = 45
    c["source_high_watermark"] = 50
    c["backlog"] = 5
    controller_ref[0] = c
    res = client.get("/readyz")
    assert res.status_code == 503
    reasons = res.json()["dependencies"]["lifecycle_projector"]["reasons"]
    assert "checkpoint_mismatch:45!=50" in reasons
    assert "backlog_nonzero:5" in reasons

    # 5. Quarantine count nonzero
    c = dict(base_controller)
    c["quarantine_count"] = 2
    controller_ref[0] = c
    res = client.get("/readyz")
    assert res.status_code == 503
    assert "quarantine_nonzero:2" in res.json()["dependencies"]["lifecycle_projector"]["reasons"]

    # 6. Last error present
    c = dict(base_controller)
    c["last_error"] = "database timeout"
    controller_ref[0] = c
    res = client.get("/readyz")
    assert res.status_code == 503
    assert "last_error:database timeout" in res.json()["dependencies"]["lifecycle_projector"]["reasons"]

    # 7. Reader None / unavailable
    monkeypatch.setattr(
        bff_main.read_store,
        "trade_journey_projection_reader",
        lambda: None,
    )
    res = client.get("/readyz")
    assert res.status_code == 503
    assert any("projection_reader_unavailable" in r for r in res.json()["dependencies"]["lifecycle_projector"]["reasons"])

    # 8. Writer backend disabled / not postgres
    monkeypatch.setattr(
        bff_main.read_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )
    controller_ref[0] = dict(base_controller)
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "disabled")
    res = client.get("/readyz")
    assert res.status_code == 503
    dep = res.json()["dependencies"]["lifecycle_projector"]
    assert dep["ready"] is False
    assert dep["writer_backend"] == "disabled"
    assert "writer_backend_mismatch:disabled!=postgres" in dep["reasons"]

    # 9. Writer backend shadow
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "shadow")
    res = client.get("/readyz")
    assert res.status_code == 503
    dep = res.json()["dependencies"]["lifecycle_projector"]
    assert dep["ready"] is False
    assert "writer_backend_mismatch:shadow!=postgres" in dep["reasons"]

    # 10. Recover writer backend to postgres
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "postgres")
    res = client.get("/readyz")
    assert res.status_code == 200
    dep = res.json()["dependencies"]["lifecycle_projector"]
    assert dep["ready"] is True
    assert dep["writer_backend"] == "postgres"
    assert dep["reasons"] == []
