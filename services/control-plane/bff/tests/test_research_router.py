"""Standalone contract tests for the prepared typed Research router."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from research.router import RESEARCH_ROUTE_INVENTORY, create_research_router  # noqa: E402


# Copied from the audited migration assignment, not derived from router.py.
# This protects against a future edit reducing the router-owned surface while
# updating its exported inventory to match.
EXPECTED_RESEARCH_ROUTE_DECORATORS = {
    ("GET", "/api/v1/workbench/knowledge"),
    ("GET", "/api/v1/operator/research/oss-activation-ready"),
    ("GET", "/api/v1/operator/research/oss-preactivation"),
    ("GET", "/api/v1/operator/source/ops"),
    ("GET", "/api/v1/operator/search/ops"),
    ("POST", "/api/v1/operator/source/dlq/replay"),
    ("POST", "/api/v1/operator/source/frontier/{frontier_id}/replay"),
    ("POST", "/api/v1/operator/search/index/refresh"),
    ("POST", "/api/v1/operator/search/index/materialize"),
    ("POST", "/api/v1/research/tickets"),
    ("GET", "/api/v1/research/tickets"),
    ("GET", "/api/v1/research/tickets/{ticket_id}"),
    ("PATCH", "/api/v1/research/tickets/{ticket_id}"),
    ("GET", "/api/v1/research/search"),
    ("GET", "/api/v1/research/source-connectors"),
    ("GET", "/api/v1/research/source-change-proposals"),
    ("GET", "/api/v1/research/analysis"),
    ("GET", "/api/v1/research/analysis/{analysis_id}"),
    ("POST", "/api/v1/experiments/launch"),
    ("GET", "/api/v1/experiments"),
    ("GET", "/api/v1/experiments/{experiment_id}"),
    ("POST", "/api/v1/experiments/{experiment_id}/cancel"),
    ("GET", "/api/v1/artifacts"),
    ("GET", "/api/v1/artifacts/compare"),
    ("GET", "/api/v1/artifacts/{artifact_id}"),
    ("POST", "/api/v1/knowledge/notes"),
    ("GET", "/api/v1/knowledge/notes"),
    ("GET", "/api/v1/knowledge/notes/{note_id}"),
    ("GET", "/api/v1/knowledge/evidence"),
    ("GET", "/api/v1/knowledge/evidence/{ref_id}"),
    ("GET", "/api/v1/knowledge/insights"),
    ("GET", "/api/v1/knowledge/insights/{insight_id}"),
    ("GET", "/api/v1/knowledge/strategy-specs"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}/versions"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}/compare"),
    ("GET", "/api/v1/knowledge/memory"),
    ("GET", "/api/v1/knowledge/memory/{entry_id}"),
    ("GET", "/bff/synthesis/conflict-logs"),
    ("GET", "/bff/synthesis/conflict-logs/{log_id}"),
    ("GET", "/bff/search"),
    ("GET", "/bff/artifacts"),
    ("GET", "/bff/artifacts/{artifact_id}"),
    ("GET", "/bff/research-analyses"),
    ("GET", "/bff/research-analyses/{analysis_id}"),
    ("PATCH", "/bff/artifacts/{artifact_id}"),
    ("POST", "/bff/artifacts"),
}


class _Port:
    def __init__(self, *, source: str = "typed_store") -> None:
        self.source = source
        self.analyses: Dict[str, Dict[str, Any]] = {
            "analysis-1": {
                "analysis_id": "analysis-1",
                "ticket_id": "ticket-1",
                "experiment_id": "experiment-1",
                "status": "completed",
                "run_at": "2026-08-30T00:00:00Z",
                "summary": {"verdict": "hold"},
            }
        }
        self.artifacts: Dict[str, Dict[str, Any]] = {
            "artifact-1": {
                "artifact_id": "artifact-1",
                "experiment_id": "experiment-1",
                "ticket_id": "ticket-1",
                "lineage_id": "lineage-1",
                "status": "sealed",
                "allowedActions": {"canCompare": True},
            },
            "artifact-2": {
                "artifact_id": "artifact-2",
                "status": "superseded",
                "allowedActions": {"canCompare": True},
            },
            "artifact-3": {
                "artifact_id": "artifact-3",
                "experiment_id": "experiment-other",
                "ticket_id": "ticket-1",
                "lineage_id": "lineage-1",
                "status": "sealed",
                "allowedActions": {"canCompare": True},
            },
        }
        self.experiments: Dict[str, Dict[str, Any]] = {
            "experiment-1": {
                "experiment_id": "experiment-1",
                "ticket_id": "ticket-1",
                "status": "queued",
                "allowedActions": {"canCancel": True, "canDelete": True},
            }
        }
        self.last_artifact_filters: Dict[str, Any] = {}
        self.last_search_filters: Dict[str, Any] = {}
        self.tickets: Dict[str, Dict[str, Any]] = {
            "ticket-1": {
                "ticket_id": "ticket-1",
                "title": "Durable ticket",
                "status": "open",
                "allowedActions": {"canEdit": True},
            }
        }
        self.notes: Dict[str, Dict[str, Any]] = {}
        self.strategy_specs: Dict[str, Dict[str, Any]] = {
            "strategy-1": {
                "strategy_id": "strategy-1",
                "title": "Alpha Strategy",
                "versions": {
                    "v1": {
                        "strategy_id": "strategy-1",
                        "spec_version_id": "strategy-1-v1",
                        "spec_version": "v1",
                        "lifecycle_state": "candidate",
                        "title": "Alpha Strategy",
                        "hypothesis": "Initial alpha hypothesis",
                        "objective": "Verify the signal",
                        "allowedActions": {"canCompare": True},
                        "citation_bundle": {"evidence": ["evidence-include"]},
                    },
                    "v2": {
                        "strategy_id": "strategy-1",
                        "spec_version_id": "strategy-1-v2",
                        "spec_version": "v2",
                        "parent_spec_version_id": "strategy-1-v1",
                        "lifecycle_state": "approved",
                        "title": "Alpha Strategy",
                        "hypothesis": "Refined alpha hypothesis",
                        "objective": "Verify the signal",
                        "allowedActions": {"canCompare": True},
                        "citation_bundle": {"evidence": ["evidence-include"]},
                    },
                },
            }
        }
        self.evidence_refs: Dict[str, Dict[str, Any]] = {
            "evidence-include": {
                "ref_id": "evidence-include",
                "source_document": {"title": "Primary source"},
                "link_type": "supporting_evidence",
                "credibility": {"tier": "primary", "verified": True},
                "linked_object_summary": {
                    "entity_type": "research_note",
                    "entity_ref": "note-include",
                },
                "resolved_link": {"href": "/knowledge/notes/note-include"},
                "route_href": "/knowledge/evidence/evidence-include",
            },
            "evidence-exclude": {
                "ref_id": "evidence-exclude",
                "source_document": {"title": "Secondary source"},
                "link_type": "citation",
                "credibility": {"tier": "secondary", "verified": False},
                "linked_object_summary": {
                    "entity_type": "strategy_spec",
                    "entity_ref": "strat-exclude",
                },
                "resolved_link": {"href": "/knowledge/strategy-specs/strat-exclude"},
            },
        }
        self.insights: Dict[str, Dict[str, Any]] = {
            "insight-include": {
                "insight_id": "insight-include",
                "summary": "Fresh alpha insight",
                "scope": "persona",
                "scope_ref": "persona-1",
                "status": "active",
                "confidence": {"score": 0.9},
                "tags": ["alpha"],
                "linked_sources": [{"entity_type": "research_note", "entity_ref": "note-include"}],
                "aggregated_at": "2026-08-29T00:00:00Z",
            },
            "insight-exclude": {
                "insight_id": "insight-exclude",
                "summary": "Archived beta insight",
                "scope": "persona",
                "scope_ref": "persona-2",
                "status": "archived",
                "confidence": {"score": 0.2},
                "tags": ["beta"],
                "linked_sources": [{"entity_type": "experiment", "entity_ref": "experiment-1"}],
                "aggregated_at": "2026-04-01T00:00:00Z",
            },
        }
        self.memory_entries: Dict[str, Dict[str, Any]] = {
            "memory-include": {
                "entry_id": "memory-include",
                "headline": "Retained alpha memory",
                "knowledge_type": "lesson",
                "scope": "persona",
                "scope_filter": "persona-1",
                "tags": ["alpha"],
            },
            "memory-exclude": {
                "entry_id": "memory-exclude",
                "headline": "Retained beta memory",
                "knowledge_type": "lesson",
                "scope": "persona",
                "scope_filter": "persona-2",
                "tags": ["beta"],
            },
        }
        self.conflict_logs: Dict[str, Dict[str, Any]] = {
            "conflict-1": {
                "log_id": "conflict-1",
                "capital_pool_id": "pool-1",
                "scope_ref": "scope-1",
                "proposal_ids": ["proposal-1", "proposal-2"],
                "weighting_outputs": {"proposal-1": 1.0, "proposal-2": 0.0},
            }
        }

    def dataset_source(self, _dataset: str) -> str:
        return self.source

    def dataset_surface_status(
        self,
        dataset: str,
        *,
        snapshot_at: str,
        source: str,
        has_data: bool,
        missing_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        if source == "missing" or has_data is False:
            return {
                "status": "unavailable",
                "source": source,
                "snapshot_at": snapshot_at,
                "message": missing_message,
            }
        if source == "local_snapshot":
            return {"status": "degraded", "source": source, "snapshot_at": snapshot_at}
        return {"status": "ok", "source": source}

    def list_research_analyses(self, *, ticket_id=None, experiment_id=None, statuses=None, date_range=None) -> List[Dict[str, Any]]:
        records = list(self.analyses.values())
        if ticket_id:
            records = [item for item in records if item["ticket_id"] == ticket_id]
        if experiment_id:
            records = [item for item in records if item["experiment_id"] == experiment_id]
        if statuses:
            records = [item for item in records if item["status"] in statuses]
        return records

    def get_research_analysis(self, analysis_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.analyses.get(str(analysis_id))

    def list_research_artifacts(self, **filters: Any) -> List[Dict[str, Any]]:
        self.last_artifact_filters = dict(filters)
        return [
            artifact
            for artifact in self.artifacts.values()
            if all(value is None or artifact.get(name) == value for name, value in filters.items())
        ]

    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.artifacts.get(str(artifact_id))

    def compare_research_artifacts(self, artifact_ids: List[str]) -> Dict[str, Any]:
        return {"artifacts": [self.artifacts[artifact_id] for artifact_id in artifact_ids], "comparisons": []}

    def list_research_tickets(self, **_filters: Any) -> List[Dict[str, Any]]:
        return list(self.tickets.values())

    def get_research_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self.tickets.get(ticket_id)

    def create_research_ticket(self, **payload: Any) -> Dict[str, Any]:
        ticket = {
            "ticket_id": "ticket-created",
            "status": "open",
            "created_at": payload["created_at"],
            "allowedActions": {"canEdit": True},
            **payload,
        }
        self.tickets[ticket["ticket_id"]] = ticket
        return ticket

    def patch_research_ticket(self, ticket_id: str, *, patch: Dict[str, Any], **_kwargs: Any) -> Optional[Dict[str, Any]]:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return None
        ticket.update(patch)
        ticket["updated_at"] = "2026-08-30T00:00:00Z"
        return ticket

    def list_research_experiments(self, **filters: Any) -> List[Dict[str, Any]]:
        return [
            experiment
            for experiment in self.experiments.values()
            if all(value is None or experiment.get(name) == value for name, value in filters.items())
        ]

    def get_research_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        return self.experiments.get(experiment_id)

    def get_research_search_index(self) -> Dict[str, Any]:
        return {"snapshot_at": "2026-08-30T00:00:00Z", "indexed_match_types": ["ticket"]}

    def list_research_search_results(self, **filters: Any) -> List[Dict[str, Any]]:
        self.last_search_filters = dict(filters)
        return []

    def get_source_connector_registry(self) -> Dict[str, Any]:
        return {"source": "service_client", "connectors": [{"connector_id": "source-1"}]}

    def get_source_change_proposals(self, **_filters: Any) -> Dict[str, Any]:
        return {"source": "service_client", "proposals": [{"proposal_id": "proposal-1"}]}

    def create_research_note(self, note: Dict[str, Any]) -> Dict[str, Any]:
        self.notes[note["note_id"]] = note
        return note

    def list_research_notes(self) -> List[Dict[str, Any]]:
        return list(self.notes.values())

    def get_research_note(self, note_id: str) -> Optional[Dict[str, Any]]:
        return self.notes.get(note_id)

    def list_evidence_refs(self) -> List[Dict[str, Any]]:
        return list(self.evidence_refs.values())

    def get_evidence_ref(self, ref_id: str) -> Optional[Dict[str, Any]]:
        return self.evidence_refs.get(ref_id)

    def get_evidence_ref_detail(self, ref_id: str) -> Optional[Dict[str, Any]]:
        return self.evidence_refs.get(ref_id)

    def list_insight_cards(self) -> List[Dict[str, Any]]:
        return list(self.insights.values())

    def get_insight_card_detail(self, insight_id: str) -> Optional[Dict[str, Any]]:
        return self.insights.get(insight_id)

    def list_institutional_memory_entries(self) -> List[Dict[str, Any]]:
        return list(self.memory_entries.values())

    def get_institutional_memory_entry(self, entry_id: str, **_kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.memory_entries.get(entry_id)

    def list_strategy_specs(self, **_filters: Any) -> List[Dict[str, Any]]:
        return [
            {
                "strategy_id": spec["strategy_id"],
                "title": spec["title"],
                "current_spec_version": "v2",
                "lifecycle_state": "approved",
            }
            for spec in self.strategy_specs.values()
        ]

    def get_strategy_spec(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        spec = self.strategy_specs.get(strategy_id)
        if not spec:
            return None
        current = spec["versions"]["v2"]
        return {
            "strategy_id": strategy_id,
            "title": spec["title"],
            "name": spec["title"],
            "spec_version_id": current["spec_version_id"],
            "spec_version": current["spec_version"],
            "lifecycle_state": current["lifecycle_state"],
        }

    def get_strategy_spec_detail(
        self,
        strategy_id: str,
        *,
        version_selector: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        spec = self.strategy_specs.get(strategy_id)
        if not spec:
            return None
        selector = version_selector or "v2"
        if selector == "current":
            selector = "v2"
        return spec["versions"].get(selector)

    def list_strategy_spec_versions(self, strategy_id: str) -> List[Dict[str, Any]]:
        spec = self.strategy_specs.get(strategy_id)
        if not spec:
            return []
        return [
            {
                "spec_version_id": detail["spec_version_id"],
                "spec_version": detail["spec_version"],
                "lifecycle_state": detail["lifecycle_state"],
            }
            for detail in spec["versions"].values()
        ]

    def compare_strategy_spec_versions(
        self,
        strategy_id: str,
        *,
        left_selector: str,
        right_selector: str,
    ) -> Optional[Dict[str, Any]]:
        left = self.get_strategy_spec_detail(strategy_id, version_selector=left_selector)
        right = self.get_strategy_spec_detail(strategy_id, version_selector=right_selector)
        if not left or not right:
            return None
        return {
            "strategy_id": strategy_id,
            "left_version": left["spec_version"],
            "right_version": right["spec_version"],
            "changed_sections": ["hypothesis"],
        }

    def list_synthesis_conflict_logs(self, **filters: Any) -> List[Dict[str, Any]]:
        records = list(self.conflict_logs.values())
        return [
            record
            for record in records
            if all(value is None or record.get(name) == value for name, value in filters.items())
        ]

    def get_synthesis_conflict_log(self, log_id: str) -> Optional[Dict[str, Any]]:
        return self.conflict_logs.get(log_id)

    def list_strategies(self) -> List[Dict[str, Any]]:
        return [{"strategy_id": "strategy-1", "title": "Alpha Strategy", "lifecycle_state": "active"}]

    def list_personas(self) -> List[Dict[str, Any]]:
        return [{"persona_id": "persona-1", "name": "Alpha Persona", "lifecycle_state": "active"}]

    def list_capital_pools(self) -> List[Dict[str, Any]]:
        return [{"pool_id": "pool-1", "name": "Alpha Pool", "status": "active"}]

    def get_source_ops_snapshot(self, **_filters: Any) -> Dict[str, Any]:
        return {"source": "service_client", "summary": {"healthy": True}}

    def get_search_ops_snapshot(self, **_filters: Any) -> Dict[str, Any]:
        return {"source": "service_client", "summary": {"freshness_ok": True}}


def _bff_error(status_code, code, message, reason, **extra):
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, "reason": reason, **extra},
    )


def _router(port: _Port, *, capabilities: Optional[List[str]] = None):
    return create_research_router(
        get_read_store=lambda: port,
        extract_identity=lambda _authorization: SimpleNamespace(operator_id="op-test"),
        require_read_role=lambda _identity: None,
        require_operator_role=lambda _identity: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-08-30T00:00:00Z",
        dataset_surface_status=port.dataset_surface_status,
        get_capabilities=lambda _identity: capabilities,
        include_prepared_subrouters=False,
    )


def _client(port: _Port, *, capabilities: Optional[List[str]] = None) -> TestClient:
    app = FastAPI()
    app.include_router(_router(port, capabilities=capabilities))
    return TestClient(app)


def test_research_router_declares_all_47_assigned_decorators() -> None:
    router = _router(_Port())
    actual = {
        (method, route.path)
        for route in router.routes
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }

    assert len(EXPECTED_RESEARCH_ROUTE_DECORATORS) == 47
    assert set(RESEARCH_ROUTE_INVENTORY) == EXPECTED_RESEARCH_ROUTE_DECORATORS
    assert EXPECTED_RESEARCH_ROUTE_DECORATORS <= actual

    # The final generic aliases are represented by typed port-backed reads and
    # explicit fail-closed write semantics; no in-memory fallback is exposed.
    assert ("GET", "/bff/artifacts/{artifact_id}") in actual
    assert ("GET", "/bff/research-analyses/{analysis_id}") in actual
    assert ("PATCH", "/bff/artifacts/{artifact_id}") in actual
    assert ("POST", "/bff/artifacts") in actual


def test_research_inventory_ticket_and_source_routes_use_injected_port() -> None:
    client = _client(_Port())

    listed = client.get("/api/v1/research/tickets")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["ticket_id"] == "ticket-1"

    created = client.post(
        "/api/v1/research/tickets",
        json={"title": "New", "description": "durable", "priority": "normal", "owner": "research"},
    )
    assert created.status_code == 200
    assert created.json()["ticket_id"] == "ticket-created"

    connectors = client.get("/api/v1/research/source-connectors")
    assert connectors.status_code == 200
    assert connectors.json()["data"] == [{"connector_id": "source-1"}]

    proposals = client.get("/api/v1/research/source-change-proposals")
    assert proposals.status_code == 200
    assert proposals.json()["data"] == [{"proposal_id": "proposal-1"}]
    assert proposals.json()["meta"]["source"] == "service_client"
    assert proposals.json()["meta"]["surfaces"]["source_change_proposals"] == "ok"

    source_ops = client.get("/api/v1/operator/source/ops")
    assert source_ops.status_code == 200
    assert source_ops.json()["data"]["source"] == "service_client"

    # The source command seam is composition-owned.  The prepared domain
    # router fails closed instead of falling back to main.py's client globals.
    command = client.post("/api/v1/operator/search/index/materialize")
    assert command.status_code == 501


def test_research_note_create_preserves_server_owned_validation_and_identity() -> None:
    port = _Port()
    client = _client(port)

    owner_override = client.post(
        "/api/v1/knowledge/notes",
        json={
            "body": "Caller tries to choose the note owner.",
            "attachment_type": "free_standing",
            "attachment_ref": None,
            "owner_ref": {"owner_id": "attacker"},
        },
    )
    assert owner_override.status_code == 400
    assert owner_override.json()["detail"]["precondition_failed"] == "owner_ref"

    missing_body = client.post(
        "/api/v1/knowledge/notes",
        json={"attachment_type": "free_standing", "attachment_ref": None},
    )
    assert missing_body.status_code == 400
    assert missing_body.json()["detail"]["precondition_failed"] == "body"

    missing_attachment = client.post(
        "/api/v1/knowledge/notes",
        json={"body": "No attachment taxonomy was provided."},
    )
    assert missing_attachment.status_code == 400
    assert missing_attachment.json()["detail"]["precondition_failed"] == "attachment_type"

    created = client.post(
        "/api/v1/knowledge/notes",
        json={
            "title": "Server identity",
            "body": "The BFF assigns this note's owner.",
            "attachment_type": "free_standing",
            "attachment_ref": None,
            "tags": ["routing"],
        },
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert set(receipt) == {"note_id", "created_at", "route_href"}
    assert receipt["note_id"].startswith("note-")
    assert receipt["route_href"] == f"/knowledge/notes/{receipt['note_id']}"
    assert port.notes[receipt["note_id"]]["owner_ref"] == {
        "owner_type": "operator",
        "owner_id": "op-test",
        "display_name": "Operator op-test",
    }


def test_research_note_list_preserves_legacy_filters_and_envelope() -> None:
    port = _Port()
    matching_ticket_ref = "tkt-11111111-1111-1111-1111-111111111111"
    port.tickets[matching_ticket_ref] = {
        "ticket_id": matching_ticket_ref,
        "title": "Attached ticket",
        "status": "open",
    }
    port.notes = {
        "note-include": {
            "note_id": "note-include",
            "title": "Included note",
            "body": "The **alpha** note is included.",
            "owner_ref": {"owner_id": "op-a"},
            "attachment_type": "free_standing",
            "attachment_ref": None,
            "tags": ["alpha"],
            "created_at": "2026-08-30T00:00:00Z",
            "updated_at": "2026-08-30T00:00:00Z",
        },
        "note-wrong-owner": {
            "note_id": "note-wrong-owner",
            "title": "Excluded owner",
            "body": "This must be excluded.",
            "owner_ref": {"owner_id": "op-b"},
            "attachment_type": "free_standing",
            "attachment_ref": None,
            "tags": ["alpha"],
        },
        "note-wrong-tag": {
            "note_id": "note-wrong-tag",
            "title": "Excluded tag",
            "body": "This must be excluded.",
            "owner_ref": {"owner_id": "op-a"},
            "attachment_type": "free_standing",
            "attachment_ref": None,
            "tags": ["beta"],
        },
        "note-wrong-attachment": {
            "note_id": "note-wrong-attachment",
            "title": "Excluded attachment",
            "body": "This must be excluded.",
            "owner_ref": {"owner_id": "op-a"},
            "attachment_type": "research_ticket",
            "attachment_ref": matching_ticket_ref,
            "tags": ["alpha"],
        },
        "note-wrong-ref": {
            "note_id": "note-wrong-ref",
            "title": "Excluded attachment ref",
            "body": "This must be excluded.",
            "owner_ref": {"owner_id": "op-a"},
            "attachment_type": "research_ticket",
            "attachment_ref": "tkt-22222222-2222-2222-2222-222222222222",
            "tags": ["alpha"],
        },
    }
    client = _client(port)

    response = client.get(
        "/api/v1/knowledge/notes",
        params={"owner_ref": "op-a", "attachment_type": "free_standing", "tags": "alpha"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"notes", "pagination", "meta"}
    assert [note["note_id"] for note in payload["notes"]] == ["note-include"]
    assert payload["notes"][0]["excerpt"] == "The alpha note is included."
    assert "body" not in payload["notes"][0]
    assert payload["pagination"] == {
        "page_size": 20,
        "next_page_token": None,
        "has_more": False,
    }
    assert payload["meta"]["surfaces"]["research_note_list"] == "ok"

    attached = client.get(
        "/api/v1/knowledge/notes",
        params={
            "owner_ref": "op-a",
            "attachment_type": "research_ticket",
            "attachment_ref": matching_ticket_ref,
            "tags": "alpha",
        },
    )
    assert attached.status_code == 200, attached.text
    assert [note["note_id"] for note in attached.json()["notes"]] == ["note-wrong-attachment"]
    assert attached.json()["notes"][0]["attachment"] == {
        "type": "research_ticket",
        "ref": matching_ticket_ref,
        "display_label": "Attached ticket",
    }

    invalid_ref = client.get("/api/v1/knowledge/notes", params={"attachment_ref": "ticket-1"})
    assert invalid_ref.status_code == 400
    assert invalid_ref.json()["detail"]["precondition_failed"] == "attachment_ref"


def test_research_note_detail_preserves_attachment_link_and_surface_projections() -> None:
    port = _Port()
    ticket_id = "tkt-11111111-1111-1111-1111-111111111111"
    memory_id = "mem-11111111-1111-1111-1111-111111111111"
    port.tickets[ticket_id] = {"ticket_id": ticket_id, "title": "Attached ticket"}
    port.evidence_refs["evidence-include"]["display_label"] = "Primary source"
    port.memory_entries[memory_id] = {
        "entry_id": memory_id,
        "knowledge_type": "lesson",
        "content": {"headline": "Durable memory anchor"},
        "lifecycle": {"status": "active"},
    }
    port.notes["note-detail"] = {
        "note_id": "note-detail",
        "title": "Linked note detail",
        "body": "The complete durable note.",
        "owner_ref": {"owner_type": "operator", "owner_id": "op-test"},
        "attachment_type": "research_ticket",
        "attachment_ref": ticket_id,
        "tags": ["alpha"],
        "linked_evidence_refs": ["evidence-include", "evidence-missing"],
        "linked_memory_anchors": [memory_id, "memory-missing"],
        "created_at": "2026-08-30T00:00:00Z",
        "updated_at": "2026-08-30T01:00:00Z",
    }

    response = _client(port).get("/api/v1/knowledge/notes/note-detail")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "note_id", "title", "body", "owner_ref", "attachment", "tags",
        "linked_evidence_refs", "linked_memory_anchors", "created_at", "updated_at", "meta",
    }
    assert payload["attachment"] == {
        "type": "research_ticket",
        "ref": ticket_id,
        "display_label": "Attached ticket",
        "route_href": f"/research/tickets/{ticket_id}",
    }
    assert payload["linked_evidence_refs"] == [
        {
            "ref_id": "evidence-include",
            "resolution_state": "resolved",
            "display_label": "Primary source",
            "route_href": "/knowledge/evidence/evidence-include",
        },
        {
            "ref_id": "evidence-missing",
            "resolution_state": "unresolved",
            "display_label": None,
            "route_href": None,
        },
    ]
    assert payload["linked_memory_anchors"] == [
        {
            "entry_id": memory_id,
            "headline": "Durable memory anchor",
            "knowledge_type": "lesson",
            "lifecycle_status": "active",
            "route_href": f"/knowledge/memory/{memory_id}",
        }
    ]
    assert payload["meta"]["surfaces"] == {
        "research_note_detail": "ok",
        "evidence_links": "ok",
        "memory_anchors": "degraded",
    }


def test_strategy_spec_routes_preserve_legacy_projections_and_compare_gates() -> None:
    port = _Port()
    client = _client(port)

    listed = client.get("/api/v1/knowledge/strategy-specs")
    assert listed.status_code == 200, listed.text
    assert set(listed.json()) == {"items", "page_info", "meta"}
    assert listed.json()["items"] == [{
        "strategy_id": "strategy-1",
        "title": "Alpha Strategy",
        "current_spec_version": "v2",
        "lifecycle_state": "approved",
    }]
    assert listed.json()["page_info"] == {
        "next_page_token": None,
        "page_size": 20,
        "has_more": False,
    }
    assert listed.json()["meta"]["surfaces"] == {"strategy_spec_list": "ok"}

    detail = client.get("/api/v1/knowledge/strategy-specs/strategy-1", params={"version": "v1"})
    assert detail.status_code == 200, detail.text
    assert detail.json()["strategy_id"] == "strategy-1"
    assert detail.json()["spec_version"] == "v1"
    assert detail.json()["lifecycle_state"] == "candidate"
    assert detail.json()["meta"]["surfaces"] == {
        "strategy_spec_detail": "ok",
        "citation_bundle": "ok",
        "version_ancestry": "degraded",
    }

    versions = client.get("/api/v1/knowledge/strategy-specs/strategy-1/versions")
    assert versions.status_code == 200, versions.text
    assert versions.json()["strategy_id"] == "strategy-1"
    assert [item["spec_version"] for item in versions.json()["versions"]] == ["v1", "v2"]
    assert versions.json()["meta"]["surfaces"] == {"version_history": "ok"}

    same_version = client.get(
        "/api/v1/knowledge/strategy-specs/strategy-1/compare",
        params={"left_version": "v1", "right_version": "v1"},
    )
    assert same_version.status_code == 422
    assert same_version.json()["detail"]["precondition_failed"] == "left_version"

    compared = client.get(
        "/api/v1/knowledge/strategy-specs/strategy-1/compare",
        params={"left_version": "v1", "right_version": "v2"},
    )
    assert compared.status_code == 200, compared.text
    assert compared.json()["changed_sections"] == ["hypothesis"]
    assert compared.json()["meta"]["surfaces"] == {"strategy_spec_compare": "ok"}

    port.strategy_specs["strategy-1"]["versions"]["v1"]["allowedActions"] = {"canCompare": False}
    forbidden = client.get(
        "/api/v1/knowledge/strategy-specs/strategy-1/compare",
        params={"left_version": "v1", "right_version": "v2"},
    )
    assert forbidden.status_code == 422
    assert forbidden.json()["detail"]["precondition_failed"] == "lifecycle_state"


def test_knowledge_evidence_insight_and_memory_routes_preserve_filters_and_envelopes() -> None:
    client = _client(_Port())

    evidence = client.get(
        "/api/v1/knowledge/evidence",
        params={
            "linked_entity_type": "research_note",
            "linked_entity_ref": "note-include",
            "link_type": "supporting_evidence",
            "credibility_tier": "primary",
            "verified": "true",
        },
    )
    assert evidence.status_code == 200, evidence.text
    evidence_payload = evidence.json()
    assert [item["ref_id"] for item in evidence_payload["evidence_refs"]] == ["evidence-include"]
    assert set(evidence_payload) == {"evidence_refs", "pagination", "meta"}
    assert evidence_payload["meta"]["surfaces"]["evidence_refs_list"] == "ok"

    missing_evidence_parent = client.get(
        "/api/v1/knowledge/evidence", params={"linked_entity_ref": "note-include"}
    )
    assert missing_evidence_parent.status_code == 400
    assert missing_evidence_parent.json()["detail"]["precondition_failed"] == "linked_entity_ref"

    insights = client.get(
        "/api/v1/knowledge/insights",
        params={
            "status": "active",
            "tag": "alpha",
            "linked_entity_type": "research_note",
            "linked_entity_ref": "note-include",
            "recency": "7d",
            "confidence_min": "0.8",
        },
    )
    assert insights.status_code == 200, insights.text
    insight_payload = insights.json()
    assert [item["insight_id"] for item in insight_payload["insight_cards"]] == ["insight-include"]
    assert insight_payload["filter_metadata"]["total_active_count"] == 1
    assert insight_payload["meta"]["surfaces"]["insight_cards"] == "ok"

    memory = client.get(
        "/api/v1/knowledge/memory",
        params={
            "knowledge_type": "lesson",
            "scope": "persona",
            "scope_filter": "persona-1",
            "tags": "alpha",
        },
    )
    assert memory.status_code == 200, memory.text
    assert [item["entry_id"] for item in memory.json()["entries"]] == ["memory-include"]
    assert memory.json()["pagination"]["total_count"] == 1


def test_knowledge_detail_routes_preserve_redaction_projection_and_source_surfaces() -> None:
    port = _Port()
    port.evidence_refs["evidence-detail"] = {
        "ref_id": "evidence-detail",
        "source_document": {"title": "Operator note", "source_type": "internal"},
        "link_type": "supporting_evidence",
        "credibility": {"tier": "primary", "verified": True, "reason": "reviewed"},
        "resolved_link": {"href": "/knowledge/notes/note-include", "availability": "available"},
        "linked_object_summary": {"entity_type": "research_note", "entity_ref": "note-include"},
        "linked_decisions": [
            {"entity_type": "strategy_spec", "entity_ref": "strategy-sensitive"},
            {"entity_type": "research_note", "entity_ref": "note-include"},
        ],
        "source_note_context": {"note_id": "note-include", "title": "Research context"},
        "source_memory_context": {"entry_id": "memory-include", "headline": "Memory context"},
        "created_at": "2026-08-29T00:00:00Z",
    }
    port.evidence_refs["evidence-blocked"] = {
        "ref_id": "evidence-blocked",
        "evidence_type": "alert",
        "source_document": {"title": "Restricted alert"},
    }
    port.insights["insight-detail"] = {
        "insight_id": "insight-detail",
        "summary": "Composed insight",
        "scope": "persona",
        "scope_context": {"scope_ref": "persona-1", "display_label": "Alpha Persona"},
        "status": "active",
        "superseded_by": {"insight_id": None, "summary": None, "route_href": None},
        "confidence": {"score": 0.92, "label": "high", "basis": "two sources"},
        "tags": ["alpha", "validated"],
        "source_ref": "research-run-1",
        "supporting_evidence_refs": [
            {
                "ref_id": "evidence-detail",
                "display_label": "Operator note",
                "resolved_link": {"href": "/knowledge/evidence/evidence-detail"},
            }
        ],
        "linked_sources": [
            {
                "entity_type": "research_note",
                "entity_ref": "note-include",
                "display_label": "Research context",
                "route_href": "/knowledge/notes/note-include",
            }
        ],
        "aggregation_provenance": {"aggregated_at": "2026-08-30T00:00:00Z"},
        "created_at": "2026-08-29T00:00:00Z",
        "updated_at": "2026-08-30T00:00:00Z",
    }
    port.memory_entries["memory-detail"] = {
        "entry_id": "memory-detail",
        "knowledge_type": "lesson",
        "content": {"headline": "Preserved memory detail"},
        "source_event": {"type": "experiment", "id": "experiment-1"},
        "scope": {"type": "persona", "filter": "persona-1"},
    }

    client = _client(port, capabilities=[])

    evidence = client.get("/api/v1/knowledge/evidence/evidence-detail")
    assert evidence.status_code == 200, evidence.text
    evidence_payload = evidence.json()
    assert set(evidence_payload) == {
        "ref_id", "source_document", "link_type", "credibility", "resolved_link",
        "linked_object_summary", "linked_decisions", "source_note_context",
        "source_memory_context", "created_at", "meta",
    }
    redacted_decision = evidence_payload["linked_decisions"][0]
    assert redacted_decision["ref_id"] == "strategy-sensitive"
    assert redacted_decision["kind"] == "strategy"
    assert redacted_decision["required_capability"] == "strategy.view"
    assert redacted_decision["reason"] == "insufficient_capability"
    assert redacted_decision["redacted"] is True
    assert evidence_payload["linked_decisions"][1] == {
        "entity_type": "research_note", "entity_ref": "note-include",
    }
    assert evidence_payload["meta"] == {
        "snapshot_at": "2026-08-30T00:00:00Z",
        "surfaces": {
            "evidence_ref_detail": "ok", "resolved_link": "ok", "linked_decisions": "ok",
        },
        "redacted_evidence_count": 1,
    }

    blocked = client.get("/api/v1/knowledge/evidence/evidence-blocked")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["redacted"] is True
    assert blocked.json()["meta"]["redacted_evidence_count"] == 1

    insight = client.get("/api/v1/knowledge/insights/insight-detail")
    assert insight.status_code == 200, insight.text
    insight_payload = insight.json()
    assert insight_payload["scope_context"]["display_label"] == "Alpha Persona"
    assert insight_payload["confidence"] == {"score": 0.92, "label": "high", "basis": "two sources"}
    assert insight_payload["supporting_evidence_refs"][0]["ref_id"] == "evidence-detail"
    assert insight_payload["linked_sources"][0]["route_href"] == "/knowledge/notes/note-include"
    assert insight_payload["meta"]["surfaces"] == {
        "insight_card_detail": "ok", "supporting_evidence_refs": "ok", "linked_sources": "ok",
    }

    memory = client.get("/api/v1/knowledge/memory/memory-detail")
    assert memory.status_code == 200, memory.text
    assert memory.json()["content"]["headline"] == "Preserved memory detail"
    assert memory.json()["meta"]["surfaces"] == {
        "entry_detail": "ok", "source_context": "ok",
    }

    port.source = "local_snapshot"
    degraded_insight = client.get("/api/v1/knowledge/insights/insight-detail")
    assert degraded_insight.status_code == 200, degraded_insight.text
    assert degraded_insight.json()["meta"]["surfaces"] == {
        "insight_card_detail": "degraded",
        "supporting_evidence_refs": "degraded",
        "linked_sources": "degraded",
    }
    degraded_memory = client.get("/api/v1/knowledge/memory/memory-detail")
    assert degraded_memory.status_code == 200, degraded_memory.text
    assert degraded_memory.json()["meta"]["surfaces"] == {
        "entry_detail": "degraded", "source_context": "degraded",
    }


def test_conflict_log_and_cross_entity_search_routes_use_injected_port_data() -> None:
    port = _Port()
    port.conflict_logs["conflict-1"].update(
        {
            "allocation_policy_artifact_id": "allocation-1",
            "allocation_policy_artifact_href": "/bff/allocation-policies/allocation-1",
            "governance_approval_id": "approval-1",
        }
    )
    client = _client(port)

    logs = client.get("/bff/synthesis/conflict-logs", params={"capital_pool_id": "pool-1"})
    assert logs.status_code == 200, logs.text
    assert logs.json()["items"][0]["id"] == "conflict-1"
    assert logs.json()["items"][0]["view"]["proposal_rows"][0]["state"] == "selected"

    detail = client.get("/bff/synthesis/conflict-logs/conflict-1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["resolution_state"] == "resolved"
    assert detail.json()["data"]["view"]["links"] == {
        "allocation_policy_artifact": {
            "id": "allocation-1", "href": "/bff/allocation-policies/allocation-1",
        },
        "governance_approval": {"id": "approval-1", "href": "/bff/approvals/approval-1"},
    }

    search = client.get("/bff/search", params={"q": "alpha", "types": "strategy,persona"})
    assert search.status_code == 200, search.text
    assert {(item["type"], item["id"]) for item in search.json()["items"]} == {
        ("strategy", "strategy-1"),
        ("persona", "persona-1"),
    }
    assert search.json()["page_info"]["returned"] == 2


def test_generic_artifact_write_aliases_are_replaced_with_typed_fail_closed_contracts() -> None:
    client = _client(_Port())

    immutable = client.patch("/bff/artifacts/artifact-1", json={"status": "sealed"})
    assert immutable.status_code == 409
    assert immutable.json()["detail"]["code"] == "OPERATION_NOT_ALLOWED"

    unsupported = client.post("/bff/artifacts", json={"name": "not-a-generic-artifact"})
    assert unsupported.status_code == 501
    assert unsupported.json()["detail"]["code"] == "NOT_IMPLEMENTED"


def test_typed_analysis_routes_use_durable_port_and_preserve_links() -> None:
    client = _client(_Port())

    listed = client.get("/api/v1/research/analyses", params={"status": "completed"})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["links"]["self"] == "/api/v1/research/analyses/analysis-1"

    detail = client.get("/api/v1/research/analyses/analysis-1")
    assert detail.status_code == 200
    assert detail.json()["links"]["linked_experiment_detail"] == "/research/experiments/experiment-1"


def test_typed_analysis_validation_and_unavailable_surface_are_explicit() -> None:
    invalid = _client(_Port()).get("/api/v1/research/analyses", params={"status": "archived"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["precondition_failed"] == "status"

    unavailable = _client(_Port(source="missing")).get("/api/v1/research/analyses")
    assert unavailable.status_code == 200
    assert unavailable.json()["data"] == []
    assert unavailable.json()["meta"]["surfaces"]["analysis_results"]["status"] == "unavailable"

    compat = _client(_Port()).get("/api/v1/research/analysis/analysis-1")
    assert compat.status_code == 200
    assert compat.json()["links"]["self"] == "/api/v1/research/analysis/analysis-1"


def test_typed_artifact_and_bff_replacement_routes_are_backed_by_same_port() -> None:
    client = _client(_Port())

    detail = client.get("/api/v1/research/artifacts/artifact-1")
    assert detail.status_code == 200
    assert detail.json()["artifact_id"] == "artifact-1"

    bff_detail = client.get("/bff/artifacts/artifact-1")
    assert bff_detail.status_code == 200
    assert bff_detail.json()["data"]["artifact_id"] == "artifact-1"

    comparison = client.get("/api/v1/research/artifacts/compare", params={"artifact_ids": "artifact-1,artifact-2"})
    assert comparison.status_code == 200
    assert [item["artifact_id"] for item in comparison.json()["artifacts"]] == ["artifact-1", "artifact-2"]

    non_comparable_port = _Port()
    non_comparable_port.artifacts["artifact-1"]["allowedActions"] = {"canCompare": False}
    rejected = _client(non_comparable_port).get(
        "/api/v1/research/artifacts/compare",
        params={"artifact_ids": "artifact-1,artifact-2"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "OPERATION_NOT_ALLOWED"


def test_inventory_routes_preserve_ticket_and_experiment_validation() -> None:
    client = _client(_Port())

    invalid_priority = client.post(
        "/api/v1/research/tickets",
        json={"title": "New", "description": "durable", "priority": "urgent", "owner": "research"},
    )
    assert invalid_priority.status_code == 422
    assert invalid_priority.json()["detail"]["precondition_failed"] == "priority"

    invalid_experiment_status = client.get("/api/v1/experiments", params={"status": "paused"})
    assert invalid_experiment_status.status_code == 422
    assert invalid_experiment_status.json()["detail"]["precondition_failed"] == "status"

    live_execution_mode = client.post(
        "/api/v1/experiments/launch",
        json={
            "ticket_id": "ticket-1",
            "experiment_name": "Invalid live launch",
            "strategy_selector": {"strategy_id": "strategy-1"},
            "parameter_set": {},
            "run_config": {
                "dataset_ref": "equities-us-2026Q1",
                "time_range": {"start_at": "2026-03-01T00:00:00Z", "end_at": "2026-03-31T00:00:00Z"},
                "execution_mode": "live",
                "priority": "normal",
                "requested_by": "research",
            },
        },
    )
    assert live_execution_mode.status_code == 422
    assert live_execution_mode.json()["detail"]["precondition_failed"] == "execution_mode"


def test_inventory_artifact_filters_are_validated_and_forwarded_to_the_port() -> None:
    port = _Port()
    client = _client(port)

    response = client.get(
        "/api/v1/artifacts",
        params={
            "experiment_id": "experiment-1",
            "ticket_id": "ticket-1",
            "lineage_id": "lineage-1",
            "status": "sealed",
        },
    )
    assert response.status_code == 200, response.text
    assert [item["artifact_id"] for item in response.json()["artifacts"]] == ["artifact-1"]
    assert port.last_artifact_filters == {
        "experiment_id": "experiment-1",
        "ticket_id": "ticket-1",
        "lineage_id": "lineage-1",
        "status": "sealed",
    }

    class _NarrowArtifactPort(_Port):
        def list_research_artifacts(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
            self.last_artifact_filters = {"status": status}
            return [
                artifact
                for artifact in self.artifacts.values()
                if status is None or artifact.get("status") == status
            ]

    narrow_port = _NarrowArtifactPort()
    narrow_response = _client(narrow_port).get(
        "/api/v1/artifacts",
        params={
            "experiment_id": "experiment-1",
            "ticket_id": "ticket-1",
            "lineage_id": "lineage-1",
            "status": "sealed",
        },
    )
    assert narrow_response.status_code == 200, narrow_response.text
    assert [item["artifact_id"] for item in narrow_response.json()["artifacts"]] == ["artifact-1"]
    assert narrow_port.last_artifact_filters == {"status": "sealed"}

    invalid = client.get("/api/v1/artifacts", params={"status": "draft"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["precondition_failed"] == "status"


def test_inventory_search_rejects_invalid_frozen_match_type_before_port_access() -> None:
    client = _client(_Port())

    invalid = client.get(
        "/api/v1/research/search", params={"q": "alpha", "match_type": "note"}
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["precondition_failed"] == "match_type"


def test_inventory_experiment_and_ticket_routes_restore_legacy_projections() -> None:
    client = _client(_Port())

    experiments = client.get("/api/v1/experiments")
    assert experiments.status_code == 200, experiments.text
    item = experiments.json()["data"][0]
    assert item["links"] == {
        "self": "/api/v1/experiments/experiment-1",
        "workbench_detail": "/research/experiments/experiment-1",
    }
    assert item["allowedActions"] == {"canCancel": True}
    assert experiments.json()["meta"]["surfaces"]["experiment_history"] == "ok"

    tickets = client.get("/api/v1/research/tickets")
    assert tickets.status_code == 200, tickets.text
    assert tickets.json()["meta"]["surfaces"]["ticket_list"] == "fresh"

    ticket = client.get("/api/v1/research/tickets/ticket-1")
    assert ticket.status_code == 200, ticket.text
    assert ticket.json()["meta"]["surfaces"]["ticket_detail"] == "fresh"

    unavailable = _client(_Port(source="missing")).get("/api/v1/research/tickets")
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["data"] == []
    assert unavailable.json()["page_info"] == {"next_page_token": None, "total": 0}
    assert unavailable.json()["meta"]["surfaces"]["ticket_list"] == "unavailable"


def test_inventory_routes_publish_legacy_request_and_query_contracts_to_openapi() -> None:
    client = _client(_Port())
    schema = client.get("/openapi.json").json()["paths"]

    ticket_create = schema["/api/v1/research/tickets"]["post"]
    assert "requestBody" in ticket_create
    ticket_list_params = {item["name"] for item in schema["/api/v1/research/tickets"]["get"]["parameters"]}
    assert {"status", "owner", "page_token", "page_size"} <= ticket_list_params

    launch = schema["/api/v1/experiments/launch"]["post"]
    assert "requestBody" in launch
    experiment_list_params = {item["name"] for item in schema["/api/v1/experiments"]["get"]["parameters"]}
    assert {"ticket_id", "status", "page_token", "page_size"} <= experiment_list_params
