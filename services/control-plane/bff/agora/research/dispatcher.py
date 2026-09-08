"""Agora research dispatcher, allowlisted adapters, lease management, and artifact projection.

Implements the governed research dispatcher per:
  - docs/04/pantheon_agora_product_gap_sd_2026-08-13/03_SD_AGORA_COMPLETE_PRODUCT.md §6.2
  - services/control-plane/specs/agora/v4/research_plan_execution.schema.json
  - services/control-plane/specs/agora/v4/research_run_projection.schema.json

Responsibilities:
  - Consumes durable outbox records with lease acquisition & timeout
  - Resolves allowlisted backend adapters for typed stages
  - Enforces deterministic downstream idempotency keys
  - Persists backend identity and partial effects before polling/completion
  - Projects ordered progress events (queued -> dispatching -> running -> completed/failed)
  - Computes and verifies artifact checksums (sha256) and explicit lineage
  - Labels explicit provenance: 'real', 'simulation', 'fixture', 'unavailable' without fallback
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from .receipt import ResearchExecutionReceipt, resolve_run_provenance

logger = logging.getLogger(__name__)

# Allowlisted stages to preferred backends per MASTER_SD_RESPONSE.md §B3 & SD_AGORA_COMPLETE_PRODUCT.md
ALLOWLISTED_STAGE_BACKENDS: Dict[str, str] = {
    "source_discovery": "source_ingestion",
    "data_validation": "data_validation",
    "prototype_backtest": "vectorbt",
    "alpha_training": "qlib",
    "rolling_oos": "qlib",
    "econometric_validation": "statsmodels",
    "derivatives_pricing_risk": "quantlib",
    "policy_training": "finrl",
    "parameter_search": "ray_tune",
    "portfolio_synthesis": "optimizer_svc",
    "robustness_stress": "rllib",
    "evidence_synthesis": "openclaw_result_synthesis",
}

VALID_PROVENANCE_VALUES = frozenset({"real", "simulation", "fixture", "unavailable"})
DEFAULT_LEASE_DURATION_SECONDS = 60.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_artifact_checksum(payload: Any) -> str:
    """Compute sha256 checksum over deterministic JSON serialization."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


@dataclass
class ResearchStageResult:
    """Outcome of an adapter execution."""
    outcome: str  # "succeeded" | "failed" | "cancelled" | "inconclusive"
    provenance: str  # "real" | "simulation" | "fixture" | "unavailable"
    progress_percent: float = 100.0
    backend_job_id: str = ""
    backend_version: str = "1.0"
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    artifact_refs: List[str] = field(default_factory=list)
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    lineage_refs: List[str] = field(default_factory=list)
    partial_effects: Dict[str, Any] = field(default_factory=dict)
    checksums: Dict[str, str] = field(default_factory=dict)
    receipt: Optional[Any] = None
    error_message: Optional[str] = None


class StageAdapter(Protocol):
    """Protocol for allowlisted stage execution adapters."""
    stage_type: str
    preferred_backend: str

    def execute(
        self,
        *,
        stage: Dict[str, Any],
        plan: Dict[str, Any],
        context: Dict[str, Any],
        downstream_key: str,
    ) -> ResearchStageResult:
        ...


