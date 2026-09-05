"""Narrow test-only loader for suites requiring isolated BFF composition."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def load_isolated_composition(name: str) -> ModuleType:
    """Load main under the real package without path or top-level aliases."""
    qualified_name = f"services.control_plane.bff._test_composition_{name}"
    spec = importlib.util.spec_from_file_location(qualified_name, MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load isolated BFF composition from {MAIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module
