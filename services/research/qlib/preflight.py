"""
Qlib pre-activation preflight scaffold.

Checks the required Qlib supervised-alpha activation gates without importing
the Qlib training adapter, executing LightGBM, or writing registry/governance
state.  The report is fail-closed: missing probes are BLOCKED, not UNKNOWN,
because production activation must not start without explicit evidence.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence

MIN_PRODUCTION_INSTRUMENTS = 50
MIN_HISTORY_YEARS = 2.0
REQUIRED_OHLCV_FIELDS = frozenset({"open", "high", "low", "close", "volume"})
ALLOWED_DATA_FREQUENCIES = frozenset({"daily", "intraday"})


class GateState(str, Enum):
    OPEN = "open"
    BLOCKED = "blocked"


@dataclasses.dataclass(frozen=True)
class GateResult:
    name: str
    state: GateState
    detail: str
    required: bool = True


@dataclasses.dataclass(frozen=True)
class QlibPreflightReport:
    gates: List[GateResult]
    activation_allowed: bool
    summary: str

    @classmethod
    def from_gates(cls, gates: List[GateResult]) -> "QlibPreflightReport":
        blocked = [gate for gate in gates if gate.required and gate.state == GateState.BLOCKED]
        if blocked:
            names = ", ".join(gate.name for gate in blocked)
            summary = f"Blocked by required gates: {names}"
            return cls(gates=list(gates), activation_allowed=False, summary=summary)
        return cls(
            gates=list(gates),
            activation_allowed=True,
            summary="All required Qlib pre-activation gates open.",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activation_allowed": self.activation_allowed,
            "summary": self.summary,
            "gates": [dataclasses.asdict(gate) for gate in self.gates],
        }


def check_rs003_candidate(candidate_probe: Optional[Mapping[str, Any]]) -> GateResult:
    """Require a replication-gate-passed StrategySpec candidate registry ref."""
    if candidate_probe is None:
        return GateResult(
            name="rs003_candidate",
            state=GateState.BLOCKED,
            detail="RS-003 candidate probe missing; attach candidate registry ref and pass evidence.",
        )

    registry_id = _first_present_str(
        candidate_probe,
        ("candidate_registry_id", "registry_id", "artifact_id"),
    )
    artifact_state = _str(candidate_probe.get("artifact_state"))
    evidence_ref = _first_present_str(
        candidate_probe,
        ("rs003_evidence_ref", "replication_evidence_ref", "pass_evidence_ref"),
    )

    problems: List[str] = []
    if not registry_id:
        problems.append("candidate registry ref missing")
    if artifact_state != "candidate":
        problems.append(f"artifact_state={artifact_state!r}, need 'candidate'")
    if not evidence_ref:
        problems.append("RS-003 pass evidence ref missing")

    if problems:
        return GateResult(
            name="rs003_candidate",
            state=GateState.BLOCKED,
            detail="; ".join(problems),
        )
    return GateResult(
        name="rs003_candidate",
        state=GateState.OPEN,
        detail=f"RS-003 candidate ready: {registry_id}",
    )


def check_governed_dataset(dataset_manifest: Optional[Mapping[str, Any]]) -> GateResult:
    """Require governed >=50 instrument, >=2 year OHLCV dataset evidence."""
    if dataset_manifest is None:
        return GateResult(
            name="governed_dataset",
            state=GateState.BLOCKED,
            detail="Governed dataset manifest missing; attach refs, universe size, and history window.",
        )

    refs = _string_list(dataset_manifest.get("source_dataset_refs"))
    if not refs:
        single_ref = _str(dataset_manifest.get("source_dataset_ref"))
        refs = [single_ref] if single_ref else []

    fields = {field.lower() for field in _string_list(dataset_manifest.get("ohlcv_fields"))}
    missing_fields = sorted(REQUIRED_OHLCV_FIELDS - fields)
    instrument_count = _int(dataset_manifest.get("num_instruments"))
    frequency = _str(dataset_manifest.get("data_frequency")).lower()
    history_years = _history_years(dataset_manifest)
    governed = dataset_manifest.get("governed")

    problems: List[str] = []
    if not refs:
        problems.append("source_dataset_refs missing")
    if governed is not True:
        problems.append("governed=True not proven")
    if instrument_count < MIN_PRODUCTION_INSTRUMENTS:
        problems.append(
            f"num_instruments={instrument_count}, need >= {MIN_PRODUCTION_INSTRUMENTS}"
        )
    if history_years < MIN_HISTORY_YEARS:
        problems.append(f"history_years={history_years:.2f}, need >= {MIN_HISTORY_YEARS:.1f}")
    if frequency not in ALLOWED_DATA_FREQUENCIES:
        problems.append(f"data_frequency={frequency!r}, need one of {sorted(ALLOWED_DATA_FREQUENCIES)}")
    if missing_fields:
        problems.append(f"missing OHLCV fields: {missing_fields}")

    if problems:
        return GateResult(
            name="governed_dataset",
            state=GateState.BLOCKED,
            detail="; ".join(problems),
        )
    return GateResult(
        name="governed_dataset",
        state=GateState.OPEN,
        detail=(
            f"Governed dataset ready: {instrument_count} instruments, "
            f"{history_years:.2f} years, refs={refs}"
        ),
    )


def check_strategy_spec_binding(binding_probe: Optional[Mapping[str, Any]]) -> GateResult:
    """Require a concrete StrategySpec binding and supervised label definition."""
    if binding_probe is None:
        return GateResult(
            name="strategy_spec_binding",
            state=GateState.BLOCKED,
            detail="StrategySpec binding missing; attach spec id, label definition, and target framing.",
        )

    spec_id = _first_present_str(
        binding_probe,
        ("source_strategy_spec_id", "strategy_spec_id", "canonical_strategy_spec_id"),
    )
    strategy_id = _str(binding_probe.get("strategy_id"))
    label_definition = _str(binding_probe.get("label_definition"))
    supervised_target = _str(binding_probe.get("supervised_target"))
    problem_type = _str(binding_probe.get("problem_type")).lower()

    problems: List[str] = []
    if not spec_id:
        problems.append("StrategySpec id missing")
    if not strategy_id:
        problems.append("strategy_id missing")
    if not label_definition:
        problems.append("label_definition missing")
    if not supervised_target:
        problems.append("supervised_target missing")
    if problem_type and problem_type not in {"prediction", "ranking", "regression"}:
        problems.append(f"problem_type={problem_type!r} is not a supervised alpha framing")

    if problems:
        return GateResult(
            name="strategy_spec_binding",
            state=GateState.BLOCKED,
            detail="; ".join(problems),
        )
    return GateResult(
        name="strategy_spec_binding",
        state=GateState.OPEN,
        detail=f"StrategySpec binding ready: {spec_id} -> {strategy_id}",
    )


def run_qlib_preflight(
    *,
    rs003_candidate_probe: Optional[Mapping[str, Any]] = None,
    governed_dataset_manifest: Optional[Mapping[str, Any]] = None,
    strategy_spec_binding: Optional[Mapping[str, Any]] = None,
) -> QlibPreflightReport:
    """Run the offline, non-writing Qlib activation readiness check."""
    gates = [
        check_rs003_candidate(rs003_candidate_probe),
        check_governed_dataset(governed_dataset_manifest),
        check_strategy_spec_binding(strategy_spec_binding),
    ]
    return QlibPreflightReport.from_gates(gates)


def load_preflight_packet(packet: Mapping[str, Any]) -> QlibPreflightReport:
    """Build a report from a JSON-like packet without side effects."""
    return run_qlib_preflight(
        rs003_candidate_probe=packet.get("rs003_candidate"),
        governed_dataset_manifest=packet.get("governed_dataset"),
        strategy_spec_binding=packet.get("strategy_spec_binding"),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI helper: read one JSON packet from stdin and print a readiness report."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run the Qlib pre-activation preflight.")
    parser.parse_args(argv)

    packet = json.loads(sys.stdin.read() or "{}")
    if not isinstance(packet, Mapping):
        raise SystemExit("preflight packet must be a JSON object")
    report = load_preflight_packet(packet)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.activation_allowed else 2


def _first_present_str(payload: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = _str(payload.get(key))
        if value:
            return value
    return ""


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _history_years(manifest: Mapping[str, Any]) -> float:
    value = manifest.get("history_years")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    start = _parse_date(_str(manifest.get("start_date")))
    end = _parse_date(_str(manifest.get("end_date")))
    if start is None or end is None or end <= start:
        return 0.0
    return (end - start).days / 365.25


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
