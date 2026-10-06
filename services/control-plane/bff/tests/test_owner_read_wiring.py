"""Real read-port shapes and fresh-process production router composition.

Only owner I/O is replaced. In particular, the runtime projector itself is
never injected by the test: main must wire the correct callable before mount.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from services.control_plane.bff.management_read_models.router import get_paper_telemetry_read_model
from services.control_plane.bff.management_read_models.service import (
    _build_trading_pulse_baseline_comparison,
    _project_operator_runtime_state_row,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.ports.persona_capital_runtime import RuntimePort


def test_owner_binding_metadata_survives_to_paper_and_trading_pulse():
    owner_row = {
        "binding_id": "binding-distinct", "runtime_id": "runtime-distinct",
        "deployment_mode": "paper", "status": "active",
        "metadata": {"strategy_id": "strategy-distinct", "persona_id": "persona-distinct"},
    }
    port = RuntimePort(runtime_bindings_provider=lambda: [owner_row])
    binding = port.get_runtime_binding("binding-distinct")
    assert binding["strategy_id"] == "strategy-distinct"
    assert binding["persona_id"] == "persona-distinct"
    assert "strategy_id" not in owner_row  # read adapter must not mutate owner state
    store = SimpleNamespace(list_runtime_bindings=port.list_runtime_bindings, list_telemetry_events=lambda: [])
    paper = get_paper_telemetry_read_model(store=store, strategy_id="strategy-distinct")
    assert [r["strategy_id"] for r in paper["items"]] == ["strategy-distinct"]
    assert paper["items"][0]["persona_id"] == "persona-distinct"
    row = _project_operator_runtime_state_row(None, binding, prefetched=True)
    comparison = _build_trading_pulse_baseline_comparison(None, row, prefetched=True)
    assert comparison["strategyId"] == comparison["strategy_id"] == "strategy-distinct"
    assert comparison["runtimeId"] == "runtime-distinct"
    assert comparison["runtimeBindingId"] == "binding-distinct"
    assert comparison["status"] == "unavailable"  # no invented drift evidence


@pytest.mark.parametrize("metadata", [None, [], {}, {"unrelated": "value"}])
def test_binding_id_is_not_promoted_to_strategy_identity(metadata):
    port = RuntimePort(runtime_bindings_provider=lambda: [{"binding_id": "binding-only", "metadata": metadata}])
    assert not port.list_runtime_bindings()[0].get("strategy_id")


def test_existing_explicit_identity_is_preserved():
    port = RuntimePort(runtime_bindings_provider=lambda: [{
        "binding_id": "b", "strategy_id": "explicit", "metadata": {"strategy_id": "legacy"},
    }])
    assert port.list_runtime_bindings()[0]["strategy_id"] == "explicit"


@pytest.mark.parametrize("sessions", [[], [{"runtime_id": "r", "binding_id": "b", "active": True}]])
def test_monitoring_owner_empty_and_nonempty_are_available(sessions):
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=lambda: sessions)
    assert store.list_paper_runtime_monitoring_sessions() == sessions
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "service"


@pytest.mark.parametrize("result", [None, {}, "invalid"])
def test_malformed_monitoring_owner_is_unavailable(result):
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=lambda: result)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "unavailable"


def test_failed_monitoring_owner_is_unavailable():
    def failed():
        raise TimeoutError("owner unavailable")
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=failed)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "unavailable"


def test_unconfigured_monitoring_owner_is_missing(monkeypatch):
    store = create_in_memory_read_surface_ports()
    # The in-memory factory deliberately supplies an empty provider; remove
    # that test owner to exercise the production unconfigured case.
    store._paper_runtime_monitoring_sessions_provider = None
    store._paper_fleet_reconciler_url = None
    monkeypatch.delenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", raising=False)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "missing"


def test_native_main_captures_context_projector_before_facade_install(tmp_path):
    root = Path(__file__).resolve().parents[4]
    code = r'''
import inspect, json, os
from unittest.mock import MagicMock, patch
from services.control_plane.bff.bootstrap.dependencies import AppDependencies
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.settings_store import SettingsStore
root = os.environ['BFF_DATA_DIR']
deps = AppDependencies(
    deployment_queries=MagicMock(), deployment_commands=MagicMock(),
    read_surface=create_in_memory_read_surface_ports(),
    command_store=CommandStore(root + '/commands.jsonl'),
    persona_write_owner=MagicMock(), ranking_write_owner=MagicMock(),
    strategy_write_owner=MagicMock(), settings_store=SettingsStore(root + '/settings.json'),
    decision_journal_write_owner=MagicMock(),
)
with patch.object(AppDependencies, 'create_default', return_value=deps):
    from services.control_plane.bff import main
route = next(r for r in main.app.routes if getattr(r, 'path', '') == '/api/v1/operator/runtime-state')
project = inspect.getclosurevars(route.endpoint).nonlocals['_project_operator_runtime_state_row']
row = project({'binding_id': 'b', 'runtime_id': 'r', 'strategy_id': 's', 'deployment_mode': 'paper'})
assert row['runtime_id'] == 'r'
assert row['strategy_id'] == 's'
assert 'telemetry_observation' in row
assert 'monitoring_observation' in row
assert 'rollback_observation' in row
assert project.__module__ == 'services.control_plane.bff.assistant.management_service'
print('native-runtime-projector-ok')
'''
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=root, text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(root), "BFF_DATA_DIR": str(tmp_path)}, timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "native-runtime-projector-ok" in run.stdout
