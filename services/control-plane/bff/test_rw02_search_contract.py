from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import DefaultResearchKnowledgeSourcePort


OPERATOR_AUTH = "Bearer test-operator:operator"

_SEARCH_DOCUMENTS = [
    {
        "result_id": "rt-20260419-007",
        "match_type": "ticket",
        "title": "Evaluate momentum factor decay in high-volatility regime",
        "excerpt": "Research ticket asks whether momentum factors lose predictive power during sustained volatility spikes.",
        "linked_ticket_id": "rt-20260419-007",
        "linked_ticket_status": "in_progress",
        "relevance_score": 0.98,
        "updated_at": "2026-04-19T20:15:00Z",
        "search_text": "momentum decay volatility regime ticket predictive power rebalancing windows",
        "links": {
            "result_detail": "/research/tickets/rt-20260419-007",
            "linked_ticket_detail": "/research/tickets/rt-20260419-007",
        },
    },
    {
        "result_id": "exp-20260419-012",
        "match_type": "experiment",
        "title": "Momentum decay replay on March volatility cluster",
        "excerpt": "Experiment replay compares signal half-life before and after the volatility cluster.",
        "linked_ticket_id": "rt-20260419-007",
        "linked_ticket_status": "in_progress",
        "relevance_score": 0.91,
        "updated_at": "2026-04-19T20:14:30Z",
        "search_text": "momentum decay experiment replay march volatility cluster signal half life",
        "links": {
            "result_detail": "/research/experiments/exp-20260419-012",
            "linked_ticket_detail": "/research/tickets/rt-20260419-007",
        },
    },
    {
        "result_id": "artifact-20260418-005",
        "match_type": "artifact",
        "title": "Momentum regime-break feature set v5",
        "excerpt": "Artifact includes volatility bucketing and a shorter half-life decay coefficient.",
        "linked_ticket_id": "rt-20260419-007",
        "linked_ticket_status": "in_progress",
        "relevance_score": 0.87,
        "updated_at": "2026-04-18T20:12:58Z",
        "search_text": "momentum artifact volatility regime bucketing shorter half life decay",
        "links": {
            "result_detail": "/research/artifacts/artifact-20260418-005",
            "linked_ticket_detail": "/research/tickets/rt-20260419-007",
        },
    },
    {
        "result_id": "rt-20260415-001",
        "match_type": "ticket",
        "title": "Validate signal quality on macro event windows",
        "excerpt": "Closed research ticket recorded weaker signal quality around scheduled macro events and tested whether exclusion windows were sufficient.",
        "linked_ticket_id": "rt-20260415-001",
        "linked_ticket_status": "closed",
        "relevance_score": 0.69,
        "updated_at": "2026-04-19T11:00:00Z",
        "search_text": "signal quality macro event windows exclusion windows scheduled macro events closed ticket",
        "links": {
            "result_detail": "/research/tickets/rt-20260415-001",
            "linked_ticket_detail": "/research/tickets/rt-20260415-001",
        },
    },
]

_SEARCH_INDEX = {
    "adapter_id": "rw02-search-index",
    "snapshot_at": "2026-04-19T20:14:30Z",
    "adapter_state": "fresh",
    "indexed_match_types": ["ticket", "experiment", "artifact"],
    "source_watermarks": {
        "tickets": "2026-04-19T20:14:10Z",
        "experiments": "2026-04-19T20:13:42Z",
        "artifacts": "2026-04-19T20:12:58Z",
    },
}


class _SearchPortDouble(DefaultResearchKnowledgeSourcePort):
    """Typed RW-02 double retaining the governed multi-term search contract."""

    def __init__(self, *, available: bool = True) -> None:
        super().__init__(search_documents_store=_SEARCH_DOCUMENTS if available else [])
        self._available = available

    def dataset_source(self, dataset: str, **_: object) -> str:
        if dataset in {"research_search_documents", "research_search_index"}:
            return "local_snapshot" if self._available else "missing"
        return super().dataset_source(dataset)

    def get_research_search_index(self) -> dict | None:
        return dict(_SEARCH_INDEX) if self._available else None

    def list_research_search_results(
        self,
        *,
        query: str,
        match_type: str = "all",
        status: str | None = None,
        date_range: str | None = None,
    ) -> list[dict]:
        del date_range
        terms = [term for term in query.lower().split() if term]
        matches: list[dict] = []
        governed: dict[str, dict] = {}
        for document in self._search_documents:
            document_type = str(document["match_type"])
            if match_type != "all" and document_type != match_type:
                continue
            if status and str(document.get("linked_ticket_status")) != status:
                continue
            haystack = " ".join(
                str(document.get(key) or "")
                for key in ("title", "excerpt", "search_text")
            ).lower()
            matched_terms = [term for term in terms if term in haystack]
            if len(matched_terms) != len(terms):
                continue
            result_id = str(document["result_id"])
            matches.append(
                {
                    "result_id": result_id,
                    "match_type": document_type,
                    "title": document["title"],
                    "excerpt": document["excerpt"],
                    "linked_ticket_id": document["linked_ticket_id"],
                    "relevance_score": document["relevance_score"],
                    "links": dict(document["links"]),
                }
            )
            governed[result_id] = {
                "evidence_bundle_id": f"evbundle-rw02-{result_id}",
                "citations": [f"{document_type}:{result_id}"],
                "matched_items": [
                    {
                        "knowledge_object_id": result_id,
                        "source_id": f"src-rw02-{result_id}",
                        "evidence_item_id": f"evi-rw02-{result_id}",
                        "content_ref": f"{document['links']['result_detail']}#search-index",
                        "citation_label": f"{document_type}:{result_id}",
                        "matched_terms": matched_terms,
                    }
                ],
            }
        matches.sort(key=lambda item: float(item["relevance_score"]), reverse=True)
        self._last_governed_search_refs = governed
        return matches


@contextmanager
def _seeded_client(*, allow_local_snapshot_fallback: bool):
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _SearchPortDouble(
            available=allow_local_snapshot_fallback,
        )
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
        first_store = _SearchPortDouble()
        first_results = first_store.list_research_search_results(
            query="momentum decay volatility",
            match_type="all",
        )
        first_refs = first_store.get_last_governed_search_refs()

        replayed_store = _SearchPortDouble()
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
        store = _SearchPortDouble()

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
        empty_payload = empty_query.json()
        assert empty_payload["error"]["code"] == "VALIDATION_FAILED"
        assert empty_payload["error"]["details"]["reason"] == "q is required and must be non-empty"

        bad_filter = client.get(
            "/api/v1/research/search?q=momentum&match_type=note",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert bad_filter.status_code == 400, bad_filter.text
        assert bad_filter.json()["error"]["code"] == "VALIDATION_FAILED"


def test_rw02_search_returns_contract_unavailable_when_index_adapter_missing() -> None:
    with _seeded_client(allow_local_snapshot_fallback=False) as client:
        response = client.get(
            "/api/v1/research/search?q=momentum",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 503, response.text
        payload = response.json()
        assert payload["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert payload["error"]["details"]["reason"] == "SEARCH_RESULTS_UNAVAILABLE"
        assert payload["surfaces"] == {"search_results": "unavailable"}
