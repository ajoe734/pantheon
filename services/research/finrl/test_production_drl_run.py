"""
Tests for production DRL run.
"""
import pytest
from production_drl_run import main
import os

def test_production_drl_training():
    # Run training
    result = main()
    
    # Assert metrics exist
    assert "sharpe" in result.training_result.metrics
    assert result.training_result.metrics["sharpe"] > 0
    
    # Assert evaluation file created
    assert os.path.exists("support/evidence/OSS-FINRL-V2-001/evaluation_summary.json")

def test_admission_packet_generation():
    from registry_admission_packet import generate_admission_packet
    run_id = "test-run"
    summary = {"sharpe": 1.0}
    packet = generate_admission_packet(run_id, summary, "test-artifact")
    assert packet["packet_id"].startswith("finrl-admission-test-run")
    assert os.path.exists("support/evidence/OSS-FINRL-V2-001/admission_packet.json")
