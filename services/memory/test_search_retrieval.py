"""Tests for unified Search backend integration in memory services."""

import unittest
import uuid
from services.memory.institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryStore,
)
from services.memory.persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryStore,
)
from services.memory.search_retrieval import (
    project_institutional_entry,
    project_persona_entry,
    retrieve_institutional_with_backend,
    retrieve_persona_with_backend,
)
from services.search.pg_retrieval import PostgresRetrievalBackend


class TestSearchRetrievalIntegration(unittest.TestCase):
    def setUp(self):
        self.dsn = "postgresql://postgres:postgres@localhost:25432/pantheon_search"
        self.inst_store = InstitutionalMemoryStore()
        self.persona_store = PersonaMemoryStore()
        try:
            self.backend = PostgresRetrievalBackend(dsn=self.dsn)
            health = self.backend.check_health()
            self.has_backend = health.get("status") == "ok"
        except Exception:
            self.has_backend = False

    def test_project_institutional_entry(self):
        eid = f"mem-{uuid.uuid4()}"
        entry = InstitutionalMemoryEntry(
            entry_id=eid,
            knowledge_type="incident_lesson",
            content={"headline": "止損執行失敗分析", "body": "流動性不足導致滑價過大", "tags": ["risk", "execution"]},
            source_event_type="postmortem_published",
            source_event_id="pm-101",
            written_at="2026-09-01T12:00:00Z",
            write_authority="incident-svc",
            scope="system_wide",
        )
        rec = project_institutional_entry(entry)
        self.assertEqual(rec.id, eid)
        self.assertEqual(rec.record_kind, "institutional_memory")
        self.assertEqual(rec.title, "止損執行失敗分析")
        self.assertIn("流動性不足", rec.search_text)
        self.assertIn("public", rec.access_scope)

    def test_project_persona_entry(self):
        mid = str(uuid.uuid4())
        entry = PersonaMemoryEntry(
            memory_id=mid,
            persona_id="persona-alpha",
            memory_type="preference",
            content={"summary": "偏好動能均線策略", "tags": ["momentum", "twse"]},
            source_event_type="session_end",
            source_event_id="sess-101",
            written_at="2026-09-01T12:00:00Z",
            write_authority="persona-memory-svc",
        )
        rec = project_persona_entry(entry)
        self.assertEqual(rec.id, mid)
        self.assertEqual(rec.record_kind, "persona_memory")
        self.assertEqual(rec.persona_id, "persona-alpha")
        self.assertIn("偏好動能均線", rec.search_text)

    def test_end_to_end_search_memory_retrieval(self):
        if not self.has_backend:
            self.skipTest("Local Postgres backend container not reachable")

        # 1. Add and index institutional entry
        eid = f"mem-{uuid.uuid4()}"
        entry = InstitutionalMemoryEntry(
            entry_id=eid,
            knowledge_type="incident_lesson",
            content={"headline": "台股期貨高頻停損檢討", "body": "因瞬時買賣單失衡導致強制平倉", "tags": ["taiwan", "futures"]},
            source_event_type="postmortem_published",
            source_event_id="pm-e2e-1",
            written_at="2026-09-01T10:00:00Z",
            write_authority="incident-svc",
            scope="system_wide",
        )
        self.inst_store.create(entry)
        rec = project_institutional_entry(entry)
        self.backend.index_documents([rec])

        hits = retrieve_institutional_with_backend(
            self.inst_store,
            self.backend,
            query="期貨強制平倉",
            limit=5,
        )
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].entry.entry_id, eid)

        # 2. Persona isolation check
        p_id_1 = str(uuid.uuid4())
        p_id_2 = str(uuid.uuid4())
        p_entry_1 = PersonaMemoryEntry(
            memory_id=p_id_1,
            persona_id="persona-tw-trader",
            memory_type="strategy_lesson",
            content={"summary": "避免在開盤五分鐘內進行大部位下單", "tags": ["trading", "rules"]},
            source_event_type="session_end",
            source_event_id="sess-e2e-1",
            written_at="2026-09-01T10:00:00Z",
            write_authority="persona-memory-svc",
        )
        p_entry_2 = PersonaMemoryEntry(
            memory_id=p_id_2,
            persona_id="persona-us-macro",
            memory_type="strategy_lesson",
            content={"summary": "Avoid trading during Fed interest rate announcement", "tags": ["macro", "fomc"]},
            source_event_type="session_end",
            source_event_id="sess-e2e-2",
            written_at="2026-09-01T10:00:00Z",
            write_authority="persona-memory-svc",
        )
        self.persona_store.create(p_entry_1)
        self.persona_store.create(p_entry_2)
        rec1 = project_persona_entry(p_entry_1)
        rec2 = project_persona_entry(p_entry_2)
        self.backend.index_documents([rec1, rec2])

        # Query persona-tw-trader
        p_hits = retrieve_persona_with_backend(
            self.persona_store,
            persona_id="persona-tw-trader",
            backend=self.backend,
            query="開盤大部位下單",
            limit=5,
        )
        self.assertEqual(len(p_hits), 1)
        self.assertEqual(p_hits[0].entry.persona_id, "persona-tw-trader")

        # Re-query with different persona should NOT return persona-tw-trader's memories
        other_hits = retrieve_persona_with_backend(
            self.persona_store,
            persona_id="persona-us-macro",
            backend=self.backend,
            query="開盤大部位下單",
            limit=5,
        )
        for h in other_hits:
            self.assertNotEqual(h.entry.persona_id, "persona-tw-trader")


if __name__ == "__main__":
    unittest.main()
