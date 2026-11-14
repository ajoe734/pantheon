"""Persona pool HTTP -> persisted case -> owner outcome -> Memory HTTP ranking.

Registry, cron/provider responses and paper book values are local test fixtures;
the owner policy, case composer, persistence and application routes are real.
This is source integration evidence, not hosted or performance evidence.
"""
import asyncio
import copy
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from services.control_plane.persona.test_persona_strategy_pool import (
    CHECKSUM, _auth, _decision, _member, _post, env,  # noqa: F401
)
from services.knowledge import case_file as cf
from services.memory.test_hindsight_index import persona
from services.memory.test_usefulness import auth, memory  # noqa: F401
from services.memory.usefulness import usefulness_score
from services.persona.case_file_outcomes import CaseFileOutcomes, OutcomeTrackingController, STATE_KEY
from services.persona.cron_ooda_closure import append_ooda_packet

DECIDED = "2026-11-13T15:00:00+00:00"
NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)


@pytest.fixture
def outcome(env, memory, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "control-plane" / "persona"))
    persona_client, registry, owner = env
    memory_client, (_, per) = memory
    module = sys.modules["persona_main_pool_test_module"]
    pool = module.POOL_STORE
    raw = owner._records.get("persona-1")
    assert owner._records.compare_and_set("persona-1", raw, {**raw, "lifecycle_state": "paper_owner"})[0]
    registry.add("loser", persona="persona-1")
    before = _post(persona_client, [_member("loser")], 0).json()
    pool._now = lambda: "2026-11-13T14:00:00+00:00"
    book = {"checksum": CHECKSUM, "source_ref": "test:paper-owner-before", "since_inception": True,
            "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
            "dates": ["2026-11-10", "2026-11-11", "2026-11-12"], "returns": [.01, .02, -.1]}
    removed = _decision(persona_client, 1, books={"loser": book})
    assert removed.status_code == 201, removed.text
    after = removed.json()
    assert after["members"][0]["stage"] == "removed", after
    learned = persona(tenant_id="tenant-a", persona_id="persona-1",
                      content={"summary": "Reduce momentum exposure", "tags": ["family:momentum"]})
    assert memory_client.post("/api/memory/persona-entries", json=learned.to_dict(), headers=auth("memory-writer")).status_code == 201

    def owner_http(endpoint, path, *, tenant_id, params=None, **kwargs):
        if endpoint.base_url == "memory":
            response = memory_client.get(path, params=params, headers=auth("operator", tenant_id))
            assert response.status_code == 200, response.text
            return response.json()
        if endpoint.base_url == "persona":
            response = persona_client.get(path, headers=_auth(["viewer"], tenant_id))
            assert response.status_code == 200, response.text
            return response.json()
        raise cf.OwnerUnavailable("fixture_owner_unavailable")
    monkeypatch.setattr(cf, "http_json", owner_http)
    case = cf.compose_case_file(cf.Subject("member_removal", "persona-1", "tenant-a", "momentum",
        member_artifact_refs=("loser",)), cf.Caller("memory-operator-1", ("operator",), "fixture-case", "persona-1"),
        cf.Owners(memory=cf.OwnerEndpoint("memory"), pool=cf.OwnerEndpoint("persona"),
                  registry=cf.OwnerEndpoint("registry"), search=cf.OwnerEndpoint("search")))
    item = next(i for i in case["groups"]["lessons"]["items"] if i["source_id"] == learned.memory_id)
    decision = {"kind": "remove", "status": "deterministic_risk_only", "case_file_id": case["case_file_id"],
                "case_file_digest": case["digest"], "cited_case_file_item_ids": [item["item_id"]]}
    packet = {"packet_id": "decision-packet", "tenant_id": "tenant-a", "persona_ids": ["persona-1"],
        "environment": "paper", "created_at": DECIDED, "updated_at": DECIDED,
        "persona_cycle": {"phase": "terminal", "harness_version": 1, "decisions": [decision],
            "case_files": {case["case_file_id"]: case}, "observe_bundle": {"pool": before},
            "risk_result": {"status": "applied", "receipt_ref": "/api/personas/persona-1/strategy-pool/versions/2"}}}
    packets = {packet["packet_id"]: packet}
    sent = []
    def send(payload):
        sent.append(payload)
        response = memory_client.post("/api/memory/writebacks/learn-feedback", json=payload, headers=auth())
        assert response.status_code == 201, response.text
        return response.json()
    tracker = CaseFileOutcomes(owner, pool, now=lambda: NOW, sender=send)
    return SimpleNamespace(owner=owner, pool=pool, packets=packets, tracker=tracker, learned=learned,
        per=per, sent=sent, module=module, memory_client=memory_client, path=tmp_path / "packets.jsonl")


def window(o, returns=-.01, count=10):
    days = [(date(2026, 11, 14) + timedelta(days=i)).isoformat() for i in range(count)]
    # Explicit owner dates (including weekend sessions) are never guessed by production.
    packet = {"packet_id": "outcome-packet", "tenant_id": "tenant-a", "persona_ids": ["persona-1"],
        "environment": "paper", "created_at": "2026-11-25T15:00:00+00:00", "updated_at": "2026-11-25T15:00:00+00:00",
        "persona_cycle": {"phase": "terminal", "harness_version": 1, "observe_bundle": {
            "persona_id": "persona-1", "unavailable": [], "incident_refs": [], "incidents": {"status": "observed"},
            "observations": [{"date": d, "source_ref": "fixture:trading-calendar"} for d in days],
            "books": {"loser": {"checksum": CHECKSUM, "source_ref": "fixture:paper-owner-outcome",
                "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
                "dates": days, "returns": [returns] * count}}}}}
    o.packets[packet["packet_id"]] = packet
    return packet


