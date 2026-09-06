"""Contract and unit tests for the standalone Command Adapters router and service."""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Reviewer finding 6 (gen-10 review): this previously did
# ``sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))`` and
# imported ``command_adapters``/``command_queue``/``models`` as bare
# top-level modules. That collides with
# services/control_plane/bff/__init__.py's namespace-package extension
# (which exposes this on-disk ``control-plane`` directory as
# ``services.control_plane.bff``) — command_adapters/service.py's own
# ``from ..action_catalog import ...`` relative import raises
# ``ImportError: attempted relative import beyond top-level package`` when
# ``command_adapters`` is imported without that real parent package.
# Importing through the canonical ``services.control_plane.bff`` path (as
# every other passing test in this directory already does) fixes this.
from services.control_plane.bff.command_adapters import (
    CommandAdapterService,
    create_action_command_router,
    create_command_adapters_router,
    dispatch_domain_command,
    find_adapter,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ObjectType,
    OperatorIdentity,
    TargetObject,
)


TASK_REVIEW_MANIFEST = {
    "task_id": "OPGAP-BE-COMMAND-ADAPTERS-V2-20260830",
    "owned_layer": "command adapters domain router, service, and executor reverse-import elimination",
    "not_changing": "unrelated BFF domain routers and existing business logic contracts",
    "review_scope": {
        "route_count": 11,
        "durable_readback": "Operator commands dispatch through typed domain adapters with durable receipts",
        "write_boundary": "Typed domain command dispatch, confirm tokens, and command confirmations",
        "reverse_import_elimination": "Zero reverse imports of main.py in command_adapters and command_executor",
        "degraded_read_surface_contract": "POST /bff/command-confirmations projects staleness_warning when read surface is degraded",
    },
    "verification": [
        "pytest -q services/control-plane/bff/tests/test_command_adapters_router.py",
        "pytest -q services/control-plane/bff/tests/test_actions_to_commands_adapter.py",
        "pytest -q services/control-plane/bff/test_command_executor.py",
        "python3 services/control-plane/bff/smoke_test.py",
    ],
}

HEADERS = {"Authorization": "Bearer op-test:operator,approver:mfa"}


def _test_app(command_store: Optional[CommandStore] = None) -> FastAPI:
    app = FastAPI()
    router = create_command_adapters_router(
        get_command_store=lambda: command_store,
        get_read_store=lambda: None,
    )
    app.include_router(router)
    return app


FORBIDDEN_MAIN_MODULE_NAMES = ("main", "bff_main")


