"""Unit tests for the Hindsight mirror, anchors and rebuild (fake transport only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from services.memory import hindsight_index as hs
from services.memory.institutional_memory_store import InstitutionalMemoryEntry, InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryEntry, PersonaMemoryStore


class FakeClient:
    def __init__(self, fail: int = 0, reason: str = "hindsight_unreachable"):
        self.retained, self.invalidated, self.deleted = [], [], []
        self.fail, self.reason = fail, reason

    def _maybe_fail(self):
        if self.fail:
            self.fail -= 1
            raise hs.HindsightError(self.reason)

    def retain(self, bank, items):
        self._maybe_fail()
        self.retained.append((bank, items))

    def invalidate_document(self, bank, document_id, reason):
        self._maybe_fail()
        self.invalidated.append((bank, document_id, reason))
        return 1

    def delete_bank(self, bank):
        self.deleted.append(bank)


def mid(name: str) -> str:
    """Canonical-format id for a readable test name."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name)) if name.startswith("p-") or name in {"pmine", "pother", "priv", "com"} \
        else "mem-" + str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def institution(**changes):
    payload = dict(entry_id=f"mem-{uuid.uuid4()}", knowledge_type="incident_lesson",
        content={"headline": "台股期貨風控檢討", "body": "流動性不足時應避免大部位下單", "tags": ["risk"]},
        source_event_type="postmortem_published", source_event_id=f"pm-{uuid.uuid4()}",
        written_at=datetime.now(timezone.utc).isoformat(), write_authority="incident-svc",
        scope="system_wide", tenant_id="projection-test")
    payload.update(changes)
    return InstitutionalMemoryEntry(**payload)


def persona(**changes):
    payload = dict(memory_id=str(uuid.uuid4()), persona_id="persona-alpha", memory_type="strategy_lesson",
        content={"summary": "流動性不足時應避免大部位下單", "tags": ["risk"]},
        source_event_type="session_end", source_event_id=f"session-{uuid.uuid4()}",
        written_at=datetime.now(timezone.utc).isoformat(), write_authority="persona-memory-svc",
        tenant_id="projection-test", relevance_scope="persona_and_committee")
    payload.update(changes)
    return PersonaMemoryEntry(**payload)


def inst(entry_id=None, **kw):
    base = dict(entry_id=entry_id or mid("mem-1"), knowledge_type="incident_lesson",
                content={"headline": "止損失敗", "body": "流動性不足", "tags": ["family:Momentum", "risk"],
                         "structured_payload": {"underlyings": ["TW", "TX"], "dataset_refs": [{"dataset_id": "d1"}],
                                                "regime_tag": "high_vol"}},
                source_event_type="postmortem_published", source_event_id="pm-1",
                written_at="2026-09-01T12:00:00Z", write_authority="incident-svc",
                scope="system_wide", tenant_id="t1")
    base.update(kw)
    return InstitutionalMemoryEntry(**base)


def pentry(memory_id=None, **kw):
    base = dict(memory_id=memory_id or mid("p-1"), persona_id="alpha", memory_type="preference",
                content={"summary": "偏好動能", "tags": []}, source_event_type="session_end",
                source_event_id="s-1", written_at="2026-09-01T12:00:00Z",
                write_authority="persona-memory-svc", tenant_id="t1")
    base.update(kw)
    return PersonaMemoryEntry(**base)


@pytest.fixture(autouse=True)
def _reset():
    hs.DIAGNOSTICS.reset()
    hs.set_hindsight_client(None)
    yield
    hs.set_hindsight_client(None)


def test_anchors_are_deterministic_from_fields_and_tags():
    assert hs.anchors_for(inst()) == [
        "dataset:d1", "family:momentum", "regime:high_vol", "underlying:tw", "underlying:tx"]
    assert hs.anchors_for(inst(scope="instrument_class", scope_filter="Futures",
                               content={"headline": "h", "body": "b"})) == ["instrument:futures"]
    assert hs.anchors_for(pentry()) == []


def test_retain_item_shape_for_institutional_and_persona():
    item = hs.retain_item(inst())
    assert item["document_id"] == mid("mem-1") and item["context"] == "postmortem_published"
    assert item["resolve_entities"] is False and item["timestamp"] == "2026-09-01T12:00:00Z"
    assert {e["text"] for e in item["entities"]} == {"dataset:d1", "family:momentum", "regime:high_vol",
                                                     "underlying:tw", "underlying:tx"}
    assert "anchor:family:momentum" in item["tags"] and "scope:system_wide" in item["tags"]
    assert "type:incident_lesson" in item["tags"]
    assert all(isinstance(v, str) for v in item["metadata"].values())
    p = hs.retain_item(pentry(relevance_scope="persona_and_committee"))
    assert p["tags"][0] == hs.ANCHOR_NONE_TAG and "scope:committee" in p["tags"]
    assert hs.retain_item(pentry())["tags"][1] == "scope:private"


