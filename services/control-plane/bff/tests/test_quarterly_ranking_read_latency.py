"""BFF-QUARTERLY-RANKING-READ-LATENCY-20261114.

Quarterly ranking, recommendations and drilldown read the complete owner
evidence (no truncation, no ordering assumption) with owner pages fetched
concurrently so a slow owner is waited on about once, ranking output unchanged.
"""
from __future__ import annotations

import copy
import tempfile
import time
from typing import Any, Dict, List, Tuple

import pytest

from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.tests.test_bff_pm12_persona_league import (
    HEADERS,
    _CountingPm12LeagueTestStore,
    _fresh_client,
)

PERSONA_COUNT = 166
ROUTES = (
    "/bff/management/quarterly-ranking",
    "/bff/management/quarterly-ranking/recommendations",
    "/bff/management/quarterly-ranking/drilldown",
)


class _Owner:
    """Fake source-ingest owner: append-order pages, call counting, optional
    latency / unavailability."""

    def __init__(self, total: int, *, unavailable: bool = False, delay: float = 0.0):
        self.total = total
        self.unavailable = unavailable
        self.delay = delay
        self.calls: List[str] = []

    def http_get(self, base_url: str, path: str) -> Tuple[bool, Any]:
        self.calls.append(path)
        if self.delay:
            time.sleep(self.delay)
        if self.unavailable:
            return False, None
        query = dict(p.split("=") for p in path.split("?", 1)[1].split("&"))
        size, start = int(query["page_size"]), int(query["page_token"])
        items = [
            {
                "evidence_item_id": f"ev-{i}",
                "item_type": "filing",
                "citation_label": f"ev-{i}",
                "available_time": "2026-02-15T00:00:00Z",
                "metadata": {},
            }
            for i in range(start, min(start + size, self.total))
        ]
        return True, {"items": items, "total": self.total, "summary": None}


class _OwnerBackedStore(_CountingPm12LeagueTestStore):
    def __init__(self, owner: _Owner, persona_count: int = PERSONA_COUNT):
        super().__init__(fallback=True)
        self._port = DefaultResearchKnowledgeSourcePort(
            source_ingest_service_url="http://owner.invalid",
            http_get_fn=owner.http_get,
        )
        for n in range(persona_count):
            self.create_persona(
                persona_id=f"p-{n:03d}",
                name=f"Persona {n:03d}",
                archetype=("trend", "mean_reversion", "carry")[n % 3],
            )

    def read_evidence_refs(self, **kwargs: Any):
        return self._port.read_evidence_refs(**kwargs)


def _get(owner: _Owner, route: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, store=_OwnerBackedStore(owner))
        params = {"quarter": "2026-Q1", "page_size": 200}
        if route.endswith("drilldown"):
            params["persona_id"] = "p-001"
        response = client.get(route, headers=HEADERS, params=params)
        assert response.status_code in (200, 404), response.text
        return {"status": response.status_code, "body": response.json()}


@pytest.mark.parametrize("route", ROUTES)
def test_owner_requests_are_complete_pages_only(route: str) -> None:
    # Exactness requires every owner row (the owner has no time filter), so
    # requests are exactly ceil(total / page) -- never a truncated read.
    for total, expected in ((50, 1), (1_600, 4), (5_000, 10)):
        owner = _Owner(total)
        _get(owner, route)
        assert len(owner.calls) == expected, (total, owner.calls)


@pytest.mark.parametrize("route", ROUTES)
def test_unavailable_owner_is_paid_once_and_marks_surface(route: str) -> None:
    owner = _Owner(10_000, unavailable=True, delay=0.05)
    result = _get(owner, route)
    assert len(owner.calls) == 1
    if route.endswith("quarterly-ranking") and result["status"] == 200:
        surfaces = (result["body"].get("meta") or {}).get("surfaces") or result["body"].get("surfaces") or {}
        assert "unavailable" in repr(surfaces.get("evidence_refs", surfaces))


def test_slow_available_owner_wait_is_paid_once_not_per_page() -> None:
    owner = _Owner(3_000, delay=0.2)
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
    )
    started = time.monotonic()
    rows = personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs)
    elapsed = time.monotonic() - started
    assert rows is not None and len(rows) == 3_000
    assert len(owner.calls) == 6
    # first page sequential + the other five concurrently = 2 waits, not 6
    assert elapsed < 0.2 * 3, elapsed


