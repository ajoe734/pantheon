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
SOURCE_PATHS = (
    "services/control-plane/bff/agora/candidate_decisions/router.py",
    "services/control-plane/bff/agora/dashboard/router.py",
    "services/control-plane/bff/agora/dataset_extraction/router.py",
    "services/control-plane/bff/agora/identity/router.py",
    "services/control-plane/bff/agora/interaction/persona_client.py",
    "services/control-plane/bff/agora/interaction/router.py",
    "services/control-plane/bff/agora/interaction/runner.py",
    "services/control-plane/bff/agora/interaction/worker.py",
    "services/control-plane/bff/agora/research/router.py",
    "services/control-plane/bff/agora/router.py",
    "services/control-plane/bff/agora/servant/router.py",
    "services/control-plane/bff/agora/service.py",
    "services/control-plane/bff/agora/strategy_workshop/_admission.py",
    "services/control-plane/bff/agora/strategy_workshop/_common.py",
    "services/control-plane/bff/agora/strategy_workshop/routes/execution.py",
    "services/control-plane/bff/agora/strategy_workshop/routes/session.py",
    "services/control-plane/bff/agora/strategy_workshop/routes/stream.py",
    "services/control-plane/bff/agora/strategy_workshop/routes/versions.py",
    "services/control-plane/bff/agora/trading_room/router.py",
    "services/control-plane/bff/assistant/tool_contracts.py",
    "services/control-plane/bff/capital/router.py",
    "services/control-plane/bff/command_adapters/base.py",
    "services/control-plane/bff/command_executor.py",
    "services/control-plane/bff/console_gap/consult_rules.py",
    "services/control-plane/bff/console_gap/datasources.py",
    "services/control-plane/bff/console_gap/memory_governance.py",
    "services/control-plane/bff/console_gap/permissions.py",
    "services/control-plane/bff/console_gap/route_policies.py",
    "services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py",
    "services/control-plane/bff/control_loops/router.py",
    "services/control-plane/bff/control_loops/service.py",
    "services/control-plane/bff/deployment/service.py",
    "services/control-plane/bff/events/router.py",
    "services/control-plane/bff/evolution/router.py",
    "services/control-plane/bff/incidents/router.py",
    "services/control-plane/bff/incidents/service.py",
    "services/control-plane/bff/main.py",
    "services/control-plane/bff/management_read_models/ranking_router.py",
    "services/control-plane/bff/management_read_models/router.py",
    "services/control-plane/bff/management_read_models/service.py",
    "services/control-plane/bff/personas/reconciliation.py",
    "services/control-plane/bff/personas/service.py",
    "services/control-plane/bff/ports/__init__.py",
    "services/control-plane/bff/ports/lifecycle_telemetry_governance.py",
    "services/control-plane/bff/ports/operations_consultation.py",
    "services/control-plane/bff/ports/read_surface_ports.py",
    "services/control-plane/bff/postmortems/router.py",
    "services/control-plane/bff/reproduce_sse_gap.py",
    "services/control-plane/bff/strategies/router.py",
    "services/control-plane/bff/tools_integrations/router.py",
    "services/control-plane/bff/tools_integrations/service.py",
)


def _run_fresh(code: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=ROOT, text=True,
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_declared_sources_use_only_canonical_internal_imports() -> None:
    assert len(SOURCE_PATHS) == 51
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
import json
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
import builtins, json
real_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'services.control_plane.bff.operations_read_model':
        raise ModuleNotFoundError(name, name=name)
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
        canonical_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert any(module.startswith(CANONICAL) for module in canonical_imports)
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
from fastapi import HTTPException
from services.control_plane.bff.agora.dashboard.router import _raise_cross_user_forbidden
def bff_error(status, code, message, reason, **kwargs):
    return HTTPException(status_code=status, detail={'code': code.value, 'reason': reason})
try:
    _raise_cross_user_forbidden(bff_error=bff_error, resource='recipe', resource_id='other')
except HTTPException as exc:
    print(json.dumps({'status': exc.status_code, 'code': exc.detail['code'], 'reason': exc.detail['reason'], 'bare': 'models' in sys.modules}))
""")
    assert payload == {"status": 403, "code": "FORBIDDEN", "reason": "CROSS_USER_ACCESS_FORBIDDEN", "bare": False}


def test_representative_delayed_and_worker_imports_in_fresh_process() -> None:
    payload = _run_fresh("""
import importlib, json, sys
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


def test_representative_negative_branches_keep_exact_contracts() -> None:
    payload = _run_fresh("""
import asyncio, json
from types import SimpleNamespace
from fastapi import HTTPException
from services.control_plane.bff.agora.candidate_decisions.router import create_candidate_decision_router
from services.control_plane.bff.agora.dashboard.router import create_dashboard_router
from services.control_plane.bff.assistant.routes import create_assistant_router
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

def bff_error(status, code, message, reason=None, **kwargs):
    return HTTPException(status_code=status, detail={'code': code.value, 'reason': reason})
def endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)
identity = SimpleNamespace(operator_id='viewer', roles=['viewer'], claims={
    'tenant_id': 'tenant-a', 'user_id': 'viewer', 'capabilities': []})
candidate = create_candidate_decision_router(
    service=object(), extract_identity=lambda _: identity,
    require_read_role=lambda _: None, require_write_role=lambda _: None,
    bff_error=bff_error, utc_now=lambda: '2026-09-05T00:00:00Z')
dashboard = create_dashboard_router(
    extract_identity=lambda _: identity, require_read_role=lambda _: None,
    bff_error=bff_error, utc_now=lambda: '2026-09-05T00:00:00Z')
assistant = create_assistant_router(
    build_context_pack=lambda *args: None, extract_identity=lambda _: identity,
    require_read_role=lambda _: None, bff_error=bff_error)
results = {}
try:
    endpoint(candidate, '/bff/agora/proposals/{proposal_id}/candidate')('p1', SimpleNamespace(headers={}), None, None)
except HTTPException as exc:
    results['capability'] = [exc.status_code, exc.detail['code'], exc.detail['reason']]
try:
    endpoint(dashboard, '/bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals')('s1', {}, None, None)
except HTTPException as exc:
    results['validation'] = [exc.status_code, exc.detail['code'], exc.detail['reason']]
try:
    asyncio.run(endpoint(assistant, '/bff/assistant/providers')(False, None))
except HTTPException as exc:
    results['assistant_disabled'] = [exc.status_code, exc.detail['code'], exc.detail['reason']]
try:
    OpenClawOpsClient(base_url='').list_assistant_providers()
except OpenClawOpsClientError as exc:
    results['provider_unavailable'] = [exc.status_code, exc.error_code]
print(json.dumps(results, sort_keys=True))
""")
    assert payload == {
        "assistant_disabled": [503, "PRECONDITION_FAILED", "OpenClaw adapter provider readiness is not configured for this BFF."],
        "capability": [403, "FORBIDDEN", "capability_missing"],
        "provider_unavailable": [503, "OPENCLAW_ADAPTER_URL_NOT_CONFIGURED"],
        "validation": [400, "VALIDATION_FAILED", "missing_field"],
    }
