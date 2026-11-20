"""Execution-state + topic_summary shaping (recorded hosted shapes 2026-11-20)."""
from __future__ import annotations

from services.control_plane.bff.meetings import client as mc
from services.control_plane.bff.meetings.client import (
    MeetingsOwnerClient,
    parse_topic_summary,
    shape_meeting_detail,
    shape_meeting_list,
)

_ES = {
    "status": "dead_letter", "attempt_count": 3, "max_attempts": 3,
    "last_error": "provider timeout", "updated_at": "2026-11-19T01:00:00+00:00",
}


def _raw(mid, topic, mtype="MT-1"):
    return {
        "meeting_id": mid, "meeting_type": mtype, "topic": topic, "status": "convened",
        "round": 0, "participants": [], "execution_state": dict(_ES),
        "effective_status": "execution_failed", "created_at": "2026-11-19T00:00:00Z",
    }


def test_topic_templates_parse_structured():
    a = parse_topic_summary("Pool evolution review for persona-alpha-1 (2026-W47)")
    assert a == {"kind": "pool_evolution_review", "subject_persona_id": "persona-alpha-1",
                 "period": "2026-W47", "lesson_count": None}
    b = parse_topic_summary("Optimisation review for persona-beta: lessons:4")
    assert b == {"kind": "optimisation_review", "subject_persona_id": "persona-beta",
                 "period": None, "lesson_count": 4}
    assert parse_topic_summary("Quarterly strategy chat") is None
    assert parse_topic_summary(None) is None


def test_list_passes_execution_state_and_dead_letter_count():
    payload = {
        "items": [
            _raw("m-1", "Pool evolution review for persona-alpha (2026-W47)"),
            _raw("m-2", "Optimisation review for persona-beta: lessons:4", "MT-2"),
        ],
        "next_cursor": None,
        "summary": {"dead_letter_count": 2},
    }
    out = shape_meeting_list(payload, is_operator=True, caller_user_id="u", snapshot_at="t")
    it = out["items"][0]
    assert it["topic"].startswith("Pool evolution review")  # unchanged
    assert it["topic_summary"]["subject_persona_id"] == "persona-alpha"
    assert it["effective_status"] == "execution_failed" and it["status"] == "convened"
    assert it["execution_state"]["attempt_count"] == 3
    assert out["data"]["summary"]["dead_letter_count"] == 2
    assert out["data"]["summary"]["open_count"] == 0


def test_detail_passes_execution_state_and_topic_summary():
    d = shape_meeting_detail(
        _raw("m-2", "Optimisation review for persona-beta: lessons:4", "MT-2"),
        is_operator=True, caller_user_id="u", snapshot_at="t",
    )
    assert d["effective_status"] == "execution_failed"
    assert d["execution_state"]["last_error"] == "provider timeout"
    assert d["topic_summary"]["lesson_count"] == 4
    assert d["data"]["topic_summary"]["kind"] == "optimisation_review"


def test_client_forwards_include_failed_and_summary(monkeypatch):
    calls = []

    def fake_call(self, method, path, **kw):
        calls.append(path)
        return {"items": [_raw("m-1", "x")], "next_cursor": None, "summary": {"dead_letter_count": 1}}

    monkeypatch.setattr(MeetingsOwnerClient, "_call", fake_call)
    monkeypatch.setattr(MeetingsOwnerClient, "__init__", lambda self, *a, **k: setattr(self, "tenant_id", None))
    out = MeetingsOwnerClient("t", "a").list_meetings(status="upcoming", include_failed=True)
    assert "include_failed=true" in calls[0]
    assert out["summary"] == {"dead_letter_count": 1}
    MeetingsOwnerClient("t", "a").list_meetings(status="upcoming")
    assert "include_failed" not in calls[1]
