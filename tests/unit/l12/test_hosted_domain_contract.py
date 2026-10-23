"""Hosted-chain behaviour of the three L12 domain suites, against in-memory fake owners.

Focus (HOSTED-ALL12-DOMAIN-PHASES-20261023): pause before the mutation a human decision
covers, genuine content/reference propagation across suites, hosted exclusion of shared
infrastructure cases, and unchanged default (isolated) behaviour. No owner, Compose
service or provider is contacted.
"""

from __future__ import annotations

import base64
import importlib.util
import inspect
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

import pytest

L12_DIR = Path(__file__).resolve().parents[2] / "integration" / "l12"
CHAIN = "chain-x"
TENANT = "tenant-hosted"
SHA = "a" * 40
EXECUTOR_TOKEN = ".".join(
    base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
    for part in ({"alg": "none"}, {"sub": "executor-1"}, "sig")
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
                                           "distillation_job_id": "dj-1"}},
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
            return self.alpha_run()
        raise AssertionError(f"unexpected owner call {method} {path}")

    def alpha_run(self) -> dict[str, Any]:
        return {"run_id": "auth-run-1", "parameters": {
            "record_type": "ExperimentRun", "strategy_spec_id": self.registry_id,
            "experiment_run": {"run_id": "exp-1", "strategy_spec_id": self.registry_id, "status": "completed"}}}

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
    harness._ensure_persona = lambda *a, **k: None
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
    persona = f"hosted-all12-{CHAIN}-persona"
    owners.sessions.append({
        "session_id": "trn-prior", "persona_id": persona, "trace_id": f"hosted-all12-{CHAIN}-trace-teaching",
        "context_refs": [{"type": "experiment_run", "id": "auth-run-1", "domain_run_id": "exp-1",
                          "strategy_spec_id": owners.registry_id}],
    })
    harness.run_case(3)
    assert ("POST", "/api/training/sessions") not in owners.calls
    assert harness.chain["session"]["session_id"] == "trn-prior"
    assert harness._hosted_resource_ids()["teaching_session_id"] == "trn-prior"


# --------------------------------------------------------------------------- human learning (B_learning)


def _terminal(validation: str = "passed") -> dict[str, Any]:
    return {
        "session_id": "trn-1", "persona_id": "persona-teach", "eval_identity": "eid-1",
        "started_at": "2026-10-23T01:00:00Z", "updated_at": "2026-10-23T02:00:00Z",
        "evaluation_result": {"eval_id": "teval-1", "validation_status": validation,
                              "metrics": {"total_return": 0.12, "sharpe_ratio": 1.4, "max_drawdown": -0.07}},
    }


CONTROLS = {"controls": [
    {"parameter_key": "short_window", "proposed_value": 7},
    {"parameter_key": "long_window", "proposed_value": 21},
    {"parameter_key": "untouched", "proposed_value": None},
]}


def test_agora_content_is_built_from_the_real_teaching_result(monkeypatch: pytest.MonkeyPatch) -> None:
    human = _load(monkeypatch, "test_current_human_learning_deployed_e2e.py", "hosted_human_mod")
    content = human.teaching_agora_content(
        terminal=_terminal(), controls_record=CONTROLS, registry_id="reg-1", strategy_id="strat-1", checksum="sha-1")
    assert content["teaching_session_id"] == "trn-1"
    assert content["teaching"]["eval_identity"] == "eid-1"
    assert content["target"]["registry_id"] == "reg-1" and content["source_strategy_spec_id"] == "reg-1"
    assert content["decision"] == "approve"
    assert [s["action"] for s in content["steps"]] == ["set_short_window", "set_long_window"]
    assert content["steps"][0]["observation"] == [0.12, 1.4, -0.07, 7.0]
    assert content["steps"][0]["feedback_event_id"] == "eid-1:short_window"
    # nothing is relabelled from the isolated suite's fixed rows
    assert "buy_small" not in json.dumps(content) and "reg-l12-human-learning" not in json.dumps(content)
    rejected = human.teaching_agora_content(
        terminal=_terminal("failed"), controls_record=CONTROLS, registry_id="reg-1", strategy_id="s", checksum="c")
    assert rejected["decision"] == "reject"
    with pytest.raises(RuntimeError, match="fewer than two distinct"):
        human.teaching_agora_content(
            terminal=_terminal(), controls_record={"controls": CONTROLS["controls"][:1]},
            registry_id="r", strategy_id="s", checksum="c")


