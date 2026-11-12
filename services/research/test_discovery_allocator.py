from __future__ import annotations

import pytest

from services.research.discovery_allocator import (
    MAX_DISCOVERY_PERSONAS,
    DiscoveryAllocator,
    allocator_enabled,
)


@pytest.mark.parametrize("value", [None, "", " ", "false", "0", "1", "yes", "on", "invalid"])
def test_allocator_disabled_without_explicit_true(value) -> None:
    env = {} if value is None else {"DISCOVERY_ALLOCATOR_ENABLED": value}
    assert allocator_enabled(env) is False


@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_allocator_enabled_with_explicit_true(value) -> None:
    assert allocator_enabled({"DISCOVERY_ALLOCATOR_ENABLED": value}) is True


def _scorer(persona, artifact):
    return {
        "score": artifact["score"],
        "match_id": f"m-{persona['persona_id']}-{artifact['registry_id']}",
        "blockers": artifact.get("blockers", ()),
    }


def _arts(n, **extra):
    return [
        {"registry_id": f"a{i}", "score": 90 - i, **extra} for i in range(n)
    ]


def _personas(n):
    return [{"persona_id": f"p{i}"} for i in range(n)]


def _run(*, arts, personas, cap=1000.0, per=100.0, active=(), min_score=50.0):
    calls = []
    alloc = DiscoveryAllocator(
        scorer=_scorer,
        provision=calls.append,
        paper_capital_cap=cap,
        per_persona_capital=per,
        min_score=min_score,
    )
    res = alloc.allocate(
        admitted_artifacts=arts,
        discovery_personas=personas,
        active_bindings=active,
        tenant_id="t",
        date="2026-11-10",
    )
    return res, calls


def test_binds_at_most_five_concurrent_personas() -> None:
    res, calls = _run(arts=_arts(10), personas=_personas(10))
    assert len(res.bindings) == MAX_DISCOVERY_PERSONAS == len(calls)


def test_existing_bindings_consume_slots() -> None:
    active = [{"persona_id": f"x{i}", "artifact_ref": f"z{i}", "paper_capital": 10.0} for i in range(4)]
    res, _ = _run(arts=_arts(5), personas=_personas(5), active=active)
    assert len(res.bindings) == 1


def test_capital_cap_limits_bindings() -> None:
    res, _ = _run(arts=_arts(5), personas=_personas(5), cap=250.0, per=100.0)
    assert len(res.bindings) == 2
    assert any(s["reason"] == "capital_cap" for s in res.skipped)


def test_paper_only_and_request_is_paper() -> None:
    arts = _arts(2) + [{"registry_id": "live", "score": 99, "execution_context": "live"}]
    res, calls = _run(arts=arts, personas=_personas(3))
    assert all(c["execution_context"] == "paper" for c in calls)
    assert all(b.artifact_ref != "live" for b in res.bindings)


def test_selection_by_score_and_score_recorded() -> None:
    res, calls = _run(arts=_arts(3), personas=_personas(1))
    assert [b.artifact_ref for b in res.bindings] == ["a0"]
    assert res.bindings[0].score == 90
    assert calls[0]["metadata"]["discovery_match_score"] == 90
    assert calls[0]["metadata"]["discovery_match_id"] == "m-p0-a0"
    assert calls[0]["strategy_artifact_ref"] == "a0"


def test_blocked_and_low_score_skipped() -> None:
    arts = [
        {"registry_id": "b", "score": 99, "blockers": ["x"]},
        {"registry_id": "low", "score": 10},
    ]
    res, calls = _run(arts=arts, personas=_personas(2))
    assert not res.bindings and not calls
