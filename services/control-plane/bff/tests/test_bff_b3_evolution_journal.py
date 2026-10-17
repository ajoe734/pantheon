"""
BFF-B3-005: contract tests for GET /bff/management/evolution-journal.

The route is a read-only Management aggregate. It composes evolution decisions,
postmortems, mutation review projections, rollback records, and freeze orders
into one journal envelope while preserving source-surface metadata.
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

from typing import Any, Dict, List, Optional, Tuple
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.evolution.router import create_evolution_router
from services.control_plane.bff.governance.service import GovernanceService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.command_adapters.preconditions import (
    _mutation_review_projection,
    set_ops_console_precondition_resolvers,
)

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-evolution:operator,reviewer"}

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


class _EvolutionJournalTestStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.default_data = _load_default_fixture_pack_datasets()
        persona_capital_kwargs = {
            "evolution_decisions": list(data.get("evolution_decisions", {}).values()) if isinstance(data.get("evolution_decisions"), dict) else data.get("evolution_decisions", []),
            "personas": list(data.get("personas", {}).values()) if isinstance(data.get("personas"), dict) else data.get("personas", []),
            "candidate_artifacts": list(data.get("candidate_artifacts", {}).values()) if isinstance(data.get("candidate_artifacts"), dict) else data.get("candidate_artifacts", []),
            "evolution_programs": list(data.get("evolution_programs", {}).values()) if isinstance(data.get("evolution_programs"), dict) else data.get("evolution_programs", []),
            "bindings": list(data.get("bindings", {}).values()) if isinstance(data.get("bindings"), dict) else data.get("bindings", []),
            "capital_pools": list(data.get("capital_pools", {}).values()) if isinstance(data.get("capital_pools"), dict) else data.get("capital_pools", []),
        }
        lifecycle_kwargs = {
            "incidents": data.get("incidents", {}),
            "postmortems": data.get("postmortems", {}),
            "kill_switch": data.get("kill_switch", {"enabled": False, "status": "armed"}),
            "governance_audit_events": list(data.get("governance_audit_events", {}).values()) if isinstance(data.get("governance_audit_events"), dict) else data.get("governance_audit_events", []),
            "freeze_orders": data.get("freeze_orders", {}),
            "all_rollbacks": list((data.get("all_rollbacks") or data.get("rollbacks", {})).values()) if isinstance(data.get("all_rollbacks") or data.get("rollbacks"), dict) else (data.get("all_rollbacks") or data.get("rollbacks", [])),
        }
        ooda_kwargs = {
            "approval_decisions": list(data.get("approval_decisions", {}).values()) if isinstance(data.get("approval_decisions"), dict) else data.get("approval_decisions", []),
            "mutation_reviews": list(data.get("mutation_reviews", {}).values()) if isinstance(data.get("mutation_reviews"), dict) else data.get("mutation_reviews", []),
        }
        self.ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs=persona_capital_kwargs,
            lifecycle_telemetry_governance_kwargs=lifecycle_kwargs,
            ooda_management_kwargs=ooda_kwargs,
        )
        self.list_evolution_decisions_call_count = 0
        self.get_evolution_decision_by_id_call_count = 0

    def dataset_source(self, dataset: str) -> str:
        key = dataset
        if dataset == "persona_bindings" and "persona_bindings" not in self.data:
            key = "bindings"
        if dataset == "all_rollbacks" and "all_rollbacks" not in self.data:
            key = "rollbacks"
        if key in self.data:
            return "local_snapshot" if self.data[key] is not None else "missing"
        return self.ports.dataset_source(dataset)

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("bindings") or self.data.get("persona_bindings") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("runtime_bindings") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("personas") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("incidents") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_evolution_decisions_call_count += 1
        val = self.data.get("evolution_decisions")
        if val is None:
            val = self.default_data.get("evolution_decisions") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def get_approval_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        val = self.data.get("approval_decisions")
        if isinstance(val, dict) and decision_id in val:
            return val[decision_id]
        if isinstance(val, list):
            for d in val:
                if d.get("approval_decision_id") == decision_id or d.get("id") == decision_id:
                    return d
        def_val = self.default_data.get("approval_decisions")
        if isinstance(def_val, dict) and decision_id in def_val:
            return def_val[decision_id]
        if isinstance(def_val, list):
            for d in def_val:
                if d.get("approval_decision_id") == decision_id or d.get("id") == decision_id:
                    return d
        return {"id": decision_id, "approval_decision_id": decision_id, "status": "approved"}

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[dict[str, Any]]:
        self.get_evolution_decision_by_id_call_count += 1
        clean_id = str(decision_id or "").strip()
        if not clean_id:
            return None
        for d in self.list_evolution_decisions():
            if str(d.get("decision_id") or d.get("id") or "").strip() == clean_id:
                return d
        return None

    def get_evolution_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        return self.get_evolution_decision_by_id(decision_id)

    def get_incident(self, incident_id: str) -> Optional[dict[str, Any]]:
        val = self.data.get("incidents")
        if isinstance(val, dict) and incident_id in val:
            return val[incident_id]
        if isinstance(val, list):
            for d in val:
                if d.get("incident_id") == incident_id or d.get("id") == incident_id:
                    return d
        def_val = self.default_data.get("incidents")
        if isinstance(def_val, dict) and incident_id in def_val:
            return def_val[incident_id]
        if isinstance(def_val, list):
            for d in def_val:
                if d.get("incident_id") == incident_id or d.get("id") == incident_id:
                    return d
        return None

    def get_postmortem(self, postmortem_id: str) -> Optional[dict[str, Any]]:
        val = self.data.get("postmortems")
        if isinstance(val, dict) and postmortem_id in val:
            return val[postmortem_id]
        if isinstance(val, list):
            for d in val:
                if d.get("postmortem_id") == postmortem_id or d.get("id") == postmortem_id:
                    return d
        def_val = self.default_data.get("postmortems")
        if isinstance(def_val, dict) and postmortem_id in def_val:
            return def_val[postmortem_id]
        if isinstance(def_val, list):
            for d in def_val:
                if d.get("postmortem_id") == postmortem_id or d.get("id") == postmortem_id:
                    return d
        return None

    def list_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("postmortems")
        items = list(val.values()) if isinstance(val, dict) else (list(val) if isinstance(val, list) else [])
        if not items:
            def_val = self.default_data.get("postmortems") or {}
            items = list(def_val.values()) if isinstance(def_val, dict) else list(def_val)
        if not items:
            items = [
                {"postmortem_id": "pm-1", "title": "Postmortem 1", "status": "published", "created_at": "2026-05-01T00:00:00Z"},
                {"postmortem_id": "pm-2", "title": "Postmortem 2", "status": "published", "created_at": "2026-05-02T00:00:00Z"},
            ]
        return items

    def list_freeze_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("freeze_orders")
        items = list(val.values()) if isinstance(val, dict) else (list(val) if isinstance(val, list) else [])
        if not items:
            def_val = self.default_data.get("freeze_orders") or {}
            items = list(def_val.values()) if isinstance(def_val, dict) else list(def_val)
        if not items:
            items = [
                {"freeze_order_id": "fo-1", "status": "active", "scope": "pool", "created_at": "2026-05-01T00:00:00Z"},
                {"freeze_order_id": "fo-2", "status": "active", "scope": "pool", "created_at": "2026-05-02T00:00:00Z"},
            ]
        return items

    def list_all_rollbacks(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("all_rollbacks") or self.data.get("rollbacks")
        items = list(val.values()) if isinstance(val, dict) else (list(val) if isinstance(val, list) else [])
        if not items:
            def_val = self.default_data.get("all_rollbacks") or self.default_data.get("rollbacks") or {}
            items = list(def_val.values()) if isinstance(def_val, dict) else list(def_val)
        if not items:
            items = [
                {"rollback_id": "rb-1", "status": "completed", "action_type": "rollback", "created_at": "2026-05-01T00:00:00Z"},
                {"rollback_id": "rb-2", "status": "completed", "action_type": "rollback", "created_at": "2026-05-02T00:00:00Z"},
            ]
        return items

    def list_approval_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("approval_decisions")
        items = list(val.values()) if isinstance(val, dict) else (list(val) if isinstance(val, list) else [])
        if not items and self.default_data.get("approval_decisions"):
            def_val = self.default_data["approval_decisions"]
            items = list(def_val.values()) if isinstance(def_val, dict) else list(def_val)
        return items

    def dataset_source(self, dataset: str) -> str:
        key = dataset
        if dataset == "persona_bindings" and "persona_bindings" not in self.data:
            key = "bindings"
        if dataset == "all_rollbacks" and "all_rollbacks" not in self.data:
            key = "rollbacks"
        if key in self.data:
            return "local_snapshot" if self.data[key] is not None else "missing"
        return "local_snapshot"

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
        if name.startswith("get_") and name[4:] in self.data:
            val = self.data[name[4:]]
            if isinstance(val, dict):
                return lambda item_id, **kw: val.get(item_id)
        raise AttributeError(f"'_EvolutionJournalTestStore' has no attribute '{name}'")


class _JournalTestClient(TestClient):
    @property
    def store(self) -> Any:
        return self.app.state.store

    @store.setter
    def store(self, val: Any) -> None:
        self.app.state.store = val


def _create_app_for_store(
    store: Any,
    mutation_review_projection: Any = _mutation_review_projection,
) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        code = "HTTP_ERROR"
        message = str(detail)
        if isinstance(detail, dict):
            code = detail.get("code") or (detail.get("error", {}).get("code") if isinstance(detail.get("error"), dict) else None) or "HTTP_ERROR"
            message = detail.get("message") or str(detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message, "details": detail if isinstance(detail, dict) else {"reason": message}}},
        )

    class _Identity:
        def __init__(self, op_id: str, roles: set[str]) -> None:
            self.operator_id = op_id
            self.roles = roles

    def _extract_identity(auth: Optional[str] = None) -> Optional[_Identity]:
        if not auth or not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer "):].strip()
        if ":" in token:
            op_id, roles_str = token.split(":", 1)
            roles = {r.strip() for r in roles_str.split(",") if r.strip()}
        else:
            op_id = token
            roles = {"operator", "viewer", "reviewer", "admin"}
        return _Identity(op_id, roles)

    def _require_read(identity: Optional[_Identity]) -> None:
        if identity is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "AUTH_REQUIRED", "message": "Authentication required"},
            )

    def _dataset_surface_status(dataset: str, *, snapshot_at: str, **kwargs: Any) -> Dict[str, Any]:
        curr_store = app.state.store
        source = kwargs.get("source")
        if not source and hasattr(curr_store, "dataset_source"):
            try:
                source = curr_store.dataset_source(dataset)
            except Exception:
                source = "missing"
        source = source or "ok"
        if source in ("missing", "unavailable"):
            return {"status": "unavailable", "dataset": dataset, "snapshot_at": snapshot_at, "source": source}
        if source == "local_snapshot":
            return {"status": "degraded", "dataset": dataset, "snapshot_at": snapshot_at, "source": source}
        return {"status": "available", "dataset": dataset, "snapshot_at": snapshot_at, "source": source}

    app.state.store = store
    set_ops_console_precondition_resolvers(read_surface=lambda: app.state.store)
    router = create_evolution_router(
        read_surface=lambda: app.state.store,
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        dataset_surface_status=_dataset_surface_status,
        mutation_review_projection=mutation_review_projection,
    )
    app.include_router(router)
    return app


def _fresh_client(td: str, raise_server_exceptions: bool = True) -> _JournalTestClient:
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
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    return _JournalTestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_evolution_journal_composes_required_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)

            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body.keys()) == {"data", "page_info", "meta"}
            assert set(body["data"].keys()) == {"id", "items", "summary"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert summary["decision_count"] >= 1
            assert summary["postmortem_count"] >= 1
            assert summary["mutation_review_count"] >= 1
            assert summary["rollback_count"] >= 1
            assert summary["freeze_order_count"] >= 1
            assert "byType" not in summary
            assert "byStatus" not in summary
            assert "byRiskLevel" not in summary
            assert body["data"]["id"] == "management_evolution_journal"
            assert body["meta"]["surfaces"]["management_evolution_journal"]["source"] == "bff_composed"
            for surface in [
                "evolution_decisions",
                "postmortems",
                "mutation_review",
                "rollbacks",
                "freeze_orders",
            ]:
                assert surface in body["meta"]["surfaces"]

            by_type = {item["entry_type"]: item for item in items}
            assert by_type["evolution_decision"]["decision"]["id"]
            assert by_type["postmortem"]["postmortem"]["postmortem_id"]
            assert by_type["rollback"]["rollback"]["rollback_id"]
            assert by_type["freeze_order"]["freeze_order"]["freeze_order_id"]
            assert "entryType" not in by_type["freeze_order"]
            assert "freezeOrder" not in by_type["freeze_order"]
            mutation_review = next(
                item for item in items
                if item["entry_type"] == "mutation_review"
                and item["source_id"] == "evo-dec-88f3a2c1"
            )
            assert mutation_review["mutation_review"]["decision_id"] == "evo-dec-88f3a2c1"
            assert "mutationReview" not in mutation_review
        finally:
            pass


def test_evolution_journal_supports_filters_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)

            resp = client.get(
                "/bff/management/evolution-journal"
                "?source_type=mutation_review&status=reviewed&risk_level=medium&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] == 1
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert len(items) == 1
            item = items[0]
            assert item["entry_type"] == "mutation_review"
            assert item["source_id"] == "evo-dec-88f3a2c1"
            assert item["mutation_review"]["decision_id"] == "evo-dec-88f3a2c1"
            assert item["mutation_review"]["allowedActions"]["canApproveMutation"] is True
            assert summary["pending_review_count"] == 1
        finally:
            pass


def test_evolution_journal_server_side_filtering_and_origin() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)

            # 1. Test filtering by persona: resolved through explicit lineage
            # (persona-alpha declares runtime_id=runtime-042, which owns
            # incident inc-20260410-001, whose incident_ref is carried by
            # evo-dec-001), never a free-text substring match against an
            # unrelated artifact id.
            resp = client.get(
                "/bff/management/evolution-journal?persona=persona-alpha",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            assert len(items) > 0
            source_ids = {item["source_id"] for item in items}
            assert "evo-dec-001" in source_ids
            # evo-dec-88f3a2c1 targets artifact-44d7e9b0, which is not in
            # persona-alpha's lineage at all — it must not appear.
            assert "evo-dec-88f3a2c1" not in source_ids

            # 2. Test filtering by decision
            resp = client.get(
                "/bff/management/evolution-journal?decision=evo-dec-88f3a2c1",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            assert len(items) > 0
            for item in items:
                assert "evo-dec-88f3a2c1" in str(item).lower()

            # 3. Test filtering by mutation_review
            resp = client.get(
                "/bff/management/evolution-journal?mutation_review=evo-dec-88f3a2c1",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            assert len(items) > 0
            for item in items:
                assert item["entry_type"] in ("mutation_review", "evolution_decision")
                assert "evo-dec-88f3a2c1" in str(item).lower()

            # 4. Verify origin field in response is set correctly (seed/live)
            # Since our default read_surfaces has mock data, most should be tagged "seed"
            resp = client.get(
                "/bff/management/evolution-journal",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            assert len(items) > 0
            for item in items:
                assert "origin" in item
                # evo-vslice-1, btc-drift, etc., are mock/seed data
                if "vslice" in str(item).lower() or "btc-drift" in str(item).lower():
                    assert item["origin"] == "seed"

            # 5. Empty filtered hit for nonexistent persona
            resp = client.get(
                "/bff/management/evolution-journal?persona=nonexistent-persona-id-xyz",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 0
            assert body["page_info"]["total"] == 0

            # 6. Empty filtered hit for nonexistent decision
            resp = client.get(
                "/bff/management/evolution-journal?decision=nonexistent-dec-id-xyz",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 0
            assert body["page_info"]["total"] == 0

        finally:
            pass


def test_evolution_journal_requires_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal")

            assert resp.status_code == 401, resp.text
            assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            pass


def test_evochain_007_filters_provenance_and_decoys() -> None:
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {
                "persona-1": {
                    "id": "persona-1",
                    "persona_id": "persona-1",
                    "artifact_id": "artifact-1"
                },
                "persona-10": {
                    "id": "persona-10",
                    "persona_id": "persona-10",
                    "artifact_id": "artifact-10"
                }
            },
            "runtime_bindings": {
                "runtime-1": {
                    "id": "runtime-1",
                    "runtime_id": "runtime-1",
                    "persona_id": "persona-1",
                    "artifact_id": "artifact-1"
                }
            },
            "incidents": {
                "inc-1": {
                    "id": "inc-1",
                    "incident_id": "inc-1",
                    "artifact_id": "artifact-1",
                    "runtime_id": "runtime-1"
                }
            },
            "evolution_decisions": {
                "dec-1": {
                    "id": "dec-1",
                    "decision_id": "dec-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-1",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Retrain dec-1"},
                    "created_at": "2026-07-14T00:00:00Z"
                },
                "dec-10": {
                    "id": "dec-10",
                    "decision_id": "dec-10",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-10",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Retrain dec-10"},
                    "created_at": "2026-07-14T00:01:00Z"
                },
                "dec-decoy": {
                    "id": "dec-decoy",
                    "decision_id": "dec-decoy",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-other",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "title": "Decoy for persona-1",
                    "summary": "This is a decoy mentioning persona-1 but not associated with it",
                    "proposed_changes": {"summary": "Decoy"},
                    "created_at": "2026-07-14T00:02:00Z"
                },
                "evo-vslice-1": {
                    "id": "evo-vslice-1",
                    "decision_id": "evo-vslice-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-vslice",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Vslice"},
                    "created_at": "2026-07-14T00:03:00Z"
                },
                "dec-live": {
                    "id": "dec-live",
                    "decision_id": "dec-live",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-live",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "metadata": {
                        "origin": "live"
                    },
                    "proposed_changes": {"summary": "Live"},
                    "created_at": "2026-07-14T00:04:00Z"
                },
                "dec-explicit-unknown": {
                    "id": "dec-explicit-unknown",
                    "decision_id": "dec-explicit-unknown",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-unknown",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "metadata": {
                        "origin": "unknown"
                    },
                    "proposed_changes": {"summary": "Unknown"},
                    "created_at": "2026-07-14T00:05:00Z"
                }
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {}
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)

            # 1. Test persona decoy / collision filtering (explicit lineage matching)
            # Querying for persona-1 should ONLY match dec-1 (artifact-1 is in persona-1's lineage)
            # It should not match dec-10 (persona-10 collision) and should not match dec-decoy (free text decoy)
            resp = client.get("/bff/management/evolution-journal?persona=persona-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            assert len(items) == 2
            assert all(item["source_id"] == "dec-1" for item in items)

            # 2. Test typed hits / empty results
            # Querying for decision dec-1 should return decision item
            resp = client.get("/bff/management/evolution-journal?decision=dec-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items_dec = resp.json()["data"]["items"]
            assert len(items_dec) == 1
            assert items_dec[0]["entry_type"] == "evolution_decision"

            # Querying for mutation_review dec-1 should return mutation_review item
            resp = client.get("/bff/management/evolution-journal?mutation_review=dec-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items_mr = resp.json()["data"]["items"]
            assert len(items_mr) == 1
            assert items_mr[0]["entry_type"] == "mutation_review"

            # Querying for nonexistent decision should return 0 items
            resp = client.get("/bff/management/evolution-journal?decision=nonexistent", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            assert len(resp.json()["data"]["items"]) == 0

            # 3. Test authoritative seed/live/unknown provenance
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            # 6 decisions + 6 mutation reviews + 6 backfilled default items = 18 total items
            assert len(items) == 18

            by_id_and_type = {(item["source_id"], item["entry_type"]): item for item in items}
            assert by_id_and_type[("evo-vslice-1", "evolution_decision")]["origin"] == "seed"
            assert by_id_and_type[("dec-live", "evolution_decision")]["origin"] == "live"
            assert by_id_and_type[("dec-explicit-unknown", "evolution_decision")]["origin"] == "unknown"
            assert by_id_and_type[("dec-1", "evolution_decision")]["origin"] == "unknown"

            # Verify mutation reviews also preserve the correct origin
            assert by_id_and_type[("evo-vslice-1", "mutation_review")]["origin"] == "seed"
            assert by_id_and_type[("dec-live", "mutation_review")]["origin"] == "live"
            assert by_id_and_type[("dec-explicit-unknown", "mutation_review")]["origin"] == "unknown"
            assert by_id_and_type[("dec-1", "mutation_review")]["origin"] == "unknown"

            # 4. Test filtered total plus next token (paging)
            # Querying for persona-1 with page_size=1 should return exactly 1 item and total=2 (dec-1 decision + dec-1 mutation_review)
            resp = client.get("/bff/management/evolution-journal?persona=persona-1&page_size=1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 2
            assert len(body["data"]["items"]) == 1
            next_token = body["page_info"]["next_page_token"]
            assert next_token is not None

            # Get next page
            resp = client.get(f"/bff/management/evolution-journal?persona=persona-1&page_size=1&page_token={next_token}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 1
            assert body["page_info"]["total"] == 2
            assert body["page_info"]["next_page_token"] is None

        finally:
            pass


def test_evochain_007_filter_dependency_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, raise_server_exceptions=False)

        def mock_list_personas(*args, **kwargs):
            raise RuntimeError("Database connection lost")

        client.store.list_personas = mock_list_personas

        resp = client.get("/bff/management/evolution-journal?persona=persona-1", headers=OPERATOR_HEADERS)
        assert resp.status_code == 500


def test_evochain_007_persona_mutation_review_collision() -> None:
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {},
            "runtime_bindings": {},
            "incidents": {},
            "evolution_decisions": {
                "dec-1": {
                    "id": "dec-1",
                    "decision_id": "dec-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-1",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Retrain dec-1"},
                    "created_at": "2026-07-14T00:00:00Z"
                }
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {}
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            # Querying with persona=mutation_review should NOT match mutation reviews unless there is a matching persona
            resp = client.get("/bff/management/evolution-journal?persona=mutation_review", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]["items"]) == 0
        finally:
            pass


def test_evochain_007_origin_exact_match_not_broad_substring() -> None:
    """A decision id that merely *contains* the substring "seed" (e.g. a
    "live-seedling" artifact) must not be honestly reported as seed-derived
    provenance; only exact registered seed identifiers/prefixes qualify."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {},
            "runtime_bindings": {},
            "incidents": {},
            "evolution_decisions": {
                "live-seedling-1": {
                    "id": "live-seedling-1",
                    "decision_id": "live-seedling-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-live-seedling",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Not actually a registered seed"},
                    "created_at": "2026-07-14T00:00:00Z"
                },
                "evo-vslice-1": {
                    "id": "evo-vslice-1",
                    "decision_id": "evo-vslice-1",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-vslice",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Registered vslice seed"},
                    "created_at": "2026-07-14T00:01:00Z"
                }
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {}
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            by_id = {
                item["source_id"]: item
                for item in items
                if item["entry_type"] == "evolution_decision"
            }
            assert by_id["live-seedling-1"]["origin"] == "unknown"
            assert by_id["evo-vslice-1"]["origin"] == "seed"
        finally:
            pass


