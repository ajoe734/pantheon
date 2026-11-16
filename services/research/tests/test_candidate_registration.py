"""Unit and owner-boundary tests for demand-driven template candidate registration."""
from __future__ import annotations

import copy
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.evolution.approved_baselines import resolve_member_baseline
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid
from services.registry.alpha_template import (
    build_alpha_template_registry_payload,
    load_library_templates,
)
from services.registry.service import app as registry_app, get_registry_service
from services.registry.storage import reset_store as reset_registry_store
from services.registry.strategy_artifact import strategy_artifact_checksum
from services.research.alpha_replication.shared_stores import (
    SharedBacktestRunStore,
    SharedReplicationGate,
)
from services.research.candidate_registration import (
    CandidateRegistrationError,
    TemplateCandidateRegistrar,
)
from services.research.research_demand import ResearchDemandStore
from services.research.template_demand_search import TemplateDemandSearch, compile_template
from services.research.tests.test_persona_demand_routes_auth import (
    AUDIENCE,
    ISSUER,
    SECRET,
    TENANT,
    _cycle_headers,
)
from services.research.tests.test_research_orchestrator_http_service import (
    _load_service_module,
)
from services.runtime_auth_inbound import encode_jwt_hs256

_CP_GOV = Path(__file__).resolve().parents[2] / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))
from approval_decision import ApprovalDecision, EvidenceRef  # type: ignore  # noqa: E402

BACKTEST_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "replication"
    / "fixtures"
    / "recorded_backtest_tw_2330.json"
)
BARS_FIXTURE = Path(__file__).with_name("fixtures") / "template_demand_tw_bars.json"


def _recorded_dataset() -> dict[str, Any]:
    return json.loads(BARS_FIXTURE.read_text(encoding="utf-8"))["dataset"]


def _template_entries(*ids: str) -> list[dict[str, Any]]:
    out = []
    for tpl in load_library_templates():
        if ids and tpl["template_id"] not in ids:
            continue
        rid, payload = build_alpha_template_registry_payload(tpl)
        out.append(
            {
                "entry": {
                    "registry_id": rid,
                    "artifact_type": "alpha_template",
                    "owner_tenant": "__builtin__",
                    "checksum": payload.checksum,
                    "metadata": payload.metadata,
                }
            }
        )
    return out


def _governed_passing_evaluation_for_artifact(
    artifact: dict[str, Any], dataset: dict[str, Any], *, run_id: str = "bt-tpl-pass-001"
) -> dict[str, Any]:
    """Bind the recorded TWSE 2330 governed evaluation to the compiled template artifact identity."""
    ev = json.loads(BACKTEST_FIXTURE.read_text(encoding="utf-8"))
    checksum = strategy_artifact_checksum(artifact)
    ev["backtest_run_id"] = run_id
    ev["dataset_id"] = str(dataset["dataset_id"])
    ev["source_dataset_refs"] = list(dataset["source_dataset_refs"])
    ev["strategy_artifact"] = {
        "artifact_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"],
        "version": artifact["version"],
        "checksum": checksum,
    }
    return ev


