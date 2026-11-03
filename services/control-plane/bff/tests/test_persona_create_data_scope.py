"""PERSONA-DEPLOY-BASELINE-TEST-SCOPE-20261103: create-time data_scope declaration."""
from __future__ import annotations

import tempfile

import pytest

from services.control_plane.bff.personas import fleet_classification as fc
from services.control_plane.bff.tests.test_bff_persona_create_paper_bundle import (
    HEADERS,
    _fresh_client,
    _isolate_persona_create_service_clients,  # noqa: F401  (autouse fixture)
)

_ROUTES = ("/bff/personas", "/bff/management/personas/create-paper-bundle")


def _body(name: str, **extra):
    return {
        "name": name,
        "archetype": "momentum",
        "risk": "low",
        "mandate": "Paper-only lifecycle verification in dev",
        "market": "TW",
        "symbols": ["2330.TW"],
        **extra,
    }


def _post(client, route, body, key):
    return client.post(route, json=body, headers={**HEADERS, "Idempotency-Key": key})


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("scope", ["test", "acceptance", "fixture", "synthetic", "regular"])
def test_declared_scope_is_stored_in_metadata(route, scope) -> None:
    with tempfile.TemporaryDirectory() as td:
        client, store = _fresh_client(td)
        resp = _post(client, route, _body(f"Scope {scope}", data_scope=scope), f"k-{scope}")
        assert resp.status_code == 201, resp.text
        record = store.get_persona(resp.json()["data"]["id"])
        assert record["metadata"]["data_scope"] == scope
        expected = fc.DATA_SCOPE_REGULAR if scope == "regular" else fc.DATA_SCOPE_TEST
        assert fc.classify_persona_data_scope(record) == (expected, "data_scope")


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("bad", ["prod", "unknown", "all", "", 7])
def test_unknown_scope_is_rejected(route, bad) -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _ = _fresh_client(td)
        resp = _post(client, route, _body("Bad Scope", data_scope=bad), "k-bad")
        assert resp.status_code == 422, resp.text


def test_absent_scope_keeps_provenance_classification() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, store = _fresh_client(td)
        resp = _post(client, _ROUTES[0], _body("No Scope"), "k-none")
        assert resp.status_code == 201, resp.text
        record = store.get_persona(resp.json()["data"]["id"])
        assert "data_scope" not in record["metadata"]
        assert fc.classify_persona_data_scope(record) == ("regular", "provisioning_provenance")


def test_replay_with_different_scope_conflicts() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _ = _fresh_client(td)
        assert _post(client, _ROUTES[0], _body("Replay", data_scope="test"), "k-r").status_code == 201
        same = _post(client, _ROUTES[0], _body("Replay", data_scope="test"), "k-r")
        assert same.status_code == 201, same.text
        other = _post(client, _ROUTES[0], _body("Replay", data_scope="regular"), "k-r")
        assert other.status_code in (409, 422), other.text
        assert other.status_code != 201
