"""Production wiring regressions for the Agora servant Persona owner."""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_read_surface_ports


def test_production_router_does_not_treat_read_surface_as_servant_writer(
    monkeypatch,
) -> None:
    """The mounted production route must receive a separate durable writer."""

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    read_ports = create_read_surface_ports()
    monkeypatch.setattr(bff_main, "read_store", read_ports)
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: {
            "status": "created",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "workspace_ref": f"workspace://{persona['persona_id']}",
        },
    )

    assert not callable(getattr(read_ports, "create_persona", None))
    assert not callable(getattr(read_ports, "update_persona", None))
    assert not callable(
        getattr(read_ports, "upsert_persona_capability_snapshot", None)
    )

    response = TestClient(
        bff_main.app,
        raise_server_exceptions=False,
    ).post(
        "/bff/agora/servant/ensure",
        headers={
            "Authorization": "Bearer production-write-owner:operator",
            "Idempotency-Key": "production-servant-owner-001",
            "X-Request-Id": "req-production-servant-owner-001",
        },
    )

    assert response.status_code == 200, response.text

