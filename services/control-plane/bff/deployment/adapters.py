"""Consuming-domain-owned adapters implementing Deployment ports."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, Sequence

from services.control_plane.bff.command_adapters.deployment_adapter import DeploymentCommandAdapter
from services.control_plane.bff.ports import ReadSurfacePorts
from .ports import DeploymentCommands, DeploymentQueries


class DeploymentReadSurfaceAdapter:
    """Consuming-domain-owned read adapter implementing DeploymentQueries via ReadSurfacePorts."""

    def __init__(self, read_surface: ReadSurfacePorts) -> None:
        if read_surface is None or not isinstance(read_surface, ReadSurfacePorts):
            raise TypeError(
                f"read_surface must implement ReadSurfacePorts, got {type(read_surface)}"
            )
        self._read_surface = read_surface

    def list_registry_entries(self) -> Sequence[Dict[str, Any]]:
        return self._read_surface.list_registry_entries()

    def get_binding(self, binding_id: str) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_binding(binding_id)

    def list_deployment_plans(self, *args: Any, **kwargs: Any) -> Sequence[Dict[str, Any]]:
        return self._read_surface.list_deployment_plans(*args, **kwargs)

    def get_deployment_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_deployment_plan(plan_id)

    def get_approval_decision(self, decision_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_approval_decision(decision_id)

    def get_review_summary(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_review_summary(plan_id)

    def get_allowed_actions(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_allowed_actions(plan_id)

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_capital_pool(pool_id)

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> Sequence[Dict[str, Any]]:
        return self._read_surface.get_bindings_for_pool(pool_id)

    def get_runtime_binding(self, runtime_binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_runtime_binding(runtime_binding_id)

    def list_runtime_bindings(self) -> Sequence[Dict[str, Any]]:
        return self._read_surface.list_runtime_bindings()

    def get_rollbacks(self, runtime_id: Optional[str]) -> Sequence[Dict[str, Any]]:
        return self._read_surface.get_rollbacks(runtime_id)

    def get_latest_run(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_latest_run(plan_id)

    def get_deployment_diff(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_deployment_diff(plan_id)

    def dataset_source(self, dataset: str) -> str:
        return self._read_surface.dataset_source(dataset)

    def get_paper_runtime_monitoring_session(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_paper_runtime_monitoring_session(*args, **kwargs)

    def get_telemetry_summary(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._read_surface.get_telemetry_summary(runtime_id)


class DefaultDeploymentCommands:
    """Default deployment command implementation handling deployment mutations via canonical write owner."""

    def __init__(self, write_owner: Optional[DeploymentCommandAdapter] = None) -> None:
        resolved = write_owner or DeploymentCommandAdapter()
        if not (isinstance(resolved, DeploymentCommandAdapter) or hasattr(resolved, "execute")):
            raise TypeError(
                f"write_owner must be a DeploymentCommandAdapter, got {type(resolved)}"
            )
        self._write_owner = resolved

    def create_deployment_plan(self, **kwargs: Any) -> Dict[str, Any]:
        command_id = f"cmd-{uuid.uuid4().hex[:12]}"
        receipt = self._write_owner.execute(
            command_id=command_id,
            command_type="CreateDeployment",
            params=dict(kwargs),
        )
        record = dict(kwargs)
        if isinstance(receipt, dict):
            if receipt.get("entity_id"):
                record["plan_id"] = receipt["entity_id"]
                record["id"] = receipt["entity_id"]
            if receipt.get("status"):
                record["status"] = receipt["status"]
            receipt_body = receipt.get("domain_receipt")
            if isinstance(receipt_body, dict):
                record.update({k: v for k, v in receipt_body.items() if k not in record})
        return record


__all__ = [
    "DeploymentReadSurfaceAdapter",
    "DefaultDeploymentCommands",
]
