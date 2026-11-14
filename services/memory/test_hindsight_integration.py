"""HTTP-route integration against a real Hindsight + memory-index-postgres pair.

Opt in with ``PANTHEON_HINDSIGHT_IT_URL`` / ``PANTHEON_HINDSIGHT_IT_KEY`` pointing at
the compose ``hindsight`` service (see docker-compose.yml).  Every test uses a unique
tenant so its banks are isolated and deleted afterwards; no product data is touched.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from services.memory import hindsight_index as hs
from services.memory import main
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryStore
from services.memory.test_hindsight_index import institution, persona
from services.memory.test_main import JWT_SECRET, _make_jwt

URL = os.getenv("PANTHEON_HINDSIGHT_IT_URL", "")
KEY = os.getenv("PANTHEON_HINDSIGHT_IT_KEY", "")
pytestmark = pytest.mark.skipif(not (URL and KEY), reason="set PANTHEON_HINDSIGHT_IT_URL and PANTHEON_HINDSIGHT_IT_KEY")


@pytest.fixture
def env(monkeypatch):
    tenant = "it-" + uuid.uuid4().hex[:10]
    stores = (InstitutionalMemoryStore(), PersonaMemoryStore())
    monkeypatch.setattr(main, "_store", lambda: stores[0])
    monkeypatch.setattr(main, "_persona_store", lambda: stores[1])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv(hs.URL_ENV, URL)
    monkeypatch.setenv(hs.KEY_ENV, KEY)
    monkeypatch.delenv(hs.REFLECT_ENV, raising=False)
    hs.set_hindsight_client(None)
    hs.DIAGNOSTICS.reset()
    client = hs.HindsightClient(URL, KEY)
    yield TestClient(main.app), stores, tenant, client
    hs.flush_mirrors()
    for bank in (hs.facts_bank(tenant), hs.persona_bank(tenant, "persona-alpha"), hs.persona_bank(tenant, "persona-beta")):
        client.delete_bank(bank)


def auth(tenant, roles=("operator",)):
    return {"Authorization": "Bearer " + _make_jwt(tenant_id=tenant, roles=list(roles))}


def retrieve(client, tenant, roles=("operator",), **params):
    base = {"actor_id": "memory-operator-1", "actor_roles": list(roles), "session_id": "it-session",
            "scope": "institutional", "query": ""}
    base.update(params)
    return client.get("/api/memory/retrieve", params=base, headers=auth(tenant, roles))


def ids(response):
    assert response.status_code == 200, response.text
    return [h["entry"].get("entry_id") or h["entry"].get("memory_id") for h in response.json()["hits"]]


def test_traditional_chinese_recall_through_http_route_with_anchor_filtering(env):
    api, (inst, _), tenant, _client = env
    tw = inst.create(institution(tenant_id=tenant, content={
        "headline": "台灣股票風控檢討", "body": "市場波動上升，流動性不足時應降低台灣股票曝險",
        "tags": ["underlying:TW"]}))
    other = inst.create(institution(tenant_id=tenant, content={
        "headline": "外匯部位檢討", "body": "美元兌日圓部位於波動上升時應縮減",
        "tags": ["underlying:JPY"]}))
    assert hs.flush_mirrors()
    assert hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    response = retrieve(api, tenant, query="台灣股票風險曝險")
    assert tw.entry_id in ids(response)
    body = response.json()
    assert body["retrieval_mode"] == "hindsight" and body["fallback_reason"] is None
    assert body["llm_layers"] == "disabled"
    filtered = retrieve(api, tenant, query="部位波動", subject_anchors=["underlying:JPY"])
    assert ids(filtered) == [other.entry_id]


def test_supersede_and_archive_invalidate_in_the_index(env):
    api, (inst, _), tenant, client = env
    old = inst.create(institution(tenant_id=tenant))
    new = inst.create(institution(tenant_id=tenant, source_event_id="pm-new"))
    assert hs.flush_mirrors()
    bank = hs.facts_bank(tenant)
    assert client.memory_units(bank, old.entry_id)
    inst.supersede(old.entry_id, new.entry_id)
    assert hs.flush_mirrors()
    assert client.memory_units(bank, old.entry_id) == []
    assert client.memory_units(bank, new.entry_id)
    recalled = hs.recall_canonical_ids(client, bank, query="流動性風控", groups=[], limit=10)
    assert [cid for cid, _ in recalled] == [new.entry_id]
    inst.archive(new.entry_id, reason="obsolete")
    assert hs.flush_mirrors()
    assert hs.recall_canonical_ids(client, bank, query="流動性風控", groups=[], limit=10) == []


def test_persona_never_sees_other_persona_and_committee_only_gets_committee_scope(env):
    api, (_, per), tenant, _client = env
    private = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_private",
                                 content={"summary": "我的私人解讀：流動性不足時降低曝險", "tags": []}))
    shared = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_and_committee",
                                content={"summary": "可供委員會的解讀：流動性不足時降低曝險", "tags": []}))
    beta = per.create(persona(tenant_id=tenant, persona_id="persona-beta", relevance_scope="persona_and_committee",
                              content={"summary": "貝塔的解讀：流動性不足時降低曝險", "tags": []}))
    assert hs.flush_mirrors()
    own = retrieve(api, tenant, scope="persona", persona_id="persona-alpha", query="流動性不足降低曝險")
    assert set(ids(own)) == {private.memory_id, shared.memory_id}
    assert own.json()["retrieval_mode"] == "hindsight"
    theirs = retrieve(api, tenant, scope="persona", persona_id="persona-beta", query="流動性不足降低曝險")
    assert ids(theirs) == [beta.memory_id]
    committee = retrieve(api, tenant, roles=("consultation_session",), scope="persona", persona_id="persona-alpha",
                         query="流動性不足降低曝險", session_persona_id="persona-alpha")
    assert committee.status_code == 200, committee.text
    assert ids(committee) == [shared.memory_id]
    # Index-level proof: beta's bank never holds alpha's documents.
    bank = hs.persona_bank(tenant, "persona-beta")
    assert not _client.memory_units(bank, private.memory_id) and not _client.memory_units(bank, shared.memory_id)


def test_rebuild_restores_a_dropped_bank_idempotently(env):
    api, (inst, per), tenant, client = env
    live = inst.create(institution(tenant_id=tenant))
    gone = inst.create(institution(tenant_id=tenant, source_event_id="pm-gone"))
    inst.archive(gone.entry_id, reason="obsolete")
    assert hs.flush_mirrors()
    bank = hs.facts_bank(tenant)
    client.delete_bank(bank)
    assert client.memory_units(bank, live.entry_id) == []
    first = hs.rebuild(inst, per)
    second = hs.rebuild(inst, per)
    assert first["failures"] == [] and first["retained"] == second["retained"] == 1
    assert client.memory_units(bank, live.entry_id) and not client.memory_units(bank, gone.entry_id)
    assert ids(retrieve(api, tenant, query="流動性風控")) == [live.entry_id]


def test_reflect_reports_unavailable_with_reason(env, monkeypatch):
    api, _stores, tenant, _client = env
    body = {"query": "流動性風險", "bank": "institutional", "actor_id": "memory-operator-1",
            "actor_roles": ["operator"], "session_id": "it-session"}
    response = api.post("/api/memory/reflect", json=body, headers=auth(tenant))
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "reflect_not_configured"
    monkeypatch.setenv(hs.REFLECT_ENV, "true")  # configured, but the deployed LLM provider is none
    response = api.post("/api/memory/reflect", json=body, headers=auth(tenant))
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "hindsight_http_400"


def test_unreachable_index_falls_back_explicitly_without_blocking_canonical_state(env, monkeypatch):
    api, (inst, _), tenant, _client = env
    monkeypatch.setenv(hs.URL_ENV, "http://127.0.0.1:1")
    monkeypatch.setattr(hs.time, "sleep", lambda *_: None)
    entry = inst.create(institution(tenant_id=tenant))
    assert inst.get(entry.entry_id) is entry
    assert hs.flush_mirrors()
    assert hs.DIAGNOSTICS.snapshot()["failures"][0]["reason"] == "hindsight_unreachable"
    body = retrieve(api, tenant, query="流動性風控").json()
    assert body["retrieval_mode"] == "keyword_fallback" and body["fallback_reason"] == "hindsight_unreachable"
