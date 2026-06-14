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
        assert payload["error"]["details"]["precondition_failed"] == "linked_entity_ref"


# --------------------------------------------------------------------------- #
# BFF-FINAL-007: Evidence redaction capability enforcement tests
# --------------------------------------------------------------------------- #

def test_bff_final_007_redact_evidence_refs_insufficient_capability() -> None:
    """Refs for evidence kinds the operator lacks capability for are replaced by RedactedEvidenceRef."""
    OperatorIdentity = read_store_module.OperatorIdentity
    _redact = read_store_module.redact_evidence_refs

    # operator role: has risk.alert.read, risk.incident.read, runtime.read, artifact.read
    # does NOT have metric.read or job.read
    identity = OperatorIdentity(operator_id="op-test-007", roles=["operator"])
    caps = ["risk.alert.read", "risk.incident.read", "runtime.read", "artifact.read"]

    refs = [
        {"ref_id": "ref-alert-001", "type": "alert"},    # risk.alert.read → passes
        {"ref_id": "ref-metric-001", "type": "metric"},  # metric.read → redacted
        {"ref_id": "ref-job-001", "type": "job"},         # job.read → redacted
    ]

    processed, redacted_count = _redact(identity, refs, capabilities=caps)

    assert redacted_count == 2
    assert len(processed) == 3

    # Alert ref passes through unchanged (operator has risk.alert.read)
    assert processed[0] == refs[0]

    # Metric ref is replaced by a redacted envelope
    metric_ref = processed[1]
    assert metric_ref["redacted"] is True
    assert metric_ref["required_capability"] == "metric.read"
    assert metric_ref["reason"] == "insufficient_capability"
    assert metric_ref["ref_id"] == "ref-metric-001"
    assert metric_ref["kind"] == "metric"

    # Job ref is replaced by a redacted envelope
    job_ref = processed[2]
    assert job_ref["redacted"] is True
    assert job_ref["required_capability"] == "job.read"
    assert job_ref["ref_id"] == "ref-job-001"
    assert job_ref["kind"] == "job"


def test_bff_final_007_redact_evidence_refs_none_capabilities_is_noop() -> None:
    """When capabilities=None, redaction is a no-op (backwards-compatible)."""
    OperatorIdentity = read_store_module.OperatorIdentity
    _redact = read_store_module.redact_evidence_refs

    identity = OperatorIdentity(operator_id="op-test-007", roles=["operator"])
    refs = [
        {"ref_id": "ref-metric-001", "type": "metric"},
        {"ref_id": "ref-alert-001", "type": "alert"},
    ]

    processed, redacted_count = _redact(identity, refs, capabilities=None)

    assert redacted_count == 0
    assert processed == refs


# --------------------------------------------------------------------------- #
# BFF-FINAL-007: endpoint-level insufficient-capability tests
# --------------------------------------------------------------------------- #

