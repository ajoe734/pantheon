"""Tests for ResearchKnowledgeSourcePort and console_gap/knowledge router integration."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from domain_ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    ResearchKnowledgeSourcePort,
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
        }
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
    port = DefaultResearchKnowledgeSourcePort()

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
        }
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
