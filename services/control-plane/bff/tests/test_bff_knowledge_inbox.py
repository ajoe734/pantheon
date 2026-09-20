from __future__ import annotations

import tempfile
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.knowledge import create_knowledge_router
from services.control_plane.bff.ports.read_surface_ports import create_in_memory_read_surface_ports


OPERATOR_HEADERS = {"Authorization": "Bearer op-knowledge:operator,reviewer"}


from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.auth import policy as auth_policy


def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: str = "2026-06-15T08:00:00Z",
    source: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    if source == "missing" or has_data is False:
        return {
            "status": "unavailable",
            "source": source or "missing",
            "snapshot_at": snapshot_at,
            "message": missing_message or f"{dataset} has no readable records",
        }
    return {
        "status": "ok",
        "source": source or "service_store",
        "snapshot_at": snapshot_at,
    }


def _create_app(store_getter: Callable[[], Any]) -> FastAPI:
    app = build_bff_app()
    router = create_knowledge_router(
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        read_store_getter=store_getter,
        utc_now=lambda: "2026-06-15T08:10:00Z",
        dataset_surface_status=_dataset_surface_status,
    )
    app.include_router(router)
    return app


def _knowledge_store(td: str, *, empty: bool = False):
    store = create_in_memory_read_surface_ports()
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
        store = _knowledge_store(td)
        app = _create_app(lambda: store)
        client = TestClient(app)

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


def test_bff_knowledge_inbox_empty_store_returns_degraded_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = _knowledge_store(td, empty=True)
        app = _create_app(lambda: store)
        client = TestClient(app)

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


def test_bff_knowledge_inbox_auth_and_openapi_contract() -> None:
    app = _create_app(lambda: None)
    client = TestClient(app, raise_server_exceptions=False)

    anonymous = client.get("/bff/knowledge")
    assert anonymous.status_code == 401, anonymous.text

    app.openapi_schema = None
    schema = app.openapi()
    assert "/bff/knowledge" in schema["paths"]
    assert "get" in schema["paths"]["/bff/knowledge"]
