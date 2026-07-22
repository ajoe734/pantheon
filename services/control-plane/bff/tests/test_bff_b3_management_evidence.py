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
        "PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON": os.environ.get("PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON"),
        "PANTHEON_AUDIT_OUT_DIR": os.environ.get("PANTHEON_AUDIT_OUT_DIR"),
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
                        "overall": "pass",
                        "artifact_manifest": {
                            "file_count": 2,
                            "total_bytes": 88,
                            "limits": {
                                "max_files": 32,
                                "max_total_bytes": 8388608,
                                "max_file_bytes": 4194304,
                            },
                            "files": [
                                {
                                    "path": "BFF-LIVE-EVIDENCE-PREFLIGHT.json",
                                    "bytes": 44,
                                    "current_run_allowed": True,
                                    "forbidden_audit_scope": False,
                                    "oversized": False,
                                },
                                {
                                    "path": "release-gate-summary.json",
                                    "bytes": 44,
                                    "current_run_allowed": True,
                                    "forbidden_audit_scope": False,
                                    "oversized": False,
                                },
                            ],
                        },
                        "criteria": {
                            "rbac_matrix": {
                                "status": "pass",
                                "label": "RBAC matrix",
                                "note": "all role/path cases matched expected status",
                            },
                            "current_run_only": {
                                "status": "pass",
                                "label": "Current-run artifact scope",
                                "note": "2 artifact file(s); current-run scope only",
                            },
                        },
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
        os.environ.pop("PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON", None)
        os.environ.pop("PANTHEON_AUDIT_OUT_DIR", None)
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        try:
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            with TestClient(bff_main.app) as client:
                yield client
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
        assert set(payload.keys()) == {"data", "page_info", "meta"}
        items = payload["data"]["items"]
        summary = payload["data"]["summary"]
        facets = payload["data"]["facets"]
        assert payload["page_info"]["total"] == 1
        assert summary["total_evidence"] == 1
        assert summary["returned_evidence"] == 1
        assert summary["by_source_type"] == {"metric": 1}
        assert facets["credibility_tiers"] == {"primary": 1}
        assert payload["meta"]["surfaces"]["management_evidence"]["source"] == "bff_composed"
        assert payload["meta"]["surfaces"]["evidence_refs"]["status"] in {"ok", "degraded"}

        item = items[0]
        assert item["id"] == "evref-b3-metric-001"
        assert item["ref_id"] == "evref-b3-metric-001"
        assert item["source_type"] == "metric"
        assert item["linked_object_summary"]["entity_ref"] == "exp-b3-alpha"
        assert item["route_href"] == "/knowledge/evidence/evref-b3-metric-001"
        assert item["redacted"] is False
        assert "refId" not in item
        assert "sourceType" not in item
        assert "linkedObjectSummary" not in item
        assert "routeHref" not in item


def test_management_evidence_preserves_artifact_manifest_from_store() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence?ref_id=evref-b3-metric-001",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["page_info"]["total"] == 1

        item = payload["data"]["items"][0]
        assert item["overall"] == "pass"
        assert "artifactManifest" not in item
        manifest = item["artifact_manifest"]
        assert manifest["file_count"] == 2
        assert manifest["total_bytes"] == 88
        assert manifest["limits"]["max_files"] == 32
        assert [entry["path"] for entry in manifest["files"]] == [
            "BFF-LIVE-EVIDENCE-PREFLIGHT.json",
            "release-gate-summary.json",
        ]
        assert all(entry["current_run_allowed"] is True for entry in manifest["files"])
        assert item["criteria"]["rbac_matrix"]["status"] == "pass"
        assert item["criteria"]["current_run_only"]["note"] == "2 artifact file(s); current-run scope only"
        assert "artifactManifest" not in item


