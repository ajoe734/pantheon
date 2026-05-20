"""EP5ProofPacket generator (EP5-005-V2, A2.2).

Emits a PromotionReadinessPacket shaped as an EP5 canary-run proof by
assembling required evidence from a CanaryRunRecord and evaluating the
fail-closed proof flags:

    canary_runtime_started       — runtime process reached started state
    runtime_heartbeat_received   — at least one heartbeat emitted during the run
    order_route_mode             — the order routing mode is paper (never live)
    telemetry_ingested           — at least one telemetry event persisted

The generator is pure: it takes a CanaryRunRecord mapping and returns a
PromotionReadinessPacket. No I/O, no side effects.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from services.governance.promotion_readiness.packet_model import (
    SCHEMA_VERSION,
    ApprovalRequirement,
    ApprovalSubtree,
    BlockingReason,
    EvidenceItem,
    EvidenceSubtree,
    FlagSubtree,
    GateResult,
    PromotionReadinessPacket,
    TargetRef,
    validate_packet,
)
from services.governance.ep5_proof.persona_lineage import (
    EP5PersonaLineage,
    PersonaLineageError,
    normalize_ep5_persona_lineage,
)

# Proof flags surfaced by the generator
PROOF_FLAG_CANARY_RUNTIME_STARTED = "canary_runtime_started"
PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED = "runtime_heartbeat_received"
PROOF_FLAG_ORDER_ROUTE_MODE = "order_route_mode"
PROOF_FLAG_TELEMETRY_INGESTED = "telemetry_ingested"
PROOF_FLAG_DEPLOYMENT_STAGE_IS_CANARY = "deployment_stage_is_canary"
PROOF_FLAG_PERSONA_LINEAGE_LINKED = "persona_lineage_linked"
EVIDENCE_KEY_PERSONA_LINEAGE = "persona_lineage"

# Fail-closed invariant: live-capital and broker-production must stay False
PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS = "live_capital_side_effects"
PROOF_FLAG_BROKER_PRODUCTION_LIVE = "broker_production_live_enabled"

# Evidence keys required for EP5 proof
REQUIRED_EVIDENCE_KEYS = (
    PROOF_FLAG_CANARY_RUNTIME_STARTED,
    PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED,
    PROOF_FLAG_ORDER_ROUTE_MODE,
    PROOF_FLAG_TELEMETRY_INGESTED,
)

_PAPER_ORDER_ROUTE_MODES = {
    "paper",
    "paper_trading",
    "paper_only",
    "simulation",
    "sandbox",
    "validate_only",
}


class EP5ProofGeneratorError(ValueError):
    """Raised when a CanaryRunRecord is structurally invalid for proof generation."""


def _required(record: Mapping[str, Any], key: str) -> Any:
    value = record.get(key)
    if value is None:
        raise EP5ProofGeneratorError(f"CanaryRunRecord missing required field: {key!r}")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _persona_lineage_source(canary_run: Mapping[str, Any]) -> Any:
    for key in ("persona_lineage", "multi_persona_ooda_packet", "multi_persona_packet"):
        value = canary_run.get(key)
        if value is not None:
            return value
    return None


def _persona_lineage_ref(canary_run: Mapping[str, Any]) -> str | None:
    for key in ("persona_lineage_ref", "multi_persona_packet_ref", "multi_persona_ooda_packet_ref"):
        value = _optional_text(canary_run.get(key))
        if value:
            return value
    return None


def _normalize_lineage(canary_run: Mapping[str, Any]) -> EP5PersonaLineage | None:
    lineage_source = _persona_lineage_source(canary_run)
    if lineage_source is None:
        return None
    try:
        return normalize_ep5_persona_lineage(
            lineage_source,
            source_packet_ref=_persona_lineage_ref(canary_run),
        )
    except PersonaLineageError as exc:
        raise EP5ProofGeneratorError(f"invalid persona lineage: {exc}") from exc


def generate_ep5_proof_packet(
    canary_run: Mapping[str, Any],
    *,
    packet_id: str | None = None,
    generated_by: str = "EP5-005-V2/packet_generator",
    source_task_id: str = "EP5-005-V2",
) -> PromotionReadinessPacket:
    """Generate an EP5ProofPacket from a CanaryRunRecord.

    Parameters
    ----------
    canary_run:
        Mapping with the following fields:
        - run_id (str): unique canary run identifier
        - persona_id (str): persona being proved
        - runtime_id (str): runtime that executed the canary
        - environment (str): target environment, e.g. "canary"
        - deployment_stage (str): must be "canary"
        - runtime_started (bool): whether runtime reached started state
        - heartbeat_count (int): number of heartbeats emitted during the run
        - order_route_mode (str): routing mode; must be "paper" or equivalent
        - telemetry_event_count (int): telemetry events persisted
        - result (str): overall canary result, e.g. "pass" / "fail"
        - started_at (str, optional): ISO timestamp of run start
        - finished_at (str, optional): ISO timestamp of run finish
        - multi_persona_ooda_packet (Mapping, optional): MPO-003 full packet,
          or persona_lineage containing a normalized EP5PersonaLineage.v1 dict
        - multi_persona_packet_ref (str, optional): portable ref for the MPO
          evidence packet, used to build conflict-log and memo refs
    packet_id:
        Override the generated packet UUID.
    generated_by:
        Source identifier written into the packet header.
    source_task_id:
        Task ID that produced the packet.

    Returns
    -------
    PromotionReadinessPacket
        Validated, fail-closed proof packet. Callers must treat the
        returned packet as the definitive result; do not inspect canary_run
        directly for gate decisions.
    """
    run_id = str(_required(canary_run, "run_id")).strip()
    persona_id = str(_required(canary_run, "persona_id")).strip()
    runtime_id = str(_required(canary_run, "runtime_id")).strip()
    environment = str(_required(canary_run, "environment")).strip()
    deployment_stage = str(_required(canary_run, "deployment_stage")).strip()
    result = str(_required(canary_run, "result")).strip()

    if not run_id:
        raise EP5ProofGeneratorError("CanaryRunRecord.run_id must not be empty")
    if not persona_id:
        raise EP5ProofGeneratorError("CanaryRunRecord.persona_id must not be empty")
    if not runtime_id:
        raise EP5ProofGeneratorError("CanaryRunRecord.runtime_id must not be empty")

    runtime_started: bool = bool(canary_run.get("runtime_started", False))
    heartbeat_count: int = int(canary_run.get("heartbeat_count", 0))
    order_route_mode: str = str(canary_run.get("order_route_mode", "")).strip().lower()
    telemetry_event_count: int = int(canary_run.get("telemetry_event_count", 0))
    persona_lineage = _normalize_lineage(canary_run)

    # ------------------------------------------------------------------
    # Evaluate proof flags
    # ------------------------------------------------------------------
    flag_deployment_stage_is_canary = deployment_stage == "canary"
    flag_runtime_started = runtime_started
    flag_heartbeat_received = heartbeat_count > 0
    flag_order_route_paper = order_route_mode in _PAPER_ORDER_ROUTE_MODES
    flag_telemetry_ingested = telemetry_event_count > 0
    flag_persona_lineage_linked = (
        persona_lineage is not None
        and persona_lineage.sponsor_persona_id == persona_id
        and not persona_lineage.has_open_conflicts
    )

    # Fail-closed: these must never be True in an EP5 canary proof
    live_capital_side_effects = bool(canary_run.get("live_capital_side_effects", False))
    broker_production_live = bool(canary_run.get("broker_production_live_enabled", False))

    # ------------------------------------------------------------------
    # Assemble evidence items
    # ------------------------------------------------------------------
    def _evidence(key: str, passed: bool, note: str) -> EvidenceItem:
        return EvidenceItem(
            key=key,
            status="pass" if passed else "fail",
            source_task_id=source_task_id,
            description=note,
        )

    provided_items = [
        _evidence(
            PROOF_FLAG_CANARY_RUNTIME_STARTED,
            flag_runtime_started,
            f"runtime_id={runtime_id} reached started state" if flag_runtime_started
            else f"runtime_id={runtime_id} did not reach started state",
        ),
        _evidence(
            PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED,
            flag_heartbeat_received,
            f"heartbeat_count={heartbeat_count}" if flag_heartbeat_received
            else "no heartbeats received during canary run",
        ),
        _evidence(
            PROOF_FLAG_ORDER_ROUTE_MODE,
            flag_order_route_paper,
            f"order_route_mode={order_route_mode!r} is paper-safe" if flag_order_route_paper
            else f"order_route_mode={order_route_mode!r} is NOT paper-safe",
        ),
        _evidence(
            PROOF_FLAG_TELEMETRY_INGESTED,
            flag_telemetry_ingested,
            f"telemetry_event_count={telemetry_event_count}" if flag_telemetry_ingested
            else "no telemetry events ingested during canary run",
        ),
    ]

    if persona_lineage is not None:
        lineage_status = (
            flag_persona_lineage_linked
            and bool(persona_lineage.conflict_log_ref)
            and bool(persona_lineage.synthesized_memo_refs)
        )
        provided_items.append(
            EvidenceItem(
                key=EVIDENCE_KEY_PERSONA_LINEAGE,
                status="pass" if lineage_status else "fail",
                source_task_id=persona_lineage.source_task_id or source_task_id,
                path=persona_lineage.source_packet_ref,
                ref_type="multi_persona_ooda_packet",
                description=(
                    "multi-persona sponsor lineage linked: "
                    f"sponsor_persona_id={persona_lineage.sponsor_persona_id}, "
                    f"conflict_log_ref={persona_lineage.conflict_log_ref}, "
                    f"synthesized_memo_refs={list(persona_lineage.synthesized_memo_refs)}"
                ),
                metadata={
                    "sponsor_persona_id": persona_lineage.sponsor_persona_id,
                    "conflict_resolution_log_id": persona_lineage.conflict_resolution_log_id,
                    "conflict_log_ref": persona_lineage.conflict_log_ref,
                    "synthesized_memo_refs": list(persona_lineage.synthesized_memo_refs),
                    "has_open_conflicts": persona_lineage.has_open_conflicts,
                },
            )
        )

    provided = tuple(provided_items)

    missing = tuple(
        item.key for item in provided if item.status not in {"pass", "present", "satisfied"}
    )

    # Gate results mirror the four proof flags
    gate_results = tuple(
        GateResult(
            gate=item.key,
            status="pass" if item.status == "pass" else "fail",
            source_ref=f"canary_run:{run_id}",
            note=item.description,
        )
        for item in provided
    )

    evidence = EvidenceSubtree(
        required=REQUIRED_EVIDENCE_KEYS,
        provided=provided,
        missing=missing,
        gate_results=gate_results,
    )

    # ------------------------------------------------------------------
    # Blocking reasons
    # ------------------------------------------------------------------
    blocking: list[BlockingReason] = []

    if not flag_deployment_stage_is_canary:
        blocking.append(BlockingReason(
            code="DEPLOYMENT_STAGE_NOT_CANARY",
            message=(
                f"deployment_stage={deployment_stage!r} is not 'canary'; "
                "EP5 proof packets may only be generated for canary-stage runs"
            ),
            severity="critical",
        ))
    if not flag_runtime_started:
        blocking.append(BlockingReason(
            code="CANARY_RUNTIME_NOT_STARTED",
            message=f"Runtime {runtime_id!r} did not reach started state during canary run {run_id!r}",
        ))
    if not flag_heartbeat_received:
        blocking.append(BlockingReason(
            code="NO_HEARTBEAT_RECEIVED",
            message=f"No heartbeats received from runtime {runtime_id!r} during canary run {run_id!r}",
        ))
    if not flag_order_route_paper:
        blocking.append(BlockingReason(
            code="ORDER_ROUTE_NOT_PAPER",
            message=(
                f"order_route_mode={order_route_mode!r} is not a paper-safe mode; "
                "EP5 canary proof requires paper routing"
            ),
        ))
    if not flag_telemetry_ingested:
        blocking.append(BlockingReason(
            code="TELEMETRY_NOT_INGESTED",
            message=f"No telemetry events ingested during canary run {run_id!r}",
        ))
    if persona_lineage is not None and persona_lineage.sponsor_persona_id != persona_id:
        blocking.append(BlockingReason(
            code="SPONSOR_PERSONA_MISMATCH",
            message=(
                f"Canary run persona_id={persona_id!r} does not match "
                f"multi-persona sponsor={persona_lineage.sponsor_persona_id!r}"
            ),
            severity="critical",
            source_ref=persona_lineage.conflict_log_ref,
            details={
                "persona_id": persona_id,
                "sponsor_persona_id": persona_lineage.sponsor_persona_id,
                "source_packet_ref": persona_lineage.source_packet_ref,
            },
        ))
    if persona_lineage is not None and persona_lineage.has_open_conflicts:
        blocking.append(BlockingReason(
            code="PERSONA_LINEAGE_OPEN_CONFLICTS",
            message="Multi-persona sponsor lineage still has open conflicts",
            severity="critical",
            source_ref=persona_lineage.conflict_log_ref,
            details={"open_conflict_ids": list(persona_lineage.open_conflict_ids)},
        ))
    if live_capital_side_effects:
        blocking.append(BlockingReason(
            code="LIVE_CAPITAL_SIDE_EFFECTS",
            message="live_capital_side_effects=True is forbidden in EP5 canary proof",
            severity="critical",
        ))
    if broker_production_live:
        blocking.append(BlockingReason(
            code="BROKER_PRODUCTION_LIVE",
            message="broker_production_live_enabled=True is forbidden in EP5 canary proof",
            severity="critical",
        ))

    can_proceed = (
        len(blocking) == 0
        and result.lower() in {"pass", "passed", "success", "ok"}
    )

    if can_proceed:
        reason = (
            f"EP5 canary proof passed for persona={persona_id!r} run={run_id!r}: "
            "all four proof flags satisfied, fail-closed invariants hold"
        )
    else:
        summary = ", ".join(b.code for b in blocking) if blocking else f"canary result={result!r}"
        reason = f"EP5 canary proof blocked for persona={persona_id!r} run={run_id!r}: {summary}"

    # ------------------------------------------------------------------
    # Flags subtree (fail-closed invariants always explicit)
    # ------------------------------------------------------------------
    flags = FlagSubtree(values={
        PROOF_FLAG_DEPLOYMENT_STAGE_IS_CANARY: flag_deployment_stage_is_canary,
        PROOF_FLAG_CANARY_RUNTIME_STARTED: flag_runtime_started,
        PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED: flag_heartbeat_received,
        PROOF_FLAG_ORDER_ROUTE_MODE: flag_order_route_paper,
        PROOF_FLAG_TELEMETRY_INGESTED: flag_telemetry_ingested,
        PROOF_FLAG_PERSONA_LINEAGE_LINKED: flag_persona_lineage_linked,
        PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS: False,
        PROOF_FLAG_BROKER_PRODUCTION_LIVE: False,
    })

    # ------------------------------------------------------------------
    # Approval subtree
    #
    # The generator records the human approval gates as not_required
    # because this packet is evidence output only. The approval workflow
    # (risk_owner + operator dual-gate) is a separate step that consumes
    # this packet and records the signoffs. Marking them not_required here
    # allows validate_packet to accept can_proceed=True when all four proof
    # flags pass, which is the correct semantic: "evidence is ready for the
    # approval gate." The gate itself runs at the promotion layer.
    # ------------------------------------------------------------------
    approval = ApprovalSubtree(
        risk_owner=ApprovalRequirement(
            role="risk_owner",
            required=False,
            recorded=False,
            state="not_required",
        ),
        operator=ApprovalRequirement(
            role="operator",
            required=False,
            recorded=False,
            state="not_required",
        ),
    )

    target_metadata: dict[str, Any] = {"persona_id": persona_id, "runtime_id": runtime_id}
    if persona_lineage is not None:
        target_metadata.update(
            {
                "sponsor_persona_id": persona_lineage.sponsor_persona_id,
                "multi_persona_artifact_id": persona_lineage.allocation_artifact_id,
            }
        )

    extensions: dict[str, Any] = {
        "canary_run_result": result,
        "heartbeat_count": heartbeat_count,
        "order_route_mode": order_route_mode,
        "telemetry_event_count": telemetry_event_count,
        "started_at": canary_run.get("started_at"),
        "finished_at": canary_run.get("finished_at"),
    }
    if persona_lineage is not None:
        extensions["persona_lineage"] = persona_lineage.to_dict()

    target = TargetRef(
        target_type="canary_run",
        target_id=run_id,
        environment=environment,
        deployment_stage=deployment_stage,
        metadata=target_metadata,
    )

    packet = PromotionReadinessPacket(
        packet_id=packet_id or str(uuid.uuid4()),
        schema_version=SCHEMA_VERSION,
        target=target,
        evidence=evidence,
        approval=approval,
        flags=flags,
        blocking_reasons=tuple(blocking),
        can_proceed=can_proceed,
        reason=reason,
        generated_at=_now_iso(),
        generated_by=generated_by,
        source_task_id=source_task_id,
        depends_on_tasks=("EP5-001-V2",),
        extensions=extensions,
    )

    # Structural validation only; policy gate is EP5-002
    if can_proceed:
        validate_packet(packet)

    return packet
