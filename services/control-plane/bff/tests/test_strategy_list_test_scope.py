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