def _evochain_007_lineage_mock_data(evolution_decisions: dict, **surfaces) -> dict:
    mock_data = {
        "personas": {},
        "runtime_bindings": {},
        "incidents": {},
        "evolution_decisions": evolution_decisions,
        "postmortems": {},
        "freeze_orders": {},
        "rollbacks": {},
    }
    mock_data.update(surfaces)
    return mock_data


def test_evochain_007_persona_lineage_shared_artifact_boundary() -> None:
    """Two unrelated personas independently bind to the same shared artifact.
    Filtering by persona-a must not pull in persona-b's own decision just
    because their bindings happen to reference the same artifact_id."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                # Each persona declares its own runtime_id so read_store's
                # binding-ownership reconciliation (typed exact identity,
                # not a raw copied persona_id) resolves ownership correctly.
                "persona-a": {"id": "persona-a", "persona_id": "persona-a", "runtime_id": "runtime-a"},
                "persona-b": {"id": "persona-b", "persona_id": "persona-b", "runtime_id": "runtime-b"},
            },
            runtime_bindings={
                "runtime-a": {
                    "id": "runtime-a",
                    "runtime_id": "runtime-a",
                    "persona_id": "persona-a",
                    "artifact_id": "artifact-shared",
                },
                "runtime-b": {
                    "id": "runtime-b",
                    "runtime_id": "runtime-b",
                    "persona_id": "persona-b",
                    "artifact_id": "artifact-shared",
                },
            },
            evolution_decisions={
                "dec-a": {
                    "id": "dec-a",
                    "decision_id": "dec-a",
                    "target_type": "runtime",
                    "target_id": "runtime-a",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-a own decision"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-b": {
                    "id": "dec-b",
                    "decision_id": "dec-b",
                    "target_type": "artifact",
                    "target_id": "artifact-shared",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-b's own decision, shares the artifact"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-a", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            source_ids = {item["source_id"] for item in items}
            assert "dec-a" in source_ids
            assert "dec-b" not in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_namespace_collision() -> None:
    """persona-pool-1 and persona-pool-10 must not collide through their
    capital_pool_id / persona_capital_binding_id namespaces."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                # Each persona declares its own runtime_binding_id so
                # read_store's binding-ownership reconciliation (typed exact
                # identity) resolves ownership correctly.
                "persona-pool-1": {
                    "id": "persona-pool-1",
                    "persona_id": "persona-pool-1",
                    "runtime_binding_id": "binding-pool-1",
                },
                "persona-pool-10": {
                    "id": "persona-pool-10",
                    "persona_id": "persona-pool-10",
                    "runtime_binding_id": "binding-pool-10",
                },
            },
            runtime_bindings={
                "binding-pool-1": {
                    "id": "binding-pool-1",
                    "persona_id": "persona-pool-1",
                    "capital_pool_id": "pool-1",
                },
                "binding-pool-10": {
                    "id": "binding-pool-10",
                    "persona_id": "persona-pool-10",
                    "capital_pool_id": "pool-10",
                },
            },
            incidents={
                "inc-pool-1": {
                    "id": "inc-pool-1",
                    "incident_id": "inc-pool-1",
                    "capital_pool_id": "pool-1",
                },
                "inc-pool-10": {
                    "id": "inc-pool-10",
                    "incident_id": "inc-pool-10",
                    "capital_pool_id": "pool-10",
                },
            },
            evolution_decisions={
                "dec-pool-1": {
                    "id": "dec-pool-1",
                    "decision_id": "dec-pool-1",
                    "target_type": "incident",
                    "incident_ref": "inc-pool-1",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "pool-1 decision"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-pool-10": {
                    "id": "dec-pool-10",
                    "decision_id": "dec-pool-10",
                    "target_type": "incident",
                    "incident_ref": "inc-pool-10",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "pool-10 decision"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-pool-1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-pool-1" in source_ids
            assert "dec-pool-10" not in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_pool_only_convergence() -> None:
    """A persona linked to a decision only through a shared capital_pool_id
    chain (no direct runtime/binding id overlap) must still resolve."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-pool-only": {
                    "id": "persona-pool-only",
                    "persona_id": "persona-pool-only",
                    "runtime_binding_id": "binding-pool-only",
                },
            },
            runtime_bindings={
                "binding-pool-only": {
                    "id": "binding-pool-only",
                    "persona_id": "persona-pool-only",
                    "capital_pool_id": "pool-only-42",
                },
            },
            incidents={
                "inc-pool-only": {
                    "id": "inc-pool-only",
                    "incident_id": "inc-pool-only",
                    "capital_pool_id": "pool-only-42",
                },
            },
            evolution_decisions={
                "dec-pool-only": {
                    "id": "dec-pool-only",
                    "decision_id": "dec-pool-only",
                    "target_type": "incident",
                    "incident_ref": "inc-pool-only",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "resolved only via capital_pool_id"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-pool-only", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-pool-only" in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_capital_binding_only_convergence() -> None:
    """A persona linked to a decision only through persona_capital_binding_id
    (no runtime/plan/pool overlap) must still resolve."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-cb-only": {
                    "id": "persona-cb-only",
                    "persona_id": "persona-cb-only",
                    "persona_capital_binding_id": "pcb-only-7",
                },
            },
            incidents={
                "inc-cb-only": {
                    "id": "inc-cb-only",
                    "incident_id": "inc-cb-only",
                    "persona_capital_binding_id": "pcb-only-7",
                },
            },
            evolution_decisions={
                "dec-cb-only": {
                    "id": "dec-cb-only",
                    "decision_id": "dec-cb-only",
                    "target_type": "incident",
                    "incident_ref": "inc-cb-only",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "resolved only via persona_capital_binding_id"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-cb-only", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-cb-only" in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_deep_chain_requires_fixed_point() -> None:
    """persona -[persona_id]-> binding1(runtime-deep-1) -[plan]->
    binding2(runtime-deep-2) -[pool]-> binding3(runtime-deep-3)
    -[persona_capital_binding]-> binding4(runtime-deep-4) -[pool]->
    incident -[incident_ref]-> decision is a 5-edge chain. Every binding
    below has its own distinct runtime_id (bindings are keyed/deduped by
    runtime_id in read_store, so two bindings can never legitimately share
    one), and the bindings dict is deliberately ordered in *reverse*
    dependency order (hop4 first, hop1 last) so a single left-to-right pass
    cannot resolve it. A hardcoded 3-iteration traversal converges at most 3
    edges per query and would never reach the leaf decision; only a
    fixed-point loop (iterate until no set grows) resolves it regardless of
    edge order or chain depth."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-deep": {
                    "id": "persona-deep",
                    "persona_id": "persona-deep",
                    "runtime_id": "runtime-deep-1",
                },
            },
            runtime_bindings={
                # Listed in reverse dependency order on purpose.
                "binding-hop4": {
                    "id": "binding-hop4",
                    "runtime_id": "runtime-deep-4",
                    "persona_capital_binding_id": "pcb-deep-3",
                    "capital_pool_id": "pool-deep-4",
                },
                "binding-hop3": {
                    "id": "binding-hop3",
                    "runtime_id": "runtime-deep-3",
                    "capital_pool_id": "pool-deep-2",
                    "persona_capital_binding_id": "pcb-deep-3",
                },
                "binding-hop2": {
                    "id": "binding-hop2",
                    "runtime_id": "runtime-deep-2",
                    "plan_id": "plan-deep-1",
                    "capital_pool_id": "pool-deep-2",
                },
                "binding-hop1": {
                    "id": "binding-hop1",
                    "persona_id": "persona-deep",
                    "runtime_id": "runtime-deep-1",
                    "plan_id": "plan-deep-1",
                },
            },
            incidents={
                "inc-deep-5": {
                    "id": "inc-deep-5",
                    "incident_id": "inc-deep-5",
                    "capital_pool_id": "pool-deep-4",
                },
            },
            evolution_decisions={
                "dec-deep-6": {
                    "id": "dec-deep-6",
                    "decision_id": "dec-deep-6",
                    "target_type": "incident",
                    "incident_ref": "inc-deep-5",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "5 edges deep from the queried persona"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-unrelated": {
                    "id": "dec-unrelated",
                    "decision_id": "dec-unrelated",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-unrelated",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "not connected to persona-deep at all"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-deep", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-deep-6" in source_ids
            assert "dec-unrelated" not in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_canonical_binding_without_runtime_row() -> None:
    """A canonical persona-capital binding (read_store.list_bindings) can
    exist with no matching runtime row at all. list_runtime_bindings() only
    uses list_bindings() to enrich runtime rows that already exist, so
    relying on it alone would make a runtime-less persona -> binding -> pool
    -> incident chain invisible; the endpoint must also read list_bindings()
    directly."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-nr": {
                    "id": "persona-nr",
                    "persona_id": "persona-nr",
                    "persona_capital_binding_id": "pcb-nr",
                },
            },
            bindings={
                "pcb-nr": {
                    "id": "pcb-nr",
                    "binding_id": "pcb-nr",
                    "persona_id": "persona-nr",
                    "capital_pool_id": "pool-nr",
                },
            },
            incidents={
                "inc-nr": {
                    "id": "inc-nr",
                    "incident_id": "inc-nr",
                    "capital_pool_id": "pool-nr",
                },
            },
            evolution_decisions={
                "dec-nr": {
                    "id": "dec-nr",
                    "decision_id": "dec-nr",
                    "target_type": "incident",
                    "incident_ref": "inc-nr",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "resolved only via a runtime-less canonical binding"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-nr", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-nr" in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_shared_pool_does_not_cross_persona_ownership() -> None:
    """Two distinct personas independently bind to the same shared capital
    pool. Filtering by persona-pool-a must not adopt persona-pool-b's own
    identity (and therefore not its private decision) just because their
    bindings happen to reference the same capital_pool_id."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-pool-a": {
                    "id": "persona-pool-a",
                    "persona_id": "persona-pool-a",
                    "runtime_binding_id": "binding-pool-a",
                },
                # Declared so read_store's binding-ownership reconciliation
                # (typed exact identity) confirms binding-pool-b really
                # belongs to persona-pool-b, rather than nulling out its
                # persona_id as an unclaimed binding.
                "persona-pool-b": {
                    "id": "persona-pool-b",
                    "persona_id": "persona-pool-b",
                    "runtime_binding_id": "binding-pool-b",
                },
            },
            runtime_bindings={
                "binding-pool-a": {
                    "id": "binding-pool-a",
                    "persona_id": "persona-pool-a",
                    "capital_pool_id": "pool-shared",
                },
                "binding-pool-b": {
                    "id": "binding-pool-b",
                    "persona_id": "persona-pool-b",
                    "runtime_id": "runtime-pool-b",
                    "capital_pool_id": "pool-shared",
                },
            },
            evolution_decisions={
                "dec-pool-a": {
                    "id": "dec-pool-a",
                    "decision_id": "dec-pool-a",
                    "target_type": "artifact",
                    "capital_pool_id": "pool-shared",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-pool-a's own decision, via the shared pool"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-pool-b-private": {
                    "id": "dec-pool-b-private",
                    "decision_id": "dec-pool-b-private",
                    "target_type": "runtime",
                    "target_id": "runtime-pool-b",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "persona-pool-b's own private decision"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-pool-a", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-pool-a" in source_ids
            assert "dec-pool-b-private" not in source_ids
        finally:
            pass


def test_evochain_007_persona_lineage_typed_namespaces_reject_cross_type_string_collision() -> None:
    """A persona's own runtime_id and an unrelated decision's artifact
    target_id happen to be the identical raw string. Matching must stay
    scoped to the typed namespace the id belongs to (runtime vs artifact),
    never a flattened id blob, so the two must not collide."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = _evochain_007_lineage_mock_data(
            personas={
                "persona-typed": {
                    "id": "persona-typed",
                    "persona_id": "persona-typed",
                    "runtime_id": "same-token",
                },
            },
            evolution_decisions={
                "dec-unrelated-artifact": {
                    "id": "dec-unrelated-artifact",
                    "decision_id": "dec-unrelated-artifact",
                    "target_type": "artifact",
                    "target_id": "same-token",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "unrelated artifact that happens to share the raw string"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "dec-own-runtime": {
                    "id": "dec-own-runtime",
                    "decision_id": "dec-own-runtime",
                    "target_type": "runtime",
                    "target_id": "same-token",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "genuinely persona-typed's own runtime"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
            },
        )
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal?persona=persona-typed", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            source_ids = {item["source_id"] for item in resp.json()["data"]["items"]}
            assert "dec-own-runtime" in source_ids
            assert "dec-unrelated-artifact" not in source_ids
        finally:
            pass


def test_evochain_007_seed_registry_recognizes_registered_families() -> None:
    """Registered seed identifier families beyond evo-vslice- (ev-seed-001..005
    decision ids from services/evolution/seed_data.py, the documented seed
    incident inc-87c655c3e3c9, and decisions that reference a registered seed
    artifact/runtime/binding/plan target) must be honestly labeled
    origin:seed; a similarly-spelled but unregistered id must stay unknown."""
    import json
    with tempfile.TemporaryDirectory() as td:
        mock_data = {
            "personas": {},
            "runtime_bindings": {},
            "incidents": {},
            "evolution_decisions": {
                "ev-seed-003": {
                    "id": "ev-seed-003",
                    "decision_id": "ev-seed-003",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-ev-seed-003",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Registered ev-seed family member"},
                    "created_at": "2026-07-14T00:00:00Z",
                },
                "ev-seeding-decoy": {
                    "id": "ev-seeding-decoy",
                    "decision_id": "ev-seeding-decoy",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-decoy",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Looks like the ev-seed- family but is not registered"},
                    "created_at": "2026-07-14T00:01:00Z",
                },
                "dec-swept-from-seed-incident": {
                    "id": "dec-swept-from-seed-incident",
                    "decision_id": "dec-swept-from-seed-incident",
                    "target_type": "incident",
                    "incident_ref": "inc-87c655c3e3c9",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Swept from the documented seed incident"},
                    "created_at": "2026-07-14T00:02:00Z",
                },
                "dec-targets-seed-artifact": {
                    "id": "dec-targets-seed-artifact",
                    "decision_id": "dec-targets-seed-artifact",
                    "target_type": "artifact",
                    "target_id": "artifact-042",
                    "status": "proposed",
                    "action_type": "retrain",
                    "risk_level": "low",
                    "proposed_changes": {"summary": "Targets the registered seed artifact"},
                    "created_at": "2026-07-14T00:03:00Z",
                },
            },
            "postmortems": {},
            "freeze_orders": {},
            "rollbacks": {},
        }
        with open(os.path.join(td, "read_surfaces.json"), "w") as f:
            json.dump(mock_data, f)

        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            by_id = {
                item["source_id"]: item
                for item in items
                if item["entry_type"] == "evolution_decision"
            }
            assert by_id["ev-seed-003"]["origin"] == "seed"
            assert by_id["ev-seeding-decoy"]["origin"] == "unknown"
            assert by_id["dec-swept-from-seed-incident"]["origin"] == "seed"
            assert by_id["dec-targets-seed-artifact"]["origin"] == "seed"
        finally:
            pass


def test_evochain_007_filter_dependency_surfaces_reported_and_fail_closed() -> None:
    """The personas/persona_bindings/runtime_bindings/incidents datasets that
    back the ?persona= lineage filter must be visible in meta.surfaces, and a
    *silently* unavailable dependency (dataset_source reports "missing" with
    no exception raised) must fail the filtered request closed (503) instead
    of returning an authoritative-looking empty total=0."""
    with tempfile.TemporaryDirectory() as td:
        ports = create_in_memory_read_surface_ports()
        app = _create_app_for_store(ports)
        client = _JournalTestClient(app, raise_server_exceptions=False)

        resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        surfaces = resp.json()["meta"]["surfaces"]
        for key in ("personas", "persona_bindings", "runtime_bindings", "incidents"):
            assert key in surfaces

        original_dataset_source = client.store.dataset_source
        client.store.dataset_source = (
            lambda dataset, **kwargs: "missing"
            if dataset == "personas"
            else original_dataset_source(dataset, **kwargs)
        )
        resp = client.get(
            "/bff/management/evolution-journal?persona=persona-alpha",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 503, resp.text


def test_focused_decision_read_amplification_bounded() -> None:
    """EVOLUTION-JOURNAL-OWNER-READ-FANOUT-20261017: Focused originalID request
    must not fan out to repeated per-row owner hydration across all history.

    With 77 proposals in the owner store (matching the production incident state
    where GET4ea430bd returned 77 proposals), a narrow request for
    ?decision=evo-sweep-inc-drift-a24770da155d must execute exactly 1 list_evolution_decisions
    call and 0 get_evolution_decision_by_id calls (down from 78 and 77 on base).
    """
    with tempfile.TemporaryDirectory() as td:
        target_id = "evo-sweep-inc-drift-a24770da155d"
        decisions: list[dict[str, Any]] = [
            {
                "id": f"dec-{i:03d}",
                "decision_id": f"dec-{i:03d}",
                "target_type": "artifact",
                "target_id": f"art-{i:03d}",
                "target_version": "v1.0.0",
                "action_type": "mutation",
                "status": "pending",
                "decision_state": "pending",
                "risk_level": "low",
                "created_at": "2026-06-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
                "occurred_at": "2026-06-01T00:00:00Z",
                "proposed_changes": {"summary": f"Summary for dec-{i:03d}"},
                "approval_decision_id": f"appr-{i:03d}",
                "risk_assessment": {"risk_summary": "low risk"},
            }
            for i in range(76)
        ]
        decisions.append(
            {
                "id": target_id,
                "decision_id": target_id,
                "target_type": "artifact",
                "target_id": "art-target",
                "target_version": "v1.0.0",
                "action_type": "mutation",
                "status": "approved",
                "decision_state": "approved",
                "risk_level": "low",
                "created_at": "2026-06-02T00:00:00Z",
                "updated_at": "2026-06-02T00:00:00Z",
                "occurred_at": "2026-06-02T00:00:00Z",
                "proposed_changes": {"summary": "Target decision proposal"},
                "approval_decision_id": "appr-target",
                "risk_assessment": {"risk_summary": "low risk"},
            }
        )

        data = {
            "evolution_decisions": {d["decision_id"]: d for d in decisions},
            "approval_decisions": {},
            "postmortems": {},
            "freeze_orders": {},
            "all_rollbacks": {},
        }
        store = _EvolutionJournalTestStore(data)
        app = _create_app_for_store(store)
        client = _JournalTestClient(app, raise_server_exceptions=True)

        store.list_evolution_decisions_call_count = 0
        store.get_evolution_decision_by_id_call_count = 0

        resp = client.get(
            f"/bff/management/evolution-journal?decision={target_id}",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items = body["data"]["items"]

        assert len(items) == 1
        assert items[0]["source_id"] == target_id
        assert items[0]["entry_type"] == "evolution_decision"

        # Owner calls must remain strictly bounded: exactly 1 list call, 0 per-row hydration calls
        assert store.list_evolution_decisions_call_count == 1
        assert store.get_evolution_decision_by_id_call_count == 0


def test_unfiltered_default_page_read_amplification_bounded_across_history_sizes() -> None:
    """EVOLUTION-JOURNAL-OWNER-READ-FANOUT-20261017: Unfiltered default page
    must bound owner reads across multiple history sizes (N=10, N=50).

    In both cases, list_evolution_decisions must be called exactly once, and
    mutation review hydration must reuse the already-read decision without
    issuing get_evolution_decision_by_id calls.
    """
    with tempfile.TemporaryDirectory() as td:
        for n_decisions in (10, 50):
            decisions = [
                {
                    "id": f"evo-dec-hist-{n_decisions}-{i:03d}",
                    "decision_id": f"evo-dec-hist-{n_decisions}-{i:03d}",
                    "target_type": "artifact",
                    "target_id": f"art-hist-{i:03d}",
                    "target_version": "v1.0.0",
                    "action_type": "mutation",
                    "status": "pending",
                    "decision_state": "pending",
                    "risk_level": "low",
                    "created_at": f"2026-06-{1 + (i % 25):02d}T00:00:00Z",
                    "updated_at": f"2026-06-{1 + (i % 25):02d}T00:00:00Z",
                    "occurred_at": f"2026-06-{1 + (i % 25):02d}T00:00:00Z",
                    "proposed_changes": {"summary": f"Summary {i}"},
                    "approval_decision_id": f"appr-{i:03d}",
                    "risk_assessment": {"risk_summary": "low"},
                }
                for i in range(n_decisions)
            ]
            data = {
                "evolution_decisions": {d["decision_id"]: d for d in decisions},
                "approval_decisions": {},
                "postmortems": {},
                "freeze_orders": {},
                "all_rollbacks": {},
            }
            store = _EvolutionJournalTestStore(data)
            app = _create_app_for_store(store)
            client = _JournalTestClient(app, raise_server_exceptions=True)

            store.list_evolution_decisions_call_count = 0
            store.get_evolution_decision_by_id_call_count = 0

            resp = client.get(
                "/bff/management/evolution-journal",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body["data"]["items"]
            page_info = body["page_info"]
            summary = body["data"]["summary"]

            # Owner reads must be exactly 1 list call, 0 per-row lookups
            assert store.list_evolution_decisions_call_count == 1
            assert store.get_evolution_decision_by_id_call_count == 0

            # Sliced page items must not exceed page_size (20)
            expected_total = n_decisions * 2 + 6  # 2 decisions per entry (decision + mutation_review) + 6 fixture items (postmortems, freeze orders, rollbacks)
            expected_page_size = min(expected_total, 20)
            assert len(items) == expected_page_size
            assert page_info["page_size"] == 20

            # Summary preserves full filtered totals across all history
            assert summary["total_items"] == expected_total
            assert summary["returned_items"] == expected_page_size
            assert summary["decision_count"] == n_decisions
            assert summary["mutation_review_count"] == n_decisions


def test_divergent_status_decision_state_filtering_and_canonical_summary() -> None:
    """EVOLUTION-JOURNAL-FILTER-SEMANTICS-20261017: Status filtering and full summary
    must obey canonical decision_state precedence for mutation_review and status
    precedence for evolution_decision BEFORE slicing.

    A decision with status='pending' and decision_state='approved' produces:
    - evolution_decision with status='pending' (status FIRST)
    - mutation_review with status='approved' (decision_state FIRST)

    Filtering ?status=approved selects mutation_review and omits evolution_decision.
    Filtering ?status=pending selects evolution_decision and omits mutation_review.
    Summary by_status preserves both distinct canonical counts, and owner calls
    remain strictly bounded.
    """
    record = {
        "id": "evo-dec-divergent-001",
        "decision_id": "evo-dec-divergent-001",
        "target_type": "artifact",
        "target_id": "art-divergent-001",
        "target_version": "1.0",
        "action_type": "mutation",
        "status": "pending",
        "decision_state": "approved",
        "risk_level": "low",
        "created_at": "2026-10-17T00:00:00Z",
        "proposed_changes": {"summary": "Divergent status fixture"},
        "approval_decision_id": "appr-divergent-001",
    }
    data = {
        "evolution_decisions": {record["id"]: record},
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    # 1. Unfiltered request for mutation_review: renders status='approved'
    store.list_evolution_decisions_call_count = 0
    store.get_evolution_decision_by_id_call_count = 0
    resp_mr = client.get(
        f"/bff/management/evolution-journal?mutation_review={record['id']}",
        headers=OPERATOR_HEADERS,
    )
    assert resp_mr.status_code == 200, resp_mr.text
    body_mr = resp_mr.json()
    items_mr = body_mr["data"]["items"]
    assert len(items_mr) == 1
    assert items_mr[0]["status"] == "approved"
    assert items_mr[0]["entry_type"] == "mutation_review"
    assert (items_mr[0].get("mutation_review") or {}).get("decision_state") == "approved"

    # 2. Unfiltered request for decision: renders status='pending' (evolution_journal_status status FIRST)
    resp_dec = client.get(
        f"/bff/management/evolution-journal?decision={record['id']}",
        headers=OPERATOR_HEADERS,
    )
    assert resp_dec.status_code == 200, resp_dec.text
    body_dec = resp_dec.json()
    items_dec = body_dec["data"]["items"]
    assert len(items_dec) == 1
    assert items_dec[0]["status"] == "pending"
    assert items_dec[0]["entry_type"] == "evolution_decision"

    # 3. Filter status=approved: selects mutation_review, omits evolution_decision
    resp_appr = client.get(
        "/bff/management/evolution-journal?status=approved",
        headers=OPERATOR_HEADERS,
    )
    assert resp_appr.status_code == 200, resp_appr.text
    items_appr = resp_appr.json()["data"]["items"]
    matched_mr = [it for it in items_appr if it.get("source_id") == record["id"]]
    assert len(matched_mr) == 1
    assert matched_mr[0]["entry_type"] == "mutation_review"
    assert matched_mr[0]["status"] == "approved"

    # 4. Filter status=pending: selects evolution_decision, omits mutation_review
    resp_pend = client.get(
        "/bff/management/evolution-journal?status=pending",
        headers=OPERATOR_HEADERS,
    )
    assert resp_pend.status_code == 200, resp_pend.text
    items_pend = resp_pend.json()["data"]["items"]
    matched_pend = [it for it in items_pend if it.get("source_id") == record["id"]]
    assert len(matched_pend) == 1
    assert matched_pend[0]["entry_type"] == "evolution_decision"
    assert matched_pend[0]["status"] == "pending"

    # 5. Full summary reflects distinct states for both entry types
    resp_all = client.get(
        "/bff/management/evolution-journal",
        headers=OPERATOR_HEADERS,
    )
    assert resp_all.status_code == 200
    summary = resp_all.json()["data"]["summary"]
    assert summary["by_status"]["approved"] >= 1
    assert summary["by_status"]["pending"] >= 1
    assert summary["decision_count"] == 1
    assert summary["mutation_review_count"] == 1

    # Owner queries must remain bounded across calls: 0 per-row lookups
    assert store.get_evolution_decision_by_id_call_count == 0


def test_full_summary_by_status_consistent_across_pages_and_history_sizes() -> None:
    """EVOLUTION-JOURNAL-FILTER-SEMANTICS-20261017: Summary by_status must be consistent
    across pages and history sizes (N=30 decisions, exceeding default page_size=20),
    correctly projecting off-page rows without full-history deep hydration.

    Each raw decision has status='pending' and decision_state='approved':
    - 30 evolution_decisions with status='pending'
    - 30 mutation_reviews with status='approved'
    """
    n_decisions = 30
    decisions = [
        {
            "id": f"evo-dec-page-{i:03d}",
            "decision_id": f"evo-dec-page-{i:03d}",
            "target_type": "artifact",
            "target_id": f"art-page-{i:03d}",
            "target_version": "v1.0.0",
            "action_type": "mutation",
            "status": "pending",
            "decision_state": "approved",
            "risk_level": "low",
            "created_at": f"2026-06-{1 + (i % 25):02d}T00:00:00Z",
            "updated_at": f"2026-06-{1 + (i % 25):02d}T00:00:00Z",
            "occurred_at": f"2026-06-{1 + (i % 25):02d}T00:00:00Z",
            "proposed_changes": {"summary": f"Summary for dec {i}"},
            "approval_decision_id": f"appr-page-{i:03d}",
            "risk_assessment": {"risk_summary": "low"},
        }
        for i in range(n_decisions)
    ]
    data = {
        "evolution_decisions": {d["decision_id"]: d for d in decisions},
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    store.list_evolution_decisions_call_count = 0
    store.get_evolution_decision_by_id_call_count = 0

    # Unfiltered page 1: 20 items returned out of total (30*2 + 6 default fixtures = 66)
    resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["data"]["items"]
    summary = body["data"]["summary"]

    assert len(items) == 20
    # Returned items reflect distinct entry-type statuses:
    # evolution_decision has status='pending'; mutation_review has status='approved'
    for it in items:
        if it.get("entry_type") == "evolution_decision":
            assert it["status"] == "pending"
        elif it.get("entry_type") == "mutation_review":
            assert it["status"] == "approved"

    # Full summary across all 66 items reflects canonical states without full hydration
    assert summary["by_status"]["approved"] == n_decisions
    assert summary["by_status"]["pending"] == n_decisions
    assert summary["decision_count"] == n_decisions
    assert summary["mutation_review_count"] == n_decisions

    # Filtering by approved retains all 30 mutation_review items
    resp_appr = client.get(
        "/bff/management/evolution-journal?status=approved",
        headers=OPERATOR_HEADERS,
    )
    assert resp_appr.status_code == 200
    appr_data = resp_appr.json()["data"]
    assert appr_data["summary"]["total_items"] == n_decisions
    assert appr_data["summary"]["by_status"]["approved"] == n_decisions
    assert len(appr_data["items"]) == 20
    for it in appr_data["items"]:
        assert it.get("entry_type") == "mutation_review"
        assert it["status"] == "approved"

    # Page 2 pagination retains approved summary consistency
    next_tok = resp_appr.json()["page_info"]["next_page_token"]
    assert next_tok is not None
    resp_appr_p2 = client.get(
        f"/bff/management/evolution-journal?status=approved&page_token={next_tok}",
        headers=OPERATOR_HEADERS,
    )
    assert resp_appr_p2.status_code == 200
    appr_p2_data = resp_appr_p2.json()["data"]
    assert len(appr_p2_data["items"]) == 10
    assert appr_p2_data["summary"]["total_items"] == n_decisions
    for it in appr_p2_data["items"]:
        assert it.get("entry_type") == "mutation_review"
        assert it["status"] == "approved"

    # Filtering by pending yields all 30 evolution_decision items
    resp_pend = client.get(
        "/bff/management/evolution-journal?status=pending",
        headers=OPERATOR_HEADERS,
    )
    assert resp_pend.status_code == 200
    pend_data = resp_pend.json()["data"]
    assert pend_data["summary"]["total_items"] == n_decisions
    assert pend_data["summary"]["by_status"]["pending"] == n_decisions
    for it in pend_data["items"]:
        assert it.get("entry_type") == "evolution_decision"
        assert it["status"] == "pending"

    # Bounded owner reads: 0 per-row lookups across all queries
    assert store.get_evolution_decision_by_id_call_count == 0


def test_persona_lineage_filtering_through_linked_incident() -> None:
    """EVOLUTION-JOURNAL-FILTER-SEMANTICS-20261017: Persona lineage filtering must
    match mutation reviews and decisions through linked_incident_id using existing
    _EVOLUTION_JOURNAL_REFERENCE_FIELD_CATEGORY without postmortem graph traversal.
    """
    persona_id = "persona-gamma"
    runtime_id = "runtime-gamma-001"
    runtime_binding_id = "binding-gamma-001"
    incident_id = "inc-gamma-001"
    postmortem_id = "pm-gamma-001"

    data = {
        "personas": {
            persona_id: {
                "id": persona_id,
                "persona_id": persona_id,
                "runtime_id": runtime_id,
            }
        },
        "runtime_bindings": {
            runtime_binding_id: {
                "id": runtime_binding_id,
                "runtime_id": runtime_id,
                "persona_id": persona_id,
            }
        },
        "incidents": {
            incident_id: {
                "id": incident_id,
                "incident_id": incident_id,
                "runtime_id": runtime_id,
            }
        },
        "postmortems": {
            postmortem_id: {
                "id": postmortem_id,
                "postmortem_id": postmortem_id,
                "incident_id": incident_id,
                "title": "Postmortem for inc-gamma-001",
                "status": "published",
                "created_at": "2026-07-01T00:00:00Z",
            }
        },
        "evolution_decisions": {
            "dec-linked-inc": {
                "id": "dec-linked-inc",
                "decision_id": "dec-linked-inc",
                "target_type": "artifact",
                "target_id": "art-unrelated-1",
                "linked_incident_id": incident_id,
                "status": "pending",
                "decision_state": "approved",
                "action_type": "mutation",
                "risk_level": "low",
                "proposed_changes": {"summary": "Linked via incident"},
                "approval_decision_id": "appr-inc-1",
                "created_at": "2026-07-02T00:00:00Z",
            },
            "dec-linked-pm": {
                "id": "dec-linked-pm",
                "decision_id": "dec-linked-pm",
                "target_type": "artifact",
                "target_id": "art-unrelated-2",
                "linked_postmortem_id": postmortem_id,
                "status": "pending",
                "decision_state": "approved",
                "action_type": "mutation",
                "risk_level": "low",
                "proposed_changes": {"summary": "Linked via postmortem"},
                "approval_decision_id": "appr-pm-1",
                "created_at": "2026-07-03T00:00:00Z",
            },
            "dec-unrelated": {
                "id": "dec-unrelated",
                "decision_id": "dec-unrelated",
                "target_type": "artifact",
                "target_id": "art-other",
                "status": "pending",
                "decision_state": "approved",
                "action_type": "mutation",
                "risk_level": "low",
                "proposed_changes": {"summary": "Unrelated decision"},
                "approval_decision_id": "appr-unrelated",
                "created_at": "2026-07-04T00:00:00Z",
            },
        },
        "approval_decisions": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    store.list_evolution_decisions_call_count = 0
    store.get_evolution_decision_by_id_call_count = 0

    resp = client.get(
        f"/bff/management/evolution-journal?persona={persona_id}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]

    matched_sources = {it["source_id"] for it in items}
    # dec-linked-inc must match through linked_incident_id
    assert "dec-linked-inc" in matched_sources
    # dec-linked-pm has NO postmortem category in REFERENCE_FIELD_CATEGORY and must NOT match
    assert "dec-linked-pm" not in matched_sources
    # dec-unrelated must NOT match
    assert "dec-unrelated" not in matched_sources

    # Both evolution_decision and mutation_review items for dec-linked-inc are included
    types_for_inc = {it["entry_type"] for it in items if it["source_id"] == "dec-linked-inc"}
    assert "evolution_decision" in types_for_inc
    assert "mutation_review" in types_for_inc

    # Owner queries remain bounded
    assert store.get_evolution_decision_by_id_call_count == 0


def test_lightweight_callback_semantic_defaults_and_summary() -> None:
    """EVOLUTION-JOURNAL-FILTER-SEMANTICS-20261017: Configured canonical projection
    allows target_type/action_type/risk_level to be None and target_version must NOT
    fallback to artifact_version. Off-page summaries must not depend on page hydration.
    Preserve callback-absent old fallback separately.
    """
    record = {
        "id": "evo-dec-none-defaults",
        "decision_id": "evo-dec-none-defaults",
        "target_id": "art-none-001",
        "artifact_id": "art-none-001",
        "artifact_version": "v9.9.9",
        # target_type, target_version, action_type, risk_level, decision_state, status are all None
        "approval_decision_id": "appr-none-001",
        "created_at": "2026-10-17T00:00:00Z",
    }
    data = {
        "evolution_decisions": {record["id"]: record},
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    store = _EvolutionJournalTestStore(data)

    # 1. Configured canonical projection callback
    app_with_cb = _create_app_for_store(store)
    client_with_cb = _JournalTestClient(app_with_cb, raise_server_exceptions=True)

    resp = client_with_cb.get(
        f"/bff/management/evolution-journal?mutation_review={record['id']}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"]["items"][0]
    mr = item["mutation_review"]

    # Canonical projection preserves None: target_version must NOT fallback to artifact_version
    assert mr.get("target_type") is None
    assert mr.get("target_version") is None
    assert mr.get("action_type") is None
    assert mr.get("risk_level") is None
    assert item["target"].get("version") is None
    assert item["status"] == "unknown"

    # Off-page summary does not replace None with defaults
    summary = resp.json()["data"]["summary"]
    assert summary["by_risk_level"].get("low", 0) == 0

    # Filtering by risk_level=low does NOT match this item
    resp_filtered = client_with_cb.get(
        f"/bff/management/evolution-journal?mutation_review={record['id']}&risk_level=low",
        headers=OPERATOR_HEADERS,
    )
    assert resp_filtered.status_code == 200
    assert len(resp_filtered.json()["data"]["items"]) == 0

    # 2. Callback-absent fallback (mutation_review_projection is None)
    app_no_cb = _create_app_for_store(store, mutation_review_projection=None)
    client_no_cb = _JournalTestClient(app_no_cb, raise_server_exceptions=True)

    resp_no_cb = client_no_cb.get(
        f"/bff/management/evolution-journal?mutation_review={record['id']}",
        headers=OPERATOR_HEADERS,
    )
    assert resp_no_cb.status_code == 200, resp_no_cb.text
    item_no_cb = resp_no_cb.json()["data"]["items"][0]
    mr_no_cb = item_no_cb["mutation_review"]

    # Callback-absent old fallback preserves previous defaults
    assert mr_no_cb.get("target_type") == "artifact"
    assert mr_no_cb.get("target_version") == "v9.9.9"
    assert mr_no_cb.get("action_type") == "mutation"
    assert mr_no_cb.get("risk_level") == "low"
    assert item_no_cb["status"] == "pending"

