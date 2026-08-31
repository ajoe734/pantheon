"""Deployment domain support for the Deployment BFF router.

Encapsulates:
- PKT-001 operator deployment-plan list filtering/projection helpers
- Deployment stage-truth composition (approval / plan / saga / binding / runtime_fleet)
- Deployment diff default/unavailable payload shaping

The service keeps deployment-specific read-store access and pure projection
logic behind explicit injected dependencies. It deliberately does not import
the BFF composition root.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence


class DeploymentService:
    """Shared deployment-domain dependencies and projection helpers."""

    pkt001_deployment_plan_filter_statuses = frozenset({"pending_review", "approved", "rejected"})

    deployment_stage_truth_order = (
        "approval",
        "plan",
        "saga",
        "binding",
        "runtime_fleet",
    )
    deployment_stage_failure_statuses = frozenset({
        "aborted",
        "blocked",
        "dead_lettered",
        "degraded",
        "failed",
        "missing",
        "rejected",
        "stale",
        "unavailable",
    })

    deployment_diff_categories = (
        "parameters",
        "bindings",
        "capital_allocation",
        "risk_controls",
        "stage_transition",
    )

    def __init__(
        self,
        *,
        get_read_store: Callable[[], Any],
        bff_error: Callable[..., Exception],
        dataset_surface_status: Callable[..., Dict[str, Any]],
        composed_surface_status: Callable[..., Dict[str, Any]],
        aggregate_group_surface: Callable[..., Dict[str, Any]],
        split_csv_query: Callable[[Optional[str]], Optional[list]],
        snapshot_meta: Callable[[str], Dict[str, Any]],
    ) -> None:
        self._get_read_store = get_read_store
        self._bff_error = bff_error
        self._dataset_surface_status = dataset_surface_status
        self._composed_surface_status = composed_surface_status
        self._aggregate_group_surface = aggregate_group_surface
        self._split_csv_query = split_csv_query
        self._snapshot_meta = snapshot_meta

    @property
    def read_store(self) -> Any:
        """Resolve the current store per request, including test substitutions."""
        return self._get_read_store()

    # -- PKT-001: operator deployment-plans list ---------------------------- #

    def pkt001_requested_plan_statuses(self, status: Optional[str]) -> Optional[set]:
        from models import ErrorCode

        requested = self._split_csv_query(status)
        if requested is None:
            return None
        normalized = {token.lower() for token in requested}
        invalid = normalized - self.pkt001_deployment_plan_filter_statuses
        if invalid:
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid deployment plan status filter",
                f"status must be one of {sorted(self.pkt001_deployment_plan_filter_statuses)}",
                precondition_failed="status",
            )
        return normalized

    @staticmethod
    def pkt001_governance_outcome(
        plan: Dict[str, Any],
        approval_decision: Optional[Dict[str, Any]],
        review: Optional[Dict[str, Any]],
    ) -> str:
        raw_value = str(
            (review or {}).get("governanceOutcome")
            or (approval_decision or {}).get("outcome")
            or plan.get("status")
            or ""
        ).strip().lower()
        if raw_value in {"", "pending_review", "under_review", "in_review"}:
            return "pending"
        if raw_value in {"approve", "approved_with_conditions"}:
            return "approved"
        if raw_value in {"reject"}:
            return "rejected"
        return raw_value

    def pkt001_plan_filter_status(
        self,
        plan: Dict[str, Any],
        approval_decision: Optional[Dict[str, Any]],
        review: Optional[Dict[str, Any]],
    ) -> str:
        governance_outcome = self.pkt001_governance_outcome(plan, approval_decision, review)
        if governance_outcome == "approved":
            return "approved"
        if governance_outcome == "rejected":
            return "rejected"
        return "pending_review"

    def pkt001_plan_list_item(
        self,
        plan: Dict[str, Any],
        approval_decision: Optional[Dict[str, Any]],
        review: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "plan_id": plan.get("plan_id") or plan.get("id"),
            "artifact_id": plan.get("artifact_id"),
            "target_stage": plan.get("target_stage") or plan.get("stage"),
            "risk_level": (approval_decision or {}).get("risk_level"),
            "governance_outcome": self.pkt001_governance_outcome(plan, approval_decision, review),
            "submitted_at": (
                plan.get("submitted_at")
                or plan.get("created_at")
                or (approval_decision or {}).get("decided_at")
            ),
        }

    @staticmethod
    def pkt001_allowed_actions_present(allowed_actions: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(allowed_actions, dict):
            return False
        required_fields = ("canApprove", "canReject", "canPromoteToPaper")
        return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)

    def pkt001_degradation_meta(self, surfaces: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        from main import _surface_degradation_reason  # local import to avoid composition-root cycle

        reason_templates = {
            "deployment_plans": (
                "Deployment plan list is degraded and may be stale.",
                "Deployment plan list is currently unavailable.",
            ),
            "deployment_plan": (
                "Deployment plan detail is degraded and may be stale.",
                "Deployment plan detail is currently unavailable.",
            ),
            "approval_decision": (
                "Approval decision detail is degraded and may be stale.",
                "Approval decision detail is currently unavailable.",
            ),
            "capital_pool": (
                "Capital pool detail is degraded and may be stale.",
                "Capital pool detail is currently unavailable.",
            ),
            "bindings": (
                "Binding detail is degraded and may be stale.",
                "Binding detail is currently unavailable.",
            ),
            "runtime_binding": (
                "Runtime binding detail is degraded and may be stale.",
                "Runtime binding detail is currently unavailable.",
            ),
            "allowedActions": (
                "Action authority is degraded. All CTAs disabled for safety.",
                "Action authority service is unavailable. All CTAs disabled for safety.",
            ),
            "latestRun": (
                "Latest run progress is degraded and may be stale.",
                "Latest run progress is currently unavailable.",
            ),
            "review": (
                "Review summary is degraded and may be stale.",
                "Review summary is currently unavailable.",
            ),
        }
        degradation: Dict[str, Any] = {}
        for surface_name, surface in surfaces.items():
            templates = reason_templates.get(surface_name)
            if not templates:
                continue
            reason = _surface_degradation_reason(
                surface,
                degraded_reason=templates[0],
                unavailable_reason=templates[1],
            )
            if reason is not None:
                degradation[f"{surface_name}_reason"] = reason
        if degradation and "allowedActions" in surfaces:
            degradation["disable_ctas"] = surfaces["allowedActions"].get("status") != "ok"
        return degradation

    # -- Deployment stage truth ---------------------------------------------- #

    @staticmethod
    def deployment_stage_status(value: Any, *, default: str = "unknown") -> str:
        status = str(value or "").strip().lower()
        return status or default

    def deployment_stage_entry(
        self,
        *,
        stage: str,
        status: Any,
        source_dataset: str,
        source_id: Optional[str] = None,
        available: bool = True,
        message: Optional[str] = None,
        failure: Optional[bool] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        normalized_status = self.deployment_stage_status(status)
        entry: Dict[str, Any] = {
            "stage": stage,
            "status": normalized_status,
            "source_dataset": source_dataset,
            "available": bool(available),
            "failure": bool(
                normalized_status in self.deployment_stage_failure_statuses
                if failure is None
                else failure
            ),
        }
        if source_id:
            entry["source_id"] = source_id
        if message:
            entry["message"] = message
        for key, value in extra.items():
            if value is not None:
                entry[key] = value
        return entry

    @staticmethod
    def deployment_plan_identifier(plan: Dict[str, Any]) -> str:
        return str(plan.get("plan_id") or plan.get("id") or "").strip()

    def runtime_binding_matches_deployment_plan(
        self,
        runtime_binding: Dict[str, Any],
        plan: Dict[str, Any],
    ) -> bool:
        plan_id = self.deployment_plan_identifier(plan)
        runtime_plan_id = str(
            runtime_binding.get("plan_id") or runtime_binding.get("deployment_plan_id") or ""
        ).strip()
        if plan_id and runtime_plan_id == plan_id:
            return True

        requested_binding_ids = {
            str(plan.get("runtime_binding_id") or "").strip(),
            str(plan.get("binding_id") or plan.get("persona_capital_binding_id") or "").strip(),
        }
        requested_binding_ids.update(
            str(value).strip()
            for value in (plan.get("binding_ids") or [])
            if str(value).strip()
        )
        requested_binding_ids.discard("")
        runtime_binding_ids = {
            str(runtime_binding.get("id") or "").strip(),
            str(runtime_binding.get("binding_id") or "").strip(),
            str(runtime_binding.get("runtime_binding_id") or "").strip(),
            str(runtime_binding.get("persona_capital_binding_id") or "").strip(),
        }
        runtime_binding_ids.discard("")
        return bool(requested_binding_ids.intersection(runtime_binding_ids))

    def deployment_runtime_binding(self, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        runtime_binding_id = str(plan.get("runtime_binding_id") or "").strip()
        if runtime_binding_id:
            binding = self.read_store.get_runtime_binding(runtime_binding_id)
            if binding:
                return binding

        for binding in self.read_store.list_runtime_bindings():
            if self.runtime_binding_matches_deployment_plan(binding, plan):
                return binding
        return None

    def runtime_fleet_stage_truth(self, runtime_binding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not runtime_binding:
            return self.deployment_stage_entry(
                stage="runtime_fleet",
                status="unavailable",
                source_dataset="runtime_bindings",
                available=False,
                message=(
                    "Runtime fleet evidence unavailable because no RuntimeBinding "
                    "projection is linked to this deployment plan."
                ),
            )

        runtime_id = str(
            runtime_binding.get("runtime_id")
            or runtime_binding.get("id")
            or runtime_binding.get("binding_id")
            or ""
        ).strip()
        runtime_binding_id = str(
            runtime_binding.get("runtime_binding_id")
            or runtime_binding.get("binding_id")
            or runtime_binding.get("id")
            or ""
        ).strip()
        deployment_stage = str(
            runtime_binding.get("deployment_stage") or runtime_binding.get("deployment_mode") or ""
        ).strip().lower()

        monitoring = self.read_store.get_paper_runtime_monitoring_session(
            runtime_id=runtime_id,
            binding_id=runtime_binding_id,
        )
        if monitoring:
            active = bool(monitoring.get("active", True))
            staleness = monitoring.get("staleness") if isinstance(monitoring.get("staleness"), dict) else {}
            terminal_reason = (
                monitoring.get("terminal_reason")
                or monitoring.get("ended_reason")
                or staleness.get("reason")
            )
            status = "active" if active and not terminal_reason else "degraded"
            return self.deployment_stage_entry(
                stage="runtime_fleet",
                status=status,
                source_dataset="paper_runtime_monitoring_sessions",
                source_id=str(monitoring.get("session_id") or monitoring.get("id") or ""),
                available=True,
                failure=status != "active",
                runtime_id=runtime_id,
                runtime_binding_id=runtime_binding_id,
                deployment_stage=deployment_stage or None,
                active=active,
                last_heartbeat_at=monitoring.get("last_heartbeat_at"),
                terminal_reason=terminal_reason,
            )

        telemetry = self.read_store.get_telemetry_summary(runtime_id) if runtime_id else None
        if telemetry:
            health_summary = telemetry.get("health_summary") if isinstance(telemetry.get("health_summary"), dict) else {}
            unhealthy = [
                str(key)
                for key, value in health_summary.items()
                if str(value).strip().lower() not in {"", "ok", "not_applicable"}
            ]
            status = "degraded" if unhealthy else "observed"
            return self.deployment_stage_entry(
                stage="runtime_fleet",
                status=status,
                source_dataset="telemetry_summaries",
                source_id=runtime_id,
                available=True,
                failure=status == "degraded",
                runtime_id=runtime_id,
                runtime_binding_id=runtime_binding_id,
                deployment_stage=deployment_stage or None,
                last_heartbeat_at=telemetry.get("last_heartbeat_at"),
                last_event_at=telemetry.get("last_event_at"),
                degraded_checks=unhealthy or None,
            )

        source_dataset = (
            "paper_runtime_monitoring_sessions"
            if deployment_stage == "paper"
            else "telemetry_summaries"
        )
        return self.deployment_stage_entry(
            stage="runtime_fleet",
            status="unavailable",
            source_dataset=source_dataset,
            source_id=runtime_id or None,
            available=False,
            failure=True,
            runtime_id=runtime_id or None,
            runtime_binding_id=runtime_binding_id or None,
            deployment_stage=deployment_stage or None,
            message=(
                "Runtime fleet evidence unavailable; status is not inferred from "
                "deployment plan metadata or RuntimeBinding existence."
            ),
        )

    def deployment_stage_truth(self, plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        plan_id = self.deployment_plan_identifier(plan)
        approval_decision_id = str(plan.get("approval_decision_id") or "").strip()
        approval_decision = self.read_store.get_approval_decision(approval_decision_id)
        if approval_decision:
            approval_status = approval_decision.get("outcome") or approval_decision.get("state")
            approval_entry = self.deployment_stage_entry(
                stage="approval",
                status=approval_status,
                source_dataset="approval_decisions",
                source_id=str(
                    approval_decision.get("decision_id")
                    or approval_decision.get("id")
                    or approval_decision_id
                ),
                available=True,
                failure=self.deployment_stage_status(approval_status) in {"rejected", "failed"},
                decision_state=approval_decision.get("state"),
                reviewer=approval_decision.get("reviewer"),
            )
        else:
            plan_status = self.deployment_stage_status(plan.get("status"))
            pending_status = "pending" if plan_status in {"pending_approval", "draft", "proposed"} else "missing"
            approval_entry = self.deployment_stage_entry(
                stage="approval",
                status=pending_status,
                source_dataset="approval_decisions",
                source_id=approval_decision_id or None,
                available=False,
                failure=False,
                message="Approval decision has not been recorded for this deployment plan.",
            )

        plan_status = plan.get("status") or plan.get("state") or "unknown"
        plan_entry = self.deployment_stage_entry(
            stage="plan",
            status=plan_status,
            source_dataset="deployment_plans",
            source_id=plan_id or None,
            available=True,
            failure=self.deployment_stage_status(plan_status) in {"aborted", "failed", "rejected"},
            current_stage=plan.get("current_stage"),
            target_stage=plan.get("target_stage") or plan.get("stage"),
            transition_type=plan.get("transition_type"),
        )

        saga_progress = plan.get("saga_progress") if isinstance(plan.get("saga_progress"), dict) else {}
        if saga_progress:
            saga_status = saga_progress.get("progress_status") or saga_progress.get("saga_status")
            saga_entry = self.deployment_stage_entry(
                stage="saga",
                status=saga_status,
                source_dataset="deployment_sagas",
                source_id=str(saga_progress.get("saga_id") or plan.get("deployment_saga_id") or ""),
                available=True,
                failure=self.deployment_stage_status(saga_status) in {"blocked", "failed"},
                saga_status=saga_progress.get("saga_status"),
                current_step=saga_progress.get("current_step"),
                blocked_reason=saga_progress.get("blocked_reason"),
                dlq_count=saga_progress.get("dlq_count"),
                pending_event_count=saga_progress.get("pending_event_count"),
            )
        else:
            saga_entry = self.deployment_stage_entry(
                stage="saga",
                status="not_started",
                source_dataset="deployment_sagas",
                source_id=str(plan.get("deployment_saga_id") or "") or None,
                available=False,
                failure=False,
                message="Deployment saga progress has not been observed for this plan.",
            )

        runtime_binding = self.deployment_runtime_binding(plan)
        if runtime_binding:
            binding_status = runtime_binding.get("status") or runtime_binding.get("state") or "present"
            binding_entry = self.deployment_stage_entry(
                stage="binding",
                status=binding_status,
                source_dataset="runtime_bindings",
                source_id=str(
                    runtime_binding.get("runtime_binding_id")
                    or runtime_binding.get("binding_id")
                    or runtime_binding.get("id")
                    or ""
                ),
                available=True,
                failure=self.deployment_stage_status(binding_status) in {"failed", "rejected", "stopped"},
                runtime_id=runtime_binding.get("runtime_id"),
                deployment_stage=runtime_binding.get("deployment_stage") or runtime_binding.get("deployment_mode"),
                artifact_id=runtime_binding.get("artifact_id"),
                artifact_version=runtime_binding.get("artifact_version"),
            )
        else:
            binding_entry = self.deployment_stage_entry(
                stage="binding",
                status="missing",
                source_dataset="runtime_bindings",
                available=False,
                failure=False,
                message="RuntimeBinding projection is not available for this deployment plan.",
            )

        return {
            "approval": approval_entry,
            "plan": plan_entry,
            "saga": saga_entry,
            "binding": binding_entry,
            "runtime_fleet": self.runtime_fleet_stage_truth(runtime_binding),
        }

    def deployment_stage_truth_surfaces(
        self,
        stage_truth: Dict[str, Dict[str, Any]],
        *,
        snapshot_at: str,
    ) -> Dict[str, Dict[str, Any]]:
        surfaces: Dict[str, Dict[str, Any]] = {}
        for stage in self.deployment_stage_truth_order:
            entry = stage_truth.get(stage) or {}
            dataset = str(entry.get("source_dataset") or "").strip() or "deployment_plans"
            surface = self._dataset_surface_status(
                dataset,
                snapshot_at=snapshot_at,
                has_data=bool(entry.get("available")),
                missing_message=entry.get("message"),
            )
            if entry.get("failure") and surface.get("status") == "ok":
                surface["status"] = "degraded"
                surface["message"] = entry.get("message") or f"{stage} stage requires operator attention."
            surfaces[f"{stage}_stage"] = surface

        surfaces["deployment_stage_truth"] = self._aggregate_group_surface(
            "deployment_stage_truth",
            [surfaces[f"{stage}_stage"] for stage in self.deployment_stage_truth_order],
            snapshot_at=snapshot_at,
            unavailable_message="Deployment stage truth is unavailable.",
            degraded_message="Deployment stage truth is degraded because one or more stages need evidence or attention.",
        )
        return surfaces

    def deployment_stage_truth_collection_surfaces(
        self,
        stage_truths: Sequence[Dict[str, Dict[str, Any]]],
        *,
        snapshot_at: str,
    ) -> Dict[str, Dict[str, Any]]:
        collected = [
            self.deployment_stage_truth_surfaces(stage_truth, snapshot_at=snapshot_at)
            for stage_truth in stage_truths
            if stage_truth
        ]
        if not collected:
            return {}
        if len(collected) == 1:
            return collected[0]

        surfaces: Dict[str, Dict[str, Any]] = {}
        for stage in self.deployment_stage_truth_order:
            surface_key = f"{stage}_stage"
            stage_label = stage.replace("_", " ")
            surfaces[surface_key] = self._aggregate_group_surface(
                surface_key,
                [item[surface_key] for item in collected],
                snapshot_at=snapshot_at,
                unavailable_message=(
                    f"{stage_label} stage truth is unavailable across listed deployment plans."
                ),
                degraded_message=(
                    f"{stage_label} stage truth is degraded for one or more listed deployment plans."
                ),
            )

        surfaces["deployment_stage_truth"] = self._aggregate_group_surface(
            "deployment_stage_truth",
            [surfaces[f"{stage}_stage"] for stage in self.deployment_stage_truth_order],
            snapshot_at=snapshot_at,
            unavailable_message="Deployment stage truth is unavailable.",
            degraded_message=(
                "Deployment stage truth is degraded because one or more stages need "
                "evidence or attention."
            ),
        )
        return surfaces

    def deployment_plan_with_stage_truth(
        self,
        plan: Dict[str, Any],
        *,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        payload = dict(plan)
        payload["stage_truth"] = self.deployment_stage_truth(plan)
        return payload

    # -- Deployment diff ------------------------------------------------------ #

    def default_deployment_diff_summary(self) -> Dict[str, Any]:
        return {
            "total_changes": 0,
            "by_category": {
                category: {"count": 0, "highest_risk_tier": None}
                for category in self.deployment_diff_categories
            },
        }

    @staticmethod
    def deployment_diff_allowed_actions_present(payload: Dict[str, Any]) -> bool:
        allowed_actions = payload.get("allowedActions")
        if not isinstance(allowed_actions, dict):
            return False
        required_fields = ("canProceedToApproval", "canEscalateDiff")
        return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)

    def unavailable_deployment_diff_payload(self, plan_id: str, snapshot_at: str) -> Dict[str, Any]:
        deployment_diff_surface = self._dataset_surface_status(
            "deployment_diffs",
            snapshot_at=snapshot_at,
            has_data=False,
            missing_message="Deployment diff unavailable for this plan.",
        )
        allowed_actions_surface = self._composed_surface_status(
            snapshot_at=snapshot_at,
            available=False,
            missing_message="Deployment diff authority unavailable.",
        )
        allowed_actions_surface["status"] = deployment_diff_surface.get("status")
        meta = self._snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "deployment_diff": deployment_diff_surface,
            "allowedActions": allowed_actions_surface,
        }
        return {
            "plan_id": plan_id,
            "artifact_id": None,
            "stage": None,
            "submitted_at": None,
            "submitted_by": None,
            "previous_plan_id": None,
            "first_deployment": False,
            "changes": [],
            "change_summary": self.default_deployment_diff_summary(),
            "allowedActions": {
                "canProceedToApproval": False,
                "canEscalateDiff": False,
            },
            "meta": meta,
        }