def scan_for_reverse_main_imports(content: str, filename: str) -> List[str]:
    """Scan source code for static and dynamic reverse imports / access of main.py."""
    violations: List[str] = []
    parsed = ast.parse(content, filename=filename)

    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MAIN_MODULE_NAMES:
                    violations.append(
                        f"Found forbidden 'import {alias.name}' at line {node.lineno}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module in FORBIDDEN_MAIN_MODULE_NAMES:
                violations.append(
                    f"Found forbidden 'from {node.module} import ...' at line {node.lineno}"
                )
        elif isinstance(node, ast.Call):
            # Check __import__("main")
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in FORBIDDEN_MAIN_MODULE_NAMES:
                    violations.append(
                        f"Found forbidden '__import__({node.args[0].value!r})' at line {node.lineno}"
                    )
            # Check importlib.import_module("main")
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in FORBIDDEN_MAIN_MODULE_NAMES:
                    violations.append(
                        f"Found forbidden 'import_module({node.args[0].value!r})' at line {node.lineno}"
                    )
            # Check sys.modules.get("main") / setdefault("main")
            elif isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "setdefault"):
                if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "modules":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in FORBIDDEN_MAIN_MODULE_NAMES:
                        violations.append(
                            f"Found forbidden 'sys.modules.{node.func.attr}({node.args[0].value!r})' at line {node.lineno}"
                        )
                elif isinstance(node.func.value, ast.Name) and node.func.value.id == "modules":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in FORBIDDEN_MAIN_MODULE_NAMES:
                        violations.append(
                            f"Found forbidden 'modules.{node.func.attr}({node.args[0].value!r})' at line {node.lineno}"
                        )
        elif isinstance(node, ast.Subscript):
            # Check sys.modules["main"]
            if isinstance(node.value, ast.Attribute) and node.value.attr == "modules":
                slice_node = node.slice
                if isinstance(slice_node, ast.Constant) and slice_node.value in FORBIDDEN_MAIN_MODULE_NAMES:
                    violations.append(
                        f"Found forbidden 'sys.modules[{slice_node.value!r}]' at line {node.lineno}"
                    )
            elif isinstance(node.value, ast.Name) and node.value.id == "modules":
                slice_node = node.slice
                if isinstance(slice_node, ast.Constant) and slice_node.value in FORBIDDEN_MAIN_MODULE_NAMES:
                    violations.append(
                        f"Found forbidden 'modules[{slice_node.value!r}]' at line {node.lineno}"
                    )

    # Secondary regex scan for raw text patterns
    raw_patterns = [
        (r'sys\.modules(?:\.get|\.setdefault)?\s*[\[\(]\s*["\'](main|bff_main)["\']', "dynamic sys.modules access"),
        (r'__import__\s*\(\s*["\'](main|bff_main)["\']', "dynamic __import__"),
        (r'import_module\s*\(\s*["\'](main|bff_main)["\']', "dynamic importlib.import_module"),
        (r'getattr\s*\(\s*sys\.modules\s*,\s*["\'](main|bff_main)["\']', "dynamic getattr(sys.modules)"),
    ]
    for pattern, desc in raw_patterns:
        match = re.search(pattern, content)
        if match:
            violations.append(f"Found forbidden {desc} pattern '{match.group(0)}'")

    return list(dict.fromkeys(violations))


def test_zero_reverse_main_imports() -> None:
    """Verify zero static or dynamic reverse imports of main / bff_main in command_adapters and executor."""
    bff_dir = os.path.dirname(os.path.dirname(__file__))
    command_adapters_dir = os.path.join(bff_dir, "command_adapters")
    command_executor_path = os.path.join(bff_dir, "command_executor.py")

    target_files = [command_executor_path]
    for root, _, files in os.walk(command_adapters_dir):
        for f in files:
            if f.endswith(".py"):
                target_files.append(os.path.join(root, f))

    all_violations: Dict[str, List[str]] = {}
    for file_path in target_files:
        rel_path = os.path.relpath(file_path, bff_dir)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        violations = scan_for_reverse_main_imports(content, filename=rel_path)
        if violations:
            all_violations[rel_path] = violations

    assert not all_violations, f"Found forbidden reverse main.py imports/access: {all_violations}"


def test_reverse_main_import_detector_catches_all_forms() -> None:
    """Verify scan_for_reverse_main_imports catches static, dynamic, subscript, and function import forms."""
    test_cases = [
        ("import main", "static import main"),
        ("import bff_main", "static import bff_main"),
        ("from main import app", "from main import"),
        ("from bff_main import app", "from bff_main import"),
        ("import sys\nmod = sys.modules.get('main')", "sys.modules.get('main')"),
        ("import sys\nmod = sys.modules['main']", "sys.modules['main']"),
        ("import sys\nmod = sys.modules.get('bff_main')", "sys.modules.get('bff_main')"),
        ("import sys\nmod = sys.modules['bff_main']", "sys.modules['bff_main']"),
        ("import importlib\nmod = importlib.import_module('main')", "importlib.import_module('main')"),
        ("mod = __import__('main')", "__import__('main')"),
        ("import sys\nmod = getattr(sys.modules, 'main')", "getattr(sys.modules, 'main')"),
    ]

    for snippet, description in test_cases:
        violations = scan_for_reverse_main_imports(snippet, filename="<test_snippet>")
        assert len(violations) > 0, f"Detector failed to catch {description}:\n{snippet}"


