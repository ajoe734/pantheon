"""Regime classifier research models (SD-REGIME-STATE-ENGINE-20261114, waves 1: research layer).

Research-only: fits and validates models and registers them as registry ``model_artifact``
entries. Nothing here serves a decision-date regime and no language model produces any output.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
JUMPMODELS_VERSION = "0.1.1"
JUMPMODELS_LICENSE = "Apache-2.0"

_PACKAGE = "pantheon_vendor_jumpmodels"


def load_jumpmodels() -> ModuleType:
    """Import the pinned vendored ``jumpmodels`` under a private package name.

    The vendored directory is itself the package, so it is registered under a private name
    rather than put on ``sys.path``; a site-packages ``jumpmodels`` can never shadow it.
    """
    if _PACKAGE in sys.modules:
        return sys.modules[_PACKAGE]
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        _PACKAGE, VENDOR_DIR / "jumpmodels" / "__init__.py",
        submodule_search_locations=[str(VENDOR_DIR / "jumpmodels")],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - the vendored tree is checked in
        raise ImportError("vendored jumpmodels is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_PACKAGE, None)
        raise
    return module


def jump_module() -> ModuleType:
    """The vendored ``jump`` submodule (``JumpModel``)."""
    load_jumpmodels()
    return importlib.import_module(f"{_PACKAGE}.jump")


def library_versions() -> dict[str, str]:
    import ruptures
    import statsmodels

    return {"statsmodels": statsmodels.__version__, "ruptures": ruptures.__version__,
            "jumpmodels": JUMPMODELS_VERSION}
