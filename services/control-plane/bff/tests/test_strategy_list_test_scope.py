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
    assert body["meta"]["hidden_persona_strategy_count"] == 2
    assert body["meta"]["persona_exclusion_scope"] == ["test"]
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
    assert body["meta"]["hidden_persona_strategy_count"] == 0
    assert body["meta"]["persona_exclusion_scope"] == []


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


# --- REGISTRY-STRATEGY-LIST-OWNER-HIDE-20261125: hosted-shape owner hide ------

_HOSTED_REAL_DRAFTS = 4205
_HOSTED_RAW_SEED_DRAFTS = 1500
_HOSTED_SIMULATION_DRAFTS = 100
_HOSTED_TEST_PERSONA_DRAFTS = 1200
_HOSTED_DATA_SCOPE_DRAFTS = 416
_HOSTED_HIDDEN_DRAFTS = (
    _HOSTED_RAW_SEED_DRAFTS
    + _HOSTED_SIMULATION_DRAFTS
    + _HOSTED_TEST_PERSONA_DRAFTS
    + _HOSTED_DATA_SCOPE_DRAFTS
)  # 3216 hidden drafts
_HOSTED_DRAFTS = _HOSTED_REAL_DRAFTS + _HOSTED_HIDDEN_DRAFTS  # 7421 = 38 owner pages of 200


def _hosted_shape_world() -> _World:
    """The 141-row world plus 7421 draft families in the hosted proportions:
    4205 visible and 3216 hidden (raw seeds, simulations, test-persona rows and
    rows whose data_scope is test)."""
    import dataclasses

    from services.control_plane.bff.tests.test_strategy_operational_status import (
        _persona_record,
        _spec_entry,
    )
    from services.registry.models import ArtifactState

    world = _World()
    world.persona_records["persona-hosted-real"] = _persona_record(
        "persona-hosted-real", lifecycle_state="paper_running"
    )
    world.persona_records["persona-hosted-test"] = {
        **_persona_record("persona-hosted-test", lifecycle_state="paper_running"),
        "metadata": {"data_scope": "test"},
    }

    def _draft(sid: str, title: str, persona_ids: list, n: int, data_scope: str = "") -> None:
        entry = _spec_entry(
            strategy_id=sid,
            title=title,
            persona_ids=persona_ids,
            updated_at=f"2026-10-{1 + (n % 28):02d}T{(n // 28) % 24:02d}:{n % 60:02d}:00Z",
        )
        entry.metadata["strategy_spec"]["lifecycle_state"] = "draft"
        if data_scope:
            entry.metadata["data_scope"] = data_scope
        world.entries.append(dataclasses.replace(entry, artifact_state=ArtifactState.DRAFT))

    n = 0
    for _ in range(_HOSTED_REAL_DRAFTS):
        _draft(f"strat-hosted-real-{n:05d}", f"Real draft {n}", ["persona-hosted-real"], n)
        n += 1
    for i in range(_HOSTED_RAW_SEED_DRAFTS):
        _draft(f"strat-tw-official:{i:05d}", f"TW official seed {i}", [], n)
        n += 1
    for i in range(_HOSTED_SIMULATION_DRAFTS):
        _draft(f"strat-sim-{i:05d}", f"SIMULATION fixture {i}", [], n)
        n += 1
    for i in range(_HOSTED_TEST_PERSONA_DRAFTS):
        _draft(f"strat-testpersona-{i:05d}", f"Probe {i}", ["persona-hosted-test"], n)
        n += 1
    for i in range(_HOSTED_DATA_SCOPE_DRAFTS):
        _draft(f"strat-scope-{i:05d}", f"Scoped {i}", [], n, data_scope="test")
        n += 1
    return world


