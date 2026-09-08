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
  4. Tenant scoping filters records before any owner/provenance is derived,
     so a foreign tenant's provenance never leaks into another tenant's
     context even when the authorized record count is zero.
  5. Real telemetry-summary provenance (owner/source_kind/source_version/
     observed_at/correlation_id) is preserved, not overwritten with
     runtime_id/live/now/freshness=0.
  6. A domain surface status probe answered successfully followed by the
     actual read call failing (error swallowed by the underlying port) is
     not reported as a healthy/live read; availability is bound to the
     real read outcome and fails closed.
  7. A runtime/pool observation failure inside the portfolio/persona_fleet
     collectors is aggregated into the surface status instead of being
     masked by another contributing surface's success.
  8. A record that itself carries an explicit unavailable status/
     degradation_reason (e.g. the owner reported itself offline) is
     preserved verbatim and never promoted to status="ok" merely because
     the read returned a non-empty batch.
  9. A record with observed_at=None never has its freshness fabricated
     from the request clock; unknown observation time/freshness stay
     null rather than manufacturing fresh owner evidence.
  10. A provider that answers a status probe successfully and then fails
      on the real read (error swallowed inside the port) returns an
      explicit degradation_reason instead of a bare live/None pairing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.persona_capital_runtime import RuntimePort

NOW = "2026-09-08T18:00:00Z"


def _collect_context(store: SimpleNamespace, tenant_id: str = "tenant-a") -> dict:
    """Execute the exact committed context collector plus its tenant-scoping
    helpers, isolating unrelated adapters."""
    tree = ast.parse(Path("services/control-plane/bff/main.py").read_text())
    names = {
        "_mgmt_nl_collect_context",
        "_mgmt_nl_filter_tenant_records",
        "_mgmt_nl_record_matches_tenant",
        "_mgmt_nl_record_tenant_ids",
        "_mgmt_nl_scope_values",
    }
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = dict(__import__("typing").__dict__)
    namespace.update(
        re=re,
        _management_ai_context_service=ManagementService(read_store=store, utc_now=lambda: NOW),
        _mgmt_nl_add_record_entities=lambda *args: None,
        _management_telemetry_rollup=lambda *args: {},
    )
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), namespace)
    return namespace["_mgmt_nl_collect_context"]("portfolio", NOW, tenant_id)


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


def test_telemetry_replay_provenance_is_preserved() -> None:
    record = dict(
        runtime_id="r1",
        owner="telemetry",
        source_kind="backfill",
        source_version="v9",
        observed_at="2026-09-07T18:00:00Z",
        correlation_id="c9",
    )
    store = SimpleNamespace(get_telemetry_summary=lambda _: record)
    _summary, obs = ManagementService(read_store=store, utc_now=lambda: NOW).get_context_telemetry_summary("r1")
    assert (
        obs["owner"],
        obs["source_kind"],
        obs["source_version"],
        obs["observed_at"],
        obs["freshness_seconds"],
        obs["correlation_id"],
    ) == ("telemetry", "backfill", "v9", record["observed_at"], 86400, "c9"), obs


def test_tenant_b_provenance_does_not_enter_tenant_a_context() -> None:
    foreign = dict(
        runtime_id="r-b",
        tenant_id="tenant-b",
        owner="owner-b",
        source_kind="live",
        source_version="private-v-b",
        correlation_id="private-c-b",
    )
    store = SimpleNamespace(list_runtime_bindings=lambda: [foreign], list_capital_pools=lambda: [])
    context = _collect_context(store)
    assert context["snippets"]["portfolio"]["runtime_count"] == 0
    assert "private-c-b" not in repr(context), context["surfaces"]


def test_unavailable_runtime_does_not_produce_healthy_portfolio() -> None:
    def fail() -> None:
        raise RuntimeError("runtime owner down")

    context = _collect_context(
        SimpleNamespace(list_runtime_bindings=fail, list_capital_pools=lambda: [{"pool_id": "p1"}])
    )
    surface = context["surfaces"]["portfolio_book"]
    assert surface["status"] != "ok", surface


def test_read_failure_after_status_probe_is_not_healthy_live() -> None:
    calls = []

    def provider():
        calls.append(1)
        if len(calls) == 1:
            return [{"runtime_id": "r1"}]
        raise RuntimeError("provider became unavailable")

    runtime = RuntimePort(runtime_bindings_provider=provider)
    store = SimpleNamespace(
        get_surface_status=lambda: {"persona_capital_runtime": {"runtime": runtime.get_surface_status()}},
        list_runtime_bindings=runtime.list_runtime_bindings,
    )
    rows, obs = ManagementService(read_store=store).get_context_runtime_bindings()
    assert rows == []
    assert obs["status"] != "ok", obs


@pytest.mark.parametrize("subject", ["runtime", "telemetry"])
def test_explicit_unavailable_owner_is_not_promoted_to_ok(subject: str) -> None:
    row = dict(
        runtime_id="r1",
        owner="runtime-owner",
        source_kind="unavailable",
        status="unavailable",
        observed_at=None,
        degradation_reason="owner offline",
    )
    service = ManagementService(
        read_store=SimpleNamespace(list_runtime_bindings=lambda: [row], get_telemetry_summary=lambda _: row),
        utc_now=lambda: NOW,
    )
    _rows, obs = (
        service.get_context_runtime_bindings() if subject == "runtime" else service.get_context_telemetry_summary("r1")
    )
    assert obs["status"] != "ok" and obs["degradation_reason"] == "owner offline", obs


@pytest.mark.parametrize("subject", ["runtime", "telemetry"])
def test_missing_observation_time_does_not_fabricate_freshness(subject: str) -> None:
    row = dict(runtime_id="r1", owner="runtime-owner", source_kind="backfill", source_version="v1", observed_at=None)
    service = ManagementService(
        read_store=SimpleNamespace(list_runtime_bindings=lambda: [row], get_telemetry_summary=lambda _: row),
        utc_now=lambda: NOW,
    )
    _rows, obs = (
        service.get_context_runtime_bindings() if subject == "runtime" else service.get_context_telemetry_summary("r1")
    )
    assert obs["observed_at"] is None and obs["freshness_seconds"] is None, obs


def test_swallowed_provider_failure_explains_degradation() -> None:
    calls = []

    def provider():
        calls.append(1)
        if len(calls) == 1:
            return [{"runtime_id": "r1"}]
        raise RuntimeError("provider became unavailable")

    runtime = RuntimePort(runtime_bindings_provider=provider)
    service = ManagementService(
        read_store=SimpleNamespace(
            get_surface_status=lambda: {"persona_capital_runtime": {"runtime": runtime.get_surface_status()}},
            list_runtime_bindings=runtime.list_runtime_bindings,
        ),
        utc_now=lambda: NOW,
    )
    rows, obs = service.get_context_runtime_bindings()
    assert rows == []
    assert obs["degradation_reason"] and obs["freshness_seconds"] is None, obs