class FakeLearningOwners:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.requests: dict[str, dict[str, Any]] = {}

    def __call__(self, base_url: str, path: str, *, method: str = "GET", payload: Any = None,
                 headers: Any = None, expected: Any = (200,)) -> Any:
        self.calls.append((method, path, payload))
        if path.endswith("/terminal-readback"):
            return _terminal()
        if path.startswith("/api/training/controls/"):
            return CONTROLS
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
    mod, harness, owners = _human(monkeypatch, tmp_path, predecessors=preds)
    harness._ensure_persona = lambda *a, **k: None
    inputs = harness._hosted_teaching_inputs()
    assert inputs["session_id"] == "trn-1" and inputs["persona_id"] == "persona-teach"
    assert "session:trn-1" in inputs["source_refs"] and "registry:reg-1" in inputs["source_refs"]
    assert inputs["captured_at"] == "2026-10-23T02:00:00Z"  # from Teaching, so replays are byte-identical
    assert inputs["content"]["target"]["registry_id"] == "reg-1"
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


class FakeRuntimeHttp:
    def __init__(self, *, h3: bool, validation_ok: bool = True, drop_reference: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.bodies: dict[str, Any] = {}
        self.h3, self.validation_ok, self.drop_reference = h3, validation_ok, drop_reference
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
            return {"entry": {"registry_id": registry_id, "artifact_state": "approved", "checksum": "sha-1",
                              "approval_decision_id": "apv-1", "strategy_id": "strat-1"}}
        if path.startswith("/api/governance/approvals/"):
            decision = path.rsplit("/", 1)[-1]
            if decision == "apv-h3" and self.h3:
                return {"decision_id": decision, "tenant_id": TENANT, "decision_state": "decided",
                        "decision": "approved", "target_id": f"hosted-all12-{CHAIN}-plan", "version": 2}
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
        if path.startswith("/api/runtime-bindings/"):
            return {"binding_id": "rb-1", "runtime_id": "rt-1", "artifact_id": registry_id, "status": "active",
                    "capital_pool_id": self.pool.get("pool_id"), "plan_id": self.plan.get("plan_id"),
                    "persona_capital_binding_id": self.binding.get("binding_id")}
        raise AssertionError(f"unexpected owner call {method} {path}")

    def writes(self) -> list[tuple[str, str, str]]:
        return [c for c in self.calls if c[1] != "GET" and c[2] != "/api/deployment/plans/validate"]


def _runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str, **kwargs: Any) -> tuple[Any, Any, FakeRuntimeHttp]:
    h3 = kwargs.pop("h3", False)
    validation_ok = kwargs.pop("validation_ok", True)
    drop_reference = kwargs.pop("drop_reference", False)
    mod = _load(monkeypatch, "test_current_runtime_loops_deployed_e2e.py", "hosted_runtime_mod")
    _hosted_env(monkeypatch, tmp_path, phase, **kwargs)
    settings = mod.Settings(
        urls={s: f"http://{s}.invalid" for s in ("bff", "capital", "deployment", "evolution", "fleet", "governance",
                                                 "incidents", "reconciliation", "registry", "runtime",
                                                 "source_ingest", "telemetry")},
        expected_sha=SHA, compose_project="p", compose_files=(), evidence_output=tmp_path / "evidence.json",
        capital_headers={"Authorization": "Bearer capital-real"},
    )
    chain = mod.RuntimeChain(settings)
    fake = FakeRuntimeHttp(h3=h3, validation_ok=validation_ok, drop_reference=drop_reference)
    chain.http = fake
    chain._identity = lambda service: chain.evidence.identities.setdefault(service, {"service": service})
    return mod, chain, fake


PROMOTE_PREDECESSORS = {"registry_id": f"reg-{CHAIN}", "checksum": "sha-1", "governance_handoff_id": "gh-consult-1",
                        "teaching_session_id": "trn-1", "persona_id": "persona-teach"}
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


