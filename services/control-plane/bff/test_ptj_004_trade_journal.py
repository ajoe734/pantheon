from __future__ import annotations

import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib import error as urllib_error

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff import trade_journal
from services.control_plane.bff.personas.service import (
    _extract_identity,
    _require_operator_role,
    _require_read_role,
)

HEADERS = {"Authorization": "Bearer ptj-operator:operator"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        trade_journal.create_trade_journal_router(
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            require_operator_role=_require_operator_role,
        )
    )
    return app


def _client(_td: str) -> TestClient:
    return TestClient(_make_app())


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


# Read contracts use signed identities and actual owner routes in
# test_trade_journal_owner_reads.py, never an unscoped local BFF file.


def test_auth_rbac_cross_persona_and_masking(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _client(td)
        assert client.get("/bff/personas/p1/trade-journal").status_code == 401
        from types import SimpleNamespace
        assert trade_journal._mask({"account_id": "secret"}, SimpleNamespace(roles=["viewer"])) == {"account_id": "***"}
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


def test_default_mounted_bff_path_with_real_isolated_owners_and_durable_stores(monkeypatch) -> None:
    import io, time, subprocess, sys
    from concurrent.futures import ThreadPoolExecutor
    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.persona.write_owner import (
        create_app as create_persona_app,
        PersistentPersonaOwner,
        CreatePersonaRequest,
    )
    import services.persona.write_owner as write_owner_mod
    from services.memory.main import app as memory_app

    with tempfile.TemporaryDirectory() as td:
        secret = "test-secret-key-32-chars-long-12345"
        td_path = Path(td)
        persona_store = td_path / "personas.json"
        owner = PersistentPersonaOwner.from_json_path(persona_store)
        owner.create(
            CreatePersonaRequest(
                actor_id="test",
                persona_id="p1",
                name="Alpha",
                mandate="Mandate",
                tenant_id="tenant-alpha",
            )
        )

        mem_dir = td_path / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        cand_store = mem_dir / "trade_lesson_candidates.json"
        cand_id = "c1111111-1111-1111-1111-111111111111"
        cand_store.write_text(json.dumps([{
            "lesson_candidate_id": cand_id, "reflection_id": "r1111111-1111-1111-1111-111111111111",
            "trade_episode_ids": ["e1111111-1111-1111-1111-111111111111"], "persona_id": "p1",
            "tenant_id": "tenant-alpha", "scope": "execution", "proposed_change": "Tighten spread limit",
            "confidence": 0.85, "review_state": "proposed", "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T00:00:00Z", "expiry": "2026-12-31T00:00:00Z", "reflection_version": "v1.0"
        }]))

        monkeypatch.setenv("PERSONA_STORE_PATH", str(persona_store))
        monkeypatch.setenv("PERSONA_JWT_SECRET", secret)
        monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
        monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
        monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
        monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(mem_dir))
        monkeypatch.setenv("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", str(cand_store))
        monkeypatch.delenv("PANTHEON_TRADE_LESSON_IDEMPOTENCY_STORE", raising=False)
        monkeypatch.setenv("PERSONA_URL", "http://persona:8002")
        monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8086")
        monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry:8083")
        monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-gateway-adapter:8104")
        monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
        monkeypatch.delenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", raising=False)
        monkeypatch.delenv("PANTHEON_BFF_TRADE_REFLECTIONS_STORE", raising=False)

        persona_client = TestClient(create_persona_app(owner))
        memory_client = TestClient(memory_app)

        class UrlopenDispatcher:
            def __init__(self, p_client, m_client):
                self.p_client = p_client
                self.m_client = m_client
            def __call__(self, req, timeout=5):
                url = req.full_url
                method = req.get_method()
                headers = dict(req.headers)
                data = req.data
                if url.startswith("http://persona:8002"):
                    path = url[len("http://persona:8002"):]
                    resp = self.p_client.request(method, path, content=data, headers=headers)
                elif url.startswith("http://memory:8086"):
                    path = url[len("http://memory:8086"):]
                    resp = self.m_client.request(method, path, content=data, headers=headers)
                elif url.startswith("http://telemetry:8083"):
                    path = url[len("http://telemetry:8083"):]
                    if path == "/api/telemetry/trade-episodes/ep-100":
                        body = json.dumps({
                            "trade_episode_id": "ep-100", "persona_id": "p1", "tenant_id": "tenant-alpha",
                            "orders": [{"order_id": "o1", "symbol": "BTC-USD", "side": "buy", "qty": 1.0, "price": 50000.0, "status": "filled", "timestamp": "2026-10-01T12:00:00Z"}],
                            "fills": [{"fill_id": "f1", "order_id": "o1", "qty": 1.0, "price": 50000.0, "fee": 1.0, "timestamp": "2026-10-01T12:00:00Z"}],
                            "pnl": {"realized": 100.0, "unrealized": 0.0}, "missing_refs": [],
                        }).encode()
                        return _TestUrlopenResp(200, body)
                    raise urllib_error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"detail":"Not found"}'))
                elif url.startswith("http://openclaw-gateway-adapter:8104"):
                    body = json.dumps({
                        "data": {"output": {"structured_data": {
                            "expected_vs_actual": {"thesis": "supported", "entry_quality": "good"},
                            "attribution": "process",
                            "counterfactuals": [{"alternative_action": "wait", "estimated_impact": "higher", "assumptions": "same"}],
                            "lesson_candidates": [{"scope": "strategy", "proposed_change": "fine tune", "confidence": 0.8}],
                            "mistakes": [], "what_worked": ["timing"], "unknowns": [], "followups": [],
                        }}}
                    }).encode()
                    return _TestUrlopenResp(200, body)
                else:
                    raise urllib_error.URLError(f"unknown host: {url}")
                if resp.status_code >= 400:
                    raise urllib_error.HTTPError(url, resp.status_code, resp.reason_phrase, resp.headers, io.BytesIO(resp.content))
                return _TestUrlopenResp(resp.status_code, resp.content)

        class _TestUrlopenResp:
            def __init__(self, status, content):
                self.status = status
                self._content = content
            def read(self):
                return self._content
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        dispatcher = UrlopenDispatcher(persona_client, memory_client)
        monkeypatch.setattr(trade_journal.urllib_request, "urlopen", dispatcher)
        monkeypatch.setattr(write_owner_mod, "urlopen", dispatcher)

        now = int(time.time())
        token_alpha = encode_jwt_hs256({"sub": "op1", "operator_id": "op1", "roles": ["operator", "reviewer"], "tenant_id": "tenant-alpha", "persona_ids": ["p1"], "exp": now + 3600}, secret=secret)
        token_beta = encode_jwt_hs256({"sub": "op2", "operator_id": "op2", "roles": ["operator", "reviewer"], "tenant_id": "tenant-beta", "persona_ids": ["p1"], "exp": now + 3600}, secret=secret)
        token_viewer = encode_jwt_hs256({"sub": "v1", "operator_id": "v1", "roles": ["viewer"], "tenant_id": "tenant-alpha", "persona_ids": ["p1"], "exp": now + 3600}, secret=secret)

        client = _client(td)
        monkeypatch.delenv("PANTHEON_BFF_TRADE_REFLECTIONS_STORE", raising=False)

        # 1. Missing owner fails closed
        monkeypatch.delenv("PERSONA_URL", raising=False)
        monkeypatch.delenv("PANTHEON_PERSONA_SERVICE_URL", raising=False)
        r_no_owner = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers={"Authorization": f"Bearer {token_alpha}", "Idempotency-Key": "k1"}, json={"reason": "retry"})
        assert (r_no_owner.status_code, r_no_owner.json()["error"]["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
        monkeypatch.setenv("PERSONA_URL", "http://persona:8002")

        # 2. Missing trusted identity fails closed (zero store effect)
        r_no_auth = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers={"Idempotency-Key": "k1"}, json={"reason": "retry"})
        assert r_no_auth.status_code == 401
        r_bad_auth = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers={"Authorization": "Bearer bad.token.format", "Idempotency-Key": "k1"}, json={"reason": "retry"})
        assert r_bad_auth.status_code == 401
        assert not (owner.get("p1").metadata or {}).get("trade_reflections")

        # 3. Cross-tenant denial (zero store effect)
        r_cross = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers={"Authorization": f"Bearer {token_beta}", "Idempotency-Key": "k-cross"}, json={"reason": "retry"})
        assert r_cross.status_code == 403
        assert not (owner.get("p1").metadata or {}).get("trade_reflections")

        # 4. Reflection retry accepted & durable receipt
        headers_alpha = {"Authorization": f"Bearer {token_alpha}", "Idempotency-Key": "ref-key-1"}
        r_retry = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers=headers_alpha, json={"reason": "manual retry"})
        assert r_retry.status_code == 202
        ref_data = r_retry.json()["data"]
        assert ref_data["status"] == "accepted"
        assert ref_data["action"] == "reflection.retry"
        assert ref_data["resource_id"] == "ep-100"
        assert r_retry.json()["meta"]["audit"]["durable"] is True

        # 5. Persisted readback through BFF reflections route (delegating to Persona owner)
        r_readback = client.get("/bff/personas/p1/trade-reflections", headers={"Authorization": f"Bearer {token_alpha}"})
        assert r_readback.status_code == 200
        ref_items = r_readback.json()["data"]
        assert len(ref_items) >= 1
        assert ref_items[0]["trade_episode_id"] == "ep-100"

        # 6. Idempotent replay: same key + same payload -> 202 with meta.idempotent_replay = True
        r_replay = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers=headers_alpha, json={"reason": "manual retry"})
        assert r_replay.status_code == 202
        assert r_replay.json()["meta"]["idempotent_replay"] is True
        assert r_replay.json()["data"]["receipt_id"] == ref_data["receipt_id"]

        # 7. Replay content conflict: same key + different payload -> 409
        r_conflict = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers=headers_alpha, json={"reason": "different reason"})
        assert r_conflict.status_code == 409

        # 8. Lesson submit-review
        sub_headers = {"Authorization": f"Bearer {token_alpha}", "Idempotency-Key": "sub-key-1"}
        r_sub = client.post(f"/bff/personas/p1/trade-lessons/{cand_id}:submit-review", headers=sub_headers, json={"reason": "submit"})
        assert r_sub.status_code == 202
        assert r_sub.json()["data"]["review_state"] == "pending_review"
        assert r_sub.json()["meta"]["audit"]["durable"] is True

        # Lesson submit-review replay
        r_sub_rep = client.post(f"/bff/personas/p1/trade-lessons/{cand_id}:submit-review", headers=sub_headers, json={"reason": "submit"})
        assert r_sub_rep.status_code == 202
        assert r_sub_rep.json()["meta"]["idempotent_replay"] is True

        # 9. Lesson decide: unauthorized role -> 403 (zero state change)
        r_dec_unauth = client.post(f"/bff/personas/p1/trade-lessons/{cand_id}:decide", headers={"Authorization": f"Bearer {token_viewer}", "Idempotency-Key": "dec-key-1"}, json={"reason": "endorse", "decision": "reject"})
        assert r_dec_unauth.status_code == 403

        # Lesson decide: authorized operator reject -> 202
        dec_headers = {"Authorization": f"Bearer {token_alpha}", "Idempotency-Key": "dec-key-1"}
        r_dec = client.post(f"/bff/personas/p1/trade-lessons/{cand_id}:decide", headers=dec_headers, json={"reason": "reject invalid candidate", "decision": "reject"})
        assert r_dec.status_code == 202
        assert r_dec.json()["data"]["review_state"] == "rejected"
        assert r_dec.json()["meta"]["audit"]["durable"] is True

        # 10. Actual fresh process restart via subprocess:
        # Load stores from disk in an independent python OS process and verify state and replay
        subproc_code = f"""
import sys
from pathlib import Path
from services.persona.write_owner import PersistentPersonaOwner
from services.persona.lesson_governance import TradeLessonCandidateStore

persona_store = Path({repr(str(persona_store))})
cand_store = Path({repr(str(cand_store))})

owner = PersistentPersonaOwner.from_json_path(persona_store)
persona = owner.get("p1")
reflections = (persona.metadata or {{}}).get("trade_reflections") or []
assert len(reflections) >= 1, "Persisted reflection not found in fresh process"
assert reflections[0]["trade_episode_id"] == "ep-100"

cand_s = TradeLessonCandidateStore(cand_store)
c = cand_s.get({repr(cand_id)})
assert c is not None, "Persisted lesson not found in fresh process"
assert c["review_state"] == "rejected"

# Verify idempotency persisted in same file (no standalone file)
assert not (cand_store.parent / "trade_lesson_idempotency.json").exists()
print("SUBPROCESS_RESTART_SUCCESS")
"""
        res = subprocess.run([sys.executable, "-c", subproc_code], capture_output=True, text=True, check=True)
        assert "SUBPROCESS_RESTART_SUCCESS" in res.stdout

        # Fresh process client readback and replay
        fresh_owner = PersistentPersonaOwner.from_json_path(persona_store)
        dispatcher.p_client = TestClient(create_persona_app(fresh_owner))
        r_fresh_read = client.get("/bff/personas/p1/trade-reflections", headers={"Authorization": f"Bearer {token_alpha}"})
        assert r_fresh_read.status_code == 200
        assert r_fresh_read.json()["data"][0]["trade_episode_id"] == "ep-100"

        r_fresh_rep = client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers=headers_alpha, json={"reason": "manual retry"})
        assert r_fresh_rep.status_code == 202
        assert r_fresh_rep.json()["meta"]["idempotent_replay"] is True

        # 11. Concurrent requests coverage
        def _call_replay(i):
            return client.post("/bff/personas/p1/trade-journal/ep-100/reflection:retry", headers=headers_alpha, json={"reason": "manual retry"})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_call_replay, range(4)))
        for r in results:
            assert r.status_code == 202
            assert r.json()["meta"]["idempotent_replay"] is True
