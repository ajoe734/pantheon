from __future__ import annotations

import ast
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.params import Param as FastAPIParam
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.management_read_models.ranking_router import create_performance_attribution_router
from services.control_plane.bff.shared.cross_domain_utils import (
    _management_as_float,
    _management_first_float,
    _management_nested_value,
    _management_telemetry_rollup,
    _resolve_param,
)


from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt
from services.control_plane.bff.ports.read_surface_ports import PaperReconcilerUnavailableError


from pathlib import Path
import json

# --- Local port of read_store's static default-fixture-pack loader --------
# This mirrors read_store._load_default_fixture_pack_datasets (and its small
# helpers) so this contract test no longer imports from the legacy composite
# read_store module. The source data and merge semantics are identical: the
# three static fixtures_pack_*.json files under bff/data/ are loaded and
# deep-merged (dict datasets merge per-record, list datasets append records
# not already present, keyed by the first matching id-like field).
_LOCAL_FIXTURE_PACK_PATHS = (
    Path(__file__).resolve().parent.parent / "data" / "fixtures_pack_a.json",
    Path(__file__).resolve().parent.parent / "data" / "fixtures_pack_b.json",
    Path(__file__).resolve().parent.parent / "data" / "fixtures_pack_c.json",
)
_LOCAL_FIXTURE_DATASET_ALIASES = {
    "deployments": "deployment_plans",
    "runtimes": "runtime_bindings",
}
_LOCAL_FIXTURE_RECORD_KEYS = [
    "id",
    "analysis_id",
    "entry_id",
    "decision_id",
    "intervention_id",
    "job_id",
    "plan_id",
    "program_id",
    "pool_id",
    "persona_id",
    "server_id",
    "signal_id",
    "skill_id",
    "session_id",
    "sessionId",
    "packet_id",
    "strategy_id",
    "experiment_id",
    "artifact_id",
    "rebalance_id",
    "binding_id",
    "runtime_id",
    "tool_id",
    "channel_id",
]


def _local_record_key(record, candidates):
    for key in candidates:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _local_fixture_list_record_key(record):
    if isinstance(record, dict):
        key = _local_record_key(record, _LOCAL_FIXTURE_RECORD_KEYS)
        if key:
            return key
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _local_load_fixture_pack_datasets(path: Path):
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


