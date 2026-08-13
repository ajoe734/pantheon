#!/usr/bin/env python3
"""Tests for verify_product_v2_current_closure.py."""
from __future__ import annotations

from pathlib import Path

from scripts.verify_product_v2_current_closure import (
    verify_evidence_manifests,
    verify_integrated_r3_closure,
)


def test_verify_evidence_manifests():
    results = verify_evidence_manifests()
    assert results["passed"] is True
    assert len(results["missing_tracks"]) == 0
    assert len(results["invalid_manifests"]) == 0


def test_verify_integrated_r3_closure():
    report = verify_integrated_r3_closure()
    assert report["status"] == "passed"
    assert len(report["r3_tracks_verified"]) == 8
    
    evidence_dir = Path("docs/deployment/evidence/product-v2/integrated-hosted-r3")
    assert (evidence_dir / "evidence.json").exists()
    assert (evidence_dir / "evidence.md").exists()
