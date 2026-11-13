"""Production dispatch -> research executor -> real case composer over owner HTTP.

Owner data, gate verdicts and pool admission are controlled ports. This proves
orchestration and refusal, not profitable mutations or hosted deployment.
"""
import copy
import time
import urllib.request
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from services.knowledge.test_case_file_integration import _Fixture
from services.research.alpha_replication import evolution_executor as executor
from services.research.tests.test_evolution_gate_executor import research, world  # noqa: F401

TENANT = "tenant-dev"
LESSON = "lessons:memory_institutional:prior-failure"
OWNER_URLOPEN = urllib.request.urlopen


@contextmanager
def case_owner_server(monkeypatch, parent, persona_id="persona-1"):
    family = parent["strategy_id"]
    routes = {
        "/api/registry/entries/": {"strategy_id": family, "artifact_state": "approved"},
        "/api/registry/strategies/": [],
        f"/api/personas/{persona_id}/strategy-pool/versions": {"versions": [], "refusals": []},
        f"/api/personas/{persona_id}/strategy-pool": {"pool_version": 1, "members": []},
        "/api/memory/retrieve": {"retrieval_mode": "hybrid", "hits": [{
            "type": "institutional", "relevance_score": .9,
            "entry": {"entry_id": "prior-failure", "knowledge_type": "research_finding",
                      "content": {"headline": "Prior child failed after costs in a weak revenue regime",
                                  "tags": [f"family:{family}", "underlying:2330", "negative_memory"]}},
        }]},
        "/api/search/v2/query": {"retrieval_mode": "hybrid", "results": []},
    }
    server = _Fixture(routes)
    for owner in ("REGISTRY", "PERSONA", "MEMORY", "SEARCH"):
        monkeypatch.setenv(f"PANTHEON_{owner}_API_URL", server.endpoint.base_url)
    monkeypatch.setenv("RESEARCH_CASE_FILE_ACTOR_ID", "research-service")
    monkeypatch.setenv("RESEARCH_CASE_FILE_ACTOR_ROLES", "researcher")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def owners(monkeypatch, world):
    with case_owner_server(monkeypatch, world.parent) as server:
        yield server


def dispatch(research, world, monkeypatch, declaration=None, *, action="mutate", decision="evd-case", attempt="first"):
    admitted = []
    monkeypatch.setattr(research, "_admit_evolution_child", lambda _params, child: admitted.append(child))
    client = TestClient(research.app)
    task = client.post("/api/research-orchestrator/tasks", json={
        "title": decision, "objective": f"approved {action}", "constraints": {"tenant_id": TENANT},
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"task-{decision}-{attempt}"}).json()
    member = {"persona_id": "persona-1", "artifact_ref": world.parent["artifact_id"],
              "checksum": "owner-checksum", "joined_at": "2026-10-01T00:00:00Z"}
    if declaration is not None:
        # The existing evolution receipt adapter forwards the pool_member
        # envelope intact, including an explicit proposal's declaration.
        member["addresses_lessons"] = declaration
    response = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={
        "parameters": {"tenant_id": TENANT, "decision_id": decision, "action_type": action,
                       "target_artifact_id": world.parent["artifact_id"], "target_version": world.parent["version"],
                       "pool_member": member, "work_item_id": task["task_id"]},
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"run-{decision}-{attempt}"})
    assert response.status_code in {200, 201}, response.text
    run = response.json()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = client.get(f"/api/research-orchestrator/runs/{run['run_id']}").json()
        if run["status"] in {"completed", "failed"}:
            return run, admitted
        time.sleep(.02)
    pytest.fail("bounded dispatch did not reach a terminal receipt")


