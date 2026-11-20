"""BFF-STRATEGY-SYNTHETIC-STUBS-20261120 AC4: GET /bff/meetings forwards include_failed
to the consultation owner and keeps execution_state, effective_status and
summary.dead_letter_count next to the persona display name."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.meetings.router import create_meetings_router
from services.control_plane.bff.models import utc_now

PERSONA = "persona-6507750312870d03a241"


class _Identity:
    operator_id = "viewer-1"
    claims: Dict[str, Any] = {}


class _FakeClient:
    calls: List[Dict[str, Any]] = []

    def __init__(self, tenant_id: str, actor_id: str) -> None:
        pass

    def list_meetings(self, **kwargs: Any) -> Dict[str, Any]:
        _FakeClient.calls.append(kwargs)
        items = [{
            "meeting_id": "mtg-492d824b116f9ace", "status": "upcoming", "type": "MT-1",
            "chair_persona_id": PERSONA, "execution_state": {"state": "dead_letter", "last_error": "provider timeout"},
            "effective_status": "execution_failed",
        }] if kwargs.get("include_failed") else []
        return {"items": items, "next_cursor": None, "summary": {"dead_letter_count": 1}}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_meetings_router(
        extract_identity=lambda *_a, **_k: _Identity(),
        require_read_role=lambda *_a, **_k: None,
        require_operator_role=lambda *_a, **_k: None,
        utc_now=utc_now,
        client_factory=lambda t, a: _FakeClient(t, a),
    ))
    return TestClient(app)


def test_include_failed_forwarded_and_dead_letter_preserved() -> None:
    _FakeClient.calls.clear()
    hidden = _client().get("/bff/meetings?status=upcoming")
    shown = _client().get("/bff/meetings?status=upcoming&include_failed=true")
    assert hidden.status_code == shown.status_code == 200
    assert [c.get("include_failed") for c in _FakeClient.calls] == [False, True]
    assert hidden.json()["items"] == []
    body = shown.json()
    row = body["items"][0]
    assert row["effective_status"] == "execution_failed"
    assert row["execution_state"]["last_error"] == "provider timeout"
    assert body["data"]["summary"]["dead_letter_count"] == 1
