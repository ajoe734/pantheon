from __future__ import annotations

import importlib
import json
import os
import unittest
from pathlib import Path
import tempfile
import sys
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
main = importlib.import_module("services.memory.main")
from services.memory.persona_memory_store import PersonaMemoryStore  # noqa: E402


def _payload(entry_id: str = "mem-11111111-1111-1111-1111-111111111111", **overrides):
    payload = {
        "entry_id": entry_id,
        "knowledge_type": "research_finding",
        "content": {
            "headline": "Service-backed institutional memory entry",
            "body": "Persisted through the memory service using the canonical store.",
            "structured_payload": {"confidence": "high"},
            "tags": ["service", "canonical"],
        },
        "source_event_type": "research_task_completed",
        "source_event_id": "rt-001",
        "written_at": "2026-04-20T04:00:00Z",
        "write_authority": "research-svc",
        "scope": "strategy_family",
        "scope_filter": "momentum",
        "contributing_persona_ids": ["persona-alpha"],
        "reuse_count": 3,
    }
    payload.update(overrides)
    return payload


def _persona_payload(memory_id: str = "pmem-11111111-1111-1111-1111-111111111111", **overrides):
    payload = {
        "memory_id": memory_id,
        "persona_id": "persona-alpha",
        "memory_type": "strategy_lesson",
        "content": {
            "summary": "Persona alpha reduces momentum exposure around regime breaks.",
            "structured_payload": {"lag_bars": 2},
            "tags": ["persona", "momentum", "regime_break"],
        },
        "source_event_type": "postmortem_published",
        "source_event_id": "PM-2026-042",
        "written_at": "2026-06-09T01:00:00Z",
        "write_authority": "incident-svc",
        "relevance_scope": "persona_and_committee",
        "reuse_count": 0,
    }
    payload.update(overrides)
    return payload


def _learn_feedback_payload(source_event_id: str = "TEL-2026-0609-001", **overrides):
    payload = {
        "source_event_type": "runtime_telemetry_outcome",
        "source_event_id": source_event_id,
        "write_authority": "telemetry-svc",
        "sponsor_persona_id": "persona-sponsor",
        "contributing_persona_ids": ["persona-alpha", "persona-beta"],
        "summary": "Runtime fill telemetry showed beta should reduce overlap with alpha during regime breaks.",
        "headline": "Runtime telemetry learn feedback",
        "body": "Fill and slippage evidence showed correlated exposure during a regime break.",
        "written_at": "2026-06-09T03:00:00Z",
        "runtime_telemetry_evidence": [
            {
                "ref_type": "telemetry_event",
                "ref_id": "fill-001",
                "event_type": "fill_observation",
                "runtime_binding_id": "rb-001",
            }
        ],
        "contributor_feedback": [
            {
                "persona_id": "persona-alpha",
                "proposal_id": "pap-alpha-001",
                "summary": "Alpha proposal held up, but its fill evidence should inform future sizing.",
                "tags": ["alpha", "fill"],
            },
            {
                "persona_id": "persona-beta",
                "proposal_id": "pap-beta-001",
                "summary": "Beta proposal should cut overlap when alpha already covers the factor.",
                "tags": ["beta", "overlap"],
            },
        ],
        "tags": ["mpos", "runtime"],
    }
    payload.update(overrides)
    return payload


