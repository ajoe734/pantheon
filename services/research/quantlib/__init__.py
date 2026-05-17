"""QuantLib option pricing adapter exports.

This package also contains a legacy ``adapter/`` subpackage. Load the task
adapter from ``adapter.py`` by file path so package imports stay unambiguous.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ADAPTER_FILE = Path(__file__).with_name("adapter.py")
_SPEC = importlib.util.spec_from_file_location("_quantlib_option_adapter", _ADAPTER_FILE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Cannot load QuantLib option adapter from {_ADAPTER_FILE}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

price_european = _MODULE.price_european
price_american_binomial = _MODULE.price_american_binomial

__all__ = ["price_european", "price_american_binomial"]
