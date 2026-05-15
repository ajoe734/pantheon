"""MGMT-OODA-004 contract tests for BFF OODA packet read routes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


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


@contextmanager
def _ooda_client(
    monkeypatch,
    *,
    packets: Optional[list[dict]] = OODA_PACKETS,
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
        monkeypatch.setenv("PANTHEON_OODA_API_URL", "")
        monkeypatch.setenv("PANTHEON_CONTROL_PLANE_OODA_URL", "")
        monkeypatch.delenv("PANTHEON_OODA_PACKET_ENABLED", raising=False)
        if packets is not None:
            store_path = Path(td) / "ooda_packets.jsonl"
            store_path.write_text(
                "\n".join(json.dumps(packet, sort_keys=True) for packet in packets) + "\n",
                encoding="utf-8",
            )
            monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(store_path))
        else:
            monkeypatch.delenv("PANTHEON_BFF_OODA_PACKET_STORE", raising=False)
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store


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
    assert response.json()["detail"]["error"]["code"] == "OBJECT_NOT_FOUND"


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
    assert response.json()["detail"]["error"]["code"] == "DOWNSTREAM_UNAVAILABLE"