def _local_merge_default_fixture_pack(target, fixture):
    changed = False
    for raw_key, incoming in fixture.items():
        key = _LOCAL_FIXTURE_DATASET_ALIASES.get(raw_key, raw_key)
        if isinstance(incoming, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                target[key] = json.loads(json.dumps(incoming))
                changed = True
                continue
            for record_key, record in incoming.items():
                if record_key not in existing:
                    existing[record_key] = json.loads(json.dumps(record))
                    changed = True
            continue
        if isinstance(incoming, list):
            existing = target.get(key)
            if not isinstance(existing, list):
                target[key] = json.loads(json.dumps(incoming))
                changed = True
                continue
            seen = {_local_fixture_list_record_key(record) for record in existing}
            for record in incoming:
                record_key = _local_fixture_list_record_key(record)
                if record_key in seen:
                    continue
                existing.append(json.loads(json.dumps(record)))
                seen.add(record_key)
                changed = True
    return changed


def _load_default_fixture_pack_datasets():
    merged = {}
    for path in _LOCAL_FIXTURE_PACK_PATHS:
        _local_merge_default_fixture_pack(merged, _local_load_fixture_pack_datasets(path))
    return merged

HEADERS = {"Authorization": "Bearer op-pm12:operator,reviewer"}


@pytest.fixture(autouse=True)
def _canonical_persona_owner_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeOwnerTransport()
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner")
    store = MemoryPersonaProvisioningStore()
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", store)
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: store)
    monkeypatch.setattr(personas_service, "_PersonaOwnerHttpTransport", lambda *a, **kw: transport)
    monkeypatch.setattr(
        personas_service,
        "_register_persona_cron_required",
        lambda persona_id, pool_id, binding_id, **kw: _schedule_receipt(persona_id, pool_id, binding_id),
    )
    try:
        from services.persona.runtime_profile import build_persona_runtime_profile
        monkeypatch.setattr(personas_service, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    except ImportError:
        monkeypatch.setattr(personas_service, "build_persona_runtime_profile", lambda *a, **kw: type("Profile", (), {"to_dict": lambda s: {}})(), raising=False)


class _Pm12LeagueTestStore:
    def __init__(self, fallback: bool = True) -> None:
        self.fallback = fallback
        if fallback:
            raw_data = dict(_load_default_fixture_pack_datasets())
            data_file = Path(__file__).resolve().parent.parent / "data" / "read_surfaces.json"
            if data_file.exists():
                try:
                    loaded = json.loads(data_file.read_text(encoding="utf-8"))
                    for k, v in loaded.items():
                        if k not in raw_data or not raw_data[k]:
                            raw_data[k] = v
                        elif isinstance(raw_data[k], dict) and isinstance(v, dict):
                            raw_data[k] = {**raw_data[k], **v}
                        elif isinstance(raw_data[k], list) and isinstance(v, list):
                            raw_data[k] = [*raw_data[k], *v]
                except Exception:
                    pass
        else:
            raw_data = {}
        self.raw_data = raw_data
        self.personas = {}
        self.ranking_snapshots = {}

    def count_strategy_persona_bindings(self) -> Dict[str, int]:
        return {}

    def dataset_source(self, dataset: str) -> str:
        if dataset == "evidence_refs" and self.fallback:
            return "local_snapshot"
        if self.fallback and dataset in self.raw_data and self.raw_data[dataset]:
            return "local_snapshot"
        return "missing"

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        res = list(self.personas.values())
        if self.fallback:
            personas = self.raw_data.get("personas", {})
            raw_list = list(personas.values()) if isinstance(personas, dict) else personas
            existing_ids = {p.get("persona_id") or p.get("id") for p in res}
            for p in raw_list:
                if isinstance(p, dict):
                    pid = p.get("persona_id") or p.get("id")
                    if pid not in existing_ids:
                        rec = dict(p)
                        rec.setdefault("tenant_id", "pantheon-dev")
                        res.append(rec)
        return res

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        if persona_id in self.personas:
            return self.personas[persona_id]
        for p in self.list_personas():
            if p.get("persona_id") == persona_id or p.get("id") == persona_id:
                return p
        return None

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        pid = kwargs.get("persona_id") or kwargs.get("id")
        meta = dict(kwargs.get("metadata") or {})
        if "archetype" in kwargs and "archetype" not in meta:
            meta["archetype"] = kwargs["archetype"]
        kwargs.setdefault("tenant_id", "pantheon-dev")
        rec = {"id": pid, "persona_id": pid, **kwargs, "metadata": meta}
        self.personas[pid] = rec
        return rec

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            entries = self.raw_data.get("persona_league", {})
            return [dict(e) for e in (entries.values() if isinstance(entries, dict) else entries) if isinstance(e, dict)]
        return []

    def get_persona_league_entry(self, persona_id: str) -> dict[str, Any] | None:
        for e in self.list_persona_league():
            if e.get("persona_id") == persona_id or e.get("id") == persona_id:
                return e
        return None

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            bindings = self.raw_data.get("bindings", {})
            return [dict(b) for b in (bindings.values() if isinstance(bindings, dict) else bindings) if isinstance(b, dict)]
        return []

    def get_bindings_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        return [b for b in self.list_bindings() if b.get("persona_id") == persona_id]

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            bindings = self.raw_data.get("runtime_bindings", {})
            raw_list = [dict(b) for b in (bindings.values() if isinstance(bindings, dict) else bindings) if isinstance(b, dict)]
            for b in raw_list:
                if (b.get("id") == "runtime-042" or b.get("runtime_id") == "runtime-042") and not b.get("persona_id"):
                    b["persona_id"] = "persona-alpha"
                    b["binding_id"] = "binding-042"
            return raw_list
        return []

    def get_runtime_binding(self, runtime_id: str) -> dict[str, Any] | None:
        for b in self.list_runtime_bindings():
            if b.get("runtime_id") == runtime_id or b.get("id") == runtime_id:
                return b
        return None

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            pools = self.raw_data.get("capital_pools", {})
            return [dict(p) for p in (pools.values() if isinstance(pools, dict) else pools) if isinstance(p, dict)]
        return []

    def get_capital_pool(self, pool_id: str) -> dict[str, Any] | None:
        for p in self.list_capital_pools():
            if p.get("pool_id") == pool_id or p.get("id") == pool_id:
                return p
        return None

    def list_evidence_refs(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            refs = self.raw_data.get("evidence_refs", {})
            raw_list = [dict(r) for r in (refs.values() if isinstance(refs, dict) else refs) if isinstance(r, dict)]
            if not raw_list:
                raw_list = [
                    {
                        "id": "ev-q1-001",
                        "ref_id": "ev-q1-001",
                        "persona_id": "persona-alpha",
                        "created_at": "2026-02-15T00:00:00Z",
                        "source_document": {
                            "captured_at": "2026-02-15T00:00:00Z",
                        },
                        "source": "paper_monitoring",
                    }
                ]
            else:
                for r in raw_list:
                    r["created_at"] = "2026-02-15T00:00:00Z"
                    doc = dict(r.get("source_document") or {})
                    doc["captured_at"] = "2026-02-15T00:00:00Z"
                    r["source_document"] = doc
            return raw_list
        return []

    def get_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        if self.fallback:
            sessions = self.raw_data.get("sessions") or self.raw_data.get("persona_sessions") or {}
            raw_list = [dict(s) for s in (sessions.values() if isinstance(sessions, dict) else sessions) if isinstance(s, dict)]
            if persona_id is None:
                return raw_list
            matching = [s for s in raw_list if s.get("persona_id") == persona_id or persona_id in s.get("personas", [])]
            return matching
        return []

    def list_sessions_for_persona(self, persona_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_sessions_for_persona(persona_id)

    def get_teaching_sessions_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        if self.fallback:
            sessions = self.raw_data.get("teaching_sessions", {})
            raw_list = [dict(s) for s in (sessions.values() if isinstance(sessions, dict) else sessions) if isinstance(s, dict)]
            return [s for s in raw_list if s.get("persona_id") == persona_id]
        return []

    def list_teaching_sessions_for_persona(self, persona_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_teaching_sessions_for_persona(persona_id)

    def get_capability_snapshot_for_persona(self, persona_id: str) -> dict[str, Any]:
        return {
            "snapshot_id": f"cap-{persona_id}",
            "persona_id": persona_id,
            "effective_skills": ["trend_follower", "risk_manager"],
            "effective_tools": ["order_router"],
            "effective_workflows": [],
            "restrictions": [],
            "source_refs": [],
        }

    def put_ranking_snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        sid = record.get("ranking_snapshot_id") or record.get("id")
        self.ranking_snapshots[sid] = record
        return record

    def get_ranking_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self.ranking_snapshots.get(snapshot_id)

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        if getattr(self, "paper_reconciler_unavailable", False):
            raise PaperReconcilerUnavailableError("paper fleet reconciler connection refused")
        return []

    def write_ranking_snapshot(self, *args: Any, **kwargs: Any) -> None:
        return None

    def update_persona(self, **kwargs: Any) -> dict[str, Any]:
        pid = kwargs.get("persona_id") or kwargs.get("id")
        if pid in self.personas:
            self.personas[pid].update(kwargs)
            return self.personas[pid]
        return kwargs

    def get_route_policy_for_persona(self, persona_id: str) -> dict[str, Any] | None:
        return {
            "version": "1.0",
            "rules": [{"mode": "consult_required"}],
            "consult_policy": {"version": "1.0", "trigger_rules": []},
        }

    def get_persona_allowed_actions(self, persona_id: str) -> dict[str, bool]:
        return {"paper_deploy": True, "kill_switch": True}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("list_"):
            return lambda *a, **kw: []
        if name.startswith("get_"):
            return lambda *a, **kw: None
        raise AttributeError(f"'_Pm12LeagueTestStore' has no attribute '{name}'")


class _CountingPm12LeagueTestStore(_Pm12LeagueTestStore):
    def __init__(self, fallback: bool = True) -> None:
        super().__init__(fallback=fallback)
        self.calls: Dict[str, int] = {
            "get_route_policy_for_persona": 0,
            "get_persona_allowed_actions": 0,
            "list_runtime_bindings": 0,
            "get_persona": 0,
            "list_personas": 0,
            "get_sessions_for_persona": 0,
            "list_authoritative_paper_runtime_monitoring_sessions": 0,
        }

    def reset_counts(self) -> None:
        for k in self.calls:
            self.calls[k] = 0

    def get_route_policy_for_persona(self, persona_id: str) -> dict[str, Any] | None:
        self.calls["get_route_policy_for_persona"] += 1
        return super().get_route_policy_for_persona(persona_id)

    def get_persona_allowed_actions(self, persona_id: str) -> dict[str, bool]:
        self.calls["get_persona_allowed_actions"] += 1
        return super().get_persona_allowed_actions(persona_id)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls["list_runtime_bindings"] += 1
        return super().list_runtime_bindings(**kwargs)

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        self.calls["get_persona"] += 1
        return super().get_persona(persona_id)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls["list_personas"] += 1
        return super().list_personas(**kwargs)

    def get_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        self.calls["get_sessions_for_persona"] += 1
        return super().get_sessions_for_persona(persona_id)

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        self.calls["list_authoritative_paper_runtime_monitoring_sessions"] += 1
        return super().list_authoritative_paper_runtime_monitoring_sessions()



_PM12_FUNCS = None

def _compile_pm12_namespace(store):
    from functools import partial
    import inspect

    def _page_slice(items, page_token, page_size):
        start = int(page_token) if page_token else 0
        end = start + page_size
        next_page_token = str(end) if end < len(items) else None
        return items[start:end], next_page_token

    def _aggregate_group_surface(surface_key, source_surfaces, *, snapshot_at, unavailable_message, degraded_message):
        return {"status": "ok", "snapshot_at": snapshot_at, "source": "bff_composed", "available": True}

    ns = dict(__import__("typing").__dict__)
    ns.update({
        "__package__": "services.control_plane.bff.pm12",
        "datetime": datetime,
        "date": datetime.date,
        "timezone": timezone,
        "timedelta": timedelta,
        "read_store": store,
        "_list_persona_records": personas_service._list_persona_records,
        "utc_now": getattr(personas_service, "utc_now", lambda: datetime.now(timezone.utc).isoformat()),
        "_dataset_surface_status": getattr(personas_service, "_dataset_surface_status", lambda *a, **kw: {"status": "ok"}),
        "_PM12_ATTRIBUTION_DIMENSIONS": ("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
        "ops_read_model_sanitize_metric": lambda v: v,
        "FastAPIParam": FastAPIParam,
        "_page_slice": _page_slice,
        "_aggregate_group_surface": _aggregate_group_surface,
        "_snapshot_meta": lambda snapshot_at: {"snapshot_at": snapshot_at},
        "_management_as_float": _management_as_float,
        "_management_nested_value": _management_nested_value,
        "_management_first_float": _management_first_float,
        "_management_telemetry_rollup": _management_telemetry_rollup,
        "_resolve_param": _resolve_param,
    })
    pm12_tree = ast.parse((Path(__file__).resolve().parent.parent / "pm12" / "service.py").read_text(encoding="utf-8"))
    funcs = [
        n for n in pm12_tree.body
        if isinstance(n, ast.FunctionDef) and (n.name.startswith("_pm12_") or n.name.startswith("_management_"))
    ]
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "pm12_service.py", "exec"), ns)
    for attr, val in list(ns.items()):
        if callable(val) and attr.startswith(("_pm12_", "_management_")):
            if attr == "_pm12_performance_attribution_response":
                continue
            try:
                sig = inspect.signature(val)
                if "read_store" in sig.parameters:
                    ns[attr] = partial(val, read_store=store)
            except Exception:
                pass

    raw_resp_fn = ns["_pm12_performance_attribution_response"]

    def _resp_wrapper(*args, **kwargs):
        if "rows_fn" not in kwargs and "_pm12_performance_attribution_rows" in ns:
            kwargs["rows_fn"] = ns["_pm12_performance_attribution_rows"]
        if "sources_fn" not in kwargs and "_pm12_performance_attribution_sources" in ns:
            kwargs["sources_fn"] = ns["_pm12_performance_attribution_sources"]
        return raw_resp_fn(*args, read_store=store, **kwargs)

    ns["_pm12_performance_attribution_response"] = _resp_wrapper
    return ns


_ACTIVE_PM12_NS = {}


@pytest.fixture(autouse=True)
def _reset_current_persona_service():
    prev = personas_service._current_persona_service.get()
    yield
    personas_service._current_persona_service.set(prev)


def _fresh_client(td: str, *, fallback: bool = True, store: Optional[_Pm12LeagueTestStore] = None) -> TestClient:
    global _ACTIVE_PM12_NS
    if store is None:
        store = _Pm12LeagueTestStore(fallback=fallback)
    cmd_store = CommandStore(os.path.join(td, "commands.jsonl"))
    service = PersonaService(
        read_store=store,
        write_owner=store,
        ranking_write_owner=store,
        command_store=cmd_store,
    )
    personas_service._current_persona_service.set(service)

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
            return JSONResponse(status_code=exc.status_code, content=detail)

        field = None
        message = "Validation failed" if exc.status_code == 422 else "Error"
        code = "VALIDATION_FAILED" if exc.status_code == 422 else "ERROR"
        if isinstance(detail, dict):
            field = detail.get("field")
            message = detail.get("message") or str(detail.get("error") or "")
            if exc.status_code == 422:
                code = "VALIDATION_FAILED"
            elif detail.get("error"):
                code = str(detail["error"])
        elif isinstance(detail, str):
            message = detail

        content = {
            "error": {
                "code": code,
                "message": message,
            }
        }
        if field is not None:
            content["field"] = field
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_FAILED",
                    "message": "Request validation failed",
                    "details": {"errors": exc.errors()},
                }
            }
        )

    app.include_router(create_personas_router(service=service))
    pm12_ns = _compile_pm12_namespace(store)
    _ACTIVE_PM12_NS = pm12_ns
    app.include_router(create_performance_attribution_router(
        bff_me_tenant_payload=lambda ident, requested_tenant=None: {"id": "pantheon-dev"},
        pm12_performance_attribution_response=lambda *a, **kw: _ACTIVE_PM12_NS["_pm12_performance_attribution_response"](*a, **kw),
    ))
    return TestClient(app, raise_server_exceptions=True)



