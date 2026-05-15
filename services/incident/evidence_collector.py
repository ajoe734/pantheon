"""
PostmortemEvidenceCollector — P1-EVO-001

Assembles telemetry, runtime-binding, deployment, artifact, and capital evidence
into an IncidentCase and a structured EvidenceBundle ready for Postmortem
creation.

This module closes PM-GAP-001 (EvidenceCollector not verified) from SA-17.

Evidence chain
--------------
TelemetryEvidence + RuntimeBindingEvidence + DeploymentEvidence
  → EvidenceBundle
  → IncidentCase (with all required evidence fields populated)
  → Postmortem (propagates the same fields from IncidentCase)
  → EvolutionDecision.linked_postmortem_id (set by EVO-003 / evolution controller)

Write authority: Incident domain only.
No other plane may write IncidentCase or Postmortem records.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.incident.incident import (
    IncidentCase,
    IncidentError,
    IncidentSeverity,
    IncidentStatus,
    validate_incident_case,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Evidence input types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryEvidence:
    """
    Telemetry event references gathered for an incident.

    telemetry_event_ids    : canonical event IDs from the telemetry ingest store
    heartbeat_lag_ms       : last observed heartbeat lag (None if unavailable)
    pnl_snapshot_refs      : list of pnl_snapshot event IDs
    order_rejection_refs   : list of order_rejection event IDs
    fill_observation_refs  : list of fill_observation event IDs
    """
    telemetry_event_ids: List[str]
    heartbeat_lag_ms: Optional[float] = None
    pnl_snapshot_refs: List[str] = field(default_factory=list)
    order_rejection_refs: List[str] = field(default_factory=list)
    fill_observation_refs: List[str] = field(default_factory=list)

    def all_event_ids(self) -> List[str]:
        seen: Dict[str, None] = {}
        for eid in (
            self.telemetry_event_ids
            + self.pnl_snapshot_refs
            + self.order_rejection_refs
            + self.fill_observation_refs
        ):
            seen[eid] = None
        return list(seen)


@dataclass(frozen=True)
class RuntimeBindingEvidence:
    """
    Runtime binding context snapshot captured at incident creation time.

    These fields are denormalized copies of the canonical RuntimeBinding state
    so that the incident is forensically complete without querying a live binding.
    """
    binding_id: str
    runtime_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    trace_id: str


@dataclass(frozen=True)
class EvidenceBundle:
    """
    Complete evidence package assembled by PostmortemEvidenceCollector.

    Carries all data needed to create a forensically complete IncidentCase
    and a subsequent Postmortem.

    Fields
    ------
    runtime_binding  : RuntimeBindingEvidence — identity and deployment context
    telemetry        : TelemetryEvidence — event refs collected at incident time
    evidence_summary : human-readable summary of what was collected
    lineage_ref      : composite lineage ref (artifact_id@artifact_version)
    """
    runtime_binding: RuntimeBindingEvidence
    telemetry: TelemetryEvidence
    evidence_summary: str
    lineage_ref: str


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class EvidenceCollectionError(ValueError):
    """Raised when evidence assembly fails validation."""


class PostmortemEvidenceCollector:
    """
    Assembles telemetry, runtime-binding, deployment, artifact, and capital
    evidence into a structured EvidenceBundle and creates a validated IncidentCase.

    Acceptance: P1-EVO-001 — "IncidentCase gathers telemetry/runtime
    binding/deployment/artifact/capital evidence"

    Usage
    -----
    collector = PostmortemEvidenceCollector()

    bundle = collector.assemble_bundle(
        runtime_binding=RuntimeBindingEvidence(...),
        telemetry=TelemetryEvidence(telemetry_event_ids=["evt-001", "evt-002"]),
    )

    incident = collector.create_incident(
        bundle=bundle,
        title="Drawdown threshold breached",
        severity=IncidentSeverity.HIGH,
    )
    """

    def assemble_bundle(
        self,
        *,
        runtime_binding: RuntimeBindingEvidence,
        telemetry: TelemetryEvidence,
        extra_notes: Optional[str] = None,
    ) -> EvidenceBundle:
        """
        Assemble a complete EvidenceBundle from runtime binding and telemetry.

        Validates that all required identity fields are present before producing
        the bundle.  Raises EvidenceCollectionError on missing required fields.
        """
        self._validate_runtime_binding(runtime_binding)
        self._validate_telemetry(telemetry)

        all_event_ids = telemetry.all_event_ids()
        parts: List[str] = [
            f"binding={runtime_binding.binding_id}",
            f"stage={runtime_binding.deployment_stage}",
            f"artifact={runtime_binding.artifact_id}@{runtime_binding.artifact_version}",
            f"pool={runtime_binding.capital_pool_id}",
            f"telemetry_events={len(all_event_ids)}",
        ]
        if runtime_binding.trace_id:
            parts.append(f"trace={runtime_binding.trace_id}")
        if telemetry.heartbeat_lag_ms is not None:
            parts.append(f"heartbeat_lag_ms={telemetry.heartbeat_lag_ms}")
        if extra_notes:
            parts.append(extra_notes)

        return EvidenceBundle(
            runtime_binding=runtime_binding,
            telemetry=telemetry,
            evidence_summary="; ".join(parts),
            lineage_ref=f"{runtime_binding.artifact_id}@{runtime_binding.artifact_version}",
        )

    def create_incident(
        self,
        *,
        bundle: EvidenceBundle,
        title: str,
        severity: IncidentSeverity | str,
        incident_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> IncidentCase:
        """
        Create a validated IncidentCase from an EvidenceBundle.

        All required evidence fields (binding_id, deployment_stage,
        deployment_plan_id, capital_pool_id, persona_capital_binding_id,
        artifact_id, artifact_version, runtime_id, trace_id) are taken from
        the bundle so the caller cannot accidentally omit them.

        Returns a validated IncidentCase ready for insertion into IncidentStore.
        Raises EvidenceCollectionError if validation fails.
        """
        rb = bundle.runtime_binding
        all_event_ids = bundle.telemetry.all_event_ids()

        severity_value = severity.value if isinstance(severity, IncidentSeverity) else severity

        incident = IncidentCase(
            incident_id=incident_id or _new_id("inc"),
            title=title,
            status=IncidentStatus.OPEN.value,
            severity=severity_value,
            created_at=created_at or _utc_now(),
            binding_id=rb.binding_id,
            deployment_stage=rb.deployment_stage,
            deployment_plan_id=rb.deployment_plan_id,
            capital_pool_id=rb.capital_pool_id,
            persona_capital_binding_id=rb.persona_capital_binding_id,
            artifact_id=rb.artifact_id,
            artifact_version=rb.artifact_version,
            runtime_id=rb.runtime_id,
            trace_id=rb.trace_id,
            telemetry_event_ids=all_event_ids,
            evidence_summary=bundle.evidence_summary,
            lineage_ref=bundle.lineage_ref,
        )

        errors = validate_incident_case(incident)
        if errors:
            raise EvidenceCollectionError(
                f"Evidence assembly produced an invalid IncidentCase: {errors}"
            )
        return incident

    # ---- Private validators ------------------------------------------------

    @staticmethod
    def _validate_runtime_binding(rb: RuntimeBindingEvidence) -> None:
        missing: List[str] = []
        for attr in (
            "binding_id",
            "runtime_id",
            "deployment_stage",
            "deployment_plan_id",
            "capital_pool_id",
            "persona_capital_binding_id",
            "artifact_id",
            "artifact_version",
            "trace_id",
        ):
            if not getattr(rb, attr, None):
                missing.append(attr)
        if missing:
            raise EvidenceCollectionError(
                f"RuntimeBindingEvidence is missing required fields: {missing}"
            )
        valid_stages = {"paper", "canary", "live", "frozen"}
        if rb.deployment_stage not in valid_stages:
            raise EvidenceCollectionError(
                f"deployment_stage must be one of {sorted(valid_stages)}, "
                f"got {rb.deployment_stage!r}"
            )

    @staticmethod
    def _validate_telemetry(tel: TelemetryEvidence) -> None:
        if not tel.all_event_ids():
            raise EvidenceCollectionError(
                "TelemetryEvidence must carry at least one telemetry_event_id"
            )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def build_evidence_bundle(
    *,
    binding_id: str,
    runtime_id: str,
    deployment_stage: str,
    deployment_plan_id: str,
    capital_pool_id: str,
    persona_capital_binding_id: str,
    artifact_id: str,
    artifact_version: str,
    trace_id: str,
    telemetry_event_ids: List[str],
    heartbeat_lag_ms: Optional[float] = None,
    extra_notes: Optional[str] = None,
) -> EvidenceBundle:
    """
    Convenience factory that assembles an EvidenceBundle from flat keyword args.

    Useful for callers that already have individual field values and do not want
    to construct RuntimeBindingEvidence and TelemetryEvidence manually.
    """
    collector = PostmortemEvidenceCollector()
    rb = RuntimeBindingEvidence(
        binding_id=binding_id,
        runtime_id=runtime_id,
        deployment_stage=deployment_stage,
        deployment_plan_id=deployment_plan_id,
        capital_pool_id=capital_pool_id,
        persona_capital_binding_id=persona_capital_binding_id,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        trace_id=trace_id,
    )
    tel = TelemetryEvidence(
        telemetry_event_ids=telemetry_event_ids,
        heartbeat_lag_ms=heartbeat_lag_ms,
    )
    return collector.assemble_bundle(
        runtime_binding=rb,
        telemetry=tel,
        extra_notes=extra_notes,
    )
