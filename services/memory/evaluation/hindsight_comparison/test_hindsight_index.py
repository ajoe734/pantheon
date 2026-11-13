import re

from services.memory.evaluation.hindsight_comparison import hindsight_index as hi


class FakeHindsight:
    """In-memory stand-in for the Hindsight HTTP API (unit tests only)."""

    def __init__(self):
        self.banks, self.calls = {}, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        m = re.match(r"/v1/default/banks/([^/]+)(/.*)?$", path)
        bank, rest = m.group(1), m.group(2) or ""
        docs = self.banks.setdefault(bank, {})
        if method == "POST" and rest == "/memories":
            for it in body["items"]:
                docs[it["document_id"]] = it
            return {"success": True, "usage": {"llm_calls": 0}}
        if method == "DELETE":
            docs.pop(rest.split("/documents/")[1], None)
            return {}
        if rest == "/memories/recall":
            want = {t for g in body.get("tag_groups", []) for t in g["tags"]}
            res = [{"id": f"f-{d['document_id']}", "document_id": d["document_id"], "text": d["content"]}
                   for d in docs.values() if not want or want & set(d["tags"])]
            return {"results": res}
        return {}


def item(cid, layer="persona", persona="alice", scope="persona_private", fam="momentum", extra=None):
    key = "memory_id" if layer == "persona" else "entry_id"
    content = {"summary": f"s-{cid}"} if layer == "persona" else {"headline": "h", "body": f"b-{cid}"}
    return {"layer": layer, "anchors": {"strategy_family": fam, "strategy_families": [fam], "underlyings": ["TXF"], **(extra or {})},
            "entry": {key: cid, "persona_id": persona, "relevance_scope": scope, "content": content,
                      "source_event_type": "postmortem_published", "source_event_id": f"e-{cid}",
                      "write_authority": "incident-svc", "written_at": "2026-10-01T00:00:00Z"}}


def test_document_shape_tags_entities_from_anchors_only():
    d = hi.build_document(item("m1"), with_entities=True)
    assert d["document_id"] == "m1" and d["timestamp"] == "2026-10-01T00:00:00Z"
    assert "family:momentum" in d["tags"] and "underlying:TXF" in d["tags"]
    assert {"text": "TXF", "type": "underlying"} in d["entities"]
    assert d["metadata"]["source_event_id"] == "e-m1" and d["metadata"]["write_authority"] == "incident-svc"
    assert "entities" not in hi.build_document(item("m1"), with_entities=False)


def test_banks_private_per_persona_shared_otherwise():
    assert hi.bank_for(item("a", persona="alice"), "llm") == "llm-persona-alice"
    assert hi.bank_for(item("b", scope="persona_and_committee"), "llm") == "llm-shared"
    assert hi.bank_for(item("c", layer="institutional"), "llm") == "llm-shared"


def test_persona_never_sees_other_private_any_arm():
    items = [item("pa", persona="alice"), item("pb", persona="bob"), item("sh", layer="institutional")]
    for fam in ("llm", "chunks"):
        ix = hi.HindsightIndex(FakeHindsight(), fam)
        ix.retain(items)
        got = ix.recall("alice", "q", known_ids={"pa", "pb", "sh"}).canonical_ids
        assert "pb" not in got and set(got) == {"pa", "sh"}
        got = ix.recall("bob", "q", tags=["family:momentum"], known_ids={"pa", "pb", "sh"}).canonical_ids
        assert "pa" not in got and "pb" in got


def test_unmappable_dropped_and_counted():
    fake = FakeHindsight()
    ix = hi.HindsightIndex(fake, "llm")
    ix.retain([item("sh", layer="institutional")])
    fake.banks["llm-shared"]["ghost"] = {"document_id": "ghost", "content": "x", "tags": []}
    fake.banks["llm-shared"]["nodoc"] = {"document_id": "", "content": "x", "tags": []}
    r = ix.recall("alice", "q", known_ids={"sh"})
    assert r.canonical_ids == ["sh"] and r.dropped == 2


def test_observation_maps_via_source_fact_ids():
    fake = FakeHindsight()
    ix = hi.HindsightIndex(fake, "llm")
    ix.retain([item("sh", layer="institutional")])
    ix.fact_to_doc["f9"] = "sh"
    assert ix._map({"source_fact_ids": ["f9"]}, {"sh"}) == "sh"


def test_rebuild_deletes_then_reretains_and_chunks_config():
    fake = FakeHindsight()
    ix = hi.HindsightIndex(fake, "chunks")
    items = [item("sh", layer="institutional")]
    ix.retain(items)
    n = len(fake.calls)
    ix.rebuild(items)
    assert [c[0] for c in fake.calls[n:n + 2]] == ["DELETE", "POST"]
    assert any(c[0] == "PATCH" and c[2] == {"updates": {"retain_extraction_mode": "chunks"}} for c in fake.calls)


def test_global_ranking_across_banks_before_topk():
    items = [item(f"p{i}", persona="alice") for i in range(8)] + [item("sh1", layer="institutional"),
                                                                    item("sh2", layer="institutional")]
    ix = hi.HindsightIndex(FakeHindsight(), "llm")
    ix.retain(items)
    known = {canonical for canonical in [hi.canonical_id(i) for i in items]}
    got = ix.recall("alice", "q", known_ids=known).canonical_ids
    assert len(got) == 8 and {"sh1", "sh2"} <= set(got)           # shared hits not starved by 8 private hits
    # usefulness-style rerank is applied to the whole pool before the cut
    got = ix.recall("alice", "q", known_ids=known, rerank=lambda ids: sorted(ids, reverse=True)).canonical_ids
    assert got == sorted(known, reverse=True)[:8]


def test_scored_results_ranked_globally():
    rows = [[{"document_id": "a", "score": 0.1}, {"document_id": "b", "score": 0.9}],
            [{"document_id": "c", "score": 0.5}]]
    assert [r["document_id"] for r in hi._merge_banks(rows)] == ["b", "c", "a"]
