"""Tests for the EP5 closeout renderer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.governance.ep5_proof.closeout_renderer import (
    CLOSEOUT_PACKET_VERSION,
    EP5CloseoutRendererError,
    build_ep5_closeout_packet,
    main,
    render_ep5_closeout_json,
    render_ep5_closeout_markdown,
)
from services.governance.ep5_proof.observation_report import build_canary_observation_report


_VALID_OBSERVATION_REQUEST = {
    "report_id": "canary-observation-closeout-001",
    "proof_id": "ep5-proof-closeout-001",
    "promotion_readiness_packet_id": "prp-closeout-001",
    "run_id": "canary-run-closeout-001",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-closeout-001",
    "runtime_binding_id": "rb-closeout-001",
    "artifact_id": "artifact-alpha-v1",
    "deployment_plan_id": "dep-canary-closeout-001",
    "mode": "validate_only",
    "started_at": "2026-05-20T00:00:00Z",
    "ended_at": "2026-05-20T00:05:00Z",
    "telemetry_refs": ["support/evidence/EP5-009-V2/telemetry-heartbeat.json"],
    "audit_refs": ["support/evidence/EP5-009-V2/governance-audit.json"],
    "incident_refs": [],
    "reconciliation_status": "within_threshold",
    "reconciliation_record_ref": "rec-canary-closeout-001",
    "evidence_refs": ["support/evidence/EP5-009-V2/manual-observation.json"],
    "rollback_drill_completed": True,
    "kill_switch_demo_completed": True,
}


def _observation_report(**overrides):
    return build_canary_observation_report({**_VALID_OBSERVATION_REQUEST, **overrides})


def test_builds_passing_closeout_packet_from_observation_report() -> None:
    packet = build_ep5_closeout_packet(
        _observation_report(),
        packet_id="ep5-closeout-fixed",
        generated_at="2026-05-20T00:10:00Z",
    )

    assert packet["packet_id"] == "ep5-closeout-fixed"
    assert packet["packet_version"] == CLOSEOUT_PACKET_VERSION
    assert packet["status"] == "passed"
    assert packet["can_close_out"] is True
    assert packet["depends_on_tasks"] == ["EP5-002-V2", "EP5-005-V2", "EP5-009-V2"]
    assert packet["inputs"] == {
        "observation_report_id": "canary-observation-closeout-001",
        "proof_packet_id": "ep5-proof-closeout-001",
        "promotion_readiness_packet_id": "prp-closeout-001",
    }
    assert packet["proof"]["flags"]["canary_runtime_started"] is True
    assert packet["proof"]["flags"]["runtime_heartbeat_received"] is True
    assert packet["proof"]["flags"]["telemetry_ingested"] is True
    assert packet["fail_closed"] == {
        "passed": True,
        "unsafe_true_flags": [],
        "broker_production_live_enabled": False,
        "capital_binding_live_enabled": False,
        "live_capital_side_effects": False,
        "deployment_stage": "canary",
        "order_route_mode": "validate_only",
    }
    assert packet["blocking_reasons"] == []


def test_json_rendering_is_deterministic_and_round_trips() -> None:
    packet = build_ep5_closeout_packet(
        _observation_report(),
        packet_id="ep5-closeout-json",
        generated_at="2026-05-20T00:10:00Z",
    )

    rendered = render_ep5_closeout_json(packet)

    assert rendered.endswith("\n")
    assert json.loads(rendered) == packet
    assert rendered.index('"blocking_reasons"') < rendered.index('"can_close_out"')


def test_markdown_renders_reviewer_summary() -> None:
    packet = build_ep5_closeout_packet(
        _observation_report(),
        packet_id="ep5-closeout-md",
        generated_at="2026-05-20T00:10:00Z",
    )

    markdown = render_ep5_closeout_markdown(packet)

    assert markdown.startswith("# EP5 Canary Closeout Packet\n")
    assert "| Status | passed |" in markdown
    assert "| Can close out | true |" in markdown
    assert "No blocking reasons." in markdown
    assert "support/evidence/EP5-009-V2/manual-observation.json" in markdown


def test_fail_closed_flag_blocks_closeout_even_when_observation_passes() -> None:
    report = _observation_report()
    report["promotion_readiness_packet"]["flags"]["broker_production_live_enabled"] = True
    report["promotion_readiness_packet"]["can_proceed"] = False

    packet = build_ep5_closeout_packet(
        report,
        packet_id="ep5-closeout-fail-closed",
        generated_at="2026-05-20T00:10:00Z",
    )

    assert packet["status"] == "failed"
    assert packet["can_close_out"] is False
    assert packet["fail_closed"]["passed"] is False
    assert "broker_production_live_enabled" in packet["fail_closed"]["unsafe_true_flags"]
    codes = [reason["code"] for reason in packet["blocking_reasons"]]
    assert "unsafe_flag_enabled" in codes
    assert "FAIL_CLOSED_FLAG_ENABLED" in codes


def test_observation_blockers_are_preserved_in_closeout() -> None:
    packet = build_ep5_closeout_packet(
        _observation_report(telemetry_refs=[]),
        packet_id="ep5-closeout-observation-failed",
        generated_at="2026-05-20T00:10:00Z",
    )

    assert packet["status"] == "failed"
    codes = [reason["code"] for reason in packet["blocking_reasons"]]
    assert "MISSING_TELEMETRY_REFS" in codes
    assert "NO_HEARTBEAT_RECEIVED" in codes
    assert "OBSERVATION_REPORT_NOT_PASSING" in codes


def test_rejects_missing_required_input_fields() -> None:
    with pytest.raises(EP5CloseoutRendererError, match="report_id"):
        build_ep5_closeout_packet({"run_id": "canary-run"})


def test_cli_writes_json_and_markdown_outputs(tmp_path: Path) -> None:
    observation_path = tmp_path / "observation-report.json"
    json_output = tmp_path / "closeout-packet.json"
    markdown_output = tmp_path / "README.md"
    observation_path.write_text(json.dumps(_observation_report()), encoding="utf-8")

    exit_code = main(
        [
            "--input",
            str(observation_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--packet-id",
            "ep5-closeout-cli",
        ]
    )

    assert exit_code == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["packet_id"] == "ep5-closeout-cli"
    assert payload["status"] == "passed"
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# EP5 Canary Closeout Packet" in markdown
    assert "| Packet ID | ep5-closeout-cli |" in markdown
