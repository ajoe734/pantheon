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
            assert set(body["data"].keys()) == {"id", "items", "summary", "auto_proposal_counts"}
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
            # One row per decision: the mutation review is nested in the decision row.
            assert not [item for item in items if item["entry_type"] == "mutation_review"]
            mutation_review = next(
                item for item in items
                if item["entry_type"] == "evolution_decision"
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
            assert item["entry_type"] == "evolution_decision"
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
                assert item["entry_type"] == "evolution_decision"
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
            assert len(items) == 1
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
            assert items_mr[0]["entry_type"] == "evolution_decision"
            assert items_mr[0]["mutation_review"]["decision_id"] == "dec-1"

            # Querying for nonexistent decision should return 0 items
            resp = client.get("/bff/management/evolution-journal?decision=nonexistent", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            assert len(resp.json()["data"]["items"]) == 0

            # 3. Test authoritative seed/live/unknown provenance
            resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            # 6 decisions (mutation review nested) + 6 backfilled default items = 12 total items
            assert len(items) == 12

            by_id_and_type = {(item["source_id"], item["entry_type"]): item for item in items}
            assert by_id_and_type[("evo-vslice-1", "evolution_decision")]["origin"] == "seed"
            assert by_id_and_type[("dec-live", "evolution_decision")]["origin"] == "live"
            assert by_id_and_type[("dec-explicit-unknown", "evolution_decision")]["origin"] == "unknown"
            assert by_id_and_type[("dec-1", "evolution_decision")]["origin"] == "unknown"

            # Verify mutation reviews also preserve the correct origin
            assert not [key for key in by_id_and_type if key[1] == "mutation_review"]

            # 4. Test filtered total plus next token (paging)
            # persona-1 lineage owns exactly one decision row (mutation review nested), so one page.
            resp = client.get("/bff/management/evolution-journal?persona=persona-1&page_size=1", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 1
            assert len(body["data"]["items"]) == 1
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
            expected_total = n_decisions + 6  # 1 row per decision (mutation_review nested) + 6 fixture items (postmortems, freeze orders, rollbacks)
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

    A decision with status='pending' and decision_state='approved' produces ONE
    evolution_decision row with status='pending' (status FIRST) whose nested
    mutation_review keeps decision_state='approved'.

    Filtering ?status=pending selects the row; ?status=approved does not, because
    row status follows the decision status. The divergence stays visible nested.
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
    assert items_mr[0]["status"] == "pending"
    assert items_mr[0]["entry_type"] == "evolution_decision"
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

    # 3. Filter status=approved: the single row follows decision status, so it is omitted
    resp_appr = client.get(
        "/bff/management/evolution-journal?status=approved",
        headers=OPERATOR_HEADERS,
    )
    assert resp_appr.status_code == 200, resp_appr.text
    items_appr = resp_appr.json()["data"]["items"]
    assert [it for it in items_appr if it.get("source_id") == record["id"]] == []

    # 4. Filter status=pending: selects the single evolution_decision row
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

    # 5. Full summary counts the one row once
    resp_all = client.get(
        "/bff/management/evolution-journal",
        headers=OPERATOR_HEADERS,
    )
    assert resp_all.status_code == 200
    summary = resp_all.json()["data"]["summary"]
    assert "approved" not in summary["by_status"]
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
    - 30 evolution_decision rows with status='pending', each carrying a nested
      mutation_review whose decision_state is 'approved'
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

    # Unfiltered page 1: 20 items returned out of total (30 + 6 default fixtures = 36)
    resp = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["data"]["items"]
    summary = body["data"]["summary"]

    assert len(items) == 20
    for it in items:
        assert it.get("entry_type") != "mutation_review"
        if it.get("entry_type") == "evolution_decision":
            assert it["status"] == "pending"
            assert it["mutation_review"]["decision_state"] == "approved"

    # Full summary across all 36 rows reflects canonical states without full hydration
    assert "approved" not in summary["by_status"]
    assert summary["by_status"]["pending"] == n_decisions
    assert summary["decision_count"] == n_decisions
    assert summary["mutation_review_count"] == n_decisions

    # Row status follows the decision status, so approved matches nothing
    resp_appr = client.get(
        "/bff/management/evolution-journal?status=approved",
        headers=OPERATOR_HEADERS,
    )
    assert resp_appr.status_code == 200
    assert resp_appr.json()["data"]["summary"]["total_items"] == 0

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

    # One decision row for dec-linked-inc, carrying the nested mutation_review
    rows_for_inc = [it for it in items if it["source_id"] == "dec-linked-inc"]
    assert [it["entry_type"] for it in rows_for_inc] == ["evolution_decision"]
    assert isinstance(rows_for_inc[0]["mutation_review"], dict)

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
    assert mr.get("decision_state") is None
    assert item["entry_type"] == "evolution_decision"

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
    assert mr_no_cb.get("decision_state") == "pending"
    assert item_no_cb["entry_type"] == "evolution_decision"


# --------------------------------------------------------------------------- #
# EVOLUTION-JOURNAL-EVIDENCE-CLASS-20261104 Regression & Contract Tests
# --------------------------------------------------------------------------- #


def _real_hosted_incident_followup_decision(
    decision_id: str = "evo-sweep-agent-000b205c0ef9-0000",
    *,
    created_at: str = "2026-11-04T01:00:00Z",
    linked_incident_id: str = "agent-000b205c0ef9-0000",
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "target_type": "candidate_artifact",
        "target_id": "infra-component-agent-finding-000b205c0ef9",
        "risk_level": "low",
        "linked_incident_id": linked_incident_id,
        "action_type": "flag_for_review",
        "decision_state": "proposed",
        "status": "proposed",
        "created_at": created_at,
        "updated_at": created_at,
        "threshold_snapshots": [
            {
                "breached": True,
                "comparator": "gte",
                "metric_name": "incident_followup_required",
                "observed_value": 1,
                "threshold_value": 1,
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                "signal_type": "manual_review",
                "window": "active-incident",
            }
        ],
    }


def _real_hosted_incident_severity_decision(
    decision_id: str = "evo-sweep-inc-deployment-comp-deployment-saga-plan-persona-paper-1bad2eca6831457cd885",
    *,
    created_at: str = "2026-11-04T01:05:00Z",
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "target_type": "candidate_artifact",
        "target_id": "artifact-persona-paper-1bad2eca6831457cd885",
        "risk_level": "medium",
        "action_type": "freeze",
        "decision_state": "proposed",
        "status": "proposed",
        "created_at": created_at,
        "updated_at": created_at,
        "threshold_snapshots": [
            {
                "breached": True,
                "comparator": "gte",
                "metric_name": "severity1_incident_count",
                "observed_value": 1,
                "threshold_value": 1,
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                "signal_type": "governance_incident",
                "window": "active-incident",
            }
        ],
    }


def _threshold_signal_decision(
    decision_id: str,
    *,
    signal_type: str = "performance_degradation",
    decision_state: str = "proposed",
    created_at: str = "2026-11-04T01:10:00Z",
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "target_type": "candidate_artifact",
        "target_id": f"artifact-{decision_id}",
        "risk_level": "medium",
        "action_type": "retrain",
        "decision_state": decision_state,
        "status": decision_state,
        "created_at": created_at,
        "updated_at": created_at,
        "threshold_snapshots": [
            {
                "breached": True,
                "comparator": "gt",
                "metric_name": "rolling_drawdown_pct",
                "observed_value": 0.18,
                "threshold_value": 0.12,
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
                "signal_type": signal_type,
                "window": "7d",
            }
        ],
    }


def test_evidence_class_classification_on_decisions_and_mutation_reviews_real_hosted_shapes() -> None:
    """Every journal item derived from a decision including its mutation_review item
    carries evidence_class (threshold_signal, incident_followup, incident_severity).
    """
    dec_followup = _real_hosted_incident_followup_decision()
    dec_severity = _real_hosted_incident_severity_decision()
    signal_types = [
        "performance_degradation",
        "execution_drift",
        "feature_drift",
        "human_correction",
    ]
    threshold_decisions = [
        _threshold_signal_decision(f"evo-thresh-{sig}", signal_type=sig)
        for sig in signal_types
    ]
    all_decisions = [dec_followup, dec_severity, *threshold_decisions]
    data = {
        "evolution_decisions": {d["decision_id"]: d for d in all_decisions},
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    resp = client.get(
        "/bff/management/evolution-journal?page_size=50",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]

    by_id = {it["id"]: it for it in items}
    assert not [it for it in items if it["entry_type"] == "mutation_review"]
    decision_rows = [it for it in items if it["entry_type"] == "evolution_decision"]
    assert len(decision_rows) == len(all_decisions)

    for dec, expected in (
        (dec_followup, "incident_followup"),
        (dec_severity, "incident_severity"),
    ):
        row = by_id[f"evolution_decision:{dec['decision_id']}"]
        assert row["evidence_class"] == expected
        assert row["mutation_review"]["evidence_class"] == expected

    for sig in signal_types:
        row = by_id[f"evolution_decision:evo-thresh-{sig}"]
        assert row["evidence_class"] == "threshold_signal"
        assert row["mutation_review"]["evidence_class"] == "threshold_signal"


def test_view_filter_all_history_auto_proposals_and_auto_proposal_counts() -> None:
    """Verify view=all (and omitted view), view=history, view=auto_proposals, and
    auto_proposal_counts counted per record so a decision + mutation_review count once.
    """
    dec_followup = _real_hosted_incident_followup_decision()
    dec_severity = _real_hosted_incident_severity_decision()
    dec_thresh_proposed = _threshold_signal_decision(
        "evo-thresh-proposed-001",
        signal_type="execution_drift",
        decision_state="proposed",
        created_at="2026-11-04T01:15:00Z",
    )
    dec_approved = {
        **_real_hosted_incident_followup_decision(
            "evo-decided-approved-001",
            created_at="2026-11-04T01:20:00Z",
        ),
        "decision_state": "approved",
        "status": "approved",
    }
    data = {
        "evolution_decisions": {
            dec_followup["decision_id"]: dec_followup,
            dec_severity["decision_id"]: dec_severity,
            dec_thresh_proposed["decision_id"]: dec_thresh_proposed,
            dec_approved["decision_id"]: dec_approved,
        },
        "approval_decisions": {},
        "postmortems": {
            "pm-draft-001": {
                "postmortem_id": "pm-draft-001",
                "incident_id": "inc-draft-001",
                "title": "Draft postmortem 1",
                "status": "draft",
                "created_at": "2026-11-04T01:25:00Z",
            },
            "pm-draft-002": {
                "postmortem_id": "pm-draft-002",
                "incident_id": "inc-draft-002",
                "title": "Draft postmortem 2",
                "status": "draft",
                "created_at": "2026-11-04T01:26:00Z",
            },
            "pm-published-001": {
                "postmortem_id": "pm-published-001",
                "incident_id": "inc-pub-001",
                "title": "Published postmortem",
                "status": "published",
                "created_at": "2026-11-04T01:27:00Z",
            },
        },
        "freeze_orders": {
            "fo-hist-001": {
                "freeze_order_id": "fo-hist-001",
                "status": "active",
                "scope": "pool",
                "target_id": "pool-alpha",
                "created_at": "2026-11-04T01:28:00Z",
            }
        },
        "all_rollbacks": {
            "rb-hist-001": {
                "rollback_id": "rb-hist-001",
                "status": "completed",
                "action_type": "rollback",
                "runtime_id": "rt-alpha",
                "created_at": "2026-11-04T01:29:00Z",
            }
        },
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    expected_counts = {
        "incident_followup": 1,
        "incident_severity": 1,
        "postmortem_draft": 2,
        "total": 4,
    }

    # 1. Omitting view behaves identically to view=all
    resp_omitted = client.get("/bff/management/evolution-journal?page_size=50", headers=OPERATOR_HEADERS)
    resp_all = client.get("/bff/management/evolution-journal?view=all&page_size=50", headers=OPERATOR_HEADERS)
    assert resp_omitted.status_code == 200
    assert resp_all.status_code == 200
    body_omitted = resp_omitted.json()
    body_all = resp_all.json()
    assert [it["id"] for it in body_omitted["data"]["items"]] == [
        it["id"] for it in body_all["data"]["items"]
    ]
    assert body_omitted["data"]["auto_proposal_counts"] == expected_counts
    assert body_all["data"]["auto_proposal_counts"] == expected_counts
    # 4 decisions (one row each) + 3 postmortems + 1 freeze order + 1 rollback
    assert body_all["page_info"]["total"] == 9

    # 2. view=history returns freeze_order, rollback, non-draft postmortem,
    #    non-proposed decision (+ mutation_review), and proposed threshold_signal decision (+ mutation_review)
    resp_hist = client.get("/bff/management/evolution-journal?view=history&page_size=50", headers=OPERATOR_HEADERS)
    assert resp_hist.status_code == 200
    body_hist = resp_hist.json()
    hist_ids = {it["id"] for it in body_hist["data"]["items"]}
    assert hist_ids == {
        "freeze_order:fo-hist-001",
        "rollback:rb-hist-001",
        "postmortem:pm-published-001",
        f"evolution_decision:{dec_approved['decision_id']}",
        f"evolution_decision:{dec_thresh_proposed['decision_id']}",
    }
    assert body_hist["page_info"]["total"] == 5
    assert body_hist["data"]["summary"]["total_items"] == 5
    assert body_hist["data"]["auto_proposal_counts"] == expected_counts

    # 3. view=auto_proposals returns remaining proposed decisions (+ mutation_reviews) and draft postmortems
    resp_auto = client.get(
        "/bff/management/evolution-journal?view=auto_proposals&page_size=50",
        headers=OPERATOR_HEADERS,
    )
    assert resp_auto.status_code == 200
    body_auto = resp_auto.json()
    auto_ids = {it["id"] for it in body_auto["data"]["items"]}
    assert auto_ids == {
        f"evolution_decision:{dec_followup['decision_id']}",
        f"evolution_decision:{dec_severity['decision_id']}",
        "postmortem:pm-draft-001",
        "postmortem:pm-draft-002",
    }
    # The list and the counts agree: page_info.total == auto_proposal_counts.total
    assert body_auto["page_info"]["total"] == expected_counts["total"] == 4
    assert body_auto["data"]["summary"]["total_items"] == 4
    assert body_auto["data"]["auto_proposal_counts"] == expected_counts

    # 4. Invalid view is rejected with 400
    resp_bad = client.get("/bff/management/evolution-journal?view=bogus", headers=OPERATOR_HEADERS)
    assert resp_bad.status_code == 400


def test_view_history_filter_runs_before_pagination_across_multiple_pages() -> None:
    """Filter runs before pagination so page_info, next_page_token, and summary
    describe the filtered set, and a history item beyond the first raw page is returned.
    """
    # 15 auto-proposal decisions (15 rows, mutation_review nested) with newer timestamps
    auto_decisions = {
        f"evo-sweep-agent-{i:04d}": _real_hosted_incident_followup_decision(
            f"evo-sweep-agent-{i:04d}",
            created_at=f"2026-11-04T02:{i:02d}:00Z",
        )
        for i in range(15)
    }
    # 1 threshold_signal proposed decision + 1 freeze_order + 1 rollback = 3 history items
    # All have older timestamps (00:01..00:03), placing them at raw indices 16..18 (beyond raw page 1)
    hist_thresh = _threshold_signal_decision(
        "evo-thresh-old-001",
        signal_type="performance_degradation",
        decision_state="proposed",
        created_at="2026-11-04T00:03:00Z",
    )
    all_decisions = {**auto_decisions, hist_thresh["decision_id"]: hist_thresh}
    data = {
        "evolution_decisions": all_decisions,
        "approval_decisions": {},
        "postmortems": {
            "pm-draft-new": {
                "postmortem_id": "pm-draft-new",
                "incident_id": "inc-draft-new",
                "title": "Draft PM",
                "status": "draft",
                "created_at": "2026-11-04T03:00:00Z",
            }
        },
        "freeze_orders": {
            "fo-old-001": {
                "freeze_order_id": "fo-old-001",
                "status": "active",
                "scope": "pool",
                "target_id": "pool-1",
                "created_at": "2026-11-04T00:02:00Z",
            }
        },
        "all_rollbacks": {
            "rb-oldest-001": {
                "rollback_id": "rb-oldest-001",
                "status": "completed",
                "action_type": "rollback",
                "runtime_id": "rt-1",
                "created_at": "2026-11-04T00:01:00Z",
            }
        },
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    # Raw page 1 (view=all, page_size=2) only sees the newest auto proposals
    raw_p1 = client.get("/bff/management/evolution-journal?page_size=2", headers=OPERATOR_HEADERS).json()
    assert raw_p1["page_info"]["total"] == 19

    # Filtered page 1 (view=history, page_size=2) sees the first 2 of 3 history items
    hist_p1_resp = client.get(
        "/bff/management/evolution-journal?view=history&page_size=2",
        headers=OPERATOR_HEADERS,
    )
    assert hist_p1_resp.status_code == 200
    hist_p1 = hist_p1_resp.json()
    assert hist_p1["page_info"]["total"] == 3
    assert hist_p1["page_info"]["page_size"] == 2
    assert hist_p1["page_info"]["next_page_token"] == "2"
    assert hist_p1["data"]["summary"]["total_items"] == 3
    assert hist_p1["data"]["summary"]["returned_items"] == 2
    assert len(hist_p1["data"]["items"]) == 2

    # Filtered page 2 (view=history, page_size=2, page_token=2) returns the remaining history item
    hist_p2_resp = client.get(
        "/bff/management/evolution-journal?view=history&page_size=2&page_token=2",
        headers=OPERATOR_HEADERS,
    )
    assert hist_p2_resp.status_code == 200
    hist_p2 = hist_p2_resp.json()
    assert hist_p2["page_info"]["total"] == 3
    assert hist_p2["page_info"]["next_page_token"] is None
    assert hist_p2["data"]["summary"]["total_items"] == 3
    assert hist_p2["data"]["summary"]["returned_items"] == 1
    p2_ids = [it["id"] for it in hist_p2["data"]["items"]]
    assert p2_ids == ["rollback:rb-oldest-001"]

    # view=auto_proposals across several pages: list total equals the counts total
    auto_resp = client.get(
        "/bff/management/evolution-journal?view=auto_proposals&page_size=4",
        headers=OPERATOR_HEADERS,
    )
    assert auto_resp.status_code == 200
    auto_body = auto_resp.json()
    counts = auto_body["data"]["auto_proposal_counts"]
    assert counts == {
        "incident_followup": 15,
        "incident_severity": 0,
        "postmortem_draft": 1,
        "total": 16,
    }
    assert auto_body["page_info"]["total"] == counts["total"]
    assert auto_body["data"]["summary"]["total_items"] == counts["total"]
    seen_ids: list[str] = []
    token = auto_body["page_info"]["next_page_token"]
    seen_ids.extend(it["id"] for it in auto_body["data"]["items"])
    while token is not None:
        page = client.get(
            f"/bff/management/evolution-journal?view=auto_proposals&page_size=4&page_token={token}",
            headers=OPERATOR_HEADERS,
        ).json()
        assert page["page_info"]["total"] == counts["total"]
        seen_ids.extend(it["id"] for it in page["data"]["items"])
        token = page["page_info"]["next_page_token"]
    assert len(seen_ids) == len(set(seen_ids)) == counts["total"]


def test_explicit_deep_link_filters_decision_mutation_review_and_persona_return_target_under_any_view() -> None:
    """Explicit filters decision, mutation_review, and persona still return their
    target record under any view so a deep link never lands on an empty page.
    """
    persona_id = "persona-paper-1bad2eca6831457cd885"
    runtime_id = "rt-paper-1bad2eca"
    incident_id = "agent-000b205c0ef9-0000"
    dec_followup = _real_hosted_incident_followup_decision(linked_incident_id=incident_id)
    dec_approved = {
        **_real_hosted_incident_severity_decision("evo-approved-deep-link"),
        "decision_state": "approved",
        "status": "approved",
    }
    data = {
        "personas": {
            persona_id: {
                "id": persona_id,
                "persona_id": persona_id,
                "runtime_id": runtime_id,
            }
        },
        "runtime_bindings": {
            "bind-deep-1": {
                "id": "bind-deep-1",
                "binding_id": "bind-deep-1",
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
        "evolution_decisions": {
            dec_followup["decision_id"]: dec_followup,
            dec_approved["decision_id"]: dec_approved,
        },
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    store = _EvolutionJournalTestStore(data)
    app = _create_app_for_store(store)
    client = _JournalTestClient(app, raise_server_exceptions=True)

    for view_val in ("all", "history", "auto_proposals"):
        # 1. Deep link by decision (auto proposal opened under view=history, and history decision under view=auto_proposals)
        r_dec_auto = client.get(
            f"/bff/management/evolution-journal?view={view_val}&decision={dec_followup['decision_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert r_dec_auto.status_code == 200
        items_dec_auto = r_dec_auto.json()["data"]["items"]
        assert len(items_dec_auto) == 1
        assert items_dec_auto[0]["id"] == f"evolution_decision:{dec_followup['decision_id']}"

        r_dec_hist = client.get(
            f"/bff/management/evolution-journal?view={view_val}&decision={dec_approved['decision_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert r_dec_hist.status_code == 200
        items_dec_hist = r_dec_hist.json()["data"]["items"]
        assert len(items_dec_hist) == 1
        assert items_dec_hist[0]["id"] == f"evolution_decision:{dec_approved['decision_id']}"

        # 2. Deep link by mutation_review
        r_mr = client.get(
            f"/bff/management/evolution-journal?view={view_val}&mutation_review={dec_followup['decision_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert r_mr.status_code == 200
        items_mr = r_mr.json()["data"]["items"]
        assert len(items_mr) == 1
        assert items_mr[0]["id"] == f"evolution_decision:{dec_followup['decision_id']}"
        assert items_mr[0]["mutation_review"]["decision_id"] == dec_followup["decision_id"]
        assert items_mr[0]["evidence_class"] == "incident_followup"

        # 2b. source_type=mutation_review selects decision rows that carry a projection
        r_st = client.get(
            f"/bff/management/evolution-journal?view={view_val}&source_type=mutation_review&decision={dec_followup['decision_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert r_st.status_code == 200
        items_st = r_st.json()["data"]["items"]
        assert [it["id"] for it in items_st] == [f"evolution_decision:{dec_followup['decision_id']}"]

        # 3. Deep link by persona (matches dec_followup via linked_incident_id under any view)
        r_persona = client.get(
            f"/bff/management/evolution-journal?view={view_val}&persona={persona_id}",
            headers=OPERATOR_HEADERS,
        )
        assert r_persona.status_code == 200
        items_persona = r_persona.json()["data"]["items"]
        matched_ids = {it["id"] for it in items_persona}
        assert f"evolution_decision:{dec_followup['decision_id']}" in matched_ids
        assert not [i for i in matched_ids if i.startswith("mutation_review:")]


def test_bounded_postmortem_owner_read_with_2027_real_draft_postmortems_and_no_owner_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """With 2027 draft postmortems of the real 23-key shape (~2.8KB each) in the
    postmortems owner service, a journal request stays bounded: the owner is not
    asked for every postmortem on each page read, body size and owner calls are
    bounded for the default view and view=history, and no record is mutated.
    """
    from pathlib import Path
    from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
        CompositeLifecycleTelemetryGovernancePort,
        DomainGovernancePort,
        DomainIncidentPort,
        DomainLifecyclePort,
        DomainTelemetryPort,
        InMemoryLineagePort,
    )
    from services.control_plane.bff.ports.persona_capital_runtime import (
        create_in_memory_persona_capital_runtime_port,
    )
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
    from services.control_plane.bff.tests.test_postmortem_owner_read import (
        LivePostmortemsOwnerServer,
        build_real_postmortem_dict,
    )

    postmortems_2027 = [build_real_postmortem_dict(i, status="draft") for i in range(2027)]
    dec_followup = _real_hosted_incident_followup_decision()
    dec_severity = _real_hosted_incident_severity_decision()
    dec_history = {
        **_threshold_signal_decision(
            "evo-dec-real-history-001",
            signal_type="performance_degradation",
            decision_state="approved",
            created_at="2026-11-04T02:00:00Z",
        ),
    }
    decisions_map = {
        dec_followup["decision_id"]: dec_followup,
        dec_severity["decision_id"]: dec_severity,
        dec_history["decision_id"]: dec_history,
    }

    with LivePostmortemsOwnerServer(Path(tmp_path) / "pm_owner.json", postmortems=postmortems_2027) as owner_srv:
        monkeypatch.setenv("PANTHEON_POSTMORTEMS_API_URL", owner_srv.base_url)
        inc_port = DomainIncidentPort(
            incidents={},
            evolution_decisions=decisions_map,
            postmortems_api_url=owner_srv.base_url,
        )
        ltg_port = CompositeLifecycleTelemetryGovernancePort(
            incident_port=inc_port,
            lifecycle_port=DomainLifecyclePort(incidents={}),
            governance_port=DomainGovernancePort(
                evolution_decisions=decisions_map,
                freeze_orders={},
                all_rollbacks=[],
            ),
            lineage_port=InMemoryLineagePort(),
            telemetry_port=DomainTelemetryPort(),
        )
        pcr_port = create_in_memory_persona_capital_runtime_port(
            evolution_decisions=list(decisions_map.values()),
        )
        ports = ReadSurfacePorts(
            persona_capital_runtime=pcr_port,
            lifecycle_telemetry_governance=ltg_port,
        )
        app = _create_app_for_store(ports)
        client = _JournalTestClient(app, raise_server_exceptions=True)

        # 1. Default view (view omitted -> all, page_size=20)
        owner_srv.requests_received.clear()
        resp_default = client.get("/bff/management/evolution-journal", headers=OPERATOR_HEADERS)
        assert resp_default.status_code == 200, resp_default.text
        default_calls = list(owner_srv.requests_received)
        # Owner is called at most twice (non-draft bounded page + draft bounded page_size=20), never 11+ pages or 2027 times
        assert 1 <= len(default_calls) <= 2, default_calls
        assert any("status=draft" in path and "page_size=20" in path for path in default_calls), default_calls
        # Body size for the 20-item page stays well under 250 KB (vs ~5.7 MB for 2027 postmortems)
        assert len(resp_default.content) < 250_000, len(resp_default.content)
        body_default = resp_default.json()
        assert len(body_default["data"]["items"]) == 20
        assert body_default["data"]["auto_proposal_counts"] == {
            "incident_followup": 1,
            "incident_severity": 1,
            "postmortem_draft": 2027,
            "total": 2029,
        }
        assert body_default["page_info"]["total"] == 2027 + 3

        # 2. History view (view=history)
        owner_srv.requests_received.clear()
        resp_history = client.get(
            "/bff/management/evolution-journal?view=history",
            headers=OPERATOR_HEADERS,
        )
        assert resp_history.status_code == 200, resp_history.text
        history_calls = list(owner_srv.requests_received)
        assert 1 <= len(history_calls) <= 2, history_calls
        assert any("status=draft" in path and "page_size=1" in path for path in history_calls), history_calls
        assert len(resp_history.content) < 50_000, len(resp_history.content)
        body_history = resp_history.json()
        assert {it["id"] for it in body_history["data"]["items"]} == {
            f"evolution_decision:{dec_history['decision_id']}",
        }
        assert body_history["data"]["auto_proposal_counts"] == {
            "incident_followup": 1,
            "incident_severity": 1,
            "postmortem_draft": 2027,
            "total": 2029,
        }

        # 3. No proposal or postmortem was deleted or changed in state
        assert len(owner_srv.store._postmortems) == 2027
        assert dec_followup["decision_state"] == "proposed"
        assert dec_severity["decision_state"] == "proposed"


# --- Journal freeze/rollback from the canonical Governance owner (mounted HTTP) ---
# Real Journal router + middleware + default ReadSurfacePorts + real owner
# transport; only the outbound urlopen (the owner network edge) is faked.

def _mounted_owner_journal(monkeypatch, governance: dict, decisions: dict) -> dict:
    import base64
    import io
    import urllib.error
    from unittest.mock import patch

    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.personas.service import _dataset_surface_status as persona_surface_status
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance.test")
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution.test")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "svc-token")
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime.test")
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", "fresh")
    claims = base64.urlsafe_b64encode(json.dumps({"tenant_id": "t-own"}).encode()).decode().rstrip("=")
    auth = f"Bearer test.{claims}.test"
    calls: list[dict] = []

    class _Resp(io.BytesIO):
        status = 200

    def transport(req, timeout=None):
        tenant = req.get_header("X-tenant-id")
        calls.append({"url": req.full_url, "tenant": tenant})
        tail = req.full_url.rsplit("/", 1)[-1]
        if req.full_url.startswith("http://runtime.test/"):
            return _Resp(json.dumps({"bindings": [{"binding_id": "rt1", "tenant_id": "t-own"}]}).encode())
        if req.full_url.startswith("http://evolution.test/"):
            d = decisions.get(tail)
            if d is None or d.get("tenant_id") != tenant:
                raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
            return _Resp(json.dumps(d).encode())
        value = governance[tail]
        if isinstance(value, Exception):
            raise value
        return _Resp(json.dumps(value).encode())

    store = ReadSurfacePorts()
    store.list_evolution_decisions = lambda: []
    store.list_postmortems = lambda **kw: []
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(create_evolution_router(
        read_surface=store,
        extract_identity=lambda a: object(),
        require_read_role=lambda i: None,
        dataset_surface_status=lambda dataset, **kw: persona_surface_status(dataset, read_store=store, **kw),
    ))
    with patch("urllib.request.urlopen", transport), TestClient(app) as client:
        res = client.get("/bff/management/evolution-journal", headers={"Authorization": auth, "X-Tenant-Id": "t-own"})
    assert res.status_code == 200, res.text
    body = res.json()
    return {
        "ids": [r["source_id"] for r in body["data"]["items"]],
        "freeze": body["meta"]["surfaces"]["freeze_orders"],
        "rollbacks": body["meta"]["surfaces"]["rollbacks"],
        "calls": calls,
    }


_JOURNAL_DECISIONS = {
    "d-own": {"decision_id": "d-own", "tenant_id": "t-own", "target_id": "a1", "target_version": "v1", "action_type": "freeze"},
    "d-foreign": {"decision_id": "d-foreign", "tenant_id": "t-other", "target_id": "a9", "target_version": "v1", "action_type": "freeze"},
    "d-rb": {"decision_id": "d-rb", "tenant_id": "t-own", "target_id": "rt1", "action_type": "force_risk_off"},
}


def test_journal_owner_reads_prove_tenant_via_decision_not_row_body(monkeypatch) -> None:
    result = _mounted_owner_journal(
        monkeypatch,
        {
            "freeze-orders": [
                {"freeze_order_id": "own", "status": "active", "evolution_decision_id": "d-own",
                 "tenant_id": "t-own", "target_id": "a1", "target_version": "v1"},
                {"freeze_order_id": "foreign", "status": "active", "evolution_decision_id": "d-foreign", "tenant_id": "t-other"},
                # Valid other-tenant caller wrote the requester's tenant into the body: not proof.
                {"freeze_order_id": "caller-extra", "status": "active", "evolution_decision_id": "d-foreign",
                 "tenant_id": "t-own", "target_id": "a9", "target_version": "v1"},
                {"freeze_order_id": "claim-only", "status": "active", "tenant_id": "t-own"},
                {"freeze_order_id": "legacy", "status": "active"},
            ],
            "rollbacks": [],
        },
        _JOURNAL_DECISIONS,
    )
    assert result["ids"] == ["own"]  # verified own row stays visible beside unresolved legacy rows
    assert result["freeze"]["status"] == "unavailable"  # unresolved provenance is not healthy
    assert result["rollbacks"]["status"] == "ok"  # healthy empty
    gov_calls = [c for c in result["calls"] if c["url"].startswith("http://governance.test/")]
    assert len(gov_calls) == 2  # one read per dataset: rows and source agree


def test_journal_owner_reads_healthy_empty_and_timeout_stay_distinct(monkeypatch) -> None:
    empty = _mounted_owner_journal(monkeypatch, {"freeze-orders": [], "rollbacks": []}, _JOURNAL_DECISIONS)
    assert empty["ids"] == [] and empty["freeze"]["status"] == "ok" and empty["rollbacks"]["status"] == "ok"

    timeout = _mounted_owner_journal(
        monkeypatch, {"freeze-orders": TimeoutError("owner timeout"), "rollbacks": []}, _JOURNAL_DECISIONS
    )
    assert timeout["ids"] == []
    assert timeout["freeze"]["status"] == "unavailable"
    assert timeout["rollbacks"]["status"] == "ok"


def test_journal_owner_rollbacks_use_canonical_owner_rows(monkeypatch) -> None:
    result = _mounted_owner_journal(
        monkeypatch,
        {
            "freeze-orders": [],
            "rollbacks": [
                {"rollback_id": "r-own", "runtime_id": "rt1", "status": "completed", "evolution_decision_id": "d-rb"},
                {"rollback_id": "r-foreign", "runtime_id": "rt1", "status": "completed", "tenant_id": "t-other", "evolution_decision_id": "d-foreign"},
            ],
        },
        _JOURNAL_DECISIONS,
    )
    assert result["ids"] == ["r-own"]
    assert result["rollbacks"]["status"] != "ok"  # foreign/unknown 404 is conservatively unresolved, not healthy


# --------------------------------------------------------------------------- #
# BFF-EVO-ALPHA-PAGE-TRUTH-20261107: one journal row per decision
# --------------------------------------------------------------------------- #


def _hosted_all12_decision(decision_id: str, state: str, *, created_by_id: str, created_at: str) -> dict[str, Any]:
    """Decision of the real hosted shape: incident_severity evidence, hosted-all12 plan."""
    return {
        **_real_hosted_incident_severity_decision(decision_id, created_at=created_at),
        "decision_state": state,
        "status": state,
        "created_by_id": created_by_id,
        "approval_decision_id": f"appr-{decision_id}",
        "metadata": {"deployment_plan_id": "hosted-all12-h12-01a11-20261023-9aeb"},
        "required_approvals": 1,
        "review_chain": [],
        "risk_assessment": {"risk_summary": "severity-1 incident"},
    }


def test_hosted_shape_history_reports_one_row_per_decision() -> None:
    decisions = [
        _hosted_all12_decision(
            "evo-sweep-inc-drift-87d6930c62ee", "approved",
            created_by_id="evolution-daily-sweep", created_at="2026-11-05T01:00:00Z",
        ),
        _hosted_all12_decision(
            "evo-h12-01a11-20261023-9aeb-duplicate-execution-usera", "executed",
            created_by_id="operator", created_at="2026-11-05T02:00:00Z",
        ),
        _hosted_all12_decision(
            "evo-h12-01a11-20261023-9aeb-duplicate-execution", "canceled",
            created_by_id="operator", created_at="2026-11-05T03:00:00Z",
        ),
    ]
    data = {
        "evolution_decisions": {d["decision_id"]: d for d in decisions},
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    client = _JournalTestClient(_create_app_for_store(_EvolutionJournalTestStore(data)), raise_server_exceptions=True)

    for view in ("all", "history"):
        body = client.get(
            # source_type scopes away the fixture-pack postmortems/freezes/rollbacks the test store adds
            f"/bff/management/evolution-journal?view={view}&source_type=evolution_decision&page_size=50",
            headers=OPERATOR_HEADERS,
        ).json()
        assert body["page_info"]["total"] == 3
        summary = body["data"]["summary"]
        assert summary["total_items"] == 3
        assert summary["decision_count"] == 3
        assert summary["mutation_review_count"] == 3
        assert summary["by_status"] == {"approved": 1, "executed": 1, "canceled": 1}
        assert summary["by_type"] == {"evolution_decision": 3}
        rows = body["data"]["items"]
        assert [r["entry_type"] for r in rows] == ["evolution_decision"] * 3
        for row in rows:
            nested = row["mutation_review"]
            assert nested["decision_id"] == row["source_id"]
            assert nested["evidence_class"] == "incident_severity"
            assert "allowedActions" in nested
            assert "approval_decision" in nested
            assert row["decision"]["metadata"]["deployment_plan_id"].startswith("hosted-all12-")

    auto = client.get(
        "/bff/management/evolution-journal?view=auto_proposals&page_size=50", headers=OPERATOR_HEADERS
    ).json()
    assert auto["page_info"]["total"] == auto["data"]["auto_proposal_counts"]["total"]
    assert auto["data"]["auto_proposal_counts"]["incident_severity"] == 0


def test_summary_counts_pending_reviews_once_per_decision() -> None:
    decisions = {}
    for idx, state in enumerate(("pending", "reviewed", "under_review", "in_review", "approved")):
        did = f"evo-pending-{idx}"
        decisions[did] = {
            **_hosted_all12_decision(did, state, created_by_id="operator", created_at=f"2026-11-05T0{idx}:00:00Z"),
        }
    data = {
        "evolution_decisions": decisions,
        "approval_decisions": {},
        "postmortems": {},
        "freeze_orders": {},
        "all_rollbacks": {},
    }
    client = _JournalTestClient(_create_app_for_store(_EvolutionJournalTestStore(data)), raise_server_exceptions=True)
    summary = client.get(
        "/bff/management/evolution-journal?source_type=evolution_decision&page_size=50",
        headers=OPERATOR_HEADERS,
    ).json()["data"]["summary"]
    assert summary["total_items"] == 5
    assert summary["decision_count"] == 5
    assert summary["mutation_review_count"] == 5
    assert summary["pending_review_count"] == 4

    by_source_type = client.get(
        "/bff/management/evolution-journal?source_type=mutation_review&page_size=50", headers=OPERATOR_HEADERS
    ).json()
    assert by_source_type["page_info"]["total"] == 5
