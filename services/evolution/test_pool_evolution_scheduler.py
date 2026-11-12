"""Cadence uses dated observed member books and owner-approved baselines."""
import copy
from datetime import date, timedelta

import pytest

from services.evolution.auto_consumer import ListEvidenceSink
from services.evolution.decay_monitor import WindowLedger
from services.evolution.pool_evolution_scheduler import PoolEvolutionScheduler
from services.evolution.test_approved_baselines import book, genuine_entry


@pytest.fixture
def lane(tmp_path):
    entry = genuine_entry()
    entry["version"] = "1.0.0"
    ref = entry["registry_id"]
    member = {"artifact_ref": ref, "checksum": entry["checksum"], "stage": "active", "risk_weight": 1,
              "baseline_ref": entry["approval_decision_id"], "joined_at": "2024-01-01T00:00:00Z"}
    pool = {"tenant_id": "t1", "persona_id": "p1", "pool_version": 1, "members": [member]}
    days = [(date(2024, 1, 2) + timedelta(days=n)).isoformat() for n in range(20)]

    class Owner:
        trials = 0
        def pools(self): return [pool]
        def publish(self, _): return {"status": "published"}
        def entry(self, _): return entry
        def books(self, _): return {ref: self.observation}
        def family_state(self, _): return {"trials": self.trials}

    class Decisions:
        proposals = []
        def propose(self, body): self.proposals.append(body)

    owner, decisions, evidence = Owner(), Decisions(), ListEvidenceSink()
    owner.observation = {**book(sharpe=10), "checksum": entry["checksum"], "dates": days, "source_ref": "unit-observations"}
    scheduler = PoolEvolutionScheduler(owner=owner, decisions=decisions, evidence=evidence,
        ledger=WindowLedger(tmp_path / "windows.jsonl"), clock=lambda: date(2026, 11, 12))
    return scheduler, pool, entry, owner, decisions, evidence


def test_twenty_observed_days_revalidate_and_weekly_retrain_as_low_risk_controller(lane):
    scheduler, pool, entry, owner, decisions, _ = lane
    report = scheduler.tick()
    assert not report.errors
    assert [p["action_type"] for p in decisions.proposals] == ["revalidate", "retrain"]
    assert all(p["created_by_role"] == "evolution_controller" and p["risk_level"] == "low"
               and p["target_stage"] == "paper" and p["metadata"]["variants"] == 50 for p in decisions.proposals)
    first_ids = report.proposed
    pool["pool_version"] = 99
    assert scheduler.tick().proposed == first_ids  # publication cannot reset the cadence
    owner.observation["dates"] = owner.observation["dates"][:19]
    decisions.proposals.clear()
    scheduler.tick()
    assert [p["action_type"] for p in decisions.proposals] == ["retrain"]


def test_missing_baseline_is_skipped_with_durable_reason(lane):
    scheduler, _, entry, _, decisions, evidence = lane
    entry.pop("replication_proof")
    report = scheduler.tick()
    assert list(report.skipped.values())[0].startswith("no_approved_baseline:")
    assert not decisions.proposals and evidence.entries[-1]["skipped"] == report.skipped


def test_family_500_is_tried_and_does_not_suppress_revalidation(lane):
    scheduler, _, _, owner, decisions, _ = lane
    owner.trials = 500
    report = scheduler.tick()
    assert list(report.skipped.values()) == ["family_tried"]
    assert [p["action_type"] for p in decisions.proposals] == ["revalidate"]


def test_factor_rule_uses_mutate_with_fifty_variants(lane, monkeypatch):
    # The strategy-kind branch is independent of baseline proof validation.
    import services.evolution.pool_evolution_scheduler as module
    scheduler, _, entry, _, decisions, _ = lane
    original = module.resolve_member_baseline
    baseline = original(tenant_id="t1", persona_id="p1", artifact_ref=entry["registry_id"],
                        checksum=entry["checksum"], entry=entry)
    monkeypatch.setattr(module, "resolve_member_baseline", lambda **_: baseline)
    entry["metadata"]["strategy_artifact"]["strategy_logic"]["kind"] = "factor_rule_v1"
    scheduler.tick()
    assert decisions.proposals[-1]["action_type"] == "mutate"
    assert decisions.proposals[-1]["metadata"]["variants"] == 50


@pytest.mark.parametrize("bad", ["checksum", "future", "duplicate", "source"])
def test_unbound_or_invalid_book_never_proposes(lane, bad):
    scheduler, _, _, owner, decisions, _ = lane
    owner.observation = copy.deepcopy(owner.observation)
    if bad == "checksum": owner.observation["checksum"] = "other"
    if bad == "future": owner.observation["dates"] = ["2099-01-01"]
    if bad == "duplicate": owner.observation["dates"] *= 2
    if bad == "source": owner.observation.pop("source_ref")
    assert scheduler.tick().errors and not decisions.proposals


def test_decay_uses_member_own_baseline_and_three_distinct_observations(lane):
    scheduler, _, _, owner, decisions, _ = lane
    owner.observation = copy.deepcopy(owner.observation)
    owner.observation['sharpe_ratio'] = 0.01
    owner.observation['max_drawdown'] = .5
    assert not any(p['action_type'] == 'retire' for p in decisions.proposals)
    for day in ('2024-01-22', '2024-01-23', '2024-01-24'):
        owner.observation['dates'].append(day)
        scheduler.tick()
    retire = [p for p in decisions.proposals if p['action_type'] == 'retire']
    assert len(retire) == 1
    assert retire[0]['metadata']['pool_member']['checksum'] == owner.observation['checksum']
    assert retire[0]['metadata']['decay']['baseline_sharpe'] > 0


def test_pending_publication_is_retried_before_scheduling(lane):
    scheduler, _, _, owner, decisions, _ = lane
    def unavailable(_):
        raise RuntimeError("publication pending")
    owner.publish = unavailable
    assert scheduler.tick().errors and not decisions.proposals
    owner.publish = lambda _: {"status": "published"}
    assert scheduler.tick().proposed
