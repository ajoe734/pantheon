from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import DefaultResearchKnowledgeSourcePort, create_in_memory_read_surface_ports


OPERATOR_AUTH = "Bearer test-operator:operator"


@contextmanager
def _seeded_client(*, allow_local_snapshot_fallback: bool):
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = create_in_memory_read_surface_ports()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_rw02_search_contract_returns_ranked_projection_and_index_adapter_meta() -> None:
    with _seeded_client(allow_local_snapshot_fallback=True) as client:
        response = client.get(
            "/api/v1/research/search?q=momentum%20decay%20volatility&match_type=all&page_size=2",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"] == {
            "next_page_token": "2",
            "total": 3,
        }
        assert [item["result_id"] for item in payload["data"]] == [
            "rt-20260419-007",
            "exp-20260419-012",
        ]
        assert payload["data"][0]["links"] == {
            "result_detail": "/research/tickets/rt-20260419-007",
            "linked_ticket_detail": "/research/tickets/rt-20260419-007",
        }
        assert payload["data"][1]["match_type"] == "experiment"
        assert payload["meta"]["surfaces"]["search_results"] == "degraded"
        assert payload["meta"]["index_adapter"] == {
            "snapshot_at": "2026-04-19T20:14:30Z",
            "adapter_state": "degraded",
            "indexed_match_types": ["ticket", "experiment", "artifact"],
            "source_watermarks": {
                "tickets": "2026-04-19T20:14:10Z",
                "experiments": "2026-04-19T20:13:42Z",
                "artifacts": "2026-04-19T20:12:58Z",
            },
        }
        assert payload["meta"]["governed_evidence"]["rt-20260419-007"] == {
            "evidence_bundle_id": "evbundle-rw02-rt-20260419-007",
            "citations": ["ticket:rt-20260419-007"],
            "matched_items": [
                {
                    "knowledge_object_id": "rt-20260419-007",
                    "source_id": "src-rw02-rt-20260419-007",
                    "evidence_item_id": "evi-rw02-rt-20260419-007",
                    "content_ref": "/research/tickets/rt-20260419-007#search-index",
                    "citation_label": "ticket:rt-20260419-007",
                    "matched_terms": ["momentum", "decay", "volatility"],
                }
            ],
        }


def test_rw02_governed_evidence_refs_remain_stable_after_durable_replay() -> None:
    with tempfile.TemporaryDirectory() as td:
        storage_path = os.path.join(td, "read_surfaces.json")
        first_store = DefaultResearchKnowledgeSourcePort()
        first_results = first_store.list_research_search_results(
            query="momentum decay volatility",
            match_type="all",
        )
        first_refs = first_store.get_last_governed_search_refs()

        replayed_store = DefaultResearchKnowledgeSourcePort()
        replayed_results = replayed_store.list_research_search_results(
            query="momentum decay volatility",
            match_type="all",
        )
        replayed_refs = replayed_store.get_last_governed_search_refs()

        assert [item["result_id"] for item in replayed_results] == [item["result_id"] for item in first_results]
        assert replayed_refs == first_refs
        assert replayed_refs["rt-20260419-007"]["evidence_bundle_id"] == "evbundle-rw02-rt-20260419-007"


def test_rw02_durable_replay_does_not_pollute_narrow_match_type_search() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = DefaultResearchKnowledgeSourcePort()

        store.list_research_search_results(query="momentum", match_type="all")
        artifact_results = store.list_research_search_results(query="momentum", match_type="artifact")

        assert [(item["result_id"], item["match_type"]) for item in artifact_results] == [
            ("artifact-20260418-005", "artifact"),
        ]
        assert store.get_last_governed_search_refs() == {
            "artifact-20260418-005": {
                "evidence_bundle_id": "evbundle-rw02-artifact-20260418-005",
                "citations": ["artifact:artifact-20260418-005"],
                "matched_items": [
                    {
                        "knowledge_object_id": "artifact-20260418-005",
                        "source_id": "src-rw02-artifact-20260418-005",
                        "evidence_item_id": "evi-rw02-artifact-20260418-005",
                        "content_ref": "/research/artifacts/artifact-20260418-005#search-index",
                        "citation_label": "artifact:artifact-20260418-005",
                        "matched_terms": ["momentum"],
                    }
                ],
            }
        }


def test_rw02_search_contract_applies_backend_owned_filters() -> None:
    with _seeded_client(allow_local_snapshot_fallback=True) as client:
        response = client.get(
            "/api/v1/research/search?q=macro%20event&match_type=ticket&status=closed&date_range=30d",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"]["total"] == 1
        assert payload["data"] == [
            {
                "result_id": "rt-20260415-001",
                "match_type": "ticket",
                "title": "Validate signal quality on macro event windows",
                "excerpt": (
                    "Closed research ticket recorded weaker signal quality around scheduled macro events "
                    "and tested whether exclusion windows were sufficient."
                ),
                "linked_ticket_id": "rt-20260415-001",
                "relevance_score": 0.69,
                "links": {
                    "result_detail": "/research/tickets/rt-20260415-001",
                    "linked_ticket_detail": "/research/tickets/rt-20260415-001",
                },
            }
        ]


def test_rw02_search_rejects_invalid_query_params_with_contract_error_shape() -> None:
    with _seeded_client(allow_local_snapshot_fallback=True) as client:
        empty_query = client.get(
            "/api/v1/research/search?q=",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert empty_query.status_code == 400, empty_query.text
        assert empty_query.json() == {
            "error": "invalid_search_query",
            "detail": "q is required and must be non-empty",
        }

        bad_filter = client.get(
            "/api/v1/research/search?q=momentum&match_type=note",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert bad_filter.status_code == 400, bad_filter.text
        assert bad_filter.json()["error"] == "invalid_search_query"


def test_rw02_search_returns_contract_unavailable_when_index_adapter_missing() -> None:
    with _seeded_client(allow_local_snapshot_fallback=False) as client:
        response = client.get(
            "/api/v1/research/search?q=momentum",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 503, response.text
        assert response.json() == {
            "error": "search_unavailable",
            "meta": {
                "surfaces": {
                    "search_results": "unavailable",
                }
            },
        }
