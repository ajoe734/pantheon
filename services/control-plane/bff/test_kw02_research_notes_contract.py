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

bff_main = load_isolated_composition("kw02")


OPERATOR_TOKEN = "Bearer op-2:operator"
NOTE_ID = "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890"


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
        "PANTHEON_BFF_RESEARCH_NOTES_STORE": os.environ.get("PANTHEON_BFF_RESEARCH_NOTES_STORE"),
        "PANTHEON_BFF_RESEARCH_TICKET_STORE": os.environ.get("PANTHEON_BFF_RESEARCH_TICKET_STORE"),
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE"),
        "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE": os.environ.get("PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"),
        "PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE"),
        "PANTHEON_BFF_STRATEGY_SPEC_STORE": os.environ.get("PANTHEON_BFF_STRATEGY_SPEC_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stores = {
            "notes": root / "research_notes.json",
            "tickets": root / "research_tickets.json",
            "personas": root / "personas.json",
            "memory": root / "institutional_memory.json",
            "evidence": root / "evidence_refs.json",
            "strategy": root / "strategy_specs.json",
        }
        stores["notes"].write_text("{}", encoding="utf-8")
        stores["tickets"].write_text(
            json.dumps(
                {
                    "tkt-12345678-1234-1234-1234-1234567890ab": {
                        "ticket_id": "tkt-12345678-1234-1234-1234-1234567890ab",
                        "title": "Latency spike investigation",
                        "status": "open",
                        "priority": "high",
                        "owner": "op-2",
                        "created_at": "2026-04-20T10:00:00Z",
                        "updated_at": "2026-04-20T10:00:00Z",
                        "closed_at": None,
                        "archived_at": None,
                        "lifecycle_history": [],
                        "linked_experiments": [],
                        "linked_artifacts": [],
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        stores["personas"].write_text(
            json.dumps(
                {
                    "persona-HAWK-001": {
                        "persona_id": "persona-HAWK-001",
                        "name": "HAWK (Persona)",
                        "mandate": "risk",
                        "lifecycle_state": "active",
                        "created_at": "2026-04-10T00:00:00Z",
                        "updated_at": "2026-04-20T00:00:00Z",
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
                        "source_event": {
                            "type": "research_ticket_closed",
                            "id": "tkt-12345678-1234-1234-1234-1234567890ab",
                            "href": "/research/tickets/tkt-12345678-1234-1234-1234-1234567890ab",
                        },
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
        stores["evidence"].write_text(
            json.dumps(
                {
                    "evref-11111111-2222-3333-4444-555555555555": {
                        "ref_id": "evref-11111111-2222-3333-4444-555555555555",
                        "display_label": "Latency histogram",
                        "route_href": "/knowledge/evidence/evref-11111111-2222-3333-4444-555555555555",
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

        os.environ["PANTHEON_BFF_RESEARCH_NOTES_STORE"] = str(stores["notes"])
        os.environ["PANTHEON_BFF_RESEARCH_TICKET_STORE"] = str(stores["tickets"])
        os.environ["PANTHEON_BFF_PERSONA_REGISTRY_STORE"] = str(stores["personas"])
        os.environ["PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"] = str(stores["memory"])
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(stores["evidence"])
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


def test_kw02_list_and_detail_return_contract_shape_with_degraded_fallback() -> None:
    with _seeded_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/notes",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"research_note_list": "degraded"}
        assert payload["pagination"]["page_size"] == 20
        first_note = payload["notes"][0]
        assert sorted(first_note.keys()) == [
            "attachment",
            "created_at",
            "excerpt",
            "note_id",
            "owner_ref",
            "route_href",
            "tags",
            "title",
            "updated_at",
        ]
        assert first_note["route_href"] == f"/knowledge/notes/{first_note['note_id']}"

        detail_response = client.get(
            f"/api/v1/knowledge/notes/{NOTE_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["note_id"] == NOTE_ID
        assert detail["attachment"]["route_href"] == "/research/tickets/tkt-7a8b9c0d-1234-5678-abcd-ef0123456789"
        assert detail["linked_evidence_refs"][0]["resolution_state"] == "resolved"
        assert detail["linked_evidence_refs"][1]["resolution_state"] == "unresolved"
        assert detail["linked_memory_anchors"][0]["route_href"] == "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123"
        assert detail["meta"]["surfaces"] == {
            "research_note_detail": "degraded",
            "evidence_links": "degraded",
            "memory_anchors": "degraded",
        }


def test_kw02_create_and_read_round_trip_on_service_backed_store() -> None:
    with _service_backed_client() as client:
        create_response = client.post(
            "/api/v1/knowledge/notes",
            headers={"Authorization": OPERATOR_TOKEN},
            json={
                "title": "Opening-auction latency note",
                "body": "Observed latency spike during the opening auction.",
                "attachment_type": "research_ticket",
                "attachment_ref": "tkt-12345678-1234-1234-1234-1234567890ab",
                "tags": ["latency", "opening-auction"],
                "linked_evidence_refs": ["evref-11111111-2222-3333-4444-555555555555"],
                "linked_memory_anchors": ["mem-11111111-2222-3333-4444-555555555555"],
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        note_id = created["note_id"]
        assert note_id.startswith("note-")
        assert created["route_href"] == f"/knowledge/notes/{note_id}"

        list_response = client.get(
            "/api/v1/knowledge/notes?attachment_type=research_ticket&attachment_ref=tkt-12345678-1234-1234-1234-1234567890ab",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        list_payload = list_response.json()

        assert list_payload["meta"]["surfaces"] == {"research_note_list": "ok"}
        assert [item["note_id"] for item in list_payload["notes"]] == [note_id]
        assert list_payload["notes"][0]["owner_ref"]["owner_id"] == "op-2"
        assert list_payload["notes"][0]["attachment"]["display_label"] == "Latency spike investigation"

        detail_response = client.get(
            f"/api/v1/knowledge/notes/{note_id}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["attachment"]["route_href"] == "/research/tickets/tkt-12345678-1234-1234-1234-1234567890ab"
        assert detail["linked_evidence_refs"] == [
            {
                "ref_id": "evref-11111111-2222-3333-4444-555555555555",
                "resolution_state": "resolved",
                "display_label": "Latency histogram",
                "route_href": "/knowledge/evidence/evref-11111111-2222-3333-4444-555555555555",
            }
        ]
        assert detail["linked_memory_anchors"] == [
            {
                "entry_id": "mem-11111111-2222-3333-4444-555555555555",
                "headline": "Latency surge pattern",
                "knowledge_type": "regime_pattern",
                "lifecycle_status": "active",
                "route_href": "/knowledge/memory/mem-11111111-2222-3333-4444-555555555555",
            }
        ]
        assert detail["meta"]["surfaces"] == {
            "research_note_detail": "ok",
            "evidence_links": "ok",
            "memory_anchors": "ok",
        }


def test_kw02_list_empty_service_store_reports_available_surface() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/notes",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["notes"] == []
        assert payload["pagination"] == {
            "page_size": 20,
            "next_page_token": None,
            "has_more": False,
        }
        assert payload["meta"]["surfaces"] == {"research_note_list": "ok"}


def test_kw02_list_empty_filter_still_reports_available_surface_on_service_store() -> None:
    with _service_backed_client() as client:
        create_response = client.post(
            "/api/v1/knowledge/notes",
            headers={"Authorization": OPERATOR_TOKEN},
            json={
                "title": "Latency note",
                "body": "Observed latency spike during the opening auction.",
                "attachment_type": "research_ticket",
                "attachment_ref": "tkt-12345678-1234-1234-1234-1234567890ab",
                "tags": ["latency"],
            },
        )
        assert create_response.status_code == 201, create_response.text

        list_response = client.get(
            "/api/v1/knowledge/notes?tags=does-not-match",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["notes"] == []
        assert payload["pagination"] == {
            "page_size": 20,
            "next_page_token": None,
            "has_more": False,
        }
        assert payload["meta"]["surfaces"] == {"research_note_list": "ok"}


def test_kw02_create_rejects_invalid_memory_anchor_and_missing_attachment() -> None:
    with _service_backed_client() as client:
        invalid_anchor_response = client.post(
            "/api/v1/knowledge/notes",
            headers={"Authorization": OPERATOR_TOKEN},
            json={
                "body": "Anchor should fail.",
                "attachment_type": "free_standing",
                "attachment_ref": None,
                "linked_memory_anchors": ["mem-does-not-match-format"],
            },
        )
        assert invalid_anchor_response.status_code == 400, invalid_anchor_response.text

        missing_attachment_response = client.post(
            "/api/v1/knowledge/notes",
            headers={"Authorization": OPERATOR_TOKEN},
            json={
                "body": "Attachment should fail.",
                "attachment_type": "research_ticket",
                "attachment_ref": "tkt-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            },
        )
        assert missing_attachment_response.status_code == 422, missing_attachment_response.text
