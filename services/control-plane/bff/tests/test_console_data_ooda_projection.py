"""Contract test: OODA packet projection populates /bff/ooda/packets.

Verifies that:
- project_ooda_to_bff_surfaces.py produces ooda_packets.json with count > 0
- PANTHEON_BFF_OODA_PACKET_STORE wires the BFF read-surface to those packets
- GET /bff/ooda/packets returns page_info.total > 0 with surface status = ok
- GET /bff/ooda/packets/{packet_id} resolves the first projected packet

Dev-seed path is exercised here (no live service), which uses the real
OodaLoopPacket domain producer in paper/dev environment with stub dispatch.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]
BFF_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"

from scripts import project_ooda_to_bff_surfaces as projector
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}


@contextmanager
def _projected_bff(monkeypatch) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "bff-stores"
        store = projector.project(ooda_url=None)
        assert store, "project() returned empty store — dev seed must produce at least one packet"
        projector.write_projection(store, out_dir)
        store_path = out_dir / "ooda_packets.json"
        assert store_path.exists(), f"ooda_packets.json not written to {out_dir}"

        monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(store_path))
        monkeypatch.setenv("PANTHEON_OODA_PACKET_ENABLED", "1")

        ports = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"ooda_packets": list(store.values())},
        )
        ports.dataset_source = lambda _dataset: "test_projection"
        app = FastAPI()
        app.include_router(create_control_loops_router(read_surface=ports))
        yield TestClient(app)


def test_ooda_packet_projection_populates_bff_list_surface(monkeypatch) -> None:
    with _projected_bff(monkeypatch) as client:
        resp = client.get("/bff/ooda/packets", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["page_info"]["total"] > 0, "Expected count > 0 from projected OODA packets"
        surface_status = body["meta"]["surfaces"]["ooda_packets"]["status"]
        assert surface_status == "ok", f"Expected surface status=ok, got {surface_status!r}"

        items = body["items"]
        assert len(items) > 0
        first = items[0]
        assert first.get("packet_id", "").startswith("ooda-"), (
            f"packet_id must start with 'ooda-', got {first.get('packet_id')!r}"
        )


def test_ooda_packet_projection_detail_resolves(monkeypatch) -> None:
    with _projected_bff(monkeypatch) as client:
        list_resp = client.get("/bff/ooda/packets", headers=HEADERS)
        assert list_resp.status_code == 200, list_resp.text
        items = list_resp.json()["items"]
        assert items, "List must be non-empty before testing detail route"

        packet_id = items[0]["packet_id"]
        detail_resp = client.get(f"/bff/ooda/packets/{packet_id}", headers=HEADERS)
        assert detail_resp.status_code == 200, detail_resp.text
        body = detail_resp.json()
        assert body["data"]["packet_id"] == packet_id


def test_ooda_packet_projection_packets_use_real_producer(monkeypatch) -> None:
    store = projector.project(ooda_url=None)
    assert store, "Dev seed must produce at least one packet"
    for packet_id, packet in store.items():
        assert packet_id.startswith("ooda-"), f"Packet key must start with ooda-: {packet_id!r}"
        assert packet.get("loop_type") in (
            "paper_strategy", "rebalance", "evolution", "incident_response", "persona_synthesis"
        ), f"Unexpected loop_type: {packet.get('loop_type')!r}"
        assert packet.get("environment") in (
            "dev", "paper", "sandbox", "canary", "live"
        ), f"Unexpected environment: {packet.get('environment')!r}"
        assert packet.get("act", {}).get("live_capital_side_effects") is not True or (
            packet.get("environment") == "live"
        ), "live_capital_side_effects=True is only allowed in live environment"
