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
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple
import urllib.error
import urllib.request

from .receipt import ResearchExecutionReceipt, resolve_run_provenance, VALID_MODES, VALID_PROVENANCE_VALUES

logger = logging.getLogger(__name__)

# Consolidated at the authoritative Research orchestrator service (services/research/main.py)
from services.research.main import ALLOWLISTED_STAGE_BACKENDS

VALID_PROVENANCE_VALUES = frozenset({"real", "simulation", "fixture", "unavailable"})
DEFAULT_LEASE_DURATION_SECONDS = 60.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_artifact_checksum(payload: Any) -> str:
    """Compute sha256 checksum over deterministic JSON serialization."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def resolve_governed_dataset(
    stage: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
    *,
    dataset_store: Optional[Any] = None,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve canonical input_refs into typed execution inputs through canonical dataset owner."""
    if stage.get("dataset"):
        return stage["dataset"]
    if plan and plan.get("dataset"):
        return plan["dataset"]

    input_refs = stage.get("input_refs") or (plan.get("input_refs") if plan else None)
    if not input_refs or not isinstance(input_refs, (list, tuple)):
        return None
    valid_refs = []
    for r in input_refs:
        if isinstance(r, dict):
            ref_val = r.get("id") or r.get("ref") or r.get("uri")
            if ref_val and str(ref_val).strip():
                valid_refs.append(str(ref_val).strip())
        elif isinstance(r, str) and r.strip():
            valid_refs.append(r.strip())
    if not valid_refs:
        return None

    r_tenant = str(tenant_id or stage.get("tenant_id") or (plan.get("tenant_id") if plan else "") or "").strip()
    r_user = str(user_id or stage.get("user_id") or (plan.get("user_id") if plan else "") or "").strip()

    store = dataset_store
    if store is None:
        try:
            from ..dataset_extraction.router import _default_store
            store = _default_store()
        except Exception:
            try:
                from services.control_plane.bff.agora.dataset_extraction.router import _default_store
                store = _default_store()
            except Exception:
                return None
    if store is None:
        return None

    strategy_id = str((plan.get("strategy_id") if plan else None) or stage.get("strategy_id") or "strategy-default")
    for ref in valid_refs:
        record = None
        if hasattr(store, "get_by_ref"):
            record = store.get_by_ref(ref, tenant_id=r_tenant, user_id=r_user)
        elif hasattr(store, "get"):
            clean_id = ref.split(":", 1)[-1] if ":" in ref else ref
            record = store.get(clean_id, tenant_id=r_tenant, user_id=r_user) or store.get(ref, tenant_id=r_tenant, user_id=r_user)
            if record is None and hasattr(store, "_records"):
                for r in store._records.values():
                    if (not r_tenant or getattr(r, "tenant_id", None) == r_tenant) and (not r_user or getattr(r, "user_id", None) == r_user):
                        if getattr(r, "evidence_id", None) in (ref, clean_id) or getattr(r, "dataset_version_id", None) in (ref, clean_id):
                            record = r
                            break
        if record is not None:
            content = getattr(record, "content", {}) or {}
            clean_id = ref.split(":", 1)[-1] if ":" in ref else ref
            version_id = getattr(record, "dataset_version_id", clean_id)
            ds = dict(content) if isinstance(content, dict) else {"records": content}
            ds.setdefault("dataset_id", ref if ref.startswith("dataset:") else f"dataset:{ref}")
            ds.setdefault("strategy_id", strategy_id)
            ds.setdefault("source_dataset_refs", valid_refs)
            ds.setdefault("dataset_version_id", version_id)
            ds.setdefault("tenant_id", getattr(record, "tenant_id", r_tenant))
            ds.setdefault("user_id", getattr(record, "user_id", r_user))
            ds.setdefault("lineage_ref", f"lineage://agora/dataset/{version_id}")
            if getattr(record, "learning_eligible", None) is not None:
                ds.setdefault("learning_eligible", record.learning_eligible)
            return ds
    return None


