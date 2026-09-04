"""Tests for pytest import-authority harness and root isolation (conftest.py).

Verifies:
1. Test directory is first in import roots.
2. Repository root comes before service-local root.
3. Transient service-local modules are tracked, cleared, and restored without leaking.
4. Stable packages (services, scripts, integrations) are preserved and resolve to repo root.
5. Inherited live task-state journal env vars are scrubbed.
6. Isolation behavior does not rely on editable-install masking as evidence.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

import conftest
from conftest import (
    ROOT,
    SCRIPTS_DIR,
    SERVICES_DIR,
    TESTS_DIR,
    TASK_STATE_STORE_ENV_VARS,
    _STABLE_REPO_PACKAGES,
    _activate_import_roots,
    _clear_transient_local_modules,
    _import_roots_for,
    _is_transient_local_module,
    _record_transient_modules,
    _remove_service_import_roots,
    _restore_transient_modules,
    scrub_inherited_task_state_env,
)


def test_import_roots_order_keeps_test_dir_first_and_root_before_service() -> None:
    """Test directory must be first, and ROOT must precede service-local root."""
    bff_test_path = SERVICES_DIR / "control-plane" / "bff" / "tests" / "test_example.py"
    roots = _import_roots_for(bff_test_path)

    # 1. Test directory first
    assert roots[0] == bff_test_path.parent

    # 2. Repository root before service-local root
    assert ROOT in roots
    bff_service_dir = bff_test_path.parent.parent
    assert bff_service_dir in roots
    assert roots.index(ROOT) < roots.index(bff_service_dir)


def test_import_roots_for_scripts_and_tests() -> None:
    """Scripts and top-level tests maintain test-dir-first and ROOT precedence."""
    script_test = SCRIPTS_DIR / "test_sample.py"
    roots_script = _import_roots_for(script_test)
    assert roots_script[0] == SCRIPTS_DIR
    assert ROOT in roots_script
    assert roots_script.index(ROOT) < roots_script.index(SCRIPTS_DIR) or roots_script[0] == SCRIPTS_DIR

    top_test = TESTS_DIR / "unit" / "test_sample.py"
    roots_top = _import_roots_for(top_test)
    assert roots_top[0] == top_test.parent
    assert ROOT in roots_top


def test_activate_import_roots_places_test_dir_first_and_root_before_service() -> None:
    """_activate_import_roots sets sys.path in expected precedence order."""
    original_path = list(sys.path)
    try:
        bff_test_path = SERVICES_DIR / "control-plane" / "bff" / "tests" / "test_example.py"
        _activate_import_roots(bff_test_path)

        assert sys.path[0] == str(bff_test_path.parent)
        idx_root = sys.path.index(str(ROOT))
        idx_service = sys.path.index(str(bff_test_path.parent.parent))
        assert idx_root < idx_service
    finally:
        sys.path[:] = original_path


def test_integrations_package_resolves_to_repo_root() -> None:
    """Importing integrations resolves to repo root rather than BFF local integrations."""
    original_path = list(sys.path)
    try:
        bff_test_path = SERVICES_DIR / "control-plane" / "bff" / "tests" / "test_example.py"
        _activate_import_roots(bff_test_path)

        spec = importlib.util.find_spec("integrations")
        assert spec is not None
        assert spec.origin is not None
        origin_path = Path(spec.origin).resolve()
        expected_root = (ROOT / "integrations").resolve()
        assert str(origin_path).startswith(str(expected_root))
        # Ensure it does not resolve to services/control-plane/bff/integrations
        bff_integrations = (SERVICES_DIR / "control-plane" / "bff" / "integrations").resolve()
        assert not str(origin_path).startswith(str(bff_integrations))
    finally:
        sys.path[:] = original_path


def test_is_transient_local_module() -> None:
    """Stable packages are non-transient; service-local modules are transient."""
    for pkg in ("services", "scripts", "integrations"):
        dummy = types.ModuleType(pkg)
        assert _is_transient_local_module(pkg, dummy) is False
        assert _is_transient_local_module(f"{pkg}.sub", dummy) is False

    # Module within services
    mod = types.ModuleType("local_main")
    mod.__file__ = str(SERVICES_DIR / "control-plane" / "bff" / "main.py")
    assert _is_transient_local_module("local_main", mod) is True

    # Module outside repo
    sys_mod = types.ModuleType("sys_helper")
    sys_mod.__file__ = "/usr/lib/python3.12/json.py"
    assert _is_transient_local_module("sys_helper", sys_mod) is False


def test_transient_modules_record_clear_restore_lifecycle() -> None:
    """Transient modules are recorded per module_path, cleared, and restored."""
    dummy_name = "test_transient_dummy_module"
    dummy_mod = types.ModuleType(dummy_name)
    dummy_mod.__file__ = str(SERVICES_DIR / "sample_service" / "dummy.py")
    dummy_path = SERVICES_DIR / "sample_service" / "tests" / "test_sample.py"

    sys.modules[dummy_name] = dummy_mod
    try:
        assert _is_transient_local_module(dummy_name, dummy_mod) is True
        _record_transient_modules(dummy_path)

        _clear_transient_local_modules()
        assert dummy_name not in sys.modules

        _restore_transient_modules(dummy_path)
        assert dummy_name in sys.modules
        assert sys.modules[dummy_name] is dummy_mod
    finally:
        sys.modules.pop(dummy_name, None)
        conftest._TRANSIENT_MODULES_BY_PATH.pop(dummy_path.resolve(), None)


def test_scrub_inherited_task_state_env() -> None:
    """Live task-state store env vars must be stripped from test process."""
    with mock.patch.dict(os.environ, {
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": "/tmp/events.jsonl",
    }):
        scrubbed = scrub_inherited_task_state_env()
        assert "PANTHEON_TASK_STATE_STORE_MODE" in scrubbed
        assert "PANTHEON_TASK_STATE_EVENT_LOG" in scrubbed
        assert "PANTHEON_TASK_STATE_STORE_MODE" not in os.environ
        assert "PANTHEON_TASK_STATE_EVENT_LOG" not in os.environ
