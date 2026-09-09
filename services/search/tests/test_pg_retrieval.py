"""Unit and integration tests for PostgresRetrievalBackend.

Tests table schema setup, upsert, soft/hard deletion, pre-retrieval filtering,
CJK full-text search, semantic vector retrieval, and hybrid RRF retrieval.
"""

from __future__ import annotations

import os
import unittest
import uuid

from services.search.filters import SearchAccessContext, SearchFilters
from services.search.pg_retrieval import (
    PostgresRetrievalBackend,
    RetrievalIndexRecord,
)

POSTGRES_TEST_DSN = os.getenv(
    "PANTHEON_SEARCH_POSTGRES_DSN",
    "postgresql://postgres:postgres@localhost:25432/pantheon_search",
)


class TestPostgresRetrievalBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.backend = PostgresRetrievalBackend(dsn=POSTGRES_TEST_DSN)
            health = cls.backend.check_health()
            cls.has_postgres = health.get("status") == "ok"
        except Exception:
            cls.has_postgres = False

    def setUp(self):
        if not self.has_postgres:
            self.skipTest("PostgreSQL test container not available at localhost:25432")

    def test_health_check(self):
        health = self.backend.check_health()
        self.assertEqual(health.get("status"), "ok")
        self.assertEqual(health.get("backend"), "postgres_pgvector")
        self.assertEqual(health.get("vector_dimension"), 1024)

    def test_upsert_and_keyword_retrieval(self):
        doc_id = f"test-doc-{uuid.uuid4()}"
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
            strategy_id="strat-alpha",
            title="Statistical Arbitrage in US Equities",
            search_text="Pairs trading on S&P 500 equities using mean reversion and cointegration.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"paper:{doc_id}",
            evidence_bundle_id="bundle-1",
            evidence_item_id="item-1",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.85,
            metadata={"strategy_family": "mean_reversion"},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["open"],
        )
        filters = SearchFilters(source_types=["research_paper"], strategy_id="strat-alpha")

        hits = self.backend.search(
            query="cointegration pairs trading",
            mode="keyword",
            access_context=context,
            filters=filters,
            limit=5,
        )
        self.assertTrue(len(hits) >= 1)
        found = [h for h in hits if h.id == doc_id]
        self.assertEqual(len(found), 1)
        self.assertIn("cointegration", found[0].matched_terms)

    def test_cjk_fulltext_retrieval(self):
        doc_id = f"test-cjk-{uuid.uuid4()}"
        record = RetrievalIndexRecord(
            id=doc_id,
            record_kind="knowledge_object",
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
            title="台股期貨高頻停損檢討報告",
            search_text="因瞬時買賣單失衡導致強制平倉，建議增設流動性門檻與動態滑價保護。",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"cjk:{doc_id}",
            evidence_bundle_id="bundle-cjk",
            evidence_item_id="item-cjk",
            event_time="2026-08-15T00:00:00Z",
            available_time="2026-08-15T00:00:00Z",
            relevance_score=0.9,
            metadata={},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["internal"],
        )
        hits = self.backend.search(
            query="台股 期貨 停損",
            mode="full_text",
            access_context=context,
            limit=20,
        )
        self.assertTrue(any(h.id == doc_id for h in hits))

    def test_hybrid_rrf_retrieval(self):
        doc_id = f"test-hyb-{uuid.uuid4()}"
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
            strategy_id="strat-crypto-mm",
            title="High-Frequency Market Making on Order Book Extremes",
            search_text="Inventory risk management and queue priority modeling in limit order books.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"hyb:{doc_id}",
            evidence_bundle_id="bundle-hyb",
            evidence_item_id="item-hyb",
            event_time="2026-08-20T00:00:00Z",
            available_time="2026-08-20T00:00:00Z",
            relevance_score=0.92,
            metadata={},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(
            environment="paper",
            access_scopes=["public"],
            license_scopes=["open"],
        )
        hits = self.backend.search(
            query="order book market making inventory risk",
            mode="hybrid",
            access_context=context,
            limit=5,
        )
        self.assertTrue(any(h.id == doc_id for h in hits))
        match = next(h for h in hits if h.id == doc_id)
        self.assertIn("rrf_score", match.component_scores)

    def test_pre_retrieval_filtering_acls(self):
        doc_id = f"test-sec-{uuid.uuid4()}"
        record = RetrievalIndexRecord(
            id=doc_id,
            record_kind="knowledge_object",
            tenant_id="tenant-a",
            persona_id="persona-secret",
            workspace_id="ws-private",
            environment_scope=["live"],
            access_scope=["restricted"],
            license_scope="restricted",
            role_scope=["admin"],
            sensitivity="confidential",
            capital_pool_scope=["pool-1"],
            source_type="internal_strategy",
            asset_class=["fx"],
            strategy_id="strat-fx-secret",
            title="Classified FX Momentum Strategy",
            search_text="Proprietary latency arbitrage and forward curve momentum.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"sec:{doc_id}",
            evidence_bundle_id="bundle-sec",
            evidence_item_id="item-sec",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.99,
            metadata={},
        )
        self.backend.index_documents([record])

        # Wrong access scope and wrong environment
        unauth_context = SearchAccessContext(
            persona_id="persona-other",
            workspace_id="ws-public",
            environment="paper",
            access_scopes=["public"],
            license_scopes=["open"],
        )
        hits = self.backend.search(
            query="Classified FX Momentum",
            mode="keyword",
            access_context=unauth_context,
            limit=10,
        )
        self.assertFalse(any(h.id == doc_id for h in hits))

    def test_soft_and_hard_deletion(self):
        doc_id = f"test-del-{uuid.uuid4()}"
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
            strategy_id="strat-del",
            title="Temporary Research Document",
            search_text="This document will be deleted to test tombstone mechanics.",
            content_ref=f"/docs/{doc_id}",
            citation_label=f"del:{doc_id}",
            evidence_bundle_id="bundle-del",
            evidence_item_id="item-del",
            event_time="2026-08-01T00:00:00Z",
            available_time="2026-08-01T00:00:00Z",
            relevance_score=0.5,
            metadata={},
        )
        self.backend.index_documents([record])

        context = SearchAccessContext(environment="paper", access_scopes=["public"], license_scopes=["open"])
        hits = self.backend.search(query="Temporary Research Document", mode="keyword", access_context=context, limit=5)
        self.assertTrue(any(h.id == doc_id for h in hits))

        # Soft delete
        self.backend.delete_document(doc_id, hard=False)
        hits_after_soft = self.backend.search(query="Temporary Research Document", mode="keyword", access_context=context, limit=5)
        self.assertFalse(any(h.id == doc_id for h in hits_after_soft))

        # Hard delete
        self.backend.delete_document(doc_id, hard=True)


if __name__ == "__main__":
    unittest.main()
