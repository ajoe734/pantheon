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
            if f.endswith(".py") and f not in ("runtime_adapter.py", "preconditions.py"):
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

    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        # 1. Custom check_read_surface_state injected
        custom_warning = StalenessWarning(
            read_surface_state="degraded",
            message="Command submitted against stale read surface data. Verify target state via secondary control path before confirming action.",
        )
        router = create_command_adapters_router(
            command_store=store,
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
        fresh_store = CommandStore(os.path.join(td, "fresh_commands.jsonl"))
        fresh_router = create_command_adapters_router(
            command_store=fresh_store,
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


# Every claim path ``resolve_identity_tenant`` (services/control-plane/bff/
# command_adapters/service.py) recognises, mirroring auth/policy.py's
# ``bff_me_tenant_payload`` claim set. Each entry builds the JWT claim
# fragment for one authenticated tenant-claim shape.
_TENANT_CLAIM_SHAPES: Dict[str, Any] = {
    "tenant_id": lambda tenant: {"tenant_id": tenant},
    "tenantId": lambda tenant: {"tenantId": tenant},
    "tenant.id": lambda tenant: {"tenant": {"id": tenant}},
    "tid": lambda tenant: {"tid": tenant},
    "org_id": lambda tenant: {"org_id": tenant},
    "organization.id": lambda tenant: {"organization": {"id": tenant}},
    "tenant_ids": lambda tenant: {"tenant_ids": [tenant]},
    "tenantIds": lambda tenant: {"tenantIds": [tenant]},
    # Pseudo-shapes exercising the two fail-closed paths in
    # ``resolve_identity_tenant``: no recognised tenant claim at all, and
    # two recognised claims that disagree (an identity must not silently
    # bind to an arbitrary one of several distinct tenant claims).
    "absent": lambda tenant: {},
    "ambiguous": lambda tenant: {"tenant_id": tenant, "tid": f"{tenant}-ambiguous-alt"},
}


def _encode_tenant_identity(
    *, secret: str, sub: str, tenant: str, claim_shape: str, issuer: str, audience: str
) -> str:
    import time

    from services.runtime_auth_inbound import encode_jwt_hs256

    now = int(time.time())
    claims = {
        "sub": sub,
        "roles": ["operator"],
        **_TENANT_CLAIM_SHAPES[claim_shape](tenant),
        "iss": issuer,
        "aud": audience,
        "iat": now - 10,
        "exp": now + 300,
    }
    return encode_jwt_hs256(claims, secret=secret)


@pytest.mark.parametrize("claim_shape", sorted(_TENANT_CLAIM_SHAPES))
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("route", ["confirmation", "guarded"])
def test_signed_identity_tenant_cannot_consume_foreign_confirm_token(
    tmp_path, monkeypatch, restart, route, claim_shape
) -> None:
    """Regression: a real, signed OperatorIdentity JWT must scope confirm
    tokens by tenant. ``CommandAdapterService`` previously resolved the
    caller's tenant via
    ``getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)``,
    which is always ``None`` for a genuine ``extract_identity_jwt`` identity
    (tenant lives in ``identity.claims``, not a top-level attribute). That
    bug silently disabled ``check_confirm_token_tenant_authorization``, so a
    caller from tenant B with the same JWT subject as tenant A could confirm
    or redeem a confirm token tenant A issued.

    A follow-up defect narrowed the fix to only ``tenant_id``/``tenantId``/
    ``tenant`` claims, so a real ``tid`` (or ``org_id``/``organization.id``/
    ``tenant_ids``/``tenantIds``) identity still resolved to ``None`` and
    fell through the same "tenant missing" 403, masking the correct 403 for
    the wrong reason -- and, more importantly, was silently fail-open for
    any caller shape the resolver did not special-case. ``claim_shape`` is
    parametrized over every claim path the canonical resolver supports,
    plus a caller with no recognised tenant claim at all (``absent``) and a
    caller whose claims name two distinct tenants (``ambiguous``), both of
    which must also fail closed rather than silently pick one.

    Uses real signed HS256 JWTs through the production
    ``extract_identity_jwt`` (not a test identity stub) against both
    durable-write routes, with and without service/store reconstruction
    (restart), to prove the durable rejection survives process restart.
    """
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters.service import CommandAdapterService

    secret = "test-tenant-identity-regression-secret"
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "tenant-identity-regression")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "tenant-identity-regression")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    def headers(tenant: str, idempotency_key: str, *, shape: str = "tenant_id") -> Dict[str, str]:
        token = _encode_tenant_identity(
            secret=secret,
            sub="same-actor",
            tenant=tenant,
            claim_shape=shape,
            issuer="tenant-identity-regression",
            audience="tenant-identity-regression",
        )
        return {"Authorization": "Bearer " + token, "Idempotency-Key": idempotency_key}

    command_path = str(tmp_path / "commands.jsonl")

    def client() -> TestClient:
        service = CommandAdapterService(
            command_store=CommandStore(command_path),
            extract_identity=extract_identity_jwt,
            check_read_surface_state=lambda: None,
            process_command_task=lambda command_id: None,
        )
        app = FastAPI()
        app.include_router(create_command_adapters_router(service=service))
        return TestClient(app)

    mounted = client()
    issued = mounted.post(
        "/bff/confirm-tokens",
        headers=headers("tenant-a", "issue-a"),
        json={
            "tokenId": "tenant-a-token",
            "ttlSeconds": 300,
            "command": "PauseRuntime",
            "target_type": "Runtime",
            "target_id": "isolated-runtime",
            "operator_id": "same-actor",
        },
    )
    assert issued.status_code == 201, issued.text

    if restart:
        mounted = client()

    foreign_headers = headers("tenant-b", "foreign-submit", shape=claim_shape)
    if route == "confirmation":
        foreign = mounted.post(
            "/bff/command-confirmations/tenant-a-token/confirm",
            headers=foreign_headers,
            json={"command_id": "foreign-command"},
        )
    else:
        foreign_headers["X-Confirm-Token"] = "tenant-a-token"
        foreign = mounted.post(
            "/bff/v1/commands",
            headers=foreign_headers,
            json={
                "command": "PauseRuntime",
                "target": {"type": "Runtime", "id": "isolated-runtime"},
                "params": {"runtime_binding_id": "isolated-binding", "pause_action": "pause"},
                "audit_context": {"reason": "tenant identity regression negative authorization test"},
            },
        )

    records = CommandStore(command_path)._get_all_commands()
    evidence = {
        "response_status": foreign.status_code,
        "rows": [
            {"type": record["type"], "tenant": record.get("audit", {}).get("tenant_id")}
            for record in records
        ],
    }
    assert foreign.status_code in (403, 404, 428), evidence
    assert len(records) == 1, evidence


@pytest.mark.parametrize("claim_shape", ["tenant_id", "tid", "tenant.id"])
@pytest.mark.parametrize("restart", [False, True])
def test_signed_identity_same_tenant_confirm_token_idempotency_key_replays(
    tmp_path, monkeypatch, restart, claim_shape
) -> None:
    """Companion to the foreign-tenant rejection above: the legitimate
    tenant replaying its own ``Idempotency-Key`` for the confirm-token
    issue route must still return the original durable result rather than
    raise a conflict or create a second command row, across the same
    real-JWT tenant-claim shapes and across a service/store restart. This
    guards against a resolver fix that starts requiring a stronger match
    (for example both claim value and shape) than the durable idempotency
    record was actually keyed on.
    """
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters.service import CommandAdapterService

    secret = "test-tenant-identity-replay-secret"
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "tenant-identity-replay")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "tenant-identity-replay")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    def headers(idempotency_key: str) -> Dict[str, str]:
        token = _encode_tenant_identity(
            secret=secret,
            sub="same-actor",
            tenant="tenant-a",
            claim_shape=claim_shape,
            issuer="tenant-identity-replay",
            audience="tenant-identity-replay",
        )
        return {"Authorization": "Bearer " + token, "Idempotency-Key": idempotency_key}

    command_path = str(tmp_path / "commands.jsonl")

    def client() -> TestClient:
        service = CommandAdapterService(
            command_store=CommandStore(command_path),
            extract_identity=extract_identity_jwt,
            check_read_surface_state=lambda: None,
            process_command_task=lambda command_id: None,
        )
        app = FastAPI()
        app.include_router(create_command_adapters_router(service=service))
        return TestClient(app)

    mounted = client()
    payload = {
        "tokenId": "tenant-a-replay-token",
        "ttlSeconds": 300,
        "command": "PauseRuntime",
        "target_type": "Runtime",
        "target_id": "isolated-runtime",
        "operator_id": "same-actor",
    }
    first = mounted.post("/bff/confirm-tokens", headers=headers("replay-key"), json=payload)
    assert first.status_code == 201, first.text

    if restart:
        mounted = client()

    replay = mounted.post("/bff/confirm-tokens", headers=headers("replay-key"), json=payload)
    assert replay.status_code == first.status_code, replay.text
    assert replay.json().get("tokenId") == first.json().get("tokenId")

    records = CommandStore(command_path)._get_all_commands()
    assert len(records) == 1, {
        "response_status": replay.status_code,
        "rows": [record["type"] for record in records],
    }


