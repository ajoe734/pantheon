"""
BFF-B3-006: contract tests for GET /bff/management/evidence.

The route adapts the existing knowledge evidence read surface into a
Management Evidence Explorer aggregate while preserving read auth, filters,
pagination, source-surface metadata, and evidence capability redaction.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


ADMIN_HEADERS = {"Authorization": "Bearer op-b3:admin"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-b3:operator"}


@contextmanager
def _evidence_client() -> Iterator[TestClient]:
    tracked_env = {
        "PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE"),
        "PANTHEON_BFF_EVIDENCE_OPERATION_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_OPERATION_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        operation_store = root / "evidence_operations.json"
        evidence_store.write_text(
            json.dumps(
                {
                    "evref-b3-metric-001": {
                        "ref_id": "evref-b3-metric-001",
                        "evidence_type": "metric",
                        "link_type": "supporting_evidence",
                        "source_document": {
                            "title": "Runtime performance window",
                            "source_type": "metric",
                            "source_ref": "metric://runtime-alpha/window",
                            "captured_at": "2026-05-23T09:00:00Z",
                        },
                        "credibility": {"tier": "primary", "verified": True},
                        "linked_object_summary": {
                            "entity_type": "experiment",
                            "entity_ref": "exp-b3-alpha",
                            "display_label": "Alpha experiment",
                        },
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/metrics/runtime-alpha/window",
                        },
                        "route_href": "/knowledge/evidence/evref-b3-metric-001",
                        "created_at": "2026-05-23T09:00:00Z",
                    },
                    "evref-b3-alert-001": {
                        "ref_id": "evref-b3-alert-001",
                        "evidence_type": "alert",
                        "link_type": "corroboration",
                        "source_document": {
                            "title": "Risk alert packet",
                            "source_type": "alert",
                            "source_ref": "alert://risk-alpha",
                            "captured_at": "2026-05-23T09:05:00Z",
                        },
                        "credibility": {"tier": "secondary", "verified": False},
                        "linked_object_summary": {
                            "entity_type": "artifact",
                            "entity_ref": "artifact-b3-alpha",
                            "display_label": "Runtime artifact",
                        },
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/alerts/risk-alpha",
                        },
                        "linked_decisions": [
                            {
                                "entity_type": "decision",
                                "entity_ref": "dec-b3-alpha",
                                "display_label": "Approve runtime artifact",
                                "route_href": "/management/decisions/dec-b3-alpha",
                                "link_type": "corroboration",
                                "relationship_note": "Alert packet was reviewed during artifact approval.",
                            },
                            {
                                "entity_type": "readiness",
                                "entity_ref": "broker-live",
                                "display_label": "Broker Live readiness",
                                "route_href": "/management/readiness/broker-live",
                                "link_type": "readiness_evidence",
                                "relationship_note": "Alert packet is part of broker-live readiness review.",
                            },
                            {
                                "entity_type": "assertion",
                                "entity_ref": "assert-no-real-capital",
                                "display_label": "No real capital assertion",
                                "link_type": "assertion_support",
                                "relationship_note": "Alert packet supports no-real-capital safety assertion.",
                            },
                        ],
                        "source_note_context": {
                            "note_id": "note-b3-alert",
                            "title": "Risk alpha alert note",
                            "excerpt": "Operator note for the risk alpha alert packet.",
                            "route_href": "/knowledge/notes/note-b3-alert",
                        },
                        "source_memory_context": {
                            "entry_id": "mem-b3-alert",
                            "headline": "Runtime alert memory",
                            "excerpt": "Institutional memory entry for this alert pattern.",
                            "route_href": "/knowledge/memory/mem-b3-alert",
                        },
                        "route_href": "/knowledge/evidence/evref-b3-alert-001",
                        "created_at": "2026-05-23T09:05:00Z",
                    },
                    "evref-b3-producer-001": {
                        "ref_id": "evref-b3-producer-001",
                        "evidence_type": "artifact",
                        "link_type": None,
                        "source_document": {
                            "title": "TW momentum candidate",
                            "captured_at": "2026-06-15T13:06:00Z",
                        },
                        "credibility": {
                            "tier": "producer_record",
                            "verified": True,
                            "last_verified_at": "2026-06-15T13:06:00Z",
                            "verification_method": "research_orchestrator_projection",
                        },
                        "linked_object_summary": {
                            "entity_type": "artifact",
                            "entity_ref": "rart-20260615-002",
                            "display_label": "TW momentum candidate artifact",
                        },
                        "resolved_link": {
                            "availability": "unavailable",
                            "route_href": None,
                        },
                        "route_href": "/knowledge/evidence/evref-b3-producer-001",
                        "created_at": "2026-06-15T13:06:00Z",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)
        os.environ["PANTHEON_BFF_EVIDENCE_OPERATION_STORE"] = str(operation_store)
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        try:
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_management_evidence_composes_explorer_envelope_with_filters() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence"
            "?linked_entity_type=experiment"
            "&linked_entity_ref=exp-b3-alpha"
            "&credibility_tier=primary"
            "&verified=true"
            "&page_size=1",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"] == payload["items"]
        assert payload["page_info"]["total"] == 1
        assert payload["summary"]["totalEvidence"] == 1
        assert payload["summary"]["returnedEvidence"] == 1
        assert payload["summary"]["bySourceType"] == {"metric": 1}
        assert payload["facets"]["credibilityTiers"] == {"primary": 1}
        assert payload["meta"]["surfaces"]["management_evidence"]["source"] == "bff_composed"
        assert payload["meta"]["surfaces"]["evidence_refs"]["status"] in {"ok", "degraded"}

        item = payload["items"][0]
        assert item["id"] == "evref-b3-metric-001"
        assert item["refId"] == "evref-b3-metric-001"
        assert item["sourceType"] == "metric"
        assert item["linkedObjectSummary"]["entity_ref"] == "exp-b3-alpha"
        assert item["routeHref"] == "/knowledge/evidence/evref-b3-metric-001"
        assert item["actionability"] == {
            "state": "traceable",
            "severity": "ok",
            "reasons": [],
            "can_trace": True,
            "can_open_source": True,
            "can_open_linked_object": True,
        }
        assert item["allowedActions"]["canOpenSource"] is True
        assert item["allowedActions"]["canOpenLinkedObject"] is True
        assert item["allowedActions"]["canInspectChain"] is True
        assert item["allowedActions"]["canMarkStale"] is True
        assert item["allowedActions"]["canRequestEvidence"] is True
        assert item["allowedActions"]["canCreateDispositionTask"] is True
        assert item["allowedActions"]["canAssignReviewer"] is True
        assert item["allowedActions"]["canResolve"] is False
        assert item["operation"]["status"] == "none"
        assert item["redacted"] is False
        assert payload["summary"]["traceableEvidence"] == 1
        assert payload["summary"]["needsAttentionEvidence"] == 0
        assert payload["facets"]["actionabilityStates"] == {"traceable": 1}
        assert payload["meta"]["performance"]["row_count"] == 1
        assert payload["meta"]["performance"]["filtered_total"] == 1
        assert payload["meta"]["performance"]["timings_ms"]["total"] >= 0


def test_management_evidence_marks_verified_but_untraceable_rows_as_unresolved() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence?ref_id=evref-b3-producer-001",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["totalEvidence"] == 1
        assert payload["summary"]["verifiedEvidence"] == 1
        assert payload["summary"]["traceableEvidence"] == 0
        assert payload["summary"]["unresolvedSourceEvidence"] == 1
        assert payload["summary"]["needsAttentionEvidence"] == 1
        assert payload["facets"]["actionabilityStates"] == {"unresolved_source": 1}

        item = payload["items"][0]
        assert item["id"] == "evref-b3-producer-001"
        assert item["credibility"]["verified"] is True
        assert item["actionability"]["state"] == "unresolved_source"
        assert item["actionability"]["severity"] == "warning"
        assert item["actionability"]["can_trace"] is False
        assert item["actionability"]["can_open_source"] is False
        assert item["actionability"]["can_open_linked_object"] is True
        assert item["actionability"]["reasons"] == [
            "missing_source_type",
            "missing_source_ref",
            "missing_link_type",
            "resolved_link_unavailable",
        ]
        assert item["linkedObjectLink"] == {
            "availability": "available",
            "route_href": "/management/artifacts/rart-20260615-002",
            "display_label": "TW momentum candidate artifact",
            "entity_type": "artifact",
            "entity_ref": "rart-20260615-002",
        }
        assert item["allowedActions"] == {
            "canOpenSource": False,
            "canOpenLinkedObject": True,
            "canInspectChain": True,
            "canMarkStale": True,
            "canRequestEvidence": True,
            "canCreateDispositionTask": True,
            "canAssignReviewer": True,
            "canResolve": False,
        }
        assert item["disabledActionReasons"]["canOpenSource"] == (
            "Source link is unavailable or incomplete."
        )
        assert item["disabledActionReasons"]["canResolve"] == (
            "No open evidence operation exists to resolve."
        )
        assert item["operation"]["status"] == "none"
        assert item["operation"]["task_refs"] == []
        assert item["operation"]["command_refs"] == []
        assert payload["meta"]["performance"]["timings_ms"]["read_store_load"] >= 0


def test_management_evidence_preserves_capability_redaction() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence?ref_id=evref-b3-metric-001",
            headers=OPERATOR_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["redactedEvidence"] == 1
        assert payload["meta"]["redacted_evidence_count"] == 1

        item = payload["items"][0]
        assert item["id"] == "evref-b3-metric-001"
        assert item["redacted"] is True
        assert item["requiredCapability"] == "metric.read"
        assert item["reason"] == "insufficient_capability"


def test_management_evidence_detail_composes_operations_relationships_and_chain() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence/evref-b3-alert-001",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["id"] == "evref-b3-alert-001"
        assert payload["sourceDocument"]["source_type"] == "alert"
        assert payload["resolvedLink"]["route_href"] == "/alerts/risk-alpha"
        assert payload["linkedObjectLink"] == {
            "availability": "available",
            "route_href": "/management/artifacts/artifact-b3-alpha",
            "display_label": "Runtime artifact",
            "entity_type": "artifact",
            "entity_ref": "artifact-b3-alpha",
        }
        assert payload["actionability"]["state"] == "traceable"
        assert payload["allowedActions"]["canOpenSource"] is True
        assert payload["allowedActions"]["canOpenLinkedObject"] is True
        assert payload["allowedActions"]["canInspectChain"] is True
        assert payload["allowedActions"]["canMarkStale"] is True
        assert payload["allowedActions"]["canRequestEvidence"] is True
        assert payload["allowedActions"]["canCreateDispositionTask"] is True
        assert payload["allowedActions"]["canAssignReviewer"] is True
        assert payload["allowedActions"]["canResolve"] is False
        assert payload["disabledActionReasons"]["canResolve"] == (
            "No open evidence operation exists to resolve."
        )

        relationships = payload["relationships"]
        assert relationships["artifacts"][0]["entity_ref"] == "artifact-b3-alpha"
        assert relationships["artifacts"][0]["route_href"] == "/management/artifacts/artifact-b3-alpha"
        assert relationships["decisions"][0]["entity_ref"] == "dec-b3-alpha"
        assert relationships["readiness"][0]["route_href"] == "/management/readiness/broker-live"
        assert relationships["assertions"][0]["entity_ref"] == "assert-no-real-capital"
        assert relationships["notes"][0]["entity_ref"] == "note-b3-alert"
        assert relationships["memory"][0]["entity_ref"] == "mem-b3-alert"

        chain = payload["chain"]
        assert chain["empty_reason"] is None
        assert chain["degraded_reasons"] == []
        node_ids = {node["id"] for node in chain["nodes"]}
        assert "evidence:evref-b3-alert-001" in node_ids
        assert "artifact:artifact-b3-alpha" in node_ids
        assert "readiness:broker-live" in node_ids
        evidence_node = next(
            node for node in chain["nodes"] if node["id"] == "evidence:evref-b3-alert-001"
        )
        assert evidence_node["route_href"] == "/management/evidence?ref_id=evref-b3-alert-001"
        assert any(edge["to"] == "artifact:artifact-b3-alpha" for edge in chain["edges"])

        assert payload["operation"]["status"] == "none"
        assert payload["tasks"] == []
        assert payload["auditEvents"] == []
        assert payload["meta"]["surfaces"]["management_evidence_detail"]["source"] == "bff_composed"
        assert payload["meta"]["surfaces"]["relationships"]["status"] == "ok"
        assert payload["meta"]["surfaces"]["chain"]["status"] == "ok"
        assert payload["meta"]["surfaces"]["assertions"]["status"] == "ok"
        assert payload["meta"]["surfaces"]["readiness_relationships"]["status"] == "ok"
        assert payload["meta"]["surfaces"]["operation_state"]["status"] == "ok"
        assert payload["meta"]["performance"]["row_count"] == 1


def test_management_evidence_detail_marks_untraceable_producer_row() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence/evref-b3-producer-001",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["id"] == "evref-b3-producer-001"
        assert payload["credibility"]["verified"] is True
        assert payload["actionability"]["state"] == "unresolved_source"
        assert payload["linkedObjectLink"]["route_href"] == "/management/artifacts/rart-20260615-002"
        assert payload["relationships"]["artifacts"][0]["entity_ref"] == "rart-20260615-002"
        assert payload["relationships"]["readiness"] == []
        assert payload["relationships"]["assertions"] == []
        assert payload["chain"]["empty_reason"] is None
        assert "resolved_link_unavailable" in payload["chain"]["degraded_reasons"]
        assert payload["meta"]["surfaces"]["assertions"]["status"] == "unavailable"
        assert payload["meta"]["surfaces"]["readiness_relationships"]["status"] == "unavailable"


def test_management_evidence_detail_preserves_capability_redaction() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence/evref-b3-metric-001",
            headers=OPERATOR_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["id"] == "evref-b3-metric-001"
        assert payload["redacted"] is True
        assert payload["actionability"]["state"] == "redacted"
        assert payload["allowedActions"]["canOpenSource"] is False
        assert payload["allowedActions"]["canInspectChain"] is False
        assert payload["relationships"]["artifacts"] == []
        assert payload["chain"]["empty_reason"] == "redacted"
        assert payload["meta"]["redacted_evidence_count"] == 1


def test_management_evidence_action_command_marks_stale_and_overlays_reads() -> None:
    with _evidence_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                **OPERATOR_HEADERS,
                "Idempotency-Key": "evidence-mark-stale-001",
                "X-Trace-Id": "trace-evidence-mark-stale-001",
            },
            json={
                "command": "EvidenceRefAction",
                "target": {"type": "EvidenceRef", "id": "evref-b3-alert-001"},
                "params": {"action_id": "mark_stale"},
                "audit_context": {"reason": "Source packet is stale after readiness review."},
            },
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["data"]["command"] == "EvidenceRefAction"
        assert body["meta"]["idempotency"]["replayed"] is False

        stored = bff_main.command_store.get_command_by_idempotency_key(
            "evidence-mark-stale-001",
            operator_id="op-b3",
        )
        assert stored is not None
        assert stored["status"] == "executed"
        assert stored["target"] == {"type": "EvidenceRef", "id": "evref-b3-alert-001"}
        assert stored["params"]["ref_id"] == "evref-b3-alert-001"
        assert stored["result"]["operation"]["status"] == "stale"

        detail = client.get(
            "/bff/management/evidence/evref-b3-alert-001",
            headers=ADMIN_HEADERS,
        )
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["operation"]["status"] == "stale"
        assert payload["operation"]["last_reason"] == "Source packet is stale after readiness review."
        assert payload["actionability"]["state"] == "stale"
        assert payload["allowedActions"]["canMarkStale"] is False
        assert payload["allowedActions"]["canResolve"] is True
        assert payload["disabledActionReasons"]["canMarkStale"] == "Evidence is already marked stale."
        assert payload["auditEvents"][0]["command_id"] == stored["command_id"]
        assert payload["auditEvents"][0]["action"] == "mark_stale"

        list_response = client.get(
            "/bff/management/evidence?ref_id=evref-b3-alert-001",
            headers=ADMIN_HEADERS,
        )
        assert list_response.status_code == 200, list_response.text
        list_payload = list_response.json()
        row = list_payload["items"][0]
        assert row["operation"]["status"] == "stale"
        assert row["actionability"]["state"] == "stale"
        assert list_payload["facets"]["operationStatuses"] == {"stale": 1}


def test_management_evidence_action_command_replays_same_idempotency_key() -> None:
    with _evidence_client() as client:
        payload = {
            "command": "EvidenceRefAction",
            "target": {"type": "EvidenceRef", "id": "evref-b3-alert-001"},
            "params": {"action_id": "request_more_evidence"},
            "audit_context": {"reason": "Need a current packet before disposition."},
        }
        first = client.post(
            "/bff/v1/commands",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "evidence-request-more-001"},
            json=payload,
        )
        second = client.post(
            "/bff/v1/commands",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "evidence-request-more-001"},
            json=payload,
        )

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert second.json()["data"]["commandId"] == first.json()["data"]["commandId"]
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        assert len(bff_main.command_store._get_all_commands()) == 1


def test_management_evidence_action_command_assign_reviewer_requires_reviewer() -> None:
    with _evidence_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "evidence-assign-missing-reviewer-001"},
            json={
                "command": "EvidenceRefAction",
                "target": {"type": "EvidenceRef", "id": "evref-b3-alert-001"},
                "params": {"action_id": "assign_reviewer"},
                "audit_context": {"reason": "Reviewer is required for assignment."},
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["precondition_failed"] == "reviewer"
        assert bff_main.command_store._get_all_commands() == []


def test_management_evidence_action_command_rejects_ref_id_target_mismatch() -> None:
    with _evidence_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "evidence-target-mismatch-001"},
            json={
                "command": "EvidenceRefAction",
                "target": {"type": "EvidenceRef", "id": "evref-b3-alert-001"},
                "params": {
                    "ref_id": "evref-b3-producer-001",
                    "action_id": "mark_stale",
                },
                "audit_context": {"reason": "Mismatched target must be rejected."},
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["precondition_failed"] == "ref_id"
        assert bff_main.command_store._get_all_commands() == []


def test_management_evidence_requires_read_authentication() -> None:
    with _evidence_client() as client:
        response = client.get("/bff/management/evidence")

        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_management_evidence_detail_requires_read_authentication() -> None:
    with _evidence_client() as client:
        response = client.get("/bff/management/evidence/evref-b3-alert-001")

        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
