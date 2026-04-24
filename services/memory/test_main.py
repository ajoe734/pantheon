from __future__ import annotations

import importlib
import os
import unittest
from pathlib import Path
import tempfile
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
main = importlib.import_module("services.memory.main")


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


class TestMemoryService(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._original_store = os.environ.get("PANTHEON_MEMORY_STORE")
        self.store_path = Path(self._tempdir.name) / "institutional_memory_entries.json"
        os.environ["PANTHEON_MEMORY_STORE"] = str(self.store_path)
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        if self._original_store is None:
            os.environ.pop("PANTHEON_MEMORY_STORE", None)
        else:
            os.environ["PANTHEON_MEMORY_STORE"] = self._original_store
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


if __name__ == "__main__":
    unittest.main()