def _validate_receipt(
    receipt_val: Any,
    run_id: Optional[str],
    observed_prov: str,
    stage_type: str,
    receipt_label: str = "Owner-emitted",
    provenance_label: str = "stage",
) -> Optional[ResearchExecutionReceipt]:
    if receipt_val is None:
        return None
    receipt = ResearchExecutionReceipt.from_dict(receipt_val) if isinstance(receipt_val, dict) else receipt_val
    if not getattr(receipt, "receipt_id", None):
        raise RuntimeError(f"{receipt_label} receipt missing receipt_id")
    if not getattr(receipt, "completed_at", None):
        raise RuntimeError(f"{receipt_label} receipt missing completed_at timestamp")
    try:
        datetime.fromisoformat(str(receipt.completed_at).replace("Z", "+00:00"))
    except Exception as exc:
        raise RuntimeError(f"{receipt_label} receipt has invalid completed_at timestamp: {receipt.completed_at}") from exc
    if run_id and str(receipt.run_id) != str(run_id):
        raise RuntimeError(f"{receipt_label} receipt run_id mismatch: expected {run_id}, got {receipt.run_id}")
    if str(receipt.mode).lower() not in VALID_MODES:
        raise RuntimeError(f"{receipt_label} receipt has invalid mode: {receipt.mode}")
    if str(receipt.mode).lower() != observed_prov:
        raise RuntimeError(
            f"{receipt_label} receipt mode '{receipt.mode}' contradicts observed {provenance_label} provenance '{observed_prov}' for stage '{stage_type}'."
        )
    if str(getattr(receipt, "spec_version", "1.0")) != "1.0":
        raise RuntimeError(f"{receipt_label} receipt has invalid spec_version: {receipt.spec_version}")
    return receipt


def _validate_real_mode_fields(stage_type: str, backend_ref: Any, checksum: Any, metrics: Any, has_artifact_refs: bool = True) -> None:
    if not backend_ref:
        raise RuntimeError(f"Authentic real execution for stage '{stage_type}' missing backend reference.")
    if not checksum:
        raise RuntimeError(f"Authentic real execution for stage '{stage_type}' missing genuine backend artifact digest.")
    if metrics is None or not isinstance(metrics, list) or len(metrics) == 0:
        raise RuntimeError(f"Authentic real execution for stage '{stage_type}' missing genuine backend metrics.")
    if not has_artifact_refs:
        raise RuntimeError(f"Authentic real execution for stage '{stage_type}' missing genuine owner artifact identities.")


def _validate_and_tag_provenance(metrics: List[Any], evidence_refs: List[Any], observed_prov: str, stage_type: str) -> None:
    for m in metrics:
        if isinstance(m, dict):
            if "provenance" not in m:
                m["provenance"] = observed_prov
            elif observed_prov == "simulation" and str(m.get("provenance", "")).lower() == "real":
                m["provenance"] = "simulation"
            elif str(m.get("provenance", "")).lower() != observed_prov:
                raise RuntimeError(
                    f"Backend metric provenance '{m.get('provenance')}' contradicts observed stage provenance '{observed_prov}' for stage '{stage_type}'."
                )
    for ev in evidence_refs:
        if isinstance(ev, dict) and "provenance" not in ev:
            ev["provenance"] = observed_prov


