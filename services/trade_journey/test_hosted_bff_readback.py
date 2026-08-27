from __future__ import annotations

import json

from services.trade_journey import hosted_bff_readback as readback
from services.trade_journey.hosted_lifecycle_probe import (
    EXPECTED_STAGES,
    SCHEMA_VERSION as SOURCE_SCHEMA,
    TASK_ID,
)
from services.trade_journey.lifecycle_projector import STABLE_IDENTITY_FIELDS


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
