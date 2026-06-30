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
                        "route_href": "/knowledge/evidence/evref-b3-alert-001",
                        "created_at": "2026-05-23T09:05:00Z",
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
        try:
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
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
        assert item["redacted"] is False


def test_management_evidence_preserves_artifact_manifest_from_store() -> None:
    with _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence?ref_id=evref-b3-metric-001",
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["page_info"]["total"] == 1

        item = payload["items"][0]
        assert item["overall"] == "pass"
        assert item["artifactManifest"] == item["artifact_manifest"]
        manifest = item["artifactManifest"]
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
        yield TestClient(bff_main.app)
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

    with _current_run_evidence_client(verifier_path) as client:
        response = client.get(
            "/bff/management/evidence?ref_id=BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_info"]["total"] == 1
    assert payload["summary"]["bySourceType"] == {"workflow_artifact": 1}
    assert payload["facets"]["linkTypes"] == {"provenance": 1}
    assert payload["meta"]["surfaces"]["evidence_refs"]["source"] == "bff_current_run_artifact"
    assert payload["meta"]["surfaces"]["management_evidence"]["source"] == "bff_composed"

    item = payload["items"][0]
    assert item["id"] == "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY"
    assert item["sourceType"] == "workflow_artifact"
    assert item["linkType"] == "provenance"
    assert item["credibility"] == {"tier": "primary", "verified": True}
    assert item["linkedObjectSummary"]["entity_type"] == "artifact"
    assert item["overall"] == "pass"
    assert item["artifactManifest"] == manifest
    assert item["artifact_manifest"] == manifest
    assert item["criteria"]["rbac_matrix"]["status"] == "fail"
    assert item["criteria"]["rbac_matrix"]["note"] == "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON"
    assert item["criteria"]["current_run_only"]["status"] == "pass"


def test_management_evidence_ignores_malformed_current_run_verifier(tmp_path: Path) -> None:
    verifier_path = tmp_path / "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json"
    verifier_path.write_text("{not-json", encoding="utf-8")

    with _current_run_evidence_client(verifier_path) as client:
        response = client.get("/bff/management/evidence", headers=ADMIN_HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"] == []
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
        assert payload["summary"]["redactedEvidence"] == 1
        assert payload["meta"]["redacted_evidence_count"] == 1

        item = payload["items"][0]
        assert item["id"] == "evref-b3-metric-001"
        assert item["redacted"] is True
        assert item["requiredCapability"] == "metric.read"
        assert item["reason"] == "insufficient_capability"


def test_management_evidence_requires_read_authentication() -> None:
    with _evidence_client() as client:
        response = client.get("/bff/management/evidence")

        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
