"""Measured usefulness, replay and isolation through canonical Memory routes."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.memory import main, search_retrieval as retrieval
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryStore
from services.memory.test_memory_index_projection import institution, persona
from services.memory.test_main import JWT_SECRET, _make_jwt
from services.memory.usefulness import usefulness_score


def mark(decision="decision-1", outcome="helped", **changes):
    value = {"decision_id": decision, "case_file_id": "case-1", "cited_item_ids": ["lesson-1"],
             "outcome": outcome, "reason": "paired_paper_window", "evidence_refs": ["packet-1"],
             "window_end": "2026-11-13"}
    return {**value, **changes}


def payload(entry, layer="persona", outcome="helped", decision="decision-1"):
    return {"source_event_type": "case_file_usefulness", "source_event_id": decision,
            "write_authority": "persona-svc", "tenant_id": "tenant-a", "sponsor_persona_id": "persona-alpha",
            "marks": [{"entry_id": getattr(entry, "memory_id", None) or entry.entry_id,
                       "layer": layer, "mark": mark(decision, outcome)}]}


@pytest.fixture
def memory(tmp_path, monkeypatch):
    stores = (InstitutionalMemoryStore(tmp_path / "im.json"), PersonaMemoryStore(tmp_path / "pm.json"))
    monkeypatch.setattr(main, "_store", lambda: stores[0])
    monkeypatch.setattr(main, "_persona_store", lambda: stores[1])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setattr(retrieval, "_GLOBAL_SEARCH_BACKEND", None)
    monkeypatch.delenv("PANTHEON_SEARCH_API_URL", raising=False)
    return TestClient(main.app), stores


def auth(role="persona-svc", tenant="tenant-a"):
    return {"Authorization": "Bearer " + _make_jwt(roles=[role], tenant_id=tenant)}


@pytest.mark.parametrize("factory,store_index", [(persona, 1), (institution, 0)])
def test_persisted_marks_are_bounded_replay_safe_and_three_hurts_queue_review(memory, factory, store_index):
    _, stores = memory
    store = stores[store_index]
    entry = store.create(factory(tenant_id="tenant-a", reuse_count=100000))
    key = getattr(entry, "memory_id", None) or entry.entry_id
    assert usefulness_score(entry) == 0
    for i in range(100):
        updated = store.mark_usefulness(key, mark(f"d-{i}", "hurt"))
        assert -5 <= usefulness_score(updated) <= 5
    assert updated.memory_review["status"] == "pending"
    assert updated.memory_review["reason"] == "three_hurt_marks"
    assert len(updated.usefulness_marks) == 100
    assert store.mark_usefulness(key, mark("d-0", "hurt")) == updated
    with pytest.raises(ValueError, match="replay_conflict"):
        store.mark_usefulness(key, mark("d-0", "helped"))
    loaded = type(store)(store._path).require(key)
    assert loaded.usefulness_marks == updated.usefulness_marks
    assert loaded.memory_review == updated.memory_review


@pytest.mark.parametrize("scope", ["persona", "institutional"])
def test_backend_reranks_all_candidates_before_limit_and_read_counts_never_boost(memory, scope):
    _, stores = memory
    store = stores[scope == "persona"]
    factory = persona if scope == "persona" else institution
    entries = [store.create(factory(tenant_id="tenant-a", reuse_count=999 if i == 0 else 0)) for i in range(3)]
    key = lambda e: getattr(e, "memory_id", None) or e.entry_id
    store.mark_usefulness(key(entries[-1]), mark())
    backend = SimpleNamespace(search=lambda **kw: [SimpleNamespace(id=key(e), score=1.0) for e in entries])
    if scope == "persona":
        hits = retrieval.retrieve_persona_with_backend(store, "persona-alpha", backend, query="風控", limit=1)
    else:
        hits = retrieval.retrieve_institutional_with_backend(store, backend, query="風控", limit=1)
    assert key(hits[0].entry) == key(entries[-1])
    assert hits[0].relevance_score == pytest.approx(101.6667)
    assert usefulness_score(replace(entries[0], reuse_count=10**9)) == 0


def test_writeback_requires_persona_service_and_exact_tenant_and_persona(memory):
    client, (_, per) = memory
    entry = per.create(persona(tenant_id="tenant-a"))
    data = payload(entry)
    route = "/api/memory/writebacks/learn-feedback"
    assert client.post(route, json=data).status_code == 401
    for role in ("operator", "memory-writer", "researcher"):
        assert client.post(route, json=data, headers=auth(role)).status_code == 403
    assert client.post(route, json=data, headers=auth(tenant="other")).status_code == 403
    data["sponsor_persona_id"] = "other-persona"
    assert client.post(route, json=data, headers=auth()).status_code == 422
    assert per.require(entry.memory_id).usefulness_marks == {}
    data["sponsor_persona_id"] = "persona-alpha"
    for _ in range(2):
        response = client.post(route, json=data, headers=auth())
        assert response.status_code == 201, response.text
    assert len(per.require(entry.memory_id).usefulness_marks) == 1
    data["marks"][0]["mark"]["outcome"] = "hurt"
    assert client.post(route, json=data, headers=auth()).status_code == 422


def test_missing_confounded_does_not_boost_and_review_queue_uses_existing_retrieve(memory):
    client, (_, per) = memory
    entry = per.create(persona(tenant_id="tenant-a"))
    route = "/api/memory/writebacks/learn-feedback"
    response = client.post(route, json=payload(entry, outcome="confounded"), headers=auth())
    assert response.status_code == 201, response.text
    assert usefulness_score(per.require(entry.memory_id)) == 0
    for i in range(3):
        assert client.post(route, json=payload(entry, outcome="hurt", decision=f"hurt-{i}"), headers=auth()).status_code == 201
    response = client.get("/api/memory/retrieve", headers=auth("operator"), params={
        "actor_id": "memory-operator-1", "actor_roles": "operator", "session_id": "review-test", "scope": "persona",
        "persona_id": "persona-alpha", "review_required": True})
    assert response.status_code == 200, response.text
    assert response.json()["hits"][0]["entry"]["memory_review"]["status"] == "pending"


def test_batch_rejects_cross_tenant_and_direct_entry_injection(memory):
    client, (_, per) = memory
    entry = per.create(persona(tenant_id="tenant-a"))
    foreign = per.create(persona(tenant_id="tenant-b"))
    data = payload(entry)
    data["marks"] += payload(foreign)["marks"]
    assert client.post("/api/memory/writebacks/learn-feedback", json=data, headers=auth()).status_code == 422
    assert per.require(entry.memory_id).usefulness_marks == {}
    data = persona(tenant_id="tenant-a").to_dict()
    data["usefulness_marks"] = {"forged": mark()}
    assert client.post("/api/memory/persona-entries", json=data, headers=auth("memory-writer")).status_code == 422