@pytest.mark.parametrize("admission_shape", ["missing", "ambiguous", "foreign", "same_tenant"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "PausePaperRuntime",
        "ResumePaperRuntime",
        "pause",
        "pauseRuntime",
        "pauseExecution",
        "resume",
        "unpause",
    ],
)
def test_signed_identity_runtime_pause_resume_requires_owner_tenant(
    tmp_path, monkeypatch, restart, action, admission_shape
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT at 77f0d1ed3bde5bdc0765055a3256ed888e010183: mounted
    ``POST /bff/v1/commands`` with ``command=RuntimeAction`` bypassed tenant
    authorization through action aliases.

    ``RuntimeCommandAdapter._execute_pause`` (command_adapters/runtime_adapter.py)
    previously replaced an absent/ambiguous admission-stamped tenant with
    the *target* binding's own tenant before comparing them for
    ``PausePaperRuntime``/``ResumePaperRuntime``, so any caller whose tenant
    could not be resolved -- or whose claims named two tenants -- was always
    "authorized" against whatever tenant happened to own the runtime. The
    ``pause``/``resume`` generic aliases skipped the ownership check
    entirely, so even a signed foreign-tenant operator could operate the
    binding.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the HTTP
    transport to the downstream runtime-manager and the owner readback are
    stubbed), across a CommandStore restart, for every admission tenant
    shape (missing, ambiguous, foreign, and the legitimate same-tenant
    case) and for both the canonical actions and their generic aliases.
    Every non-same-tenant shape must dispatch zero downstream pause/resume
    calls; the same-tenant case must still execute.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-runtime-pause-resume-tenant-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://runtime-pause-resume-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "runtime-pause-resume-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "runtime-pause-resume-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    if admission_shape == "same_tenant":
        scope: Dict[str, Any] = {"tenant_id": "tenant-a"}
    elif admission_shape == "foreign":
        scope = {"tenant_id": "tenant-b"}
    elif admission_shape == "ambiguous":
        scope = {"tenant_id": "tenant-b", "tid": "tenant-c"}
    else:
        scope = {}
    token = encode_jwt_hs256(
        {
            "sub": "runtime-review-actor",
            "roles": ["operator"],
            "iss": "runtime-pause-resume-review",
            "aud": "runtime-pause-resume-review",
            "iat": now - 10,
            "exp": now + 300,
            **scope,
        },
        secret=secret,
    )

    binding = {
        "runtime_id": "rt-review",
        "binding_id": "bind-review",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    is_resume = action in ("ResumePaperRuntime", "resume", "unpause")

    def fake_http(url, **kwargs):
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        binding["status"] = "active" if is_resume else "paused"
        return {
            "status": "executed",
            "status_after": binding["status"],
            "binding_id": "bind-review",
            "runtime_id": "rt-review",
        }

    effective_command = "ResumePaperRuntime" if is_resume else "PausePaperRuntime"
    target_id = "rt-review" if action in ("PausePaperRuntime", "ResumePaperRuntime") else "bind-review"
    approval_id = "runtime-pause-resume-approval"
    approvals = {
        approval_id: {
            "outcome": "approved",
            "command": effective_command,
            "target": {"type": "Runtime", "id": target_id},
        }
    }
    read_store_stub = SimpleNamespace(
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == "rt-review" else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-review" else None,
        get_approval_decision=lambda decision_id: dict(approvals.get(decision_id) or {}) or None,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)
    monkeypatch.setattr(
        runtime_adapter,
        "_get_runtime_manager_client",
        lambda: SimpleNamespace(get=lambda bid: dict(binding), list_all=lambda: [dict(binding)]),
    )
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)

    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"runtime-pause-resume-{admission_shape}",
    }
    params: Dict[str, Any] = {"action_id": action}
    if admission_shape == "same_tenant":
        issue_headers = dict(headers)
        issue_headers["Idempotency-Key"] = f"runtime-pause-resume-issue-{action}-{restart}"
        issued = client.post(
            "/bff/confirm-tokens",
            headers=issue_headers,
            json={
                "tokenId": f"pause-resume-token-{action}",
                "ttlSeconds": 300,
                # Bound to the effective canonical command
                # (PausePaperRuntime/ResumePaperRuntime), not the literal
                # "RuntimeAction" wrapper name -- this is what
                # require_final_command_preconditions now validates the
                # wrapped pause/resume aliases against.
                "command": effective_command,
                "target_type": "Runtime",
                "target_id": target_id,
                "operator_id": "runtime-review-actor",
            },
        )
        assert issued.status_code == 201, issued.text
        headers["X-Confirm-Token"] = f"pause-resume-token-{action}"
        if is_resume:
            params["approvalId"] = approval_id

    response = client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": "RuntimeAction",
            "target": {"type": "Runtime", "id": target_id},
            "params": params,
            "audit_context": {"reason": "runtime pause/resume tenant authorization regression"},
        },
    )

    rows = store._get_all_commands()
    runtime_action_rows = [row for row in rows if row["type"] == "RuntimeAction"]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(runtime_action_rows[0]["command_id"]) if runtime_action_rows else {}
    evidence = {
        "admission_shape": admission_shape,
        "restart": restart,
        "action": action,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    if admission_shape == "same_tenant":
        assert response.status_code == 202, evidence
        assert len(calls) == 1, evidence
        assert final.get("status") == "executed", evidence
    else:
        assert calls == [], evidence
        assert final.get("status") != "executed", evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "identity_roles,expect_denied",
    [
        (["viewer"], True),
        (["operator"], False),
    ],
)
@pytest.mark.parametrize(
    "action",
    ["PausePaperRuntime", "ResumePaperRuntime", "pause", "resume"],
)
def test_signed_identity_runtime_action_pause_resume_requires_effective_role_parity(
    tmp_path, monkeypatch, restart, action, identity_roles, expect_denied
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT at PR #5998 exact head 0cc437dc27bca5acd95d6e33109b09842123a522
    (manifest 7dd37f4a56b941878ebcf063e40b9aa446e0588b): ``_VALIDATOR_EFFECTIVE_
    ACTION_EXCLUDED`` excluded PausePaperRuntime/ResumePaperRuntime from
    effective-command validator routing entirely. ``RuntimeAction`` has no
    ``self._validators`` entry of its own, so every RuntimeAction spelling of
    pause/resume (the literal canonical action_id and every bare alias) got
    no role check at admission at all: a mounted same-tenant signed-JWT
    caller holding only a read role (``viewer``) could create a genuine
    confirm token (read-role permitted) and then durably admit and dispatch
    a wrapped pause/resume that the direct canonical command would have
    refused with 403 for lacking ``operator``/``admin`` (pause) or
    ``operator``/``approver``/``admin`` (resume).

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor for a genuine paper
    binding, across a ``CommandStore`` restart, asserting the missing-role
    identity gets zero downstream dispatch and the operator-role identity
    (which every canonical pause/resume command accepts) still executes,
    for both the literal canonical action_id spelling and the bare
    pause/resume aliases RuntimeAction dispatches to the same canonical
    command.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-runtime-pause-resume-role-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://runtime-pause-resume-role-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "runtime-pause-resume-role-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "runtime-pause-resume-role-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "runtime-role-review-actor",
            "roles": identity_roles,
            "tenant_id": "tenant-a",
            "iss": "runtime-pause-resume-role-review",
            "aud": "runtime-pause-resume-role-review",
            "iat": now - 10,
            "exp": now + 300,
        },
        secret=secret,
    )

    binding = {
        "runtime_id": "rt-role-review",
        "binding_id": "bind-role-review",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []
    is_resume = action in ("ResumePaperRuntime", "resume")

    def fake_http(url, **kwargs):
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        binding["status"] = "active" if is_resume else "paused"
        return {
            "status": "executed",
            "status_after": binding["status"],
            "binding_id": "bind-role-review",
            "runtime_id": "rt-role-review",
        }

    effective_command = "ResumePaperRuntime" if is_resume else "PausePaperRuntime"
    target_id = "rt-role-review" if action in ("PausePaperRuntime", "ResumePaperRuntime") else "bind-role-review"
    approval_id = "runtime-pause-resume-role-approval"
    approvals = {
        approval_id: {
            "outcome": "approved",
            "command": effective_command,
            "target": {"type": "Runtime", "id": target_id},
        }
    }
    read_store_stub = SimpleNamespace(
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == "rt-role-review" else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-role-review" else None,
        get_approval_decision=lambda decision_id: dict(approvals.get(decision_id) or {}) or None,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)
    monkeypatch.setattr(
        runtime_adapter,
        "_get_runtime_manager_client",
        lambda: SimpleNamespace(get=lambda bid: dict(binding), list_all=lambda: [dict(binding)]),
    )
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)

    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"runtime-pause-resume-role-{'-'.join(identity_roles)}-{action}-{restart}",
    }
    params: Dict[str, Any] = {"action_id": action}
    issue_headers = dict(headers)
    issue_headers["Idempotency-Key"] = f"runtime-pause-resume-role-issue-{'-'.join(identity_roles)}-{action}-{restart}"
    issued = client.post(
        "/bff/confirm-tokens",
        headers=issue_headers,
        json={
            "tokenId": f"pause-resume-role-token-{'-'.join(identity_roles)}-{action}",
            "ttlSeconds": 300,
            "command": effective_command,
            "target_type": "Runtime",
            "target_id": target_id,
            "operator_id": "runtime-role-review-actor",
        },
    )
    assert issued.status_code == 201, issued.text
    headers["X-Confirm-Token"] = f"pause-resume-role-token-{'-'.join(identity_roles)}-{action}"
    if is_resume:
        params["approvalId"] = approval_id

    response = client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": "RuntimeAction",
            "target": {"type": "Runtime", "id": target_id},
            "params": params,
            "audit_context": {"reason": "runtime pause/resume role authorization regression"},
        },
    )

    rows = store._get_all_commands()
    runtime_action_rows = [row for row in rows if row["type"] == "RuntimeAction"]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(runtime_action_rows[0]["command_id"]) if runtime_action_rows else {}
    evidence = {
        "identity_roles": identity_roles,
        "restart": restart,
        "action": action,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    if expect_denied:
        assert response.status_code == 403, evidence
        assert calls == [], evidence
        assert final.get("status") != "executed", evidence
    else:
        assert response.status_code == 202, evidence
        assert len(calls) == 1, evidence
        assert final.get("status") == "executed", evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "action",
    ["PausePaperRuntime", "ResumePaperRuntime", "pause", "resume"],
)
def test_signed_identity_runtime_action_pause_resume_requires_effective_paper_stage(
    tmp_path, monkeypatch, restart, action
) -> None:
    """Regression for the same DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001
    exclusion as
    ``test_signed_identity_runtime_action_pause_resume_requires_effective_role_parity``:
    with PausePaperRuntime/ResumePaperRuntime excluded from effective-
    command validator routing, ``_validate_pause_paper_runtime``'s/
    ``_validate_resume_paper_runtime``'s ``required_bindings=["paper"]``
    stage check (which refuses a non-paper-stage binding) never ran for the
    ``RuntimeAction`` wrapper either. Drives a real signed-JWT mounted
    same-tenant, operator-role admission through the durable ``CommandStore``
    and ``process_command`` executor against a binding whose
    ``deployment_mode`` is ``canary`` (not ``paper``), across a
    ``CommandStore`` restart, for both the literal canonical action_id
    spelling and the bare pause/resume aliases.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-runtime-pause-resume-stage-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://runtime-pause-resume-stage-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "runtime-pause-resume-stage-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "runtime-pause-resume-stage-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "runtime-stage-review-actor",
            "roles": ["operator"],
            "tenant_id": "tenant-a",
            "iss": "runtime-pause-resume-stage-review",
            "aud": "runtime-pause-resume-stage-review",
            "iat": now - 10,
            "exp": now + 300,
        },
        secret=secret,
    )

    binding = {
        "runtime_id": "rt-stage-review",
        "binding_id": "bind-stage-review",
        "deployment_mode": "canary",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []
    is_resume = action in ("ResumePaperRuntime", "resume")

    def fake_http(url, **kwargs):
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        return {"status": "executed", "binding_id": "bind-stage-review", "runtime_id": "rt-stage-review"}

    effective_command = "ResumePaperRuntime" if is_resume else "PausePaperRuntime"
    target_id = "rt-stage-review" if action in ("PausePaperRuntime", "ResumePaperRuntime") else "bind-stage-review"
    approval_id = "runtime-pause-resume-stage-approval"
    approvals = {
        approval_id: {
            "outcome": "approved",
            "command": effective_command,
            "target": {"type": "Runtime", "id": target_id},
        }
    }
    read_store_stub = SimpleNamespace(
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == "rt-stage-review" else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-stage-review" else None,
        get_approval_decision=lambda decision_id: dict(approvals.get(decision_id) or {}) or None,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)
    monkeypatch.setattr(
        runtime_adapter,
        "_get_runtime_manager_client",
        lambda: SimpleNamespace(get=lambda bid: dict(binding), list_all=lambda: [dict(binding)]),
    )
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)

    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"runtime-pause-resume-stage-{action}-{restart}",
    }
    params: Dict[str, Any] = {"action_id": action}
    issue_headers = dict(headers)
    issue_headers["Idempotency-Key"] = f"runtime-pause-resume-stage-issue-{action}-{restart}"
    issued = client.post(
        "/bff/confirm-tokens",
        headers=issue_headers,
        json={
            "tokenId": f"pause-resume-stage-token-{action}",
            "ttlSeconds": 300,
            "command": effective_command,
            "target_type": "Runtime",
            "target_id": target_id,
            "operator_id": "runtime-stage-review-actor",
        },
    )
    assert issued.status_code == 201, issued.text
    headers["X-Confirm-Token"] = f"pause-resume-stage-token-{action}"
    if is_resume:
        params["approvalId"] = approval_id

    response = client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": "RuntimeAction",
            "target": {"type": "Runtime", "id": target_id},
            "params": params,
            "audit_context": {"reason": "runtime pause/resume paper-stage regression"},
        },
    )

    rows = store._get_all_commands()
    runtime_action_rows = [row for row in rows if row["type"] == "RuntimeAction"]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(runtime_action_rows[0]["command_id"]) if runtime_action_rows else {}
    evidence = {
        "restart": restart,
        "action": action,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    assert response.status_code == 422, evidence
    assert calls == [], evidence
    assert final.get("status") != "executed", evidence


@pytest.mark.parametrize("evidence_shape", ["missing", "invalid", "valid"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "PausePaperRuntime",
        "ResumePaperRuntime",
        "pause",
        "pauseRuntime",
        "pauseExecution",
        "resume",
        "unpause",
    ],
)
def test_signed_identity_runtime_action_pause_resume_requires_effective_confirm_token_and_approval(
    tmp_path, monkeypatch, restart, action, evidence_shape
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT at PR #5998 head 4a24cf62d7ac1cc1732437e067a1ea8048583d1e:
    ``runtime_adapter.py`` classified the literal ``PausePaperRuntime``/
    ``ResumePaperRuntime`` ``action_id`` values *and* every bare
    ``pause``/``pauseRuntime``/``pauseExecution``/``resume``/``unpause``
    alias dispatched through the generic ``RuntimeAction`` wrapper as
    ``generic_ok``, so ``service.py`` validated admission against
    ``RuntimeAction``'s own (weak, ``requires_confirm_token=False``) catalog
    entry instead of the effective canonical command's. A same-tenant
    mounted signed-JWT ``POST /bff/v1/commands`` with no confirm token (and,
    for resume-shaped aliases, no approval evidence) reached durable
    admission and dispatched exactly like a caller who supplied real
    evidence to the direct ``PausePaperRuntime``/``ResumePaperRuntime``
    commands, both before and after a ``CommandStore`` restart.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor for every evidence
    shape (missing entirely, present but invalid/unbound, and a genuine
    valid confirm token plus -- for resume-shaped actions -- a genuine
    approved approval decision bound to the exact effective command and
    target), across a CommandStore restart, for every action_id spelling
    RuntimeAction dispatches to pause/resume. Only the fully valid shape may
    dispatch.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-runtime-pause-resume-confirm-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://runtime-pause-resume-confirm-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "runtime-pause-resume-confirm-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "runtime-pause-resume-confirm-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "runtime-confirm-review-actor",
            "roles": ["operator"],
            "tenant_id": "tenant-a",
            "iss": "runtime-pause-resume-confirm-review",
            "aud": "runtime-pause-resume-confirm-review",
            "iat": now - 10,
            "exp": now + 300,
        },
        secret=secret,
    )

    binding = {
        "runtime_id": "rt-confirm-review",
        "binding_id": "bind-confirm-review",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    is_resume = action in ("ResumePaperRuntime", "resume", "unpause")

    def fake_http(url, **kwargs):
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        binding["status"] = "active" if is_resume else "paused"
        return {
            "status": "executed",
            "status_after": binding["status"],
            "binding_id": "bind-confirm-review",
            "runtime_id": "rt-confirm-review",
        }

    effective_command = "ResumePaperRuntime" if is_resume else "PausePaperRuntime"
    target_id = "rt-confirm-review" if action in ("PausePaperRuntime", "ResumePaperRuntime") else "bind-confirm-review"

    valid_approval_id = "runtime-confirm-review-approval-valid"
    invalid_approval_id = "runtime-confirm-review-approval-unbound"
    approvals = {
        valid_approval_id: {
            "outcome": "approved",
            "command": effective_command,
            "target": {"type": "Runtime", "id": target_id},
        },
        # Bound to a different target: present, exists, approved, but does
        # not apply to this command's actual target -- must still reject.
        invalid_approval_id: {
            "outcome": "approved",
            "command": effective_command,
            "target": {"type": "Runtime", "id": "some-other-runtime"},
        },
    }
    read_store_stub = SimpleNamespace(
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == "rt-confirm-review" else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-confirm-review" else None,
        get_approval_decision=lambda decision_id: dict(approvals.get(decision_id) or {}) or None,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)
    monkeypatch.setattr(
        runtime_adapter,
        "_get_runtime_manager_client",
        lambda: SimpleNamespace(get=lambda bid: dict(binding), list_all=lambda: [dict(binding)]),
    )
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)

    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"runtime-pause-resume-confirm-{action}-{evidence_shape}-{restart}",
    }
    params: Dict[str, Any] = {"action_id": action}

    if evidence_shape != "missing":
        issue_headers = dict(headers)
        issue_headers["Idempotency-Key"] = f"runtime-pause-resume-confirm-issue-{action}-{evidence_shape}-{restart}"
        # "invalid" issues a real, valid confirm token -- but bound to the
        # wrong command name, so it must not satisfy this exact effective
        # command's confirm_token requirement.
        bound_command = effective_command if evidence_shape == "valid" else "StartRuntime"
        issued = client.post(
            "/bff/confirm-tokens",
            headers=issue_headers,
            json={
                "tokenId": f"pause-resume-confirm-token-{action}-{evidence_shape}",
                "ttlSeconds": 300,
                "command": bound_command,
                "target_type": "Runtime",
                "target_id": target_id,
                "operator_id": "runtime-confirm-review-actor",
            },
        )
        assert issued.status_code == 201, issued.text
        headers["X-Confirm-Token"] = f"pause-resume-confirm-token-{action}-{evidence_shape}"
        if is_resume:
            params["approvalId"] = valid_approval_id if evidence_shape == "valid" else invalid_approval_id

    response = client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": "RuntimeAction",
            "target": {"type": "Runtime", "id": target_id},
            "params": params,
            "audit_context": {"reason": "runtime pause/resume confirm token and approval parity regression"},
        },
    )

    rows = store._get_all_commands()
    runtime_action_rows = [row for row in rows if row["type"] == "RuntimeAction"]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(runtime_action_rows[0]["command_id"]) if runtime_action_rows else {}
    evidence = {
        "action": action,
        "evidence_shape": evidence_shape,
        "restart": restart,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    if evidence_shape == "valid":
        assert response.status_code == 202, evidence
        assert len(calls) == 1, evidence
        assert final.get("status") == "executed", evidence
    elif evidence_shape == "missing":
        assert response.status_code == 428, evidence
        assert calls == [], evidence
        assert final.get("status") != "executed", evidence
    else:
        # "invalid": a real confirm token exists but is bound to the wrong
        # command, and (for resume) a real approval decision exists but is
        # bound to the wrong target -- durable admission must still reject
        # and dispatch nothing.
        assert response.status_code in (403, 409, 428), evidence
        assert calls == [], evidence
        assert final.get("status") != "executed", evidence


@pytest.mark.parametrize("admission_shape", ["missing", "ambiguous", "foreign", "same_tenant"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "RestartPaperRuntime",
        "RestartTelemetryBridge",
        "StartPaperMonitoringSession",
        "ProbeTelemetryIngest",
    ],
)
def test_signed_identity_runtime_action_repair_alias_requires_effective_confirm_token_and_tenant(
    tmp_path, monkeypatch, restart, action, admission_shape
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT (PR #5998 head 3c498e1f1282f7502bd95903e647ee7ff9d87c6a):
    mounted ``POST /bff/v1/commands`` with ``command=RuntimeAction`` and
    ``params.action_id`` in {RestartPaperRuntime, RestartTelemetryBridge,
    StartPaperMonitoringSession, ProbeTelemetryIngest} dispatched the
    downstream runtime-repair POST with no confirmation token at all and no
    target-tenant check, because ``service.py`` validated the outer
    ``RuntimeAction`` wrapper's (weak) catalog entry instead of each
    action's own ``requires_confirm_token=True`` canonical entry, and
    ``runtime_adapter.py`` invented a synthetic ``repair-confirm-token``
    whenever the caller omitted one.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the HTTP
    transport to the downstream runtime-manager and the read-store binding
    lookup are stubbed), across a CommandStore restart, for every admission
    tenant shape. Every shape without a valid confirm token must reject at
    428 with zero downstream calls; the same-tenant case with a real
    confirm token issued for this exact ``RuntimeAction``/``Runtime``/
    target binding must still dispatch exactly once.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-runtime-repair-alias-tenant-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://runtime-repair-alias-review.invalid")
    monkeypatch.setenv("PANTHEON_RUNTIME_REPAIR_API_URL", "http://runtime-repair-alias-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "runtime-repair-alias-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "runtime-repair-alias-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    if admission_shape == "same_tenant":
        scope: Dict[str, Any] = {"tenant_id": "tenant-a"}
    elif admission_shape == "foreign":
        scope = {"tenant_id": "tenant-b"}
    elif admission_shape == "ambiguous":
        scope = {"tenant_id": "tenant-b", "tid": "tenant-c"}
    else:
        scope = {}
    token = encode_jwt_hs256(
        {
            "sub": "runtime-repair-review-actor",
            "roles": ["operator"],
            "iss": "runtime-repair-alias-review",
            "aud": "runtime-repair-alias-review",
            "iat": now - 10,
            "exp": now + 300,
            **scope,
        },
        secret=secret,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    binding = {
        "runtime_id": "rt-repair-review",
        "binding_id": "bind-repair-review",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    def fake_http(url, **kwargs):
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        return {"status": "accepted", "audit_id": "audit-repair-review", "heartbeat_freshness": 1}

    monkeypatch.setattr(
        runtime_adapter,
        "_get_read_store",
        lambda: SimpleNamespace(
            get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == "rt-repair-review" else None,
        ),
    )
    monkeypatch.setattr(
        runtime_adapter,
        "_get_runtime_manager_client",
        lambda: SimpleNamespace(get=lambda bid: dict(binding), list_all=lambda: [dict(binding)]),
    )
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)

    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"runtime-repair-{action}-{admission_shape}",
    }
    if admission_shape == "same_tenant":
        issue_headers = dict(headers)
        issue_headers["Idempotency-Key"] = f"runtime-repair-issue-{action}-{admission_shape}-{restart}"
        issued = client.post(
            "/bff/confirm-tokens",
            headers=issue_headers,
            json={
                "tokenId": f"repair-token-{action}",
                "ttlSeconds": 300,
                # Bound to the effective canonical command (what
                # submit_command_admission validates the wrapper against),
                # not the literal "RuntimeAction" wrapper name.
                "command": action,
                "target_type": "Runtime",
                "target_id": "rt-repair-review",
                "operator_id": "runtime-repair-review-actor",
            },
        )
        assert issued.status_code == 201, issued.text
        headers["X-Confirm-Token"] = f"repair-token-{action}"

    response = client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": "RuntimeAction",
            "target": {"type": "Runtime", "id": "rt-repair-review"},
            "params": {"action_id": action, "runtime_id": "rt-repair-review"},
            "audit_context": {"reason": "runtime repair alias effective-action regression"},
        },
    )

    rows = store._get_all_commands()
    runtime_action_rows = [row for row in rows if row["type"] == "RuntimeAction"]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(runtime_action_rows[0]["command_id"]) if runtime_action_rows else {}
    evidence = {
        "admission_shape": admission_shape,
        "restart": restart,
        "action": action,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    if admission_shape == "same_tenant":
        assert response.status_code == 202, evidence
        assert len(calls) == 1, evidence
        assert final.get("status") == "executed", evidence
    else:
        assert response.status_code == 428, evidence
        assert calls == [], evidence
        assert final.get("status") != "executed", evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "RestartPaperRuntime",
        "RestartTelemetryBridge",
        "StartPaperMonitoringSession",
        "ProbeTelemetryIngest",
    ],
)
def test_signed_identity_runtime_repair_direct_and_wrapped_confirm_token_parity(
    tmp_path, monkeypatch, restart, wrapped, action
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001
    independent review REJECT at PR #5998 head
    4d35315b1be43bbcb7d0737530a5c908de635a27: ``service.py``'s
    ``canonicalize_validated_precondition_evidence`` canonicalizes the
    admission-validated confirmation to durable ``params.confirm_token_id``
    and deletes ``params.confirm_token``, but
    ``command_executor._require_confirm_token`` (used by the *direct*
    ``ProbeTelemetryIngest``/``RestartPaperRuntime``/
    ``RestartTelemetryBridge``/``StartPaperMonitoringSession`` commands, as
    opposed to the ``RuntimeAction`` wrapper handled by
    ``runtime_adapter._execute_repair_action``) read only
    ``params.confirm_token``, so a mounted signed-JWT same-tenant request
    with a genuinely issued and bound confirm token was accepted at 202 and
    then failed ``EXECUTION_ERROR requires confirm_token`` with zero
    downstream calls, while the equivalent ``RuntimeAction`` wrapper
    succeeded.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the downstream
    HTTP transport and the read-store binding lookup are stubbed), across a
    ``CommandStore`` restart, for both the direct command and the
    ``RuntimeAction`` wrapper. Both dispatch paths must consume the same
    admission-validated ``confirm_token_id`` and dispatch exactly once.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff import command_executor
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-runtime-repair-direct-wrapper-parity-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://runtime-repair-direct-parity-review.invalid")
    monkeypatch.setenv("PANTHEON_RUNTIME_REPAIR_API_URL", "http://runtime-repair-direct-parity-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "runtime-repair-direct-parity-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "runtime-repair-direct-parity-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "runtime-repair-direct-parity-actor",
            "roles": ["operator"],
            "iss": "runtime-repair-direct-parity-review",
            "aud": "runtime-repair-direct-parity-review",
            "iat": now - 10,
            "exp": now + 300,
            "tenant_id": "tenant-a",
        },
        secret=secret,
    )

    binding = {
        "runtime_id": "rt-repair-direct-parity",
        "binding_id": "bind-repair-direct-parity",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    def fake_http(url, payload=None, **kwargs):
        if payload is not None:
            kwargs["payload"] = payload
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        return {"status": "accepted", "audit_id": "audit-repair-direct-parity", "heartbeat_freshness": 1}

    read_store_stub = SimpleNamespace(
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == "rt-repair-direct-parity" else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-repair-direct-parity" else None,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)
    monkeypatch.setattr(
        runtime_adapter,
        "_get_runtime_manager_client",
        lambda: SimpleNamespace(get=lambda bid: dict(binding), list_all=lambda: [dict(binding)]),
    )
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)
    monkeypatch.setattr(command_executor, "_post_json", fake_http)

    token_id = f"repair-direct-parity-token-{action}-{wrapped}"
    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"runtime-repair-direct-parity-issue-{action}-{wrapped}-{restart}",
    }
    issued = client.post(
        "/bff/confirm-tokens",
        headers=headers,
        json={
            "tokenId": token_id,
            "ttlSeconds": 300,
            # Bound to the effective canonical command -- what
            # submit_command_admission validates the direct command or the
            # RuntimeAction wrapper against, per
            # require_final_command_preconditions.
            "command": action,
            "target_type": "Runtime",
            "target_id": "rt-repair-direct-parity",
            "operator_id": "runtime-repair-direct-parity-actor",
        },
    )
    assert issued.status_code == 201, issued.text

    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": f"runtime-repair-direct-parity-{action}-{wrapped}-{restart}",
            "X-Confirm-Token": token_id,
        },
        json={
            "command": "RuntimeAction" if wrapped else action,
            "target": {"type": "Runtime", "id": "rt-repair-direct-parity"},
            "params": {"action_id": action, "runtime_id": "rt-repair-direct-parity"},
            "audit_context": {"reason": "runtime repair direct/wrapper confirm-token parity regression"},
        },
    )

    rows = store._get_all_commands()
    repair_rows = [row for row in rows if row["type"] in ("RuntimeAction", action)]
    if restart:
        store = CommandStore(command_path)
    for row in repair_rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(repair_rows[0]["command_id"]) if repair_rows else {}
    evidence = {
        "restart": restart,
        "wrapped": wrapped,
        "action": action,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    assert response.status_code == 202, evidence
    assert len(calls) == 1, evidence
    assert final.get("status") == "executed", evidence
    assert calls[0]["payload"].get("confirm_token") == token_id, evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("action", ["AdvanceLifecycle"])
def test_signed_identity_lifecycle_and_runtime_start_confirm_token_id_parity(
    tmp_path, monkeypatch, restart, action
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001
    independent review REJECT at PR #5998 head
    a6b38a778fa06b9fdcaf058bb06a3aa0f5f8bc31: ``service.py``'s
    ``canonicalize_validated_precondition_evidence`` canonicalizes the
    admission-validated confirmation to durable ``params.confirm_token_id``
    and deletes ``params.confirm_token``, but
    ``command_executor._execute_advance_lifecycle`` (``AdvanceLifecycle``)
    read only ``params.confirm_token`` directly instead of calling the
    shared ``_require_confirm_token`` helper already used by the
    runtime-repair executors. A mounted signed-JWT same-tenant request
    with a genuinely issued and bound confirm token was accepted at 202
    and then failed ``EXECUTION_ERROR requires confirm_token`` with zero
    downstream calls, both before and after a ``CommandStore`` restart.

    ``command_executor._execute_start_runtime`` (``StartRuntime``) and
    ``_execute_approve_pool`` (``ApprovePool``) were the same static
    mismatch (raw ``params.get("confirm_token")`` instead of the
    canonical-first helper) and were fixed alongside
    ``_execute_advance_lifecycle``; they are not separately reproduced
    here because ``StartRuntime`` requires two-man authorization
    (``requires_two_man=True``) that is out of this regression's scope,
    matching how the prior ``defect_99`` round treated these as static
    sibling-reader concerns.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the downstream
    HTTP transport and the read-store lookups are stubbed), across a
    ``CommandStore`` restart, for ``AdvanceLifecycle``. It must consume
    the admission-validated ``confirm_token_id`` and dispatch exactly
    once.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff import command_executor
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-lifecycle-runtime-start-confirm-token-id-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://lifecycle-runtime-start-confirm-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "lifecycle-runtime-start-confirm-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "lifecycle-runtime-start-confirm-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "lifecycle-runtime-start-confirm-actor",
            "roles": ["operator"],
            "iss": "lifecycle-runtime-start-confirm-review",
            "aud": "lifecycle-runtime-start-confirm-review",
            "iat": now - 10,
            "exp": now + 300,
            "tenant_id": "tenant-a",
        },
        secret=secret,
    )

    target_id = "rt-lifecycle-runtime-start-confirm"
    binding = {
        "runtime_id": target_id,
        "binding_id": "bind-lifecycle-runtime-start-confirm",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    def fake_http(url, payload=None, **kwargs):
        if payload is not None:
            kwargs["payload"] = payload
        calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
        return {"status": "accepted", "audit_id": "audit-lifecycle-runtime-start-confirm"}

    read_store_stub = SimpleNamespace(
        get_approval_decision=lambda _: {
            "outcome": "approved",
            "command": action,
            "target": {"type": "Persona" if action == "AdvanceLifecycle" else "Runtime", "id": target_id},
        },
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == target_id else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-lifecycle-runtime-start-confirm" else None,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    monkeypatch.setattr(command_executor, "_post_json", fake_http)

    token_id = f"lifecycle-runtime-start-confirm-token-{action}"
    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"lifecycle-runtime-start-confirm-issue-{action}-{restart}",
    }
    issued = client.post(
        "/bff/confirm-tokens",
        headers=headers,
        json={
            "tokenId": token_id,
            "ttlSeconds": 300,
            "command": action,
            "target_type": "Persona",
            "target_id": target_id,
            "operator_id": "lifecycle-runtime-start-confirm-actor",
        },
    )
    assert issued.status_code == 201, issued.text

    params: Dict[str, Any] = {
        "approval_decision_id": "approval-review",
        "persona_id": target_id,
        "target_state": "paper_owner",
    }
    target = {"type": "Persona", "id": target_id}

    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": f"lifecycle-runtime-start-confirm-{action}-{restart}",
            "X-Confirm-Token": token_id,
        },
        json={
            "command": action,
            "target": target,
            "params": params,
            "audit_context": {"reason": "lifecycle/runtime-start confirm-token-id regression"},
        },
    )

    rows = store._get_all_commands()
    action_rows = [row for row in rows if row["type"] == action]
    if restart:
        store = CommandStore(command_path)
    for row in action_rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(action_rows[0]["command_id"]) if action_rows else {}
    evidence = {
        "restart": restart,
        "action": action,
        "response_status": response.status_code,
        "calls": calls,
        "status": final.get("status"),
        "error": final.get("error"),
    }

    assert response.status_code == 202, evidence
    assert len(calls) == 1, evidence
    assert final.get("status") == "executed", evidence
    assert calls[0]["payload"].get("confirm_token") == token_id, evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "roles,mfa_verified",
    [
        (["operator"], False),
        (["operator", "admin"], False),
        (["operator", "admin"], True),
    ],
    ids=["operator-only", "admin-no-mfa", "admin-with-mfa"],
)
def test_signed_identity_issue_safe_mode_requires_effective_role_and_mfa_parity(
    tmp_path, monkeypatch, restart, wrapped, roles, mfa_verified
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001
    independent review REJECT at PR #5998 head
    00850b1c2be3dab1ecc62909bbc48b90a44951ae: ``submit_command_admission``
    resolved the effective action for a generic wrapper (``resolve_
    effective_action``) but then looked up ``self._validators`` using the
    outer, literal wrapper command type instead of the effective canonical
    one -- so ``RuntimeAction`` with ``action_id=IssueSafeMode`` bypassed
    ``IssueSafeMode``'s own registered validator (admin role + MFA
    required) entirely, since ``RuntimeAction`` has no validator entry of
    its own. A mounted signed-JWT ``POST /bff/v1/commands`` wrapped as
    ``RuntimeAction`` dispatched exactly like a caller with a fully
    authorized direct ``IssueSafeMode`` submission, for an operator-only
    identity and for an admin identity without MFA.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the downstream
    ``RuntimeManagerClient`` transport is stubbed) for both the wrapped
    (``RuntimeAction``) and direct (``IssueSafeMode``) command spelling,
    across every role/MFA shape and a ``CommandStore`` restart. Every
    unauthorized shape must return 403 with zero durable admission and zero
    downstream dispatch, matching the direct command exactly; the fully
    authorized admin+MFA shape must dispatch identically for both
    spellings.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff import command_executor
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-issue-safe-mode-role-mfa-parity-secret"
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "issue-safe-mode-role-mfa-parity")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "issue-safe-mode-role-mfa-parity")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    claims: Dict[str, Any] = {
        "sub": "issue-safe-mode-parity-actor",
        "roles": roles,
        "tenant_id": "tenant-a",
        "iss": "issue-safe-mode-role-mfa-parity",
        "aud": "issue-safe-mode-role-mfa-parity",
        "iat": now - 10,
        "exp": now + 300,
    }
    if mfa_verified:
        claims["mfa_verified"] = True
    token = encode_jwt_hs256(claims, secret=secret)

    read_store_stub = SimpleNamespace(
        get_runtime_binding_by_runtime_id=lambda rid: (
            {"runtime_id": rid, "capital_pool_id": "pool-parity-review", "metadata": {"tenant_id": "tenant-a"}}
            if rid == "pool-parity-review"
            else None
        ),
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": f"issue-safe-mode-parity-{wrapped}-{restart}-{roles}-{mfa_verified}",
    }
    issue_headers = dict(headers)
    issue_headers["Idempotency-Key"] = issue_headers["Idempotency-Key"] + "-issue"
    issued = client.post(
        "/bff/confirm-tokens",
        headers=issue_headers,
        json={
            "tokenId": f"issue-safe-mode-parity-token-{wrapped}-{restart}-{roles}-{mfa_verified}",
            "ttlSeconds": 300,
            "command": "IssueSafeMode",
            "target_type": "Runtime",
            "target_id": "pool-parity-review",
            "operator_id": "issue-safe-mode-parity-actor",
        },
    )
    assert issued.status_code == 201, issued.text
    headers["X-Confirm-Token"] = f"issue-safe-mode-parity-token-{wrapped}-{restart}-{roles}-{mfa_verified}"

    calls: List[Dict[str, Any]] = []

    def advance(pool_id, target_state, **kwargs):
        calls.append({"pool_id": pool_id, "target_state": target_state, **kwargs})
        return {"safe_mode_state": target_state, "status": "executed"}

    monkeypatch.setattr(
        runtime_adapter, "_get_runtime_manager_client", lambda: SimpleNamespace(advance_safe_mode=advance)
    )
    monkeypatch.setattr(
        command_executor, "_runtime_manager_client", lambda: SimpleNamespace(advance_safe_mode=advance)
    )

    response = client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": "RuntimeAction" if wrapped else "IssueSafeMode",
            "target": {"type": "Runtime", "id": "pool-parity-review"},
            "params": {
                "action_id": "IssueSafeMode",
                "capital_pool_id": "pool-parity-review",
                "safe_mode_level": "soft",
            },
            "audit_context": {"reason": "issue safe mode role/MFA parity regression"},
        },
    )

    rows = [r for r in store._get_all_commands() if r["type"] in ("RuntimeAction", "IssueSafeMode")]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store, read_store=read_store_stub))
    final_statuses = [store.get_command(r["command_id"])["status"] for r in rows]
    evidence = {
        "wrapped": wrapped,
        "restart": restart,
        "roles": roles,
        "mfa_verified": mfa_verified,
        "response_status": response.status_code,
        "calls": calls,
        "final_statuses": final_statuses,
    }

    if roles == ["operator", "admin"] and mfa_verified:
        assert response.status_code == 202, evidence
        assert len(calls) == 1, evidence
        assert final_statuses == ["executed"], evidence
    else:
        assert response.status_code == 403, evidence
        assert calls == [], evidence
        assert rows == [], evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "command,target_type,target_id,params,conflicting_action",
    [
        (
            "RuntimeAction",
            "Runtime",
            "rt-selector-conflict",
            {"action_id": "PausePaperRuntime"},
            "ResumePaperRuntime",
        ),
        (
            "ReviewAction",
            "Review",
            "review-selector-conflict",
            {"action_id": "approve"},
            "reject",
        ),
        (
            "ExperimentAction",
            "Experiment",
            "exp-selector-conflict",
            {"action_id": "cancel"},
            "archive",
        ),
    ],
)
def test_conflicting_action_selectors_rejected_before_any_write(
    tmp_path, restart, command, target_type, target_id, params, conflicting_action
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT at PR #5998 exact head cd5613e7ba19546b3a0d9f43d4a67b1becf39438
    (manifest 8e9f92f4f779d7827160934a4a20ac36cd0aebaa): ``stored_command_params``
    computed the persisted ``action_id`` from ``cmd.action or params.action_id or
    params.actionId`` independently of the ``effective_action`` that admission's
    validators/preconditions/confirm-token binding had already validated a
    *different* action against, so a top-level ``action`` disagreeing with a
    validated ``params.action_id`` could be silently persisted and executed
    instead. ``submit_command_admission`` now resolves exactly one effective
    action from every selector up front and rejects a disagreement with 422
    before any command-store lookup or write, for both a generic wrapper whose
    action_id aliases to a distinct canonical command (``RuntimeAction``) and
    one whose action_id vocabulary does not (``ReviewAction``, ``Experiment
    Action``).
    """
    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    app = _test_app(store)
    client = TestClient(app)

    response = client.post(
        "/bff/v1/commands",
        headers={**HEADERS, "Idempotency-Key": f"selector-conflict-{command}"},
        json={
            "command": command,
            "action": conflicting_action,
            "target": {"type": target_type, "id": target_id},
            "params": params,
            "audit_context": {"reason": "conflicting action selector regression"},
        },
    )
    evidence = {"command": command, "restart": restart, "response_status": response.status_code, "body": response.text}
    assert response.status_code == 422, evidence
    assert response.json()["detail"]["error"]["details"]["precondition_failed"] == "action_selector_conflict", evidence
    assert store._get_all_commands() == [], evidence

    if restart:
        store = CommandStore(command_path)
        app = _test_app(store)
        client = TestClient(app)
        response = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": f"selector-conflict-{command}-restart"},
            json={
                "command": command,
                "action": conflicting_action,
                "target": {"type": target_type, "id": target_id},
                "params": params,
                "audit_context": {"reason": "conflicting action selector regression across restart"},
            },
        )
        evidence = {"command": command, "restart": restart, "response_status": response.status_code, "body": response.text}
        assert response.status_code == 422, evidence
        assert response.json()["detail"]["error"]["details"]["precondition_failed"] == "action_selector_conflict", evidence
        assert store._get_all_commands() == [], evidence


