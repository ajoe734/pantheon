"""BFF-B6-001-SEC-FIX regression coverage for Management NL security hardening."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer op-b6-sec:operator"}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@contextmanager
def _seeded_client(
    tmp_path: Path,
    monkeypatch,
    *,
    evidence_refs: dict | None = None,
) -> Iterator[TestClient]:
    read_surface_path = tmp_path / "read_surfaces.json"
    _write_json(
        read_surface_path,
        {
            "capital_pools": {
                "pool-alpha": {
                    "pool_id": "pool-alpha",
                    "name": "Alpha Pool",
                    "status": "active",
                    "tenant_id": "tenant-alpha",
                },
                "pool-beta": {
                    "pool_id": "pool-beta",
                    "name": "Beta Pool",
                    "status": "active",
                    "tenant_id": "tenant-beta",
                },
            },
            "runtime_bindings": {
                "rb-alpha": {
                    "binding_id": "rb-alpha",
                    "runtime_id": "rt-alpha",
                    "status": "running",
                    "deployment_stage": "paper",
                    "capital_pool_id": "pool-alpha",
                    "tenant_id": "tenant-alpha",
                },
                "rb-beta": {
                    "binding_id": "rb-beta",
                    "runtime_id": "rt-beta",
                    "status": "running",
                    "deployment_stage": "paper",
                    "capital_pool_id": "pool-beta",
                    "tenant_id": "tenant-beta",
                },
            },
            "telemetry_summaries": {
                "rt-alpha": {
                    "runtime_id": "rt-alpha",
                    "pnl": 1.25,
                    "fill_rate": 0.9,
                    "total_trades": 3,
                    "metrics": {"pnl": 1.25, "fill_rate": 0.9, "total_trades": 3},
                    "collected_at": "2026-05-25T12:00:00Z",
                },
                "rt-beta": {
                    "runtime_id": "rt-beta",
                    "pnl": 9.99,
                    "fill_rate": 0.5,
                    "total_trades": 99,
                    "metrics": {"pnl": 9.99, "fill_rate": 0.5, "total_trades": 99},
                    "collected_at": "2026-05-25T12:00:00Z",
                },
            },
            "agora_audit_events": {},
            "agora_sessions": {},
        },
    )
    if evidence_refs is not None:
        evidence_path = tmp_path / "evidence_refs.json"
        _write_json(evidence_path, evidence_refs)
        monkeypatch.setenv("PANTHEON_BFF_EVIDENCE_REF_STORE", str(evidence_path))
    else:
        monkeypatch.delenv("PANTHEON_BFF_EVIDENCE_REF_STORE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    original_store = bff_main.read_store
    bff_main.read_store = ReadSurfaceStore(
        str(read_surface_path),
        allow_local_snapshot_fallback=True,
    )
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
    bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
        storage_path="off",
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
    bff_main._sse_buffers["ask"].clear()
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_nl_ask_tenant_scopes_portfolio_summary(tmp_path, monkeypatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-tenant-portfolio"},
        )

    assert resp.status_code == 202, resp.text
    portfolio = resp.json()["data"]["summary_context"]["portfolio"]
    assert portfolio["total_pnl"] == 1.25
    assert portfolio["total_trades"] == 3


def test_nl_ask_filters_evidence_by_tenant_and_used_entities(tmp_path, monkeypatch) -> None:
    evidence_refs = {
        "ev-same-runtime": {
            "ref_id": "ev-same-runtime",
            "display_label": "same tenant runtime evidence",
            "tenant_id": "tenant-alpha",
            "evidence_type": "runtime",
            "source_document": {"source_type": "telemetry", "title": "Alpha telemetry"},
            "linked_object_summary": {"entity_type": "runtime", "entity_ref": "rt-alpha"},
        },
        "ev-tenant-agnostic": {
            "ref_id": "ev-tenant-agnostic",
            "display_label": "tenant agnostic runtime evidence",
            "evidence_type": "runtime",
            "source_document": {"source_type": "telemetry", "title": "Shared telemetry"},
            "linked_object_summary": {"entity_type": "runtime", "entity_ref": "rt-alpha"},
        },
        "ev-mismatched-tenant": {
            "ref_id": "ev-mismatched-tenant",
            "display_label": "mismatched tenant runtime evidence",
            "tenant_id": "tenant-beta",
            "evidence_type": "runtime",
            "source_document": {"source_type": "telemetry", "title": "Beta telemetry"},
            "linked_object_summary": {"entity_type": "runtime", "entity_ref": "rt-alpha"},
        },
        "ev-unrelated-runtime": {
            "ref_id": "ev-unrelated-runtime",
            "display_label": "unrelated runtime evidence",
            "tenant_id": "tenant-alpha",
            "evidence_type": "runtime",
            "source_document": {"source_type": "telemetry", "title": "Other telemetry"},
            "linked_object_summary": {"entity_type": "runtime", "entity_ref": "rt-other"},
        },
    }
    with _seeded_client(tmp_path, monkeypatch, evidence_refs=evidence_refs) as client:
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "How is the alpha runtime?", "focus": "trading_pulse"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-evidence-scope"},
        )

    assert resp.status_code == 202, resp.text
    refs = resp.json()["data"]["evidenceRefs"]
    ref_ids = {ref["ref_id"] for ref in refs}
    assert "ev-same-runtime" in ref_ids
    assert "ev-tenant-agnostic" in ref_ids
    assert "ev-mismatched-tenant" not in ref_ids
    assert "ev-unrelated-runtime" not in ref_ids


def test_nl_ask_rejects_question_over_2048_bytes(tmp_path, monkeypatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "x" * 2049},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-question-size"},
        )

    assert resp.status_code == 413, resp.text
    body = resp.json()
    assert body["error"]["code"] == "REQUEST_TOO_LARGE"
    assert body["error"]["details"]["precondition_failed"] == "question_size"


def test_high_risk_classifier_uses_boundaries_and_cjk_synonyms(tmp_path, monkeypatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        safe = client.post(
            "/bff/management/nl/ask",
            json={"question": "Summarize the predeployment strategy review", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-boundary-safe"},
        )
        assert safe.status_code == 202, safe.text

        refused = client.post(
            "/bff/management/nl/ask",
            json={"question": "請幫我重啟 runtime rt-alpha", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-cjk-risk"},
        )
        assert refused.status_code == 403, refused.text
        details = refused.json()["error"]["details"]
        assert details["matched_category"] == "runtime_control"
        assert details["matched_pattern"] == "重啟 runtime"


def test_happy_path_audit_failure_fails_closed_before_session_side_effects(tmp_path, monkeypatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        store = bff_main.read_store

        def fail_audit(event: dict) -> dict:
            raise OSError("audit store unavailable")

        monkeypatch.setattr(store, "record_agora_audit_event", fail_audit)
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio", "session_id": "audit-fail-session"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-audit-fail"},
        )

        assert resp.status_code == 503, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "audit_write"
        assert store.get_agora_session("audit-fail-session") is None
        assert bff_main._MGMT_NL_IDEMPOTENCY == {}
        assert list(bff_main._sse_buffers["ask"]) == []