def _resolve_genuine_artifacts(
    owner_art_id: Optional[str],
    owner_art_refs: Any,
    owner_checksums: Any,
    checksum: Optional[str],
) -> Tuple[List[Any], Dict[str, str]]:
    genuine_refs: List[Any] = []
    genuine_checksums: Dict[str, str] = {}

    if owner_art_refs and isinstance(owner_art_refs, (list, tuple)):
        genuine_refs.extend(owner_art_refs)
    elif owner_art_id:
        genuine_refs.append({"artifact_id": owner_art_id, "ref": f"artifact://{owner_art_id}", "digest": checksum})

    if owner_art_id and not any((r.get("artifact_id") == owner_art_id) if isinstance(r, dict) else (r == owner_art_id) for r in genuine_refs):
        genuine_refs.append({"artifact_id": owner_art_id, "ref": f"artifact://{owner_art_id}", "digest": checksum})

    if owner_checksums and isinstance(owner_checksums, dict):
        for k, v in owner_checksums.items():
            if str(k).lower() != "artifact":
                genuine_checksums[k] = v

    if checksum:
        if owner_art_id:
            genuine_checksums[owner_art_id] = checksum
            genuine_checksums[f"artifact://{owner_art_id}"] = checksum
        for r in genuine_refs:
            if isinstance(r, dict):
                for k in ("artifact_id", "ref_id", "id", "ref"):
                    if r.get(k):
                        genuine_checksums[str(r[k])] = r.get("digest") or checksum
            elif isinstance(r, str):
                genuine_checksums[r] = checksum

    return genuine_refs, genuine_checksums


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
        provenance = "fixture" if requested_mode == "fixture" else ("simulation" if requested_mode in ("simulation", "real") else self.default_provenance)
        if provenance not in VALID_PROVENANCE_VALUES:
            provenance = "unavailable"

        stage_id = stage.get("stage_id", "stage-unknown")
        strategy_id = plan.get("strategy_id", "strategy-unknown")
        backend_job_id = f"job:{self.preferred_backend}:{downstream_key}"
        artifact_id = f"art:{stage_id}:{self.preferred_backend}"
        now_ts = _utc_now_iso()
        artifact_payload = {
            "artifact_id": artifact_id, "stage_id": stage_id, "stage_type": self.stage_type,
            "strategy_id": strategy_id, "backend": self.preferred_backend,
            "backend_job_id": backend_job_id, "provenance": provenance, "created_at": now_ts,
        }
        checksum = compute_artifact_checksum(artifact_payload)
        artifact_ref = f"research-artifact://{self.preferred_backend}/{artifact_id}"
        run_id = str(context.get("run_id") or stage.get("run_id") or "")
        corr_id = str(context.get("correlation_id") or plan.get("correlation_id") or plan.get("trace_id") or "")
        receipt = ResearchExecutionReceipt(
            receipt_id=f"rcpt-{uuid.uuid4().hex[:10]}", run_id=run_id,
            executor=f"{self.preferred_backend}_executor", mode="simulation",
            correlation_id=corr_id, completed_at=now_ts,
            backend_reference=f"{self.preferred_backend}://jobs/{backend_job_id}", artifact_digest=checksum,
        ) if run_id else None

        return ResearchStageResult(
            outcome="succeeded",
            provenance=provenance,
            progress_percent=100.0,
            backend_job_id=backend_job_id,
            backend_version="1.0.0",
            metrics=[{"metric_name": f"{self.stage_type}_execution_score", "value": 1.0, "provenance": provenance}],
            findings=[{"stage_type": self.stage_type, "status": "completed", "backend": self.preferred_backend}],
            warnings=["default_adapter_did_not_execute_real_backend"] if requested_mode == "real" else [],
            blocking_reasons=[],
            artifact_refs=[artifact_ref],
            evidence_refs=[{"ref_type": "research_evidence", "ref_id": f"ev:{stage_id}:{checksum[:8]}", "stage_type": self.stage_type, "provenance": provenance, "checksum": checksum, "as_of": now_ts}],
            lineage_refs=[f"lineage://research/{strategy_id}/{stage_id}/{checksum[:12]}"],
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
            terminal_success_outcomes = {"succeeded", "completed", "passed", "pass"}
            terminal_failure_outcomes = {"failed", "cancelled", "canceled", "inconclusive", "timed_out", "timeout"}
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
            observed_prov = result.provenance if result.provenance in VALID_PROVENANCE_VALUES else self.mode
            result.provenance = observed_prov
            if self.mode == "real" and observed_prov == "fixture":
                raise RuntimeError("Generated fixture inputs must never be promoted to real provenance")
            if observed_prov == "fixture" and getattr(result, "receipt", None) is not None:
                if str(getattr(result.receipt, "mode", "")).lower() in ("real", "simulation"):
                    raise RuntimeError("Generated fixture inputs must never be promoted to real provenance")

            checksum = next(iter(result.checksums.values()), None) if result.checksums else None
            backend_ref = self.backend_reference or getattr(result, "backend_job_id", None) or None
            if self.mode == "real" and observed_prov == "real":
                _validate_real_mode_fields(self.stage_type, backend_ref, checksum, result.metrics, bool(result.artifact_refs))

            result.receipt = _validate_receipt(result.receipt, run_id, observed_prov, self.stage_type, "Owner-emitted", "stage")
            if result.receipt is None and observed_prov == "real":
                observed_prov = "simulation"
                result.provenance = observed_prov

            _validate_and_tag_provenance(result.metrics, result.evidence_refs, observed_prov, self.stage_type)
            return result

        if isinstance(backend_output, dict):
            status_val = str(backend_output.get("status") or backend_output.get("execution_status") or "").lower().strip()
            outcome_val = str(backend_output.get("outcome") or "").lower().strip()
            terminal_success_values = {"succeeded", "completed", "success", "passed", "pass"}
            terminal_failure_values = {"failed", "error", "fail", "cancelled", "canceled", "timed_out", "timeout"}
            nonterminal_values = {"running", "queued", "pending", "in_progress", "scheduled", "dispatching"}

            if status_val in terminal_failure_values or outcome_val in terminal_failure_values:
                err_text = backend_output.get("error") or backend_output.get("error_message") or backend_output.get("message") or f"status={status_val or outcome_val}"
                raise RuntimeError(f"Authentic execution failed for stage '{self.stage_type}': {err_text}")
            if status_val in nonterminal_values or outcome_val in nonterminal_values:
                raise RuntimeError(f"Authentic execution for stage '{self.stage_type}' returned nonterminal status '{status_val or outcome_val}'.")
            if not (status_val in terminal_success_values or outcome_val in terminal_success_values):
                raise RuntimeError(
                    f"Authentic execution for stage '{self.stage_type}' returned invalid or nonterminal status "
                    f"'{status_val or outcome_val or 'absent'}'. Explicit validated terminal success is required."
                )

            observed_prov = backend_output.get("provenance") or self.mode
            if observed_prov not in VALID_PROVENANCE_VALUES:
                observed_prov = "unavailable"

            backend_ref = backend_output.get("backend_reference") or self.backend_reference
            checksum = backend_output.get("artifact_digest") or backend_output.get("checksum")
            raw_metrics = backend_output.get("metrics")

            if self.mode == "real" and observed_prov == "real":
                _validate_real_mode_fields(self.stage_type, backend_ref, checksum, raw_metrics, has_artifact_refs=True)

            receipt = _validate_receipt(backend_output.get("receipt"), run_id, observed_prov, self.stage_type, "Owner-emitted", "stage")
            if receipt is None and observed_prov == "real":
                observed_prov = "simulation"

            result = super().execute(stage=stage, plan=plan, context=context, downstream_key=downstream_key)
            result.receipt = receipt
            result.provenance = observed_prov
            result.metrics = list(raw_metrics) if raw_metrics is not None and isinstance(raw_metrics, list) else []

            genuine_refs, genuine_checksums = _resolve_genuine_artifacts(
                owner_art_id=backend_output.get("artifact_id"),
                owner_art_refs=backend_output.get("artifact_refs") or backend_output.get("artifacts"),
                owner_checksums=backend_output.get("checksums"),
                checksum=checksum,
            )
            result.artifact_refs = genuine_refs
            result.checksums = genuine_checksums
            result.evidence_refs = list(backend_output.get("evidence_refs") or [])
            result.lineage_refs = list(backend_output.get("lineage_refs") or [])
            _validate_and_tag_provenance(result.metrics, result.evidence_refs, observed_prov, self.stage_type)
            return result

        # Fallback for simulation mode only when no backend output provided
        result = super().execute(stage=stage, plan=plan, context=context, downstream_key=downstream_key)
        result.receipt = None
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


class AuthenticResearchBackendClient:
    """Authentic research execution owner client producing verified terminal results and receipts."""

    def __init__(
        self,
        stage_type: str,
        preferred_backend: str,
        *,
        executor: Optional[str] = None,
        base_url: Optional[str] = None,
        backend_fn: Optional[Callable[..., Any]] = None,
        transport: Optional[Callable[[urllib.request.Request], Any]] = None,
    ) -> None:
        self.stage_type = stage_type
        self.preferred_backend = preferred_backend
        self.executor = executor or f"{preferred_backend}_executor"
        self.base_url = (
            base_url
            or os.getenv(f"AGORA_RESEARCH_{preferred_backend.upper()}_URL")
            or os.getenv("AGORA_RESEARCH_BACKEND_URL")
        )
        self.backend_fn = backend_fn
        self._transport = transport

    def execute(
        self,
        *,
        stage: Dict[str, Any],
        plan: Dict[str, Any],
        context: Dict[str, Any],
        downstream_key: str,
    ) -> Dict[str, Any]:
        run_id = str(context.get("run_id") or stage.get("run_id") or "")
        correlation_id = str(
            context.get("correlation_id")
            or stage.get("correlation_id")
            or plan.get("correlation_id")
            or plan.get("trace_id")
            or (f"workshop:{plan.get('workshop_id')}" if plan.get("workshop_id") else "")
            or (f"plan:{plan.get('plan_id')}" if plan.get("plan_id") else "")
            or ""
        )

        if self.backend_fn is not None:
            resp_data = self.backend_fn(
                stage=stage,
                plan=plan,
                context=context,
                downstream_key=downstream_key,
            )
        else:
            if not self.base_url and not self._transport:
                raise RuntimeError(
                    f"Backend execution owner for stage '{self.stage_type}' ({self.preferred_backend}) is absent: "
                    f"neither base_url (AGORA_RESEARCH_{self.preferred_backend.upper()}_URL) nor backend_fn is configured."
                )

            stage_payload = dict(stage)
            plan_payload = dict(plan)
            if not stage_payload.get("correlation_id") and correlation_id:
                stage_payload["correlation_id"] = correlation_id
            if not plan_payload.get("correlation_id") and correlation_id:
                plan_payload["correlation_id"] = correlation_id

            # Resolve canonical input_refs into execution inputs (dataset) if absent
            if not stage_payload.get("dataset") and not plan_payload.get("dataset"):
                resolved_ds = resolve_governed_dataset(
                    stage_payload,
                    plan_payload,
                    dataset_store=context.get("dataset_store") if isinstance(context, dict) else None,
                    tenant_id=(context.get("tenant_id") if isinstance(context, dict) else None) or plan_payload.get("tenant_id"),
                    user_id=(context.get("user_id") if isinstance(context, dict) else None) or plan_payload.get("user_id"),
                )
                if resolved_ds:
                    stage_payload["dataset"] = resolved_ds
                    if isinstance(stage, dict) and "dataset" not in stage:
                        stage["dataset"] = resolved_ds

            payload = {
                "stage_type": self.stage_type,
                "preferred_backend": self.preferred_backend,
                "stage": stage_payload,
                "plan": plan_payload,
                "context": context,
                "downstream_key": downstream_key,
                "run_id": run_id,
                "correlation_id": correlation_id,
                "dataset": stage_payload.get("dataset") or plan_payload.get("dataset"),
            }
            body_bytes = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")

            raw_url = str(self.base_url or "http://agora-research-backend").rstrip("/")
            if not raw_url.endswith(f"/stages/{self.stage_type}/execute") and not raw_url.endswith("/execute"):
                target_url = f"{raw_url}/stages/{self.stage_type}/execute"
            else:
                target_url = raw_url

            req = urllib.request.Request(
                target_url,
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Correlation-Id": correlation_id,
                    "X-Run-Id": run_id,
                },
                method="POST",
            )

            transport = self._transport or urllib.request.urlopen
            try:
                resp = transport(req)
                if hasattr(resp, "read"):
                    raw_bytes = resp.read()
                elif isinstance(resp, (bytes, str)):
                    raw_bytes = resp
                else:
                    raw_bytes = resp
                if isinstance(raw_bytes, bytes):
                    raw_bytes = raw_bytes.decode("utf-8")
                resp_data = json.loads(raw_bytes) if isinstance(raw_bytes, str) else raw_bytes
            except Exception as exc:
                raise RuntimeError(
                    f"Backend execution owner submission/readback failed for stage '{self.stage_type}' "
                    f"at '{target_url}': {exc}"
                ) from exc

        if not isinstance(resp_data, dict):
            raise RuntimeError(
                f"Backend execution owner for stage '{self.stage_type}' returned non-dict response: {type(resp_data)}"
            )

        raw_status = resp_data.get("status") or resp_data.get("execution_status")
        raw_outcome = resp_data.get("outcome")
        status_val = str(raw_status or "").lower().strip()
        outcome_val = str(raw_outcome or "").lower().strip()

        terminal_success_values = {"succeeded", "completed", "success", "passed", "pass"}
        terminal_failure_values = {"failed", "error", "fail", "cancelled", "canceled", "timed_out", "timeout"}
        nonterminal_values = {"running", "queued", "pending", "in_progress", "scheduled", "dispatching"}

        # Reject failure statuses
        if status_val in terminal_failure_values or outcome_val in terminal_failure_values:
            err = (
                resp_data.get("error")
                or resp_data.get("error_message")
                or resp_data.get("message")
                or f"status={status_val or outcome_val}"
            )
            raise RuntimeError(
                f"Backend execution owner returned failure outcome for stage '{self.stage_type}': {err}"
            )

        # Reject nonterminal statuses
        if status_val in nonterminal_values or outcome_val in nonterminal_values:
            raise RuntimeError(
                f"Backend execution owner for stage '{self.stage_type}' returned nonterminal status '{status_val or outcome_val}'."
            )

        # Reject unknown/invalid statuses
        if status_val and status_val not in terminal_success_values:
            raise RuntimeError(
                f"Backend execution owner returned invalid or unrecognized status '{status_val}' for stage '{self.stage_type}'."
            )
        if outcome_val and outcome_val not in terminal_success_values:
            raise RuntimeError(
                f"Backend execution owner returned invalid or unrecognized outcome '{outcome_val}' for stage '{self.stage_type}'."
            )
        if not status_val and not outcome_val:
            raise RuntimeError(
                f"Backend execution owner for stage '{self.stage_type}' returned missing status and outcome."
            )

        backend_ref = str(resp_data.get("backend_reference") or "").strip()
        if not backend_ref:
            raise RuntimeError(f"Backend execution owner response for stage '{self.stage_type}' missing backend_reference")

        artifact_digest = str(resp_data.get("artifact_digest") or resp_data.get("checksum") or "").strip()
        if not artifact_digest:
            raise RuntimeError(f"Backend execution owner response for stage '{self.stage_type}' missing artifact_digest")

        metrics = resp_data.get("metrics")
        if not metrics or not isinstance(metrics, list) or len(metrics) == 0:
            raise RuntimeError(f"Backend execution owner response for stage '{self.stage_type}' missing genuine metrics")

        observed_prov = str(resp_data.get("provenance") or "").lower().strip()
        if not observed_prov:
            observed_prov = "simulation"
        elif observed_prov not in VALID_PROVENANCE_VALUES:
            observed_prov = "unavailable"

        receipt = _validate_receipt(resp_data.get("receipt"), run_id, observed_prov, self.stage_type, "Backend", "backend")
        if receipt is None and observed_prov == "real":
            observed_prov = "simulation"

        metrics_list = list(metrics)
        _validate_and_tag_provenance(metrics_list, [], observed_prov, self.stage_type)

        artifact_id = str(resp_data.get("artifact_id") or "").strip()
        artifact_refs = resp_data.get("artifact_refs")
        if artifact_refs is None and resp_data.get("artifacts") is not None:
            artifact_refs = resp_data.get("artifacts")

        genuine_refs, genuine_checksums = _resolve_genuine_artifacts(
            owner_art_id=artifact_id,
            owner_art_refs=artifact_refs,
            owner_checksums=resp_data.get("checksums"),
            checksum=artifact_digest,
        )

        return {
            "status": "succeeded",
            "outcome": "succeeded",
            "backend_reference": backend_ref,
            "artifact_id": artifact_id,
            "artifact_digest": artifact_digest,
            "artifact_refs": genuine_refs,
            "checksums": genuine_checksums,
            "metrics": metrics_list,
            "receipt": receipt,
            "provenance": observed_prov,
        }