def test_hosted_shape_every_filter_is_one_bounded_owner_read_with_exact_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _hosted_shape_world()
    client = _client(monkeypatch, world)
    base_non_retired = TOTAL_COUNT  # the 141 base rows are all non-retired specs

    cases = {
        "page_size=20": base_non_retired + _HOSTED_REAL_DRAFTS,
        "state=draft&page_size=20": _HOSTED_REAL_DRAFTS,
        "exclude_state=draft&page_size=20": base_non_retired,
        "persona_id=persona-hosted-real&page_size=20": _HOSTED_REAL_DRAFTS,
        "state=approved&page_size=20": base_non_retired,
    }
    for query, expected_total in cases.items():
        world.owner_summary_calls.clear()
        resp = client.get(f"/bff/strategies?{query}", headers=HEADERS)
        assert resp.status_code == 200, (query, resp.text)
        body = resp.json()
        calls = world.owner_summary_calls
        # One page read; the only possible second read is the operational-count
        # candidate set, which exists only when it fits one owner page. Never a walk.
        assert 1 <= len(calls) <= 2, (query, calls)
        assert all("hide_stubs=true" in c[0] for c in calls), (query, calls)
        assert sum(1 for c in calls if "page_size=200" in c[0]) <= 1, (query, calls)
        assert body["page_info"]["total"] == expected_total, query
        assert body["meta"]["total"] == expected_total, query
        meta = body["meta"]
        # Hidden counts describe the filtered candidate set: only the draft
        # views and the unfiltered view contain hidden rows.
        hidden_in_candidates = query.startswith(("page_size=", "state=draft"))
        assert meta["state_counts"]["draft"] == _HOSTED_DRAFTS  # keeps its present meaning
        vsc = meta["visible_state_counts"]
        assert vsc["draft"] == _HOSTED_REAL_DRAFTS
        if query.startswith("state=draft") or query.startswith("persona_id="):
            assert body["page_info"]["total"] == vsc["draft"], query
        if query.startswith("exclude_state=draft") or query.startswith("state=approved"):
            assert body["page_info"]["total"] == vsc["approved"], query
        if query.startswith("page_size="):
            assert body["page_info"]["total"] == sum(
                v for k, v in vsc.items() if k != "retired"
            )
        assert meta["hidden_test_persona_strategy_count"] == (
            _HOSTED_TEST_PERSONA_DRAFTS + _HOSTED_DATA_SCOPE_DRAFTS
            if hidden_in_candidates
            else 0
        ), query
        assert meta["hidden_unbound_stub_strategy_count"] == (
            _HOSTED_RAW_SEED_DRAFTS + _HOSTED_SIMULATION_DRAFTS
            if hidden_in_candidates
            else 0
        ), query
        assert len(body["items"]) == 20


def test_hosted_shape_owner_without_hide_support_falls_back_without_zero_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An owner that does not answer the hide input gives no visible counts:
    ``meta.visible_state_counts`` is null (never zeros) and the list is still
    classified exactly by the BFF."""
    from services.control_plane.bff.command_adapters import base as adapter_base

    world = _World()
    app, _ = _build_app(monkeypatch, world)
    owner_http = adapter_base.http_request_json

    def _legacy_owner(url: str, **kwargs: Any) -> Any:
        out = owner_http(url, **kwargs)
        if isinstance(out, dict):
            out = {k: v for k, v in out.items() if k not in ("visible_state_counts", "hidden")}
        return out

    monkeypatch.setattr(adapter_base, "http_request_json", _legacy_owner)
    body = TestClient(app).get("/bff/strategies?page_size=20", headers=HEADERS).json()
    assert body["meta"]["visible_state_counts"] is None
    assert body["page_info"]["total"] == TOTAL_COUNT


def test_include_test_personas_keeps_every_row_and_counts_them_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _hosted_shape_world()
    client = _client(monkeypatch, world)
    world.owner_summary_calls.clear()
    body = client.get(
        "/bff/strategies?state=draft&page_size=20&include_test_personas=true", headers=HEADERS
    ).json()
    assert body["page_info"]["total"] == _HOSTED_DRAFTS
    assert all("hide_stubs" not in c[0] for c in world.owner_summary_calls)
    assert body["meta"]["visible_state_counts"] == body["meta"]["state_counts"]
    assert body["meta"]["hidden_test_persona_strategy_count"] == 0


def test_hosted_shape_hide_retired_total_and_stub_counts_are_exact_not_approximate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounded hide-retired read no longer estimates the stub total from the
    pages it happened to read: the owner excludes and counts the stubs over the
    whole candidate set."""
    world = _hosted_shape_world()
    client = _client(monkeypatch, world)
    world.owner_summary_calls.clear()
    resp = client.get("/bff/strategies?hide_retired_personas=true&page_size=20", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    retired_personas = len(world.retired_strategy_ids)
    expected_total = TOTAL_COUNT + _HOSTED_REAL_DRAFTS - retired_personas
    assert body["page_info"]["total"] == expected_total
    meta = body["meta"]
    assert meta["hidden_retired_persona_count"] == retired_personas
    assert meta["hidden_test_persona_strategy_count"] == _HOSTED_TEST_PERSONA_DRAFTS + _HOSTED_DATA_SCOPE_DRAFTS
    assert meta["hidden_unbound_stub_strategy_count"] == _HOSTED_RAW_SEED_DRAFTS + _HOSTED_SIMULATION_DRAFTS
    assert meta["visible_state_counts"]["draft"] == _HOSTED_REAL_DRAFTS
    assert all("hide_stubs=true" in c[0] for c in world.owner_summary_calls)
    # first page of 200 + the draft-excluded reconciliation read; never 38 pages
    assert len(world.owner_summary_calls) <= 3, world.owner_summary_calls
