from __future__ import annotations

import json
import time
from unittest import mock

from fastapi.testclient import TestClient

from services.research import discovery_allocator as da
from services.research.tests.test_research_orchestrator_http_service import _load_service_module


def _wire(module, calls):
    def factory():
        real = da.DiscoveryAllocator(
            scorer=lambda p, a: {"score": 90, "match_id": f"m-{p['persona_id']}-{a['strategy_spec_id']}"},
            provision=calls.append,
            paper_capital_cap=10_000,
            per_persona_capital=100,
            max_personas=99,  # must still be clamped to 5
        )
        return real

    module.discovery_allocator_ports.update(
        list_personas=lambda: [{"persona_id": f"p{i}"} for i in range(8)],
        allocator_factory=factory,
    )


def _records(module):
    path = module.store.data_dir / "discovery_allocator_runs.jsonl"
    deadline = time.time() + 5
    while time.time() < deadline and not (path.exists() and path.read_text().strip()):
        time.sleep(0.05)
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_startup_schedule_runs_allocator_paper_only_cap_five() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    admitted = [{"strategy_spec_id": f"spec-{i}", "registry_id": f"spec-{i}", "status": "admitted"} for i in range(8)]
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "true"}):
        with TestClient(module.app):  # runs startup handlers -> scheduler
            rec = _records(module)[0]
    assert rec["enabled"] is True
    assert rec["admitted_candidates_seen"] == 8
    assert len(rec["bound"]) == 5 == len(calls) == da.MAX_DISCOVERY_PERSONAS
    assert {c["execution_context"] for c in calls} == {"paper"}
    assert all(b["execution_context"] == "paper" for b in rec["bound"])


def test_setting_off_records_disabled_and_allocates_nothing() -> None:
    module = _load_service_module()
    calls: list = []
    _wire(module, calls)
    admitted = [{"strategy_spec_id": "s", "registry_id": "s", "status": "admitted"}]
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "false"}):
        with TestClient(module.app):
            rec = _records(module)[0]
    assert rec["reason"] == "disabled"
    assert rec["bound"] == [] and calls == []


def test_no_personas_records_reason() -> None:
    module = _load_service_module()
    admitted = [{"strategy_spec_id": "s", "registry_id": "s", "status": "admitted"}]
    with mock.patch.object(module.alpha_replication_admission_store, "list_admissions", return_value=admitted), \
         mock.patch.dict("os.environ", {"DISCOVERY_ALLOCATOR_ENABLED": "true"}):
        with TestClient(module.app):
            rec = _records(module)[0]
    assert rec["admitted_candidates_seen"] == 1
    assert rec["reason"] == "no_discovery_personas"