@contextmanager
def _current_run_evidence_client(verifier_path: Path) -> Iterator[TestClient]:
    tracked_env = {
        "PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE"),
        "PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON": os.environ.get("PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON"),
        "PANTHEON_AUDIT_OUT_DIR": os.environ.get("PANTHEON_AUDIT_OUT_DIR"),
    }
    os.environ.pop("PANTHEON_BFF_EVIDENCE_REF_STORE", None)
    os.environ["PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON"] = str(verifier_path)
    os.environ.pop("PANTHEON_AUDIT_OUT_DIR", None)
    original_store = bff_main.read_store
    try:
        bff_main.read_store = ReadSurfaceStore(
            str(verifier_path.parent / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        with TestClient(bff_main.app) as client:
            yield client
    finally:
        bff_main.read_store = original_store
        for key, value in tracked_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_live_evidence_verifier(path: Path, *, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
                "artifact_dir": str(path.parent),
                "overall": "pass",
                "artifact_manifest": manifest,
                "criteria": {
                    "rbac_matrix": {
                        "status": "fail",
                        "label": "RBAC matrix",
                        "note": "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON",
                    },
                    "current_run_only": {
                        "status": "pass",
                        "label": "Evidence written to `.lovable/audits/current-run`.",
                        "note": "4 artifact file(s); current-run scope only",
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_management_evidence_projects_current_run_verifier_when_store_missing(tmp_path: Path) -> None:
    verifier_path = (
        tmp_path
        / ".lovable"
        / "audits"
        / "current-run"
        / "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json"
    )
    manifest = {
        "file_count": 4,
        "total_bytes": 128,
        "limits": {
            "max_files": 32,
            "max_total_bytes": 8388608,
            "max_file_bytes": 4194304,
        },
        "files": [
            {
                "path": "BFF-LIVE-EVIDENCE-PREFLIGHT.json",
                "bytes": 32,
                "current_run_allowed": True,
                "forbidden_audit_scope": False,
                "oversized": False,
            },
            {
                "path": "BFF-LUV-AUTHED-LIVE-001-live-smoke.json",
                "bytes": 32,
                "current_run_allowed": True,
                "forbidden_audit_scope": False,
                "oversized": False,
            },
            {
                "path": "BFF-CONSOL-011-sse-replay-smoke.json",
                "bytes": 32,
                "current_run_allowed": True,
                "forbidden_audit_scope": False,
                "oversized": False,
            },
            {
                "path": "release-gate-summary.json",
                "bytes": 32,
                "current_run_allowed": True,
                "forbidden_audit_scope": False,
                "oversized": False,
            },
        ],
    }
    _write_live_evidence_verifier(verifier_path, manifest=manifest)
    secret_set_command = (
        "gh secret set PANTHEON_BFF_RBAC_TOKENS_JSON --repo ajoe734/pantheon "
        "--env dev < /secure/path/PANTHEON_BFF_RBAC_TOKENS_JSON.txt"
    )
    verifier_path.parent.joinpath("BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "operator_remediation": {
                    "github_environment": "dev",
                    "repository": "ajoe734/pantheon",
                    "required_secret_names": [
                        "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
                        "PANTHEON_BFF_RBAC_TOKENS_JSON",
                    ],
                    "missing_secret_names": ["PANTHEON_BFF_RBAC_TOKENS_JSON"],
                    "missing_workflow_inputs": ["APPROVAL_RACE_ID"],
                    "invalid_inputs": [
                        {"name": "SOAK_SECONDS", "reason": "must be at least 75"},
                    ],
                    "secret_set_commands": [secret_set_command],
                    "workflow_dispatch": {
                        "recommended_workflow": "Pantheon Stage 0 CI",
                        "mode": "live-evidence",
                        "environment": "dev",
                        "run_command_template": (
                            "gh workflow run \"Pantheon Stage 0 CI\" --repo ajoe734/pantheon "
                            "--ref dev -f mode=live-evidence -f environment=dev"
                        ),
                    },
                    "notes": [
                        "Set secrets on the selected GitHub environment, not only at repository scope.",
                    ],
                },
                "secret_values_written": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    verifier_path.parent.joinpath("release-gate-summary.json").write_text(
        json.dumps(
            {
                "generatedAt": "2026-07-04T13:30:00Z",
                "overall": "fail",
                "auditDir": ".lovable/audits/current-run",
                "runUrl": "https://github.com/ajoe734/pantheon/actions/runs/123456789",
                "checklistOut": ".lovable/audits/current-run/Release_Gate_Checklist.md",
                "gates": {
                    "3": [
                        {
                            "label": "Authenticated: strict bearer RBAC matrix evidence passed.",
                            "status": "fail",
                            "owner": "Codex",
                            "evidence": "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json",
                            "note": "preflight bearer hash inventory mismatch",
                        },
                    ],
                    "7": [
                        {
                            "label": "Evidence written to `.lovable/audits/current-run`.",
                            "status": "pass",
                            "owner": "",
                            "evidence": ".lovable/audits/current-run",
                            "note": "4 audit file(s) found",
                        },
                    ],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with _current_run_evidence_client(verifier_path) as client:
        response = client.get(
            "/bff/management/evidence?ref_id=BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_info"]["total"] == 1
    assert payload["data"]["summary"]["by_source_type"] == {"workflow_artifact": 1}
    assert payload["data"]["facets"]["link_types"] == {"provenance": 1}
    assert payload["meta"]["surfaces"]["evidence_refs"]["source"] == "bff_current_run_artifact"
    assert payload["meta"]["surfaces"]["management_evidence"]["source"] == "bff_composed"

    item = payload["data"]["items"][0]
    assert item["id"] == "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY"
    assert item["source_type"] == "workflow_artifact"
    assert item["link_type"] == "provenance"
    assert item["credibility"] == {"tier": "primary", "verified": True}
    assert item["linked_object_summary"]["entity_type"] == "artifact"
    assert item["overall"] == "pass"
    assert item["artifact_manifest"] == manifest
    assert item["criteria"]["rbac_matrix"]["status"] == "fail"
    assert item["criteria"]["rbac_matrix"]["note"] == "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON"
    assert item["criteria"]["current_run_only"]["status"] == "pass"
    release_gate_summary = item["release_gate_summary"]
    assert release_gate_summary["overall"] == "fail"
    assert release_gate_summary["generated_at"] == "2026-07-04T13:30:00Z"
    assert release_gate_summary["audit_dir"] == ".lovable/audits/current-run"
    assert release_gate_summary["run_url"] == "https://github.com/ajoe734/pantheon/actions/runs/123456789"
    assert release_gate_summary["open_check_count"] == 1
    assert release_gate_summary["checks"] == [
        {
            "gate": "3",
            "index": 0,
            "label": "Authenticated: strict bearer RBAC matrix evidence passed.",
            "status": "fail",
            "note": "preflight bearer hash inventory mismatch",
            "owner": "Codex",
            "evidence": "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json",
            "blocking": True,
        },
        {
            "gate": "7",
            "index": 0,
            "label": "Evidence written to `.lovable/audits/current-run`.",
            "status": "pass",
            "note": "4 audit file(s) found",
            "owner": None,
            "evidence": ".lovable/audits/current-run",
            "blocking": False,
        },
    ]
    assert "runUrl" not in release_gate_summary
    remediation = item["operator_remediation"]
    assert remediation["github_environment"] == "dev"
    assert remediation["repository"] == "ajoe734/pantheon"
    assert remediation["required_secret_names"] == [
        "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
        "PANTHEON_BFF_RBAC_TOKENS_JSON",
    ]
    assert remediation["missing_secret_names"] == ["PANTHEON_BFF_RBAC_TOKENS_JSON"]
    assert remediation["missing_workflow_inputs"] == ["APPROVAL_RACE_ID"]
    assert remediation["invalid_inputs"] == [
        {"name": "SOAK_SECONDS", "reason": "must be at least 75"},
    ]
    assert remediation["secret_set_commands"] == [secret_set_command]
    assert remediation["workflow_dispatch"]["recommended_workflow"] == "Pantheon Stage 0 CI"
    assert remediation["workflow_dispatch"]["mode"] == "live-evidence"
    assert remediation["workflow_dispatch"]["environment"] == "dev"
    assert remediation["notes"] == [
        "Set secrets on the selected GitHub environment, not only at repository scope.",
    ]
    assert "token-value" not in json.dumps(remediation)
    assert "sourceType" not in item
    assert "linkType" not in item
    assert "linkedObjectSummary" not in item
    assert "artifactManifest" not in item


def test_management_evidence_ignores_malformed_current_run_verifier(tmp_path: Path) -> None:
    verifier_path = tmp_path / "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json"
    verifier_path.write_text("{not-json", encoding="utf-8")

    with _current_run_evidence_client(verifier_path) as client:
        response = client.get("/bff/management/evidence", headers=ADMIN_HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["items"] == []
    assert payload["page_info"]["total"] == 0
    assert payload["meta"]["surfaces"]["evidence_refs"]["status"] == "unavailable"


def test_management_evidence_preserves_capability_redaction() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence?ref_id=evref-b3-metric-001",
            headers=OPERATOR_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["summary"]["redacted_evidence"] == 1
        assert payload["meta"]["redacted_evidence_count"] == 1

        item = payload["data"]["items"][0]
        assert item["id"] == "evref-b3-metric-001"
        assert item["redacted"] is True
        assert item["required_capability"] == "metric.read"
        assert item["reason"] == "insufficient_capability"
        assert "requiredCapability" not in item


def test_knowledge_evidence_detail_exposes_linked_object_summary() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/api/v1/knowledge/evidence/evref-b3-alert-001",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ref_id"] == "evref-b3-alert-001"
        assert payload["linked_object_summary"] == {
            "entity_type": "artifact",
            "entity_ref": "artifact-b3-alpha",
            "display_label": "Runtime artifact",
        }
        assert payload["resolved_link"]["route_href"] == "/alerts/risk-alpha"
        assert payload["meta"]["surfaces"]["evidence_ref_detail"] in {"ok", "degraded"}


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
