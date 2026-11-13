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


def test_rows_keep_owner_order_and_are_not_truncated() -> None:
    owner = _Owner(3_100)
    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://owner.invalid", http_get_fn=owner.http_get
    )
    rows = personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs)
    assert [r["ref_id"] for r in rows] == [f"ev-{i}" for i in range(3_100)]


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


@pytest.mark.parametrize("route", ROUTES)
def test_owner_pages_out_of_order_beyond_cap_match_full_stream(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-quarter linked rows sit behind 1500+ newer rows in owner append
    order (the shape that was silently truncated)."""
    newer = _linked_refs("newer", 1_600, "2026-06-01T00:00:00Z")
    inq = _linked_refs("q", 501, "2026-02-15T00:00:00Z")
    rows = newer + inq

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
                    "linked_object_summary": r["linked_object_summary"],
                }
                for r in rows[start:start + size]
            ]
            return True, {"items": items, "total": len(rows), "summary": None}

    bounded = _get(_OrderedOwner(0), route)
    monkeypatch.setattr(personas_service, "_PM12_QUARTER_EVIDENCE_PAGE_SIZE", None)
    full = _get(_OrderedOwner(0), route)
    assert bounded["status"] == full["status"]
    assert _strip_volatile(copy.deepcopy(bounded["body"])) == _strip_volatile(
        copy.deepcopy(full["body"])
    )
