from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
OPENAPI_PATH = ROOT / "services/control-plane/openapi/agora_v1_8.openapi.yaml"
SCHEMA_PATH = AGORA_SPECS / "v9/workshop_live_operations.schema.json"
MANIFEST_PATH = AGORA_SPECS / "v9/capability_manifest_v1_8.json"

EXPECTED_ROUTES = {
    "GET /bff/agora/workshops/{workshop_id}/versions",
    "POST /bff/agora/workshops/{workshop_id}/versions",
    "POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
    "POST /bff/agora/workshops/{workshop_id}/research-runs",
    "POST /bff/agora/workshops/{workshop_id}/consultations",
    "POST /bff/agora/workshops/{workshop_id}/conclude",
}

REQUIRED_DEFINITIONS = {
    "WorkshopVersionCreateRequest",
    "WorkshopResearchRunRequest",
    "WorkshopConsultationRequest",
    "WorkshopConcludeRequest",
    "WorkshopCommandReceipt",
    "WorkshopLiveOperationEnvelope",
    "NoDirectActionProof",
}

ROUTE_SOURCE_FILES = (
    ROOT / "services/control-plane/bff/agora/strategy_workshop/router.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _declared_routes(path: Path) -> set[str]:
    routes: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not decorator.args:
                continue
            route_arg = decorator.args[0]
            if isinstance(route_arg, ast.Constant) and isinstance(route_arg.value, str):
                routes.add(f"{method} {route_arg.value}")
    return routes


def _openapi_routes() -> set[str]:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    return {
        f"{method.upper()} {path}"
        for path, path_item in spec["paths"].items()
        for method in path_item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }


def _manifest_routes() -> set[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        route
        for capability in manifest["capabilities"]
        for route in capability["routes"]
    }


def test_v1_8_bundle_extends_frozen_v1_7_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_8.json").read_text(encoding="utf-8"))

    assert bundle["bundle_version"] == "1.8"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_7.json",
        "bundle_version": "1.7",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_7.json"),
    }


def test_v1_8_bundle_hashes_match_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_8.json").read_text(encoding="utf-8"))

    for relative_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / relative_path
        assert path.exists(), relative_path
        assert _sha256(path) == expected_hash
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_8.openapi.yaml",
        "sha256": _sha256(OPENAPI_PATH),
    }


def test_required_definition_checksums_are_locked() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_8.json").read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert set(bundle["required_definition_checksums"]) == REQUIRED_DEFINITIONS
    assert set(bundle["required_definition_checksums"]) == set(manifest["required_definition_checksums"])
    for name, expected_hash in bundle["required_definition_checksums"].items():
        actual_hash = hashlib.sha256(_stable_json(schema["definitions"][name]).encode()).hexdigest()
        assert actual_hash == expected_hash, name


def test_openapi_manifest_and_live_decorators_have_exact_route_parity() -> None:
    source_routes: set[str] = set()
    for source_path in ROUTE_SOURCE_FILES:
        source_routes.update(_declared_routes(source_path))

    assert _openapi_routes() == EXPECTED_ROUTES
    assert _manifest_routes() == EXPECTED_ROUTES
    assert EXPECTED_ROUTES <= source_routes


def test_openapi_wires_additive_manifest_and_payload_schemas() -> None:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))

    assert spec["info"]["x-extends-contract"] == "services/control-plane/specs/agora/bundle_index.v1_7.json"
    assert spec["info"]["x-capability-manifest"] == (
        "services/control-plane/specs/agora/v9/capability_manifest_v1_8.json"
    )
    assert spec["components"]["schemas"]["WorkshopVersionCreateRequest"]["$ref"].endswith(
        "workshop_live_operations.schema.json#/definitions/WorkshopVersionCreateRequest"
    )
    assert spec["components"]["schemas"]["WorkshopLiveOperationEnvelope"]["$ref"].endswith(
        "workshop_live_operations.schema.json#/definitions/WorkshopLiveOperationEnvelope"
    )


def test_write_requests_require_authority_and_safe_mode_boundaries() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(
        schema["definitions"]["WorkshopResearchRunRequest"],
        format_checker=FormatChecker(),
    )

    valid = {
        "research_context": "research-only validation",
        "approval_decision_id": "approval-1",
        "adapter": "handoff_only",
        "requested_mode": "handoff_only",
        "dispatch_mode": "handoff_only",
    }
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({**valid, "requested_mode": "live"}))
    assert list(validator.iter_errors({k: v for k, v in valid.items() if k != "approval_decision_id"}))


def test_no_direct_action_proof_is_fail_closed_false_only() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema["definitions"]["NoDirectActionProof"])

    assert list(
        validator.iter_errors(
            {
                "deployment_triggered": False,
                "order_submitted": False,
                "live_capital_changed": False,
            }
        )
    ) == []
    assert list(
        validator.iter_errors(
            {
                "deployment_triggered": True,
                "order_submitted": False,
                "live_capital_changed": False,
            }
        )
    )
