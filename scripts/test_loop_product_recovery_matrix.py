#!/usr/bin/env python3
"""
Tests for loop recovery and fault-injection matrix.
"""
import pytest
import os
import importlib

# Bypass package import SyntaxError
run_module = importlib.import_module("scripts.run_loop_product_recovery_matrix")

@pytest.mark.asyncio
async def test_recovery_matrix_scenarios():
    # Execute the full matrix scenarios
    # run_matrix initializes the DB schema, runs each scenario, and writes evidence files.
    await run_module.run_matrix()
    
    # Assert evidence files exist
    assert os.path.exists("docs/deployment/evidence/loop-product-level/LOOP-PROD-REC-001/evidence.json")
    assert os.path.exists("docs/deployment/evidence/loop-product-level/LOOP-PROD-REC-001/evidence.sha256")
