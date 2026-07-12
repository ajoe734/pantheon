from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import main as bff_main
import trade_journal

HEADERS = {"Authorization": "Bearer ptj-operator:operator"}


def _client(td: str) -> TestClient:
    episodes = [
        {"trade_episode_id": "e1", "persona_id": "p1", "environment": "paper", "status": "reflected", "instrument_id": "SPY", "account_id": "secret", "coverage": {"execution": {"state": "complete"}}},
        {"trade_episode_id": "e2", "persona_id": "p1", "environment": "paper", "status": "reflection_failed", "instrument_id": "QQQ", "missing_refs": ["pnl"], "coverage": {"outcome": {"state": "partial"}}},
        {"trade_episode_id": "other", "persona_id": "p2", "environment": "paper"},
    ]
    reflections = [{"reflection_id": "r1", "trade_episode_id": "e1", "persona_id": "p1", "environment": "paper", "review_state": "proposed"}]
    patterns = [{"pattern_id": "pat1", "persona_id": "p1", "environment": "paper", "sample_size": 3}]
    for env, name, data in (("PANTHEON_BFF_TRADE_EPISODES_STORE", "episodes.json", episodes), ("PANTHEON_BFF_TRADE_REFLECTIONS_STORE", "reflections.json", reflections), ("PANTHEON_BFF_TRADE_PATTERNS_STORE", "patterns.json", patterns)):
        path = Path(td) / name; path.write_text(json.dumps(data)); os.environ[env] = str(path)
    lessons = [
        {"lesson_id": "l1", "persona_id": "p1", "status": "draft"},
        {"lesson_id": "l2", "persona_id": "p1", "status": "pending_review"},
    ]
    lessons_path = Path(td) / "lessons.json"; lessons_path.write_text(json.dumps(lessons)); os.environ["PANTHEON_BFF_TRADE_LESSONS_STORE"] = str(lessons_path)
    command_path = Path(td) / "commands.jsonl"; command_path.touch(); os.environ["PANTHEON_BFF_TRADE_JOURNAL_COMMAND_STORE"] = str(command_path)
    return TestClient(bff_main.app)


def test_list_detail_inbox_patterns_pagination_and_partial() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        first = client.get("/bff/personas/p1/trade-journal?environment=paper&limit=1", headers=HEADERS)
        assert first.status_code == 200
        assert first.json()["page_info"] == {"next_cursor": 1, "has_more": True}
        second = client.get("/bff/personas/p1/trade-journal?cursor=1&coverage_state=partial", headers=HEADERS)
        assert second.status_code == 200
        detail = client.get("/bff/personas/p1/trade-journal/e1", headers=HEADERS)
        assert detail.json()["data"]["account_id"] == "secret"
        assert client.get("/bff/personas/p1/trade-reflections", headers=HEADERS).json()["data"][0]["reflection_id"] == "r1"
        assert client.get("/bff/personas/p1/trade-patterns", headers=HEADERS).json()["data"][0]["sample_size"] == 3


def test_auth_rbac_cross_persona_and_masking(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        assert client.get("/bff/personas/p1/trade-journal").status_code == 401
        viewer = client.get("/bff/personas/p1/trade-journal/e1", headers={"Authorization": "Bearer view:viewer"})
        assert viewer.json()["data"]["account_id"] == "***"
        original = bff_main._extract_identity
        monkeypatch.setattr(trade_journal, "_allowed", lambda identity, persona_id: persona_id == "p1")
        assert client.get("/bff/personas/p2/trade-journal", headers=HEADERS).status_code == 403
        assert client.post("/bff/personas/p1/trade-journal/e1/reflection:retry", headers={"Authorization": "Bearer view:viewer", "Idempotency-Key": "x"}, json={"reason": "retry"}).status_code == 403


def test_commands_are_idempotent_and_conflicts_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        url = "/bff/personas/p1/trade-journal/e2/reflection:retry"
        headers = {**HEADERS, "Idempotency-Key": "retry-1"}
        first = client.post(url, headers=headers, json={"reason": "recover downstream", "facts_snapshot_ref": "facts://same"})
        duplicate = client.post(url, headers=headers, json={"reason": "recover downstream", "facts_snapshot_ref": "facts://same"})
        conflict = client.post(url, headers=headers, json={"reason": "different"})
        assert first.status_code == duplicate.status_code == 202
        assert first.json()["data"]["receipt_id"] == duplicate.json()["data"]["receipt_id"]
        assert first.json()["data"]["facts_snapshot_ref"] == "facts://same"
        assert conflict.status_code == 409
        submit = client.post("/bff/personas/p1/trade-lessons/l1:submit-review", headers={**HEADERS, "Idempotency-Key": "s1"}, json={"reason": "review"})
        decide = client.post("/bff/personas/p1/trade-lessons/l2:decide", headers={**HEADERS, "Idempotency-Key": "d1"}, json={"reason": "endorse", "decision": "endorsed"})
        assert submit.status_code == decide.status_code == 202
        assert duplicate.json()["meta"]["idempotent_replay"] is True
        records = (Path(td) / "commands.jsonl").read_text().splitlines()
        assert len(records) == 3
        assert first.json()["meta"]["audit"]["durable"] is True


def test_commands_fail_closed_for_missing_target_invalid_transition_and_owner(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        headers = {**HEADERS, "Idempotency-Key": "fail-1"}
        missing = client.post("/bff/personas/p1/trade-journal/missing/reflection:retry", headers=headers, json={"reason": "retry"})
        invalid = client.post("/bff/personas/p1/trade-journal/e1/reflection:retry", headers=headers, json={"reason": "retry"})
        monkeypatch.delenv("PANTHEON_BFF_TRADE_JOURNAL_COMMAND_STORE")
        unavailable = client.post("/bff/personas/p1/trade-journal/e2/reflection:retry", headers=headers, json={"reason": "retry"})
        assert (missing.status_code, missing.json()["error"]["code"]) == (404, "RESOURCE_NOT_FOUND")
        assert (invalid.status_code, invalid.json()["error"]["code"]) == (409, "INVALID_TRANSITION")
        assert (unavailable.status_code, unavailable.json()["error"]["code"]) == (503, "DEPENDENCY_UNAVAILABLE")


def test_command_idempotency_survives_router_memory_reset() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        url = "/bff/personas/p1/trade-journal/e2/reflection:retry"
        headers = {**HEADERS, "Idempotency-Key": "durable-1"}
        first = client.post(url, headers=headers, json={"reason": "retry"})
        replay = TestClient(bff_main.app).post(url, headers=headers, json={"reason": "retry"})
        assert replay.status_code == 202
        assert replay.json()["data"]["receipt_id"] == first.json()["data"]["receipt_id"]
        assert replay.json()["meta"]["idempotent_replay"] is True


def test_downstream_unavailable_is_explicit() -> None:
    with tempfile.TemporaryDirectory() as td:
        _client(td)
        os.environ["PANTHEON_BFF_TRADE_EPISODES_STORE"] = str(Path(td) / "missing.json")
        response = TestClient(bff_main.app).get("/bff/personas/p1/trade-journal", headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
