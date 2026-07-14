from __future__ import annotations

import os
import uuid

import pytest

from .store import ProposalConflict, ProposalStore, payload_fingerprint


def _record(proposal_id: str) -> dict:
    return {
        "proposal_id": proposal_id, "revision": 1, "tenant_id": "tenant-a",
        "owner_user_id": "user-a", "state": "draft", "proposed_value": {"risk": 0.08},
        "created_at": "2026-07-14T00:00:00Z", "updated_at": "2026-07-14T00:00:00Z",
        "audit": [],
    }


def test_postgres_two_instances_survive_restart_and_share_idempotency() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_gov_{uuid.uuid4().hex[:12]}"
    first = ProposalStore(backend="postgres", dsn=dsn, schema=schema)
    saved = first.create(_record("prop-first"), "create-1")

    restarted = ProposalStore(backend="postgres", dsn=dsn, schema=schema)
    assert restarted.get(saved["proposal_id"], "tenant-a", "user-a") == saved
    assert restarted.create(_record("prop-replay"), "create-1")["proposal_id"] == saved["proposal_id"]
    with pytest.raises(ProposalConflict):
        restarted.create({**_record("prop-conflict"), "proposed_value": {"risk": 0.01}}, "create-1")

    command = {"interaction_id": "int-shared", "status": "queued"}
    fp = payload_fingerprint(command)
    claimed = first.once("command:tenant-a:user-a", "cmd-1", fp, lambda: command)
    assert claimed.run_side_effects is True
    first.complete_side_effects("command:tenant-a:user-a", "cmd-1")
    replay = restarted.once("command:tenant-a:user-a", "cmd-1", fp, lambda: command)
    assert replay.data == command
    assert replay.replayed is True
    assert replay.run_side_effects is False

    recoverable = first.once("command:tenant-a:user-a", "cmd-release", fp, lambda: command)
    assert recoverable.run_side_effects is True
    first.release_side_effects("command:tenant-a:user-a", "cmd-release")
    reclaimed = restarted.once("command:tenant-a:user-a", "cmd-release", fp, lambda: command)
    assert reclaimed.replayed is True
    assert reclaimed.run_side_effects is True

    expired = first.once("command:tenant-a:user-a", "cmd-expired", fp, lambda: command)
    assert expired.run_side_effects is True
    with first._connect() as conn:
        conn.execute(
            f"UPDATE {first._command_table} SET lease_until=now()-interval '1 second' WHERE scope_key=%s",
            ("command:tenant-a:user-a:cmd-expired",),
        )
    reclaimed_expired = restarted.once(
        "command:tenant-a:user-a", "cmd-expired", fp, lambda: command
    )
    assert reclaimed_expired.replayed is True
    assert reclaimed_expired.run_side_effects is True