def test_prior_failure_is_cited_by_real_mutate_dispatch(research, world, owners, monkeypatch):
    run, admitted = dispatch(research, world, monkeypatch, {"lesson_ids": [LESSON]})
    assert run["status"] == "completed" and run["outcome"] == "candidate_created", run
    case = run["evolution_case_file"]
    assert case["subject"]["strategy_family"] == world.parent["strategy_id"]
    assert case["subject"]["underlyings"] == world.parent["parameters"]["symbols"]
    assert case["subject"]["member_artifact_refs"] == [world.parent["artifact_id"]]
    assert case["subject"]["persona_id"] == "persona-1" and case["subject"]["tenant_id"] == TENANT
    assert "failed after costs" in case["groups"]["lessons"]["items"][0]["excerpt"]
    assert case["execution_may_read"] is False
    assert all(c[2] == TENANT for c in owners.calls)
    queries = [c[1] for c in owners.calls if c[1].startswith("/api/memory/retrieve")]
    assert queries and all("family%3A" in q and "underlying%3A" in q for q in queries)
    assert len(admitted) == len(world.registered) == 50
    for child in run["evolution_result"]["children"]:
        assert child["addresses_lessons"] == {"lesson_ids": [LESSON]}
        assert child["case_file_id"] == case["case_file_id"]
        assert child["case_file_digest"] == case["digest"]
    for posted in world.registered.values():
        assert posted["metadata"]["case_file_digest"] == case["digest"]
        assert posted["metadata"]["addresses_lessons"] == {"lesson_ids": [LESSON]}
        assert "case_file_id" not in posted["strategy_artifact"]


@pytest.mark.parametrize("declaration,reason", [
    (None, "addresses_lessons_missing"),
    ({"lesson_ids": ["invented-id"]}, "addresses_lessons_unknown_citation"),
    ({"none_applicable": True}, "addresses_lessons_none_applicable_requires_reason"),
    ({"lesson_ids": []}, "addresses_lessons_invalid"),
    ({"lesson_ids": [LESSON, LESSON]}, "addresses_lessons_invalid"),
])
def test_dispatch_refuses_invalid_declarations_with_a_recorded_reason(
    research, world, owners, monkeypatch, declaration, reason,
):
    run, admitted = dispatch(research, world, monkeypatch, declaration)
    assert run["status"] == "completed" and run["outcome"] == "no_candidate", run
    assert run["outcome_reason"] == reason
    assert run["evolution_case_file_refusal"]["reason"] == reason
    assert run["evolution_case_file"]["case_file_id"]
    assert world.registered == {} and admitted == []
    assert not any(method == "POST" for method, _url in world.calls)


def test_explicit_none_applicable_is_preserved(research, world, owners, monkeypatch):
    declaration = {"none_applicable": True, "reason": "This structural control does not change the cost mechanism."}
    run, _ = dispatch(research, world, monkeypatch, declaration)
    assert run["outcome"] == "candidate_created", run
    assert run["evolution_child_context"]["addresses_lessons"] == declaration


def test_unavailable_memory_is_not_no_applicable_lessons(research, world, owners, monkeypatch):
    owners.routes.pop("/api/memory/retrieve")
    run, _ = dispatch(research, world, monkeypatch, {"none_applicable": True, "reason": "No lessons"})
    assert run["outcome"] == "no_candidate", run
    assert run["outcome_reason"].startswith("case_file_unavailable:")
    assert run["evolution_case_file"]["groups"]["lessons"]["status"] == "unavailable"
    assert not world.registered


def test_missing_caller_configuration_fails_closed(research, world, owners, monkeypatch):
    monkeypatch.delenv("RESEARCH_CASE_FILE_ACTOR_ROLES")
    run, _ = dispatch(research, world, monkeypatch, {"lesson_ids": [LESSON]})
    assert run["outcome_reason"] == "case_file_caller_context_missing"
    assert run["evolution_case_file_refusal"]["reason"] == run["outcome_reason"]
    assert not world.registered


def test_retrain_without_declaration_is_refused_before_training(research, world, owners, monkeypatch):
    run, _ = dispatch(research, world, monkeypatch, action="retrain")
    assert run["outcome_reason"] == "addresses_lessons_missing", run
    assert run["evolution_case_file"]["digest"]
    assert not any(method == "POST" for method, _url in world.calls)


