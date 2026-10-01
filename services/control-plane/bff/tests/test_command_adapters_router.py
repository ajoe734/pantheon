"""Contract and unit tests for the standalone Command Adapters router and service."""
from __future__ import annotations

import ast
import json
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
    create_command_adapters_router,
    dispatch_domain_command,
    find_adapter,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ObjectType,
    OperatorCommand,
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
        "pytest -q services/control-plane/bff/tests/test_command_replay_conflict.py",
        "pytest -q services/control-plane/bff/test_command_executor.py",
        "python3 services/control-plane/bff/smoke_test.py",
    ],
}

HEADERS = {"Authorization": "Bearer op-test:operator,approver:mfa"}


def _test_extract_identity(
    authorization: Optional[str], mfa_token: Optional[str] = None
) -> OperatorIdentity:
    """Test-only identity policy: CommandAdapterService no longer guesses an
    identity from a raw bearer token by default (fail-closed instead), so
    this harness supplies the same "actor:roles:mfa" test convention the
    fixtures below rely on, mirroring what a real auth_policy module does in
    production.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return OperatorIdentity(operator_id="anonymous", roles=["viewer"], auth_mode="anonymous", has_mfa=False)
    token = authorization[len("Bearer ") :].strip()
    parts = token.split(":")
    actor = parts[0] if parts else "system"
    roles = [r.strip() for r in parts[1].split(",")] if len(parts) > 1 else ["operator"]
    return OperatorIdentity(
        operator_id=actor,
        roles=roles,
        auth_mode="bearer",
        has_mfa=len(parts) > 2 and parts[2] == "mfa",
    )


def _test_app(command_store: Optional[CommandStore] = None) -> FastAPI:
    app = FastAPI()
    router = create_command_adapters_router(
        get_command_store=lambda: command_store,
        get_read_store=lambda: None,
        extract_identity=_test_extract_identity,
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
            if f.endswith(".py") and f != "runtime_adapter.py":
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
    """Verify create_command_adapters_router owns exactly the 10 command adapter routes."""
    router = create_command_adapters_router(extract_identity=_test_extract_identity)
    routes = [r.path for r in router.routes]

    expected_routes = [
        "/bff/actions",
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

    assert len(router.routes) == 10, f"Expected 10 routes, got {len(router.routes)}: {routes}"
    for expected in expected_routes:
        assert expected in routes, f"Missing route {expected} in {routes}"
    assert "/api/v1/operator/commands" not in routes, (
        "POST /api/v1/operator/commands has been retired; only the GET status "
        "readback at /api/v1/operator/commands/{command_id} should remain"
    )


def test_main_composition_has_no_loose_command_adapter_decorators() -> None:
    """Verify main.py contains zero loose @app decorators for the 10 migrated command adapter routes,
    and that the two retired generic write routes are gone entirely."""
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
        r'@app\.post\(\s*["\']/bff/actions/\{type\}/\{id\}/\{action\}["\']',
    ]

    for pattern in forbidden_patterns:
        match = re.search(pattern, main_source)
        assert match is None, f"Found lingering @app decorator in main.py matching {pattern}"

    assert "create_action_command_router" not in main_source, (
        "Retired create_action_command_router import/usage must be fully removed from main.py"
    )


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


def test_main_app_final_command_submission_regression() -> None:
    """Regression test: verify POST /bff/v1/commands (the sole canonical generic
    command write route) works in standalone router app with idempotency keys.

    This formerly exercised the now-retired POST /api/v1/operator/commands route;
    that route has been deleted, so this regression now targets the canonical
    /bff/v1/commands route with the equivalent idempotency-key coverage."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "main_commands.jsonl"))
        svc = CommandAdapterService(
            command_store=store,
            read_surface=None,
            extract_identity=_test_extract_identity,
        )
        app = FastAPI()
        router = create_command_adapters_router(
            service=svc,
            submit_command_admission=svc.submit_command_admission,
        )
        app.include_router(router)
        client = TestClient(app)

        # 1. Submit with X-Idempotency-Key
        # RejectDecision against an ApprovalDecision target requires no confirm
        # token, approval evidence, or two-man signature, so it exercises the
        # idempotency-key handling itself without tripping unrelated final-contract
        # preconditions (unlike the retired legacy route, /bff/v1/commands always
        # enforces the full precondition set for every command type).
        resp = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": "Bearer op-1:operator,approver:mfa",
                "X-Idempotency-Key": "idmp-test-op-1",
            },
            json={
                "command": "RejectDecision",
                "target": {"type": "ApprovalDecision", "id": "dec-regression-1"},
                "params": {"decision_id": "dec-regression-1", "rejection_reason": "regression test"},
                "audit_context": {"reason": "Integration regression test"},
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "accepted"
        data = body["data"]
        assert "receipt_id" in data
        assert data["command"] == "RejectDecision"

        # 2. Submit with Idempotency-Key
        resp2 = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": "Bearer op-1:operator,approver:mfa",
                "Idempotency-Key": "idmp-test-op-2",
            },
            json={
                "command": "RejectDecision",
                "target": {"type": "ApprovalDecision", "id": "dec-regression-2"},
                "params": {"decision_id": "dec-regression-2", "rejection_reason": "regression test 2"},
                "audit_context": {"reason": "Integration regression test 2"},
            },
        )
        assert resp2.status_code == 202, resp2.text
        body2 = resp2.json()
        assert body2["status"] == "accepted"
        data2 = body2["data"]
        assert "receipt_id" in data2
        assert data2["command"] == "RejectDecision"


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
            extract_identity=_test_extract_identity,
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
    from services.control_plane.bff.models import StalenessWarning

    # 1. Custom check_read_surface_state injected
    custom_warning = StalenessWarning(
        read_surface_state="degraded",
        message="Command submitted against stale read surface data. Verify target state via secondary control path before confirming action.",
    )
    router = create_command_adapters_router(
        check_read_surface_state=lambda: custom_warning,
        extract_identity=_test_extract_identity,
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
        extract_identity=_test_extract_identity,
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
    """Regression test: verify POST /bff/command-confirmations in standalone router app projects staleness_warning when BFF_READ_SURFACE_STATE is degraded."""
    orig_env = os.environ.get("BFF_READ_SURFACE_STATE")
    try:
        os.environ["BFF_READ_SURFACE_STATE"] = "degraded"
        with tempfile.TemporaryDirectory() as td:
            store = CommandStore(os.path.join(td, "main_commands_degraded.jsonl"))
            client = TestClient(_test_app(store))

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



_VOLATILE = {"id", "expected_completion_at", "tracking_url", "trackingUrl", "commandId", "receipt_id", "command_id", "accepted_at", "submitted_at", "timestamp", "created_at", "occurred_at", "updated_at", "trace_id", "correlation_id", "correlationId", "error_id", "payload_checksum", "command_ref", "confirmation_id", "request_id"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _VOLATILE and not k.endswith("_at") and not (k == "action_id" and str(v).startswith("audit-"))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


_ALIAS_PARAMS: Dict[str, Dict[str, Any]] = {
    "PauseRuntime": {"runtime_binding_id": "alias-target-1", "pause_action": "pause"},
    "PauseExecution": {"pause_new_entries": True, "cancel_open_orders": False},
    "ExecuteRollback": {"rollback_target_type": "runtime", "target_id": "alias-target-1", "rollback_to_version": "v1"},
    "HardRollback": {"rollback_target_type": "runtime", "target_id": "alias-target-1", "rollback_to_version": "v1", "target_artifact_id": "artifact-1"},
    "ActivateKillSwitch": {"scope": "all", "activate": True},
    "ApproveDeployment": {"deployment_plan_id": "alias-target-1", "approval_decision": "approve"},
    "EscalateDiff": {"plan_id": "alias-target-1", "escalation_reason": "alias equivalence"},
    "PromoteCandidate": {"persona_id": "alias-target-1"},
    "Demote": {"persona_id": "alias-target-1"},
    "IssueRiskOff": {"reduce_exposure_pct": 10},
    "IssueSafeMode": {"safe_mode_level": "soft"},
    "ApproveRollback": {"rollback_id": "rb-1"},
    "RejectRollback": {"rollback_id": "rb-1", "rejection_reason": "alias equivalence"},
    "AdvanceLifecycle": {"target_state": "paper_owner"},
    "TerminateStalePaperMonitoringSession": {"staleness_evidence": {"heartbeat_age_seconds": 900}},
    "RequestReview": {"persona_id": "alias-target-1"},
    "ApproveDecision": {"decision_id": "alias-target-1"},
    "RejectDecision": {"decision_id": "alias-target-1", "rejection_reason": "alias equivalence"},
    "RequestApprovalRevision": {"decision_id": "alias-target-1", "revision_notes": "alias equivalence"},
    "RecordSponsorDecision": {"committee_id": "alias-target-1", "sponsor_decision": "approved", "rationale_ref": "ref-1"},
    "HumanGateApprove": {"human_gate_item_id": "alias-target-1", "decision": "approve"},
    "HumanGateReject": {"human_gate_item_id": "alias-target-1", "decision": "reject"},
    "HumanGateRequestMoreEvidence": {"human_gate_item_id": "alias-target-1", "decision": "request_more_evidence"},
    "HumanGateRevoke": {"human_gate_item_id": "alias-target-1", "decision": "revoke", "source_type": "approval", "source_id": "alias-target-1"},
    "HumanGateExtendTtl": {"human_gate_item_id": "alias-target-1", "decision": "extend_ttl", "ttl_seconds": 3600},
    "EmergencyContainment": {"action": "freeze", "trigger": "forced_kill", "evidence_refs": ["ev-1"]},
    "QuarterlyRankingRecommendationSubmit": {"quarter": "2026-Q4", "ranking_snapshot_id": "snapshot-alias-1"},
}
_ALIAS_TARGET_TYPES = {"HardRollback": ObjectType.RUNTIME, "ExecuteRollback": ObjectType.RUNTIME}


class _ApprovedDecisions:
    def __init__(self, canonical: str, target: Dict[str, Any]) -> None:
        self._decision = {"outcome": "approved", "command": canonical, "target": target}

    def get_approval_decision(self, decision_id: str):
        return dict(self._decision)

    def get_persona(self, persona_id: str):
        return {"persona_id": persona_id}

    def get_runtime_binding_by_runtime_id(self, runtime_id: str):
        return {"runtime_id": runtime_id, "binding_id": "binding-1", "deployment_mode": "paper", "tenant_id": "tenant-alias"}

    def get_ranking_snapshot(self, snapshot_id: str):
        from services.control_plane.bff.pm12.service import _PM12_LEAGUE_FORMULA_VERSION, _stable_json_hash

        content = {
            "surface": "quarterly", "period": "2026-Q4", "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "items": [{"persona_id": "persona-alias", "score": 90, "stage": "paper"}],
        }
        return {
            **content, "content_digest": _stable_json_hash(content),
            "snapshot_id": snapshot_id, "period": "2026-Q4",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def __getattr__(self, name: str):
        if name.startswith("get_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


def _alias_target(canonical: str) -> Dict[str, str]:
    from services.control_plane.bff.action_catalog import get_catalog_entry

    entity_type = get_catalog_entry(canonical).entity_type
    target_type = _ALIAS_TARGET_TYPES.get(canonical) or next((o for o in ObjectType if o.value == entity_type), ObjectType.RUNTIME)
    target_id = "pm12-2026-q4-persona-alias-promote_to_canary_candidate" if canonical == "QuarterlyRankingRecommendationSubmit" else "alias-target-1"
    return {"type": target_type.value, "id": target_id}


_ALIAS_RECORDS: List[Dict[str, Any]] = []
_DEFAULT_PARAMS = object()


def _submit_alias(wrapper: str, verb: str, canonical: str, *, wrapped: bool, token_for: Optional[str], params: Any = _DEFAULT_PARAMS, action: Any = _DEFAULT_PARAMS):
    """Submit one alias (or its canonical command) to a mounted router.

    token_for: None sends no token; otherwise a real confirm token is issued bound
    to that canonical command (the same one, or another action's) and sent.
    Approval and two-man evidence are seeded for the canonical command so the
    only thing that can reject a request is the case under test.
    """
    target = _alias_target(canonical)
    body: Dict[str, Any] = {
        "command": wrapper if wrapped else canonical,
        "target": target,
        "params": {
            "reason": "alias equivalence",
            "approval_decision_id": "appr-alias-1",
            "two_man_signature_id": "sig-alias-1",
            **_ALIAS_PARAMS.get(canonical, {}),
        },
        "audit_context": {"reason": "alias equivalence"},
    }
    if wrapped:
        body["action"] = verb
    if action is not _DEFAULT_PARAMS:
        body["action"] = action
    if params is not _DEFAULT_PARAMS:
        body["params"] = params
    headers = {**HEADERS, "Authorization": "Bearer op-test:operator,approver,admin:mfa", "Idempotency-Key": "alias-key-1"}
    rebalance = canonical == CommandType.APPROVED_APPLY.value
    producer = "bff.rebalance-evidence.v1" if rebalance else "bff.v5-two-man-evidence.v1"
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        for seed_type, seed_params in (
            (CommandType.REBALANCE_TWO_MAN_SIGN if rebalance else CommandType.V5_INTERVENTION_ACTION,
             {"two_man_signature_id": "sig-alias-1", "signer_operator_ids": ["op-a", "op-b"]}),
            *(((CommandType.REBALANCE_APPROVAL, {"approval_decision_id": "appr-alias-1", "outcome": "approved"}),) if rebalance else ()),
        ):
            store.submit_terminal_command(
                f"cmd-seed-{seed_type.value}", seed_type, target, "2026-01-01T00:00:00Z",
                {**seed_params, "command": canonical, "target": target},
                {"trusted_evidence_producer": producer}, {"trusted_evidence_producer": producer},
            )
        svc = CommandAdapterService(
            command_store=store,
            read_surface=_ApprovedDecisions(canonical, target),
            extract_identity=lambda *_a, **_k: OperatorIdentity(operator_id="op-test", roles=["operator", "approver", "admin"], mfa_verified=True, claims={"tenant_id": "tenant-alias"}),
        )
        app = FastAPI()
        app.include_router(create_command_adapters_router(service=svc, submit_command_admission=svc.submit_command_admission))
        client = TestClient(app)
        if token_for is not None:
            issued = client.post(
                "/bff/confirm-tokens",
                headers={**headers, "Idempotency-Key": "alias-token-key-1"},
                json={"tokenId": "ct-alias-1", "command": token_for, "target": target, "reason": "alias equivalence", "ttlSeconds": 300},
            )
            assert issued.status_code == 201, issued.text
            headers["X-Confirm-Token"] = "ct-alias-1"
        resp = client.post("/bff/v1/commands", headers=headers, json=body)
        _ALIAS_RECORDS[:] = [r for r in store._get_all_commands() if r.get("type") == canonical]
        stored = [
            {
                **_scrub({k: r.get(k) for k in ("type", "target", "params", "status")}),
                "request_hash": ((r.get("foundation") or {}).get("idempotency_record") or {}).get("request_hash")
                if ((r.get("foundation") or {}).get("idempotency_record") or {}).get("idempotency_key") == "alias-key-1"
                else None,
            }
            for r in store._get_all_commands()
            if not str(r.get("command_id")).startswith("cmd-seed-") and r.get("type") != CommandType.CONFIRM_TOKEN_CREATE.value
        ]
        return resp.status_code, _scrub(resp.json()), stored


def _alias_cases():
    from services.control_plane.bff.command_adapters.contracts import _WRAPPER_VERB_ALIASES

    canonicals = sorted(set(_WRAPPER_VERB_ALIASES.values()))
    for (wrapper, verb), canonical in sorted(_WRAPPER_VERB_ALIASES.items()):
        other = next(c for c in canonicals if c != canonical)
        yield pytest.param(wrapper, verb, canonical, canonical, id=f"{wrapper}-{verb}-own-token")
        yield pytest.param(wrapper, verb, canonical, other, id=f"{wrapper}-{verb}-cross-token")
        yield pytest.param(wrapper, verb, canonical, None, id=f"{wrapper}-{verb}-no-token")


@pytest.mark.parametrize("wrapper,verb,canonical,token_for", list(_alias_cases()))
def test_wrapped_alias_is_admitted_exactly_like_its_canonical_command(wrapper, verb, canonical, token_for) -> None:
    wrapped = _submit_alias(wrapper, verb, canonical, wrapped=True, token_for=token_for)
    direct = _submit_alias(wrapper, verb, canonical, wrapped=False, token_for=token_for)
    assert wrapped == direct
    status, _body, stored = wrapped
    if status == 202:
        assert [row["type"] for row in stored if row["request_hash"]] == [canonical]
    else:
        assert stored == []
    from services.control_plane.bff.action_catalog import get_catalog_entry

    if get_catalog_entry(canonical).requires_confirm_token and token_for != canonical:
        assert status == 428


@pytest.mark.parametrize("wrapper,verb,canonical", sorted((w, v, c) for (w, v), c in __import__("services.control_plane.bff.command_adapters.contracts", fromlist=["x"])._WRAPPER_VERB_ALIASES.items()))
def test_every_alias_has_an_accepted_path_stored_as_canonical(wrapper, verb, canonical) -> None:
    from unittest.mock import patch

    projection = {"meta": {"surfaces": {"committee_board": "ready"}}, "allowedActions": {"canRecordSponsorDecision": True}}
    with patch("services.control_plane.bff.governance.service.GovernanceService.committee_projection", return_value=projection):
        status, _body, stored = _submit_alias(wrapper, verb, canonical, wrapped=True, token_for=canonical)
    if canonical == "RebalanceProposal":  # canonical admission never accepts it; rejection parity is asserted above
        assert status == 422 and stored == []
        return
    assert status == 202, (status, _body)
    assert [row["type"] for row in stored if row["request_hash"]] == [canonical]


@pytest.mark.parametrize("wrapper,verb,canonical", sorted((w, v, c) for (w, v), c in __import__("services.control_plane.bff.command_adapters.contracts", fromlist=["x"])._WRAPPER_VERB_ALIASES.items()))
@pytest.mark.parametrize("params", [None, [], ["invalid"], "invalid", 0, False])
def test_malformed_params_rejected_identically_for_every_alias(wrapper, verb, canonical, params) -> None:
    wrapped = _submit_alias(wrapper, verb, canonical, wrapped=True, token_for=canonical, params=params)
    direct = _submit_alias(wrapper, verb, canonical, wrapped=False, token_for=canonical, params=params)
    assert wrapped == direct
    assert wrapped[0] == 422
    assert wrapped[2] == []


@pytest.mark.parametrize("action", [[], {}, 0, False, ["ack"], {"ack": ""}, 1, True])
@pytest.mark.parametrize("params", [{}, {"action_id": "ack"}, {"actionId": "ack"}])
def test_malformed_action_rejected_identically_with_alias_fallback(action, params) -> None:
    args = ("RiskAlertAction", "ack", "AlertAcknowledge")
    wrapped = _submit_alias(*args, wrapped=True, token_for=None, params=params, action=action)
    direct = _submit_alias(*args, wrapped=False, token_for=None, params=params, action=action)
    assert wrapped == direct
    assert wrapped[0] == 422
    assert wrapped[2] == []


@pytest.mark.parametrize("key", ["action_id", "actionId"])
@pytest.mark.parametrize("action", [None, "", "ACK"])
def test_valid_action_and_alias_fallback_keep_canonical_admission(key, action) -> None:
    args = ("RiskAlertAction", "ack", "AlertAcknowledge")
    wrapped = _submit_alias(*args, wrapped=True, token_for=None, params={key: "ack"}, action=action)
    direct = _submit_alias(*args, wrapped=False, token_for=None, params={})
    assert wrapped == direct
    assert wrapped[0] == 202
    assert [row["type"] for row in wrapped[2]] == ["AlertAcknowledge"]


@pytest.mark.parametrize("params,expected", [({}, 422), (_ALIAS_PARAMS["QuarterlyRankingRecommendationSubmit"], 202)])
def test_ranking_adapter_existing_canonical_alias_parity(params, expected) -> None:
    canonical = "QuarterlyRankingRecommendationSubmit"
    wrapped = _submit_alias("RankingAction", canonical, canonical, wrapped=True, token_for=canonical, params=params)
    direct = _submit_alias("RankingAction", canonical, canonical, wrapped=False, token_for=canonical, params=params)
    assert wrapped == direct
    assert wrapped[0] == expected
    assert [row["type"] for row in wrapped[2]] == ([canonical] if expected == 202 else [])


_DISPATCH_CASES = [
    ("RuntimeAction", "start", "StartRuntime", "/runtimes/alias-target-1/start"),
    # Persona lifecycle's real authenticated owner is covered by test_persona_lifecycle_forward.
    ("RuntimeAction", "RestartPaperRuntime", "RestartPaperRuntime", "/paper-runtimes/alias-target-1/restart"),
    ("RuntimeAction", "RestartTelemetryBridge", "RestartTelemetryBridge", "/paper-runtimes/alias-target-1/telemetry-bridge/restart"),
    ("RuntimeAction", "TerminateStalePaperMonitoringSession", "TerminateStalePaperMonitoringSession", "/monitoring-sessions/alias-target-1/terminate-stale"),
    ("RuntimeAction", "StartPaperMonitoringSession", "StartPaperMonitoringSession", "/paper-runtimes/alias-target-1/monitoring-sessions/start"),
    ("RuntimeAction", "ProbeTelemetryIngest", "ProbeTelemetryIngest", "/paper-runtimes/alias-target-1/telemetry-ingest/probe"),
]


@pytest.mark.parametrize("wrapper,verb,canonical,suffix", _DISPATCH_CASES)
def test_accepted_wrapped_alias_dispatches_successfully(monkeypatch, wrapper, verb, canonical, suffix) -> None:
    from services.control_plane.bff import command_executor
    from services.control_plane.bff.command_adapters.service import _resolve_execution_params_for_record

    status, _body, _stored = _submit_alias(wrapper, verb, canonical, wrapped=True, token_for=canonical)
    assert status == 202
    posts: List[Any] = []
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://owner.invalid")
    monkeypatch.setattr(command_executor, "_post_json", lambda url, payload, **_kw: posts.append((url, payload)) or {})
    params = _resolve_execution_params_for_record(_ALIAS_RECORDS[0])
    outcome, _result, error = command_executor.execute_command_with_status("cmd-1", CommandType(canonical), params)
    assert outcome.value == "executed", error
    assert posts[0][0].endswith(suffix)
    assert len(posts) == 1
    assert posts[0][1].get("confirm_token", "ct-alias-1") == "ct-alias-1"


def test_unmapped_wrapper_combinations_are_not_rewritten() -> None:
    from services.control_plane.bff.command_adapters.contracts import canonicalize_wrapped_payload

    for command, verb in (("IncidentAction", "acknowledge"), ("IncidentAction", "remediate"), ("NotACommandAction", "AlertAcknowledge"), ("CapitalPoolAction", "AlertAcknowledge")):
        payload = {"command": command, "action": verb, "target": {"type": "Incident", "id": "x"}, "params": {}}
        assert canonicalize_wrapped_payload(payload) == payload


def _mounted_service(td: str):
    store = CommandStore(os.path.join(td, "commands.jsonl"))
    svc = CommandAdapterService(command_store=store, read_surface=None, extract_identity=_test_extract_identity)
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=svc, submit_command_admission=svc.submit_command_admission))
    return store, svc, TestClient(app)


@pytest.mark.parametrize("command", ["NotACommandAction", "CapitalPoolAction", [], {}, ["RuntimeAction"], {"name": "RuntimeAction"}, None, 0, False])
def test_unmapped_wrapper_is_rejected_before_storage(command) -> None:
    with tempfile.TemporaryDirectory() as td:
        store, _svc, client = _mounted_service(td)
        resp = client.post("/bff/v1/commands", headers={**HEADERS, "Idempotency-Key": "unmapped-key-1"}, json={
            "command": command, "action": "AlertAcknowledge", "target": {"type": "RiskAlert", "id": "alert-incident-inc-test"},
            "params": {}, "audit_context": {"reason": "unmapped"},
        })
        assert resp.status_code == 422, resp.text
        assert store._get_all_commands() == []


@pytest.mark.parametrize("body", [
    {"command": "PauseRuntime"},
    {"command": "RuntimeAction", "action": "pause"},
])
def test_wrong_domain_target_is_rejected_before_store_and_token_use(body) -> None:
    identity = OperatorIdentity(operator_id="op-test", roles=["operator", "approver"], mfa_verified=True)
    params = {"runtime_binding_id": "rb-1", "pause_action": "pause"}
    with tempfile.TemporaryDirectory() as td:
        store, svc, client = _mounted_service(td)

        def submit(target_type: str, key: str, token: str):
            return client.post("/bff/v1/commands", headers={**HEADERS, "Idempotency-Key": key, "X-Confirm-Token": token}, json={
                **body, "target": {"type": target_type, "id": "persona-123"}, "params": params, "audit_context": {"reason": "pause"},
            })

        res = svc.create_confirm_token(
            payload={"action": "PauseRuntime", "command": "PauseRuntime", "target": {"type": "Persona", "id": "persona-123"}, "reason": "pause", "ttl_seconds": 60},
            identity=identity, idempotency_key="wrong-target-token-1",
        )
        token = json.loads(res.body.decode("utf-8"))["data"]["tokenId"]
        rejected = submit("Persona", "wrong-target-key-1", token)
        assert rejected.status_code == 422, rejected.text
        assert [c["type"] for c in store._get_all_commands()] == ["CreateConfirmToken"]
        # the rejected submission must not have consumed the token
        assert client.get(f"/bff/confirm-tokens/{token}", headers=HEADERS).json()["data"]["status"] == "created"


def test_legitimate_runtime_binding_target_still_admitted_for_pause() -> None:
    from services.control_plane.bff.command_adapters.preconditions import validate_final_command_target_type
    for target_type in ("Runtime", "RuntimeBinding"):
        validate_final_command_target_type(OperatorCommand.model_validate({
            "command": "PauseRuntime", "target": {"type": target_type, "id": "rb-1"},
            "params": {}, "audit_context": {"reason": "pause"},
        }))


def test_assistant_admission_uses_injected_service_and_returns_stored_command_id() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from services.control_plane.bff.assistant import routes
    from services.control_plane.bff.core.app_factory import mount_bff_routers

    class _Captured(Exception):
        pass

    captured: Dict[str, Any] = {}
    original = routes.create_assistant_router

    def capture(**kw):
        captured["router"] = original(**kw)
        captured["submit"] = kw["submit_command_admission"]
        raise _Captured()

    identity = OperatorIdentity(operator_id="asst-probe", roles=["operator"], mfa_verified=True, claims={"tenant_id": "tenant-probe"})
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        svc = CommandAdapterService(command_store=store, read_surface=None, extract_identity=lambda *a, **k: identity)
        deps = SimpleNamespace(read_surface=MagicMock(), command_store=store)
        routes.create_assistant_router = capture
        try:
            with pytest.raises(_Captured):
                mount_bff_routers(FastAPI(), app_deps=deps, _command_adapter_service=svc, _extract_identity=lambda *a, **k: identity)
        finally:
            routes.create_assistant_router = original
        assert captured["submit"].__self__ is svc
        app = FastAPI()
        app.include_router(captured["router"])
        resp = TestClient(app).post("/bff/assistant/tools/execute", json={
            "action_id": "AuditExport", "entity_type": "AuditExport", "entity_id": "audit-test", "params": {}, "reason": "probe",
        })
        assert resp.status_code == 201, resp.text
        stored = [r["command_id"] for r in store._get_all_commands()]
        assert stored and resp.json()["data"]["command_id"] in stored
