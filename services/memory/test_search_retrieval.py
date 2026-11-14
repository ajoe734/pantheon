"""Retrieval facade: Hindsight candidates hydrated and authorised from canonical stores."""
from __future__ import annotations

import pytest

from services.memory import hindsight_index as hs
from services.memory.search_retrieval import retrieve_institutional, retrieve_persona
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryStore
from services.memory.test_hindsight_index import inst, mid, pentry


class Recall:
    def __init__(self, results=None, error=None):
        self.results, self.error, self.calls = results or [], error, []

    def recall(self, bank, body):
        self.calls.append((bank, body))
        if self.error:
            raise hs.HindsightError(self.error)
        return {"results": self.results}


def doc(cid, final=1.0):
    return {"document_id": cid, "scores": {"final": final}}


@pytest.fixture(autouse=True)
def _no_mirror():
    hs.flush_mirrors()
    hs.set_hindsight_client(type("Off", (), {"retain": lambda *a: None,
                                              "invalidate_document": lambda *a: 0})())
    yield
    hs.flush_mirrors()
    hs.set_hindsight_client(None)


def test_hindsight_candidates_are_hydrated_and_reranked_by_usefulness():
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))
    store.create(inst(mid("b")))
    client = Recall([doc(mid("a"), 1.0), doc(mid("b"), 1.0), doc(mid("ghost"), 2.0)])
    diag = {}
    hits = retrieve_institutional(store, client=client, query="止損", tenant_id="t1", diagnostics=diag)
    assert diag["retrieval_mode"] == "hindsight" and diag["fallback_reason"] is None
    assert {h.entry.entry_id for h in hits} == {mid("a"), mid("b")}  # unknown id dropped


def test_subject_anchors_become_any_strict_tag_group_and_filter_hydration():
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))
    store.create(inst(mid("n"), content={"headline": "h", "body": mid("b")}))
    client = Recall([doc(mid("a")), doc(mid("n"))])
    hits = retrieve_institutional(store, client=client, query="q", tenant_id="t1",
                                  subject_anchors=["underlying:TW"])
    assert client.calls[0][0] == hs.facts_bank("t1")
    assert client.calls[0][1]["tag_groups"] == [{"tags": ["anchor:underlying:tw"], "match": "any_strict"}]
    assert [h.entry.entry_id for h in hits] == [mid("a")]  # index result without the anchor is rejected


def test_other_tenant_and_inactive_entries_are_dropped():
    store = InstitutionalMemoryStore()
    store.create(inst(mid("mine")))
    store.create(inst(mid("theirs"), tenant_id="t2"))
    store.create(inst(mid("old")))
    store.archive(mid("old"), reason="x")
    hits = retrieve_institutional(store, client=Recall([doc(mid("mine")), doc(mid("theirs")), doc(mid("old"))]),
                                  query="q", tenant_id="t1")
    assert [h.entry.entry_id for h in hits] == [mid("mine")]


def test_persona_never_receives_another_persona_entry():
    store = PersonaMemoryStore()
    store.create(pentry(mid("pmine")))
    store.create(pentry(mid("pother"), persona_id="beta"))
    client = Recall([doc(mid("pmine")), doc(mid("pother"))])
    hits = retrieve_persona(store, "alpha", client=client, query="q", tenant_id="t1")
    assert client.calls[0][0] == hs.persona_bank("t1", "alpha")
    assert [h.entry.memory_id for h in hits] == [mid("pmine")]


def test_committee_callers_only_get_committee_scope():
    store = PersonaMemoryStore()
    store.create(pentry(mid("priv")))
    store.create(pentry(mid("com"), relevance_scope="persona_and_committee"))
    client = Recall([doc(mid("priv")), doc(mid("com"))])
    hits = retrieve_persona(store, "alpha", client=client, query="q", tenant_id="t1",
                            relevance_scope="persona_and_committee")
    assert {"tags": ["scope:committee"], "match": "any_strict"} in client.calls[0][1]["tag_groups"]
    assert [h.entry.memory_id for h in hits] == [mid("com")]


def test_observations_map_to_canonical_ids_through_source_facts():
    response = {"results": [{"id": "o1", "type": "observation", "document_id": None,
                             "source_fact_ids": ["f1"], "scores": {"final": 3.0}}],
                "source_facts": {"f1": {"document_id": mid("a")}}}

    class C:
        def recall(self, bank, body):
            assert body["include"]["source_facts"] == {}
            return response
    assert hs.recall_canonical_ids(C(), mid("b"), query="q", groups=[], limit=5) == [(mid("a"), 3.0)]


@pytest.mark.parametrize("client,query,reason", [
    (None, "q", "hindsight_unconfigured"),
    (Recall(error="hindsight_unreachable"), "q", "hindsight_unreachable"),
    (Recall(), "  ", "empty_query"),
])
def test_keyword_fallback_is_explicit_with_reason(client, query, reason):
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))
    diag = {}
    hits = retrieve_institutional(store, client=client, query=query, tenant_id="t1", diagnostics=diag)
    assert diag == {"retrieval_mode": "keyword_fallback", "fallback_reason": reason}
    assert hits == [] or hits[0].entry.entry_id == mid("a")
