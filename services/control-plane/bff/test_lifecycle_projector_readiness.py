from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.trade_journey_projection_store import ProjectionReadUnavailable
from services.foundation.health import (
    health_payload,
    readiness_status_code,
    register_fastapi_health_routes,
)


def _lifecycle_projector_dependency(read_store: Any) -> Dict[str, Any]:
    reader_backend = os.getenv(
        "PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres"
    ).strip().lower()
    if reader_backend != "postgres":
        return {
            "ready": False,
            "status": "degraded",
            "worker_status": "error",
            "writer_backend": "disabled",
            "reader_backend": reader_backend,
            "reasons": [f"legacy_reader_retired:{reader_backend}"],
            "error_reason": f"legacy_reader_retired:{reader_backend}",
        }

    reader = getattr(read_store, "trade_journey_projection_reader", lambda: None)()
    tenant_id = os.getenv("PANTHEON_BFF_HEALTH_TENANT_ID", "default").strip()
    environment = os.getenv(
        "PANTHEON_BFF_TRADE_JOURNEY_HEALTH_ENVIRONMENT", "paper"
    ).strip()
    reasons: List[str] = []
    controller: Dict[str, Any] = {}
    try:
        if reader is None:
            raise ProjectionReadUnavailable(
                "Postgres reader selected but no projection reader was configured"
            )
        controller = dict(
            reader.controller_freshness(
                tenant_id=tenant_id,
                environment=environment,
            )
            or {}
        )
    except (ProjectionReadUnavailable, ValueError) as exc:
        reasons.append(f"projection_reader_unavailable:{exc}")
    except Exception as exc:  # noqa: BLE001 - readiness is fail-closed truth
        reasons.append(f"projection_reader_error:{type(exc).__name__}")

    raw_writer_backend = os.getenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND")
    if raw_writer_backend is not None and raw_writer_backend.strip():
        writer_backend = raw_writer_backend.strip().lower()
    else:
        writer_backend = "postgres" if controller else "disabled"

    if writer_backend not in {"postgres", "shadow", "relational"}:
        reasons.append(
            f"writer_backend_mismatch:{writer_backend or 'missing'}!=postgres"
        )

    expected_sha = (
        os.getenv("BFF_COMMIT") or os.getenv("GIT_SHA") or ""
    ).strip()
    controller_sha = str(controller.get("deployment_sha") or "").strip()
    checkpoint = int(controller.get("checkpoint") or 0)
    source_high = int(controller.get("source_high_watermark") or 0)
    backlog = int(controller.get("backlog") or 0)
    quarantine_count = int(controller.get("quarantine_count") or 0)
    if not controller:
        reasons.append("controller_missing")
    if controller.get("status") != "ready":
        reasons.append(f"controller_not_ready:{controller.get('status') or 'missing'}")
    if controller.get("mode") != "live" or controller.get("accepted_live") is not True:
        reasons.append(
            "live_truth_not_accepted:"
            f"{controller.get('mode') or 'missing'}:"
            f"{str(bool(controller.get('accepted_live'))).lower()}"
        )
    if checkpoint != source_high:
        reasons.append(f"checkpoint_mismatch:{checkpoint}!={source_high}")
    if backlog != 0:
        reasons.append(f"backlog_nonzero:{backlog}")
    if quarantine_count != 0:
        reasons.append(f"quarantine_nonzero:{quarantine_count}")
    if controller.get("last_error"):
        reasons.append(f"last_error:{controller['last_error']}")
    if expected_sha and expected_sha != "unknown" and controller_sha != expected_sha:
        reasons.append(
            f"deployment_sha_mismatch:{controller_sha or 'missing'}!={expected_sha}"
        )

    last_poll_at = str(controller.get("last_poll_at") or "").strip()
    freshness_age_seconds: Optional[float] = None
    if not last_poll_at:
        reasons.append("last_poll_missing")
    else:
        try:
            last_poll = datetime.fromisoformat(last_poll_at.replace("Z", "+00:00"))
            if last_poll.tzinfo is None:
                last_poll = last_poll.replace(tzinfo=timezone.utc)
            freshness_age_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - last_poll.astimezone(timezone.utc)).total_seconds(),
            )
            max_age = max(
                1.0,
                float(os.getenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", "120")),
            )
            if freshness_age_seconds > max_age:
                reasons.append(
                    f"last_poll_stale:{freshness_age_seconds:.3f}>{max_age:.3f}"
                )
        except (TypeError, ValueError):
            reasons.append("last_poll_invalid")

    ready = not reasons
    root = Path(
        os.getenv(
            "LIFECYCLE_PROJECTION_ROOT",
            "/tmp/pantheon/bff/lifecycle-projection",
        )
    )
    return {
        "ready": ready,
        "status": "ready" if ready else "degraded",
        "worker_status": "ready" if ready else "error",
        "writer_backend": writer_backend,
        "reader_backend": "postgres",
        "tenant_scope": tenant_id,
        "environment_scope": environment,
        "deployment_sha": controller_sha or None,
        "expected_deployment_sha": expected_sha or None,
        "checkpoint": checkpoint,
        "source_high_watermark": source_high,
        "backlog": backlog,
        "quarantine_count": quarantine_count,
        "generation": controller.get("generation"),
        "mode": controller.get("mode"),
        "accepted_live": bool(controller.get("accepted_live")),
        "last_poll_at": last_poll_at or None,
        "freshness_age_seconds": freshness_age_seconds,
        "reasons": reasons,
        "error_reason": reasons[0] if reasons else None,
        "legacy_recovery_stores": {
            "trade_journey_events": str(
                root / "current" / "trade_journey_events.json"
            ),
            "loop_runs": str(root / "current" / "loop_runs.json"),
            "preserved": True,
            "accepted_reader": False,
        },
        "controller": controller,
    }


def _create_app(read_store: Any) -> FastAPI:
    app = FastAPI()

    def _deps():
        return {
            "runtime_manager": {
                "status": "ok" if os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip() else "degraded",
                "url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip(),
            },
            "governance": {
                "status": "ok" if os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "").strip() else "degraded",
                "url": os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "").strip(),
            },
            "deployment": {
                "status": "ok" if os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip() else "degraded",
                "url": os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip(),
            },
            "lifecycle_projector": _lifecycle_projector_dependency(read_store),
        }

    register_fastapi_health_routes(
        app,
        "operator-bff",
        dependencies=_deps,
        details=lambda: {"version": "0.2.0", "data_dir": "/tmp/pantheon/bff"},
    )

    async def _bff_readyz():
        payload = health_payload(
            "operator-bff",
            dependencies=_deps,
            details=lambda: {"version": "0.2.0", "data_dir": "/tmp/pantheon/bff"},
        )
        return JSONResponse(payload, status_code=readiness_status_code(payload))

    app.add_api_route("/bff/readyz", _bff_readyz, methods=["GET"])
    return app


