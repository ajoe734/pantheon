"""Governed QuantLib adapter exports plus OSS-QUANTLIB-001 pricing facade."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .quantlib_adapter import (
    GovernedMarketSnapshot,
    GovernedOptionSpec,
    GovernedBondSpec,
    GovernedQuantLibInputAdapter,
    QuantLibBackend,
    QuantLibWorkflowError,
    StubQuantLibBackend,
    run_quantlib_workflow,
)

_TASK_ADAPTER_FILE = Path(__file__).resolve().parents[1] / "adapter.py"
_TASK_SPEC = importlib.util.spec_from_file_location(
    "_quantlib_option_adapter", _TASK_ADAPTER_FILE
)
if _TASK_SPEC is None or _TASK_SPEC.loader is None:
    raise ImportError(f"Cannot load QuantLib option adapter from {_TASK_ADAPTER_FILE}")
_TASK_MODULE = importlib.util.module_from_spec(_TASK_SPEC)
_TASK_SPEC.loader.exec_module(_TASK_MODULE)

price_european = _TASK_MODULE.price_european
price_american_binomial = _TASK_MODULE.price_american_binomial

__all__ = [
    "GovernedMarketSnapshot",
    "GovernedOptionSpec",
    "GovernedBondSpec",
    "GovernedQuantLibInputAdapter",
    "QuantLibBackend",
    "QuantLibWorkflowError",
    "StubQuantLibBackend",
    "run_quantlib_workflow",
    "price_european",
    "price_american_binomial",
]
