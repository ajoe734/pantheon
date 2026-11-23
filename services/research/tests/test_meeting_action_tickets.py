"""Real Research HTTP routes + real PostgresJsonOwnerStore concurrency and scope.

Set RESEARCH_MEETING_TEST_DSN to an isolated test database. CI's explicitly
provisioned DATABASE_URL is also admitted when CI=true. Every test owns a fresh
schema and drops only that schema. No hosted credentials or writes are used.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.foundation.postgres_json_store import quote_pg_identifier
from services.research.write_owner import ResearchWriteOwner
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "meeting-action-test-only-secret"
URL = "/api/research/tickets"


def headers(actor="consultation-svc", roles=("service",), tenant="tenant-a"):
    token = encode_jwt_hs256({"sub": actor, "roles": list(roles), "tenant_id": tenant}, secret=SECRET)
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}


def assignment(index="1", owner="persona-1", subject="pool-1"):
    return {"title": "Compare alternatives", "description": "Use the supplied sources", "owner": owner,
            "meeting_action": {"meeting_id": "mtg-week-47", "source_action_id": index,
                "meeting_type": "MT-1", "subject_ref": subject, "assignee_kind": "persona",
                "deliverable": "A saved analysis with citations", "acceptance_criterion": "Compare both alternatives",
                "due_at": "2026-11-20T00:00:00Z", "reviewer_id": "consultation-svc"}}


@pytest.fixture
def world(monkeypatch, tmp_path):
    dsn = os.getenv("RESEARCH_MEETING_TEST_DSN") or (os.getenv("DATABASE_URL") if os.getenv("CI") == "true" else None)
    if not dsn:
        pytest.skip("requires isolated RESEARCH_MEETING_TEST_DSN (CI may use its provisioned DATABASE_URL)")
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", SECRET)
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", str(tmp_path / "research"))
    monkeypatch.setenv("ALPHA_REPLICATION_DATA_DIR", str(tmp_path / "alpha"))
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    name = "research_meeting_action_test_main"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    schema = "meeting_action_test_" + uuid.uuid4().hex
    owner = ResearchWriteOwner(dsn=dsn, schema=schema)
    module.set_write_owner(owner)
    client = TestClient(module.app)
    try:
        yield module, client, owner
    finally:
        client.close()
        with owner._tickets_store._connect() as conn:
            conn.execute(f"DROP SCHEMA {quote_pg_identifier(schema)} CASCADE")
        sys.modules.pop(name, None)


def create(world, payload=None, **kw):
    response = world[1].post(URL, json=payload or assignment(), headers=headers(**kw))
    assert response.status_code == 200, response.text
    return response.json()


def command(world, ticket, action, *, actor="persona-1", roles=("persona",), key=None, **fields):
    patch = {"action": action, "expected_revision": ticket["revision"],
             "idempotency_key": key or f"{action}-{ticket['revision']}", **fields}
    return world[1].patch(f"{URL}/{ticket['ticket_id']}", json={"patch": patch}, headers=headers(actor, roles))


def note(world, ticket, *, actor="persona-1", roles=("persona",), key="analysis", body="Alternative A is supported by source X; B lacks sufficient evidence."):
    response = world[1].post("/api/research/notes", headers=headers(actor, roles), json={
        "meeting_ticket_id": ticket["ticket_id"], "idempotency_key": key,
        "title": "Comparison", "body": body, "source_refs": ["source-X"]})
    assert response.status_code == 200, response.text
    return {"kind": "research_note", "artifact_id": response.json()["note_id"]}


def advance(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_adopted_action_create_is_atomic_and_conflicting_replay_is_rejected(world):
    client, owner = world[1:]
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post(URL, headers=headers(), json=assignment()), range(8)))
    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["ticket_id"] for r in responses}) == 1
    ticket = responses[0].json()
    assert ticket["action_id"] == ticket["ticket_id"] and ticket["revision"] == 0
    changed = assignment(); changed["meeting_action"]["acceptance_criterion"] = "Different decision"
    assert client.post(URL, headers=headers(), json=changed).status_code == 409
    progressed = advance(command(world, ticket, "start"))
    replay = create(world)
    assert replay["revision"] == progressed["revision"] and len(replay["lifecycle_history"]) == 2
    with owner._tickets_store._connect() as conn:
        assert conn.execute(f"SELECT count(*) FROM {owner._tickets_store.table}").fetchone()[0] == 1
    ordinary = {"title": "Overwrite", "ticket_id": ticket["ticket_id"]}
    assert client.post(URL, json=ordinary).status_code in (400, 409)


def test_capable_persona_submits_returned_corrected_then_reviewer_accepts(world):
    client = world[1]
    ticket = create(world)
    first = note(world, ticket)
    assert note(world, ticket) == first
    submitted = advance(command(world, ticket, "submit", evidence_refs=[first]))
    assert submitted["status"] == "in_progress" and submitted["action_state"] == "submitted"
    assert not submitted["allowedActions"]["canClose"]
    assert command(world, submitted, "accept", reason="Self review", review_source="mtg-next").status_code == 403
    assert client.patch(f"{URL}/{ticket['ticket_id']}", headers=headers(), json={"status": "closed"}).status_code == 400
    returned = advance(command(world, submitted, "return", actor="consultation-svc", roles=("service",),
                               reason="Add source for alternative B", review_source="mtg-week-48"))
    second = note(world, returned, key="corrected", body="A and B compared with sources X and Y.")
    corrected = advance(command(world, returned, "submit", evidence_refs=[second]))
    accepted = advance(command(world, corrected, "accept", actor="consultation-svc", roles=("service",),
                                reason="Both alternatives compared against criterion", review_source="mtg-week-49"))
    assert accepted["status"] == "closed" and accepted["action_state"] == "accepted"
    assert accepted["owner"] == "persona-1" and accepted["required_actor"] is None
    assert accepted["lifecycle_history"][2]["review_decision"]["reason"] == "Add source for alternative B"
    assert accepted["lifecycle_history"][1]["evidence_refs"][0]["artifact_id"] == first["artifact_id"]
    assert accepted["evidence_refs"][0]["artifact_id"] == second["artifact_id"]
    retry = command(world, corrected, "accept", actor="consultation-svc", roles=("service",),
                    reason="Both alternatives compared against criterion", review_source="mtg-week-49")
    assert advance(retry)["revision"] == accepted["revision"]
    assert command(world, accepted, "start").status_code == 409
    assert len(advance(client.get(f"{URL}/{ticket['ticket_id']}", headers=headers()))["lifecycle_history"]) == 5


def test_blocked_persona_human_evidence_resumes_original_owner_and_id(world):
    ticket = create(world)
    blocked = advance(command(world, ticket, "request_input", reason="Supply licensed report excerpt", human_actor_id="human-1"))
    assert blocked["required_actor"] == "human-1"
    params = {"meeting_linked": "true", "human_actionable": "true"}
    inbox = advance(world[1].get(URL, params=params, headers=headers("human-1", ("viewer",))))["data"]
    assert inbox[0]["ticket_id"] == ticket["ticket_id"] and inbox[0]["action_state"] == "waiting_for_input"
    human = note(world, blocked, actor="human-1", roles=("human",), body="Permitted excerpt with usage authorization from licensed source.")
    assert command(world, blocked, "provide_input", actor="human-2", roles=("human",), evidence_refs=[human]).status_code in (403, 404)
    assert command(world, blocked, "provide_input", actor="human-1", roles=("human",), evidence_refs=[]).status_code == 400
    resumed = advance(command(world, blocked, "provide_input", actor="human-1", roles=("human",), evidence_refs=[human]))
    assert advance(world[1].get(URL, params=params, headers=headers("human-1", ("viewer",))))["data"] == []
    assert resumed["owner"] == "persona-1" and resumed["required_actor"] == "persona-1"
    assert resumed["ticket_id"] == ticket["ticket_id"] and resumed["action_state"] == "in_progress"
    assert command(world, resumed, "submit", evidence_refs=[{"kind": "membership_ack", "artifact_id": "joined"}]).status_code == 403
    result = note(world, resumed, key="result")
    submitted = advance(command(world, resumed, "submit", actor="persona-pool-cycle", roles=("viewer",), evidence_refs=[result]))
    accepted = advance(command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
                                reason="Analysis meets the criterion using permitted input", review_source="mtg-week-48"))
    assert accepted["human_input"]["provided_by"] == "human-1"
    assert accepted["lifecycle_history"][-2]["transitioned_by"] == "persona-pool-cycle"
    assert accepted["lifecycle_history"][-2]["accountable_owner"] == "persona-1"


def test_direct_human_assignment_submit_return_and_accept_uses_original_owner(world):
    client = world[1]
    payload = assignment("direct-human", owner="human-1")
    payload["meeting_action"]["assignee_kind"] = "human"
    ticket = create(world, payload)
    params = {"meeting_linked": "true", "human_actionable": "true"}

    def inbox():
        return advance(client.get(URL, params=params, headers=headers("human-1", ("viewer",))))["data"]

    assert ticket["action_state"] == "open" and ticket["required_actor"] == "human-1"
    assert inbox()[0]["ticket_id"] == ticket["ticket_id"]
    assert inbox()[0]["description"] == payload["description"]
    ordinary_page = advance(client.get(URL, params={"meeting_linked": "true"}, headers=headers("human-1", ("viewer",))))
    assert "description" not in ordinary_page["data"][0]
    started = advance(command(world, ticket, "start", actor="human-1", roles=("viewer",)))
    assert inbox()[0]["revision"] == started["revision"]
    result = note(world, started, actor="human-1", roles=("viewer",))
    submitted = advance(command(world, started, "submit", actor="human-1", roles=("viewer",), evidence_refs=[result]))
    assert submitted["action_state"] == "submitted" and inbox() == []
    assert submitted["status"] == "in_progress" and submitted["required_actor"] == "consultation-svc"
    returned = advance(command(world, submitted, "return", actor="consultation-svc", roles=("service",),
                               reason="Compare the other alternative", review_source="mtg-week-48"))
    assert inbox()[0]["action_state"] == "returned" and inbox()[0]["revision"] == returned["revision"]
    assert returned["required_actor"] == "human-1"
    corrected = note(world, returned, actor="human-1", roles=("viewer",), key="corrected", body="Both alternatives compared.")
    resubmitted = advance(command(world, returned, "submit", actor="human-1", roles=("viewer",), evidence_refs=[corrected]))
    accepted = advance(command(world, resubmitted, "accept", actor="consultation-svc", roles=("service",),
                                reason="Both alternatives meet criterion", review_source="mtg-week-49"))
    assert inbox() == [] and accepted["action_state"] == "accepted"
    assert accepted["owner"] == "human-1" and accepted["ticket_id"] == ticket["ticket_id"]
    assert len(accepted["lifecycle_history"]) == 6


def inline_report(body="Report A and B: A has the supported risk assumptions."):
    return {"title": "Human comparison", "body": body, "source_refs": ["licensed-report:page-3"]}


def note_count(world):
    store = world[2]._notes_store
    with store._connect() as conn:
        return conn.execute(f"SELECT count(*) FROM {store.table}").fetchone()[0]


def human_assignment(world):
    payload = assignment("inline-human", owner="human-1")
    payload["meeting_action"]["assignee_kind"] = "human"
    return create(world, payload)


def test_inline_human_submission_persists_real_body_on_original_ticket(world):
    client, owner = world[1:]
    ticket = human_assignment(world)
    report = inline_report()
    submit = lambda: command(world, ticket, "submit", actor="human-1", roles=("viewer",),
                             key="human-report", evidence_note=report)
    submitted = advance(submit())
    assert submitted["ticket_id"] == ticket["ticket_id"] and submitted["action_state"] == "submitted"
    assert submitted["status"] != "closed" and submitted["revision"] == 1
    body = submitted["evidence_notes"][0]
    assert body == {**report, "note_id": submitted["evidence_refs"][0]["artifact_id"],
        "actor_id": "human-1", "tenant_id": "tenant-a", "ticket_id": ticket["ticket_id"],
        "checksum": submitted["evidence_refs"][0]["checksum"]}
    saved = owner._notes_store.get(body["note_id"])
    assert saved["body"] == report["body"] and saved["_meeting_binding"]["actor_id"] == "human-1"
    assert "evidence_notes" not in owner._tickets_store.get(ticket["ticket_id"])
    assert advance(submit()) == submitted and note_count(world) == 1
    assert advance(client.get(f"{URL}/{ticket['ticket_id']}", headers=headers())) == submitted
    assert advance(client.get(f"/api/research/notes/{body['note_id']}", headers=headers()))["body"] == report["body"]
    returned = advance(command(world, submitted, "return", actor="consultation-svc", roles=("service",),
                               reason="Include fees", review_source="mtg-week-48"))
    corrected = inline_report("After fees, alternative B has the supported net return.")
    resubmitted = advance(command(world, returned, "submit", actor="human-1", roles=("viewer",),
                                 key="human-report-corrected", evidence_note=corrected))
    assert note_count(world) == 2 and resubmitted["evidence_notes"][0]["body"] == corrected["body"]
    assert owner._notes_store.get(body["note_id"])["body"] == report["body"]
    accepted = advance(command(world, resubmitted, "accept", actor="consultation-svc", roles=("service",),
                               reason="Fee comparison satisfies criterion", review_source="mtg-week-49"))
    assert accepted["action_state"] == "accepted" and accepted["lifecycle_history"][1]["transitioned_by"] == "human-1"


def test_inline_human_input_resumes_persona_and_recovers_lost_response(world):
    client = world[1]
    blocked = advance(command(world, create(world), "request_input", reason="Supply report excerpt",
                              human_actor_id="human-1"))
    provide = lambda: command(world, blocked, "provide_input", actor="human-1", roles=("human",),
                              key="report-input", evidence_note=inline_report())
    resumed = advance(provide())
    assert resumed["owner"] == resumed["required_actor"] == "persona-1"
    assert resumed["action_state"] == "in_progress" and resumed["revision"] == 2
    assert resumed["human_input"]["provided_by"] == "human-1"
    assert resumed["evidence_notes"][0]["body"] == inline_report()["body"]
    assert advance(provide()) == resumed and note_count(world) == 1
    for actor, roles in (("persona-1", ("persona",)), ("consultation-svc", ("service",))):
        assert advance(client.get(f"{URL}/{blocked['ticket_id']}", headers=headers(actor, roles))) == resumed
    # Exact reply recovery does not grant the provider later read/list or write access.
    assert client.get(f"{URL}/{blocked['ticket_id']}", headers=headers("human-1", ("human",))).status_code == 404
    assert command(world, resumed, "submit", actor="human-1", roles=("human",),
                   evidence_note=inline_report()).status_code == 404
    refs = [{"kind": r["kind"], "artifact_id": r["artifact_id"]} for r in resumed["human_input"]["evidence_refs"]]
    submitted = advance(command(world, resumed, "submit", evidence_refs=refs))
    assert submitted["ticket_id"] == blocked["ticket_id"] and submitted["action_state"] == "submitted"
    assert provide().status_code == 404  # no access to later owner results through an old replay


def test_inline_note_denied_stale_invalid_and_conflicting_commands_do_not_write(world):
    client, owner = world[1:]
    ticket = human_assignment(world)
    report = inline_report()
    for actor, roles in (("other-human", ("human",)), ("consultation-svc", ("service",))):
        assert command(world, ticket, "submit", actor=actor, roles=roles, evidence_note=report).status_code in {403, 404}
    for fields in ({"expected_revision": 99}, {"evidence_note": None},
                   {"evidence_note": {"title": "Empty", "body": " "}},
                   {"evidence_note": {**report, "actor_id": "forged"}},
                   {"evidence_note": {**report, "body": "x" * 12001}},
                   {"evidence_note": {**report, "source_refs": ["x"] * 9}},
                   {"evidence_note": {**report, "body": "文" * 6000}},
                   {"evidence_refs": [{"kind": "research_note", "artifact_id": "missing"}]}):
        patch = {"action": "submit", "expected_revision": ticket["revision"], "idempotency_key": "report",
                 "evidence_note": report, **fields}
        response = client.patch(f"{URL}/{ticket['ticket_id']}", headers=headers("human-1", ("viewer",)), json={"patch": patch})
        assert response.status_code in {400, 403, 409}, response.text
        assert note_count(world) == 0
    forged = client.patch(f"{URL}/{ticket['ticket_id']}", headers=headers("human-1", ("viewer",)),
        json={"actor_id": "human-2", "patch": {"action": "submit", "expected_revision": 0,
            "idempotency_key": "report", "evidence_note": report}})
    assert forged.status_code == 403 and note_count(world) == 0
    foreign = client.patch(f"{URL}/{ticket['ticket_id']}", headers=headers("human-1", ("viewer",), "tenant-b"),
        json={"patch": {"action": "submit", "expected_revision": 0, "idempotency_key": "report", "evidence_note": report}})
    assert foreign.status_code == 404 and note_count(world) == 0
    submitted = advance(command(world, ticket, "submit", actor="human-1", roles=("viewer",), key="report", evidence_note=report))
    assert command(world, ticket, "submit", actor="human-1", roles=("viewer",), key="report",
                   evidence_note=inline_report("Conflicting report")).status_code == 409
    assert command(world, ticket, "submit", actor="human-1", roles=("viewer",), key="different",
                   evidence_note=report).status_code == 409
    assert note_count(world) == 1
    assert owner._tickets_store.get(ticket["ticket_id"])["revision"] == submitted["revision"]
    assert client.get(f"{URL}/{ticket['ticket_id']}", headers=headers("unrelated", ("viewer",))).status_code == 404
    assert client.get(f"/api/research/notes/{submitted['evidence_notes'][0]['note_id']}",
                      headers=headers("unrelated", ("viewer",))).status_code == 404


def test_inline_provider_requires_existing_input_permission_before_note_write(world):
    ticket = advance(command(world, create(world), "request_input", reason="Supply report", human_actor_id="human-1"))
    assert command(world, ticket, "provide_input", actor="human-1", roles=("viewer",),
                   evidence_note=inline_report()).status_code == 403
    assert note_count(world) == 0


def test_inline_note_and_ticket_rollback_together_and_retry_after_failure(world, monkeypatch):
    owner = world[2]
    ticket = human_assignment(world)
    original = owner._tickets_store.compare_and_set

    def fail_after_note(*args, **kwargs):
        # The actual owner note is visible inside this transaction, never committed alone.
        conn = kwargs["conn"]
        assert conn.execute(f"SELECT count(*) FROM {owner._notes_store.table}").fetchone()[0] == 1
        raise RuntimeError("simulated ticket write failure after note insert")

    monkeypatch.setattr(owner._tickets_store, "compare_and_set", fail_after_note)
    submit = lambda: command(world, ticket, "submit", actor="human-1", roles=("viewer",), key="recover", evidence_note=inline_report())
    with pytest.raises(RuntimeError, match="simulated ticket write failure"):
        submit()
    assert note_count(world) == 0 and owner._tickets_store.get(ticket["ticket_id"])["revision"] == 0
    monkeypatch.setattr(owner._tickets_store, "compare_and_set", original)
    submitted = advance(submit())
    assert submitted["revision"] == 1 and note_count(world) == 1
    assert advance(submit()) == submitted and note_count(world) == 1


def test_concurrent_inline_note_replays_commit_one_body_and_one_transition(world):
    ticket = human_assignment(world)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: command(world, ticket, "submit", actor="human-1", roles=("viewer",),
            key="same-report", evidence_note=inline_report()), range(6)))
    results = [advance(r) for r in responses]
    assert all(r == results[0] for r in results) and note_count(world) == 1
    assert results[0]["revision"] == 1 and len(results[0]["lifecycle_history"]) == 2


def test_human_actionable_pages_push_down_filter_without_widening_visibility(world, monkeypatch):
    client, owner = world[1:]
    # These Persona rows are visible to the human reviewer but are not human
    # work. Force all of them before the actionable rows in keyset order.
    unrelated = []
    for i in range(24):
        payload = assignment(f"unrelated-{i}")
        payload["meeting_action"].update(meeting_id=f"mtg-persona-{i // 8}", reviewer_id="human-1")
        unrelated.append(create(world, payload))
    last_unrelated = max(t["ticket_id"] for t in unrelated)

    def late_assignment(kind, assignee, source):
        for i in range(10000):
            action_id = f"{source}-{i}"
            tid = "rt-action-" + owner._action_digest(["tenant-a", "mtg-human", action_id])[:32]
            if tid > last_unrelated:
                payload = assignment(action_id, owner=assignee)
                payload["meeting_action"].update(meeting_id="mtg-human", assignee_kind=kind)
                return create(world, payload)
        pytest.fail("could not place actionable fixture after unrelated rows")

    direct = [late_assignment("human", "human-1", f"direct-{i}") for i in range(3)]
    direct[1] = advance(command(world, direct[1], "start", actor="human-1", roles=("viewer",)))
    submitted = advance(command(world, direct[2], "submit", actor="human-1", roles=("viewer",),
                                evidence_refs=[note(world, direct[2], actor="human-1", roles=("viewer",))]))
    direct[2] = advance(command(world, submitted, "return", actor="consultation-svc", roles=("service",),
                                reason="Add missing source", review_source="mtg-next"))
    waiting = advance(command(world, late_assignment("persona", "persona-1", "waiting"), "request_input",
                              reason="Supply licensed excerpt", human_actor_id="human-1"))
    foreign_human = late_assignment("human", "human-2", "other-human")
    foreign_waiting = advance(command(world, late_assignment("persona", "persona-1", "other-waiting"), "request_input",
                                      reason="Supply other authorization", human_actor_id="human-2"))
    foreign_tenant = assignment("other-tenant", owner="human-1")
    foreign_tenant["meeting_action"]["assignee_kind"] = "human"
    create(world, foreign_tenant, tenant="tenant-b")
    owner._tickets_store.put("legacy-human", {"ticket_id": "legacy-human", "owner": "human-1", "status": "open"})
    monkeypatch.setattr(owner._tickets_store, "list_all", lambda: pytest.fail("meeting pages must use bounded SQL"))

    params = {"meeting_linked": "true", "human_actionable": "true", "page_size": 2}
    expected = {t["ticket_id"] for t in [*direct, waiting]}
    seen = []
    while True:
        page = advance(client.get(URL, params=params, headers=headers("human-1", ("viewer",))))
        assert len(page["data"]) == 2
        seen.extend(t["ticket_id"] for t in page["data"])
        for ticket in page["data"]:
            assert ticket["revision"] == next(t["revision"] for t in [*direct, waiting] if t["ticket_id"] == ticket["ticket_id"])
        token = page["page_info"]["next_page_token"]
        if not token:
            break
        # The new filter, authenticated actor, and existing filters all bind
        # the cursor. A cursor cannot continue a differently scoped queue.
        for changes, auth in [({"human_actionable": "false"}, headers("human-1", ("viewer",))),
                              ({"owner": "human-1"}, headers("human-1", ("viewer",))),
                              ({}, headers("human-2", ("viewer",))),
                              ({}, headers("human-1", ("viewer",), "tenant-b"))]:
            assert client.get(URL, params={**params, "page_token": token, **changes}, headers=auth).status_code == 400
        params["page_token"] = token
    assert seen == sorted(expected) and len(seen) == len(expected)

    params.pop("page_token", None)
    params["page_size"] = 100
    all_visible = expected | {foreign_human["ticket_id"], foreign_waiting["ticket_id"]}
    for actor, roles in (("operator-1", ("operator",)), ("admin-1", ("admin",)), ("consultation-svc", ("service",))):
        rows = advance(client.get(URL, params=params, headers=headers(actor, roles)))["data"]
        assert {t["ticket_id"] for t in rows} == all_visible
    assert advance(client.get(URL, params=params, headers=headers("stranger", ("viewer",))))["data"] == []
    assert {t["ticket_id"] for t in advance(client.get(URL, params=params, headers=headers("human-2", ("viewer",))))["data"]} == {
        foreign_human["ticket_id"], foreign_waiting["ticket_id"]}
    filtered = advance(client.get(URL, params={**params, "owner": "human-1", "meeting_type": "MT-1",
        "subject_ref": "pool-1", "due_before": "2026-12-01T00:00:00Z", "action_state": "open,returned"},
        headers=headers("human-1", ("viewer",))))["data"]
    assert {t["ticket_id"] for t in filtered} == {direct[0]["ticket_id"], direct[2]["ticket_id"]}


def test_human_actionable_requires_meeting_query_auth_and_preserves_ordinary_lists(world):
    client, owner = world[1:]
    ordinary = advance(client.post(URL, json={"title": "Existing ordinary work", "owner": "human-1"}))
    assert client.get(URL, params={"human_actionable": "true"}).status_code == 400
    assert client.get(URL, params={"meeting_linked": "true", "human_actionable": "true"}).status_code == 401
    for params in ({}, {"human_actionable": "false"}):
        assert advance(client.get(URL, params=params))[0]["ticket_id"] == ordinary["ticket_id"]
    from services.research.write_owner import MeetingActionError
    with pytest.raises(MeetingActionError, match="meeting_linked=true"):
        owner.list_research_tickets(human_actionable=True)


def test_tenant_actor_role_and_legacy_boundaries(world):
    client, owner = world[1:]
    ticket = create(world); tid = ticket["ticket_id"]
    assert client.get(f"{URL}/{tid}").status_code == 401
    assert client.get(f"{URL}/{tid}", headers=headers(tenant="tenant-b")).status_code == 404
    assert client.get(f"{URL}/{tid}", headers=headers("stranger", ("viewer",))).status_code == 404
    assert client.get(f"{URL}/{tid}", headers={**headers(tenant="tenant-b"), "X-Tenant-Id": "tenant-a"}).status_code == 403
    assert client.post(URL, json={**assignment("2"), "actor_id": "forged"}, headers=headers()).status_code == 403
    assert client.post(URL, json={**assignment("2"), "tenant_id": "tenant-b"}, headers=headers()).status_code == 403
    assert client.post(URL, json=assignment("2"), headers=headers("someone", ("viewer",))).status_code == 403
    assert client.post(URL, json=assignment("2")).status_code == 401
    forged = headers(); forged["Authorization"] += "tampered"
    assert client.get(f"{URL}/{tid}", headers=forged).status_code == 401
    assert client.get(URL, params={"meeting_linked": "true"}).status_code == 401
    ordinary = advance(client.post(URL, json={"title": "Historical ordinary ticket", "owner": "alice"}))
    assert advance(client.patch(f"{URL}/{ordinary['ticket_id']}", json={"status": "closed"}))["status"] == "closed"
    owner._tickets_store.put("legacy-missing-tenant", {"ticket_id": "legacy-missing-tenant", "owner": "persona-1", "status": "open"})
    listed = advance(client.get(URL))
    assert len(listed) == 2 and tid not in {t["ticket_id"] for t in listed}
    scoped = advance(client.get(URL, params={"meeting_linked": "true"}, headers=headers()))
    assert [r["ticket_id"] for r in scoped["data"]] == [tid]
    assert advance(client.get(URL, params={"meeting_linked": "true"}, headers=headers(tenant="tenant-b")))["data"] == []


def test_evidence_rechecked_and_notes_cannot_forge_or_overwrite_scope(world):
    module, client, owner = world
    ticket = create(world)
    module.store.put_artifact({"artifact_id": "foreign", "tenant_id": "tenant-b", "checksum": "sha256:foreign"})
    module.store.put_artifact({"artifact_id": "unbound", "checksum": "sha256:unbound"})
    for artifact_id in ("foreign", "unbound", "missing"):
        assert command(world, ticket, "submit", evidence_refs=[{"kind": "research_artifact", "artifact_id": artifact_id}]).status_code == 403
    assert command(world, ticket, "submit", evidence_refs=[{"kind": "research_artifact", "artifact_id": "foreign", "verified": True}]).status_code == 400
    ref = note(world, ticket)
    assert client.post("/api/research/notes", json={"note_id": ref["artifact_id"], "body": "overwrite"}).status_code in (400, 409)
    assert client.post("/api/research/notes", json={"_meeting_binding": {"tenant_id": "tenant-a"}}).status_code == 400
    assert client.get(f"/api/research/notes/{ref['artifact_id']}").status_code == 401
    assert client.get(f"/api/research/notes/{ref['artifact_id']}", headers=headers(tenant="tenant-b")).status_code == 404
    assert advance(client.get("/api/research/notes")) == []
    submitted = advance(command(world, ticket, "submit", evidence_refs=[ref]))
    record = owner._notes_store.get(ref["artifact_id"])
    assert owner._notes_store.delete_if_matches(ref["artifact_id"], record)
    assert command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
                   reason="Must resolve again", review_source="mtg-next").status_code == 403
    same = advance(client.get(f"{URL}/{ticket['ticket_id']}", headers=headers()))
    assert same["revision"] == submitted["revision"] and same["action_state"] == "submitted"


def test_stale_and_simultaneous_transitions_do_not_lose_history(world):
    ticket = create(world)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda i: command(world, ticket, "start", key=f"start-{i}"), range(6)))
    assert sum(r.status_code == 200 for r in responses) == 1
    assert {r.status_code for r in responses} <= {200, 409}
    current = advance(world[1].get(f"{URL}/{ticket['ticket_id']}", headers=headers()))
    assert current["revision"] == 1 and len(current["lifecycle_history"]) == 2
    result = note(world, current)
    submitted = advance(command(world, current, "submit", evidence_refs=[result]))
    assert command(world, current, "submit", evidence_refs=[result], key="different").status_code == 409
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
            reason="Meets criterion", review_source="mtg-48", key="same-accept"), range(6)))
    assert {r.status_code for r in responses} == {200}
    assert {r.json()["revision"] for r in responses} == {3}
    assert command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
                   reason="Conflicting replay", review_source="mtg-48", key="same-accept").status_code == 409
    fresh_owner = ResearchWriteOwner(dsn=world[2].dsn, schema=world[2].schema)
    persisted = fresh_owner._tickets_store.get(ticket["ticket_id"])
    assert persisted["action_state"] == "accepted" and len(persisted["lifecycle_history"]) == 4


def test_series_filters_bounded_keyset_pages_and_overdue_across_periods(world):
    client = world[1]
    tickets = [create(world, assignment(str(i))) for i in range(5)]
    create(world, assignment("other-series", subject="pool-2"))
    create(world, assignment("other-owner", owner="persona-2"))
    create(world, assignment("other-tenant"), tenant="tenant-b")
    params = {"meeting_linked": "true", "meeting_type": "MT-1", "subject_ref": "pool-1", "owner": "persona-1",
              "due_before": "2026-12-30T00:00:00Z", "page_size": 2}
    ids = []
    while True:
        page = advance(client.get(URL, params=params, headers=headers()))
        assert len(page["data"]) <= 2
        ids.extend(r["ticket_id"] for r in page["data"])
        token = page["page_info"]["next_page_token"]
        if not token:
            break
        assert client.get(URL, params={**params, "page_token": token, "subject_ref": "other"}, headers=headers()).status_code == 400
        params["page_token"] = token
    assert set(ids) == {t["ticket_id"] for t in tickets} and len(ids) == 5
    assert client.get(URL, params={"meeting_linked": "true", "page_size": 101}, headers=headers()).status_code == 422
    assert client.get(URL, params={"meeting_linked": "true", "due_before": "bad"}, headers=headers()).status_code == 400
    assert client.get(URL, params={"subject_ref": "pool-1"}, headers=headers()).status_code == 400
    params.pop("page_token", None)
    assert advance(client.get(URL, params={**params, "subject_ref": "' OR TRUE --"}, headers=headers()))["data"] == []
    submitted = advance(command(world, tickets[0], "submit", evidence_refs=[note(world, tickets[0])]))
    # Inclusive timestamp boundary avoids losing submissions in the same timestamp unit.
    page = advance(client.get(URL, params={**params, "action_state": "submitted", "submitted_after": submitted["submitted_at"]}, headers=headers()))
    assert [r["ticket_id"] for r in page["data"]] == [submitted["ticket_id"]]
    personal = advance(client.get(URL, params={"meeting_linked": "true"}, headers=headers("persona-2", ("persona",))))
    assert len(personal["data"]) == 1 and personal["data"][0]["owner"] == "persona-2"


def test_ticket_note_and_history_byte_measurements(world):
    ticket = create(world)
    result = note(world, ticket)
    submitted = advance(command(world, ticket, "submit", evidence_refs=[result]))
    accepted = advance(command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
                                reason="Comparison has citations for both alternatives", review_source="mtg-week-48"))
    owner = world[2]
    with owner._tickets_store._connect() as conn:
        row = conn.execute(f"SELECT octet_length(payload::text), octet_length((payload->'lifecycle_history')::text), jsonb_array_length(payload->'lifecycle_history') FROM {owner._tickets_store.table}").fetchone()
        note_bytes = conn.execute(f"SELECT octet_length(payload::text) FROM {owner._notes_store.table}").fetchone()[0]
    measured = {"source": "local Postgres 16 actual HTTP fixture; not hosted measurement", "tickets": 1,
                "ticket_jsonb_text_bytes": row[0], "history_bytes": row[1], "history_count": row[2],
                "notes": 1, "note_jsonb_text_bytes": note_bytes, "writes": {"ticket_insert": 1, "ticket_updates": 2, "note_insert": 1}}
    print("MEETING_ACTION_MEASURE=" + json.dumps(measured, sort_keys=True))
    assert row[0] < owner._ACTION_MAX_BYTES and row[2] == 3
    assert note_bytes < 16384


def test_owner_rejects_malformed_and_forged_commands_without_mutation(world):
    client = world[1]
    ticket = create(world)
    for fields in ({"action": {"status": "closed"}}, {"action": "submit", "evidence_refs": []},
                   {"action": "request_input", "reason": "", "human_actor_id": "h"},
                   {"action": "start", "expected_revision": True},
                   {"action": "start", "owner": "other"}):
        response = command(world, ticket, "start", **{k: v for k, v in fields.items() if k != "action"}) if fields["action"] == "start" else client.patch(
            f"{URL}/{ticket['ticket_id']}", headers=headers("persona-1", ("persona",)),
            json={"patch": {"expected_revision": 0, "idempotency_key": "malformed", **fields}})
        assert response.status_code in (400, 409), response.text
    wrong_actor = client.patch(f"{URL}/{ticket['ticket_id']}", headers=headers("persona-1", ("persona",)),
                              json={"actor_id": "persona-2", "patch": {"action": "start", "expected_revision": 0, "idempotency_key": "forged"}})
    assert wrong_actor.status_code == 403
    assert client.post(URL, headers=headers("consultation-svc", ("viewer",)), json=assignment("view-only")).status_code == 403
    assert advance(client.get(f"{URL}/{ticket['ticket_id']}", headers=headers()))["revision"] == 0


def test_different_ticket_notes_and_conflicting_note_replays_are_rejected(world):
    ticket = create(world)
    ref = note(world, ticket)
    other = create(world, assignment("other"))
    assert command(world, other, "submit", evidence_refs=[ref]).status_code == 403
    duplicate = world[1].post("/api/research/notes", headers=headers("persona-1", ("persona",)), json={
        "meeting_ticket_id": ticket["ticket_id"], "idempotency_key": "analysis", "title": "Changed", "body": "Different result"})
    assert duplicate.status_code == 409
    # Local saved outputs are resolved through the same real scoped owner used
    # by GET /api/research/artifacts, without accepting caller-provided URLs.
    world[0].store.put_artifact({"artifact_id": "saved-1", "tenant_id": "tenant-a", "checksum": "sha256:recorded"})
    submitted = advance(command(world, ticket, "submit", evidence_refs=[{"kind": "research_artifact", "artifact_id": "saved-1"}]))
    world[0].store.put_artifact({"artifact_id": "saved-1", "tenant_id": "tenant-a", "checksum": "sha256:changed"})
    assert command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
                   reason="Cannot accept changed evidence", review_source="mtg-48").status_code == 409


def test_generic_experiment_link_never_mutates_meeting_obligation(world):
    client = world[1]
    ticket = create(world)
    experiment = advance(client.post("/api/research/experiments", json={"ticket_id": ticket["ticket_id"], "experiment_name": "Legacy caller"}))
    advance(client.post(f"/api/research/experiments/{experiment['experiment_id']}/cancel", json={"actor_id": "legacy"}))
    advance(client.post(f"/api/research/experiments/{experiment['experiment_id']}/retry", json={"actor_id": "legacy", "idempotency_key": "retry-1"}))
    current = advance(client.get(f"{URL}/{ticket['ticket_id']}", headers=headers()))
    assert current == ticket


def test_assignment_and_note_count_caps_preserve_existing_retries(world, monkeypatch):
    monkeypatch.setattr(world[2], "_ACTION_PER_MEETING_MAX", 2)
    one = create(world, assignment("one"))
    create(world, assignment("two"))
    assert world[1].post(URL, headers=headers(), json=assignment("three")).status_code == 409
    assert create(world, assignment("one"))["ticket_id"] == one["ticket_id"]
    monkeypatch.setattr(world[2], "_ACTION_NOTES_PER_TICKET_MAX", 1)
    first = note(world, one)
    assert note(world, one) == first
    response = world[1].post("/api/research/notes", headers=headers("persona-1", ("persona",)), json={
        "meeting_ticket_id": one["ticket_id"], "idempotency_key": "new", "title": "Extra", "body": "Extra"})
    assert response.status_code == 409
    submitted = advance(command(world, one, "submit", evidence_refs=[first]))
    assert submitted["action_state"] == "submitted"


def test_read_only_acceptance_probe_uses_real_owner_http(world):
    ticket = create(world)
    submitted = advance(command(world, ticket, "submit", evidence_refs=[note(world, ticket)]))
    accepted = advance(command(world, submitted, "accept", actor="consultation-svc", roles=("service",),
                                reason="Meets criterion", review_source="mtg-48"))
    path = Path(__file__).resolve().parents[3] / "docs/deployment/evidence/RESEARCH-MEETING-ACTION-TICKETS-20261123/probe.py"
    spec = importlib.util.spec_from_file_location("meeting_action_probe", path)
    probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
    def fetch(path, query):
        return advance(world[1].get(path, params=query, headers=headers()))
    result = probe.inspect("http://testserver", "tenant-a", accepted["ticket_id"], "unused", "accepted", fetch)
    assert result["read_only"] is True and result["series_ticket_count"] == 1
    assert result["verified_note_count"] == 1
    def missing_body(path, query):
        result = fetch(path, query)
        if path.startswith(URL + "/"):
            result["evidence_notes"][0]["body"] = ""
        return result
    with pytest.raises(AssertionError):
        probe.inspect("http://testserver", "tenant-a", accepted["ticket_id"], "unused", "accepted", missing_body)
    assert advance(world[1].get(f"{URL}/{ticket['ticket_id']}", headers=headers())) == accepted
