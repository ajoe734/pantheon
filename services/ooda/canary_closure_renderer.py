"""Render closed CanaryOodaPacket proof artifacts."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from services.ooda.canary_packet_model import (
    CANARY_OODA_PACKET_SCHEMA_VERSION,
    CanaryOodaPacket,
    validate_canary_packet,
)


class CanaryClosureRenderError(ValueError):
    """Raised when a canary closure packet is not safe to render."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class CanaryClosureRenderResult:
    packet_id: str
    json_payload: dict[str, Any]
    json_text: str
    markdown_text: str
    validation_errors: tuple[str, ...] = ()


def render_canary_closure(
    packet: CanaryOodaPacket | Mapping[str, Any],
    *,
    indent: int = 2,
) -> CanaryClosureRenderResult:
    """Render a valid closed canary packet as JSON and reviewer Markdown."""

    packet_obj = _normalize_packet(packet)
    _raise_if_not_renderable(packet)

    json_payload = packet_obj.to_dict()
    json_text = json.dumps(json_payload, indent=indent, sort_keys=True)
    markdown_text = _render_markdown(packet_obj)
    return CanaryClosureRenderResult(
        packet_id=packet_obj.packet_id,
        json_payload=json_payload,
        json_text=json_text,
        markdown_text=markdown_text,
    )


def render_canary_closure_json(
    packet: CanaryOodaPacket | Mapping[str, Any],
    *,
    indent: int = 2,
) -> str:
    return render_canary_closure(packet, indent=indent).json_text


def render_canary_closure_markdown(packet: CanaryOodaPacket | Mapping[str, Any]) -> str:
    return render_canary_closure(packet).markdown_text


def write_canary_closure_artifacts(
    packet: CanaryOodaPacket | Mapping[str, Any],
    *,
    json_path: str | Path,
    markdown_path: str | Path,
    indent: int = 2,
) -> CanaryClosureRenderResult:
    result = render_canary_closure(packet, indent=indent)
    _write_text(json_path, result.json_text)
    _write_text(markdown_path, result.markdown_text)
    return result


def validate_closed_canary_packet(packet: CanaryOodaPacket | Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    data = packet.to_dict() if isinstance(packet, CanaryOodaPacket) else packet

    if not isinstance(data, Mapping):
        return ["canary closure renderer requires a CanaryOodaPacket or mapping"]
    if _status_value(data.get("status")) != "closed":
        errors.append("canary closure renderer requires status closed")

    errors.extend(validate_canary_packet(data))
    return errors


def _raise_if_not_renderable(packet: CanaryOodaPacket | Mapping[str, Any]) -> None:
    errors = validate_closed_canary_packet(packet)
    if errors:
        raise CanaryClosureRenderError(errors)


def _normalize_packet(packet: CanaryOodaPacket | Mapping[str, Any]) -> CanaryOodaPacket:
    if isinstance(packet, CanaryOodaPacket):
        return packet
    if isinstance(packet, Mapping):
        return CanaryOodaPacket.from_dict(packet)
    raise CanaryClosureRenderError(
        ["canary closure renderer requires a CanaryOodaPacket or mapping"]
    )


def _render_markdown(packet: CanaryOodaPacket) -> str:
    payload = packet.to_dict()
    return "\n".join(
        [
            "# Canary OODA Closure",
            "",
            "## Packet",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Packet ID | {_code(payload['packet_id'])} |",
            f"| Schema version | {_code(CANARY_OODA_PACKET_SCHEMA_VERSION)} |",
            f"| Loop type | {_code(payload['loop_type'])} |",
            f"| Environment | {_code(payload['environment'])} |",
            f"| Status | {_code(payload['status'])} |",
            "",
            "## Stage Evidence",
            "",
            "| Stage | Field | Evidence |",
            "|---|---|---|",
            *_stage_rows(packet),
            "",
            "## Closure Assertions",
            "",
            "| Assertion | Value |",
            "|---|---|",
            *_assertion_rows(packet),
            "",
            "## Validation",
            "",
            "No validation errors.",
            "",
        ]
    )


def _stage_rows(packet: CanaryOodaPacket) -> list[str]:
    stage_values: list[tuple[str, str, Any]] = [
        ("Observe", "source_refs", packet.observe.source_refs),
        ("Observe", "telemetry_refs", packet.observe.telemetry_refs),
        ("Orient", "strategy_spec_ref", packet.orient.strategy_spec_ref),
        ("Orient", "experiment_run_ref", packet.orient.experiment_run_ref),
        ("Orient", "drift_report_ref", packet.orient.drift_report_ref),
        ("Decide", "approval_decision_ref", packet.decide.approval_decision_ref),
        ("Decide", "deployment_plan_ref", packet.decide.deployment_plan_ref),
        ("Decide", "human_gate_ref", packet.decide.human_gate_ref),
        ("Act", "runtime_binding_ref", packet.act.runtime_binding_ref),
        ("Act", "canary_runtime_ref", packet.act.canary_runtime_ref),
        ("Act", "rollback_drill_ref", packet.act.rollback_drill_ref),
        ("Learn", "incident_ref", packet.learn.incident_ref),
        ("Learn", "postmortem_ref", packet.learn.postmortem_ref),
        ("Learn", "evolution_proposal_ref", packet.learn.evolution_proposal_ref),
    ]
    return [
        f"| {stage} | {_code(field_name)} | {_markdown_value(value)} |"
        for stage, field_name, value in stage_values
    ]


def _assertion_rows(packet: CanaryOodaPacket) -> list[str]:
    assertions = packet.assertions.to_dict()
    return [
        f"| {_code(field_name)} | {_markdown_value(value)} |"
        for field_name, value in assertions.items()
    ]


def _markdown_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return _code("[]")
        return "<br>".join(_code(item) for item in value)
    if value is None:
        return _code("null")
    if isinstance(value, bool):
        return _code(str(value).lower())
    return _code(value)


def _code(value: Any) -> str:
    escaped = html.escape(str(value), quote=False).replace("|", "&#124;")
    return f"<code>{escaped}</code>"


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _write_text(path: str | Path, text: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text.rstrip() + "\n", encoding="utf-8")


__all__ = [
    "CanaryClosureRenderError",
    "CanaryClosureRenderResult",
    "render_canary_closure",
    "render_canary_closure_json",
    "render_canary_closure_markdown",
    "validate_closed_canary_packet",
    "write_canary_closure_artifacts",
]
