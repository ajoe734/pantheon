"""Two consecutive daily cycles through the real persona schedule entry.

Real: PersonaCycleController.consume_run, CycleOwners.risk, PoolAutonomyCycle,
HttpPoolCyclePorts, PersonaStrategyPoolStore (policy + recorded-TWSE admission
replay) and ResearchDemandStore. Faked: only the owner HTTP transport, which
dispatches to those real owners, plus the OpenClaw turn.
"""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.persona.persona_cycle_controller import CycleOwners, PersonaCycleController, read_packets
from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle, governed_dataset_loader
from services.research.research_demand import ResearchDemandStore
from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset

TENANT, PID = "tenant-a", "persona-pool-cycle"
JOINED = "2026-10-05T00:00:00+00:00"


class FakeConsultationOwner:
    """The consultation owner's HTTP boundary (fixture). Opening records the request exactly as the real
    client posts it; the *provider* answers (transcripts, memo, publication) are driven by the test."""

    def __init__(self):
        self.requests, self.memos, self.transcripts, self.opened_by = {}, {}, [], []

    def factory(self, tenant_id=None, service_actor=None, **_):
        owner = self
        class Client:
            def list_requests(self, *, target_type=None, target_id=None, status=None, tenant_id=None):
                return [dict(r) for r in owner.requests.values()
                        if (not target_type or r["target_type"] == target_type) and (not target_id or r["target_id"] == target_id)]
            def get_request(self, request_id, *, tenant_id=None):
                return dict(owner.requests[request_id]) if request_id in owner.requests else None
            def list_memos(self, *, request_id=None, status=None):
                return [dict(m) for m in owner.memos.values()
                        if (not request_id or m["request_id"] == request_id) and (not status or m["status"] == status)]
            def get_memo(self, memo_id):
                return dict(owner.memos[memo_id]) if memo_id in owner.memos else None
            def list_transcripts(self):
                return [dict(t) for t in owner.transcripts]
            def open_persona_panel(self, requirement, *, persona_id, trace_id, task=None, evidence_refs=None,
                                   request_id=None, tenant_id=None):
                assert requirement.initiator_persona_id == persona_id and service_actor == f"persona:{persona_id}"
                assert request_id not in owner.requests, "duplicate consultation opened"
                owner.opened_by.append(service_actor)
                owner.requests[request_id] = {
                    "request_id": request_id, "tenant_id": tenant_id, "status": "submitted", "from_persona_id": persona_id,
                    "target_type": requirement.decision_type, "target_id": requirement.decision_id,
                    "metadata": {"panel": requirement.to_panel_metadata()}, "trace_id": trace_id}
                return {"request_id": request_id}
        return Client()

    def publish(self, recommendations):
        """Providers answered: one contribution per panel participant, then the memo is published."""
        (request,) = self.requests.values()
        self.transcripts.append({"request_id": request["request_id"], "events": [
            {"actor": {"actor_type": "persona", "actor_id": p["persona_id"]}, "event_type": "persona_contribution",
             "content": {"recommendation": recommendations[p["persona_id"]]}, "evidence_refs": ["review:" + p["persona_id"]]}
            for p in request["metadata"]["panel"]["participants"] if p["persona_id"] in recommendations]})
        request["status"] = "published"
        risk = recommendations.get("persona-risk-officer")
        self.memos["memo-" + request["request_id"]] = {
            "memo_id": "memo-" + request["request_id"], "request_id": request["request_id"], "status": "published",
            "target_type": request["target_type"], "target_id": request["target_id"],
            "recommendation": "approve" if risk == "approve" else "reject"}


class StubOwners(CycleOwners):
    """Real risk owner; Observe reads the real pool store with owner-supplied books."""

    def __init__(self, pool, books, observations):
        super().__init__(None, pool)
        self.books, self.observations = books, observations

    def observe(self, persona, *, since=None):
        pool = self.pool_store.current(PID, TENANT)
        # Like telemetry's member_virtual_books: only bound (admitted, live) members have a book.
        live = {m["artifact_ref"] for m in pool["members"] if m["stage"] != "removed"}
        books = {ref: b for ref, b in self.books.items() if ref in live}
        return {"pool": pool, "pool_health": self.pool_store.health(PID, TENANT, books, self.observations),
                "books": books, "observations": self.observations, "unavailable": [],
                "telemetry_refs": [], "incident_refs": [], "human_feedback_refs": [], "harness_version": 1}