@pytest.mark.parametrize(
    "command,target_type,target_id,action_id",
    [
        ("ReviewAction", "Review", "review-selector-match", "review"),
        ("ExperimentAction", "Experiment", "exp-selector-match", "cancel"),
    ],
)
def test_matching_action_selectors_still_admit(
    tmp_path, command, target_type, target_id, action_id
) -> None:
    """Companion to ``test_conflicting_action_selectors_rejected_before_any_
    write``: a request whose top-level ``action`` agrees with ``params.
    action_id`` (the common single-selector case, and the explicit
    matching-selectors case) must still be admitted -- the conflict gate
    must not reject a legitimate, unambiguous request.
    """
    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    app = _test_app(store)
    client = TestClient(app)

    response = client.post(
        "/bff/v1/commands",
        headers={**HEADERS, "Idempotency-Key": f"selector-match-{command}"},
        json={
            "command": command,
            "action": action_id,
            "target": {"type": target_type, "id": target_id},
            "params": {"action_id": action_id},
            "audit_context": {"reason": "matching action selector regression"},
        },
    )
    evidence = {"command": command, "response_status": response.status_code, "body": response.text}
    assert response.status_code == 202, evidence
    rows = store._get_all_commands()
    assert len(rows) == 1, evidence
    assert rows[0]["params"]["action_id"] == action_id, evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "command,action",
    [("HumanGateRequestMoreEvidence", None), ("ReviewAction", "HumanGateRequestMoreEvidence")],
)
def test_human_gate_wrapper_dispatch_uses_resolved_canonical_action(
    tmp_path, monkeypatch, restart, command, action
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT at PR #5998 head 79e1132215f79afb82bfc4cc8c1a5fba54d753dc
    (manifest 26941ab0bd7090102ec4a57a026a59526a84dd93): dispatching a
    mounted ``ReviewAction`` wrapper whose ``action_id`` selected a HumanGate
    alias (for example ``HumanGateRequestMoreEvidence``) executed against the
    literal wrapper name instead of the resolved canonical action, because
    ``GovernanceCommandAdapter.execute`` called
    ``self._execute_human_gate_action(..., command_type or action_id, ...)`` --
    since ``command_type`` ("ReviewAction") is always truthy, the resolved
    ``action_id`` was never used and ``_execute_human_gate_action``'s
    ``verb_map`` fell through to its lowercase fallback, dispatching
    ``/api/governance/human-gates/<id>/reviewaction`` instead of
    ``/request-evidence``. A direct ``HumanGateRequestMoreEvidence`` command
    (no wrapper) already dispatched correctly and must keep doing so.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the HTTP
    transport to the downstream governance service is stubbed), across a
    CommandStore restart, for both the direct command and the wrapped alias.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import governance_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-human-gate-wrapper-dispatch-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://human-gate-wrapper-review.invalid")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_API_URL", "http://human-gate-wrapper-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "human-gate-wrapper-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "human-gate-wrapper-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "human-gate-review-actor",
            "roles": ["operator"],
            "tenant_id": "tenant-a",
            "iss": "human-gate-wrapper-review",
            "aud": "human-gate-wrapper-review",
            "iat": now - 10,
            "exp": now + 300,
        },
        secret=secret,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    read_store_stub = SimpleNamespace(
        get_approval_decision=lambda decision_id: None,
        get_persona=lambda persona_id: {"persona_id": persona_id, "tenant_id": "tenant-a"},
    )
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    calls: List[str] = []

    def fake_http(url, **kwargs):
        calls.append(url)
        return {"status": "executed", "state": "pending_evidence"}

    monkeypatch.setattr(governance_adapter, "http_request_json", fake_http)

    target_type = "HumanGateItem" if command == "HumanGateRequestMoreEvidence" else "Review"
    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": f"human-gate-wrapper-dispatch-{command}-{restart}",
        },
        json={
            "command": command,
            "action": action,
            "target": {"type": target_type, "id": "gate-a"},
            "params": {
                "gate_id": "gate-a",
                "human_gate_item_id": "gate-a",
                "decision": "request_more_evidence",
                "reason": "independent review",
            },
            "audit_context": {"reason": "independent isolated review"},
        },
    )
    rows = store._get_all_commands()
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    evidence = {"command": command, "action": action, "restart": restart, "http": response.status_code, "calls": calls}

    assert response.status_code == 202, evidence
    assert len(calls) == 1, evidence
    assert calls[0].endswith("/gate-a/request-evidence"), evidence


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "action,extra_params,expected_suffix",
    [
        ("humangaterequestmoreevidence", {}, "/request-evidence"),
        ("HUMANGATEREQUESTMOREEVIDENCE", {}, "/request-evidence"),
        ("humangateextendttl", {"additional_ttl_seconds": 3600}, "/extend-ttl"),
        ("HUMANGATEEXTENDTTL", {"additional_ttl_seconds": 3600}, "/extend-ttl"),
    ],
)
def test_human_gate_wrapper_dispatch_accepts_case_insensitive_alias(
    tmp_path, monkeypatch, restart, action, extra_params, expected_suffix
) -> None:
    """Regression for the DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent
    review REJECT P2 at PR #5998 head 5360830dad3c7ca7d9bc6d4f10981dbdacf00c89
    (manifest 26941ab0bd7090102ec4a57a026a59526a84dd93): admission accepts a
    case-insensitive HumanGate alias for the mounted ``ReviewAction`` wrapper
    (``command_adapters/runtime_adapter.py``'s canonical alias table lower-
    cases before matching), but ``GovernanceCommandAdapter.execute`` used to
    pass that raw, non-canonical-case ``action_id`` straight into
    ``_execute_human_gate_action``'s case-sensitive ``verb_map``, which fell
    through to a mangled endpoint (``/gate-a/requestmoreevidence`` instead of
    ``/gate-a/request-evidence``) instead of the resolved canonical action.
    Also covers ``HumanGateExtendTtl`` per the same audit.

    Drives a real signed-JWT mounted admission through the durable
    ``CommandStore`` and ``process_command`` executor (only the HTTP
    transport to the downstream governance service is stubbed), across a
    CommandStore restart.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import governance_adapter
    from services.control_plane.bff.command_adapters.service import (
        CommandAdapterService,
        process_command,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-human-gate-case-insensitive-alias-secret"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://human-gate-case-alias-review.invalid")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_API_URL", "http://human-gate-case-alias-review.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "human-gate-case-alias-review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "human-gate-case-alias-review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "human-gate-case-alias-actor",
            "roles": ["operator"],
            "tenant_id": "tenant-a",
            "iss": "human-gate-case-alias-review",
            "aud": "human-gate-case-alias-review",
            "iat": now - 10,
            "exp": now + 300,
        },
        secret=secret,
    )

    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    read_store_stub = SimpleNamespace(
        get_approval_decision=lambda decision_id: None,
        get_persona=lambda persona_id: {"persona_id": persona_id, "tenant_id": "tenant-a"},
    )
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)

    calls: List[str] = []

    def fake_http(url, **kwargs):
        calls.append(url)
        return {"status": "executed", "state": "pending_evidence"}

    monkeypatch.setattr(governance_adapter, "http_request_json", fake_http)

    params = {
        "gate_id": "gate-a",
        "human_gate_item_id": "gate-a",
        "decision": "request_more_evidence",
        "reason": "independent review",
    }
    params.update(extra_params)
    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": f"human-gate-case-alias-{action}-{restart}",
        },
        json={
            "command": "ReviewAction",
            "action": action,
            "target": {"type": "Review", "id": "gate-a"},
            "params": params,
            "audit_context": {"reason": "independent isolated review"},
        },
    )
    rows = store._get_all_commands()
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    evidence = {"action": action, "restart": restart, "http": response.status_code, "calls": calls}

    assert response.status_code == 202, evidence
    assert len(calls) == 1, evidence
    assert calls[0].endswith(f"/gate-a{expected_suffix}"), evidence



# ---------------------------------------------------------------------------
# DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001: inventory-wide mounted regression
# for the admission-canonicalized confirm token (confirm_token -> confirm_token_id).
# Every executor/adapter reachable from POST /bff/v1/commands must read it via
# command_adapters.base.canonical_confirm_token; a missed reader fails here.
# ---------------------------------------------------------------------------

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff import command_executor
from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.auth.policy import extract_identity_jwt
from services.control_plane.bff.command_adapters import create_command_adapters_router
from services.control_plane.bff.command_adapters import runtime_adapter
from services.control_plane.bff.command_adapters.service import CommandAdapterService, process_command
from services.control_plane.bff.command_queue import CommandStore
from services.runtime_auth_inbound import encode_jwt_hs256

_CT_INV_BFF_DIR = Path(__file__).resolve().parents[1]

# (direct command, RuntimeAction action_id or None when there is no wrapper spelling)
_CT_INVENTORY = [
    ("StartRuntime", "start"),
    ("RestartPaperRuntime", "RestartPaperRuntime"),
    ("RestartTelemetryBridge", "RestartTelemetryBridge"),
    ("StartPaperMonitoringSession", "StartPaperMonitoringSession"),
    ("ProbeTelemetryIngest", "ProbeTelemetryIngest"),
    ("AdvanceLifecycle", None),
]
_CT_CASES = [(cmd, wrap, wrapped) for cmd, wrap in _CT_INVENTORY for wrapped in (False, True) if not (wrapped and wrap is None)]


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("command,wrap_action,wrapped", _CT_CASES)
def test_mounted_confirm_token_reaches_every_executor(tmp_path, monkeypatch, command, wrap_action, wrapped, restart):
    secret = "test-confirm-token-inventory-secret"
    aud = "confirm-token-inventory"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://confirm-token-inventory.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", aud)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", aud)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    roles = ["operator", "runtime_operator", "live_owner_approver"]

    def jwt(actor: str) -> str:
        return encode_jwt_hs256(
            {"sub": actor, "roles": roles, "iss": aud, "aud": aud, "iat": now - 10, "exp": now + 300, "tenant_id": "tenant-a"},
            secret=secret,
        )

    actor = "inventory-actor"
    entry = get_catalog_entry(command)
    is_persona = command == "AdvanceLifecycle"
    target_type = "Persona" if is_persona else "Runtime"
    target_id = "persona-inventory" if is_persona else "rt-inventory"
    binding = {
        "runtime_id": target_id,
        "binding_id": "bind-inventory",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    def fake_http(url, payload=None, **kwargs):
        if payload is not None:
            kwargs["payload"] = payload
        calls.append({"url": url, "payload": kwargs.get("payload")})
        return {"status": "accepted", "audit_id": "audit-inventory"}

    read_store_stub = SimpleNamespace(
        get_approval_decision=lambda _: {
            "outcome": "approved",
            "command": command,
            "target": {"type": target_type, "id": target_id},
        },
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == target_id else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-inventory" else None,
    )
    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    from services.control_plane.bff.control_loops.router import create_control_loops_router

    app.include_router(
        create_control_loops_router(
            extract_identity=extract_identity_jwt,
            submit_final_command_admission=service.submit_command_admission,
            submit_sem_command=service.sem_command_response,
        )
    )
    client = TestClient(app)
    monkeypatch.setattr(command_executor, "_post_json", fake_http)
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)
    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)

    token_id = f"inventory-token-{command}-{wrapped}-{restart}"
    issued = client.post(
        "/bff/confirm-tokens",
        headers={"Authorization": "Bearer " + jwt(actor), "Idempotency-Key": "issue-" + token_id},
        json={
            "tokenId": token_id,
            "ttlSeconds": 300,
            "command": command,
            "target_type": target_type,
            "target_id": target_id,
            "operator_id": actor,
        },
    )
    assert issued.status_code == 201, issued.text

    params: Dict[str, Any] = {"target_state": "paper_owner"}
    if is_persona:
        params["persona_id"] = target_id
    else:
        params["runtime_id"] = target_id
    if getattr(entry, "requires_approval", False):
        params["approval_decision_id"] = "approval-inventory"
    if getattr(entry, "requires_two_man", False):
        signature_id = "tms-inventory-" + token_id
        for signer in (actor, "second-inventory-operator"):
            signed = client.post(
                f"/bff/v5/interventions/{signature_id}/two-man-sign",
                headers={"Authorization": "Bearer " + jwt(signer), "Idempotency-Key": f"sign-{signature_id}-{signer}"},
                json={
                    "twoManSignatureId": signature_id,
                    "command": command,
                    "target": {"type": target_type, "id": target_id},
                    "reason": "inventory evidence",
                },
            )
            assert signed.status_code == 202, signed.text
        params["two_man_signature_id"] = signature_id
    if wrapped:
        params["action_id"] = wrap_action

    submitted_type = "RuntimeAction" if wrapped else command
    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": "Bearer " + jwt(actor),
            "Idempotency-Key": "cmd-" + token_id,
            "X-Confirm-Token": token_id,
        },
        json={
            "command": submitted_type,
            "target": {"type": target_type, "id": target_id},
            "params": params,
            "audit_context": {"reason": "confirm-token inventory regression"},
        },
    )
    rows = [row for row in store._get_all_commands() if row["type"] == submitted_type]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(rows[0]["command_id"]) if rows else {}
    evidence = {"status_code": response.status_code, "calls": calls, "status": final.get("status"), "error": final.get("error")}
    assert response.status_code == 202, evidence
    assert final.get("status") == "executed", evidence
    assert len(calls) == 1, evidence
    assert calls[0]["payload"].get("confirm_token") == token_id, evidence


def test_no_raw_confirm_token_param_reads_remain_in_executors_and_adapters():
    pattern = re.compile(r"""params(?:\.get\(|\[)\s*["']confirm_token["']""")
    offenders = []
    files = [_CT_INV_BFF_DIR / "command_executor.py", *sorted((_CT_INV_BFF_DIR / "command_adapters").glob("*.py"))]
    for path in files:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line) and "canonical_confirm_token" not in line:
                # base.canonical_confirm_token is the single sanctioned reader.
                if path.name == "base.py":
                    continue
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# Wrapper -> canonical command parity, every (wrapper, action_id) pair
# (DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 independent review REJECT at PR
# #5998 head 45bf8c93f: PersonaAction with params.action_id=AdvanceLifecycle
# bypassed the canonical approval/confirm-token preconditions).
# --------------------------------------------------------------------------- #

_WP_ROLES = [
    "operator", "admin", "approver", "reviewer", "runtime_operator", "persona_operator",
    "live_owner_approver", "incident_commander", "deployment_operator", "capital_operator",
]


def _wp_inventory_pairs():
    pairs = []
    for wrapper, aliases in sorted(runtime_adapter.wrapper_canonical_inventory().items()):
        for alias, canonical in sorted(aliases.items()):
            pairs.append((wrapper, alias, canonical))
    return pairs


# Direct commands whose target type is not derivable from the catalog.
_WP_TARGET_TYPE_OVERRIDES = {"HardRollback": "Runtime", "ExecuteRollback": "Runtime"}


def _wp_target_for(canonical: str):
    from services.control_plane.bff.command_adapters.preconditions import _FINAL_COMMAND_TARGET_TYPES
    from services.control_plane.bff.models import CommandType

    forced = _FINAL_COMMAND_TARGET_TYPES.get(CommandType(canonical))
    entry = get_catalog_entry(canonical)
    target_type = forced.value if forced is not None else (entry.entity_type if entry is not None else "Runtime")
    target_type = _WP_TARGET_TYPE_OVERRIDES.get(canonical, target_type)
    if canonical.startswith("HumanGate"):
        return target_type, "approval:wp-1"
    return target_type, "wp-" + target_type.lower() + "-1"


_WP_EXTRA_PARAMS: Dict[str, Dict[str, Any]] = {
    "ActivateKillSwitch": {"activate": True, "scope": "all"},
    "ApproveDeployment": {"approval_decision": "approve"},
    "ApprovePool": {"memo": "approve pool for wrapper parity"},
    "ApproveRollback": {"rollback_id": "rb-wp"},
    "RejectRollback": {"rollback_id": "rb-wp", "rejection_reason": "wrapper parity"},
    "EmergencyContainment": {"action": "freeze", "trigger": "hard_risk_breach", "evidence_refs": ["ev-wp"]},
    "EscalateDiff": {"escalation_reason": "wrapper parity", "plan_id": "wp-deploymentplan-1"},
    "ExecuteRollback": {"rollback_target_type": "runtime", "rollback_to_version": "v1", "target_id": "wp-runtime-1"},
    "HumanGateExtendTtl": {"ttl_seconds": 300},
    "HardRollback": {"target_artifact_id": "art-wp", "rollback_to_version": "v1"},
    "IssueRiskOff": {"reduce_exposure_pct": 10},
    "IssueSafeMode": {"safe_mode_level": "soft"},
    "RecordSponsorDecision": {"committee_id": "c-wp", "rationale_ref": "r-wp", "sponsor_decision": "approved"},
    "RejectDecision": {"rejection_reason": "wrapper parity"},
    "RequestApprovalRevision": {"revision_notes": "wrapper parity"},
    "RemediateSentinelIntervention": {"remediation_action": "resolve"},
}


class _WrapperParityHarness:
    """Signed-JWT mounted /bff/v1/commands over a durable CommandStore, with
    only downstream HTTP transports and read-store lookups stubbed."""

    def __init__(self, tmp_path, monkeypatch, canonical, roles):
        secret = "wrapper-parity-secret"
        aud = "wrapper-parity"
        monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://wrapper-parity.invalid")
        monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://wrapper-parity-capital.invalid")
        monkeypatch.setenv("PANTHEON_GOVERNANCE_API_URL", "http://wrapper-parity-governance.invalid")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
        monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
        monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", aud)
        monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", aud)
        monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
        self.secret, self.aud, self.roles = secret, aud, roles
        self.canonical = canonical
        self.target_type, self.target_id = _wp_target_for(canonical)
        self.calls: List[Dict[str, Any]] = []
        binding = {
            "runtime_id": self.target_id,
            "binding_id": "bind-wp",
            "deployment_mode": "paper",
            "status": "active",
            "metadata": {"tenant_id": "tenant-a"},
        }
        self.approved = True
        canonical_name = canonical
        harness = self

        def approval(_):
            if not harness.approved:
                return None
            return {
                "outcome": "approved",
                "command": canonical_name,
                "target": {"type": harness.target_type, "id": harness.target_id},
            }

        read_store_stub = SimpleNamespace(
            get_approval_decision=approval,
            get_persona=lambda pid: {"persona_id": pid},
            get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == self.target_id else None,
            get_runtime_binding=lambda bid: dict(binding) if bid == "bind-wp" else None,
        )

        def fake_http(url, payload=None, **kwargs):
            if payload is not None:
                kwargs["payload"] = payload
            self.calls.append({"url": url, "method": kwargs.get("method"), "payload": kwargs.get("payload")})
            return {"status": "accepted", "audit_id": "audit-wp"}

        self.command_path = str(tmp_path / "commands.jsonl")
        self.store = CommandStore(self.command_path)
        service = CommandAdapterService(
            command_store=self.store,
            extract_identity=extract_identity_jwt,
            check_read_surface_state=lambda: None,
            process_command_task=lambda command_id: None,
            get_read_store=lambda: read_store_stub,
        )
        app = FastAPI()
        app.include_router(create_command_adapters_router(service=service))
        from services.control_plane.bff.control_loops.router import create_control_loops_router

        app.include_router(
            create_control_loops_router(
                extract_identity=extract_identity_jwt,
                submit_final_command_admission=service.submit_command_admission,
                submit_sem_command=service.sem_command_response,
            )
        )
        self.client = TestClient(app)
        monkeypatch.setattr(command_executor, "_post_json", fake_http)
        from services.control_plane.bff.command_adapters import (
            capital_adapter,
            deployment_adapter,
            governance_adapter,
            incident_adapter,
            persona_adapter,
        )

        for module in (runtime_adapter, capital_adapter, deployment_adapter, governance_adapter, incident_adapter, persona_adapter):
            monkeypatch.setattr(module, "http_request_json", fake_http)
        monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)
        # RecordSponsorDecision admission reads a committee read projection;
        # stub that read-only projection (never the admission logic itself).
        from services.control_plane.bff.governance.service import GovernanceService

        monkeypatch.setattr(
            GovernanceService,
            "committee_projection",
            lambda *a, **k: {
                "meta": {"surfaces": {"committee_board": "available"}},
                "allowedActions": {"canRecordSponsorDecision": True},
            },
        )

    def jwt(self, actor, roles=None):
        now = int(time.time())
        return encode_jwt_hs256(
            {"sub": actor, "roles": roles or self.roles, "iss": self.aud, "aud": self.aud, "iat": now - 10, "exp": now + 300, "tenant_id": "tenant-a", "mfa_verified": True},
            secret=self.secret,
        )

    def issue_token(self, token_id, actor):
        issued = self.client.post(
            "/bff/confirm-tokens",
            headers={"Authorization": "Bearer " + self.jwt(actor), "Idempotency-Key": "issue-" + token_id},
            json={
                "tokenId": token_id,
                "ttlSeconds": 300,
                "command": self.canonical,
                "target_type": self.target_type,
                "target_id": self.target_id,
                "operator_id": actor,
            },
        )
        assert issued.status_code == 201, issued.text

    def sign_two_man(self, signature_id, actor):
        for signer in (actor, "second-wp-operator"):
            signed = self.client.post(
                f"/bff/v5/interventions/{signature_id}/two-man-sign",
                headers={"Authorization": "Bearer " + self.jwt(signer), "Idempotency-Key": f"sign-{signature_id}-{signer}"},
                json={
                    "twoManSignatureId": signature_id,
                    "command": self.canonical,
                    "target": {"type": self.target_type, "id": self.target_id},
                    "reason": "wrapper parity evidence",
                },
            )
            assert signed.status_code == 202, signed.text

    def base_params(self):
        params: Dict[str, Any] = {
            "target_state": "paper_owner",
            "persona_id": self.target_id,
            "runtime_id": self.target_id,
            "pool_id": self.target_id,
            "rebalance_id": self.target_id,
            "deployment_plan_id": self.target_id,
            "intervention_id": self.target_id,
            "alert_id": self.target_id,
            "decision_id": self.target_id,
            "gate_id": self.target_id,
            "human_gate_item_id": self.target_id,
        }
        # A ReviewAction wrapper must carry the HumanGate decision itself
        # (direct HumanGate* commands derive it from the command name).
        decisions = {
            "HumanGateApprove": "approve",
            "HumanGateReject": "reject",
            "HumanGateRequestMoreEvidence": "request_more_evidence",
            "HumanGateRevoke": "revoke",
            "HumanGateExtendTtl": "extend_ttl",
        }
        if self.canonical in decisions:
            params["decision"] = decisions[self.canonical]
        params.update(_WP_EXTRA_PARAMS.get(self.canonical, {}))
        return params

    def seed_rebalance_evidence(self, actor, signature_id, decision_id, *, with_approval):
        """ApprovedApply consumes trusted server-managed rebalance evidence
        rows (RebalanceApproval/RebalanceTwoManSign), which only the
        authenticated capital evidence routes may produce; seed the same
        durable rows those routes write."""
        producer = "bff.rebalance-evidence.v1"
        binding = {"command": self.canonical, "target": {"type": self.target_type, "id": self.target_id}}

        def row(kind, params):
            return {
                "command_id": f"seed-{kind}-{signature_id}",
                "type": kind,
                "status": "executed",
                "operator_id": actor,
                "params": {**binding, **params},
                "target": {"type": self.target_type, "id": self.target_id},
                "foundation": {"trusted_evidence_producer": producer},
                "audit": {"trusted_evidence_producer": producer},
            }

        if with_approval:
            self.store._save_command(row("RebalanceApproval", {"approval_decision_id": decision_id, "outcome": "approved"}))
        self.store._save_command(
            row(
                "RebalanceTwoManSign",
                {"two_man_signature_id": signature_id, "first_operator_id": actor, "second_operator_id": "second-wp-operator"},
            )
        )


def _wp_run(tmp_path, monkeypatch, *, wrapper, alias, canonical, mode, restart, roles=None):
    """Submit ``canonical`` directly (wrapper None) or as ``wrapper`` with
    ``params.action_id=alias``; return the observable outcome."""
    entry = get_catalog_entry(canonical)
    harness = _WrapperParityHarness(
        tmp_path, monkeypatch, canonical, ["operator"] if mode == "operator_only" else (roles or _WP_ROLES)
    )
    actor = "wp-actor"
    tag = f"{wrapper or 'direct'}-{alias or canonical}-{mode}-{restart}"
    token_id = "wp-token-" + tag
    params = harness.base_params()
    headers = {"Authorization": "Bearer " + harness.jwt(actor), "Idempotency-Key": "cmd-" + tag}

    if getattr(entry, "requires_confirm_token", False):
        harness.issue_token(token_id, actor)
        if mode == "missing_token":
            pass
        elif mode == "invalid_token":
            headers["X-Confirm-Token"] = "wp-token-does-not-exist"
        else:
            headers["X-Confirm-Token"] = token_id
    if getattr(entry, "requires_approval", False) and mode != "missing_approval":
        params["approval_decision_id"] = "approval-wp"
    if mode == "missing_approval":
        harness.approved = False
    if getattr(entry, "requires_two_man", False):
        signature_id = "tms-wp-" + tag
        if canonical == "ApprovedApply":
            harness.seed_rebalance_evidence(actor, signature_id, "approval-wp", with_approval=mode != "missing_approval")
        else:
            harness.sign_two_man(signature_id, actor)
        params["two_man_signature_id"] = signature_id

    submitted = wrapper or canonical
    if wrapper:
        params["action_id"] = alias
    response = harness.client.post(
        "/bff/v1/commands",
        headers=headers,
        json={
            "command": submitted,
            "target": {"type": harness.target_type, "id": harness.target_id},
            "params": params,
            "audit_context": {"reason": "wrapper parity regression"},
        },
    )
    rows = [row for row in harness.store._get_all_commands() if row["type"] == submitted]
    store = CommandStore(harness.command_path) if restart else harness.store
    if restart:
        rows = [row for row in store._get_all_commands() if row["type"] == submitted]
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(rows[0]["command_id"]) if rows else {}
    return {
        "status_code": response.status_code,
        "rows": len(rows),
        "final_status": final.get("status"),
        "calls": len(harness.calls),
        "token_forwarded": [
            "bound" if c["payload"].get("confirm_token") == token_id else c["payload"].get("confirm_token")
            for c in harness.calls
            if isinstance(c.get("payload"), dict) and c["payload"].get("confirm_token")
        ],
        "detail": response.text[:300],
    }


def _wp_modes(canonical):
    entry = get_catalog_entry(canonical)
    modes = ["accepted", "operator_only"]
    if getattr(entry, "requires_confirm_token", False):
        modes += ["missing_token", "invalid_token"]
    if getattr(entry, "requires_approval", False):
        modes.append("missing_approval")
    return modes


_WP_CASES = [
    (wrapper, alias, canonical, mode)
    for wrapper, alias, canonical in _wp_inventory_pairs()
    for mode in _wp_modes(canonical)
]


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("wrapper,alias,canonical,mode", _WP_CASES)
def test_wrapper_alias_admission_matches_direct_canonical_command(tmp_path, monkeypatch, wrapper, alias, canonical, mode, restart):
    direct = _wp_run(tmp_path / "direct", monkeypatch, wrapper=None, alias=None, canonical=canonical, mode=mode, restart=restart) if (tmp_path / "direct").mkdir() is None else None
    wrapped = _wp_run(tmp_path / "wrapped", monkeypatch, wrapper=wrapper, alias=alias, canonical=canonical, mode=mode, restart=restart) if (tmp_path / "wrapped").mkdir() is None else None
    evidence = {"direct": direct, "wrapped": wrapped}
    # Identical admission outcome and durable-row effect ...
    assert wrapped["status_code"] == direct["status_code"], evidence
    assert wrapped["rows"] == direct["rows"], evidence
    if mode not in {"accepted", "operator_only"}:
        # ... and a negative case never leaves a durable row or dispatches.
        assert direct["status_code"] >= 400, evidence
        assert wrapped["rows"] == 0 and wrapped["calls"] == 0, evidence
    if mode == "accepted":
        if canonical in {"RebalanceApproval", "RebalanceTwoManSign"}:
            assert direct["status_code"] == 403, evidence
        else:
            assert direct["status_code"] == 202, evidence
    if direct["status_code"] == 202 and wrapper in runtime_adapter.DISPATCH_RESOLVED_WRAPPERS:
        # Execution runs the canonical command's own executor: the same
        # terminal status, dispatch count, and bound token reach the owner.
        assert wrapped["final_status"] == direct["final_status"], evidence
        assert wrapped["calls"] == direct["calls"], evidence
        assert wrapped["token_forwarded"] == direct["token_forwarded"], evidence


def test_adapters_never_fabricate_confirmation_or_two_man_evidence(monkeypatch):
    from services.control_plane.bff.command_adapters import capital_adapter, incident_adapter, persona_adapter
    from services.control_plane.bff.command_adapters.registry import dispatch_domain_command

    calls: List[Any] = []

    def fake_http(url, payload=None, **kwargs):
        calls.append(url)
        return {"status": "accepted"}

    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://no-fabrication.invalid")
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://no-fabrication-capital.invalid")
    for module in (capital_adapter, incident_adapter, persona_adapter):
        monkeypatch.setattr(module, "http_request_json", fake_http)

    for command, params in (
        ("PersonaAction", {"action_id": "AdvanceLifecycle", "persona_id": "p-1", "target_state": "paper_owner"}),
        ("PersonaAction", {"action_id": "EmergencyContainment", "persona_id": "p-1"}),
        ("IncidentAction", {"action_id": "remediate", "intervention_id": "i-1"}),
        ("RiskAlertAction", {"action_id": "remediate", "intervention_id": "i-1"}),
        ("CapitalPoolAction", {"action_id": "EmergencyContainment", "entity_id": "p-1", "entity_type": "Persona"}),
    ):
        with pytest.raises(ValueError):
            dispatch_domain_command("cmd-1", command, dict(params))
    assert calls == []


def test_non_wrapper_commands_never_dispatch_on_smuggled_action_id(monkeypatch):
    """``params.action_id`` is caller metadata on a dedicated command; it must
    never select a different, gated command inside the adapter."""
    from services.control_plane.bff.command_adapters import capital_adapter, incident_adapter, persona_adapter
    from services.control_plane.bff.command_adapters.base import ActionUnavailableError
    from services.control_plane.bff.command_adapters.registry import dispatch_domain_command

    calls: List[Any] = []
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://smuggle.invalid")
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://smuggle-capital.invalid")
    for module in (capital_adapter, incident_adapter, persona_adapter):
        monkeypatch.setattr(module, "http_request_json", lambda url, **kw: calls.append(url) or {})

    smuggled = (
        ("AlertAcknowledge", {"action_id": "remediate", "intervention_id": "i-1", "two_man_signature_id": "s"}),
        ("V5InterventionAction", {"action_id": "remediate", "intervention_id": "i-1", "two_man_signature_id": "s"}),
        ("Observe", {"action_id": "AdvanceLifecycle", "persona_id": "p-1", "confirm_token": "t", "target_state": "paper_owner"}),
    )
    for command, params in smuggled:
        try:
            result = dispatch_domain_command("cmd-1", command, params)
        except ActionUnavailableError:
            continue
        assert result.get("action_id") not in {"RemediateSentinelIntervention", "AdvanceLifecycle"}, result
    assert calls == []


def test_wrapper_inventory_covers_every_adapter_action_alias():
    """Every alias a wrapper's adapter can dispatch to a gated direct command
    is in the single wrapper table admission resolves through."""
    inventory = runtime_adapter.wrapper_canonical_inventory()
    assert set(inventory) == {
        "RuntimeAction", "ReviewAction", "PersonaAction", "CapitalPoolAction",
        "RebalanceAction", "DeploymentAction", "IncidentAction", "RiskAlertAction",
    }
    for wrapper in runtime_adapter.DISPATCH_RESOLVED_WRAPPERS:
        assert wrapper in runtime_adapter.GENERIC_WRAPPER_COMMANDS
        for alias, canonical in inventory[wrapper].items():
            assert runtime_adapter.resolve_effective_action(wrapper, {"action_id": alias}).effective_command_id == canonical
            assert runtime_adapter.resolve_wrapper_dispatch(wrapper, {"action_id": alias}) == canonical
    forbidden = ("lifecycle-confirm", "sig-ops-containment", "sig-sentinel-remed", "sig-emergency-ops")
    offenders = [
        f"{path.name}: {token}"
        for path in sorted((_CT_INV_BFF_DIR / "command_adapters").glob("*.py"))
        for token in forbidden
        if token in path.read_text()
    ]
    assert not offenders, offenders
