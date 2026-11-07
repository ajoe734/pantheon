from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.control_loops.service import ControlLoopsService
from services.control_plane.bff.core.app_factory import create_version_handler
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.trade_journeys import create_trade_journeys_router
from services.control_plane.bff.trade_journey_projection_store import (
    InvalidPageToken,
    ProjectionPage,
    TimelinePage,
)
from services.trade_journey import hosted_bff_readback as readback
from services.trade_journey.hosted_lifecycle_probe import (
    EXPECTED_STAGES,
    SCHEMA_VERSION as SOURCE_SCHEMA,
    TASK_ID,
)
from services.trade_journey.lifecycle_projector import STABLE_IDENTITY_FIELDS
from services.trade_journey.materializer import JourneyProjection


SHA = "a" * 40
BASE_URL = "https://pantheon-dev-bff.example.test"
IDENTITY = {
    "tenant_id": "tenant-dev",
    "environment": "paper",
    "journey_id": "tj-hosted-001",
    "run_id": "run-hosted-001",
    "loop_run_id": "lr-hosted-001",
    "signal_id": "signal-hosted-001",
    "strategy_id": "strategy-hosted-001",
    "runtime_id": "runtime-hosted-001",
    "binding_id": "binding-hosted-001",
    "capital_pool_id": "pool-hosted-001",
    "persona_id": "persona-hosted-001",
    "persona_capital_binding_id": "pcb-hosted-001",
    "artifact_id": "artifact-hosted-001",
    "artifact_version": "1.0.0",
    "plan_id": "plan-hosted-001",
    "trace_id": "trace-hosted-001",
}
EVENT_TYPES = [
    "signal_generation",
    "trade_decision",
    "risk_evaluation",
    "order_submitted",
    "order_accepted",
    "paper_fill_simulated",
    "position_snapshot",
    "reconciliation_completed",
]
EVENTS = [
    {"event_id": f"canonical-{index}", "event_type": event_type, "ingested_seq": 100 + index, "sequence_no": index}
    for index, event_type in enumerate(EVENT_TYPES, start=1)
]
CONTROLLER = {
    "deployment_sha": SHA,
    "generation": 12,
    "checkpoint": 108,
    "mode": "live",
    "accepted_live": True,
    "truth_level": "canonical_live",
    "status": "ready",
    "backlog": 0,
    "source_high_watermark": 108,
    "quarantine_count": 0,
}


def _source_artifact():
    return {
        "schema_version": SOURCE_SCHEMA,
        "task_id": TASK_ID,
        "outcome": "passed",
        "expected_deployment_sha": SHA,
        "proof": {
            "source": {
                "baseline_high_watermark": 100,
                "source_high_watermark": 108,
            },
            "identity": dict(IDENTITY),
            "events": list(EVENTS),
            "projection": {
                "backend": "postgres",
                "generation": 12,
                "deployment_sha": SHA,
                "loop_status": "completed",
            },
        },
    }


