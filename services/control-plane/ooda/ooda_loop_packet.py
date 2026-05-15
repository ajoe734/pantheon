"""
OodaLoopPacket — Pantheon Management OODA Loop Contract

Replayable evidence packet recording one complete
Observe -> Orient -> Decide -> Act -> Learn/Evolve governance loop.

Public API
----------
OodaLoopPacket      — dataclass representing the full packet
ObserveBundle       — Observe stage evidence refs
OrientBundle        — Orient stage interpretation refs
DecideBundle        — Decide stage approval/decision refs
ActBundle           — Act stage runtime/command/broker refs
LearnBundle         — Learn/Evolve stage follow-through refs
OodaLoopStore       — in-memory + JSONL persistence store
validate_packet()   — standalone structural validation helper

Schema
------
The canonical JSON schema lives at:
    services/control-plane/ooda/ooda_loop_packet.schema.json

Allowed stage transition order:
    open -> observing -> oriented -> decided -> acted -> evolving -> closed
    Any stage -> failed
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LoopType(str, Enum):
    PAPER_STRATEGY = "paper_strategy"
    REBALANCE = "rebalance"
    EVOLUTION = "evolution"
    INCIDENT_RESPONSE = "incident_response"
    PERSONA_SYNTHESIS = "persona_synthesis"


class LoopStatus(str, Enum):
    OPEN = "open"
    OBSERVING = "observing"
    ORIENTED = "oriented"
    DECIDED = "decided"
    ACTED = "acted"
    EVOLVING = "evolving"
    CLOSED = "closed"
    FAILED = "failed"


class LoopEnvironment(str, Enum):
    DEV = "dev"
    PAPER = "paper"
    SANDBOX = "sandbox"
    CANARY = "canary"
    LIVE = "live"


# Allowed forward transitions (terminal states have no successors)
_ALLOWED_TRANSITIONS: Dict[LoopStatus, List[LoopStatus]] = {
    LoopStatus.OPEN:       [LoopStatus.OBSERVING, LoopStatus.FAILED],
    LoopStatus.OBSERVING:  [LoopStatus.ORIENTED,  LoopStatus.FAILED],
    LoopStatus.ORIENTED:   [LoopStatus.DECIDED,   LoopStatus.FAILED],
    LoopStatus.DECIDED:    [LoopStatus.ACTED,      LoopStatus.FAILED],
    LoopStatus.ACTED:      [LoopStatus.EVOLVING,   LoopStatus.CLOSED, LoopStatus.FAILED],
    LoopStatus.EVOLVING:   [LoopStatus.CLOSED,     LoopStatus.FAILED],
    LoopStatus.CLOSED:     [],
    LoopStatus.FAILED:     [],
}

LIVE_SIDE_EFFECTS_ALLOWED_ENVS = {LoopEnvironment.LIVE}


# ---------------------------------------------------------------------------
# Stage bundles
# ---------------------------------------------------------------------------

@dataclass
class ObserveBundle:
    """Evidence gathered during the Observe stage."""
    source_refs: List[str] = field(default_factory=list)
    telemetry_refs: List[str] = field(default_factory=list)
    signal_refs: List[str] = field(default_factory=list)
    market_refs: List[str] = field(default_factory=list)
    incident_refs: List[str] = field(default_factory=list)
    human_feedback_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObserveBundle":
        return cls(
            source_refs=data.get("source_refs", []),
            telemetry_refs=data.get("telemetry_refs", []),
            signal_refs=data.get("signal_refs", []),
            market_refs=data.get("market_refs", []),
            incident_refs=data.get("incident_refs", []),
            human_feedback_refs=data.get("human_feedback_refs", []),
        )


@dataclass
class OrientBundle:
    """Interpretation artifacts produced during the Orient stage."""
    regime_state_ref: Optional[str] = None
    universe_selection_ref: Optional[str] = None
    signal_inference_refs: List[str] = field(default_factory=list)
    allocation_proposal_refs: List[str] = field(default_factory=list)
    risk_adjudication_ref: Optional[str] = None
    persona_proposal_refs: List[str] = field(default_factory=list)
    evidence_bundle_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrientBundle":
        return cls(
            regime_state_ref=data.get("regime_state_ref"),
            universe_selection_ref=data.get("universe_selection_ref"),
            signal_inference_refs=data.get("signal_inference_refs", []),
            allocation_proposal_refs=data.get("allocation_proposal_refs", []),
            risk_adjudication_ref=data.get("risk_adjudication_ref"),
            persona_proposal_refs=data.get("persona_proposal_refs", []),
            evidence_bundle_refs=data.get("evidence_bundle_refs", []),
        )


@dataclass
class DecideBundle:
    """Decisions and approvals produced during the Decide stage."""
    approval_decision_id: Optional[str] = None
    deployment_plan_id: Optional[str] = None
    evolution_decision_id: Optional[str] = None
    sponsor_persona_id: Optional[str] = None
    decision_rationale_ref: Optional[str] = None
    policy_decision_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecideBundle":
        return cls(
            approval_decision_id=data.get("approval_decision_id"),
            deployment_plan_id=data.get("deployment_plan_id"),
            evolution_decision_id=data.get("evolution_decision_id"),
            sponsor_persona_id=data.get("sponsor_persona_id"),
            decision_rationale_ref=data.get("decision_rationale_ref"),
            policy_decision_refs=data.get("policy_decision_refs", []),
        )


@dataclass
class ObservationWindow:
    """Time window for post-action observation."""
    start_at: str
    end_at: Optional[str] = None
    duration_hours: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObservationWindow":
        return cls(
            start_at=data["start_at"],
            end_at=data.get("end_at"),
            duration_hours=data.get("duration_hours"),
        )


@dataclass
class ActBundle:
    """Actions taken during the Act stage."""
    runtime_binding_id: Optional[str] = None
    command_receipt_refs: List[str] = field(default_factory=list)
    broker_evidence_refs: List[str] = field(default_factory=list)
    rollback_refs: List[str] = field(default_factory=list)
    safe_mode_refs: List[str] = field(default_factory=list)
    live_capital_side_effects: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActBundle":
        return cls(
            runtime_binding_id=data.get("runtime_binding_id"),
            command_receipt_refs=data.get("command_receipt_refs", []),
            broker_evidence_refs=data.get("broker_evidence_refs", []),
            rollback_refs=data.get("rollback_refs", []),
            safe_mode_refs=data.get("safe_mode_refs", []),
            live_capital_side_effects=data.get("live_capital_side_effects", False),
        )


@dataclass
class LearnBundle:
    """Learning and evolution artifacts produced after the Act stage."""
    telemetry_refs: List[str] = field(default_factory=list)
    postmortem_refs: List[str] = field(default_factory=list)
    evolution_followthrough_refs: List[str] = field(default_factory=list)
    trainer_refs: List[str] = field(default_factory=list)
    retrain_refs: List[str] = field(default_factory=list)
    observation_window: Optional[ObservationWindow] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.observation_window is not None:
            d["observation_window"] = self.observation_window.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LearnBundle":
        ow_data = data.get("observation_window")
        return cls(
            telemetry_refs=data.get("telemetry_refs", []),
            postmortem_refs=data.get("postmortem_refs", []),
            evolution_followthrough_refs=data.get("evolution_followthrough_refs", []),
            trainer_refs=data.get("trainer_refs", []),
            retrain_refs=data.get("retrain_refs", []),
            observation_window=(
                ObservationWindow.from_dict(ow_data) if ow_data else None
            ),
        )


# ---------------------------------------------------------------------------
# OodaLoopPacket
# ---------------------------------------------------------------------------

@dataclass
class OodaLoopPacket:
    """Replayable evidence packet for one complete OODA governance loop."""

    packet_id: str
    loop_type: LoopType | str
    status: LoopStatus | str
    environment: LoopEnvironment | str
    created_at: str
    updated_at: str
    capital_pool_id: Optional[str] = None
    strategy_id: Optional[str] = None
    persona_ids: List[str] = field(default_factory=list)
    observe: ObserveBundle = field(default_factory=ObserveBundle)
    orient: OrientBundle = field(default_factory=OrientBundle)
    decide: DecideBundle = field(default_factory=DecideBundle)
    act: ActBundle = field(default_factory=ActBundle)
    learn: LearnBundle = field(default_factory=LearnBundle)
    audit_refs: List[str] = field(default_factory=list)
    closed_at: Optional[str] = None

    # -- factory ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        loop_type: LoopType | str,
        environment: LoopEnvironment | str,
        capital_pool_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        persona_ids: Optional[List[str]] = None,
    ) -> "OodaLoopPacket":
        now = _now_utc()
        return cls(
            packet_id=f"ooda-{uuid.uuid4().hex[:12]}",
            loop_type=loop_type,
            status=LoopStatus.OPEN,
            environment=environment,
            created_at=now,
            updated_at=now,
            capital_pool_id=capital_pool_id,
            strategy_id=strategy_id,
            persona_ids=persona_ids or [],
        )

    # -- stage transitions ------------------------------------------------

    def advance(self, to_status: LoopStatus | str) -> None:
        """Advance the loop to *to_status*, validating the allowed transition."""
        current = LoopStatus(self.status)
        target = LoopStatus(to_status)
        allowed = _ALLOWED_TRANSITIONS.get(current, [])
        if target not in allowed:
            raise ValueError(
                f"Invalid OODA transition: {current} -> {target}. "
                f"Allowed from {current}: {[s.value for s in allowed]}"
            )
        self.status = target
        self.updated_at = _now_utc()
        if target in (LoopStatus.CLOSED, LoopStatus.FAILED):
            self.closed_at = self.updated_at

    # -- bundle helpers ---------------------------------------------------

    def add_observe_ref(self, kind: str, ref: str) -> None:
        """Append *ref* to the named observe bundle list and advance to observing."""
        bundle = self.observe
        _list_append(bundle, kind, ref)
        if LoopStatus(self.status) == LoopStatus.OPEN:
            self.advance(LoopStatus.OBSERVING)
        else:
            self.updated_at = _now_utc()

    def add_orient_ref(self, kind: str, ref: str) -> None:
        """Append *ref* to the named orient bundle field."""
        bundle = self.orient
        _list_append(bundle, kind, ref)
        self.updated_at = _now_utc()

    def add_decide_ref(self, kind: str, ref: str) -> None:
        """Append *ref* to the named decide bundle field."""
        bundle = self.decide
        _list_append(bundle, kind, ref)
        self.updated_at = _now_utc()

    def add_act_ref(self, kind: str, ref: str) -> None:
        """Append *ref* to the named act bundle field."""
        bundle = self.act
        _list_append(bundle, kind, ref)
        self.updated_at = _now_utc()

    def add_learn_ref(self, kind: str, ref: str) -> None:
        """Append *ref* to the named learn bundle field."""
        bundle = self.learn
        _list_append(bundle, kind, ref)
        self.updated_at = _now_utc()

    # -- validation -------------------------------------------------------

    def validate(self) -> List[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: List[str] = []

        if not self.packet_id:
            errors.append("packet_id is required")
        if not str(self.packet_id).startswith("ooda-"):
            errors.append("packet_id must start with 'ooda-'")

        try:
            LoopType(self.loop_type)
        except ValueError:
            errors.append(f"Unknown loop_type: {self.loop_type!r}")

        try:
            LoopStatus(self.status)
        except ValueError:
            errors.append(f"Unknown status: {self.status!r}")

        try:
            env = LoopEnvironment(self.environment)
        except ValueError:
            errors.append(f"Unknown environment: {self.environment!r}")
            env = None

        if env and env not in LIVE_SIDE_EFFECTS_ALLOWED_ENVS:
            if self.act.live_capital_side_effects:
                errors.append(
                    f"live_capital_side_effects must be False in environment '{self.environment}'"
                )

        if not self.created_at:
            errors.append("created_at is required")
        if not self.updated_at:
            errors.append("updated_at is required")

        return errors

    # -- serialization ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "loop_type": LoopType(self.loop_type).value,
            "status": LoopStatus(self.status).value,
            "environment": LoopEnvironment(self.environment).value,
            "capital_pool_id": self.capital_pool_id,
            "strategy_id": self.strategy_id,
            "persona_ids": list(self.persona_ids),
            "observe": self.observe.to_dict(),
            "orient": self.orient.to_dict(),
            "decide": self.decide.to_dict(),
            "act": self.act.to_dict(),
            "learn": self.learn.to_dict(),
            "audit_refs": list(self.audit_refs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OodaLoopPacket":
        return cls(
            packet_id=data["packet_id"],
            loop_type=data["loop_type"],
            status=data["status"],
            environment=data["environment"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            capital_pool_id=data.get("capital_pool_id"),
            strategy_id=data.get("strategy_id"),
            persona_ids=data.get("persona_ids", []),
            observe=ObserveBundle.from_dict(data.get("observe", {})),
            orient=OrientBundle.from_dict(data.get("orient", {})),
            decide=DecideBundle.from_dict(data.get("decide", {})),
            act=ActBundle.from_dict(data.get("act", {})),
            learn=LearnBundle.from_dict(data.get("learn", {})),
            audit_refs=data.get("audit_refs", []),
            closed_at=data.get("closed_at"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "OodaLoopPacket":
        return cls.from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# OodaLoopStore
# ---------------------------------------------------------------------------

class OodaLoopStore:
    """In-memory OODA packet store backed by an append-only JSONL file."""

    def __init__(self, store_path: Optional[str] = None) -> None:
        self._packets: Dict[str, OodaLoopPacket] = {}
        self._path: Optional[Path] = Path(store_path) if store_path else None
        if self._path and self._path.exists():
            self._load()

    # -- mutation ---------------------------------------------------------

    def add(self, packet: OodaLoopPacket) -> None:
        """Add a new packet; raise ValueError if packet_id already exists."""
        if packet.packet_id in self._packets:
            raise ValueError(f"Packet already exists: {packet.packet_id}")
        errors = packet.validate()
        if errors:
            raise ValueError(f"Invalid OodaLoopPacket: {errors}")
        self._packets[packet.packet_id] = packet
        self._append(packet)

    def update(self, packet: OodaLoopPacket) -> None:
        """Update an existing packet and append a snapshot to the JSONL store."""
        if packet.packet_id not in self._packets:
            raise KeyError(f"Packet not found: {packet.packet_id}")
        errors = packet.validate()
        if errors:
            raise ValueError(f"Invalid OodaLoopPacket: {errors}")
        self._packets[packet.packet_id] = packet
        self._append(packet)

    # -- query ------------------------------------------------------------

    def get(self, packet_id: str) -> OodaLoopPacket:
        if packet_id not in self._packets:
            raise KeyError(f"Packet not found: {packet_id}")
        return self._packets[packet_id]

    def list(
        self,
        loop_type: Optional[LoopType | str] = None,
        status: Optional[LoopStatus | str] = None,
        environment: Optional[LoopEnvironment | str] = None,
    ) -> List[OodaLoopPacket]:
        results = list(self._packets.values())
        if loop_type is not None:
            results = [p for p in results if str(p.loop_type) == str(loop_type)]
        if status is not None:
            results = [p for p in results if str(p.status) == str(status)]
        if environment is not None:
            results = [p for p in results if str(p.environment) == str(environment)]
        return sorted(results, key=lambda p: p.created_at)

    # -- persistence ------------------------------------------------------

    def _append(self, packet: OodaLoopPacket) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(packet.to_dict()) + "\n")

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                packet = OodaLoopPacket.from_dict(data)
                self._packets[packet.packet_id] = packet


# ---------------------------------------------------------------------------
# Standalone validator
# ---------------------------------------------------------------------------

def validate_packet(data: Dict[str, Any]) -> List[str]:
    """Validate a raw dict against the OodaLoopPacket contract."""
    try:
        packet = OodaLoopPacket.from_dict(data)
        return packet.validate()
    except (KeyError, TypeError) as exc:
        return [f"Schema error: {exc}"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _list_append(bundle: Any, field_name: str, value: str) -> None:
    """Append *value* to *bundle.<field_name>*, or set the field if it is None."""
    attr = getattr(bundle, field_name, None)
    if isinstance(attr, list):
        attr.append(value)
    elif attr is None:
        setattr(bundle, field_name, value)
    else:
        raise AttributeError(
            f"Field '{field_name}' on {type(bundle).__name__} is not a list"
        )