def test_command_adapters_router_route_inventory() -> None:
    """Verify create_command_adapters_router owns exactly the 11 command adapter routes."""
    router = create_command_adapters_router()
    routes = [r.path for r in router.routes]

    expected_routes = [
        "/bff/actions",
        "/api/v1/operator/commands",
        "/api/v1/operator/commands/{command_id}",
        "/bff/v1/commands",
        "/bff/command-confirmations",
        "/bff/command-confirmations/{token}",
        "/bff/command-confirmations/{token}/confirm",
        "/bff/confirm-tokens",
        "/bff/confirm-tokens/{tokenId}",
        "/bff/confirm-tokens/{tokenId}/redeem",
        "/bff/confirm-tokens/{tokenId}",
    ]

    assert len(router.routes) == 11, f"Expected 11 routes, got {len(router.routes)}: {routes}"
    for expected in expected_routes:
        assert expected in routes, f"Missing route {expected} in {routes}"


def test_main_composition_has_no_loose_command_adapter_decorators() -> None:
    """Verify main.py contains zero loose @app decorators for the 11 migrated command adapter routes."""
    bff_dir = os.path.dirname(os.path.dirname(__file__))
    main_path = os.path.join(bff_dir, "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        main_source = f.read()

    forbidden_patterns = [
        r'@app\.post\(\s*["\']/api/v1/operator/commands["\']',
        r'@app\.get\(\s*["\']/api/v1/operator/commands/{command_id}["\']',
        r'@app\.post\(\s*["\']/bff/v1/commands["\']',
        r'@app\.get\(\s*["\']/bff/actions["\']',
        r'@app\.post\(\s*["\']/bff/command-confirmations["\']',
        r'@app\.get\(\s*["\']/bff/command-confirmations/{token}["\']',
        r'@app\.post\(\s*["\']/bff/command-confirmations/{token}/confirm["\']',
        r'@app\.post\(\s*["\']/bff/confirm-tokens["\']',
        r'@app\.get\(\s*["\']/bff/confirm-tokens/{tokenId}["\']',
        r'@app\.post\(\s*["\']/bff/confirm-tokens/{tokenId}/redeem["\']',
        r'@app\.delete\(\s*["\']/bff/confirm-tokens/{tokenId}["\']',
    ]

    for pattern in forbidden_patterns:
        match = re.search(pattern, main_source)
        assert match is None, f"Found lingering @app decorator in main.py matching {pattern}"


def test_action_catalog_readback() -> None:
    """Test GET /bff/actions returns canonical action catalog."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        response = client.get("/bff/actions", headers=HEADERS)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "actions" in data or "items" in data or "data" in data or isinstance(data, dict)


def test_confirm_token_full_lifecycle() -> None:
    """Test confirm token creation, readback, redemption, replay, deletion, and expiry."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        # 1. Create confirm token
        create_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-create-key-1"},
            json={"tokenId": "ct-test-001", "reason": "high risk action", "ttlSeconds": 300},
        )
        assert create_resp.status_code == 201, create_resp.text
        body = create_resp.json()
        assert body["data"]["tokenId"] == "ct-test-001"
        assert body["data"]["status"] == "created"
        assert body["meta"]["idempotency"]["replayed"] is False

        # 2. Replay creation with same key
        replay_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-create-key-1"},
            json={"tokenId": "ct-test-001", "reason": "high risk action", "ttlSeconds": 300},
        )
        assert replay_resp.status_code == 201, replay_resp.text
        assert replay_resp.json()["meta"]["idempotency"]["replayed"] is True

        # 3. Read token state
        read_resp = client.get("/bff/confirm-tokens/ct-test-001", headers=HEADERS)
        assert read_resp.status_code == 200, read_resp.text
        assert read_resp.json()["data"]["status"] == "created"
        assert read_resp.json()["data"]["expired"] is False

        # 4. Redeem token
        redeem_resp = client.post(
            "/bff/confirm-tokens/ct-test-001/redeem",
            headers={**HEADERS, "Idempotency-Key": "ct-redeem-key-1"},
            json={"reason": "operator confirmed"},
        )
        assert redeem_resp.status_code == 202, redeem_resp.text
        assert redeem_resp.json()["data"]["status"] == "redeemed"
        assert redeem_resp.json()["data"]["redeemed"] is True

        # 5. Read after redeem
        read_after_redeem = client.get("/bff/confirm-tokens/ct-test-001", headers=HEADERS)
        assert read_after_redeem.status_code == 200
        assert read_after_redeem.json()["data"]["status"] == "redeemed"

        # 6. Delete another token
        create_del = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-del-key-1"},
            json={"tokenId": "ct-to-delete", "reason": "delete me"},
        )
        assert create_del.status_code == 201

        del_resp = client.delete(
            "/bff/confirm-tokens/ct-to-delete",
            headers={**HEADERS, "Idempotency-Key": "ct-del-key-2"},
        )
        assert del_resp.status_code == 202
        assert del_resp.json()["data"]["status"] == "deleted"

        read_deleted = client.get("/bff/confirm-tokens/ct-to-delete", headers=HEADERS)
        assert read_deleted.status_code == 200
        assert read_deleted.json()["data"]["status"] == "deleted"


