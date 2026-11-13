"""Case-file views on the OODA packet detail and Agora panel/memo reads.

Case files are composed by the real knowledge composer over the real memory
service (HTTP transport), persisted as the owners persist them (packet record,
Consultation transcript), then read back through the BFF routes.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from services.consultation import models as cm
from services.control_plane.bff.tests.test_agora_committee_memory_owners import (  # noqa: F401
    PANEL, headers, stack,
)
from services.control_plane.bff.tests.test_control_loops_evidence_redaction_sweep import (
    LOW_CAPABILITY_TOKEN, _ControlLoopsSweepStore, _build_control_loops_app, _stub_auth_env,
)
from services.knowledge import case_file as cf

TENANT = "tenant-a"


TAGS = ["family:momentum", "decision:consult"]


@pytest.fixture
def seeded(stack):
    """Real memory writes tagged for this decision: one committee-shared, one private, one institutional."""
    from uuid import uuid4
    memory = stack[4]["memory.test"]
    common = {"source_event_type": "postmortem_published", "source_event_id": "case-file-view",
              "written_at": "2026-11-12T00:00:00Z", "write_authority": "incident-svc", "reuse_count": 0}
    rows = [("persona-entries", {**common, "memory_id": "pmem-" + str(uuid4()), "persona_id": "persona-a",
                                  "memory_type": "strategy_lesson", "relevance_scope": "persona_private",
                                  "content": {"summary": "momentum consult private lesson", "tags": TAGS}}),
            ("persona-entries", {**common, "memory_id": "pmem-" + str(uuid4()), "persona_id": "persona-a",
                                  "memory_type": "strategy_lesson", "relevance_scope": "persona_and_committee",
                                  "content": {"summary": "momentum consult shared lesson", "tags": TAGS}}),
            ("entries", {**common, "entry_id": "mem-" + str(uuid4()), "knowledge_type": "incident_lesson",
                         "content": {"headline": "momentum consult", "body": "institutional lesson", "tags": TAGS},
                         "scope": "system_wide", "contributing_persona_ids": ["persona-a"]})]
    for endpoint, payload in rows:
        r = memory.post("/api/memory/" + endpoint, headers=headers(), json=payload)
        assert r.status_code == 201, r.text
    return stack


def compose(persona_caller: str):
    token = headers()["Authorization"].removeprefix("Bearer ")
    subject = cf.Subject(decision_type="consult", persona_id="persona-a", tenant_id=TENANT,
                         strategy_family="momentum")
    caller = cf.Caller(actor_id="alice", actor_roles=("operator",), session_id="s-1", persona_id=persona_caller)
    return cf.compose_case_file(subject, caller, cf.Owners(memory=cf.OwnerEndpoint("http://memory.test", token)))


def items(case):
    return [i for g in case["groups"].values() for i in g["items"]]


def packet_with(case, persona_ids=("persona-a",), **cycle):
    return {"packet_id": "cycle-1", "loop_type": "paper_strategy", "status": "decided", "environment": "paper",
            "tenant_id": TENANT, "persona_ids": list(persona_ids),
            "persona_cycle": {"phase": "terminal", "case_files": {case["case_file_id"]: case} if case else {},
                              **cycle}}


def packet_client(packet):
    class Store(_ControlLoopsSweepStore):
        def get_ooda_packet(self, packet_id):
            return copy.deepcopy(packet) if packet_id == packet["packet_id"] else None
    return TestClient(_build_control_loops_app(Store()))


def test_packet_with_case_file_shows_items_and_citations(seeded):
    case = compose("persona-a")
    cited = next(i for i in items(case) if i["kind"].startswith("memory_"))
    assert cited["retrieval_mode"]
    packet = packet_with(case, decisions=[
        {"kind": "no_action", "status": "no_action", "case_file_id": case["case_file_id"],
         "cited_case_file_item_ids": [cited["item_id"], "lessons:ghost"], "none_applicable_reason": None}],
        turn={"prompt": "PRIVATE_PROMPT"})
    with _stub_auth_env():
        r = packet_client(packet).get("/bff/ooda/packets/cycle-1", headers={"Authorization": LOW_CAPABILITY_TOKEN})
    assert r.status_code == 200, r.text
    view = r.json()["data"]["case_file_view"]
    assert view["status"] == "ok"
    got = {i["item_id"]: i for g in view["case_files"][0]["groups"].values() for i in g["items"]}
    assert got[cited["item_id"]]["excerpt"] == cited["excerpt"]
    assert got[cited["item_id"]]["source_id"] == cited["source_id"]
    assert got[cited["item_id"]]["retrieval_mode"] == cited["retrieval_mode"]
    assert view["decisions"][0]["cited_item_ids"] == [cited["item_id"]]
    assert view["decisions"][0]["unresolved_item_ids"] == ["lessons:ghost"]
    assert "case_files" not in r.json()["data"]["persona_cycle"]


def test_packet_without_case_file_fails_closed_with_reason():
    packet = packet_with(None, failure_reason="case_file_unavailable:owner_unconfigured")
    with _stub_auth_env():
        r = packet_client(packet).get("/bff/ooda/packets/cycle-1", headers={"Authorization": LOW_CAPABILITY_TOKEN})
    assert r.status_code == 200
    assert r.json()["data"]["case_file_view"] == {
        "status": "unavailable", "reason": "case_file_unavailable:owner_unconfigured", "case_file": None}


def test_packet_never_shows_another_personas_private_memory(seeded):
    case = compose("persona-a")
    private = [i for i in items(case) if i.get("relevance_scope") == "persona_private"]
    assert private, "fixture must contain the persona's private memory"
    own = packet_client(packet_with(case))
    other = packet_client(packet_with(case, persona_ids=("persona-b",)))
    with _stub_auth_env():
        h = {"Authorization": LOW_CAPABILITY_TOKEN}
        own_ids = {i["item_id"] for g in own.get("/bff/ooda/packets/cycle-1", headers=h).json()["data"][
            "case_file_view"]["case_files"][0]["groups"].values() for i in g["items"]}
        view = other.get("/bff/ooda/packets/cycle-1", headers=h).json()["data"]["case_file_view"]
    other_ids = {i["item_id"] for g in view["case_files"][0]["groups"].values() for i in g["items"]}
    assert {p["item_id"] for p in private} <= own_ids
    assert not ({p["item_id"] for p in private} & other_ids)
    assert view["case_files"][0]["private_items_withheld"] == len(private)


def record_panel(store, case, *, claims, rejected=None):
    actor = cm.ActorRef(actor_type="persona", actor_id="persona-a")
    base = {"case_file_id": case["case_file_id"], "digest": case["digest"]}
    events = [
        ("panel_case_file_attached", cm.ActorRef(actor_type="service", actor_id="consult"),
         {**base, "case_file": case}),
        ("persona_contribution", actor, {**base, "claims": claims, "recommendation": "approve",
                                         "role": "primary_reviewer"}),
    ]
    if rejected:
        events.append(("panel_contribution_rejected", cm.ActorRef(actor_type="service", actor_id="consult"),
                       {**base, **rejected}))
    store.put_transcript(cm.ConsultTranscript(
        transcript_id="transcript-panel-a", session_id="panel-a", request_id="panel-a",
        events=[cm.TranscriptEvent(event_id=f"e{n}", session_id="panel-a", sequence_no=n, event_type=t,
                                   actor=a, content=c) for n, (t, a, c) in enumerate(events, 1)]))


def test_panel_and_memo_show_case_file_citations_and_rejections(seeded):
    client, _, _, _, _, store = seeded
    case = compose("")  # service caller: shared memory only
    assert not [i for i in items(case) if i.get("relevance_scope") == "persona_private"]
    cited = items(case)[0]["item_id"]
    record_panel(store, case, claims=[{"claim": "grounded", "evidence_refs": [cited]},
                                      {"claim": "stray", "evidence_refs": ["lessons:ghost"]}],
                 rejected={"persona_id": "persona-b", "contribution_id": "c-2", "reason": "claim_uncited"})
    calls_before = len(seeded[1])
    panel = client.get(PANEL + "/panel-a", headers=headers())
    memos = client.get(PANEL + "/panel-a/memos", headers=headers())
    assert panel.status_code == memos.status_code == 200, (panel.text, memos.text)
    for view in (panel.json()["data"]["case_file_view"], memos.json()["data"][0]["case_file_view"]):
        assert view["status"] == "ok"
        assert view["case_file"]["case_file_id"] == case["case_file_id"]
        got = {i["item_id"]: i for g in view["case_file"]["groups"].values() for i in g["items"]}
        assert got[cited]["excerpt"] and got[cited]["retrieval_mode"] and got[cited]["source_id"]
        claims = view["contributions"][0]["claims"]
        assert claims[0]["cited_item_ids"] == [cited]
        assert claims[1]["cited_item_ids"] == [] and claims[1]["unresolved_item_ids"] == ["lessons:ghost"]
        assert view["rejected_contributions"] == [{"persona_id": "persona-b", "contribution_id": "c-2",
                                                   "reason": "claim_uncited", "cited": False}]
    assert all(m == "GET" for m, *_ in seeded[1][calls_before:])


def test_panel_without_case_file_and_foreign_tenant_case_file(stack):
    client, _, _, _, _, store = stack
    body = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]
    assert body["case_file_view"]["reason"] == "no_case_file_recorded"
    assert body["case_file_view"]["case_file"] is None
    foreign = copy.deepcopy(compose(""))
    foreign["subject"]["tenant_id"] = "tenant-other"
    record_panel(store, foreign, claims=[])
    view = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["case_file_view"]
    assert view == {"status": "unavailable", "reason": "case_file_invalid", "case_file": None}


def test_panel_private_item_is_withheld(seeded):
    client, _, _, _, _, store = seeded
    case = compose("persona-a")  # includes persona-a private memory: must never ship in a panel view
    private = {i["item_id"] for i in items(case) if i.get("relevance_scope") == "persona_private"}
    assert private
    record_panel(store, case, claims=[])
    view = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["case_file_view"]
    shown = {i["item_id"] for g in view["case_file"]["groups"].values() for i in g["items"]}
    assert not (shown & private)


def test_consultation_outage_is_recorded_not_guessed(stack):
    client, _, control, *_ = stack
    resp = client.get(PANEL + "/panel-a", headers=headers())
    assert resp.status_code == 200
    control["response"][("consultation.test", "/api/consult/requests/panel-a/transcript")] = {"request_id": "x"}
    view = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["case_file_view"]
    assert view["reason"] == "invalid_transcript_response" and view["case_file"] is None
