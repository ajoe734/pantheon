from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import DefaultResearchKnowledgeSourcePort


def test_rw02_search_projects_governed_result_through_research_port() -> None:
    port = DefaultResearchKnowledgeSourcePort(
        search_documents_store=[
            {
                "result_id": "exp-20260419-012",
                "match_type": "experiment",
                "title": "Momentum replay",
                "excerpt": "Governed experiment evidence",
                "search_text": "momentum experiment",
                "linked_ticket_id": "rt-20260419-007",
                "updated_at": "2026-04-20T10:00:00Z",
            }
        ]
    )

    results = port.list_research_search_results(query="momentum", match_type="all")

    assert [item["result_id"] for item in results] == ["exp-20260419-012"]
    assert results[0]["links"]["result_detail"] == "/research/experiments/exp-20260419-012"
