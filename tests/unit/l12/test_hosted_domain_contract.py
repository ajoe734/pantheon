"""Hosted-chain behaviour of the three L12 domain suites, against in-memory fake owners.

Focus (HOSTED-ALL12-DOMAIN-PHASES-20261023): pause before the mutation a human decision
covers, genuine content/reference propagation across suites, hosted exclusion of shared
infrastructure cases, and unchanged default (isolated) behaviour. No owner, Compose
service or provider is contacted.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import inspect
import json
import sys
import urllib.parse
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

L12_DIR = Path(__file__).resolve().parents[2] / "integration" / "l12"
CHAIN = "chain-x"
TENANT = "tenant-hosted"
SHA = "a" * 40
EXECUTOR_TOKEN = ".".join(
    base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
    for part in ({"alg": "none"}, {"sub": "executor-1", "roles": ["operator"]}, "sig")
)
CAPITAL_JWT = ".".join(
    base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
    for part in ({"alg": "none"}, {"sub": "svc-capital", "service": "svc-capital",
                                    "delegated_actor_id": "operator-real"}, "sig")
)


def _load(monkeypatch: pytest.MonkeyPatch, filename: str, alias: str) -> Any:
    monkeypatch.syspath_prepend(str(L12_DIR))
    monkeypatch.setenv("PANTHEON_L12_TENANT_ID", TENANT)
    sys.modules.pop("l12_owner_auth", None)
    spec = importlib.util.spec_from_file_location(alias, L12_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, alias, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def auth(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(L12_DIR))
    sys.modules.pop("l12_owner_auth", None)
    import l12_owner_auth

    return l12_owner_auth


def _hosted_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
    *,
    predecessors: Mapping[str, Any] | None = None,
    decisions: Mapping[str, Any] | None = None,
) -> None:
    input_path = tmp_path / "chain-input.json"
    input_path.write_text(
        json.dumps({"chain_id": CHAIN, "phase": phase, "predecessors": predecessors or {}, "decisions": decisions or {}})
    )
    for key, value in {
        "PANTHEON_L12_HOSTED_CHAIN": "1",
        "PANTHEON_L12_CROSS_LOOP_RUN_ID": CHAIN,
        "PANTHEON_L12_HOSTED_CHAIN_PHASE": phase,
        "PANTHEON_L12_HOSTED_CHAIN_INPUT": str(input_path),
        "PANTHEON_L12_HOSTED_EXECUTOR_TOKEN": EXECUTOR_TOKEN,
        "PANTHEON_L12_REPORT_PATH": str(tmp_path / "report.json"),
        "PANTHEON_L12_TRAINING_TOKEN": "training-token",
        "PANTHEON_L12_AGORA_HANDOFF_TOKEN": "agora",
        "PANTHEON_L12_POLICY_LEARNING_TOKEN": "policy",
        "PANTHEON_L12_CONSULTATION_TOKEN": "consult",
        "PANTHEON_L12_BFF_BEARER": "bff-real",
    }.items():
        monkeypatch.setenv(key, value)
    # the harness-minted tokens must be ignored in hosted mode
    monkeypatch.setenv("PANTHEON_L12_OPERATOR_TOKEN", "minted.operator.token")
    monkeypatch.setenv("PANTHEON_L12_REVIEWER_TOKEN", "minted.reviewer.token")


# --------------------------------------------------------------------------- shared auth seam


def test_default_mode_keeps_harness_minted_tokens_and_hosted_rejects_them(
    auth: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PANTHEON_L12_HOSTED_CHAIN", raising=False)
    monkeypatch.setenv("PANTHEON_L12_OPERATOR_TOKEN", "minted.operator.token")
    assert auth.human_token("OPERATOR") == "minted.operator.token"

    monkeypatch.setenv("PANTHEON_L12_HOSTED_CHAIN", "1")
    monkeypatch.delenv("PANTHEON_L12_HOSTED_EXECUTOR_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="HOSTED_EXECUTOR_TOKEN"):
        auth.human_token("OPERATOR")
    with pytest.raises(auth.HostedDecisionNotCallerOwned):
        auth.human_token("REVIEWER")
    monkeypatch.setenv("PANTHEON_L12_HOSTED_EXECUTOR_TOKEN", "real.executor.token")
    assert auth.human_token("OPERATOR") == "real.executor.token"

    with pytest.raises(auth.HostedDecisionNotCallerOwned):
        auth.approve_registry_entry(
            lambda *a: pytest.fail("a hosted caller must not post an approval"),
            decision_id="d", entry={}, tenant_id=TENANT, rationale="r",
        )


def test_hosted_input_rejects_credentials_approval_flags_and_foreign_phase(
    auth: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _hosted_env(monkeypatch, tmp_path, "A_research", decisions={"H1": {"decision_id": "apv-1", "approved": True}})
    with pytest.raises(auth.HostedInputError, match="approval flag"):
        auth.load_hosted_input(("A_research",))
    _hosted_env(monkeypatch, tmp_path, "A_research", predecessors={"mfa_verified": True})
    with pytest.raises(auth.HostedInputError, match="credential"):
        auth.load_hosted_input(("A_research",))
    _hosted_env(monkeypatch, tmp_path, "E_close")
    with pytest.raises(auth.HostedInputError, match="not run by this suite"):
        auth.load_hosted_input(("A_research",))


def test_require_decided_approval_pauses_unless_the_exact_decision_is_decided(auth: Any) -> None:
    decided = {
        "decision_id": "apv-1", "tenant_id": TENANT, "decision_state": "decided", "decision": "approved",
        "target_id": "reg-1", "candidate_digest": "sha-1", "version": 3, "actor_id": "human-1",
        "expires_at": "2999-01-01T00:00:00Z",
    }
    kwargs = dict(point="H1", blocked_before="alpha_admission", tenant_id=TENANT,
                  target_ids=("reg-1",), candidate_digest="sha-1")
    ref = {"decision_id": "apv-1"}
    proof = auth.require_decided_approval(lambda _id: decided, ref, **kwargs)
    assert proof["decision_id"] == "apv-1" and proof["decision_maker_actor_id"] == "human-1"
    for read, reference in (
        (lambda _id: None, ref),
        (lambda _id: decided, None),
        (lambda _id: {**decided, "decision_state": "under_review", "decision": None}, ref),
        (lambda _id: {**decided, "target_id": "reg-other"}, ref),
        (lambda _id: {**decided, "candidate_digest": "sha-other"}, ref),
        (lambda _id: {**decided, "tenant_id": "other"}, ref),
        (lambda _id: {**decided, "revoked_at": "2026-10-01T00:00:00Z"}, ref),
        (lambda _id: {**decided, "expires_at": "2000-01-01T00:00:00Z"}, ref),
    ):
        with pytest.raises(auth.HostedPause) as paused:
            auth.require_decided_approval(read, reference, **kwargs)
        assert paused.value.blocked_before == "alpha_admission"


# --------------------------------------------------------------------------- research (A_research)


class FakeResearchOwners:
    """Registry, Governance, Source, Research and Training behind one fake HTTP seam."""

    def __init__(self, mod: Any, *, entry_state: str = "draft", decided: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.sid = f"hosted-all12-{CHAIN}-src"
        self.cid = f"hosted-all12-{CHAIN}-conn"
        self.source = {
            "source_id": self.sid, "connector_id": self.cid, "source_type": "internal_note",
            "title": "t", "content_ref": "urn:x", "metadata": {"tenant_id": TENANT},
        }
        self.digest = mod._source_digest(self.source)
        self.registry_id = f"reg-strategy-spec-{self.sid}-{self.digest.removeprefix('sha256:')[:12]}"
        self.entry: dict[str, Any] = {
            "registry_id": self.registry_id, "strategy_id": "strat-1", "artifact_state": entry_state,
            "checksum": "sha-1", "version": 1, "updated_at": "t0",
            "approval_decision_id": "apv-1" if entry_state == "approved" else None,
            "metadata": {"distillation": {"source_id": self.sid, "source_digest": self.digest,
                                           "distillation_job_id": "dj-1"},
                         "strategy_spec": {"metadata": {"execution_recipe": dict(mod.HOSTED_EXECUTION_RECIPE)}}},
        }
        self.decisions: dict[str, dict[str, Any]] = {}
        if decided:
            self.decisions["apv-1"] = {
                "decision_id": "apv-1", "tenant_id": TENANT, "decision_state": "decided",
                "decision": "approved", "target_id": self.registry_id, "candidate_digest": "sha-1",
                "version": 3, "actor_id": "human-1", "expires_at": "2999-01-01T00:00:00Z",
            }
        self.admission: dict[str, Any] | None = None
        if entry_state == "approved":
            self.admission = {"admission_id": "adm-1", "strategy_spec_id": self.registry_id}
        self.sessions: list[dict[str, Any]] = []

    def __call__(self, base_url: str, path: str, *, method: str = "GET", payload: Any = None,
                 headers: Any = None, expected: Any = (200,)) -> Any:
        self.calls.append((method, path))
        unquoted = urllib.parse.unquote(path)
        if "/api/source-ingest/source-records/" in path:
            return {"source_record": self.source}
        if path.endswith("/controller/readback"):
            return {"schema_version": "v", "connectors": [
                {"connector_id": self.cid, "latest_source_record": {"source_id": self.sid}, "schedule": None}]}
        if path.endswith("/advance"):
            self.entry.update(artifact_state=payload["target_state"], version=self.entry["version"] + 1,
                              approval_decision_id=payload.get("approval_decision_id") or self.entry["approval_decision_id"])
            return {"entry": dict(self.entry)}
        if "/api/registry/strategy-specs/" in path:
            return {"entry": dict(self.entry)}
        if "/api/governance/approvals/" in path:
            return self.decisions.get(unquoted.rsplit("/", 1)[-1], {"detail": "not found"})
        if "/alpha-replication/admissions" in path:
            if method == "POST":
                self.admission = {"admission_id": "adm-1", "strategy_spec_id": payload["strategy_spec_id"]}
                return self.admission
            return self.admission or {"detail": "not found"}
        if path == "/api/training/sessions" and method == "POST":
            session = {"session_id": "trn-new", "persona_id": payload["persona_id"],
                       "trace_id": payload["trace_id"], "context_refs": payload["context_refs"]}
            self.sessions.append(session)
            return session
        if path.startswith("/api/training/sessions?"):
            return list(self.sessions)
        if path.startswith("/api/training/sessions/"):
            return next(s for s in self.sessions if s["session_id"] == path.rsplit("/", 1)[-1])
        if path == "/api/research-orchestrator/runs":
            return [{"run_id": "auth-run-1", "parameters": {
                "record_type": "ExperimentRun", "strategy_spec_id": self.registry_id,
                "experiment_run": {"run_id": "exp-1", "strategy_spec_id": self.registry_id, "status": "completed"}}}]
        if path.startswith("/api/research-orchestrator/runs/"):
            run_id = unquoted.rsplit("/", 1)[-1]
            return self.alpha_run(run_id, "exp-" + run_id.rsplit("-", 1)[-1])
        raise AssertionError(f"unexpected owner call {method} {path}")

    def alpha_run(self, run_id: str = "auth-run-1", domain: str = "exp-1") -> dict[str, Any]:
        return {"run_id": run_id, "parameters": {
            "record_type": "ExperimentRun", "strategy_spec_id": self.registry_id,
            "experiment_run": {"run_id": domain, "strategy_spec_id": self.registry_id, "status": "completed"}}}

    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] != "GET"]


def _research(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owners_kwargs: dict[str, Any] | None = None,
              predecessors: Mapping[str, Any] | None = None, decided: bool = False,
              entry_state: str = "draft") -> tuple[Any, Any, FakeResearchOwners]:
    mod = _load(monkeypatch, "test_current_research_loops_deployed_e2e.py", "hosted_research_mod")
    decisions = {"H1": {"decision_id": "apv-1"}} if decided else {}
    _hosted_env(monkeypatch, tmp_path, "A_research", predecessors=predecessors, decisions=decisions)
    harness = mod.DeployedResearchHarness()
    owners = FakeResearchOwners(mod, entry_state=entry_state, decided=decided)
    harness._http_json = owners
    harness._service_identity = lambda service: {"compose_service": service}
    owners.persona_calls = []
    harness._ensure_persona = lambda pid, **k: owners.persona_calls.append((pid, len(owners.calls)))
    harness.poll_seconds, harness.timeout_seconds = 0.001, 1
    return mod, harness, owners


def test_research_a1_returns_real_draft_then_pauses_before_alpha_without_h1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, harness, owners = _research(monkeypatch, tmp_path)
    with pytest.raises(pytest.skip.Exception, match="alpha_admission"):
        harness.run_case(2)
    assert owners.writes() == [], "pause must precede every owner mutation"
    receipt = json.loads((tmp_path / "report.json").read_text())
    assert receipt["status"] == "paused" and receipt["phase_status"] == "paused"
    assert receipt["blocked_before"] == "alpha_admission"
    assert receipt["chain_id"] == CHAIN and receipt["phase"] == "A_research"
    assert receipt["resource_ids"]["registry_id"] == owners.registry_id
    assert receipt["resource_ids"]["checksum"] == "sha-1"
    assert receipt["resource_ids"]["source_id"] == owners.sid
    assert owners.sid.startswith("hosted-all12-chain-x-")


def test_hosted_source_declares_supported_momentum_recipe_and_isolated_default_stays_pairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, harness, owners = _research(monkeypatch, tmp_path)
    hosted_seed, default_seed = mod._strategy_seed(True), mod._strategy_seed(False)
    assert hosted_seed["execution_recipe"] == mod.HOSTED_EXECUTION_RECIPE
    assert mod.HOSTED_EXECUTION_DATA_SOURCE in hosted_seed["required_data"]
    assert "pairs" in default_seed["hypothesis"] and "execution_recipe" not in default_seed

    # the recipe the owner read back must equal the Source's; any drift stops the phase
    owners.entry["metadata"]["strategy_spec"]["metadata"]["execution_recipe"]["order_quantity"] = 5
    with pytest.raises(RuntimeError, match="execution_recipe"):
        harness.run_case(2)
    assert owners.writes() == []


def test_research_a2_reuses_exact_registry_id_and_checksum_with_h1_and_never_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, harness, owners = _research(
        monkeypatch, tmp_path, decided=True, predecessors={"checksum": "sha-1"}
    )
    harness.run_case(2)
    advances = [c for c in owners.writes() if c[1].endswith("/advance")]
    assert len(advances) == 2
    assert owners.entry["artifact_state"] == "approved" and owners.entry["approval_decision_id"] == "apv-1"
    assert [c for c in owners.writes() if "admissions" in c[1]] == [("POST", "/api/research-orchestrator/alpha-replication/admissions")]
    assert harness.chain["admission"]["strategy_spec_id"] == owners.registry_id
    assert harness.chain["h1_decision_id"] == "apv-1"

    # the worker-default Persona for the actual strategy is ensured after H1's approved advance and
    # before Alpha admission, and the same id is the chain's Persona handed to Teaching
    sid = owners.entry["strategy_id"]
    assert harness.chain["persona_id"] == f"persona-{sid}"
    first = owners.persona_calls[0]
    assert first[0] == f"persona-{sid}"
    approved_at = max(i for i, c in enumerate(owners.calls) if c[1].endswith("/advance"))
    admit_at = next(i for i, c in enumerate(owners.calls) if c == ("POST", "/api/research-orchestrator/alpha-replication/admissions"))
    assert approved_at < first[1] <= admit_at
    assert harness._hosted_resource_ids()["persona_id"] == f"persona-{sid}"

    # a separate process of the same chain: everything exists, so nothing is written again
    mod2, second, owners2 = _research(
        monkeypatch, tmp_path, decided=True, predecessors={"checksum": "sha-1"}, entry_state="approved"
    )
    second.run_case(2)
    assert owners2.writes() == []


def test_research_rejects_a_predecessor_checksum_that_drifted_from_the_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, harness, owners = _research(monkeypatch, tmp_path, decided=True, predecessors={"checksum": "sha-other"})
    with pytest.raises(pytest.fail.Exception, match="checksum drifted"):
        harness.run_case(2)
    assert owners.writes() == []


def test_research_reuses_the_chain_teaching_session_instead_of_opening_another(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, harness, owners = _research(monkeypatch, tmp_path, decided=True, entry_state="approved",
                                   predecessors={"checksum": "sha-1"})
    persona = f"persona-{owners.entry['strategy_id']}"
    owners.sessions.append({
        "session_id": "trn-prior", "persona_id": persona, "trace_id": f"hosted-all12-{CHAIN}-trace-teaching",
        "context_refs": [{"type": "experiment_run", "id": "auth-run-1", "domain_run_id": "exp-1",
                          "strategy_spec_id": owners.registry_id}],
    })
    harness.run_case(3)
    assert ("POST", "/api/training/sessions") not in owners.calls
    assert harness.chain["session"]["session_id"] == "trn-prior"
    assert harness._hosted_resource_ids()["teaching_session_id"] == "trn-prior"


def test_research_reuses_the_alpha_worker_teaching_session_with_a_different_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(ref: dict[str, Any]) -> tuple[Any, Any]:
        _, harness, owners = _research(monkeypatch, tmp_path, decided=True, entry_state="approved",
                                       predecessors={"checksum": "sha-1"})
        owners.sessions.append({
            "session_id": "trn-worker", "persona_id": f"persona-{owners.entry['strategy_id']}",
            "trace_id": "trace-teaching-worker-default",
            "context_refs": [{"type": "experiment_run", **ref}],
        })
        harness.run_case(3)
        return harness, owners

    harness, owners = run({"id": "auth-run-1", "domain_run_id": "exp-1", "strategy_spec_id": owners_registry(monkeypatch, tmp_path)})
    assert ("POST", "/api/training/sessions") not in owners.calls
    assert harness.chain["session"]["session_id"] == "trn-worker"

    # a later worker receipt (replay changed the causal run ids) is used, not the first historical run
    later, owners = run({"id": "auth-run-2", "domain_run_id": "exp-2", "strategy_spec_id": owners_registry(monkeypatch, tmp_path)})
    assert ("POST", "/api/training/sessions") not in owners.calls
    assert later.chain["experiment_run"]["run_id"] == "exp-2"
    assert later.chain["authority_run"]["run_id"] == "auth-run-2"


def test_research_pauses_without_alternate_teaching_post_when_worker_receipt_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for refs in ([], [{"type": "experiment_run", "id": "auth-run-1", "domain_run_id": "exp-1",
                       "strategy_spec_id": "reg-other"}]):
        _, harness, owners = _research(monkeypatch, tmp_path, decided=True, entry_state="approved",
                                       predecessors={"checksum": "sha-1"})
        owners.sessions.append({"session_id": "trn-x", "persona_id": f"hosted-all12-{CHAIN}-persona",
                                "trace_id": "t", "context_refs": refs})
        with pytest.raises(pytest.skip.Exception, match="teaching_session"):
            harness.run_case(3)
        assert ("POST", "/api/training/sessions") not in owners.calls
        receipt = json.loads((tmp_path / "report.json").read_text())
        assert receipt["status"] == "paused" and receipt["blocked_before"] == "teaching_session"
        assert receipt["resource_ids"]["registry_id"] == owners.registry_id


def owners_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    return _research(monkeypatch, tmp_path)[2].registry_id


# --------------------------------------------------------------------------- human learning (B_learning)


def _terminal(validation: str = "passed", candidate: bool = True) -> dict[str, Any]:
    terminal = {
        "session_id": "trn-1", "persona_id": "persona-teach", "status": "active", "eval_identity": "eid-1",
        "started_at": "2026-10-23T01:00:00Z", "updated_at": "2026-10-23T02:00:00Z",
        "evaluation_result": {"eval_id": "teval-1", "session_id": "trn-1", "persona_id": "persona-teach",
                              "eval_identity": "eid-1", "validation_status": validation, "validation_errors": [],
                              "metrics": {"total_return": 0.12, "sharpe_ratio": 1.4, "max_drawdown": -0.07}},
    }
    if candidate:
        controls = [{"parameter_key": "short_window", "current_value": 5},
                    {"parameter_key": "long_window", "current_value": 30}]
        digest = hashlib.sha256(json.dumps(controls, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        terminal["preview"] = {"candidate_snapshot_at": "2026-10-23T01:30:00Z", "preview_quality": "vectorbt",
                               "evaluation_proof": {"session_id": "trn-1", "job_id": "job-1", "eval_id": "teval-1",
                                                    "proof_digest": "pd-1", "governance_gate_state": validation,
                                                    "candidate_digest": "abc", "controls": controls,
                                                    "controls_digest": digest,
                                                    "backend": {"framework": "vectorbt", "run_id": "vbt-1"},
                                                    "authority": {"dataset": {"dataset_version_id": "d", "digest": "s"},
                                                                  "policy": {"policy_id": "p", "digest": "s"}}}}
    return terminal


def _edit(actor: str = "executor-1", actor_type: str = "user", event_id: str = "tevt-1") -> dict[str, Any]:
    return {"event_id": event_id, "event_type": "control_patch", "actor": actor, "actor_type": actor_type,
            "timestamp": "2026-10-23T01:20:00Z",
            "patch_delta": [{"parameter_key": "short_window", "previous_value": 7, "new_value": 5},
                            {"parameter_key": "long_window", "previous_value": 21, "new_value": 30}]}


EVENTS = [_edit()]
OPERATOR = {"sub": "executor-1", "roles": ["operator"]}


def _content(human: Any, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(terminal=_terminal(), events=EVENTS, operator=OPERATOR,
                                  registry_id="reg-1", strategy_id="strat-1", checksum="sha-1")
    return human.teaching_agora_content(**(kwargs | over))


HUMAN = "human-session-9"
PRE_EDIT = {"eval_id": "teval-0", "eval_identity": "eid-0", "evaluated_at": "2026-10-23T01:10:00Z",
            "metrics": {"total_return": 0.01, "sharpe_ratio": 0.2, "max_drawdown": -0.2}}


def _hosted_content(human: Any, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(events=[_edit(actor=HUMAN)], hosted=True, executor_subject="executor-1",
                                  service_subjects=["operator-bff"], pre_edit_evaluation=PRE_EDIT)
    return _content(human, **(kwargs | over))


def test_hosted_research_never_patches_controls_and_pauses_until_a_human_edit_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, research, _ = _research(monkeypatch, tmp_path)
    calls: list[tuple[str, str]] = []
    research.chain["session"] = {"session_id": "trn-1", "persona_id": "persona-teach"}
    research._current_case = {}
    state = {"events": []}

    def http(_u: str, path: str, *, method: str = "GET", payload: Any = None, headers: Any = None, **_: Any) -> Any:
        calls.append((method, path))
        if "/controls/" in path:
            return {"controls": [{"parameter_key": "short_window", "current_value": 5},
                                 {"parameter_key": "long_window", "current_value": 30}]}
        if path.endswith("/events"):
            return state["events"]
        if path.endswith("/terminal-readback"):
            return _terminal()
        if path.endswith("/preview-jobs"):
            return {"job_id": "job-1", "eval_id": "teval-1"}
        if "/preview-jobs/" in path:
            return {"job_id": "job-1", "session_id": "trn-1", "status": "completed", "eval_id": "teval-1"}
        raise AssertionError(f"unexpected call {method} {path}")

    research._http_json = http
    with pytest.raises(sys.modules["l12_owner_auth"].HostedPause) as paused:
        research._case_persona_teaching({})
    assert paused.value.blocked_before == "teaching_human_control_edit"
    assert "awaiting_human_edit" in paused.value.detail and "Management UI" in paused.value.detail
    assert paused.value.resource_ids["pre_edit_evaluation"]["eval_id"] == "teval-1"
    state["events"] = [_edit(actor=HUMAN)]
    research._case_persona_teaching({})
    assert not [c for c in calls if c[0] == "PATCH"], "hosted mode must never write Teaching controls"
    assert research.chain["pre_edit_evaluation"]["eval_id"] == "teval-1"


def test_isolated_research_still_patches_controls_with_the_operator_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, research, _ = _research(monkeypatch, tmp_path)
    research.hosted = False
    research._current_case = {}
    seen: dict[str, Any] = {}
    research._http_json = lambda _u, path, *, method="GET", payload=None, headers=None, **_: (
        seen.update(patch=(headers, payload)) or {"status": "accepted", "patch_delta": _edit()["patch_delta"]})
    wanted = research._operator_teaching_patch("trn-1", {"short_window": {"current_value": 7},
                                                         "long_window": {"current_value": 21}})
    assert wanted == {"short_window": 5, "long_window": 30}
    assert seen["patch"][0]["Authorization"] == f"Bearer {EXECUTOR_TOKEN}"
    assert seen["patch"][1] == {"patches": [{"parameter_key": "short_window", "proposed_value": 5},
                                            {"parameter_key": "long_window", "proposed_value": 30}]}


def test_isolated_agora_content_is_the_operator_edit_on_the_candidate(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    human = _load(monkeypatch, "test_current_human_learning_deployed_e2e.py", "hosted_human_mod")
    content = _content(human)
    assert content["actor_role"] == "operator" and content["actor_id"] == "executor-1"
    assert content["steps"][0]["observation"] == [0.12, 1.4, -0.07, 7.0]


def test_hosted_agora_content_accepts_a_distinct_human_edit_and_observes_pre_edit_metrics(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    human = _load(monkeypatch, "test_current_human_learning_deployed_e2e.py", "hosted_human_mod")
    content = _hosted_content(human)
    assert content["actor_id"] == HUMAN and content["actor_role"] == "operator"
    assert content["decision"] == "edit" and content["control_edit_event_id"] == "tevt-1"
    assert content["target"]["promotion_state"] == "candidate" and content["target"]["lineage_ref"] == "teaching-candidate:trn-1:abc"
    assert [s["action"] for s in content["steps"]] == ["set_short_window", "set_long_window"]
    # observation is the pre-edit evaluation (0.01/0.2/-0.2), never the post-edit 0.12/1.4/-0.07
    assert content["steps"][0]["observation"] == [0.01, 0.2, -0.2, 7.0]
    assert content["steps"][0]["feedback_event_id"] == "tevt-1:short_window"
    from services.research.imitation.bc_trainer import prepare_dataset
    prepared = prepare_dataset({"dataset_id": "d", "strategy_id": "strat-1", "source_dataset_refs": ["r"],
                                "sessions": [{**content, "trajectory_id": "t"}]})
    assert prepared.included_trajectory_ids == ("t",)


def test_hosted_agora_content_rejects_the_executors_own_and_service_edits_with_named_reasons(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    human = _load(monkeypatch, "test_current_human_learning_deployed_e2e.py", "hosted_human_mod")
    cases = (
        ({"events": [_edit(actor="executor-1")]}, "executor_edit_rejected"),
        ({"events": [_edit(actor="operator-bff", actor_type="service")]}, "service_identity_edit_rejected"),
        ({"events": [_edit(actor="operator-bff")]}, "service_identity_edit_rejected"),
        ({"events": []}, "awaiting_human_edit"),
        ({"pre_edit_evaluation": None}, "pre_edit_evaluation_missing"),
        ({"pre_edit_evaluation": {**PRE_EDIT, "eval_id": "teval-1"}}, "pre_edit_evaluation_missing"),
        ({"executor_subject": ""}, "executor_subject_unknown"),
    )
    for over, reason in cases:
        with pytest.raises(human.NoGenuineControlEdit) as raised:
            _hosted_content(human, **over)
        assert raised.value.reason == reason, (over, raised.value.reason)
    # a pre-edit evaluation taken after the human edit cannot explain it
    with pytest.raises(human.NoGenuineControlEdit):
        _hosted_content(human, pre_edit_evaluation={**PRE_EDIT, "evaluated_at": "2026-10-23T01:25:00Z"})


def test_agora_content_refuses_service_only_or_invented_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    human = _load(monkeypatch, "test_current_human_learning_deployed_e2e.py", "hosted_human_mod")
    for over in (
        {"events": [_edit(actor_type="service")]},            # service is never relabelled human
        {"events": [_edit(actor="someone-else")]},            # not the verified OPERATOR
        {"events": []},                                       # GET controls alone is not an edit
        {"events": [{**_edit(), "patch_delta": _edit()["patch_delta"][:1]}]},
        {"events": [{**_edit(), "timestamp": "2026-10-23T01:45:00Z"}]},  # after the candidate snapshot
        {"events": [{**_edit(), "patch_delta": [{**d, "previous_value": d["new_value"]} for d in _edit()["patch_delta"]]}]},  # zero delta
        {"events": [{**_edit(), "patch_delta": [{**d, "new_value": 99, "previous_value": 1} for d in _edit()["patch_delta"]]}]},  # not the evaluated controls
        {"terminal": _terminal(candidate=False)},             # no real candidate snapshot: no invented target
        {"operator": {"sub": "executor-1", "roles": []}},
    ):
        with pytest.raises(human.NoGenuineControlEdit):
            _content(human, **over)


class FakeLearningOwners:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.requests: dict[str, dict[str, Any]] = {}

    def __call__(self, base_url: str, path: str, *, method: str = "GET", payload: Any = None,
                 headers: Any = None, expected: Any = (200,)) -> Any:
        self.calls.append((method, path, payload))
        if path.endswith("/terminal-readback"):
            return _terminal()
        if path.endswith("/events"):
            return [_edit(actor=HUMAN)]
        if path == "/api/consult/requests" and method == "POST":
            self.requests[payload["request_id"]] = {**payload, "status": "draft"}
            return self.requests[payload["request_id"]]
        if path.startswith("/api/consult/requests/") and method == "GET":
            return self.requests[urllib.parse.unquote(path.rsplit("/", 1)[-1])]
        if path.endswith("/submit"):
            return {}
        if path.startswith("/api/consult/memos"):
            return []
        raise AssertionError(f"unexpected owner call {method} {path}")


def _human(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: Any) -> tuple[Any, Any, FakeLearningOwners]:
    mod = _load(monkeypatch, "test_current_human_learning_deployed_e2e.py", "hosted_human_mod")
    _hosted_env(monkeypatch, tmp_path, "B_learning", **env)
    harness = mod.DeployedHumanLearningHarness()
    owners = FakeLearningOwners()
    harness._http_json = owners
    harness._service_identities = lambda services: {}
    harness._service_identity = lambda service, **k: {}
    harness.poll_seconds, harness.timeout_seconds = 0.001, 0.05
    return mod, harness, owners


def test_human_b_pauses_before_agora_without_a_research_predecessor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, harness, owners = _human(monkeypatch, tmp_path)
    with pytest.raises(pytest.skip.Exception, match="agora_interaction_evidence"):
        harness.run_case(1)
    assert [c for c in owners.calls if c[0] != "GET"] == []
    assert json.loads((tmp_path / "report.json").read_text())["phase_status"] == "paused"


def test_human_agora_evidence_carries_the_teaching_id_registry_and_real_captured_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    preds = {"teaching_session_id": "trn-1", "registry_id": "reg-1", "checksum": "sha-1",
             "strategy_id": "strat-1", "eval_identity": "eid-1"}
    mod, harness, owners = _human(monkeypatch, tmp_path, predecessors={**preds, "pre_edit_evaluation": PRE_EDIT})
    harness._ensure_persona = lambda *a, **k: None
    inputs = harness._hosted_teaching_inputs()
    assert inputs["session_id"] == "trn-1" and inputs["persona_id"] == "persona-teach"
    assert "session:trn-1" in inputs["source_refs"] and "registry:reg-1" in inputs["source_refs"]
    assert inputs["captured_at"] == "2026-10-23T02:00:00Z"  # from Teaching, so replays are byte-identical
    assert inputs["content"]["source_strategy_spec_id"] == "reg-1"
    assert inputs["control_edit_event_id"] == "tevt-1" and "control_edit:tevt-1" in inputs["source_refs"]
    harness.chain.update(preds | {"teaching_eval_identity": "eid-1"})
    with pytest.raises(mod.HostedPause):
        harness._hosted_h2_consent()


def test_human_consultation_pauses_before_creating_the_request_without_h2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, harness, owners = _human(monkeypatch, tmp_path)
    case: dict[str, Any] = {}
    harness._current_case = case
    with pytest.raises(mod.HostedPause) as paused:
        harness._case_consultation_governance_handoff(case)
    assert paused.value.blocked_before == "consultation_request"
    assert [c for c in owners.calls if c[0] != "GET"] == []


def test_human_consult_request_persists_and_reads_back_actual_predecessors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h2 = {"record_ref": "consent-1", "decided_by": "human-1", "recorded_at": "2026-10-23T03:00:00Z"}
    mod, harness, owners = _human(monkeypatch, tmp_path, decisions={"H2": h2})
    harness.chain.update(
        registry_id="reg-1", checksum="sha-1", teaching_session_id="trn-1", teaching_eval_identity="eid-1",
        evidence_id="ev-1", dataset_version_id="dsv-1", candidate_id="cand-1", experiment_run_id="exp-1",
        agora_handoff_id="gh-1",
    )
    case: dict[str, Any] = {}
    harness._current_case = case
    with pytest.raises(mod.BoundaryFailure):  # no memo exists in this fake: stop after the request is verified
        harness._case_consultation_governance_handoff(case)
    create = next(c for c in owners.calls if c[0] == "POST" and c[1] == "/api/consult/requests")
    metadata = create[2]["metadata"]
    assert create[2]["request_id"] == f"hosted-all12-{CHAIN}-cr"
    assert metadata["registry_id"] == "reg-1" and metadata["teaching_session_id"] == "trn-1"
    assert metadata["candidate_id"] == "cand-1" and metadata["h2_consent_ref"] == h2
    assert metadata["chain_id"] == CHAIN and metadata["paper_only"] is True
    assert any(c[0] == "GET" and c[1].endswith(f"hosted-all12-{CHAIN}-cr") for c in owners.calls)
    assert harness.chain["h2_consent_ref"] == "consent-1"


def test_hosted_never_restarts_a_shared_service_and_isolated_still_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, harness, _ = _human(monkeypatch, tmp_path)
    harness._command = lambda *a, **k: pytest.fail("hosted mode reached a Compose command")
    assert harness._restart_or_skip("consultation-svc")["skipped"] == "hosted_shared_service_restart_excluded"
    with pytest.raises(RuntimeError, match="never restarts"):
        harness._restart_service("consultation-svc")

    for name in ("PANTHEON_L12_HOSTED_CHAIN", "PANTHEON_L12_CROSS_LOOP_RUN_ID"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PANTHEON_L12_BFF_BEARER", "bearer")
    isolated = mod.DeployedHumanLearningHarness()
    assert isolated.hosted is False and isolated.hosted_input is None
    isolated._restart_service = lambda service: {"service": service, "restarted": True}
    assert isolated._restart_or_skip("consultation-svc") == {"service": "consultation-svc", "restarted": True}
    monkeypatch.delenv("PANTHEON_L12_AGORA_HANDOFF_TOKEN")
    assert mod.DeployedHumanLearningHarness().agora_handoff_token == "pantheon-local-agora-handoff-service-token"


# --------------------------------------------------------------------------- runtime (C_promote, D_observe)


_DATA_SOURCE = "source-ingest:normalized/taiwanstockprice/finmind-daily"
EXEC_ID = f"hosted-all12-{CHAIN}-execution-artifact"


def _runtime_spec(*, recipe: bool = True) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "strategy_id": "strat-1",
        "market_scope": {"symbols": ["2330.TW"], "frequency": "1d"},
        "data_dependencies": [{"ref": _DATA_SOURCE, "kind": "dataset"}],
        "execution_profile": {"quantity_type": "SHARES"},
        "metadata": {},
    }
    if recipe:
        spec["metadata"]["execution_recipe"] = {
            "kind": "close_to_close_momentum", "symbols": ["2330.TW"], "bar_frequency": "1d",
            "data_source": _DATA_SOURCE, "lookback_bars": 2, "momentum_threshold": 0.01,
            "positive_action": "BUY", "non_positive_action": "SELL", "order_quantity": 1,
            "quantity_type": "SHARES",
        }
    return spec


def _spec_checksum(spec: Mapping[str, Any]) -> str:
    from services.registry.strategy_artifact import strategy_spec_payload_checksum

    return strategy_spec_payload_checksum(spec)


def _artifact_checksum(artifact: Mapping[str, Any]) -> str:
    from services.registry.strategy_artifact import strategy_artifact_checksum

    return strategy_artifact_checksum(artifact)


def _expected_execution_checksum() -> str:
    from services.registry.strategy_artifact import build_execution_artifact_registration

    entry = {"registry_id": f"reg-{CHAIN}", "version": "1.0.0", "checksum": _spec_checksum(_runtime_spec()),
             "lineage": {"source_run_ids": ["seed-1"]}, "metadata": {"strategy_spec": _runtime_spec()}}
    registration = build_execution_artifact_registration(entry, artifact_id=EXEC_ID, algorithm_commit=SHA)
    return _artifact_checksum(registration["strategy_artifact"])


EXEC_CHECKSUM = _expected_execution_checksum()


class FakeRuntimeHttp:
    def __init__(self, *, h3: bool, validation_ok: bool = True, drop_reference: bool = False,
                 bundle: bool = True, h3_scope: dict[str, Any] | None = None) -> None:
        self.execution: dict[str, Any] | None = None
        self.calls: list[tuple[str, str, str]] = []
        self.bodies: dict[str, Any] = {}
        self.h3, self.validation_ok, self.drop_reference = h3, validation_ok, drop_reference
        self.bundle, self.h3_scope = bundle, h3_scope
        self.incidents: list[dict[str, Any]] = []
        self.proposals: list[dict[str, Any]] = []
        self.pool: dict[str, Any] = {}
        self.binding: dict[str, Any] = {}
        self.plan: dict[str, Any] = {}
        self.dispatched = False

    def request(self, service: str, method: str, path: str, *, body: Any = None, headers: Any = None,
                query: Any = None, expected: Any = frozenset({200}), timeout: float = 20.0) -> Any:
        self.calls.append((service, method, path))
        self.bodies[f"{method} {path}"] = body
        registry_id = f"reg-{CHAIN}"
        if path.startswith("/api/registry/strategy-specs/"):
            spec = _runtime_spec(recipe=self.bundle)
            return {"entry": {"registry_id": registry_id, "artifact_state": "approved",
                              "checksum": _spec_checksum(spec), "approval_decision_id": "apv-1",
                              "strategy_id": "strat-1", "version": "1.0.0",
                              "lineage": {"source_run_ids": ["seed-1"]}, "metadata": {"strategy_spec": spec}}}
        if path == "/api/registry/strategy-artifacts" and method == "POST":
            artifact = body["strategy_artifact"]
            self.execution = {"registry_id": body["registry_id"], "artifact_type": "execution_bundle",
                              "artifact_state": "candidate", "checksum": _artifact_checksum(artifact),
                              "version": artifact["version"], "updated_at": "t0",
                              "metadata": {**body["metadata"], "strategy_artifact": artifact}}
            return {"entry": self.execution}
        if path.startswith("/api/registry/strategy-artifacts/") and path.endswith("/advance"):
            assert self.execution is not None
            self.execution = {**self.execution, "artifact_state": body["target_state"],
                              "approval_decision_id": body.get("approval_decision_id")}
            return {"entry": self.execution}
        if path.startswith("/api/registry/strategy-artifacts/"):
            return {"entry": self.execution} if self.execution else {"detail": "StrategyArtifact registry entry not found"}
        if path.startswith("/api/governance/approvals/"):
            decision = path.rsplit("/", 1)[-1]
            if decision == "apv-h3" and self.h3:
                scope = {"target_type": "registry_entry", "target_id": EXEC_ID, "target_version": "1.0.0",
                         "candidate_digest": EXEC_CHECKSUM, "capital_pool_id": f"hosted-all12-{CHAIN}-pool",
                         "persona_id": "persona-teach", **(self.h3_scope or {})}
                return {"decision_id": decision, "tenant_id": TENANT, "decision_state": "decided",
                        "decision": "approved", "version": 2, **scope}
            return {"detail": "not found"}
        if path.startswith("/api/capital-pools") and method == "GET":
            return self.pool or {"detail": "nf"}
        if path == "/api/capital-pools":
            self.pool = {"pool_id": body["pool_id"]}
            return self.pool
        if path.startswith("/api/bindings/") and path.endswith("/activate"):
            self.binding.update(status="active")
            return self.binding
        if path.startswith("/api/bindings/") and method == "GET":
            return self.binding or {"detail": "nf"}
        if path == "/api/bindings":
            self.binding = {"binding_id": body["binding_id"], "status": "pending", "allowed_deployment_scope": "paper"}
            return self.binding
        if path == "/api/deployment/plans/validate":
            return {"ok": self.validation_ok, "errors": [] if self.validation_ok else ["no executable artifact"]}
        if path == "/api/deployment/plans" and method == "POST":
            metadata = dict(body["metadata"])
            if self.drop_reference:
                metadata.pop("consult_handoff_id")
            self.plan = {**body, "metadata": metadata}
            return self.plan
        if path.startswith("/api/deployment/plans/") and path.endswith("/dispatch"):
            self.dispatched = True
            self.plan = {**self.plan, "status": "executed"}
            return {"deployment_saga": {"saga": {"saga_id": "saga-1"}}}
        if path.startswith("/api/deployment/plans/") and method == "GET":
            return self.plan or {"detail": "nf"}
        if path == "/api/runtime-bindings":
            return {"bindings": [{"binding_id": "rb-1"}]}
        if path == "/api/incidents":
            return self.incidents
        if path == "/api/evolution/proposals":
            return self.proposals
        if path.startswith("/api/evolution/proposals/"):
            found = [p for p in self.proposals if p["decision_id"] == path.rsplit("/", 1)[-1]]
            return found[0] if found else {"detail": "nf"}
        if path.startswith("/api/runtime-bindings/"):
            return {"binding_id": "rb-1", "runtime_id": "rt-1", "artifact_id": EXEC_ID, "status": "active",
                    "capital_pool_id": self.pool.get("pool_id") or f"hosted-all12-{CHAIN}-pool",
                    "plan_id": self.plan.get("plan_id") or f"hosted-all12-{CHAIN}-plan",
                    "persona_capital_binding_id": self.binding.get("binding_id")}
        raise AssertionError(f"unexpected owner call {method} {path}")

    def writes(self) -> list[tuple[str, str, str]]:
        return [c for c in self.calls if c[1] != "GET" and c[2] != "/api/deployment/plans/validate"]


def _runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str, **kwargs: Any) -> tuple[Any, Any, FakeRuntimeHttp]:
    h3 = kwargs.pop("h3", False)
    validation_ok = kwargs.pop("validation_ok", True)
    drop_reference = kwargs.pop("drop_reference", False)
    bundle = kwargs.pop("bundle", True)
    h3_scope = kwargs.pop("h3_scope", None)
    mod = _load(monkeypatch, "test_current_runtime_loops_deployed_e2e.py", "hosted_runtime_mod")
    _hosted_env(monkeypatch, tmp_path, phase, **kwargs)
    settings = mod.Settings(
        urls={s: f"http://{s}.invalid" for s in ("bff", "capital", "deployment", "evolution", "fleet", "governance",
                                                 "incidents", "reconciliation", "registry", "runtime",
                                                 "source_ingest", "telemetry")},
        expected_sha=SHA, compose_project="p", compose_files=(), evidence_output=tmp_path / "evidence.json",
        capital_headers={"Authorization": f"Bearer {CAPITAL_JWT}"},
    )
    chain = mod.RuntimeChain(settings)
    fake = FakeRuntimeHttp(h3=h3, validation_ok=validation_ok, drop_reference=drop_reference,
                           bundle=bundle, h3_scope=h3_scope)
    chain.http = fake
    chain._identity = lambda service: chain.evidence.identities.setdefault(service, {"service": service})
    return mod, chain, fake


PROMOTE_PREDECESSORS = {"registry_id": f"reg-{CHAIN}", "checksum": _spec_checksum(_runtime_spec()), "governance_handoff_id": "gh-consult-1",
                        "teaching_session_id": "trn-1", "persona_id": "persona-teach"}
_PAPER_FILL = {"event": {"event_id": "ev-1", "artifact_id": EXEC_ID, "metrics": {"pnl": 1.0}, "metadata": {}},
               "summary": {"runtime_id": "rt-1", "state": "running"}}
PROMOTE_DECISIONS = {"H1": {"decision_id": "apv-1"}, "H3": {"decision_id": "apv-h3"}}


def test_hosted_runtime_headers_are_real_identities_and_default_headers_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, chain, _ = _runtime(monkeypatch, tmp_path, "C_promote")
    for headers in (chain.runtime_headers, chain.deployment_headers, chain.evolution_headers):
        assert headers["Authorization"] == f"Bearer {EXECUTOR_TOKEN}" and "mfa" not in headers["Authorization"]
    assert chain.bff_headers["Authorization"] == "Bearer bff-real"

    for name in ("PANTHEON_L12_HOSTED_CHAIN", "PANTHEON_L12_CROSS_LOOP_RUN_ID", "PANTHEON_L12_HOSTED_CHAIN_PHASE"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "i")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "a")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "s" * 64)
    isolated = mod.RuntimeChain(chain.settings)
    assert isolated.hosted_input is None
    assert isolated.runtime_headers["Authorization"] == "Bearer l12-current-e2e:operator:mfa"


def test_hosted_promote_pauses_before_any_write_when_predecessors_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote")
    result = chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "capital_pool_creation"
    assert fake.writes() == [] and result["cases"] == {}


def test_hosted_promote_registers_candidate_then_pauses_for_exact_h3_before_covered_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                              decisions={"H1": PROMOTE_DECISIONS["H1"]})
    chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "execution_artifact_approval"
    ids = receipt["resource_ids"]
    # exact candidate version/checksum and the actual paper pool/persona are named for the H3 reviewer
    assert ids["execution_artifact_id"] == EXEC_ID and ids["execution_artifact_version"] == "1.0.0"
    assert ids["execution_artifact_checksum"] == EXEC_CHECKSUM
    assert ids["capital_pool_id"] == f"hosted-all12-{CHAIN}-pool" and ids["persona_id"] == "persona-teach"
    # candidate registered (distinct id, Spec lineage), but nothing H3 covers was written
    assert fake.execution["artifact_state"] == "candidate" and fake.execution["registry_id"] != f"reg-{CHAIN}"
    lineage = fake.execution["metadata"]["strategy_artifact"]["lineage"]
    assert lineage["source_strategy_spec_id"] == f"reg-{CHAIN}"
    covered = [c for c in fake.writes() if c[2].endswith("/advance") or "/api/deployment/plans" in c[2]]
    assert covered == [] and not fake.dispatched
    assert not any("/strategy-specs/" in c[2] and c[1] != "GET" for c in fake.calls)  # H1 Spec untouched
    # Capital compares the body actor to the verified principal: the real delegated actor, not an isolated one
    for write in ("POST /api/capital-pools", "POST /api/bindings", f"POST /api/bindings/hosted-all12-{CHAIN}-pcb/activate"):
        assert fake.bodies[write]["actor_id"] == "operator-real"
    activation = fake.bodies[f"POST /api/bindings/hosted-all12-{CHAIN}-pcb/activate"]
    assert "approval_decision_id" not in activation  # paper capital: no invented approval

    # same chain, H3 now decided, separate process: one H3 used for Registry advance AND plan, one dispatch
    _, second, fake2 = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                                decisions=PROMOTE_DECISIONS, h3=True)
    fake2.pool, fake2.binding, fake2.execution = dict(fake.pool), {**fake.binding}, dict(fake.execution)
    second._runtime_event = lambda runtime_id: _PAPER_FILL
    second._fleet_worker = lambda binding_id: {"worker": {}}
    second.run_hosted()
    done = json.loads((tmp_path / "evidence.json").read_text())
    assert done["phase_status"] == "completed" and done["status"] == "passed"
    assert fake2.dispatched
    assert [c for c in fake2.writes() if c[2] in {"/api/capital-pools", "/api/bindings", "/api/strategy-artifacts"}] == []
    assert not any(c[2] == "/api/registry/strategy-artifacts" for c in fake2.writes())  # no duplicate candidate
    advances = [fake2.bodies[f"POST {c[2]}"] for c in fake2.writes() if c[2].endswith("/advance")]
    assert [a["target_state"] for a in advances] == ["approved"] and advances[0]["approval_decision_id"] == "apv-h3"
    plan = fake2.plan
    assert plan["registry_id"] == EXEC_ID and plan["approval_decision_id"] == "apv-h3"
    assert plan["metadata"]["consult_handoff_id"] == "gh-consult-1" and plan["metadata"]["chain_id"] == CHAIN
    assert plan["metadata"]["registry_checksum"] == EXEC_CHECKSUM
    assert plan["metadata"]["source_strategy_spec_id"] == f"reg-{CHAIN}"
    assert plan["metadata"]["h1_decision_id"] == "apv-1"  # H1 stays the Spec's decision
    ids = done["resource_ids"]
    assert ids["runtime_binding_id"] == "rb-1" and ids["h3_decision_id"] == "apv-h3"
    assert ids["capital_pool_id"] == f"hosted-all12-{CHAIN}-pool" and ids["capital_binding_id"] == f"hosted-all12-{CHAIN}-pcb"
    assert not {"pool_id", "pcb_id", "binding_id"} & set(ids)  # canonical names only
    assert "loop_08_promotion_deployment" in second.evidence.cases and "loop_09_capital_artifact_execution" in second.evidence.cases


def test_hosted_promote_pauses_as_unsupported_executable_path_for_research_only_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # a Spec without an explicit supported recipe (e.g. pairs research) is honestly not executable
    research_only = {**PROMOTE_PREDECESSORS, "checksum": _spec_checksum(_runtime_spec(recipe=False))}
    _, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=research_only,
                              decisions=PROMOTE_DECISIONS, h3=True, bundle=False)
    chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "unsupported_executable_path"
    assert "research-only" in receipt["detail"] and receipt["status"] == "paused"
    assert fake.writes() == [] and not fake.dispatched
    assert not any("parent" in c[2] or "tw-session" in c[2] for c in fake.calls)  # unrelated artifact untouched


def test_hosted_promote_h3_must_match_the_exact_artifact_pool_and_persona(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h1_only_scope = {"target_id": f"reg-{CHAIN}"}  # H1 / Spec-targeted decision must never be reused
    for scope in (h1_only_scope, {"capital_pool_id": "some-other-pool"}, {"persona_id": None},
                  {"candidate_digest": _spec_checksum(_runtime_spec())}, {"target_version": "9.9.9"},
                  {"target_type": "deployment_plan"}):
        _, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                                  decisions=PROMOTE_DECISIONS, h3=True, h3_scope=scope)
        chain.run_hosted()
        receipt = json.loads((tmp_path / "evidence.json").read_text())
        assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "execution_artifact_approval", scope
        assert not fake.dispatched and fake.plan == {}
        assert [c for c in fake.writes() if c[2].endswith("/advance") or "/api/deployment/plans" in c[2]] == []


def test_hosted_promote_with_no_paper_fill_pauses_at_loop_9_instead_of_completing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                              decisions=PROMOTE_DECISIONS, h3=True)
    chain._runtime_event = lambda runtime_id: None
    chain._fleet_worker = lambda binding_id: None
    chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "runtime_execution_observation"
    assert set(receipt["cases"]) == {"loop_08_promotion_deployment"}  # loop 9 not claimed
    assert receipt["resource_ids"]["runtime_binding_id"] == "rb-1"


def test_hosted_promote_fails_when_the_plan_does_not_persist_predecessor_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                                decisions=PROMOTE_DECISIONS, h3=True, drop_reference=True)
    with pytest.raises(mod.DeployedProofError, match="did not persist predecessor references"):
        chain.run_hosted()
    assert not fake.dispatched


D_PREDECESSORS = {"runtime_binding_id": "rb-1", "runtime_id": "rt-1", "plan_id": f"hosted-all12-{CHAIN}-plan"}
INCIDENT = {"incident_id": "inc-1", "binding_id": "rb-1", "telemetry_event_ids": ["ev-1"]}


def _proposal(**over: Any) -> dict[str, Any]:
    return {"decision_id": "evo-1", "decision_state": "approved", "target_id": EXEC_ID,
            "capital_pool_id": f"hosted-all12-{CHAIN}-pool", "linked_incident_id": "inc-1", "metadata": {}, **over}


def _observe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fill: bool = True, incidents: Any = (),
             proposals: Any = (), **kwargs: Any) -> tuple[Any, Any, FakeRuntimeHttp]:
    mod, chain, fake = _runtime(monkeypatch, tmp_path, "D_observe", predecessors=D_PREDECESSORS, **kwargs)
    chain._runtime_event = lambda runtime_id: _PAPER_FILL if fill else None
    chain._fleet_worker = lambda binding_id: {"worker": {}}
    chain._bff_health = lambda: {"data": {"targets": {"runtime-manager": {"ok": True},
                                                      "paper-fleet-reconciler": {"ok": True}}}}
    fake.incidents, fake.proposals = list(incidents), list(proposals)
    return mod, chain, fake


def _paused_at(tmp_path: Path) -> tuple[str, set[str]]:
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused"
    return receipt["blocked_before"], {c["loop"] for c in receipt["cases"].values()}


def test_hosted_observe_pauses_without_claiming_loops_that_were_not_observed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, chain, fake = _observe(monkeypatch, tmp_path, fill=False)
    chain.run_hosted()
    assert _paused_at(tmp_path) == ("runtime_execution_observation", set())

    _, chain, fake = _observe(monkeypatch, tmp_path)
    chain.run_hosted()
    assert _paused_at(tmp_path) == ("drift_incident_observation", set())  # natural incident absent: nothing injected

    _, chain, fake = _observe(monkeypatch, tmp_path, incidents=[INCIDENT])
    chain.run_hosted()
    assert _paused_at(tmp_path) == ("evolution_decision_observation", {10})

    _, chain, fake = _observe(monkeypatch, tmp_path, incidents=[INCIDENT], proposals=[_proposal()])
    chain.run_hosted()  # loops 10-12 observed, H4 not supplied
    assert _paused_at(tmp_path) == ("evolution_decision_acceptance", {10, 11, 12})
    assert fake.writes() == []  # D only reads owners


def test_hosted_observe_h4_must_match_this_chains_target_pool_and_incident_lineage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h4 = {"H4": {"decision_id": "evo-1"}}
    for off in ({"target_id": "other-artifact"}, {"capital_pool_id": "other-pool"},
                {"decision_state": "proposed"}):
        _, chain, _ = _observe(monkeypatch, tmp_path, incidents=[INCIDENT], proposals=[_proposal(**off)], decisions=h4)
        chain.run_hosted()
        assert _paused_at(tmp_path)[0] == "evolution_decision_acceptance", off

    # an approved decision on another incident satisfies nothing: the loop-11 read finds no lineage match
    _, chain, _ = _observe(monkeypatch, tmp_path, incidents=[INCIDENT],
                           proposals=[_proposal(linked_incident_id="inc-other")], decisions=h4)
    chain.run_hosted()
    assert _paused_at(tmp_path)[0] == "evolution_decision_observation"

    _, chain, _ = _observe(monkeypatch, tmp_path, incidents=[INCIDENT], proposals=[_proposal()], decisions=h4)
    chain.run_hosted()
    done = json.loads((tmp_path / "evidence.json").read_text())
    assert done["phase_status"] == "completed" and {c["loop"] for c in done["cases"].values()} == {10, 11, 12}
    assert done["resource_ids"]["h4_decision_id"] == "evo-1" and done["resource_ids"]["incident_id"] == "inc-1"


def test_hosted_runtime_paths_never_stop_or_restart_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, _, _ = _runtime(monkeypatch, tmp_path, "D_observe")
    for method in (mod.RuntimeChain._hosted_promote, mod.RuntimeChain._hosted_observe,
                   mod.RuntimeChain._hosted_capital, mod.RuntimeChain._hosted_paper_fill,
                   mod.RuntimeChain.run_hosted):
        source = inspect.getsource(method)
        for forbidden in ('"stop"', '"restart"', "_retire_invalid_preexisting_bindings", "negative_", "mfa"):
            assert forbidden not in source, (method.__name__, forbidden)


def test_hosted_capital_credential_is_requested_fresh_for_every_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote")
    issued: list[str] = []

    def issue() -> str:
        issued.append(CAPITAL_JWT)
        return f"Bearer {CAPITAL_JWT}"

    chain.settings = replace(chain.settings, capital_headers={}, capital_authorization=issue)
    assert chain.settings.capital_auth() == {"Authorization": f"Bearer {CAPITAL_JWT}"}
    chain.settings.capital_auth()
    assert len(issued) == 2


# --------------------------------------------------------------------------- persona writer identity


@pytest.mark.parametrize(
    "filename, cls",
    [
        ("test_current_research_loops_deployed_e2e.py", "DeployedResearchHarness"),
        ("test_current_human_learning_deployed_e2e.py", "DeployedHumanLearningHarness"),
    ],
)
def test_hosted_ensure_persona_pairs_service_token_with_service_actor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, filename: str, cls: str
) -> None:
    mod = _load(monkeypatch, filename, f"persona_writer_{cls}")
    _hosted_env(monkeypatch, tmp_path, "A_research" if "research" in filename else "B_learning")
    monkeypatch.setenv("PANTHEON_L12_PERSONA_SERVICE_TOKEN", "opaque-persona-service-token")
    monkeypatch.setenv("PANTHEON_L12_PERSONA_SERVICE_ACTOR_ID", "operator-bff")
    harness = getattr(mod, cls)()
    seen: list[tuple[str, str, Any, str]] = []

    def owner(base_url: str, path: str, *, method: str = "GET", payload: Any = None,
              headers: Mapping[str, str] | None = None, expected: Any = (200,), **_k: Any) -> Any:
        seen.append((method, path, payload, (headers or {}).get("Authorization", "")))
        if method == "POST":
            return {}
        return {"persona_id": "p-1", "tenant_id": harness.tenant_id}

    harness._http_json = owner
    # same-tenant existing Persona is reused without any write
    harness._ensure_persona("p-1", name="n", mandate="m")
    assert [c[0] for c in seen] == ["GET"]
    assert seen[0][3] == "Bearer opaque-persona-service-token"

    seen.clear()
    state = {"n": 0}

    def create(base_url: str, path: str, **kw: Any) -> Any:
        state["n"] += 1
        seen.append((kw.get("method", "GET"), path, kw.get("payload"), (kw.get("headers") or {}).get("Authorization", "")))
        if state["n"] == 1:
            return None  # 404: absent
        return {} if kw.get("method") == "POST" else {"persona_id": "p-1", "tenant_id": harness.tenant_id}

    harness._http_json = create
    harness._ensure_persona("p-1", name="n", mandate="m")
    post = next(c for c in seen if c[0] == "POST")
    assert post[2]["actor_id"] == "operator-bff" and post[2]["tenant_id"] == harness.tenant_id
    # logical owner stays the genuine executing human, never relabelled as the service actor
    import l12_owner_auth
    human = l12_owner_auth.token_subject(l12_owner_auth.human_token("OPERATOR"))
    assert post[2]["owner"] == human and human != "operator-bff"
    assert post[2]["metadata"] == {"requested_by": {"actor_type": "operator", "actor_id": human}}
    assert {c[3] for c in seen} == {"Bearer opaque-persona-service-token"}

    # without a staged writer (isolated / ordinary operator) the caller identity is untouched
    monkeypatch.delenv("PANTHEON_L12_PERSONA_SERVICE_TOKEN")
    assert mod.persona_writer_identity() is None
    monkeypatch.delenv("PANTHEON_L12_HOSTED_CHAIN")
    monkeypatch.setenv("PANTHEON_L12_PERSONA_SERVICE_TOKEN", "x")
    assert mod.persona_writer_identity() is None
