"""Thin StrategyArtifact-to-RuntimeBinding promotion orchestration.

This module deliberately owns no persistence.  It composes the canonical
registry, governance, deployment, runtime-manager, and (optionally) paper
fleet HTTP APIs and returns a redacted, JSON-serialisable receipt.  In
particular, promotion uses the runtime-manager's canonical replace route;
``/api/rollback`` is reserved for the explicit rollback operation.

The pipeline is synchronous because each write is followed by an authoritative
readback before the next write is allowed.  ``PromotePipeline`` instances are
not intended to be shared concurrently.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote


_CORE_SERVICES = ("registry", "governance", "deployment", "runtime_manager")
_RUNNING_FLEET_STATUSES = {"running", "active", "ready"}


class PromotePipelineError(RuntimeError):
    """Fail-closed error raised for transport, contract, or readback mismatch."""

    def __init__(
        self,
        message: str,
        *,
        calls: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.calls = tuple(dict(call) for call in (calls or ()))


@dataclass(frozen=True)
class ServiceResponse:
    """Transport-neutral service response."""

    status_code: int
    body: Any

    @property
    def payload(self) -> Any:
        return self.body

    def json(self) -> Any:
        return self.body


class ServiceTransport(Protocol):
    """Minimal injectable service transport used by :class:`PromotePipeline`."""

    def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> ServiceResponse:
        ...

    def is_configured(self, service: str) -> bool:
        ...


class HttpxServiceTransport:
    """``httpx`` implementation of the generic service transport.

    Runtime bearer and MFA material is attached only when ``service`` is
    ``runtime_manager``.  It is held by this transport and is never returned to
    the pipeline, which keeps it out of receipts and exception call summaries.
    """

    def __init__(
        self,
        base_urls: Mapping[str, str],
        *,
        deployment_bearer_token: str | None = None,
        deployment_tenant_id: str | None = None,
        runtime_bearer_token: str | None = None,
        runtime_mfa_token: str | None = None,
        timeout_seconds: float = 10.0,
        client: Any | None = None,
    ) -> None:
        normalized: dict[str, str] = {}
        for name, raw_url in base_urls.items():
            url = str(raw_url or "").strip().rstrip("/")
            if url:
                normalized[str(name)] = url
        self.base_urls = normalized
        self._deployment_bearer_token = str(
            deployment_bearer_token or ""
        ).strip()
        self._deployment_tenant_id = str(deployment_tenant_id or "").strip()
        self._runtime_bearer_token = str(runtime_bearer_token or "").strip()
        self._runtime_mfa_token = str(runtime_mfa_token or "").strip()
        self._owns_client = client is None
        if client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - deployment image includes httpx
                raise PromotePipelineError(
                    "HttpxServiceTransport requires the 'httpx' package"
                ) from exc
            client = httpx.Client(timeout=timeout_seconds)
        self._client = client

    @classmethod
    def from_env(
        cls,
        *,
        timeout_seconds: float = 10.0,
        client: Any | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "HttpxServiceTransport":
        env = os.environ if environ is None else environ

        def first(*names: str) -> str:
            for name in names:
                value = str(env.get(name) or "").strip()
                if value:
                    return value
            return ""

        base_urls = {
            "registry": first("PANTHEON_REGISTRY_URL", "PANTHEON_REGISTRY_API_URL"),
            "governance": first(
                "PANTHEON_GOVERNANCE_URL", "PANTHEON_GOVERNANCE_API_URL"
            ),
            "deployment": first(
                "PANTHEON_DEPLOYMENT_URL", "PANTHEON_DEPLOYMENT_API_URL"
            ),
            "runtime_manager": first(
                "PANTHEON_RUNTIME_MANAGER_URL", "PANTHEON_INTERNAL_API_URL"
            ),
            "fleet": first(
                "PANTHEON_PAPER_FLEET_RECONCILER_URL", "PANTHEON_FLEET_URL"
            ),
        }
        return cls(
            base_urls,
            deployment_bearer_token=first(
                "PANTHEON_DEPLOYMENT_SERVICE_TOKEN",
                "PANTHEON_DEPLOYMENT_AUTH_TOKEN",
            ),
            deployment_tenant_id=first("PANTHEON_DEPLOYMENT_TENANT_ID"),
            runtime_bearer_token=first(
                "PANTHEON_RUNTIME_MANAGER_TOKEN", "PANTHEON_RUNTIME_AUTH_TOKEN"
            ),
            runtime_mfa_token=first("PANTHEON_RUNTIME_MFA_TOKEN"),
            timeout_seconds=timeout_seconds,
            client=client,
        )

    def is_configured(self, service: str) -> bool:
        return bool(self.base_urls.get(service))

    def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> ServiceResponse:
        base_url = self.base_urls.get(service)
        if not base_url:
            raise PromotePipelineError(f"Service {service!r} has no configured base URL")

        headers: dict[str, str] = {"Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if service == "deployment":
            if not self._deployment_bearer_token:
                raise PromotePipelineError(
                    "PANTHEON_DEPLOYMENT_SERVICE_TOKEN is required for deployment API calls"
                )
            if not self._deployment_tenant_id:
                raise PromotePipelineError(
                    "PANTHEON_DEPLOYMENT_TENANT_ID is required for deployment API calls"
                )
            token = self._deployment_bearer_token
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )
            headers["X-Tenant-Id"] = self._deployment_tenant_id
        if service == "runtime_manager":
            if self._runtime_bearer_token:
                token = self._runtime_bearer_token
                headers["Authorization"] = (
                    token if token.lower().startswith("bearer ") else f"Bearer {token}"
                )
            if self._runtime_mfa_token:
                headers["X-MFA-Token"] = self._runtime_mfa_token

        response = self._client.request(
            method.upper(),
            f"{base_url}{path}",
            json=dict(json) if json is not None else None,
            params=dict(params) if params is not None else None,
            headers=headers,
        )
        try:
            body = response.json()
        except (TypeError, ValueError):
            body = response.text or None
        return ServiceResponse(status_code=int(response.status_code), body=body)

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> "HttpxServiceTransport":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


@dataclass(frozen=True)
class PromoteRequest:
    """Caller-owned identities needed for one fail-closed paper replacement."""

    registry_id: str
    approval_decision_id: str
    current_binding_id: str
    plan_id: str
    expected_runtime_id: str
    source_task_id: str
    created_by: str
    tenant_id: str

    def __post_init__(self) -> None:
        missing = [
            name
            for name in (
                "registry_id",
                "approval_decision_id",
                "current_binding_id",
                "plan_id",
                "expected_runtime_id",
                "source_task_id",
                "created_by",
                "tenant_id",
            )
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise PromotePipelineError(
                "PromoteRequest requires non-empty fields: " + ", ".join(missing)
            )


class PromotePipeline:
    """Orchestrate one governed paper binding replacement and its rollback."""

    def __init__(
        self,
        transport: ServiceTransport,
        *,
        fleet_enabled: bool | None = None,
        fleet_poll_timeout_seconds: float = 30.0,
        fleet_poll_interval_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if fleet_poll_timeout_seconds < 0:
            raise ValueError("fleet_poll_timeout_seconds must be non-negative")
        if fleet_poll_interval_seconds < 0:
            raise ValueError("fleet_poll_interval_seconds must be non-negative")
        self.transport = transport
        self._fleet_enabled_override = fleet_enabled
        self._fleet_poll_timeout_seconds = fleet_poll_timeout_seconds
        self._fleet_poll_interval_seconds = fleet_poll_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def promote(self, request: PromoteRequest | Mapping[str, Any]) -> dict[str, Any]:
        """Promote one approved StrategyArtifact through canonical service APIs."""
        req = request if isinstance(request, PromoteRequest) else PromoteRequest(**request)
        self._calls = []
        self._require_core_services()

        initial_view = self._get_mapping(
            "registry",
            f"/api/registry/strategy-artifacts/{_path_id(req.registry_id)}",
            label="StrategyArtifact registry view",
        )
        initial_entry, initial_artifact = self._registry_parts(
            initial_view, expected_registry_id=req.registry_id
        )
        self._validate_artifact_identity(initial_entry, initial_artifact, req)

        decision = self._get_mapping(
            "governance",
            f"/api/governance/approvals/{_path_id(req.approval_decision_id)}",
            label="ApprovalDecision",
        )
        self._validate_approval(decision, initial_entry, req)

        artifact_state = _text(initial_entry.get("artifact_state"))
        if artifact_state == "candidate":
            approver = _text(decision.get("actor_id")) or req.created_by
            self._call(
                "registry",
                "POST",
                f"/api/registry/strategy-artifacts/{_path_id(req.registry_id)}/advance",
                json_body={
                    "target_state": "approved",
                    "approver": approver,
                    "approval_decision_id": req.approval_decision_id,
                },
                expected_statuses=(200,),
            )
            approved_view = self._get_mapping(
                "registry",
                f"/api/registry/strategy-artifacts/{_path_id(req.registry_id)}",
                label="approved StrategyArtifact readback",
            )
            approved_entry, artifact = self._registry_parts(
                approved_view, expected_registry_id=req.registry_id
            )
            if _json_safe(artifact) != _json_safe(initial_artifact):
                self._fail("StrategyArtifact content changed while advancing to approved")
        elif artifact_state == "approved":
            approved_view = initial_view
            approved_entry = initial_entry
            artifact = initial_artifact
        else:
            self._fail(
                "StrategyArtifact must be candidate or approved before promotion; "
                f"got {artifact_state!r}"
            )

        self._validate_approved_registry(approved_entry, artifact, req)
        binding_intent = _required_mapping(
            artifact.get("binding_intent"), "StrategyArtifact.binding_intent", self._fail
        )

        current_response, current_status = self._call(
            "runtime_manager",
            "GET",
            f"/api/runtime-bindings/{_path_id(req.current_binding_id)}",
            expected_statuses=(200, 404),
        )
        current_binding = (
            _required_mapping(current_response, "current RuntimeBinding", self._fail)
            if current_status == 200
            else self._binding_from_intent(binding_intent)
        )

        replay_binding = self._completed_promotion_binding(
            req=req,
            entry=approved_entry,
            binding_intent=binding_intent,
            current_binding=current_binding,
            current_found=current_status == 200,
        )
        if replay_binding is not None:
            completed_plan, saga_progress = self._completed_deployment_readback(
                req=req,
                new_binding_id=_required_text(
                    replay_binding, "binding_id", "replayed RuntimeBinding", self._fail
                ),
                previous_binding=current_binding,
            )
            replay_projection = self._get_mapping(
                "registry",
                f"/api/registry/strategy-artifacts/{_path_id(req.registry_id)}",
                label="promotion replay registry projection",
            )
            self._validate_registry_projection(
                replay_projection,
                expected_stage="paper",
                expected_plan_id=req.plan_id,
                expected_binding_id=_required_text(
                    replay_binding, "binding_id", "replayed RuntimeBinding", self._fail
                ),
            )
            fleet = self._wait_for_fleet(
                new_binding_id=_required_text(
                    replay_binding, "binding_id", "replayed RuntimeBinding", self._fail
                ),
                runtime_id=req.expected_runtime_id,
                old_binding_id=req.current_binding_id,
            )
            pre = (
                self._binding_snapshot(current_binding)
                if current_status == 200
                and _text(current_binding.get("binding_id")) != _text(replay_binding.get("binding_id"))
                else None
            )
            receipt = self._promotion_receipt(
                req=req,
                entry=approved_entry,
                pre=pre or self._binding_snapshot(current_binding),
                post=self._binding_snapshot(replay_binding),
                plan=self._plan_snapshot(completed_plan),
                saga={
                    "saga_id": saga_progress.get("saga_id"),
                    "replayed": True,
                    "completed": True,
                    "binding_created_recorded": True,
                    "runtime_active_recorded": True,
                },
                fleet=fleet,
                replayed=True,
            )
            return self._finalize_receipt(receipt)

        if current_status != 200:
            self._fail(
                f"Current RuntimeBinding {req.current_binding_id!r} was not found and no "
                "completed promotion replay was identifiable"
            )
        self._validate_current_binding(current_binding, binding_intent, artifact, decision, req)

        registry_entry_payload = dict(approved_entry)
        plan_request = self._build_plan_request(
            req=req,
            registry_entry=registry_entry_payload,
            artifact=artifact,
            decision=decision,
            current_binding=current_binding,
            binding_intent=binding_intent,
        )
        plan_payload, plan_status = self._call(
            "deployment",
            "POST",
            "/api/deployment/plans",
            json_body=plan_request,
            expected_statuses=(200, 201, 409, 422),
        )
        if plan_status in {409, 422}:
            # A deterministic plan id makes retry safe.  Only an exact canonical
            # readback is accepted; an unrelated validation error remains fatal.
            plan = self._get_mapping(
                "deployment",
                f"/api/deployment/plans/{_path_id(req.plan_id)}",
                label="existing DeploymentPlan readback",
            )
        else:
            plan = self._extract_plan(plan_payload)
        self._validate_plan(plan, approved_entry, current_binding, req)

        dispatch_payload = {
            "trace_id": f"{req.source_task_id}:{req.plan_id}",
            "correlation_id": req.source_task_id,
            "idempotency_key": f"{req.source_task_id}:{req.plan_id}:dispatch",
            "actor_id": req.created_by,
            "source_task_id": req.source_task_id,
            "registry_entry": registry_entry_payload,
            "metadata": {
                "promotion_pipeline": "EVOLOOP-006",
                "tenant_id": req.tenant_id,
                "current_binding_id": req.current_binding_id,
                "expected_runtime_id": req.expected_runtime_id,
            },
        }
        dispatch = self._post_mapping(
            "deployment",
            f"/api/deployment/plans/{_path_id(req.plan_id)}/dispatch",
            dispatch_payload,
            label="deployment dispatch",
        )
        saga_id, dispatch_replayed = self._dispatch_saga(dispatch, req.plan_id)

        replace_body = self._build_replace_body(
            req=req,
            plan=plan,
            entry=approved_entry,
            artifact=artifact,
            current_binding=current_binding,
            binding_intent=binding_intent,
        )
        replace_result = self._post_mapping(
            "runtime_manager",
            f"/api/runtimes/{_path_id(req.expected_runtime_id)}/replace",
            replace_body,
            label="runtime replace result",
            expected_statuses=(200, 201, 202),
        )
        self._validate_forward_replace_result(
            replace_result,
            current_binding=current_binding,
            expected_runtime_id=req.expected_runtime_id,
        )
        new_binding_id = self._new_binding_id(replace_result)
        if new_binding_id == req.current_binding_id:
            self._fail("Runtime replace returned the existing binding id instead of a replacement")

        new_binding = self._get_mapping(
            "runtime_manager",
            f"/api/runtime-bindings/{_path_id(new_binding_id)}",
            label="new RuntimeBinding readback",
        )
        self._validate_new_binding(
            new_binding,
            expected_binding_id=new_binding_id,
            expected_artifact_id=_required_text(
                approved_entry, "registry_id", "approved registry entry", self._fail
            ),
            expected_artifact_version=_required_text(
                approved_entry, "version", "approved registry entry", self._fail
            ),
            expected_plan_id=req.plan_id,
            expected_runtime_id=req.expected_runtime_id,
            expected_pool_id=_required_text(
                current_binding, "capital_pool_id", "current RuntimeBinding", self._fail
            ),
            expected_pcb_id=_required_text(
                current_binding,
                "persona_capital_binding_id",
                "current RuntimeBinding",
                self._fail,
            ),
            expected_parent_binding_id=req.current_binding_id,
        )
        retired_binding = self._get_mapping(
            "runtime_manager",
            f"/api/runtime-bindings/{_path_id(req.current_binding_id)}",
            label="retired previous RuntimeBinding readback",
        )
        self._require_binding_equal(
            retired_binding,
            {**dict(current_binding), "status": "retired"},
            label="retired previous RuntimeBinding readback",
        )
        active_binding = self._get_active_binding(
            _required_text(
                current_binding, "capital_pool_id", "current RuntimeBinding", self._fail
            )
        )
        self._require_binding_equal(
            active_binding,
            new_binding,
            label="active pool RuntimeBinding readback",
        )

        self._post_mapping(
            "deployment",
            f"/api/deployment/sagas/{_path_id(saga_id)}/binding-created",
            {
                "binding_id": new_binding_id,
                "runtime_id": req.expected_runtime_id,
                "note": f"{req.source_task_id} canonical replace readback passed",
            },
            label="saga binding-created receipt",
        )
        fleet = self._wait_for_fleet(
            new_binding_id=new_binding_id,
            runtime_id=req.expected_runtime_id,
            old_binding_id=req.current_binding_id,
        )
        self._post_mapping(
            "deployment",
            f"/api/deployment/sagas/{_path_id(saga_id)}/runtime-active",
            {
                "binding_id": new_binding_id,
                "runtime_id": req.expected_runtime_id,
                "note": f"{req.source_task_id} active pool readback passed",
            },
            label="saga runtime-active receipt",
        )
        completed_plan, saga_progress = self._completed_deployment_readback(
            req=req,
            new_binding_id=new_binding_id,
            previous_binding=current_binding,
            expected_saga_id=saga_id,
        )

        projection = self._put_mapping(
            "registry",
            f"/api/registry/entries/{_path_id(req.registry_id)}/deployment-summary",
            {
                "current_stage": "paper",
                "deployment_plan_id": req.plan_id,
                "runtime_binding_id": new_binding_id,
            },
            label="registry deployment-summary projection",
        )
        self._validate_registry_projection(
            projection,
            expected_stage="paper",
            expected_plan_id=req.plan_id,
            expected_binding_id=new_binding_id,
        )
        receipt = self._promotion_receipt(
            req=req,
            entry=approved_entry,
            pre=self._binding_snapshot(current_binding),
            post=self._binding_snapshot(new_binding),
            plan=self._plan_snapshot(completed_plan),
            saga={
                "saga_id": saga_id,
                "dispatch_replayed": dispatch_replayed,
                "completed": True,
                "progress_status": saga_progress.get("progress_status"),
                "saga_status": saga_progress.get("saga_status"),
                "binding_created_recorded": True,
                "runtime_active_recorded": True,
            },
            fleet=fleet,
            replayed=False,
        )
        return self._finalize_receipt(receipt)

    def rollback(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Re-bind the exact pre-promotion paper state recorded in ``receipt``."""
        self._calls = []
        self._require_core_services()
        promoted_receipt = _required_mapping(receipt, "promotion receipt", self._fail)
        if _text(promoted_receipt.get("operation")) != "promote":
            self._fail("rollback requires a promote receipt")

        original_pre = _required_mapping(
            promoted_receipt.get("pre"), "promotion receipt pre-state", self._fail
        )
        promoted_post = _required_mapping(
            promoted_receipt.get("post"), "promotion receipt post-state", self._fail
        )
        registry = _required_mapping(
            promoted_receipt.get("registry"), "promotion receipt registry", self._fail
        )
        self._validate_rollback_receipt_states(original_pre, promoted_post, registry)

        original_binding_id = _required_text(
            original_pre, "binding_id", "promotion receipt pre-state", self._fail
        )
        authoritative_original = self._get_mapping(
            "runtime_manager",
            f"/api/runtime-bindings/{_path_id(original_binding_id)}",
            label="authoritative pre-promotion RuntimeBinding",
        )
        self._require_binding_equal(
            authoritative_original,
            {**dict(original_pre), "status": "retired"},
            label="authoritative pre-promotion RuntimeBinding",
        )

        promoted_binding_id = _required_text(
            promoted_post, "binding_id", "promotion receipt post-state", self._fail
        )
        pool_id = _required_text(
            original_pre, "capital_pool_id", "promotion receipt pre-state", self._fail
        )
        runtime_id = _required_text(
            original_pre, "runtime_id", "promotion receipt pre-state", self._fail
        )
        promoted_plan_id = _required_text(
            promoted_post, "plan_id", "promotion receipt post-state", self._fail
        )
        promoted_plan = self._get_mapping(
            "deployment",
            f"/api/deployment/plans/{_path_id(promoted_plan_id)}",
            label="authoritative promotion DeploymentPlan",
        )
        for key, expected in {
            "plan_id": promoted_plan_id,
            "artifact_id": _required_text(
                registry, "registry_id", "promotion receipt registry", self._fail
            ),
            "binding_id": promoted_binding_id,
            "status": "executed",
            "transition_type": "replace",
            "runtime_action": "replace_binding",
        }.items():
            self._require_value(
                promoted_plan, key, expected, "authoritative promotion DeploymentPlan"
            )
        rollback_ref = _required_mapping(
            promoted_plan.get("rollback"),
            "authoritative promotion DeploymentPlan.rollback",
            self._fail,
        )
        self._require_value(
            rollback_ref,
            "target_artifact_id",
            authoritative_original.get("artifact_id"),
            "authoritative promotion DeploymentPlan.rollback",
        )
        self._require_value(
            rollback_ref,
            "target_version",
            authoritative_original.get("artifact_version"),
            "authoritative promotion DeploymentPlan.rollback",
        )
        current_payload, current_status = self._call(
            "runtime_manager",
            "GET",
            f"/api/runtime-bindings/{_path_id(promoted_binding_id)}",
            expected_statuses=(200, 404),
        )
        current = (
            _required_mapping(current_payload, "promoted RuntimeBinding", self._fail)
            if current_status == 200
            else None
        )
        active = self._get_active_binding(pool_id)

        if current is None:
            self._fail("Promoted RuntimeBinding is missing; rollback cannot verify lineage")
        if _text(current.get("status")) != "active":
            if self._binding_matches_restored_state(active, original_pre):
                self._require_binding_equal(
                    current,
                    {**dict(promoted_post), "status": "retired"},
                    label="rollback replay promoted RuntimeBinding",
                )
                self._validate_restored_binding(
                    active,
                    expected_binding_id=_required_text(
                        active, "binding_id", "rollback replay binding", self._fail
                    ),
                    original_pre=authoritative_original,
                    rollback_parent=promoted_binding_id,
                )
                replay_projection = self._get_mapping(
                    "registry",
                    f"/api/registry/strategy-artifacts/{_path_id(_required_text(registry, 'registry_id', 'promotion receipt registry', self._fail))}",
                    label="rollback replay registry projection",
                )
                self._validate_registry_projection(
                    replay_projection,
                    expected_stage="none",
                    expected_plan_id=None,
                    expected_binding_id=None,
                )
                fleet = self._wait_for_fleet(
                    new_binding_id=_required_text(
                        active, "binding_id", "rollback replay binding", self._fail
                    ),
                    runtime_id=runtime_id,
                    old_binding_id=promoted_binding_id,
                )
                replay_receipt = self._rollback_receipt(
                    promotion_receipt=promoted_receipt,
                    pre=self._binding_snapshot(promoted_post),
                    post=self._binding_snapshot(active),
                    registry_id=_required_text(
                        registry, "registry_id", "promotion receipt registry", self._fail
                    ),
                    fleet=fleet,
                    replayed=True,
                )
                return self._finalize_receipt(replay_receipt)
            self._fail(
                "Promoted RuntimeBinding is no longer active and exact rollback replay "
                "state was not found"
            )

        self._require_binding_equal(current, promoted_post, label="rollback preflight")
        self._require_binding_equal(active, current, label="rollback active pool preflight")

        original_metadata = (
            dict(authoritative_original.get("metadata") or {})
            if isinstance(authoritative_original.get("metadata"), Mapping)
            else {}
        )
        allowed_scope = (
            _text(authoritative_original.get("allowed_deployment_scope"))
            or _text(original_metadata.get("allowed_deployment_scope"))
            or _text(authoritative_original.get("deployment_mode"))
        )
        replacement_metadata = {
            **original_metadata,
            "rollback_of_plan_id": promoted_plan_id,
            "rollback_of_binding_id": promoted_binding_id,
            "source_task_id": _text(promoted_receipt.get("source_task_id")),
        }
        rollback_body = {
            "current_binding_id": promoted_binding_id,
            "action_type": "replace",
            "replacement_plan_id": _required_text(
                authoritative_original,
                "plan_id",
                "authoritative pre-promotion RuntimeBinding",
                self._fail,
            ),
            "replacement_plan_status": "approved",
            "replacement_artifact_id": _required_text(
                authoritative_original,
                "artifact_id",
                "authoritative pre-promotion RuntimeBinding",
                self._fail,
            ),
            "replacement_artifact_version": _required_text(
                authoritative_original,
                "artifact_version",
                "authoritative pre-promotion RuntimeBinding",
                self._fail,
            ),
            "replacement_persona_capital_binding_id": _required_text(
                authoritative_original,
                "persona_capital_binding_id",
                "authoritative pre-promotion RuntimeBinding",
                self._fail,
            ),
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": allowed_scope,
            "replacement_deployment_mode": _required_text(
                authoritative_original,
                "deployment_mode",
                "authoritative pre-promotion RuntimeBinding",
                self._fail,
            ),
            "replacement_runtime_id": runtime_id,
            "loader_checks_passed": True,
            "opened_by_artifact_id": _required_text(
                authoritative_original,
                "artifact_id",
                "authoritative pre-promotion RuntimeBinding",
                self._fail,
            ),
            "replacement_metadata": replacement_metadata,
            "replacement_strategy_id": _text(
                authoritative_original.get("strategy_id")
            )
            or _text(original_metadata.get("strategy_id")),
        }
        rollback_result = self._post_mapping(
            "runtime_manager",
            "/api/rollback",
            rollback_body,
            label="runtime rollback result",
            expected_statuses=(200, 201),
        )
        restored_binding_id = self._new_binding_id(rollback_result)
        if restored_binding_id == promoted_binding_id:
            self._fail("Runtime rollback returned the promoted binding id as the replacement")

        restored = self._get_mapping(
            "runtime_manager",
            f"/api/runtime-bindings/{_path_id(restored_binding_id)}",
            label="restored RuntimeBinding readback",
        )
        self._validate_restored_binding(
            restored,
            expected_binding_id=restored_binding_id,
            original_pre=authoritative_original,
            rollback_parent=promoted_binding_id,
        )
        active_restored = self._get_active_binding(pool_id)
        self._require_binding_equal(
            active_restored, restored, label="rollback active pool readback"
        )

        fleet = self._wait_for_fleet(
            new_binding_id=restored_binding_id,
            runtime_id=runtime_id,
            old_binding_id=promoted_binding_id,
        )

        registry_id = _required_text(
            registry, "registry_id", "promotion receipt registry", self._fail
        )
        projection = self._put_mapping(
            "registry",
            f"/api/registry/entries/{_path_id(registry_id)}/deployment-summary",
            {
                "current_stage": "none",
                "deployment_plan_id": None,
                "runtime_binding_id": None,
            },
            label="rollback registry deployment-summary projection",
        )
        self._validate_registry_projection(
            projection,
            expected_stage="none",
            expected_plan_id=None,
            expected_binding_id=None,
        )
        rollback_receipt = self._rollback_receipt(
            promotion_receipt=promoted_receipt,
            pre=self._binding_snapshot(promoted_post),
            post=self._binding_snapshot(restored),
            registry_id=registry_id,
            fleet=fleet,
            replayed=False,
        )
        return self._finalize_receipt(rollback_receipt)

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    def _call(
        self,
        service: str,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        expected_statuses: Sequence[int] = (200,),
    ) -> tuple[Any, int]:
        try:
            raw_response = self.transport.request(
                service,
                method.upper(),
                path,
                json=json_body,
                params=params,
            )
            response = _normalize_response(raw_response)
        except PromotePipelineError as exc:
            self._fail(f"{service} {method.upper()} {path} failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - transport boundary
            self._fail(
                f"{service} {method.upper()} {path} transport failure: "
                f"{type(exc).__name__}: {exc}"
            )

        call = {
            "service": service,
            "method": method.upper(),
            "path": path,
            "status": response.status_code,
        }
        self._calls.append(call)
        if response.status_code not in set(expected_statuses):
            self._fail(
                f"{service} {method.upper()} {path} returned HTTP "
                f"{response.status_code}; expected {sorted(set(expected_statuses))}"
            )
        return response.body, response.status_code

    def _get_mapping(self, service: str, path: str, *, label: str) -> dict[str, Any]:
        payload, _ = self._call(service, "GET", path)
        return _required_mapping(payload, label, self._fail)

    def _post_mapping(
        self,
        service: str,
        path: str,
        body: Mapping[str, Any],
        *,
        label: str,
        expected_statuses: Sequence[int] = (200, 201),
    ) -> dict[str, Any]:
        payload, _ = self._call(
            service,
            "POST",
            path,
            json_body=body,
            expected_statuses=expected_statuses,
        )
        return _required_mapping(payload, label, self._fail)

    def _put_mapping(
        self,
        service: str,
        path: str,
        body: Mapping[str, Any],
        *,
        label: str,
    ) -> dict[str, Any]:
        payload, _ = self._call(
            service,
            "PUT",
            path,
            json_body=body,
            expected_statuses=(200,),
        )
        return _required_mapping(payload, label, self._fail)

    def _require_core_services(self) -> None:
        missing = [service for service in _CORE_SERVICES if not self._is_configured(service)]
        if missing:
            self._fail("Missing service transport configuration: " + ", ".join(missing))

    def _is_configured(self, service: str) -> bool:
        checker = getattr(self.transport, "is_configured", None)
        if callable(checker):
            try:
                return bool(checker(service))
            except Exception:  # noqa: BLE001 - fall through to common fake shapes
                pass
        for attribute in ("base_urls", "services", "configured_services"):
            value = getattr(self.transport, attribute, None)
            if isinstance(value, Mapping):
                return bool(value.get(service))
            if isinstance(value, (set, frozenset, list, tuple)):
                return service in value
        # Generic fakes often intentionally omit discovery.  Core services are
        # required by contract; fleet remains opt-in when it cannot be discovered.
        return service in _CORE_SERVICES

    def _fleet_is_configured(self) -> bool:
        if self._fleet_enabled_override is not None:
            return self._fleet_enabled_override
        return self._is_configured("fleet")

    # ------------------------------------------------------------------
    # Contract validation and request construction
    # ------------------------------------------------------------------

    def _registry_parts(
        self,
        view: Mapping[str, Any],
        *,
        expected_registry_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        entry_source = view.get("entry") if isinstance(view.get("entry"), Mapping) else view
        entry = dict(entry_source)
        if _text(entry.get("registry_id")) != expected_registry_id:
            self._fail(
                "Registry view identity mismatch: expected "
                f"{expected_registry_id!r}, got {_text(entry.get('registry_id'))!r}"
            )
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
        artifact_source = (
            view.get("strategy_artifact")
            if isinstance(view.get("strategy_artifact"), Mapping)
            else metadata.get("strategy_artifact")
        )
        artifact = _required_mapping(
            artifact_source, "registry StrategyArtifact overlay", self._fail
        )
        return entry, artifact

    def _validate_artifact_identity(
        self,
        entry: Mapping[str, Any],
        artifact: Mapping[str, Any],
        req: PromoteRequest,
    ) -> None:
        artifact_id = _required_text(artifact, "artifact_id", "StrategyArtifact", self._fail)
        version = _required_text(artifact, "version", "StrategyArtifact", self._fail)
        strategy_id = _required_text(artifact, "strategy_id", "StrategyArtifact", self._fail)
        if artifact_id != req.registry_id:
            self._fail(
                f"StrategyArtifact.artifact_id {artifact_id!r} does not match registry_id "
                f"{req.registry_id!r}"
            )
        self._require_value(entry, "version", version, "registry entry")
        self._require_value(entry, "strategy_id", strategy_id, "registry entry")
        if _text(entry.get("artifact_type")) != "execution_bundle":
            self._fail("StrategyArtifact registry entry must use artifact_type='execution_bundle'")

    def _validate_approval(
        self,
        decision: Mapping[str, Any],
        entry: Mapping[str, Any],
        req: PromoteRequest,
    ) -> None:
        self._require_value(
            decision, "decision_id", req.approval_decision_id, "ApprovalDecision"
        )
        self._require_value(
            decision, "tenant_id", req.tenant_id, "ApprovalDecision"
        )
        state = _text(decision.get("decision_state") or decision.get("state"))
        outcome = _text(decision.get("decision") or decision.get("outcome"))
        if state != "decided":
            self._fail(f"ApprovalDecision must be decided; got {state!r}")
        if outcome not in {"approved", "approved_with_conditions"}:
            self._fail(
                "ApprovalDecision must be approved or approved_with_conditions; "
                f"got {outcome!r}"
            )
        expires_at = _text(decision.get("expires_at"))
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except ValueError:
                self._fail("ApprovalDecision.expires_at is not a valid ISO-8601 timestamp")
            if expiry <= datetime.now(timezone.utc):
                self._fail("ApprovalDecision is expired")
        self._require_value(decision, "target_id", req.registry_id, "ApprovalDecision")
        self._require_value(
            decision, "target_type", "registry_entry", "ApprovalDecision"
        )
        self._require_value(
            decision,
            "target_version",
            _required_text(entry, "version", "registry entry", self._fail),
            "ApprovalDecision",
        )

    def _validate_approved_registry(
        self,
        entry: Mapping[str, Any],
        artifact: Mapping[str, Any],
        req: PromoteRequest,
    ) -> None:
        self._validate_artifact_identity(entry, artifact, req)
        if _text(entry.get("artifact_state")) != "approved":
            self._fail("StrategyArtifact approval readback did not report artifact_state='approved'")
        linked_decision = _text(entry.get("approval_decision_id"))
        if linked_decision != req.approval_decision_id:
            self._fail(
                "Registry approval_decision_id must match the governed decision: "
                f"expected {req.approval_decision_id!r}, got {linked_decision!r}"
            )

    def _binding_from_intent(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "binding_id": intent.get("observed_runtime_binding_id"),
            "runtime_id": intent.get("observed_runtime_id"),
            "plan_id": intent.get("observed_deployment_plan_id"),
            "capital_pool_id": intent.get("observed_capital_pool_id"),
            "artifact_id": intent.get("observed_placeholder_artifact_id"),
            "artifact_version": intent.get("observed_placeholder_artifact_version"),
            "status": intent.get("observed_binding_status"),
            "deployment_mode": intent.get("observed_deployment_mode"),
            "execution_mode": intent.get("observed_deployment_mode"),
            "effective_at": intent.get("observed_effective_at"),
            "persona_capital_binding_id": intent.get("persona_capital_binding_id"),
        }

    def _validate_current_binding(
        self,
        binding: Mapping[str, Any],
        intent: Mapping[str, Any],
        artifact: Mapping[str, Any],
        decision: Mapping[str, Any],
        req: PromoteRequest,
    ) -> None:
        expected = {
            "binding_id": req.current_binding_id,
            "runtime_id": req.expected_runtime_id,
            "status": "active",
            "deployment_mode": "paper",
            "execution_mode": "paper",
        }
        for key, value in expected.items():
            self._require_value(binding, key, value, "current RuntimeBinding")

        exact_intent_fields = {
            "observed_runtime_id": _required_text(
                binding, "runtime_id", "current RuntimeBinding", self._fail
            ),
            "observed_deployment_plan_id": _required_text(
                binding, "plan_id", "current RuntimeBinding", self._fail
            ),
            "observed_capital_pool_id": _required_text(
                binding, "capital_pool_id", "current RuntimeBinding", self._fail
            ),
            "observed_placeholder_artifact_id": _required_text(
                binding, "artifact_id", "current RuntimeBinding", self._fail
            ),
            "observed_placeholder_artifact_version": _required_text(
                binding, "artifact_version", "current RuntimeBinding", self._fail
            ),
            "observed_binding_status": "active",
            "observed_deployment_mode": "paper",
            "persona_capital_binding_id": _required_text(
                binding,
                "persona_capital_binding_id",
                "current RuntimeBinding",
                self._fail,
            ),
            "replacement_task_id": req.source_task_id,
        }
        for key, value in exact_intent_fields.items():
            self._require_value(intent, key, value, "StrategyArtifact.binding_intent")

        observed_binding_id = _required_text(
            intent,
            "observed_runtime_binding_id",
            "StrategyArtifact.binding_intent",
            self._fail,
        )
        if req.current_binding_id == observed_binding_id:
            effective_at = _text(binding.get("effective_at"))
            if effective_at:
                self._require_value(
                    intent,
                    "observed_effective_at",
                    effective_at,
                    "StrategyArtifact.binding_intent",
                )
        else:
            self._validate_restored_slot_lineage(
                binding=binding,
                intent=intent,
                req=req,
                observed_binding_id=observed_binding_id,
            )

        binding_strategy_id = _text(binding.get("strategy_id"))
        metadata = binding.get("metadata") if isinstance(binding.get("metadata"), Mapping) else {}
        binding_strategy_id = binding_strategy_id or _text(metadata.get("strategy_id"))
        if binding_strategy_id and binding_strategy_id != _text(artifact.get("strategy_id")):
            self._fail("Current RuntimeBinding strategy_id does not match StrategyArtifact")

        decision_pool = _text(decision.get("capital_pool_id"))
        if decision_pool and decision_pool != _text(binding.get("capital_pool_id")):
            self._fail("ApprovalDecision capital_pool_id does not match current RuntimeBinding")
        decision_persona = _text(decision.get("persona_id"))
        intent_persona = _text(intent.get("persona_id"))
        if decision_persona and intent_persona and decision_persona != intent_persona:
            self._fail("ApprovalDecision persona_id does not match StrategyArtifact binding intent")

    def _validate_restored_slot_lineage(
        self,
        *,
        binding: Mapping[str, Any],
        intent: Mapping[str, Any],
        req: PromoteRequest,
        observed_binding_id: str,
    ) -> None:
        """Allow re-promotion only from this pipeline's proven rollback child."""
        if _text(binding.get("rollback_action_type")) != "replace":
            self._fail(
                "Current binding differs from binding_intent and is not a replace rollback"
            )
        promoted_parent_id = _required_text(
            binding,
            "rollback_parent",
            "restored RuntimeBinding",
            self._fail,
        )
        promoted_parent = self._get_mapping(
            "runtime_manager",
            f"/api/runtime-bindings/{_path_id(promoted_parent_id)}",
            label="restored slot promotion parent",
        )
        for key, expected in {
            "binding_id": promoted_parent_id,
            "artifact_id": req.registry_id,
            "runtime_id": req.expected_runtime_id,
            "capital_pool_id": binding.get("capital_pool_id"),
            "persona_capital_binding_id": binding.get(
                "persona_capital_binding_id"
            ),
            "deployment_mode": "paper",
            "status": "retired",
        }.items():
            self._require_value(
                promoted_parent, key, expected, "restored slot promotion parent"
            )
        parent_metadata = _required_mapping(
            promoted_parent.get("metadata"),
            "restored slot promotion parent.metadata",
            self._fail,
        )
        self._require_value(
            parent_metadata,
            "replacement_kind",
            "forward",
            "restored slot promotion parent.metadata",
        )
        self._require_value(
            parent_metadata,
            "replacement_parent_binding_id",
            observed_binding_id,
            "restored slot promotion parent.metadata",
        )

    def _build_plan_request(
        self,
        *,
        req: PromoteRequest,
        registry_entry: Mapping[str, Any],
        artifact: Mapping[str, Any],
        decision: Mapping[str, Any],
        current_binding: Mapping[str, Any],
        binding_intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "plan_id": req.plan_id,
            "approval_decision_id": req.approval_decision_id,
            "capital_pool_id": _required_text(
                current_binding, "capital_pool_id", "current RuntimeBinding", self._fail
            ),
            "target_stage": "paper",
            "current_stage": "paper",
            "registry_id": req.registry_id,
            "registry_entry": dict(registry_entry),
            "approval_decision": dict(decision),
            "created_by": req.created_by,
            "sponsor_persona_id": _required_text(
                binding_intent, "persona_id", "StrategyArtifact.binding_intent", self._fail
            ),
            "binding_id": req.current_binding_id,
            "rollback": {
                "target_artifact_id": _required_text(
                    current_binding, "artifact_id", "current RuntimeBinding", self._fail
                ),
                "target_version": _required_text(
                    current_binding,
                    "artifact_version",
                    "current RuntimeBinding",
                    self._fail,
                ),
                "action_type": "replace",
                "reason": f"{req.source_task_id} same-stage governed replacement",
            },
            "pre_checks": [
                "approval_decided_approved",
                "binding_intent_exact",
                "runtime_id_exact",
                "current_binding_active_paper",
            ],
            "post_checks": [
                "new_binding_active",
                "active_pool_binding_exact",
                "fleet_old_binding_absent",
            ],
            "metadata": {
                "source_task_id": req.source_task_id,
                "tenant_id": req.tenant_id,
                "strategy_id": artifact.get("strategy_id"),
                "strategy_artifact_id": artifact.get("artifact_id"),
                "expected_runtime_id": req.expected_runtime_id,
                "replaces_binding_id": req.current_binding_id,
            },
            "supersedes_plan_id": current_binding.get("plan_id"),
            "status": "approved",
        }

    def _extract_plan(self, payload: Any) -> dict[str, Any]:
        mapping = _required_mapping(payload, "DeploymentPlan response", self._fail)
        nested = mapping.get("plan")
        return dict(nested) if isinstance(nested, Mapping) else mapping

    def _validate_plan(
        self,
        plan: Mapping[str, Any],
        entry: Mapping[str, Any],
        current_binding: Mapping[str, Any],
        req: PromoteRequest,
    ) -> None:
        expected = {
            "plan_id": req.plan_id,
            "approval_decision_id": req.approval_decision_id,
            "artifact_id": req.registry_id,
            "artifact_version": _required_text(entry, "version", "registry entry", self._fail),
            "strategy_id": _required_text(
                entry, "strategy_id", "registry entry", self._fail
            ),
            "capital_pool_id": _required_text(
                current_binding, "capital_pool_id", "current RuntimeBinding", self._fail
            ),
            "current_stage": "paper",
            "target_stage": "paper",
            "transition_type": "replace",
            "runtime_action": "replace_binding",
            "binding_id": req.current_binding_id,
        }
        for key, value in expected.items():
            self._require_value(plan, key, value, "DeploymentPlan")
        if _text(plan.get("status")) not in {"approved", "executing"}:
            self._fail(
                "DeploymentPlan must be approved or executing before runtime replace; "
                f"got {_text(plan.get('status'))!r}"
            )
        rollback = _required_mapping(plan.get("rollback"), "DeploymentPlan.rollback", self._fail)
        self._require_value(
            rollback,
            "target_artifact_id",
            _required_text(current_binding, "artifact_id", "current RuntimeBinding", self._fail),
            "DeploymentPlan.rollback",
        )
        self._require_value(
            rollback,
            "target_version",
            _required_text(
                current_binding, "artifact_version", "current RuntimeBinding", self._fail
            ),
            "DeploymentPlan.rollback",
        )
        self._require_value(rollback, "action_type", "replace", "DeploymentPlan.rollback")

    def _completed_deployment_readback(
        self,
        *,
        req: PromoteRequest,
        new_binding_id: str,
        previous_binding: Mapping[str, Any],
        expected_saga_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Require authoritative executed-plan and completed-saga state.

        This helper is also the replay boundary.  A runtime cutover without
        completed deployment state fails closed instead of returning a
        synthetic success receipt; durable saga repair remains owned by the
        generic deployment dispatcher.
        """
        plan = self._get_mapping(
            "deployment",
            f"/api/deployment/plans/{_path_id(req.plan_id)}",
            label="completed DeploymentPlan readback",
        )
        expected = {
            "plan_id": req.plan_id,
            "approval_decision_id": req.approval_decision_id,
            "artifact_id": req.registry_id,
            "capital_pool_id": _required_text(
                previous_binding,
                "capital_pool_id",
                "previous RuntimeBinding",
                self._fail,
            ),
            "current_stage": "paper",
            "target_stage": "paper",
            "transition_type": "replace",
            "runtime_action": "replace_binding",
            "status": "executed",
            "binding_id": new_binding_id,
        }
        for key, value in expected.items():
            self._require_value(plan, key, value, "completed DeploymentPlan")
        lifecycle = (
            plan.get("metadata", {}).get("runtime_lifecycle", {})
            if isinstance(plan.get("metadata"), Mapping)
            else {}
        )
        if not isinstance(lifecycle, Mapping):
            self._fail("Completed DeploymentPlan lacks runtime_lifecycle metadata")
        self._require_value(
            lifecycle,
            "binding_id",
            new_binding_id,
            "completed DeploymentPlan.runtime_lifecycle",
        )
        self._require_value(
            lifecycle,
            "runtime_id",
            req.expected_runtime_id,
            "completed DeploymentPlan.runtime_lifecycle",
        )
        self._require_value(
            lifecycle,
            "runtime_status",
            "active",
            "completed DeploymentPlan.runtime_lifecycle",
        )

        progress = self._get_mapping(
            "deployment",
            f"/api/deployment/plans/{_path_id(req.plan_id)}/saga-progress",
            label="completed deployment saga progress",
        )
        self._require_value(progress, "plan_id", req.plan_id, "deployment saga progress")
        self._require_value(
            progress, "progress_status", "completed", "deployment saga progress"
        )
        self._require_value(
            progress, "saga_status", "completed", "deployment saga progress"
        )
        self._require_value(
            progress, "current_step", "runtime_active", "deployment saga progress"
        )
        if expected_saga_id is not None:
            self._require_value(
                progress,
                "saga_id",
                expected_saga_id,
                "deployment saga progress",
            )
        return plan, progress

    def _dispatch_saga(
        self, dispatch: Mapping[str, Any], expected_plan_id: str
    ) -> tuple[str, bool]:
        plan = dispatch.get("plan")
        if isinstance(plan, Mapping):
            self._require_value(plan, "plan_id", expected_plan_id, "deployment dispatch plan")
        deployment_saga = dispatch.get("deployment_saga")
        saga: Mapping[str, Any] | None = None
        if isinstance(deployment_saga, Mapping):
            nested = deployment_saga.get("saga")
            saga = nested if isinstance(nested, Mapping) else deployment_saga
        elif isinstance(dispatch.get("saga"), Mapping):
            saga = dispatch["saga"]
        else:
            saga = dispatch
        saga_id = _required_text(saga, "saga_id", "deployment dispatch saga", self._fail)
        saga_plan_id = _text(saga.get("plan_id"))
        if saga_plan_id and saga_plan_id != expected_plan_id:
            self._fail("Deployment saga plan_id does not match the requested DeploymentPlan")
        return saga_id, bool(dispatch.get("replayed"))

    def _build_replace_body(
        self,
        *,
        req: PromoteRequest,
        plan: Mapping[str, Any],
        entry: Mapping[str, Any],
        artifact: Mapping[str, Any],
        current_binding: Mapping[str, Any],
        binding_intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        metadata = (
            current_binding.get("metadata")
            if isinstance(current_binding.get("metadata"), Mapping)
            else {}
        )
        allowed_scope = (
            _text(current_binding.get("allowed_deployment_scope"))
            or _text(metadata.get("allowed_deployment_scope"))
            or _text(binding_intent.get("allowed_deployment_scope"))
            or "paper"
        )
        if allowed_scope not in {"paper", "canary", "live"}:
            self._fail(
                f"PersonaCapitalBinding allowed_deployment_scope cannot permit paper: {allowed_scope!r}"
            )
        strategy_metadata = {
            "strategy_id": artifact.get("strategy_id"),
            "strategy_artifact_id": artifact.get("artifact_id"),
            "strategy_artifact_version": artifact.get("version"),
            "algorithm_ref": artifact.get("algorithm_ref"),
            "strategy_logic": artifact.get("strategy_logic"),
            "parameters": artifact.get("parameters"),
        }
        return {
            "plan_id": req.plan_id,
            "plan_status": _text(plan.get("status")),
            "approval_decision_id": req.approval_decision_id,
            "sponsor_persona_id": _required_text(
                plan,
                "sponsor_persona_id",
                "DeploymentPlan",
                self._fail,
            ),
            "runtime_action": "replace_binding",
            "current_binding_id": req.current_binding_id,
            "old_binding_id": req.current_binding_id,
            "target_stage": "paper",
            "deployment_mode": "paper",
            "runtime_id": req.expected_runtime_id,
            "expected_runtime_id": req.expected_runtime_id,
            "artifact_id": req.registry_id,
            "artifact_version": entry.get("version"),
            "new_artifact": {
                "registry_id": req.registry_id,
                "artifact_id": artifact.get("artifact_id"),
                "artifact_version": artifact.get("version"),
                "strategy_id": artifact.get("strategy_id"),
            },
            "capital_pool_id": current_binding.get("capital_pool_id"),
            "persona_capital_binding_id": current_binding.get(
                "persona_capital_binding_id"
            ),
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": allowed_scope,
            "loader_checks_passed": True,
            "source_task_id": req.source_task_id,
            "strategy_id": artifact.get("strategy_id"),
            "strategy_metadata": strategy_metadata,
            "metadata": {
                **strategy_metadata,
                "strategy_artifact": dict(artifact),
                "registry_id": req.registry_id,
                "deployment_plan_id": req.plan_id,
                "approval_decision_id": req.approval_decision_id,
                "source_task_id": req.source_task_id,
                "replaces_binding_id": req.current_binding_id,
            },
        }

    def _new_binding_id(self, result: Mapping[str, Any]) -> str:
        for key in ("new_binding", "binding", "runtime_binding", "replacement_binding"):
            candidate = result.get(key)
            if isinstance(candidate, Mapping) and _text(candidate.get("binding_id")):
                return _text(candidate.get("binding_id"))
        direct = _text(result.get("new_binding_id") or result.get("binding_id"))
        if direct:
            return direct
        self._fail("Runtime mutation response did not include a new RuntimeBinding id")
        raise AssertionError("unreachable")

    def _validate_forward_replace_result(
        self,
        result: Mapping[str, Any],
        *,
        current_binding: Mapping[str, Any],
        expected_runtime_id: str,
    ) -> None:
        self._require_value(
            result, "operation", "forward_replace", "runtime replace result"
        )
        old_binding = _required_mapping(
            result.get("old_binding"),
            "runtime replace result.old_binding",
            self._fail,
        )
        self._require_value(
            old_binding,
            "binding_id",
            _required_text(
                current_binding, "binding_id", "current RuntimeBinding", self._fail
            ),
            "runtime replace result.old_binding",
        )
        self._require_value(
            old_binding, "status", "retired", "runtime replace result.old_binding"
        )
        self._require_value(
            old_binding,
            "runtime_id",
            expected_runtime_id,
            "runtime replace result.old_binding",
        )
        new_binding = _required_mapping(
            result.get("new_binding"),
            "runtime replace result.new_binding",
            self._fail,
        )
        if new_binding.get("rollback_parent") not in (None, "") or new_binding.get(
            "rollback_action_type"
        ) not in (None, ""):
            self._fail("Forward replace result must not carry rollback lineage")

    def _validate_new_binding(
        self,
        binding: Mapping[str, Any],
        *,
        expected_binding_id: str,
        expected_artifact_id: str,
        expected_artifact_version: str,
        expected_plan_id: str,
        expected_runtime_id: str,
        expected_pool_id: str,
        expected_pcb_id: str,
        expected_parent_binding_id: str,
    ) -> None:
        expected = {
            "binding_id": expected_binding_id,
            "artifact_id": expected_artifact_id,
            "artifact_version": expected_artifact_version,
            "plan_id": expected_plan_id,
            "runtime_id": expected_runtime_id,
            "capital_pool_id": expected_pool_id,
            "persona_capital_binding_id": expected_pcb_id,
            "status": "active",
            "deployment_mode": "paper",
            "execution_mode": "paper",
        }
        for key, value in expected.items():
            self._require_value(binding, key, value, "new RuntimeBinding")
        if binding.get("rollback_parent") not in (None, "") or binding.get(
            "rollback_action_type"
        ) not in (None, ""):
            self._fail("Forward RuntimeBinding must not carry rollback lineage")
        metadata = _required_mapping(
            binding.get("metadata"), "new RuntimeBinding.metadata", self._fail
        )
        self._require_value(
            metadata, "replacement_kind", "forward", "new RuntimeBinding.metadata"
        )
        self._require_value(
            metadata,
            "replacement_parent_binding_id",
            expected_parent_binding_id,
            "new RuntimeBinding.metadata",
        )

    def _validate_restored_binding(
        self,
        binding: Mapping[str, Any],
        *,
        expected_binding_id: str,
        original_pre: Mapping[str, Any],
        rollback_parent: str,
    ) -> None:
        expected = {
            "binding_id": expected_binding_id,
            "artifact_id": _required_text(
                original_pre, "artifact_id", "promotion receipt pre-state", self._fail
            ),
            "artifact_version": _required_text(
                original_pre,
                "artifact_version",
                "promotion receipt pre-state",
                self._fail,
            ),
            "plan_id": _required_text(
                original_pre, "plan_id", "promotion receipt pre-state", self._fail
            ),
            "runtime_id": _required_text(
                original_pre, "runtime_id", "promotion receipt pre-state", self._fail
            ),
            "capital_pool_id": _required_text(
                original_pre, "capital_pool_id", "promotion receipt pre-state", self._fail
            ),
            "persona_capital_binding_id": _required_text(
                original_pre,
                "persona_capital_binding_id",
                "promotion receipt pre-state",
                self._fail,
            ),
            "status": "active",
            "deployment_mode": _required_text(
                original_pre,
                "deployment_mode",
                "promotion receipt pre-state",
                self._fail,
            ),
        }
        for key, value in expected.items():
            self._require_value(binding, key, value, "restored RuntimeBinding")
        execution_mode = _text(binding.get("execution_mode"))
        if execution_mode and execution_mode != expected["deployment_mode"]:
            self._fail("Restored RuntimeBinding execution_mode is not the original mode")
        parent = _text(binding.get("rollback_parent"))
        if parent != rollback_parent:
            self._fail("Restored RuntimeBinding rollback_parent is not the promoted binding")
        if _text(binding.get("rollback_action_type")) != "replace":
            self._fail("Restored RuntimeBinding rollback_action_type must be 'replace'")

    def _get_active_binding(self, pool_id: str) -> dict[str, Any]:
        return self._get_mapping(
            "runtime_manager",
            f"/api/runtimes/{_path_id(pool_id)}/active",
            label="active pool RuntimeBinding",
        )

    def _completed_promotion_binding(
        self,
        *,
        req: PromoteRequest,
        entry: Mapping[str, Any],
        binding_intent: Mapping[str, Any],
        current_binding: Mapping[str, Any],
        current_found: bool,
    ) -> dict[str, Any] | None:
        target_version = _required_text(entry, "version", "approved registry entry", self._fail)
        expected_pool_id = _required_text(
            binding_intent,
            "observed_capital_pool_id",
            "StrategyArtifact.binding_intent",
            self._fail,
        )
        expected_pcb_id = _required_text(
            binding_intent,
            "persona_capital_binding_id",
            "StrategyArtifact.binding_intent",
            self._fail,
        )

        def is_target(binding: Mapping[str, Any]) -> bool:
            return all(
                (
                    _text(binding.get("artifact_id")) == req.registry_id,
                    _text(binding.get("artifact_version")) == target_version,
                    _text(binding.get("plan_id")) == req.plan_id,
                    _text(binding.get("runtime_id")) == req.expected_runtime_id,
                    _text(binding.get("capital_pool_id")) == expected_pool_id,
                    _text(binding.get("persona_capital_binding_id")) == expected_pcb_id,
                    _text(binding.get("status")) == "active",
                    _text(binding.get("deployment_mode")) == "paper",
                    _text(binding.get("execution_mode")) in {"", "paper"},
                )
            )

        if current_found and is_target(current_binding):
            active = self._get_active_binding(
                _required_text(
                    current_binding, "capital_pool_id", "current RuntimeBinding", self._fail
                )
            )
            self._require_binding_equal(active, current_binding, label="promotion replay")
            return dict(current_binding)

        immutable_old_match = (
            _text(current_binding.get("binding_id")) == req.current_binding_id
            and _text(current_binding.get("runtime_id")) == req.expected_runtime_id
            and _text(current_binding.get("plan_id"))
            == _text(binding_intent.get("observed_deployment_plan_id"))
            and _text(current_binding.get("capital_pool_id")) == expected_pool_id
            and _text(current_binding.get("persona_capital_binding_id")) == expected_pcb_id
            and _text(current_binding.get("artifact_id"))
            == _text(binding_intent.get("observed_placeholder_artifact_id"))
            and _text(current_binding.get("artifact_version"))
            == _text(binding_intent.get("observed_placeholder_artifact_version"))
            and _text(current_binding.get("deployment_mode")) == "paper"
        )
        if not immutable_old_match:
            return None
        pool_id = _text(current_binding.get("capital_pool_id"))
        if not pool_id:
            return None
        if current_found and _text(current_binding.get("status")) == "active":
            return None
        active = self._get_active_binding(pool_id)
        if is_target(active):
            return active
        return None

    def _binding_matches_restored_state(
        self, binding: Mapping[str, Any], original_pre: Mapping[str, Any]
    ) -> bool:
        keys = (
            "artifact_id",
            "artifact_version",
            "plan_id",
            "runtime_id",
            "capital_pool_id",
            "persona_capital_binding_id",
            "deployment_mode",
        )
        return _text(binding.get("status")) == "active" and all(
            _text(binding.get(key)) == _text(original_pre.get(key)) for key in keys
        )

    def _validate_rollback_receipt_states(
        self,
        original_pre: Mapping[str, Any],
        promoted_post: Mapping[str, Any],
        registry: Mapping[str, Any],
    ) -> None:
        for label, state in (("pre", original_pre), ("post", promoted_post)):
            for key in (
                "binding_id",
                "runtime_id",
                "capital_pool_id",
                "artifact_id",
                "artifact_version",
                "deployment_mode",
                "plan_id",
                "persona_capital_binding_id",
            ):
                _required_text(state, key, f"promotion receipt {label}-state", self._fail)
        if _text(original_pre.get("runtime_id")) != _text(promoted_post.get("runtime_id")):
            self._fail("Promotion receipt runtime changed; exact rollback is not safe")
        if _text(original_pre.get("capital_pool_id")) != _text(
            promoted_post.get("capital_pool_id")
        ):
            self._fail("Promotion receipt capital pool changed; exact rollback is not safe")
        if _text(original_pre.get("persona_capital_binding_id")) != _text(
            promoted_post.get("persona_capital_binding_id")
        ):
            self._fail("Promotion receipt PersonaCapitalBinding changed; exact rollback is not safe")
        if _text(original_pre.get("deployment_mode")) != "paper" or _text(
            promoted_post.get("deployment_mode")
        ) != "paper":
            self._fail("EVOLOOP-006 rollback receipt must describe paper bindings")
        if _text(registry.get("registry_id")) != _text(promoted_post.get("artifact_id")):
            self._fail("Promotion receipt registry id does not match promoted artifact")

    def _validate_registry_projection(
        self,
        projection: Mapping[str, Any],
        *,
        expected_stage: str,
        expected_plan_id: str | None,
        expected_binding_id: str | None,
    ) -> None:
        entry_source = (
            projection.get("entry")
            if isinstance(projection.get("entry"), Mapping)
            else projection
        )
        summary = (
            entry_source.get("deployment_summary")
            if isinstance(entry_source.get("deployment_summary"), Mapping)
            else {}
        )
        stage = _text(summary.get("current_stage") or projection.get("deployment_stage"))
        if stage != expected_stage:
            self._fail(
                f"Registry deployment projection stage mismatch: expected {expected_stage!r}, "
                f"got {stage!r}"
            )
        if expected_plan_id is not None:
            self._require_value(
                summary, "deployment_plan_id", expected_plan_id, "registry deployment_summary"
            )
        elif summary.get("deployment_plan_id") not in (None, ""):
            self._fail("Rollback projection retained a deployment_plan_id")
        if expected_binding_id is not None:
            self._require_value(
                summary,
                "runtime_binding_id",
                expected_binding_id,
                "registry deployment_summary",
            )
        elif summary.get("runtime_binding_id") not in (None, ""):
            self._fail("Rollback projection retained a runtime_binding_id")

    # ------------------------------------------------------------------
    # Fleet convergence
    # ------------------------------------------------------------------

    def _wait_for_fleet(
        self,
        *,
        new_binding_id: str,
        runtime_id: str,
        old_binding_id: str,
    ) -> dict[str, Any]:
        if not self._fleet_is_configured():
            if self._fleet_enabled_override is not False:
                self._fail(
                    "Paper fleet service is required for runtime convergence proof; "
                    "set fleet_enabled=False only in a deliberately isolated test"
                )
            return {
                "configured": False,
                "converged": None,
                "attempts": 0,
                "new_binding_id": new_binding_id,
                "runtime_id": runtime_id,
                "old_binding_absent": None,
            }

        deadline = self._clock() + self._fleet_poll_timeout_seconds
        attempts = 0
        last_summary: dict[str, Any] | None = None
        while True:
            attempts += 1
            state = self._get_mapping("fleet", "/api/fleet/state", label="fleet state")
            converged, summary = self._fleet_converged(
                state,
                new_binding_id=new_binding_id,
                runtime_id=runtime_id,
                old_binding_id=old_binding_id,
            )
            last_summary = summary
            if converged:
                return {
                    "configured": True,
                    "converged": True,
                    "attempts": attempts,
                    **summary,
                }
            now = self._clock()
            if now >= deadline:
                self._fail(
                    "Fleet did not converge to the expected replacement before timeout: "
                    f"{last_summary}"
                )
            sleep_for = min(self._fleet_poll_interval_seconds, max(0.0, deadline - now))
            if sleep_for:
                self._sleeper(sleep_for)

    def _fleet_converged(
        self,
        state: Mapping[str, Any],
        *,
        new_binding_id: str,
        runtime_id: str,
        old_binding_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        records: list[Mapping[str, Any]] = []
        source = ""
        for key in ("workers", "runtimes", "bindings"):
            value = state.get(key)
            if isinstance(value, list):
                records = [item for item in value if isinstance(item, Mapping)]
                source = key
                break
        if not records and isinstance(state.get("monitoring_sessions"), list):
            records = [
                item
                for item in state["monitoring_sessions"]
                if isinstance(item, Mapping)
                and (
                    item.get("active") is True
                    or _text(item.get("status")) in _RUNNING_FLEET_STATUSES
                )
            ]
            source = "monitoring_sessions"

        def binding_id(record: Mapping[str, Any]) -> str:
            return _text(record.get("binding_id") or record.get("runtime_binding_id"))

        def running(record: Mapping[str, Any]) -> bool:
            return _text(record.get("status")) in _RUNNING_FLEET_STATUSES

        new_records = [record for record in records if binding_id(record) == new_binding_id]
        new_running = any(
            _text(record.get("runtime_id")) == runtime_id and running(record)
            for record in new_records
        )
        old_absent = all(binding_id(record) != old_binding_id for record in records)
        summary = {
            "source": source or "unknown",
            "new_binding_id": new_binding_id,
            "runtime_id": runtime_id,
            "new_binding_running": new_running,
            "old_binding_id": old_binding_id,
            "old_binding_absent": old_absent,
            "observed_record_count": len(records),
        }
        return new_running and old_absent, summary

    # ------------------------------------------------------------------
    # Receipts and generic comparisons
    # ------------------------------------------------------------------

    def _promotion_receipt(
        self,
        *,
        req: PromoteRequest,
        entry: Mapping[str, Any],
        pre: Mapping[str, Any] | None,
        post: Mapping[str, Any],
        plan: Mapping[str, Any],
        saga: Mapping[str, Any],
        fleet: Mapping[str, Any],
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "receipt_schema_version": "evoloop_006.promote_receipt.v1",
            "operation": "promote",
            "status": "already_promoted" if replayed else "promoted",
            "replayed": replayed,
            "source_task_id": req.source_task_id,
            "calls": list(self._calls),
            "registry": {
                "registry_id": req.registry_id,
                "artifact_state": _text(entry.get("artifact_state")),
                "artifact_version": _text(entry.get("version")),
                "approval_decision_id": req.approval_decision_id,
                "deployment_stage": "paper",
                "runtime_binding_id": post.get("binding_id"),
            },
            "pre": dict(pre) if pre is not None else None,
            "post": dict(post),
            "plan": dict(plan),
            "saga": dict(saga),
            "fleet": dict(fleet),
            "invariants": {
                "governance_decided_approved": True,
                "registry_artifact_approved": True,
                "binding_intent_exact": True,
                "runtime_id_exact": True,
                "paper_to_paper": True,
                "transition_type_replace": True,
                "runtime_action_replace_binding": True,
                "new_binding_active": True,
                "active_pool_binding_exact": True,
                "runtime_auth_not_in_receipt": True,
                "no_store_edits": True,
            },
            "no_store_edits": True,
        }

    def _rollback_receipt(
        self,
        *,
        promotion_receipt: Mapping[str, Any],
        pre: Mapping[str, Any],
        post: Mapping[str, Any],
        registry_id: str,
        fleet: Mapping[str, Any],
        replayed: bool,
    ) -> dict[str, Any]:
        original_pre = _required_mapping(
            promotion_receipt.get("pre"), "promotion receipt pre-state", self._fail
        )
        return {
            "receipt_schema_version": "evoloop_006.rollback_receipt.v1",
            "operation": "rollback",
            "status": "already_rolled_back" if replayed else "rolled_back",
            "replayed": replayed,
            "source_task_id": _text(promotion_receipt.get("source_task_id")),
            "calls": list(self._calls),
            "registry": {
                "registry_id": registry_id,
                "deployment_stage": "none",
                "deployment_plan_id": None,
                "runtime_binding_id": None,
            },
            "pre": dict(pre),
            "post": dict(post),
            "restored": {
                key: original_pre.get(key)
                for key in (
                    "artifact_id",
                    "artifact_version",
                    "plan_id",
                    "persona_capital_binding_id",
                    "capital_pool_id",
                    "runtime_id",
                    "deployment_mode",
                )
            },
            "plan": {
                "plan_id": original_pre.get("plan_id"),
                "artifact_id": original_pre.get("artifact_id"),
                "artifact_version": original_pre.get("artifact_version"),
                "runtime_action": "replace_binding",
            },
            "saga": None,
            "fleet": dict(fleet),
            "invariants": {
                "rollback_route_only": True,
                "pre_state_artifact_exact": True,
                "pre_state_plan_exact": True,
                "pre_state_persona_capital_binding_exact": True,
                "pre_state_runtime_exact": True,
                "restored_binding_active": True,
                "active_pool_binding_exact": True,
                "promoted_registry_projection_none": True,
                "runtime_auth_not_in_receipt": True,
                "no_store_edits": True,
            },
            "no_store_edits": True,
        }

    def _binding_snapshot(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        keys = (
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "deployment_mode",
            "execution_mode",
            "status",
            "plan_id",
            "persona_capital_binding_id",
            "effective_at",
            "retired_at",
            "rollback_parent",
            "rollback_action_type",
            "allowed_deployment_scope",
        )
        snapshot = {key: binding.get(key) for key in keys if binding.get(key) is not None}
        snapshot.setdefault(
            "allowed_deployment_scope", _text(binding.get("deployment_mode")) or "paper"
        )
        return snapshot

    def _plan_snapshot(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        keys = (
            "plan_id",
            "approval_decision_id",
            "artifact_id",
            "artifact_version",
            "strategy_id",
            "capital_pool_id",
            "current_stage",
            "target_stage",
            "transition_type",
            "runtime_action",
            "status",
            "binding_id",
            "created_by",
            "supersedes_plan_id",
        )
        snapshot = {key: plan.get(key) for key in keys if plan.get(key) is not None}
        rollback = plan.get("rollback")
        if isinstance(rollback, Mapping):
            snapshot["rollback"] = {
                key: rollback.get(key)
                for key in ("target_artifact_id", "target_version", "action_type")
                if rollback.get(key) is not None
            }
        return snapshot

    def _plan_snapshot_from_binding(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": binding.get("plan_id"),
            "artifact_id": binding.get("artifact_id"),
            "artifact_version": binding.get("artifact_version"),
            "capital_pool_id": binding.get("capital_pool_id"),
            "current_stage": "paper",
            "target_stage": "paper",
            "transition_type": "replace",
            "runtime_action": "replace_binding",
            "status": "executed",
            "binding_id": binding.get("binding_id"),
        }

    def _require_binding_equal(
        self,
        observed: Mapping[str, Any],
        expected: Mapping[str, Any],
        *,
        label: str,
    ) -> None:
        for key in (
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "deployment_mode",
            "status",
            "plan_id",
            "persona_capital_binding_id",
        ):
            expected_value = _text(expected.get(key))
            if expected_value and _text(observed.get(key)) != expected_value:
                self._fail(
                    f"{label} {key} mismatch: expected {expected_value!r}, "
                    f"got {_text(observed.get(key))!r}"
                )

    def _require_value(
        self,
        mapping: Mapping[str, Any],
        key: str,
        expected: Any,
        label: str,
    ) -> None:
        actual = mapping.get(key)
        if isinstance(expected, Enum):
            expected = expected.value
        if isinstance(actual, Enum):
            actual = actual.value
        if actual != expected:
            self._fail(
                f"{label}.{key} mismatch: expected {expected!r}, got {actual!r}"
            )

    def _finalize_receipt(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        safe = _json_safe(receipt)
        try:
            json.dumps(safe, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            self._fail(f"Receipt is not strict JSON serialisable: {exc}")
        return safe

    def _fail(self, message: str) -> None:
        raise PromotePipelineError(message, calls=self._calls)


def promote(
    request: PromoteRequest | Mapping[str, Any],
    *,
    transport: ServiceTransport | None = None,
    **pipeline_options: Any,
) -> dict[str, Any]:
    """Convenience wrapper using environment-configured HTTP transport by default."""
    owned_transport = transport is None
    actual_transport = transport or HttpxServiceTransport.from_env()
    try:
        return PromotePipeline(actual_transport, **pipeline_options).promote(request)
    finally:
        if owned_transport and isinstance(actual_transport, HttpxServiceTransport):
            actual_transport.close()


def rollback(
    receipt: Mapping[str, Any],
    *,
    transport: ServiceTransport | None = None,
    **pipeline_options: Any,
) -> dict[str, Any]:
    """Convenience wrapper for :meth:`PromotePipeline.rollback`."""
    owned_transport = transport is None
    actual_transport = transport or HttpxServiceTransport.from_env()
    try:
        return PromotePipeline(actual_transport, **pipeline_options).rollback(receipt)
    finally:
        if owned_transport and isinstance(actual_transport, HttpxServiceTransport):
            actual_transport.close()


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------


def _normalize_response(response: Any) -> ServiceResponse:
    if isinstance(response, ServiceResponse):
        return response
    if isinstance(response, tuple) and len(response) == 2:
        first, second = response
        if isinstance(first, int):
            return ServiceResponse(first, second)
        if isinstance(second, int):
            return ServiceResponse(second, first)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                body = json_method()
            except (TypeError, ValueError):
                body = getattr(response, "text", None)
        else:
            body = getattr(response, "body", getattr(response, "payload", None))
        return ServiceResponse(int(status_code), body)
    if isinstance(response, Mapping):
        return ServiceResponse(200, dict(response))
    raise PromotePipelineError(
        "Service transport returned an unsupported response shape "
        f"{type(response).__name__}"
    )


def _required_mapping(
    value: Any,
    label: str,
    fail: Callable[[str], None],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        fail(f"{label} must be a JSON object")
    return dict(value)


def _required_text(
    mapping: Mapping[str, Any],
    key: str,
    label: str,
    fail: Callable[[str], None],
) -> str:
    value = _text(mapping.get(key))
    if not value:
        fail(f"{label}.{key} is required")
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    return str(value).strip()


def _path_id(value: str) -> str:
    return quote(str(value), safe="")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = [
    "HttpxServiceTransport",
    "PromotePipeline",
    "PromotePipelineError",
    "PromoteRequest",
    "ServiceResponse",
    "ServiceTransport",
    "promote",
    "rollback",
]
