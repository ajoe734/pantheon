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
        self.events = []
        self._active_units: dict[tuple[str, str], list[dict]] = {}
        self.fail, self.reason = fail, reason

    def _maybe_fail(self):
        if self.fail:
            self.fail -= 1
            raise hs.HindsightError(self.reason)

    def retain(self, bank, items):
        self._maybe_fail()
        self.retained.append((bank, items))
        doc_id = items[0]["document_id"]
        self.events.append(("retain", doc_id))
        self._active_units[(bank, doc_id)] = [{"id": f"unit-{doc_id}", "document_id": doc_id}]

    def invalidate_document(self, bank, document_id, reason):
        self._maybe_fail()
        self.invalidated.append((bank, document_id, reason))
        self.events.append(("invalidate", document_id))
        self._active_units.pop((bank, document_id), None)
        return 1

    def memory_units(self, bank, document_id):
        return list(self._active_units.get((bank, document_id), []))

    def delete_bank(self, bank):
        self.deleted.append(bank)
        for key in [k for k in list(self._active_units) if k[0] == bank]:
            self._active_units.pop(key, None)


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
    hs.stop_resync_timer()
    hs.flush_mirrors()
    hs.DIAGNOSTICS.reset()
    hs.set_hindsight_client(None)
    yield
    hs.stop_resync_timer()
    hs.flush_mirrors()
    hs.DIAGNOSTICS.reset()
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
    hs.flush_mirrors()
    # The mirror only guarantees FIFO per (bank, canonical_id); cross-document order is unspecified.
    assert sorted(i[1][0]["document_id"] for i in client.retained) == sorted([mid("a"), mid("b")])
    store.supersede(mid("a"), mid("b"))
    store.archive(mid("b"), reason="obsolete")
    hs.flush_mirrors()
    assert sorted(client.invalidated) == sorted(
        [(hs.facts_bank("t1"), mid("a"), "superseded"), (hs.facts_bank("t1"), mid("b"), "obsolete")])
    # Per-document lifecycle order is still exact: retain before invalidate.
    for doc in (mid("a"), mid("b")):
        assert client.events.index(("retain", doc)) < client.events.index(("invalidate", doc))
    store.create(inst(mid("e"), expires_at="2099-01-01T00:00:00Z"))
    archived = store.archive_expired(now="2100-01-01T00:00:00Z", reason="ttl")
    hs.flush_mirrors()
    assert [x.entry_id for x in archived] == [mid("e")]
    assert client.invalidated[-1] == (hs.facts_bank("t1"), mid("e"), "ttl")


def test_cross_document_reordering_is_tolerated_and_per_document_order_is_exact(monkeypatch):
    import threading
    import time

    monkeypatch.setattr(hs, "MAX_MIRROR_WORKERS", 4)

    class Delayed(FakeClient):
        def retain(self, bank, items):
            if items[0]["document_id"] == mid("a"):
                time.sleep(0.15)  # force b to overtake a across documents
            return super().retain(bank, items)

    client = Delayed()
    hs.set_hindsight_client(client)
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))
    store.create(inst(mid("b")))
    store.supersede(mid("a"), mid("b"))
    store.archive(mid("b"), reason="obsolete")
    assert hs.flush_mirrors()
    assert sorted(i[1][0]["document_id"] for i in client.retained) == sorted([mid("a"), mid("b")])
    assert sorted(client.invalidated) == sorted(
        [(hs.facts_bank("t1"), mid("a"), "superseded"), (hs.facts_bank("t1"), mid("b"), "obsolete")])
    for doc in (mid("a"), mid("b")):
        assert client.events.index(("retain", doc)) < client.events.index(("invalidate", doc))


def test_persona_store_mirrors_into_persona_bank():
    client = FakeClient()
    hs.set_hindsight_client(client)
    store = PersonaMemoryStore()
    store.create(pentry())
    hs.flush_mirrors()
    assert client.retained[0][0] == hs.persona_bank("t1", "alpha")
    store.archive(mid("p-1"), reason="stale")
    hs.flush_mirrors()
    assert client.invalidated == [(hs.persona_bank("t1", "alpha"), mid("p-1"), "stale")]


