"""Tests for ResearchKnowledgeSourcePort and console_gap/knowledge router integration."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


from ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    ResearchKnowledgeSourcePort,
    ResearchWriteOwnerUnavailableError,
    _parse_rfc3339,
    _utc_now_rfc3339,
)
from console_gap.knowledge import create_knowledge_router
from services.memory.institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryStore,
)


def _sample_utc_now() -> str:
    return "2026-08-28T01:00:00Z"


def _fake_extract_identity(auth: str | None) -> dict:
    return {"user_id": "test-user", "roles": ["operator", "research"]}


def _fake_require_read_role(identity: dict) -> None:
    pass


def _fake_dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: str,
    source: str = "missing",
    has_data: bool = True,
    missing_message: str | None = None,
) -> dict:
    if source in ("missing", "unavailable") or not has_data:
        return {
            "status": "unavailable",
            "source": source,
            "message": missing_message or f"{dataset} has no readable records",
        }
    return {"status": "ok", "source": source}


# -----------------------------------------------------------------------------
# Unit Tests for DefaultResearchKnowledgeSourcePort
# -----------------------------------------------------------------------------

def test_port_initialization_and_surface_status():
    port = DefaultResearchKnowledgeSourcePort()
    assert port.dataset_source("research_notes") == "missing"
    assert port.dataset_source("data_sources") == "missing"
    assert port.dataset_source("search_ops") == "missing"

    surface = port.dataset_surface_status("research_notes", snapshot_at="2026-08-28T01:00:00Z", has_data=False)
    assert surface["status"] == "unavailable"
    assert surface["source"] == "missing"


class _FakeResearchWriteOwner:
    """Minimal stand-in for ``services.research.write_owner.ResearchWriteOwner``.

    BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001 and BFF-RESEARCH-SINGLE-OWNER-001:
    notes, tickets, and experiments persistence belongs exclusively to
    ResearchWriteOwner / Research service. This fake is injected via the port's
    ``research_write_owner`` constructor kwarg (dependency injection for testing),
    not a production fallback.
    """

    _CANCELABLE = frozenset({"queued", "running"})

    def __init__(self) -> None:
        self._experiments: dict[str, dict] = {}
        self._notes: dict[str, dict] = {}
        self._tickets: dict[str, dict] = {}

    def create_research_note(self, note: dict) -> dict:
        note_id = str(note.get("note_id") or "").strip()
        payload = dict(note)
        self._notes[note_id] = payload
        return payload

    def create_research_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        owner: str,
        actor_id: str,
        created_at: str | None = None,
    ) -> dict:
        timestamp = created_at or _utc_now_rfc3339()
        ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(self._tickets) + 1:03d}"
        ticket = {
            "ticket_id": ticket_id,
            "title": title,
            "description": description,
            "status": "open",
            "priority": priority,
            "owner": owner,
            "created_at": timestamp,
            "updated_at": timestamp,
            "closed_at": None,
            "archived_at": None,
            "lifecycle_history": [
                {
                    "from_status": None,
                    "to_status": "open",
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            ],
            "linked_experiments": [],
            "linked_artifacts": [],
            "allowedActions": {"canEdit": True, "canClose": True, "canArchive": False},
        }
        self._tickets[ticket_id] = ticket
        return dict(ticket)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: dict,
        actor_id: str,
        updated_at: str | None = None,
    ) -> dict | None:
        ticket = self._tickets.get(str(ticket_id))
        if ticket is None:
            return None
        timestamp = updated_at or _utc_now_rfc3339()
        for f in {"title", "description", "priority", "owner"}:
            if f in patch:
                ticket[f] = patch[f]
        next_status = patch.get("status")
        if next_status is not None and next_status != ticket.get("status"):
            prev_status = ticket.get("status")
            ticket["status"] = next_status
            if next_status == "closed":
                ticket["closed_at"] = timestamp
                ticket["archived_at"] = None
                ticket["allowedActions"] = {"canEdit": False, "canClose": False, "canArchive": True}
            elif next_status == "archived":
                ticket["archived_at"] = timestamp
                ticket["allowedActions"] = {"canEdit": False, "canClose": False, "canArchive": False}
            else:
                if next_status in {"open", "in_progress"}:
                    ticket["closed_at"] = None
                if next_status != "archived":
                    ticket["archived_at"] = None
                ticket["allowedActions"] = {"canEdit": True, "canClose": True, "canArchive": False}
            ticket.setdefault("lifecycle_history", []).append({
                "from_status": prev_status,
                "to_status": next_status,
                "transitioned_at": timestamp,
                "transitioned_by": actor_id,
            })
        ticket["updated_at"] = timestamp
        return dict(ticket)

    def create_research_experiment(
        self,
        *,
        ticket_id,
        experiment_name,
        strategy_selector,
        parameter_set,
        run_config,
        launch_context,
        queued_at=None,
    ):
        timestamp = queued_at or _utc_now_rfc3339()
        date_part = timestamp[:10].replace("-", "")
        exp_id = f"exp-{date_part}-{len(self._experiments) + 1:03d}"
        record = {
            "experiment_id": exp_id,
            "ticket_id": ticket_id,
            "experiment_name": experiment_name,
            "status": "queued",
            "queued_at": timestamp,
            "strategy_selector": strategy_selector,
            "parameter_set": parameter_set,
            "run_config": run_config,
            "launch_context": launch_context,
            "allowedActions": {"canCancel": True},
        }
        self._experiments[exp_id] = record
        return dict(record)

    def get_research_experiment(self, experiment_id):
        record = self._experiments.get(str(experiment_id))
        return dict(record) if record else None

    def list_research_experiments(self, *, ticket_id=None, status=None):
        items = list(self._experiments.values())
        if ticket_id:
            items = [e for e in items if e.get("ticket_id") == ticket_id]
        if status:
            items = [e for e in items if e.get("status") == status]
        return [dict(e) for e in items]

    def cancel_research_experiment(self, experiment_id, *, completed_at=None):
        record = self._experiments.get(str(experiment_id))
        if record is None or record.get("status") not in self._CANCELABLE:
            return None
        record["status"] = "canceled"
        record["completed_at"] = completed_at or _utc_now_rfc3339()
        record["allowedActions"] = {"canCancel": False}
        return dict(record)


def test_research_notes_crud_and_sorting():
    port = DefaultResearchKnowledgeSourcePort(
        research_notes_store={
            "note-1": {
                "note_id": "note-1",
                "title": "Older Note",
                "body": "First finding",
                "created_at": "2026-08-20T10:00:00Z",
                "updated_at": "2026-08-20T10:00:00Z",
            },
            "note-2": {
                "note_id": "note-2",
                "title": "Newer Note",
                "body": "Second finding",
                "created_at": "2026-08-25T12:00:00Z",
                "updated_at": "2026-08-26T14:00:00Z",
            },
        },
        research_write_owner=_FakeResearchWriteOwner(),
    )
    assert port.dataset_source("research_notes") == "typed_store"

    notes = port.list_research_notes()
    assert len(notes) == 2
    assert notes[0]["note_id"] == "note-2"
    assert notes[1]["note_id"] == "note-1"

    note = port.get_research_note("note-1")
    assert note is not None
    assert note["title"] == "Older Note"

    # Create a new note
    created = port.create_research_note({
        "note_id": "note-3",
        "title": "Third Note",
        "body": "Third finding",
        "created_at": "2026-08-28T00:00:00Z",
    })
    assert created is not None
    assert created["note_id"] == "note-3"
    assert port.get_research_note("note-3") is not None


def test_evidence_refs_filtering_and_projection():
    port = DefaultResearchKnowledgeSourcePort(
        evidence_refs_store={
            "ref-1": {
                "ref_id": "ref-1",
                "tenant_id": "tenant-a",
                "display_label": "Evidence 1",
                "source_document": {
                    "title": "Doc 1",
                    "source_type": "sec_filing",
                    "source_ref": "sec://123",
                    "captured_at": "2026-08-27T08:00:00Z",
                },
                "link_type": "supporting",
                "credibility": {"tier": "primary", "verified": True},
                "linked_object_summary": {
                    "entity_type": "strategy_spec",
                    "entity_ref": "strat-1",
                },
            },
            "ref-2": {
                "ref_id": "ref-2",
                "tenant_id": "tenant-b",
                "display_label": "Evidence 2",
                "source_document": {
                    "title": "Doc 2",
                    "source_type": "news",
                    "captured_at": "2026-08-26T08:00:00Z",
                },
                "link_type": "context",
                "credibility": {"tier": "unverified", "verified": False},
            },
        }
    )

    # Filter by tenant
    tenant_a_refs = port.list_evidence_refs(tenant_id="tenant-a")
    assert len(tenant_a_refs) == 1
    assert tenant_a_refs[0]["ref_id"] == "ref-1"
    assert tenant_a_refs[0]["route_href"] == "/knowledge/evidence/ref-1"

    # Filter by source_type
    news_refs = port.list_evidence_refs(source_types={"news"})
    assert len(news_refs) == 1
    assert news_refs[0]["ref_id"] == "ref-2"

    # Detail projection
    detail = port.get_evidence_ref_detail("ref-1")
    assert detail is not None
    assert detail["ref_id"] == "ref-1"
    assert detail["credibility"]["tier"] == "primary"


def test_insight_cards_projection_and_sorting():
    port = DefaultResearchKnowledgeSourcePort(
        insight_cards_store={
            "ins-1": {
                "insight_id": "ins-1",
                "summary": "Alpha Decay Insight",
                "scope": "strategy",
                "scope_ref": "strat-1",
                "confidence": {"score": 0.85, "label": "high"},
                "aggregation_provenance": {"aggregated_at": "2026-08-27T10:00:00Z"},
            },
            "ins-2": {
                "insight_id": "ins-2",
                "summary": "Regime Shift Insight",
                "scope": "global",
                "confidence": {"score": 0.95, "label": "very_high"},
                "aggregation_provenance": {"aggregated_at": "2026-08-28T00:00:00Z"},
            },
        }
    )

    cards = port.list_insight_cards()
    assert len(cards) == 2
    assert cards[0]["insight_id"] == "ins-2"
    assert cards[0]["route_href"] == "/knowledge/insights/ins-2"
    assert cards[1]["insight_id"] == "ins-1"
    assert cards[1]["route_href"] == "/knowledge/insights/ins-1"

    detail = port.get_insight_card_detail("ins-1")
    assert detail is not None
    assert detail["summary"] == "Alpha Decay Insight"


def test_strategy_specs_versions_and_compare():
    port = DefaultResearchKnowledgeSourcePort(
        strategy_specs_store={
            "strat-1": {
                "strategy_id": "strat-1",
                "title": "Momentum Alpha",
                "versions": [
                    {
                        "spec_version_id": "strat-1-v1",
                        "spec_version": "v1",
                        "lifecycle_state": "candidate",
                        "hypothesis": "Short-term momentum yields alpha.",
                        "objective": "Sharpe > 2.0",
                        "created_at": "2026-08-20T00:00:00Z",
                    },
                    {
                        "spec_version_id": "strat-1-v2",
                        "spec_version": "v2",
                        "lifecycle_state": "approved",
                        "hypothesis": "Refined multi-factor momentum.",
                        "objective": "Sharpe > 2.5",
                        "execution_profile": {"execution_mode_hint": "twap"},
                        "created_at": "2026-08-25T00:00:00Z",
                    },
                ],
            }
        }
    )

    specs = port.list_strategy_specs()
    assert len(specs) == 1
    assert specs[0]["strategy_id"] == "strat-1"
    assert specs[0]["current_spec_version"] == "v2"

    detail = port.get_strategy_spec_detail("strat-1", version_selector="v1")
    assert detail is not None
    assert detail["spec_version_id"] == "strat-1-v1"

    versions = port.list_strategy_spec_versions("strat-1")
    assert len(versions) == 2

    # Compare
    comparison = port.compare_strategy_spec_versions(
        "strat-1",
        left_selector="strat-1-v1",
        right_selector="strat-1-v2",
    )
    assert comparison is not None
    assert comparison["strategy_id"] == "strat-1"
    changed_sections = [c["section"] for c in comparison["changed_sections"]]
    assert "hypothesis" in changed_sections
    assert "objective" in changed_sections


def test_institutional_memory_integration():
    mem_store = InstitutionalMemoryStore()
    entry = InstitutionalMemoryEntry(
        entry_id="mem-11111111-2222-3333-4444-555555555555",
        knowledge_type="incident_lesson",
        content={"headline": "Guard against stale quotes", "body": "Validate timestamp before execution."},
        source_event_type="runtime_telemetry_outcome",
        source_event_id="evt-100",
        written_at="2026-08-27T10:00:00Z",
        write_authority="telemetry-svc",
        scope="system_wide",
    )
    mem_store.create(entry)

    port = DefaultResearchKnowledgeSourcePort(institutional_memory_store=mem_store)
    assert port.dataset_source("institutional_memory_entries") == "typed_store"

    entries = port.list_institutional_memory_entries()
    assert len(entries) == 1
    assert entries[0]["entry_id"] == "mem-11111111-2222-3333-4444-555555555555"
    assert entries[0]["headline"] == "Guard against stale quotes"

    detail = port.get_institutional_memory_entry("mem-11111111-2222-3333-4444-555555555555")
    assert detail is not None
    assert detail["content"]["headline"] == "Guard against stale quotes"


def test_research_tickets_lifecycle():
    port = DefaultResearchKnowledgeSourcePort(research_write_owner=_FakeResearchWriteOwner())

    # Create ticket
    created = port.create_research_ticket(
        title="Explore LLM Agent Alpha",
        description="Research agent-driven sentiment alpha.",
        priority="high",
        owner="Antigravity",
        actor_id="operator-1",
        created_at="2026-08-28T01:00:00Z",
    )
    ticket_id = created["ticket_id"]
    assert ticket_id.startswith("rt-20260828-")
    assert created["status"] == "open"
    assert created["allowedActions"]["canEdit"] is True

    # Patch ticket
    patched = port.patch_research_ticket(
        ticket_id,
        patch={"status": "closed", "priority": "normal"},
        actor_id="operator-1",
    )
    assert patched is not None
    assert patched["status"] == "closed"
    assert patched["priority"] == "normal"
    assert patched["closed_at"] is not None
    assert patched["allowedActions"]["canArchive"] is True

    # List
    tickets = port.list_research_tickets(owner="Antigravity")
    assert len(tickets) == 1
    assert tickets[0]["ticket_id"] == ticket_id


def test_research_notes_and_tickets_fail_closed_without_write_owner():
    port = DefaultResearchKnowledgeSourcePort()

    with pytest.raises(ResearchWriteOwnerUnavailableError):
        port.create_research_note({"note_id": "n1", "title": "Fail"})

    with pytest.raises(ResearchWriteOwnerUnavailableError):
        port.create_research_ticket(
            title="Fail Ticket",
            description="desc",
            priority="high",
            owner="Antigravity",
            actor_id="op1",
        )

    with pytest.raises(ResearchWriteOwnerUnavailableError):
        port.patch_research_ticket("rt-1", patch={"status": "closed"}, actor_id="op1")


def test_research_analyses_and_experiments_no_overlays():
    port = DefaultResearchKnowledgeSourcePort(
        research_analyses_store={
            "ana-1": {
                "analysis_id": "ana-1",
                "ticket_id": "rt-1",
                "experiment_id": "exp-1",
                "status": "completed",
                "run_at": "2026-08-27T12:00:00Z",
                "summary": {"headline": "Backtest successful", "verdict": "pass"},
            }
        },
        research_write_owner=_FakeResearchWriteOwner(),
    )

    analyses = port.list_research_analyses(ticket_id="rt-1")
    assert len(analyses) == 1
    assert analyses[0]["analysis_id"] == "ana-1"

    # Create experiment
    exp = port.create_research_experiment(
        ticket_id="rt-1",
        experiment_name="Backtest Momentum v1",
        strategy_selector={"strategy_id": "strat-1"},
        parameter_set={"fast": 10, "slow": 30},
        run_config={"backend": "lean", "execution_mode": "backtest"},
        launch_context={"analysis_refs": ["ana-1"]},
        queued_at="2026-08-28T01:00:00Z",
    )
    exp_id = exp["experiment_id"]
    assert exp_id.startswith("exp-20260828-")
    assert exp["status"] == "queued"
    assert exp["allowedActions"]["canCancel"] is True

    # Cancel experiment
    canceled = port.cancel_research_experiment(exp_id)
    assert canceled is not None
    assert canceled["status"] == "canceled"
    assert canceled["allowedActions"]["canCancel"] is False


def test_research_experiments_fail_closed_without_write_owner(monkeypatch):
    """No in-memory fallback: an unconfigured ResearchWriteOwner must raise,
    never silently return an empty/fake success (BFF-RESEARCH-JOBS-OWNER-BINDING
    -CORRECTIVE-001)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESEARCH_STORE_DSN", raising=False)
    port = DefaultResearchKnowledgeSourcePort()

    with pytest.raises(ResearchWriteOwnerUnavailableError):
        port.list_research_experiments()

    with pytest.raises(ResearchWriteOwnerUnavailableError):
        port.create_research_experiment(
            ticket_id="rt-1",
            experiment_name="Should Fail Closed",
            strategy_selector={},
            parameter_set={},
            run_config={},
            launch_context={},
        )

    with pytest.raises(ResearchWriteOwnerUnavailableError):
        port.cancel_research_experiment("exp-does-not-matter")