class FakeClient:
    def __init__(self, *, generation: int = 12, identity: str = "operator") -> None:
        self.generation = generation
        self.identity = identity

    def request(self, method, url, *, headers=None, payload=None):
        auth = (headers or {}).get("Authorization")
        if url.endswith("/bff/version"):
            return readback.HttpResult(
                200,
                {
                    "source_commit_sha": SHA,
                    "source_commit_known": True,
                    "environment": "dev",
                    "config_posture": {
                        "auth_stub": False,
                        "auth_mode": "strict",
                        "dev_login_enabled": True,
                        "trade_journey_reader_backend": "postgres",
                        "trade_journey_projection_schema": (
                            "trade_journey_projection"
                        ),
                    },
                },
            )
        if url.endswith("/bff/auth/dev-login"):
            assert payload["client_secret"] == "client-secret"
            return readback.HttpResult(
                200,
                {
                    "access_token": "token-value",
                    "token_type": "bearer",
                    "expires_in": 900,
                    "meta": {"identity": self.identity},
                },
            )
        if auth != "Bearer token-value":
            return readback.HttpResult(401, {"error": {"code": "AUTH_REQUIRED"}})
        if "page_token=stale-cutover-token" in url:
            return readback.HttpResult(
                400, {"error": {"code": "VALIDATION_FAILED"}}
            )
        if "tenant-dev-outside" in url or "environment=live" in url:
            return readback.HttpResult(
                404, {"error": {"code": "RESOURCE_NOT_FOUND"}}
            )
        if "/bff/v5/loop-runs/" in url:
            controller = dict(CONTROLLER, generation=self.generation)
            return readback.HttpResult(
                200,
                {
                    "data": {
                        "id": IDENTITY["loop_run_id"],
                        **IDENTITY,
                        "source": "postgres_lifecycle_projection",
                        "status": "completed",
                        "freshness_lineage": {
                            "accepted_live": True,
                            "mode": "live",
                        },
                    },
                    "meta": {
                        "surfaces": {
                            "loop_run_detail": {
                                "status": "ok",
                                "source": "postgres_lifecycle_projection",
                                "truth_status": "formal",
                                "projection_schema_version": "pantheon.trade-journey-projection.v1",
                                "controller": controller,
                            }
                        }
                    },
                },
            )
        if url.split("?", 1)[0].endswith("/evidence"):
            by_stage = {
                EXPECTED_STAGES[event["event_type"]]: {
                    "event_ids": [
                        event["event_id"]
                    ]
                }
                for event in EVENTS
            }
            return readback.HttpResult(
                200,
                {
                    "data": {
                        "journey_id": IDENTITY["journey_id"],
                        "by_stage": by_stage,
                    },
                    "meta": self._journey_meta(),
                },
            )
        if url.split("?", 1)[0].endswith("/timeline"):
            return readback.HttpResult(
                200,
                {
                    "data": {
                        "id": f"{IDENTITY['journey_id']}-timeline",
                        "journey_id": IDENTITY["journey_id"],
                        "items": [dict(event) for event in EVENTS],
                    },
                    "meta": self._journey_meta(),
                },
            )
        if url.split("?", 1)[0].endswith("/graph"):
            return readback.HttpResult(
                200,
                {
                    "data": {
                        "journey_id": IDENTITY["journey_id"],
                        "nodes": [
                            {
                                "id": IDENTITY["journey_id"],
                                "type": "journey_id",
                            }
                        ],
                        "edges": [],
                    },
                    "meta": self._journey_meta(),
                },
            )
        if url.split("?", 1)[0].endswith("/trade-journeys"):
            return readback.HttpResult(
                200,
                {
                    "data": {
                        "id": "trade-journeys",
                        "tenant_id": IDENTITY["tenant_id"],
                        "environment": "paper",
                        "items": [
                            {
                                "journey_id": IDENTITY["journey_id"],
                                "tenant_id": IDENTITY["tenant_id"],
                                "environment": "paper",
                                "status": "completed",
                                "read_state": "formal",
                            }
                        ],
                    },
                    "meta": self._journey_meta(),
                },
            )
        controller = dict(CONTROLLER, generation=self.generation)
        return readback.HttpResult(
            200,
            {
                "data": {
                    "journey_id": IDENTITY["journey_id"],
                    "tenant_id": IDENTITY["tenant_id"],
                    "environment": "paper",
                    "status": "completed",
                    "read_state": "formal",
                    "event_count": 8,
                },
                "meta": {
                    **self._journey_meta(),
                },
            },
        )

    def _journey_meta(self):
        controller = dict(CONTROLLER, generation=self.generation)
        return {
            "read_state": "formal",
            "freshness": {
                "rebuild_status": "postgres_projection_reader",
                "projector_owned": True,
                "projection_schema_version": "pantheon.trade-journey-projection.v1",
                "generation": self.generation,
                "accepted_live": True,
                "projection_mode": "live",
                "truth_level": "canonical_live",
                "controller": controller,
            },
        }


