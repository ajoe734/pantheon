"""
BFF-B3-007: contract tests for GET /bff/management/persona-intent.

The route is a read-only Management aggregate. It composes redacted persona
trace, trainer, and Agora intent summaries without exposing raw transcripts,
message bodies, tool lists, or capability internals.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.tests.conftest import make_composed_persona_service

# Local re-implementation of read_store._load_default_fixture_pack_datasets:
# merges the same static, committed fixture-pack JSON files directly off
# disk, with no import from / coupling to read_store.py's adapter machinery.
_FIXTURE_PACK_DIR = Path(os.path.dirname(os.path.dirname(__file__))) / "data"
_FIXTURE_PACK_PATHS = (
    _FIXTURE_PACK_DIR / "fixtures_pack_a.json",
    _FIXTURE_PACK_DIR / "fixtures_pack_b.json",
    _FIXTURE_PACK_DIR / "fixtures_pack_c.json",
)
_FIXTURE_DATASET_ALIASES = {
    "deployments": "deployment_plans",
    "runtimes": "runtime_bindings",
}
_FIXTURE_RECORD_KEYS = [
    "id", "analysis_id", "entry_id", "decision_id", "intervention_id", "job_id",
    "plan_id", "program_id", "pool_id", "persona_id", "server_id", "signal_id",
    "skill_id", "session_id", "sessionId", "packet_id", "strategy_id",
    "experiment_id", "artifact_id", "rebalance_id", "binding_id", "runtime_id",
    "tool_id", "channel_id",
]


def _fixture_pack_record_key(record: Any) -> str:
    if isinstance(record, dict):
        for key in _FIXTURE_RECORD_KEYS:
            value = record.get(key)
            if value not in (None, ""):
                return str(value)
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _load_fixture_pack_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return {}
    return json.loads(json.dumps(datasets))


def _merge_fixture_pack(target: dict[str, Any], fixture: dict[str, Any]) -> None:
    for raw_key, incoming in fixture.items():
        key = _FIXTURE_DATASET_ALIASES.get(raw_key, raw_key)
        if isinstance(incoming, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                target[key] = json.loads(json.dumps(incoming))
                continue
            for record_key, record in incoming.items():
                if record_key not in existing:
                    existing[record_key] = json.loads(json.dumps(record))
            continue
        if isinstance(incoming, list):
            existing = target.get(key)
            if not isinstance(existing, list):
                target[key] = json.loads(json.dumps(incoming))
                continue
            seen = {_fixture_pack_record_key(record) for record in existing}
            for record in incoming:
                record_key = _fixture_pack_record_key(record)
                if record_key in seen:
                    continue
                existing.append(json.loads(json.dumps(record)))
                seen.add(record_key)


def _load_default_fixture_pack_datasets() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in _FIXTURE_PACK_PATHS:
        _merge_fixture_pack(merged, _load_fixture_pack_file(path))
    return merged

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-persona-intent:operator"}


class _PersonaIntentTestStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        persona_capital_kwargs = {
            "personas": list(data.get("personas", {}).values()) if isinstance(data.get("personas"), dict) else data.get("personas", []),
        }
        self.ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs=persona_capital_kwargs,
        )

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("personas") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def get_sessions_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        val = self.data.get("sessions") or {}
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not persona_id:
            return items
        return [s for s in items if s.get("persona_id") == persona_id or s.get("target_id") == persona_id]

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        val = self.data.get("teaching_sessions") or {}
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not persona_id:
            return items
        return [s for s in items if s.get("persona_id") == persona_id or s.get("target_id") == persona_id]

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("agora_sessions") or self.data.get("consultation_sessions") or {}
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not items:
            items = [
                {
                    "session_id": "cs-20260410-001",
                    "persona_id": "persona-alpha",
                    "persona_ids": ["persona-alpha"],
                    "session_type": "consult",
                    "status": "terminated",
                    "started_at": "2026-04-10T10:00:00Z",
                    "messages": [{"id": "m1", "created_at": "2026-04-10T10:05:00Z", "role": "user"}],
                }
            ]
        else:
            for s in items:
                if not s.get("messages"):
                    s["messages"] = [{"id": "m1", "created_at": s.get("started_at") or "2026-04-10T10:05:00Z", "role": "user"}]
        return items

    def list_consult_requests(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_agora_sessions(**kwargs)

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        snaps = self.data.get("capability_snapshots") or {}
        if isinstance(snaps, dict) and persona_id in snaps:
            return snaps[persona_id]
        return {
            "persona_id": persona_id,
            "capabilities": [{"name": "tool_exec", "effective": True}],
            "tools_enabled": ["tool_exec"],
            "effective_tools": ["tool_exec"],
        }

    def dataset_source(self, dataset: str) -> str:
        key = dataset
        if dataset == "agora_sessions" and "agora_sessions" not in self.data:
            key = "consultation_sessions"
        if key in self.data:
            return "local_snapshot" if self.data[key] is not None else "missing"
        return self.ports.dataset_source(dataset)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.ports, name, None)
        if attr is not None and callable(attr):
            def _safe_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except TypeError:
                    return attr(*args)
            return _safe_wrapper
        if attr is not None:
            return attr
        if name.startswith("list_") and name[5:] in self.data:
            val = self.data[name[5:]]
            items = list(val.values()) if isinstance(val, dict) else val
            return lambda **kw: items
        raise AttributeError(f"'_PersonaIntentTestStore' has no attribute '{name}'")


class _FakeOwner:
    pass


class _FakeCommandStore:
    def get_all(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass


def _fresh_client(td: str) -> TestClient:
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    snapshot_path = os.path.join(td, "read_surfaces.json")
    if os.path.exists(snapshot_path):
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = _load_default_fixture_pack_datasets()
    else:
        default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "read_surfaces.json")
        if os.path.exists(default_path):
            try:
                with open(default_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = _load_default_fixture_pack_datasets()
        else:
            data = _load_default_fixture_pack_datasets()

    for p in data.get("personas", {}).values() if isinstance(data.get("personas"), dict) else data.get("personas", []):
        if isinstance(p, dict):
            p.setdefault("tenant_id", "pantheon-dev")

    store = _PersonaIntentTestStore(data)
    service = PersonaService(
        read_store=store,
        write_owner=_FakeOwner(),
        ranking_write_owner=_FakeOwner(),
        command_store=_FakeCommandStore(),
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    app.include_router(create_personas_router(service=service))
    return TestClient(app)


def test_persona_intent_composes_redacted_trace_trainer_and_agora_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)

        # Default GET /bff/management/persona-intent returns trade_intent items only
        default_resp = client.get("/bff/management/persona-intent", headers=OPERATOR_HEADERS)
        assert default_resp.status_code == 200, default_resp.text
        default_body = default_resp.json()
        assert set(default_body.keys()) == {"data", "page_info", "meta"}
        assert set(default_body["data"].keys()) == {"id", "items", "summary"}
        default_items = default_body["data"]["items"]
        assert len(default_items) >= 1
        assert all(item["kind"] == "trade_intent" for item in default_items)
        assert default_body["data"]["summary"]["trade_intent_count"] == len(default_items)
        assert default_body["data"]["summary"]["available_by_kind"]["trade_intent"] >= 1
        assert default_body["data"]["summary"]["available_by_kind"]["trainer"] >= 1
        assert default_body["data"]["summary"]["available_by_kind"]["consultation"] >= 1

        # Querying kind=all composes trade_intent, trainer, consultation, and meeting items
        resp = client.get("/bff/management/persona-intent?kind=all", headers=OPERATOR_HEADERS)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"data", "page_info", "meta"}
        assert set(body["data"].keys()) == {"id", "items", "summary"}
        items = body["data"]["items"]
        summary = body["data"]["summary"]
        assert summary["persona_trace_count"] >= 1
        assert summary["trainer_session_count"] >= 1
        assert summary["agora_session_count"] >= 1
        assert summary["redacted_item_count"] == summary["total_items"]
        assert "bySourceType" not in summary
        assert "byStatus" not in summary
        assert "byIntent" not in summary
        assert body["meta"]["surfaces"]["management_persona_intent"]["source"] == "bff_composed"
        for surface in [
            "persona_traces",
            "persona_sessions",
            "capability_snapshots",
            "teaching_sessions",
            "agora_sessions",
        ]:
            assert surface in body["meta"]["surfaces"]

        by_type = {item["source_type"]: item for item in items}
        assert by_type["persona_trace"]["kind"] == "trade_intent"
        assert by_type["persona_trace"]["trace"]["trace_id"]
        assert by_type["persona_trace"]["trace"]["capability_summary"]["effective_tool_count"] >= 1
        assert by_type["persona_trace"]["tools_used"]
        assert by_type["persona_trace"]["topic"]
        assert by_type["persona_trace"]["proposed_action"]
        assert by_type["persona_trace"]["outcome"]
        assert by_type["persona_trace"]["decision"]
        assert by_type["persona_trace"]["persona_display_name"] == "Alpha Persona"
        assert by_type["trainer_session"]["kind"] == "trainer"
        assert by_type["trainer_session"]["trainer"]["outcome_count"] >= 1
        assert by_type["trainer_session"]["proposed_action"]
        assert by_type["trainer_session"]["outcome"]
        assert by_type["agora_session"]["kind"] == "consultation"
        assert by_type["agora_session"]["agora"]["message_count"] >= 1
        assert by_type["agora_session"]["topic"]
        assert by_type["agora_session"]["proposed_action"]
        assert by_type["agora_session"]["outcome"]
        assert "sourceType" not in by_type["persona_trace"]
        assert "personaId" not in by_type["persona_trace"]
        assert "sessionId" not in by_type["agora_session"]["agora"]


def test_persona_intent_supports_filters_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)

        resp = client.get(
            "/bff/management/persona-intent"
            "?source_type=persona_trace&persona_id=persona-alpha&status=active&page_size=1",
            headers=OPERATOR_HEADERS,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["page_size"] == 1
        assert body["page_info"]["total"] == 1
        items = body["data"]["items"]
        assert len(items) == 1
        item = items[0]
        assert item["source_type"] == "persona_trace"
        assert item["kind"] == "trade_intent"
        assert item["persona_id"] == "persona-alpha"
        assert item["persona_display_name"] == "Alpha Persona"
        assert item["status"] == "active"
        assert item["redaction"]["policy"] == "sd_persona_meetings_12_2_private_memory_boundary"
        assert item["redaction"]["reason_code"] == "private_memory_withheld"
        assert any(wf["reason_code"] == "private_memory_withheld" for wf in item["withheld_fields"])
        assert any(wf["reason_code"] == "private_interpretation_withheld" for wf in item["withheld_fields"])


def test_persona_intent_requires_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/management/persona-intent")

        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_persona_intent_real_read_surface_ports_empty_and_tenant_review() -> None:
    """Regression test for P1: verify real ReadSurfacePorts doesn't raise AttributeError on persona-intent calls."""
    from services.control_plane.bff.personas import service as ps

    real_ports = create_in_memory_read_surface_ports()
    old_store = ps._composed_persona_service
    try:
        ps._composed_persona_service = make_composed_persona_service(read_store=real_ports)
        items, p_sessions, t_sessions, agora_sessions = ps._persona_intent_all_items("tenant-review")
        assert items == []
        assert p_sessions == []
        assert t_sessions == []
        assert agora_sessions == []
    finally:
        ps._composed_persona_service = old_store


