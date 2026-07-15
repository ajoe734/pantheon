from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from assistant.repair_receipts import (
    RepairReceiptError,
    issue_repair_receipt,
    verify_repair_receipt,
)


REPAIR = {
    "task_id": "LOOP-PROD-MAI-001",
    "task_worktree": "/srv/pantheon-assistant/worktrees/pantheon/loop-prod-mai-001",
    "declared_scope": ["services/control-plane/bff"],
    "expected_branch": "task/LOOP-PROD-MAI-001",
    "remote": "origin",
    "merge_target": "dev",
    "repo_key": "pantheon",
    "require_clean": True,
    "require_pr": True,
}
CONTROL = {
    "active": True,
    "mode": "kernel_repair",
    "activation_id": "ctrl-test-1",
    "management_session_id": "mgmt-repair-1",
    "capabilities": ["assistant.kernel.repair"],
}
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _issue(monkeypatch) -> str:
    monkeypatch.setenv("PANTHEON_ASSISTANT_REPAIR_RECEIPT_KEY", "receipt-unit-test-secret")
    return issue_repair_receipt(
        REPAIR,
        actor_id="operator-1",
        tenant_id="tenant-1",
        control_status=CONTROL,
        now=NOW,
    )


def test_repair_receipt_round_trip_returns_signed_canonical_metadata(monkeypatch) -> None:
    token = _issue(monkeypatch)

    verified = verify_repair_receipt(
        token,
        actor_id="operator-1",
        tenant_id="tenant-1",
        control_status=CONTROL,
        supplied_repair=REPAIR,
        now=NOW + timedelta(seconds=1),
    )

    assert verified == REPAIR


@pytest.mark.parametrize(
    ("actor_id", "tenant_id", "control", "reason"),
    [
        ("operator-2", "tenant-1", CONTROL, "actor_id_mismatch"),
        ("operator-1", "tenant-2", CONTROL, "tenant_id_mismatch"),
        (
            "operator-1",
            "tenant-1",
            {**CONTROL, "activation_id": "ctrl-other"},
            "activation_id_mismatch",
        ),
        (
            "operator-1",
            "tenant-1",
            {**CONTROL, "management_session_id": "mgmt-other"},
            "management_session_id_mismatch",
        ),
    ],
)
def test_repair_receipt_rejects_binding_reuse(
    monkeypatch,
    actor_id,
    tenant_id,
    control,
    reason,
) -> None:
    token = _issue(monkeypatch)

    with pytest.raises(RepairReceiptError) as exc_info:
        verify_repair_receipt(
            token,
            actor_id=actor_id,
            tenant_id=tenant_id,
            control_status=control,
            supplied_repair=REPAIR,
            now=NOW + timedelta(seconds=1),
        )

    assert exc_info.value.reason == reason


def test_repair_receipt_rejects_tampering_and_expiry(monkeypatch) -> None:
    token = _issue(monkeypatch)
    tampered = {**REPAIR, "task_worktree": "/srv/shared/live-checkout"}

    with pytest.raises(RepairReceiptError) as mismatch:
        verify_repair_receipt(
            token,
            actor_id="operator-1",
            tenant_id="tenant-1",
            control_status=CONTROL,
            supplied_repair=tampered,
            now=NOW + timedelta(seconds=1),
        )
    assert mismatch.value.reason == "repair_metadata_mismatch"

    with pytest.raises(RepairReceiptError) as expired:
        verify_repair_receipt(
            token,
            actor_id="operator-1",
            tenant_id="tenant-1",
            control_status=CONTROL,
            supplied_repair=REPAIR,
            now=NOW + timedelta(hours=1),
        )
    assert expired.value.reason == "expired"


def test_repair_receipt_fails_closed_without_signing_key(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_ASSISTANT_REPAIR_RECEIPT_KEY", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_JWT_SECRET", raising=False)

    with pytest.raises(RepairReceiptError) as exc_info:
        issue_repair_receipt(
            REPAIR,
            actor_id="operator-1",
            tenant_id="tenant-1",
            control_status=CONTROL,
            now=NOW,
        )

    assert exc_info.value.reason == "receipt_key_unconfigured"
