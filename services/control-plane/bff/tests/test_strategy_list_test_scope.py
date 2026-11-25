"""BFF-STRATEGY-ATTRIBUTION-HIDE-TEST-PERSONAS-20261120 (RC6 WP6.4).

Reproduced on the coordinator hosted reads 2026-11-20 (BFF fb8ed951d, sweep in
mgmt-readability-sweep-20261120): the default strategy list (drafts hidden)
started with "Pantheon Dev Paper Release 43688794313 Attempt 1" whose persona
carries metadata.data_scope test, and performance attribution listed it as a
persona. Both surfaces must hide test-scoped personas by default, say how many
were hidden, and return them on request.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.performance.service import (
    pm12_attribution_test_scope_hiding,
)
from services.control_plane.bff.tests.test_strategy_operational_status import (
    HEADERS,
    TOTAL_COUNT,
    _World,
    _build_app,
    _permissive_auth,  # noqa: F401 - autouse fixture
)


def _mark_test_scoped(world: _World, strategy_ids: list) -> list:
    marked = []
    for entry in world.entries:
        if entry.strategy_id in strategy_ids:
            for pid in entry.metadata["persona_ids"]:
                world.persona_records[pid]["metadata"] = {"data_scope": "test"}
                marked.append(pid)
    return marked


def _client(monkeypatch: pytest.MonkeyPatch, world: _World) -> TestClient:
    app, _ = _build_app(monkeypatch, world)
    return TestClient(app)


def test_default_list_hides_test_scoped_strategies_and_counts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    hidden_ids = world.running_strategy_ids[:2]
    _mark_test_scoped(world, hidden_ids)
    client = _client(monkeypatch, world)

    body = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=HEADERS).json()
    ids = {row["id"] for row in body["items"]}
    assert not ids & set(hidden_ids)
    assert body["page_info"]["total"] == TOTAL_COUNT - 2
    assert body["meta"]["hidden_test_persona_strategy_count"] == 2
    assert body["meta"]["include_test_personas"] is False


def test_include_test_personas_returns_them(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World()
    hidden_ids = world.running_strategy_ids[:2]
    _mark_test_scoped(world, hidden_ids)
    client = _client(monkeypatch, world)

    body = client.get(
        "/bff/strategies?exclude_state=draft&page_size=200&include_test_personas=true",
        headers=HEADERS,
    ).json()
    ids = {row["id"] for row in body["items"]}
    assert set(hidden_ids) <= ids
    assert body["page_info"]["total"] == TOTAL_COUNT
    assert body["meta"]["hidden_test_persona_strategy_count"] == 0


def test_no_test_personas_means_nothing_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World()
    client = _client(monkeypatch, world)
    body = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=HEADERS).json()
    assert body["page_info"]["total"] == TOTAL_COUNT
    assert body["meta"]["hidden_test_persona_strategy_count"] == 0


def test_hiding_composes_with_hide_retired_and_reads_directory_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    _mark_test_scoped(world, world.running_strategy_ids[:1])
    client = _client(monkeypatch, world)

    for query in (
        "exclude_state=draft&page_size=20",
        "exclude_state=draft&page_size=20&hide_retired_personas=true",
        "page_size=200&hide_retired_personas=true",
    ):
        world.directory_reads.clear()
        resp = client.get(f"/bff/strategies?{query}", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        assert world.directory_reads == ["tenant-dev"], query
        assert resp.json()["meta"]["hidden_test_persona_strategy_count"] == 1


def test_mixed_persona_strategy_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World()
    sid = world.running_strategy_ids[0]
    entry = next(e for e in world.entries if e.strategy_id == sid)
    regular_pid = "persona-regular-extra"
    world.persona_records[regular_pid] = {
        "persona_id": regular_pid, "tenant_id": "tenant-dev",
        "lifecycle_state": "paper_running", "status": "active",
    }
    entry.metadata["persona_ids"].append(regular_pid)
    entry.metadata["strategy_spec"]["persona_ids"].append(regular_pid)
    _mark_test_scoped(world, [sid])
    # _mark_test_scoped marks every persona of the strategy; keep one regular
    world.persona_records[regular_pid].pop("metadata", None)
    client = _client(monkeypatch, world)
    body = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=HEADERS).json()
    assert sid in {row["id"] for row in body["items"]}
    assert body["meta"]["hidden_test_persona_strategy_count"] == 0


# --- performance attribution ------------------------------------------------


def test_attribution_hiding_helper_counts_and_keeps_regular() -> None:
    personas: Dict[str, Any] = {
        "p-test": {"persona_id": "p-test", "metadata": {"data_scope": "test"}},
        "p-reg": {"persona_id": "p-reg", "metadata": {"data_scope": "regular"}},
    }
    facts = [
        {"persona_id": "p-test", "strategy_id": "s-only-test"},
        {"persona_id": "p-test", "strategy_id": "s-mixed"},
        {"persona_id": "p-reg", "strategy_id": "s-mixed"},
        {"persona_id": "p-reg", "strategy_id": "s-reg"},
    ]
    kept, hidden_personas, hidden_strategies = pm12_attribution_test_scope_hiding(facts, personas)
    assert [f["persona_id"] for f in kept] == ["p-reg", "p-reg"]
    assert hidden_personas == 1
    assert hidden_strategies == 1  # s-mixed still has a regular persona


def test_attribution_hiding_helper_noop_without_test_personas() -> None:
    facts = [{"persona_id": "p", "strategy_id": "s"}]
    kept, hp, hs = pm12_attribution_test_scope_hiding(
        facts, {"p": {"persona_id": "p", "metadata": {"data_scope": "regular"}}}
    )
    assert kept is facts and (hp, hs) == (0, 0)


# --- BFF-STRATEGIES-DEFAULT-PAGE-BOUNDED-READ-20261125: 7,500-row scale ---------

def _scale_service(draft_count: int = 7329, non_draft_count: int = 171):
    """Bare StrategiesService over a fake owner registry (7,500 rows)."""
    from concurrent.futures import ThreadPoolExecutor

    from services.control_plane.bff.strategies.routes.common import (
        PersonaStateLookup,
        STUB_TEST_PERSONA_PROBE,
        operational_counts,
        strategy_stub_reason,
        summary_persona_ids,
    )
    from services.control_plane.bff.strategies.service import StrategiesService

    rows = []
    for i in range(non_draft_count + draft_count):
        non_draft = i % 44 == 0 and (i // 44) < non_draft_count
        row = {
            "strategy_id": f"s-{i:05d}",
            "lifecycle_state": "approved" if non_draft else "draft",
            "persona_ids": [f"p-{i:05d}"] if non_draft else [],
        }
        rows.append(row)
    # first two rows are test-probe strategies bound to a test persona
    rows[0]["persona_ids"] = ["p-test"]
    rows[1]["persona_ids"] = ["p-test"]
    calls: list = []

    class _Store:
        def list_strategy_summaries_page(self, *, state=None, persona_id=None, page_token=None, page_size=20, exclude_state=None):
            calls.append((exclude_state, page_token, page_size))
            pool = [r for r in rows if exclude_state != "draft" or r["lifecycle_state"] != "draft"]
            start = int(page_token or 0)
            chunk = pool[start : start + page_size]
            nxt = start + len(chunk)
            return {
                "items": chunk,
                "total": len(pool),
                "next_page_token": str(nxt) if nxt < len(pool) else None,
                "state_counts": {"draft": draft_count, "approved": non_draft_count},
            }

    svc = object.__new__(StrategiesService)
    svc._read_surface = _Store()
    svc._get_read_store = None
    svc._bff_error = None
    svc._invoke_read_owner = lambda fn: fn()
    lookup = PersonaStateLookup(
        available=True,
        states_by_id={r["persona_ids"][0]: "active" for r in rows if r["persona_ids"] and r["persona_ids"][0] != "p-test"} | {"p-test": "active"},
        test_persona_ids=frozenset({"p-test"}),
    )
    executor = ThreadPoolExecutor(max_workers=2)

    def _overlap(fn):
        fut = executor.submit(fn)
        return fut.result

    kwargs = dict(
        persona_lookup=lookup,
        summary_persona_ids_fn=summary_persona_ids,
        operational_counts_fn=operational_counts,
        status_retired="retired",
        page_slice_fn=lambda items, token, size: (
            items[int(token or 0) : int(token or 0) + size],
            None,
        ),
        stub_reason_fn=lambda row: strategy_stub_reason(row, lookup),
        test_probe_reason=STUB_TEST_PERSONA_PROBE,
        submit_overlap_fn=_overlap,
    )
    return svc, kwargs, calls, rows


def test_scale_7500_default_first_page_uses_at_most_two_owner_calls() -> None:
    svc, kwargs, calls, _rows = _scale_service()
    result = svc.list_strategy_summaries_hide_retired_unfiltered_page(
        page_size=20, hide_retired=False, **kwargs
    )
    assert len(calls) <= 2, calls
    assert calls and any(c[0] == "draft" for c in calls)
    assert result["hidden_test_persona_strategy_count"] == 2
    assert result["total"] == 7500 - 2
    assert len(result["page_summaries"]) == 20
    assert all("p-test" not in r["persona_ids"] for r in result["page_summaries"])
    assert result["next_page_token"] == "20"
    assert result["state_counts"]["draft"] == 7329


def test_scale_7500_hide_retired_true_and_cursor_advance_without_duplicates() -> None:
    svc, kwargs, calls, _rows = _scale_service()
    seen: list = []
    token = None
    for _ in range(3):
        calls.clear()
        result = svc.list_strategy_summaries_hide_retired_unfiltered_page(
            page_token=token, page_size=20, hide_retired=True, **kwargs
        )
        assert len(calls) <= 3, calls  # bounded, never the 38-page walk
        seen.extend(r["strategy_id"] for r in result["page_summaries"])
        token = result["next_page_token"]
        assert token is not None
    assert len(seen) == len(set(seen)) == 60
    assert seen == sorted(seen)
    assert "s-00000" not in seen and "s-00001" not in seen


def test_scale_prefetched_first_page_is_reused_and_unavailable_persona_never_hides_rows() -> None:
    from dataclasses import replace

    svc, kwargs, calls, rows = _scale_service()
    prefetched = svc.list_strategy_summaries_page(page_size=20)
    calls.clear()
    result = svc.list_strategy_summaries_hide_retired_unfiltered_page(
        page_size=20, hide_retired=False, first_page_fn=lambda: prefetched, **kwargs
    )
    # two test probes sit in the prefetched 20 rows, so one 200-row continuation
    # page overlaps the non-draft read: 2 owner calls beyond the prefetched page
    assert sorted(str(c[0]) for c in calls) == ["None", "draft"]
    assert len(result["page_summaries"]) == 20
    assert result["hidden_test_persona_strategy_count"] == 2

    lookup = kwargs["persona_lookup"]
    unavailable = replace(lookup, available=False, failure="down")
    kwargs["persona_lookup"] = unavailable
    kwargs["stub_reason_fn"] = lambda row: None
    calls.clear()
    result = svc.list_strategy_summaries_hide_retired_unfiltered_page(
        page_size=20, hide_retired=True, **kwargs
    )
    assert result["hidden_retired_persona_count"] == 0
    assert result["total"] == 7500
