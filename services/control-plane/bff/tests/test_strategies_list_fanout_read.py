"""Risk-center nine-read benchmark and strategy page memo regression tests.

Run as a script with --benchmark before|after --output /tmp/result.json.
The before mode loads the immutable pre-change port, keeping every other
router, fixture and injected downstream latency identical.
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
import math
from pathlib import Path
import pstats
import subprocess
import threading
import time
import types
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from services.control_plane.bff.auth import policy
from services.control_plane.bff.command_adapters import base
from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.tests.conftest import build_consolidated_cross_cutting_app, make_composed_persona_service
from services.control_plane.bff.tests.test_strategy_list_default_view import _make_registry_entry
from services.registry.models import ArtifactState
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore

BASE = "e2f185058632ea4873ff047d46342c2a02851a0a"
PORT_PATH = "services/control-plane/bff/ports/research_knowledge_source.py"
PATHS = [
    "/bff/strategies?exclude_state=draft&page_size=200",
    "/bff/personas?page_size=200", "/bff/capital-pools?page_size=200",
    "/bff/runtimes?page_size=200", "/bff/tools", "/bff/mcp-servers",
    "/bff/skills", "/bff/alerts",
    "/bff/incidents?status=open%2Cinvestigating%2Cmitigating%2Cin_progress&page_size=200",
]
HEADERS = {"Authorization": "Bearer fanout:operator,viewer:tenant-dev", "X-Tenant-Id": "tenant-dev"}


def baseline_port():
    source = subprocess.check_output(["git", "show", f"{BASE}:{PORT_PATH}"], text=True)
    module = types.ModuleType("services.control_plane.bff.ports._fanout_baseline")
    # dataclass resolves the module during compilation.
    import sys
    sys.modules[module.__name__] = module
    exec(compile(source, f"{BASE}/{PORT_PATH}", "exec"), module.__dict__)
    return module.DefaultResearchKnowledgeSourcePort


@contextmanager
def caller(tenant="tenant-dev", token=None):
    a = owner_reads.authorization.set(token or f"Bearer fanout:operator,viewer:{tenant}")
    t = owner_reads.selected_tenant.set(tenant)
    try:
        with owner_reads.request_read_scope():
            yield
    finally:
        owner_reads.selected_tenant.reset(t)
        owner_reads.authorization.reset(a)


def fixture(monkeypatch, port_class=DefaultResearchKnowledgeSourcePort, latency=0.0):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.fixture")
    store = RegistryStore()
    for tenant, size in (("tenant-dev", 3648), ("tenant-other", 23)):
        for i in range(size):
            entry = _make_registry_entry(
                registry_id=f"reg-{tenant}-{i:04d}", strategy_id=f"strat-{tenant}-{i:04d}",
                version="1.0.0", owner_tenant=tenant, title=f"Strategy {i:04d}",
                artifact_state=ArtifactState.APPROVED if i < (166 if tenant == "tenant-dev" else 7) else ArtifactState.DRAFT,
                updated_at="2026-11-14T12:00:00Z",
            )
            store._entries[entry.registry_id] = entry
    registry = RegistryService(store=store)
    calls = []
    lock = threading.Lock()

    def http(url, **kw):
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        assert q.get("view") == ["strategy_summary_page"], url
        with lock:
            calls.append((url, kw["tenant_id"]))
        time.sleep(latency)
        return registry.strategy_spec_summary_page(
            visible_tenants=frozenset({kw["tenant_id"], "__builtin__"}), artifact_type="strategy_spec",
            state=q.get("state", [None])[0], exclude_state=q.get("exclude_state", [None])[0],
            persona_id=q.get("persona_id", [None])[0], page_token=q.get("page_token", [None])[0],
            page_size=int(q.get("page_size", [20])[0]),
        )

    monkeypatch.setattr(base, "http_request_json", http)
    port = port_class()
    ports = create_in_memory_read_surface_ports()
    ports.research_knowledge_source = port
    from services.control_plane.bff.personas import service as personas
    monkeypatch.setattr(personas, "_composed_persona_service", make_composed_persona_service(read_store=ports))
    app = build_consolidated_cross_cutting_app(ports)
    app.add_middleware(owner_reads.OwnerReadContextMiddleware)
    return app, port, calls


async def read(app, path, headers=HEADERS):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        start = time.perf_counter()
        r = await client.get(path, headers=headers)
        body = r.json()
        assert r.status_code == 200, (path, r.status_code, body)
        return time.perf_counter() - start, body


def stats(samples):
    ordered = sorted(samples)
    return {"p50_seconds": ordered[math.ceil(len(samples)*.5)-1],
            "p95_seconds": ordered[math.ceil(len(samples)*.95)-1],
            "max_seconds": max(samples), "rounds": len(samples), "samples_seconds": samples}


async def nine_reads(app, rounds):
    samples = {p: [] for p in PATHS}
    for _ in range(rounds):
        results = await asyncio.wait_for(asyncio.gather(*(read(app, p) for p in PATHS)), timeout=15)
        for p, (elapsed, body) in zip(PATHS, results):
            samples[p].append(elapsed)
            if p == PATHS[0]:
                assert body["page_info"]["total"] == 166
    return {p: stats(s) for p, s in samples.items()}


def test_response_order_tokens_and_tenant_rbac_match_baseline(monkeypatch):
    async def compare():
        bodies = []
        for cls in (baseline_port(), DefaultResearchKnowledgeSourcePort):
            app, _, _ = fixture(monkeypatch, cls)
            pages = []
            for headers in (HEADERS, {"Authorization": "Bearer other:viewer:tenant-other", "X-Tenant-Id": "tenant-other"}):
                for path in (PATHS[0], "/bff/strategies?page_size=100&page_token=100", "/bff/strategies?state=approved&page_size=100&page_token=100"):
                    _, body = await read(app, path, headers)
                    # All snapshot timestamps vary independently of store state.
                    def scrub(v):
                        if isinstance(v, dict):
                            return {k: scrub(x) for k, x in v.items() if k not in {"snapshot_at", "snapshotAt"}}
                        return [scrub(x) for x in v] if isinstance(v, list) else v
                    pages.append(scrub(body))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as c:
                for headers in ({}, {"Authorization": "Bearer no-role:unprivileged:tenant-dev"}, {**HEADERS, "X-Tenant-Id": "tenant-other"}):
                    response = await c.get(PATHS[0], headers=headers)
                    assert response.status_code in (401, 403)
            bodies.append(pages)
        assert bodies[0] == bodies[1]
    asyncio.run(compare())


def test_nine_read_fanout_p95(monkeypatch):
    app, _, _ = fixture(monkeypatch, latency=0.54)
    result = asyncio.run(nine_reads(app, 20))
    assert result[PATHS[0]]["p95_seconds"] < 2.0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=("before", "after"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with pytest.MonkeyPatch.context() as mp:
        app, port, calls = fixture(mp, baseline_port() if args.benchmark == "before" else DefaultResearchKnowledgeSourcePort, latency=0.54)
        profiler = cProfile.Profile()
        with caller():
            profiler.runcall(port.list_strategy_specs_page, exclude_state="draft", page_size=200)
        ps = pstats.Stats(profiler)
        profile = [{"file": k[0], "line": k[1], "name": k[2], "calls": v[1], "self_seconds": v[2], "cumulative_seconds": v[3]} for k,v in sorted(ps.stats.items(), key=lambda kv: kv[1][3], reverse=True)[:16]]
        # A fresh port keeps the first timed round cold in both modes.
        app, port, calls = fixture(mp, baseline_port() if args.benchmark == "before" else DefaultResearchKnowledgeSourcePort, latency=0.54)
        result = asyncio.run(nine_reads(app, 20))
        output = {"mode": args.benchmark, "base": BASE, "fixture": {"non_draft":166, "non_retired":3648, "other_tenant":23, "injected_registry_latency_seconds":0.54}, "fanout":result, "owner_calls":len(calls), "profile":profile}
        Path(args.output).write_text(json.dumps(output, indent=2)+"\n")
        print(json.dumps({"mode": args.benchmark, "strategies": result[PATHS[0]], "owner_calls":len(calls)}))