class _TestStore:
    def trade_journey_projection_reader(self):
        return None


class _BffFixture:
    def __init__(self):
        self.active_store = _TestStore()

    @property
    def app(self):
        return _create_app(self.active_store)


bff_fixture = _BffFixture()


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
    client = TestClient(bff_fixture.app)

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
        bff_fixture.active_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )

    client = TestClient(bff_fixture.app)
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
        bff_fixture.active_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )

    response = TestClient(bff_fixture.app).get("/readyz")
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
        bff_fixture.active_store,
        "trade_journey_projection_reader",
        lambda: Reader(),
    )

    response = TestClient(bff_fixture.app).get("/readyz")
    assert response.status_code == 200, response.text
    dependency = response.json()["dependencies"]["lifecycle_projector"]
    assert dependency["reader_backend"] == "postgres"
    assert dependency["deployment_sha"] == "deadbeef"
    assert dependency["checkpoint"] == dependency["source_high_watermark"] == 19
    assert dependency["legacy_recovery_stores"]["preserved"] is True
    assert dependency["legacy_recovery_stores"]["accepted_reader"] is False

    controller["deployment_sha"] = "stale-sha"
    rejected = TestClient(bff_fixture.app).get("/readyz")
    assert rejected.status_code == 503
    rejected_dependency = rejected.json()["dependencies"]["lifecycle_projector"]
    assert rejected_dependency["ready"] is False
    assert rejected_dependency["error_reason"] == (
        "deployment_sha_mismatch:stale-sha!=deadbeef"
    )
