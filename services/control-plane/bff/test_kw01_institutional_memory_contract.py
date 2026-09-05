from __future__ import annotations

import os
import sys
import tempfile
import importlib.util
from contextlib import contextmanager
from pathlib import Path
import json

from fastapi.testclient import TestClient

_MODULE_DIR = Path(__file__).resolve().parent
from services.control_plane.bff.tests.knowledge_read_port_fixtures import (  # noqa: E402
    create_environment_knowledge_read_ports,
    create_seeded_knowledge_read_ports,
)


from services.control_plane.bff.tests.isolated_composition import load_isolated_composition

bff_main = load_isolated_composition("kw01")


OPERATOR_TOKEN = "Bearer op-2:operator"
ENTRY_ID = "mem-22222222-2222-2222-2222-222222222222"


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
        "BFF_READ_SURFACE_STATE": os.environ.get("BFF_READ_SURFACE_STATE"),
        "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE": os.environ.get(
            "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"
        ),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        memory_store = root / "institutional_memory.json"
        memory_store.write_text(
            json.dumps(
                {
                    ENTRY_ID: {
                        "entry_id": ENTRY_ID,
                        "knowledge_type": "research_finding",
                        "content": {
                            "headline": "Service-backed entry wins over local fallback",
                            "body": "Used to verify KW-01 reads backend-owned institutional memory first.",
                            "structured_payload": {"confidence": "high"},
                            "tags": ["service", "memory"],
                        },
                        "source_event": {
                            "type": "post_incident_review",
                            "id": "inc-2026-04-05-001",
                            "href": "/operator/post-incident-review?incident=inc-2026-04-05-001",
                        },
                        "contributing_persona_ids": ["persona-alpha", "persona-risk-chief"],
                        "written_at": "2026-04-20T03:00:00Z",
                        "write_authority": "research-svc",
                        "scope": {"type": "strategy_family", "filter": "momentum"},
                        "lifecycle": {"status": "active", "superseded_by": None},
                        "usage": {"reuse_count": 7, "last_cited_at": "2026-04-20T03:05:00Z"},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.environ["PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"] = str(memory_store)

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


@contextmanager
def _memory_service_data_dir_client():
    tracked_env = {
        "PANTHEON_MEMORY_DATA_DIR": os.environ.get("PANTHEON_MEMORY_DATA_DIR"),
        "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE": os.environ.get(
            "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE"
        ),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        memory_dir = root / "memory-data"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "institutional_memory_entries.json").write_text(
            json.dumps(
                [
                    {
                        "entry_id": "mem-33333333-3333-3333-3333-333333333333",
                        "knowledge_type": "policy_precedent",
                        "content": {
                            "headline": "Resolved from memory service data dir",
                            "body": "Used to verify BFF can discover the canonical memory store path.",
                            "structured_payload": {"owner": "memory-service"},
                            "tags": ["service-dir", "memory"],
                        },
                        "source_event": {
                            "type": "governance_review_closed",
                            "id": "gr-001",
                            "href": "/governance-review-queue/gr-001",
                        },
                        "contributing_persona_ids": ["persona-risk-chief"],
                        "written_at": "2026-04-20T04:10:00Z",
                        "write_authority": "governance-svc",
                        "scope": {"type": "system_wide", "filter": None},
                        "lifecycle": {"status": "active", "superseded_by": None},
                        "usage": {"reuse_count": 2, "last_cited_at": "2026-04-20T04:20:00Z"},
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        os.environ["PANTHEON_MEMORY_DATA_DIR"] = str(memory_dir)
        os.environ.pop("PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE", None)

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


def test_kw01_institutional_memory_list_returns_published_contract_shape() -> None:
    with _service_backed_client() as client:
        response = client.get(
            "/api/v1/knowledge/memory",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert sorted(payload.keys()) == ["entries", "meta", "pagination"]
        assert payload["pagination"]["page"] == 1
        assert payload["pagination"]["page_size"] == 20
        assert payload["meta"]["surfaces"] == {"memory_list": "ok"}

        first_entry = payload["entries"][0]
        assert first_entry["entry_id"] == ENTRY_ID
        assert sorted(first_entry.keys()) == [
            "entry_id",
            "headline",
            "is_superseded",
            "knowledge_type",
            "reuse_count",
            "route_href",
            "scope",
            "scope_filter",
            "tags",
            "write_authority",
            "written_at",
        ]
        assert first_entry["route_href"] == f"/knowledge/memory/{first_entry['entry_id']}"


def test_kw01_institutional_memory_detail_returns_published_contract_shape() -> None:
    with _service_backed_client() as client:
        response = client.get(
            f"/api/v1/knowledge/memory/{ENTRY_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["entry_id"] == ENTRY_ID
        assert (
            payload["source_event"]["href"]
            == "/operator/post-incident-review?incident=inc-2026-04-05-001"
        )
        assert payload["lifecycle"] == {"status": "active", "superseded_by": None}
        assert payload["meta"]["surfaces"] == {
            "entry_detail": "ok",
            "source_context": "ok",
        }
        assert sorted(payload.keys()) == [
            "content",
            "contributing_persona_ids",
            "entry_id",
            "knowledge_type",
            "lifecycle",
            "meta",
            "scope",
            "source_event",
            "usage",
            "write_authority",
            "written_at",
        ]


def test_kw01_service_backed_reads_override_seeded_snapshot() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/memory",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text

        payload = list_response.json()
        assert [item["entry_id"] for item in payload["entries"]] == ["mem-22222222-2222-2222-2222-222222222222"]
        assert payload["meta"]["surfaces"] == {"memory_list": "ok"}

        detail_response = client.get(
            "/api/v1/knowledge/memory/mem-22222222-2222-2222-2222-222222222222",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text

        detail = detail_response.json()
        assert detail["content"]["headline"] == "Service-backed entry wins over local fallback"
        assert detail["entry_id"] == ENTRY_ID
        assert detail["meta"]["surfaces"] == {
            "entry_detail": "ok",
            "source_context": "ok",
        }


def test_kw01_reads_memory_service_store_via_data_dir() -> None:
    with _memory_service_data_dir_client() as client:
        response = client.get(
            "/api/v1/knowledge/memory",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert [item["entry_id"] for item in payload["entries"]] == ["mem-33333333-3333-3333-3333-333333333333"]
        assert payload["meta"]["surfaces"] == {"memory_list": "ok"}


def test_kw01_degraded_surface_uses_service_truth_without_local_snapshot_fallback() -> None:
    previous = os.environ.get("BFF_READ_SURFACE_STATE")
    os.environ["BFF_READ_SURFACE_STATE"] = "degraded"
    try:
        with _service_backed_client() as client:
            response = client.get(
                f"/api/v1/knowledge/memory/{ENTRY_ID}",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["entry_id"] == ENTRY_ID
            assert payload["meta"]["surfaces"] == {
                "entry_detail": "degraded",
                "source_context": "degraded",
            }
    finally:
        if previous is None:
            os.environ.pop("BFF_READ_SURFACE_STATE", None)
        else:
            os.environ["BFF_READ_SURFACE_STATE"] = previous


def test_kw01_unavailable_without_service_store_even_if_local_snapshot_is_seeded() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/knowledge/memory",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["entries"] == []
        assert payload["pagination"] == {
            "total_count": 0,
            "page": 1,
            "page_size": 20,
            "total_pages": 0,
        }
        assert payload["meta"]["surfaces"] == {"memory_list": "unavailable"}


def test_kw01_detail_returns_404_without_service_store_even_if_local_snapshot_is_seeded() -> None:
    with _seeded_client() as client:
        response = client.get(
            f"/api/v1/knowledge/memory/{ENTRY_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 404, response.text
        payload = response.json()
        assert payload["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert payload["error"]["details"]["entry_id"] == ENTRY_ID