def test_hosted_promote_stops_before_runtime_binding_without_h3_and_dispatches_once_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                              decisions={"H1": PROMOTE_DECISIONS["H1"]})
    chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "runtime_binding_dispatch"
    assert not fake.dispatched
    assert receipt["resource_ids"]["plan_id"] == f"hosted-all12-{CHAIN}-plan"
    plan = fake.plan
    assert plan["registry_id"] == f"reg-{CHAIN}" and plan["approval_decision_id"] == "apv-1"
    assert plan["metadata"]["consult_handoff_id"] == "gh-consult-1"  # actual Consultation predecessor
    assert plan["metadata"]["registry_checksum"] == "sha-1" and plan["metadata"]["chain_id"] == CHAIN
    activation = fake.bodies[f"POST /api/bindings/hosted-all12-{CHAIN}-pcb/activate"]
    assert "approval_decision_id" not in activation  # paper capital: no invented approval

    # same chain, H3 now decided, separate process: no duplicate predecessor creation, one dispatch
    _, second, fake2 = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                                decisions=PROMOTE_DECISIONS, h3=True)
    fake2.pool, fake2.binding, fake2.plan = dict(fake.pool), {**fake.binding}, dict(fake.plan)
    second.run_hosted()
    done = json.loads((tmp_path / "evidence.json").read_text())
    assert done["phase_status"] == "completed" and done["status"] == "passed"
    assert fake2.dispatched
    assert [c for c in fake2.writes() if c[2] in {"/api/capital-pools", "/api/bindings", "/api/deployment/plans"}] == []
    assert done["resource_ids"]["binding_id"] == "rb-1" and done["resource_ids"]["h3_decision_id"] == "apv-h3"
    assert "loop_08_promotion_deployment" in second.evidence.cases


def test_hosted_promote_reports_unsupported_executable_path_without_creating_a_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                                decisions=PROMOTE_DECISIONS, h3=True, validation_ok=False)
    with pytest.raises(mod.UnsupportedExecutablePath, match="no executable artifact"):
        chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "failed" and receipt["blocked_before"] == "unsupported_executable_path"
    assert ("deployment", "POST", "/api/deployment/plans") not in fake.calls and not fake.dispatched


def test_hosted_promote_fails_when_the_plan_does_not_persist_predecessor_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, chain, fake = _runtime(monkeypatch, tmp_path, "C_promote", predecessors=PROMOTE_PREDECESSORS,
                                decisions=PROMOTE_DECISIONS, h3=True, drop_reference=True)
    with pytest.raises(mod.DeployedProofError, match="did not persist predecessor references"):
        chain.run_hosted()
    assert not fake.dispatched


def test_hosted_observe_records_h4_pending_and_no_hosted_path_stops_or_restarts_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, chain, fake = _runtime(
        monkeypatch, tmp_path, "D_observe",
        predecessors={"binding_id": "rb-1", "runtime_id": "rt-1", "plan_id": f"hosted-all12-{CHAIN}-plan"},
    )
    chain._runtime_event = lambda runtime_id: None
    chain._fleet_worker = lambda binding_id: None
    chain._bff_health = lambda: {"data": {"targets": {"runtime-manager": {"ok": True}}}}
    fake.plan = {"plan_id": f"hosted-all12-{CHAIN}-plan"}
    chain.run_hosted()
    receipt = json.loads((tmp_path / "evidence.json").read_text())
    assert receipt["phase_status"] == "paused" and receipt["blocked_before"] == "evolution_decision_acceptance"
    assert receipt["observations"]["paper_fill_observed"] is False
    assert fake.writes() == []

    mod = sys.modules["hosted_runtime_mod"]
    for method in (mod.RuntimeChain._hosted_promote, mod.RuntimeChain._hosted_observe,
                   mod.RuntimeChain._hosted_capital, mod.RuntimeChain.run_hosted):
        source = inspect.getsource(method)
        for forbidden in ('"stop"', '"restart"', "_retire_invalid_preexisting_bindings", "negative_", "mfa"):
            assert forbidden not in source, (method.__name__, forbidden)
