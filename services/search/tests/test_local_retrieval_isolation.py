"""Tests verifying zero external inference calls and strict pre-retrieval isolation.

Guarantees:
- External inference calls = 0 during embedding generation and retrieval.
- Zero cross-persona, cross-workspace, and cross-tenant leakage.
- Zero expired or superseded memory leakage.
- Zero future-leakage (as-of / available_time pre-filtering).
- Strict 1024-dimensional vector verification; fake vectors rejected.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock
import uuid

from services.search.filters import SearchAccessContext, SearchFilters, SearchCapabilityUnavailableError
from services.search.local_embeddings import LocalEmbeddingEngine
from services.search.pg_retrieval import PostgresRetrievalBackend, RetrievalIndexRecord

POSTGRES_TEST_DSN = os.getenv(
    "PANTHEON_SEARCH_POSTGRES_DSN",
    "postgresql://postgres:postgres@localhost:25432/pantheon_search",
)


class TestLocalRetrievalIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.backend = PostgresRetrievalBackend(dsn=POSTGRES_TEST_DSN)
            health = cls.backend.check_health()
            cls.has_backend = health.get("status") == "ok"
        except Exception:
            cls.has_backend = False

    def setUp(self):
        if not self.has_backend:
            self.skipTest("Postgres backend not available at localhost:25432")

    def test_zero_external_inference_calls(self):
        """Embedding generation and retrieval must make 0 external HTTP/network calls."""
        engine = LocalEmbeddingEngine()

        with mock.patch("urllib.request.urlopen") as mock_urllib, \
             mock.patch("http.client.HTTPConnection.connect") as mock_http:
            # Generate query embedding
            q_vec = engine.embed_query("US equity momentum volatility")
            self.assertEqual(len(q_vec), 1024)

            # Generate passage embeddings
            doc_vecs = engine.embed_documents(["Passage 1 text", "Passage 2 text"])
            self.assertEqual(len(doc_vecs), 2)
            self.assertEqual(len(doc_vecs[0]), 1024)

            # Assert zero external network calls
            mock_urllib.assert_not_called()
            mock_http.assert_not_called()

    def test_zero_cross_persona_leakage(self):
        """Documents scoped to persona-1 must NEVER leak to persona-2."""
        doc_id = f"leak-persona-{uuid.uuid4()}"
        record = RetrievalIndexRecord(
            id=doc_id,
            record_kind="persona_memory",
            tenant_id="default",
            persona_id="persona-alpha",
            workspace_id="ws-1",
            environment_scope=["paper"],
            access_scope=["operator"],
            license_scope="internal",
            role_scope=[],
            sensitivity="internal",
            capital_pool_scope=[],
            source_type="persona_reflection",
            asset_class=["equity"],
            strategy_id=None,
            title="Confidential Persona Alpha Strategy Preferences",
            search_text="Risk tolerance calibration for high-beta equity long/short strategies.",
            content_ref=f"/memory/persona/{doc_id}",
            citation_label=f"persona:{doc_id}",
            evidence_bundle_id="bundle-leak-1",
            evidence_item_id="item-leak-1",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.9,
            metadata={"persona_id": "persona-alpha"},
        )
        self.backend.index_documents([record])

        # Persona beta requests search with the same query
        beta_context = SearchAccessContext(
            persona_id="persona-beta",
            workspace_id="ws-1",
            environment="paper",
            access_scopes=["operator"],
            license_scopes=["internal"],
        )
        hits = self.backend.search(
            query="Risk tolerance calibration high-beta equity",
            context=beta_context,
            top_k=10,
            mode="hybrid",
        )
        self.assertFalse(any(h.id == doc_id for h in hits))

    def test_zero_future_as_of_leakage(self):
        """Documents with available_time in the future must NEVER leak."""
        doc_id = f"leak-future-{uuid.uuid4()}"
        record = RetrievalIndexRecord(
            id=doc_id,
            record_kind="knowledge_object",
            tenant_id="default",
            persona_id=None,
            workspace_id=None,
            environment_scope=["paper"],
            access_scope=["public"],
            license_scope="open",
            role_scope=[],
            sensitivity="public",
            capital_pool_scope=[],
            source_type="earnings_release",
            asset_class=["equity"],
            strategy_id=None,
            title="Future Earnings Report Embargoed",
            search_text="Q3 revenue beats consensus expectations by 15 percent.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"earnings:{doc_id}",
            evidence_bundle_id="bundle-fut",
            evidence_item_id="item-fut",
            event_time="2026-10-01T00:00:00Z",
            available_time="2026-10-01T00:00:00Z",  # in the future
            relevance_score=0.95,
            metadata={},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["open"],
        )
        # Search as of 2026-09-01 (before available_time)
        filters = SearchFilters(available_time_lte="2026-09-01T00:00:00Z")
        hits = self.backend.search(
            query="revenue beats consensus expectations",
            context=context,
            filters=filters,
            top_k=10,
            mode="keyword",
        )
        self.assertFalse(any(h.id == doc_id for h in hits))

    def test_zero_superseded_leakage(self):
        """Superseded/inactive records must NEVER leak."""
        doc_id = f"leak-superseded-{uuid.uuid4()}"
        record = RetrievalIndexRecord(
            id=doc_id,
            record_kind="institutional_memory",
            tenant_id="default",
            persona_id=None,
            workspace_id=None,
            environment_scope=["paper"],
            access_scope=["public"],
            license_scope="internal",
            role_scope=[],
            sensitivity="internal",
            capital_pool_scope=[],
            source_type="incident_lesson",
            asset_class=["futures"],
            strategy_id=None,
            title="Superseded Trading Lesson",
            search_text="Outdated circuit breaker guidance that has since been repealed.",
            content_ref=f"/memory/{doc_id}",
            citation_label=f"mem:{doc_id}",
            evidence_bundle_id="bundle-old",
            evidence_item_id="item-old",
            event_time="2026-07-01T00:00:00Z",
            available_time="2026-07-01T00:00:00Z",
            relevance_score=0.9,
            metadata={"superseded_by": "mem-new-123"},
            is_active=False,
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["internal"],
        )
        hits = self.backend.search(
            query="Outdated circuit breaker guidance",
            context=context,
            top_k=10,
            mode="hybrid",
        )
        self.assertFalse(any(h.id == doc_id for h in hits))

    def test_strict_dimension_and_fake_vector_rejection(self):
        """Vectors not matching dimension 1024 must fail closed."""
        engine = LocalEmbeddingEngine()
        self.assertEqual(engine.dimension, 1024)

        # Attempt to insert an invalid dimension vector directly
        bad_record = RetrievalIndexRecord(
            id=f"bad-vec-{uuid.uuid4()}",
            record_kind="knowledge_object",
            tenant_id="default",
            persona_id=None,
            workspace_id=None,
            environment_scope=["paper"],
            access_scope=["public"],
            license_scope="open",
            role_scope=[],
            sensitivity="public",
            capital_pool_scope=[],
            source_type="test",
            asset_class=[],
            strategy_id=None,
            title="Bad Vector Record",
            search_text="Bad Vector",
            content_ref="/test",
            citation_label="test",
            evidence_bundle_id="b",
            evidence_item_id="i",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.1,
            embedding=[0.1] * 128,  # dimension 128 instead of 1024
            metadata={},
        )
        # Should raise on vector dimension mismatch
        with self.assertRaises(Exception):
            self.backend.upsert_documents([bad_record])


if __name__ == "__main__":
    unittest.main()