def _two_days(tmp_path, monkeypatch, *, registry, dataset, spec_id="spec-reviewed-001", replay_books=False,
              answers="approve", final_day=True):
    """Daily cycles through the real schedule entry; ``registry`` holds 'current' and 'candidate'.

    Day 1 opens the demand, day 2 opens the risk consultation (no admission), the providers then answer
    (``answers``: 'approve', 'oppose' or None = unanswered) and day 3 acts on the published memo.
    """
    from datetime import date
    monkeypatch.syspath_prepend(str(importlib.import_module("pathlib").Path(__file__).parents[2] / "control-plane" / "persona"))
    from services.control_plane.persona.persona_strategy_pool import (
        AppendPoolVersionRequest, PersonaStrategyPoolStore, PoolMember)

    current = registry["current"]
    artifact = registry["candidate"]["metadata"]["strategy_artifact"]
    pool = PersonaStrategyPoolStore(AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda rid, tenant: registry[rid], owner_lister=lambda pid, tenant: [])
    pool.append(PID, TENANT, AppendPoolVersionRequest(expected_version=0, change_reason="seed", members=[PoolMember(
        artifact_ref="current", checksum=current["checksum"], stage="active", risk_weight=.7,
        baseline_ref=current["approval_decision_id"], joined_at=JOINED if replay_books else "2026-01-01T00:00:00+00:00")]), actor_id="fixture")
    dates = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    books = {ref: {"checksum": e["checksum"], "source_ref": "test:virtual-book-owner", "since_inception": True,
                   "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional", "dates": dates,
                   "returns": ([.002, -.0005] if ref == "candidate" else [.0002, -.0004]) * 15} for ref, e in registry.items()}
    if replay_books:  # owner-supplied books are the real replay of each artifact on the recorded prices
        from services.control_plane.persona.pool_policy import load_policy
        from services.persona.pool_autonomy_cycle import candidate_virtual_book
        books = {ref: candidate_virtual_book(dataset=dataset, artifact=e["metadata"]["strategy_artifact"],
                                             checksum=e["checksum"], policy=load_policy()) for ref, e in registry.items()}
        # The current member joined 2026-10-05: its own telemetry book covers only the days since joining.
        cur = books["current"]
        keep = [i for i, d in enumerate(cur["dates"]) if d >= JOINED[:10]]
        books["current"] = {**cur, "dates": [cur["dates"][i] for i in keep], "returns": [cur["returns"][i] for i in keep]}
        dates = books["current"]["dates"]
    observations = [{"date": d, "source_ref": "test:risk-owner", "gross_notional": 20, "gross_limit": 100,
                     "drawdown": .01, "drawdown_limit": .1} for d in dates[-20:]]

    clock = [datetime.now(timezone.utc) - timedelta(days=1)]
    demands = ResearchDemandStore(tmp_path / "demands.json", now=lambda: clock[0])
    memory, supply, http, proposals = [], [], [], []

    def transport(method, service, path, tenant, body=None):
        http.append((method, service, path))
        assert tenant == TENANT
        if service == "research" and path.endswith("/persona-demands"):
            return demands.open(tenant, body)
        if service == "research" and path.endswith("/persona-demands/reconcile"):
            return {"demands": demands.reconcile(tenant, body["persona_id"], body["members"])}
        if service == "research" and "alpha-replication" in path:
            return list(supply)
        if service == "registry" and path.endswith("/entries") and "/strategies/" in path:
            return [{"entry": e, "deployment_stage": None} for e in registry.values()
                    if e["metadata"]["strategy_artifact"]["strategy_id"] == path.split("/")[4]]
        if service == "registry":
            return {"entry": registry[path.rsplit("/", 1)[1]]}
        if service == "evolution" and method == "POST" and path == "/api/evolution/proposals":
            if any(b["decision_id"] == body["decision_id"] for b in proposals):
                raise type("HTTPError", (Exception,), {"code": 409})()
            proposals.append(body)
            return {"decision_id": body["decision_id"], "decision_state": "proposed"}
        if service == "memory" and method == "POST":
            memory.append(body)
            return {"created": True}
        if service == "memory":
            return {"entries": [{"content": {"tags": m["tags"]}} for m in memory]}
        raise AssertionError((method, service, path))

    # Real scheduled construction: in-process persona owner plus the governed dataset loader reading the
    # configured authority paths. Only the authority snapshot reader serves the governed dataset.
    monkeypatch.syspath_prepend(str(importlib.import_module("pathlib").Path(__file__).parents[2] / "training-session"))
    authority = importlib.import_module("evaluation_authority")
    monkeypatch.setattr(authority, "load_evaluation_authority", lambda *a, **k: SimpleNamespace(vectorbt_dataset=dataset))
    env = {"PERSONA_POOL_CANONICAL_DATASET_PATH": "/governed/dataset.json",
           "PERSONA_POOL_THRESHOLD_POLICY_PATH": "/governed/policy.json"}
    consult = FakeConsultationOwner()
    import services.consultation.client as consultation_client
    monkeypatch.setattr(consultation_client, "ConsultationServiceClient", consult.factory)  # owner HTTP boundary only
    ports = HttpPoolCyclePorts(transport, pool_store=pool, dataset_loader=governed_dataset_loader(env), env=env)
    controller = PersonaCycleController(None, StubOwners(pool, books, observations), store_path=tmp_path / "packets.jsonl",
        turn_runner=lambda *a: SimpleNamespace(status="error", persona_id=PID, decision="", to_dict=lambda: {}),
        pool_cycle=PoolAutonomyCycle(ports))
    persona = {"persona_id": PID, "tenant_id": TENANT, "lifecycle_state": "paper_owner", "name": "Pool", "mandate": "Taiwan momentum",
               "required_data_sources": ["tw-margin-balance"]}

    def day(number):
        return controller.consume_run(persona, "job-1", {"runId": f"run-{number}", "runAtMs": 1794477600000 + number * 86400000,
                                                          "jobId": "job-1", "status": "ok"})

    cycle1 = day(1)["pool_autonomy"]
    assert cycle1["status"] == "completed" and cycle1["decide"]["kind"] == "open_demand"
    open_rows = demands.list(TENANT, PID, "open")
    assert len(open_rows) == 1 and open_rows[0]["short_measure"] == "gross_utilisation"
    assert open_rows[0]["mandate"] == "Taiwan momentum" and open_rows[0]["missing_datasets"] == ["tw-margin-balance"]
    tried = current["metadata"]["strategy_artifact"]["strategy_id"].lower()
    assert open_rows[0]["tried_families"] == [tried]
    assert cycle1["act"]["receipts"][0]["demand_id"] == open_rows[0]["demand_id"]

    # Research completes a reviewed-spec admission (real research-owner response shape, a StrategySpec identity);
    # the persona resolves it to the approved execution bundle and the next day decides on it.
    from services.research.tests.test_research_orchestrator_http_service import _load_service_module
    admission = {"tenant_id": TENANT, "strategy_spec_id": spec_id, "strategy_id": artifact["strategy_id"],
                 "spec_version": "1.0.0", "checksum": "sha256:" + "a" * 64, "status": "admitted"}
    supply.append(_load_service_module()._build_alpha_replication_entry(admission, {**admission, "status": "completed"}))
    clock[0] = datetime.now(timezone.utc) - timedelta(seconds=5)
    cycle2 = day(2)["pool_autonomy"]
    assert cycle2["decide"]["kind"] == "admit_trial", (cycle2["decide"], cycle2["orient"])
    assert cycle2["decide"]["candidate_ref"] == "candidate"  # resolved to the approved execution bundle, not the spec id
    # Day 2 only opens the risk consultation (as persona:<id>); nothing is admitted and nothing is learned as refused.
    opened = cycle2["act"]["receipts"][0]
    assert opened["route"] == "consultation:trial_admission" and opened["status"] == "consultation_opened", opened
    assert consult.opened_by == ["persona:" + PID] and len(consult.requests) == 1
    assert not any(m["artifact_ref"] == "candidate" for m in pool.current(PID, TENANT)["members"])
    assert cycle2["learn"]["memory_entries"] == ["admission_pending"] and cycle2["learn"]["tried_families"] == []
    assert not any(t.startswith("tried_family") for m in memory for t in m["tags"] if "admission_pending" in m["tags"])
    assert [d["status"] for d in demands.list(TENANT, PID)] == ["open"] and len(demands.list(TENANT, PID)) == 1
    if not final_day:
        return SimpleNamespace(cycle2=cycle2, pool=pool, memory=memory, artifact=artifact, consult=consult, day=day,
                               proposals=proposals, demands=demands, http=http)
    # Day 3 while the consultation is still unanswered: pending again, no second consultation, no admission.
    pending = day(3)["pool_autonomy"]
    assert pending["act"]["receipts"][0]["status"] == "consultation_pending" and len(consult.requests) == 1
    assert pending["learn"]["memory_entries"] == ["admission_pending"]
    if answers is not None:
        persona_ids = [p["persona_id"] for p in next(iter(consult.requests.values()))["metadata"]["panel"]["participants"]]
        consult.publish({pid_: ("approve" if answers == "approve" or pid_ != "persona-risk-officer" else "reject")
                         for pid_ in persona_ids})
    cycle2 = day(4)["pool_autonomy"]
    assert len(read_packets(controller.path)) == 4
    assert all(p["pool_autonomy"]["act"]["paper_only"] and not p["act"]["live_capital_side_effects"]
               for p in read_packets(controller.path).values())
    assert not any(service in ("capital", "provisioning") for _, service, _ in http)
    return SimpleNamespace(cycle2=cycle2, pool=pool, memory=memory, artifact=artifact, consult=consult, day=day,
                           proposals=proposals, demands=demands, http=http)


