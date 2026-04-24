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


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


bff_main = _load_module("bff_main_kw03_test_module", _MODULE_DIR / "main.py")
read_store_module = _load_module("bff_read_store_kw03_test_module", _MODULE_DIR / "read_store.py")
ReadSurfaceStore = read_store_module.ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"
FALLBACK_REF_ID = "evref-c3d4e5f6-a7b8-9012-cdef-012345678901"
SERVICE_REF_ID = "evref-20000000-1111-2222-3333-444444444444"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


@contextmanager
def _service_backed_client():
    tracked_env = {
        "PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        evidence_store.write_text(
            json.dumps(
                {
                    "evref-10000000-1111-2222-3333-444444444444": {
                        "ref_id": "evref-10000000-1111-2222-3333-444444444444",
                        "source_document": {
                            "title": "Volatility Regime Analysis Q1 2026",
                            "source_type": "external_paper",
                            "source_ref": "external://arxiv.org/abs/2026.12345",
                            "excerpt": "Independent volatility-regime analysis used as a background citation.",
                            "storage_preview": {
                                "available": False,
                                "preview_type": "unavailable",
                                "preview_token": None,
                            },
                            "captured_at": "2026-04-01T14:00:00Z",
                            "captured_by": "Operator: Alice Chen",
                        },
                        "link_type": "citation",
                        "credibility": {
                            "tier": "secondary",
                            "verified": False,
                            "last_verified_at": None,
                            "verification_method": None,
                        },
                        "linked_object_summary": {
                            "entity_type": "strategy_spec",
                            "entity_ref": "strat-99999999-aaaa-bbbb-cccc-dddddddddddd",
                            "display_label": "Momentum v2.1 Strategy Spec",
                        },
                        "resolved_link": {
                            "availability": "external",
                            "route_href": "https://arxiv.org/abs/2026.12345",
                            "display_label": "Open external paper",
                            "open_in_new_tab": True,
                        },
                        "linked_decisions": [
                            {
                                "entity_type": "strategy_spec",
                                "entity_ref": "strat-99999999-aaaa-bbbb-cccc-dddddddddddd",
                                "display_label": "Momentum v2.1 Strategy Spec",
                                "route_href": "/knowledge/strategy-specs/strat-99999999-aaaa-bbbb-cccc-dddddddddddd",
                                "link_type": "citation",
                                "relationship_note": "Background citation attached to the current strategy spec.",
                            }
                        ],
                        "source_note_context": None,
                        "source_memory_context": None,
                        "created_at": "2026-04-01T14:00:00Z",
                    },
                    SERVICE_REF_ID: {
                        "ref_id": SERVICE_REF_ID,
                        "source_document": {
                            "title": "Research Note: Counter-trend divergence in Asia session",
                            "source_type": "research_note",
                            "source_ref": "note://pantheon-internal/note-77777777-8888-9999-aaaa-bbbbbbbbbbbb",
                            "excerpt": (
                                "Observed counter-trend divergence in Asia session data between 02:00 and 04:00 UTC."
                            ),
                            "storage_preview": {
                                "available": False,
                                "preview_type": "unavailable",
                                "preview_token": None,
                            },
                            "captured_at": "2026-04-10T09:15:00Z",
                            "captured_by": "Persona: Momentum-alpha",
                        },
                        "link_type": "counter_evidence",
                        "credibility": {
                            "tier": "tertiary",
                            "verified": False,
                            "last_verified_at": None,
                            "verification_method": None,
                        },
                        "linked_object_summary": {
                            "entity_type": "experiment",
                            "entity_ref": "exp-20260419-012",
                            "display_label": "Momentum decay replay on March volatility cluster",
                        },
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/knowledge/notes/note-77777777-8888-9999-aaaa-bbbbbbbbbbbb",
                            "display_label": "View research note",
                            "open_in_new_tab": False,
                        },
                        "linked_decisions": [
                            {
                                "entity_type": "experiment",
                                "entity_ref": "exp-20260419-012",
                                "display_label": "Momentum decay replay on March volatility cluster",
                                "route_href": "/research/experiments/exp-20260419-012",
                                "link_type": "counter_evidence",
                                "relationship_note": "The divergence challenges the Asia-session assumption in the replay candidate.",
                            }
                        ],
                        "source_note_context": {
                            "note_id": "note-77777777-8888-9999-aaaa-bbbbbbbbbbbb",
                            "title": "Counter-trend divergence in Asia session",
                            "excerpt": "Observed counter-trend divergence in Asia session data between 02:00 and 04:00 UTC.",
                            "route_href": "/knowledge/notes/note-77777777-8888-9999-aaaa-bbbbbbbbbbbb",
                        },
                        "source_memory_context": None,
                        "created_at": "2026-04-10T09:15:00Z",
                    },
                    "evref-30000000-1111-2222-3333-444444444444": {
                        "ref_id": "evref-30000000-1111-2222-3333-444444444444",
                        "source_document": {
                            "title": "Execution slippage histogram",
                            "source_type": "experiment_artifact",
                            "source_ref": "artifact://research/artifact-abc123",
                            "excerpt": "Histogram confirms the opening-window slippage cluster.",
                            "storage_preview": {
                                "available": True,
                                "preview_type": "image",
                                "preview_token": "prev-artifact-abc123",
                            },
                            "captured_at": "2026-04-16T13:10:00Z",
                            "captured_by": "Operator: Alice Chen",
                        },
                        "link_type": "supporting_evidence",
                        "credibility": {
                            "tier": "primary",
                            "verified": True,
                            "last_verified_at": "2026-04-17T11:30:00Z",
                            "verification_method": "operator_review",
                        },
                        "linked_object_summary": {
                            "entity_type": "memory_entry",
                            "entity_ref": "mem-11111111-2222-3333-4444-555555555555",
                            "display_label": "Latency surge pattern",
                        },
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/research/artifacts/artifact-abc123",
                            "display_label": "Open experiment artifact",
                            "open_in_new_tab": False,
                        },
                        "linked_decisions": [
                            {
                                "entity_type": "memory_entry",
                                "entity_ref": "mem-11111111-2222-3333-4444-555555555555",
                                "display_label": "Latency surge pattern",
                                "route_href": "/knowledge/memory/mem-11111111-2222-3333-4444-555555555555",
                                "link_type": "supporting_evidence",
                                "relationship_note": "Supports the institutional memory entry for the opening-auction slippage pattern.",
                            }
                        ],
                        "source_note_context": None,
                        "source_memory_context": None,
                        "created_at": "2026-04-16T13:15:00Z",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
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


def test_kw03_list_and_detail_return_contract_shape_with_degraded_fallback() -> None:
    with _seeded_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/evidence",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"evidence_refs_list": "degraded"}
        assert payload["pagination"]["page_size"] == 20
        first_item = payload["evidence_refs"][0]
        assert first_item["ref_id"] == FALLBACK_REF_ID
        assert sorted(first_item.keys()) == [
            "credibility",
            "link_type",
            "linked_object_summary",
            "ref_id",
            "resolved_link",
            "route_href",
            "source_document",
        ]
        assert first_item["resolved_link"]["availability"] == "available"
        assert first_item["route_href"] == f"/knowledge/evidence/{FALLBACK_REF_ID}"

        detail_response = client.get(
            f"/api/v1/knowledge/evidence/{FALLBACK_REF_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["ref_id"] == FALLBACK_REF_ID
        assert detail["source_document"]["storage_preview"]["preview_type"] == "image"
        assert detail["resolved_link"]["route_href"] == "/research/artifacts/artifact-abc123"
        assert detail["linked_decisions"][0]["entity_type"] == "memory_entry"
        assert detail["source_note_context"]["note_id"] == "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert detail["source_memory_context"]["entry_id"] == "mem-e5f6a7b8-c9d0-1234-efab-234567890123"
        assert detail["meta"]["surfaces"] == {
            "evidence_ref_detail": "degraded",
            "resolved_link": "degraded",
            "linked_decisions": "degraded",
        }


def test_kw03_service_backed_filters_and_detail_preserve_contract_semantics() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/evidence?linked_entity_type=strategy_spec&verified=false",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"evidence_refs_list": "ok"}
        assert [item["ref_id"] for item in payload["evidence_refs"]] == [
            "evref-10000000-1111-2222-3333-444444444444"
        ]
        assert payload["evidence_refs"][0]["resolved_link"] == {
            "availability": "external",
            "route_href": "https://arxiv.org/abs/2026.12345",
            "display_label": "Open external paper",
            "open_in_new_tab": True,
        }

        detail_response = client.get(
            f"/api/v1/knowledge/evidence/{SERVICE_REF_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["source_document"]["source_type"] == "research_note"
        assert detail["credibility"]["verification_method"] is None
        assert detail["resolved_link"]["route_href"] == "/knowledge/notes/note-77777777-8888-9999-aaaa-bbbbbbbbbbbb"
        assert detail["linked_decisions"] == [
            {
                "entity_type": "experiment",
                "entity_ref": "exp-20260419-012",
                "display_label": "Momentum decay replay on March volatility cluster",
                "route_href": "/research/experiments/exp-20260419-012",
                "link_type": "counter_evidence",
                "relationship_note": "The divergence challenges the Asia-session assumption in the replay candidate.",
            }
        ]
        assert detail["source_note_context"]["title"] == "Counter-trend divergence in Asia session"
        assert detail["source_memory_context"] is None
        assert detail["meta"]["surfaces"] == {
            "evidence_ref_detail": "ok",
            "resolved_link": "ok",
            "linked_decisions": "ok",
        }


def test_kw03_list_empty_filter_still_reports_available_surface_on_service_store() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/evidence?linked_entity_type=artifact",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["evidence_refs"] == []
        assert payload["pagination"] == {
            "page_size": 20,
            "next_page_token": None,
            "has_more": False,
        }
        assert payload["meta"]["surfaces"] == {"evidence_refs_list": "ok"}


def test_kw03_list_rejects_linked_entity_ref_without_type() -> None:
    with _service_backed_client() as client:
        response = client.get(
            "/api/v1/knowledge/evidence?linked_entity_ref=strat-99999999-aaaa-bbbb-cccc-dddddddddddd",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 400, response.text
        payload = response.json()
        assert payload["detail"]["error"]["details"]["precondition_failed"] == "linked_entity_ref"
