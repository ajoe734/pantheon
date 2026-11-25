"""Bounded, tenant-scoped paging of GET /api/source-ingest/management/strategy-seeds."""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus, SourceType
from services.source_ingestion.routers import management as management_module
from services.source_ingestion.routers.management import create_management_router
from services.source_ingestion.runtime import SourceIngestionRuntime
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed, StrategySpecSeedStatus

URL = "/api/source-ingest/management/strategy-seeds"
SECRET = "source-seed-owner-test-secret"


def _seed(seed_id: str, *, source_id: str, metadata: dict[str, Any] | None = None) -> StrategySpecSeed:
    # Hosted-shaped row: ~3 KB with a large execution recipe the scorer never reads.
    meta = {"strategy_family": "momentum", "execution_route": "none", "source_license_scope": "internal",
            "execution_recipe": {"steps": [{"name": f"step-{i}", "note": "x" * 120} for i in range(20)]}}
    meta.update(metadata or {})
    return StrategySpecSeed(
        seed_id=seed_id, source_id=source_id, evidence_bundle_id=f"evb-{source_id}",
        hypothesis=f"Hypothesis for {seed_id} on TWSE flow.", asset_class=["equity"], market_scope=["TWSE"],
        holding_period="swing", required_data=["ohlcv", "institutional_flow"], backend_hint="qlib",
        feature_hints=["momentum"], label_hints=["forward_return_5d"], risk_notes=["liquidity guard"],
        confidence=0.8, status=StrategySpecSeedStatus.DRAFT, source_ids=[source_id],
        evidence_item_ids=[f"evi-{source_id}"], citation_refs=["cite:1"], trace_refs=["trace:1"],
        created_at="2026-10-18T00:00:00Z", metadata=meta)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRATEGY_SPEC_SEED_STORE_PATH", str(tmp_path / "distill_seeds.jsonl"))
    monkeypatch.setenv("STRATEGY_SEED_STORE_PATH", str(tmp_path / "distill_seeds.jsonl"))
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "permissive")
    monkeypatch.setenv("STRATEGY_SEED_MAX_PER_SOURCE", "1000")
    runtime = SourceIngestionRuntime()
    runtime.evidence_repository.add_source_record(SourceRecord(
        source_id="src-owned", connector_id="c", source_type=SourceType.MARKET, title="t", content_ref="r",
        status=SourceRecordStatus.NORMALIZED, created_at="2026-10-18T00:00:00Z", metadata={"tenant_id": "tenant-dev"}))
    app = FastAPI()
    app.include_router(create_management_router(runtime))

    def headers(tenant: str = "tenant-dev") -> dict[str, str]:
        token = encode_jwt_hs256({"sub": "op", "roles": ["operator"], "tenant_id": tenant, "exp": int(time.time()) + 600}, secret=SECRET)
        return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}

    return runtime, TestClient(app), headers


def _fill(runtime: SourceIngestionRuntime, count: int, prefix: str = "seed") -> list[str]:
    """Interleave: own via metadata tenant, own via source record, other tenant, unowned."""
    mine: list[str] = []
    for i in range(count):
        sid = f"{prefix}-{i:04d}"
        kind = i % 4
        if kind == 0:
            runtime.strategy_seed_store.save(_seed(sid, source_id=f"src-m{i}", metadata={"tenant_id": "tenant-dev"}))
            mine.append(sid)
        elif kind == 1:
            runtime.strategy_seed_store.save(_seed(sid, source_id="src-owned"))
            mine.append(sid)
        elif kind == 2:
            runtime.strategy_seed_store.save(_seed(sid, source_id=f"src-o{i}", metadata={"tenant_id": "tenant-other"}))
        else:
            runtime.strategy_seed_store.save(_seed(sid, source_id=f"src-unowned{i}"))
    return mine


def _read_all(client, headers, **params) -> tuple[list[dict], list[dict]]:
    rows, pages, token = [], [], None
    while True:
        q = {**params, **({"page_token": token} if token else {})}
        body = client.get(URL, params=q, headers=headers()).json()
        pages.append(body)
        rows.extend(body["items"])
        token = body["next_page_token"]
        if not token:
            return rows, pages


def test_default_response_shape_is_unchanged(env) -> None:
    runtime, client, headers = env
    mine = _fill(runtime, 8)
    body = client.get(URL, headers=headers()).json()
    assert set(body) == {"items", "count", "store_path"}
    assert [i["seed_id"] for i in body["items"]] == mine
    assert "execution_recipe" in body["items"][0]["metadata"]


def test_pages_cover_exactly_the_tenant_seeds_once_each(env) -> None:
    runtime, client, headers = env
    mine = _fill(runtime, 40)
    rows, pages = _read_all(client, headers, page_size=3, fields="match")
    assert [r["seed_id"] for r in rows] == mine
    assert all(len(p["items"]) <= 3 for p in pages)
    full, _ = _read_all(client, headers, page_size=7)
    assert [r["seed_id"] for r in full] == mine
    assert full[0] == runtime.strategy_seed_store.get(mine[0]).to_dict()


