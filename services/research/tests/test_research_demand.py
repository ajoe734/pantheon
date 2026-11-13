from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient

from services.research.research_demand import DemandError, ResearchDemandStore
from services.research.tests.test_research_orchestrator_http_service import _load_service_module

T0 = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
BODY = {"persona_id": "p1", "mandate": "momentum", "missing_datasets": ["tw-margin"], "tried_families": ["b", "a", "a"],
        "short_measure": "gross_utilisation", "pool_version": 2}


def store(tmp_path, clock):
    return ResearchDemandStore(tmp_path / "d.json", now=lambda: clock[0])


def test_open_dedupes_and_is_tenant_scoped(tmp_path):
    clock = [T0]
    s = store(tmp_path, clock)
    first = s.open("t1", BODY)
    again = s.open("t1", BODY)
    assert first["created"] and not again["created"] and again["demand_id"] == first["demand_id"]
    assert first["tried_families"] == ["a", "b"] and first["status"] == "open"
    assert s.open("t1", {**BODY, "short_measure": "effective_bets"})["created"]
    assert s.open("t2", BODY)["created"]
    assert len(s.list("t1")) == 2 and len(s.list("t2")) == 1 and s.list("t3") == []


def test_invalid_record_is_refused(tmp_path):
    with pytest.raises(DemandError):
        store(tmp_path, [T0]).open("t1", {**BODY, "short_measure": "vibes"})
    with pytest.raises(DemandError):
        store(tmp_path, [T0]).open("t1", {**BODY, "mandate": ""})


def test_legacy_demand_without_context_still_reconciles(tmp_path):
    s = store(tmp_path, [T0])
    row = s.open("t1", BODY)
    legacy = {k: v for k, v in row.items()
              if not k.startswith(("case_file", "retrieval_influence")) and k != "created"}
    s.path.write_text(json.dumps([legacy]))
    assert not s.open("t1", BODY)["created"]
    later = {"artifact_ref": "new", "joined_at": (T0 + timedelta(days=1)).isoformat()}
    assert s.reconcile("t1", "p1", [later])[0]["status"] == "satisfied"


def test_satisfied_only_by_later_member_of_same_persona(tmp_path):
    clock = [T0]
    s = store(tmp_path, clock)
    s.open("t1", BODY)
    clock[0] = T0 + timedelta(days=1)
    older = {"artifact_ref": "old", "joined_at": (T0 - timedelta(days=5)).isoformat()}
    assert s.reconcile("t1", "p1", [older])[0]["status"] == "open"
    later = {"artifact_ref": "new", "joined_at": clock[0].isoformat()}
    row = s.reconcile("t1", "p1", [older, later])[0]
    assert row["status"] == "satisfied" and row["satisfied_by"] == "new"
    # a satisfied demand no longer blocks a new one
    assert s.open("t1", BODY)["created"]


def test_expires_after_thirty_days_and_new_demand_may_open(tmp_path):
    clock = [T0]
    s = store(tmp_path, clock)
    first = s.open("t1", BODY)
    clock[0] = T0 + timedelta(days=29, hours=23)
    assert s.list("t1", status="open")
    clock[0] = T0 + timedelta(days=30)
    assert s.list("t1", status="open") == [] and s.list("t1", status="expired")[0]["demand_id"] == first["demand_id"]
    assert s.open("t1", BODY)["created"]


def test_http_routes_require_tenant_auth_and_serve_records():
    module = _load_service_module()
    client = TestClient(module.app)
    assert client.get("/api/research/persona-demands").status_code in (401, 403)
    assert not hasattr(module, "discovery_allocator_ports")
    assert not [r for r in module.app.routes if "discovery" in getattr(r, "path", "")]