def test_research_artifacts_comparison():
    port = DefaultResearchKnowledgeSourcePort(
        research_artifacts_store={
            "art-1": {
                "artifact_id": "art-1",
                "name": "Model Weights v1",
                "artifact_type": "model",
                "status": "sealed",
                "metrics": {
                    "sharpe_ratio": 1.8,
                    "max_drawdown": -0.15,
                    "annualized_return": 0.22,
                },
                "parameters": {"fast_period": 10},
                "created_at": "2026-08-20T00:00:00Z",
            },
            "art-2": {
                "artifact_id": "art-2",
                "name": "Model Weights v2",
                "artifact_type": "model",
                "status": "sealed",
                "metrics": {
                    "sharpe_ratio": 2.3,
                    "max_drawdown": -0.10,
                    "annualized_return": 0.30,
                },
                "parameters": {"fast_period": 12},
                "created_at": "2026-08-25T00:00:00Z",
            },
        }
    )

    artifacts = port.list_research_artifacts()
    assert len(artifacts) == 2
    assert artifacts[0]["artifact_id"] == "art-2"

    comparison = port.compare_research_artifacts(["art-1", "art-2"])
    assert len(comparison["artifacts"]) == 2
    sharpe_comp = next(c for c in comparison["comparisons"] if c["field_key"] == "metrics.sharpe_ratio")
    assert sharpe_comp["delta"] == 0.5
    assert sharpe_comp["polarity"] == "better"