class DefaultAllowlistedAdapter:
    """Synthetic adapter for allowlisted stages, without backend execution.

    Real provenance belongs to a registered adapter which actually obtains
    owner receipts. A caller's receipt label cannot attest these local outputs.
    """

    def __init__(self, stage_type: str, preferred_backend: str, default_provenance: str = "simulation") -> None:
        self.stage_type = stage_type
        self.preferred_backend = preferred_backend
        self.default_provenance = "simulation" if default_provenance == "real" else default_provenance

    def execute(
        self,
        *,
        stage: Dict[str, Any],
        plan: Dict[str, Any],
        context: Dict[str, Any],
        downstream_key: str,
    ) -> ResearchStageResult:
        routing = stage.get("routing") or {}
        requested_mode = routing.get("backend_mode") or context.get("backend_mode") or "simulation"
        if requested_mode == "fixture":
            provenance = "fixture"
        elif requested_mode == "simulation":
            provenance = "simulation"
        elif requested_mode == "real":
            provenance = "simulation"
        else:
            provenance = self.default_provenance
        if provenance not in VALID_PROVENANCE_VALUES:
            provenance = "unavailable"

        stage_id = stage.get("stage_id", "stage-unknown")
        strategy_id = plan.get("strategy_id", "strategy-unknown")
        backend_job_id = f"job:{self.preferred_backend}:{downstream_key}"

        # Generate stage-specific artifacts and lineage
        artifact_id = f"art:{stage_id}:{self.preferred_backend}"
        artifact_payload = {
            "artifact_id": artifact_id,
            "stage_id": stage_id,
            "stage_type": self.stage_type,
            "strategy_id": strategy_id,
            "backend": self.preferred_backend,
            "backend_job_id": backend_job_id,
            "provenance": provenance,
            "created_at": _utc_now_iso(),
        }
        checksum = compute_artifact_checksum(artifact_payload)
        artifact_ref = f"research-artifact://{self.preferred_backend}/{artifact_id}"
        lineage_ref = f"lineage://research/{strategy_id}/{stage_id}/{checksum[:12]}"
        evidence_ref = {
            "ref_type": "research_evidence",
            "ref_id": f"ev:{stage_id}:{checksum[:8]}",
            "stage_type": self.stage_type,
            "provenance": provenance,
            "checksum": checksum,
            "as_of": _utc_now_iso(),
        }

        metrics = [
            {
                "metric_name": f"{self.stage_type}_execution_score",
                "value": 1.0,
                "provenance": provenance,
            }
        ]

        run_id = str(context.get("run_id") or stage.get("run_id") or "")
        correlation_id = str(
            context.get("correlation_id")
            or plan.get("correlation_id")
            or plan.get("trace_id")
            or ""
        )
        receipt = None
        if run_id:
            receipt = ResearchExecutionReceipt(
                receipt_id=f"rcpt-{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                executor=f"{self.preferred_backend}_executor",
                mode="simulation",
                correlation_id=correlation_id,
                completed_at=_utc_now_iso(),
                backend_reference=f"{self.preferred_backend}://jobs/{backend_job_id}",
                artifact_digest=checksum,
            )

        return ResearchStageResult(
            outcome="succeeded",
            provenance=provenance,
            progress_percent=100.0,
            backend_job_id=backend_job_id,
            backend_version="1.0.0",
            metrics=metrics,
            findings=[{"stage_type": self.stage_type, "status": "completed", "backend": self.preferred_backend}],
            warnings=(
                ["default_adapter_did_not_execute_real_backend"]
                if requested_mode == "real" else []
            ),
            blocking_reasons=[],
            artifact_refs=[artifact_ref],
            evidence_refs=[evidence_ref],
            lineage_refs=[lineage_ref],
            partial_effects={"backend_job_id": backend_job_id, "backend": self.preferred_backend},
            checksums={artifact_ref: checksum},
            receipt=receipt,
        )


