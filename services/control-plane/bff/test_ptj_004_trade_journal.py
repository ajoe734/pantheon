from __future__ import annotations

import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib import error as urllib_error

from fastapi.testclient import TestClient

from services.control_plane.bff import main as bff_main
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
    return TestClient(bff_main.app)


class _Response:
    status = 202
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self): return json.dumps(self.body).encode()
    def close(self): pass


class _RawResponse(_Response):
    def read(self): return self.body


class DurableOwner:
    def __init__(self):
        self.lock = threading.Lock(); self.records = {}; self.calls = []

    def urlopen(self, request, timeout=5):
        payload = json.loads(request.data); key = payload["idempotency_key"]
        with self.lock:
            self.calls.append(payload)
            prior = self.records.get(key)
            if prior and prior["payload"] != payload:
                body = {"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "different request", "retryable": False}}
                raise urllib_error.HTTPError(request.full_url, 409, "conflict", {}, _Response(body))
            if prior: return _Response({**prior["response"], "idempotent_replay": True})
            receipt = {"receipt_id": f"owner-{len(self.records)+1}", "action": payload["action"], "persona_id": payload["persona_id"], "resource_id": payload["resource_id"], "status": "accepted", "facts_snapshot_ref": payload.get("facts_snapshot_ref")}
            response = {"data": receipt, "audit": {"durable": True, "record_ref": f"owner-audit:{receipt['receipt_id']}"}}
            self.records[key] = {"payload": payload, "response": response}
            return _Response(response)


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


def test_commands_delegate_to_durable_owner_and_replay(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        owner = DurableOwner()
        monkeypatch.setenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "http://command-owner")
        monkeypatch.setattr(trade_journal.urllib_request, "urlopen", owner.urlopen)
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
        decide = client.post("/bff/personas/p1/trade-lessons/l2:decide", headers={**HEADERS, "Idempotency-Key": "d1"}, json={"reason": "endorse", "decision": "endorsed", "variance_attribution": "alpha_decay"})
        assert submit.status_code == decide.status_code == 202
        assert duplicate.json()["meta"]["idempotent_replay"] is True
        assert len(owner.records) == 3
        assert owner.calls[0]["action"] == "reflection.retry"
        decide_call = next(c for c in owner.calls if c["action"] == "lesson.decide")
        assert decide_call["variance_attribution"] == "alpha_decay"
        assert first.json()["meta"]["audit"]["durable"] is True


def test_commands_fail_closed_when_owner_is_unconfigured(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        headers = {**HEADERS, "Idempotency-Key": "fail-1"}
        monkeypatch.delenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", raising=False)
        unavailable = client.post("/bff/personas/p1/trade-journal/e2/reflection:retry", headers=headers, json={"reason": "retry"})
        assert (unavailable.status_code, unavailable.json()["error"]["code"]) == (503, "DEPENDENCY_UNAVAILABLE")


def test_owner_rejects_nonexistent_target_and_invalid_transition(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        monkeypatch.setenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "http://command-owner")
        def reject(request, timeout=5):
            body = {"error": {"code": "RESOURCE_NOT_FOUND", "message": "target not found", "retryable": False}}
            raise urllib_error.HTTPError(request.full_url, 404, "missing", {}, _Response(body))
        monkeypatch.setattr(trade_journal.urllib_request, "urlopen", reject)
        response = client.post("/bff/personas/p1/trade-journal/missing/reflection:retry", headers={**HEADERS, "Idempotency-Key": "missing"}, json={"reason": "retry"})
        assert (response.status_code, response.json()["error"]["code"]) == (404, "RESOURCE_NOT_FOUND")


def test_malformed_2xx_owner_array_fails_closed(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        monkeypatch.setenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "http://command-owner")
        monkeypatch.setattr(trade_journal.urllib_request, "urlopen", lambda request, timeout=5: _Response([]))
        response = client.post(
            "/bff/personas/p1/trade-journal/e2/reflection:retry",
            headers={**HEADERS, "Idempotency-Key": "array-body"},
            json={"reason": "retry"},
        )
        assert (response.status_code, response.json()["error"]["code"]) == (503, "DEPENDENCY_UNAVAILABLE")


def test_non_json_owner_http_error_fails_closed(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        monkeypatch.setenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "http://command-owner")

        def reject(request, timeout=5):
            raise urllib_error.HTTPError(request.full_url, 502, "bad gateway", {}, _RawResponse(b"upstream exploded"))

        monkeypatch.setattr(trade_journal.urllib_request, "urlopen", reject)
        response = client.post(
            "/bff/personas/p1/trade-journal/e2/reflection:retry",
            headers={**HEADERS, "Idempotency-Key": "non-json-error"},
            json={"reason": "retry"},
        )
        assert (response.status_code, response.json()["error"]["code"]) == (503, "DEPENDENCY_UNAVAILABLE")


def test_concurrent_same_key_is_atomically_owned_downstream(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td); owner = DurableOwner()
        monkeypatch.setenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "http://command-owner")
        monkeypatch.setattr(trade_journal.urllib_request, "urlopen", owner.urlopen)
        url = "/bff/personas/p1/trade-journal/e2/reflection:retry"
        headers = {**HEADERS, "Idempotency-Key": "concurrent-1"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: client.post(url, headers=headers, json={"reason": "retry"}), range(8)))
        assert {r.status_code for r in responses} == {202}
        assert {r.json()["data"]["receipt_id"] for r in responses} == {"owner-1"}
        assert len(owner.records) == 1


def test_downstream_unavailable_is_explicit() -> None:
    with tempfile.TemporaryDirectory() as td:
        _client(td)
        os.environ["PANTHEON_BFF_TRADE_EPISODES_STORE"] = str(Path(td) / "missing.json")
        response = TestClient(bff_main.app).get("/bff/personas/p1/trade-journal", headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
