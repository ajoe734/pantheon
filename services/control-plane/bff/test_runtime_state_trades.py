"""Tests for surfacing executed paper trades in the operator runtime-state DTO."""
import os
import unittest
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock

from services.control_plane.bff.management_read_models.service import (
    ManagementService,
    _derive_runtime_state_last_updated_at,
    _derive_runtime_state_row_health,
    _project_runtime_state_latest_rollback,
    _project_runtime_state_monitoring_session,
    _project_runtime_state_telemetry_summary,
)


def _project_operator_runtime_state_row(
    binding: Dict[str, Any],
    *,
    read_store: Optional[Any] = None,
    context_service: Optional[ManagementService] = None,
    prefetched: bool = False,
    telemetry_summary_record: Optional[Dict[str, Any]] = None,
    monitoring_session_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
    runtime_binding_id = (
        binding.get("runtime_binding_id")
        or binding.get("binding_id")
        or binding.get("id")
    )
    svc = context_service or (ManagementService(read_store=read_store) if read_store else None)

    telemetry_observation: Optional[Dict[str, Any]] = None
    if prefetched:
        raw_telemetry_summary = telemetry_summary_record
    elif svc:
        raw_telemetry_summary, telemetry_observation = svc.get_context_telemetry_summary(runtime_id)
    elif read_store and hasattr(read_store, "get_telemetry_summary"):
        raw_telemetry_summary = read_store.get_telemetry_summary(runtime_id)
    else:
        raw_telemetry_summary = None

    telemetry_summary = _project_runtime_state_telemetry_summary(raw_telemetry_summary)

    monitoring_observation: Optional[Dict[str, Any]] = None
    if prefetched:
        raw_monitoring_session = monitoring_session_record
    elif svc:
        raw_monitoring_session, monitoring_observation = svc.get_context_monitoring_session(
            runtime_id, str(runtime_binding_id or "")
        )
    elif read_store and hasattr(read_store, "get_paper_runtime_monitoring_session"):
        raw_monitoring_session = read_store.get_paper_runtime_monitoring_session(
            runtime_id=runtime_id, binding_id=str(runtime_binding_id or "")
        )
    else:
        raw_monitoring_session = None

    monitoring_session = _project_runtime_state_monitoring_session(raw_monitoring_session)

    rollback_observation: Optional[Dict[str, Any]] = None
    if svc:
        rollbacks, rollback_observation = svc.get_context_rollbacks(runtime_id)
    elif read_store and hasattr(read_store, "get_rollbacks"):
        try:
            rollbacks = read_store.get_rollbacks(runtime_id) or []
        except Exception:
            rollbacks = []
    else:
        rollbacks = []

    latest_rollback = _project_runtime_state_latest_rollback(rollbacks)
    artifact_id = binding.get("artifact_id")
    artifact_version = binding.get("artifact_version") or binding.get("version")
    plan_id = binding.get("plan_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "deployment_stage": binding.get("deployment_stage") or binding.get("deployment_mode"),
        "status": binding.get("status"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": f"/operator/deployment-review?plan={plan_id}",
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "telemetry_summary": telemetry_summary,
        "telemetry_observation": telemetry_observation,
        "monitoring_observation": monitoring_observation,
        "rollback_observation": rollback_observation,
        "executed_trade_count": (telemetry_summary or {}).get("executed_trade_count"),
        "total_trades": ((telemetry_summary or {}).get("metrics") or {}).get("total_trades"),
        "position_count": (telemetry_summary or {}).get("position_count"),
        "positions": (telemetry_summary or {}).get("positions"),
        "last_fill": (telemetry_summary or {}).get("last_fill"),
        "paper_runtime_monitoring": monitoring_session,
        "row_health": _derive_runtime_state_row_health(
            binding=binding,
            telemetry_summary=telemetry_summary,
            monitoring_session=monitoring_session,
        ),
        "rollback_summary": {
            "count": len(rollbacks),
            "latest": latest_rollback,
            "href": f"/api/v1/runtimes/{runtime_id}/rollbacks",
        },
        "last_updated_at": _derive_runtime_state_last_updated_at(
            binding,
            telemetry_summary,
            latest_rollback,
            monitoring_session,
        ),
    }


def _mgmt_nl_scoped_runtime_rows(
    runtime_bindings: List[Dict[str, Any]],
    entities: Set[Tuple[str, str]],
    *,
    read_store: Any,
    context_service: ManagementService,
) -> List[Dict[str, Any]]:
    rows = []
    for binding in runtime_bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or binding.get("binding_id") or "").strip()
        binding_id = str(binding.get("binding_id") or binding.get("runtime_binding_id") or binding.get("id") or "").strip()
        if runtime_id:
            entities.add(("runtime", runtime_id))
        if binding_id:
            entities.add(("runtime_binding", binding_id))
        if binding.get("capital_pool_id"):
            entities.add(("capital_pool", binding.get("capital_pool_id")))
        rows.append(
            _project_operator_runtime_state_row(
                binding, read_store=read_store, context_service=context_service
            )
        )
    return rows


def _mgmt_nl_trading_pulse_snippet(
    runtime_bindings: List[Dict[str, Any]],
    entities: Set[Tuple[str, str]],
    *,
    read_store: Any,
    context_service: ManagementService,
) -> Dict[str, Any]:
    runtime_rows = _mgmt_nl_scoped_runtime_rows(
        runtime_bindings, entities, read_store=read_store, context_service=context_service
    )
    telemetry_rows = [
        row.get("telemetry_summary")
        for row in runtime_rows
        if isinstance(row.get("telemetry_summary"), dict)
    ]
    telemetry_observations = [
        row.get(key)
        for row in runtime_rows
        for key in ("telemetry_observation", "monitoring_observation", "rollback_observation")
        if isinstance(row.get(key), dict)
    ]
    pnl_values = [
        float((row.get("metrics") or {}).get("pnl"))
        for row in telemetry_rows
        if (row.get("metrics") or {}).get("pnl") is not None
    ]
    fill_rate_values = [
        float((row.get("metrics") or {}).get("fill_rate"))
        for row in telemetry_rows
        if (row.get("metrics") or {}).get("fill_rate") is not None
    ]
    trade_values = [
        int((row.get("metrics") or {}).get("total_trades"))
        for row in telemetry_rows
        if (row.get("metrics") or {}).get("total_trades") is not None
    ]
    summary = {
        "runtimeCount": len(runtime_rows),
        "runtime_count": len(runtime_rows),
        "telemetryCoverageCount": len(telemetry_rows),
        "telemetry_coverage_count": len(telemetry_rows),
        "totalPnl": round(sum(pnl_values), 6) if pnl_values else None,
        "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "averageFillRate": sum(fill_rate_values) / len(fill_rate_values) if fill_rate_values else None,
        "average_fill_rate": sum(fill_rate_values) / len(fill_rate_values) if fill_rate_values else None,
        "totalTrades": int(sum(trade_values)) if trade_values else 0,
        "total_trades": int(sum(trade_values)) if trade_values else 0,
    }
    cards = [
        {"cardId": "runtime-status", "card_id": "runtime-status", "label": "Runtime Status", "value": len(runtime_rows)},
        {"cardId": "pnl", "card_id": "pnl", "label": "P&L", "value": summary["totalPnl"]},
        {"cardId": "execution-quality", "card_id": "execution-quality", "label": "Execution Quality", "value": summary["averageFillRate"]},
    ]
    return {"summary": summary, "cards": cards, "telemetry_observations": telemetry_observations}


def _mgmt_nl_merge_owner_observations(
    observations: List[Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    status_rank = {"ok": 0, "degraded": 1, "unavailable": 2}
    present = [obs for obs in observations if isinstance(obs, dict)]
    if not present:
        return {
            "status": "unavailable",
            "owner": "management_ai_context",
            "source_kind": "unavailable",
            "degradation_reason": "no contributing owner observation was collected.",
            "contributing_observations": [],
        }
    worst = max(present, key=lambda obs: status_rank.get(str(obs.get("status")), 0))
    degradation_reasons = [
        str(obs.get("degradation_reason"))
        for obs in present
        if obs.get("degradation_reason")
    ]
    merged = dict(worst)
    merged["degradation_reason"] = "; ".join(dict.fromkeys(degradation_reasons)) or worst.get("degradation_reason")
    merged["contributing_observations"] = present
    return merged


def _mgmt_nl_collect_context(
    focus: str,
    snapshot_at: str,
    tenant_id: Optional[str] = None,
    *,
    read_store: Any,
    context_service: ManagementService,
) -> Dict[str, Any]:
    use_all = focus in ("all", "")
    snippets: Dict[str, Any] = {}
    surfaces: Dict[str, Any] = {}
    evidence_entities: Set[Tuple[str, str]] = set()
    evidence_source_types: set = set()

    def _tenant_record_filter(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if tenant_id is None:
            return records
        return [r for r in records if r.get("tenant_id") in (None, tenant_id, "*")]

    if use_all or focus == "trading_pulse":
        try:
            runtime_bindings, runtime_bindings_obs = context_service.get_context_runtime_bindings(
                record_filter=_tenant_record_filter
            )
            pulse_data = _mgmt_nl_trading_pulse_snippet(
                runtime_bindings, evidence_entities, read_store=read_store, context_service=context_service
            )
            evidence_source_types.update({"runtime", "runtime_binding", "telemetry", "paper_live_drift"})
            snippets["trading_pulse"] = {
                "summary": pulse_data.get("summary"),
                "cards": pulse_data.get("cards"),
            }
            trading_pulse_owner_observation = _mgmt_nl_merge_owner_observations(
                [runtime_bindings_obs, *pulse_data.get("telemetry_observations", [])]
            )
            surfaces["management_trading_pulse"] = {
                "status": trading_pulse_owner_observation["status"],
                "source": "bff_composed",
                "owner_observation": trading_pulse_owner_observation,
            }
        except Exception:
            surfaces["management_trading_pulse"] = {"status": "unavailable", "source": "error"}

    return {
        "snippets": snippets,
        "surfaces": surfaces,
        "evidence_entities": evidence_entities,
        "evidence_source_types": evidence_source_types,
    }


class _RuntimeCompat:
    data_store = None
    _management_ai_context_service = None

    @staticmethod
    def _project_runtime_state_telemetry_summary(summary):
        return _project_runtime_state_telemetry_summary(summary)

    def _project_operator_runtime_state_row(self, binding):
        return _project_operator_runtime_state_row(
            binding,
            read_store=self.data_store,
            context_service=self._management_ai_context_service,
        )

    def _mgmt_nl_collect_context(self, focus, snapshot_at, tenant_id=None):
        return _mgmt_nl_collect_context(
            focus,
            snapshot_at,
            tenant_id,
            read_store=self.data_store,
            context_service=self._management_ai_context_service,
        )


runtime_compat = _RuntimeCompat()

_SUMMARY = {
    "window": "latest",
    "collected_at": "2026-06-14T11:36:37Z",
    "pnl": 28.12,
    "total_trades": 1,
    "fill_rate": 1.0,
    "deployment_stage": "paper",
    "state": "active",
    "executed_trade_count": 1,
    "position_count": 1,
    "positions": [{"symbol": "AAPL", "quantity": 7.0}],
    "last_fill": {"symbol": "AAPL", "quantity": 7.0, "fill_price": 100.0, "action": "market_order"},
}


class TestRuntimeStateTradesProjection(unittest.TestCase):
    def test_telemetry_summary_projection_surfaces_trade_fields(self):
        proj = runtime_compat._project_runtime_state_telemetry_summary(_SUMMARY)
        self.assertEqual(proj["executed_trade_count"], 1)
        self.assertEqual(proj["position_count"], 1)
        self.assertEqual(proj["positions"], [{"symbol": "AAPL", "quantity": 7.0}])
        self.assertEqual(proj["last_fill"]["symbol"], "AAPL")
        self.assertEqual(proj["metrics"]["total_trades"], 1)
        self.assertEqual(proj["metrics"]["pnl"], 28.12)

    def test_runtime_state_row_surfaces_trades_at_top_level(self):
        original_data_store = runtime_compat.data_store
        original_context_service = runtime_compat._management_ai_context_service
        store = MagicMock()
        store.get_telemetry_summary.return_value = dict(_SUMMARY)
        store.get_paper_runtime_monitoring_session.return_value = None
        store.get_rollbacks.return_value = []
        runtime_compat.data_store = store
        runtime_compat._management_ai_context_service = ManagementService(read_store=store)
        try:
            row = runtime_compat._project_operator_runtime_state_row(
                {
                    "runtime_id": "rt-paper-001",
                    "binding_id": "rb-paper-001",
                    "deployment_stage": "paper",
                    "status": "active",
                    "capital_pool_id": "pool-001",
                    "artifact_id": "artifact-001",
                    "artifact_version": "1.0.0",
                    "plan_id": "plan-001",
                }
            )
        finally:
            runtime_compat.data_store = original_data_store
            runtime_compat._management_ai_context_service = original_context_service
        self.assertEqual(row["executed_trade_count"], 1)
        self.assertEqual(row["total_trades"], 1)
        self.assertEqual(row["position_count"], 1)
        self.assertEqual(row["positions"], [{"symbol": "AAPL", "quantity": 7.0}])
        self.assertEqual(row["last_fill"]["symbol"], "AAPL")

    def test_projection_handles_summary_without_trade_fields(self):
        proj = runtime_compat._project_runtime_state_telemetry_summary(
            {"window": "latest", "pnl": 0.0, "deployment_stage": "paper"}
        )
        self.assertNotIn("executed_trade_count", proj)
        self.assertEqual(proj["metrics"]["total_trades"], None)


class TestRealTradingPulseHelperPreservesTelemetryOwnerFailure(unittest.TestCase):
    """MGMT-READ-001 sixth review: exercises the real
    _mgmt_nl_trading_pulse_snippet/_mgmt_nl_scoped_runtime_rows/
    _project_operator_runtime_state_row helper chain (not stubbed) so a
    telemetry owner that explicitly reports itself unavailable is never
    masked by a healthy runtime binding, whether or not the underlying
    telemetry read raises."""

    def _run(self, raises: bool):
        binding = dict(
            runtime_id="r1",
            tenant_id="tenant-a",
            owner="runtime-owner",
            status="ok",
            source_kind="live",
            source_version="rv1",
        )
        telemetry = dict(
            runtime_id="r1",
            owner="telemetry-owner",
            status="unavailable",
            source_kind="unavailable",
            source_version="tv1",
            correlation_id="telemetry-correlation",
            observed_at=None,
            degradation_reason="telemetry owner offline",
        )

        def read_telemetry(_runtime_id):
            if raises:
                raise RuntimeError("telemetry owner offline")
            return telemetry

        from types import SimpleNamespace

        store = SimpleNamespace(
            list_runtime_bindings=lambda: [binding],
            get_telemetry_summary=read_telemetry,
            get_paper_runtime_monitoring_session=lambda **kwargs: None,
            get_rollbacks=lambda _runtime_id: [],
        )
        original_data_store = runtime_compat.data_store
        original_context_service = runtime_compat._management_ai_context_service
        runtime_compat.data_store = store
        runtime_compat._management_ai_context_service = ManagementService(read_store=store)
        try:
            return runtime_compat._mgmt_nl_collect_context(
                "trading_pulse", "2026-09-08T18:00:00Z", "tenant-a"
            )
        finally:
            runtime_compat.data_store = original_data_store
            runtime_compat._management_ai_context_service = original_context_service

    def test_non_raising_failed_telemetry_owner_is_preserved(self):
        import json

        result = self._run(raises=False)
        surface = result["surfaces"]["management_trading_pulse"]
        self.assertNotEqual(surface["status"], "ok", surface)
        encoded = json.dumps(surface)
        self.assertIn("runtime-owner", encoded, surface)
        self.assertIn("telemetry owner offline", encoded, surface)
        self.assertIn("telemetry-correlation", encoded, surface)

    def test_raising_telemetry_read_does_not_discard_runtime_observation(self):
        import json

        result = self._run(raises=True)
        surface = result["surfaces"]["management_trading_pulse"]
        self.assertNotEqual(surface["status"], "ok", surface)
        encoded = json.dumps(surface)
        self.assertIn("runtime-owner", encoded, surface)
        self.assertIn("telemetry owner offline", encoded, surface)


class TestAuxiliaryOwnerReadFailurePreservesCollectedObservations(unittest.TestCase):
    """MGMT-READ-001 seventh review: a bare
    read_store.get_paper_runtime_monitoring_session/get_rollbacks raise
    inside _project_operator_runtime_state_row previously propagated past
    _mgmt_nl_trading_pulse_snippet/_mgmt_nl_collect_context and discarded
    every already-collected owner (runtime, telemetry), returning a bare
    status=unavailable/source=error with no owner or reason. Both reads now
    go through typed accessors that never raise."""

    def _run(self, method: str):
        binding = dict(
            runtime_id="r1",
            tenant_id="tenant-a",
            owner="runtime-owner",
            status="ok",
            source_kind="live",
            source_version="rv1",
        )
        telemetry = dict(
            runtime_id="r1",
            owner="telemetry-owner",
            status="ok",
            source_kind="live",
            source_version="tv1",
        )

        def fail(*_args, **_kwargs):
            raise RuntimeError(method + " owner offline")

        from types import SimpleNamespace

        kwargs = {
            "list_runtime_bindings": lambda: [binding],
            "get_telemetry_summary": lambda _runtime_id: telemetry,
            "get_paper_runtime_monitoring_session": lambda **_kwargs: None,
            "get_rollbacks": lambda _runtime_id: [],
        }
        kwargs[method] = fail
        store = SimpleNamespace(**kwargs)
        original_data_store = runtime_compat.data_store
        original_context_service = runtime_compat._management_ai_context_service
        runtime_compat.data_store = store
        runtime_compat._management_ai_context_service = ManagementService(read_store=store)
        try:
            return runtime_compat._mgmt_nl_collect_context(
                "trading_pulse", "2026-09-08T18:00:00Z", "tenant-a"
            )
        finally:
            runtime_compat.data_store = original_data_store
            runtime_compat._management_ai_context_service = original_context_service

    def test_monitoring_session_failure_preserves_collected_observations(self):
        import json

        result = self._run("get_paper_runtime_monitoring_session")
        surface = result["surfaces"]["management_trading_pulse"]
        encoded = json.dumps(surface)
        self.assertIn("runtime-owner", encoded, surface)
        self.assertIn("telemetry-owner", encoded, surface)
        self.assertIn("get_paper_runtime_monitoring_session owner offline", encoded, surface)

    def test_rollbacks_failure_preserves_collected_observations(self):
        import json

        result = self._run("get_rollbacks")
        surface = result["surfaces"]["management_trading_pulse"]
        encoded = json.dumps(surface)
        self.assertIn("runtime-owner", encoded, surface)
        self.assertIn("telemetry-owner", encoded, surface)
        self.assertIn("get_rollbacks owner offline", encoded, surface)


if __name__ == "__main__":
    unittest.main()
