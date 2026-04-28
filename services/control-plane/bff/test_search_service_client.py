from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore


def test_rw02_search_uses_explicit_search_service_url_for_normal_path() -> None:
    calls = []

    def fake_post(base_url, path, *, body, headers=None):
        calls.append((base_url, path, body, headers))
        assert base_url == "http://search-svc:8098"
        assert path == "/api/search/query"
        assert body["access_context"] == {
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        }
        assert body["documents"]
        return True, {
            "request_id": body["request_id"],
            "trace_id": body["trace_id"],
            "results": [
                {
                    "result_id": "exp-20260419-012",
                    "evidence_bundle_id": "evbundle-rw02-exp-20260419-012",
                    "citations": ["experiment:exp-20260419-012"],
                    "matched_items": [
                        {
                            "knowledge_object_id": "exp-20260419-012",
                            "source_id": "src-rw02-exp-20260419-012",
                            "evidence_item_id": "evi-rw02-exp-20260419-012",
                            "content_ref": "/research/experiments/exp-20260419-012#search-index",
                            "citation_label": "experiment:exp-20260419-012",
                            "matched_terms": ["momentum"],
                        }
                    ],
                    "relevance_score": 0.91,
                }
            ],
        }

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search-svc:8098"}):
            with mock.patch("read_store._http_json_post", side_effect=fake_post):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=True,
                )
                results = store.list_research_search_results(query="momentum", match_type="all")

    assert len(calls) == 1
    assert [item["result_id"] for item in results] == ["exp-20260419-012"]
    assert results[0]["relevance_score"] == 0.91
    assert store.get_last_governed_search_refs() == {
        "exp-20260419-012": {
            "evidence_bundle_id": "evbundle-rw02-exp-20260419-012",
            "citations": ["experiment:exp-20260419-012"],
            "matched_items": [
                {
                    "knowledge_object_id": "exp-20260419-012",
                    "source_id": "src-rw02-exp-20260419-012",
                    "evidence_item_id": "evi-rw02-exp-20260419-012",
                    "content_ref": "/research/experiments/exp-20260419-012#search-index",
                    "citation_label": "experiment:exp-20260419-012",
                    "matched_terms": ["momentum"],
                }
            ],
        }
    }