def _create_persona(client: TestClient, name: str, *, archetype: str, key: str) -> str:
    response = client.post(
        "/bff/personas",
        headers={**HEADERS, "Idempotency-Key": key},
        json={"name": name, "archetype": archetype},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def test_pm12_persona_league_returns_composed_table() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get("/bff/management/persona-league", headers=HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            assert set(body["data"]) >= {"items", "summary"}
            assert body["page_info"]["total"] >= 1
            assert "GET /bff/personas/{id}/capabilities" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["persona_league"]["status"] == "ok"
            assert "persona_sessions" in body["meta"]["surfaces"]

            rows = {row["id"]: row for row in body["data"]["items"]}
            row = rows["persona-alpha"]
            assert row["persona_id"] == "persona-alpha"
            assert row["route_policy_summary"]["rule_count"] >= 0
            assert row["capability_summary"]["skill_count"] >= 0
            assert row["binding_summary"]["total"] >= 1
            assert row["session_summary"]["total"] >= 1
            assert row["evaluation_summary"]["total"] >= 1
            assert row["memory_summary"]["total"] >= 0
            assert row["health_summary"]["health"] in {"healthy", "degraded"}
            assert row["links"]["detail"] == "/bff/personas/persona-alpha"
            assert row["links"]["route_policy"] == "/bff/personas/persona-alpha/route-policy"
            assert "allowed_action_summary" in row
            assert "personaId" not in row
            assert "routePolicy" not in row
            assert "capabilities" not in row
            assert "allowedActions" not in row
        finally:
            pass


def test_pm12_persona_league_filters_searches_and_paginates() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td, fallback=False)
            macro_id = _create_persona(client, "Macro PM12", archetype="macro", key="pm12-macro")
            _create_persona(client, "Risk PM12", archetype="risk", key="pm12-risk")

            response = client.get(
                "/bff/management/persona-league",
                headers=HEADERS,
                params={"state": "provisioning", "archetype": "macro", "q": "macro", "page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page_info"]["total"] == 1
            assert body["page_info"]["next_page_token"] is None
            assert body["data"]["items"][0]["id"] == macro_id
            assert body["data"]["items"][0]["archetype"] == "macro"
        finally:
            pass


def test_pm12_persona_league_rankings_returns_computed_blocks() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/persona-league/rankings",
                headers=HEADERS,
                params={"criteria": "overall,pnl", "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert [block["criteria"] for block in items] == ["overall", "pnl"]
            assert items[0]["items"][0]["rank"] == (1 if items[0]["items"][0]["score"] is not None else None)
            assert items[0]["items"][0]["persona_id"]
            assert "overall_score" in items[0]["items"][0]
            assert summary["persona_count"] >= 1
            assert "rankingId" not in items[0]
            assert "formulaVersion" not in items[0]
            assert "rankedCount" not in items[0]
            assert "personaId" not in items[0]["items"][0]
            assert "overallScore" not in items[0]["items"][0]
            assert "scoreField" not in items[0]["items"][0]
            assert "personaCount" not in summary
            assert "topPersonaId" not in summary
            assert body["meta"]["surfaces"]["persona_league_rankings"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
        finally:
            pass


def test_pm12_persona_league_movers_returns_current_snapshot_movers() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/persona-league/movers",
                headers=HEADERS,
                params={"direction": "new", "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert "movers" not in body
            assert "movers" not in body["data"]
            assert summary["persona_count"] >= 1
            assert summary["mover_count"] >= 1
            assert summary["returned_count"] == 1
            assert summary["direction"] == "new"
            assert summary["baseline_status"] == "unavailable"
            assert summary["new_count"] == summary["persona_count"]
            assert items[0]["current_rank"] == (1 if items[0]["current_score"] is not None else None)
            assert items[0]["previous_rank"] is None
            assert items[0]["rank_delta"] is None
            assert items[0]["score_delta"] is None
            assert items[0]["direction"] == "new"
            assert items[0]["baseline_status"] == "unavailable"
            assert items[0]["movement"]["basis"] == "current_persona_league_snapshot_no_historical_baseline"
            assert body["page_info"]["total"] == summary["mover_count"]
            assert "personaCount" not in summary
            assert "moverCount" not in summary
            assert "returnedCount" not in summary
            assert "baselineStatus" not in summary
            assert "currentRank" not in items[0]
            assert "previousRank" not in items[0]
            assert "rankDelta" not in items[0]
            assert "scoreDelta" not in items[0]
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_movers"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league/rankings" in body["meta"]["composition_sources"]
        finally:
            pass


def test_pm12_persona_league_movers_rejects_invalid_direction() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/persona-league/movers",
                headers=HEADERS,
                params={"direction": "sideways"},
            )

            assert response.status_code == 422, response.text
            body = response.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["field"] == "direction"
        finally:
            pass


def test_pm12_persona_league_tiers_returns_config_and_current_assignments() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get("/bff/management/persona-league/tiers", headers=HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assignments = body["data"]["related"]["assignments"]
            assert len(items) == 4
            assert items[0]["tier_id"] == "tier-1"
            assert summary["formula_version"] == "pm12-return-on-capital-v2"
            assert summary["persona_count"] == len(assignments)
            assert "tierId" not in items[0]
            assert "formulaVersion" not in summary
            assert "personaCount" not in summary
            assert "minScore" not in items[0]
            assert "governancePosture" not in items[0]
            assert "personaIds" not in items[0]
            assert "personaId" not in assignments[0]
            assert "overallScore" not in assignments[0]
            assert "byTier" not in summary
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_tiers"]["status"] in {"ok", "degraded"}
        finally:
            pass


def test_pm12_persona_league_heatmap_uses_snake_case_rows() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/persona-league/heatmap",
                headers=HEADERS,
                params={"bucket": "day", "bucket_count": 2, "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            summary = data["summary"]
            row = data["items"][0]
            bucket = data["buckets"][0]
            cell = row["cells"][0]

            assert data["heatmap_id"] == "persona-league-heatmap"
            assert data["formula_version"] == "pm12-return-on-capital-v2"
            assert summary["returned_persona_count"] == 1
            assert summary["bucket_count"] == 2
            assert row["persona_id"]
            assert row["runtime_ids"]
            assert bucket["bucket_id"]
            assert bucket["start_at"]
            assert bucket["end_exclusive_at"]
            assert cell["persona_id"] == row["persona_id"]
            assert cell["bucket_id"] == bucket["bucket_id"]
            assert cell["composite_score"] is None
            assert cell["components"]["pnl_score"] is None
            assert cell["formula_version"] == "pm12-return-on-capital-v2"
            assert cell["observed_telemetry_count"] >= 0

            assert "heatmapId" not in data
            assert "formulaVersion" not in data
            assert "returnedPersonaCount" not in summary
            assert "bucketId" not in bucket
            assert "startAt" not in bucket
            assert "endExclusiveAt" not in bucket
            assert "personaId" not in row
            assert "tierId" not in row
            assert "latestScore" not in row
            assert "runtimeIds" not in row
            assert "personaId" not in cell
            assert "bucketId" not in cell
            assert "overallScore" not in cell
            assert "formulaVersion" not in cell
            assert "observedTelemetryCount" not in cell
        finally:
            pass


def test_pm12_quarterly_ranking_returns_formula_window_and_evidence() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            items = data["items"]
            summary = data["summary"]
            assert "rankings" not in body
            assert "rankings" not in data
            assert summary["quarter"] == "2026-Q1"
            assert summary["formula_version"] == "pm12-return-on-capital-v2"
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert data["quarter_window"]["end_exclusive_at"] == "2026-04-01T00:00:00Z"
            assert data["formula"]["weights"]["pnl"] == 0.35
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] >= 1
            assert items[0]["rank"] == (1 if items[0]["score"] is not None else None)
            assert items[0]["quarter"] == "2026-Q1"
            assert items[0]["score_field"] == "overall_score"
            assert data["evidence_refs"]
            assert summary["evidence_ref_count"] == len(data["evidence_refs"])
            assert "quarterWindow" not in data
            assert "formulaVersion" not in summary
            assert "evidenceRefs" not in data
            assert "evidenceRefCount" not in summary
            assert "scoreField" not in items[0]
            assert "quarterWindow" not in items[0]
            assert "formulaVersion" not in items[0]
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["quarterly_ranking"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
            assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]
        finally:
            pass


def test_pm12_quarterly_ranking_formula_returns_weights_and_governance_trace() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/quarterly-ranking/formula",
                headers=HEADERS,
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"] == body["formula"]
            assert body["formula"]["formula_version"] == "pm12-return-on-capital-v2"
            assert body["formula"]["score_field"] == "overall_score"
            assert body["formula"]["weights"] == {
                "pnl": 0.35,
                "risk": 0.25,
                "execution": 0.25,
                "activity": 0.15,
            }
            assert body["summary"]["weight_total"] == 1.0
            assert body["summary"]["evidence_ref_count"] == len(body["evidence_refs"])
            assert body["version_history"][0]["formula_version"] == "pm12-return-on-capital-v2"
            assert body["version_history"][0]["governance_evidence_refs"]
            assert body["formula"]["change_control"]["requires_governance_evidence"] is True
            assert "formulaVersion" not in body["formula"]
            assert "weightTotal" not in body["summary"]
            assert "evidenceRefs" not in body
            assert "versionHistory" not in body
            assert "changeControl" not in body["formula"]
            assert body["meta"]["version_policy"] == "formula_version_changes_require_governance_evidence"
            assert body["meta"]["surfaces"]["quarterly_ranking_formula"]["status"] == "ok"
        finally:
            pass


def test_pm12_quarterly_ranking_drilldown_uses_snake_case_lightweight_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "persona_id": "persona-alpha"},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            data = body["data"]
            summary = body["summary"]
            contribution = body["contributions"][0]
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert data["persona_id"] == "persona-alpha"
            assert data["summary"]["persona_id"] == "persona-alpha"
            assert summary["component_count"] == len(data["contributions"])
            assert contribution["score_field"]
            assert contribution["weighted_contribution"] is None
            assert contribution["score"] is None
            assert contribution["contribution_share"] is None
            assert data["source_breakdown"]["route_policy_summary"]["rule_count"] >= 0
            assert data["source_breakdown"]["session_count"] >= 0
            assert data["links"]["parent_ranking"].endswith("quarter=2026-Q1")

            assert "rankingItem" not in body
            assert "contributionBreakdown" not in body
            assert "sourceBreakdown" not in body
            assert "quarterWindow" not in body
            assert "evidenceRefs" not in body
            assert "correlationId" not in body["meta"]
            assert "quarterWindow" not in data
            assert "personaId" not in data
            assert "rankingItem" not in data
            assert "contributionBreakdown" not in data
            assert "sourceBreakdown" not in data
            assert "evidenceRefs" not in data
            assert "parentRanking" not in data["links"]
            assert "scoreField" not in contribution
            assert "weightedContribution" not in contribution
            assert "contributionShare" not in contribution
            assert "rankedCount" not in summary
            assert "formulaVersion" not in summary
            assert "evidenceRefCount" not in summary
            assert "capabilities" not in data["source_breakdown"]
            assert "sessions" not in data["source_breakdown"]
            assert "memory" not in data["source_breakdown"]
            assert "allowedActions" not in data["source_breakdown"]
        finally:
            pass


def test_pm12_quarterly_ranking_recommendations_are_governance_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "page_size": 3},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            items = data["items"]
            summary = data["summary"]
            assert "recommendations" not in body
            assert "recommendations" not in data
            assert summary["quarter"] == "2026-Q1"
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert body["page_info"]["page_size"] == 3
            assert body["page_info"]["total"] >= len(items) >= 1
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["live_capital_mutation"] is False
            assert summary["live_capital_mutation_count"] == 0
            assert summary["human_gate_decision_count"] == body["page_info"]["total"]
            assert "human_gate_decision" in body["meta"]["governance_destinations"]
            assert "GET /bff/management/human-inbox" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["quarterly_ranking_recommendations"]["status"] in {"ok", "degraded"}

            allowed = set(summary["allowed_actions"])
            assert allowed == {
                "promote_to_canary_candidate",
                "increase_research_budget",
                "grant_tool_access",
                "reduce_capital_access",
                "require_retraining",
                "freeze_persona",
                "suspend_persona",
                "retire_persona",
            }
            assert "quarterWindow" not in data
            assert "allowedActions" not in summary
            assert "humanGateDecisionCount" not in summary
            assert "liveCapitalMutationCount" not in summary
            for recommendation in items:
                assert recommendation["score"] is None
                assert recommendation["action_id"] in allowed
                assert recommendation["recommendation_type"] == "governance_advisory"
                assert recommendation["requires_human_gate_decision"] is True
                assert recommendation["live_capital_mutation"] is False
                assert recommendation["governance"]["live_capital_mutation"] is False
                assert "human_inbox" in recommendation["governance"]["destinations"]
                assert "actionId" not in recommendation
                assert "recommendationType" not in recommendation
                assert "requiresHumanGateDecision" not in recommendation
                assert "liveCapitalMutation" not in recommendation
                assert "liveCapitalMutation" not in recommendation["governance"]
        finally:
            pass


def test_pm12_quarterly_ranking_rejects_invalid_quarter() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert response.status_code == 422, response.text
            body = response.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["field"] == "quarter"

            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert recommendations.status_code == 422, recommendations.text
            recommendations_body = recommendations.json()
            assert "detail" not in recommendations_body
            assert recommendations_body["error"]["code"] == "VALIDATION_FAILED"
            assert recommendations_body["field"] == "quarter"
        finally:
            pass


def test_pm12_performance_attribution_pages_before_projection_and_uses_snake_case() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        client.raise_server_exceptions = True
        original_projector = _ACTIVE_PM12_NS["_pm12_performance_attribution_rows"]
        projected_entry_counts: list[int] = []
        try:
            def recording_projector(entries, *args, **kwargs):
                projected_entry_counts.append(len(entries))
                return original_projector(entries, *args, **kwargs)

            _ACTIVE_PM12_NS["_pm12_performance_attribution_rows"] = recording_projector
            response = client.get(
                "/bff/management/performance-attribution",
                headers=HEADERS,
                params={
                    "dimension": "persona,strategy,pool,asset,broker,runtime,regime",
                    "page_size": 1,
                },
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            summary = data["summary"]
            items = data["items"]
            assert "items" not in body
            assert "rows" not in body
            assert "summary" not in body
            assert "rows" not in data
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] == summary["row_count"]
            assert summary["returned_row_count"] == len(items) == 1
            assert projected_entry_counts == [1]
            assert summary["supported_dimensions"]
            assert summary["runtime_count"] >= 1
            assert "supportedDimensions" not in summary
            assert "rowCount" not in summary
            assert "returnedRowCount" not in summary
            assert "runtimeCount" not in summary
            assert "totalPnl" not in summary

            item = items[0]
            assert item["dimension_key"]
            assert item["runtime_count"] >= 1
            assert item["holding_count"] >= 1
            assert "dimensionKey" not in item
            assert "totalPnl" not in item
            assert "runtimeCount" not in item
            assert "holdingCount" not in item
            assert "sourceRefs" not in item
            assert "capitalPool" not in item["links"]
            assert "runtime_ids" in item["source_refs"]
            assert "runtimeIds" not in item["source_refs"]

            metrics = item["metrics"]
            assert "total_pnl" in metrics
            assert "runtime_count" in metrics
            assert "pnl_contribution_pct" in metrics
            assert "totalPnl" not in metrics
            assert "runtimeCount" not in metrics
            assert "pnlContributionPct" not in metrics
        finally:
            _ACTIVE_PM12_NS["_pm12_performance_attribution_rows"] = original_projector


def test_pm12_persona_league_requires_auth() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            client.raise_server_exceptions = True
            response = client.get("/bff/management/persona-league")

            assert response.status_code == 401, response.text

            rankings = client.get("/bff/management/persona-league/rankings")
            assert rankings.status_code == 401, rankings.text

            movers = client.get("/bff/management/persona-league/movers")
            assert movers.status_code == 401, movers.text

            tiers = client.get("/bff/management/persona-league/tiers")
            assert tiers.status_code == 401, tiers.text

            heatmap = client.get("/bff/management/persona-league/heatmap")
            assert heatmap.status_code == 401, heatmap.text

            quarterly = client.get("/bff/management/quarterly-ranking")
            assert quarterly.status_code == 401, quarterly.text

            recommendations = client.get("/bff/management/quarterly-ranking/recommendations")
            assert recommendations.status_code == 401, recommendations.text

            formula = client.get("/bff/management/quarterly-ranking/formula")
            assert formula.status_code == 401, formula.text
        finally:
            pass


def test_pm12_quarterly_ranking_read_fanout_reduction_and_parity() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = _CountingPm12LeagueTestStore(fallback=True)
        client = _fresh_client(td, store=store)
        client.raise_server_exceptions = True

        all_personas = store.list_personas()
        persona_count = len(all_personas)
        assert persona_count >= 2, "Expected at least 2 personas in test fixtures"

        # 1. Quarterly Ranking Fanout Reduction:
        # Verify that quarterly ranking avoids fanout reads for unused UI summaries
        # (route policy, allowed actions, health) while reusing pre-fetched runtime bindings.
        store.reset_counts()
        response = client.get(
            "/bff/management/quarterly-ranking",
            headers=HEADERS,
            params={"quarter": "2026-Q1"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        items = body["data"]["items"]
        assert len(items) == persona_count

        # Read counters: Route policy and allowed actions must be completely eliminated (0 calls).
        # Runtime bindings must be fetched in bulk exactly once (1 call instead of 1 + N).
        assert store.calls["get_route_policy_for_persona"] == 0, (
            f"Expected 0 route policy calls on quarterly ranking path, got {store.calls['get_route_policy_for_persona']}"
        )
        assert store.calls["get_persona_allowed_actions"] == 0, (
            f"Expected 0 allowed actions calls on quarterly ranking path, got {store.calls['get_persona_allowed_actions']}"
        )
        assert store.calls["list_runtime_bindings"] == 1, (
            f"Expected 1 runtime bindings prefetch call, got {store.calls['list_runtime_bindings']}"
        )
        assert store.calls["get_sessions_for_persona"] == 1, (
            f"Expected 1 bulk session prefetch call, got {store.calls['get_sessions_for_persona']}"
        )

        # Integrity of ranking output: real scores, rank ordering, exclusions preserved
        prev_score = float("inf")
        for idx, item in enumerate(items, start=1):
            assert item["rank"] == (idx if item["overall_score"] is not None else None)
            assert item["quarter"] == "2026-Q1"
            if item["overall_score"] is None:
                assert item["tier"] == "unknown"
                assert item["eligible"] is False
                assert "unknown_pnl_return" in item["exclusion_codes"]
            else:
                assert item["overall_score"] <= prev_score
                prev_score = item["overall_score"]
            assert "eligible" in item
            assert "exclusion_codes" in item
            assert "exclusion_reasons" in item

            # Unused rich UI summaries must NOT be present or fabricated on ranking items
            assert "route_policy_summary" not in item
            assert "health_summary" not in item
            assert "allowed_action_summary" not in item

        # 1b. Regression: Successful empty [] bulk sessions must remain authoritative request-local prefetch
        # without falling back to N per-Persona rereads.
        orig_sessions = dict(store.raw_data.get("sessions") or {})
        orig_persona_sessions = dict(store.raw_data.get("persona_sessions") or {})
        try:
            store.raw_data["sessions"] = {}
            store.raw_data["persona_sessions"] = {}
            store.reset_counts()
            empty_bulk_resp = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q1"},
            )
            assert empty_bulk_resp.status_code == 200, empty_bulk_resp.text
            assert store.calls["get_sessions_for_persona"] == 1, (
                f"Expected exactly 1 bulk session read and 0 per-persona rereads for empty bulk result, got {store.calls['get_sessions_for_persona']}"
            )
            assert store.calls["get_route_policy_for_persona"] == 0
            assert store.calls["get_persona_allowed_actions"] == 0
            assert store.calls["list_runtime_bindings"] == 1
            assert len(empty_bulk_resp.json()["data"]["items"]) == persona_count
        finally:
            store.raw_data["sessions"] = orig_sessions
            store.raw_data["persona_sessions"] = orig_persona_sessions

        # 1c. Regression: Authoritative paper runtime monitoring owner error must NOT be swallowed into []
        # and must propagate to produce HTTP 503 instead of falling back to healthy empty result.
        store.paper_reconciler_unavailable = True
        try:
            store.reset_counts()
            reconciler_down_resp = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q1"},
            )
            assert reconciler_down_resp.status_code == 503, reconciler_down_resp.text
            assert "Paper fleet reconciler unavailable" in reconciler_down_resp.text
        finally:
            store.paper_reconciler_unavailable = False

        # 2. Rich UI Route Preserves Full Summaries:
        # Default behavior for other callers (e.g. /bff/management/persona-league) must retain rich summaries.
        store.reset_counts()
        league_resp = client.get("/bff/management/persona-league", headers=HEADERS)
        assert league_resp.status_code == 200, league_resp.text
        league_items = league_resp.json()["data"]["items"]
        assert len(league_items) == persona_count

        for item in league_items:
            assert "route_policy_summary" in item
            assert "health_summary" in item
            assert "allowed_action_summary" in item

        assert store.calls["get_route_policy_for_persona"] >= persona_count
        assert store.calls["get_persona_allowed_actions"] >= persona_count
        assert store.calls["list_runtime_bindings"] == 1

        # 3. Drilldown Route Enriches On-Demand:
        # When drilldown is called for a single persona, route policy and health summary are loaded
        # on-demand for that single persona only.
        store.reset_counts()
        drilldown_resp = client.get(
            "/bff/management/quarterly-ranking/drilldown",
            headers=HEADERS,
            params={"quarter": "2026-Q1", "persona_id": "persona-alpha"},
        )
        assert drilldown_resp.status_code == 200, drilldown_resp.text
        drilldown_body = drilldown_resp.json()
        assert drilldown_body["data"]["persona_id"] == "persona-alpha"
        assert drilldown_body["data"]["source_breakdown"]["route_policy_summary"]["rule_count"] >= 0
        assert drilldown_body["data"]["source_breakdown"]["health"] is not None
        assert store.calls["get_route_policy_for_persona"] == 1, (
            f"Expected exactly 1 route policy call for drilldown target, got {store.calls['get_route_policy_for_persona']}"
        )

        # 4. Ordering, Filters, and Pagination:
        page_resp = client.get(
            "/bff/management/quarterly-ranking",
            headers=HEADERS,
            params={"quarter": "2026-Q1", "page_size": 1},
        )
        assert page_resp.status_code == 200, page_resp.text
        page_body = page_resp.json()
        assert len(page_body["data"]["items"]) == 1
        assert page_body["data"]["items"][0]["persona_id"] == items[0]["persona_id"]
        assert page_body["page_info"]["total"] == persona_count
        assert page_body["page_info"]["next_page_token"] is not None

        # 5. Tenant Boundary Isolation & Evaluator Read Contract:
        # Cross-tenant caller cannot see tenant personas or leak private state
        store.reset_counts()
        alien_resp = client.get(
            "/bff/management/quarterly-ranking",
            headers={"Authorization": "Bearer op-alien:operator,reviewer:alien-tenant-999"},
            params={"quarter": "2026-Q1"},
        )
        assert alien_resp.status_code == 200, alien_resp.text
        assert alien_resp.json()["data"]["summary"]["ranking_universe_count"] == 0
        assert len(alien_resp.json()["data"]["items"]) == 0
        assert store.calls["list_authoritative_paper_runtime_monitoring_sessions"] == 0

        # Original tenant-visible boundary: evaluator principal has 0 unnecessary monitoring reads,
        # GET ranking remains 200, and denied write remains 403.
        store.reset_counts()
        evaluator_headers = {"Authorization": "Bearer evaluator-token:viewer:tenant-dev"}
        eval_resp = client.get(
            "/bff/management/quarterly-ranking",
            headers=evaluator_headers,
            params={"quarter": "2026-Q1"},
        )
        assert eval_resp.status_code == 200, eval_resp.text
        assert eval_resp.json()["data"]["summary"]["ranking_universe_count"] == 0
        assert len(eval_resp.json()["data"]["items"]) == 0
        assert store.calls["list_authoritative_paper_runtime_monitoring_sessions"] == 0

        denied = client.post(
            "/bff/management/quarterly-ranking/recommendations/r1/submit",
            headers={**evaluator_headers, "Idempotency-Key": "evaluator-denied"},
            json={"quarter": "2026-Q1"},
        )
        assert denied.status_code == 403



# Owner rows and telemetry are local test inputs. The registered HTTP router,
# projection, telemetry reducer, scorer, adapter and snapshot identity are real.
def _return_ranking(monkeypatch, tmp_path, observations, *, quarter="2026-Q4", route="/bff/management/quarterly-ranking"):
    rows = []
    summaries = {}
    for index, (name, metrics) in enumerate(observations):
        runtime_id = f"return-runtime-{index}"
        metrics = dict(metrics)
        if "quarter_equity_observations" not in metrics and "pnl_period_start_at" in metrics:
            # Explicit local owner-valued flat-ledger fixtures for the return
            # normalisation tests. No production fallback reconstructs history.
            metrics["quarter_equity_observations"] = [
                _quarter_test_observation(runtime_id, record)
                for record in [metrics, *metrics.get("history", [])]
            ]
        rows.append({
            "id": name, "persona_id": name, "name": name,
            "state": "paper_running", "stage": "paper_running", "risk": "low",
            "runtime_resolution": "active", "session_resolution": "active",
            "runtime_ids": [runtime_id], "capital_scope": "paper_ledger",
            "session_summary": {"active": 1}, "evaluation_summary": {"completed": 0},
            "capability_summary": {"has_snapshot": False}, "memory_summary": {"total": 0},
            "routed_strategy_count": 1,
        })
        summaries[runtime_id] = {"runtime_id": runtime_id, "collected_at": "2026-11-12T04:10:00Z",
                                 "drawdown": 0.0, "fill_rate": 0.0, "avg_slippage_bps": 0.0,
                                 "fill_rate_at": "2026-11-12T04:10:00Z",
                                 "avg_slippage_bps_at": "2026-11-12T04:10:00Z",
                                 **metrics}
    store = _Pm12LeagueTestStore(fallback=False)
    store.get_telemetry_summary = lambda runtime_id: summaries.get(runtime_id)
    monkeypatch.setattr(personas_service, "_pm12_persona_league_rows", lambda **kwargs: rows)
    with _fresh_client(str(tmp_path), store=store) as client:
        response = client.get(route, headers=HEADERS, params={"quarter": quarter, "persona_id": rows[0]["id"]})
        assert response.status_code == 200, response.text
        return response.json()["data"]


def _period_pnl(pnl, capital):
    return {"pnl": pnl, "initial_cash": capital, "pnl_period_start_at": "2026-10-01T00:00:00Z",
            "pnl_at": "2026-11-12T04:10:00Z"}


def _quarter_test_observation(runtime_id, record):
    from services.execution.lean_runtime.performance_telemetry import quarter_equity_observation
    pnl, capital = record.get("pnl"), record.get("initial_cash")
    equity = capital + pnl if isinstance(capital, (int, float)) and isinstance(pnl, (int, float)) else None
    return {
        **quarter_equity_observation(
            {**record, "portfolio_value": equity, "cash": equity,
             "valuation_mark_count": 0, "fill_count": 1},
            {"valuation_method": "fill_cash_ledger_mark_to_market", "mark_refs": []},
            as_of=record.get("pnl_at"),
        ),
        "runtime_id": runtime_id, "binding_id": "test-binding",
        "event_id": f"test-owner-{runtime_id}-{record.get('pnl_at')}",
    }


def test_quarterly_route_return_on_capital_discriminates_audited_pnls(monkeypatch, tmp_path):
    # Inherited audited PnLs. Capital provenance and its boundary are recorded
    # in the task evidence; activity is an explicitly compatible test tuple.
    source_path = Path(__file__).resolve().parents[4] / "docs/deployment/evidence/RANKING-PNL-NORMALISATION-20261112/hosted-capital-observations.json"
    recorded = {row["name"]: row for row in json.loads(source_path.read_text())}
    data = _return_ranking(monkeypatch, tmp_path, [
        (name, {**_period_pnl(pnl, recorded[name]["initial_cash"]),
                "pnl_period_start_at": recorded[name]["ledger_started_at"]})
        for name, pnl in [("衡岳 R2", 12880.116), ("青禾 R3", 4730.0), ("澄川 R2", 207.0)]
    ])
    items = data["items"]
    assert [item["persona_id"] for item in items] == ["衡岳 R2", "青禾 R3", "澄川 R2"]
    assert [item["components"]["pnl_score"] for item in items] == [62.880116, 54.73, 50.207]
    assert [item["overall_score"] for item in items] == [49.508041, 46.6555, 45.07245]
    for item in items:
        metrics = item["metrics"]
        assert metrics["pnl_capital"] == 100000.0
        assert metrics["pnl_return_unit"] == "fraction"
        assert metrics["pnl_unit"] == "ledger_currency"
        assert metrics["pnl_period"]["quarter"] == "2026-Q4"
        assert metrics["pnl_period"]["as_of"] == "2026-11-12T04:10:00Z"
        assert item["allocation_policy_input"]["source_formula_version"] == "pm12-return-on-capital-v2"
        assert item["allocation_policy_input"]["rank_score"] == item["overall_score"]
    assert data["formula"]["version_history"][1]["formula_version"] == "pm12-default-v1"
    assert "operator-20261112" in data["formula"]["governance_evidence_refs"][0]


def test_quarterly_route_return_is_scale_invariant_and_zero_pnl_is_known(monkeypatch, tmp_path):
    data = _return_ranking(monkeypatch, tmp_path, [
        ("small", _period_pnl(20.0, 100.0)), ("scaled", _period_pnl(2000.0, 10000.0)),
        ("lower", _period_pnl(10.0, 100.0)), ("zero", _period_pnl(0.0, 100.0)),
        ("loss", _period_pnl(-20.0, 100.0)),
    ])
    scores = {item["persona_id"]: item["components"]["pnl_score"] for item in data["items"]}
    assert scores == {"small": 70.0, "scaled": 70.0, "lower": 60.0, "zero": 50.0, "loss": 30.0}


@pytest.mark.parametrize("changes", [
    {"initial_cash": None}, {"initial_cash": 0}, {"initial_cash": -10}, {"initial_cash": True},
    {"initial_cash": float("inf")}, {"pnl": None}, {"pnl": float("nan")}, {"pnl": False},
    {"pnl_period_start_at": None}, {"pnl_at": None},
    {"pnl_period_start_at": "2026-09-30T23:59:59Z"},
    {"pnl_at": "2027-01-01T00:00:00Z"},
])
def test_quarterly_route_unknown_pnl_stays_unknown_and_fails_closed(monkeypatch, tmp_path, changes):
    data = _return_ranking(monkeypatch, tmp_path, [("unknown", {**_period_pnl(207.0, 100000.0), **changes})])
    item = data["items"][0]
    assert item["components"]["pnl_score"] is None
    assert item["overall_score"] is None and item["score"] is None and item["rank"] is None
    assert item["tier"] == "unknown" and item["eligible"] is False
    assert item["allocation_policy_input"] is None
    assert "unknown_pnl_return" in item["exclusion_codes"]


def test_quarterly_route_does_not_sum_cumulative_history(monkeypatch, tmp_path):
    observation = _period_pnl(200.0, 1000.0)
    observation["history"] = [{"runtime_id": "return-runtime-0", "collected_at": "2026-11-01T00:00:00Z",
                               **_period_pnl(100.0, 1000.0), "pnl_at": "2026-11-01T00:00:00Z"}]
    item = _return_ranking(monkeypatch, tmp_path, [("history", observation)])["items"][0]
    assert item["metrics"]["pnl"] == 200.0
    assert item["metrics"]["pnl_capital"] == 1000.0
    assert item["components"]["pnl_score"] == 70.0


def test_quarterly_drilldown_keeps_unknown_contribution(monkeypatch, tmp_path):
    data = _return_ranking(monkeypatch, tmp_path, [("unknown", _period_pnl(None, 1000.0))],
                           route="/bff/management/quarterly-ranking/drilldown")
    pnl = next(row for row in data["contributions"] if row["key"] == "pnl")
    assert pnl["score"] is None and pnl["weighted_contribution"] is None
    assert data["score"] is None and data["summary"]["total_weighted_contribution"] is None


def test_pm12_v1_snapshot_digest_stays_readable_and_v2_is_versioned():
    import hashlib
    from services.rankings.snapshots import snapshot_record, _pm12_ranking_snapshot_payload_items
    legacy = [{"persona_id": "legacy", "formula_version": "pm12-default-v1", "overall_score": 62.5}]
    original_content = {"surface": "quarterly", "period": "2026-Q4", "formula_version": "pm12-default-v1",
                        "items": _pm12_ranking_snapshot_payload_items(legacy)}
    original_digest = hashlib.sha256(json.dumps(original_content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    record = snapshot_record(legacy, surface="quarterly", period="2026-Q4")
    assert record.content_digest == original_digest and record.formula_version == "pm12-default-v1"
    current = [{**legacy[0], "formula_version": "pm12-return-on-capital-v2"}]
    updated = snapshot_record(current, surface="quarterly", period="2026-Q4")
    assert updated.formula_version == "pm12-return-on-capital-v2"
    assert updated.content_digest != record.content_digest
    with pytest.raises(ValueError, match="mixed"):
        snapshot_record(legacy + current, surface="quarterly", period="2026-Q4")


def test_pnl_projection_reaches_quarterly_http_route(monkeypatch, tmp_path):
    from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
    from services.telemetry.test_runtime_summary_projection import _event
    projection = RuntimeSummaryProjectionStore(path=str(tmp_path / "projection.json"))
    event = _event("pnl_snapshot", created_at="2026-11-12T04:10:00Z")
    event["runtime_id"] = "return-runtime-0"
    event["metrics"] = {**_period_pnl(4730.0, 100000.0), "portfolio_value": 104730.0,
                        "cash": 104730.0, "valuation_mark_count": 0, "fill_count": 1}
    event["metadata"].update(valuation_method="fill_cash_ledger_mark_to_market", mark_refs=[])
    event["pnl_as_of"] = event["created_at"]
    summary = projection.project_event(event)
    item = _return_ranking(monkeypatch, tmp_path, [("projected", summary)])["items"][0]
    assert item["components"]["pnl_score"] == 54.73
    assert item["metrics"]["pnl_sources"][0]["event_id"] == event["event_id"]


@pytest.mark.parametrize("version", ["pm12-default-v1", "pm12-return-on-capital-v2"])
def test_bff_allocation_reader_accepts_both_snapshot_versions(version):
    from services.rankings.snapshots import snapshot_record
    from services.rankings.snapshots import SUPPORTED_FORMULA_VERSIONS, _stable_json_hash
    snapshot = snapshot_record([{"persona_id": "p", "formula_version": version}],
                               surface="quarterly", period="2026-Q4").to_canonical_dict()
    store = SimpleNamespace(get_ranking_snapshot=lambda snapshot_id: snapshot)
    # Reuse this suite's established PM-12 source namespace, supplying its
    # explicit read port. The legacy service's optional main fallback is never
    # imported or exercised by an application-layer test.
    namespace = _compile_pm12_namespace(store)
    namespace.update(SUPPORTED_FORMULA_VERSIONS=SUPPORTED_FORMULA_VERSIONS, _stable_json_hash=_stable_json_hash)
    assert namespace["_pm12_allocation_snapshot_record"](
        snapshot["ranking_snapshot_id"], bff_error_fn=lambda *a, **kw: HTTPException(status_code=422)
    ) == snapshot


def test_pnl_return_requires_all_runtime_denominators_and_compatible_windows():
    records = [{"runtime_id": runtime_id, **_period_pnl(pnl, 1000.0), "pnl_currency": "TWD"}
               for runtime_id, pnl in [("r1", 100.0), ("r2", 200.0)]]
    metrics = personas_service._pm12_telemetry_metrics_from_records(["r1", "r2"], records)
    assert metrics["pnl_return"] == 0.15 and metrics["pnl_capital"] == 2000.0
    assert personas_service._pm12_telemetry_metrics_from_records(["r1", "r2", "missing"], records)["pnl_return"] is None
    records[1]["pnl_currency"] = "USD"
    assert personas_service._pm12_telemetry_metrics_from_records(["r1", "r2"], records)["pnl_return"] is None
    records[1]["pnl_currency"] = "TWD"
    records[1]["pnl_period_start_at"] = "2026-10-02T00:00:00Z"
    assert personas_service._pm12_telemetry_metrics_from_records(["r1", "r2"], records)["pnl_return"] is None


def _project_quarter_series(tmp_path, events):
    from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
    path = str(tmp_path / "quarter-owner-projection.json")
    for event in events:
        # Read from a new store each time to prove the retained series is durable.
        RuntimeSummaryProjectionStore(path=path).project_event(event)
    return RuntimeSummaryProjectionStore(path=path).get("return-runtime-0")


def test_quarterly_http_route_retains_maximum_decline_after_recovery(monkeypatch, tmp_path):
    from services.telemetry.test_runtime_summary_projection import _quarter_valuation_event
    events = [
        _quarter_valuation_event("2026-09-30T23:59:59Z", 10.0, started_at="2026-09-01T00:00:00Z"),
        _quarter_valuation_event("2026-10-01T00:00:00Z", 1000.0, started_at="2026-09-01T00:00:00Z"),
        _quarter_valuation_event("2026-11-01T00:00:00Z", 800.0, started_at="2026-09-01T00:00:00Z", fills=2),
        _quarter_valuation_event("2026-12-15T00:00:00Z", 1200.0, started_at="2026-09-01T00:00:00Z", fills=3),
        _quarter_valuation_event("2027-01-01T00:00:00Z", 1.0, started_at="2026-09-01T00:00:00Z", fills=4),
    ]
    summary = _project_quarter_series(tmp_path, events)
    # Current rolling drawdown and cumulative latest PnL are outside the quarter.
    summary["drawdown"] = 0.0
    data = _return_ranking(monkeypatch, tmp_path, [("recovered", summary)])
    item = data["items"][0]
    metrics = item["metrics"]
    assert metrics["pnl"] == 200.0 and metrics["pnl_return"] == 0.2
    assert metrics["drawdown"] == pytest.approx(0.2)
    assert metrics["pnl_period"]["as_of"] == "2026-12-15T00:00:00Z"
    assert metrics["total_trades"] == 2
    assert item["components"]["risk_score"] == 55.0
    assert item["quarter_window"]["timezone"] == "UTC"
    assert metrics["runtime_coverage"] == {"observed": 1, "expected": 1, "ratio": 1.0}
    assert metrics["temporal_observation_coverage"]["observed_utc_days"] == 3
    assert metrics["temporal_observation_coverage"]["ratio"] == pytest.approx(3 / 92)
    assert metrics["trading_sample_sufficiency"]["status"] == "unknown"
    assert all(ref["observed_at"].startswith("2026-") for ref in metrics["telemetry_evidence_refs"])


def test_quarterly_route_deduplicates_cumulative_observations(monkeypatch, tmp_path):
    sample = _quarter_test_observation("return-runtime-0", _period_pnl(200.0, 1000.0))
    only_final = _return_ranking(monkeypatch, tmp_path, [("single", {"quarter_equity_observations": [sample]})])["items"][0]
    repeated = _return_ranking(monkeypatch, tmp_path, [("repeated", {"quarter_equity_observations": [
        sample, {**sample, "event_id": "retried-owner-event"}, sample,
    ]})])["items"][0]
    assert only_final["metrics"]["pnl"] == repeated["metrics"]["pnl"] == 200.0
    assert only_final["metrics"]["drawdown"] == repeated["metrics"]["drawdown"] == 0.0
    assert repeated["metrics"]["telemetry_coverage_count"] == 1
    assert repeated["metrics"]["total_trades"] == 1


@pytest.mark.parametrize("defect,expected", [
    ("missing_series", "missing_quarter_series"),
    ("missing_open", "missing_quarter_opening_equity"),
    ("missing_marks", "missing_marks"),
    ("conflict", "ambiguous_quarter_observation"),
    ("cash_flow", "incompatible_ledger_funding_or_binding"),
    ("timezone", "invalid_observation_timezone"),
])
def test_quarterly_http_route_unknown_series_fails_closed(monkeypatch, tmp_path, defect, expected):
    from services.telemetry.test_runtime_summary_projection import _quarter_valuation_event
    first = _quarter_valuation_event("2026-10-01T00:00:00Z", 1000.0)
    last = _quarter_valuation_event("2026-12-15T00:00:00Z", 1200.0, fills=2)
    events = [first, last]
    if defect == "missing_marks":
        last["metadata"]["mark_refs"] = []
    elif defect == "missing_open":
        last["metrics"]["pnl_period_start_at"] = "2026-09-01T00:00:00Z"
        events = [last]
    elif defect == "conflict":
        events.append(_quarter_valuation_event("2026-12-15T00:00:00Z", 1300.0, fills=2))
    elif defect == "cash_flow":
        last["metrics"].update(initial_cash=1100.0, pnl=100.0)
    summary = _project_quarter_series(tmp_path, events)
    if defect == "missing_series":
        summary["quarter_equity_observations"] = []
    elif defect == "timezone":
        summary["quarter_equity_observations"][0]["as_of"] = "2026-10-01T00:00:00"
    item = _return_ranking(monkeypatch, tmp_path, [("unknown", summary)])["items"][0]
    assert item["score"] is None and item["rank"] is None and item["eligible"] is False
    assert item["metrics"]["drawdown"] is None and item["metrics"]["pnl"] is None
    assert expected in item["metrics"]["drawdown_reason"]
    assert item["allocation_policy_input"] is None


def test_quarterly_route_uses_offset_instant_and_per_metric_quarter_time(monkeypatch, tmp_path):
    sample = _quarter_test_observation("return-runtime-0", _period_pnl(100.0, 1000.0))
    sample["as_of"] = "2027-01-01T07:59:59+08:00"
    item = _return_ranking(monkeypatch, tmp_path, [("offset", {
        "quarter_equity_observations": [sample], "fill_rate": 0.0, "avg_slippage_bps": 99,
        "fill_rate_at": "2027-01-01T00:00:00Z", "avg_slippage_bps_at": "2026-09-30T23:59:59Z",
    })])["items"][0]
    assert item["metrics"]["pnl"] == 100.0
    assert item["metrics"]["pnl_period"]["as_of"] == "2026-12-31T23:59:59Z"
    assert item["metrics"]["fill_rate"] is None and item["metrics"]["avg_slippage_bps"] is None


def test_quarterly_runtime_aggregation_requires_synchronized_currency_series():
    window = personas_service._pm12_quarter_window("2026-Q4", "2026-12-31T23:59:59Z")
    records = []
    for runtime_id in ("r1", "r2"):
        observations = []
        for at, pnl in [("2026-11-01T00:00:00Z", -200.0), ("2026-12-15T00:00:00Z", 200.0)]:
            sample = _quarter_test_observation(runtime_id, {**_period_pnl(pnl, 1000.0), "pnl_at": at, "pnl_currency": "TWD"})
            observations.append(sample)
        records.append({"runtime_id": runtime_id, "quarter_equity_observations": observations})
    reduce = lambda: personas_service._pm12_telemetry_metrics_from_records(["r1", "r2"], records, quarter_window=window)
    metrics = reduce()
    assert metrics["pnl"] == 400.0 and metrics["pnl_capital"] == 2000.0
    assert metrics["drawdown"] == 0.2 and metrics["runtime_coverage"]["ratio"] == 1.0
    records[1]["quarter_equity_observations"][0]["as_of"] = "2026-11-02T00:00:00Z"
    assert "incompatible_runtime_quarter_series" in reduce()["pnl_return_reason"]
    records[1]["quarter_equity_observations"][0]["as_of"] = "2026-11-01T00:00:00Z"
    for sample in records[1]["quarter_equity_observations"]:
        sample["currency"] = "USD"
    assert reduce()["pnl"] is None


def test_quarterly_route_legacy_summary_never_supplies_quarter_equity(monkeypatch, tmp_path):
    item = _return_ranking(monkeypatch, tmp_path, [("legacy", {"pnl": 200, "drawdown": 0,
                          "initial_cash": 1000, "total_trades": 10})])["items"][0]
    assert item["metrics"]["pnl"] is None and item["metrics"]["drawdown"] is None
    assert item["metrics"]["pnl_return_reason"] == "missing_quarter_series"