def test_banks_isolate_tenant_and_persona():
    assert hs.facts_bank("t1") != hs.facts_bank("t2")
    assert hs.persona_bank("t1", mid("a")) != hs.persona_bank("t1", mid("b")) != hs.facts_bank("t1")
    assert hs.bank_for(pentry()) == hs.persona_bank("t1", "alpha")
    assert hs.bank_for(inst()) == hs.facts_bank("t1")


def test_store_create_supersede_archive_expiry_are_mirrored_after_persist():
    client = FakeClient()
    hs.set_hindsight_client(client)
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))
    store.create(inst(mid("b")))
    assert [i[1][0]["document_id"] for i in client.retained] == [mid("a"), mid("b")]
    store.supersede(mid("a"), mid("b"))
    store.archive(mid("b"), reason="obsolete")
    assert client.invalidated == [(hs.facts_bank("t1"), mid("a"), "superseded"), (hs.facts_bank("t1"), mid("b"), "obsolete")]
    store.create(inst(mid("e"), expires_at="2099-01-01T00:00:00Z"))
    archived = store.archive_expired(now="2100-01-01T00:00:00Z", reason="ttl")
    assert [x.entry_id for x in archived] == [mid("e")]
    assert client.invalidated[-1] == (hs.facts_bank("t1"), mid("e"), "ttl")


def test_persona_store_mirrors_into_persona_bank():
    client = FakeClient()
    hs.set_hindsight_client(client)
    store = PersonaMemoryStore()
    store.create(pentry())
    assert client.retained[0][0] == hs.persona_bank("t1", "alpha")
    store.archive(mid("p-1"), reason="stale")
    assert client.invalidated == [(hs.persona_bank("t1", "alpha"), mid("p-1"), "stale")]


def test_mirror_retries_then_reports_and_never_blocks_canonical_write(monkeypatch):
    monkeypatch.setattr(hs.time, "sleep", lambda *_: None)
    client = FakeClient(fail=2)
    hs.set_hindsight_client(client)
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))  # 2 failures then success
    assert len(client.retained) == 1 and hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    client.fail = 99
    created = store.create(inst(mid("b")))
    assert store.get(mid("b")) is created  # canonical write persisted despite outage
    snap = hs.DIAGNOSTICS.snapshot()
    assert snap["counts"]["failed"] == 1
    assert snap["failures"] == [{"entry_id": mid("b"), "action": "retain", "reason": "hindsight_unreachable"}]


def test_unconfigured_mirror_reports_reason_without_raising(monkeypatch):
    monkeypatch.delenv(hs.URL_ENV, raising=False)
    result = hs.mirror_entry(inst())
    assert result == {"status": "failed", "entry_id": mid("mem-1"), "action": "retain", "reason": "hindsight_unconfigured"}


def test_anchor_none_is_counted():
    hs.set_hindsight_client(FakeClient())
    hs.mirror_entry(pentry())
    assert hs.DIAGNOSTICS.snapshot()["counts"]["anchor_none"] == 1


def test_rebuild_drops_banks_and_reretains_active_entries_idempotently():
    client = FakeClient()
    istore, pstore = InstitutionalMemoryStore(), PersonaMemoryStore()
    istore.create(inst(mid("a")))
    istore.create(inst(mid("b")))
    istore.archive(mid("b"), reason="x")
    pstore.create(pentry())
    first = hs.rebuild(istore, pstore, client=client)
    second = hs.rebuild(istore, pstore, client=client)
    assert first == second
    assert first["banks"] == 2 and first["retained"] == 2 and first["inactive_skipped"] == 1
    assert first["anchor_none"] == 1 and first["failures"] == []
    assert set(client.deleted) == {hs.facts_bank("t1"), hs.persona_bank("t1", "alpha")}


def test_rebuild_reports_failures():
    client = FakeClient(fail=1)
    istore = InstitutionalMemoryStore()
    istore.create(inst(mid("a")))
    report = hs.rebuild(istore, PersonaMemoryStore(), client=client)
    assert report["failures"][0]["reason"] == "hindsight_unreachable"
