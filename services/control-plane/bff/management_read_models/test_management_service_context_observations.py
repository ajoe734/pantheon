"""Regression tests for MGMT-READ-001 owner-projection observations.

Acceptance criteria covered (pkt-pantheon-structural-closure-functional-v2-20260903):
  1. Real owner provenance (owner/source_kind/source_version/observed_at/
     correlation_id) carried on a record is preserved on the returned
     ManagementObservation instead of being overwritten with
     management_ai_context/live/now/freshness=0.
  2. An unconfigured or provider-failed domain (real DomainRuntimePort with
     no store/provider configured) is reported as an explicit
     status="unavailable" observation, not inferred as healthy/live just
     because the list call did not raise.
  3. A telemetry read failure surfaced through the committed
     ``_mgmt_nl_collect_context`` portfolio collector produces an explicit
     unavailable telemetry observation and the portfolio_book surface does
     not report "ok" while that failure is unresolved.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports import create_read_surface_ports

NOW = "2026-09-08T18:00:00Z"


def test_replay_owner_provenance_is_preserved() -> None:
    record = dict(
        runtime_id="r1",
        owner="runtime",
        source_kind="replayed",
        source_version="v17",
        observed_at="2026-09-07T18:00:00Z",
        correlation_id="c17",
    )
    store = SimpleNamespace(list_runtime_bindings=lambda: [record])
    rows, obs = ManagementService(read_store=store, utc_now=lambda: NOW).get_context_runtime_bindings()
    assert rows == [record]
    assert (
        obs["owner"],
        obs["source_kind"],
        obs["source_version"],
        obs["observed_at"],
        obs["freshness_seconds"],
        obs["correlation_id"],
    ) == ("runtime", "replayed", "v17", record["observed_at"], 86400, "c17")


def test_unconfigured_domain_is_not_healthy_live() -> None:
    rows, obs = ManagementService(read_store=create_read_surface_ports()).get_context_runtime_bindings()
    assert rows == []
    assert obs["status"] == "unavailable", (rows, obs)
    assert obs["source_kind"] == "unavailable"
    assert obs["degradation_reason"]


def test_capital_pools_unconfigured_domain_is_not_healthy_live() -> None:
    rows, obs = ManagementService(read_store=create_read_surface_ports()).get_context_capital_pools()
    assert rows == []
    assert obs["status"] == "unavailable", (rows, obs)


def test_telemetry_read_failure_returns_explicit_unavailable_observation() -> None:
    def failed_telemetry(runtime_id: str) -> None:
        raise RuntimeError("telemetry owner unavailable")

    store = SimpleNamespace(get_telemetry_summary=failed_telemetry)
    summary, obs = ManagementService(read_store=store, utc_now=lambda: NOW).get_context_telemetry_summary("r1")
    assert summary is None
    assert obs["subject_type"] == "telemetry"
    assert obs["status"] == "unavailable"
    assert obs["source_kind"] == "unavailable"


def test_telemetry_failure_remains_explicit_in_portfolio_context() -> None:
    # Execute the exact committed context collector, isolating unrelated adapters.
    tree = ast.parse(Path("services/control-plane/bff/main.py").read_text())
    node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_mgmt_nl_collect_context"
    )

    def failed_telemetry(runtime_id: str) -> None:
        raise RuntimeError("telemetry owner unavailable")

    store = SimpleNamespace(
        list_runtime_bindings=lambda: [{"runtime_id": "r1"}],
        list_capital_pools=lambda: [{"pool_id": "p1"}],
        get_telemetry_summary=failed_telemetry,
    )
    namespace = dict(__import__("typing").__dict__)
    namespace.update(
        _management_ai_context_service=ManagementService(read_store=store),
        _mgmt_nl_filter_tenant_records=lambda rows, tenant: rows,
        _mgmt_nl_add_record_entities=lambda *args: None,
        _management_telemetry_rollup=lambda *args: {},
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), namespace)
    context = namespace["_mgmt_nl_collect_context"]("portfolio", NOW)
    surface = context["surfaces"]["portfolio_book"]
    assert surface["status"] != "ok", surface
    assert any(
        o["subject_type"] == "telemetry" and o["status"] == "unavailable"
        for o in surface["owner_observations"]
    )