def test_wrong_tenant_sees_nothing_and_tokens_carry_no_seed_id(env) -> None:
    runtime, client, headers = env
    _fill(runtime, 20)
    rows, pages = _read_all(client, lambda: headers("tenant-nobody"), page_size=2, fields="match")
    assert rows == []
    for p in pages:
        token = p["next_page_token"] or ""
        assert "seed-" not in token and "src-" not in token


def test_pages_are_stable_while_seeds_are_appended(env) -> None:
    runtime, client, headers = env
    mine = _fill(runtime, 24)
    first = client.get(URL, params={"page_size": 4, "fields": "match"}, headers=headers()).json()
    token = first["next_page_token"]
    runtime.strategy_seed_store.save(_seed("seed-appended-1", source_id="src-m-new", metadata={"tenant_id": "tenant-dev"}))
    seen = [r["seed_id"] for r in first["items"]]
    while token:
        body = client.get(URL, params={"page_size": 4, "fields": "match", "page_token": token}, headers=headers()).json()
        seen += [r["seed_id"] for r in body["items"]]
        token = body["next_page_token"]
    assert seen == mine + ["seed-appended-1"]
    assert len(seen) == len(set(seen))


def test_page_work_is_bounded_no_whole_store_parse_no_tenant_evidence_scan(env, monkeypatch) -> None:
    runtime, client, headers = env
    _fill(runtime, 200)
    parses = {"n": 0}
    real = StrategySpecSeed.from_dict.__func__

    def counting(cls, data):
        parses["n"] += 1
        return real(cls, data)

    monkeypatch.setattr(StrategySpecSeed, "from_dict", classmethod(counting))
    repo = runtime.evidence_repository
    reads = {"point": 0, "scan": 0}
    for name, key in (("get_source_record", "point"), ("get_bundle", "point"), ("get_evidence_item", "point"),
                      ("list_source_records", "scan"), ("list_bundles", "scan"), ("list_evidence_items", "scan")):
        original = getattr(repo, name)
        monkeypatch.setattr(repo, name, (lambda o, k: (lambda *a, **kw: (reads.__setitem__(k, reads[k] + 1), o(*a, **kw))[1]))(original, key))
    store_parses = []
    real_window = runtime.strategy_seed_store.read_window
    monkeypatch.setattr(runtime.strategy_seed_store, "read_window", lambda **kw: (store_parses.append(kw), real_window(**kw))[1])

    body = client.get(URL, params={"page_size": 5, "fields": "match"}, headers=headers()).json()
    assert body["count"] == 5
    assert parses["n"] == 0, "match projection must not build seed objects"
    assert reads["scan"] == 0, "no tenant-wide evidence listing per page"
    assert body["scanned"] <= 5 * 4 + 25 and reads["point"] <= body["scanned"] * 3
    full = client.get(URL, params={"page_size": 5}, headers=headers()).json()
    assert parses["n"] == full["count"] == 5, "full pages parse only the page"


def test_scan_budget_bounds_a_sparse_tenant(env) -> None:
    runtime, client, headers = env
    for i in range(1300):
        runtime.strategy_seed_store.save(_seed(f"other-{i:05d}", source_id=f"s{i}", metadata={"tenant_id": "tenant-other"}))
    runtime.strategy_seed_store.save(_seed("mine-last", source_id="sx", metadata={"tenant_id": "tenant-dev"}))
    first = client.get(URL, params={"page_size": 5, "fields": "match"}, headers=headers()).json()
    assert first["scanned"] <= management_module.SEED_PAGE_SCAN_MAX
    assert first["items"] == [] and first["next_page_token"]
    rows, _ = _read_all(client, headers, page_size=5, fields="match")
    assert [r["seed_id"] for r in rows] == ["mine-last"]


def test_match_projection_is_small_and_scores_like_the_full_seed(env) -> None:
    from services.control_plane.persona.persona_strategy_discovery import discover_for_demand
    runtime, client, headers = env
    _fill(runtime, 8)
    full = client.get(URL, headers=headers()).json()["items"]
    match, _ = _read_all(client, headers, page_size=50, fields="match")
    assert "execution_recipe" not in match[0]["metadata"] and "label_hints" not in match[0]
    assert len(str(match[0])) < len(str(full[0])) / 2
    persona = {"persona_id": "p1", "tenant_id": "tenant-dev", "name": "p"}
    demand = {"demand_id": "d1", "tried_families": [], "missing_datasets": []}
    a = discover_for_demand(persona, demand, strategy_seeds=full, created_at="2026-11-25T00:00:00Z", discovery_session_id="s")
    b = discover_for_demand(persona, demand, strategy_seeds=match, created_at="2026-11-25T00:00:00Z", discovery_session_id="s")
    assert a == b and a["candidates_considered"] == len(full)


def test_invalid_inputs_and_route_is_not_on_event_loop(env) -> None:
    runtime, client, headers = env
    assert client.get(URL, params={"page_token": "bogus"}, headers=headers()).status_code == 422
    assert client.get(URL, params={"fields": "everything"}, headers=headers()).status_code == 422
    assert client.get(URL).status_code in (401, 403)
    route = next(r for r in create_management_router(runtime).routes if getattr(r, "path", "") == URL)
    assert not inspect.iscoroutinefunction(route.endpoint), "sync endpoints run in the threadpool, not the event loop"
