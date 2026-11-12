from __future__ import annotations

from services.research.discovery_allocator import (
    MAX_DISCOVERY_PERSONAS,
    DiscoveryAllocator,
    read_marked_personas,
    resolve_gate_passed,
)

PROFILE = {"persona_id": "discovery-mandate", "mandate": "tw swing"}


def _scorer(profile, artifact):
    return {
        "score": artifact["score"],
        "match_id": f"m-{artifact['registry_id']}",
        "blockers": artifact.get("blockers", ()),
    }


def _arts(n, **extra):
    return [
        {"registry_id": f"a{i}", "strategy_spec_id": f"s{i}", "score": 90 - i,
         "backtest_run_id": f"bt-{i}", "symbols": ["2330", "2317"], **extra}
        for i in range(n)
    ]


def _run(*, arts, cap=1000.0, per=100.0, active=(), taken=(), min_score=50.0):
    calls = []

    def provision(req):
        calls.append(req)
        return {"persona_id": f"persona-{len(calls)}"}

    alloc = DiscoveryAllocator(
        scorer=_scorer, provision=provision, paper_capital_cap=cap,
        per_persona_capital=per, min_score=min_score,
    )
    res = alloc.allocate(
        candidates=arts, mandate_profile=PROFILE, active_slots=active,
        taken_artifacts=taken, tenant_id="t", date="2026-11-12",
    )
    return res, calls


def test_binds_at_most_five_concurrent_personas() -> None:
    res, calls = _run(arts=_arts(10))
    assert len(res.bindings) == MAX_DISCOVERY_PERSONAS == len(calls)


def test_existing_marked_slots_consume_slots_and_free_on_retire() -> None:
    active = [{"persona_id": f"x{i}", "artifact_ref": f"z{i}", "paper_capital": 10.0} for i in range(4)]
    res, _ = _run(arts=_arts(5), active=active)
    assert len(res.bindings) == 1
    # a retired persona is not an active slot (its artifact stays taken, never re-bound)
    res, _ = _run(arts=_arts(5), active=active[:3], taken=["a0"])
    assert len(res.bindings) == 2 and all(b.artifact_ref != "a0" for b in res.bindings)


def test_capital_cap_limits_bindings() -> None:
    res, _ = _run(arts=_arts(5), cap=250.0, per=100.0)
    assert len(res.bindings) == 2
    assert any(s["reason"] == "capital_cap" for s in res.skipped)


def test_request_is_persona_create_payload_with_universe_and_marker() -> None:
    res, calls = _run(arts=_arts(3))
    assert calls[0]["execution_context"] == "paper"
    assert calls[0]["strategy_artifact_ref"] == "a0"
    assert calls[0]["symbols"] == ["2330", "2317"]
    marker = calls[0]["discovery_allocator"]
    assert marker["marker"] is True and marker["match_id"] == "m-a0" and marker["match_score"] == 90
    assert marker["backtest_run_id"] == "bt-0"
    assert res.bindings[0].persona_id == "persona-1" and res.bindings[0].symbols == ("2330", "2317")


def test_missing_universe_or_run_skipped_with_reason() -> None:
    arts = [
        {"registry_id": "nou", "score": 99, "backtest_run_id": "bt", "symbols": []},
        {"registry_id": "nor", "score": 99, "symbols": ["2330"]},
        {"registry_id": "live", "score": 99, "backtest_run_id": "bt", "symbols": ["2330"], "execution_context": "live"},
    ]
    res, calls = _run(arts=arts)
    assert not calls
    assert {s["artifact_ref"]: s["reason"] for s in res.skipped} == {
        "nou": "universe_missing", "nor": "backtest_run_missing", "live": "not_paper"}


def test_blocked_and_low_score_skipped() -> None:
    arts = _arts(0) + [
        {"registry_id": "b", "score": 99, "blockers": ["x"], "backtest_run_id": "r", "symbols": ["2330"]},
        {"registry_id": "low", "score": 10, "backtest_run_id": "r", "symbols": ["2330"]},
    ]
    res, calls = _run(arts=arts)
    assert not res.bindings and not calls


def test_only_marked_personas_count() -> None:
    rows = [
        {"persona_id": "base", "tenant_id": "t", "metadata": {"strategy_family": "dev_paper_baseline"}},
        {"persona_id": "probe", "tenant_id": "t", "lifecycle_state": "paper_running", "metadata": {}},
        {"persona_id": "d1", "tenant_id": "t", "lifecycle_state": "paper_running",
         "metadata": {"discovery_allocator": {"marker": True, "strategy_artifact_ref": "a1", "paper_capital": 100}}},
        {"persona_id": "d2", "tenant_id": "t", "lifecycle_state": "retired",
         "metadata": {"discovery_allocator": {"marker": True, "strategy_artifact_ref": "a2", "paper_capital": 100}}},
        {"persona_id": "fake", "tenant_id": "t", "metadata": {"discovery_allocator": {"marker": "yes"}}},
        {"persona_id": "other", "tenant_id": "x",
         "metadata": {"discovery_allocator": {"marker": True, "strategy_artifact_ref": "a3"}}},
    ]
    slots, taken = read_marked_personas(rows, tenant_id="t")
    assert [s["persona_id"] for s in slots] == ["d1"] and sorted(taken) == ["a1", "a2"]


def _finding(spec, run, tenant="t"):
    return {"source_event_type": "research_finding_published",
            "metadata": {"event_type": "research_finding_published", "tenant_id": tenant,
                         "strategy_spec_id": spec, "metrics": {"backtest_run_id": run}}}


def test_gate_pass_requires_finding_completed_run_and_universe() -> None:
    runs = {
        "bt-ok": {"status": "completed", "artifact_checksum": "c", "dataset_symbols": ["2330"], "dataset_id": "d"},
        "bt-nouni": {"status": "completed", "artifact_checksum": "c"},
        "bt-ih": {"status": "insufficient_history", "artifact_checksum": "c", "dataset_symbols": ["2330"]},
    }
    adm = [{"strategy_spec_id": s, "checksum": "x"} for s in ("ok", "nopass", "nouni", "ih", "norun", "other")]
    findings = [_finding("ok", "bt-ok"), _finding("nouni", "bt-nouni"), _finding("ih", "bt-ih"),
                _finding("norun", "bt-gone"), _finding("other", "bt-ok", tenant="x")]
    passed, skipped = resolve_gate_passed(adm, findings, runs.get, tenant_id="t")
    assert [p["strategy_spec_id"] for p in passed] == ["ok"]
    assert passed[0]["symbols"] == ["2330"] and passed[0]["backtest_run_id"] == "bt-ok"
    assert {s["artifact_ref"]: s["reason"] for s in skipped} == {
        "nopass": "no_gate_pass", "nouni": "universe_missing", "ih": "backtest_run_not_completed",
        "norun": "backtest_run_missing", "other": "no_gate_pass"}