class RealGovernanceApprovalHarness:
    """Executes real ApprovalDecision transitions and serves ApprovalEvidence to RegistryService."""

    def __init__(self) -> None:
        self.decisions: dict[str, ApprovalDecision] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def get(self, decision_id: str) -> ApprovalEvidence:
        dec = self.decisions.get(decision_id)
        if dec is None:
            raise ApprovalInvalid("Governance decision not found")
        payload = dec.to_dict()
        payload["recorded_at"] = payload.get("recorded_at") or payload.get("decided_at") or payload.get("created_at")
        payload["authority_status"] = payload.get("authority_status") or "authoritative"
        payload["controller_record_ref"] = payload.get("controller_record_ref") or f"governance-controller://{decision_id}"
        payload["event_id"] = payload.get("event_id") or f"evt-{decision_id}-v{payload.get('version', 1)}"
        return ApprovalEvidence.model_validate(payload)

    def verify(self, decision_id: str, *, expected: dict[str, Any], now: Any = None, usage_context: Any = None) -> ApprovalEvidence:
        return self.get(decision_id).require_valid(expected=expected, now=now, usage_context=usage_context)

    def handle(self, method: str, path: str, tenant_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET" and path.startswith("/api/governance/approvals/"):
            did = path.rsplit("/", 1)[-1]
            dec = self.decisions.get(did)
            if dec is None or dec.tenant_id != tenant_id:
                raise LookupError(f"404: approval {did} not found")
            return dec.to_dict()
        if method == "POST" and path == "/api/governance/approvals":
            assert body is not None
            assert body["expected_version"] == 0
            did = str(body["decision_id"])
            fields = {
                k: v
                for k, v in body.items()
                if k not in {"decision_id", "expected_version"}
            }
            dec = ApprovalDecision.create_proposed(decision_id=did, **fields)
            dec.version = 1
            dec.event_id = f"evt-{did}-v1"
            errs = dec.validate()
            if errs:
                raise ValueError("; ".join(errs))
            self.decisions[did] = dec
            return dec.to_dict()
        if method == "POST" and path.endswith("/review"):
            assert body is not None
            did = path.split("/")[-2]
            dec = self.decisions[did]
            assert dec.version == body["expected_version"]
            dec.accept_review(actor_role=body["actor_role"], actor_id=body["actor_id"])
            dec.version = int(body["expected_version"]) + 1
            dec.event_id = f"evt-{did}-v{dec.version}"
            errs = dec.validate()
            if errs:
                raise ValueError("; ".join(errs))
            return dec.to_dict()
        if method == "POST" and path.endswith("/decide"):
            assert body is not None
            did = path.split("/")[-2]
            dec = self.decisions[did]
            assert dec.version == body["expected_version"]
            refs = [EvidenceRef(**r) for r in (body.get("evidence_refs") or [])]
            dec.decide(
                actor_role=body["actor_role"],
                actor_id=body["actor_id"],
                outcome=body["outcome"],
                candidate_digest=body["candidate_digest"],
                expires_at=body["expires_at"],
                rationale=body["rationale"],
                evidence_refs=refs or None,
            )
            dec.version = int(body["expected_version"]) + 1
            dec.event_id = f"evt-{did}-v{dec.version}"
            errs = dec.validate()
            if errs:
                raise ValueError("; ".join(errs))
            return dec.to_dict()
        raise AssertionError(f"Unexpected governance call: {method} {path}")


class OwnerEnvironment:
    """Wires real Research, Registry, and Governance domain services over in-process HTTP."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
        reset_registry_store()
        for key, value in {
            "AUTH_MODE": "strict",
            "JWT_SECRET": SECRET,
            "JWT_ISSUER": ISSUER,
            "JWT_AUDIENCE": AUDIENCE,
            "MFA_REQUIRED": "false",
        }.items():
            monkeypatch.setenv("PANTHEON_RESEARCH_" + key, value)
            monkeypatch.setenv("PANTHEON_REGISTRY_" + key, value)

        self.data_dir = tmp_path / "alpha"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("ALPHA_REPLICATION_DATA_DIR", str(self.data_dir))
        monkeypatch.setenv("RESEARCH_REPLICATION_TRIAL_COUNTER_PATH", str(tmp_path / "trials.json"))
        self.research = _load_service_module(data_dir=str(tmp_path / "research"))
        monkeypatch.setattr(self.research, "ALPHA_REPLICATION_DATA_DIR", self.data_dir)

        self.research_client = TestClient(self.research.app)
        self.registry_client = TestClient(registry_app)
        self.governance = RealGovernanceApprovalHarness()
        get_registry_service().approval_reader = self.governance
        monkeypatch.setattr(
            "services.governance.approval_authority.configured_approval_reader",
            lambda *a, **kw: self.governance,
        )

        research_client = self.research_client

        class _ResearchOpener:
            def open(self, request: Any, timeout: float | None = None) -> Any:
                resp = research_client.request(
                    request.get_method(),
                    request.full_url,
                    headers=dict(request.header_items()),
                )
                if resp.status_code >= 400:
                    import urllib.error

                    raise urllib.error.HTTPError(
                        request.full_url, resp.status_code, resp.text, {}, io.BytesIO(resp.content)
                    )
                return io.BytesIO(resp.content)

        monkeypatch.setenv("RESEARCH_ORCHESTRATOR_API_URL", "http://testserver")
        monkeypatch.setattr("services.registry.service.urllib.request.build_opener", lambda *a: _ResearchOpener())

    def registry_headers(self, tenant: str = TENANT, sub: str = "research-orchestrator") -> dict[str, str]:
        tok = encode_jwt_hs256(
            {
                "sub": sub,
                "tenant_id": tenant,
                "roles": ["registry-writer", "operator"],
                "iss": ISSUER,
                "aud": AUDIENCE,
                "exp": int(time.time()) + 900,
            },
            secret=SECRET,
        )
        return {"Authorization": f"Bearer {tok}", "X-Tenant-Id": tenant}

    def owner_call(
        self,
        *,
        step: str,
        service: str,
        method: str,
        url: str,
        tenant_id: str,
        token_variable: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> Any:
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        if urlsplit(url).query:
            path += "?" + urlsplit(url).query
        if service == "registry":
            resp = self.registry_client.request(
                method,
                path,
                json=body,
                headers={**self.registry_headers(tenant_id), **(extra_headers or {})},
            )
            if resp.status_code >= 400:
                err = RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
                setattr(err, "status", resp.status_code)
                raise err
            return resp.json()
        if service == "governance":
            return self.governance.handle(method, path, tenant_id, body)
        raise AssertionError(f"Unexpected service {service} at {step}")


def test_passing_template_attempt_registers_bundle_approves_baseline_and_records_replication_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = OwnerEnvironment(tmp_path, monkeypatch)
    dataset = _recorded_dataset()
    tpl_views = _template_entries("qlib_alpha158:KMID")
    template = tpl_views[0]["entry"]["metadata"]["alpha_template"]
    demand = env.research.research_demand_store.open(
        TENANT,
        {
            "persona_id": "persona-alpha",
            "mandate": "TW equity",
            "missing_datasets": [],
            "tried_families": [],
            "short_measure": "effective_bets",
            "pool_version": 0,
        },
    )
    artifact = compile_template(
        template,
        universe=["2330"],
        demand=demand,
        source_ref="registry:" + tpl_views[0]["entry"]["registry_id"],
        dataset=dataset,
    )
    assert artifact["lineage"]["source_strategy_spec_id"] == artifact["artifact_id"]

    bt_store = SharedBacktestRunStore(env.data_dir / "backtest_runs.json")
    raw_gate = SharedReplicationGate(
        trial_counter_path=tmp_path / "trials.json",
        backtest_run_store=bt_store,
    )
    passing_eval = _governed_passing_evaluation_for_artifact(artifact, dataset)

    class _PreRecordedProtocolGate:
        def __init__(self, inner: SharedReplicationGate) -> None:
            self._inner = inner
            self.backtest_run_store = inner.backtest_run_store

        def get_family_trial_count(self, family: str) -> int:
            return self._inner.get_family_trial_count(family)

        def get_family_total_trial_count(self, family: str) -> int:
            return self._inner.get_family_total_trial_count(family)

        def record_family_trial(self, family: str) -> int:
            return self._inner.record_family_trial(family)

        def evaluate_candidate(self, request: Any) -> Any:
            self.backtest_run_store.record(passing_eval)
            request.backtest_evaluation = copy.deepcopy(passing_eval)
            return self._inner.evaluate_candidate(request)

    registrar = TemplateCandidateRegistrar(
        data_dir=env.data_dir,
        owner_call=env.owner_call,
        admission_store=env.research.alpha_replication_admission_store,
        queue_store=env.research.alpha_replication_queue,
        backtest_run_store=bt_store,
    )
    search = TemplateDemandSearch(
        store=env.research.research_demand_store,
        gate=_PreRecordedProtocolGate(raw_gate),
        context_loader=lambda _d: {"dataset": dataset, "universe": ["2330"], "templates": tpl_views},
        lock_path=env.data_dir / "template_search.lock",
        registrar=registrar,
        run_budget=1,
    )
    updated_demand = search.run(demand)
    attempts = updated_demand["discovery"]["template_search"]["attempts"]
    assert artifact["artifact_id"] in attempts
    receipt = attempts[artifact["artifact_id"]]
    assert receipt["status"] == "admitted"
    assert receipt["registry_id"] == artifact["artifact_id"]
    assert receipt["baseline_ref"].startswith("apv-tpl-")
    reg = receipt["registration"]
    assert reg["status"] == "approved"
    assert reg["demand_ref"] == demand["demand_id"]
    assert reg["persona_id"] == "persona-alpha"
    assert reg["tenant_id"] == TENANT
    assert reg["source_strategy_spec_id"] == artifact["artifact_id"]

    # Verify the Registry entry is approved, carries the verified gate proof, and resolves its baseline.
    got = env.registry_client.get(
        f"/api/registry/strategy-artifacts/{artifact['artifact_id']}",
        headers=env.registry_headers(TENANT),
    )
    assert got.status_code == 200, got.text
    entry = got.json()["entry"]
    assert entry["artifact_state"] == "approved"
    assert entry["artifact_type"] == "execution_bundle"
    assert entry["approval_decision_id"] == reg["approval_decision_id"]
    assert entry["replication_success"] is True
    assert entry["replication_proof"]["owner_verified_binding"]["producer_verified"] is True
    assert entry["lineage"]["source_strategy_spec_id"] == artifact["artifact_id"]
    assert entry["metadata"]["demand_ref"] == demand["demand_id"]
    assert entry["metadata"]["persona_id"] == "persona-alpha"

    baseline = resolve_member_baseline(
        tenant_id=TENANT,
        persona_id="persona-alpha",
        artifact_ref=artifact["artifact_id"],
        checksum=entry["checksum"],
        entry=entry,
    )
    assert baseline["status"] == "approved"
    assert baseline["oos_sharpe"] == passing_eval["out_of_sample"]["sharpe_ratio"]

    # Verify Research alpha-replication entries endpoint returns the admitted, completed entry.
    rep_resp = env.research_client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_cycle_headers(),
    )
    assert rep_resp.status_code == 200, rep_resp.text
    rows = rep_resp.json()
    assert len(rows) == 1
    assert rows[0]["strategy_spec_id"] == artifact["artifact_id"]
    assert rows[0]["admission_status"] == "admitted"
    assert rows[0]["queue_status"] == "completed"
    assert rows[0]["persona_id"] == "persona-alpha"
    assert rows[0]["demand_ref"] == demand["demand_id"]

    # Idempotent replay: calling register_passed_attempt again returns the same registration without duplicate approvals.
    before_gov_calls = len(env.governance.calls)
    replayed = registrar.register_passed_attempt(
        demand=demand,
        template=template,
        artifact=artifact,
        receipt=receipt,
        backtest_evaluation=passing_eval,
    )
    assert replayed["approval_decision_id"] == reg["approval_decision_id"]
    assert replayed["registry_id"] == reg["registry_id"]
    assert len(env.governance.calls) == before_gov_calls


def test_failing_template_attempt_registers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = OwnerEnvironment(tmp_path, monkeypatch)
    dataset = _recorded_dataset()
    tpl_views = _template_entries("qlib_alpha158:KMID")
    demand = env.research.research_demand_store.open(
        TENANT,
        {
            "persona_id": "persona-alpha",
            "mandate": "TW equity",
            "missing_datasets": [],
            "tried_families": [],
            "short_measure": "effective_bets",
            "pool_version": 0,
        },
    )
    bt_store = SharedBacktestRunStore(env.data_dir / "backtest_runs.json")
    gate = SharedReplicationGate(
        trial_counter_path=tmp_path / "trials.json",
        backtest_run_store=bt_store,
    )
    registrar = TemplateCandidateRegistrar(
        data_dir=env.data_dir,
        owner_call=env.owner_call,
        admission_store=env.research.alpha_replication_admission_store,
        queue_store=env.research.alpha_replication_queue,
        backtest_run_store=bt_store,
    )
    search = TemplateDemandSearch(
        store=env.research.research_demand_store,
        gate=gate,
        context_loader=lambda _d: {"dataset": dataset, "universe": ["2330"], "templates": tpl_views},
        lock_path=env.data_dir / "template_search.lock",
        registrar=registrar,
        run_budget=1,
    )
    updated = search.run(demand)
    attempts = updated["discovery"]["template_search"]["attempts"]
    assert len(attempts) == 1
    aid, receipt = next(iter(attempts.items()))
    assert receipt["status"] == "rejected"
    assert "registration" not in receipt

    # Registry, Governance, and Replication stores remain completely untouched.
    assert env.governance.decisions == {}
    assert env.research.alpha_replication_admission_store.list_admissions(TENANT) == []
    assert env.research.alpha_replication_queue.list_all() == []
    got = env.registry_client.get(
        f"/api/registry/strategy-artifacts/{aid}",
        headers=env.registry_headers(TENANT),
    )
    assert got.status_code == 404

    # Direct invocation with a rejected receipt fails closed and writes nothing.
    template = tpl_views[0]["entry"]["metadata"]["alpha_template"]
    artifact = compile_template(
        template,
        universe=["2330"],
        demand=demand,
        source_ref="registry:" + tpl_views[0]["entry"]["registry_id"],
        dataset=dataset,
    )
    with pytest.raises(CandidateRegistrationError, match="attempt_did_not_pass_gate"):
        registrar.register_passed_attempt(
            demand=demand,
            template=template,
            artifact=artifact,
            receipt=receipt,
        )
    assert env.governance.decisions == {}


def test_registration_retry_after_transient_owner_failure_does_not_recharge_family_trial_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = OwnerEnvironment(tmp_path, monkeypatch)
    dataset = _recorded_dataset()
    tpl_views = _template_entries("qlib_alpha158:KMID")
    template = tpl_views[0]["entry"]["metadata"]["alpha_template"]
    demand = env.research.research_demand_store.open(
        TENANT,
        {
            "persona_id": "persona-alpha",
            "mandate": "TW equity",
            "missing_datasets": [],
            "tried_families": [],
            "short_measure": "effective_bets",
            "pool_version": 0,
        },
    )
    artifact = compile_template(
        template,
        universe=["2330"],
        demand=demand,
        source_ref="registry:" + tpl_views[0]["entry"]["registry_id"],
        dataset=dataset,
    )
    passing_eval = _governed_passing_evaluation_for_artifact(artifact, dataset)
    bt_store = SharedBacktestRunStore(env.data_dir / "backtest_runs.json")
    raw_gate = SharedReplicationGate(
        trial_counter_path=tmp_path / "trials.json",
        backtest_run_store=bt_store,
    )

    class _PreRecordedProtocolGate:
        def __init__(self, inner: SharedReplicationGate) -> None:
            self._inner = inner
            self.backtest_run_store = inner.backtest_run_store

        def get_family_trial_count(self, family: str) -> int:
            return self._inner.get_family_trial_count(family)

        def get_family_total_trial_count(self, family: str) -> int:
            return self._inner.get_family_total_trial_count(family)

        def record_family_trial(self, family: str) -> int:
            return self._inner.record_family_trial(family)

        def evaluate_candidate(self, request: Any) -> Any:
            self.backtest_run_store.record(passing_eval)
            request.backtest_evaluation = copy.deepcopy(passing_eval)
            return self._inner.evaluate_candidate(request)

    fail_governance = True

    def flaky_owner_call(**kwargs: Any) -> Any:
        if fail_governance and kwargs.get("service") == "governance":
            raise RuntimeError("governance_temporarily_unreachable")
        return env.owner_call(**kwargs)

    registrar = TemplateCandidateRegistrar(
        data_dir=env.data_dir,
        owner_call=flaky_owner_call,
        admission_store=env.research.alpha_replication_admission_store,
        queue_store=env.research.alpha_replication_queue,
        backtest_run_store=bt_store,
    )
    search = TemplateDemandSearch(
        store=env.research.research_demand_store,
        gate=_PreRecordedProtocolGate(raw_gate),
        context_loader=lambda _d: {"dataset": dataset, "universe": ["2330"], "templates": tpl_views},
        lock_path=env.data_dir / "template_search.lock",
        registrar=registrar,
        family_budget=1,
        run_budget=1,
    )

    # First run: gate passes (trial count = 1), governance fails mid-registration.
    first = search.run(demand)
    family = f"template:{template['origin']['library']}:{template['family']}"
    assert raw_gate.get_family_total_trial_count(family) == 1
    assert first["discovery"]["template_search"]["reason"].startswith("template_search_unavailable:RuntimeError")
    assert first["discovery"]["template_search"]["attempts"][artifact["artifact_id"]]["status"] == "admitted"
    assert "registration" not in first["discovery"]["template_search"]["attempts"][artifact["artifact_id"]]

    # Second run (replay after recovery): completes registration without re-charging the family trial counter!
    fail_governance = False
    second = search.run(demand)
    assert raw_gate.get_family_total_trial_count(family) == 1
    assert second["discovery"]["template_search"]["reason"] == "search_completed"
    reg = second["discovery"]["template_search"]["attempts"][artifact["artifact_id"]]["registration"]
    assert reg["status"] == "approved"
    assert reg["registry_id"] == artifact["artifact_id"]


def test_register_requalified_member_creates_new_immutable_entry_with_legacy_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = OwnerEnvironment(tmp_path, monkeypatch)
    dataset = _recorded_dataset()
    tpl_views = _template_entries("qlib_alpha158:KMID")
    template = tpl_views[0]["entry"]["metadata"]["alpha_template"]
    demand = env.research.research_demand_store.open(
        TENANT,
        {
            "persona_id": "persona-alpha",
            "mandate": "TW equity",
            "missing_datasets": [],
            "tried_families": [],
            "short_measure": "effective_bets",
            "pool_version": 0,
        },
    )
    legacy_artifact = compile_template(
        template,
        universe=["2330"],
        demand=demand,
        source_ref="registry:" + tpl_views[0]["entry"]["registry_id"],
        dataset=dataset,
    )
    legacy_id = legacy_artifact["artifact_id"]
    legacy_entry = {"registry_id": legacy_id, "version": "1.0.0"}

    requalified = copy.deepcopy(legacy_artifact)
    requalified["artifact_id"] = f"{legacy_id}-requalify-001"
    requalified["version"] = "1.1.0"
    requalified["lineage"] = {
        **dict(legacy_artifact["lineage"]),
        "parent_registry_ids": [legacy_id],
    }
    passing_eval = _governed_passing_evaluation_for_artifact(requalified, dataset, run_id="bt-req-pass-001")
    bt_store = SharedBacktestRunStore(env.data_dir / "backtest_runs.json")
    bt_store.record(passing_eval)
    raw_gate = SharedReplicationGate(
        trial_counter_path=tmp_path / "trials.json",
        backtest_run_store=bt_store,
    )
    from services.research.replication.gate_schema import ReplicationRequest

    req = ReplicationRequest(
        candidate_id="req-cand-1",
        source_task_id="evd-req-1",
        research_handoff={
            "source_metadata": {
                "api_endpoint": "evolution://decisions/evd-req-1",
                "retrieved_at": "2026-11-16T00:00:00Z",
                "governance_context": "Approved EvolutionDecision evd-req-1",
            },
            "normalized_findings": {
                "strategy_spec": {"strategy_id": requalified["strategy_id"], "name": requalified["strategy_id"]},
                "replication_notes": "Re-qualification",
                "evaluation_hypotheses": "H1: positive OOS Sharpe",
            },
            "family_id": requalified["strategy_id"],
            "strategy_artifact": requalified,
            "backtest_dataset": {**dataset, "strategy_id": requalified["strategy_id"]},
        },
        proposed_strategy_spec={
            "name": requalified["strategy_id"],
            "description": "Re-qualified",
            "signals": requalified["strategy_logic"],
            "parameters": dict(requalified["parameters"]),
            "governance": {"approval_required": True, "policy_id": "oc-001-research-normalization"},
        },
        backtest_evaluation=passing_eval,
        metadata={
            "tenant_id": TENANT,
            "persona_id": "persona-alpha",
            "decision_id": "evd-req-1",
            "dataset_version_id": passing_eval["dataset_id"],
            "strategy_artifact_checksum": strategy_artifact_checksum(requalified),
        },
        timestamp="2026-11-16T00:00:00Z",
    )
    gate_resp = raw_gate.evaluate_candidate(req)
    assert gate_resp.passed

    registrar = TemplateCandidateRegistrar(
        data_dir=env.data_dir,
        owner_call=env.owner_call,
        admission_store=env.research.alpha_replication_admission_store,
        queue_store=env.research.alpha_replication_queue,
        backtest_run_store=bt_store,
    )
    with pytest.raises(CandidateRegistrationError, match="requalified_artifact_must_be_new_registry_entry"):
        registrar.register_requalified_member(
            tenant_id=TENANT,
            persona_id="persona-alpha",
            decision_id="evd-req-1",
            legacy_entry=legacy_entry,
            artifact=legacy_artifact,
            gate_proof=gate_resp.to_dict(),
            backtest_evaluation=passing_eval,
        )
    no_lineage = copy.deepcopy(requalified)
    no_lineage["lineage"]["parent_registry_ids"] = []
    with pytest.raises(CandidateRegistrationError, match="requalified_artifact_missing_legacy_lineage"):
        registrar.register_requalified_member(
            tenant_id=TENANT,
            persona_id="persona-alpha",
            decision_id="evd-req-1",
            legacy_entry=legacy_entry,
            artifact=no_lineage,
            gate_proof=gate_resp.to_dict(),
            backtest_evaluation=passing_eval,
        )

    reg = registrar.register_requalified_member(
        tenant_id=TENANT,
        persona_id="persona-alpha",
        decision_id="evd-req-1",
        legacy_entry=legacy_entry,
        artifact=requalified,
        gate_proof=gate_resp.to_dict(),
        backtest_evaluation=passing_eval,
    )
    assert reg["status"] == "approved"
    assert reg["registry_id"] == requalified["artifact_id"]
    assert reg["replaces_registry_id"] == legacy_id
    got = env.registry_client.get(
        f"/api/registry/strategy-artifacts/{requalified['artifact_id']}",
        headers=env.registry_headers(TENANT),
    ).json()["entry"]
    assert got["artifact_state"] == "approved"
    assert got["lineage"]["parent_registry_ids"] == [legacy_id]
    assert got["metadata"]["replaces_registry_id"] == legacy_id
    baseline = resolve_member_baseline(
        tenant_id=TENANT,
        persona_id="persona-alpha",
        artifact_ref=requalified["artifact_id"],
        checksum=got["checksum"],
        entry=got,
    )
    assert baseline["status"] == "approved"