def _spec_linked(entry, spec_id="spec-reviewed-001"):
    return {**entry, "lineage": {**entry["lineage"], "source_strategy_spec_id": spec_id}}


FIXTURE = Path(__file__).with_name("fixtures") / "persona_cycle_twse_daily_recorded.json"


def twse_recorded_dataset(symbols=("2330", "2883")):
    """Recorded TWSE MI_INDEX daily bars (2024-11-11..2026-11-12, 487 trading days) for ``symbols``."""
    payload = json.loads(FIXTURE.read_text())
    number = lambda value: float(value.replace(",", ""))
    records = [{"instrument": sym + ".TWSE", "date": day["date"], "open": number(r[0]), "high": number(r[1]),
                "low": number(r[2]), "close": number(r[3]), "volume": number(r[4])}
               for day in payload["days"]
               if all(sym in day["rows"] and "--" not in day["rows"][sym][:4] for sym in symbols)
               for sym, r in day["rows"].items() if sym in symbols]
    return {"dataset_id": "recorded:twse:mi_index:20241111-20261112", "strategy_id": "pool-admission",
            "data_frequency": "daily", "source_dataset_refs": [d["source_url"] for d in payload["days"]], "records": records}


def twse_admittable_registry():
    """Registry responses (owner fixtures) for the recorded-TWSE candidate that passes the real admission policy.

    Returns ``(registry, dataset, new_artifact, checksum)``; asserts the candidate is not passed off as gate-approved.
    """
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.replication.gate import ReplicationGate
    from services.research.replication.test_gate import TestReplicationGate
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        build_canonical_tw_strategy_artifact, run_replication_backtest_protocol)

    dataset = twse_recorded_dataset()  # 2330 + 2883
    entries, one, _ = entries_and_members()
    shell = entries[one["artifact_ref"]]  # genuine registry response shell (gate-proof fields etc.)
    symbols = ["2330.TW", "2883.TW"]
    base = build_canonical_tw_strategy_artifact(strategy_id="strat-tw-2330-2883-lb10", symbols=symbols, lookback_bars=10,
                                                momentum_threshold=0.01)
    new = build_canonical_tw_strategy_artifact(strategy_id="strat-tw-2883-lb20", symbols=["2883.TW"], lookback_bars=20,
                                               momentum_threshold=0.01, source_strategy_spec_id="spec-reviewed-001")
    checksum = strategy_artifact_checksum(new)

    def owner_entry(name, artifact):
        """Owner HTTP fixture (recorded-data scope): a registry response whose gate proof is the owner's to give.

        Identity, checksum, run id and OOS metrics are the artifact's own computed values and its real backtest on
        the recorded prices; the persisted proof/binding is rewritten coherently around them (explicitly a fixture:
        the real ReplicationGate refuses this artifact, asserted below). The local baseline validator is untouched.
        """
        evaluation = run_replication_backtest_protocol(strategy_artifact=artifact, dataset=dataset, run_id="bt-" + name)
        oos = evaluation["out_of_sample"]
        run_id, cs = "bt-" + name, strategy_artifact_checksum(artifact)
        summary = {**shell["evaluation_summary"], "backtest_run_id": run_id,
                   "out_of_sample_sharpe_ratio": oos["sharpe_ratio"], "max_drawdown": oos["max_drawdown"]}
        proof = copy.deepcopy(shell["replication_proof"])
        proof["backtest_run_id"], proof["evaluation_summary"] = run_id, copy.deepcopy(summary)
        for result in proof["replication_results"]:
            if result.get("criterion_id") == "real_backtest_validation":
                result["details"].update({"backtest_run_id": run_id, "out_of_sample_sharpe_ratio": oos["sharpe_ratio"],
                                          "out_of_sample_max_drawdown": oos["max_drawdown"]})
        proof["owner_verified_binding"].update({"backtest_run_id": run_id, "artifact_checksum": cs,
            "out_of_sample_sharpe_ratio": oos["sharpe_ratio"], "out_of_sample_max_drawdown": oos["max_drawdown"]})
        return _spec_linked({**copy.deepcopy(shell), "registry_id": name, "checksum": cs,
            "approval_decision_id": "dec-fixture-" + name, "producer_run_id": run_id, "artifact_type": "execution_bundle",
            "evaluation_summary": summary, "replication_proof": proof,
            "lineage": {**shell["lineage"], "source_run_ids": [run_id]}, "metadata": {"strategy_artifact": artifact}})

    current, candidate = owner_entry("current", base), owner_entry("candidate", new)
    current["lineage"].pop("source_strategy_spec_id", None)
    registry = {"current": current, "candidate": candidate}
    # Real gate on the same artifact and recorded data: refused (not a genuinely gate-approved artifact).
    gate_dataset = twse_recorded_dataset(("2330", "2883", "0050"))  # + 0050 for the gate's benchmark requirement
    evaluation = run_replication_backtest_protocol(strategy_artifact=new, dataset=gate_dataset, run_id="bt-candidate-gate")
    request = TestReplicationGate("test_audit_log")._build_request()
    request.backtest_evaluation = evaluation
    request.metadata = {"strategy_artifact_checksum": checksum, "dataset_version_id": evaluation["dataset_id"]}
    gate = ReplicationGate()
    gate.backtest_run_store.record(evaluation)
    gate_failed = [r.criterion_id for r in gate.evaluate_candidate(request).results if not r.passed]
    assert gate_failed, "the candidate must not be passed off as gate-approved"
    return registry, dataset, new, checksum