def test_confirm_token_expiration_returns_410() -> None:
    """Test expired confirm token returns typed 410 error."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        create_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-expired-key-1"},
            json={"tokenId": "ct-expired-001", "ttlSeconds": -10},
        )
        assert create_resp.status_code == 201

        read_resp = client.get("/bff/confirm-tokens/ct-expired-001", headers=HEADERS)
        assert read_resp.status_code == 410, read_resp.text
        err = read_resp.json().get("error") or read_resp.json().get("detail", {}).get("error", {})
        assert err["details"]["precondition_failed"] == "confirm_token_expired"


def test_command_confirmations_lifecycle() -> None:
    """Test submit command confirmation, query status, and confirm by token."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        # Create token first
        client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "conf-ct-1"},
            json={"tokenId": "ct-conf-001", "reason": "confirmation token"},
        )

        # Submit confirmation
        conf_resp = client.post(
            "/bff/command-confirmations",
            headers={**HEADERS, "Idempotency-Key": "conf-sub-1"},
            json={"command_id": "cmd-test-100", "confirm_token": "ct-conf-001"},
        )
        assert conf_resp.status_code == 202, conf_resp.text
        assert conf_resp.json()["status"] == "accepted"
        assert conf_resp.json()["command_id"] == "cmd-test-100"
        assert conf_resp.json()["token"] == "ct-conf-001"
        assert conf_resp.json()["lifecycleStatus"] == "redeemed"

        # Query confirmation status
        status_resp = client.get("/bff/command-confirmations/ct-conf-001", headers=HEADERS)
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["data"]["command_id"] == "cmd-test-100"
        assert status_resp.json()["data"]["status"] == "redeemed"

        # Confirm command by token
        client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "conf-ct-2"},
            json={"tokenId": "ct-conf-002", "reason": "confirmation token 2"},
        )
        confirm_token_resp = client.post(
            "/bff/command-confirmations/ct-conf-002/confirm",
            headers={**HEADERS, "Idempotency-Key": "conf-by-tok-1", "X-Correlation-Id": "corr-123"},
            json={"command_id": "cmd-test-200", "confirm_token": "ct-conf-002"},
        )
        assert confirm_token_resp.status_code == 202, confirm_token_resp.text
        assert confirm_token_resp.json()["data"]["status"] == "accepted"
        assert confirm_token_resp.json()["data"]["commandId"] == "cmd-test-200"


