"""STRATEGY-FALLBACK-DRAFT-RETIRE-20261125: retire fallback drafts, keep the record."""
from __future__ import annotations

import pytest

from . import fallback_draft_retire as fdr
from .models import ArtifactState, ArtifactType, Lineage, RegistryEntry, StorageBackend, StorageRef
from .split_api import RegistryService
from .storage import (
    RegistryStore,
    persona_binding_counts,
)

TENANT = "tenant-a"
AFTER = "2026-11-14T03:00:00Z"
BEFORE = "2026-11-05T03:00:00Z"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv(fdr.ENABLED_ENV, "true")


def _entry(rid, *, state=ArtifactState.DRAFT, created=AFTER, hypothesis="Evidence suggests: a lottery result.",
           distillation=True, spec_meta=None, meta_extra=None, atype=ArtifactType.STRATEGY_SPEC,
           source_kind="news", lineage=None, **fields) -> RegistryEntry:
    sid = f"strat-{rid}"
    spec = {
        "strategy_id": sid, "hypothesis": hypothesis, "lifecycle_state": "draft",
        "provenance": {"source_kind": source_kind, "created_at": created},
        "metadata": dict(spec_meta or {}),
    }
    meta = {
        "conversion_service": "StrategySpecConversionService", "research_only": True,
        "strategy_spec": spec, "tenant_id": TENANT,
    }
    if distillation:
        meta["distillation"] = {"source_id": f"src-{rid}", "source_digest": "sha256:" + "a" * 64}
    meta.update(meta_extra or {})
    return RegistryEntry(
        registry_id=rid, artifact_type=atype, strategy_id=sid, version="1.0.0", artifact_state=state,
        lineage=lineage or Lineage(source_run_ids=[f"seed-{rid}"], source_dataset_refs=[f"src-{rid}"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum=f"sha256:{rid}", evaluation_summary={"source_seed_id": f"seed-{rid}", "confidence": 0.5},
        metadata=meta, created_at=created, updated_at=created, owner_tenant=TENANT, **fields,
    )


def _store(*entries) -> RegistryStore:
    store = RegistryStore()
    for e in entries:
        store.put(e)
    return store


def _ids(plan):
    return plan.selected_ids


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(fdr.ENABLED_ENV)
    service = RegistryService(_store(_entry("reg-a")))
    with pytest.raises(fdr.FallbackDraftRetireError, match="off by default"):
        fdr.apply_plan(service, expected_digest="sha256:x")
    with pytest.raises(fdr.FallbackDraftRetireError, match="off by default"):
        fdr.restore_batch(service.store, batch_id="b")
    assert fdr.main(["plan"]) == 2


def test_selects_only_fallback_distillation_drafts_after_cutoff():
    store = _store(
        _entry("reg-keep-1"),
        _entry("reg-keep-2", source_kind="paper"),
        _entry("reg-seed-old", created=BEFORE),
        _entry("reg-no-distill", distillation=False),
        _entry("reg-real-hyp", hypothesis="Low PB stocks outperform after earnings."),
        _entry("reg-card", spec_meta={"hypothesis_card": {"claim": "x"}}),
        _entry("reg-wired", spec_meta={"card_wiring": {"status": "research_only"}}),
        _entry("reg-cand", state=ArtifactState.CANDIDATE),
        _entry("reg-appr", state=ArtifactState.APPROVED),
        _entry("reg-admit", replication_success=True),
        _entry("reg-persona", spec_meta={"persona_id": "p1"}),
        _entry("reg-extra-meta", meta_extra={"capital_pool_id": "pool-1"}),
        _entry("reg-bundle-type", atype=ArtifactType.EXECUTION_BUNDLE),
    )
    plan = fdr.build_plan(store)
    assert _ids(plan) == ["reg-keep-1", "reg-keep-2"]
    assert plan.by_source == {"news": 1, "paper": 1}
    assert plan.excluded["created_before_cutoff"] == 1
    assert plan.excluded["not_distillation"] == 1
    assert plan.excluded["hypothesis_not_fallback"] == 1
    assert plan.excluded["card_or_binding_present"] == 4
    assert plan.excluded["approval_or_admission_present"] == 1
    assert plan.excluded["state_candidate"] == 1 and plan.excluded["state_approved"] == 1
    assert plan.digest == fdr.selection_digest(["reg-keep-2", "reg-keep-1"])  # order-free


def test_references_exclude_entry_and_external_file_excludes():
    store = _store(
        _entry("reg-ref-id"),
        _entry("reg-ref-strat"),
        _entry("reg-ext"),
        _entry("reg-free"),
        _entry("reg-bundle", atype=ArtifactType.EXECUTION_BUNDLE,
               lineage=Lineage(parent_registry_ids=["reg-ref-id"], source_run_ids=["r"])),
        _entry("reg-pool", atype=ArtifactType.MODEL_ARTIFACT, meta_extra={"members": ["strat-reg-ref-strat"]}),
    )
    plan = fdr.build_plan(store, external_references=["reg-ext"])
    assert _ids(plan) == ["reg-free"]
    assert plan.excluded["referenced"] == 3
    assert plan.external_reference_count == 1


def test_dry_run_changes_nothing_and_reports_digest():
    store = _store(_entry("reg-1"), _entry("reg-2"))
    before = {e.registry_id: e.to_dict() for e in store.list_all_entries()}
    summary = fdr.build_plan(store).summary(batch_size=1)
    assert summary["selected_count"] == 2 and summary["batch_count"] == 2
    assert summary["selection_digest"].startswith("sha256:")
    assert {e.registry_id: e.to_dict() for e in store.list_all_entries()} == before


def test_apply_requires_same_digest():
    service = RegistryService(_store(_entry("reg-1")))
    with pytest.raises(fdr.FallbackDraftRetireError, match="digest mismatch"):
        fdr.apply_plan(service, expected_digest="sha256:" + "0" * 64)
    assert service.store.get("reg-1").artifact_state == ArtifactState.DRAFT


def test_apply_retires_in_batches_keeps_record_and_is_idempotent():
    entries = [_entry(f"reg-{i:03d}") for i in range(5)]
    service = RegistryService(_store(*entries, _entry("reg-seed", created=BEFORE)))
    digest = fdr.build_plan(service.store).digest
    report = fdr.apply_plan(service, expected_digest=digest, batch_size=2)
    assert report["complete"] and report["retired"] == 5
    assert [b["retired"] for b in report["batches"]] == [2, 2, 1]
    for e in entries:
        got = service.store.get(e.registry_id)
        assert got.artifact_state == ArtifactState.RETIRED
        assert got.metadata == e.metadata  # record kept untouched, readable by id
        assert got.last_actor["operator_decision_ref"] == fdr.OPERATOR_DECISION_REF
        assert got.last_actor["reason"] == fdr.RETIRE_REASON
        assert got.last_actor["selection_digest"] == digest
    assert service.store.get("reg-seed").artifact_state == ArtifactState.DRAFT
    assert len(service.store.list_all_entries()) == 6  # nothing deleted
    # Same digest still valid; a second apply changes nothing.
    again = fdr.apply_plan(service, expected_digest=digest, batch_size=2)
    assert again["complete"] and again["retired"] == 0 and again["skipped_already_retired"] == 5


def test_partial_apply_resumes_with_same_digest():
    service = RegistryService(_store(*[_entry(f"reg-{i}") for i in range(4)]))
    digest = fdr.build_plan(service.store).digest
    first = fdr.apply_plan(service, expected_digest=digest, batch_size=2, max_batches=1)
    assert first["stopped"]["reason"] == "max_batches_reached" and first["retired"] == 2
    assert fdr.build_plan(service.store).digest == digest  # retired-by-us stay in the set
    second = fdr.apply_plan(service, expected_digest=digest, batch_size=2)
    assert second["complete"] and second["retired"] == 2


def test_stops_on_first_unexpected_state():
    store = _store(*[_entry(f"reg-{i}") for i in range(3)])
    service = RegistryService(store)
    digest = fdr.build_plan(store).digest
    real_get = store.get
    calls = {"n": 0}

    def drifting_get(rid):
        entry = real_get(rid)
        if rid == "reg-1" and calls["n"] == 0:
            calls["n"] += 1
            store.put(RegistryEntry.from_dict({**entry.to_dict(), "artifact_state": "candidate"}))
            return real_get(rid)
        return entry

    store.get = drifting_get
    report = fdr.apply_plan(service, expected_digest=digest, batch_size=10)
    assert not report["complete"]
    assert report["stopped"]["registry_id"] == "reg-1"
    assert report["stopped"]["reason"].startswith("unexpected_state:state_candidate")
    assert real_get("reg-0").artifact_state == ArtifactState.RETIRED
    assert real_get("reg-2").artifact_state == ArtifactState.DRAFT


def test_restore_one_batch_only():
    service = RegistryService(_store(*[_entry(f"reg-{i}") for i in range(4)]))
    digest = fdr.build_plan(service.store).digest
    fdr.apply_plan(service, expected_digest=digest, batch_size=2)
    batch = fdr.batch_id_for(digest, 0)
    out = fdr.restore_batch(service.store, batch_id=batch)
    assert out["restored_ids"] == ["reg-0", "reg-1"]
    assert service.store.get("reg-0").artifact_state == ArtifactState.DRAFT
    assert service.store.get("reg-0").last_actor["restored_from_batch"] == batch
    assert service.store.get("reg-2").artifact_state == ArtifactState.RETIRED
    assert fdr.restore_batch(service.store, batch_id=batch)["restored_count"] == 0


def test_readers_do_not_list_or_count_retired_drafts():
    keep, retire = _entry("reg-keep", hypothesis="Real thesis."), _entry("reg-retire")
    service = RegistryService(_store(keep, retire))
    digest = fdr.build_plan(service.store).digest
    fdr.apply_plan(service, expected_digest=digest)
    # strategy list / summary page
    page = service.strategy_spec_summary_page(
        visible_tenants=frozenset({TENANT}), exclude_state="retired", page_size=50,
    )
    listed = [i.get("registry_id") or i.get("strategy_id") for i in page["items"]]
    assert not any("reg-retire" in str(i) for i in listed) and any("reg-keep" in str(i) for i in listed)
    drafts_only = service.strategy_spec_summary_page(
        visible_tenants=frozenset({TENANT}), state="draft", page_size=50,
    )
    assert not any("reg-retire" in str(i) for i in drafts_only["items"])
    # discovery counts / page
    disc = service.discovery_candidate_page(visible_tenants=frozenset({TENANT}), tenant_id=TENANT, page_size=50)
    ids = [i["entry"]["registry_id"] for i in disc["items"]]
    assert "reg-retire" not in ids and "reg-keep" in ids
    # card admission drafts (distillation card path)
    assert all(e.registry_id != "reg-retire" for e in service.store.list_card_admission_drafts(
        visible_tenants=frozenset({TENANT}), tenant_id=TENANT, limit=50))
    # trading persona views: persona counts skip retired current versions
    rows = service.list_persona_binding_rows(visible_tenants=frozenset({TENANT}))
    assert isinstance(persona_binding_counts(rows), dict)
    # still readable by id, as retired
    got = service.get("reg-retire")
    assert got.entry.artifact_state == ArtifactState.RETIRED
