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


if __name__ == "__main__":
    unittest.main()
