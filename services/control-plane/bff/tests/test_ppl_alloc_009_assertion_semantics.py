"""Regression coverage for PPL-ALLOC-009 exact-pair assertion comparators.

The quarterly-recommendation submit, nested source_recommendation, and
allocation-row evaluate assertions must accept browser JSON round-trips that are
numerically identical (for example 1.0 -> 1) while still fail-closing on genuine
type or value divergence. This mirrors the semantic comparison already used by
the rebalance-proposal line assertion so the acceptance chain stops rediscovering
the same representational mismatch one deploy at a time.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402


def test_float_int_roundtrip_matches():
    # Browser JSON collapses 1.0 -> 1 / 0.0 -> 0; these must be treated as equal.
    assert main._pm12_semantic_values_match(1.0, 1)
    assert main._pm12_semantic_values_match(0.0, 0)
    assert main._pm12_semantic_values_match(1, 1.0)
    assert main._pm12_semantic_values_match(Decimal("1.0"), 1)


def test_nested_and_list_roundtrip_matches():
    assert main._pm12_semantic_values_match([1.0, 2.0], [1, 2])
    assert main._pm12_semantic_values_match(
        {"target_weight": 1.0, "delta": 0.0},
        {"delta": 0, "target_weight": 1},
    )
    assert main._pm12_semantic_values_match(
        {"a": [{"w": 0.5}, {"w": 1.0}]},
        {"a": [{"w": 0.5}, {"w": 1}]},
    )


def test_genuine_divergence_still_fails_closed():
    # Different numeric value must not match.
    assert not main._pm12_semantic_values_match(1.0, 2.0)
    # Booleans must never collapse into numbers.
    assert not main._pm12_semantic_values_match(True, 1)
    assert not main._pm12_semantic_values_match(1, True)
    assert not main._pm12_semantic_values_match(False, 0)
    # A stringified number is not the number.
    assert not main._pm12_semantic_values_match("1", 1)
    assert not main._pm12_semantic_values_match(None, 0)


def test_noncanonicalizable_input_fails_closed():
    # A value that cannot be canonicalized (non-finite / unsupported) stays strict.
    assert not main._pm12_semantic_values_match(float("nan"), 0)
    assert not main._pm12_semantic_values_match(float("inf"), float("inf"))
    assert not main._pm12_semantic_values_match({1: "int-key"}, {1: "int-key"})


def test_semantic_hash_still_used_for_rebalance_line():
    # The shared canonicalizer underpins the rebalance-line assertion hash too.
    line_float = {"target_weight": 1.0, "delta": 0.0, "capital_scope": "pool"}
    line_int = {"target_weight": 1, "delta": 0, "capital_scope": "pool"}
    assert main._pm12_allocation_line_assertion_hash(
        line_float
    ) == main._pm12_allocation_line_assertion_hash(line_int)
