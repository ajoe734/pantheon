"""Regression checks for the canonical BFF internal-import boundary."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BFF = ROOT / "services/control-plane/bff"
CANONICAL = "services.control_plane.bff"
LOCAL_ROOTS = {
    "action_catalog", "agora", "assistant", "command_executor", "command_queue",
    "downstream_health_monitor", "emergency_containment_policy", "loop_inventory",
    "main", "management_read_models", "models", "openclaw_ops_client",
    "operations_read_model", "paper_eligibility_proof", "persona_allocation_policy",
    "persona_provisioning", "persona_provisioning_coordinator", "ports",
    "source_management_client", "trade_journal", "trade_journey_projection_store",
}
SOURCE_PATHS = sorted(
    str(path.relative_to(ROOT))
    for path in BFF.rglob("*.py")
    if "test" not in path.parts
    and "tests" not in path.parts
    and not path.name.startswith(("test", "smoke"))
    and not path.name.endswith("_test.py")
    and path.name != "conftest.py"
)


def _run_fresh(code: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=ROOT, text=True,
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_declared_sources_use_only_canonical_internal_imports() -> None:
    assert len(SOURCE_PATHS) >= 51
    violations: list[str] = []
    for relative in SOURCE_PATHS:
        tree = ast.parse((ROOT / relative).read_text(), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in LOCAL_ROOTS:
                        violations.append(f"{relative}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in LOCAL_ROOTS:
                    violations.append(f"{relative}:{node.lineno}: from {node.module}")
    assert violations == []


def test_internal_imports_do_not_have_namespace_fallbacks() -> None:
    violations: list[str] = []
    for relative in SOURCE_PATHS:
        tree = ast.parse((ROOT / relative).read_text(), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            canonical_import = any(
                isinstance(child, ast.ImportFrom)
                and (child.module or "").startswith(CANONICAL)
                for child in node.body
            )
            catches_import_error = any(
                isinstance(handler.type, ast.Name)
                and handler.type.id in {"ImportError", "ModuleNotFoundError"}
                for handler in node.handlers
            )
            if canonical_import and catches_import_error:
                violations.append(f"{relative}:{node.lineno}")
    assert violations == []


def test_management_consumers_share_canonical_model_identity() -> None:
    payload = _run_fresh("""
import json, sys
sys.path.insert(0, '.')
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.management_read_models import ranking_router, router, service
print(json.dumps({
 'router': router.ErrorCode is ErrorCode,
 'ranking': ranking_router.ErrorCode is ErrorCode,
 'service': service.ErrorCode is ErrorCode,
}))
""")
    assert payload == {"router": True, "ranking": True, "service": True}


def test_missing_internal_dependency_fails_import_instead_of_substituting_models() -> None:
    payload = _run_fresh("""
import builtins, json, sys
sys.path.insert(0, '.')
real_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'services.control_plane.bff.operations_read_model':
        raise ModuleNotFoundError(name)
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
try:
    __import__('services.control_plane.bff.management_read_models.router')
except ModuleNotFoundError as exc:
    print(json.dumps({'failed_closed': exc.name == 'services.control_plane.bff.operations_read_model'}))
""")
    assert payload == {"failed_closed": True}


def test_diagnostic_entrypoints_have_no_path_surgery() -> None:
    for relative in (
        "contract_snapshots/report_execute_plans_bff_coverage.py",
        "reproduce_sse_gap.py",
    ):
        tree = ast.parse((BFF / relative).read_text())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
            and node.func.value.attr == "path"
        ]
        assert calls == []


def test_cross_user_forbidden_is_403_without_bare_models_alias() -> None:
    payload = _run_fresh("""
import json, sys
sys.path.insert(0, '.')
from fastapi import HTTPException
from services.control_plane.bff.agora.dashboard.router import _raise_cross_user_forbidden
def bff_error(status, code, message, reason, **kwargs):
    return HTTPException(status_code=status, detail={'code': str(code), 'reason': reason})
try:
    _raise_cross_user_forbidden(bff_error=bff_error, resource='recipe', resource_id='other')
except HTTPException as exc:
    print(json.dumps({'status': exc.status_code, 'reason': exc.detail['reason'], 'bare': 'models' in sys.modules}))
""")
    assert payload == {"status": 403, "reason": "CROSS_USER_ACCESS_FORBIDDEN", "bare": False}


def test_representative_delayed_and_worker_imports_in_fresh_process() -> None:
    payload = _run_fresh("""
import importlib, json, sys
sys.path.insert(0, '.')
names = [
 'services.control_plane.bff.agora.interaction.worker',
 'services.control_plane.bff.assistant.tool_contracts',
 'services.control_plane.bff.events.router',
 'services.control_plane.bff.tools_integrations.service',
]
for name in names: importlib.import_module(name)
print(json.dumps({'count': len(names), 'bare_models': 'models' in sys.modules}))
""")
    assert payload == {"count": 4, "bare_models": False}