def test_two_daily_cycles_demand_then_admitted_trial_member(tmp_path, monkeypatch):
    """Acceptance 7 on recorded TWSE prices with the unmodified pool policy and real gate result recorded.

    Only the owner HTTP responses are fixtures (AC7). See ``twse_admittable_registry`` for the registry fixtures; the pool
    store, admission replay, candidate book and pool policy are real and unmodified. The trial is admitted only after the
    risk consultation opened on one cycle is published and verified on a later one.
    """
    from services.registry.strategy_artifact import strategy_artifact_checksum
    registry, dataset, new, checksum = twse_admittable_registry()
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=dataset, replay_books=True)
    receipt = out.cycle2["act"]["receipts"][0]
    assert receipt["status"] != "refused", (receipt, out.pool.current(PID, TENANT).get("decision"), out.pool.current(PID, TENANT))
    members = {m["artifact_ref"]: m for m in out.pool.current(PID, TENANT)["members"]}
    assert members["candidate"]["stage"] == "trial" and members["candidate"]["checksum"] == checksum
    assert checksum == strategy_artifact_checksum(new)  # nothing rewritten
    assert out.cycle2["learn"]["memory_entries"][0] == "admitted", out.cycle2["learn"]
    assert any("admitted" in m["tags"] for m in out.memory)


