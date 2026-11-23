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
    human = note(world, blocked, actor="human-1", roles=("human",), body="Permitted excerpt with usage authorization from licensed source.")
    assert command(world, blocked, "provide_input", actor="human-2", roles=("human",), evidence_refs=[human]).status_code in (403, 404)
    assert command(world, blocked, "provide_input", actor="human-1", roles=("human",), evidence_refs=[]).status_code == 400
    resumed = advance(command(world, blocked, "provide_input", actor="human-1", roles=("human",), evidence_refs=[human]))
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
