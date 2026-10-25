"""Fake-owner tests for the hosted phase/resume/E_close contract of the stimulus parent."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PARENT = Path(__file__).resolve().parents[2] / "integration/l12/test_stimulus_cross_loop_deployed_e2e.py"
_spec = importlib.util.spec_from_file_location("stimulus_parent_under_test", PARENT)
parent = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = parent
_spec.loader.exec_module(parent)

SHA = "b" * 40
CHAIN = "hosted-all12-chain-0001"
TENANT = "tenant-dev"


AUTH, ALPHA, SESS, EVAL_IDENT, EVAL_ID = "auth-run-1", "alpha-run-1", "ts-1", "eval-ident-1", "eval-1"
IMIT_RUN, IMIT_TASK, REQ, HANDOFF = "imit-run-1", "task-1", "req-1", "ho-1"
CAND, DSV = "cand-1", "dsv-1"
CHAIN_REFS = {"registry_id": "reg-1", "candidate_id": CAND, "dataset_version_id": DSV}

# Real DTO shapes of the A/B domain loop receipts (terminal_output/authority_readback/next_consumer).
DOMAIN_CASES: dict[str, dict[str, Any]] = {
    "alpha_replication": {
        "terminal_output": {"type": "ExperimentRun", "id": ALPHA, "authority_run_id": AUTH, "strategy_spec_id": "reg-1"},
        "authority_readback": {"authority_run_id": AUTH, "domain_run_id": ALPHA, "status": "completed"},
        "next_consumer": {"receipt_type": "TeachingSession", "receipt_id": SESS},
    },
    "persona_teaching": {
        "terminal_output": {"type": "TeachingEvaluationResult", "id": EVAL_IDENT, "session_id": SESS, "eval_id": EVAL_ID},
        "authority_readback": {"session_id": SESS, "eval_identity": EVAL_IDENT},
    },
    "imitation_research_handoff": {
        "terminal_output": {"type": "ExperimentRun", "task_id": IMIT_TASK, "run_id": IMIT_RUN},
        "authority_readback": {"task_id": IMIT_TASK, "run_id": IMIT_RUN},
    },
    "consultation_governance_handoff": {
        "terminal_output": {"type": "ConsultMemo", "id": "memo-1", "governance_handoff_id": HANDOFF},
        "authority_readback": {"memo_id": "memo-1", "request_id": REQ},
        "next_consumer": {"receipt_type": "ConsultGateHandoff", "receipt_id": HANDOFF},
    },
}


def domain_records() -> dict[tuple[str, str], Any]:
    """Actual Research/Training/Consultation owner records behind the A/B loops."""

    return {
        ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": {"tenant_id": TENANT, "metadata": {"chain_id": CHAIN}}},
        ("research", f"/api/research-orchestrator/runs/{AUTH}"): {
            "run_id": AUTH, "tenant_id": TENANT,
            "parameters": {"tenant_id": TENANT, "strategy_spec_id": "reg-1",
                           "experiment_run": {"run_id": ALPHA, "status": "completed"}},
        },
        ("training", f"/api/training/sessions/{SESS}"): {
            "session_id": SESS, "tenant_id": TENANT,
            "context_refs": [{"type": "experiment_run", "id": AUTH, "domain_run_id": ALPHA}],
        },
        ("training", f"/api/training/sessions/{SESS}/terminal-readback"): {
            "session_id": SESS, "tenant_id": TENANT, "eval_identity": EVAL_IDENT, "evaluation_result": {"eval_id": EVAL_ID},
        },
        ("research", f"/api/research-orchestrator/runs/{IMIT_RUN}"): {
            "run_id": IMIT_RUN, "task_id": IMIT_TASK, "tenant_id": TENANT,
            "metadata": {"candidate_id": CAND},
        },
        ("consultation", f"/api/consult/requests/{REQ}"): {
            "request_id": REQ, "tenant_id": TENANT, "target_type": "experiment_run", "target_id": IMIT_RUN,
            "metadata": {"chain_id": CHAIN, "registry_id": "reg-1", "teaching_session_id": SESS,
                         "dataset_version_id": DSV, "candidate_id": CAND, "experiment_run_id": IMIT_RUN},
        },
        ("consultation", f"/api/consult/handoffs/{HANDOFF}"): {
            "handoff_id": HANDOFF, "request_id": REQ, "status": "acknowledged",
        },
    }


def domain_loops(*phases: str) -> list[dict[str, Any]]:
    keys = {"A_research": ("alpha_replication", "persona_teaching"),
            "B_learning": ("human_imitation_shadow_evaluation", "consultation")}
    cases = {c[0]: c[2] for c in parent.LOOP_CASES}
    # deep copies: tests mutate these records and must not leak into each other
    return copy.deepcopy([
        {"loop_id": loop, "domain": "x", **{k: v for k, v in DOMAIN_CASES[cases[loop]].items()},
         "next_receipt": DOMAIN_CASES[cases[loop]].get("next_consumer", {"n": 1}), "owner_observation": {"svc": "x"}, "trigger": "t"}
        for phase in phases for loop in keys[phase]
    ])


def _case(loop_id: str, runtime: bool) -> dict[str, Any]:
    if loop_id in DOMAIN_CASES:
        base = _case("x", False)
        return {**base, "loop_id": loop_id, **DOMAIN_CASES[loop_id]}
    if runtime:
        return {
            "trigger_id": f"trg-{loop_id}", "terminal_output_id": f"out-{loop_id}",
            "authority_readback": {"id": loop_id}, "next_consumer_readback": {"n": 1},
            "owner_worker_identity": {"svc": "x"},
        }
    return {
        "loop_id": loop_id, "trigger": f"trg-{loop_id}", "terminal_output": f"out-{loop_id}",
        "authority_readback": {"id": loop_id}, "next_consumer": {"n": 1}, "owner": {"svc": "x"},
    }


def _report(domain: str, phase: str, status: str, keys: list[str], resources: dict[str, str], blocked: str | None = None) -> dict[str, Any]:
    runtime = domain == "runtime"
    cases: Any = {k: _case(k, True) for k in keys} if runtime else [_case(k, False) for k in keys]
    return {
        "git_sha": SHA, "status": "passed" if status == "completed" else "paused",
        "chain_id": CHAIN, "phase": phase, "phase_status": status, "blocked_before": blocked,
        "resource_ids": resources, "cases": cases,
    }


class FakeChild:
    def __init__(self, reports: dict[str, dict[str, Any]]) -> None:
        self.reports, self.calls = reports, []

    def __call__(self, *_args: Any, **kwargs: Any) -> SimpleNamespace:
        env = kwargs["env"]
        phase = env.get("PANTHEON_L12_HOSTED_CHAIN_PHASE")
        self.calls.append({
            "env": dict(env),
            "input": json.loads(Path(env["PANTHEON_L12_HOSTED_CHAIN_INPUT"]).read_text()) if phase else None,
        })
        Path(env["PANTHEON_L12_REPORT_PATH"]).write_text(json.dumps(self.reports[phase]), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


RETIRED = {  # write path suffix -> (state key, state after the write)
    "/status": None,
    "/retire": ("status", "retired"),
    "/advance": ("artifact_state", "retired"),
    "/revoke": ("decision_state", "revoked"),
}


def _service_transition_table() -> dict[str, set[str]]:
    """The DeploymentPlan transition table, read from services/deployment/service.py itself."""
    source = (Path(__file__).resolve().parents[3] / "services/deployment/service.py").read_text()
    block = source[source.index("allowed = {\n            PlanStatus.DRAFT"):]
    block = block[: block.index("        }\n") ]
    table: dict[str, set[str]] = {}
    for line in block.splitlines()[1:]:
        key, _, rest = line.strip().partition(": ")
        names = {n.strip().split(".")[1].lower() for n in rest.strip("{},").split(",") if "PlanStatus." in n}
        table[key.split(".")[1].lower()] = names
    return table


PLAN_TRANSITIONS = _service_transition_table()


def _saga(**extra: Any) -> dict[str, Any]:
    return {"saga_id": "saga-1", "plan_id": "plan-1", "tenant_id": TENANT, "binding_id": "rt-1", "status": "completed", **extra}


class FakeOwner:
    actor_id = "exec-actor-1"
    roles = ("persona.admin", "capital.admin", "risk_owner")

    def __init__(self, records: dict[tuple[str, str], Any], stuck: tuple[str, ...] = ()) -> None:
        self.records, self.log, self.stuck = records, [], stuck

    def request(self, service, method, path, body=None, idempotency_key=None):
        self.log.append((service, method, path, body))
        if method == "GET":
            record = self.records.get((service, path))
            return (200, record) if record is not None else (404, {})
        base = path.rsplit("/", 1)[0]
        record = self.records.get((service, base))
        if record is not None and path.endswith("/status") and base.startswith("/api/deployment/plans/"):
            # real DeploymentPlan transition table (services/deployment/service.py update_status)
            current, target = record.get("status"), (body or {}).get("status")
            if target != current and target not in PLAN_TRANSITIONS.get(current, set()):
                return 400, {"detail": f"Invalid plan status transition: {current} -> {target}"}
        if record is not None and base not in self.stuck:
            inner = next((v for v in record.values() if isinstance(v, dict)), record) if "tenant_id" not in record else record
            if body and "status" in body:
                inner["status"] = body["status"]
            else:
                for suffix, change in RETIRED.items():
                    if change and path.endswith(suffix):
                        inner[change[0]] = change[1]
        return 200, {}


def _owned(**extra: Any) -> dict[str, Any]:
    return {"tenant_id": TENANT, "metadata": {"chain_id": CHAIN}, **extra}


def _settings(tmp_path: Path, name: str = "receipt.json") -> Any:
    return parent.Settings(expected_sha=SHA, evidence_output=tmp_path / name, timeout_seconds=60)


def _runner(tmp_path, phase, owner, prior=None, name="receipt.json", decisions=None):
    return parent.HostedChainRunner(
        _settings(tmp_path, name), chain_id=CHAIN, phase=phase, tenant_id=TENANT,
        owner=owner, prior_receipt=prior, decision_refs=decisions,
    )


def _receipt(tmp_path: Path, name: str = "receipt.json") -> dict[str, Any]:
    return json.loads((tmp_path / name).read_text())


A_LOOPS = list(parent.CANONICAL_LOOP_IDS[:4])
A_KEYS = [c[2] for c in parent.LOOP_CASES[:4]]
B_KEYS = [c[2] for c in parent.LOOP_CASES[4:7]]


def test_paused_phase_is_normal_progress_not_failure_or_all12(monkeypatch, tmp_path):
    child = FakeChild({"A_research": _report("research", "A_research", "paused", A_KEYS[:2], {"registry_id": "reg-1"}, "alpha_admission")})
    monkeypatch.setattr(subprocess, "run", child)
    evidence = _runner(tmp_path, "A_research", FakeOwner({})).run()
    hosted = _receipt(tmp_path)["hosted"]
    assert evidence.status == "paused" and evidence.error is None
    assert hosted["phase_status"] == "paused" and hosted["blocked_before"] == "alpha_admission"
    assert hosted["all12_passed"] is False
    assert hosted["resource_ids"] == {"registry_id": "reg-1"}
    assert [l["loop_id"] for l in hosted["loops"]] == A_LOOPS[:2]
    assert child.calls[0]["env"][parent.HOSTED_FLAG] == "1"
    assert child.calls[0]["env"]["PANTHEON_L12_CROSS_LOOP_RUN_ID"] == CHAIN


def test_separate_process_resume_reuses_completed_phase_without_redispatch(monkeypatch, tmp_path):
    reports = {
        "A_research": _report("research", "A_research", "completed", A_KEYS, {"registry_id": "reg-1"}),
        "B_learning": _report("human", "B_learning", "completed", B_KEYS, {"handoff_id": "ho-1"}),
    }
    child = FakeChild(reports)
    monkeypatch.setattr(subprocess, "run", child)
    owner = FakeOwner({("registry", "/api/registry/strategy-specs/reg-1"): {"entry": _owned()}, **domain_records()})
    _runner(tmp_path, "A_research", owner).run()
    first = _receipt(tmp_path)

    # new "process": only the receipt file and owner reads cross the boundary
    _runner(tmp_path, "A_research", owner, prior=first, name="again.json").run()
    assert len(child.calls) == 1
    assert _receipt(tmp_path, "again.json")["hosted"]["phases"]["A_research"]["reused"] is True

    _runner(tmp_path, "B_learning", owner, prior=first, name="b.json").run()
    assert len(child.calls) == 2
    assert child.calls[1]["input"] == {
        "chain_id": CHAIN, "decisions": {}, "phase": "B_learning", "predecessors": {"registry_id": "reg-1"},
    }
    assert ("registry", "GET", "/api/registry/strategy-specs/reg-1", None) in owner.log
    assert _receipt(tmp_path, "b.json")["hosted"]["resource_ids"] == {"registry_id": "reg-1", "handoff_id": "ho-1"}


def test_phase_order_is_exact(monkeypatch, tmp_path):
    child = FakeChild({})
    monkeypatch.setattr(subprocess, "run", child)
    with pytest.raises(parent.StimulusProofError, match="requires completed predecessor phase B_learning"):
        _runner(tmp_path, "C_promote", FakeOwner({})).run()
    assert child.calls == []
    assert parent.HOSTED_PHASES == ("A_research", "B_learning", "C_promote", "D_observe", "E_close")
    assert _receipt(tmp_path)["hosted"]["phase_status"] == "failed"


@pytest.mark.parametrize("record", [None, {"entry": {"tenant_id": "other", "metadata": {"chain_id": CHAIN}}},
                                    {"entry": {"tenant_id": TENANT, "metadata": {"chain_id": "someone-else"}}}])
def test_missing_or_mismatched_predecessor_pauses_without_launch(monkeypatch, tmp_path, record):
    child = FakeChild({})
    monkeypatch.setattr(subprocess, "run", child)
    prior = {"hosted": {"chain_id": CHAIN, "resource_ids": {"registry_id": "reg-1"}, "loops": domain_loops("A_research"),
                        "phases": {"A_research": {"phase_status": "completed"}}}}
    records = {} if record is None else {("registry", "/api/registry/strategy-specs/reg-1"): record}
    runner = _runner(tmp_path, "B_learning", FakeOwner(records), prior=prior)
    evidence = runner.run()
    hosted = _receipt(tmp_path)["hosted"]
    assert child.calls == [] and evidence.status == "paused"
    assert hosted["blocked_before"] == "B_learning:predecessor_verification"
    assert "registry_id reg-1" in hosted["phases"]["B_learning"]["evidence"][0]


def test_receipt_only_approval_is_not_accepted(monkeypatch, tmp_path):
    child = FakeChild({})
    monkeypatch.setattr(subprocess, "run", child)
    prior = {"hosted": {"chain_id": CHAIN, "resource_ids": {"registry_id": "reg-1"},
                        "decisions": {"H1": "apv-1"},
                        "phases": {"A_research": {"phase_status": "paused"}}}}
    owner = FakeOwner({
        ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": _owned()},
        ("governance", "/api/governance/approvals/apv-1"): _owned(decision_state="proposed", target_id="reg-1"),
    })
    evidence = _runner(tmp_path, "A_research", owner, prior=prior).run()
    assert child.calls == [] and evidence.status == "paused"
    assert "not an approved decided record" in _receipt(tmp_path)["hosted"]["phases"]["A_research"]["evidence"][0]


def test_e_close_retires_only_verified_chain_records(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", FakeChild({}))
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:4]}
    prior = {"hosted": {"chain_id": CHAIN, "phases": done, "decisions": {"H1": "apv-1"}, "resource_ids": {
        "capital_binding_id": "bnd-1", "capital_pool_id": "pool-unrelated", "plan_id": "plan-1",
        "runtime_binding_id": "rt-1", "registry_id": "reg-1", "teaching_session_id": "ts-1"}}}
    owner = FakeOwner({
        ("capital", "/api/bindings/bnd-1"): {"binding": _owned(status="active")},
        ("capital", "/api/capital-pools/pool-unrelated"): {"tenant_id": TENANT, "metadata": {"chain_id": "other-chain"}},
        ("runtime", "/api/runtime-bindings/rt-1"): {"binding": _owned(status="active")},
        ("deployment", "/api/deployment/plans/plan-1"): _owned(status="executed"),
        ("deployment", "/api/deployment/sagas?plan_id=plan-1"): [_saga()],
        ("deployment", "/api/deployment/sagas/saga-1"): _saga(),
        ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": _owned(artifact_state="approved", version=3)},
        ("governance", "/api/governance/approvals/apv-1"): _owned(decision_state="decided", decision="approved", target_id="reg-1", version=3),
    })
    evidence = _runner(tmp_path, "E_close", owner, prior=prior).run()
    writes = [(m, p) for _s, m, p, _b in owner.log if m != "GET"]
    assert writes == [
        ("POST", "/api/runtime-bindings/rt-1/retire"),
        ("POST", "/api/deployment/sagas/saga-1/retire"),
        ("POST", "/api/deployment/sagas/saga-1/retire/finalize"),
        ("PATCH", "/api/bindings/bnd-1/status"),
        ("POST", "/api/registry/strategy-specs/reg-1/advance"),
        ("POST", "/api/governance/approvals/apv-1/revoke"),
    ]
    assert all("pool-unrelated" not in p for _m, p in writes)
    bodies = {p: b for _s, m, p, b in owner.log if m != "GET"}
    # real executing identity and the owners' actual request schemas
    assert bodies["/api/bindings/bnd-1/status"] == {"actor_id": "exec-actor-1", "actor_role": "persona.admin", "status": "revoked"}
    assert bodies["/api/governance/approvals/apv-1/revoke"] == {"actor_id": "exec-actor-1", "actor_role": "risk_owner", "expected_version": 3}
    assert set(bodies["/api/registry/strategy-specs/reg-1/advance"]) == {
        "target_state", "command_key", "expected_artifact_state", "expected_version", "expected_updated_at"}
    close = _receipt(tmp_path)["hosted"]["phases"]["E_close"]
    assert "chain label" in close["rejected_unrelated"][0]
    assert close["residual_non_retirable"] == ["teaching_session_id"]
    # the executed plan is retained history; only its saga and runtime binding are retired
    assert close["retained_history"][0]["plan_state"] == "executed" and close["retained_history"][0]["saga_state"] == "retired"
    assert all(r["kind"] != "plan_id" for r in close["retired"])
    assert owner.records[("deployment", "/api/deployment/plans/plan-1")]["status"] == "executed"
    # the unrelated pool stays unretired, so cleanup is incomplete and never complete/all12
    assert evidence.status == "paused" and close["phase_status"] == "paused"
    assert close["blocked_before"] == "E_close:cleanup_incomplete"
    assert _receipt(tmp_path)["hosted"]["all12_passed"] is False


def _exec_record(spec_id="reg-1", checksum="sha256:exec"):
    return _owned(
        artifact_state="approved", version="1.0.0", checksum=checksum,
        metadata={"chain_id": CHAIN, "strategy_artifact": {"lineage": {"source_strategy_spec_id": spec_id}}},
    )


EXEC_RESOURCES = {"registry_id": "reg-1", "execution_artifact_id": "exec-1",
                  "execution_artifact_version": "1.0.0", "execution_artifact_checksum": "sha256:exec"}


def test_e_close_executed_plan_refused_abort_and_saga_needs_retired_binding(monkeypatch, tmp_path):
    plan = _owned(status="executed")
    owner = FakeOwner({("deployment", "/api/deployment/plans/plan-1"): plan})
    code, _ = owner.request("deployment", "POST", "/api/deployment/plans/plan-1/status", {"status": "aborted"})
    assert code == 400 and plan["status"] == "executed"
    # no retired runtime binding: the saga is never touched
    _runner(tmp_path, "E_close", owner, prior=_close_prior(plan_id="plan-1")).run()
    assert [x for x in owner.log if x[1] != "GET"][1:] == []
    close = _receipt(tmp_path)["hosted"]["phases"]["E_close"]
    assert close["phase_status"] == "paused" and close["retained_history"] == []


def test_e_close_still_aborts_unexecuted_plan(monkeypatch, tmp_path):
    owner = FakeOwner({("deployment", "/api/deployment/plans/plan-1"): _owned(status="approved")})
    _runner(tmp_path, "E_close", owner, prior=_close_prior(plan_id="plan-1")).run()
    assert [(m, p, b) for _s, m, p, b in owner.log if m != "GET"] == [
        ("POST", "/api/deployment/plans/plan-1/status", {"status": "aborted"})]


def test_owner_client_sends_configured_tenant_on_generic_requests(monkeypatch):
    seen = {}
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def read(self): return b"{}"
    monkeypatch.setattr(parent.urllib.request, "urlopen", lambda request, timeout=0: seen.update(request.header_items()) or Resp())
    parent.UrllibOwnerClient({"deployment": "http://d"}, "owner-token", tenant_id=TENANT).request("deployment", "GET", "/api/deployment/plans/p")
    assert seen["X-tenant-id"] == TENANT and seen["Authorization"] == "Bearer owner-token"


def test_e_close_retires_the_derived_execution_artifact_before_its_spec(monkeypatch, tmp_path):
    owner = FakeOwner({
        ("registry", "/api/registry/strategy-artifacts/exec-1"): {"entry": _exec_record()},
        ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": _owned(artifact_state="approved", version=3)},
        ("governance", "/api/governance/approvals/apv-1"): _owned(decision_state="decided", decision="approved", target_id="reg-1", version=3),
    })
    _runner(tmp_path, "E_close", owner, prior=_close_prior(**EXEC_RESOURCES)).run()
    writes = [p for _s, m, p, _b in owner.log if m != "GET"]
    assert writes == [
        "/api/registry/strategy-artifacts/exec-1/advance",
        "/api/registry/strategy-specs/reg-1/advance",
        "/api/governance/approvals/apv-1/revoke",
    ]
    bodies = {p: b for _s, m, p, b in owner.log if m != "GET"}
    assert bodies[writes[0]]["command_key"] != bodies[writes[1]]["command_key"]
    close = _receipt(tmp_path)["hosted"]["phases"]["E_close"]
    assert {"kind": "execution_artifact_id", "id": "exec-1", "http": 200} in close["retired"]
    # the version/checksum references stay labelled residual, not silently dropped
    assert {"execution_artifact_checksum", "execution_artifact_version"} <= set(close["residual_non_retirable"])


@pytest.mark.parametrize("record", [_exec_record(spec_id="someone-elses-spec"), _exec_record(checksum="sha256:other")])
def test_e_close_never_retires_an_execution_artifact_that_is_not_the_chains_derived_one(monkeypatch, tmp_path, record):
    owner = FakeOwner({("registry", "/api/registry/strategy-artifacts/exec-1"): {"entry": record}})
    _runner(tmp_path, "E_close", owner, prior=_close_prior(**EXEC_RESOURCES)).run()
    assert [x for x in owner.log if x[1] != "GET"] == []
    close = _receipt(tmp_path)["hosted"]["phases"]["E_close"]
    assert close["phase_status"] == "paused" and "execution_artifact_id exec-1" in close["rejected_unrelated"][0]


H3_EXEC = dict(
    decision_id="apv-3", decision_state="decided", decision="approved", target_type="registry_entry",
    target_id="exec-1", target_version="1.0.0", candidate_digest="sha256:exec",
    capital_pool_id="pool-1", persona_id="persona-1",
)


def _h3_problems(tmp_path, **override):
    record = {**H3_EXEC, **override}
    owner = FakeOwner({
        ("registry", "/api/registry/strategy-artifacts/exec-1"): {"entry": _exec_record()},
        ("governance", "/api/governance/approvals/apv-3"): _owned(**record),
    })
    runner = _runner(tmp_path, "C_promote", owner, decisions={"H3": {"decision_id": "apv-3"}})
    runner.resource_ids.update(EXEC_RESOURCES, capital_pool_id="pool-1", persona_id="persona-1")
    return runner.verify_predecessors()


def test_exact_h3_for_the_execution_artifact_is_accepted(tmp_path):
    assert not [p for p in _h3_problems(tmp_path) if "H3" in p]


@pytest.mark.parametrize("override", [
    dict(target_id="reg-1"),
    dict(decision="rejected"),
    dict(decision_state="pending"),
    dict(tenant_id="foreign-tenant"),
    dict(persona_id="persona-other"),
    dict(capital_pool_id="pool-other"),
    dict(revoked_at="2026-10-01T00:00:00Z"),
    dict(superseded_by="apv-9"),
    dict(expires_at="2020-01-01T00:00:00Z"),
    dict(target_type="strategy_spec"),
    dict(target_version="9.9.9"),
    dict(candidate_digest="sha256:other"),
    dict(decision_id="apv-other"),
])
def test_h3_not_exactly_covering_the_execution_artifact_is_a_predecessor_problem(tmp_path, override):
    assert any(p.startswith("decision H3") for p in _h3_problems(tmp_path, **override))


def _close_prior(**resources):
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:4]}
    return {"hosted": {"chain_id": CHAIN, "phases": done, "decisions": {"H1": "apv-1"}, "resource_ids": resources}}


def test_e_close_unrelated_registry_blocks_h1_revoke(monkeypatch, tmp_path):
    owner = FakeOwner({
        ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": {"tenant_id": TENANT, "metadata": {"chain_id": "other"}, "artifact_state": "approved"}},
        ("governance", "/api/governance/approvals/apv-1"): _owned(decision_state="decided", decision="approved", target_id="reg-1", version=3),
    })
    _runner(tmp_path, "E_close", owner, prior=_close_prior(registry_id="reg-1")).run()
    assert [x for x in owner.log if x[1] != "GET"] == []
    assert _receipt(tmp_path)["hosted"]["phases"]["E_close"]["phase_status"] == "paused"


def test_e_close_accepted_but_not_retired_is_incomplete(monkeypatch, tmp_path):
    owner = FakeOwner({
        ("capital", "/api/bindings/bnd-1"): {"binding": _owned(status="active")},
    }, stuck=("/api/bindings/bnd-1",))
    _runner(tmp_path, "E_close", owner, prior=_close_prior(capital_binding_id="bnd-1")).run()
    close = _receipt(tmp_path)["hosted"]["phases"]["E_close"]
    assert close["phase_status"] == "paused" and "accepted (200) but state" in close["incomplete_cleanup"][0]
    assert _receipt(tmp_path)["hosted"]["all12_passed"] is False


def test_e_close_completed_receipt_is_not_trusted(monkeypatch, tmp_path):
    prior = _close_prior(capital_binding_id="bnd-1")
    prior["hosted"]["phases"]["E_close"] = {"phase_status": "completed"}
    owner = FakeOwner({("capital", "/api/bindings/bnd-1"): {"binding": _owned(status="active")}})
    _runner(tmp_path, "E_close", owner, prior=prior).run()
    assert [p for _s, m, p, _b in owner.log if m != "GET"] == ["/api/bindings/bnd-1/status"]


def test_h2_consent_is_a_carried_reference_not_a_governance_gate(monkeypatch, tmp_path):
    child = FakeChild({"B_learning": _report("human", "B_learning", "completed", B_KEYS, {})})
    monkeypatch.setattr(subprocess, "run", child)
    done = {"A_research": {"phase_status": "completed"}}
    prior = {"hosted": {"chain_id": CHAIN, "phases": done, "loops": domain_loops("A_research"), "resource_ids": dict(CHAIN_REFS)}}
    owner = FakeOwner(domain_records())
    _runner(tmp_path, "B_learning", owner, prior=prior, decisions={"H2": "consent-rec-1"}).run()
    assert not [x for x in owner.log if x[0] == "governance" or x[1] != "GET"] and child.calls[0]["input"]["decisions"] == {"H2": "consent-rec-1"}


def test_entry_honors_shared_chain_input_packet(monkeypatch, tmp_path):
    packet = tmp_path / "in.json"
    packet.write_text(json.dumps({"chain_id": CHAIN, "phase": "B_learning", "decisions": {"H2": "c-1"},
                                  "predecessors": {"registry_id": "reg-1"}}))
    captured = {}
    class Capture:
        def __init__(self, *_a, **kw): captured.update(kw)
        def run(self): return "ok"
    for k, v in {"PANTHEON_L12_CROSS_LOOP_RUN_ID": CHAIN, "PANTHEON_L12_HOSTED_CHAIN_PHASE": "B_learning",
                 "PANTHEON_L12_TENANT_ID": TENANT, "PANTHEON_L12_HOSTED_CHAIN_INPUT": str(packet),
                 "PANTHEON_L12_EXPECTED_SHA": SHA, "PANTHEON_L12_STIMULUS_EVIDENCE_OUTPUT": str(tmp_path / "e.json")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(parent, "Settings", SimpleNamespace(from_env=lambda: object()))
    monkeypatch.setattr(parent.UrllibOwnerClient, "from_env", classmethod(lambda cls: object()))
    monkeypatch.setattr(parent, "HostedChainRunner", Capture)
    assert parent.run_hosted_phase_from_env() == "ok"
    assert captured["decision_refs"] == {"H2": "c-1"} and captured["predecessors"] == {"registry_id": "reg-1"}
    packet.write_text(json.dumps({"chain_id": "other-chain-0001"}))
    with pytest.raises(parent.StimulusProofError):
        parent.run_hosted_phase_from_env()


def test_all12_requires_every_phase_completed(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", FakeChild({}))
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:4]}
    runner = _runner(tmp_path, "E_close", FakeOwner({}), prior={"hosted": {"chain_id": CHAIN, "phases": done}})
    runner.run()
    hosted = _receipt(tmp_path)["hosted"]
    # no loop evidence carried and no pre-mutation baseline: never all12, cleanup paused honestly
    assert hosted["phase_status"] == "paused" and hosted["all12_passed"] is False
    assert hosted["phases"]["E_close"]["baseline_fleet_check"] == "baseline_not_recorded"


def test_default_isolated_launch_has_no_hosted_environment(monkeypatch, tmp_path):
    child = FakeChild({None: {"git_sha": SHA, "status": "passed"}})
    monkeypatch.setattr(subprocess, "run", child)
    gate = parent.StimulusDrivenClosureGate(_settings(tmp_path))
    gate._run_domain_suite("research", tmp_path / "r.json")
    env = child.calls[0]["env"]
    assert parent.HOSTED_FLAG not in env or env[parent.HOSTED_FLAG] != "1"
    assert "PANTHEON_L12_HOSTED_CHAIN_PHASE" not in env
    assert gate.evidence.hosted is None


def test_completed_phase_reuse_rereads_owner_truth(monkeypatch, tmp_path):
    child = FakeChild({"A_research": _report("research", "A_research", "completed", A_KEYS, {"registry_id": "reg-1"})})
    monkeypatch.setattr(subprocess, "run", child)
    prior = {"hosted": {"chain_id": CHAIN, "resource_ids": {"registry_id": "reg-1"},
                        "decisions": {"H1": "apv-1"},
                        "phases": {"A_research": {"phase_status": "completed"}}}}
    # owner record deleted and approval revoked: old receipt must not stay passed
    owner = FakeOwner({("governance", "/api/governance/approvals/apv-1"): _owned(decision_state="decided", decision="rejected", target_id="reg-1")})
    evidence = _runner(tmp_path, "A_research", owner, prior=prior).run()
    hosted = _receipt(tmp_path)["hosted"]
    assert child.calls == [] and evidence.status == "paused"
    assert hosted["phases"]["A_research"].get("reused") is None
    assert hosted["all12_passed"] is False


def test_legacy_fullchain_test_is_excluded_in_hosted_mode():
    skip = [m for m in parent.test_stimulus_driven_cross_loop_management_closure.pytestmark if m.name == "skipif"][0]
    # the mark condition is evaluated at import; hosted mode must be part of it
    source = PARENT.read_text(encoding="utf-8")
    block = source[source.index("@pytest.mark.skipif", source.index("def test_prebuilt_verifier")):source.index("def test_stimulus_driven_cross_loop")]
    assert "HOSTED_FLAG" in block and skip.args


FLEET = "/api/runtime-fleet/desired-state?stage=paper&include_excluded=true"


def _fleet(count, ids):
    return {"active_count": count, "bindings": [{"binding_id": i} for i in ids]}


def test_paused_partial_case_is_kept_as_progress_not_normalized(monkeypatch, tmp_path):
    report = _report("runtime", "D_observe", "paused", [parent.LOOP_CASES[9][2]], {})
    report["cases"][parent.LOOP_CASES[9][2]]["next_consumer_readback"] = None  # real A1H1-style partial
    monkeypatch.setattr(subprocess, "run", FakeChild({"D_observe": report}))
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:3]}
    prior = {"hosted": {"chain_id": CHAIN, "phases": done, "loops": domain_loops("A_research", "B_learning"), "resource_ids": dict(CHAIN_REFS)}}
    evidence = _runner(tmp_path, "D_observe", FakeOwner(domain_records()), prior=prior).run()
    hosted = _receipt(tmp_path)["hosted"]
    assert evidence.status == "paused" and [l["loop_id"] for l in hosted["loops"]] == ["alpha_replication", "persona_teaching", "human_imitation_shadow_evaluation", "consultation"]
    assert "telemetry_reconciliation" in hosted["phases"]["D_observe"]["partial_cases"]


def test_completed_phase_with_partial_case_is_still_rejected(monkeypatch, tmp_path):
    report = _report("research", "A_research", "completed", A_KEYS, {})
    report["cases"][0]["next_consumer"] = None
    monkeypatch.setattr(subprocess, "run", FakeChild({"A_research": report}))
    with pytest.raises(parent.StimulusProofError, match="next receipt"):
        _runner(tmp_path, "A_research", FakeOwner({})).run()


def test_object_decisions_parse_decision_id_and_forward_h2_mapping(monkeypatch, tmp_path):
    child = FakeChild({"C_promote": _report("runtime", "C_promote", "paused", [], {}, "h3")})
    monkeypatch.setattr(subprocess, "run", child)
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:2]}
    h2 = {"consent_id": "c-1", "recorded_by": "human"}
    decisions = {"H1": {"decision_id": "apv-1"}, "H2": h2, "H3": {"decision_id": "apv-3"}}
    owner = FakeOwner({
        ("governance", "/api/governance/approvals/apv-1"): _owned(decision_state="decided", decision="approved", target_id="reg-1"),
        ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": _owned()},
        ("runtime", FLEET): _fleet(1, ["old"]),
        **domain_records(),
    })
    prior = {"hosted": {"chain_id": CHAIN, "phases": done, "resource_ids": {"registry_id": "reg-1"},
                        "loops": domain_loops("A_research", "B_learning"), "resource_ids": dict(CHAIN_REFS)}}
    _runner(tmp_path, "C_promote", owner, prior=prior, decisions=decisions).run()
    assert ("governance", "GET", "/api/governance/approvals/apv-1", None) in owner.log
    assert child.calls[0]["input"]["decisions"]["H2"] == h2


def test_registry_without_chain_id_is_verified_by_distillation_lineage(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", FakeChild({}))
    src = f"hosted-all12-{CHAIN}-src"
    lineage = {"source_id": src, "source_digest": "sha256:ab"}
    entry = {"tenant_id": TENANT, "metadata": {"distillation": lineage}, "checksum": "ck"}
    prior = {"hosted": {"chain_id": CHAIN, "phases": {"A_research": {"phase_status": "completed"}}, "loops": domain_loops("A_research"),
                        "resource_ids": {**CHAIN_REFS, "source_id": src, "checksum": "ck"}}}
    owner = FakeOwner({**domain_records(), ("registry", "/api/registry/strategy-specs/reg-1"): {"entry": entry}})
    runner = _runner(tmp_path, "A_research", owner, prior=prior)
    assert runner.verify_predecessors() == []
    entry["metadata"]["distillation"] = dict(lineage, source_id=f"hosted-all12-{CHAIN}-other-src")
    assert "not this chain's Source" in runner.verify_predecessors()[0]
    entry["metadata"] = {}
    assert "no source/distillation lineage" in runner.verify_predecessors()[0]


def test_typed_resource_ids_reject_objects_and_paths(tmp_path):
    with pytest.raises(parent.StimulusProofError):
        parent.HostedChainRunner(_settings(tmp_path), chain_id=CHAIN, phase="A_research", tenant_id=TENANT,
                                 owner=FakeOwner({}), predecessors={"capital_pool_id": "../x"})
    with pytest.raises(parent.StimulusProofError):
        parent.HostedChainRunner(_settings(tmp_path), chain_id=CHAIN, phase="A_research", tenant_id=TENANT,
                                 owner=FakeOwner({}), predecessors={"runtime_binding_id": {"a": 1}})


def _close_owner(fleet_after, binding_status="active"):
    return FakeOwner({
        ("capital", "/api/bindings/cb-1"): {"binding": _owned(status=binding_status)},
        ("capital", "/api/capital-pools/cp-1"): {"pool": _owned(status="active")},
        ("runtime", "/api/runtime-bindings/rb-1"): _owned(status="active"),
        ("runtime", FLEET): fleet_after,
    })


def test_baseline_recorded_before_first_mutation_and_restored_at_close(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", FakeChild({"C_promote": _report("runtime", "C_promote", "completed", [parent.LOOP_CASES[7][2], parent.LOOP_CASES[8][2]], {
        "capital_pool_id": "cp-1", "capital_binding_id": "cb-1", "runtime_binding_id": "rb-1"})}))
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:2]}
    owner = FakeOwner({("runtime", FLEET): _fleet(1, ["old"]), **domain_records()})
    _runner(tmp_path, "C_promote", owner, prior={"hosted": {"chain_id": CHAIN, "phases": done, "loops": domain_loops("A_research", "B_learning"), "resource_ids": dict(CHAIN_REFS)}}).run()
    c = _receipt(tmp_path)["hosted"]
    assert c["baseline_fleet"]["active_count"] == 1 and c["baseline_fleet"]["binding_ids"] == ["old"]
    assert c["resource_ids"] == {**CHAIN_REFS, "capital_pool_id": "cp-1", "capital_binding_id": "cb-1", "runtime_binding_id": "rb-1"}
    c["loops"] = [l for l in c["loops"] if l["loop_id"] not in {"alpha_replication", "persona_teaching",
                  "human_imitation_shadow_evaluation", "consultation"}] + domain_loops("A_research", "B_learning")

    c["phases"]["D_observe"] = {"phase_status": "completed"}
    c["resource_ids"] = {k: v for k, v in c["resource_ids"].items() if k not in CHAIN_REFS}  # Registry retire is covered elsewhere
    close = _close_owner(_fleet(1, ["old"]))
    _runner(tmp_path, "E_close", close, prior={"hosted": c}, name="close.json").run()
    phase = _receipt(tmp_path, "close.json")["hosted"]["phases"]["E_close"]
    assert phase["baseline_fleet_check"] == "verified" and phase["phase_status"] == "completed"
    assert {r["kind"] for r in phase["retired"]} == {"capital_binding_id", "runtime_binding_id", "capital_pool_id"}

    drift = _close_owner(_fleet(2, ["old", "stray"]))
    _runner(tmp_path, "E_close", drift, prior={"hosted": c}, name="drift.json").run()
    phase = _receipt(tmp_path, "drift.json")["hosted"]["phases"]["E_close"]
    assert phase["baseline_fleet_check"] == "mismatch" and phase["phase_status"] == "paused"
    assert _receipt(tmp_path, "drift.json")["hosted"]["all12_passed"] is False


def test_unreadable_baseline_pauses_c_before_launch(monkeypatch, tmp_path):
    child = FakeChild({})
    monkeypatch.setattr(subprocess, "run", child)
    done = {p: {"phase_status": "completed"} for p in parent.HOSTED_PHASES[:2]}
    prior = {"hosted": {"chain_id": CHAIN, "phases": done, "loops": domain_loops("A_research", "B_learning"), "resource_ids": dict(CHAIN_REFS)}}
    evidence = _runner(tmp_path, "C_promote", FakeOwner(domain_records()), prior=prior).run()
    assert child.calls == [] and evidence.status == "paused"
    assert _receipt(tmp_path)["hosted"]["blocked_before"] == "C_promote:baseline_unavailable"


def test_opaque_h2_consent_ref_accepted_and_lineage_is_exact(monkeypatch, tmp_path):
    ref = "consent rec/1?x#y"
    runner = parent.HostedChainRunner(_settings(tmp_path), chain_id=CHAIN, phase="A_research", tenant_id=TENANT,
                                      owner=FakeOwner({}), predecessors={"h2_consent_ref": ref})
    assert runner.resource_ids["h2_consent_ref"] == ref
    src = f"hosted-all12-{CHAIN}-src"
    entry = {"tenant_id": TENANT, "metadata": {"distillation": {"source_id": f"{src}-other", "source_digest": "sha256:ab"}}}
    owner = FakeOwner({("registry", "/api/registry/strategy-specs/reg-1"): {"entry": entry}})
    r = parent.HostedChainRunner(_settings(tmp_path), chain_id=CHAIN, phase="A_research", tenant_id=TENANT, owner=owner,
                                 predecessors={"registry_id": "reg-1", "source_id": f"{src}-other"})
    assert "not this chain's Source" in r.verify_predecessors()[0]
    entry["metadata"]["distillation"]["source_id"] = src
    r.resource_ids.update(source_id=src, source_digest="sha256:zz")
    assert "digest differs" in r.verify_predecessors()[0]


def _reuse_prior(*phases):
    done = {p: {"phase_status": "completed"} for p in phases}
    return {"hosted": {"chain_id": CHAIN, "phases": done, "loops": domain_loops(*phases), "resource_ids": dict(CHAIN_REFS)}}


def _reuse(tmp_path, monkeypatch, records, prior, phase="A_research"):
    child = FakeChild({})
    monkeypatch.setattr(subprocess, "run", child)
    owner = FakeOwner(records)
    evidence = _runner(tmp_path, phase, owner, prior=prior).run()
    return child, owner, evidence, _receipt(tmp_path)["hosted"]


def test_completed_a_b_reuse_reads_actual_domain_outputs(monkeypatch, tmp_path):
    child, owner, evidence, hosted = _reuse(tmp_path, monkeypatch, domain_records(), _reuse_prior("A_research", "B_learning"), "B_learning")
    assert child.calls == [] and evidence.status == "passed" and hosted["phases"]["B_learning"]["reused"] is True
    got = {(s, p) for s, m, p, _b in owner.log if m == "GET"}
    assert got == set(domain_records())  # exact A authority run, Teaching session+terminal readback, B run, consult request, handoff
    assert all(m == "GET" for _s, m, _p, _b in owner.log)


@pytest.mark.parametrize("missing", list(domain_records()))
def test_missing_domain_owner_record_pauses_instead_of_reuse(monkeypatch, tmp_path, missing):
    records = {k: v for k, v in domain_records().items() if k != missing}
    child, _owner, evidence, hosted = _reuse(tmp_path, monkeypatch, records, _reuse_prior("A_research", "B_learning"), "B_learning")
    assert child.calls == [] and evidence.status == "paused"
    assert hosted["phases"]["B_learning"].get("reused") is None
    assert "owner GET returned 404" in " ".join(hosted["phases"]["B_learning"]["evidence"])


def _registry_only():
    return {k: v for k, v in domain_records().items() if k[0] == "registry"}


def test_absent_required_domain_refs_pause_even_with_completed_receipt(monkeypatch, tmp_path):
    prior = _reuse_prior("A_research")
    prior["hosted"]["loops"] = []
    child, owner, evidence, hosted = _reuse(tmp_path, monkeypatch, _registry_only(), prior)
    assert child.calls == [] and evidence.status == "paused" and not [x for x in owner.log if x[0] != "registry"]
    assert "loop receipts are missing" in hosted["phases"]["A_research"]["evidence"][0]
    prior["hosted"]["loops"] = domain_loops("A_research")
    for loop in prior["hosted"]["loops"]:
        loop["terminal_output"] = {"id": loop["loop_id"]}
        loop["authority_readback"] = {"id": loop["loop_id"]}
    _c, _o, evidence, hosted = _reuse(tmp_path, monkeypatch, _registry_only(), prior)
    assert evidence.status == "paused"
    assert "lacks required authority_run_id" in " ".join(hosted["phases"]["A_research"]["evidence"])


@pytest.mark.parametrize("path,patch,needle", [
    (("research", f"/api/research-orchestrator/runs/{AUTH}"), {"tenant_id": "other", "parameters": {"tenant_id": "other", "experiment_run": {"run_id": ALPHA, "status": "completed"}}}, "tenant"),
    (("research", f"/api/research-orchestrator/runs/{AUTH}"), {"parameters": {"tenant_id": TENANT, "experiment_run": {"run_id": "other-run", "status": "completed"}}}, "embedded ExperimentRun"),
    (("training", f"/api/training/sessions/{SESS}"), {"context_refs": [{"id": "elsewhere", "domain_run_id": ALPHA}]}, "context_refs"),
    (("training", f"/api/training/sessions/{SESS}/terminal-readback"), {"eval_identity": "different"}, "eval_identity"),
    (("research", f"/api/research-orchestrator/runs/{IMIT_RUN}"), {"task_id": "other-task"}, "run/task identity"),
    (("consultation", f"/api/consult/requests/{REQ}"), {"target_id": "other-run"}, "target is not"),
    (("consultation", f"/api/consult/handoffs/{HANDOFF}"), {"status": "pending"}, "not acknowledged"),
])
def test_mismatched_domain_owner_record_pauses(monkeypatch, tmp_path, path, patch, needle):
    records = domain_records()
    records[path] = {**records[path], **patch}
    child, _owner, evidence, hosted = _reuse(tmp_path, monkeypatch, records, _reuse_prior("A_research", "B_learning"), "B_learning")
    assert child.calls == [] and evidence.status == "paused"
    assert needle in " ".join(hosted["phases"]["B_learning"]["evidence"])


FOREIGN_CASES = [
    # same-tenant foreign-chain outputs that are self-consistent with their own loop receipt
    (("research", f"/api/research-orchestrator/runs/{AUTH}"), "alpha", "is not this chain's Registry"),
    (("consultation", f"/api/consult/requests/{REQ}"), "chain", "chain label"),
    (("consultation", f"/api/consult/requests/{REQ}"), "registry_id", "lineage registry_id"),
    (("consultation", f"/api/consult/requests/{REQ}"), "teaching_session_id", "lineage teaching_session_id"),
    (("consultation", f"/api/consult/requests/{REQ}"), "missing", "lineage dataset_version_id"),
    (("research", f"/api/research-orchestrator/runs/{IMIT_RUN}"), "candidate", "lineage candidate_id"),
]


@pytest.mark.parametrize("path,kind,needle", FOREIGN_CASES)
def test_same_tenant_foreign_chain_outputs_pause(monkeypatch, tmp_path, path, kind, needle):
    records, prior = domain_records(), _reuse_prior("A_research", "B_learning")
    record = records[path]
    if kind == "alpha":
        record["parameters"]["strategy_spec_id"] = "foreign-spec"
        prior["hosted"]["loops"][0]["terminal_output"]["strategy_spec_id"] = "foreign-spec"
    elif kind == "chain":
        record["metadata"]["chain_id"] = "other-chain"
    elif kind == "missing":
        del record["metadata"]["dataset_version_id"]
    elif kind == "candidate":
        record["metadata"]["candidate_id"] = "foreign-cand"
    else:
        record["metadata"][kind] = "foreign"
    child, _owner, evidence, hosted = _reuse(tmp_path, monkeypatch, records, prior, "B_learning")
    assert child.calls == [] and evidence.status == "paused"
    assert needle in " ".join(hosted["phases"]["B_learning"]["evidence"])


def test_owner_client_reads_domain_services_under_their_own_headers(monkeypatch):
    seen = {}
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def read(self): return b"{}"
    def fake_open(request, timeout=0):
        seen[request.full_url] = dict(request.header_items())
        return Resp()
    monkeypatch.setattr(parent.urllib.request, "urlopen", fake_open)
    client = parent.UrllibOwnerClient(
        {"training": "http://t", "registry": "http://r"}, "owner-token",
        service_headers={"training": {"Authorization": "Bearer train", "X-Tenant-Id": TENANT, "X-Pantheon-Service": "training-session-preview-worker"}},
    )
    client.request("training", "GET", "/x")
    client.request("registry", "GET", "/y")
    assert seen["http://t/x"]["Authorization"] == "Bearer train" and seen["http://t/x"]["X-pantheon-service"] == "training-session-preview-worker"
    assert seen["http://r/y"]["Authorization"] == "Bearer owner-token"
    with pytest.raises(parent.StimulusProofError, match="no owner URL"):
        client.request("consultation", "GET", "/z")


def test_owner_client_signs_capital_requests_fresh_with_real_principal(monkeypatch):
    import base64
    seen = []
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def read(self): return b"{}"
    monkeypatch.setattr(parent.urllib.request, "urlopen", lambda request, timeout=0: seen.append(dict(request.header_items())) or Resp())
    issued = []
    def mint():
        claims = base64.urlsafe_b64encode(json.dumps({"sub": "operator-bff", "service": "operator-bff"}).encode()).decode().rstrip("=")
        issued.append(1)
        return f"Bearer h.{claims}.s{len(issued)}"
    client = parent.UrllibOwnerClient(
        {"capital": "http://c", "registry": "http://r"}, "owner-token",
        actor_id="human", roles=("operator",), capital_authorization=mint, tenant_id=TENANT,
    )
    client.request("capital", "GET", "/api/bindings/b")
    client.request("capital", "PATCH", "/api/bindings/b/status", {"status": "revoked"})
    client.request("registry", "GET", "/y")
    assert len(issued) == 2 and seen[0]["Authorization"] != seen[1]["Authorization"]
    assert seen[0]["X-pantheon-service"] == "operator-bff" and seen[1]["X-tenant-id"] == TENANT
    assert seen[2]["Authorization"] == "Bearer owner-token"
    assert client.identity_for("capital")[0] == "operator-bff" and "persona.admin" in client.identity_for("capital")[1]
    assert client.identity_for("registry") == ("human", ("operator",))


def test_owner_client_routes_hosted_capital_only_through_the_bff_entry(monkeypatch):
    seen = []
    class Resp:
        status = 200
        def __init__(self, raw): self.raw = raw
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def read(self): return self.raw
    def fake_open(request, timeout=0):
        seen.append((request.get_method(), request.full_url, dict(request.header_items()), request.data))
        return Resp(json.dumps({"data": {"binding_id": "b", "status": "active"}}).encode())
    monkeypatch.setattr(parent.urllib.request, "urlopen", fake_open)
    client = parent.UrllibOwnerClient(
        {"capital": "http://c", "bff": "http://bff"}, "owner-token",
        capital_authorization=lambda: pytest.fail("no Capital token may be signed or fetched"),
        tenant_id=TENANT, bff_bearer="session-token",
    )
    status, payload = client.request("capital", "GET", "/api/bindings/b")
    assert status == 200 and payload == {"binding": {"binding_id": "b", "status": "active"}}
    assert client.retire_chain_paper_capital("persona-1", "pool-1", "b") == 200
    assert [(m, u) for m, u, _h, _d in seen] == [
        ("GET", "http://bff/api/v1/bindings/b"),
        ("POST", "http://bff/bff/personas/persona-1/provisioning/paper-capital/retire")]
    assert all(h["Authorization"] == "Bearer session-token" for _m, _u, h, _d in seen)
    assert json.loads(seen[1][3]) == {"binding_id": "b", "capital_pool_id": "pool-1"}
    with pytest.raises(parent.StimulusProofError, match="only through the BFF"):
        client.request("capital", "PATCH", "/api/bindings/b/status", {"status": "revoked"})
    assert len(seen) == 2


def test_e_close_retires_hosted_capital_via_bff_entry_binding_before_pool(monkeypatch, tmp_path):
    prior = _close_prior(capital_pool_id="pool-1", capital_binding_id="bnd-1", persona_id="persona-1",
                         runtime_binding_id="rt-1")
    owner = FakeOwner({
        ("capital", "/api/capital-pools/pool-1"): _owned(status="active"),
        ("capital", "/api/bindings/bnd-1"): {"binding": _owned(status="active")},
        ("runtime", "/api/runtime-bindings/rt-1"): {"binding": _owned(status="active")},
    })
    def retire(persona_id, pool_id, binding_id):
        owner.log.append(("bff", "POST", f"retire:{persona_id}:{binding_id}:{pool_id}", None))
        owner.records[("capital", "/api/bindings/bnd-1")]["binding"]["status"] = "revoked"
        owner.records[("capital", "/api/capital-pools/pool-1")]["status"] = "archived"
        return 200
    owner.retire_chain_paper_capital, owner._bff_capital = retire, lambda: True
    _runner(tmp_path, "E_close", owner, prior=prior).run()
    assert [p for _s, m, p, _b in owner.log if m != "GET"] == [
        "/api/runtime-bindings/rt-1/retire", "retire:persona-1:bnd-1:pool-1"]
    kinds = [r["kind"] for r in _receipt(tmp_path)["hosted"]["phases"]["E_close"]["retired"]]
    assert kinds == ["runtime_binding_id", "capital_binding_id", "capital_pool_id"]


def test_fake_owner_uses_the_real_plan_transition_table():
    assert PLAN_TRANSITIONS["executed"] == set() and PLAN_TRANSITIONS["failed"] == set()
    assert PLAN_TRANSITIONS["approved"] == {"executing", "rejected", "aborted"}
    for status, allowed in PLAN_TRANSITIONS.items():
        if status == "aborted":
            continue  # same-state requests are a no-op
        owner = FakeOwner({("deployment", "/api/deployment/plans/p"): _owned(status=status)})
        code, _ = owner.request("deployment", "POST", "/api/deployment/plans/p/status", {"status": "aborted"})
        assert (code == 200) == ("aborted" in allowed), status


def test_e_close_retires_runtime_binding_then_capital_binding_then_pool(monkeypatch, tmp_path):
    prior = _close_prior(capital_pool_id="pool-1", capital_binding_id="bnd-1", runtime_binding_id="rt-1")
    owner = FakeOwner({
        ("capital", "/api/capital-pools/pool-1"): _owned(status="active"),
        ("capital", "/api/bindings/bnd-1"): {"binding": _owned(status="active")},
        ("runtime", "/api/runtime-bindings/rt-1"): {"binding": _owned(status="active")},
    })
    _runner(tmp_path, "E_close", owner, prior=prior).run()
    assert [p for _s, m, p, _b in owner.log if m != "GET"] == [
        "/api/runtime-bindings/rt-1/retire", "/api/bindings/bnd-1/status", "/api/capital-pools/pool-1/status"]


@pytest.mark.parametrize("state", ["failed", "rejected", "aborted"])
def test_e_close_dead_end_plan_states_are_not_sent_a_transition(monkeypatch, tmp_path, state):
    owner = FakeOwner({("deployment", "/api/deployment/plans/plan-1"): _owned(status=state)})
    _runner(tmp_path, "E_close", owner, prior=_close_prior(plan_id="plan-1")).run()
    assert [x for x in owner.log if x[1] != "GET"] == []
    assert _receipt(tmp_path)["hosted"]["phases"]["E_close"]["retired"][0]["already"] is True


def test_failed_child_still_leaves_created_ids_in_the_parent_receipt(monkeypatch, tmp_path):
    def crashing_child(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        Path(kwargs["env"][parent.HOSTED_IDS_PATH_ENV]).write_text(
            json.dumps({"capital_pool_id": "pool-1", "capital_binding_id": "bnd-1", "plan_id": ["bad/id"]}))
        return SimpleNamespace(returncode=1, stdout="boom", stderr="")

    monkeypatch.setattr(subprocess, "run", crashing_child)
    prior = {"hosted": {"chain_id": CHAIN, "resource_ids": dict(CHAIN_REFS), "loops": domain_loops("A_research", "B_learning"),
                        "phases": {p: {"phase_status": "completed"} for p in ("A_research", "B_learning")}}}
    owner = FakeOwner({("runtime", FLEET): _fleet(1, ["old"]), **domain_records()})
    with pytest.raises(parent.StimulusProofError, match="exited 1"):
        _runner(tmp_path, "C_promote", owner, prior=prior).run()
    hosted = _receipt(tmp_path)["hosted"]
    assert hosted["phase_status"] == "failed"
    assert hosted["resource_ids"] == {**CHAIN_REFS, "capital_pool_id": "pool-1", "capital_binding_id": "bnd-1"}

    # ...and E_close, which no longer needs C/D to have completed, retires them dependants-first
    owner = FakeOwner({
        ("capital", "/api/capital-pools/pool-1"): _owned(status="active"),
        ("capital", "/api/bindings/bnd-1"): {"binding": _owned(status="active")},
    })
    _runner(tmp_path, "E_close", owner, prior=_receipt(tmp_path), name="close.json").run()
    assert [p for _s, m, p, _b in owner.log if m != "GET"] == [
        "/api/bindings/bnd-1/status", "/api/capital-pools/pool-1/status"]
    assert _receipt(tmp_path, "close.json")["hosted"]["all12_passed"] is False


def test_persisted_ids_flush_on_every_change(tmp_path):
    module = _runtime_module()
    target = tmp_path / "ids.json"
    ids = module.PersistedIds(str(target))
    ids["capital_pool_id"] = "pool-1"
    assert json.loads(target.read_text()) == {"capital_pool_id": "pool-1"}
    ids.update(plan_id="plan-1")
    assert json.loads(target.read_text()) == {"capital_pool_id": "pool-1", "plan_id": "plan-1"}


def _runtime_module():
    sys.path.insert(0, str(PARENT.parent))
    sys.modules.pop("l12_owner_auth", None)
    spec = importlib.util.spec_from_file_location(
        "runtime_under_test", PARENT.parent / "test_current_runtime_loops_deployed_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_c_promote_resume_after_redeploy_reuses_the_pinned_execution_artifact(monkeypatch):
    module = _runtime_module()
    import l12_owner_auth
    from services.registry import strategy_artifact

    def derive(_entry, *, artifact_id, algorithm_commit, requested_by):
        return {"strategy_artifact": {"version": "1.0.0", "commit": algorithm_commit}, "metadata": {}}

    # the real checksum embeds the deriving SHA, so two deployed SHAs give two checksums
    monkeypatch.setattr(strategy_artifact, "build_execution_artifact_registration", derive)
    monkeypatch.setattr(strategy_artifact, "strategy_artifact_checksum", lambda art: f"sha256:{art['commit']}")
    monkeypatch.setattr(module, "human_token", lambda _name: "tok")
    monkeypatch.setattr(module, "bearer", lambda _tok: {})
    monkeypatch.setattr(module.RuntimeChain, "_identity", lambda self, service: {})
    monkeypatch.setattr(module.RuntimeChain, "_governance_read", lambda self, decision_id: None)  # H3 undecided
    monkeypatch.setattr(
        module.RuntimeChain, "_hosted_capital",
        lambda self, persona_id, ids: {"capital_pool_id": "pool-1", "persona_id": persona_id, "capital_binding_id": "bnd-1"})

    class Http:
        def __init__(self) -> None:
            self.artifact: dict[str, Any] | None = None
            self.posts: list[str] = []

        def request(self, service, method, path, *, body=None, **_kw):
            if path.startswith("/api/registry/strategy-specs/"):
                return {"entry": {"registry_id": "reg-1", "artifact_state": "approved", "checksum": "sha256:spec",
                                  "approval_decision_id": "apv-1", "strategy_id": "s-1"}}
            if method == "POST" and path == "/api/registry/strategy-artifacts":
                self.posts.append(body["strategy_artifact"]["commit"])
                self.artifact = {"registry_id": CHAIN_EXEC, "artifact_type": "execution_bundle", "version": "1.0.0",
                                 "checksum": f"sha256:{body['strategy_artifact']['commit']}", "artifact_state": "candidate",
                                 "metadata": {"strategy_artifact": {"lineage": {"source_strategy_spec_id": "reg-1"}}}}
                return {"entry": self.artifact}
            if path.startswith("/api/registry/strategy-artifacts/"):
                return {"entry": self.artifact} if self.artifact else {}
            raise AssertionError(f"unexpected {method} {path}")

    chain_id = "hosted-all12-chain-0001"
    CHAIN_EXEC = l12_owner_auth.hosted_id(chain_id, "execution-artifact")
    http = Http()
    predecessors = {"registry_id": "reg-1", "checksum": "sha256:spec", "governance_handoff_id": "ho-1"}
    decisions = {"H1": {"decision_id": "apv-1"}}

    def run(sha: str, pinned: dict[str, Any]) -> dict[str, Any]:
        chain = module.RuntimeChain.__new__(module.RuntimeChain)
        chain.settings = SimpleNamespace(expected_sha=sha)
        chain.http, chain.evidence = http, SimpleNamespace(identities={})
        hosted = l12_owner_auth.HostedChainInput(chain_id, "C_promote", {**predecessors, **pinned}, decisions)
        ids: dict[str, Any] = {}
        with pytest.raises(l12_owner_auth.HostedPause, match="H3") as paused:
            chain._hosted_promote(hosted, ids)
        assert paused.value.blocked_before == "execution_artifact_approval"
        return ids

    first = run("a" * 40, {})
    assert first["execution_artifact_checksum"] == "sha256:" + "a" * 40 and first["execution_artifact_id"] == CHAIN_EXEC
    # redeployed to another SHA: the registered artifact stays the chain's identity
    second = run("c" * 40, {k: v for k, v in first.items() if k.startswith("execution_artifact")})
    assert second["execution_artifact_checksum"] == first["execution_artifact_checksum"]
    assert http.posts == ["a" * 40]  # never re-derived or re-registered under the new SHA
    # a pin that disagrees with the registered artifact is refused, not silently replaced
    with pytest.raises(module.DeployedProofError, match="pinned"):
        run("c" * 40, {"execution_artifact_checksum": "sha256:other"})


def test_hosted_d_module_cases_accept_the_recorded_incident_and_typed_health(monkeypatch):
    module = _runtime_module()
    monkeypatch.setattr(module, "hosted_chain_enabled", lambda: True)
    typed = {"runtime-manager": True, "paper-fleet-reconciler": True}
    results = {"hosted": {"phase": "D_observe"}, "cases": {
        "loop_10_telemetry_reconciliation_incident": {
            "terminal_output_id": "inc-1", "authority_readback": {"incident_id": "inc-1"},
            "assertions": {"incident_id": "inc-1", "real_runtime_event_preserved": True}},
        "loop_11_evolution_decision": {
            "trigger_id": "inc-1", "authority_readback": {"linked_incident_id": "inc-1"},
            "assertions": {"proposal_only": True}},
        "loop_12_bff_typed_health": {
            "authority_readback": {"targets": typed}, "next_consumer_readback": {"typed_health": typed},
            "assertions": {"typed_targets_ready": True}},
    }}
    module.test_loop_10_runtime_event_creates_drift_and_incident(results)
    module.test_loop_11_incident_creates_evolution_decision(results)
    module.test_loop_12_bff_reads_typed_worker_and_api_health(results)
    # the isolated shape is still required when not hosted
    monkeypatch.setattr(module, "hosted_chain_enabled", lambda: False)
    with pytest.raises(AssertionError):
        module.test_loop_10_runtime_event_creates_drift_and_incident(results)
