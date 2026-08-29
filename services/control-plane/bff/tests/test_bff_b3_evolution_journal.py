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
from ports import create_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-evolution:operator,reviewer"}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = create_read_surface_ports()
    return TestClient(bff_main.app)


def test_evolution_journal_composes_required_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body.keys()) == {"data", "page_info", "meta"}
            assert set(body["data"].keys()) == {"id", "items", "summary"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert summary["decision_count"] >= 1
            assert summary["postmortem_count"] >= 1
            assert summary["mutation_review_count"] >= 1
            assert summary["rollback_count"] >= 1
            assert summary["freeze_order_count"] >= 1
            assert "byType" not in summary
            assert "byStatus" not in summary
            assert "byRiskLevel" not in summary
            assert body["meta"]["surfaces"]["management_evolution_journal"]["source"] == "bff_composed"
            for surface in [
                "evolution_decisions",
                "postmortems",
                "mutation_review",
                "rollbacks",
                "freeze_orders",
            ]:
                assert surface in body["meta"]["surfaces"]

            by_type = {item["entry_type"]: item for item in items}
            assert by_type["evolution_decision"]["decision"]["id"]
            assert by_type["postmortem"]["postmortem"]["postmortem_id"]
            assert by_type["rollback"]["rollback"]["rollback_id"]
            assert by_type["freeze_order"]["freeze_order"]["freeze_order_id"]
            assert "entryType" not in by_type["freeze_order"]
            assert "freezeOrder" not in by_type["freeze_order"]
            mutation_review = next(
                item for item in items
                if item["entry_type"] == "mutation_review"
                and item["source_id"] == "evo-dec-88f3a2c1"
            )
            assert mutation_review["mutation_review"]["decision_id"] == "evo-dec-88f3a2c1"
            assert "mutationReview" not in mutation_review
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
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert len(items) == 1
            item = items[0]
            assert item["entry_type"] == "mutation_review"
            assert item["source_id"] == "evo-dec-88f3a2c1"
            assert item["mutation_review"]["allowedActions"]["canApproveMutation"] is True
            assert summary["pending_review_count"] == 1
        finally:
            bff_main.read_store = original_store


def test_evolution_journal_server_side_filtering_and_origin() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            # 1. Test filtering by persona: resolved through explicit lineage
            # (persona-alpha declares runtime_id=runtime-042, which owns
            # incident inc-20260410-001, whose incident_ref is carried by
            # evo-dec-001), never a free-text substring match against an
            # unrelated artifact id.
            resp = client.get(
                "/bff/management/evolution-journal?persona=persona-alpha",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            assert len(items) > 0
            source_ids = {item["source_id"] for item in items}
            assert "evo-dec-001" in source_ids
            # evo-dec-88f3a2c1 targets artifact-44d7e9b0, which is not in
            # persona-alpha's lineage at all — it must not appear.
            assert "evo-dec-88f3a2c1" not in source_ids

            # 2. Test filtering by decision
            resp = client.get(
                "/bff/management/evolution-journal?decision=evo-dec-88f3a2c1",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            assert len(items) > 0
            for item in items:
                assert "evo-dec-88f3a2c1" in str(item).lower()

            # 3. Test filtering by mutation_review
            resp = client.get(
                "/bff/management/evolution-journal?mutation_review=evo-dec-88f3a2c1",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            assert len(items) > 0
            for item in items:
                assert item["entry_type"] in ("mutation_review", "evolution_decision")
                assert "evo-dec-88f3a2c1" in str(item).lower()

            # 4. Verify origin field in response is set correctly (seed/live)
            # Since our default read_surfaces has mock data, most should be tagged "seed"
            resp = client.get(
                "/bff/management/evolution-journal",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            assert len(items) > 0
            for item in items:
                assert "origin" in item
                # evo-vslice-1, btc-drift, etc., are mock/seed data
                if "vslice" in str(item).lower() or "btc-drift" in str(item).lower():
                    assert item["origin"] == "seed"

            # 5. Empty filtered hit for nonexistent persona
            resp = client.get(
                "/bff/management/evolution-journal?persona=nonexistent-persona-id-xyz",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 0
            assert body["page_info"]["total"] == 0

            # 6. Empty filtered hit for nonexistent decision
            resp = client.get(
                "/bff/management/evolution-journal?decision=nonexistent-dec-id-xyz",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 0
            assert body["page_info"]["total"] == 0

        finally:
            bff_main.read_store = original_store


def test_evolution_journal_requires_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal")

            assert resp.status_code == 401, resp.text
            assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            bff_main.read_store = original_store


def test_evochain_007_filters_provenance_and_decoys() -> None:
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {
                "persona-1": {
                    "id": "persona-1",
                    "persona_id": "persona-1",
                    "artifact_id": "artifact-1"
                },
                "persona-10": {
                    "id": "persona-10",
                    "persona_id": "persona-10",
                    "artifact_id": "artifact-10"
                }
            },
            "runtime_bindings": {
                "runtime-1": {
                    "id": "runtime-1",
                    "runtime_id": "runtime-1",
                    "persona_id": "persona-1",
                    "artifact_id": "artifact-1"
                }
            },
            "incidents": {
                "inc-1": {
                    "id": "inc-1",
                    "incident_id": "inc-1",
                    "artifact_id": "artifact-1",
                    "runtime_id": "runtime-1"
                }
            },
            "evolution_decisions": {
                "dec-1": {
                    "id": "dec-1",
                    "decision_id": "dec-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-1",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Retrain dec-1"},
                    "created_at": "2026-07-14T00:00:00Z"
                },
                "dec-10": {
                    "id": "dec-10",
                    "decision_id": "dec-10",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-10",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Retrain dec-10"},
                    "created_at": "2026-07-14T00:01:00Z"
                },
                "dec-decoy": {
                    "id": "dec-decoy",
                    "decision_id": "dec-decoy",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-other",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "title": "Decoy for persona-1",
                    "summary": "This is a decoy mentioning persona-1 but not associated with it",
                    "proposed_changes": {"summary": "Decoy"},
                    "created_at": "2026-07-14T00:02:00Z"
                },
                "evo-vslice-1": {
                    "id": "evo-vslice-1",
                    "decision_id": "evo-vslice-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-vslice",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Vslice"},
                    "created_at": "2026-07-14T00:03:00Z"
                },
                "dec-live": {
                    "id": "dec-live",
                    "decision_id": "dec-live",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-live",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "metadata": {
                        "origin": "live"
                    },
                    "proposed_changes": {"summary": "Live"},
                    "created_at": "2026-07-14T00:04:00Z"
                },
                "dec-explicit-unknown": {
                    "id": "dec-explicit-unknown",
                    "decision_id": "dec-explicit-unknown",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-unknown",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "metadata": {
                        "origin": "unknown"
                    },
                    "proposed_changes": {"summary": "Unknown"},
                    "created_at": "2026-07-14T00:05:00Z"
                }
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {}
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            # 1. Test persona decoy / collision filtering (explicit lineage matching)
            # Querying for persona-1 should ONLY match dec-1 (artifact-1 is in persona-1's lineage)
            # It should not match dec-10 (persona-10 collision) and should not match dec-decoy (free text decoy)
            resp = client.get("/bff/management/evolution-journal?persona=persona-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            assert len(items) == 2
            assert all(item["source_id"] == "dec-1" for item in items)

            # 2. Test typed hits / empty results
            # Querying for decision dec-1 should return decision item
            resp = client.get("/bff/management/evolution-journal?decision=dec-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items_dec = resp.json()["data"]["items"]
            assert len(items_dec) == 1
            assert items_dec[0]["entry_type"] == "evolution_decision"

            # Querying for mutation_review dec-1 should return mutation_review item
            resp = client.get("/bff/management/evolution-journal?mutation_review=dec-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items_mr = resp.json()["data"]["items"]
            assert len(items_mr) == 1
            assert items_mr[0]["entry_type"] == "mutation_review"

            # Querying for nonexistent decision should return 0 items
            resp = client.get("/bff/management/evolution-journal?decision=nonexistent", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            assert len(resp.json()["data"]["items"]) == 0

            # 3. Test authoritative seed/live/unknown provenance
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            # 6 decisions + 6 mutation reviews + 6 backfilled default items = 18 total items
            assert len(items) == 18

            by_id_and_type = {(item["source_id"], item["entry_type"]): item for item in items}
            assert by_id_and_type[("evo-vslice-1", "evolution_decision")]["origin"] == "seed"
            assert by_id_and_type[("dec-live", "evolution_decision")]["origin"] == "live"
            assert by_id_and_type[("dec-explicit-unknown", "evolution_decision")]["origin"] == "unknown"
            assert by_id_and_type[("dec-1", "evolution_decision")]["origin"] == "unknown"

            # Verify mutation reviews also preserve the correct origin
            assert by_id_and_type[("evo-vslice-1", "mutation_review")]["origin"] == "seed"
            assert by_id_and_type[("dec-live", "mutation_review")]["origin"] == "live"
            assert by_id_and_type[("dec-explicit-unknown", "mutation_review")]["origin"] == "unknown"
            assert by_id_and_type[("dec-1", "mutation_review")]["origin"] == "unknown"

            # 4. Test filtered total plus next token (paging)
            # Querying for persona-1 with page_size=1 should return exactly 1 item and total=2 (dec-1 decision + dec-1 mutation_review)
            resp = client.get("/bff/management/evolution-journal?persona=persona-1&page_size=1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 2
            assert len(body["data"]["items"]) == 1
            next_token = body["page_info"]["next_page_token"]
            assert next_token is not None

            # Get next page
            resp = client.get(f"/bff/management/evolution-journal?persona=persona-1&page_size=1&page_token={next_token}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 1
            assert body["page_info"]["total"] == 2
            assert body["page_info"]["next_page_token"] is None

        finally:
            bff_main.read_store = original_store


def test_evochain_007_filter_dependency_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            # Create a TestClient with raise_server_exceptions=False to allow FastAPI exception handler to return 500
            bff_main.read_store = create_read_surface_ports()
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            # Mock read_store.list_personas to raise a RuntimeError exception
            def mock_list_personas(*args, **kwargs):
                raise RuntimeError("Database connection lost")

            bff_main.read_store.list_personas = mock_list_personas

            # Querying with persona filter should fail with 500
            resp = client.get("/bff/management/evolution-journal?persona=persona-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 500
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_mutation_review_collision() -> None:
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {},
            "runtime_bindings": {},
            "incidents": {},
            "evolution_decisions": {
                "dec-1": {
                    "id": "dec-1",
                    "decision_id": "dec-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-1",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Retrain dec-1"},
                    "created_at": "2026-07-14T00:00:00Z"
                }
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {}
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            # Querying with persona=mutation_review should NOT match mutation reviews unless there is a matching persona
            resp = client.get("/bff/management/evolution-journal?persona=mutation_review", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 0
        finally:
            bff_main.read_store = original_store


def test_evochain_007_origin_exact_match_not_broad_substring() -> None:
    """A decision id that merely *contains* the substring "seed" (e.g. a
    "live-seedling" artifact) must not be honestly reported as seed-derived
    provenance; only exact registered seed identifiers/prefixes qualify."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {},
            "runtime_bindings": {},
            "incidents": {},
            "evolution_decisions": {
                "live-seedling-1": {
                    "id": "live-seedling-1",
                    "decision_id": "live-seedling-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-live-seedling",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Not actually a registered seed"},
                    "created_at": "2026-07-14T00:00:00Z"
                },
                "evo-vslice-1": {
                    "id": "evo-vslice-1",
                    "decision_id": "evo-vslice-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-vslice",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Registered vslice seed"},
                    "created_at": "2026-07-14T00:01:00Z"
                }
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {}
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            by_id = {
                item["source_id"]: item
                for item in items
                if item["entry_type"] == "evolution_decision"
            }
            assert by_id["live-seedling-1"]["origin"] == "unknown"
            assert by_id["evo-vslice-1"]["origin"] == "seed"
        finally:
            bff_main.read_store = original_store


def _evochain_007_lineage_mock_data(evolution_decisions: dict, **surfaces) -> dict:
    mock_data = {
        "personas": {},
        "runtime_bindings": {},
        "incidents": {},
        "evolution_decisions": evolution_decisions,
        "postmortems": {},
        "freeze_orders": {},
        "rollbacks": {},
    }
    mock_data.update(surfaces)
    return mock_data


def test_evochain_007_persona_lineage_shared_artifact_boundary() -> None:
    """Two unrelated personas independently bind to the same shared artifact.
    Filtering by persona-a must not pull in persona-b's own decision just
    because their bindings happen to reference the same artifact_id."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                # Each persona declares its own runtime_id so read_store's
                # binding-ownership reconciliation (typed exact identity,
                # not a raw copied persona_id) resolves ownership correctly.
                "persona-a": {"id": "persona-a", "persona_id": "persona-a", "runtime_id": "runtime-a"},
                "persona-b": {"id": "persona-b", "persona_id": "persona-b", "runtime_id": "runtime-b"},
            },
            runtime_bindings={
                "runtime-a": {
                    "id": "runtime-a",
                    "runtime_id": "runtime-a",
                    "persona_id": "persona-a",
                    "artifact_id": "artifact-shared",
                },
                "runtime-b": {
                    "id": "runtime-b",
                    "runtime_id": "runtime-b",
                    "persona_id": "persona-b",
                    "artifact_id": "artifact-shared",
                },
            },
            evolution_decisions={
                "dec-a": {
                    "id": "dec-a",
                    "decision_id": "dec-a",
                    "target_type": "runtime",
                    "target_id": "runtime-a",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-a own decision"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-b": {
                    "id": "dec-b",
                    "decision_id": "dec-b",
                    "target_type": "artifact",
                    "target_id": "artifact-shared",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-b's own decision, shares the artifact"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-a", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            source_ids = {item["source_id"] for item in items}
            assert "dec-a" in source_ids
            assert "dec-b" not in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_namespace_collision() -> None:
    """persona-pool-1 and persona-pool-10 must not collide through their
    capital_pool_id / persona_capital_binding_id namespaces."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                # Each persona declares its own runtime_binding_id so
                # read_store's binding-ownership reconciliation (typed exact
                # identity) resolves ownership correctly.
                "persona-pool-1": {
                    "id": "persona-pool-1",
                    "persona_id": "persona-pool-1",
                    "runtime_binding_id": "binding-pool-1",
                },
                "persona-pool-10": {
                    "id": "persona-pool-10",
                    "persona_id": "persona-pool-10",
                    "runtime_binding_id": "binding-pool-10",
                },
            },
            runtime_bindings={
                "binding-pool-1": {
                    "id": "binding-pool-1",
                    "persona_id": "persona-pool-1",
                    "capital_pool_id": "pool-1",
                },
                "binding-pool-10": {
                    "id": "binding-pool-10",
                    "persona_id": "persona-pool-10",
                    "capital_pool_id": "pool-10",
                },
            },
            incidents={
                "inc-pool-1": {
                    "id": "inc-pool-1",
                    "incident_id": "inc-pool-1",
                    "capital_pool_id": "pool-1",
                },
                "inc-pool-10": {
                    "id": "inc-pool-10",
                    "incident_id": "inc-pool-10",
                    "capital_pool_id": "pool-10",
                },
            },
            evolution_decisions={
                "dec-pool-1": {
                    "id": "dec-pool-1",
                    "decision_id": "dec-pool-1",
                    "target_type": "incident",
                    "incident_ref": "inc-pool-1",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "pool-1 decision"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-pool-10": {
                    "id": "dec-pool-10",
                    "decision_id": "dec-pool-10",
                    "target_type": "incident",
                    "incident_ref": "inc-pool-10",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "pool-10 decision"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-pool-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-pool-1" in source_ids
            assert "dec-pool-10" not in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_pool_only_convergence() -> None:
    """A persona linked to a decision only through a shared capital_pool_id
    chain (no direct runtime/binding id overlap) must still resolve."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-pool-only": {
                    "id": "persona-pool-only",
                    "persona_id": "persona-pool-only",
                    "runtime_binding_id": "binding-pool-only",
                },
            },
            runtime_bindings={
                "binding-pool-only": {
                    "id": "binding-pool-only",
                    "persona_id": "persona-pool-only",
                    "capital_pool_id": "pool-only-42",
                },
            },
            incidents={
                "inc-pool-only": {
                    "id": "inc-pool-only",
                    "incident_id": "inc-pool-only",
                    "capital_pool_id": "pool-only-42",
                },
            },
            evolution_decisions={
                "dec-pool-only": {
                    "id": "dec-pool-only",
                    "decision_id": "dec-pool-only",
                    "target_type": "incident",
                    "incident_ref": "inc-pool-only",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "resolved only via capital_pool_id"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-pool-only", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-pool-only" in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_capital_binding_only_convergence() -> None:
    """A persona linked to a decision only through persona_capital_binding_id
    (no runtime/plan/pool overlap) must still resolve."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-cb-only": {
                    "id": "persona-cb-only",
                    "persona_id": "persona-cb-only",
                    "persona_capital_binding_id": "pcb-only-7",
                },
            },
            incidents={
                "inc-cb-only": {
                    "id": "inc-cb-only",
                    "incident_id": "inc-cb-only",
                    "persona_capital_binding_id": "pcb-only-7",
                },
            },
            evolution_decisions={
                "dec-cb-only": {
                    "id": "dec-cb-only",
                    "decision_id": "dec-cb-only",
                    "target_type": "incident",
                    "incident_ref": "inc-cb-only",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "resolved only via persona_capital_binding_id"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-cb-only", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-cb-only" in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_deep_chain_requires_fixed_point() -> None:
    """persona -[persona_id]-> binding1(runtime-deep-1) -[plan]->
    binding2(runtime-deep-2) -[pool]-> binding3(runtime-deep-3)
    -[persona_capital_binding]-> binding4(runtime-deep-4) -[pool]->
    incident -[incident_ref]-> decision is a 5-edge chain. Every binding
    below has its own distinct runtime_id (bindings are keyed/deduped by
    runtime_id in read_store, so two bindings can never legitimately share
    one), and the bindings dict is deliberately ordered in *reverse*
    dependency order (hop4 first, hop1 last) so a single left-to-right pass
    cannot resolve it. A hardcoded 3-iteration traversal converges at most 3
    edges per query and would never reach the leaf decision; only a
    fixed-point loop (iterate until no set grows) resolves it regardless of
    edge order or chain depth."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-deep": {
                    "id": "persona-deep",
                    "persona_id": "persona-deep",
                    "runtime_id": "runtime-deep-1",
                },
            },
            runtime_bindings={
                # Listed in reverse dependency order on purpose.
                "binding-hop4": {
                    "id": "binding-hop4",
                    "runtime_id": "runtime-deep-4",
                    "persona_capital_binding_id": "pcb-deep-3",
                    "capital_pool_id": "pool-deep-4",
                },
                "binding-hop3": {
                    "id": "binding-hop3",
                    "runtime_id": "runtime-deep-3",
                    "capital_pool_id": "pool-deep-2",
                    "persona_capital_binding_id": "pcb-deep-3",
                },
                "binding-hop2": {
                    "id": "binding-hop2",
                    "runtime_id": "runtime-deep-2",
                    "plan_id": "plan-deep-1",
                    "capital_pool_id": "pool-deep-2",
                },
                "binding-hop1": {
                    "id": "binding-hop1",
                    "persona_id": "persona-deep",
                    "runtime_id": "runtime-deep-1",
                    "plan_id": "plan-deep-1",
                },
            },
            incidents={
                "inc-deep-5": {
                    "id": "inc-deep-5",
                    "incident_id": "inc-deep-5",
                    "capital_pool_id": "pool-deep-4",
                },
            },
            evolution_decisions={
                "dec-deep-6": {
                    "id": "dec-deep-6",
                    "decision_id": "dec-deep-6",
                    "target_type": "incident",
                    "incident_ref": "inc-deep-5",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "5 edges deep from the queried persona"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-unrelated": {
                    "id": "dec-unrelated",
                    "decision_id": "dec-unrelated",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-unrelated",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "not connected to persona-deep at all"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-deep", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-deep-6" in source_ids
            assert "dec-unrelated" not in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_canonical_binding_without_runtime_row() -> None:
    """A canonical persona-capital binding (read_store.list_bindings) can
    exist with no matching runtime row at all. list_runtime_bindings() only
    uses list_bindings() to enrich runtime rows that already exist, so
    relying on it alone would make a runtime-less persona -> binding -> pool
    -> incident chain invisible; the endpoint must also read list_bindings()
    directly."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-nr": {
                    "id": "persona-nr",
                    "persona_id": "persona-nr",
                    "persona_capital_binding_id": "pcb-nr",
                },
            },
            bindings={
                "pcb-nr": {
                    "id": "pcb-nr",
                    "binding_id": "pcb-nr",
                    "persona_id": "persona-nr",
                    "capital_pool_id": "pool-nr",
                },
            },
            incidents={
                "inc-nr": {
                    "id": "inc-nr",
                    "incident_id": "inc-nr",
                    "capital_pool_id": "pool-nr",
                },
            },
            evolution_decisions={
                "dec-nr": {
                    "id": "dec-nr",
                    "decision_id": "dec-nr",
                    "target_type": "incident",
                    "incident_ref": "inc-nr",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "resolved only via a runtime-less canonical binding"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-nr", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-nr" in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_shared_pool_does_not_cross_persona_ownership() -> None:
    """Two distinct personas independently bind to the same shared capital
    pool. Filtering by persona-pool-a must not adopt persona-pool-b's own
    identity (and therefore not its private decision) just because their
    bindings happen to reference the same capital_pool_id."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-pool-a": {
                    "id": "persona-pool-a",
                    "persona_id": "persona-pool-a",
                    "runtime_binding_id": "binding-pool-a",
                },
                # Declared so read_store's binding-ownership reconciliation
                # (typed exact identity) confirms binding-pool-b really
                # belongs to persona-pool-b, rather than nulling out its
                # persona_id as an unclaimed binding.
                "persona-pool-b": {
                    "id": "persona-pool-b",
                    "persona_id": "persona-pool-b",
                    "runtime_binding_id": "binding-pool-b",
                },
            },
            runtime_bindings={
                "binding-pool-a": {
                    "id": "binding-pool-a",
                    "persona_id": "persona-pool-a",
                    "capital_pool_id": "pool-shared",
                },
                "binding-pool-b": {
                    "id": "binding-pool-b",
                    "persona_id": "persona-pool-b",
                    "runtime_id": "runtime-pool-b",
                    "capital_pool_id": "pool-shared",
                },
            },
            evolution_decisions={
                "dec-pool-a": {
                    "id": "dec-pool-a",
                    "decision_id": "dec-pool-a",
                    "target_type": "artifact",
                    "capital_pool_id": "pool-shared",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-pool-a's own decision, via the shared pool"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-pool-b-private": {
                    "id": "dec-pool-b-private",
                    "decision_id": "dec-pool-b-private",
                    "target_type": "runtime",
                    "target_id": "runtime-pool-b",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-pool-b's own private decision"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-pool-a", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-pool-a" in source_ids
            assert "dec-pool-b-private" not in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_persona_lineage_typed_namespaces_reject_cross_type_string_collision() -> None:
    """A persona's own runtime_id and an unrelated decision's artifact
    target_id happen to be the identical raw string. Matching must stay
    scoped to the typed namespace the id belongs to (runtime vs artifact),
    never a flattened id blob, so the two must not collide."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-typed": {
                    "id": "persona-typed",
                    "persona_id": "persona-typed",
                    "runtime_id": "same-token",
                },
            },
            evolution_decisions={
                "dec-unrelated-artifact": {
                    "id": "dec-unrelated-artifact",
                    "decision_id": "dec-unrelated-artifact",
                    "target_type": "artifact",
                    "target_id": "same-token",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "unrelated artifact that happens to share the raw string"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-own-runtime": {
                    "id": "dec-own-runtime",
                    "decision_id": "dec-own-runtime",
                    "target_type": "runtime",
                    "target_id": "same-token",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "genuinely persona-typed's own runtime"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-typed", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-own-runtime" in source_ids
            assert "dec-unrelated-artifact" not in source_ids
        finally:
            bff_main.read_store = original_store


def test_evochain_007_seed_registry_recognizes_registered_families() -> None:
    """Registered seed identifier families beyond evo-vslice- (ev-seed-001..005
    decision ids from services/evolution/seed_data.py, the documented seed
    incident inc-87c655c3e3c9, and decisions that reference a registered seed
    artifact/runtime/binding/plan target) must be honestly labeled
    origin:seed; a similarly-spelled but unregistered id must stay unknown."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {},
            "runtime_bindings": {},
            "incidents": {},
            "evolution_decisions": {
                "ev-seed-003": {
                    "id": "ev-seed-003",
                    "decision_id": "ev-seed-003",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-ev-seed-003",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Registered ev-seed family member"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "ev-seeding-decoy": {
                    "id": "ev-seeding-decoy",
                    "decision_id": "ev-seeding-decoy",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-decoy",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Looks like the ev-seed- family but is not registered"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
                "dec-swept-from-seed-incident": {
                    "id": "dec-swept-from-seed-incident",
                    "decision_id": "dec-swept-from-seed-incident",
                    "target_type": "incident",
                    "incident_ref": "inc-87c655c3e3c9",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Swept from the documented seed incident"},
                    "created_at": "2026-07-14T00:02:00Z",
                },
                "dec-targets-seed-artifact": {
                    "id": "dec-targets-seed-artifact",
                    "decision_id": "dec-targets-seed-artifact",
                    "target_type": "artifact",
                    "target_id": "artifact-042",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Targets the registered seed artifact"},
                    "created_at": "2026-07-14T00:03:00Z",
                },
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {},
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            by_id = {
                item["source_id"]: item
                for item in items
                if item["entry_type"] == "evolution_decision"
            }
            assert by_id["ev-seed-003"]["origin"] == "seed"
            assert by_id["ev-seeding-decoy"]["origin"] == "unknown"
            assert by_id["dec-swept-from-seed-incident"]["origin"] == "seed"
            assert by_id["dec-targets-seed-artifact"]["origin"] == "seed"
        finally:
            bff_main.read_store = original_store


def test_evochain_007_filter_dependency_surfaces_reported_and_fail_closed() -> None:
    """The personas/persona_bindings/runtime_bindings/incidents datasets that
    back the ?persona= lineage filter must be visible in meta.surfaces, and a
    *silently* unavailable dependency (dataset_source reports "missing" with
    no exception raised) must fail the filtered request closed (503) instead
    of returning an authoritative-looking empty total=0."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = create_read_surface_ports()
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            surfaces = resp.json()["meta"]["surfaces"]
            for key in ("personas", "persona_bindings", "runtime_bindings", "incidents"):
                assert key in surfaces

            original_dataset_source = bff_main.read_store.dataset_source
            bff_main.read_store.dataset_source = (
                lambda dataset, **kwargs: "missing"
                if dataset == "personas"
                else original_dataset_source(dataset, **kwargs)
            )
            resp = client.get(
                "/bff/management/evolution-journal?persona=persona-alpha",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 503, resp.text
        finally:
            bff_main.read_store = original_store
