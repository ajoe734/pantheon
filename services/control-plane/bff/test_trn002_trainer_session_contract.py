"""
Contract tests for TRN-002: trainer session endpoints.

Covers the BFF trainer session surface:
  POST /api/v1/trainer/sessions           -- create session
  GET  /api/v1/trainer/sessions           -- list sessions (persona-scoped)
  GET  /api/v1/trainer/sessions/{id}      -- session detail
  POST /api/v1/trainer/sessions/{id}/message  -- append teaching message
  GET  /api/v1/trainer/sessions/{id}/controls -- get controls
  POST /api/v1/trainer/sessions/{id}/patch    -- patch controls
  GET  /api/v1/trainer/sessions/{id}/preview  -- get preview
  POST /api/v1/trainer/sessions/{id}/preview  -- refresh preview

Commit/discard/replay and rapid-eval are out of scope (TRN-004, TRN-003).
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from test_training_session_service_client import create_training_read_surface_double


OPERATOR_AUTH = "Bearer test-operator:operator"
HEADERS = {"Authorization": OPERATOR_AUTH}

# Fixture session ids from the built-in local snapshot
_ACTIVE_SESSION = "trn-20260419-001"    # status=active, persona=persona-alpha
_COMPLETED_SESSION = "trn-20260418-003"  # status=completed


@contextmanager
def _client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = create_training_read_surface_double()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store


# ------------------------------------------------------------------ #
# POST /api/v1/trainer/sessions
# ------------------------------------------------------------------ #

def test_trn002_create_session_returns_session_contract() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions",
            json={
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "objective": "Tune momentum parameters for macro surprise events.",
                "context_refs": [{"type": "evidence", "id": "ev-trn002-001"}],
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["persona_id"] == "persona-alpha"
        assert payload["session_type"] == "trainer"
        assert payload["status"] == "active"
        assert "session_id" in payload
        assert payload["started_at"] is not None
        assert isinstance(payload["allowedActions"], dict)
        assert "links" in payload
        assert payload["links"]["self"].startswith("/api/v1/trainer/sessions/")


def test_trn002_create_session_missing_persona_id_returns_422() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions",
            json={
                "session_type": "trainer",
                "objective": "Missing persona_id.",
            },
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "persona_id"


def test_trn002_create_session_missing_objective_returns_422() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions",
            json={
                "persona_id": "persona-alpha",
                "session_type": "trainer",
            },
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "objective"


def test_trn002_create_session_wrong_session_type_returns_422() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions",
            json={
                "persona_id": "persona-alpha",
                "session_type": "coaching",
                "objective": "Wrong session type.",
            },
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "session_type"


def test_trn002_create_session_unknown_persona_returns_404() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions",
            json={
                "persona_id": "persona-nonexistent",
                "session_type": "trainer",
                "objective": "Persona does not exist.",
            },
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ #
# GET /api/v1/trainer/sessions
# ------------------------------------------------------------------ #

def test_trn002_list_sessions_returns_paginated_response() -> None:
    with _client() as c:
        resp = c.get(
            "/api/v1/trainer/sessions",
            params={"persona_id": "persona-alpha"},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert "data" in payload
        assert "page_info" in payload
        assert "meta" in payload
        assert "next_page_token" in payload["page_info"]
        assert "total" in payload["page_info"]
        assert "surfaces" in payload["meta"]


def test_trn002_list_sessions_active_filter_returns_active_session() -> None:
    with _client() as c:
        resp = c.get(
            "/api/v1/trainer/sessions",
            params={"persona_id": "persona-alpha", "status": "active"},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert any(s["session_id"] == _ACTIVE_SESSION for s in payload["data"])
        for session in payload["data"]:
            assert session["status"] == "active"


def test_trn002_list_sessions_unknown_persona_returns_404() -> None:
    with _client() as c:
        resp = c.get(
            "/api/v1/trainer/sessions",
            params={"persona_id": "persona-nonexistent"},
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


def test_trn002_list_sessions_unauthenticated_returns_401() -> None:
    # Fail-closed ordering regression: an unauthenticated caller must get 401 even
    # when the required persona_id query param is absent (previously 422-before-401).
    with _client() as c:
        resp = c.get("/api/v1/trainer/sessions")
        assert resp.status_code == 401, resp.text


def test_trn002_list_sessions_missing_persona_id_returns_422_when_authed() -> None:
    with _client() as c:
        resp = c.get("/api/v1/trainer/sessions", headers=HEADERS)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "persona_id"


# ------------------------------------------------------------------ #
# GET /api/v1/trainer/sessions/{session_id}
# ------------------------------------------------------------------ #

def test_trn002_get_session_detail_returns_full_contract() -> None:
    with _client() as c:
        resp = c.get(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}",
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["session_id"] == _ACTIVE_SESSION
        assert payload["status"] == "active"
        assert "persona_id" in payload
        assert "events" in payload
        assert "allowedActions" in payload
        assert "meta" in payload
        assert "surfaces" in payload["meta"]


def test_trn002_get_session_unknown_id_returns_404() -> None:
    with _client() as c:
        resp = c.get(
            "/api/v1/trainer/sessions/trn-nonexistent-999",
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ #
# POST /api/v1/trainer/sessions/{session_id}/message
# ------------------------------------------------------------------ #

def test_trn002_send_message_returns_event_and_session_summary() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/message",
            json={"message_body": "Reduce max drawdown threshold to 6%."},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["session_id"] == _ACTIVE_SESSION
        assert payload["status"] == "active"
        assert "accepted_at" in payload
        assert payload["event"]["message_body"] == "Reduce max drawdown threshold to 6%."
        assert payload["event"]["actor"] == "operator"
        assert payload["event"]["sequence_number"] >= 1
        assert "session_summary" in payload
        assert payload["session_summary"]["message_count"] >= 1
        assert "allowedActions" in payload


def test_trn002_send_message_missing_message_body_returns_422() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/message",
            json={},
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "message_body"


def test_trn002_send_message_to_completed_session_returns_409() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_COMPLETED_SESSION}/message",
            json={"message_body": "This should be rejected."},
            headers=HEADERS,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "status"


def test_trn002_send_message_to_unknown_session_returns_404() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions/trn-nonexistent-999/message",
            json={"message_body": "No session here."},
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ #
# GET /api/v1/trainer/sessions/{session_id}/controls
# ------------------------------------------------------------------ #

def test_trn002_get_controls_returns_controls_contract() -> None:
    with _client() as c:
        resp = c.get(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/controls",
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["session_id"] == _ACTIVE_SESSION
        assert "controls" in payload
        assert isinstance(payload["controls"], list)
        assert "allowedActions" in payload
        assert "meta" in payload


def test_trn002_get_controls_unknown_session_returns_404() -> None:
    with _client() as c:
        resp = c.get(
            "/api/v1/trainer/sessions/trn-nonexistent-999/controls",
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ #
# POST /api/v1/trainer/sessions/{session_id}/patch
# ------------------------------------------------------------------ #

def test_trn002_patch_controls_unknown_session_returns_404() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions/trn-nonexistent-999/patch",
            json={"patches": [{"parameter_key": "risk.max_drawdown", "proposed_value": 0.06}]},
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


def test_trn002_patch_controls_completed_session_returns_409() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_COMPLETED_SESSION}/patch",
            json={"patches": [{"parameter_key": "risk.max_drawdown", "proposed_value": 0.06}]},
            headers=HEADERS,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "status"


def test_trn002_patch_controls_missing_patches_returns_422() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/patch",
            json={},
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "patches"


# ------------------------------------------------------------------ #
# GET /api/v1/trainer/sessions/{session_id}/preview
# ------------------------------------------------------------------ #

def test_trn002_get_preview_returns_preview_contract() -> None:
    with _client() as c:
        resp = c.get(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/preview",
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["session_id"] == _ACTIVE_SESSION
        assert "status" in payload
        assert "metric_delta" in payload
        assert "allowedActions" in payload
        assert "meta" in payload
        assert "surfaces" in payload["meta"]


def test_trn002_get_preview_unknown_session_returns_404() -> None:
    with _client() as c:
        resp = c.get(
            "/api/v1/trainer/sessions/trn-nonexistent-999/preview",
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


def test_trn002_get_preview_unknown_eval_id_returns_404() -> None:
    with _client() as c:
        resp = c.get(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/preview",
            params={"eval_id": "teval-nonexistent"},
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ #
# POST /api/v1/trainer/sessions/{session_id}/preview
# ------------------------------------------------------------------ #

def test_trn002_refresh_preview_returns_updated_eval() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/preview",
            json={"mode": "refresh"},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["session_id"] == _ACTIVE_SESSION
        assert payload["status"] in {"preview_unavailable", "completed", "pending"}
        assert "metric_delta" in payload
        assert "allowedActions" in payload


def test_trn002_refresh_preview_completed_session_returns_409() -> None:
    with _client() as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_COMPLETED_SESSION}/preview",
            json={"mode": "refresh"},
            headers=HEADERS,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "status"


def test_trn002_refresh_preview_unknown_session_returns_404() -> None:
    with _client() as c:
        resp = c.post(
            "/api/v1/trainer/sessions/trn-nonexistent-999/preview",
            json={"mode": "refresh"},
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text