def test_mirror_retries_then_reports_and_never_blocks_canonical_write(monkeypatch):
    monkeypatch.setattr(hs.time, "sleep", lambda *_: None)
    client = FakeClient(fail=2)
    hs.set_hindsight_client(client)
    store = InstitutionalMemoryStore()
    store.create(inst(mid("a")))  # 2 failures then success
    hs.flush_mirrors()
    assert len(client.retained) == 1 and hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    client.fail = 99
    created = store.create(inst(mid("b")))
    assert store.get(mid("b")) is created  # canonical write persisted despite outage
    hs.flush_mirrors()
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


def test_slow_index_does_not_block_canonical_writes_and_keeps_document_order(tmp_path):
    import json
    import threading
    import time

    for kind, cls, make_first, make_second in (
        ("institutional", InstitutionalMemoryStore, lambda: inst(mid("s1")), lambda: inst(mid("s2"))),
        ("persona", PersonaMemoryStore, lambda: pentry(mid("p-1")), lambda: pentry(mid("p-2"))),
    ):
        entered, gate, events = threading.Event(), threading.Event(), []

        class Slow(FakeClient):
            def retain(self, bank, items):
                entered.set()
                assert gate.wait(10), "slow index gate timed out"
                events.append(("retain", items[0]["document_id"]))
                return super().retain(bank, items)

            def invalidate_document(self, bank, doc_id, reason):
                assert gate.wait(10), "slow index gate timed out"
                events.append(("invalidate", doc_id))
                return super().invalidate_document(bank, doc_id, reason)

        hs.set_hindsight_client(Slow())
        path = tmp_path / f"{kind}.json"
        store = cls(path=path)
        t0 = time.monotonic()
        first = store.create(make_first())
        assert entered.wait(2), "first mirror did not enter index transport"
        assert len(json.loads(path.read_text(encoding="utf-8"))) == 1

        got_lock = store._lock.acquire(blocking=False)
        if got_lock:
            store._lock.release()
        assert got_lock, f"{kind} canonical lock held while external index call is blocked"

        second_result = []
        second_thread = threading.Thread(target=lambda: second_result.append(store.create(make_second())))
        second_thread.start()
        second_thread.join(timeout=1.0)
        assert not second_thread.is_alive(), f"{kind} concurrent canonical write blocked on slow index"
        assert len(second_result) == 1
        assert len(json.loads(path.read_text(encoding="utf-8"))) == 2

        first_id = hs.canonical_id(first)
        second_id = hs.canonical_id(second_result[0])
        store.supersede(first_id, second_id)
        store.archive(second_id, reason="stale")
        assert time.monotonic() - t0 < 2.0
        assert store.require(first_id).superseded_by == second_id
        assert store.require(second_id).archived_reason == "stale"
        assert events == []

        gate.set()
        assert hs.flush_mirrors()
        assert events.index(("retain", first_id)) < events.index(("invalidate", first_id))
        assert events.index(("retain", second_id)) < events.index(("invalidate", second_id))