def build_canonical_research_backend_clients(
    *,
    mode: str = "real",
    required_stages: Optional[Sequence[str]] = None,
    backend_fn_overrides: Optional[Dict[str, Callable[..., Any]]] = None,
    backend_base_urls: Optional[Dict[str, str]] = None,
    default_base_url: Optional[str] = None,
    transports: Optional[Dict[str, Callable[..., Any]]] = None,
    default_transport: Optional[Callable[..., Any]] = None,
    allow_missing_endpoints: bool = False,
) -> Dict[str, AuthenticResearchBackendClient]:
    """Construct real research backend clients for allowlisted stages.

    Fails startup if any required client cannot be constructed.
    """
    if os.getenv("AGORA_RESEARCH_FAIL_BACKEND_CLIENT") in ("1", "true", "yes"):
        raise RuntimeError("Configured failure: cannot construct required research backend client")

    clients: Dict[str, AuthenticResearchBackendClient] = {}
    overrides = backend_fn_overrides or {}
    base_urls = backend_base_urls or {}
    custom_transports = transports or {}
    stages_to_build = required_stages or list(ALLOWLISTED_STAGE_BACKENDS.keys())

    for stage_type in stages_to_build:
        backend = ALLOWLISTED_STAGE_BACKENDS.get(stage_type)
        if not backend:
            raise RuntimeError(f"Unknown allowlisted backend for stage '{stage_type}'")
        base_url = (
            base_urls.get(stage_type)
            or os.getenv(f"AGORA_RESEARCH_{backend.upper()}_URL")
            or os.getenv("AGORA_RESEARCH_BACKEND_URL")
            or default_base_url
        )
        transport = custom_transports.get(stage_type) or default_transport
        client = AuthenticResearchBackendClient(
            stage_type=stage_type,
            preferred_backend=backend,
            executor=f"{backend}_executor",
            base_url=base_url,
            backend_fn=overrides.get(stage_type),
            transport=transport,
        )
        if mode == "real" and not allow_missing_endpoints:
            if not client.base_url and not client.backend_fn and not client._transport:
                raise RuntimeError(
                    f"Backend execution owner for stage '{stage_type}' ({backend}) is absent: "
                    f"neither base_url (AGORA_RESEARCH_{backend.upper()}_URL / AGORA_RESEARCH_BACKEND_URL) nor backend_fn is configured."
                )
        clients[stage_type] = client

    return clients


def build_authentic_adapter_registry(
    *,
    mode: Literal["real", "simulation"] = "real",
    execution_owners: Optional[Dict[str, Any]] = None,
    default_backend_fn: Optional[Callable[..., Any]] = None,
    backend_base_urls: Optional[Dict[str, str]] = None,
    default_base_url: Optional[str] = None,
    transports: Optional[Dict[str, Callable[..., Any]]] = None,
    default_transport: Optional[Callable[..., Any]] = None,
    allow_missing_endpoints: bool = True,
) -> AdapterRegistry:
    """Build an AdapterRegistry populated with authentic stage adapters for all allowlisted stages."""
    registry = AdapterRegistry()
    owners = execution_owners
    if owners is None and mode == "real" and default_backend_fn is None:
        owners = build_canonical_research_backend_clients(
            mode=mode,
            backend_base_urls=backend_base_urls,
            default_base_url=default_base_url,
            transports=transports,
            default_transport=default_transport,
            allow_missing_endpoints=allow_missing_endpoints,
        )
    owners = owners or {}
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
