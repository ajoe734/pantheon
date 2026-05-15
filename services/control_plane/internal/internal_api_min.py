"""Import shim for ``services/control-plane/internal/internal_api_min.py``."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPL_PATH = Path(__file__).resolve().parents[2] / "control-plane" / "internal" / "internal_api_min.py"
_SPEC = importlib.util.spec_from_file_location(__name__, _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Cannot load minimal control-plane internal API from {_IMPL_PATH}")

_MODULE = sys.modules[__name__]
_MODULE.__file__ = str(_IMPL_PATH)
_MODULE.__loader__ = _SPEC.loader
_MODULE.__spec__ = _SPEC
_SPEC.loader.exec_module(_MODULE)
