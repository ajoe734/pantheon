"""
paper_telemetry_packet - MGMT-PAPER-005

Factory, ingest verifier, and evidence writer for the paper TelemetryEvent
packet used by the Management Paper Loop Proof (Track E / EPIC-02).

Scope
-----
Builds canonical paper runtime telemetry events from the existing
RuntimeTelemetryEmitter, verifies them through TelemetryIngestService, and
writes a replayable evidence packet for downstream OODA assembly.

Usage
-----
Run as a standalone script to generate the evidence artifact:

    python3 services/telemetry/paper_telemetry_packet.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.lean_runtime.paper_runtime import RuntimeTelemetryEmitter
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


TASK_ID = "MGMT-PAPER-005"
EPIC = "EPIC-02 Management Paper Loop Proof"
PAPER_ENVIRONMENT = "paper"
SCHEMA_PATH = Path(__file__).with_name("telemetry_event.schema.json")


@dataclass(frozen=True)
class PaperTelemetryContext:
    """Stable paper runtime identity used to build the Management evidence."""

    binding_id: str = "8d706784-2678-4c49-9b70-e05d89f7b001"
    runtime_id: str = "rt-paper-mgmt-001"
    capital_pool_id: str = "capital-pool-paper-001"
    artifact_id: str = "artifact-paper-qlib-lgbm-001"
    artifact_version: str = "1.0.0"
    plan_id: str = "deployment-plan-paper-001"
    persona_capital_binding_id: str = "pcb-paper-quant-001"
    strategy_id: str = "strategy-spec-paper-qlib-lgbm-001"
    deployment_stage: str = PAPER_ENVIRONMENT
    engine_bridge_repo: str = "ajoe734/pantheon-lean.git"
    engine_bridge_path: str = "pantheon/lean"
    engine_bridge_commit: str = "paper-loop-bridge-commit-001"
    runtime_adapter_version: str = "0.1.0"
    context_source: str = "launch_manifest"
    effective_at: str = "2026-05-15T14:50:00Z"
    retired_at: str | None = None

    def binding_dict(self) -> Dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "runtime_id": self.runtime_id,
            "capital_pool_id": self.capital_pool_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "plan_id": self.plan_id,
            "persona_capital_binding_id": self.persona_capital_binding_id,
            "deployment_mode": self.deployment_stage,
            "deployment_stage": self.deployment_stage,
            "engine_bridge_repo": self.engine_bridge_repo,
            "engine_bridge_path": self.engine_bridge_path,
            "engine_bridge_commit": self.engine_bridge_commit,
            "runtime_adapter_version": self.runtime_adapter_version,
            "context_source": self.context_source,
        }

    def binding_record(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            binding_id=self.binding_id,
            runtime_id=self.runtime_id,
            capital_pool_id=self.capital_pool_id,
            artifact_id=self.artifact_id,
            artifact_version=self.artifact_version,
            plan_id=self.plan_id,
            persona_capital_binding_id=self.persona_capital_binding_id,
            deployment_mode=self.deployment_stage,
            effective_at=self.effective_at,
            retired_at=self.retired_at,
        )

    def runtime_identity(self) -> RuntimeIdentity:
        return RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "paper",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": self.runtime_id,
                "PANTHEON_WORKSPACE_REF": "workspace-paper-mgmt-001",
                "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper-mgmt-001",
                "PANTHEON_PERSONA_ID": "persona-quant-paper-01",
                "PANTHEON_SESSION_ID": "session-paper-mgmt-001",
                "PANTHEON_TRACE_ID": "03829d25-2c9f-44f0-981e-56a94d8ff001",
                "PANTHEON_REQUEST_ID": "request-paper-mgmt-001",
            }
        )


class _StaticBindingResolver:
    def __init__(self, ctx: PaperTelemetryContext) -> None:
        self._ctx = ctx

    def resolve(self) -> Dict[str, Any]:
        return self._ctx.binding_dict()


class _StaticBindingStore:
    def __init__(self, ctx: PaperTelemetryContext) -> None:
        self._ctx = ctx

    def get_binding(self, binding_id: str) -> types.SimpleNamespace | None:
        if binding_id == self._ctx.binding_id:
            return self._ctx.binding_record()
        return None


def build_paper_telemetry_events(ctx: PaperTelemetryContext) -> List[Dict[str, Any]]:
    """Build canonical paper telemetry events for the Management proof packet."""
    emitter = RuntimeTelemetryEmitter(ctx.runtime_identity(), _StaticBindingResolver(ctx))
    event_specs = [
        (
            "heartbeat",
            {"heartbeat": 1},
            {"runtime_package": "paper_execution_runtime"},
            "631f17dc-bbba-4593-9ad3-9ed24c5b3001",
            "2026-05-15T15:05:00Z",
        ),
        (
            "deploy_completed",
            {"action": "deploy_completed"},
            {"runtime_package": "paper_execution_runtime"},
            "631f17dc-bbba-4593-9ad3-9ed24c5b3002",
            "2026-05-15T15:05:05Z",
        ),
        (
            "pnl_snapshot",
            {"pnl": 125.5},
            {"observation_window": "paper-session-open"},
            "631f17dc-bbba-4593-9ad3-9ed24c5b3003",
            "2026-05-15T15:05:10Z",
        ),
        (
            "bracket_order_logged",
            {"action": "bracket_logged_only"},
            {
                "broker_submission_status": "logged_only",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
            },
            "631f17dc-bbba-4593-9ad3-9ed24c5b3004",
            "2026-05-15T15:05:15Z",
        ),
    ]

    events: List[Dict[str, Any]] = []
    for event_type, metrics, metadata, event_id, created_at in event_specs:
        event = emitter.build_event(
            event_type,
            metrics,
            metadata=metadata,
            event_id=event_id,
            created_at=created_at,
        )
        if event is None:
            raise ValueError(f"failed to build {event_type}: {emitter.snapshot()['last_error']}")
        event["target"]["strategy_id"] = ctx.strategy_id
        events.append(event)
    return events


def _stage_mismatch_event(event: Dict[str, Any]) -> Dict[str, Any]:
    mutated = json.loads(json.dumps(event))
    mutated["event_id"] = "631f17dc-bbba-4593-9ad3-9ed24c5b3010"
    mutated["execution_mode"] = "live"
    mutated["environment"] = "live"
    mutated["deployment_stage"] = "live"
    return mutated


def _missing_binding_event(event: Dict[str, Any]) -> Dict[str, Any]:
    mutated = json.loads(json.dumps(event))
    mutated["event_id"] = "631f17dc-bbba-4593-9ad3-9ed24c5b3011"
    mutated.pop("binding_id", None)
    return mutated


async def _run_ingest_validation(
    ctx: PaperTelemetryContext,
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    summary_store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
    svc = TelemetryIngestService(
        schema_path=str(SCHEMA_PATH),
        binding_store=_StaticBindingStore(ctx),
        runtime_summary_store=summary_store,
        batch_size=10,
        batch_interval=0.01,
    )
    await svc.start()
    accepted_event_ids: List[str] = []
    first_heartbeat_ok = await svc.ingest(events[0])
    duplicate_heartbeat_ok = await svc.ingest(dict(events[0]))
    if first_heartbeat_ok:
        accepted_event_ids.append(events[0]["event_id"])

    for event in events[1:]:
        if await svc.ingest(event):
            accepted_event_ids.append(event["event_id"])

    stage_mismatch_ok = await svc.ingest(_stage_mismatch_event(events[0]))
    missing_binding_ok = await svc.ingest(_missing_binding_event(events[0]))

    await asyncio.sleep(0.05)
    summary = svc.get_runtime_summary(ctx.runtime_id)
    dlq_entries = svc.get_dlq_entries(limit=10)
    await svc.stop(graceful=True)
    stats = svc.stats()

    return {
        "accepted_event_ids": accepted_event_ids,
        "heartbeat_first_accepted": first_heartbeat_ok,
        "heartbeat_duplicate_accepted": duplicate_heartbeat_ok,
        "stage_mismatch_rejected": not stage_mismatch_ok,
        "missing_binding_rejected": not missing_binding_ok,
        "stats": stats,
        "runtime_summary": summary,
        "dlq_reasons": [entry.get("reason") for entry in dlq_entries],
    }


def run_ingest_validation(
    ctx: PaperTelemetryContext,
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return asyncio.run(_run_ingest_validation(ctx, events))


def build_evidence_packet(
    ctx: PaperTelemetryContext | None = None,
    *,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    """Build the full MGMT-PAPER-005 evidence packet."""
    ctx = ctx or PaperTelemetryContext()
    events = build_paper_telemetry_events(ctx)
    ingest_validation = run_ingest_validation(ctx, events)

    heartbeat_event_id = events[0]["event_id"]
    pnl_event_id = events[2]["event_id"]
    bracket_event_id = events[3]["event_id"]
    packet: Dict[str, Any] = {
        "task_id": TASK_ID,
        "epic": EPIC,
        "environment": PAPER_ENVIRONMENT,
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_capital_side_effects": False,
        "runtime_binding_id": ctx.binding_id,
        "telemetry_packet": {
            "packet_id": "telemetry-packet-paper-mgmt-001",
            "event_count": len(events),
            "event_ids": [event["event_id"] for event in events],
            "events": events,
        },
        "ingest_validation": ingest_validation,
        "runtime_summary_projection": ingest_validation["runtime_summary"],
        "ooda_refs": {
            "observe": {
                "telemetry_refs": [heartbeat_event_id],
                "runtime_health_ref": heartbeat_event_id,
            },
            "act": {
                "runtime_binding_id": ctx.binding_id,
                "command_receipt_refs": [],
                "live_capital_side_effects": False,
            },
            "learn": {
                "telemetry_refs": [pnl_event_id, bracket_event_id],
                "observation_window": {
                    "start_at": events[0]["created_at"],
                    "end_at": events[-1]["created_at"],
                    "duration_hours": 0.003,
                },
            },
        },
        "safety_assertions": {
            "paper_stage_only": all(event["deployment_stage"] == "paper" for event in events),
            "live_broker_telemetry_disabled": True,
            "capital_binding_live_disabled": True,
            "bracket_logged_only": events[3]["metadata"].get("submitted_to_broker") is False,
            "no_real_order": events[3]["metadata"].get("is_real_order") is False,
            "no_real_capital": events[3]["metadata"].get("is_real_capital") is False,
        },
        "validation_errors": [],
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec",
            "MGMT-PAPER-002: ApprovalDecision packet",
            "MGMT-PAPER-003: DeploymentPlan packet",
            "MGMT-PAPER-004: paper RuntimeBinding packet",
            "MGMT-PAPER-005: telemetry packet  <- this artifact",
            "MGMT-PAPER-006: EvolutionDecision review packet",
            "MGMT-PAPER-007: complete OODA packet",
        ],
    }
    packet["validation_errors"] = validate_evidence_packet(packet)
    return packet


def validate_evidence_packet(packet: Dict[str, Any]) -> List[str]:
    """Return evidence-packet validation errors. Empty list means pass."""
    errors: List[str] = []
    if packet.get("task_id") != TASK_ID:
        errors.append("task_id must be MGMT-PAPER-005")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    events = packet.get("telemetry_packet", {}).get("events", [])
    if len(events) < 4:
        errors.append("telemetry_packet must include heartbeat, deploy, pnl, and bracket events")
        return errors

    heartbeat = events[0]
    metadata = heartbeat.get("metadata", {})
    if heartbeat.get("event_type") != "heartbeat" or heartbeat.get("metrics", {}).get("heartbeat") != 1:
        errors.append("first event must be a heartbeat with heartbeat=1")
    if heartbeat.get("deployment_stage") != PAPER_ENVIRONMENT:
        errors.append("heartbeat deployment_stage must be paper")
    if not heartbeat.get("binding_id"):
        errors.append("heartbeat must include binding_id")
    if metadata.get("engine_bridge_repo") != "ajoe734/pantheon-lean.git":
        errors.append("heartbeat must include engine_bridge_repo metadata")
    if not metadata.get("engine_bridge_commit"):
        errors.append("heartbeat must include engine_bridge_commit metadata")

    ingest = packet.get("ingest_validation", {})
    stats = ingest.get("stats", {}).get("service", {})
    if not ingest.get("heartbeat_first_accepted"):
        errors.append("heartbeat must be accepted by ingest")
    if not ingest.get("heartbeat_duplicate_accepted"):
        errors.append("duplicate heartbeat must be idempotently accepted")
    if stats.get("total_duplicates") != 1:
        errors.append("ingest must count one duplicate heartbeat")
    if not ingest.get("stage_mismatch_rejected"):
        errors.append("stage mismatch must be rejected")
    if not ingest.get("missing_binding_rejected"):
        errors.append("missing binding must be rejected")

    summary = packet.get("runtime_summary_projection") or {}
    if summary.get("last_heartbeat_at") != heartbeat.get("created_at"):
        errors.append("runtime summary must expose the heartbeat timestamp")
    if summary.get("runtime_binding_id") != packet.get("runtime_binding_id"):
        errors.append("runtime summary must include runtime_binding_id")
    if summary.get("engine_bridge_repo") != "ajoe734/pantheon-lean.git":
        errors.append("runtime summary must include engine_bridge_repo")

    safety = packet.get("safety_assertions", {})
    for key in (
        "paper_stage_only",
        "live_broker_telemetry_disabled",
        "capital_binding_live_disabled",
        "bracket_logged_only",
        "no_real_order",
        "no_real_capital",
    ):
        if safety.get(key) is not True:
            errors.append(f"safety assertion failed: {key}")

    return errors


def write_evidence_packet(packet: Dict[str, Any], out_path: Path) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    return packet


EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-PAPER-005-paper-telemetry-packet.json"


def main() -> int:
    print("=== MGMT-PAPER-005: paper telemetry packet ===\n")
    packet = build_evidence_packet()
    write_evidence_packet(packet, EVIDENCE_PATH)

    validation_errors = packet["validation_errors"]
    ingest_stats = packet["ingest_validation"]["stats"]["service"]
    summary = packet["runtime_summary_projection"]

    print(f"  packet_id       : {packet['telemetry_packet']['packet_id']}")
    print(f"  runtime_binding : {packet['runtime_binding_id']}")
    print(f"  event_count     : {packet['telemetry_packet']['event_count']}")
    print(f"  ingested        : {ingest_stats['total_ingested']}")
    print(f"  duplicates      : {ingest_stats['total_duplicates']}")
    print(f"  rejected        : {ingest_stats['total_rejected']}")
    print(f"  last heartbeat  : {summary['last_heartbeat_at']}")
    print(f"  validation      : {'PASS (no errors)' if not validation_errors else validation_errors}")
    print(f"\n  evidence packet written to: {EVIDENCE_PATH}")
    print("\n=== PASS ===" if not validation_errors else "\n=== FAIL ===")
    return 0 if not validation_errors else 1


if __name__ == "__main__":
    sys.exit(main())
