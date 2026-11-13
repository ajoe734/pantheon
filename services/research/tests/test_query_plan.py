from __future__ import annotations

import copy
import importlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.research import query_plan as qp
from services.research.query_plan import QueryPlanError, QueryPlanStore, build_proposal
from services.research.research_demand import ResearchDemandStore
from services.research.tests.test_research_demand import BODY
from services.research.tests.test_research_orchestrator_http_service import _load_service_module

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def demand(**extra):
    return dict(tenant_id="t1", demand_id="d1", status="open", mandate="Taiwan momentum", missing_datasets=["tw-margin-balance"],
                expires_at=(NOW + timedelta(days=20)).isoformat(), **extra)


def test_proposal_uses_only_open_tenant_demand_fields_and_the_fixed_vocabulary():
    row = demand()
    row.update(fetched_text="quality volatility liquidity", model_output="monthly revenue", tried_families=["value"])
    excluded = [dict(row, tenant_id="other"), dict(row, status="satisfied"), dict(row, expires_at=NOW.isoformat())]
    p = build_proposal("t1", [row, *excluded], now=NOW)
    assert set(p["matched_terms"]) == {"Taiwan", "momentum", "margin balance"}
    assert p["demand_ids"] == ["d1"] and p["paper_only"]
    assert all(set(change["after"]) <= set(json.loads(qp.QUERY_VOCABULARY_PATH.read_text())["terms"]) for change in p["changes"].values())
    assert build_proposal("t1", excluded, now=NOW) is None
    unknown = dict(row, mandate="devalue-untrusted", missing_datasets=[])
    assert build_proposal("t1", [unknown], now=NOW)["status"] == "no_change"


def test_exact_diff_applies_only_query_terms_and_never_modifies_source_policy(tmp_path):
    original = qp.FETCH_POLICY_PATH.read_bytes()
    p = build_proposal("t1", [demand()], now=NOW)
    policy = json.loads(original)
    candidate = copy.deepcopy(policy)
    for op in p["json_patch"]:
        _, _, sid, field = op["path"].split("/")
        assert sid in qp.QUERY_SOURCE_IDS and field == "query_terms"
        if op["op"] == "test":
            assert candidate["sources"][sid][field] == op["value"]
        else:
            candidate["sources"][sid][field] = op["value"]
    target = tmp_path / "deploy/collector/fetch-policy.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(original)
    applied = subprocess.run(["git", "apply", "--unsafe-paths", "-"], input=p["fetch_policy_diff"], text=True, cwd=tmp_path,
                             capture_output=True, timeout=10)
    assert applied.returncode == 0, applied.stderr
    assert json.loads(target.read_bytes()) == candidate
    assert qp.FETCH_POLICY_PATH.read_bytes() == original
    for sid in policy["sources"]:
        if sid not in qp.QUERY_SOURCE_IDS:
            assert policy["sources"][sid] == candidate["sources"][sid]


def test_weekly_revision_is_idempotent_tenant_scoped_and_survives_restart(tmp_path):
    clock = [NOW]
    path = tmp_path / "plans.json"
    s = QueryPlanStore(path, now=lambda: clock[0])
    a = s.refresh("t1", [demand()])
    assert QueryPlanStore(path, now=lambda: clock[0]).refresh("t1", [demand()]) == a
    b = s.refresh("t1", [dict(demand(), mandate="Taiwan value")])
    assert b["task_id"] == a["task_id"] and b["query_plan"]["revision"] == 2
    clock[0] += timedelta(days=7)
    c = s.refresh("t1", [demand()])
    assert c["task_id"] != a["task_id"] and c["query_plan"]["revision"] == 1
    assert s.refresh("other", [demand()]) is None
    assert len(s.list_tasks()) == 2


def test_research_and_collector_read_exactly_one_shared_vocabulary(monkeypatch):
    monkeypatch.syspath_prepend(str(qp.COLLECTOR_DIR))
    config = importlib.import_module("collector_stack.config")
    assert config.QUERY_VOCABULARY_PATH.resolve() == qp.QUERY_VOCABULARY_PATH.resolve()
    reads = []
    original_open = Path.open
    def tracked(path, *args, **kwargs):
        if path.name == "query_vocabulary.json":
            reads.append(path.resolve())
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", tracked)
    collector_terms = config.load_query_vocabulary()
    plan_terms = qp._read_inputs()[0]
    assert collector_terms == plan_terms
    assert reads == [qp.QUERY_VOCABULARY_PATH.resolve()] * 2
    monkeypatch.setattr(qp, "QUERY_VOCABULARY_PATH", qp.COLLECTOR_DIR / "missing-vocabulary.json")
    with pytest.raises(QueryPlanError):
        build_proposal("t1", [demand()], now=NOW)