def test_retrain_child_keeps_context_in_registry_and_across_retry(research, world, owners, monkeypatch):
    original_call = research._retrain_call

    def call(**kw):
        if kw["service"] == "training":
            if kw["url"].endswith("/sessions"):
                return {"session_id": "training-1"}
            if "/controls/" in kw["url"]:
                return {"controls": [{"parameter_key": "entry_z", "current_value": .8}]}
            if kw["url"].endswith("/controls"):
                return {"status": "accepted"}
            pytest.fail(f"unexpected training call: {kw['url']}")
        return original_call(**kw)

    def evaluate(*, kind, **_):
        return {"job_id": f"job-{kind}", "eval_id": f"eval-{kind}", "preview": {"evaluation_proof": {
            "status": "passed", "governance_gate_state": "passed", "proof_ref": f"proof-{kind}",
            "metrics": {"sharpe_ratio": .5 if kind == "baseline" else 1.5}, "validation_errors": []}}}

    monkeypatch.setattr(research, "_retrain_call", call)
    monkeypatch.setattr(research, "_retrain_search_update", lambda *_: {"entry_z": 1.0})
    monkeypatch.setattr(research, "_retrain_evaluate", evaluate)
    run, _ = dispatch(research, world, monkeypatch, {"lesson_ids": [LESSON]}, action="retrain")
    assert run["status"] == "completed" and run["outcome"] == "candidate_created", run
    assert len(world.registered) == 1
    posted = next(iter(world.registered.values()))
    assert posted["metadata"]["case_file_id"] == run["evolution_case_file"]["case_file_id"]
    assert posted["metadata"]["addresses_lessons"] == {"lesson_ids": [LESSON]}
    assert run["evolution_children"][posted["registry_id"]] == run["evolution_child_context"]
    assert "addresses_lessons" not in posted["strategy_artifact"]
    retry, _ = dispatch(research, world, monkeypatch, {"lesson_ids": [LESSON]}, action="retrain", attempt="retry")
    assert retry["outcome"] == "candidate_created", retry
    assert retry["evolution_case_file"] == run["evolution_case_file"]
    assert retry["evolution_children"] == run["evolution_children"]
    assert len(world.registered) == 1


def test_service_case_file_excludes_private_and_foreign_persona_memory(research, world, owners, monkeypatch):
    response = owners.routes["/api/memory/retrieve"]
    for persona in ("persona-1", "another-persona"):
        response["hits"].append({"type": "persona", "relevance_score": 1,
            "entry": {"memory_id": f"secret-{persona}", "persona_id": persona,
                      "relevance_scope": "persona_private", "content": {"summary": "private-secret"}}})
    run, _ = dispatch(research, world, monkeypatch, {"lesson_ids": [LESSON]})
    assert run["outcome"] == "candidate_created", run
    case = run["evolution_case_file"]
    assert case["caller"]["is_subject_persona"] is False
    assert all("secret" not in item["item_id"] for g in case["groups"].values() for item in g["items"])


def test_real_executor_checks_each_child_before_gate_or_registration(world, owners):
    case = executor.compose_member_case_file(world.parent, params={"pool_member": {"persona_id": "persona-1"}},
        tenant_id=TENANT, decision_id="direct", parent_registry_ref=world.parent["artifact_id"])

    class Gate:
        def get_family_total_trial_count(self, _): return 0
        def get_family_trial_count(self, _): return 0
        def evaluate_candidate(self, _): pytest.fail("invalid child reached the gate")

    original = copy.deepcopy(case)

    def tamper(_child, supplied_case):
        supplied_case["groups"]["lessons"]["items"].append({"item_id": "forged"})
        return {"lesson_ids": ["forged"]}

    for proposal, reason in ((None, "addresses_lessons_missing"), (tamper, "addresses_lessons_unknown_citation")):
        with pytest.raises(executor.ExecutorRefusal, match=reason):
            executor.execute_mutate(world.parent, decision_id="direct", family_id=world.parent["strategy_id"],
                gate=Gate(), request_factory=lambda _: pytest.fail("invalid child backtested"),
                register_child=lambda *_: pytest.fail("invalid child registered"), case_file=case,
                declare_lessons=proposal)
    assert case == original
