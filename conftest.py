"""Root pytest harness for full-suite collection.

Pantheon services intentionally keep small service-local modules named
``main.py``, ``adapter.py``, ``models.py``, and similar.  A root pytest run must
not let one service's top-level imports leak into another service's tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parent
SERVICES_DIR = ROOT / "services"
SCRIPTS_DIR = ROOT / "scripts"
# A worker shell and a task worktree inherit the provisioned live task-state
# journal binding. A test run must never reach it: an inherited event log turns
# any fixture board into a real authoritative commit.
TASK_STATE_STORE_ENV_VARS = (
    "PANTHEON_TASK_STATE_STORE_MODE",
    "PANTHEON_TASK_STATE_EVENT_LOG",
)
_STABLE_REPO_PACKAGES = {"integrations", "scripts", "services"}
_TRANSIENT_MODULES_BY_PATH: dict[Path, dict[str, ModuleType]] = {}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _module_file(module: ModuleType) -> Path | None:
    filename = getattr(module, "__file__", None)
    if not filename:
        return None
    try:
        return Path(filename).resolve()
    except OSError:
        return None


def _is_transient_local_module(name: str, module: ModuleType) -> bool:
    root_name = name.partition(".")[0]
    if root_name in _STABLE_REPO_PACKAGES:
        return False

    path = _module_file(module)
    if path is None:
        return False

    return _is_relative_to(path, SERVICES_DIR) or _is_relative_to(path, SCRIPTS_DIR)


def _clear_transient_local_modules() -> None:
    for name, module in list(sys.modules.items()):
        if module is not None and _is_transient_local_module(name, module):
            del sys.modules[name]


def _remove_service_import_roots() -> None:
    retained: list[str] = []
    for entry in sys.path:
        if not entry:
            retained.append(entry)
            continue
        try:
            path = Path(entry).resolve()
        except OSError:
            retained.append(entry)
            continue
        if path == ROOT:
            retained.append(entry)
            continue
        if _is_relative_to(path, SERVICES_DIR) or _is_relative_to(path, SCRIPTS_DIR):
            continue
        retained.append(entry)
    sys.path[:] = retained


def _import_roots_for(module_path: Path) -> list[Path]:
    path = module_path.resolve()
    roots: list[Path] = []

    if _is_relative_to(path, SCRIPTS_DIR):
        roots.append(SCRIPTS_DIR)
    elif _is_relative_to(path, SERVICES_DIR):
        roots.append(path.parent)
        if path.parent.name == "tests":
            roots.append(path.parent.parent)

    roots.append(ROOT)

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        text = str(root)
        if text not in seen:
            seen.add(text)
            unique_roots.append(root)
    return unique_roots


def _activate_import_roots(module_path: Path) -> None:
    _remove_service_import_roots()
    for root in reversed(_import_roots_for(module_path)):
        text = str(root)
        if text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)


def _record_transient_modules(module_path: Path) -> None:
    _TRANSIENT_MODULES_BY_PATH[module_path.resolve()] = {
        name: module
        for name, module in sys.modules.items()
        if module is not None and _is_transient_local_module(name, module)
    }


def _restore_transient_modules(module_path: Path) -> None:
    for name, module in _TRANSIENT_MODULES_BY_PATH.get(module_path.resolve(), {}).items():
        sys.modules[name] = module


class IsolatedServiceModule(pytest.Module):
    def _getobj(self):  # noqa: ANN001
        module_path = Path(self.path)
        _clear_transient_local_modules()
        _activate_import_roots(module_path)
        module = super()._getobj()
        _record_transient_modules(module_path)
        return module


def pytest_pycollect_makemodule(module_path: Path, parent):  # noqa: ANN001
    return IsolatedServiceModule.from_parent(parent, path=module_path)


def scrub_inherited_task_state_env() -> list[str]:
    """Drop any inherited live task-state journal binding from this process."""

    return [name for name in TASK_STATE_STORE_ENV_VARS if os.environ.pop(name, None) is not None]


def pytest_configure(config):  # noqa: ANN001
    scrub_inherited_task_state_env()


def pytest_runtest_setup(item):  # noqa: ANN001
    scrub_inherited_task_state_env()
    module_path = Path(item.path)
    _clear_transient_local_modules()
    _activate_import_roots(module_path)
    _restore_transient_modules(module_path)