def test_bff_final_007_review_queue_redacts_evidence_refs_for_insufficient_capability() -> None:
    """Review-queue items have review_summary.evidence_refs redacted for EvidenceKinds the operator lacks."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        store.list_governance_review_queue_items = lambda **kwargs: [
            {
                "item_id": "gov-redact-001",
                "item_type": "DeploymentPlan",
                "risk_level": "medium",
                "status": "pending",
                "submitted_at": "2026-05-07T10:00:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "pending",
                "allowedActions": {
                    "canReview": True,
                    "canForwardToApproval": False,
                    "canRequestChanges": True,
                    "canEscalate": False,
                },
                "review_summary": {
                    "risk_assessment": "Test item for evidence redaction",
                    "evidence_refs": [
                        {"ref_id": "ref-alert-ev", "type": "alert"},     # risk.alert.read → operator has this
                        {"ref_id": "ref-metric-ev", "type": "metric"},   # metric.read → operator lacks this
                        {"ref_id": "ref-strategy-ev", "type": "strategy"},  # strategy.view → operator lacks this
                    ],
                    "linked_approval_decision_id": None,
                },
            }
        ]
        store.dataset_source = lambda dataset: (
            "service_store" if dataset == "governance_review_queue_items" else "missing"
        )
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/governance/review-queue",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert len(payload["items"]) == 1
            ev_refs = payload["items"][0]["review_summary"]["evidence_refs"]
            assert len(ev_refs) == 3

            # Alert ref passes through (operator has risk.alert.read)
            assert ev_refs[0] == {"ref_id": "ref-alert-ev", "type": "alert"}

            # Metric ref is replaced with RedactedEvidenceRef
            assert ev_refs[1]["redacted"] is True
            assert ev_refs[1]["required_capability"] == "metric.read"
            assert ev_refs[1]["ref_id"] == "ref-metric-ev"
            assert ev_refs[1]["reason"] == "insufficient_capability"

            # Strategy ref is replaced with RedactedEvidenceRef
            assert ev_refs[2]["redacted"] is True
            assert ev_refs[2]["required_capability"] == "strategy.view"
            assert ev_refs[2]["ref_id"] == "ref-strategy-ev"

            # Redacted evidence count telemetry
            assert payload["meta"]["redacted_evidence_count"] == 2
        finally:
            bff_main.read_store = original_store


def test_bff_final_007_knowledge_evidence_list_redacts_items_for_insufficient_capability() -> None:
    """Knowledge evidence list replaces items with RedactedEvidenceRef when operator lacks the required capability."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        evidence_store.write_text(
            json.dumps(
                {
                    "evref-alert-cap-001": {
                        "ref_id": "evref-alert-cap-001",
                        "evidence_type": "alert",
                        "link_type": "supporting_evidence",
                        "source_document": {"title": "Alert Evidence", "source_type": "alert"},
                        "credibility": {"tier": "primary", "verified": True},
                        "linked_object_summary": {"entity_type": "runtime", "entity_ref": "rt-001"},
                        "resolved_link": {"availability": "available", "route_href": "/alerts/001"},
                        "route_href": "/knowledge/evidence/evref-alert-cap-001",
                        "created_at": "2026-05-07T10:00:00Z",
                    },
                    "evref-metric-cap-001": {
                        "ref_id": "evref-metric-cap-001",
                        "evidence_type": "metric",
                        "link_type": "supporting_evidence",
                        "source_document": {"title": "Metric Evidence", "source_type": "metric"},
                        "credibility": {"tier": "secondary", "verified": False},
                        "linked_object_summary": {"entity_type": "runtime", "entity_ref": "rt-002"},
                        "resolved_link": {"availability": "available", "route_href": "/metrics/001"},
                        "route_href": "/knowledge/evidence/evref-metric-cap-001",
                        "created_at": "2026-05-07T10:01:00Z",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        tracked_env = {"PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")}
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/knowledge/evidence",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            ev_refs = payload["evidence_refs"]
            assert len(ev_refs) == 2

            ref_by_id = {r["ref_id"]: r for r in ev_refs}

            # Alert item passes through (operator has risk.alert.read)
            alert_item = ref_by_id["evref-alert-cap-001"]
            assert "source_document" in alert_item
            assert not alert_item.get("redacted")

            # Metric item is replaced with RedactedEvidenceRef (operator lacks metric.read)
            metric_item = ref_by_id["evref-metric-cap-001"]
            assert metric_item["redacted"] is True
            assert metric_item["required_capability"] == "metric.read"
            assert metric_item["reason"] == "insufficient_capability"

            # Redacted evidence count telemetry
            assert payload["meta"]["redacted_evidence_count"] == 1
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_bff_final_007_knowledge_evidence_detail_redacts_linked_decisions_for_insufficient_capability() -> None:
    """Evidence detail redacts linked_decisions entries when operator lacks the required capability."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        detail_ref_id = "evref-detail-redact-001"
        evidence_store.write_text(
            json.dumps(
                {
                    detail_ref_id: {
                        "ref_id": detail_ref_id,
                        "link_type": "citation",
                        "source_document": {
                            "title": "Detail Test Evidence",
                            "source_type": "external_paper",
                        },
                        "credibility": {"tier": "secondary", "verified": False},
                        "resolved_link": {
                            "availability": "external",
                            "route_href": "https://example.com",
                        },
                        "linked_decisions": [
                            {
                                "ref_id": "rt-001",
                                "entity_type": "runtime",
                                "entity_ref": "rt-001",
                                "display_label": "Runtime binding",
                                "route_href": "/runtimes/rt-001",
                                "type": "runtime",      # runtime.read → operator HAS this
                            },
                            {
                                "ref_id": "strat-001",
                                "entity_type": "strategy_spec",
                                "entity_ref": "strat-001",
                                "display_label": "Strategy",
                                "route_href": "/strategies/strat-001",
                                "type": "strategy",     # strategy.view → operator LACKS this
                            },
                        ],
                        "source_note_context": None,
                        "source_memory_context": None,
                        "created_at": "2026-05-07T10:00:00Z",
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        tracked_env = {"PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")}
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                f"/api/v1/knowledge/evidence/{detail_ref_id}",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            detail = response.json()

            linked_decisions = detail["linked_decisions"]
            assert len(linked_decisions) == 2

            # Runtime decision passes through (operator has runtime.read)
            runtime_dec = linked_decisions[0]
            assert runtime_dec.get("entity_ref") == "rt-001"
            assert not runtime_dec.get("redacted")

            # Strategy decision is replaced with RedactedEvidenceRef
            strategy_dec = linked_decisions[1]
            assert strategy_dec["redacted"] is True
            assert strategy_dec["required_capability"] == "strategy.view"
            assert strategy_dec["ref_id"] == "strat-001"

            # Redacted evidence count telemetry
            assert detail["meta"]["redacted_evidence_count"] == 1
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_bff_final_007_evidence_detail_redacts_self_for_insufficient_capability() -> None:
    """Direct evidence detail endpoint returns RedactedEvidenceRef when operator lacks the EvidenceKind capability."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        detail_ref_id = "evref-metric-redact-self-001"
        evidence_store.write_text(
            json.dumps(
                {
                    detail_ref_id: {
                        "ref_id": detail_ref_id,
                        "evidence_type": "metric",   # metric.read → operator LACKS this
                        "link_type": "supporting",
                        "source_document": {
                            "title": "Metric Evidence Detail",
                            "source_type": "internal_metric",
                        },
                        "credibility": {"tier": "primary", "verified": True},
                        "resolved_link": {
                            "availability": "internal",
                            "route_href": "/metrics/metric-001",
                        },
                        "linked_decisions": [],
                        "source_note_context": None,
                        "source_memory_context": None,
                        "created_at": "2026-05-07T10:00:00Z",
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        tracked_env = {"PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")}
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                f"/api/v1/knowledge/evidence/{detail_ref_id}",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            detail = response.json()

            # The detail ref itself is redacted
            assert detail["redacted"] is True
            assert detail["ref_id"] == detail_ref_id
            assert detail["required_capability"] == "metric.read"
            assert detail["reason"] == "insufficient_capability"

            # Redaction telemetry
            assert detail["meta"]["redacted_evidence_count"] == 1

            # Sensitive detail fields must not appear
            assert "source_document" not in detail
            assert "resolved_link" not in detail
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_bff_final_007_redact_evidence_refs_source_type_only_insufficient_capability() -> None:
    """Refs with only source_document.source_type (no evidence_type) are capability-gated via SOURCE_TYPE_TO_EVIDENCE_KIND."""
    OperatorIdentity = read_store_module.OperatorIdentity
    _redact = read_store_module.redact_evidence_refs

    # Operator lacks postmortem.read and audit.read
    identity = OperatorIdentity(operator_id="op-test-007b", roles=["operator"])
    caps = ["risk.alert.read", "metric.read"]

    refs = [
        # No evidence_type; kind derived from source_document.source_type=postmortem
        {
            "ref_id": "ref-postmortem-source-001",
            "source_document": {"title": "Incident Post-Mortem", "source_type": "postmortem"},
        },
        # No evidence_type; kind derived from source_document.source_type=audit_log
        {
            "ref_id": "ref-audit-source-001",
            "source_document": {"title": "Audit Log Q1", "source_type": "audit_log"},
        },
        # No evidence_type; source_type=metric → operator HAS metric.read → passes
        {
            "ref_id": "ref-metric-source-001",
            "source_document": {"title": "Metric Export", "source_type": "metric"},
        },
    ]

    processed, redacted_count = _redact(identity, refs, capabilities=caps)

    assert redacted_count == 2
    assert len(processed) == 3

    pm_ref = processed[0]
    assert pm_ref["redacted"] is True
    assert pm_ref["required_capability"] == "postmortem.read"
    assert pm_ref["ref_id"] == "ref-postmortem-source-001"
    assert pm_ref["kind"] == "postmortem"

    audit_ref = processed[1]
    assert audit_ref["redacted"] is True
    assert audit_ref["required_capability"] == "audit.read"
    assert audit_ref["ref_id"] == "ref-audit-source-001"
    assert audit_ref["kind"] == "audit"

    # Metric passes through
    assert processed[2] == refs[2]


def test_bff_final_007_evidence_detail_redacts_source_type_only_ref() -> None:
    """Evidence detail endpoint redacts a ref that has source_document.source_type=postmortem but no evidence_type."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        detail_ref_id = "evref-postmortem-source-type-only-001"
        evidence_store.write_text(
            json.dumps(
                {
                    detail_ref_id: {
                        "ref_id": detail_ref_id,
                        # No evidence_type field — kind must be inferred from source_type
                        "link_type": "supporting",
                        "source_document": {
                            "title": "Post-Mortem Report: Outage 2026-04",
                            "source_type": "postmortem",
                        },
                        "credibility": {"tier": "primary", "verified": True},
                        "resolved_link": {
                            "availability": "internal",
                            "route_href": "/postmortems/pm-2026-04",
                        },
                        "linked_decisions": [],
                        "source_note_context": None,
                        "source_memory_context": None,
                        "created_at": "2026-05-07T10:00:00Z",
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        tracked_env = {"PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")}
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                f"/api/v1/knowledge/evidence/{detail_ref_id}",
                headers={"Authorization": OPERATOR_TOKEN},  # operator lacks postmortem.read
            )
            assert response.status_code == 200, response.text
            detail = response.json()

            # The detail ref itself is redacted via source_type derivation
            assert detail["redacted"] is True
            assert detail["ref_id"] == detail_ref_id
            assert detail["required_capability"] == "postmortem.read"
            assert detail["reason"] == "insufficient_capability"

            # Redaction telemetry
            assert detail["meta"]["redacted_evidence_count"] == 1

            # Sensitive detail fields must not leak
            assert "source_document" not in detail
            assert "resolved_link" not in detail
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
