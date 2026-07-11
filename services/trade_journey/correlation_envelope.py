"""Importable facade for the canonical control-plane correlation contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "control-plane"
    / "specs"
    / "trade_journey"
    / "correlation_envelope.py"
)
_SPEC = importlib.util.spec_from_file_location("_pantheon_correlation_envelope", _CONTRACT)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - invalid installation
    raise ImportError(f"cannot load correlation envelope contract from {_CONTRACT}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

SCHEMA_VERSION = _MODULE.SCHEMA_VERSION
ENVIRONMENTS = _MODULE.ENVIRONMENTS
STABLE_FIELDS = _MODULE.STABLE_FIELDS
REQUIRED_FIELDS = _MODULE.REQUIRED_FIELDS
CorrelationEnvelopeError = _MODULE.CorrelationEnvelopeError
mint_trade_envelope = _MODULE.mint_trade_envelope
propagate_envelope = _MODULE.propagate_envelope
assert_no_field_loss = _MODULE.assert_no_field_loss
validate_envelope = _MODULE.validate_envelope

__all__ = [
    "SCHEMA_VERSION",
    "ENVIRONMENTS",
    "STABLE_FIELDS",
    "REQUIRED_FIELDS",
    "CorrelationEnvelopeError",
    "mint_trade_envelope",
    "propagate_envelope",
    "assert_no_field_loss",
    "validate_envelope",
]
