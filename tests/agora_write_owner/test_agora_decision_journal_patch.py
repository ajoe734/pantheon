"""
Tests for Agora Decision Journal create, merge-patch, diff calculation, and idempotency protection.
"""
from __future__ import annotations

import uuid
import pytest

from services.agora.store import AgoraStore, DictRecord


def test_agora_store_journal_methods_are_retired() -> None:
    store = AgoraStore()
    with pytest.raises(RuntimeError, match="retired"):
        store.create_journal_entry(
            entry_id="dj-retired-1",
            title="Retired Title",
            decision="allocate",
            actor_id="alice",
        )

    with pytest.raises(RuntimeError, match="retired"):
        store.patch_journal_entry(
            entry_id="dj-retired-1",
            patch={"title": "New Title"},
            actor_id="alice",
            idempotency_key="idem-1",
        )

    with pytest.raises(RuntimeError, match="retired"):
        store.get_journal_entry("dj-retired-1")

    with pytest.raises(RuntimeError, match="retired"):
        store.list_journal_entries()


def test_decision_journal_create_and_patch_lifecycle(tmp_path) -> None:
    import hashlib
    import json
    from services.governance.decision_journal import (
        build_decision_journal_stores,
        create_entry,
        get_entry,
        patch_entry,
    )

    stores = build_decision_journal_stores(tmp_path)
    entry_id = f"dj-{uuid.uuid4().hex[:8]}"

    # 1. Create Decision Journal Entry via canonical owner
    created = create_entry(
        stores,
        entry_id=entry_id,
        title="Initial Allocation Policy",
        body="allocate_paper_50k",
        actor_id="operator-alice",
        tenant_id="tenant-alpha",
        created_at="2026-09-08T00:00:00Z",
        tags=["paper", "allocation"],
        visibility="team",
    )
    assert created["id"] == entry_id
    assert created["title"] == "Initial Allocation Policy"
    assert created["body"] == "allocate_paper_50k"
    assert created["version"] == 1
    assert created["canonicalWriteAuthority"] == "governance-decision-journal-svc"

    # 2. Patch Entry with Merge Patch
    patch_idempotency_1 = f"idem-patch-{uuid.uuid4().hex[:8]}"
    patch_body = {"title": "Updated Allocation Policy v2", "body": "allocate_paper_100k"}
    request_hash_1 = hashlib.sha256(json.dumps(patch_body, sort_keys=True).encode("utf-8")).hexdigest()

    patch_result = patch_entry(
        stores,
        entry_id=entry_id,
        patch=patch_body,
        actor_id="operator-alice",
        tenant_id="tenant-alpha",
        idempotency_key=patch_idempotency_1,
        request_hash=request_hash_1,
        patched_at="2026-09-08T01:00:00Z",
        correlation_id="corr-001",
    )
    assert patch_result is not None
    assert patch_result["status"] == "updated"
    assert patch_result["entry"]["version"] == 2
    assert patch_result["entry"]["title"] == "Updated Allocation Policy v2"
    assert patch_result["entry"]["body"] == "allocate_paper_100k"
    assert patch_result["audit"]["diff"]["changedFields"] == ["title", "body"]

    # 3. Replay exact same patch with same idempotency key -> status "replayed"
    replay_result = patch_entry(
        stores,
        entry_id=entry_id,
        patch=patch_body,
        actor_id="operator-alice",
        tenant_id="tenant-alpha",
        idempotency_key=patch_idempotency_1,
        request_hash=request_hash_1,
        patched_at="2026-09-08T01:00:00Z",
        correlation_id="corr-001",
    )
    assert replay_result is not None
    assert replay_result["status"] == "replayed"
    assert replay_result["entry"]["version"] == 2

    # 4. Conflict: same idempotency key with different payload/hash -> status "conflict"
    conflicting_patch = {"title": "Conflicting Title Mutation"}
    conflicting_hash = hashlib.sha256(json.dumps(conflicting_patch, sort_keys=True).encode("utf-8")).hexdigest()
    conflict_result = patch_entry(
        stores,
        entry_id=entry_id,
        patch=conflicting_patch,
        actor_id="operator-alice",
        tenant_id="tenant-alpha",
        idempotency_key=patch_idempotency_1,
        request_hash=conflicting_hash,
        patched_at="2026-09-08T02:00:00Z",
    )
    assert conflict_result is not None
    assert conflict_result["status"] == "conflict"

    # Verify entry version is still 2
    refreshed = get_entry(stores, entry_id, tenant_id="tenant-alpha", actor_id="operator-alice")
    assert refreshed is not None
    assert refreshed["version"] == 2
