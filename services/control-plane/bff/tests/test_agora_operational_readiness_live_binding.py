"""Focused live-authority coverage for Agora operational readiness."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BFF_ROOT = Path(__file__).resolve().parents[1]

from services.control_plane.bff.agora.operational_readiness import (
    AgoraOperationalReadinessService,
    EnvironmentAgoraOperationalReadinessProvider,
    ReadStoreAgoraOperationalReadinessProvider,
    create_operational_readiness_router,
)


NOW = "2026-09-01T12:00:00Z"
SNAPSHOT_ID = "mss-live-authority-001"
BINDING_ID = "rb-paper-live-001"
SURFACES = (
    "signals",
    "decision_events",
    "inbox",
    "journal",
    "candidates",
    "interactions",
    "performance",
)


def _source(*, event_time: str = "2026-09-01T11:59:00Z") -> dict[str, Any]:
    return {
        "snapshot": {
            "snapshot_id": SNAPSHOT_ID,
            "source_instance_id": "source-aapl-primary",
            "symbol": "AAPL.US",
            "event_time": event_time,
            "observed_at": "2026-09-01T11:59:10Z",
            "lineage": {
                "source_ids": ["source-aapl-primary"],
                "connector_ids": ["connector-aapl-primary"],
            },
        },
        "instance": {
            "source_instance_id": "source-aapl-primary",
            "desired_state": "enabled",
            "observed_state": "healthy",
        },
    }


def _producer(
    *,
    consumed_snapshot_id: str = SNAPSHOT_ID,
    last_success_at: str = "2026-09-01T11:59:30Z",
) -> dict[str, Any]:
    return {
        "status": "ok",
        "producer_id": "paper-signal-producer",
        "active_binding": BINDING_ID,
        "consumed_snapshot_id": consumed_snapshot_id,
        "last_success_at": last_success_at,
        "enqueued": 1,
        "reason": "canonical_signal_generation_receipt",
    }


def _surface_counts() -> dict[str, dict[str, Any]]:
    return {
        name: {"status": "ok", "count": index, "cursor": f"cursor-{index}"}
        for index, name in enumerate(SURFACES, start=1)
    }


class AuthoritativeReadStore:
    """Read-only fake implementing the exact production port names."""

    def __init__(
        self,
        *,
        source: Any = None,
        producer: Any = None,
        surfaces: Any = None,
    ) -> None:
        self.source = _source() if source is None else source
        self.producer = _producer() if producer is None else producer
        self.surfaces = _surface_counts() if surfaces is None else surfaces
        self.scopes: list[Any] = []
        self.mutation_calls = 0

    def list_telemetry_events_with_source(self) -> tuple[str, list[dict[str, Any]]]:
        return "missing", []

    def get_agora_operational_readiness_source(self, *, scope: Any = None) -> Any:
        self.scopes.append(scope)
        if isinstance(self.source, Exception):
            raise self.source
        return self.source

    def get_agora_operational_readiness_signal_producer(self, *, scope: Any = None) -> Any:
        if isinstance(self.producer, Exception):
            raise self.producer
        return self.producer

    def get_agora_operational_readiness_surfaces(self, *, scope: Any = None) -> Any:
        if isinstance(self.surfaces, Exception):
            raise self.surfaces
        return self.surfaces

    def create_signal(self, *_args: Any, **_kwargs: Any) -> None:
        self.mutation_calls += 1
        raise AssertionError("readiness must never call a mutation port")


def _service(store: AuthoritativeReadStore) -> AgoraOperationalReadinessService:
    return AgoraOperationalReadinessService(
        read_provider=ReadStoreAgoraOperationalReadinessProvider(lambda: store),
        producer_sla_seconds=300,
    )


def test_live_read_store_binding_is_healthy_and_uses_real_surface_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AuthoritativeReadStore()
    monkeypatch.setenv("GIT_SHA", "1" * 40)

    envelope = _service(store).compose_readiness(now_iso=NOW)

    assert envelope.data.status == "ok"
    assert envelope.data.source.snapshot_id == SNAPSHOT_ID
    assert envelope.data.source.source_instance_id == "source-aapl-primary"
    assert envelope.data.signal_producer.active_binding == BINDING_ID
    assert envelope.data.signal_producer.consumed_snapshot_id == SNAPSHOT_ID
    assert envelope.data.signal_producer.enqueued == 1
    assert envelope.data.deployment is not None
    assert envelope.data.deployment.source_commit_sha == "1" * 40
    assert {name: surface.count for name, surface in envelope.data.surfaces.items()} == {
        name: index for index, name in enumerate(SURFACES, start=1)
    }
    assert store.mutation_calls == 0


@pytest.mark.parametrize(
    ("source", "producer", "expected_status", "expected_reason"),
    [
        (
            _source(event_time="2026-08-30T00:00:00Z"),
            _producer(last_success_at="2026-09-01T11:59:30Z"),
            "degraded",
            "source_snapshot_stale",
        ),
        (
            _source(),
            _producer(consumed_snapshot_id="mss-other"),
            "degraded",
            "consumed_snapshot_mismatch",
        ),
        (
            _source(),
            _producer(last_success_at="2026-09-01T11:00:00Z"),
            "degraded",
            "producer_success_stale",
        ),
        (
            _source(event_time="2026-09-01T12:05:00Z"),
            _producer(),
            "unavailable",
            "source_unavailable",
        ),
    ],
)
def test_stale_mismatched_and_future_authority_fail_closed(
    source: dict[str, Any],
    producer: dict[str, Any],
    expected_status: str,
    expected_reason: str,
) -> None:
    envelope = _service(AuthoritativeReadStore(source=source, producer=producer)).compose_readiness(
        now_iso=NOW
    )

    assert envelope.data.status == expected_status
    assert envelope.data.signal_producer.reason == expected_reason


def test_missing_active_binding_fails_closed() -> None:
    producer = _producer()
    producer["active_binding"] = None

    envelope = _service(AuthoritativeReadStore(producer=producer)).compose_readiness(now_iso=NOW)

    assert envelope.data.status == "unavailable"
    assert envelope.data.signal_producer.reason == "active_binding_missing"


def test_provider_exception_degrades_with_typed_reason_instead_of_500() -> None:
    store = AuthoritativeReadStore(source=RuntimeError("owner timeout"))

    envelope = _service(store).compose_readiness(now_iso=NOW)

    assert envelope.data.status == "unavailable"
    assert envelope.data.source.freshness == "unavailable"
    assert envelope.data.source.last_failure == "source_provider_unavailable"
    assert envelope.data.signal_producer.reason == "source_unavailable"


def test_route_preserves_tenant_and_user_private_count_scope() -> None:
    private_rows = [
        {"id": "mine", "tenant_id": "tenant-a", "owner_user_id": "user-a"},
        {"id": "other-user", "tenant_id": "tenant-a", "owner_user_id": "user-b"},
        {"id": "other-tenant", "tenant_id": "tenant-b", "owner_user_id": "user-a"},
        {"id": "public", "visibility": "public"},
    ]
    store = AuthoritativeReadStore(
        surfaces={name: list(private_rows) for name in SURFACES}
    )
    identity = SimpleNamespace(
        operator_id="user-a",
        roles=["operator"],
        claims={
            "sub": "user-a",
            "tenant_id": "tenant-a",
            "allowed_tenants": ["tenant-a"],
            "roles": ["operator"],
        },
    )
    app = FastAPI()
    app.include_router(
        create_operational_readiness_router(
            utc_now=lambda: NOW,
            extract_identity=lambda _authorization: identity,
            require_read_role=lambda _identity: None,
            get_read_store=lambda: store,
        )
    )

    response = TestClient(app).get(
        "/bff/agora/operational-readiness",
        headers={"Authorization": "Bearer valid", "X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["audience"] == "tenant:tenant-a:user:user-a"
    assert all(surface["count"] == 2 for surface in body["data"]["surfaces"].values())
    assert store.scopes[-1].tenant_id == "tenant-a"
    assert store.scopes[-1].user_id == "user-a"
    assert store.mutation_calls == 0


def test_environment_provider_binds_runtime_source_and_canonical_consumption_receipt() -> None:
    store = AuthoritativeReadStore()

    def http_get(url: str, headers: Mapping[str, str], _timeout: float) -> dict[str, Any]:
        if url.endswith("/api/runtime-fleet/desired-state?stage=paper"):
            assert headers["Authorization"] == "Bearer runtime-token"
            return {"active_count": 1, "bindings": [{"binding_id": BINDING_ID, "status": "active"}]}
        if url.endswith(f"/api/runtime-bindings/{BINDING_ID}"):
            return {
                "binding_id": BINDING_ID,
                "runtime_id": "runtime-paper-001",
                "deployment_mode": "paper",
                "status": "active",
                "symbol": "AAPL.US",
                "tenant_id": "tenant-a",
            }
        if "/api/source-ingest/snapshots/latest?symbol=AAPL.US" in url:
            snapshot = dict(_source()["snapshot"])
            snapshot.pop("source_instance_id")
            return snapshot
        if url.endswith("/api/source-ingest/management/sources"):
            return {
                "sources": [{
                    "data_source_id": "source-aapl-primary",
                    "connector_id": "connector-aapl-primary",
                }],
                "count": 1,
            }
        if url.endswith("/api/source-ingest/management/sources/source-aapl-primary"):
            return {
                "source": {"data_source_id": "source-aapl-primary"},
                "desired": {"desired_lifecycle": "enabled"},
                "observed": {"health_state": "fresh"},
            }
        raise AssertionError(f"unexpected GET {url}")

    telemetry = [{
        "event_id": "telemetry-signal-001",
        "event_type": "signal_generation",
        "created_at": "2026-09-01T11:59:30Z",
        "environment": "paper",
        "tenant_id": "tenant-a",
        "metadata": {
            "source_worker": "paper-signal-producer",
            "binding_id": BINDING_ID,
            "tenant_id": "tenant-a",
            "environment": "paper",
            "market_input_snapshot_id": SNAPSHOT_ID,
        },
    }]
    provider = EnvironmentAgoraOperationalReadinessProvider(
        lambda: store,
        runtime_manager_url="http://runtime-manager:8081",
        source_ingest_url="http://source-ingest:8097",
        runtime_manager_token="runtime-token",
        http_get_json=http_get,
        telemetry_reader=lambda: telemetry,
    )
    scope = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

    envelope = AgoraOperationalReadinessService(
        read_provider=provider,
        producer_sla_seconds=300,
    ).compose_readiness(scope=scope, now_iso=NOW)

    assert provider.configured is True
    assert envelope.data.status == "ok"
    assert envelope.data.source.snapshot_id == SNAPSHOT_ID
    assert envelope.data.source.source_instance_id == "source-aapl-primary"
    assert envelope.data.signal_producer.active_binding == BINDING_ID
    assert envelope.data.signal_producer.consumed_snapshot_id == SNAPSHOT_ID
    assert envelope.data.signal_producer.reason == "canonical_signal_generation_receipt"
    assert store.mutation_calls == 0
