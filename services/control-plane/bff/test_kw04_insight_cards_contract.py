from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

_MODULE_DIR = Path(__file__).resolve().parent
from services.control_plane.bff.tests.knowledge_read_port_fixtures import (  # noqa: E402
    create_environment_knowledge_read_ports,
    create_seeded_knowledge_read_ports,
)


from services.control_plane.bff.tests.isolated_composition import load_isolated_composition

bff_main = load_isolated_composition("kw04")


OPERATOR_TOKEN = "Bearer op-2:operator"
ACTIVE_INSIGHT_ID = "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567"
SUPERSEDED_INSIGHT_ID = "ins-c9e0f1a2-3b4c-5d6e-7f80-91a2b3c4d5e6"


@contextmanager
def _seeded_client():
    original_store = bff_main.read_store
    bff_main.read_store = create_seeded_knowledge_read_ports()
    client = TestClient(bff_main.app)
    try:
        yield client
    finally:
        bff_main.read_store = original_store


@contextmanager
def _service_backed_client():
    tracked_env = {
        "PANTHEON_BFF_INSIGHT_CARD_STORE": os.environ.get("PANTHEON_BFF_INSIGHT_CARD_STORE"),
        "PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE"),
        "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE": os.environ.get("PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"),
        "PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE": os.environ.get("PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE"),
        "PANTHEON_BFF_STRATEGY_SPEC_STORE": os.environ.get("PANTHEON_BFF_STRATEGY_SPEC_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stores = {
            "insights": root / "insight_cards.json",
            "evidence": root / "evidence_refs.json",
            "memory": root / "institutional_memory.json",
            "experiments": root / "research_experiments.json",
            "strategy": root / "strategy_specs.json",
        }
        stores["insights"].write_text(
            json.dumps(
                {
                    ACTIVE_INSIGHT_ID: {
                        "insight_id": ACTIVE_INSIGHT_ID,
                        "summary": "Momentum decay strengthens after volatility breaks and remains sensitive to rebalance cadence.",
                        "scope": "strategy",
                        "scope_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "status": "active",
                        "superseded_by_id": None,
                        "confidence": {
                            "score": 0.82,
                            "label": "high",
                            "basis": "Supported by two primary evidence refs and one completed experiment.",
                        },
                        "tags": ["momentum", "volatility-regime"],
                        "source_ref": "agg-ref:test-active",
                        "supporting_evidence_refs": [
                            {
                                "ref_id": "evref-11111111-2222-3333-4444-555555555555",
                                "source_document_title": "Latency histogram",
                                "link_type": "supporting_evidence",
                                "credibility_tier": "primary",
                                "resolved_link": {
                                    "availability": "available",
                                    "route_href": "/knowledge/evidence/evref-11111111-2222-3333-4444-555555555555",
                                    "display_label": "View evidence reference",
                                    "open_in_new_tab": False,
                                },
                            }
                        ],
                        "linked_sources": [
                            {
                                "entity_type": "experiment",
                                "entity_ref": "exp-20260419-012",
                                "display_label": "Momentum decay replay on March volatility cluster",
                                "route_href": "/research/experiments/exp-20260419-012",
                                "relationship_note": "Primary aggregation input",
                            },
                            {
                                "entity_type": "memory_entry",
                                "entity_ref": "mem-11111111-2222-3333-4444-555555555555",
                                "display_label": "Latency surge pattern",
                                "route_href": "/knowledge/memory/mem-11111111-2222-3333-4444-555555555555",
                                "relationship_note": "Corroborating memory entry",
                            },
                        ],
                        "aggregation_provenance": {
                            "memory_entry_count": 1,
                            "note_count": 0,
                            "evidence_ref_count": 1,
                            "primary_evidence_count": 1,
                            "aggregated_at": "2026-04-20T10:15:00Z",
                            "aggregation_version": "v3.0.0",
                        },
                        "created_at": "2026-04-20T10:15:00Z",
                        "updated_at": "2026-04-20T10:15:00Z",
                    },
                    SUPERSEDED_INSIGHT_ID: {
                        "insight_id": SUPERSEDED_INSIGHT_ID,
                        "summary": "Older momentum synthesis retained only for supersession traceability.",
                        "scope": "strategy",
                        "scope_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "status": "superseded",
                        "superseded_by_id": ACTIVE_INSIGHT_ID,
                        "confidence": {
                            "score": 0.41,
                            "label": "low",
                            "basis": "Legacy synthesis has fewer corroborating inputs.",
                        },
                        "tags": ["momentum"],
                        "source_ref": "agg-ref:test-superseded",
                        "supporting_evidence_refs": [],
                        "linked_sources": [
                            {
                                "entity_type": "strategy_spec",
                                "entity_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                                "display_label": "Momentum Regime Response v4",
                                "route_href": "/knowledge/strategy-specs/strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                                "relationship_note": "Legacy scoped insight",
                            }
                        ],
                        "aggregation_provenance": {
                            "memory_entry_count": 0,
                            "note_count": 0,
                            "evidence_ref_count": 0,
                            "primary_evidence_count": 0,
                            "aggregated_at": "2026-03-15T09:00:00Z",
                            "aggregation_version": "v1.0.0",
                        },
                        "created_at": "2026-03-15T09:00:00Z",
                        "updated_at": "2026-03-15T09:00:00Z",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        stores["evidence"].write_text(
            json.dumps(
                {
                    "evref-11111111-2222-3333-4444-555555555555": {
                        "ref_id": "evref-11111111-2222-3333-4444-555555555555",
                        "display_label": "Latency histogram",
                        "source_document": {
                            "title": "Latency histogram",
                        },
                        "link_type": "supporting_evidence",
                        "credibility": {"tier": "primary", "verified": True},
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/knowledge/evidence/evref-11111111-2222-3333-4444-555555555555",
                            "display_label": "View evidence reference",
                            "open_in_new_tab": False,
                        },
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        stores["memory"].write_text(
            json.dumps(
                {
                    "mem-11111111-2222-3333-4444-555555555555": {
                        "entry_id": "mem-11111111-2222-3333-4444-555555555555",
                        "knowledge_type": "regime_pattern",
                        "content": {
                            "headline": "Latency surge pattern",
                            "body": "Known opening-auction latency burst pattern.",
                            "structured_payload": {},
                            "tags": ["latency"],
                        },
                        "source_event": {"type": "research_ticket_closed", "id": "tkt-1", "href": "/research/tickets/tkt-1"},
                        "contributing_persona_ids": ["persona-HAWK-001"],
                        "written_at": "2026-04-20T10:15:00Z",
                        "write_authority": "research-svc",
                        "scope": {"type": "strategy_family", "filter": "momentum"},
                        "lifecycle": {"status": "active", "superseded_by": None},
                        "usage": {"reuse_count": 1, "last_cited_at": "2026-04-20T10:15:00Z"},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        stores["experiments"].write_text(
            json.dumps(
                {
                    "exp-20260419-012": {
                        "experiment_id": "exp-20260419-012",
                        "ticket_id": "rt-20260419-007",
                        "experiment_name": "Momentum decay replay on March volatility cluster",
                        "status": "completed",
                        "queued_at": "2026-04-19T19:00:00Z",
                        "started_at": "2026-04-19T19:03:00Z",
                        "completed_at": "2026-04-19T20:15:00Z",
                        "progress": {"percent": 100, "phase": "aggregation", "message": "Aggregation complete."},
                        "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-short-halflife"},
                        "parameter_set": {},
                        "run_config": {},
                        "launch_context": {"analysis_refs": []},
                        "validation_warnings": [],
                        "artifact_ids": [],
                        "failure": {"reason_code": None, "message": None},
                        "allowedActions": {"canCancel": False},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        stores["strategy"].write_text(
            json.dumps(
                {
                    "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a": {
                        "strategy_id": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "title": "Momentum Regime Response v4",
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        os.environ["PANTHEON_BFF_INSIGHT_CARD_STORE"] = str(stores["insights"])
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(stores["evidence"])
        os.environ["PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"] = str(stores["memory"])
        os.environ["PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE"] = str(stores["experiments"])
        os.environ["PANTHEON_BFF_STRATEGY_SPEC_STORE"] = str(stores["strategy"])

        original_store = bff_main.read_store
        bff_main.read_store = create_environment_knowledge_read_ports()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_kw04_list_and_detail_return_contract_shape_with_degraded_fallback() -> None:
    with _seeded_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/insights",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"insight_cards": "degraded"}
        assert payload["pagination"]["page_size"] == 20
        assert payload["filter_metadata"]["total_active_count"] == 2
        first_card = payload["insight_cards"][0]
        assert first_card["insight_id"] == ACTIVE_INSIGHT_ID
        assert sorted(first_card.keys()) == [
            "aggregated_at",
            "confidence",
            "evidence_count",
            "insight_id",
            "primary_evidence_count",
            "route_href",
            "scope",
            "scope_ref",
            "status",
            "summary",
            "superseded_by_id",
            "tags",
        ]

        detail_response = client.get(
            f"/api/v1/knowledge/insights/{ACTIVE_INSIGHT_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["scope_context"]["route_href"] == "/knowledge/strategy-specs/strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a"
        assert detail["supporting_evidence_refs"][0]["resolved_link"]["availability"] == "available"
        assert detail["linked_sources"][0]["entity_type"] == "experiment"
        assert detail["meta"]["surfaces"] == {
            "insight_card_detail": "degraded",
            "supporting_evidence_refs": "degraded",
            "linked_sources": "degraded",
        }


def test_kw04_service_backed_filters_preserve_backend_owned_taxonomy_and_supersession() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/insights?tag=momentum&linked_entity_type=experiment&confidence_min=0.8",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"insight_cards": "ok"}
        assert [item["insight_id"] for item in payload["insight_cards"]] == [ACTIVE_INSIGHT_ID]
        assert payload["filter_metadata"]["linked_entity_types"][0]["value"] == "experiment"

        detail_response = client.get(
            f"/api/v1/knowledge/insights/{SUPERSEDED_INSIGHT_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["superseded_by"] == {
            "insight_id": ACTIVE_INSIGHT_ID,
            "summary": "Momentum decay strengthens after volatility breaks and remains sensitive to rebalance cadence.",
            "route_href": f"/knowledge/insights/{ACTIVE_INSIGHT_ID}",
        }
        assert detail["meta"]["surfaces"] == {
            "insight_card_detail": "ok",
            "supporting_evidence_refs": "ok",
            "linked_sources": "ok",
        }


def test_kw04_list_rejects_linked_entity_ref_without_type() -> None:
    with _service_backed_client() as client:
        response = client.get(
            "/api/v1/knowledge/insights?linked_entity_ref=exp-20260419-012",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 400, response.text
        payload = response.json()
        assert payload["error"]["details"]["precondition_failed"] == "linked_entity_ref"
