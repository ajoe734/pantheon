from __future__ import annotations

from fastapi.testclient import TestClient

from services.search.main import create_app


def test_search_service_filters_before_ranking_and_replays_refs(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl"))
    body = {
        "request_id": "svc-search-001",
        "trace_id": "trace-svc-search-001",
        "query": "momentum volatility",
        "persona_id": "operator-workbench",
        "workspace_id": "research-workbench",
        "source_types": ["internal_note"],
        "documents": [
            {
                "result_id": "search-public",
                "match_type": "ticket",
                "title": "Public momentum note",
                "excerpt": "Momentum volatility evidence is available to research operators.",
                "content_ref": "/research/tickets/search-public",
                "relevance_score": 0.6,
            },
            {
                "result_id": "search-private",
                "match_type": "ticket",
                "title": "Private momentum note",
                "excerpt": "Momentum volatility evidence that must be filtered before ranking.",
                "content_ref": "/research/tickets/search-private",
                "access_scope": ["risk-committee"],
                "relevance_score": 0.99,
            },
        ],
        "access_context": {
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }

    response = client.post("/api/search/query", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert [item["result_id"] for item in payload["results"]] == ["search-public"]
    assert payload["rejected_items_count"] == 1
    assert payload["index_adapter"]["adapter_state"] == "fresh"
    assert payload["index_adapter"]["indexed_source_types"] == ["internal_note"]
    assert payload["index_snapshot"]["request_id"] == "svc-search-001"
    assert "answer_context" not in payload["index_snapshot"]["result_refs"][0]
    assert "raw_payload" not in payload["index_snapshot"]["result_refs"][0]

    replay = client.get("/api/search/snapshots/svc-search-001")
    assert replay.status_code == 200, replay.text
    assert replay.json()["snapshot"] == payload["index_snapshot"]


def test_search_service_requires_scoped_access_context(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl"))

    response = client.post(
        "/api/search/query",
        json={
            "request_id": "svc-search-missing-scope",
            "trace_id": "trace-svc-search-missing-scope",
            "query": "momentum",
            "documents": [],
            "access_context": {
                "environment": "paper",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        },
    )

    assert response.status_code == 400
    assert "persona_id and workspace_id" in response.json()["detail"]