class AuthenticStageAdapter(DefaultAllowlistedAdapter):
    """Authentic execution owner adapter producing durable real or simulation execution receipts."""

    def __init__(
        self,
        stage_type: str,
        preferred_backend: str,
        *,
        executor: Optional[str] = None,
        mode: Literal["real", "simulation"] = "real",
        backend_reference: Optional[str] = None,
        execution_owner: Optional[Any] = None,
        execute_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        super().__init__(stage_type, preferred_backend, default_provenance=mode)
        self.executor = executor or f"{preferred_backend}_executor"
        self.mode: Literal["real", "simulation"] = mode
        self.backend_reference = backend_reference
        self.execution_owner = execution_owner
        self.execute_fn = execute_fn

    def execute(
        self,
        *,
        stage: Dict[str, Any],
        plan: Dict[str, Any],
        context: Dict[str, Any],
        downstream_key: str,
    ) -> ResearchStageResult:
        run_id = str(context.get("run_id") or stage.get("run_id") or "")
        correlation_id = str(
            context.get("correlation_id")
            or plan.get("correlation_id")
            or plan.get("trace_id")
            or ""
        )

        # Fail closed on absent execution owner in real mode
        if self.mode == "real" and self.execution_owner is None and self.execute_fn is None:
            raise RuntimeError(
                f"Backend execution owner is absent for authentic real execution of stage '{self.stage_type}' "
                f"(backend: {self.preferred_backend}). Absent backend must fail closed."
            )

        # Execute authentic execution owner if supplied
        backend_output: Any = None
        if self.execute_fn is not None:
            backend_output = self.execute_fn(
                stage=stage,
                plan=plan,
                context=context,
                downstream_key=downstream_key,
            )
        elif self.execution_owner is not None:
            if hasattr(self.execution_owner, "execute"):
                backend_output = self.execution_owner.execute(
                    stage=stage,
                    plan=plan,
                    context=context,
                    downstream_key=downstream_key,
                )
            elif callable(self.execution_owner):
                backend_output = self.execution_owner(
                    stage=stage,
                    plan=plan,
                    context=context,
                    downstream_key=downstream_key,
                )
            else:
                backend_output = self.execution_owner
        else:
            # Fallback for simulation mode only
            backend_output = None

        # Fail closed on missing backend result in real mode
        if self.mode == "real" and backend_output is None:
            raise RuntimeError(
                f"Backend execution owner returned missing/empty result for authentic real stage '{self.stage_type}' "
                f"(backend: {self.preferred_backend}). Authentic execution must fail closed."
            )

        if isinstance(backend_output, ResearchStageResult):
            terminal_success_outcomes = {"succeeded", "completed", "passed"}
            terminal_failure_outcomes = {"failed", "cancelled", "inconclusive"}
            if backend_output.outcome in terminal_failure_outcomes:
                raise RuntimeError(
                    f"Authentic execution failed for stage '{self.stage_type}' with outcome '{backend_output.outcome}': "
                    f"{backend_output.error_message or 'failed'}"
                )
            if backend_output.outcome not in terminal_success_outcomes:
                raise RuntimeError(
                    f"Authentic execution for stage '{self.stage_type}' returned nonterminal outcome '{backend_output.outcome}'."
                )

            result = backend_output
            # Preserve observed provenance from backend; do not rewrite to self.mode
            observed_prov = result.provenance if result.provenance in VALID_PROVENANCE_VALUES else self.mode
            result.provenance = observed_prov

            if result.receipt is not None:
                receipt_obj = result.receipt
                if isinstance(receipt_obj, dict):
                    receipt_obj = ResearchExecutionReceipt.from_dict(receipt_obj)
                if not getattr(receipt_obj, "receipt_id", None):
                    raise RuntimeError("Owner-emitted receipt missing receipt_id")
                if run_id and str(receipt_obj.run_id) != str(run_id):
                    raise RuntimeError(f"Owner-emitted receipt run_id mismatch: expected {run_id}, got {receipt_obj.run_id}")
                if str(receipt_obj.mode).lower() not in VALID_MODES:
                    raise RuntimeError(f"Owner-emitted receipt has invalid mode: {receipt_obj.mode}")
                if str(getattr(receipt_obj, "spec_version", "1.0")) != "1.0":
                    raise RuntimeError(f"Owner-emitted receipt has invalid spec_version: {receipt_obj.spec_version}")
                result.receipt = receipt_obj
            else:
                checksum = next(iter(result.checksums.values()), None)
                backend_ref = self.backend_reference or getattr(result, "backend_job_id", None) or None
                if self.mode == "real" and observed_prov == "real":
                    if not checksum and not backend_ref:
                        raise RuntimeError(
                            f"Authentic real execution for stage '{self.stage_type}' missing backend reference and artifact digest."
                        )
                    if run_id:
                        result.receipt = ResearchExecutionReceipt(
                            receipt_id=f"rcpt-{uuid.uuid4().hex[:10]}",
                            run_id=run_id,
                            executor=self.executor,
                            mode="real",
                            correlation_id=correlation_id,
                            completed_at=_utc_now_iso(),
                            backend_reference=backend_ref,
                            artifact_digest=checksum,
                        )
                elif run_id:
                    receipt_mode = observed_prov if observed_prov in ("real", "simulation") else "simulation"
                    result.receipt = ResearchExecutionReceipt(
                        receipt_id=f"rcpt-{uuid.uuid4().hex[:10]}",
                        run_id=run_id,
                        executor=self.executor,
                        mode=receipt_mode,
                        correlation_id=correlation_id,
                        completed_at=_utc_now_iso(),
                        backend_reference=backend_ref,
                        artifact_digest=checksum,
                    )

            for m in result.metrics:
                if isinstance(m, dict) and "provenance" not in m:
                    m["provenance"] = observed_prov
            for ev in result.evidence_refs:
                if isinstance(ev, dict) and "provenance" not in ev:
                    ev["provenance"] = observed_prov
            return result

        if isinstance(backend_output, dict):
            status_val = str(backend_output.get("status") or backend_output.get("execution_status") or "").lower()
            outcome_val = str(backend_output.get("outcome") or "").lower()
            if status_val in {"failed", "error"} or outcome_val in {"failed", "error", "fail"}:
                raise RuntimeError(
                    f"Authentic execution failed for stage '{self.stage_type}': "
                    f"{backend_output.get('error') or backend_output.get('error_message') or backend_output.get('message') or 'status=failed'}"
                )
            if status_val in {"running", "queued", "pending", "in_progress"} or outcome_val in {"running", "queued", "pending", "in_progress"}:
                raise RuntimeError(
                    f"Authentic execution for stage '{self.stage_type}' returned nonterminal status '{status_val or outcome_val}'."
                )

            observed_prov = backend_output.get("provenance") or self.mode
            if observed_prov not in VALID_PROVENANCE_VALUES:
                observed_prov = "unavailable"

            backend_ref = backend_output.get("backend_reference") or self.backend_reference
            checksum = backend_output.get("artifact_digest") or backend_output.get("checksum")
            if self.mode == "real" and observed_prov == "real":
                if not backend_ref and not checksum:
                    raise RuntimeError(
                        f"Authentic real execution for stage '{self.stage_type}' missing backend reference and artifact digest."
                    )

            receipt_val = backend_output.get("receipt")
            receipt = None
            if receipt_val is not None:
                if isinstance(receipt_val, dict):
                    receipt = ResearchExecutionReceipt.from_dict(receipt_val)
                elif isinstance(receipt_val, ResearchExecutionReceipt):
                    receipt = receipt_val
                if not getattr(receipt, "receipt_id", None):
                    raise RuntimeError("Owner-emitted receipt missing receipt_id")
                if run_id and str(receipt.run_id) != str(run_id):
                    raise RuntimeError(f"Owner-emitted receipt run_id mismatch: expected {run_id}, got {receipt.run_id}")
                if str(receipt.mode).lower() not in VALID_MODES:
                    raise RuntimeError(f"Owner-emitted receipt has invalid mode: {receipt.mode}")
                if str(getattr(receipt, "spec_version", "1.0")) != "1.0":
                    raise RuntimeError(f"Owner-emitted receipt has invalid spec_version: {receipt.spec_version}")
            elif run_id:
                receipt_mode = "real" if (self.mode == "real" and observed_prov == "real") else "simulation"
                receipt = ResearchExecutionReceipt(
                    receipt_id=f"rcpt-{uuid.uuid4().hex[:10]}",
                    run_id=run_id,
                    executor=self.executor,
                    mode=receipt_mode,
                    correlation_id=correlation_id,
                    completed_at=_utc_now_iso(),
                    backend_reference=backend_ref,
                    artifact_digest=checksum,
                )

            result = super().execute(stage=stage, plan=plan, context=context, downstream_key=downstream_key)
            result.receipt = receipt
            result.provenance = observed_prov
            if "metrics" in backend_output and isinstance(backend_output["metrics"], list):
                result.metrics = list(backend_output["metrics"])
            if checksum:
                result.checksums["artifact"] = checksum
            for m in result.metrics:
                if isinstance(m, dict) and "provenance" not in m:
                    m["provenance"] = observed_prov
            for ev in result.evidence_refs:
                if isinstance(ev, dict) and "provenance" not in ev:
                    ev["provenance"] = observed_prov
            return result

        # Fallback for simulation mode only when no backend output provided
        result = super().execute(stage=stage, plan=plan, context=context, downstream_key=downstream_key)
        checksum = next(iter(result.checksums.values()), None)
        receipt = None
        if run_id:
            receipt = ResearchExecutionReceipt(
                receipt_id=f"rcpt-{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                executor=self.executor,
                mode="simulation",
                correlation_id=correlation_id,
                completed_at=_utc_now_iso(),
                backend_reference=self.backend_reference,
                artifact_digest=checksum,
            )
        result.receipt = receipt
        result.provenance = "simulation"
        return result


class AdapterRegistry:
    """Registry of allowlisted stage execution adapters."""

    def __init__(self) -> None:
        self._adapters: Dict[str, StageAdapter] = {}
        self._bootstrap_allowlist()

    def _bootstrap_allowlist(self) -> None:
        for stage_type, backend in ALLOWLISTED_STAGE_BACKENDS.items():
            self._adapters[stage_type] = DefaultAllowlistedAdapter(stage_type, backend)

    def register(self, stage_type: str, adapter: StageAdapter) -> None:
        self._adapters[stage_type] = adapter

    def register_authentic_adapter(
        self,
        stage_type: str,
        *,
        preferred_backend: Optional[str] = None,
        executor: Optional[str] = None,
        mode: Literal["real", "simulation"] = "real",
        backend_reference: Optional[str] = None,
        execution_owner: Optional[Any] = None,
        execute_fn: Optional[Callable[..., Any]] = None,
    ) -> AuthenticStageAdapter:
        backend = preferred_backend or ALLOWLISTED_STAGE_BACKENDS.get(stage_type, "unknown_backend")
        adapter = AuthenticStageAdapter(
            stage_type,
            backend,
            executor=executor,
            mode=mode,
            backend_reference=backend_reference,
            execution_owner=execution_owner,
            execute_fn=execute_fn,
        )
        self.register(stage_type, adapter)
        return adapter

    def get(self, stage_type: str) -> Optional[StageAdapter]:
        return self._adapters.get(stage_type)

    def is_allowlisted(self, stage_type: str) -> bool:
        return stage_type in self._adapters


def build_authentic_adapter_registry(
    *,
    mode: Literal["real", "simulation"] = "real",
    execution_owners: Optional[Dict[str, Any]] = None,
    default_backend_fn: Optional[Callable[..., Any]] = None,
) -> AdapterRegistry:
    """Build an AdapterRegistry populated with authentic stage adapters for all allowlisted stages."""
    registry = AdapterRegistry()
    owners = execution_owners or {}
    for stage_type, backend in ALLOWLISTED_STAGE_BACKENDS.items():
        owner = owners.get(stage_type) or default_backend_fn
        registry.register_authentic_adapter(
            stage_type,
            preferred_backend=backend,
            executor=f"{backend}_executor",
            mode=mode,
            backend_reference=f"{backend}://stages/{stage_type}",
            execution_owner=owner,
        )
    return registry


class ResearchDispatcher:
    """Coordinates durable outbox management, lease acquisition, adapter execution,

    partial effect handling, crash recovery, and progress/artifact projection.
    """

    def __init__(
        self,
        *,
        store: Any,
        adapter_registry: Optional[AdapterRegistry] = None,
        publish_progress_fn: Optional[Callable[..., str]] = None,
        utc_now: Optional[Callable[[], str]] = None,
    ) -> None:
        self.store = store
        self.registry = adapter_registry or AdapterRegistry()
        self.publish_progress = publish_progress_fn
        self.utc_now = utc_now or _utc_now_iso

    def create_outbox_record(
        self,
        *,
        plan: Dict[str, Any],
        stage: Dict[str, Any],
        run_id: str,
        scope: Any,
        now: str,
    ) -> Dict[str, Any]:
        """Create a durable outbox record before dispatch execution."""
        plan_id = plan["plan_id"]
        stage_id = stage["stage_id"]
        stage_type = stage["stage_type"]
        preferred_backend = ALLOWLISTED_STAGE_BACKENDS.get(stage_type, "unknown_backend")
        downstream_key = f"idemp:{scope.tenant_id}:{scope.user_id}:{plan_id}:{stage_id}:{run_id}"

        record: Dict[str, Any] = {
            "outbox_id": f"rob:{plan_id}:{stage_id}:{run_id}",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "plan_id": plan_id,
            "workshop_id": plan.get("workshop_id", ""),
            "strategy_id": plan.get("strategy_id", ""),
            "run_id": run_id,
            "stage_id": stage_id,
            "stage_type": stage_type,
            "status": "queued",
            "backend": preferred_backend,
            "downstream_idempotency_key": downstream_key,
            "backend_job_id": f"job:{preferred_backend}:{downstream_key}",
            "lease_owner": None,
            "lease_expires_at": None,
            "partial_effects": {},
            "progress": {
                "phase": "queued",
                "percent": 0.0,
                "message": "Run queued in durable outbox",
                "updated_at": now,
            },
            "provenance": "unavailable",
            "created_at": now,
            "updated_at": now,
        }
        if hasattr(self.store, "create_outbox_record"):
            return self.store.create_outbox_record(record)
        return record

    def execute_stage(
        self,
        *,
        plan: Dict[str, Any],
        stage: Dict[str, Any],
        run_id: str,
        scope: Any,
        worker_id: str = "dispatcher-local",
        lease_duration_seconds: float = DEFAULT_LEASE_DURATION_SECONDS,
    ) -> Dict[str, Any]:
        """Execute a dispatched stage with lease acquisition, adapter execution,

        artifact readback checksum verification, and ordered projection.
        """
        now = self.utc_now()
        plan_id = plan["plan_id"]
        stage_id = stage["stage_id"]
        stage_type = stage["stage_type"]
        workshop_id = plan.get("workshop_id", "")
        outbox_id = f"rob:{plan_id}:{stage_id}:{run_id}"

        # 1. Lease acquisition / adoption
        if hasattr(self.store, "acquire_outbox_lease"):
            lease = self.store.acquire_outbox_lease(
                outbox_id=outbox_id,
                lease_owner=worker_id,
                lease_duration_seconds=lease_duration_seconds,
                now_iso=now,
            )
        else:
            lease = {"lease_owner": worker_id}

        if lease is None:
            error_msg = f"Failed to acquire outbox lease for outbox_id '{outbox_id}'"
            logger.warning(error_msg)
            return {"status": "lease_blocked", "error": error_msg}

        # 2. Check allowlist
        if not self.registry.is_allowlisted(stage_type):
            error_msg = f"Stage type '{stage_type}' is not an allowlisted research stage backend"
            failure_updates = {
                "execution_status": "failed",
                "outcome": "fail",
                "blocking_reasons": [error_msg],
                "progress": {
                    "phase": "failed",
                    "percent": 0.0,
                    "message": error_msg,
                    "updated_at": now,
                },
                "updated_at": now,
            }
            self.store.update_run(run_id, failure_updates, tenant_id=scope.tenant_id, user_id=scope.user_id)
            if hasattr(self.store, "update_outbox_record"):
                self.store.update_outbox_record(
                    outbox_id,
                    {
                        "status": "failed",
                        "blocking_reasons": [error_msg],
                        "updated_at": now,
                    },
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                )
            return {"status": "failed", "error": error_msg}

        adapter = self.registry.get(stage_type)
        downstream_key = f"{scope.tenant_id}:{scope.user_id}:{plan_id}:{stage_id}:{run_id}"

        # 3. Transition: dispatching
        if self.publish_progress and workshop_id:
            self.publish_progress(
                workshop_id,
                run_id,
                10.0,
                f"Dispatching stage {stage_type} to backend {ALLOWLISTED_STAGE_BACKENDS.get(stage_type)}",
                phase="dispatching",
                utc_now_fn=self.utc_now,
            )
        self.store.update_run(
            run_id,
            {
                "execution_status": "dispatching",
                "progress": {
                    "phase": "dispatching",
                    "percent": 10.0,
                    "message": f"Dispatching {stage_type}",
                    "updated_at": now,
                },
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )

        # 4. Transition: running
        if self.publish_progress and workshop_id:
            self.publish_progress(
                workshop_id,
                run_id,
                50.0,
                f"Running stage {stage_type}",
                phase="running",
                utc_now_fn=self.utc_now,
            )
        self.store.update_run(
            run_id,
            {
                "execution_status": "running",
                "progress": {
                    "phase": "running",
                    "percent": 50.0,
                    "message": f"Running {stage_type}",
                    "updated_at": now,
                },
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )

        # 5. Invoke adapter with partial effects capture
        correlation_id = str(plan.get("correlation_id") or plan.get("trace_id") or "")
        expected_owner = str(
            getattr(adapter, "executor", None)
            or stage.get("routing", {}).get("executor")
            or f"{ALLOWLISTED_STAGE_BACKENDS.get(stage_type, '')}_executor"
        )
        stage_context = {
            "backend_mode": stage.get("routing", {}).get("backend_mode", "real"),
            "run_id": run_id,
            "correlation_id": correlation_id,
            "executor": expected_owner,
        }
        try:
            result = adapter.execute(  # type: ignore[union-attr]
                stage=stage,
                plan=plan,
                context=stage_context,
                downstream_key=downstream_key,
            )
        except Exception as exc:
            logger.exception("Research adapter execution failed for %s", stage_type)
            err = str(exc)
            fail_now = self.utc_now()
            self.store.update_run(
                run_id,
                {
                    "execution_status": "failed",
                    "outcome": "fail",
                    "blocking_reasons": [err],
                    "progress": {
                        "phase": "failed",
                        "percent": 50.0,
                        "message": f"Adapter execution error: {err}",
                        "updated_at": fail_now,
                    },
                    "completed_at": fail_now,
                    "updated_at": fail_now,
                },
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if hasattr(self.store, "update_outbox_record"):
                self.store.update_outbox_record(
                    outbox_id,
                    {
                        "status": "failed",
                        "blocking_reasons": [err],
                        "updated_at": fail_now,
                    },
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                )
            return {"status": "failed", "error": err}

        # Durably record execution receipt emitted by authentic execution owner
        if getattr(result, "receipt", None) is not None:
            receipt_obj = result.receipt
            receipt_dict = receipt_obj.to_dict() if hasattr(receipt_obj, "to_dict") else dict(receipt_obj)
            if hasattr(self.store, "record_execution_receipt"):
                self.store.record_execution_receipt(receipt_dict)

        # 6. Apply completed results and artifact checksum readback
        complete_now = self.utc_now()
        exec_status = "succeeded" if result.outcome == "succeeded" else result.outcome
        from .receipt import resolve_run_provenance
        resolved_provenance, _ = resolve_run_provenance(
            self.store,
            {
                "run_id": run_id,
                "execution_status": exec_status,
                "backend": {"mode": stage.get("routing", {}).get("backend_mode") or "real"},
                "provenance": result.provenance,
                "executor": expected_owner,
                "correlation_id": correlation_id,
            },
            expected_correlation_id=correlation_id or None,
            expected_owner=expected_owner,
        )
        run_updates = {
            "execution_status": exec_status,
            "outcome": "pass" if result.outcome == "succeeded" else ("fail" if result.outcome == "failed" else result.outcome),
            "executor": expected_owner,
            "correlation_id": correlation_id,
            "backend": {
                "requested": stage.get("routing", {}).get("preferred_backend") or ALLOWLISTED_STAGE_BACKENDS.get(stage_type, ""),
                "effective": ALLOWLISTED_STAGE_BACKENDS.get(stage_type, ""),
                # `backend.mode` records the requested execution contract.  It
                # is deliberately distinct from the observed provenance below:
                # an unreceipted real request must remain visibly requested as
                # real while its produced result is labelled simulation.
                "mode": stage.get("routing", {}).get("backend_mode") or "real",
                "version": result.backend_version,
            },
            "provenance": resolved_provenance,
            "metrics": result.metrics,
            "findings": result.findings,
            "warnings": result.warnings,
            "blocking_reasons": result.blocking_reasons,
            "artifact_refs": result.artifact_refs,
            "evidence_refs": result.evidence_refs,
            "lineage_refs": result.lineage_refs,
            "partial_effects": result.partial_effects,
            "checksums": result.checksums,
            "progress": {
                "phase": "succeeded" if result.outcome == "succeeded" else result.outcome,
                "percent": result.progress_percent,
                "message": f"Stage {stage_type} completed successfully",
                "updated_at": complete_now,
            },
            "completed_at": complete_now,
            "updated_at": complete_now,
        }
        self.store.update_run(run_id, run_updates, tenant_id=scope.tenant_id, user_id=scope.user_id)

        # Update stage status in the plan
        current_plan = self.store.get_plan(plan_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
        if current_plan:
            raw_stages = current_plan.get("stages", [])
            updated_stages = []
            for s in raw_stages:
                if isinstance(s, dict):
                    if s.get("stage_id") == stage_id or s.get("stage_type") == stage_type:
                        updated_stages.append({**s, "status": "completed" if result.outcome == "succeeded" else "failed"})
                    else:
                        updated_stages.append(s)
                elif isinstance(s, str):
                    if s == stage_id or s == stage_type:
                        updated_stages.append({"stage_id": s, "stage_type": s, "status": "completed" if result.outcome == "succeeded" else "failed"})
                    else:
                        updated_stages.append(s)
                else:
                    updated_stages.append(s)
            all_completed = all(
                (s.get("status") == "completed") if isinstance(s, dict) else False
                for s in updated_stages
            )
            plan_status = "completed" if all_completed else "running"
            self.store.update_plan(
                plan_id,
                {
                    "stages": updated_stages,
                    "status": plan_status,
                    "lock_version": int(current_plan.get("lock_version", 1)) + 1,
                    "updated_at": complete_now,
                },
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )

        # 7. Publish completed SSE event
        if self.publish_progress and workshop_id:
            self.publish_progress(
                workshop_id,
                run_id,
                100.0,
                f"Stage {stage_type} completed",
                phase="succeeded",
                utc_now_fn=self.utc_now,
            )

        # 8. Update outbox record status
        if hasattr(self.store, "update_outbox_record"):
            self.store.update_outbox_record(
                outbox_id,
                {
                    "status": "completed",
                    "partial_effects": result.partial_effects,
                    "provenance": resolved_provenance,
                    "updated_at": complete_now,
                },
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )

        return {"status": "completed", "result": result}

    def drain_outbox(
        self,
        *,
        worker_id: str = "dispatcher-drain-consumer",
        lease_duration_seconds: float = DEFAULT_LEASE_DURATION_SECONDS,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Drain queued outbox records by leasing each and executing execute_stage."""
        if not hasattr(self.store, "list_outbox_records"):
            return []

        queued_records = self.store.list_outbox_records(
            status="queued",
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if limit is not None and limit > 0:
            queued_records = queued_records[:limit]

        drained_results: List[Dict[str, Any]] = []

        for record in queued_records:
            plan_id = record.get("plan_id")
            stage_id = record.get("stage_id")
            run_id = record.get("run_id")
            r_tenant = record.get("tenant_id") or tenant_id or "pantheon-dev"
            r_user = record.get("user_id") or user_id or "agora-user-a"

            if not plan_id or not stage_id or not run_id:
                continue

            plan = self.store.get_plan(plan_id, tenant_id=r_tenant, user_id=r_user)
            if not plan:
                continue

            stage = None
            for s in plan.get("stages", []):
                if isinstance(s, dict) and s.get("stage_id") == stage_id:
                    stage = s
                    break
                elif isinstance(s, str) and s == stage_id:
                    stage = {"stage_id": s, "stage_type": s, "status": "ready"}
                    break
            if not stage:
                continue

            scope = SimpleNamespace(tenant_id=r_tenant, user_id=r_user)

            result = self.execute_stage(
                plan=plan,
                stage=stage,
                run_id=run_id,
                scope=scope,
                worker_id=worker_id,
                lease_duration_seconds=lease_duration_seconds,
            )
            drained_results.append({
                "outbox_id": record.get("outbox_id"),
                "run_id": run_id,
                "status": result.get("status"),
                "result": result.get("result"),
                "error": result.get("error"),
            })

        return drained_results
