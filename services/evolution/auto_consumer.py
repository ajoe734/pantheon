"""Automatic evolution consumer for discovery persona artifacts (design §12.2).

The consumer moves a ``proposed`` EvolutionDecision to ``reviewed`` and
``approved`` as the ``automated_gate`` actor **only** when every check below
holds, and records the evidence of each check. Everything else is left
untouched for the operator:

* the action is one of ``retrain`` / ``mutate`` / ``revalidate`` / ``retire``;
* the decision's risk level is ``low`` (the decision model grants that to a
  paper-stage discovery retire only when it carries the discovery marker; every
  other retire stays ``high`` and needs the committee);
* the target resolves, through the persona/registry owners (never through the
  decision's own say-so), to a discovery persona's artifact that is paper-only
  and tenant-owned; ``mutate`` additionally needs a ``factor_rule_v1`` artifact;
* the decision was created by the evolution controller, in the same tenant.

Approved ``retrain`` / ``mutate`` / ``revalidate`` decisions reach research
through the existing dispatch worker. An approved ``retire`` is executed here
through the governed persona lifecycle (``paper_owner -> frozen -> retired``)
and its receipt is recorded as evidence; nothing in this module grants
capital, touches a live runtime, or changes any other action's owner.

All I/O goes through small ports so the module is deterministic under test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

AUTOMATED_GATE_ROLE = "automated_gate"
AUTOMATED_GATE_ACTOR_ID = "evolution-auto-consumer"
GATE_VERSION = "discovery-auto-gate/1"
AUTO_ACTIONS = frozenset({"retrain", "mutate", "revalidate", "retire"})
DISCOVERY_AUTO_SCOPE_KEY = "discovery_auto_scope"
PAPER_STAGE = "paper"
RETIRE_PATH = ("frozen", "retired")  # from paper_owner; frozen -> retired is governance-owned
FACTOR_RULE_KIND = "factor_rule_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


# --------------------------------------------------------------------------- ports


class DiscoveryScopePort(Protocol):
    def resolve(self, decision: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Owner-verified facts about the decision's target, or None.

        Expected keys when the target IS a discovery persona artifact:
        ``tenant_id``, ``persona_id``, ``artifact_id``, ``stage`` (``paper``),
        ``strategy_kind`` and ``persona_lifecycle_state``.
        """


