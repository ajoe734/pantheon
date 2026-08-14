"""Integration tests for L12 Twelve-Loop Verifier Harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from scripts.verify_l12_minimum_functional_closure import (
    CANONICAL_LOOP_IDS,
    CorrelatedChainResult,
    LoopCaseResult,
    TwelveLoopVerifier,
    VerifierReport,
    static_anti_mock_check,
    write_reports,
)


def test_canonical_loop_ids_has_twelve_items() -> None:
    assert len(CANONICAL_LOOP_IDS) == 12
    assert len(set(CANONICAL_LOOP_IDS)) == 12


def test_static_anti_mock_check_passes() -> None:
    assert static_anti_mock_check() is True


def test_report_writer_creates_json_and_markdown(tmp_path: Path) -> None:
    report = VerifierReport(
        timestamp="2026-08-13T00:00:00Z",
        stack_base_url="http://localhost:8000",
        overall_passed=True,
        loop_results=[
            LoopCaseResult(
                loop_id=lid,
                passed=True,
                trigger_identity=f"trig_{idx}",
                terminal_output_id=f"out_{idx}",
                readback_verified=True,
                next_consumer_receipt=f"rcpt_{idx}",
            )
            for idx, lid in enumerate(CANONICAL_LOOP_IDS)
        ],
        chain_results=[
            CorrelatedChainResult(
                chain_id="correlated_chain_full_ooda",
                passed=True,
                step_results=[{"step_index": 0, "loop_id": "source_ingestion", "status": "ok"}],
            )
        ],
        anti_mock_passed=True,
        summary={"total_loops": 12, "passed_loops": 12, "failed_loops": 0},
    )

    write_reports(report, tmp_path)

    json_file = tmp_path / "l12_verifier_report.json"
    md_file = tmp_path / "l12_verifier_report.md"

    assert json_file.exists()
    assert md_file.exists()

    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["overall_passed"] is True
    assert len(data["loop_results"]) == 12

    md_content = md_file.read_text(encoding="utf-8")
    assert "# L12 Minimum Functional Closure Verifier Report" in md_content
    assert "`source_ingestion`" in md_content


def test_verifier_report_failure_on_unreachable_stack(tmp_path: Path) -> None:
    # Point at invalid/unreachable URL port
    verifier = TwelveLoopVerifier(base_url="http://127.0.0.1:59999")
    report = verifier.verify_all()

    assert report.overall_passed is False
    assert report.summary["failed_loops"] == 12
    
    write_reports(report, tmp_path)
    json_file = tmp_path / "l12_verifier_report.json"
    assert json_file.exists()
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["overall_passed"] is False
