"""
Tests for Agora Decision Journal create, merge-patch, diff calculation, and idempotency protection.
"""
from __future__ import annotations

import uuid
import pytest

from services.agora.store import AgoraStore, DictRecord


def test_decision_journal_create_and_patch_lifecycle() -> None:
    store = AgoraStore()
    entry_id = f"dj-{uuid.uuid4().hex[:8]}"

    # 1. Create Decision Journal Entry
    created = store.create_journal_entry(
        entry_id=entry_id,
        title="Initial Allocation Policy",
        decision="allocate_paper_50k",
        actor_id="operator-alice",
        payload={
            "category": "capital",
            "tags": ["paper", "allocation"],
            "visibility": "team",
            "contextRefs": [{"type": "persona", "id": "persona-a"}],
        },
    )
    assert created.entryId == entry_id
    assert created.title == "Initial Allocation Policy"
    assert created.decision == "allocate_paper_50k"
    assert created.version == 1

    # 2. Patch Entry with Merge Patch
    patch_idempotency_1 = f"idem-patch-{uuid.uuid4().hex[:8]}"
    patch_result = store.patch_journal_entry(
        entry_id=entry_id,
        patch={"title": "Updated Allocation Policy v2", "decision": "allocate_paper_100k"},
        actor_id="operator-alice",
        idempotency_key=patch_idempotency_1,
        correlation_id="corr-001",
    )
    assert patch_result is not None
    assert patch_result["status"] == "updated"
    assert patch_result["entry"]["version"] == 2
    assert patch_result["entry"]["title"] == "Updated Allocation Policy v2"
    assert patch_result["entry"]["decision"] == "allocate_paper_100k"
    assert patch_result["audit"]["diff"]["changedFields"] == ["title", "decision"]
    assert patch_result["audit"]["diff"]["oldValues"] == {
        "title": "Initial Allocation Policy",
        "decision": "allocate_paper_50k",
    }
    assert patch_result["audit"]["diff"]["newValues"] == {
        "title": "Updated Allocation Policy v2",
        "decision": "allocate_paper_100k",
    }

    # 3. Replay exact same patch with same idempotency key -> status "replayed"
    replay_result = store.patch_journal_entry(
        entry_id=entry_id,
        patch={"title": "Updated Allocation Policy v2", "decision": "allocate_paper_100k"},
        actor_id="operator-alice",
        idempotency_key=patch_idempotency_1,
        correlation_id="corr-001",
    )
    assert replay_result is not None
    assert replay_result["status"] == "replayed"
    assert replay_result["entry"]["version"] == 2

    # 4. Conflict: same idempotency key with different payload -> status "conflict"
    conflict_result = store.patch_journal_entry(
        entry_id=entry_id,
        patch={"title": "Conflicting Title Mutation"},
        actor_id="operator-alice",
        idempotency_key=patch_idempotency_1,
    )
    assert conflict_result is not None
    assert conflict_result["status"] == "conflict"

    # Verify entry version is still 2
    refreshed = store.get_journal_entry(entry_id)
    assert refreshed is not None
    assert refreshed.version == 2
