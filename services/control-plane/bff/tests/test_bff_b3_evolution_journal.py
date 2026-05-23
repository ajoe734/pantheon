"""
BFF-B3-005: contract tests for GET /bff/management/evolution-journal.

The route is a read-only Management aggregate. It composes evolution decisions,
postmortems, mutation review projections, rollback records, and freeze orders
into one journal envelope while preserving source-surface metadata.
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-evolution:operator,reviewer"}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    return TestClient(bff_main.app)


def test_evolution_journal_composes_required_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == body["items"]
            assert body["summary"]["decision_count"] >= 1
            assert body["summary"]["postmortem_count"] >= 1
            assert body["summary"]["mutation_review_count"] >= 1
            assert body["summary"]["rollback_count"] >= 1
            assert body["summary"]["freeze_order_count"] >= 1
            assert body["meta"]["surfaces"]["management_evolution_journal"]["source"] == "bff_composed"
            for surface in [
                "evolution_decisions",
                "postmortems",
                "mutation_review",
                "rollbacks",
                "freeze_orders",
            ]:
                assert surface in body["meta"]["surfaces"]

            by_type = {item["entry_type"]: item for item in body["items"]}
            assert by_type["evolution_decision"]["decision"]["id"]
            assert by_type["postmortem"]["postmortem"]["postmortem_id"]
            assert by_type["rollback"]["rollback"]["rollback_id"]
            assert by_type["freeze_order"]["freezeOrder"]["freeze_order_id"]
            mutation_review = next(
                item for item in body["items"]
                if item["entry_type"] == "mutation_review"
                and item["source_id"] == "evo-dec-88f3a2c1"
            )
            assert mutation_review["mutationReview"]["decision_id"] == "evo-dec-88f3a2c1"
        finally:
            bff_main.read_store = original_store


def test_evolution_journal_supports_filters_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get(
                "/bff/management/evolution-journal"
                "?source_type=mutation_review&status=reviewed&risk_level=medium&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] == 1
            assert len(body["items"]) == 1
            item = body["items"][0]
            assert item["entry_type"] == "mutation_review"
            assert item["source_id"] == "evo-dec-88f3a2c1"
            assert item["mutationReview"]["allowedActions"]["canApproveMutation"] is True
            assert body["summary"]["pending_review_count"] == 1
        finally:
            bff_main.read_store = original_store


def test_evolution_journal_requires_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal")

            assert resp.status_code == 401, resp.text
            assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"
        finally:
            bff_main.read_store = original_store