def test_search_and_source_ops_fallbacks():
    port = DefaultResearchKnowledgeSourcePort()

    # Unconfigured source registry returns typed missing
    registry = port.get_source_connector_registry()
    assert registry["source"] == "missing"
    assert registry["connectors"] == []

    # Unconfigured search ops snapshot returns typed missing
    search_ops = port.get_search_ops_snapshot()
    assert search_ops["source"] == "missing"
    assert search_ops["summary"]["pipeline_run_count"] == 0

    # Unconfigured source ops snapshot returns typed missing
    source_ops = port.get_source_ops_snapshot()
    assert source_ops["source"] == "missing"
    assert source_ops["summary"]["connector_count"] == 0


# -----------------------------------------------------------------------------
# Integration Tests for console_gap/knowledge router with Port
# -----------------------------------------------------------------------------

def test_knowledge_inbox_router_with_port():
    port = DefaultResearchKnowledgeSourcePort(
        research_notes_store={
            "note-1": {
                "note_id": "note-1",
                "title": "Alpha Research Note",
                "body": "Found cross-asset momentum anomaly.",
                "created_at": "2026-08-27T10:00:00Z",
            }
        },
        insight_cards_store={
            "ins-1": {
                "insight_id": "ins-1",
                "summary": "Market regime shift imminent",
                "created_at": "2026-08-28T00:00:00Z",
            }
        },
    )
    port.list_strategy_specs = lambda **kw: []  # type: ignore[method-assign]

    router = create_knowledge_router(
        extract_identity=_fake_extract_identity,
        require_read_role=_fake_require_read_role,
        port=port,
        utc_now=_sample_utc_now,
        dataset_surface_status=_fake_dataset_surface_status,
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/bff/knowledge")
    assert response.status_code == 200
    data = response.json()

    assert "items" in data
    assert len(data["items"]) == 2
    item_types = {item["inboxType"] for item in data["items"]}
    assert "research_note" in item_types
    assert "insight" in item_types

    # Filter by item_type
    note_resp = client.get("/bff/knowledge?item_type=research_note")
    assert note_resp.status_code == 200
    note_data = note_resp.json()
    assert len(note_data["items"]) == 1
    assert note_data["items"][0]["inboxType"] == "research_note"
    assert note_data["items"][0]["title"] == "Alpha Research Note"


def test_knowledge_inbox_router_degraded_when_empty():
    port = DefaultResearchKnowledgeSourcePort()
    port.list_strategy_specs = lambda **kw: []  # type: ignore[method-assign]

    router = create_knowledge_router(
        extract_identity=_fake_extract_identity,
        require_read_role=_fake_require_read_role,
        port=port,
        utc_now=_sample_utc_now,
        dataset_surface_status=_fake_dataset_surface_status,
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/bff/knowledge")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 0
    assert data["meta"]["status"] == "unavailable"


def test_knowledge_inbox_pagination_and_validation():
    notes_store = {
        f"note-{i}": {
            "note_id": f"note-{i}",
            "title": f"Note {i}",
            "body": f"Content {i}",
            "created_at": f"2026-08-{10 + i:02d}T00:00:00Z",
        }
        for i in range(1, 6)
    }
    port = DefaultResearchKnowledgeSourcePort(research_notes_store=notes_store)
    port.list_strategy_specs = lambda **kw: []  # type: ignore[method-assign]

    router = create_knowledge_router(
        extract_identity=_fake_extract_identity,
        require_read_role=_fake_require_read_role,
        port=port,
        utc_now=_sample_utc_now,
        dataset_surface_status=_fake_dataset_surface_status,
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Page size 2, first page
    resp = client.get("/bff/knowledge?page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["page_info"]["has_more"] is True
    next_token = data["page_info"]["next_page_token"]
    assert next_token == "2"

    # Second page
    resp2 = client.get(f"/bff/knowledge?page_size=2&page_token={next_token}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    assert data2["page_info"]["has_more"] is True

    # Invalid page token (non-integer) -> 422
    resp_bad = client.get("/bff/knowledge?page_token=invalid")
    assert resp_bad.status_code == 422

    # Negative page token -> 422
    resp_neg = client.get("/bff/knowledge?page_token=-5")
    assert resp_neg.status_code == 422


def test_search_results_filtering_and_query():
    port = DefaultResearchKnowledgeSourcePort(
        search_documents_store=[
            {
                "result_id": "doc-1",
                "match_type": "ticket",
                "title": "Cross Asset Momentum",
                "excerpt": "Exploring momentum signals across equities and futures.",
                "linked_ticket_id": "rt-101",
                "linked_ticket_status": "open",
                "created_at": "2026-08-25T00:00:00Z",
            },
            {
                "result_id": "doc-2",
                "match_type": "experiment",
                "title": "Mean Reversion Backtest",
                "excerpt": "Backtest on intraday mean reversion strategies.",
                "linked_ticket_id": "rt-102",
                "linked_ticket_status": "closed",
                "created_at": "2026-08-26T00:00:00Z",
            },
        ],
        research_tickets_store={
            "rt-101": {"ticket_id": "rt-101", "status": "open"},
            "rt-102": {"ticket_id": "rt-102", "status": "closed"},
        }
    )

    index = port.get_research_search_index()
    assert index is not None
    assert "ticket" in index["indexed_match_types"]
    assert "experiment" in index["indexed_match_types"]

    # Search with keyword
    results = port.list_research_search_results(query="momentum")
    assert len(results) == 1
    assert results[0]["result_id"] == "doc-1"
    assert results[0]["match_type"] == "ticket"

    # Filter by match_type
    exp_results = port.list_research_search_results(query="", match_type="experiment")
    assert len(exp_results) == 1
    assert exp_results[0]["result_id"] == "doc-2"

    # Filter by status
    open_results = port.list_research_search_results(query="", status="open")
    assert len(open_results) == 1
    assert open_results[0]["result_id"] == "doc-1"


def _health_usage_row(source_id: str) -> dict:
    return {
        "health": {"source_id": source_id, "source_kind": "filing", "status": "ok"},
        "usage_aggregate_30d": {"query_count": 0},
        "recommendation": None,
    }


def _health_usage_payload(rows: list, summary: dict) -> dict:
    return {"source_count": len(rows), "sources": rows, "recommendation_summary": summary}


def _health_usage_port(payload, available: bool = True) -> DefaultResearchKnowledgeSourcePort:
    return DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://source-ingest-service:8000",
        http_get_fn=lambda base_url, path: (available, payload),
    )


def test_source_health_usage_snapshot_valid_populated_and_empty():
    populated = _health_usage_port(
        _health_usage_payload([_health_usage_row("sec")], {"no_action": 1})
    ).get_source_health_usage_snapshot()
    assert populated["source"] == "service_client"
    assert populated["source_count"] == 1
    assert populated["recommendation_summary"] == {"no_action": 1}

    empty = _health_usage_port(
        _health_usage_payload([], {"no_action": 0})
    ).get_source_health_usage_snapshot()
    assert empty["source"] == "service_client"
    assert empty["source_count"] == 0
    assert empty["sources"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": "shape"},
        [],
        None,
        {"source_count": 0, "sources": None, "recommendation_summary": {}},
        {"source_count": 0, "sources": "abc", "recommendation_summary": {}},
        {"source_count": 0, "sources": {"a": 1}, "recommendation_summary": {}},
        {"source_count": 1, "sources": ["row"], "recommendation_summary": {}},
        {"source_count": 1, "sources": [{"source_id": "sec"}], "recommendation_summary": {}},
        {"source_count": 1, "sources": [{**_health_usage_row("sec"), "health": {}}], "recommendation_summary": {}},
        {"source_count": 1, "sources": [{**_health_usage_row("sec"), "recommendation": "x"}], "recommendation_summary": {}},
        {"source_count": 5, "sources": [], "recommendation_summary": {}},
        {"source_count": 0, "sources": [], "recommendation_summary": None},
        {"source_count": 0, "sources": [], "recommendation_summary": "x"},
        {"source_count": 0, "sources": [], "recommendation_summary": {"no_action": "1"}},
    ],
)
def test_source_health_usage_snapshot_rejects_malformed_envelope(payload):
    result = _health_usage_port(payload).get_source_health_usage_snapshot()
    assert result == {
        "source": "unavailable",
        "source_count": 0,
        "sources": [],
        "recommendation_summary": {},
    }


def test_source_health_usage_snapshot_unavailable_and_missing():
    valid = _health_usage_payload([], {})
    assert _health_usage_port(valid, available=False).get_source_health_usage_snapshot()["source"] == "unavailable"
    assert DefaultResearchKnowledgeSourcePort().get_source_health_usage_snapshot()["source"] == "missing"


def test_source_and_search_ops_with_http_client():
    def fake_http_get(base_url: str, path: str):
        if "freshness" in path:
            return True, {"status": "fresh", "within_sla": True, "last_synced_at": "2026-08-28T01:00:00Z"}
        if "pipeline-runs" in path:
            return True, {"runs": [{"run_id": "pr-1", "status": "completed"}], "total": 1}
        if "materialize" in path:
            return True, {"materialized_at": "2026-08-28T01:00:00Z", "document_count": 42}
        if "source-ingest/registry" in path:
            return True, {
                "schema_version": "1.0",
                "connectors": [{"connector_id": "sec-edgar", "status": "healthy"}],
                "provider_examples": ["sec://"],
            }
        if "source-change-proposals" in path:
            return True, {"proposals": [{"proposal_id": "scp-1", "status": "pending"}]}
        if "source-ingest/health-usage-snapshot" in path:
            return True, _health_usage_payload(
                [_health_usage_row("sec")], {"no_action": 1}
            )
        return False, None

    port = DefaultResearchKnowledgeSourcePort(
        search_service_url="http://search-service:8000",
        source_ingest_service_url="http://source-ingest-service:8000",
        http_get_fn=fake_http_get,
    )

    # Search Ops
    search_ops = port.get_search_ops_snapshot()
    assert search_ops["source"] == "service_client"
    assert search_ops["summary"]["freshness_ok"] is True
    assert len(search_ops["pipeline_runs"]) == 1

    # Source Connectors
    registry = port.get_source_connector_registry()
    assert registry["source"] == "service_client"
    assert len(registry["connectors"]) == 1
    assert registry["connectors"][0]["connector_id"] == "sec-edgar"

    # Source Proposals
    proposals = port.get_source_change_proposals(status="pending")
    assert proposals["source"] == "service_client"
    assert len(proposals["proposals"]) == 1

    # Source Health Usage Snapshot
    health = port.get_source_health_usage_snapshot()
    assert health["source"] == "service_client"
    assert health["source_count"] == 1


def _original_workshop_registry_entry(owner_tenant: str = "tenant-dev") -> dict:
    return {
        "registry_id": "reg-strategy-spec-persona-aa060d53ff712cbba704",
        "artifact_type": "strategy_spec",
        "artifact_state": "approved",
        "version": "1.0.0",
        "strategy_id": "strategy-persona-aa060d53ff712cbba704",
        "owner_tenant": owner_tenant,
        "lineage": {
            "source_strategy_spec_id": "strategy-persona-aa060d53ff712cbba704",
            "parent_registry_ids": ["reg-strategy-spec-persona-baseline-aa060d53ff712cbba704"],
            "source_research_job_id": "job-persona-aa060d53ff712cbba704",
        },
        "evaluation_summary": {"risk_level": "medium", "sharpe": 1.42},
        "metadata": {
            "title": "Original Workshop Strategy Spec",
            "persona_id": "persona-132519698e6199e80326",
            "strategy_spec": {
                "strategy_id": "strategy-persona-aa060d53ff712cbba704",
                "spec_version": "1.0",
                "lifecycle_state": "candidate",
                "title": "Original Workshop Strategy Spec",
                "hypothesis": "Cross-session orderflow imbalance mean-reverts.",
                "objective": "Capture intraday reversion with bounded drawdown.",
                "market_scope": {"symbols": ["BTCUSDT"], "frequency": "1h"},
                "execution_profile": {"signal_schema_version": "1.0", "execution_mode_hint": "paper"},
                "evaluation_plan": {"metrics": ["sharpe_ratio"]},
                "governance": {"approval_required": True},
                "metadata": {"tenant_id": owner_tenant, "persona_id": "persona-132519698e6199e80326"},
            },
        },
        "created_at": "2026-10-18T10:00:00Z",
        "updated_at": "2026-10-18T12:00:00Z",
        "last_actor": {"actor_id": "op-workshop"},
    }


def test_original_workshop_strategy_spec_owner_read_preserves_registry_id_and_semver(monkeypatch):
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core import owner_reads

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")

    calls: list[dict] = []

    def fake_http_request_json(url: str, *, method: str = "GET", payload=None, auth_token=None, tenant_id=None, **kwargs):
        calls.append({"url": url, "method": method, "auth_token": auth_token, "tenant_id": tenant_id})
        return [_original_workshop_registry_entry("tenant-dev")]

    monkeypatch.setattr(adapter_base, "http_request_json", fake_http_request_json)

    port = DefaultResearchKnowledgeSourcePort()
    assert port._strategy_specs == {}
    # Mutating _strategy_specs on an uninjected port must never act as a production fallback
    port._strategy_specs["strategy-persona-aa060d53ff712cbba704"] = {"strategy_id": "poisoned-local-cache"}

    tok_auth = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    tok_tenant = owner_reads.selected_tenant.set("tenant-dev")
    try:
        sid = "strategy-persona-aa060d53ff712cbba704"
        reg_id = "reg-strategy-spec-persona-aa060d53ff712cbba704"

        by_reg_id = port.get_strategy_spec_detail(sid, version_selector=reg_id)
        assert by_reg_id is not None
        assert by_reg_id["strategy_id"] == sid
        assert by_reg_id["tenant_id"] == "tenant-dev"
        assert by_reg_id["spec_version_id"] == reg_id
        assert by_reg_id["strategy_spec_registry_id"] == reg_id
        assert by_reg_id["spec_version"] == "1.0.0"
        assert by_reg_id["semantic_version"] == "1.0"
        assert by_reg_id["lifecycle_state"] == "approved"
        assert by_reg_id["parent_spec_version_id"] == "reg-strategy-spec-persona-baseline-aa060d53ff712cbba704"
        assert by_reg_id["persona_ids"] == ["persona-132519698e6199e80326"]

        assert port.get_strategy_spec_detail(sid, version_selector="1.0.0")["spec_version_id"] == reg_id
        assert port.get_strategy_spec_detail(sid, version_selector="1.0")["spec_version_id"] == reg_id
        assert port.get_strategy_spec_detail(sid, version_selector="current")["spec_version_id"] == reg_id
        assert port.get_strategy_spec_detail(sid, version_selector="nonexistent-version") is None

        summary = port.get_strategy_spec(sid)
        assert summary is not None
        assert summary["strategy_id"] == sid
        assert summary["tenant_id"] == "tenant-dev"
        assert summary["spec_version_id"] == reg_id
        assert summary["strategy_spec_registry_id"] == reg_id
        assert summary["spec_version"] == "1.0.0"

        versions = port.list_strategy_spec_versions(sid)
        assert len(versions) == 1
        assert versions[0]["spec_version_id"] == reg_id
        assert versions[0]["spec_version"] == "1.0.0"
        assert versions[0]["parent_spec_version_id"] == "reg-strategy-spec-persona-baseline-aa060d53ff712cbba704"

        assert calls[0] == {
            "url": f"http://registry.internal/api/registry/strategies/{sid}/strategy-specs",
            "method": "GET",
            "auth_token": "workshop-user:operator:tenant-dev",
            "tenant_id": "tenant-dev",
        }
        assert port._strategy_specs["strategy-persona-aa060d53ff712cbba704"] == {"strategy_id": "poisoned-local-cache"}
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_strategy_spec_owner_read_missing_forbidden_and_unavailable(monkeypatch):
    import urllib.error
    from fastapi import HTTPException
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core import owner_reads

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")

    port = DefaultResearchKnowledgeSourcePort()
    sid = "strategy-persona-aa060d53ff712cbba704"

    # 1. Missing caller authorization fails closed with 503
    with pytest.raises(HTTPException) as unauth_exc:
        port.get_strategy_spec_detail(sid)
    assert unauth_exc.value.status_code == 503
    assert unauth_exc.value.detail["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    tok_auth = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    tok_tenant = owner_reads.selected_tenant.set("tenant-dev")
    try:
        # 2. Empty list (200 []) or missing version selector returns None / []; untyped HTTP 404 raises 503
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [])
        assert port.get_strategy_spec_detail(sid) is None
        assert port.get_strategy_spec(sid) is None
        assert port.list_strategy_spec_versions(sid) == []

        monkeypatch.setattr(
            adapter_base,
            "http_request_json",
            lambda *a, **k: [_original_workshop_registry_entry("tenant-dev")],
        )
        assert port.get_strategy_spec_detail(sid, version_selector="missing-version") is None

        def raise_404(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 404, "Not Found", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_404)
        with pytest.raises(HTTPException) as http_404_exc:
            port.get_strategy_spec_detail(sid)
        assert http_404_exc.value.status_code == 503
        assert http_404_exc.value.detail["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert http_404_exc.value.detail["error"]["reason"] == "strategy_registry_http_404"

        # 3. Foreign tenant payload or HTTP 403 raises 403 FORBIDDEN
        monkeypatch.setattr(
            adapter_base,
            "http_request_json",
            lambda *a, **k: [_original_workshop_registry_entry("tenant-other")],
        )
        with pytest.raises(HTTPException) as forbidden_exc:
            port.get_strategy_spec_detail(sid)
        assert forbidden_exc.value.status_code == 403
        assert forbidden_exc.value.detail["error"]["code"] == "FORBIDDEN"

        def raise_403(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 403, "Forbidden", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_403)
        with pytest.raises(HTTPException) as http_403_exc:
            port.get_strategy_spec_detail(sid)
        assert http_403_exc.value.status_code == 403

        # 4. Registry 503 or unconfigured URL raises 503 DEPENDENCY_UNAVAILABLE
        def raise_503(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 503, "Unavailable", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_503)
        with pytest.raises(HTTPException) as http_503_exc:
            port.get_strategy_spec_detail(sid)
        assert http_503_exc.value.status_code == 503
        assert http_503_exc.value.detail["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

        monkeypatch.delenv("PANTHEON_REGISTRY_API_URL", raising=False)
        monkeypatch.delenv("PANTHEON_REGISTRY_URL", raising=False)
        with pytest.raises(HTTPException) as unconf_exc:
            port.get_strategy_spec_detail(sid)
        assert unconf_exc.value.status_code == 503
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_list_strategy_specs_reads_canonical_registry_http_owner_and_ignores_local_get_store(monkeypatch):
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core import owner_reads
    from services.registry import storage as reg_storage

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")

    local_store_calls: list[str] = []

    def _poisoned_get_store():
        local_store_calls.append("get_store")
        raise BaseException("list_strategy_specs must never read process-local services.registry.storage.get_store()")

    monkeypatch.setattr(reg_storage, "get_store", _poisoned_get_store)

    v1 = _original_workshop_registry_entry("tenant-dev")
    v2 = json.loads(json.dumps(v1))
    v2["registry_id"] = "reg-strategy-spec-persona-aa060d53ff712cbba704-v2"
    v2["version"] = "1.1.0"
    v2["artifact_state"] = "candidate"
    v2["updated_at"] = "2026-10-18T14:00:00Z"
    v2["metadata"]["strategy_spec"]["spec_version"] = "1.1"
    v2["metadata"]["strategy_spec"]["title"] = "Original Workshop Strategy Spec v2"

    retired = _original_workshop_registry_entry("tenant-dev")
    retired["registry_id"] = "reg-strategy-spec-retired-001"
    retired["strategy_id"] = "strategy-retired-001"
    retired["artifact_state"] = "retired"
    retired["metadata"]["strategy_spec"]["strategy_id"] = "strategy-retired-001"
    retired["metadata"]["strategy_spec"]["lifecycle_state"] = "retired"

    calls: list[dict] = []

    def fake_http_request_json(url: str, *, method: str = "GET", payload=None, auth_token=None, tenant_id=None, **kwargs):
        calls.append({"url": url, "method": method, "auth_token": auth_token, "tenant_id": tenant_id})
        return [{"entry": v2}, {"entry": v1}, {"entry": retired}]

    monkeypatch.setattr(adapter_base, "http_request_json", fake_http_request_json)

    port = DefaultResearchKnowledgeSourcePort()
    port._strategy_specs["poisoned-local"] = {"strategy_id": "poisoned-local", "title": "Poisoned Local Cache"}

    tok_auth = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    tok_tenant = owner_reads.selected_tenant.set("tenant-dev")
    try:
        items = port.list_strategy_specs()
        assert len(items) == 1
        item = items[0]
        assert item["strategy_id"] == "strategy-persona-aa060d53ff712cbba704"
        assert item["current_spec_version_id"] == "reg-strategy-spec-persona-aa060d53ff712cbba704-v2"
        assert item["current_spec_version"] == "1.1.0"
        assert item["version_count"] == 2
        assert item["lifecycle_state"] == "candidate"
        assert item["title"] == "Original Workshop Strategy Spec v2"
        assert item["persona_ids"] == ["persona-132519698e6199e80326"]
        assert item["last_modified_at"] == "2026-10-18T14:00:00Z"
        assert calls[0] == {
            "url": "http://registry.internal/api/registry/strategy-specs",
            "method": "GET",
            "auth_token": "workshop-user:operator:tenant-dev",
            "tenant_id": "tenant-dev",
        }

        with_retired = port.list_strategy_specs(include_retired=True)
        assert [i["strategy_id"] for i in with_retired] == [
            "strategy-persona-aa060d53ff712cbba704",
            "strategy-retired-001",
        ]
        assert port.list_strategy_specs(persona_id="persona-132519698e6199e80326")[0]["strategy_id"] == (
            "strategy-persona-aa060d53ff712cbba704"
        )
        assert port.list_strategy_specs(persona_id="persona-other") == []
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_list_strategy_specs_fails_closed_on_missing_auth_unconfigured_url_foreign_tenant_and_malformed_payload(monkeypatch):
    import urllib.error
    from fastapi import HTTPException
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core import owner_reads

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")

    port = DefaultResearchKnowledgeSourcePort()

    with pytest.raises(HTTPException) as unauth_exc:
        port.list_strategy_specs()
    assert unauth_exc.value.status_code == 503
    assert unauth_exc.value.detail["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    tok_auth = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    tok_tenant = owner_reads.selected_tenant.set("tenant-dev")
    try:
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [])
        assert port.list_strategy_specs() == []

        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [_original_workshop_registry_entry("tenant-other")])
        with pytest.raises(HTTPException) as forbidden_exc:
            port.list_strategy_specs()
        assert forbidden_exc.value.status_code == 403
        assert forbidden_exc.value.detail["error"]["code"] == "FORBIDDEN"

        for bad_payload in (
            {"items": []},
            ["not-a-dict"],
            [{"entry": "not-a-dict"}],
            [{**_original_workshop_registry_entry("tenant-dev"), "artifact_type": "execution_bundle"}],
            [{**_original_workshop_registry_entry("tenant-dev"), "strategy_id": "", "metadata": {}}],
            [{**_original_workshop_registry_entry("tenant-dev"), "registry_id": "", "metadata": {}}],
            [{**_original_workshop_registry_entry("tenant-dev"), "version": "", "metadata": {}}],
        ):
            monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, payload=bad_payload, **k: payload)
            with pytest.raises(HTTPException) as bad_exc:
                port.list_strategy_specs()
            assert bad_exc.value.status_code == 503
            assert bad_exc.value.detail["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

        def raise_503(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 503, "Unavailable", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_503)
        with pytest.raises(HTTPException) as http_503_exc:
            port.list_strategy_specs()
        assert http_503_exc.value.status_code == 503

        monkeypatch.delenv("PANTHEON_REGISTRY_API_URL", raising=False)
        monkeypatch.delenv("PANTHEON_REGISTRY_URL", raising=False)
        with pytest.raises(HTTPException) as unconf_exc:
            port.list_strategy_specs()
        assert unconf_exc.value.status_code == 503
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)




def _multi_version_binding_store(strategy_count: int = 12, versions: int = 6) -> dict:
    store: dict = {}
    for n in range(strategy_count):
        sid = f"strat-bind-{n}"
        rows = []
        for v in range(1, versions + 1):
            rows.append(
                {
                    "spec_version_id": f"{sid}-v{v}",
                    "spec_version": f"v{v}",
                    "lifecycle_state": "retired" if (n % 5 == 0 and v == versions) else "approved",
                    "created_at": f"2026-08-{10 + v:02d}T00:00:00Z",
                    # older versions bind a different persona; only the current one counts
                    "persona_ids": [f"p-{n % 3}", f"p-{n % 3}", " "] if v == versions else ["p-old"],
                }
            )
        store[sid] = {"strategy_id": sid, "title": sid, "versions": rows}
    return store


def test_count_strategy_persona_bindings_matches_list_and_skips_version_normalization(monkeypatch):
    port = DefaultResearchKnowledgeSourcePort(strategy_specs_store=_multi_version_binding_store())
    expected: dict = {}
    for item in port.list_strategy_specs():
        for pid in set(item["persona_ids"]):
            expected[pid] = expected.get(pid, 0) + 1
    assert expected and "p-old" not in expected

    normalized = {"n": 0}
    original = DefaultResearchKnowledgeSourcePort._kw05_normalize_version_candidate.__func__

    def counting(cls, *args, **kwargs):
        normalized["n"] += 1
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(DefaultResearchKnowledgeSourcePort, "_kw05_normalize_version_candidate", classmethod(counting))
    assert port.count_strategy_persona_bindings() == expected
    assert normalized["n"] == 0
    port.list_strategy_specs()
    assert normalized["n"] == 9  # full list normalizes every non-retired current version
