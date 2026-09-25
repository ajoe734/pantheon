"""BFF-B6-001-SEC-FIX regression coverage for Management NL security hardening."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    get_management_nl_read_store,
    get_management_nl_sse_buffer,
    management_nl_test_client,
)


@contextmanager
def _real_main_management_nl_test_client(read_surface, *, raise_server_exceptions=False):
    """BFF-TEST-MIGRATION-REMAINING-IMPORTERS-001: GENUINE BLOCKER, narrow
    and function-scoped. The seam-based tests/rebalance_authority_test_
    support.management_nl_test_client() (built on assistant.management_
    service + core.app_factory.compose_bff_app()) correctly serves this
    file's other tests, but test_nl_ask_tenant_scopes_portfolio_summary and
    test_nl_ask_filters_evidence_by_tenant_and_used_entities exercise
    portfolio telemetry-row projection and tenant/entity evidence scoping
    that main.py only wires with real business logic (not stub defaults) at
    its own module import time (main.py's _project_operator_runtime_state_
    row and related context-service collaborators, main.py lines ~3465+,
    ~9080-9107) -- collaborator seams exist on assistant.management_service
    (get_/set_/reset_project_operator_runtime_state_row) but their real
    implementations are main.py-exclusive, with no extracted seam standing
    in for them when compose_bff_app() composes without main.py. Not a
    declared artifact of this task; per explicit governance instruction,
    this narrow, real, main.py-backed client is used only for these two
    tests rather than weakening their assertions or faking the app, and
    this file remains a real, reported (not allowlisted) main.py importer.
    """
    import importlib

    real_main = importlib.import_module("services.control_plane.bff.main")
    import services.control_plane.bff.personas.service as personas_service

    from services.control_plane.bff.tests.rebalance_authority_test_support import (
        restore_real_main_read_surface,
        sync_real_main_read_surface,
    )

    old_main_store = getattr(real_main, "read_store", None)
    old_persona_store = getattr(personas_service, "read_store", None)
    context_svc = getattr(real_main, "_management_ai_context_service", None)
    old_context_fn = getattr(context_svc, "_get_read_store", None) if context_svc is not None else None
    previous_sub_ports = sync_real_main_read_surface(real_main, read_surface)
    try:
        setattr(real_main, "read_store", read_surface)
        setattr(personas_service, "read_store", read_surface)
        if context_svc is not None:
            context_svc._get_read_store = (lambda: read_surface) if read_surface is not None else None
        client = TestClient(real_main.app, raise_server_exceptions=raise_server_exceptions)
        yield client
    finally:
        setattr(real_main, "read_store", old_main_store)
        setattr(personas_service, "read_store", old_persona_store)
        if context_svc is not None:
            context_svc._get_read_store = old_context_fn
        restore_real_main_read_surface(real_main, previous_sub_ports)


@pytest.fixture(autouse=True)
def _management_nl_command_idempotency_default_path(monkeypatch, tmp_path):
    """BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: durable admission via
    ManagementNlCommandIdempotencyStore is unconditional for both nl/ask
    transports; give it a writable per-test default path since the module
    default (/data/bff/...) does not exist in the test sandbox."""
    if not os.environ.get("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH"):
        monkeypatch.setenv(
            "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
            str(tmp_path / "management-nl-command-idempotency.json"),
        )


OPERATOR_HEADERS = {"Authorization": "Bearer op-b6-sec:operator"}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@contextmanager
def _seeded_client(
    tmp_path: Path,
    monkeypatch,
    *,
    evidence_refs: dict | None = None,
    use_real_main: bool = False,
) -> Iterator[TestClient]:
    read_surface_path = tmp_path / "read_surfaces.json"
    seeded_data = {
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
    }
    _write_json(read_surface_path, seeded_data)
    if evidence_refs is not None:
        evidence_path = tmp_path / "evidence_refs.json"
        _write_json(evidence_path, evidence_refs)
        monkeypatch.setenv("PANTHEON_BFF_EVIDENCE_REF_STORE", str(evidence_path))
    else:
        monkeypatch.delenv("PANTHEON_BFF_EVIDENCE_REF_STORE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    store = create_in_memory_read_surface_ports()
    capital_pools = list(seeded_data["capital_pools"].values())
    runtime_bindings = list(seeded_data["runtime_bindings"].values())
    telemetry_summaries = seeded_data["telemetry_summaries"]
    evidence_records = list((evidence_refs or {}).values())
    store.list_capital_pools = lambda *args, **kwargs: json.loads(json.dumps(capital_pools))
    store.list_runtime_bindings = lambda *args, **kwargs: json.loads(json.dumps(runtime_bindings))
    store.get_telemetry_summary = lambda runtime_id: json.loads(
        json.dumps(telemetry_summaries.get(str(runtime_id)))
    ) if telemetry_summaries.get(str(runtime_id)) is not None else None
    store.record_agora_audit_event = lambda event: event
    store.get_agora_session = lambda session_id: None

    def _rec_matches_tenant(rec, tid, include_tenant_agnostic=True):
        if not tid:
            return True
        rt = rec.get("tenant_id") or rec.get("tenantId")
        if rt is None:
            return include_tenant_agnostic
        return str(rt) == str(tid)

    def _ev_matches_scope(rec, linked_entities=None, source_types=None):
        pairs = set()
        lo = rec.get("linked_object_summary")
        if isinstance(lo, dict):
            t = str(lo.get("entity_type") or "").strip().lower()
            r = str(lo.get("entity_ref") or "").strip()
            if t and r:
                pairs.add((t, r))
        if linked_entities:
            normalized_entities = {
                (str(e.get("type") or e.get("entity_type") or "").strip().lower(), str(e.get("ref") or e.get("entity_ref") or "").strip())
                if isinstance(e, dict) else (str(e[0]).strip().lower(), str(e[1]).strip())
                for e in linked_entities
            }
            if pairs:
                return bool(normalized_entities and pairs.intersection(normalized_entities))
        if source_types:
            st = str(rec.get("evidence_type") or (rec.get("source_document") or {}).get("source_type") or "").strip().lower()
            return st in {str(s).strip().lower() for s in source_types}
        return True

    def list_evidence_refs(*, tenant_id=None, include_tenant_agnostic=True, linked_entities=None, source_types=None, **kwargs):
        return [
            json.loads(json.dumps(record))
            for record in evidence_records
            if _rec_matches_tenant(
                record,
                tenant_id,
                include_tenant_agnostic=include_tenant_agnostic,
            )
            and _ev_matches_scope(
                record,
                linked_entities=linked_entities,
                source_types=source_types,
            )
        ]

    store.list_evidence_refs = list_evidence_refs
    client_cm = (
        _real_main_management_nl_test_client(store, raise_server_exceptions=False)
        if use_real_main
        else management_nl_test_client(store, raise_server_exceptions=False)
    )
    with client_cm as client:
        yield client


def test_nl_ask_tenant_scopes_portfolio_summary(tmp_path, monkeypatch) -> None:
    with _seeded_client(tmp_path, monkeypatch, use_real_main=True) as client:
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
    with _seeded_client(tmp_path, monkeypatch, evidence_refs=evidence_refs, use_real_main=True) as client:
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "How is the alpha runtime?", "focus": "trading_pulse"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-sec-evidence-scope"},
        )

    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert "evidenceRefs" not in data
    refs = data["evidence_refs"]
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
        store = get_management_nl_read_store()

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
        # BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: durable command admission is
        # unconditional now, so a reservation for this key legitimately
        # exists (in_progress, not yet released) -- the legacy in-memory
        # dict this assertion used to check no longer exists. The
        # behavioural guarantee under test (no session/SSE side effects
        # were committed before the fail-closed audit-write error) is still
        # covered by the assertions above.
        assert get_management_nl_sse_buffer("ask") == []