def test_slow_owner_over_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_EVIDENCE_OWNER_READ_TIMEOUT_SECONDS", "0.1")
    owner = _Owner(3_000, delay=0.3)
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
    )
    assert personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs) is None


def test_rows_match_unpaged_read_order_and_are_not_truncated() -> None:
    owner = _Owner(3_100)
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
    )
    rows = personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs)
    legacy = port.read_evidence_refs().items  # unpaged full read, newest-first
    assert len(rows) == 3_100
    assert [r["ref_id"] for r in rows] == [r["ref_id"] for r in legacy]


_VOLATILE_KEYS = {"snapshot_at", "generated_at", "request_id", "as_of", "observed_time", "last_known_at", "correlation_id"}


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items()
            if k not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


@pytest.mark.parametrize("route", ROUTES)
def test_output_identical_to_unbounded_stream_for_166_personas(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Evidence fits one owner page, so the pre-change full stream (page_size
    # unset) and the bounded read must produce the same ranking payload.
    bounded_owner = _Owner(300)
    bounded = _get(bounded_owner, route)

    monkeypatch.setattr(personas_service, "_PM12_QUARTER_EVIDENCE_PAGE_SIZE", None)
    streamed_owner = _Owner(300)
    streamed = _get(streamed_owner, route)

    assert bounded["status"] == streamed["status"]
    assert _strip_volatile(copy.deepcopy(bounded["body"])) == _strip_volatile(
        copy.deepcopy(streamed["body"])
    )
    if route.endswith("/quarterly-ranking") and bounded["status"] == 200:
        assert len(bounded["body"]["data"]["items"]) >= PERSONA_COUNT


def _diff(a: Any, b: Any, path: str = "") -> List[str]:
    if type(a) is not type(b):
        return [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        out: List[str] = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}")
        return out
    if isinstance(a, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _linked_refs(prefix: str, count: int, captured_at: str) -> List[Dict[str, Any]]:
    return [
        {
            "ref_id": f"{prefix}-{i:04d}",
            "display_label": f"{prefix}-{i:04d}",
            "evidence_type": "artifact",
            "source_document": {"captured_at": captured_at, "source_type": "internal"},
            "linked_object_summary": {"entity_type": "persona", "entity_ref": "p-001"},
            "credibility": {"tier": "verified", "verified": True},
        }
        for i in range(count)
    ]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("newer_than_quarter", (0, 700))
def test_linked_evidence_beyond_first_page_keeps_full_output(
    route: str, newer_than_quarter: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """>500 linked in-quarter refs, some behind a full page of newer
    post-quarter refs: bounded paging must equal the unbounded stream."""
    refs = _linked_refs("q", 501, "2026-02-15T00:00:00Z") + _linked_refs(
        "newer", newer_than_quarter, "2026-06-01T00:00:00Z"
    )
    original_init = _OwnerBackedStore.__init__

    def seeded_init(self: Any, owner: _Owner, persona_count: int = PERSONA_COUNT) -> None:
        original_init(self, owner, persona_count)
        self._port._evidence_refs = {r["ref_id"]: r for r in refs}

    monkeypatch.setattr(_OwnerBackedStore, "__init__", seeded_init)
    bounded = _get(_Owner(0), route)
    monkeypatch.setattr(personas_service, "_PM12_QUARTER_EVIDENCE_PAGE_SIZE", None)
    full = _get(_Owner(0), route)

    assert bounded["status"] == full["status"] == 200
    assert _strip_volatile(copy.deepcopy(bounded["body"])) == _strip_volatile(
        copy.deepcopy(full["body"])
    )
    body = bounded["body"]["data"]
    if "evidence_refs" in body:
        assert len(body["evidence_refs"]) == 501


def _snapshot_surface(body: Dict[str, Any]) -> Dict[str, Any]:
    data = body.get("data") or {}
    return {
        k: data.get(k)
        for k in ("items", "ranking_snapshot_id", "evidence_refs", "surfaces", "evidence_surface")
        if k in data
    }


@pytest.mark.parametrize("route", ROUTES)
def test_owner_pages_out_of_order_beyond_cap_match_full_stream(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-quarter linked rows sit behind 1600 newer rows in owner append
    order. The owner projection drops linked_object_summary, so the store
    wrapper re-attaches it to the port's rows (real port, real paging) so the
    linked rows survive the quarter filter and the output is non-trivial."""
    newer = _linked_refs("newer", 1_600, "2026-06-01T00:00:00Z")
    inq = _linked_refs("q", 501, "2026-02-15T00:00:00Z")
    rows = newer + inq
    linked = {r["ref_id"]: r["linked_object_summary"] for r in rows}

    class _OrderedOwner(_Owner):
        def http_get(self, base_url: str, path: str) -> Tuple[bool, Any]:
            self.calls.append(path)
            query = dict(p.split("=") for p in path.split("?", 1)[1].split("&"))
            size, start = int(query["page_size"]), int(query["page_token"])
            items = [
                {
                    "evidence_item_id": r["ref_id"],
                    "item_type": "filing",
                    "citation_label": r["ref_id"],
                    "available_time": r["source_document"]["captured_at"],
                    "metadata": {},
                }
                for r in rows[start:start + size]
            ]
            return True, {"items": items, "total": len(rows), "summary": None}

    def linking_read(self: Any, **kwargs: Any):
        result = self._port.read_evidence_refs(**kwargs)
        for item in result.items:
            item["linked_object_summary"] = dict(linked[item["ref_id"]])
        return result

    monkeypatch.setattr(_OwnerBackedStore, "read_evidence_refs", linking_read)
    owner = _OrderedOwner(0)
    bounded = _get(owner, route)
    assert len(owner.calls) == 5  # ceil(2101 / 500)
    legacy_owner = _OrderedOwner(0)
    monkeypatch.setattr(personas_service, "_PM12_QUARTER_EVIDENCE_PAGE_SIZE", None)
    full = _get(legacy_owner, route)
    assert bounded["status"] == full["status"] == 200
    assert _strip_volatile(copy.deepcopy(bounded["body"])) == _strip_volatile(
        copy.deepcopy(full["body"])
    )
    assert _snapshot_surface(_strip_volatile(copy.deepcopy(bounded["body"]))) == _snapshot_surface(
        _strip_volatile(copy.deepcopy(full["body"]))
    )
    # non-trivial: the 501 linked in-quarter refs reach the output
    assert repr(bounded["body"]).count("q-0") > 0, "linked quarter refs must reach the output"
    probe = personas_service._pm12_quarter_evidence_refs(
        [dict(r, evidence_origin="source_ingest") for r in inq],
        {"start_at": "2026-01-01T00:00:00Z", "end_exclusive_at": "2026-04-01T00:00:00Z"},
    )
    assert len(probe) == 501


def _result(items: List[Dict[str, Any]], token: Any, total: Any = None, available: bool = True) -> Any:
    from services.control_plane.bff.ports.research_knowledge_source import EvidenceReadResult

    return EvidenceReadResult(items, total, "ok" if available else "unavailable", "x", token)


def test_empty_filtered_page_with_next_token_is_followed() -> None:
    """500 other-tenant rows (filtered to empty) then the wanted row."""
    owner = _Owner(501)
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
    )

    def read(**kw: Any):
        r = port.read_evidence_refs(**kw)
        if kw.get("page_token") is None:
            r.items = []  # tenant filter emptied the first page, token remains
        return r

    rows = personas_service._pm12_read_all_evidence_pages(read)
    assert [x["ref_id"] for x in rows] == ["ev-500"]


def test_deadline_is_enforced_on_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_EVIDENCE_OWNER_READ_TIMEOUT_SECONDS", "0.1")

    def timed(read: Any) -> Tuple[Any, float]:
        started = time.monotonic()
        out = personas_service._pm12_read_all_evidence_pages(read)
        return out, time.monotonic() - started

    # single page slower than the budget
    out, took = timed(lambda **kw: (time.sleep(0.4), _result([{"ref_id": "a"}], None))[1])
    assert out is None and took < 0.25, took

    # first page fast, next page slow (offset path)
    def slow_next(**kw: Any):
        if kw["page_token"] is None:
            time.sleep(0.02)
            return _result([{"ref_id": str(i)} for i in range(500)], "500", 5_000)
        time.sleep(0.4)
        return _result([{"ref_id": "x"}], None, 5_000)

    out, took = timed(slow_next)
    assert out is None and took < 0.25, took  # 9 remaining pages > 8 workers

    # opaque-token sequential path
    def opaque(**kw: Any):
        time.sleep(0.04)
        return _result([{"ref_id": "o"}], "t-" + str(time.monotonic()), None)

    out, took = timed(opaque)
    assert out is None and took < 0.25, took


class _WindowOwner:
    """Owner honouring captured_from/captured_before like the real route:
    filtered total, ``filters`` echoed. ``honour=False`` models an old owner."""

    def __init__(self, rows: List[Dict[str, Any]], *, honour: bool = True):
        self.rows, self.honour, self.calls = rows, honour, []

    def http_get(self, base_url: str, path: str) -> Tuple[bool, Any]:
        from urllib.parse import parse_qs, urlparse

        self.calls.append(path)
        q = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        size, start = int(q["page_size"]), int(q["page_token"])
        rows = self.rows
        payload: Dict[str, Any] = {"summary": None}
        if self.honour and "captured_from" in q:
            lo, hi = q["captured_from"], q["captured_before"]
            rows = [r for r in rows if lo <= r["available_time"].replace("Z", "+00:00") < hi]
            payload["filters"] = {"captured_from": lo, "captured_before": hi}
        payload["items"] = [
            {"evidence_item_id": r["id"], "item_type": "filing", "citation_label": r["id"],
             "available_time": r["available_time"], "metadata": {}}
            for r in rows[start:start + size]
        ]
        payload["total"] = len(rows)
        return True, payload


def test_quarter_window_makes_request_count_follow_the_quarter_not_the_total() -> None:
    rows = [{"id": f"new-{i}", "available_time": "2026-06-01T00:00:00+00:00"} for i in range(1_600)]
    rows += [{"id": f"q-{i}", "available_time": "2026-02-15T00:00:00+00:00"} for i in range(501)]
    window = {"start_at": "2026-01-01T00:00:00Z", "end_exclusive_at": "2026-04-01T00:00:00Z"}

    def run(owner: _WindowOwner) -> List[str]:
        port = DefaultResearchKnowledgeSourcePort(
            source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
        )
        got = personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs, window)
        assert got is not None
        in_q = [r["ref_id"] for r in got if str(r["ref_id"]).startswith("q-")]
        return sorted(in_q)

    new_owner, old_owner = _WindowOwner(rows), _WindowOwner(rows, honour=False)
    assert run(new_owner) == run(old_owner) == sorted(f"q-{i}" for i in range(501))
    assert len(new_owner.calls) == 2  # ceil(501/500): independent of the 1600 newer rows
    assert len(old_owner.calls) == 5  # old owner ignores the window: truthful full paging
    # a window with nothing in it is one request, not a full stream
    empty = _WindowOwner(rows)
    far = {"start_at": "2020-01-01T00:00:00Z", "end_exclusive_at": "2020-04-01T00:00:00Z"}
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=empty.http_get
    )
    assert personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs, far) == []
    assert len(empty.calls) == 1



@pytest.mark.parametrize("out_of_window", [0, 1600, 6000])
@pytest.mark.parametrize("in_window", [50, 500, 501, 1200])
def test_requests_equal_ceil_in_window_and_ignore_out_of_window_volume(in_window: int, out_of_window: int) -> None:
    import math

    rows = [{"id": f"o-{i}", "available_time": "2026-06-01T00:00:00+00:00"} for i in range(out_of_window)]
    rows += [{"id": f"q-{i}", "available_time": "2026-02-15T00:00:00+00:00"} for i in range(in_window)]
    window = {"start_at": "2026-01-01T00:00:00Z", "end_exclusive_at": "2026-04-01T00:00:00Z"}
    owner = _WindowOwner(rows)
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
    )
    got = personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs, window)
    assert got is not None and len(got) == in_window
    assert len(owner.calls) == math.ceil(in_window / 500)
