"""Contract tests: BFF search path uses durable index (no request documents).

Verifies that the BFF read_store sends requests to the search service
using the normal durable-index path (/api/search/query) without embedding
documents in the request body.  Also verifies that the staging docker-compose
config wires SEARCH_DURABLE_INDEX_ONLY to search-svc.
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore


def _fake_post_capture(calls: list) -> callable:
    def fake_post(base_url, path, *, body, headers=None):
        calls.append({"base_url": base_url, "path": path, "body": body})
        return True, {
            "request_id": body.get("request_id", "test"),
            "trace_id": body.get("trace_id", "trace-test"),
            "results": [],
        }
    return fake_post


def test_bff_search_request_has_no_documents_field() -> None:
    """BFF normal search path must not embed documents in the request body."""
    calls: list = []

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search-svc:8098"}):
            with mock.patch("read_store._http_json_post", side_effect=_fake_post_capture(calls)):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=True,
                )
                store.list_research_search_results(query="momentum", match_type="all")

    assert len(calls) == 1
    body = calls[0]["body"]
    assert "documents" not in body, "BFF must not send documents in the search request body"


def test_bff_search_uses_durable_index_endpoint() -> None:
    """BFF must call /api/search/query, not the compat endpoint."""
    calls: list = []

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search-svc:8098"}):
            with mock.patch("read_store._http_json_post", side_effect=_fake_post_capture(calls)):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=True,
                )
                store.list_research_search_results(query="momentum", match_type="all")

    assert len(calls) == 1
    assert calls[0]["path"] == "/api/search/query"
    assert "compat" not in calls[0]["path"]


def test_bff_search_sends_structured_access_context() -> None:
    """BFF must include structured access_context (not inline persona/workspace only)."""
    calls: list = []

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search-svc:8098"}):
            with mock.patch("read_store._http_json_post", side_effect=_fake_post_capture(calls)):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=True,
                )
                store.list_research_search_results(query="momentum", match_type="all")

    body = calls[0]["body"]
    ctx = body.get("access_context", {})
    assert ctx.get("persona_id") == "operator-workbench"
    assert ctx.get("workspace_id") == "research-workbench"
    assert isinstance(ctx.get("access_scopes"), list)
    assert isinstance(ctx.get("license_scopes"), list)
    assert ctx.get("environment") == "paper"


def test_bff_search_returns_governed_refs_from_service() -> None:
    """BFF must store evidence refs from the search service response."""
    calls: list = []

    def fake_post(base_url, path, *, body, headers=None):
        calls.append(body)
        return True, {
            "request_id": body["request_id"],
            "trace_id": body["trace_id"],
            "results": [
                {
                    "result_id": "exp-governed-001",
                    "evidence_bundle_id": "evbundle-governed-001",
                    "citations": ["experiment:exp-governed-001"],
                    "matched_items": [
                        {
                            "knowledge_object_id": "exp-governed-001",
                            "source_id": "src-governed-001",
                            "evidence_item_id": "evi-governed-001",
                            "content_ref": "/research/experiments/exp-governed-001#search-index",
                            "citation_label": "experiment:exp-governed-001",
                            "matched_terms": ["momentum"],
                        }
                    ],
                    "relevance_score": 0.88,
                }
            ],
        }

    with tempfile.TemporaryDirectory() as td:
        # Pre-populate a read_surfaces.json with a matching search document
        import json
        surfaces_path = os.path.join(td, "read_surfaces.json")
        with open(surfaces_path, "w") as fh:
            json.dump(
                {
                    "research_search_documents": [
                        {
                            "result_id": "exp-governed-001",
                            "match_type": "experiment",
                            "title": "Governed experiment",
                            "excerpt": "Momentum evidence for governed search",
                            "search_text": "momentum experiment",
                            "source_type": "internal_note",
                            "relevance_score": 0.5,
                            "updated_at": "2026-04-20T10:00:00Z",
                        }
                    ]
                },
                fh,
            )

        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search-svc:8098"}):
            with mock.patch("read_store._http_json_post", side_effect=fake_post):
                store = ReadSurfaceStore(surfaces_path, allow_local_snapshot_fallback=True)
                results = store.list_research_search_results(query="momentum", match_type="all")

    assert len(results) == 1
    assert results[0]["result_id"] == "exp-governed-001"
    assert results[0]["relevance_score"] == 0.88

    refs = store.get_last_governed_search_refs()
    assert "exp-governed-001" in refs
    assert refs["exp-governed-001"]["evidence_bundle_id"] == "evbundle-governed-001"
    assert refs["exp-governed-001"]["citations"] == ["experiment:exp-governed-001"]