class MismatchedControllerClient(FakeClient):
    def request(self, method, url, *, headers=None, payload=None):
        result = super().request(method, url, headers=headers, payload=payload)
        if (
            "/bff/v5/loop-runs/" in url
            and (headers or {}).get("Authorization") == "Bearer token-value"
        ):
            result.payload["meta"]["surfaces"]["loop_run_detail"]["controller"][
                "generation"
            ] = self.generation + 1
        return result


class EventuallyVisibleLoopClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.authenticated_loop_reads = 0

    def request(self, method, url, *, headers=None, payload=None):
        if (
            "/bff/v5/loop-runs/" in url
            and (headers or {}).get("Authorization") == "Bearer token-value"
        ):
            self.authenticated_loop_reads += 1
            if self.authenticated_loop_reads == 1:
                return readback.HttpResult(504, {"error": {"code": "UPSTREAM_TIMEOUT"}})
        return super().request(method, url, headers=headers, payload=payload)


class MissingLoopClient(FakeClient):
    def request(self, method, url, *, headers=None, payload=None):
        if (
            "/bff/v5/loop-runs/" in url
            and (headers or {}).get("Authorization") == "Bearer token-value"
        ):
            return readback.HttpResult(404, {"error": {"code": "RESOURCE_NOT_FOUND"}})
        return super().request(method, url, headers=headers, payload=payload)


