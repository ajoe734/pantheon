"""Search retrieval uses the narrow research port and deployment cutoff remains explicit."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from ports import DefaultResearchKnowledgeSourcePort


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_bff_search_filters_explicit_in_memory_documents() -> None:
    port = DefaultResearchKnowledgeSourcePort(
        search_documents_store=[
            {
                "result_id": "exp-governed-001",
                "match_type": "experiment",
                "title": "Governed experiment",
                "excerpt": "Momentum evidence",
                "search_text": "momentum experiment",
                "linked_ticket_id": "rt-governed-001",
                "updated_at": "2026-04-20T10:00:00Z",
            },
            {
                "result_id": "artifact-other-001",
                "match_type": "artifact",
                "title": "Other",
                "excerpt": "Unrelated record",
                "search_text": "unrelated",
                "updated_at": "2026-04-20T10:00:00Z",
            },
        ]
    )

    results = port.list_research_search_results(query="momentum", match_type="experiment")

    assert [item["result_id"] for item in results] == ["exp-governed-001"]
    assert all("documents" not in item for item in results)


def test_deployment_wires_durable_index_only_cutoff_for_search_service() -> None:
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    start = compose_text.index("  search-svc:")
    end = compose_text.index("\n  training-session-svc:", start)
    assert "SEARCH_DURABLE_INDEX_ONLY: ${SEARCH_DURABLE_INDEX_ONLY:-false}" in compose_text[start:end]
    assert "SEARCH_DURABLE_INDEX_ONLY=true" in (REPO_ROOT / "env" / "prod-control.env.example").read_text(encoding="utf-8")