def test_persona_intent_real_read_surface_ports_consultation_projection_and_tenant_filtering() -> None:
    """Regression test for P1: verify non-empty real ReadSurfacePorts projects consultations and respects tenant filtering."""
    from services.control_plane.bff.personas import service as ps

    personas = [
        {"id": "persona-alpha-1", "persona_id": "persona-alpha-1", "tenant_id": "tenant-alpha", "name": "Alpha 1"},
        {"id": "persona-beta-1", "persona_id": "persona-beta-1", "tenant_id": "tenant-beta", "name": "Beta 1"},
    ]
    consult_requests = [
        {
            "id": "cr-alpha-001",
            "request_id": "cr-alpha-001",
            "from_persona_id": "persona-alpha-1",
            "tenant_id": "tenant-alpha",
            "task": "Alpha risk consultation",
            "status": "completed",
            "created_at": "2026-10-01T01:00:00Z",
        },
        {
            "id": "cr-beta-001",
            "request_id": "cr-beta-001",
            "from_persona_id": "persona-beta-1",
            "tenant_id": "tenant-beta",
            "task": "Beta execution review",
            "status": "created",
            "created_at": "2026-10-01T01:05:00Z",
        },
        {
            "id": "consult-review-001",
            "request_id": "consult-review-001",
            "title": "Retained canonical consultation",
            "status": "completed",
            "created_at": "2026-10-01T01:10:00Z",
        },
    ]

    real_ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": personas},
        operations_consultation_kwargs={"consult_requests": consult_requests},
    )

    old_store = ps._composed_persona_service
    try:
        ps._composed_persona_service = make_composed_persona_service(read_store=real_ports)

        # 1. Tenant Alpha query
        items_alpha, _, _, agora_alpha = ps._persona_intent_all_items("tenant-alpha")
        alpha_item_ids = [it["id"] for it in items_alpha]
        assert "agora_session:cr-alpha-001" in alpha_item_ids
        assert "agora_session:consult-review-001" in alpha_item_ids
        assert "agora_session:cr-beta-001" not in alpha_item_ids

        # 2. Tenant Beta query
        items_beta, _, _, agora_beta = ps._persona_intent_all_items("tenant-beta")
        beta_item_ids = [it["id"] for it in items_beta]
        assert "agora_session:cr-beta-001" in beta_item_ids
        assert "agora_session:consult-review-001" in beta_item_ids
        assert "agora_session:cr-alpha-001" not in beta_item_ids

        # 3. HTTP route integration test via TestClient
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        service = PersonaService(
            read_store=real_ports,
            write_owner=_FakeOwner(),
            ranking_write_owner=_FakeOwner(),
            command_store=_FakeCommandStore(),
        )
        app = FastAPI()
        app.include_router(create_personas_router(service=service))

        with TestClient(app) as client:
            # Default query returns trade intents only (consultations excluded from default)
            default_resp = client.get("/bff/management/persona-intent", headers={"Authorization": "Bearer op-review:operator"})
            assert default_resp.status_code == 200, default_resp.text
            assert default_resp.json()["data"]["items"] == []

            # Explicit kind=consultation returns the consultation items
            resp = client.get("/bff/management/persona-intent?kind=consultation", headers={"Authorization": "Bearer op-review:operator"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            data = body["data"]
            assert data["summary"]["agora_session_count"] >= 1
            item_ids = [it["id"] for it in data["items"]]
            assert "agora_session:consult-review-001" in item_ids

            # Verify projected agora item structure
            canonical_item = next(it for it in data["items"] if it["id"] == "agora_session:consult-review-001")
            assert canonical_item["source_type"] == "agora_session"
            assert canonical_item["kind"] == "consultation"
            assert canonical_item["status"] == "completed"
            assert canonical_item["redacted"] is True
            assert "agora" in canonical_item
            assert canonical_item["agora"]["mode"] == "agora_session"
    finally:
        ps._composed_persona_service = old_store


def test_persona_intent_real_domain_consultation_port_store_fallback() -> None:
    """Regression test: verify DomainConsultationPort preserves tenant_id and task_summary, preventing foreign leak and generic fallback."""
    from services.consultation.models import ConsultRequest
    from services.consultation.store import ConsultationStore
    from services.control_plane.bff.personas import service as ps
    from services.control_plane.bff.ports import DomainConsultationPort

    class EmptyClient:
        def list_requests(self) -> list[Any]:
            return []

    rows = []
    for suffix, tenant, persona in [
        ("own", "pantheon-dev", "persona-own"),
        ("foreign", "tenant-beta", "persona-beta"),
        ("own-no-persona", "pantheon-dev", None),
        ("foreign-no-persona", "tenant-beta", None),
    ]:
        req = ConsultRequest(
            request_id="consult-" + suffix,
            tenant_id=tenant,
            request_type="strategy_review",
            requested_by={"actor_type": "human", "actor_id": "reviewer-" + suffix},
            from_persona_id=persona,
            target_type="strategy",
            target_id="strategy-" + suffix,
            task="Review strategy " + suffix,
            consultation_type="strategy_review",
            status="published",
            trace_id="trace-" + suffix,
            created_at="2026-10-01T01:00:00Z",
        )
        rows.append(req.model_dump(mode="json"))

    store_dir = tempfile.TemporaryDirectory(prefix="agora-persona-consult-store-")
    try:
        canonical_store = ConsultationStore(store_dir.name)
        for row in rows:
            canonical_store.put_request(ConsultRequest(**row))

        real_ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": [
                    {"id": "persona-own", "persona_id": "persona-own", "name": "Own Momentum Persona", "tenant_id": "pantheon-dev"},
                    {"id": "persona-beta", "persona_id": "persona-beta", "name": "Beta Persona", "tenant_id": "tenant-beta"},
                ]
            }
        )
        real_ports.operations_consultation = DomainConsultationPort(client=EmptyClient(), store=canonical_store)

        old_store = ps._composed_persona_service
        try:
            ps._composed_persona_service = make_composed_persona_service(read_store=real_ports)
            own_items, _, _, _ = ps._persona_intent_all_items("pantheon-dev")
            foreign_items, _, _, _ = ps._persona_intent_all_items("tenant-beta")

            own_ids = [x["id"] for x in own_items]
            foreign_ids = [x["id"] for x in foreign_items]

            assert set(own_ids) == {"agora_session:consult-own", "agora_session:consult-own-no-persona"}
            assert set(foreign_ids) == {"agora_session:consult-foreign", "agora_session:consult-foreign-no-persona"}

            # Verify task_summary preserved in title and topic, while summary includes proposed action / outcome
            own_item = next(it for it in own_items if it["id"] == "agora_session:consult-own")
            assert own_item["title"] == "Review strategy own"
            assert own_item["topic"] == "Review strategy own"
            assert own_item["agora"]["topic"] == "Review strategy own"
            assert own_item["summary"] != own_item["title"]
            assert "Review strategy own" in own_item["summary"]
            assert own_item["proposed_action"] == "strategy for strategy-own"
            assert own_item["outcome"] == "completed"
            assert own_item["decision"] == "completed"
            assert own_item["persona_display_name"] == "Own Momentum Persona"

            # HTTP route test
            os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
            os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
            service = PersonaService(
                read_store=real_ports,
                write_owner=_FakeOwner(),
                ranking_write_owner=_FakeOwner(),
                command_store=_FakeCommandStore(),
            )
            app = FastAPI()
            app.include_router(create_personas_router(service=service))

            with TestClient(app) as client:
                resp = client.get("/bff/management/persona-intent?kind=consultation", headers={"Authorization": "Bearer op-review:operator"})
                assert resp.status_code == 200, resp.text
                body = resp.json()
                http_items = body["data"]["items"]
                http_ids = [it["id"] for it in http_items]
                assert set(http_ids) == {"agora_session:consult-own", "agora_session:consult-own-no-persona"}
                assert body["data"]["summary"]["agora_session_count"] == 2
                assert body["data"]["summary"]["persona_ids"] == ["persona-own"]
        finally:
            ps._composed_persona_service = old_store
    finally:
        store_dir.cleanup()


