"""
PER-003: Persona registry live integration acceptance tests.

Asserts that /bff/personas and /bff/personas/{id} read paths use the
live persona_registry service (PersonaRegistry JSON store) rather than
fixture-backed fallback data.

Run:
    pytest services/control-plane/bff/test_persona_live_integration.py -q

Task:  PER-003
Owner: Claude2
Reviewer: Codex2
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import ReadSurfacePorts, create_read_surface_ports

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _persona_record(persona_id: str, name: str, lifecycle_state: str = "research_only") -> dict:
    return {
        "persona_id": persona_id,
        "name": name,
        "mandate": f"Test mandate for {name}",
        "lifecycle_state": lifecycle_state,
        "created_at": "2026-05-16T00:00:00Z",
        "status": "active",
    }


def _write_registry(path: Path, personas: list) -> None:
    data = {p["persona_id"]: p for p in personas}
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Test client factory
# ---------------------------------------------------------------------------

def _fresh_client(td: str, registry_path: str) -> TestClient:
    bff_main.read_store = create_read_surface_ports()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    os.environ["PANTHEON_BFF_PERSONA_REGISTRY_STORE"] = registry_path
    return TestClient(bff_main.app)


def _restore(original_store, original_env) -> None:
    bff_main.read_store = original_store
    if original_env is None:
        os.environ.pop("PANTHEON_BFF_PERSONA_REGISTRY_STORE", None)
    else:
        os.environ["PANTHEON_BFF_PERSONA_REGISTRY_STORE"] = original_env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_bff_personas_list_source_is_service_backed() -> None:
    """List endpoint meta.surfaces.persona_list.source must be service_store (service-backed)."""
    with tempfile.TemporaryDirectory() as td:
        registry_path = os.path.join(td, "personas.json")
        _write_registry(Path(registry_path), [
            _persona_record("persona-alpha", "Alpha Momentum"),
            _persona_record("persona-beta", "Beta Reversion"),
        ])
        original_store = bff_main.read_store
        original_env = os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE")
        try:
            client = _fresh_client(td, registry_path)
            resp = client.get("/bff/personas", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            surface_source = (
                body["meta"]
                .get("surfaces", {})
                .get("persona_list", {})
                .get("source")
            )
            assert surface_source == "service_store", (
                f"Expected source=service_store (service_backed), got {surface_source!r}. "
                "Persona list is served from fixture fallback instead of live registry."
            )
        finally:
            _restore(original_store, original_env)


def test_bff_personas_list_returns_seeded_personas() -> None:
    """List endpoint returns all personas written to the live registry."""
    with tempfile.TemporaryDirectory() as td:
        registry_path = os.path.join(td, "personas.json")
        _write_registry(Path(registry_path), [
            _persona_record("persona-alpha", "Alpha Momentum"),
            _persona_record("persona-beta", "Beta Reversion"),
        ])
        original_store = bff_main.read_store
        original_env = os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE")
        try:
            client = _fresh_client(td, registry_path)
            resp = client.get("/bff/personas", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            returned_ids = {item["id"] for item in body["data"]}
            assert "persona-alpha" in returned_ids, "persona-alpha must be in live registry response"
            assert "persona-beta" in returned_ids, "persona-beta must be in live registry response"
        finally:
            _restore(original_store, original_env)


def test_bff_personas_list_pagination() -> None:
    """page_size=1 returns one item; following the next_page_token yields a different item."""
    with tempfile.TemporaryDirectory() as td:
        registry_path = os.path.join(td, "personas.json")
        _write_registry(Path(registry_path), [
            _persona_record("persona-p1", "P1"),
            _persona_record("persona-p2", "P2"),
            _persona_record("persona-p3", "P3"),
        ])
        original_store = bff_main.read_store
        original_env = os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE")
        try:
            client = _fresh_client(td, registry_path)
            resp = client.get("/bff/personas?page_size=1", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]) == 1, "page_size=1 must return exactly 1 item"
            page_info = body.get("page_info", {})
            assert "next_page_token" in page_info, "page_info must have next_page_token"
            next_token = page_info["next_page_token"]
            if next_token:
                resp2 = client.get(
                    f"/bff/personas?page_size=1&page_token={next_token}", headers=HEADERS
                )
                assert resp2.status_code == 200, resp2.text
                body2 = resp2.json()
                assert len(body2["data"]) == 1, "second page must return 1 item"
                assert body2["data"][0]["id"] != body["data"][0]["id"], (
                    "second page item must differ from first page item"
                )
        finally:
            _restore(original_store, original_env)


def test_bff_persona_detail_readback() -> None:
    """Detail endpoint /bff/personas/{id} returns seeded persona with service_store source."""
    with tempfile.TemporaryDirectory() as td:
        registry_path = os.path.join(td, "personas.json")
        _write_registry(Path(registry_path), [
            _persona_record("persona-live-001", "Live Detail Persona", "consultable"),
        ])
        original_store = bff_main.read_store
        original_env = os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE")
        try:
            client = _fresh_client(td, registry_path)
            resp = client.get("/bff/personas/persona-live-001", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"]["id"] == "persona-live-001"
            assert body["data"]["name"] == "Live Detail Persona"
            surface_source = (
                body["meta"]
                .get("surfaces", {})
                .get("persona_detail", {})
                .get("source")
            )
            assert surface_source == "service_store", (
                f"Detail source must be service_store, got {surface_source!r}"
            )
        finally:
            _restore(original_store, original_env)


def test_strict_mode_does_not_fallback_to_fixture() -> None:
    """
    When allow_local_snapshot_fallback=False and no registry env var is set,
    the persona list source must NOT be local_snapshot (fixture_backed).
    """
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_env = os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE")
        try:
            os.environ.pop("PANTHEON_BFF_PERSONA_REGISTRY_STORE", None)
            bff_main.read_store = create_read_surface_ports()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._PERSONA_BFF_OVERLAY.clear()
            client = TestClient(bff_main.app)
            resp = client.get("/bff/personas", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            surface_source = (
                body["meta"]
                .get("surfaces", {})
                .get("persona_list", {})
                .get("source", "")
            )
            assert surface_source != "local_snapshot", (
                "Strict mode must not fall back to fixture (local_snapshot). "
                f"Got source={surface_source!r}"
            )
        finally:
            _restore(original_store, original_env)