def test_authenticated_public_bff_readback_correlates_both_surfaces(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=FakeClient(),
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["public_bff"]["trade_journey"]["correlated_event_count"] == 8
    assert set(artifact["public_bff"]["auth"]["negative_statuses"].values()) == {401}
    assert set(
        artifact["public_bff"]["auth"]["scope_negative_statuses"].values()
    ) == {404}
    assert set(
        artifact["public_bff"]["auth"]["conflict_negative_statuses"].values()
    ) == {400}
    assert artifact["public_bff"]["sensitive_field_posture"] == {
        "response_payloads_persisted": False,
        "access_token_persisted": False,
        "credentials_persisted": False,
        "paper_only": True,
    }
    assert artifact["public_bff"]["cross_surface"] == {
        "same_deployment_identity": True,
        "monotonic_controller_generations": True,
        "ordered_generations": [12, 12, 12, 12, 12, 12],
        "exact_event_ids": True,
        "generation_advanced_since_source_proof": False,
    }
    raw = output.read_text(encoding="utf-8")
    assert "token-value" not in raw
    assert "client-secret" not in raw


def test_authenticated_readback_requires_declared_governed_identity(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="operator-a-client",
        client_secret="client-secret",
        client=FakeClient(identity="operator_a"),
        expected_login_identity="operator_a",
    )

    assert code == 0
    assert artifact["public_bff"]["auth"]["identity"] == "operator_a"


def test_readback_generation_mismatch_fails_with_redacted_artifact(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=FakeClient(generation=11),
    )

    assert code == 1
    assert artifact["failure"]["code"] == "bff_generation_mismatch"
    assert "token-value" not in output.read_text(encoding="utf-8")
    assert all(field in IDENTITY for field in STABLE_IDENTITY_FIELDS)


def test_readback_accepts_monotonic_generation_advance_with_exact_events(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=FakeClient(generation=13),
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["public_bff"]["loop_run"]["source_projection_generation"] == 12
    assert artifact["public_bff"]["loop_run"]["projection_generation"] == 13
    assert artifact["public_bff"]["trade_journey"]["projection_generation"] == 13
    assert artifact["public_bff"]["cross_surface"] == {
        "same_deployment_identity": True,
        "monotonic_controller_generations": True,
        "ordered_generations": [13, 13, 13, 13, 13, 13],
        "exact_event_ids": True,
        "generation_advanced_since_source_proof": True,
    }


def test_readback_rejects_surface_controller_generation_mismatch(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=MismatchedControllerClient(generation=13),
    )

    assert code == 1
    assert artifact["failure"]["code"] == "bff_cross_surface_mismatch"
    assert "token-value" not in output.read_text(encoding="utf-8")


def test_readback_retries_bounded_public_surface_warmup(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=EventuallyVisibleLoopClient(),
        surface_read_poll_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert code == 0
    assert artifact["public_bff"]["loop_run"]["read_attempts"] == 2
    assert artifact["public_bff"]["loop_run"]["transient_http_statuses"] == [504]
    assert artifact["public_bff"]["trade_journey"]["read_attempts"] == 1
    assert "token-value" not in output.read_text(encoding="utf-8")


def test_readback_reports_redacted_bounded_retry_failure(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=MissingLoopClient(),
        surface_read_attempts=2,
        surface_read_poll_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert code == 1
    assert artifact["failure"] == {
        "code": "bff_loop_read_invalid",
        "message": "loop-run detail did not converge after bounded retries",
        "details": {
            "surface": "loop-run detail",
            "attempts": 2,
            "transport_error": False,
            "last_http_status": 404,
        },
    }
    raw = output.read_text(encoding="utf-8")
    assert "token-value" not in raw
    assert "client-secret" not in raw


class StubProjectionReader:
    def __init__(
        self,
        *,
        identity: Mapping[str, Any],
        events: list[dict[str, Any]],
        controller: Mapping[str, Any],
        loop_status: str = "completed",
    ) -> None:
        self.identity = identity
        self.events = events
        self.controller = controller
        self.loop_status = loop_status

    def get_loop_run(
        self,
        *,
        tenant_id: str,
        environment: str,
        loop_run_id: str,
    ) -> Optional[dict[str, Any]]:
        if tenant_id != self.identity["tenant_id"] or environment != self.identity["environment"]:
            return None
        if loop_run_id != self.identity["loop_run_id"]:
            return None
        return {
            "id": self.identity["loop_run_id"],
            **self.identity,
            "source": "postgres_lifecycle_projection",
            "status": self.loop_status,
            "freshness_lineage": {
                "accepted_live": True,
                "mode": "live",
            },
        }

    def page_loop_runs(
        self,
        *,
        tenant_id: str,
        environment: str,
        statuses: Optional[list[str]] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        if page_token == "stale-cutover-token":
            raise InvalidPageToken("stale page token")
        record = self.get_loop_run(
            tenant_id=tenant_id,
            environment=environment,
            loop_run_id=self.identity["loop_run_id"],
        )
        records = [record] if record else []
        return records, None

    def list_loop_runs(
        self,
        *,
        tenant_id: str,
        environment: str,
        statuses: Optional[list[str]] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        return self.page_loop_runs(
            tenant_id=tenant_id,
            environment=environment,
            statuses=statuses,
            page_size=page_size,
            page_token=page_token,
        )

    def controller_freshness(
        self,
        *,
        tenant_id: str,
        environment: str,
    ) -> dict[str, Any]:
        return dict(self.controller)

    def get_journey(
        self,
        *,
        tenant_id: str,
        environment: str,
        journey_id: str,
    ) -> Optional[JourneyProjection]:
        if tenant_id != self.identity["tenant_id"] or environment != self.identity["environment"]:
            return None
        if journey_id != self.identity["journey_id"]:
            return None
        timeline_items = [
            {
                "event_id": event["event_id"],
                "journey_id": self.identity["journey_id"],
                "stage": EXPECTED_STAGES[event["event_type"]],
                "event_type": event["event_type"],
                "stage_status": "succeeded",
                "status": "succeeded",
                "occurred_at": "2026-10-07T12:00:00Z",
                "recorded_at": "2026-10-07T12:00:00Z",
                "evidence_refs": [],
                "input_refs": [],
                "output_refs": [],
                "policy_refs": [],
            }
            for event in self.events
        ]
        return JourneyProjection(
            journey_id=self.identity["journey_id"],
            tenant_id=tenant_id,
            environment=environment,
            timeline=timeline_items,
            snapshot={
                "status": self.loop_status,
                "created_at": "2026-10-07T12:00:00Z",
                "updated_at": "2026-10-07T12:01:00Z",
                "identifiers": {
                    "tenant_id": self.identity["tenant_id"],
                    "journey_id": self.identity["journey_id"],
                    "loop_run_id": self.identity["loop_run_id"],
                },
                "stages": {
                    EXPECTED_STAGES[event["event_type"]]: {"status": "succeeded"}
                    for event in self.events
                },
                "completeness": {"missing_stages": []},
            },
            graph_edges=[
                {
                    "source": self.identity["journey_id"],
                    "target": self.identity["journey_id"],
                    "kind": "journey_id",
                }
            ],
            diagnostics=[],
        )

    def page_journeys(
        self,
        *,
        tenant_id: str,
        environment: str,
        filters: Any = None,
        sort: str = "updated_at_desc",
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> ProjectionPage:
        if page_token == "stale-cutover-token":
            raise InvalidPageToken("stale page token")
        journey = self.get_journey(
            tenant_id=tenant_id,
            environment=environment,
            journey_id=self.identity["journey_id"],
        )
        items = [journey] if journey else []
        return ProjectionPage(items=items, next_page_token=None, total=len(items))

    def page_timeline(
        self,
        *,
        tenant_id: str,
        environment: str,
        journey_id: str,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[TimelinePage]:
        journey = self.get_journey(
            tenant_id=tenant_id,
            environment=environment,
            journey_id=journey_id,
        )
        if journey is None:
            return None
        return TimelinePage(
            items=journey.timeline,
            next_page_token=None,
            total=len(journey.timeline),
        )


class AppTestHttpClient:
    def __init__(self, test_client: TestClient) -> None:
        self._client = test_client

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> readback.HttpResult:
        path = url
        if url.startswith(BASE_URL):
            path = url[len(BASE_URL):]
        resp = self._client.request(
            method,
            path,
            headers=dict(headers or {}),
            json=payload if payload is not None else None,
        )
        try:
            body = resp.json()
        except Exception:
            body = None
        return readback.HttpResult(status=resp.status_code, payload=body)


def _build_real_bff_app(projection_reader: StubProjectionReader) -> tuple[FastAPI, ControlLoopsService]:
    class MockReadStore:
        def __init__(self) -> None:
            self.trade_journey_projection_reader = lambda: projection_reader

        def dataset_source(self, dataset: str) -> str:
            return "postgres_lifecycle_projection"

    service = ControlLoopsService(
        read_store=MockReadStore(),
        deployed_environment="dev",
        served_stages=("paper", "broker_sandbox"),
    )

    def extract_identity(authorization: Optional[str] = None) -> OperatorIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "AUTH_REQUIRED", "message": "Missing bearer token"}},
            )
        token = authorization[len("Bearer "):].strip()
        if token != "token-value":
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "AUTH_REQUIRED", "message": "Invalid token"}},
            )
        return OperatorIdentity(
            operator_id="operator",
            roles=["operator", "viewer"],
            mfa_verified=True,
            claims={
                "tenant_id": str(projection_reader.identity["tenant_id"]),
                "allowed_tenants": [str(projection_reader.identity["tenant_id"])],
            },
        )

    def require_read_role(identity: Any) -> None:
        roles = set(getattr(identity, "roles", []) or [])
        if not {"operator", "viewer"}.intersection(roles):
            raise HTTPException(status_code=403, detail="Forbidden")

    app = FastAPI()

    app.get("/bff/version")(
        create_version_handler(
            source_commit_fn=lambda: SHA,
            auth_stub_fn=lambda: False,
            auth_mode_fn=lambda: "strict",
            dev_login_fn=lambda: True,
            environment="dev",
        )
    )

    @app.post("/bff/auth/dev-login")
    async def dev_login(payload: dict = Body(...)) -> dict[str, Any]:
        if (
            payload.get("client_id") != "client-id"
            or payload.get("client_secret") != "client-secret"
        ):
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        return {
            "access_token": "token-value",
            "token_type": "bearer",
            "expires_in": 900,
            "meta": {"identity": "operator"},
        }

    app.include_router(
        create_control_loops_router(
            service=service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
        )
    )
    app.include_router(
        create_trade_journeys_router(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            get_projection_reader=lambda: projection_reader,
        )
    )
    return app, service


def test_readback_against_real_bff_routes(tmp_path: Any) -> None:
    projection_reader = StubProjectionReader(
        identity=IDENTITY,
        events=EVENTS,
        controller=dict(CONTROLLER, generation=13),
    )
    app, _ = _build_real_bff_app(projection_reader)
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    http_client = AppTestHttpClient(client)

    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=http_client,
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["public_bff"]["loop_run"]["projection_generation"] == 13
    assert artifact["public_bff"]["trade_journey"]["projection_generation"] == 13
    assert artifact["public_bff"]["cross_surface"]["monotonic_controller_generations"] is True
    assert artifact["public_bff"]["cross_surface"]["exact_event_ids"] is True


def test_readback_detects_real_bff_drift_when_projection_schema_version_missing(tmp_path: Any) -> None:
    projection_reader = StubProjectionReader(
        identity=IDENTITY,
        events=EVENTS,
        controller=dict(CONTROLLER, generation=13),
    )
    app, service = _build_real_bff_app(projection_reader)

    # Simulate the historical drift where get_loop_run omitted projection_schema_version
    original_surface_helper = service._postgres_projection_surface

    def omitting_schema_surface(ctrl: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        surface = original_surface_helper(ctrl)
        surface.pop("projection_schema_version", None)
        return surface

    service._postgres_projection_surface = omitting_schema_surface

    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    http_client = AppTestHttpClient(client)

    source = tmp_path / "source.json"
    output = tmp_path / "readback.json"
    source.write_text(json.dumps(_source_artifact()), encoding="utf-8")

    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=SHA,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=http_client,
    )

    assert code == 1
    assert artifact["failure"]["code"] == "bff_loop_surface_invalid"
    assert artifact["failure"]["message"] == "loop-run projection metadata mismatched"


def test_readback_reproduces_and_verifies_retained_root42397678868_sell_without_holdings_journey(
    tmp_path: Any,
) -> None:
    retained_identity = {
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "journey_id": "tj_294b657b-1d64-50d8-b2bf-03e9f3c26a6f",
        "run_id": "run-rb-8aceee4784be4ae581d51ab7ef2243c4-60872d6d0ca7a7522ea9153a",
        "loop_run_id": "lr-run-rb-8aceee4784be4ae581d51ab7ef2243c4-60872d6d0ca7a7522ea9153a",
        "signal_id": "sig-60872d6d0ca7a7522ea9153a11eb8de8",
        "strategy_id": "strategy-persona-e0cd73f41c0fb27c36de",
        "runtime_id": "rt-f45eb161",
        "binding_id": "rb-8aceee4784be4ae581d51ab7ef2243c4",
        "capital_pool_id": "pool-persona-paper-e0cd73f41c0fb27c36de",
        "persona_id": "persona-034dad74863c32a796a4",
        "persona_capital_binding_id": "pcb-persona-paper-e0cd73f41c0fb27c36de",
        "artifact_id": "artifact-persona-paper-e0cd73f41c0fb27c36de",
        "artifact_version": "1.0.0",
        "plan_id": "plan-persona-paper-e0cd73f41c0fb27c36de",
        "trace_id": "f553230a-a918-5a25-b41d-c86e9ccb19f3",
    }
    retained_events = [
        {
            "event_id": "192e0569-ee44-4e5a-9f21-d24415530353",
            "event_type": "signal_generation",
            "ingested_seq": 11210607,
            "sequence_no": 1,
        },
        {
            "event_id": "20476142-6e7a-5715-b20f-fa61022cf1aa",
            "event_type": "trade_decision",
            "ingested_seq": 11210608,
            "sequence_no": 2,
        },
        {
            "event_id": "483dede1-6213-4d7b-8df4-48d1ae30b747",
            "event_type": "order_rejection",
            "ingested_seq": 11210635,
            "sequence_no": 3,
        },
    ]
    f6_sha = "f6d40b77da32309498863a6d66debcc2449268e2"
    controller = {
        **CONTROLLER,
        "deployment_sha": f6_sha,
        "generation": 2155728,
        "checkpoint": 11212948,
        "source_high_watermark": 11212948,
    }
    source_artifact = {
        "schema_version": SOURCE_SCHEMA,
        "task_id": TASK_ID,
        "outcome": "passed",
        "expected_deployment_sha": f6_sha,
        "proof": {
            "source": {
                "baseline_high_watermark": 11210574,
                "source_high_watermark": 11212948,
            },
            "identity": dict(retained_identity),
            "events": list(retained_events),
            "projection": {
                "backend": "postgres",
                "generation": 2155728,
                "deployment_sha": f6_sha,
                "loop_status": "failed",
            },
        },
    }

    class RetainedStoredRowReader(StubProjectionReader):
        def __init__(
            self,
            *,
            include_reason: bool = True,
            submission_extra: Optional[Dict[str, Any]] = None,
        ) -> None:
            super().__init__(
                identity=retained_identity,
                events=retained_events,
                controller=controller,
                loop_status="failed",
            )
            self.include_reason = include_reason
            self.submission_extra = dict(submission_extra or {})

        def get_journey(
            self,
            *,
            tenant_id: str,
            environment: str,
            journey_id: str,
        ) -> Optional[JourneyProjection]:
            if (
                tenant_id != self.identity["tenant_id"]
                or environment != self.identity["environment"]
                or journey_id != self.identity["journey_id"]
            ):
                return None
            timeline_items = [
                {
                    "event_id": event["event_id"],
                    "journey_id": journey_id,
                    "stage": EXPECTED_STAGES[event["event_type"]],
                    "event_type": event["event_type"],
                    "stage_status": (
                        "failed"
                        if event["event_type"] == "order_rejection"
                        else "succeeded"
                    ),
                    "status": (
                        "failed"
                        if event["event_type"] == "order_rejection"
                        else "succeeded"
                    ),
                    "quantity": (
                        0.0 if event["event_type"] == "order_rejection" else None
                    ),
                    "reason_code": (
                        "sell_without_holdings"
                        if (self.include_reason and event["event_type"] == "order_rejection")
                        else None
                    ),
                    "submitted_to_broker": False,
                    "occurred_at": (
                        "2026-11-07T18:06:21.000000Z"
                        if event["event_type"] == "order_rejection"
                        else "2026-11-07T18:06:17.000000Z"
                    ),
                    "recorded_at": (
                        "2026-11-07T18:06:23.815494Z"
                        if event["event_type"] == "order_rejection"
                        else "2026-11-07T18:06:19.506659Z"
                    ),
                    "evidence_refs": [],
                    "input_refs": [],
                    "output_refs": [],
                    "policy_refs": [],
                    **(
                        self.submission_extra
                        if event["event_type"] == "order_rejection"
                        else {}
                    ),
                }
                for event in self.events
            ]
            return JourneyProjection(
                journey_id=journey_id,
                tenant_id=tenant_id,
                environment=environment,
                timeline=timeline_items,
                snapshot={
                    "status": "failed",
                    "created_at": "2026-11-07T18:06:17.000000Z",
                    "updated_at": "2026-11-07T18:06:21.000000Z",
                    "identifiers": {
                        "tenant_id": [tenant_id],
                        "journey_id": [journey_id],
                        "run_id": [self.identity["run_id"]],
                        "loop_run_id": [self.identity["loop_run_id"]],
                        "signal_id": [self.identity["signal_id"]],
                        "decision_id": ["dec-rb-8aceee4784be4ae581d51ab7ef2243c4-60872d6d0ca7a7522ea9153a"],
                    },
                    "stages": {
                        "signal_generation": {
                            "status": "succeeded",
                            "updated_at": "2026-11-07T18:06:17.000000Z",
                            "revision": 1,
                        },
                        "trade_decision": {
                            "status": "succeeded",
                            "updated_at": "2026-11-07T18:06:17.000000Z",
                            "revision": 2,
                        },
                        "order_submission": {
                            "status": "failed",
                            "updated_at": "2026-11-07T18:06:21.000000Z",
                            "revision": 3,
                        },
                    },
                    "completeness": {
                        "missing_stages": [
                            "research_rationale",
                            "feature_hypothesis",
                            "backtest_validation",
                            "paper_graduation",
                            "champion_challenger",
                            "deployment_runtime",
                            "risk_evaluation",
                        ],
                        "complete": False,
                    },
                },
                graph_edges=[],
                diagnostics=[
                    {
                        "code": "missing_stages",
                        "stages": [
                            "research_rationale",
                            "feature_hypothesis",
                            "backtest_validation",
                            "paper_graduation",
                            "champion_challenger",
                            "deployment_runtime",
                            "risk_evaluation",
                        ],
                    }
                ],
            )

    def build_app(reader: StubProjectionReader) -> AppTestHttpClient:
        app, _ = _build_real_bff_app(reader)
        for route in list(app.routes):
            if getattr(route, "path", None) == "/bff/version":
                app.routes.remove(route)
        app.get("/bff/version")(
            create_version_handler(
                source_commit_fn=lambda: f6_sha,
                auth_stub_fn=lambda: False,
                auth_mode_fn=lambda: "strict",
                dev_login_fn=lambda: True,
                environment="dev",
            )
        )
        return AppTestHttpClient(
            TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
        )

    source = tmp_path / "retained-source.json"
    output = tmp_path / "retained-readback.json"
    source.write_text(json.dumps(source_artifact), encoding="utf-8")

    # 1. Repaired path: the retained Root 42397678868 sell_without_holdings 3-event
    # journey is a formal complete record of a failed outcome.
    code, artifact = readback.execute_readback(
        source_path=source,
        output=output,
        expected_sha=f6_sha,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=build_app(RetainedStoredRowReader(include_reason=True)),
    )
    assert code == 0, artifact
    assert artifact["outcome"] == "passed"
    assert artifact["public_bff"]["loop_run"]["status"] == "failed"
    assert artifact["public_bff"]["trade_journey"]["correlated_event_count"] == 3
    assert artifact["public_bff"]["cross_surface"]["exact_event_ids"] is True

    # 2. Unverified / broker-submitted failure without risk_evaluation remains partial
    # and fails closed with bff_journey_truth_invalid (reproducing the pre-fix F6 gate failure).
    bad_output = tmp_path / "unverified-readback.json"
    bad_code, bad_artifact = readback.execute_readback(
        source_path=source,
        output=bad_output,
        expected_sha=f6_sha,
        base_url=BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        client=build_app(RetainedStoredRowReader(include_reason=False)),
    )
    assert bad_code == 1
    assert bad_artifact["failure"]["code"] == "bff_journey_truth_invalid"

    # 3. Contradictory broker/fill/ledger/reconciliation identifiers or invalid
    # quantities on a sell_without_holdings rejection remain partial and fail closed.
    for idx, contra_extra in enumerate(
        [
            {"broker_order_id": "broker-real"},
            {"fill_id": "fill-real"},
            {"filled_quantity": "invalid"},
            {"metadata": {"reconciliation_id": "recon-nested"}},
            {"metrics": {"fill_quantity": "invalid"}},
        ],
        start=1,
    ):
        contra_output = tmp_path / f"contra-readback-{idx}.json"
        contra_code, contra_artifact = readback.execute_readback(
            source_path=source,
            output=contra_output,
            expected_sha=f6_sha,
            base_url=BASE_URL,
            client_id="client-id",
            client_secret="client-secret",
            client=build_app(
                RetainedStoredRowReader(
                    include_reason=True, submission_extra=contra_extra
                )
            ),
        )
        assert contra_code == 1
        assert contra_artifact["failure"]["code"] == "bff_journey_truth_invalid"



