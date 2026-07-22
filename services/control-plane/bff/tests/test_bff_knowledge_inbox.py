from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer op-knowledge:operator,reviewer"}


def _knowledge_store(td: str, *, empty: bool = False) -> ReadSurfaceStore:
    store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
    if empty:
        store.list_research_notes = lambda: []
        store.list_evidence_refs = lambda: []
        store.list_insight_cards = lambda: []
        store.list_strategy_specs = lambda **kwargs: []
        store.list_institutional_memory_entries = lambda: []
        store.dataset_source = lambda dataset, **kwargs: "missing"
        return store

    store.list_research_notes = lambda: [
        {
            "note_id": "note-knowledge-001",
            "title": "Knowledge note",
            "body": "Research note body.",
            "updated_at": "2026-06-15T08:10:00Z",
            "created_at": "2026-06-15T08:00:00Z",
            "route_href": "/knowledge/notes/note-knowledge-001",
        }
    ]
    store.list_evidence_refs = lambda: [
        {
            "ref_id": "evref-knowledge-001",
            "source_document": {"title": "Evidence packet", "captured_at": "2026-06-15T08:09:00Z"},
            "linked_object_summary": {"display_label": "Strategy evidence"},
            "credibility": {"tier": "primary", "verified": True},
            "route_href": "/knowledge/evidence/evref-knowledge-001",
        }
    ]
    store.list_insight_cards = lambda: [
        {
            "insight_id": "ins-knowledge-001",
            "summary": "Momentum regime insight",
            "status": "active",
            "confidence": {"score": 0.82, "label": "high"},
            "aggregated_at": "2026-06-15T08:08:00Z",
            "route_href": "/knowledge/insights/ins-knowledge-001",
        }
    ]
    store.list_strategy_specs = lambda **kwargs: [
        {
            "strategy_id": "strat-knowledge-001",
            "title": "Knowledge Strategy",
            "lifecycle_state": "approved",
            "hypothesis_excerpt": "Strategy hypothesis excerpt.",
            "last_modified_at": "2026-06-15T08:07:00Z",
            "route_href": "/knowledge/strategy-specs/strat-knowledge-001",
        }
    ]
    store.list_institutional_memory_entries = lambda: [
        {
            "entry_id": "mem-knowledge-001",
            "knowledge_type": "regime_pattern",
            "content": {"headline": "Volatility memory", "summary": "Reusable memory summary."},
            "written_at": "2026-06-15T08:06:00Z",
            "route_href": "/knowledge/memory/mem-knowledge-001",
        }
    ]
    store.dataset_source = lambda dataset, **kwargs: {
        "research_notes": "service_store",
        "evidence_refs": "service_store",
        "insight_cards": "service_store",
        "strategy_specs": "service_store",
        "institutional_memory_entries": "service_store",
    }.get(dataset, "missing")
    return store


def test_bff_knowledge_inbox_returns_canonical_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = _knowledge_store(td)
            client = TestClient(bff_main.app)

            response = client.get("/bff/knowledge", headers=OPERATOR_HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"] == body["items"]
            assert body["page_info"]["total"] == 5
            assert body["page_info"]["page_size"] == 20
            assert body["page_info"]["returned"] == 5
            assert body["page_info"]["next_page_token"] is None
            assert {item["inboxType"] for item in body["items"]} == {
                "research_note",
                "evidence_ref",
                "insight",
                "strategy_spec",
                "memory_entry",
            }
            assert body["meta"]["surfaces"]["knowledge_inbox"] == {
                "status": "ok",
                "source": "bff_composed",
            }
            assert body["meta"]["surfaces"]["knowledge_inbox_notes"]["source"] == "service_store"
            assert body["meta"]["composition"]["itemCounts"]["research_note"] == 1
            assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original_store


def test_bff_knowledge_inbox_empty_store_returns_degraded_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = _knowledge_store(td, empty=True)
            client = TestClient(bff_main.app)

            response = client.get("/bff/knowledge", headers=OPERATOR_HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"] == []
            assert body["items"] == []
            assert body["page_info"]["total"] == 0
            assert body["page_info"]["returned"] == 0
            assert body["meta"]["surfaces"]["knowledge_inbox"]["status"] == "unavailable"
            assert body["meta"]["surfaces"]["knowledge_inbox"]["source"] == "missing"
            assert body["meta"]["surfaces"]["knowledge_inbox_notes"]["status"] == "unavailable"
            assert body["meta"]["surfaces"]["knowledge_inbox_notes"]["source"] == "missing"
        finally:
            bff_main.read_store = original_store


def test_bff_knowledge_inbox_auth_and_openapi_contract() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    anonymous = client.get("/bff/knowledge")
    assert anonymous.status_code == 401, anonymous.text

    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()
    assert "/bff/knowledge" in schema["paths"]
    assert "get" in schema["paths"]["/bff/knowledge"]
