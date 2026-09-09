"""Unit and integration tests for QdrantRetrievalBackend."""

from __future__ import annotations

import os
import unittest
import uuid

from services.search.filters import SearchAccessContext, SearchFilters
from services.search.pg_retrieval import RetrievalIndexRecord
from services.search.qdrant_backend import QdrantRetrievalBackend

QDRANT_TEST_URL = os.getenv("PANTHEON_SEARCH_QDRANT_URL", "http://localhost:26333")


class TestQdrantRetrievalBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.backend = QdrantRetrievalBackend(url=QDRANT_TEST_URL)
            health = cls.backend.check_health()
            cls.has_qdrant = health.get("status") == "ok"
        except Exception:
            cls.has_qdrant = False

    def setUp(self):
        if not self.has_qdrant:
            self.skipTest("Qdrant test container not available at localhost:26333")

    def test_health_check(self):
        health = self.backend.check_health()
        self.assertEqual(health.get("status"), "ok")
        self.assertEqual(health.get("backend"), "qdrant")
        self.assertEqual(health.get("vector_dimension"), 1024)

    def test_upsert_and_vector_retrieval(self):
        doc_id = f"qdrant-doc-{uuid.uuid4()}"
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
            source_type="research_paper",
            asset_class=["crypto"],
            strategy_id="strat-qdrant-1",
            title="Crypto Funding Rate Arbitrage",
            search_text="Perpetual futures funding rate arbitrage across decentralized and centralized venues.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"qdrant:{doc_id}",
            evidence_bundle_id="bundle-q1",
            evidence_item_id="item-q1",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.88,
            metadata={"strategy_family": "funding_arb"},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["open"],
        )
        hits = self.backend.search(
            query="perpetual futures funding rate",
            context=context,
            top_k=20,
        )
        self.assertTrue(any(h.id == doc_id for h in hits))

    def test_pre_retrieval_filtering_acl(self):
        doc_id = f"qdrant-sec-{uuid.uuid4()}"
        record = RetrievalIndexRecord(
            id=doc_id,
            record_kind="knowledge_object",
            tenant_id="tenant-sec",
            persona_id="persona-restricted",
            workspace_id="ws-restricted",
            environment_scope=["live"],
            access_scope=["restricted"],
            license_scope="restricted",
            role_scope=["admin"],
            sensitivity="confidential",
            capital_pool_scope=["pool-alpha"],
            source_type="internal_note",
            asset_class=["fx"],
            strategy_id="strat-fx-conf",
            title="Confidential Currency Hedging Protocol",
            search_text="Dynamic cross-currency basis swap execution algorithms.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"qsec:{doc_id}",
            evidence_bundle_id="bundle-qs",
            evidence_item_id="item-qs",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.95,
            metadata={},
        )
        self.backend.index_documents([record])

        # Paper public context must NOT see this restricted document
        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["open"],
        )
        hits = self.backend.search(
            query="cross-currency basis swap",
            context=context,
            top_k=10,
        )
        self.assertFalse(any(h.id == doc_id for h in hits))

    def test_deletion(self):
        doc_id = f"qdrant-del-{uuid.uuid4()}"
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
            source_type="research_paper",
            asset_class=["equity"],
            strategy_id="strat-del-q",
            title="To Be Deleted Qdrant Entry",
            search_text="Entry to be removed from Qdrant index.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"qdel:{doc_id}",
            evidence_bundle_id="bundle-qd",
            evidence_item_id="item-qd",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.5,
            metadata={},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(environment="paper", access_scopes=["public"], license_scopes=["open"])
        hits = self.backend.search(query="To Be Deleted Qdrant Entry", context=context, top_k=5)
        self.assertTrue(any(h.id == doc_id for h in hits))

        self.backend.delete_document(doc_id, hard_delete=True)
        hits_after = self.backend.search(query="To Be Deleted Qdrant Entry", context=context, top_k=5)
        self.assertFalse(any(h.id == doc_id for h in hits_after))


if __name__ == "__main__":
    unittest.main()
