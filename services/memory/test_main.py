from __future__ import annotations

import importlib
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


if __name__ == "__main__":
    unittest.main()