class DecisionPort(Protocol):
    def list_proposed(self) -> Sequence[Mapping[str, Any]]: ...

    def review(self, decision_id: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def approve(self, decision_id: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


class PersonaLifecyclePort(Protocol):
    def get_state(self, persona_id: str, tenant_id: str) -> str: ...

    def advance(self, persona_id: str, tenant_id: str, target_state: str, governance_decision_id: str) -> str: ...


class EvidenceSink(Protocol):
    def record(self, entry: Mapping[str, Any]) -> None: ...


class JsonlEvidenceSink:
    """Append-only evidence ledger (one JSON object per line)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, entry: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(entry), sort_keys=True) + "\n")


class ListEvidenceSink:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: Mapping[str, Any]) -> None:
        self.entries.append(dict(entry))


# --------------------------------------------------------------------------- gate


@dataclass
class GateVerdict:
    decision_id: str
    eligible: bool
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    scope: dict[str, Any] | None = None

    def evidence(self) -> dict[str, Any]:
        return {
            "gate": GATE_VERSION,
            "decision_id": self.decision_id,
            "eligible": self.eligible,
            "reason": self.reason,
            "checks": self.checks,
            "scope": self.scope,
        }


def decision_digest(decision: Mapping[str, Any]) -> str:
    keys = ("decision_id", "tenant_id", "target_type", "target_id", "target_version", "action_type", "risk_level")
    payload = {k: decision.get(k) for k in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def evaluate_gate(decision: Mapping[str, Any], scope_port: DiscoveryScopePort) -> GateVerdict:
    """Decide whether the automated gate may review+approve ``decision``."""
    decision_id = _text(decision.get("decision_id"))
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> bool:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})
        return bool(ok)

    def refuse(reason: str, scope: dict[str, Any] | None = None) -> GateVerdict:
        return GateVerdict(decision_id, False, reason, checks, scope)

    action = _text(decision.get("action_type")).lower()
    if not check("state_is_proposed", _text(decision.get("decision_state")) == "proposed", decision.get("decision_state")):
        return refuse("not_proposed")
    if not check("action_in_auto_set", action in AUTO_ACTIONS, action):
        return refuse("action_requires_operator")
    if not check("risk_is_low", _text(decision.get("risk_level")).lower() == "low", decision.get("risk_level")):
        return refuse("risk_requires_operator")
    if not check(
        "created_by_evolution_controller",
        _text(decision.get("created_by_role")) == "evolution_controller",
        decision.get("created_by_role"),
    ):
        return refuse("proposer_not_controller")
    stage = _text(decision.get("target_stage")).lower() or PAPER_STAGE
    if not check("target_stage_is_paper", stage == PAPER_STAGE, decision.get("target_stage")):
        return refuse("stage_not_paper")
    if action == "retire":
        marker = (decision.get("metadata") or {}).get(DISCOVERY_AUTO_SCOPE_KEY) is True
        if not check("retire_carries_discovery_scope_marker", marker):
            return refuse("retire_outside_discovery_scope")

    try:
        scope = scope_port.resolve(decision)
    except Exception as exc:  # owner unreachable: fail closed, leave for operator
        check("scope_resolved_from_owners", False, f"{type(exc).__name__}: {exc}")
        return refuse("scope_owner_unavailable")
    if not check("scope_resolved_from_owners", scope is not None):
        return refuse("not_a_discovery_persona_artifact")
    scope = dict(scope or {})
    tenant = _text(decision.get("tenant_id"))
    if not check("scope_tenant_matches_decision", _text(scope.get("tenant_id")) == tenant and bool(tenant), scope.get("tenant_id")):
        return refuse("tenant_mismatch", scope)
    if not check("scope_stage_is_paper", _text(scope.get("stage")) == PAPER_STAGE, scope.get("stage")):
        return refuse("artifact_not_paper_only", scope)
    target_id = _text(decision.get("target_id"))
    if not check(
        "target_is_the_resolved_artifact_or_persona",
        target_id in {_text(scope.get("artifact_id")), _text(scope.get("persona_id"))},
        target_id,
    ):
        return refuse("target_not_the_resolved_discovery_artifact", scope)
    if action == "mutate" and not check(
        "mutate_needs_factor_rule_v1", _text(scope.get("strategy_kind")) == FACTOR_RULE_KIND, scope.get("strategy_kind")
    ):
        return refuse("mutate_requires_factor_rule_v1", scope)
    return GateVerdict(decision_id, True, "discovery_persona_artifact_auto_action", checks, scope)


# ------------------------------------------------------------------------ consumer


@dataclass
class ConsumerReport:
    approved: list[str] = field(default_factory=list)
    left_for_operator: dict[str, str] = field(default_factory=dict)
    retired: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


class AutoConsumer:
    """One tick: gate every proposed decision, approve the eligible ones."""

    def __init__(
        self,
        *,
        decisions: DecisionPort,
        scope: DiscoveryScopePort,
        evidence: EvidenceSink,
        personas: PersonaLifecyclePort | None = None,
        clock: Callable[[], str] = _now,
    ) -> None:
        self.decisions = decisions
        self.scope = scope
        self.evidence = evidence
        self.personas = personas
        self.clock = clock

    def tick(self) -> ConsumerReport:
        report = ConsumerReport()
        for decision in self.decisions.list_proposed():
            decision_id = _text(decision.get("decision_id"))
            verdict = evaluate_gate(decision, self.scope)
            if not verdict.eligible:
                report.left_for_operator[decision_id] = verdict.reason
                continue
            try:
                self._approve(decision, verdict, report)
            except Exception as exc:  # one bad decision never stops the tick
                report.errors[decision_id] = f"{type(exc).__name__}: {exc}"
        return report

    def _approve(self, decision: Mapping[str, Any], verdict: GateVerdict, report: ConsumerReport) -> None:
        decision_id = _text(decision.get("decision_id"))
        tenant = _text(decision.get("tenant_id"))
        approval_id = f"auto-gate-{decision_id}"
        evidence = {**verdict.evidence(), "decision_digest": decision_digest(decision), "at": self.clock()}
        note = json.dumps(evidence, sort_keys=True, default=str)
        common = {"actor_role": AUTOMATED_GATE_ROLE, "actor_id": AUTOMATED_GATE_ACTOR_ID, "tenant_id": tenant, "note": note}
        # Record the evidence before the state moves: an approved decision always has its proof.
        self.evidence.record({"event": "auto_gate_approved", **evidence})
        self.decisions.review(decision_id, {**common, "approval_decision_id": approval_id})
        self.decisions.approve(decision_id, {**common, "approval_decision_id": approval_id})
        report.approved.append(decision_id)
        if _text(decision.get("action_type")).lower() == "retire":
            self.retire_persona(decision, verdict, approval_id)
            report.retired.append(decision_id)

    def retire_persona(self, decision: Mapping[str, Any], verdict: GateVerdict, approval_id: str) -> list[str]:
        """Governed persona retire: ``paper_owner -> frozen -> retired`` (idempotent)."""
        if self.personas is None:
            raise RuntimeError("no persona lifecycle port configured for retire")
        scope = verdict.scope or {}
        persona_id = _text(scope.get("persona_id"))
        tenant = _text(decision.get("tenant_id"))
        state = self.personas.get_state(persona_id, tenant)
        steps: list[str] = []
        if state not in ("paper_owner", "frozen", "retired"):
            raise RuntimeError(f"persona {persona_id} is {state!r}; only a paper-stage persona is retired automatically")
        if state == "paper_owner":
            state = self.personas.advance(persona_id, tenant, "frozen", approval_id)
            steps.append("frozen")
        if state == "frozen":
            state = self.personas.advance(persona_id, tenant, "retired", approval_id)
            steps.append("retired")
        if state != "retired":
            raise RuntimeError(f"persona {persona_id} ended {state!r}, expected 'retired'")
        self.evidence.record(
            {
                "event": "persona_retired",
                "decision_id": _text(decision.get("decision_id")),
                "persona_id": persona_id,
                "artifact_id": scope.get("artifact_id"),
                "tenant_id": tenant,
                "governance_decision_id": approval_id,
                "transitions": steps,
                "final_state": state,
                "at": self.clock(),
            }
        )
        return steps


# --------------------------------------------------------------- paper decay -> retire


@dataclass(frozen=True)
class DecayPolicy:
    """EVOLUTION_REVIEW_AND_THRESHOLDS §7.1–§7.3 (same constants the retrain path cites)."""

    min_sharpe_ratio_of_baseline: float = 0.5
    max_drawdown_multiple: float = 1.25
    underperforming_windows: int = 3
    min_paper_windows: int = 3


@dataclass
class DecayVerdict:
    decayed: bool
    reasons: list[str]
    observed: dict[str, Any]


def evaluate_paper_decay(
    *,
    baseline_sharpe: float,
    baseline_max_drawdown: float,
    paper_windows: Sequence[Mapping[str, float]],
    policy: DecayPolicy = DecayPolicy(),
) -> DecayVerdict:
    """Has out-of-sample paper performance decayed against the admitted baseline?

    ``paper_windows`` are chronological ``{"sharpe_ratio", "max_drawdown"}``
    windows of paper results. Too little history is *not* decay (never retire on
    absence of evidence).
    """
    windows = list(paper_windows)
    observed: dict[str, Any] = {"windows": len(windows), "baseline_sharpe": baseline_sharpe,
                                "baseline_max_drawdown": baseline_max_drawdown}
    if len(windows) < policy.min_paper_windows or baseline_sharpe <= 0:
        return DecayVerdict(False, ["insufficient_paper_history"], observed)
    floor = policy.min_sharpe_ratio_of_baseline * baseline_sharpe
    ceiling = policy.max_drawdown_multiple * abs(baseline_max_drawdown)
    latest = windows[-1]
    reasons: list[str] = []
    if float(latest["sharpe_ratio"]) < floor:
        reasons.append("sharpe_below_half_of_baseline")
    if abs(float(latest["max_drawdown"])) > ceiling:
        reasons.append("drawdown_above_1_25x_baseline")
    tail = windows[-policy.underperforming_windows:]
    if len(tail) == policy.underperforming_windows and all(float(w["sharpe_ratio"]) < baseline_sharpe for w in tail):
        reasons.append("three_underperforming_windows")
    # A single bad window is noise; decay needs the persistent signal plus a hard breach.
    persistent = "three_underperforming_windows" in reasons
    hard = any(r in reasons for r in ("sharpe_below_half_of_baseline", "drawdown_above_1_25x_baseline"))
    observed.update(latest_sharpe=latest["sharpe_ratio"], latest_max_drawdown=latest["max_drawdown"],
                    sharpe_floor=floor, drawdown_ceiling=ceiling)
    return DecayVerdict(persistent and hard, reasons, observed)


def build_retire_proposal(
    *,
    decision_id: str,
    tenant_id: str,
    persona_id: str,
    artifact_id: str,
    artifact_version: str,
    verdict: DecayVerdict,
) -> dict[str, Any]:
    """Body for POST /api/evolution/proposals: a discovery paper retire."""
    if not verdict.decayed:
        raise ValueError("a retire proposal requires a decayed verdict")
    return {
        "decision_id": decision_id,
        "tenant_id": tenant_id,
        "target_type": "candidate_artifact",
        "target_id": artifact_id,
        "target_version": artifact_version,
        "action_type": "retire",
        "target_stage": PAPER_STAGE,
        "persona_id": persona_id,
        "created_by_role": "evolution_controller",
        "created_by_id": AUTOMATED_GATE_ACTOR_ID,
        "rationale": "Paper performance decayed: " + ", ".join(verdict.reasons),
        "evidence_refs": [
            {"ref_type": "telemetry_summary", "ref_id": f"paper-decay:{persona_id}:{artifact_id}",
             "note": json.dumps(verdict.observed, sort_keys=True, default=str)}
        ],
        "metadata": {DISCOVERY_AUTO_SCOPE_KEY: True, "decay": verdict.observed, "decay_reasons": verdict.reasons},
    }