class TestMemoryService(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._tracked_env = {
            key: os.environ.get(key)
            for key in (
                "PANTHEON_MEMORY_STORE",
                "PANTHEON_PERSONA_MEMORY_STORE",
                "PANTHEON_MEMORY_STORE_BACKEND",
                "PANTHEON_PERSONA_MEMORY_STORE_BACKEND",
                "PANTHEON_MEMORY_AUTHZ_MODE",
                "PANTHEON_GOVERNANCE_API_URL",
                "PANTHEON_GOVERNANCE_AUTHZ_URL",
                "PANTHEON_GOVERNANCE_SERVICE_URL",
            )
        }
        self.store_path = Path(self._tempdir.name) / "institutional_memory_entries.json"
        self.persona_store_path = Path(self._tempdir.name) / "persona_memory_entries.json"
        os.environ["PANTHEON_MEMORY_STORE"] = str(self.store_path)
        os.environ["PANTHEON_PERSONA_MEMORY_STORE"] = str(self.persona_store_path)
        os.environ["PANTHEON_MEMORY_STORE_BACKEND"] = "json"
        os.environ["PANTHEON_PERSONA_MEMORY_STORE_BACKEND"] = "json"
        os.environ.pop("PANTHEON_MEMORY_AUTHZ_MODE", None)
        os.environ.pop("PANTHEON_GOVERNANCE_API_URL", None)
        os.environ.pop("PANTHEON_GOVERNANCE_AUTHZ_URL", None)
        os.environ.pop("PANTHEON_GOVERNANCE_SERVICE_URL", None)
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        for key, value in self._tracked_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tempdir.cleanup()

    def test_store_entry_persists_canonical_record(self) -> None:
        response = self.client.post("/api/memory/entries", json=_payload())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"entry_id": "mem-11111111-1111-1111-1111-111111111111"})
        self.assertTrue(self.store_path.exists())
        persisted = self.store_path.read_text(encoding="utf-8")
        self.assertIn("mem-11111111-1111-1111-1111-111111111111", persisted)
        self.assertIn("source_event_type", persisted)

    def test_list_and_detail_routes_use_canonical_store_shape(self) -> None:
        create_response = self.client.post("/api/memory/entries", json=_payload())
        self.assertEqual(create_response.status_code, 201, create_response.text)

        list_response = self.client.get("/api/memory/entries", params={"scope": "strategy_family"})
        self.assertEqual(list_response.status_code, 200, list_response.text)
        list_payload = list_response.json()
        self.assertEqual(list_payload["count"], 1)
        self.assertEqual(list_payload["entries"][0]["entry_id"], "mem-11111111-1111-1111-1111-111111111111")

        detail_response = self.client.get("/api/memory/entries/mem-11111111-1111-1111-1111-111111111111")
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        detail_payload = detail_response.json()
        self.assertEqual(detail_payload["scope"], "strategy_family")
        self.assertEqual(detail_payload["source_event_id"], "rt-001")

    def test_invalid_entry_returns_422(self) -> None:
        response = self.client.post("/api/memory/entries", json={"entry_id": "mem-invalid"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["error"], "invalid_entry")

    def test_store_persona_entry_persists_canonical_record(self) -> None:
        response = self.client.post("/api/memory/persona-entries", json=_persona_payload())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"memory_id": "pmem-11111111-1111-1111-1111-111111111111"})
        self.assertTrue(self.persona_store_path.exists())
        persisted = self.persona_store_path.read_text(encoding="utf-8")
        self.assertIn("pmem-11111111-1111-1111-1111-111111111111", persisted)
        self.assertIn("postmortem_published", persisted)

    def test_persona_writeback_endpoint_validates_trigger_matrix(self) -> None:
        invalid = _persona_payload(
            memory_type="preference",
            source_event_type="operator_feedback",
            write_authority="incident-svc",
        )

        response = self.client.post("/api/memory/writebacks/persona", json=invalid)

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["error"], "invalid_persona_entry")

    def test_learn_feedback_writeback_creates_persona_and_sponsor_memory(self) -> None:
        response = self.client.post("/api/memory/writebacks/learn-feedback", json=_learn_feedback_payload())

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertTrue(payload["created"])
        self.assertEqual(len(payload["persona_memory_ids"]), 2)
        self.assertTrue(payload["institutional_entry_id"].startswith("mem-"))

        persona_records = json.loads(self.persona_store_path.read_text(encoding="utf-8"))
        self.assertEqual({record["persona_id"] for record in persona_records}, {"persona-alpha", "persona-beta"})
        alpha_record = next(record for record in persona_records if record["persona_id"] == "persona-alpha")
        self.assertEqual(alpha_record["source_event_type"], "runtime_telemetry_outcome")
        self.assertEqual(alpha_record["write_authority"], "telemetry-svc")
        self.assertEqual(alpha_record["memory_type"], "risk_observation")
        alpha_structured = alpha_record["content"]["structured_payload"]
        self.assertEqual(alpha_structured["proposal_ids"], ["pap-alpha-001"])
        self.assertEqual(alpha_structured["runtime_telemetry_evidence"][0]["ref_id"], "fill-001")

        institutional_records = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(len(institutional_records), 1)
        institutional_record = institutional_records[0]
        self.assertEqual(
            institutional_record["contributing_persona_ids"],
            ["persona-sponsor", "persona-alpha", "persona-beta"],
        )
        institutional_structured = institutional_record["content"]["structured_payload"]
        self.assertEqual(institutional_structured["sponsor_persona_id"], "persona-sponsor")
        self.assertEqual(set(institutional_structured["proposal_ids"]), {"pap-alpha-001", "pap-beta-001"})

    def test_learn_feedback_writeback_replay_is_idempotent_by_source_event(self) -> None:
        first = self.client.post("/api/memory/writebacks/learn-feedback", json=_learn_feedback_payload())
        second = self.client.post("/api/memory/writebacks/learn-feedback", json=_learn_feedback_payload())

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["persona_memory_ids"], first.json()["persona_memory_ids"])
        self.assertEqual(second.json()["institutional_entry_id"], first.json()["institutional_entry_id"])
        self.assertEqual(len(json.loads(self.persona_store_path.read_text(encoding="utf-8"))), 2)
        self.assertEqual(len(json.loads(self.store_path.read_text(encoding="utf-8"))), 1)

    def test_learn_feedback_writeback_requires_persona_attribution(self) -> None:
        invalid = _learn_feedback_payload(contributing_persona_ids=[], contributor_feedback=[])

        response = self.client.post("/api/memory/writebacks/learn-feedback", json=invalid)

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["error"], "invalid_learn_feedback_writeback")
        self.assertIn("contributing persona", response.json()["detail"]["message"])

    def test_learn_feedback_writeback_rejects_unauthorized_authority(self) -> None:
        invalid = _learn_feedback_payload(
            source_event_type="postmortem_published",
            write_authority="telemetry-svc",
        )

        response = self.client.post("/api/memory/writebacks/learn-feedback", json=invalid)

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"], "learn_feedback_writeback_unauthorized")

    def test_learn_feedback_writeback_accepts_postmortem_and_evolution_outcomes(self) -> None:
        cases = [
            {
                "source_event_type": "postmortem_published",
                "source_event_id": "PM-2026-0609-001",
                "write_authority": "incident-svc",
                "evidence_refs": [{"ref_type": "postmortem", "ref_id": "PM-2026-0609-001"}],
            },
            {
                "source_event_type": "evolution_decision_approved",
                "source_event_id": "EVO-2026-0609-001",
                "write_authority": "evolution-svc",
                "evidence_refs": [{"ref_type": "evolution_decision", "ref_id": "EVO-2026-0609-001"}],
            },
        ]
        for case in cases:
            payload = _learn_feedback_payload(
                source_event_id=case["source_event_id"],
                source_event_type=case["source_event_type"],
                write_authority=case["write_authority"],
                runtime_telemetry_evidence=[],
                evidence_refs=case["evidence_refs"],
            )
            with self.subTest(source_event_type=case["source_event_type"]):
                response = self.client.post("/api/memory/writebacks/learn-feedback", json=payload)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertTrue(response.json()["created"])

    def test_retrieve_fails_closed_when_governance_authz_unconfigured(self) -> None:
        self.client.post("/api/memory/entries", json=_payload())

        response = self.client.get(
            "/api/memory/retrieve",
            params={
                "actor_id": "operator-1",
                "actor_roles": "operator",
                "session_id": "sess-1",
                "scope": "institutional",
                "query": "canonical",
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["reason"], "governance_authz_unconfigured")

    def test_retrieve_uses_authz_and_increments_reuse_count(self) -> None:
        os.environ["PANTHEON_MEMORY_AUTHZ_MODE"] = "local"
        self.client.post("/api/memory/entries", json=_payload(reuse_count=0))

        response = self.client.get(
            "/api/memory/retrieve",
            params={
                "actor_id": "operator-1",
                "actor_roles": "operator",
                "session_id": "sess-1",
                "scope": "institutional",
                "query": "canonical",
                "tags": "service",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["authz"]["reason"], "authorized")
        self.assertEqual(payload["hits"][0]["entry"]["reuse_count"], 1)

    def test_retrieve_persona_memory_uses_authz_and_increments_reuse_count(self) -> None:
        os.environ["PANTHEON_MEMORY_AUTHZ_MODE"] = "local"
        self.client.post("/api/memory/persona-entries", json=_persona_payload(reuse_count=0))

        response = self.client.get(
            "/api/memory/retrieve",
            params={
                "actor_id": "persona-session-1",
                "actor_roles": "persona_session",
                "session_id": "sess-1",
                "session_persona_id": "persona-alpha",
                "persona_id": "persona-alpha",
                "scope": "persona",
                "query": "momentum regime",
                "tags": "regime_break",
                "memory_type": "strategy_lesson",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["hits"][0]["type"], "persona")
        self.assertEqual(payload["hits"][0]["entry"]["reuse_count"], 1)
        replayed = PersonaMemoryStore(path=self.persona_store_path)
        self.assertEqual(replayed.require("pmem-11111111-1111-1111-1111-111111111111").reuse_count, 1)

    def test_retrieve_both_returns_persona_and_institutional_hits(self) -> None:
        os.environ["PANTHEON_MEMORY_AUTHZ_MODE"] = "local"
        self.client.post("/api/memory/entries", json=_payload(reuse_count=0))
        self.client.post("/api/memory/persona-entries", json=_persona_payload(reuse_count=0))

        response = self.client.get(
            "/api/memory/retrieve",
            params={
                "actor_id": "research-session-1",
                "actor_roles": "research_session",
                "session_id": "sess-1",
                "session_persona_id": "persona-alpha",
                "persona_id": "persona-alpha",
                "scope": "both",
                "query": "momentum",
                "tags": "canonical,momentum",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual({hit["type"] for hit in payload["hits"]}, {"persona", "institutional"})
        self.assertEqual({hit["entry"]["reuse_count"] for hit in payload["hits"]}, {1})

    def test_consultation_retrieve_only_returns_committee_scoped_persona_memory(self) -> None:
        os.environ["PANTHEON_MEMORY_AUTHZ_MODE"] = "local"
        self.client.post(
            "/api/memory/persona-entries",
            json=_persona_payload(
                memory_id="pmem-private",
                content={"summary": "Shared consultation momentum note.", "tags": ["consultation", "momentum"]},
                relevance_scope="persona_private",
            ),
        )
        self.client.post(
            "/api/memory/persona-entries",
            json=_persona_payload(
                memory_id="pmem-committee",
                content={"summary": "Shared consultation momentum note.", "tags": ["consultation", "momentum"]},
                relevance_scope="persona_and_committee",
                source_event_id="PM-2026-043",
            ),
        )

        response = self.client.get(
            "/api/memory/retrieve",
            params={
                "actor_id": "consultation-session-1",
                "actor_roles": "consultation_session",
                "session_id": "sess-1",
                "session_persona_id": "persona-alpha",
                "persona_id": "persona-alpha",
                "scope": "persona",
                "query": "consultation momentum",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["hits"][0]["entry"]["memory_id"], "pmem-committee")

    def test_retrieve_rejects_cross_persona_session_access(self) -> None:
        os.environ["PANTHEON_MEMORY_AUTHZ_MODE"] = "local"
        self.client.post("/api/memory/entries", json=_payload())

        response = self.client.get(
            "/api/memory/retrieve",
            params={
                "actor_id": "persona-session-1",
                "actor_roles": "persona_session",
                "session_id": "sess-1",
                "session_persona_id": "persona-alpha",
                "persona_id": "persona-beta",
                "scope": "both",
                "query": "canonical",
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["reason"], "persona_scope_mismatch")

    def test_retrieve_calls_governance_authz_endpoint_when_configured(self) -> None:
        self.client.post("/api/memory/entries", json=_payload())
        os.environ["PANTHEON_GOVERNANCE_API_URL"] = "http://governance:8082"

        def fake_urlopen(request, timeout):
            self.assertEqual(request.full_url, "http://governance:8082/api/governance/authz/check")

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def read(self):
                    return b'{"allowed": true, "reason": "authorized", "policy_version": "governance-authz.v1"}'

            return Response()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = self.client.get(
                "/api/memory/retrieve",
                params={
                    "actor_id": "operator-1",
                    "actor_roles": "operator",
                    "session_id": "sess-1",
                    "scope": "institutional",
                    "query": "canonical",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["authz"]["reason"], "authorized")


# ============================================================================
# Inbound Memory API Authentication & Access Context Ceiling Tests
# ============================================================================

import time
import uuid
import pytest
from services.runtime_auth_inbound import encode_jwt_hs256

JWT_SECRET = "memory-test-secret-key-12345"


def _make_jwt(
    *,
    sub: str | None = "memory-operator-1",
    roles: list[str] | None = None,
    tenant_id: str | None = "tenant-alpha",
    exp_offset: int = 3600,
    secret: str = JWT_SECRET,
    custom_claims: dict | None = None,
) -> str:
    claims: dict = {}
    if sub is not None:
        claims["sub"] = sub
    if roles is not None:
        claims["roles"] = roles
    elif roles is None and "roles" not in (custom_claims or {}):
        claims["roles"] = ["operator", "researcher"]
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if exp_offset is not None:
        claims["exp"] = int(time.time()) + exp_offset
    if custom_claims:
        claims.update(custom_claims)
    return encode_jwt_hs256(claims, secret=secret)


def _institutional_auth_payload(**overrides) -> dict:
    payload = {
        "entry_id": f"mem-{uuid.uuid4()}",
        "knowledge_type": "research_finding",
        "content": {
            "headline": "Auth test finding",
            "body": "Auth test body content",
            "tags": ["auth", "test"],
        },
        "source_event_type": "research_task_completed",
        "source_event_id": "rt-auth-001",
        "written_at": "2026-04-20T04:00:00Z",
        "write_authority": "research-svc",
        "scope": "strategy_family",
        "scope_filter": "momentum",
        "contributing_persona_ids": ["persona-alpha"],
        "reuse_count": 0,
    }
    payload.update(overrides)
    return payload


def _persona_auth_payload(**overrides) -> dict:
    payload = {
        "memory_id": f"pmem-{uuid.uuid4()}",
        "persona_id": "persona-alpha",
        "memory_type": "strategy_lesson",
        "content": {
            "summary": "Persona lesson for auth test",
            "tags": ["auth"],
        },
        "source_event_type": "postmortem_published",
        "source_event_id": "PM-AUTH-001",
        "written_at": "2026-06-09T01:00:00Z",
        "write_authority": "incident-svc",
        "relevance_scope": "persona_private",
        "reuse_count": 0,
    }
    payload.update(overrides)
    return payload


def _retrieve_auth_params(**overrides) -> dict:
    params = {
        "query": "alpha",
        "scope": "institutional",
        "actor_id": "memory-operator-1",
        "actor_roles": ["operator"],
        "session_id": "sess-auth-001",
    }
    params.update(overrides)
    return params


@pytest.fixture
def memory_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_STORE_PATH", str(tmp_path / "memory-store.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE_PATH", str(tmp_path / "persona-store.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "memory-store.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "persona-store.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    main._STORE = None
    main._PERSONA_STORE = None
    return TestClient(main.app)


def test_memory_auth_permissive_mode_unauthenticated_allowed(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "permissive")
    resp = memory_client.get("/api/memory/retrieve", params=_retrieve_auth_params())
    assert resp.status_code == 200, resp.text


def test_memory_auth_strict_mode_missing_token_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    resp = memory_client.get("/api/memory/retrieve", params=_retrieve_auth_params())
    assert resp.status_code == 401
    assert "missing Bearer token" in resp.text


def test_memory_auth_invalid_signature_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    bad_token = _make_jwt(secret="wrong-secret-999")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(),
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


def test_memory_auth_expired_token_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    expired_token = _make_jwt(exp_offset=-100)
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(),
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


def test_memory_auth_missing_required_claims_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)

    # Missing sub
    token = _make_jwt(sub=None)
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "sub" in resp.text

    # Missing tenant
    token = _make_jwt(tenant_id=None)
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "tenant" in resp.text


def test_memory_auth_whitespace_claim_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="   ")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "whitespace" in resp.text


def test_memory_auth_retrieve_actor_forgery_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="verified-operator-1")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(actor_id="forged-operator-2"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "actor_id does not match verified token identity" in resp.text


def test_memory_auth_retrieve_role_elevation_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=["researcher"], sub="memory-operator-1")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(actor_roles=["researcher", "admin"]),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "exceed verified token roles" in resp.text


def test_memory_auth_store_entry_tenant_forgery_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-alpha", roles=["operator"])
    payload = _institutional_auth_payload(tenant_id="tenant-beta")
    resp = memory_client.post(
        "/api/memory/entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Payload tenant does not match verified token tenant" in resp.text


def test_memory_auth_store_entry_without_write_role_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    # Caller has non-writer role
    token = _make_jwt(tenant_id="tenant-alpha", roles=["guest_viewer"])
    payload = _institutional_auth_payload(tenant_id="tenant-alpha")
    resp = memory_client.post(
        "/api/memory/entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_memory_auth_store_entry_auto_populates_tenant(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-gamma", roles=["operator"])
    payload = _institutional_auth_payload()
    payload.pop("tenant_id", None)
    resp = memory_client.post(
        "/api/memory/entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    entry_id = resp.json()["entry_id"]

    # Verify stored entry has tenant_gamma
    get_resp = memory_client.get(
        f"/api/memory/entries/{entry_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["tenant_id"] == "tenant-gamma"


def test_memory_auth_persona_store_tenant_forgery_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-alpha", roles=["operator"])
    payload = _persona_auth_payload(tenant_id="tenant-beta")
    resp = memory_client.post(
        "/api/memory/persona-entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Payload tenant does not match verified token tenant" in resp.text


def test_memory_auth_env_forwards_oidc_and_role_map(memory_client, monkeypatch, tmp_path):
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from services.runtime_auth_inbound import _JWKS_CACHE

    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", "https://idp.example/jwks")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_ISSUER", "https://idp.example/issuer")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_AUDIENCE", "pantheon-stage")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_CLAIMS", "roles,groups,realm_access.roles")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP", "pantheon-operator=operator;pantheon-viewer=viewer")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP_MODE", "strict")

    env = main._memory_auth_env()
    assert env["PANTHEON_RUNTIME_JWKS_URI"] == "https://idp.example/jwks"
    assert env["PANTHEON_RUNTIME_OIDC_ISSUER"] == "https://idp.example/issuer"
    assert env["PANTHEON_RUNTIME_OIDC_AUDIENCE"] == "pantheon-stage"
    assert env["PANTHEON_RUNTIME_ROLE_CLAIMS"] == "roles,groups,realm_access.roles"
    assert env["PANTHEON_RUNTIME_ROLE_MAP"] == "pantheon-operator=operator;pantheon-viewer=viewer"
    assert env["PANTHEON_RUNTIME_ROLE_MAP_MODE"] == "strict"

    _JWKS_CACHE.clear()
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_nums = rsa_key.public_key().public_numbers()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    int_b64 = lambda val: b64(val.to_bytes((val.bit_length() + 7) // 8, "big"))
    kid = "memory-oidc-key-1"
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": kid,
                        "use": "sig",
                        "alg": "RS256",
                        "n": int_b64(pub_nums.n),
                        "e": int_b64(pub_nums.e),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def _mint_rs256(payload: dict) -> str:
        hdr_b64 = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid}).encode("utf-8"))
        pay_b64 = b64(json.dumps(payload).encode("utf-8"))
        sig = rsa_key.sign(f"{hdr_b64}.{pay_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{hdr_b64}.{pay_b64}.{b64(sig)}"

    monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", jwks_path.as_uri())
    monkeypatch.setenv("PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED", "true")
    now = int(time.time())

    # Positive RS256 with mapped `groups` claim
    rs256_group_op = _mint_rs256(
        {
            "iss": "https://idp.example/issuer",
            "aud": "pantheon-stage",
            "sub": "memory-operator-1",
            "tenant_id": "tenant-alpha",
            "email_verified": True,
            "groups": ["pantheon-operator"],
            "iat": now,
            "exp": now + 600,
        }
    )
    store_resp = memory_client.post(
        "/api/memory/entries",
        json=_institutional_auth_payload(tenant_id="tenant-alpha"),
        headers={"Authorization": f"Bearer {rs256_group_op}"},
    )
    assert store_resp.status_code == 201, store_resp.text

    # Positive RS256 with mapped dot-path `realm_access.roles` claim
    rs256_dot_op = _mint_rs256(
        {
            "iss": "https://idp.example/issuer",
            "aud": "pantheon-stage",
            "sub": "memory-operator-1",
            "tenant_id": "tenant-alpha",
            "email_verified": True,
            "realm_access": {"roles": ["pantheon-operator"]},
            "iat": now,
            "exp": now + 600,
        }
    )
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(actor_roles=["operator"]),
        headers={"Authorization": f"Bearer {rs256_dot_op}"},
    )
    assert resp.status_code == 200, resp.text

    # Negative RS256 with unmapped group claim
    rs256_unmapped = _mint_rs256(
        {
            "iss": "https://idp.example/issuer",
            "aud": "pantheon-stage",
            "sub": "memory-operator-1",
            "tenant_id": "tenant-alpha",
            "email_verified": True,
            "groups": ["unmapped-idp-group"],
            "iat": now,
            "exp": now + 600,
        }
    )
    unmapped_resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(actor_roles=["operator"]),
        headers={"Authorization": f"Bearer {rs256_unmapped}"},
    )
    assert unmapped_resp.status_code == 403

    # Negative RS256 omitting all role claims must fail closed even if DEFAULT_ROLE=operator
    monkeypatch.setenv("PANTHEON_MEMORY_DEFAULT_ROLE", "operator")
    rs256_missing_role = _mint_rs256(
        {
            "iss": "https://idp.example/issuer",
            "aud": "pantheon-stage",
            "sub": "memory-operator-1",
            "tenant_id": "tenant-alpha",
            "email_verified": True,
            "iat": now,
            "exp": now + 600,
        }
    )
    for method, path, kwargs in (
        (
            "get",
            "/api/memory/retrieve",
            {"params": _retrieve_auth_params(actor_roles=["operator"])},
        ),
        (
            "post",
            "/api/memory/entries",
            {"json": _institutional_auth_payload(tenant_id="tenant-alpha")},
        ),
    ):
        missing_resp = getattr(memory_client, method)(
            path,
            headers={"Authorization": f"Bearer {rs256_missing_role}"},
            **kwargs,
        )
        assert missing_resp.status_code == 403, f"{method} {path}: {missing_resp.text}"
    _JWKS_CACHE.clear()



if __name__ == "__main__":
    unittest.main()



def test_memory_retrieve_persona_entries_are_tenant_scoped(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    ids = {}
    for tenant in ("tenant-alpha", "tenant-beta"):
        token = _make_jwt(tenant_id=tenant, roles=["operator"])
        entry = _persona_auth_payload(tenant_id=tenant, content={"summary": f"{tenant} private", "tags": ["auth"]})
        ids[tenant] = entry["memory_id"]
        assert memory_client.post(
            "/api/memory/persona-entries", json=entry, headers={"Authorization": f"Bearer {token}"}
        ).status_code == 201
    params = _retrieve_auth_params(scope="persona", persona_id="persona-alpha", query="")
    alpha = _make_jwt(tenant_id="tenant-alpha", roles=["operator"])
    resp = memory_client.get("/api/memory/retrieve", params=params, headers={"Authorization": f"Bearer {alpha}"})
    assert resp.status_code == 200, resp.text
    got = {h["entry"]["memory_id"] for h in resp.json()["hits"]}
    assert ids["tenant-alpha"] in got and ids["tenant-beta"] not in got
    assert {h["entry"]["tenant_id"] for h in resp.json()["hits"]} == {"tenant-alpha"}
    # unauthenticated in strict mode stays rejected
    assert memory_client.get("/api/memory/retrieve", params=params).status_code == 401
    # tenant with no entries sees nothing
    gamma = _make_jwt(tenant_id="tenant-gamma", roles=["operator"])
    resp = memory_client.get("/api/memory/retrieve", params=params, headers={"Authorization": f"Bearer {gamma}"})
    assert resp.status_code == 200 and not any(
        h["entry"]["memory_id"] in ids.values() for h in resp.json()["hits"]
    )


def test_persona_retrieve_tenant_filter_precedes_ranking_limit(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    owned_id = None
    for tenant, count, reuse in [("tenant-alpha", 1, 0), ("tenant-beta", 100, 1000)]:
        token = _make_jwt(tenant_id=tenant, roles=["operator"])
        for _ in range(count):
            entry = _persona_auth_payload(tenant_id=tenant, reuse_count=reuse)
            response = memory_client.post(
                "/api/memory/persona-entries", json=entry,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 201, response.text
            if tenant == "tenant-alpha":
                owned_id = entry["memory_id"]
    response = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(scope="persona", persona_id="persona-alpha", query=""),
        headers={"Authorization": f"Bearer {_make_jwt(tenant_id='tenant-alpha', roles=['operator'])}"},
    )
    assert response.status_code == 200, response.text
    assert owned_id in {h["entry"]["memory_id"] for h in response.json()["hits"]}


def test_persona_retrieve_backend_context_uses_verified_tenant(memory_client, monkeypatch):
    from services.memory import search_retrieval

    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    seen = []

    class Backend:
        def search(self, **kwargs):
            seen.append(kwargs["context"].tenant_id)
            return []

    monkeypatch.setattr(search_retrieval, "get_search_retrieval_backend", lambda: Backend())
    response = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_auth_params(scope="persona", persona_id="persona-alpha", query="lesson"),
        headers={"Authorization": f"Bearer {_make_jwt(tenant_id='tenant-alpha', roles=['operator'])}"},
    )
    assert response.status_code == 200, response.text
    assert seen == ["tenant-alpha"], seen
