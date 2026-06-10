"""Canary OODA proof packet model.

Implements the 2026-05-19 design supplement Part G2 packet shape:
one canary_strategy OODA packet with observe/orient/decide/act/learn
stage refs, closure assertions, and open/closed/failed lifecycle status.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


CANARY_OODA_PACKET_SCHEMA_VERSION = "CanaryOodaPacket.v1"
CANARY_LOOP_TYPE = "canary_strategy"
CANARY_ENVIRONMENT = "canary"


class CanaryPacketStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass
class CanaryObserveStage:
    source_refs: list[str] = field(default_factory=list)
    telemetry_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_refs": list(self.source_refs),
            "telemetry_refs": list(self.telemetry_refs),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryObserveStage":
        data = data or {}
        return cls(
            source_refs=_string_list(data.get("source_refs")),
            telemetry_refs=_string_list(data.get("telemetry_refs")),
        )


@dataclass
class CanaryOrientStage:
    strategy_spec_ref: str = ""
    experiment_run_ref: str = ""
    drift_report_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryOrientStage":
        data = data or {}
        return cls(
            strategy_spec_ref=_string_or_empty(data.get("strategy_spec_ref")),
            experiment_run_ref=_string_or_empty(data.get("experiment_run_ref")),
            drift_report_ref=_optional_string(data.get("drift_report_ref")),
        )


@dataclass
class CanaryDecideStage:
    approval_decision_ref: str = ""
    deployment_plan_ref: str = ""
    human_gate_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryDecideStage":
        data = data or {}
        return cls(
            approval_decision_ref=_string_or_empty(data.get("approval_decision_ref")),
            deployment_plan_ref=_string_or_empty(data.get("deployment_plan_ref")),
            human_gate_ref=_string_or_empty(data.get("human_gate_ref")),
        )


@dataclass
class CanaryActStage:
    runtime_binding_ref: str = ""
    canary_runtime_ref: str = ""
    rollback_drill_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryActStage":
        data = data or {}
        return cls(
            runtime_binding_ref=_string_or_empty(data.get("runtime_binding_ref")),
            canary_runtime_ref=_string_or_empty(data.get("canary_runtime_ref")),
            rollback_drill_ref=_string_or_empty(data.get("rollback_drill_ref")),
        )


@dataclass
class CanaryLearnStage:
    incident_ref: str | None = None
    postmortem_ref: str | None = None
    evolution_proposal_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryLearnStage":
        data = data or {}
        return cls(
            incident_ref=_optional_string(data.get("incident_ref")),
            postmortem_ref=_optional_string(data.get("postmortem_ref")),
            evolution_proposal_ref=_optional_string(data.get("evolution_proposal_ref")),
        )


@dataclass
class CanaryOodaStages:
    observe: CanaryObserveStage = field(default_factory=CanaryObserveStage)
    orient: CanaryOrientStage = field(default_factory=CanaryOrientStage)
    decide: CanaryDecideStage = field(default_factory=CanaryDecideStage)
    act: CanaryActStage = field(default_factory=CanaryActStage)
    learn: CanaryLearnStage = field(default_factory=CanaryLearnStage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observe": self.observe.to_dict(),
            "orient": self.orient.to_dict(),
            "decide": self.decide.to_dict(),
            "act": self.act.to_dict(),
            "learn": self.learn.to_dict(),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryOodaStages":
        data = data or {}
        return cls(
            observe=CanaryObserveStage.from_mapping(_mapping_or_empty(data.get("observe"))),
            orient=CanaryOrientStage.from_mapping(_mapping_or_empty(data.get("orient"))),
            decide=CanaryDecideStage.from_mapping(_mapping_or_empty(data.get("decide"))),
            act=CanaryActStage.from_mapping(_mapping_or_empty(data.get("act"))),
            learn=CanaryLearnStage.from_mapping(_mapping_or_empty(data.get("learn"))),
        )


@dataclass
class CanaryAssertions:
    live_capital_scope_limited: bool = False
    rollback_drill_completed: bool = False
    telemetry_ingested: bool = False
    human_gate_valid: bool = False
    validation_errors_empty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CanaryAssertions":
        data = data or {}
        return cls(
            live_capital_scope_limited=_bool_or_false(data.get("live_capital_scope_limited")),
            rollback_drill_completed=_bool_or_false(data.get("rollback_drill_completed")),
            telemetry_ingested=_bool_or_false(data.get("telemetry_ingested")),
            human_gate_valid=_bool_or_false(data.get("human_gate_valid")),
            validation_errors_empty=_bool_or_false(data.get("validation_errors_empty")),
        )


@dataclass
class CanaryOodaPacket:
    packet_id: str
    loop_type: str = CANARY_LOOP_TYPE
    environment: str = CANARY_ENVIRONMENT
    status: CanaryPacketStatus | str = CanaryPacketStatus.OPEN
    stages: CanaryOodaStages = field(default_factory=CanaryOodaStages)
    assertions: CanaryAssertions = field(default_factory=CanaryAssertions)

    @property
    def observe(self) -> CanaryObserveStage:
        return self.stages.observe

    @property
    def orient(self) -> CanaryOrientStage:
        return self.stages.orient

    @property
    def decide(self) -> CanaryDecideStage:
        return self.stages.decide

    @property
    def act(self) -> CanaryActStage:
        return self.stages.act

    @property
    def learn(self) -> CanaryLearnStage:
        return self.stages.learn

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "loop_type": self.loop_type,
            "environment": self.environment,
            "status": _status_value(self.status),
            "stages": self.stages.to_dict(),
            "assertions": self.assertions.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def validate(self) -> list[str]:
        return validate_canary_packet(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanaryOodaPacket":
        stages_data = _stage_mapping(data)
        return cls(
            packet_id=_string_or_empty(data.get("packet_id")),
            loop_type=_string_or_empty(data.get("loop_type")),
            environment=_string_or_empty(data.get("environment")),
            status=_string_or_empty(data.get("status")),
            stages=CanaryOodaStages.from_mapping(stages_data),
            assertions=CanaryAssertions.from_mapping(_mapping_or_empty(data.get("assertions"))),
        )

    @classmethod
    def from_json(cls, raw: str) -> "CanaryOodaPacket":
        return cls.from_dict(json.loads(raw))


CANARY_OODA_PACKET_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CanaryOodaPacket",
    "type": "object",
    "required": ["packet_id", "loop_type", "environment", "status", "stages", "assertions"],
    "additionalProperties": False,
    "properties": {
        "packet_id": {"type": "string", "minLength": 1},
        "loop_type": {"const": CANARY_LOOP_TYPE},
        "environment": {"const": CANARY_ENVIRONMENT},
        "status": {"enum": [status.value for status in CanaryPacketStatus]},
        "stages": {
            "type": "object",
            "required": ["observe", "orient", "decide", "act", "learn"],
            "additionalProperties": False,
            "properties": {
                "observe": {
                    "type": "object",
                    "required": ["source_refs", "telemetry_refs"],
                    "additionalProperties": False,
                    "properties": {
                        "source_refs": {"type": "array", "items": {"type": "string"}},
                        "telemetry_refs": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "orient": {
                    "type": "object",
                    "required": ["strategy_spec_ref", "experiment_run_ref", "drift_report_ref"],
                    "additionalProperties": False,
                    "properties": {
                        "strategy_spec_ref": {"type": "string"},
                        "experiment_run_ref": {"type": "string"},
                        "drift_report_ref": {"type": ["string", "null"]},
                    },
                },
                "decide": {
                    "type": "object",
                    "required": ["approval_decision_ref", "deployment_plan_ref", "human_gate_ref"],
                    "additionalProperties": False,
                    "properties": {
                        "approval_decision_ref": {"type": "string"},
                        "deployment_plan_ref": {"type": "string"},
                        "human_gate_ref": {"type": "string"},
                    },
                },
                "act": {
                    "type": "object",
                    "required": ["runtime_binding_ref", "canary_runtime_ref", "rollback_drill_ref"],
                    "additionalProperties": False,
                    "properties": {
                        "runtime_binding_ref": {"type": "string"},
                        "canary_runtime_ref": {"type": "string"},
                        "rollback_drill_ref": {"type": "string"},
                    },
                },
                "learn": {
                    "type": "object",
                    "required": ["incident_ref", "postmortem_ref", "evolution_proposal_ref"],
                    "additionalProperties": False,
                    "properties": {
                        "incident_ref": {"type": ["string", "null"]},
                        "postmortem_ref": {"type": ["string", "null"]},
                        "evolution_proposal_ref": {"type": ["string", "null"]},
                    },
                },
            },
        },
        "assertions": {
            "type": "object",
            "required": [
                "live_capital_scope_limited",
                "rollback_drill_completed",
                "telemetry_ingested",
                "human_gate_valid",
                "validation_errors_empty",
            ],
            "additionalProperties": False,
            "properties": {
                "live_capital_scope_limited": {"type": "boolean"},
                "rollback_drill_completed": {"type": "boolean"},
                "telemetry_ingested": {"type": "boolean"},
                "human_gate_valid": {"type": "boolean"},
                "validation_errors_empty": {"type": "boolean"},
            },
        },
    },
}


def canary_ooda_packet_schema() -> dict[str, Any]:
    return copy.deepcopy(CANARY_OODA_PACKET_SCHEMA)


def validate_canary_packet(packet: CanaryOodaPacket | Mapping[str, Any]) -> list[str]:
    data = packet.to_dict() if isinstance(packet, CanaryOodaPacket) else packet
    errors: list[str] = []

    for field_name in ("packet_id", "loop_type", "environment", "status", "stages", "assertions"):
        if field_name not in data:
            errors.append(f"{field_name} is required")

    packet_id = data.get("packet_id")
    if not isinstance(packet_id, str) or not packet_id.strip():
        errors.append("packet_id must be a non-empty string")

    if data.get("loop_type") != CANARY_LOOP_TYPE:
        errors.append("loop_type must be canary_strategy")
    if data.get("environment") != CANARY_ENVIRONMENT:
        errors.append("environment must be canary")

    status = data.get("status")
    valid_statuses = {item.value for item in CanaryPacketStatus}
    if status not in valid_statuses:
        errors.append("status must be one of: closed, failed, open")

    stages = _mapping_or_empty(data.get("stages"))
    assertions = _mapping_or_empty(data.get("assertions"))
    _validate_stage_types(stages, errors)
    _validate_assertion_types(assertions, errors)

    if status == CanaryPacketStatus.CLOSED.value:
        _validate_closed_stage_refs(stages, errors)
        _validate_closed_assertions(assertions, errors)

    return errors


def _validate_stage_types(stages: Mapping[str, Any], errors: list[str]) -> None:
    observe = _mapping_or_empty(stages.get("observe"))
    for field_name in ("source_refs", "telemetry_refs"):
        _validate_string_array(f"stages.observe.{field_name}", observe.get(field_name), errors)

    orient = _mapping_or_empty(stages.get("orient"))
    for field_name in ("strategy_spec_ref", "experiment_run_ref"):
        _validate_string_field(f"stages.orient.{field_name}", orient.get(field_name), errors)
    _validate_optional_string_field("stages.orient.drift_report_ref", orient.get("drift_report_ref"), errors)

    decide = _mapping_or_empty(stages.get("decide"))
    for field_name in ("approval_decision_ref", "deployment_plan_ref", "human_gate_ref"):
        _validate_string_field(f"stages.decide.{field_name}", decide.get(field_name), errors)

    act = _mapping_or_empty(stages.get("act"))
    for field_name in ("runtime_binding_ref", "canary_runtime_ref", "rollback_drill_ref"):
        _validate_string_field(f"stages.act.{field_name}", act.get(field_name), errors)

    learn = _mapping_or_empty(stages.get("learn"))
    for field_name in ("incident_ref", "postmortem_ref", "evolution_proposal_ref"):
        _validate_optional_string_field(f"stages.learn.{field_name}", learn.get(field_name), errors)


def _validate_assertion_types(assertions: Mapping[str, Any], errors: list[str]) -> None:
    for field_name in (
        "live_capital_scope_limited",
        "rollback_drill_completed",
        "telemetry_ingested",
        "human_gate_valid",
        "validation_errors_empty",
    ):
        if not isinstance(assertions.get(field_name), bool):
            errors.append(f"assertions.{field_name} must be a boolean")


def _validate_closed_stage_refs(stages: Mapping[str, Any], errors: list[str]) -> None:
    observe = _mapping_or_empty(stages.get("observe"))
    if not _non_empty_string_list(observe.get("source_refs")):
        errors.append("closed canary packet requires stages.observe.source_refs")
    if not _non_empty_string_list(observe.get("telemetry_refs")):
        errors.append("closed canary packet requires stages.observe.telemetry_refs")

    for path in (
        "orient.strategy_spec_ref",
        "orient.experiment_run_ref",
        "decide.approval_decision_ref",
        "decide.deployment_plan_ref",
        "decide.human_gate_ref",
        "act.runtime_binding_ref",
        "act.canary_runtime_ref",
        "act.rollback_drill_ref",
    ):
        section, field_name = path.split(".", 1)
        section_data = _mapping_or_empty(stages.get(section))
        if not _non_empty_string(section_data.get(field_name)):
            errors.append(f"closed canary packet requires stages.{path}")


def _validate_closed_assertions(assertions: Mapping[str, Any], errors: list[str]) -> None:
    for field_name in (
        "live_capital_scope_limited",
        "rollback_drill_completed",
        "telemetry_ingested",
        "human_gate_valid",
        "validation_errors_empty",
    ):
        if assertions.get(field_name) is not True:
            errors.append(f"assertions.{field_name} must be true to close canary packet")


def _stage_mapping(data: Mapping[str, Any]) -> Mapping[str, Any]:
    stages = data.get("stages")
    if isinstance(stages, Mapping):
        return stages
    return {
        key: data.get(key)
        for key in ("observe", "orient", "decide", "act", "learn")
        if key in data
    }


def _status_value(status: CanaryPacketStatus | str) -> str:
    return status.value if isinstance(status, CanaryPacketStatus) else str(status)


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _bool_or_false(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _validate_string_array(path: str, value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} must be a string array")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{path}[{index}] must be a string")


def _validate_string_field(path: str, value: Any, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{path} must be a string")


def _validate_optional_string_field(path: str, value: Any, errors: list[str]) -> None:
    if value is not None and not isinstance(value, str):
        errors.append(f"{path} must be a string or null")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and any(_non_empty_string(item) for item in value)


__all__ = [
    "CANARY_ENVIRONMENT",
    "CANARY_LOOP_TYPE",
    "CANARY_OODA_PACKET_SCHEMA",
    "CANARY_OODA_PACKET_SCHEMA_VERSION",
    "CanaryActStage",
    "CanaryAssertions",
    "CanaryDecideStage",
    "CanaryLearnStage",
    "CanaryObserveStage",
    "CanaryOodaPacket",
    "CanaryOodaStages",
    "CanaryOrientStage",
    "CanaryPacketStatus",
    "canary_ooda_packet_schema",
    "validate_canary_packet",
]