def test_outage_retry_backoff_does_not_block_concurrent_canonical_writes(tmp_path, monkeypatch):
    import json
    import threading
    import time

    sleep_entered = threading.Event()
    sleep_gate = threading.Event()
    attempts_by_doc: dict[str, int] = {}
    attempts_lock = threading.Lock()

    class OutageClient:
        def retain(self, bank, items):
            doc_id = items[0]["document_id"]
            with attempts_lock:
                attempts_by_doc[doc_id] = attempts_by_doc.get(doc_id, 0) + 1
            raise hs.HindsightError("hindsight_unreachable", "simulated timeout")

        def invalidate_document(self, bank, document_id, reason):
            with attempts_lock:
                attempts_by_doc[f"inv:{document_id}"] = attempts_by_doc.get(f"inv:{document_id}", 0) + 1
            raise hs.HindsightError("hindsight_unreachable", "simulated timeout")

    def gated_sleep(_seconds: float) -> None:
        sleep_entered.set()
        sleep_gate.wait(5)

    monkeypatch.setattr(hs.time, "sleep", gated_sleep)
    hs.set_hindsight_client(OutageClient())

    inst_path = tmp_path / "inst-outage.json"
    persona_path = tmp_path / "persona-outage.json"
    inst = InstitutionalMemoryStore(path=inst_path)
    pstore = PersonaMemoryStore(path=persona_path)

    t0 = time.monotonic()
    first_inst = inst.create(institution(entry_id=mid("outage-1")))
    assert sleep_entered.wait(2), "first mirror did not enter retry backoff"

    second_inst = inst.create(institution(entry_id=mid("outage-2")))
    first_persona = pstore.create(persona(memory_id=mid("p-outage-1")))
    pstore.archive(first_persona.memory_id, reason="outage_test")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"canonical writes blocked on retry backoff ({elapsed:.3f}s)"
    assert len(json.loads(inst_path.read_text(encoding="utf-8"))) == 2
    assert len(json.loads(persona_path.read_text(encoding="utf-8"))) == 1

    sleep_gate.set()
    assert hs.flush_mirrors()
    assert attempts_by_doc[first_inst.entry_id] == hs.MIRROR_ATTEMPTS
    assert attempts_by_doc[second_inst.entry_id] == hs.MIRROR_ATTEMPTS
    assert attempts_by_doc[first_persona.memory_id] == hs.MIRROR_ATTEMPTS
    assert attempts_by_doc[f"inv:{first_persona.memory_id}"] == hs.MIRROR_ATTEMPTS
    snap = hs.DIAGNOSTICS.snapshot()
    assert snap["counts"]["failed"] == 4
    assert {f["action"] for f in snap["failures"]} == {"retain", "invalidate"}


