"""Trial admission goes through the risk persona consultation, via the real daily schedule entry.

Real: PersonaCycleController.consume_run, CycleOwners.risk, PoolAutonomyCycle, HttpPoolCyclePorts,
PersonaStrategyPoolStore (policy + recorded-TWSE admission replay), consult_triggers and the shared
published-memo verification (also used by harness adoption), ResearchDemandStore.
Faked: only owner and provider HTTP (registry/research/memory/evolution transports and the consultation
owner's boundary with provider answers) plus the OpenClaw turn. Paper only; no model call on the trade path.
"""
from __future__ import annotations

from services.research.tests.test_persona_cycle_demand_integration import (
    PID, TENANT, _two_days, twse_admittable_registry)


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
