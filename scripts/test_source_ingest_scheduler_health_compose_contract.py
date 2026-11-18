"""Compose contract: the Agora projector waits for a scheduler whose healthcheck is loop liveness."""

from __future__ import annotations

from pathlib import Path

import yaml

from services.source_ingestion import controller_healthcheck
from services.source_ingestion.controller_state import ControllerState

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
EXPECTED_HEALTHCHECK = ["CMD-SHELL", "python -m services.source_ingestion.controller_healthcheck"]


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_scheduler_healthcheck_command_is_the_liveness_module() -> None:
    assert _services()["source-ingest-scheduler"]["healthcheck"]["test"] == EXPECTED_HEALTHCHECK


def test_projector_requires_healthy_scheduler() -> None:
    depends = _services()["source-ingest-agora-projector"]["depends_on"]
    assert depends["source-ingest-scheduler"]["condition"] == "service_healthy"


def test_scheduler_with_connector_failures_satisfies_projector_condition(tmp_path) -> None:
    state = ControllerState(
        controller_id="c", controller_name="n", environment="dev", tenant_id="t", deployment={}
    )
    state.record_tick_started()
    state.record_success(desired_state={}, reconcile={}, schedule={}, actual_readback={})
    for _ in range(4):
        state.record_tick_started()
        state.record_failure(stage="schedule", reason="scheduled source tick reported 2 failed connector(s)")
    from services.source_ingestion.controller_state import ControllerStateStore

    path = tmp_path / "controller_state.json"
    ControllerStateStore(path).save(state)
    # The healthcheck command in compose runs this module; exit 0 == service_healthy.
    assert controller_healthcheck.evaluate(path, 60, 180) is None
