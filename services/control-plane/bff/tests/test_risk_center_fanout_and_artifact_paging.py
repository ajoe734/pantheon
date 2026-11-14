"""Reproduce the artifact paging prerequisite; these are diagnostic tests.

The current ports expose complete owner populations, not a bounded merged
catalog page. Passing these tests does NOT establish the task's constant-work
acceptance criterion. The benchmark reports that unmet criterion explicitly.

Rerun: .venv-pantheon/bin/python services/control-plane/bff/tests/test_risk_center_fanout_and_artifact_paging.py --benchmark
"""
from __future__ import annotations

import cProfile
from datetime import datetime, timedelta, timezone
import json
import math
import pstats
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    normalize_registry_catalog_row,
)
from services.control_plane.bff.research.service import ResearchRouterService
from services.control_plane.bff.strategies.routes.common import default_page_slice


def _fixture(size: int):
    origin = datetime(2026, 11, 14, tzinfo=timezone.utc)
    rows = [
        {
            "registry_id": f"bundle-{i:06}",
            "strategy_id": f"strategy-{i:06}",
            "version": "1.0.0",
            "artifact_state": "approved",
            "checksum": "a" * 64,
            "created_at": (origin + timedelta(seconds=i // 2)).isoformat(),
            "approver": "approver-fixture",
            "risk_level": "low",
        }
        for i in range(size)
    ]
    rows.reverse()
    work = {"owner_requests": 0, "owner_rows_received": 0}

    def http_get(_base, path):
        query = parse_qs(urlsplit(path).query)
        offset = int(query.get("page_token", ["0"])[0])
        limit = int(query.get("page_size", ["50"])[0])
        page = rows[offset:offset + limit]
        work["owner_requests"] += 1
        work["owner_rows_received"] += len(page)
        next_offset = offset + len(page)
        return True, {
            "items": page,
            "next_page_token": str(next_offset) if next_offset < size else None,
        }

    port = DefaultResearchKnowledgeSourcePort(
        registry_service_url="http://fixture.invalid",
        http_get_fn=http_get,
        research_write_owner=SimpleNamespace(list_research_artifacts=lambda: []),
    )
    service = ResearchRouterService(
        port_getter=lambda: port,
        utc_now=lambda: "2026-11-14T00:00:00Z",
        snapshot_meta=lambda at: {"snapshot_at": at},
        page_slice=default_page_slice,
    )
    return service, rows, work


def _walk(service):
    token = None
    pages = []
    while True:
        page = service.list_artifact_catalog(page_token=token, page_size=100)
        pages.append(page)
        token = page["page_info"]["next_page_token"]
        if token is None:
            return pages


def test_baseline_order_and_numeric_tokens_are_reproducible():
    service, rows, _ = _fixture(662)
    pages = _walk(service)
    expected = sorted(
        [normalize_registry_catalog_row(row) for row in rows],
        key=lambda row: (row["updatedAt"], row["id"]),
        reverse=True,
    )
    assert [item for page in pages for item in page["data"]] == expected
    assert [page["page_info"]["next_page_token"] for page in pages] == [
        "100", "200", "300", "400", "500", "600", None,
    ]
    assert all(page["items"] == page["data"] for page in pages)
    assert all(page["page_info"]["total"] == 662 for page in pages)


def test_baseline_each_page_rereads_complete_population():
    service, _, work = _fixture(662)
    service.list_artifact_catalog(page_size=100)
    assert work == {"owner_requests": 2, "owner_rows_received": 662}
    service.list_artifact_catalog(page_token="600", page_size=100)
    assert work == {"owner_requests": 4, "owner_rows_received": 1324}


def test_baseline_work_grows_with_population_despite_fixed_page_size():
    observed = []
    for size in (662, 6620):
        service, _, work = _fixture(size)
        page = service.list_artifact_catalog(page_size=100)
        assert len(page["data"]) == 100
        observed.append(work["owner_rows_received"])
    assert observed == [662, 6620]
    # This reproduces the missing owner capability, not a performance PASS.
    assert observed[1] == 10 * observed[0]


def _stats(samples):
    ordered = sorted(samples)
    return {
        "rounds": len(ordered),
        "p50_seconds": ordered[math.ceil(.5 * len(ordered)) - 1],
        "p95_seconds": ordered[math.ceil(.95 * len(ordered)) - 1],
        "max_seconds": ordered[-1],
    }


def benchmark():
    output = {"kind": "baseline_diagnostic", "acceptance_3": "NOT_MET", "fixtures": []}
    for size in (662, 6620):
        service, _, work = _fixture(size)
        first_page, full_walk = [], []
        for _ in range(20):
            start = time.perf_counter()
            service.list_artifact_catalog(page_size=100)
            first_page.append(time.perf_counter() - start)
            start = time.perf_counter()
            _walk(service)
            full_walk.append(time.perf_counter() - start)
        before = dict(work)
        profile = cProfile.Profile()
        profile.runcall(service.list_artifact_catalog, page_size=100)
        stats = pstats.Stats(profile)
        top = sorted(stats.stats.items(), key=lambda entry: entry[1][3], reverse=True)[:12]
        output["fixtures"].append({
            "artifacts": size,
            "first_page": _stats(first_page),
            "sequential_all_pages": _stats(full_walk),
            "per_page_owner_rows_received": work["owner_rows_received"] - before["owner_rows_received"],
            "per_page_owner_requests": work["owner_requests"] - before["owner_requests"],
            "profile": [{"function": key[2], "calls": val[1], "self_seconds": val[2], "cumulative_seconds": val[3]} for key, val in top],
        })
    return output


if __name__ == "__main__":
    if sys.argv[1:] != ["--benchmark"]:
        raise SystemExit("Pass --benchmark; use pytest to run diagnostic tests")
    print(json.dumps(benchmark(), indent=2))