def test_two_daily_cycles_genuine_fixture_artifact_refusal_recorded(tmp_path, monkeypatch):
    """The unmodified recorded-fixture artifacts are corr ~0.90 with the current member: refusal is recorded."""
    entries, one, two = entries_and_members()
    candidate = _spec_linked(copy.deepcopy(entries[one["artifact_ref"]]))
    registry = {"current": {**entries[two["artifact_ref"]], "registry_id": "current", "artifact_type": "execution_bundle"},
                "candidate": {**candidate, "registry_id": "candidate", "artifact_type": "execution_bundle"}}
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=recorded_dataset())
    receipt = out.cycle2["act"]["receipts"][0]
    assert receipt["status"] == "refused", receipt
    assert not any(m["artifact_ref"] == "candidate" for m in out.pool.current(PID, TENANT)["members"])
    assert out.cycle2["learn"]["memory_entries"] == ["admission_refused"], out.cycle2["learn"]
    assert any(f"tried_family:{out.artifact['strategy_id']}" in m["tags"] for m in out.memory)



# --- trial admission via risk persona consultation (moved from services/persona: needs the Research venv's numpy/vectorbt) ---
def test_consultation_opened_then_trial_member_admitted_after_approving_memo(tmp_path, monkeypatch):
    registry, dataset, _, checksum = twse_admittable_registry()
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=dataset, replay_books=True, answers="approve")
    pool = out.pool.current(PID, TENANT)
    members = {m["artifact_ref"]: m for m in pool["members"]}
    assert members["candidate"]["stage"] == "trial" and members["candidate"]["checksum"] == checksum
    receipt = out.cycle2["act"]["receipts"][0]
    request = next(iter(out.consult.requests.values()))
    memo_id = "memo-" + request["request_id"]
    assert receipt["status"] == "applied" and receipt["consultation_memo_id"] == memo_id
    # the pool owner linked the memo as evidence on the applied admission, bound to the consultation's pool version
    decision = pool["decision"]
    assert decision["consultation_memo_id"] == memo_id
    assert decision["evidence_refs"] == [{"ref_type": "consultation_memo", "ref_id": memo_id}]
    assert decision["consultation_pool_version"] == int(request["metadata"]["panel"]["gated_decision"]["target_version"])
    gated = request["metadata"]["panel"]["gated_decision"]
    assert gated["type"] == "trial_admission" and gated["candidate_digest"] and len(out.consult.requests) == 1
    assert out.consult.opened_by == ["persona:" + PID] and out.proposals == []
    assert out.cycle2["learn"]["memory_entries"] == ["admitted"]


