"""MGMT-OODA-005 contract tests for the Control Room OODA status card.

Verifies that GET /bff/v5/control-room includes an ``ooda_status`` field with
five stage cards (observe, orient, decide, act, learn), correct aggregate
counts, fail-closed gate behaviour, and live_capital_side_effects=false
posture for non-live environments.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-mgmt-ooda005:operator,reviewer,admin:mfa"}

# Sample OODA packets covering each stage
PACKETS_MULTI_STAGE = [
    {
        "packet_id": "ooda-005-observe-001",
        "status": "observing",
        "environment": "paper",
        "strategy_id": "strat-obs",
        "created_at": "2026-05-15T10:00:00Z",
        "updated_at": "2026-05-15T10:01:00Z",
    },
    {
        "packet_id": "ooda-005-orient-001",
        "status": "oriented",
        "environment": "paper",
        "strategy_id": "strat-ori",
        "created_at": "2026-05-15T10:05:00Z",
        "updated_at": "2026-05-15T10:06:00Z",
    },
    {
        "packet_id": "ooda-005-decided-001",
        "status": "decided",
        "environment": "paper",
        "decide": {"approval_decision_id": "appr-001"},
        "created_at": "2026-05-15T10:10:00Z",
        "updated_at": "2026-05-15T10:11:00Z",
    },
    {
        "packet_id": "ooda-005-acted-001",
        "status": "acted",
        "environment": "paper",
        "act": {"runtime_binding_id": "rt-paper-1", "live_capital_side_effects": False},
        "created_at": "2026-05-15T10:15:00Z",
        "updated_at": "2026-05-15T10:16:00Z",
    },
    {
        "packet_id": "ooda-005-evolving-001",
        "status": "evolving",
        "environment": "paper",
        "learn": {"evolution_followthrough_refs": ["evo-ref-001"]},
        "created_at": "2026-05-15T10:20:00Z",
        "updated_at": "2026-05-15T10:21:00Z",
    },
    {
        "packet_id": "ooda-005-closed-001",
        "status": "closed",
        "environment": "paper",
        "closed_at": "2026-05-15T11:00:00Z",
        "created_at": "2026-05-15T09:00:00Z",
        "updated_at": "2026-05-15T11:00:00Z",
    },
    {
        "packet_id": "ooda-005-failed-001",
        "status": "failed",
        "environment": "dev",
        "closed_at": "2026-05-15T09:30:00Z",
        "created_at": "2026-05-15T09:25:00Z",
        "updated_at": "2026-05-15T09:30:00Z",
    },
]


@contextmanager
def _cr_client(
    monkeypatch,
    *,
    packets: Optional[List[dict]] = PACKETS_MULTI_STAGE,
    ooda_enabled: bool = True,
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
        monkeypatch.setenv("PANTHEON_OODA_API_URL", "")
        monkeypatch.setenv("PANTHEON_CONTROL_PLANE_OODA_URL", "")
        if ooda_enabled:
            monkeypatch.delenv("PANTHEON_OODA_PACKET_ENABLED", raising=False)
        else:
            monkeypatch.setenv("PANTHEON_OODA_PACKET_ENABLED", "false")
        if packets is not None:
            store_path = Path(td) / "ooda_packets.jsonl"
            store_path.write_text(
                "\n".join(json.dumps(p) for p in packets) + "\n",
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


def test_control_room_includes_ooda_status_card(monkeypatch) -> None:
    with _cr_client(monkeypatch) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "ooda_status" in body, "ooda_status card missing from control-room response"
    card = body["ooda_status"]
    assert card["enabled"] is True
    assert card["gate_state"] == "enabled"
    assert card["live_capital_side_effects"] is False


def test_ooda_status_card_has_five_stage_cards(monkeypatch) -> None:
    with _cr_client(monkeypatch) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    card = resp.json()["ooda_status"]
    stages = card["stages"]
    assert set(stages.keys()) == {"observe", "orient", "decide", "act", "learn"}
    for stage_key, stage_card in stages.items():
        assert "label" in stage_card
        assert "description" in stage_card
        assert "status" in stage_card
        assert "active_count" in stage_card
        assert "detail_link" in stage_card
        assert stage_card["detail_link"] == f"/bff/ooda/packets?stage={stage_key}"


def test_ooda_status_card_aggregate_counts(monkeypatch) -> None:
    """open/closed/failed counts must match sample packet set."""
    with _cr_client(monkeypatch) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    card = resp.json()["ooda_status"]
    # From PACKETS_MULTI_STAGE: 5 open-status packets, 1 closed, 1 failed
    assert card["open_loop_count"] == 5
    assert card["closed_loop_count"] == 1
    assert card["failed_loop_count"] == 1
    assert card["total_packet_count"] == 7


def test_ooda_status_card_per_stage_active_counts(monkeypatch) -> None:
    with _cr_client(monkeypatch) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    stages = resp.json()["ooda_status"]["stages"]
    assert stages["observe"]["active_count"] == 1   # observing
    assert stages["orient"]["active_count"] == 1    # oriented
    assert stages["decide"]["active_count"] == 1    # decided
    assert stages["act"]["active_count"] == 1       # acted
    assert stages["learn"]["active_count"] == 1     # evolving


def test_ooda_status_card_fail_closed_when_feature_disabled(monkeypatch) -> None:
    with _cr_client(monkeypatch, ooda_enabled=False) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    card = resp.json()["ooda_status"]
    assert card["enabled"] is False
    assert card["gate_state"] == "fail_closed"
    assert card["open_loop_count"] == 0
    assert card["total_packet_count"] == 0
    for stage_card in card["stages"].values():
        assert stage_card["status"] == "fail_closed"
        assert stage_card["active_count"] == 0


def test_ooda_status_card_surface_meta_in_control_room_meta(monkeypatch) -> None:
    with _cr_client(monkeypatch) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    body = resp.json()
    meta_surfaces = body["meta"]["surfaces"]
    assert "ooda_control_room_status" in meta_surfaces
    ooda_surface_meta = meta_surfaces["ooda_control_room_status"]
    assert "snapshot_at" in ooda_surface_meta
    assert ooda_surface_meta["surface_key"] == "ooda_control_room_status"
    assert ooda_surface_meta["status"] == "ok"
    assert ooda_surface_meta["source"] == "service_store"


def test_ooda_status_card_no_source_returns_unavailable(monkeypatch) -> None:
    with _cr_client(monkeypatch, packets=None) as client:
        resp = client.get("/bff/v5/control-room", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    card = resp.json()["ooda_status"]
    assert card["enabled"] is True
    assert card["total_packet_count"] == 0
    assert card["meta"]["status"] == "unavailable"