def test_persona_intent_reproduces_hosted_20261120_sweep_and_separates_kinds_and_strips_private_memory() -> None:
    """Reproduce the 2026-11-20 coordinator hosted read (BFF fb8ed951d; mgmt-readability-sweep-20261120)
    and verify SD_PERSONA_MEETINGS §12.2 content, kind filtering, withhold reason codes, display names,
    and private memory / private interpretation stripping.
    """
    with tempfile.TemporaryDirectory() as td:
        snapshot = {
            "personas": {
                "persona-ee7112a829d38b9135ac": {
                    "id": "persona-ee7112a829d38b9135ac",
                    "persona_id": "persona-ee7112a829d38b9135ac",
                    "name": "TW Momentum Alpha",
                    "display_name": "TW Momentum Alpha",
                    "tenant_id": "pantheon-dev",
                },
                "persona-6507750312870d03a241": {
                    "id": "persona-6507750312870d03a241",
                    "persona_id": "persona-6507750312870d03a241",
                    "name": "TW Mean Reversion Beta",
                    "display_name": "TW Mean Reversion Beta",
                    "tenant_id": "pantheon-dev",
                },
                "persona-risk-officer": {
                    "id": "persona-risk-officer",
                    "persona_id": "persona-risk-officer",
                    "name": "Institutional Risk Officer",
                    "display_name": "Institutional Risk Officer",
                    "tenant_id": "pantheon-dev",
                },
            },
            "sessions": {
                "cycle-trade-20261120-001": {
                    "id": "cycle-trade-20261120-001",
                    "session_id": "cycle-trade-20261120-001",
                    "persona_id": "persona-ee7112a829d38b9135ac",
                    "session_type": "daily_cycle",
                    "status": "completed",
                    "topic": "Rebalance TW semiconductor momentum basket for 2026-11-20",
                    "proposed_action": "Increase 2330.TW weight by +2.5% and trim 2454.TW by -2.5% in paper pool v3",
                    "outcome": "approved_for_paper_execution",
                    "decision": "submit_reweight_proposal",
                    "tools_used": ["memory_search", "pool_virtual_book_read", "regime_state_lookup"],
                    "consulted_personas": ["persona-risk-officer"],
                    "evidence_refs": ["ev-cycle-20261120-001", "case-file-20261120-001"],
                    "risk_flags": ["concentration_near_cap"],
                    "started_at": "2026-11-20T01:00:00Z",
                    "updated_at": "2026-11-20T01:05:00Z",
                    "private_memory": "SECRET_PRIVATE_MEMORY_NEVER_LEAK_2330_BIAS",
                    "persona_private_memory": {"bank": "private-ee71", "note": "SECRET_BANK_NOTE"},
                    "private_interpretation": "SECRET_PRIVATE_INTERPRETATION_TURN_TEXT",
                    "persona_interpretation": "SECRET_PERSONA_INTERPRETATION_TEXT",
                    "memory_trace": ["SECRET_MEMORY_TRACE_ENTRY"],
                    "reasoning_trace": "SECRET_REASONING_TRACE",
                }
            },
            "teaching_sessions": {
                "teach-20261120-001": {
                    "id": "teach-20261120-001",
                    "session_id": "teach-20261120-001",
                    "persona_id": "persona-ee7112a829d38b9135ac",
                    "mode": "trainer",
                    "status": "completed",
                    "topic": "High-volatility stop-loss calibration",
                    "outcomes": ["Tightened intraday trailing stop from 3.5% to 2.5%"],
                    "tools_used": ["replay_simulator"],
                    "started_at": "2026-11-19T08:00:00Z",
                    "completed_at": "2026-11-19T08:30:00Z",
                    "private_memory": "SECRET_TRAINER_PRIVATE_MEMORY",
                }
            },
            "agora_sessions": {
                # Exact reproduction of hosted sweep row consult-envelope-publication-98f3c36b851627e3814268877ddb33db
                "consult-envelope-publication-98f3c36b851627e3814268877ddb33db": {
                    "request_id": "consult-envelope-publication-98f3c36b851627e3814268877ddb33db",
                    "from_persona_id": "persona-ee7112a829d38b9135ac",
                    "consultation_type": "persona_panel",
                    "target_type": "envelope_publication",
                    "target_id": "pool-v3",
                    "task_summary": "Risk review of paper envelope publication of pool v3 (live membership changed)",
                    "status": "in_progress",
                    "created_at": "2026-11-19T10:06:11Z",
                    "tools_used": ["case_file_reader", "risk_limit_verifier"],
                    "private_memory": "SECRET_PANEL_PRIVATE_MEMORY",
                    "private_interpretation": "SECRET_PANEL_PRE_READ_INTERPRETATION",
                    "case_file_items": [
                        {
                            "item_id": "cf-private-1",
                            "scope": "persona_private",
                            "excerpt": "SECRET_CASE_FILE_PRIVATE_EXCERPT",
                        },
                        {
                            "item_id": "cf-public-1",
                            "scope": "committee",
                            "excerpt": "Shared pool v3 envelope parameters",
                        },
                    ],
                },
                # Exact reproduction of hosted sweep deliberative meeting row mtg-16d42d0b44dfeab5
                "mtg-16d42d0b44dfeab5": {
                    "request_id": "mtg-16d42d0b44dfeab5",
                    "meeting_id": "mtg-16d42d0b44dfeab5",
                    "from_persona_id": "persona-6507750312870d03a241",
                    "chair_persona_id": "persona-6507750312870d03a241",
                    "consultation_type": "persona_meeting",
                    "meeting_type": "optimisation_review",
                    "task_summary": "Optimisation review for persona-6507750312870d03a241: lessons:5",
                    "status": "submitted",
                    "outcome": "propose_harness_patch",
                    "proposals": [
                        {
                            "proposal_id": "prop-mtg-001",
                            "kind": "harness_patch",
                            "summary": "Adopt 20-day volatility regime filter before mean-reversion entry",
                        }
                    ],
                    "participants": [
                        {"persona_id": "persona-6507750312870d03a241", "role": "chair"},
                        {"persona_id": "persona-ee7112a829d38b9135ac", "role": "peer"},
                        {"persona_id": "persona-risk-officer", "role": "risk"},
                    ],
                    "tools_used": ["lesson_candidate_reader", "shadow_counterfactual_eval"],
                    "created_at": "2026-11-19T10:03:31Z",
                    "private_memory": "SECRET_MEETING_PRIVATE_MEMORY",
                    "private_interpretations": ["SECRET_MEETING_INTERPRETATION_1"],
                },
            },
        }
        (Path(td) / "read_surfaces.json").write_text(json.dumps(snapshot), encoding="utf-8")
        client = _fresh_client(td)

        # 1. Default list returns ONLY trade_intent items; the pool v3 Agora consult is NOT listed as a trade intent
        resp_default = client.get("/bff/management/persona-intent", headers=OPERATOR_HEADERS)
        assert resp_default.status_code == 200, resp_default.text
        default_data = resp_default.json()["data"]
        default_items = default_data["items"]
        assert len(default_items) == 1
        trade_item = default_items[0]
        assert trade_item["id"] == "persona_trace:cycle-trade-20261120-001"
        assert trade_item["kind"] == "trade_intent"
        assert trade_item["topic"] == "Rebalance TW semiconductor momentum basket for 2026-11-20"
        assert trade_item["proposed_action"] == "Increase 2330.TW weight by +2.5% and trim 2454.TW by -2.5% in paper pool v3"
        assert trade_item["outcome"] == "approved_for_paper_execution"
        assert trade_item["decision"] == "submit_reweight_proposal"
        assert trade_item["tools_used"] == ["memory_search", "pool_virtual_book_read", "regime_state_lookup"]
        assert trade_item["summary"] != trade_item["title"]
        assert trade_item["persona_id"] == "persona-ee7112a829d38b9135ac"
        assert trade_item["persona_display_name"] == "TW Momentum Alpha"
        assert trade_item["consulted_personas"] == [
            {
                "id": "persona-risk-officer",
                "persona_id": "persona-risk-officer",
                "name": "Institutional Risk Officer",
                "display_name": "Institutional Risk Officer",
            }
        ]

        # 2. Kind filter separates consultation, meeting, and trainer sessions
        resp_consult = client.get("/bff/management/persona-intent?kind=consultation", headers=OPERATOR_HEADERS)
        assert resp_consult.status_code == 200, resp_consult.text
        consult_items = resp_consult.json()["data"]["items"]
        assert len(consult_items) == 1
        consult_item = consult_items[0]
        assert consult_item["id"] == "agora_session:consult-envelope-publication-98f3c36b851627e3814268877ddb33db"
        assert consult_item["kind"] == "consultation"
        assert consult_item["topic"] == "Risk review of paper envelope publication of pool v3 (live membership changed)"
        assert consult_item["proposed_action"] == "envelope publication for pool-v3"
        assert consult_item["outcome"] == "in_progress"
        assert consult_item["decision"] == "in_progress"
        assert consult_item["tools_used"] == ["case_file_reader", "risk_limit_verifier"]
        assert consult_item["summary"] != consult_item["title"]
        assert consult_item["persona_id"] == "persona-ee7112a829d38b9135ac"
        assert consult_item["persona_display_name"] == "TW Momentum Alpha"
        # Does not list only its own persona as consulted: consults Risk Officer with display name
        assert consult_item["consulted_personas"] == [
            {
                "id": "persona-risk-officer",
                "persona_id": "persona-risk-officer",
                "name": "Institutional Risk Officer",
                "display_name": "Institutional Risk Officer",
            }
        ]

        resp_meeting = client.get("/bff/management/persona-intent?kind=meeting", headers=OPERATOR_HEADERS)
        assert resp_meeting.status_code == 200, resp_meeting.text
        meeting_items = resp_meeting.json()["data"]["items"]
        assert len(meeting_items) == 1
        meeting_item = meeting_items[0]
        assert meeting_item["id"] == "agora_session:mtg-16d42d0b44dfeab5"
        assert meeting_item["kind"] == "meeting"
        assert meeting_item["topic"] == "Optimisation review for persona-6507750312870d03a241: lessons:5"
        assert meeting_item["proposed_action"] == "Adopt 20-day volatility regime filter before mean-reversion entry"
        assert meeting_item["outcome"] == "propose_harness_patch"
        assert meeting_item["decision"] == "propose_harness_patch"
        assert meeting_item["tools_used"] == ["lesson_candidate_reader", "shadow_counterfactual_eval"]
        assert meeting_item["persona_id"] == "persona-6507750312870d03a241"
        assert meeting_item["persona_display_name"] == "TW Mean Reversion Beta"
        assert [p["display_name"] for p in meeting_item["consulted_personas"]] == [
            "TW Momentum Alpha",
            "Institutional Risk Officer",
        ]

        resp_trainer = client.get("/bff/management/persona-intent?kind=trainer", headers=OPERATOR_HEADERS)
        assert resp_trainer.status_code == 200, resp_trainer.text
        trainer_items = resp_trainer.json()["data"]["items"]
        assert len(trainer_items) == 1
        trainer_item = trainer_items[0]
        assert trainer_item["id"] == "trainer_session:teach-20261120-001"
        assert trainer_item["kind"] == "trainer"
        assert trainer_item["topic"] == "High-volatility stop-loss calibration"
        assert trainer_item["proposed_action"] == "Tightened intraday trailing stop from 3.5% to 2.5%"
        assert trainer_item["outcome"] == "Tightened intraday trailing stop from 3.5% to 2.5%"
        assert trainer_item["tools_used"] == ["replay_simulator"]
        assert trainer_item["persona_display_name"] == "TW Momentum Alpha"

        # 3. Withheld fields carry translatable reason codes and never use management_persona_intent_public_summary
        resp_all = client.get("/bff/management/persona-intent?kind=all", headers=OPERATOR_HEADERS)
        assert resp_all.status_code == 200, resp_all.text
        all_items = resp_all.json()["data"]["items"]
        assert len(all_items) == 4
        for item in all_items:
            assert item["redaction"]["policy"] != "management_persona_intent_public_summary"
            assert item["redaction"]["reason_code"] in {
                "private_memory_withheld",
                "private_interpretation_withheld",
                "raw_control_diff_withheld",
            }
            assert item["withheld_fields"]
            for wf in item["withheld_fields"]:
                assert wf["field"]
                assert wf["reason_code"] in {
                    "private_memory_withheld",
                    "private_interpretation_withheld",
                    "raw_control_diff_withheld",
                }

        # 4. Private memory and private interpretations are NEVER returned anywhere in the payload
        serialized = json.dumps(resp_all.json())
        for forbidden_secret in (
            "SECRET_PRIVATE_MEMORY_NEVER_LEAK_2330_BIAS",
            "SECRET_BANK_NOTE",
            "SECRET_PRIVATE_INTERPRETATION_TURN_TEXT",
            "SECRET_PERSONA_INTERPRETATION_TEXT",
            "SECRET_MEMORY_TRACE_ENTRY",
            "SECRET_REASONING_TRACE",
            "SECRET_TRAINER_PRIVATE_MEMORY",
            "SECRET_PANEL_PRIVATE_MEMORY",
            "SECRET_PANEL_PRE_READ_INTERPRETATION",
            "SECRET_CASE_FILE_PRIVATE_EXCERPT",
            "SECRET_MEETING_PRIVATE_MEMORY",
            "SECRET_MEETING_INTERPRETATION_1",
        ):
            assert forbidden_secret not in serialized
        for item in all_items:
            assert "private_memory" not in item
            assert "persona_private_memory" not in item
            assert "private_interpretation" not in item
            assert "persona_interpretation" not in item


