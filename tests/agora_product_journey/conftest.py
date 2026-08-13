"""Shared fixtures and environment setup for Agora Product Journey tests."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_paths() -> None:
    for subpath in [
        "",
        "services/control-plane",
        "services/control-plane/bff",
        "services/control-plane/governance",
        "services/policy-learning",
        "services/consultation",
    ]:
        p = str(REPO_ROOT / subpath) if subpath else str(REPO_ROOT)
        if p not in sys.path:
            sys.path.insert(0, p)


_ensure_paths()


@pytest.fixture(autouse=True)
def setup_agora_sys_paths() -> None:
    """Ensure service subpaths are always in sys.path before every test."""
    _ensure_paths()


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Provide an isolated temporary directory for test stores and file backends."""
    with tempfile.TemporaryDirectory(prefix="agora_test_ws_") as tmp:
        yield Path(tmp)