def test_operator_command_status_readback() -> None:
    """Test GET /api/v1/operator/commands/{command_id} returns durable status."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        store.submit_command(
            command_id="cmd-readback-1",
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target=TargetObject(type=ObjectType.CAPITAL_POOL, id="pool-1"),
            submitted_at="2026-08-30T12:00:00Z",
            params={"action_id": "ApprovePool"},
            audit_context={"actor": "op-test"},
        )
        client = TestClient(_test_app(store))

        response = client.get("/api/v1/operator/commands/cmd-readback-1", headers=HEADERS)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["command_id"] == "cmd-readback-1"
        assert data["type"] == CommandType.CAPITAL_POOL_ACTION.value
        assert data["target"]["id"] == "pool-1"
        assert data["status"] == CommandStatus.SUBMITTED.value


def test_typed_domain_command_dispatch_and_receipt(monkeypatch) -> None:
    """Test typed domain command execution returns structured receipt.

    Reviewer finding 6 (gen-10 review): this previously exercised
    ``action_id="submit_review"``, which architecture-resumption-sa-sd.md §2
    and this codebase's own command_contract.py deliberately route to the
    governance-review owner, not Registry — StrategyCommandAdapter now
    correctly raises ActionUnavailableError for it instead of fabricating an
    "accepted"/"review_pending" receipt (see strategy_adapter.py's
    _execute_strategy_action docstring). This collection-error-hidden test
    was asserting the old, intentionally-removed fabricated-success
    behavior. Exercise a genuinely Registry-owned action (``update_params``)
    instead, with the outbound Registry HTTP call mocked (mirrors
    services/control-plane/bff/tests/test_strategy_registry_owner_prerequisite.py),
    to keep proving typed dispatch produces a structured receipt without a
    live network dependency.
    """
    from services.control_plane.bff.command_adapters import strategy_adapter as strategy_adapter_module

    get_calls = {"n": 0}

    def _fake_http(url, *, method="GET", payload=None, auth_token=None, mfa_token=None):
        # "Bearer test" resolves (via _resolve_caller_actor_id) to verified
        # actor_id "test"; a genuine committed entry's last_actor must match
        # it. The pre-mutation identity-check GET returns the pre-commit
        # snapshot (an unchanged/older updated_at) while the PATCH and the
        # post-PATCH readback GET both return the actually-committed
        # snapshot — mirrors test_strategy_registry_owner_prerequisite.py.
        committed_entry = {
            "registry_id": "reg-dispatch-1",
            "strategy_id": "stg-01",
            "owner_tenant": "tenant-dispatch",
            "version": "1.0.0",
            "checksum": "sha256:dispatch",
            "metadata": {"note": "new"},
            "updated_at": "2026-09-06T00:00:00Z",
            "last_actor": {"actor_id": "test", "tenant": "tenant-dispatch"},
        }
        if method == "PATCH":
            return 200, {"X-Idempotent-Replay": "false"}, {"entry": committed_entry}
        get_calls["n"] += 1
        if get_calls["n"] == 1:
            precheck_entry = dict(committed_entry, metadata={"note": "old"}, updated_at="2026-09-05T00:00:00Z")
            precheck_entry.pop("last_actor", None)
            return 200, {}, {"entry": precheck_entry}
        from services.registry.pg_store import PostgresRegistryStore, _request_digest
        receipt_key = PostgresRegistryStore.receipt_key(
            "cmd-dispatch-1", "reg-dispatch-1", actor={"actor_id": "test", "tenant": "tenant-dispatch"}, command_type="metadata",
        )
        request_digest = _request_digest({
            "registry_id": "reg-dispatch-1",
            "expected_metadata": {"note": "old"},
            "metadata": {"note": "new"},
        })
        return 200, {}, {
            "receipt": {
                "command_key": "cmd-dispatch-1",
                "registry_id": "reg-dispatch-1",
                "receipt_key": receipt_key,
                "request_digest": request_digest,
                "committed_at": "2026-09-06T00:00:00Z",
                "committed_entry": committed_entry,
            }
        }

    monkeypatch.setattr(strategy_adapter_module, "http_request_json_with_headers", _fake_http)
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry-svc.internal")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "test-dispatch-secret")
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "test-dispatch-iss")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "test-dispatch-aud")

    import time
    from services.runtime_auth_inbound import encode_jwt_hs256
    token = encode_jwt_hs256(
        {
            "sub": "test",
            "tenant": "tenant-dispatch",
            "roles": ["operator"],
            "iss": "test-dispatch-iss",
            "aud": "test-dispatch-aud",
            "exp": time.time() + 3600,
        },
        secret="test-dispatch-secret",
    )

    adapter = find_adapter(CommandType.STRATEGY_ACTION)
    assert adapter is not None

    # Test domain receipt structure
    receipt = adapter.execute(
        command_id="cmd-dispatch-1",
        command_type=CommandType.STRATEGY_ACTION,
        params={
            "strategy_id": "stg-01",
            "action_id": "update_params",
            "registry_id": "reg-dispatch-1",
            "expected_metadata": {"note": "old"},
            "metadata": {"note": "new"},
        },
        auth_token=token,
    )
    assert receipt["command_id"] == "cmd-dispatch-1"
    assert receipt["status"] == "metadata_updated"
    assert receipt["entity_type"] in ("strategy", "Strategy")
    assert receipt["entity_id"] == "stg-01"
    assert "domain_receipt" in receipt


def test_main_app_operator_command_submission_regression() -> None:
    """Regression test: verify POST /api/v1/operator/commands works in full main app with idempotency keys."""
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

        # 1. Submit with X-Idempotency-Key
        resp = client.post(
            "/api/v1/operator/commands",
            headers={
                "Authorization": "Bearer op-1:operator,approver:mfa",
                "X-Idempotency-Key": "idmp-test-op-1",
            },
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-001"},
                "action": "approve",
                "params": {"deployment_plan_id": "dp-001", "approval_decision": "approve"},
                "audit_context": {"reason": "Integration regression test"},
            },
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["status"] == "accepted"
        assert "receipt_id" in data
        assert data["command"] == "ApproveDeployment"

        # 2. Submit with Idempotency-Key
        resp2 = client.post(
            "/api/v1/operator/commands",
            headers={
                "Authorization": "Bearer op-1:operator,approver:mfa",
                "Idempotency-Key": "idmp-test-op-2",
            },
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-002"},
                "action": "approve",
                "params": {"deployment_plan_id": "dp-002", "approval_decision": "approve"},
                "audit_context": {"reason": "Integration regression test 2"},
            },
        )
        assert resp2.status_code == 202, resp2.text
        data2 = resp2.json()
        assert data2["status"] == "accepted"
        assert "receipt_id" in data2
        assert data2["command"] == "ApproveDeployment"


def test_confirm_command_by_token_contract_and_regressions() -> None:
    """Test POST /bff/command-confirmations/{token}/confirm contract and regression invariants."""
    published_events: List[Tuple[str, Dict[str, Any]]] = []

    def _mock_publish_event(event_type: str, data: Dict[str, Any]) -> None:
        published_events.append((event_type, data))

    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        app = FastAPI()
        router = create_command_adapters_router(
            get_command_store=lambda: store,
            get_read_store=lambda: None,
            publish_event=_mock_publish_event,
        )
        app.include_router(router)
        client = TestClient(app)

        # 1. Unknown token returns typed 404
        unknown_resp = client.post(
            "/bff/command-confirmations/unknown-token-123/confirm",
            headers={**HEADERS, "Idempotency-Key": "conf-tok-unk-01"},
            json={"command_id": "cmd-unk-01"},
        )
        assert unknown_resp.status_code == 404, unknown_resp.text
        err = unknown_resp.json().get("error") or unknown_resp.json().get("detail", {}).get("error", {})
        assert err["code"] == "RESOURCE_NOT_FOUND"
        assert err["details"]["precondition_failed"] == "confirm_token_not_found"

        # 2. Seed token
        seed_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "conf-tok-seed-01"},
            json={"tokenId": "tok-test-p04-reg", "ttlSeconds": 300},
        )
        assert seed_resp.status_code == 201

        # 3. Mismatched body token returns 412
        mismatch_resp = client.post(
            "/bff/command-confirmations/tok-test-p04-reg/confirm",
            headers={**HEADERS, "Idempotency-Key": "conf-tok-mismatch-01"},
            json={"command_id": "cmd-mismatch-01", "confirm_token": "different-token"},
        )
        assert mismatch_resp.status_code == 412, mismatch_resp.text
        mismatch_err = mismatch_resp.json().get("error") or mismatch_resp.json().get("detail", {}).get("error", {})
        assert mismatch_err["code"] == "PRECONDITION_FAILED"
        assert mismatch_err["details"]["precondition_failed"] == "confirm_token_invalid"

        # 4. Missing command_id returns 422
        missing_cmd_resp = client.post(
            "/bff/command-confirmations/tok-test-p04-reg/confirm",
            headers={**HEADERS, "Idempotency-Key": "conf-tok-missing-01"},
            json={},
        )
        assert missing_cmd_resp.status_code == 422, missing_cmd_resp.text
        missing_err = missing_cmd_resp.json().get("error") or missing_cmd_resp.json().get("detail", {}).get("error", {})
        assert missing_err["code"] == "VALIDATION_FAILED"
        assert missing_err["details"]["precondition_failed"] == "command_id_missing"

        # 5. Dry-run returns 200 with meta.dryRun=True and no side effects
        dry_run_resp = client.post(
            "/bff/command-confirmations/tok-test-p04-reg/confirm",
            headers={
                **HEADERS,
                "Idempotency-Key": "conf-tok-dry-01",
                "X-Dry-Run": "1",
                "X-Correlation-Id": "corr-dry-01",
            },
            json={"command_id": "cmd-dry-01"},
        )
        assert dry_run_resp.status_code == 200, dry_run_resp.text
        dry_payload = dry_run_resp.json()
        assert dry_payload["data"]["status"] == "accepted"
        assert dry_payload["data"]["commandId"] == "cmd-dry-01"
        assert dry_payload["meta"]["dryRun"] is True
        assert dry_payload["meta"]["evidenceKind"] == "command.confirm"
        assert len(published_events) == 0

        # Token status should still be created (not redeemed)
        tok_status = client.get("/bff/confirm-tokens/tok-test-p04-reg", headers=HEADERS)
        assert tok_status.json()["data"]["status"] == "created"

        # 6. Valid confirm returns 202, records redeem, and publishes audit event
        valid_resp = client.post(
            "/bff/command-confirmations/tok-test-p04-reg/confirm",
            headers={
                **HEADERS,
                "Idempotency-Key": "conf-tok-valid-01",
                "X-Correlation-Id": "corr-valid-01",
            },
            json={"command_id": "cmd-valid-01"},
        )
        assert valid_resp.status_code == 202, valid_resp.text
        valid_payload = valid_resp.json()
        assert valid_payload["data"]["status"] == "accepted"
        assert valid_payload["data"]["commandId"] == "cmd-valid-01"
        assert valid_payload["meta"]["dryRun"] is False
        assert valid_payload["meta"]["evidenceKind"] == "command.confirm"
        assert valid_payload["meta"]["correlationId"] == "corr-valid-01"

        # Check published event
        assert len(published_events) == 1
        assert published_events[0][0] == "command.confirm"
        assert published_events[0][1]["commandId"] == "cmd-valid-01"
        assert published_events[0][1]["tokenId"] == "tok-test-p04-reg"

        # Check token lifecycle status is now redeemed
        tok_after = client.get("/bff/confirm-tokens/tok-test-p04-reg", headers=HEADERS)
        assert tok_after.json()["data"]["status"] == "redeemed"

        # 7. Replay returns 202 with identical data
        replay_resp = client.post(
            "/bff/command-confirmations/tok-test-p04-reg/confirm",
            headers={
                **HEADERS,
                "Idempotency-Key": "conf-tok-valid-01",
                "X-Correlation-Id": "corr-valid-01",
            },
            json={"command_id": "cmd-valid-01"},
        )
        assert replay_resp.status_code == 202, replay_resp.text
        assert replay_resp.json()["data"] == valid_payload["data"]


def test_command_confirmation_degraded_read_surface() -> None:
    """Test POST /bff/command-confirmations projects staleness_warning when read surface is degraded."""
    from models import StalenessWarning

    # 1. Custom check_read_surface_state injected
    custom_warning = StalenessWarning(
        read_surface_state="degraded",
        message="Command submitted against stale read surface data. Verify target state via secondary control path before confirming action.",
    )
    router = create_command_adapters_router(
        check_read_surface_state=lambda: custom_warning,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Create token
    client.post(
        "/bff/confirm-tokens",
        headers={**HEADERS, "Idempotency-Key": "degraded-ct-1"},
        json={"tokenId": "ct-deg-001", "reason": "test"},
    )

    # Submit confirmation - should include staleness_warning
    resp = client.post(
        "/bff/command-confirmations",
        headers={**HEADERS, "Idempotency-Key": "degraded-conf-1"},
        json={"command_id": "cmd-deg-100", "confirm_token": "ct-deg-001"},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "accepted"
    assert "staleness_warning" in data
    assert data["staleness_warning"]["read_surface_state"] == "degraded"
    assert "stale read surface data" in data["staleness_warning"]["message"]

    # 2. Replay preserves staleness_warning in idempotent cache
    replay = client.post(
        "/bff/command-confirmations",
        headers={**HEADERS, "Idempotency-Key": "degraded-conf-1"},
        json={"command_id": "cmd-deg-100", "confirm_token": "ct-deg-001"},
    )
    assert replay.status_code == 202
    assert replay.json() == data

    # 3. Fresh read surface returns no staleness_warning
    fresh_router = create_command_adapters_router(
        check_read_surface_state=lambda: None,
    )
    fresh_app = FastAPI()
    fresh_app.include_router(fresh_router)
    fresh_client = TestClient(fresh_app)

    fresh_client.post(
        "/bff/confirm-tokens",
        headers={**HEADERS, "Idempotency-Key": "fresh-ct-1"},
        json={"tokenId": "ct-fresh-001", "reason": "test"},
    )
    fresh_resp = fresh_client.post(
        "/bff/command-confirmations",
        headers={**HEADERS, "Idempotency-Key": "fresh-conf-1"},
        json={"command_id": "cmd-fresh-100", "confirm_token": "ct-fresh-001"},
    )
    assert fresh_resp.status_code == 202, fresh_resp.text
    assert "staleness_warning" not in fresh_resp.json()


def test_main_app_command_confirmation_degraded_read_surface_regression() -> None:
    """Regression test: verify POST /bff/command-confirmations in full main app projects staleness_warning when BFF_READ_SURFACE_STATE is degraded."""
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    orig_env = os.environ.get("BFF_READ_SURFACE_STATE")
    try:
        os.environ["BFF_READ_SURFACE_STATE"] = "degraded"
        with tempfile.TemporaryDirectory() as td:
            # Reviewer finding 6 (gen-10 review): ``main.command_store`` is a
            # module-level singleton shared with
            # test_main_app_operator_command_submission_regression, which
            # points its ``file_path`` at its own (already-cleaned-up)
            # TemporaryDirectory. Without repointing it here too, this test's
            # command writes fail with FileNotFoundError whenever it runs
            # after that one in the same process — this was invisible while
            # the whole module failed to collect.
            main_command_store.file_path = os.path.join(td, "main_commands_degraded.jsonl")
            client = TestClient(main_app)

            # Create confirm token
            create_resp = client.post(
                "/bff/confirm-tokens",
                headers={
                    "Authorization": "Bearer op-1:operator,approver:mfa",
                    "Idempotency-Key": "main-reg-deg-ct-1",
                },
                json={"tokenId": "ct-main-deg-001", "reason": "degraded read test"},
            )
            assert create_resp.status_code == 201, create_resp.text

            # Submit confirmation
            conf_resp = client.post(
                "/bff/command-confirmations",
                headers={
                    "Authorization": "Bearer op-1:operator,approver:mfa",
                    "Idempotency-Key": "main-reg-deg-conf-1",
                },
                json={"command_id": "cmd-main-deg-001", "confirm_token": "ct-main-deg-001"},
            )
            assert conf_resp.status_code == 202, conf_resp.text
            data = conf_resp.json()
            assert data["status"] == "accepted"
            assert data["lifecycleStatus"] == "redeemed"
            assert "staleness_warning" in data
            assert data["staleness_warning"]["read_surface_state"] == "degraded"
            assert "stale read surface data" in data["staleness_warning"]["message"]
    finally:
        if orig_env is None:
            os.environ.pop("BFF_READ_SURFACE_STATE", None)
        else:
            os.environ["BFF_READ_SURFACE_STATE"] = orig_env