def test_risk_oppose_becomes_operator_decision_and_never_admits(tmp_path, monkeypatch):
    registry, dataset, _, _ = twse_admittable_registry()
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=dataset, replay_books=True, answers="oppose")
    receipt = out.cycle2["act"]["receipts"][0]
    assert receipt["status"] == "operator_decision_required" and receipt["reason"] == "risk_persona_requires_operator"
    assert receipt["operator_decision"]["status"] == "queued"
    assert not any(m["artifact_ref"] == "candidate" for m in out.pool.current(PID, TENANT)["members"])
    (proposal,) = out.proposals
    assert proposal["metadata"]["operator_required"] is True and proposal["metadata"]["candidate"]["artifact_ref"] == "candidate"
    # not a refused admission and not a tried family
    assert out.cycle2["learn"]["memory_entries"] == ["admission_operator_decision"] and out.cycle2["learn"]["tried_families"] == []
    assert not any(t.startswith("tried_family:strat-tw-2883") for m in out.memory for t in m["tags"])
    # the next daily cycle neither admits nor opens a second consultation nor queues a second decision
    again = out.day(5)["pool_autonomy"]
    assert again["act"]["receipts"][0]["status"] == "operator_decision_required"
    assert again["act"]["receipts"][0]["operator_decision"]["status"] == "already_queued"
    assert len(out.consult.requests) == 1 and len(out.proposals) == 1
    assert not any(m["artifact_ref"] == "candidate" for m in out.pool.current(PID, TENANT)["members"])


def test_unanswered_consultation_stays_pending_and_missing_owner_fails_closed(tmp_path, monkeypatch):
    registry, dataset, _, _ = twse_admittable_registry()
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=dataset, replay_books=True, answers=None)
    receipt = out.cycle2["act"]["receipts"][0]
    assert receipt["status"] == "consultation_pending" and len(out.consult.requests) == 1
    assert not any(m["artifact_ref"] == "candidate" for m in out.pool.current(PID, TENANT)["members"])
    # consultation owner becomes unreachable: recorded reason, still no admission
    import services.consultation.client as consultation_client

    def down(*a, **k):
        raise ConnectionError("consultation owner down")
    monkeypatch.setattr(consultation_client, "ConsultationServiceClient", down)
    failed = out.day(5)["pool_autonomy"]["act"]["receipts"][0]
    assert failed["status"] == "not_attempted" and failed["reason"].startswith("consultation_unavailable:ConnectionError")
    assert not any(m["artifact_ref"] == "candidate" for m in out.pool.current(PID, TENANT)["members"])
