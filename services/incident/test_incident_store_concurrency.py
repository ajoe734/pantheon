from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services.incident.incident import IncidentCase, IncidentStore


def _incident(index: int) -> IncidentCase:
    return IncidentCase(
        incident_id=f"inc-concurrent-{index}",
        title=f"Concurrent incident {index}",
        status="open",
        severity="high",
        created_at="2026-07-14T00:00:00Z",
        binding_id=f"binding-{index}",
        deployment_stage="paper",
        deployment_plan_id=f"plan-{index}",
        capital_pool_id="pool-1",
        persona_capital_binding_id=f"pcb-{index}",
        artifact_id=f"artifact-{index}",
        artifact_version="1.0.0",
        runtime_id=f"runtime-{index}",
        trace_id=f"trace-{index}",
    )


def test_json_incident_store_serializes_cross_instance_writes(tmp_path):
    path = tmp_path / "incidents.json"

    def create(index: int) -> None:
        IncidentStore(path=path).create_incident(_incident(index))

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(create, range(30)))

    reloaded = IncidentStore(path=path)
    assert {item.incident_id for item in reloaded.list_incidents()} == {
        f"inc-concurrent-{index}" for index in range(30)
    }
