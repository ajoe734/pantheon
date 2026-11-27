"""Tests for bounded pool member refusals in PersonaStrategyPoolStore (PAPER-TRADER-BOOK-ENROLLMENT-SKIP-20261127 AC3)."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

_PERSONA_DIR = str(Path(__file__).resolve().parents[1])
if _PERSONA_DIR not in sys.path:
    sys.path.insert(0, _PERSONA_DIR)

from services.control_plane.persona.persona_strategy_pool import (  # noqa: E402
    AppendPoolVersionRequest,
    PersonaStrategyPoolStore,
    PoolMember,
    PoolMemberRefused,
)
from services.foundation.reliable_delivery import AtomicJsonRecordStore  # noqa: E402


CHECKSUM = "sha256:" + "a" * 64


def _member(ref: str) -> PoolMember:
    return PoolMember(
        artifact_ref=ref,
        checksum=CHECKSUM,
        stage="active",
        risk_weight=1.0,
        baseline_ref="gate-1",
        joined_at="2026-11-27T00:00:00Z",
    )


def test_144_identical_strategy_pool_refusals_collapse_to_one_entry_with_count_144(
    tmp_path: Path,
) -> None:
    timestamps = iter([f"2026-11-27T10:{i // 60:02d}:{i % 60:02d}Z" for i in range(200)])
    store = PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda _ref, _tenant: {},
        now=lambda: next(timestamps),
    )
    request = AppendPoolVersionRequest(
        expected_version=0,
        members=[_member("missing-artifact")],
        change_reason="daily retry",
    )

    for _ in range(144):
        with pytest.raises(PoolMemberRefused):
            store.append("persona-1", "tenant-a", request, actor_id="persona-pool-cycle")

    refusals = store.refusals("persona-1", "tenant-a")
    assert len(refusals) == 1
    entry = refusals[0]
    assert entry["count"] == 144
    assert entry["refused_at"] == "2026-11-27T10:00:00Z"
    assert entry["last_at"] == "2026-11-27T10:02:23Z"
    assert entry["step"] == "member"
    assert entry["artifact_ref"] == "missing-artifact"
    assert "not a registry entry" in entry["reason"]


def test_strategy_pool_refusals_retain_at_most_20_distinct_entries(
    tmp_path: Path,
) -> None:
    tick = 0

    def _clock() -> str:
        nonlocal tick
        tick += 1
        return f"2026-11-27T11:00:{tick:02d}Z"

    store = PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda _ref, _tenant: {},
        now=_clock,
    )

    for idx in range(28):
        req = AppendPoolVersionRequest(
            expected_version=0,
            members=[_member(f"missing-{idx}")],
            change_reason=f"attempt-{idx}",
        )
        with pytest.raises(PoolMemberRefused):
            store.append("persona-1", "tenant-a", req, actor_id="operator-1")

    refusals = store.refusals("persona-1", "tenant-a")
    assert len(refusals) == PersonaStrategyPoolStore.MAX_REFUSALS == 20
    assert [r["artifact_ref"] for r in refusals] == [f"missing-{idx}" for idx in range(8, 28)]
    assert all(r["count"] == 1 for r in refusals)