def test_persona_cycle_real_research_http_entry_produces_read_only_weekly_task(tmp_path, monkeypatch):
    # Real PoolAutonomyCycle and HttpPoolCyclePorts demand calls enter the actual
    # research HTTP handlers. Pool/registry/memory owner reads are fixtures only.
    from services.persona.pool_autonomy_cycle import PoolAutonomyCycle
    from services.persona.test_pool_autonomy_cycle import Ports, pool, LOW
    from services.research.tests.test_backtest_owner_readback import _bearer, SECRET
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", SECRET)
    module = _load_service_module(data_dir=str(tmp_path))
    module.research_demand_store = ResearchDemandStore(tmp_path / "demands.json", now=lambda: NOW)
    module.query_plan_store = QueryPlanStore(tmp_path / "plans.json", now=lambda: NOW)
    client = TestClient(module.app)
    headers = _bearer("t1", ["operator"])
    calls = []
    class OwnerPorts(Ports):
        def reconcile_demands(self, persona, members):
            calls.append("reconcile")
            response = client.post("/api/research/persona-demands/reconcile", headers=headers,
                                   json={"persona_id": persona["persona_id"], "members": members})
            assert response.status_code == 200, response.text
            return response.json()["demands"]
        def open_demand(self, persona, body):
            calls.append("open")
            response = client.post("/api/research/persona-demands", headers=headers, json=body)
            assert response.status_code == 201, response.text
            return response.json()
    persona = {"persona_id": "p1", "tenant_id": "t1", "mandate": "Taiwan momentum", "required_data_sources": ["tw-margin-balance"]}
    cycle = PoolAutonomyCycle(OwnerPorts(), now=lambda: NOW.isoformat())
    result = cycle.run(persona, {"pool": pool(), "pool_health": LOW, "unavailable": []}, "cycle-1")
    assert result["status"] == "completed", repr(result)
    assert "open" in calls and "reconcile" in calls
    items = client.get("/api/research-orchestrator/tasks").json()
    assert len(items) == 1 and items[0]["query_plan"]["matched_terms"]
    task_id = items[0]["task_id"]
    assert client.get(f"/api/research-orchestrator/tasks/{task_id}").json() == items[0]
    assert client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={"adapter": "stub"}).status_code == 404
    assert client.post("/api/research/persona-demands/reconcile").status_code in (401, 403)
    # Same-week daily run is idempotent; next week's cycle emits a new proposal.
    cycle.run(persona, {"pool": pool(), "pool_health": LOW, "unavailable": []}, "cycle-2")
    assert client.get("/api/research-orchestrator/tasks").json() == items
    module.query_plan_store._now = lambda: NOW + timedelta(days=7)
    cycle.run(persona, {"pool": pool(), "pool_health": LOW, "unavailable": []}, "cycle-3")
    assert len(client.get("/api/research-orchestrator/tasks").json()) == 2


def test_final_demand_satisfied_or_expired_withdraws_proposal_and_keeps_history(tmp_path):
    clock = [NOW]
    path = tmp_path / "plans.json"
    s = QueryPlanStore(path, now=lambda: clock[0])
    other = s.refresh("other", [dict(demand(), tenant_id="other", demand_id="o1")])
    a = s.refresh("t1", [demand()])
    assert a["status"] == "proposed" and a["query_plan"]["fetch_policy_diff"]
    for rows in ([], [dict(demand(), status="satisfied")], [dict(demand(), expires_at=NOW.isoformat())]):
        s2 = QueryPlanStore(path, now=lambda: clock[0])
        w = s2.refresh("t1", rows)
        assert w["task_id"] == a["task_id"] and w["status"] == "no_change"
        plan = w["query_plan"]
        assert plan["demand_ids"] == [] and plan["json_patch"] == [] and plan["fetch_policy_diff"] == "" and plan["changes"] == {}
        assert w["source_refs"] == [] and plan["revision"] == 2
        assert w["history"][0]["demand_ids"] == ["d1"] and w["history"][0]["fetch_policy_diff"]
        # idempotent across restart
        assert QueryPlanStore(path, now=lambda: clock[0]).refresh("t1", rows) == w
    assert s.get_task(other["task_id"]) == other  # tenant isolation
    # a new demand re-proposes in the same week
    c = s.refresh("t1", [demand()])
    assert c["status"] == "proposed" and c["query_plan"]["revision"] == 3


@pytest.mark.parametrize("how", ["satisfied", "expired"])
def test_http_final_demand_closure_withdraws_task_readback(tmp_path, monkeypatch, how):
    from services.research.tests.test_research_demand import BODY
    from services.research.tests.test_backtest_owner_readback import _bearer, SECRET
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", SECRET)
    module = _load_service_module(data_dir=str(tmp_path))
    clock = [NOW]
    module.research_demand_store = ResearchDemandStore(tmp_path / "demands.json", now=lambda: clock[0])
    module.query_plan_store = QueryPlanStore(tmp_path / "plans.json", now=lambda: clock[0])
    client = TestClient(module.app)
    headers = _bearer("t1", ["operator"])
    body = dict(BODY, mandate="Taiwan momentum", missing_datasets=["tw-margin-balance"])
    created = client.post("/api/research/persona-demands", headers=headers, json=body)
    assert created.status_code == 201, created.text
    task = client.get("/api/research-orchestrator/tasks").json()[0]
    assert task["status"] == "proposed" and task["query_plan"]["fetch_policy_diff"]
    if how == "satisfied":
        clock[0] = NOW + timedelta(days=1)
        r = client.post("/api/research/persona-demands/reconcile", headers=headers, json={
            "persona_id": "p1", "members": [{"artifact_ref": "a1", "joined_at": (NOW + timedelta(hours=1)).isoformat()}]})
        assert r.status_code == 200, r.text
    else:
        clock[0] = NOW + timedelta(days=60)  # no write event: read-time expiry
    for _ in range(2):  # idempotent
        got = client.get(f"/api/research-orchestrator/tasks/{task['task_id']}").json()
        assert got["status"] == "no_change" and got["query_plan"]["demand_ids"] == []
        assert got["query_plan"]["fetch_policy_diff"] == "" and got["query_plan"]["json_patch"] == []
        assert got["history"][0]["demand_ids"] == task["query_plan"]["demand_ids"]
        assert got["query_plan"]["revision"] == 2
    assert len(client.get("/api/research-orchestrator/tasks").json()) == 1
