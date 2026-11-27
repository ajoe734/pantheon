"""AGORA-TRADER-HABIT-PROFILE-20261127: trader habit facts, private interpretations, read and correct.

SD_AGORA_SERVANT_CORE_2026-11-27 §6. These tests run the real habit derivation, the real Agora
owner (over fake Postgres rows), the real BFF servant routes, the real servant turn worker and
the real persona-memory store and memory-governance authz. Only the language model is faked.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.agora.habit_profile import (
    FEATURE_KEYS,
    HABIT_MIN_SAMPLE,
    HABIT_PROFILE_HISTORY_VERSIONS,
    HABIT_PROFILE_MAX_BYTES,
    data_family_for,
    derive_habit_profile,
    profile_key,
)
from services.agora.shadow_mode import build_decision_lock
from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
from services.control_plane.bff.agora.servant import habits as habits_module
from services.control_plane.bff.agora.servant.habits import (
    HabitService,
    InProcessHabitMemory,
    carry_interpretation_in_minutes,
    derive_interpretations,
)
from services.control_plane.bff.agora.servant.turn_worker import ServantTurnWorker
from services.control_plane.bff.tests import test_agora_servant_data_tools as data_tools_tests
from services.control_plane.bff.tests import test_agora_servant_sessions as sessions
from services.governance.authz import evaluate_authz_request

TENANT = "pantheon-dev"
USER = "agora-test-user"
OTHER = "agora-other-user"
OTHER_AUTH = {"Authorization": f"Bearer {OTHER}:operator"}
AUTH = sessions.AUTH
T0 = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- data


def _event(symbol: str, kind: str, at: datetime, *, size: float = 1000, liquidity: float = 100000,
           sector: str = "電子", strategy: str = "strat-a") -> dict[str, Any]:
    return {
        "event_kind": kind,
        "subject": {"symbol": symbol},
        "strategy_id": strategy,
        "data_cutoff": at.isoformat(),
        "triggered_at": (at - timedelta(minutes=10)).isoformat(),
        "suggested_action": kind,
        "suggested_size": size,
        "snapshot": {"sector": sector, "price": 100.0, "avg_daily_turnover": liquidity},
    }


def _lock(n: int, event: dict[str, Any], verdict: str, at: datetime, *, user: str = USER,
          modifications: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_decision_lock(
        human_decision_id=f"hd-{user}-{n}", decision_record_id=f"dr-{user}-{n}", event=event,
        decision_event_id=f"de-{user}-{n}", tenant_id=TENANT, user_id=user, body_decision=verdict,
        rationale=None, modifications=modifications, decision_time=at.isoformat(),
    )


def trader_locks(user: str = USER, *, pairs: int = 14, rejects: int = 10) -> list[dict[str, Any]]:
    """A trader who holds ~3 days, enters 10 min after the signal, rejects illiquid 電子 names."""
    locks, n = [], 0
    for i in range(pairs):
        at = T0 + timedelta(days=i * 4)
        modify = i < 4
        locks.append(_lock(n, _event(f"23{30 + i}", "entry", at), "modify" if modify else "approve",
                           at, user=user,
                           modifications={"size": 500} if modify else None))
        n += 1
        exit_at = at + timedelta(days=3)
        locks.append(_lock(n, _event(f"23{30 + i}", "exit", exit_at), "approve", exit_at, user=user))
        n += 1
    for i in range(rejects):
        at = T0 + timedelta(days=60 + i)
        locks.append(_lock(n, _event(f"30{i:02d}", "entry", at, liquidity=1000, sector="半導體"), "reject", at, user=user))
        n += 1
    return locks


def _owner():
    sessions.FakeOwnerTable.reset()
    return sessions._build_owner()


def _seed(owner, locks):
    for lock in locks:
        owner.create_decision_lock(lock, ("trader",))


@pytest.fixture()
def harness(monkeypatch, tmp_path):
    """The real servant routes with a habit service over in-process private memory."""
    owner = _owner()
    memory = InProcessHabitMemory()
    dataset_store = AgoraDatasetStore(backend="memory")
    from services.agora.service import AgoraEvidenceWriteService
    from services.agora.store import AgoraEvidenceStore

    service = HabitService(
        owner=owner, memory=memory, evidence_owner=AgoraEvidenceWriteService(AgoraEvidenceStore(dataset_store)),
        clock=lambda: "2026-11-27T03:00:00Z",
    )
    monkeypatch.setattr(habits_module, "build_default_habit_service", lambda *a, **k: service)
    client, fake = sessions._install_client(monkeypatch, tmp_path, owner=owner, reset_rows=False)
    return SimpleHarness(client, owner, memory, dataset_store, service, fake)


class SimpleHarness:
    def __init__(self, client, owner, memory, dataset_store, service, fake):
        self.client, self.owner, self.memory = client, owner, memory
        self.dataset_store, self.service, self.fake = dataset_store, service, fake

    def get(self, auth=AUTH, **params):
        return self.client.get("/bff/agora/servant/habits", params=params, headers=auth)

    def patch(self, body, key="fix-001", auth=AUTH):
        return self.client.patch("/bff/agora/servant/habits", json=body, headers={**auth, "Idempotency-Key": key})


# --------------------------------------------------------------------------- AC1: deterministic facts


def test_sd_section_6_features_are_deterministic_and_carry_sample_size():
    locks = trader_locks()
    kwargs = dict(tenant_id=TENANT, user_id=USER, strategy_id=None, locks=locks, computed_at="2026-11-27T00:00:00Z")
    first = derive_habit_profile(**kwargs)
    again = derive_habit_profile(**{**kwargs, "locks": list(reversed(locks)), "computed_at": "2027-01-01T00:00:00Z"})
    strip = lambda p: {k: v for k, v in p.items() if k != "computed_at"}  # noqa: E731
    assert strip(first) == strip(again), "same inputs, same facts (order and clock are not inputs)"
    assert [f["key"] for f in first["features"]] == list(FEATURE_KEYS)
    assert first["derivation"] == {"method": "deterministic_rules.v1", "language_model": "none"}
    for feature in first["features"]:
        assert isinstance(feature["sample_size"], int) and feature["min_sample"] == HABIT_MIN_SAMPLE
        assert feature["shown"] == (feature["sample_size"] >= HABIT_MIN_SAMPLE and feature["value"] is not None)
        if not feature["shown"]:
            assert feature["value"] is None and feature["unavailable_reason"]

    by_key = {f["key"]: f for f in first["features"]}
    assert by_key["holding_period"]["sample_size"] == 14 and by_key["holding_period"]["value"]["median"] == pytest.approx(3.0, abs=0.01)
    assert by_key["entry_timing"]["value"]["median"] == 10.0 and by_key["entry_timing"]["sample_size"] == 14
    assert by_key["position_size"]["sample_size"] == 14
    rejected = by_key["rejected_candidates"]
    assert rejected["sample_size"] == 10
    assert rejected["value"]["low_liquidity_share"] == 1.0 and rejected["value"]["top_sector"] == "半導體"
    inter = by_key["intervention"]
    assert inter["sample_size"] == len(locks) and inter["value"]["intervention_count"] == 4
    assert inter["value"]["size_down_share"] == 1.0
    assert by_key["data_order"]["sample_size"] == 0 and not by_key["data_order"]["shown"]
    assert len(json.dumps(first, ensure_ascii=False).encode()) <= HABIT_PROFILE_MAX_BYTES


def test_a_feature_is_hidden_below_ten_samples():
    profile = derive_habit_profile(
        tenant_id=TENANT, user_id=USER, strategy_id=None, locks=trader_locks(pairs=9, rejects=9),
        computed_at="2026-11-27T00:00:00Z",
    )
    hidden = {f["key"]: f for f in profile["features"]}
    assert hidden["holding_period"]["sample_size"] == 9 and not hidden["holding_period"]["shown"]
    assert hidden["holding_period"]["unavailable_reason"] == "insufficient_sample"
    assert hidden["rejected_candidates"]["value"] is None


def test_data_order_comes_from_widget_context_of_servant_sessions():
    assert data_family_for("broker-chips-flow") == "chips" and data_family_for("price-chart") == "price"
    events = [{"session_id": f"s{i}", "at": f"2026-11-0{i + 1}T00:00:00Z", "family": "chips" if i < 8 else "price",
               "ref": f"m{i}"} for i in range(10)]
    profile = derive_habit_profile(
        tenant_id=TENANT, user_id=USER, strategy_id=None, locks=[], interaction_events=events,
        computed_at="2026-11-27T00:00:00Z",
    )
    order = next(f for f in profile["features"] if f["key"] == "data_order")
    assert order["shown"] and order["value"]["first_family"] == "chips" and order["value"]["first_family_share"] == 0.8
    assert [i["statement"] for i in derive_interpretations(profile)] == ["您偏好先看籌碼再看價格"]


def test_interpretation_statements_carry_no_number():
    profile = derive_habit_profile(
        tenant_id=TENANT, user_id=USER, strategy_id=None, locks=trader_locks(), computed_at="2026-11-27T00:00:00Z"
    )
    statements = {i["rule"]: i["statement"] for i in derive_interpretations(profile)}
    assert {"short_hold", "fast_entry", "rejects_illiquid", "rejects_sector", "intervenes_smaller"} <= set(statements)
    assert not any(ch.isdigit() for text in statements.values() for ch in text)


# --------------------------------------------------------------------------- owner store, budget


def test_profile_row_is_scoped_event_driven_and_capped():
    owner = _owner()
    _seed(owner, trader_locks())
    _seed(owner, [_lock(900 + i, _event("2454", "entry", T0 + timedelta(days=i)), "approve", T0, user=OTHER) for i in range(3)])
    mine = owner.refresh_habit_profile(tenant_id=TENANT, user_id=USER)
    assert mine["decision_count"] == len(trader_locks()) and mine["version"] == 1
    assert owner.get_habit_profile(tenant_id=TENANT, user_id=OTHER) is None, "another trader's key reads as absent"
    theirs = owner.refresh_habit_profile(tenant_id=TENANT, user_id=OTHER)
    assert theirs["decision_count"] == 3 and theirs["profile_key"] != mine["profile_key"]

    again = owner.refresh_habit_profile(tenant_id=TENANT, user_id=USER)
    assert again["version"] == 1, "unchanged inputs write nothing"

    table = owner.store._habit_profiles
    row = table.get(profile_key(TENANT, USER))
    assert not row["stale_since"]
    # event-driven: a new lock flags the profile; the next read recomputes (one new version)
    new_at = T0 + timedelta(days=200)
    owner.create_decision_lock(_lock(500, _event("2317", "entry", new_at), "approve", new_at), ("trader",))
    assert table.get(profile_key(TENANT, USER))["stale_since"]
    refreshed = owner.refresh_habit_profile(tenant_id=TENANT, user_id=USER)
    assert refreshed["version"] == 2 and refreshed["decision_count"] == mine["decision_count"] + 1

    # cap: the row keeps the latest and 12 superseded versions; older ones are deleted on write
    for i in range(20):
        at = T0 + timedelta(days=300 + i)
        owner.create_decision_lock(_lock(600 + i, _event("2603", "entry", at), "approve", at), ("trader",))
        owner.refresh_habit_profile(tenant_id=TENANT, user_id=USER)
    row = table.get(profile_key(TENANT, USER))
    assert row["current"]["version"] == 22 and len(row["history"]) == HABIT_PROFILE_HISTORY_VERSIONS
    assert [h["version"] for h in row["history"]] == list(range(21, 9, -1))
    assert len(json.dumps(row, ensure_ascii=False).encode()) <= HABIT_PROFILE_MAX_BYTES * (1 + HABIT_PROFILE_HISTORY_VERSIONS)


# --------------------------------------------------------------------------- AC2: GET and PATCH


def test_get_habits_is_owner_scoped_and_404_for_another_trader(harness):
    _seed(harness.owner, trader_locks())
    _seed(harness.owner, trader_locks(OTHER, pairs=1, rejects=1))
    mine = harness.get()
    assert mine.status_code == 200, mine.text
    data = mine.json()["data"]
    assert data["profile"]["decision_count"] == len(trader_locks()) and data["profile"]["user_id"] == USER
    assert {f["key"] for f in data["profile"]["features"]} == set(FEATURE_KEYS)
    assert {i["statement"] for i in data["interpretations"]} >= {"您偏向短線持有", "您常剔除低流動性的候選"}
    assert mine.json()["meta"]["execution_authority"] == "none"

    assert harness.get(trader_id=OTHER).status_code == 404
    assert harness.get(profile_key=profile_key(TENANT, OTHER, None)).status_code == 404
    other_view = harness.get(OTHER_AUTH)
    assert other_view.status_code == 200
    assert other_view.json()["data"]["profile"]["decision_count"] == 3, "the other trader reads only their own"
    assert other_view.json()["data"]["profile"]["user_id"] == OTHER
    assert other_view.json()["data"]["servant_persona_id"] != data["servant_persona_id"]


def test_patch_correction_is_training_example_evidence_and_supersedes_not_deletes(harness):
    _seed(harness.owner, trader_locks())
    first = harness.get().json()["data"]
    target = next(i for i in first["interpretations"] if i["statement"] == "您偏向短線持有")
    response = harness.patch({"action": "replace", "interpretation_id": target["interpretation_id"],
                              "statement": "我其實是看訊號品質決定持有多久", "reason": "不是只看天數"})
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["correction"]["superseded_interpretation_id"] == target["interpretation_id"]
    statements = [i["statement"] for i in body["interpretations"]]
    assert "我其實是看訊號品質決定持有多久" in statements and "您偏向短線持有" not in statements

    evidence = body["correction"]["training_example"]
    inbox = harness.dataset_store.get_inbox_entry(evidence["evidence_id"], tenant_id=TENANT, user_id=USER)
    assert inbox["interaction_kind"] == "training_example"
    assert inbox["content"]["agora_human_evidence"]["type"] == "training_example"
    assert inbox["content"]["expected_output"]["statement"] == "我其實是看訊號品質決定持有多久"
    assert inbox["content"]["input"]["servant_interpretation"] == "您偏向短線持有"

    # superseded, not deleted: the old private entry is still in memory, marked superseded
    old = harness.memory.store.get(target["memory_id"])
    assert old is not None and old.superseded_by and old.content["summary"] == "您偏向短線持有"
    # a later read does not re-derive over the trader's own words
    again = [i["statement"] for i in harness.get().json()["data"]["interpretations"]]
    assert "我其實是看訊號品質決定持有多久" in again and "您偏向短線持有" not in again

    # idempotent replay writes one example; a reject is also a correction
    replay = harness.patch({"action": "replace", "interpretation_id": target["interpretation_id"],
                            "statement": "我其實是看訊號品質決定持有多久", "reason": "不是只看天數"})
    assert replay.status_code in (200, 404), replay.text
    rejected = harness.patch({"action": "reject", "interpretation_id": next(
        i for i in again_items(harness) if i["statement"] == "您常剔除低流動性的候選")["interpretation_id"]}, key="fix-002")
    assert rejected.status_code == 200, rejected.text
    assert any(s.startswith("交易員不同意此解讀：您常剔除低流動性的候選") for s in
               [i["statement"] for i in rejected.json()["data"]["interpretations"]])


def again_items(harness):
    return harness.get().json()["data"]["interpretations"]


def test_patch_is_owner_scoped_and_validated(harness):
    _seed(harness.owner, trader_locks())
    target = again_items(harness)[0]
    foreign = harness.patch({"action": "reject", "interpretation_id": target["interpretation_id"]}, auth=OTHER_AUTH)
    assert foreign.status_code == 404, "another trader's interpretation is not found"
    assert harness.patch({"action": "replace", "interpretation_id": "hint-unknown", "statement": "x"}).status_code == 404
    assert harness.patch({"action": "replace", "interpretation_id": target["interpretation_id"]}).status_code == 422
    assert harness.patch({"action": "delete"}).status_code == 422
    missing_key = harness.client.patch("/bff/agora/servant/habits", json={"action": "reject"}, headers=AUTH)
    assert missing_key.status_code in (400, 422)
    # the trader can also add a statement of their own
    added = harness.patch({"action": "add", "feature_key": "data_order", "statement": "我先看量再看價"}, key="add-1")
    assert added.status_code == 200, added.text
    assert "我先看量再看價" in [i["statement"] for i in added.json()["data"]["interpretations"]]
    assert harness.patch({"action": "add", "feature_key": "nope", "statement": "x"}, key="add-2").status_code == 422


def test_habits_fail_closed_when_private_memory_is_unavailable(harness):
    _seed(harness.owner, trader_locks())
    harness.service.memory = None
    read = harness.get()
    assert read.status_code == 200
    data = read.json()["data"]
    assert data["interpretations"] == [] and data["interpretations_status"]["status"] == "unavailable"
    assert data["profile"]["decision_count"] > 0, "facts stay readable; interpretations are never guessed"
    assert harness.patch({"action": "reject", "interpretation_id": "x"}).status_code == 503


# --------------------------------------------------------------------------- AC3: private memory


def test_interpretations_are_private_to_the_servant_and_cross_only_in_minutes(harness):
    _seed(harness.owner, trader_locks())
    data = harness.get().json()["data"]
    persona = data["servant_persona_id"]
    store = harness.memory.store
    entries = store.list(persona_id=persona, memory_type="preference")
    assert entries and all(e.relevance_scope == "persona_private" for e in entries)
    assert all("scope:committee" not in json.dumps(e.content) and e.tenant_id == TENANT for e in entries)
    statement = entries[0].content["summary"]

    # another persona cannot read them: store retrieval and governance authz both say no
    other_persona = "agora-servant-someone-else"
    assert store.retrieve(persona_id=other_persona, query=statement) == []
    denied = evaluate_authz_request(
        action="memory.retrieve", actor_id="x", actor_roles=["persona_session"],
        resource={"scope": "persona", "persona_id": persona}, context={"session_persona_id": other_persona, "session_id": "s-1"},
    )
    assert denied["allowed"] is False and denied["reason"] == "persona_scope_mismatch"

    # a meeting (consultation_session) reads only persona_and_committee: nothing of this
    committee_view = store.retrieve(persona_id=persona, relevance_scope="persona_and_committee", query=statement)
    assert committee_view == []
    meeting_private = evaluate_authz_request(
        action="memory.retrieve", actor_id="meeting", actor_roles=["consultation_session"],
        resource={"scope": "persona", "persona_id": persona, "relevance_scope": "persona_private"},
        context={"session_persona_id": persona, "session_id": "s-2"},
    )
    assert meeting_private["allowed"] is False and meeting_private["reason"] == "consultation_scope_denied"
    meeting_committee = evaluate_authz_request(
        action="memory.retrieve", actor_id="meeting", actor_roles=["consultation_session"],
        resource={"scope": "persona", "persona_id": persona, "relevance_scope": "persona_and_committee"},
        context={"session_persona_id": persona, "session_id": "s-2"},
    )
    assert meeting_committee["allowed"] is True, "the meeting may read committee-scoped entries only"

    # carried in minutes: the trader's meeting minutes copy the statement into committee scope
    interpretation = harness.service.interpretation_view(
        next(e.to_dict() for e in entries if e.content["summary"] == statement)
    )
    carried = carry_interpretation_in_minutes(
        harness.memory, interpretation, tenant_id=TENANT, persona_id=persona, meeting_id="meeting-1",
        minutes_ref="minutes:meeting-1", written_at="2026-11-27T04:00:00Z",
    )
    assert carried["relevance_scope"] == "persona_and_committee"
    visible = store.retrieve(persona_id=persona, relevance_scope="persona_and_committee", query=statement)
    assert [h.entry.memory_id for h in visible] == [carried["memory_id"]]
    assert all(e.relevance_scope == "persona_private" for e in store.list(
        persona_id=persona, memory_type="preference")), "the private entries stay private"


# --------------------------------------------------------------------------- AC4: the turn cites habits


def _habit_packet_facts(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return packet["habit_profile"]["facts"]


def test_turn_carries_the_profile_cites_habit_facts_and_a_correction_changes_the_next_answer(harness, monkeypatch):
    _seed(harness.owner, trader_locks())
    harness.get()  # the trader opens the habits page; interpretations are written
    persona = sessions._ensure_servant(harness.client)
    session_id = sessions._create_session(harness.client)["data"]["session_id"]

    def script(packet, call_no):
        facts = _habit_packet_facts(packet)
        reading = next((f for f in facts if f.get("habit_kind") == "interpretation"
                        and "持有" in str(f["value"])), None)
        hold_fact = next(f for f in facts if f.get("habit_kind") == "fact" and f["label"].startswith("我的習慣：持有期間"))
        assert reading is not None
        return {"answer_text": f"依您的習慣：{reading['value']}（持有中位數 {{{{{hold_fact['id']}}}}}）。",
                "cited_fact_ids": [reading["id"], hold_fact["id"]]}

    model = data_tools_tests.ScriptedModel(script)
    tools = data_tools_tests.ServantDataTools(agora_owner=harness.owner)
    worker = ServantTurnWorker(owner=harness.owner, client_factory=lambda: model, tools=tools, habits=harness.service)

    assert data_tools_tests._send(harness.client, session_id, "我該怎麼抱這檔？", "turn-1").status_code == 202
    worker.run_once()
    first = data_tools_tests._stored_answer(harness.client, session_id)
    assert "您偏向短線持有" in first["content"] and "3天" in first["content"]
    meta = first["metadata"]["tool_turn"]["habit_profile"]
    assert meta["profile_key"] == profile_key(TENANT, USER, None) and meta["cited_features"] == ["holding_period"]
    cited = [c for c in first["citations"] if c["id"] in meta["cited_fact_ids"]]
    assert {c["feature_key"] for c in cited} == {"holding_period"}
    fact = next(c for c in cited if c["habit_kind"] == "fact")
    assert fact["sample_size"] == 14 and fact["owner"] == "agora.habit_profiles" and fact["freshness"] == "fresh"
    assert all(not any(ch.isdigit() for ch in f["value"]) for f in _habit_packet_facts(model.packets[0])
               if f["habit_kind"] == "interpretation"), "the model is shown readings, never numbers"
    assert not model.persona_calls

    # the trader corrects the reading; the very next answer changes
    target = next(i for i in again_items(harness) if i["statement"] == "您偏向短線持有")
    assert harness.patch({"action": "replace", "interpretation_id": target["interpretation_id"],
                          "statement": "我持有多久取決於籌碼是否續強"}, key="turn-fix").status_code == 200

    def script_after(packet, call_no):
        facts = _habit_packet_facts(packet)
        texts = [str(f["value"]) for f in facts if f.get("habit_kind") == "interpretation"]
        assert "您偏向短線持有" not in texts
        reading = next(f for f in facts if "取決於籌碼" in str(f["value"]))
        return {"answer_text": f"依您更正的說法：{reading['value']}。", "cited_fact_ids": [reading["id"]]}

    model.script = script_after
    assert data_tools_tests._send(harness.client, session_id, "那這檔呢？", "turn-2").status_code == 202
    worker.run_once()
    second = data_tools_tests._stored_answer(harness.client, session_id)
    assert "我持有多久取決於籌碼是否續強" in second["content"] and "您偏向短線持有" not in second["content"]
    assert second["content"] != first["content"]
    assert persona


def test_a_proposal_names_the_habit_facts_it_was_made_against(harness):
    _seed(harness.owner, trader_locks())
    harness.get()
    sessions._ensure_servant(harness.client)
    session_id = sessions._create_session(harness.client)["data"]["session_id"]

    def script(packet, call_no):
        if call_no == 1:
            return {"action": "call_tools", "tool_calls": [{"tool": "propose_research", "arguments": {"question": "籌碼續強的短線策略"}}]}
        return {"answer_text": "已為您草擬研究提案。", "cited_fact_ids": []}

    model = data_tools_tests.ScriptedModel(script)
    tools = data_tools_tests.ServantDataTools(
        agora_owner=harness.owner, research_drafter=lambda q, ctx: {"title": q},
    )
    worker = ServantTurnWorker(owner=harness.owner, client_factory=lambda: model, tools=tools, habits=harness.service)
    assert data_tools_tests._send(harness.client, session_id, "幫我提一個研究", "prop-1").status_code == 202
    worker.run_once()
    answer = data_tools_tests._stored_answer(harness.client, session_id)
    proposal = next(c for c in answer["citations"] if c["tool"] == "propose_research")
    carried = {f["id"] for f in _habit_packet_facts(model.packets[0])}
    assert proposal["habit_refs"] and set(proposal["habit_refs"]) <= carried
    habit_citations = {c["id"] for c in answer["citations"] if c.get("habit_kind")}
    assert set(proposal["habit_refs"]) <= habit_citations, "the proposal's habit facts are part of its citations"


def test_a_habit_failure_never_blocks_the_answer(harness):
    _seed(harness.owner, trader_locks())
    sessions._ensure_servant(harness.client)
    session_id = sessions._create_session(harness.client)["data"]["session_id"]

    class Broken:
        def turn_context(self, **_kw):
            raise RuntimeError("habit owner down")

    model = data_tools_tests.ScriptedModel(lambda packet, n: {"answer_text": "沒有習慣資料也能回答。"})
    worker = ServantTurnWorker(
        owner=harness.owner, client_factory=lambda: model,
        tools=data_tools_tests.ServantDataTools(agora_owner=harness.owner), habits=Broken(),
    )
    assert data_tools_tests._send(harness.client, session_id, "你好", "broken-1").status_code == 202
    worker.run_once()
    answer = data_tools_tests._stored_answer(harness.client, session_id)
    assert answer["content"] == "沒有習慣資料也能回答。"
    assert model.packets[0]["habit_profile"]["facts"] == []


def test_widget_context_of_servant_messages_feeds_data_order(harness):
    sessions._ensure_servant(harness.client)
    session_id = sessions._create_session(harness.client)["data"]["session_id"]
    model = data_tools_tests.ScriptedModel(lambda packet, n: {"answer_text": "好"})
    assert data_tools_tests._send(
        harness.client, session_id, "看分點", "ctx-1", context={"widget_id": "broker-chips-flow", "page": "trading-room"},
    ).status_code == 202
    profile = harness.owner.refresh_habit_profile(tenant_id=TENANT, user_id=USER)
    order = next(f for f in profile["features"] if f["key"] == "data_order")
    assert order["sample_size"] == 1 and order["source_refs"] and not order["shown"]
    assert model is not None


# --------------------------------------------------------------------------- contracts


def test_v1_14_bundle_records_the_habit_contract(harness):
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    bundle = json.loads((root / "services/control-plane/specs/agora/bundle_index.v1_14.json").read_text(encoding="utf-8"))
    openapi_path = root / "services/control-plane/openapi/agora_v1_14.openapi.yaml"
    cap_path = root / "services/control-plane/specs/agora/v15/capability_manifest_v1_14.json"
    assert bundle["openapi"]["sha256"] == hashlib.sha256(openapi_path.read_bytes()).hexdigest()
    assert bundle["files"]["specs/agora/v15/capability_manifest_v1_14.json"] == hashlib.sha256(cap_path.read_bytes()).hexdigest()
    assert bundle["route_families"]["agora.servant.habits.v1"] == [
        "GET /bff/agora/servant/habits", "PATCH /bff/agora/servant/habits",
    ]
    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))
    assert set(openapi["paths"]["/bff/agora/servant/habits"]) == {"get", "patch"}
    schemas = openapi["components"]["schemas"]
    assert schemas["ServantHabitFeature"]["properties"]["key"]["enum"] == list(FEATURE_KEYS)
    assert schemas["ServantHabitFeature"]["properties"]["min_sample"]["const"] == HABIT_MIN_SAMPLE
    assert schemas["ServantHabitCorrectionRequest"]["properties"]["action"]["enum"] == ["reject", "replace", "add"]
    cap = json.loads(cap_path.read_text(encoding="utf-8"))
    assert {"method": "PATCH", "path": "/bff/agora/servant/habits", "owner": "agora+memory"} in cap["routes"]
    assert cap["execution_authority"] == "none" and cap["environment_ceiling"] == "paper"
    # the real response matches the documented fields
    _seed(harness.owner, trader_locks())
    data = harness.get().json()["data"]
    assert set(schemas["ServantHabitsData"]["required"]) <= set(data)
    assert set(schemas["ServantHabitProfile"]["required"]) <= set(data["profile"])
    assert set(schemas["ServantHabitFeature"]["required"]) <= set(data["profile"]["features"][0])
    assert set(schemas["ServantHabitInterpretation"]["required"]) <= set(data["interpretations"][0])