def test_rebuild_aborts_before_dropping_banks_when_mirror_drain_times_out(monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()
    events = []

    class GatedClient(FakeClient):
        def retain(self, bank, items):
            if threading.current_thread().name == "memory-mirror":
                events.append("original_mirror_entered")
                entered.set()
                assert release.wait(5), "mirror release gate timed out"
                events.append("original_mirror_finished")
            else:
                events.append("rebuild_retain")
            return super().retain(bank, items)

        def delete_bank(self, bank):
            events.append("bank_deleted_while_mirror_inflight" if not release.is_set() else "bank_deleted_after_release")
            return super().delete_bank(bank)

    client = GatedClient()
    hs.set_hindsight_client(client)
    istore, pstore = InstitutionalMemoryStore(), PersonaMemoryStore()
    real_flush = hs.flush_mirrors
    flush_results = []

    def short_flush(timeout: float = 0.02):
        value = real_flush(timeout=0.02)
        flush_results.append(value)
        return value

    monkeypatch.setattr(hs, "flush_mirrors", short_flush)
    try:
        istore.create(inst(mid("inflight-1")))
        assert entered.wait(2), "mirror did not enter transport"
        report = hs.rebuild(istore, pstore, client=client)
        before_release = list(events)
    finally:
        release.set()
        assert real_flush(timeout=2.0), "mirror failed to drain after release"

    assert flush_results == [False]
    assert before_release == ["original_mirror_entered"]
    assert "bank_deleted_while_mirror_inflight" not in events
    assert "rebuild_retain" not in events
    assert report["banks"] == 0 and report["retained"] == 0 and report["bank_counts"] == {}
    assert report["failures"] == [{"reason": "hindsight_mirror_drain_timeout"}]


def test_rebuild_orders_concurrent_writes_after_bank_repopulation_without_blocking_writers():
    import threading
    import time

    delete_entered, release_rebuild = threading.Event(), threading.Event()
    events = []

    class RecordingClient(FakeClient):
        def delete_bank(self, bank):
            events.append(("delete_bank", bank))
            delete_entered.set()
            assert release_rebuild.wait(5), "rebuild delete_bank gate timed out"
            return super().delete_bank(bank)

        def retain(self, bank, items):
            source = "mirror_retain" if threading.current_thread().name == "memory-mirror" else "rebuild_retain"
            events.append((source, items[0]["document_id"]))
            return super().retain(bank, items)

        def invalidate_document(self, bank, document_id, reason):
            events.append(("mirror_invalidate", document_id, reason))
            return super().invalidate_document(bank, document_id, reason)

    client = RecordingClient()
    istore, pstore = InstitutionalMemoryStore(), PersonaMemoryStore()
    istore.create(inst(mid("r-existing")))
    assert hs.flush_mirrors()
    hs.set_hindsight_client(client)

    rebuild_out = []
    rebuild_thread = threading.Thread(target=lambda: rebuild_out.append(hs.rebuild(istore, pstore, client=client)))
    rebuild_thread.start()
    assert delete_entered.wait(2), "rebuild did not enter delete_bank"

    t0 = time.monotonic()
    istore.archive(mid("r-existing"), reason="concurrent_archive")
    istore.create(inst(mid("r-new-active")))
    istore.create(inst(mid("r-new-transient")))
    istore.supersede(mid("r-new-transient"), mid("r-new-active"))
    assert time.monotonic() - t0 < 1.0, "canonical writes blocked while rebuild was in progress"
    assert events == [("delete_bank", hs.facts_bank("t1"))]

    release_rebuild.set()
    rebuild_thread.join(timeout=5.0)
    assert not rebuild_thread.is_alive()
    report = rebuild_out[0]
    assert report["failures"] == [] and report["banks"] == 1 and report["retained"] == 1

    del_idx = events.index(("delete_bank", hs.facts_bank("t1")))
    reb_existing_idx = events.index(("rebuild_retain", mid("r-existing")))
    inv_existing_idx = events.index(("mirror_invalidate", mid("r-existing"), "concurrent_archive"))
    ret_new_idx = events.index(("mirror_retain", mid("r-new-active")))
    ret_transient_idx = events.index(("mirror_retain", mid("r-new-transient")))
    inv_transient_idx = events.index(("mirror_invalidate", mid("r-new-transient"), "superseded"))

    assert del_idx < reb_existing_idx < inv_existing_idx
    assert del_idx < reb_existing_idx < ret_new_idx
    assert del_idx < reb_existing_idx < ret_transient_idx < inv_transient_idx


def test_mirror_dispatch_bounds_worker_threads_and_queue_capacity_with_truthful_diagnostics(monkeypatch):
    import threading

    monkeypatch.setattr(hs, "MAX_MIRROR_WORKERS", 2)
    monkeypatch.setattr(hs, "MAX_PENDING_MIRRORS", 3)

    two_workers_entered = threading.Event()
    release_workers = threading.Event()
    lock = threading.Lock()
    active_in_client = 0
    peak_in_client = 0

    class BoundedProbeClient(FakeClient):
        def retain(self, bank, items):
            nonlocal active_in_client, peak_in_client
            with lock:
                active_in_client += 1
                peak_in_client = max(peak_in_client, active_in_client)
                if active_in_client >= 2:
                    two_workers_entered.set()
            try:
                assert release_workers.wait(5), "worker gate timed out"
                return super().retain(bank, items)
            finally:
                with lock:
                    active_in_client -= 1

    client = BoundedProbeClient()
    hs.set_hindsight_client(client)
    istore = InstitutionalMemoryStore()

    # 2 entries occupy the 2 worker threads.
    istore.create(inst(mid("b-1")))
    istore.create(inst(mid("b-2")))
    assert two_workers_entered.wait(2), "expected 2 bounded workers to enter retain"

    # 3 distinct entries fill MAX_PENDING_MIRRORS=3; lifecycle invalidation on an already-queued
    # document is preserved in order, while new documents beyond MAX_PENDING_MIRRORS fail fast.
    istore.create(inst(mid("b-3")))
    istore.create(inst(mid("b-4")))
    istore.create(inst(mid("b-5")))
    istore.archive(mid("b-3"), reason="archived_while_queued")

    overflow_entry = istore.create(inst(mid("b-6")))
    overflow_archived = istore.create(inst(mid("b-7")))
    istore.archive(overflow_archived.entry_id, reason="overflow_archive")
    assert istore.get(overflow_entry.entry_id) is overflow_entry

    mirror_threads = [t for t in threading.enumerate() if t.name == "memory-mirror"]
    assert len(mirror_threads) <= 2

    snap_while_blocked = hs.DIAGNOSTICS.snapshot()
    assert snap_while_blocked["counts"]["failed"] == 3
    assert snap_while_blocked["failures"] == [
        {"entry_id": mid("b-6"), "action": "retain", "reason": "hindsight_mirror_queue_full"},
        {"entry_id": mid("b-7"), "action": "retain", "reason": "hindsight_mirror_queue_full"},
        {"entry_id": mid("b-7"), "action": "invalidate", "reason": "hindsight_mirror_queue_full"},
    ]

    release_workers.set()
    assert hs.flush_mirrors()
    assert peak_in_client == 2
    final_snap = hs.DIAGNOSTICS.snapshot()
    assert final_snap["counts"]["retained"] == 5
    assert final_snap["counts"]["invalidated"] == 1
    assert final_snap["counts"]["failed"] == 3
    assert {item[0]["document_id"] for _bank, item in client.retained} == {
        mid("b-1"), mid("b-2"), mid("b-3"), mid("b-4"), mid("b-5")
    }
    assert client.invalidated == [(hs.facts_bank("t1"), mid("b-3"), "archived_while_queued")]


def test_default_mirror_workers_is_one_and_configurable_via_env(monkeypatch):
    monkeypatch.delenv(hs.MIRROR_WORKERS_ENV, raising=False)
    assert hs.DEFAULT_MIRROR_WORKERS == 1
    assert hs._env_mirror_workers() == 1
    monkeypatch.setenv(hs.MIRROR_WORKERS_ENV, "3")
    assert hs._env_mirror_workers() == 3
    assert hs._configured_mirror_workers() == 3


def test_hindsight_client_and_mirror_entry_honour_bounded_429_retry_after(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {"Retry-After": "60"},
            io.BytesIO(b'{"error":"concurrency_limit"}'),
        )

    monkeypatch.setattr(hs.urllib.request, "urlopen", fake_urlopen)
    client = hs.HindsightClient("http://127.0.0.1:8888", "key")
    with pytest.raises(hs.HindsightError) as exc_info:
        client.retain("bank-1", [{"document_id": "d1", "content": "x"}])
    assert exc_info.value.reason == "hindsight_http_429"
    assert exc_info.value.retry_after == hs.MAX_RETRY_AFTER_SECONDS

    sleeps = []
    monkeypatch.setattr(hs.time, "sleep", lambda s: sleeps.append(s))

    class RateLimitedClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def retain(self, bank, items):
            self.calls += 1
            if self.calls == 1:
                raise hs.HindsightError("hindsight_http_429", "rate limit", retry_after=2.25)
            if self.calls == 2:
                raise hs.HindsightError("hindsight_http_429", "rate limit", retry_after=60.0)
            return super().retain(bank, items)

    rl_client = RateLimitedClient()
    res = hs.mirror_entry(inst(mid("rl-1")), client=rl_client)
    assert res["status"] == "mirrored"
    assert sleeps == [2.25, hs.MAX_RETRY_AFTER_SECONDS]


def test_failed_mirror_retained_in_bounded_resync_map_and_retried_by_timer_without_deleting_banks(monkeypatch):
    import time

    monkeypatch.setattr(hs.time, "sleep", lambda *_: None)
    assert hs.RESYNC_INTERVAL_SECONDS == 60.0
    client = FakeClient(fail=99)
    hs.set_hindsight_client(client)
    istore = InstitutionalMemoryStore()
    entry = istore.create(inst(mid("resync-1")))
    assert hs.flush_mirrors()
    assert hs.pending_resync_count() == 1
    assert hs.DIAGNOSTICS.snapshot()["pending_resync_count"] == 1
    assert hs._RESYNC_TIMER is not None and hs._RESYNC_TIMER.is_alive()

    # Cap at MAX_PENDING_MIRRORS
    monkeypatch.setattr(hs, "MAX_PENDING_MIRRORS", 3)
    for idx in (2, 3, 4):
        istore.create(inst(mid(f"resync-{idx}")))
    assert hs.flush_mirrors()
    assert hs.pending_resync_count() == 3

    # Invalidation removes the entry from the pending resync map
    istore.archive(mid("resync-2"), reason="no_longer_active")
    client.fail = 0
    assert hs.flush_mirrors()
    assert (hs.facts_bank("t1"), mid("resync-2")) not in hs._PENDING_RESYNC

    # Fast background timer tick converges remaining pending resync entries and stops when empty
    hs.stop_resync_timer()
    monkeypatch.setattr(hs, "RESYNC_INTERVAL_SECONDS", 0.03)
    monkeypatch.setattr(hs, "_LAST_RESYNC_AT", 0.0)
    with hs._IDLE:
        hs._arm_resync_timer_unlocked()
    deadline = time.monotonic() + 2.0
    while hs.pending_resync_count() > 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert hs.pending_resync_count() == 0
    assert hs._RESYNC_TIMER is None
    assert client.deleted == []
    retained_ids = {items[0]["document_id"] for _bank, items in client.retained}
    assert {mid("resync-3"), mid("resync-4")} <= retained_ids
    assert entry.entry_id not in retained_ids  # evicted when cap=3 was exceeded by resync-4


def test_catch_up_mirrors_is_idempotent_non_destructive_and_never_deletes_banks():
    istore, pstore = InstitutionalMemoryStore(), PersonaMemoryStore()
    # Created while Hindsight is unconfigured (simulating pre-existing or missed entries on startup)
    active_inst = istore.create(inst(mid("catchup-inst-1")))
    inactive_inst = istore.create(inst(mid("catchup-inst-archived")))
    istore.archive(inactive_inst.entry_id, reason="retired")
    active_persona = pstore.create(pentry(mid("p-catchup-1")))
    assert hs.flush_mirrors()

    client = FakeClient()
    hs.set_hindsight_client(client)
    hs.DIAGNOSTICS.reset()

    first = hs.catch_up_mirrors(istore, pstore, client=client)
    assert hs.flush_mirrors()
    assert first["status"] == "ok"
    assert first["scheduled"] == 2
    assert first["already_mirrored"] == 0
    assert first["inactive_skipped"] == 1
    assert client.deleted == []
    assert {items[0]["document_id"] for _bank, items in client.retained} == {
        active_inst.entry_id, active_persona.memory_id
    }

    # Second run is idempotent: schedules nothing and still never calls delete_bank
    second = hs.catch_up_mirrors(istore, pstore, client=client)
    assert hs.flush_mirrors()
    assert second["status"] == "ok"
    assert second["scheduled"] == 0
    assert second["already_mirrored"] == 2
    assert second["inactive_skipped"] == 1
    assert client.deleted == []
    assert len(client.retained) == 2


def test_burst_four_concurrent_retains_against_max_concurrency_two_bridge_converges_with_zero_drops(monkeypatch):
    import sys
    import threading
    import time
    from pathlib import Path

    adapter_dir = str(Path(__file__).resolve().parents[1] / "openclaw-gateway-adapter")
    if adapter_dir not in sys.path:
        sys.path.insert(0, adapter_dir)
    from memory_llm_bridge import BridgeConfig, MemoryLlmBridge

    # Force 4 concurrent mirror workers to exercise a 4-entry burst against a max_concurrency=2 bridge.
    monkeypatch.setattr(hs, "MAX_MIRROR_WORKERS", 4)
    bridge = MemoryLlmBridge(
        BridgeConfig(
            enabled=True,
            token="burst-token",
            max_concurrency=2,
            daily_budget=500,
            retry_after_seconds=1,
            slot_wait_seconds=5.0,
        )
    )

    class BridgeBackedClient(FakeClient):
        def retain(self, bank, items):
            def slow_llm(messages, instruction, remaining):
                time.sleep(0.08)
                return {"text": "{\"facts\": []}", "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

            resp = bridge.handle(
                authorization="Bearer burst-token",
                body={
                    "model": "memory-index",
                    "messages": [{"role": "user", "content": items[0]["content"]}],
                    "response_format": {"type": "json_object"},
                },
                operation_header="retain",
                invoke=slow_llm,
                verify_policy=lambda _agent, _deadline: None,
            )
            if resp.status_code != 200:
                retry_after = float(resp.headers["Retry-After"]) if "Retry-After" in resp.headers else None
                raise hs.HindsightError(f"hindsight_http_{resp.status_code}", str(resp.body), retry_after=retry_after)
            return super().retain(bank, items)

    client = BridgeBackedClient()
    hs.set_hindsight_client(client)
    entries = [
        pentry(mid("p-burst-1"), persona_id="alpha"),
        pentry(mid("p-burst-2"), persona_id="beta"),
        inst(mid("mem-burst-3")),
        inst(mid("mem-burst-4")),
    ]
    for entry in entries:
        hs.schedule_mirror(entry)
    assert hs.flush_mirrors(timeout=10.0)
    assert {items[0]["document_id"] for _bank, items in client.retained} == {
        hs.canonical_id(e) for e in entries
    }
    assert hs.pending_resync_count() == 0
    assert hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0

    # Also prove resync convergence when bridge slot_wait_seconds=0 forces immediate 429 during the burst:
    hs.DIAGNOSTICS.reset()
    nonblocking_bridge = MemoryLlmBridge(
        BridgeConfig(
            enabled=True,
            token="burst-token",
            max_concurrency=2,
            daily_budget=500,
            retry_after_seconds=1,
            slot_wait_seconds=0.0,
        )
    )
    release_first_two = threading.Event()
    two_entered = threading.Event()
    in_flight_lock = threading.Lock()
    in_flight_count = 0

    class NonBlockingBridgeClient(FakeClient):
        def retain(self, bank, items):
            def gated_llm(messages, instruction, remaining):
                nonlocal in_flight_count
                with in_flight_lock:
                    in_flight_count += 1
                    if in_flight_count >= 2:
                        two_entered.set()
                try:
                    release_first_two.wait(5)
                    return {"text": "{\"facts\": []}", "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
                finally:
                    with in_flight_lock:
                        in_flight_count -= 1

            resp = nonblocking_bridge.handle(
                authorization="Bearer burst-token",
                body={
                    "model": "memory-index",
                    "messages": [{"role": "user", "content": items[0]["content"]}],
                    "response_format": {"type": "json_object"},
                },
                operation_header="retain",
                invoke=gated_llm,
                verify_policy=lambda _agent, _deadline: None,
            )
            if resp.status_code != 200:
                raise hs.HindsightError(f"hindsight_http_{resp.status_code}", str(resp.body), retry_after=0.01)
            return super().retain(bank, items)

    monkeypatch.setattr(hs, "MIRROR_BACKOFF_SECONDS", 0.01)
    nb_client = NonBlockingBridgeClient()
    hs.set_hindsight_client(nb_client)
    for entry in entries[:2]:
        hs.schedule_mirror(entry)
    assert two_entered.wait(2)
    for entry in entries[2:]:
        hs.schedule_mirror(entry)
    # Wait until the 3rd and 4th exhaust their immediate retries and enter _PENDING_RESYNC
    deadline = time.monotonic() + 2.0
    while hs.pending_resync_count() < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert hs.pending_resync_count() == 2
    release_first_two.set()
    assert hs.flush_mirrors(timeout=5.0)
    resync_report = hs.resync_pending_mirrors()
    assert resync_report["mirrored"] == 2 and resync_report["pending_resync_count"] == 0
    assert {items[0]["document_id"] for _bank, items in nb_client.retained} == {
        hs.canonical_id(e) for e in entries
    }
    assert nb_client.deleted == []
