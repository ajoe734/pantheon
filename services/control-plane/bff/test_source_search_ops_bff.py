"""Source/search operations projections use the typed research port."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import DefaultResearchKnowledgeSourcePort


def test_source_ops_snapshot_projects_service_owned_records() -> None:
    def fake_get(_base_url, path):
        payloads = {
            "/api/source-ingest/registry": {"connectors": [{"connector_id": "source-1"}]},
            "/api/source-ingest/jobs": {"runs": [{"run_id": "crawl-1"}]},
            "/api/source-ingest/dlq": {"entries": [{"entry_id": "dlq-1"}]},
            "/api/source-ingest/frontier": {"frontier": [{"url": "https://example.test"}]},
            "/api/source-ingest/audit": {"actions": [{"action_id": "audit-1"}]},
        }
        return True, payloads[path]

    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://source:8097", http_get_fn=fake_get
    )
    snapshot = port.get_source_ops_snapshot(crawl_run_limit=10)

    assert snapshot["source"] == "service_client"
    assert snapshot["summary"]["connector_count"] == 1
    assert snapshot["summary"]["dlq_count"] == 1


def test_search_ops_snapshot_reports_missing_without_a_typed_owner() -> None:
    snapshot = DefaultResearchKnowledgeSourcePort().get_search_ops_snapshot()
    assert snapshot["source"] == "missing"
    assert snapshot["summary"]["freshness_status"] == "unknown"