def reviewed(o):
    return next(iter(o.tracker.reconcile("persona-1", o.packets)["decisions"].values()))


@pytest.mark.parametrize("returns,expected", [(-.01, "helped"), (.01, "hurt"), (0., "no_effect")])
def test_real_pool_removal_routes_mark_cited_lesson_and_change_ranking(outcome, returns, expected):
    o = outcome
    assert reviewed(o)["status"] == "pending"
    assert not o.sent
    before_score = usefulness_score(o.per.require(o.learned.memory_id))
    window(o, returns)
    row = reviewed(o)
    assert row["outcome"] == expected, row
    entry = o.per.require(o.learned.memory_id)
    assert list(entry.usefulness_marks.values())[0]["outcome"] == expected
    response = o.memory_client.get("/api/memory/retrieve", headers=auth("operator"), params={
        "actor_id": "memory-operator-1", "actor_roles": "operator", "session_id": "outcome-test", "scope": "persona",
        "persona_id": "persona-1", "query": "momentum"})
    assert response.status_code == 200, response.text
    score = response.json()["hits"][0]["relevance_score"]
    assert score == pytest.approx(10 + usefulness_score(entry), abs=.0001)
    assert (usefulness_score(entry) > before_score) == (expected == "helped")
    assert (usefulness_score(entry) < before_score) == (expected == "hurt")
    assert reviewed(o)["outcome"] == expected
    assert len(o.sent) == 1
    # Process replacement recovers the same stable decision and delivered receipt.
    o.tracker = CaseFileOutcomes(o.owner, o.pool, now=lambda: NOW, sender=o.tracker.sender)
    assert reviewed(o)["outcome"] == expected and len(o.sent) == 1


@pytest.mark.parametrize("missing", ["book", "checksum", "confounder", "harness", "incident", "window", "scope", "case"])
def test_unresolved_outcomes_never_claim_improvement(outcome, missing):
    o = outcome
    packet = window(o)
    bundle = packet["persona_cycle"]["observe_bundle"]
    if missing == "book":
        bundle["books"] = {}
    elif missing == "checksum":
        bundle["books"]["loser"]["checksum"] = "wrong"
    elif missing == "confounder":
        bundle["confounders"] = ["regime_changed"]
    elif missing == "harness":
        packet["persona_cycle"]["harness_version"] = 2
    elif missing == "incident":
        bundle["incidents"]["status"] = "unavailable"
    elif missing == "window":
        bundle["observations"] = []
    elif missing == "scope":
        packet["tenant_id"] = "other-tenant"
    elif missing == "case":
        o.packets["decision-packet"]["persona_cycle"]["decisions"][0]["case_file_digest"] = "wrong"
    o.tracker.now = lambda: NOW + timedelta(days=20)
    row = reviewed(o)
    assert row["outcome"] == "confounded", row
    assert row["delta"] is None and row["reason"]
    assert usefulness_score(o.per.require(o.learned.memory_id)) == 0


def test_delivery_retry_repairs_lost_receipt_without_double_counting(outcome):
    o = outcome
    window(o)
    sender = o.tracker.sender
    def lost_receipt(payload):
        sender(payload)
        raise OSError("fixture lost response")
    o.tracker.sender = lost_receipt
    assert reviewed(o)["outcome"] == "helped"
    state = o.owner.get("persona-1").metadata[STATE_KEY]
    assert next(iter(state["outbox"].values()))["status"] == "pending"
    o.tracker.sender = sender
    assert reviewed(o)["outcome"] == "helped"
    assert len(o.per.require(o.learned.memory_id).usefulness_marks) == 1
    assert next(iter(o.owner.get("persona-1").metadata[STATE_KEY]["outbox"].values()))["status"] == "delivered"


def test_repeated_proposals_for_one_owner_action_do_not_multiply_harm(outcome):
    o = outcome
    window(o, .01)
    decisions = o.packets["decision-packet"]["persona_cycle"]["decisions"]
    decisions.extend([copy.deepcopy(decisions[0]), copy.deepcopy(decisions[0])])
    result = o.tracker.reconcile("persona-1", o.packets)
    assert [r["outcome"] for r in result["decisions"].values()] == ["hurt", "confounded", "confounded"]
    assert o.per.require(o.learned.memory_id).memory_review is None


def test_production_lifespan_wires_owner_reconciliation_and_routes(outcome, monkeypatch):
    """Actual enabled Persona lifespan, with only cron registration/runner boundaries substituted."""
    o = outcome
    window(o)
    for packet in o.packets.values():
        append_ooda_packet(o.path, packet)
    monkeypatch.setenv("PERSONA_DAILY_CYCLE_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(o.path))
    import services.persona.persona_cycle_controller as cycle
    import services.control_plane.cron.persona_cron_registrar as cron
    # No terminal cron run in this poll; persisted owner packets still reconcile.
    registrar = SimpleNamespace(reconcile_daily_cycles=lambda p: {"jobs": {}, "failures": []},
                                _get_runtime=lambda: None)
    monkeypatch.setattr(cron, "PersonaCronRegistrar", lambda: registrar)
    reports = []
    async def runner(controller, stop):
        assert isinstance(controller, OutcomeTrackingController)
        controller.outcomes.now = o.tracker.now
        controller.outcomes.sender = o.tracker.sender
        reports.append(controller.reconcile_once())
        await stop.wait()
    monkeypatch.setattr(cycle, "run_controller", runner)
    async def run():
        async with o.module.persona_lifespan(o.module.app):
            await asyncio.sleep(0)
    asyncio.run(run())
    assert reports and reports[0]["failures"] == [], reports
    assert list(o.per.require(o.learned.memory_id).usefulness_marks.values())[0]["outcome"] == "helped"
