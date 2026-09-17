"""MGMT-OODA-004 contract tests for BFF OODA packet read routes."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.control_loops.router import (
    _default_extract_identity,
    _default_require_read_role,
    create_control_loops_router,
)
from services.control_plane.bff.control_loops.service import default_bff_error
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.evolution.router import (
    _default_page_slice,
    _default_read_surface_meta,
    _default_utc_now,
    create_evolution_router,
)
from services.control_plane.bff.evolution.service import (
    ooda_packet_list_payload as _production_ooda_packet_list_payload,
    ooda_packet_routes_enabled,
)
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.runtime.router import create_runtime_router
from services.control_plane.bff.strategies.router import create_strategies_router

# BFF-TEST-MIGRATION-CB07: DECOUPLED. As of this migration, the four route
# groups this file exercises (/bff/runtimes/{id}/ooda, /bff/strategies/{id}/ooda,
# /bff/evolution-programs/{id}/ooda, /bff/ooda/packets[...]) live across four
# already-extracted domain routers -- control_loops/router.py, evolution/router.py,
# strategies/router.py (via routes/detail.py), and runtime/router.py -- none of
# which import main.py. Three of the four (control_loops, evolution, strategies'
# own read_surface_meta fallback) now ship real default wiring; only
# runtime/router.py's RuntimeRouterService still has zero fallback defaults
# (every dependency resolves through service.dependency(name) with no default),
# so its four required callables (_extract_identity, _require_read_role,
# _require_ooda_packet_routes_enabled, _ooda_packet_list_payload) are supplied
# explicitly below, reusing the same production primitives (control_loops'
# default identity/role checks, evolution.service.ooda_packet_routes_enabled,
# and evolution.service.ooda_packet_list_payload) rather than duplicating
# main.py's private closures.


HEADERS = {"Authorization": "Bearer op-mgmt-ooda:operator,reviewer,admin:mfa"}

OODA_PACKETS = [
    {
        "packet_id": "ooda-packet-001",
        "status": "open",
        "stage": "act",
        "strategy_id": "strat-alpha",
        "runtime_id": "rt-paper-1",
        "evolution_program_id": "evo-program-1",
        "created_at": "2026-05-15T10:00:00Z",
        "updated_at": "2026-05-15T10:05:00Z",
        "observe_refs": [{"type": "TelemetryEvent", "id": "tel-001"}],
        "fail_closed_checks": [{"name": "live_broker_disabled", "passed": True}],
    },
    {
        "packet_id": "ooda-packet-002",
        "status": "closed",
        "stage": "learn",
        "strategy_ids": ["strat-beta"],
        "act": {"runtime_binding_id": "binding-paper-2"},
        "learn": {"evolution_program_id": "evo-program-2"},
        "created_at": "2026-05-15T11:00:00Z",
        "updated_at": "2026-05-15T11:10:00Z",
    },
]


def _normalize_ooda_packets(raw_packets: Optional[list[dict]]) -> Optional[list[dict]]:
    if raw_packets is None:
        return None
    materialized: dict[str, dict] = {}
    passthrough: list[dict] = []
    for item in raw_packets:
        if isinstance(item, dict) and item.get("schema_version") == "ooda_loop_packet_record.v1":
            rec_type = item.get("record_type")
            payload = item.get("payload", {})
            if rec_type == "packet_snapshot":
                pkt = dict(payload)
                pkt.setdefault("packet_id", item.get("packet_id"))
                materialized[str(pkt["packet_id"])] = pkt
            elif rec_type == "stage_transition":
                pkt = dict(payload.get("packet", {}))
                pkt.setdefault("packet_id", item.get("packet_id"))
                materialized[str(pkt["packet_id"])] = pkt
        elif isinstance(item, dict):
            pid = item.get("packet_id") or item.get("id")
            if pid:
                materialized[str(pid)] = item
            else:
                passthrough.append(item)
    return list(materialized.values()) + passthrough


def _require_ooda_packet_routes_enabled() -> None:
    """Reusable feature-flag gate for routers with no built-in default.

    Composes the real production predicate (``ooda_packet_routes_enabled``)
    with the real production error envelope builder (``default_bff_error``);
    it does not reimplement any packet-listing business logic.
    """
    if ooda_packet_routes_enabled():
        return
    raise default_bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "OODA packet read routes disabled",
        "PANTHEON_OODA_PACKET_ENABLED is disabled for this BFF instance.",
        precondition_failed="ooda_packet_feature_flag",
        suggestion="Re-enable the OODA packet read surface before retrying this route.",
    )


def _wired_ooda_packet_list_payload(
    packets: list[dict],
    *,
    surface_key: str,
    page_token: Optional[str] = None,
    page_size: int = 20,
    related: Optional[dict] = None,
) -> dict:
    """Adapt the production ``ooda_packet_list_payload`` to the calling
    convention used by strategies/routes/detail.py and runtime/router.py,
    which invoke it without ``snapshot_at``/``page_slice_fn``/
    ``read_surface_meta_fn`` (those routers own that plumbing internally for
    their own routes; the composition root supplies it for the others)."""
    return _production_ooda_packet_list_payload(
        packets,
        surface_key=surface_key,
        page_token=page_token,
        page_size=page_size,
        related=related,
        snapshot_at=_default_utc_now(),
        page_slice_fn=_default_page_slice,
        read_surface_meta_fn=_default_read_surface_meta,
    )


def _mounted_app(store) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_control_loops_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=default_bff_error,
        )
    )
    app.include_router(
        create_evolution_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=default_bff_error,
        )
    )
    app.include_router(
        create_strategies_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=default_bff_error,
            ooda_packet_list_payload=_wired_ooda_packet_list_payload,
            require_ooda_packet_routes_enabled=_require_ooda_packet_routes_enabled,
        )
    )
    app.include_router(
        create_runtime_router(
            read_surface=store,
            dependencies={
                "_extract_identity": _default_extract_identity,
                "_require_read_role": _default_require_read_role,
                "_require_ooda_packet_routes_enabled": _require_ooda_packet_routes_enabled,
                "_ooda_packet_list_payload": _wired_ooda_packet_list_payload,
            },
        )
    )
    return app


@contextmanager
def _ooda_client(
    monkeypatch,
    *,
    packets: Optional[list[dict]] = OODA_PACKETS,
) -> Iterator[TestClient]:
    monkeypatch.delenv("PANTHEON_OODA_PACKET_ENABLED", raising=False)
    if packets is not None:
        normalized = _normalize_ooda_packets(packets)
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"ooda_packets": normalized}
        )
        store.dataset_source = lambda ds: "service_store" if ds == "ooda_packets" else "typed_store"
    else:
        store = create_in_memory_read_surface_ports()
        store.dataset_source = lambda ds: "missing" if ds == "ooda_packets" else "typed_store"
    app = _mounted_app(store)
    yield TestClient(app, raise_server_exceptions=False)


def test_ooda_packet_list_and_detail_read_jsonl_store(monkeypatch) -> None:
    with _ooda_client(monkeypatch) as client:
        listed = client.get("/bff/ooda/packets", headers=HEADERS)
        detail = client.get("/bff/ooda/packets/ooda-packet-001", headers=HEADERS)

    assert listed.status_code == 200, listed.text
    listed_payload = listed.json()
    assert listed_payload["page_info"]["total"] == 2
    assert [item["packet_id"] for item in listed_payload["items"]] == [
        "ooda-packet-002",
        "ooda-packet-001",
    ]
    assert listed_payload["meta"]["surfaces"]["ooda_packets"]["source"] == "service_store"

    assert detail.status_code == 200, detail.text
    detail_payload = detail.json()
    assert detail_payload["data"]["packet_id"] == "ooda-packet-001"
    assert detail_payload["data"]["fail_closed_checks"][0]["passed"] is True
    assert detail_payload["meta"]["surfaces"]["ooda_packet_detail"]["status"] == "ok"


def test_ooda_packet_routes_replay_append_store_envelopes(monkeypatch) -> None:
    packet = {
        **OODA_PACKETS[0],
        "packet_id": "ooda-paper-envelope-001",
        "status": "open",
        "updated_at": "2026-05-15T12:00:00Z",
    }
    acted_packet = {
        **packet,
        "status": "acted",
        "updated_at": "2026-05-15T12:10:00Z",
        "act": {"runtime_binding_id": "runtime-paper-envelope-001"},
    }
    envelopes = [
        {
            "schema_version": "ooda_loop_packet_record.v1",
            "record_type": "packet_snapshot",
            "record_id": "ooda-rec-001",
            "packet_id": "ooda-paper-envelope-001",
            "recorded_at": "2026-05-15T12:00:00Z",
            "payload": packet,
        },
        {
            "schema_version": "ooda_loop_packet_record.v1",
            "record_type": "stage_transition",
            "record_id": "ooda-rec-002",
            "packet_id": "ooda-paper-envelope-001",
            "recorded_at": "2026-05-15T12:10:00Z",
            "payload": {
                "transition": {
                    "packet_id": "ooda-paper-envelope-001",
                    "from_status": "open",
                    "to_status": "acted",
                },
                "packet": acted_packet,
            },
        },
    ]

    with _ooda_client(monkeypatch, packets=envelopes) as client:
        detail = client.get("/bff/ooda/packets/ooda-paper-envelope-001", headers=HEADERS)
        runtime = client.get("/bff/runtimes/runtime-paper-envelope-001/ooda", headers=HEADERS)

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "acted"
    assert detail.json()["data"]["act"]["runtime_binding_id"] == "runtime-paper-envelope-001"

    assert runtime.status_code == 200, runtime.text
    assert [item["packet_id"] for item in runtime.json()["items"]] == ["ooda-paper-envelope-001"]


def test_ooda_packet_filters_and_related_routes(monkeypatch) -> None:
    with _ooda_client(monkeypatch) as client:
        filtered = client.get("/bff/ooda/packets?stage=act&status=open", headers=HEADERS)
        strategy = client.get("/bff/strategies/strat-alpha/ooda", headers=HEADERS)
        runtime = client.get("/bff/runtimes/rt-paper-1/ooda", headers=HEADERS)
        binding = client.get("/bff/runtimes/binding-paper-2/ooda", headers=HEADERS)
        evolution = client.get("/bff/evolution-programs/evo-program-1/ooda", headers=HEADERS)

    for response in (filtered, strategy, runtime, binding, evolution):
        assert response.status_code == 200, response.text

    assert [item["packet_id"] for item in filtered.json()["items"]] == ["ooda-packet-001"]
    assert [item["packet_id"] for item in strategy.json()["items"]] == ["ooda-packet-001"]
    assert [item["packet_id"] for item in runtime.json()["items"]] == ["ooda-packet-001"]
    assert [item["packet_id"] for item in binding.json()["items"]] == ["ooda-packet-002"]
    assert [item["packet_id"] for item in evolution.json()["items"]] == ["ooda-packet-001"]
    assert strategy.json()["meta"]["related"] == {"type": "Strategy", "id": "strat-alpha"}


def test_ooda_packet_unknown_id_is_404_when_source_exists(monkeypatch) -> None:
    with _ooda_client(monkeypatch) as client:
        response = client.get("/bff/ooda/packets/not-a-packet", headers=HEADERS)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_ooda_packet_missing_source_returns_unavailable_surface(monkeypatch) -> None:
    with _ooda_client(monkeypatch, packets=None) as client:
        listed = client.get("/bff/ooda/packets", headers=HEADERS)
        detail = client.get("/bff/ooda/packets/packet-unavailable", headers=HEADERS)

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    assert listed.json()["meta"]["surfaces"]["ooda_packets"]["status"] == "unavailable"

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "degraded"
    assert detail.json()["meta"]["surfaces"]["ooda_packet_detail"]["status"] == "unavailable"


def test_ooda_packet_feature_flag_fails_closed(monkeypatch) -> None:
    with _ooda_client(monkeypatch) as client:
        monkeypatch.setenv("PANTHEON_OODA_PACKET_ENABLED", "false")
        response = client.get("/bff/ooda/packets", headers=HEADERS)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
