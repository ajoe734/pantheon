"""GET /bff/strategies/{id}: independent owner reads start together, each once per request.

Overlap is proven by start order: every fake owner read blocks on a gate that
opens only once all five reads have started. Serial code would never open the
gate and the read would time out, so no wall clock is asserted.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.strategies.router import create_strategies_router

FIXTURE = json.loads((Path(__file__).resolve().parents[4] / "acceptance" / "fixtures"
                      / "hosted_strategy_rules_20261119.json").read_text())
SID = FIXTURE["strategy_id"]
HEADERS = {"Authorization": "Bearer op-strategy-view:operator,viewer:tenant-a"}
PERSONA = "persona-a81b4fd15944698341aa"


class _Owners:
    def __init__(self, *, fail: frozenset = frozenset(), gate: bool = True) -> None:
        self.calls: List[str] = []
        self.fail = fail
        self._lock = threading.Lock()
        self._all_started = threading.Event()
        self._gate = gate
        self.gate_opened_by_overlap = False

    def _enter(self, name: str, needed: int = 5) -> None:
        with self._lock:
            self.calls.append(name)
            if len({c for c in self.calls}) >= needed:
                self.gate_opened_by_overlap = True
                self._all_started.set()
        if self._gate:
            assert self._all_started.wait(timeout=10), f"{name} ran alone: reads are serial"
        if name in self.fail:
            raise RuntimeError(f"{name} down")

    # read store port
    def get_strategy_spec_detail(self, sid, version_selector="current"):
        self._enter("spec")
        if sid != SID:
            return None
        return {"strategy_id": SID, "lifecycle_state": "paper", "persona_ids": [PERSONA],
                "governance": {"risk_level": "medium"}, "market_scope": {"symbols": ["AAPL"]}}

    def get_strategy_spec(self, sid):
        return None

    def list_runtime_bindings(self):
        self._enter("bindings")
        return []

    def list_telemetry_summaries(self):
        return []

    def get_latest_ranking_snapshot(self):
        self._enter("ranking")
        return {"rankings": []}

    def read_registry_strategy_artifacts_for_strategy(self, sid):
        self._enter("rules")
        return SimpleNamespace(status="ok", items=[{"entry": e} for e in FIXTURE["entries"]])

    def directory(self, tenant_id=None, *, snapshot_at=None, **_):
        self._enter("persona")
        self.tenants.append(tenant_id)
        return SimpleNamespace(records_by_id={PERSONA: {"persona_id": PERSONA, "lifecycle_state": "paper"}})

    tenants: List[Any] = []


def _client(owners: _Owners, tenant: str = "tenant-a") -> TestClient:
    owners.tenants = []
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_strategies_router(
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role,
        bff_error=auth_policy.bff_error,
        read_surface=lambda: owners,
        list_strategy_summaries=lambda: [],
        persona_directory_snapshot=owners.directory,
        persona_record_projected_state=personas_service._persona_record_projected_state,
        persona_operational_states=personas_service._PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        bff_me_tenant_payload=lambda identity, requested_tenant=None: {"id": tenant},
        normalize_lifecycle_state=personas_service._normalize_lifecycle_state,
        normalize_risk_level=personas_service._normalize_risk_level,
    ))
    app.add_middleware(OwnerReadContextMiddleware)
    return TestClient(app)


def test_five_owner_reads_start_before_any_finishes_and_each_runs_once():
    owners = _Owners()
    resp = _client(owners, tenant="tenant-a").get(f"/bff/strategies/{SID}", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert owners.gate_opened_by_overlap
    assert sorted(owners.calls) == ["bindings", "persona", "ranking", "rules", "spec"]
    assert owners.tenants == ["tenant-a"]  # caller tenant reached the overlapped Persona read
    body = resp.json()
    assert [r["registryId"] for r in body["data"]["rules"]]
    assert body["meta"]["surfaces"]["strategy_rules"]["status"] == "ok"
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "ok"


def test_failed_secondary_reads_keep_todays_answer_and_never_fail_the_request():
    owners = _Owners(fail=frozenset({"persona", "bindings", "ranking", "rules"}))
    resp = _client(owners).get(f"/bff/strategies/{SID}", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    data, meta = resp.json()["data"], resp.json()["meta"]
    assert data["rules"] is None
    assert meta["surfaces"]["strategy_rules"]["status"] == "unavailable"
    assert meta["surfaces"]["persona_states"]["status"] == "unavailable"
    assert data["runtimeStatus"] == "unknown"
    assert data["metricsResolution"] == "unavailable"


def test_missing_strategy_is_404_and_spec_failure_is_not_swallowed():
    owners = _Owners(gate=False)
    assert _client(owners).get("/bff/strategies/does-not-exist", headers=HEADERS).status_code == 404
    owners = _Owners(fail=frozenset({"spec"}), gate=False)
    assert _client(owners).get(f"/bff/strategies/{SID}", headers=HEADERS).status_code == 503


def test_unauthenticated_reader_is_refused_before_any_owner_read():
    owners = _Owners(gate=False)
    resp = _client(owners).get(f"/bff/strategies/{SID}")
    assert resp.status_code in (401, 403)
    assert owners.calls == []
