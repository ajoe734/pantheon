"""INSTRUMENT-AWARE-TELEMETRY-BFF-DRIFT-20261107 (AC 6).

The persona validation modules must accept the paper runtime's signed fill
quantities and its non-fill terminal outcomes (risk-gate rejection and a
recorded no trade) instead of demanding one positive fill per signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from services.persona import agent_usability_validation as usability
from services.persona import remaining_gap_validation as remaining


def _fill(event_id: str, quantity: float) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": "paper_fill_simulated",
        "metrics": {"fill_quantity": quantity, "fill_price": 100.0, "submitted_to_broker": False},
        "metadata": {"instrument_id": "eq:2330", "kind": "cash_share", "multiplier": 1.0},
    }


def _gate_rejection(event_id: str, reason: str = "leverage_limit") -> dict[str, Any]:
    # Shape published by paper_runtime for a risk-gate order_rejection.
    return {
        "event_id": event_id,
        "event_type": "order_rejection",
        "metrics": {"rejected_order_count": 1, "fill_quantity": 0.0, "submitted_to_broker": False},
        "metadata": {
            "reason": reason,
            "broker_submission_status": reason,
            "risk_gate_evaluated": True,
            "instrument_id": "ssf:QFF:202611",
        },
    }


def _no_trade(event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": "paper_order_simulated",
        "metrics": {"noop_count": 1, "fill_quantity": 0.0, "submitted_to_broker": False},
        "metadata": {
            "noop_reason": "already_at_target",
            "decision_status": "no_order",
            "broker_submission_status": "not_submitted_signal_noop",
        },
    }


def test_classifier_keeps_signed_fills_gate_rejections_and_no_trades_apart() -> None:
    events = [
        _fill("fill-short", -3.0),
        _fill("fill-long", 2.0),
        _gate_rejection("rej-gate"),
        # A broker error is not a risk-gate terminal.
        _gate_rejection("rej-broker", reason="tw_broker_error"),
        _no_trade("no-trade"),
        # A paper_order_simulated that is not a recorded no trade is ignored.
        {"event_id": "sim", "event_type": "paper_order_simulated", "metadata": {}},
        {"event_id": "settled", "event_type": "paper_futures_settled", "metadata": {}},
    ]
    outcomes = usability.classify_paper_terminal_events(events)
    assert [e["event_id"] for e in outcomes["fills"]] == ["fill-short", "fill-long"]
    assert [e["event_id"] for e in outcomes["rejections"]] == ["rej-gate"]
    assert [e["event_id"] for e in outcomes["no_trades"]] == ["no-trade"]


def _signal(signal_id: str, action: str, *, quantity: int = 3) -> dict[str, Any]:
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strat-terminal",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbol": "AAPL.US",
        "action": action,
        "direction": "LONG" if action == "BUY" else "SHORT",
        "quantity": quantity,
        "quantity_type": "SHARES",
        "order_type": "MARKET",
        "source_worker": "persona-terminal-acceptance",
        "binding_id": "binding-case-terminal",
        "runtime_id": "runtime-case-terminal",
        "capital_pool_id": "pool-usability-persona-terminal",
        "artifact_id": "artifact-strat-terminal",
        "artifact_version": "3000.1.0",
        "plan_id": "plan-usability-persona-terminal",
        "persona_capital_binding_id": "pcb-usability-persona-terminal",
        "metadata": {"market_price": 100.0, "market_data": {"close": 100.0, "symbol": "AAPL.US"}},
    }


def test_execute_signals_accepts_a_real_negative_sell_fill(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_LIFECYCLE_OUTBOX_PATH", str(tmp_path / "lifecycle-outbox.json"))
    result = usability._execute_signals(
        [_signal("sig-sell-1", "SELL")],
        case_id="case-terminal",
        persona_id="persona-terminal",
    )
    (fill,) = result["fill_events"]
    assert fill["metrics"]["fill_quantity"] == -3.0
    assert result["filled"] is True
    assert result["fill_count"] == 1
    assert result["fill_rate"] == 1.0
    assert result["rejection_events"] == [] and result["no_trade_events"] == []


class _ScriptedRuntime:
    """Stands in for PaperRuntimeService and emits scripted terminal events."""

    script: list[dict[str, Any]] = []

    def __init__(self, *, telemetry_emitter: Any, **_kwargs: Any) -> None:
        self._telemetry = telemetry_emitter

    def drain_once(self) -> dict[str, Any]:
        self._telemetry.events.extend(type(self).script)
        return {"status": "scripted"}


@pytest.fixture()
def scripted_runtime(monkeypatch):
    monkeypatch.setattr(usability, "PaperRuntimeService", _ScriptedRuntime)
    return _ScriptedRuntime


def test_execute_signals_accepts_a_gate_rejection_terminal_beside_a_fill(scripted_runtime) -> None:
    scripted_runtime.script = [_fill("fill-1", -3.0), _gate_rejection("rej-1")]
    result = usability._execute_signals(
        [_signal("sig-1", "SELL"), _signal("sig-2", "BUY")],
        case_id="case-terminal",
        persona_id="persona-terminal",
    )
    assert result["fill_count"] == 1
    assert [e["event_id"] for e in result["rejection_events"]] == ["rej-1"]
    assert result["fill_rate"] == 0.5


def test_execute_signals_accepts_a_recorded_no_trade_terminal(scripted_runtime) -> None:
    scripted_runtime.script = [_no_trade("no-trade-1")]
    result = usability._execute_signals(
        [_signal("sig-1", "BUY")], case_id="case-terminal", persona_id="persona-terminal"
    )
    assert result["fill_count"] == 0
    assert [e["event_id"] for e in result["no_trade_events"]] == ["no-trade-1"]
    # A book that only skipped has not traded.
    assert result["filled"] is False


def test_execute_signals_still_fails_when_a_signal_has_no_terminal_outcome(scripted_runtime) -> None:
    scripted_runtime.script = [_fill("fill-1", 3.0)]
    with pytest.raises(AssertionError, match="expected 2 terminal outcomes"):
        usability._execute_signals(
            [_signal("sig-1", "BUY"), _signal("sig-2", "BUY")],
            case_id="case-terminal",
            persona_id="persona-terminal",
        )


def test_execute_signals_does_not_count_a_broker_error_rejection_as_terminal(scripted_runtime) -> None:
    scripted_runtime.script = [_gate_rejection("rej-broker", reason="tw_broker_error")]
    with pytest.raises(AssertionError, match="expected 1 terminal outcomes"):
        usability._execute_signals(
            [_signal("sig-1", "BUY")], case_id="case-terminal", persona_id="persona-terminal"
        )


@pytest.mark.parametrize(
    "lifecycle,events,expected",
    [
        ("market_fill_ack_recovered", [_fill("f", -2.0)], "paper_fill_simulated"),
        ("market_fill_ack_recovered", [_gate_rejection("r")], "order_rejection"),
        ("market_fill_ack_recovered", [_gate_rejection("r", "tw_broker_error")], "paper_fill_simulated"),
        ("hold_noop_recovered", [_no_trade("n")], "paper_order_simulated"),
        ("hold_noop_recovered", [_gate_rejection("r", "price_stale")], "order_rejection"),
    ],
)
def test_remaining_gap_expected_event_type_accepts_a_gate_rejection_terminal(lifecycle, events, expected) -> None:
    case = SimpleNamespace(lean_lifecycle=lifecycle)
    assert remaining._expected_lean_event_type(case, events) == expected


def test_remaining_gap_expected_event_type_is_unchanged_without_events() -> None:
    case = SimpleNamespace(lean_lifecycle="market_fill_ack_recovered")
    assert remaining._expected_lean_event_type(case) == "paper_fill_simulated"
    assert remaining._expected_lean_event_type(SimpleNamespace(lean_lifecycle="hold_noop_recovered")) == "paper_order_simulated"
