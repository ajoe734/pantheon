"""Tests for EP5ProofPacket generator (EP5-005-V2, A2.2)."""
from __future__ import annotations

import pytest

from services.governance.ep5_proof.packet_generator import (
    EP5ProofGeneratorError,
    PROOF_FLAG_BROKER_PRODUCTION_LIVE,
    PROOF_FLAG_CANARY_RUNTIME_STARTED,
    PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS,
    PROOF_FLAG_ORDER_ROUTE_MODE,
    PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED,
    PROOF_FLAG_TELEMETRY_INGESTED,
    generate_ep5_proof_packet,
)
from services.governance.promotion_readiness.packet_model import (
    PASSING_STATUSES,
    PromotionReadinessPacket,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_CANARY_RUN = {
    "run_id": "canary-run-001",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-twse-canary-01",
    "environment": "canary",
    "deployment_stage": "canary",
    "runtime_started": True,
    "heartbeat_count": 5,
    "order_route_mode": "paper",
    "telemetry_event_count": 12,
    "result": "pass",
    "started_at": "2026-05-19T10:00:00Z",
    "finished_at": "2026-05-19T10:05:00Z",
}


def _make_run(**overrides):
    run = dict(_VALID_CANARY_RUN)
    run.update(overrides)
    return run


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPassingCanaryRun:
    def test_returns_promotion_readiness_packet(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert isinstance(packet, PromotionReadinessPacket)

    def test_can_proceed_true(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert packet.can_proceed is True

    def test_no_blocking_reasons(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert len(packet.blocking_reasons) == 0

    def test_all_proof_flags_true(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        flags = packet.flags.values
        assert flags[PROOF_FLAG_CANARY_RUNTIME_STARTED] is True
        assert flags[PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED] is True
        assert flags[PROOF_FLAG_ORDER_ROUTE_MODE] is True
        assert flags[PROOF_FLAG_TELEMETRY_INGESTED] is True

    def test_fail_closed_flags_always_false(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        flags = packet.flags.values
        assert flags[PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS] is False
        assert flags[PROOF_FLAG_BROKER_PRODUCTION_LIVE] is False

    def test_evidence_all_pass(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        for item in packet.evidence.provided:
            assert item.status in PASSING_STATUSES, (
                f"Expected evidence item {item.key!r} to have a passing status, got {item.status!r}"
            )

    def test_evidence_missing_empty(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert len(packet.evidence.missing) == 0

    def test_target_fields(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert packet.target.target_id == "canary-run-001"
        assert packet.target.environment == "canary"
        assert packet.target.deployment_stage == "canary"
        assert packet.target.metadata["persona_id"] == "persona-alpha"
        assert packet.target.metadata["runtime_id"] == "rt-twse-canary-01"

    def test_depends_on_tasks_includes_ep5_001_v2(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert "EP5-001-V2" in packet.depends_on_tasks

    def test_approval_gates_not_required_at_generator_level(self):
        # The generator emits evidence; human approvals happen at the promotion layer.
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        assert packet.approval.risk_owner.required is False
        assert packet.approval.risk_owner.state == "not_required"
        assert packet.approval.operator.required is False
        assert packet.approval.operator.state == "not_required"

    def test_packet_id_override(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN, packet_id="fixed-id-123")
        assert packet.packet_id == "fixed-id-123"

    def test_extensions_carry_run_metadata(self):
        packet = generate_ep5_proof_packet(_VALID_CANARY_RUN)
        ext = packet.extensions
        assert ext["canary_run_result"] == "pass"
        assert ext["heartbeat_count"] == 5
        assert ext["order_route_mode"] == "paper"
        assert ext["telemetry_event_count"] == 12

    def test_paper_trading_mode_also_passes(self):
        packet = generate_ep5_proof_packet(_make_run(order_route_mode="paper_trading"))
        assert packet.flags.values[PROOF_FLAG_ORDER_ROUTE_MODE] is True
        assert packet.can_proceed is True


# ---------------------------------------------------------------------------
# Proof-flag failures (one per flag)
# ---------------------------------------------------------------------------


class TestProofFlagFailures:
    def test_runtime_not_started_blocks(self):
        packet = generate_ep5_proof_packet(_make_run(runtime_started=False))
        assert packet.can_proceed is False
        codes = [b.code for b in packet.blocking_reasons]
        assert "CANARY_RUNTIME_NOT_STARTED" in codes
        assert packet.flags.values[PROOF_FLAG_CANARY_RUNTIME_STARTED] is False

    def test_no_heartbeats_blocks(self):
        packet = generate_ep5_proof_packet(_make_run(heartbeat_count=0))
        assert packet.can_proceed is False
        codes = [b.code for b in packet.blocking_reasons]
        assert "NO_HEARTBEAT_RECEIVED" in codes
        assert packet.flags.values[PROOF_FLAG_RUNTIME_HEARTBEAT_RECEIVED] is False

    def test_live_order_route_mode_blocks(self):
        packet = generate_ep5_proof_packet(_make_run(order_route_mode="live"))
        assert packet.can_proceed is False
        codes = [b.code for b in packet.blocking_reasons]
        assert "ORDER_ROUTE_NOT_PAPER" in codes
        assert packet.flags.values[PROOF_FLAG_ORDER_ROUTE_MODE] is False

    def test_no_telemetry_blocks(self):
        packet = generate_ep5_proof_packet(_make_run(telemetry_event_count=0))
        assert packet.can_proceed is False
        codes = [b.code for b in packet.blocking_reasons]
        assert "TELEMETRY_NOT_INGESTED" in codes
        assert packet.flags.values[PROOF_FLAG_TELEMETRY_INGESTED] is False

    def test_failed_canary_result_blocks(self):
        packet = generate_ep5_proof_packet(_make_run(result="fail"))
        assert packet.can_proceed is False


# ---------------------------------------------------------------------------
# Fail-closed invariant: live flags must always block
# ---------------------------------------------------------------------------


class TestFailClosedInvariants:
    def test_live_capital_side_effects_blocks(self):
        packet = generate_ep5_proof_packet(
            _make_run(live_capital_side_effects=True)
        )
        assert packet.can_proceed is False
        codes = [b.code for b in packet.blocking_reasons]
        assert "LIVE_CAPITAL_SIDE_EFFECTS" in codes

    def test_broker_production_live_blocks(self):
        packet = generate_ep5_proof_packet(
            _make_run(broker_production_live_enabled=True)
        )
        assert packet.can_proceed is False
        codes = [b.code for b in packet.blocking_reasons]
        assert "BROKER_PRODUCTION_LIVE" in codes

    def test_fail_closed_flags_in_output_always_false_even_when_input_true(self):
        # The packet's flags subtree must never expose live=True
        packet = generate_ep5_proof_packet(
            _make_run(live_capital_side_effects=True, broker_production_live_enabled=True)
        )
        assert packet.flags.values[PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS] is False
        assert packet.flags.values[PROOF_FLAG_BROKER_PRODUCTION_LIVE] is False


# ---------------------------------------------------------------------------
# Missing / malformed input
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_missing_run_id_raises(self):
        run = dict(_VALID_CANARY_RUN)
        del run["run_id"]
        with pytest.raises(EP5ProofGeneratorError, match="run_id"):
            generate_ep5_proof_packet(run)

    def test_missing_persona_id_raises(self):
        run = dict(_VALID_CANARY_RUN)
        del run["persona_id"]
        with pytest.raises(EP5ProofGeneratorError, match="persona_id"):
            generate_ep5_proof_packet(run)

    def test_missing_runtime_id_raises(self):
        run = dict(_VALID_CANARY_RUN)
        del run["runtime_id"]
        with pytest.raises(EP5ProofGeneratorError, match="runtime_id"):
            generate_ep5_proof_packet(run)

    def test_missing_environment_raises(self):
        run = dict(_VALID_CANARY_RUN)
        del run["environment"]
        with pytest.raises(EP5ProofGeneratorError, match="environment"):
            generate_ep5_proof_packet(run)

    def test_missing_result_raises(self):
        run = dict(_VALID_CANARY_RUN)
        del run["result"]
        with pytest.raises(EP5ProofGeneratorError, match="result"):
            generate_ep5_proof_packet(run)

    def test_empty_run_id_raises(self):
        with pytest.raises(EP5ProofGeneratorError, match="run_id"):
            generate_ep5_proof_packet(_make_run(run_id=""))

    def test_empty_dict_raises(self):
        with pytest.raises(EP5ProofGeneratorError):
            generate_ep5_proof_packet({})
