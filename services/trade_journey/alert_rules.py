"""TJ-E2E-011 declarative aggregate SLO alert rules.

Review feedback on PR #3460 required an "executable alert-rule artifact"
instead of alert thresholds hardcoded as Python `if` statements. This module
loads `trade_journey_alert_rules.json` and evaluates its threshold rules
against a `DataQualityMetrics`/`SLOTargets` pair: changing a threshold,
severity, comparator, or alert event type is a JSON edit, not a code change.

Only the aggregate, purely-numeric threshold rules live here (materializer
lag, completeness rates, SSE disconnects, API p95 latency).  Per-journey
diagnostics (stalled/orphan/identifier-conflict/conflicting-terminal/
reconciliation-mismatch/broker-reject) stay in
`slo_data_quality.evaluate_data_quality` because they require walking each
journey's own diagnostics/stage state, not a single metric-vs-threshold
comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from services.trade_journey.slo_data_quality import DataQualityMetrics, SLOTargets

import json

DEFAULT_ALERT_RULES_PATH = Path(__file__).with_name("trade_journey_alert_rules.json")
SCHEMA_VERSION = "trade_journey_alert_rules.v1"
_VALID_COMPARATORS = {"gt", "lt"}


@dataclass(frozen=True)
class AlertRule:
    code: str
    metric_name: str
    comparator: str
    severity: str
    event_type: str
    target_field: str | None = None
    target_unit_multiplier: float = 1.0
    threshold: float | None = None


@dataclass(frozen=True)
class AlertRuleFiring:
    rule: AlertRule
    observed_value: float
    target_value: float


def load_alert_rules(path: Path = DEFAULT_ALERT_RULES_PATH) -> tuple[AlertRule, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("trade_journey_alert_rules.json must contain a non-empty rules list")
    loaded: list[AlertRule] = []
    for rule in rules:
        comparator = rule.get("comparator")
        if comparator not in _VALID_COMPARATORS:
            raise ValueError(f"alert rule {rule.get('code')!r} has an invalid comparator: {comparator!r}")
        if rule.get("target_field") is None and rule.get("threshold") is None:
            raise ValueError(f"alert rule {rule.get('code')!r} needs a target_field or a threshold")
        loaded.append(AlertRule(
            code=rule["code"],
            metric_name=rule["metric_name"],
            comparator=comparator,
            severity=rule["severity"],
            event_type=rule["event_type"],
            target_field=rule.get("target_field"),
            target_unit_multiplier=float(rule.get("target_unit_multiplier", 1.0)),
            threshold=rule.get("threshold"),
        ))
    return tuple(loaded)


def _rule_threshold(rule: AlertRule, targets: "SLOTargets") -> float:
    if rule.target_field is not None:
        return float(getattr(targets, rule.target_field)) * rule.target_unit_multiplier
    return float(rule.threshold)  # type: ignore[arg-type]


def evaluate_aggregate_rules(
    metrics: "DataQualityMetrics",
    targets: "SLOTargets",
    rules: tuple[AlertRule, ...] | None = None,
) -> tuple[AlertRuleFiring, ...]:
    """Return every rule whose metric breaches its threshold.

    A metric value of ``None`` (no samples observed yet, e.g. no API latency
    recorded) never fires a rule rather than being coerced into a fabricated
    breach.
    """
    active_rules = rules if rules is not None else load_alert_rules()
    firings: list[AlertRuleFiring] = []
    for rule in active_rules:
        observed = getattr(metrics, rule.metric_name)
        if observed is None:
            continue
        threshold = _rule_threshold(rule, targets)
        breached = observed > threshold if rule.comparator == "gt" else observed < threshold
        if breached:
            firings.append(AlertRuleFiring(rule=rule, observed_value=observed, target_value=threshold))
    return tuple(firings)
