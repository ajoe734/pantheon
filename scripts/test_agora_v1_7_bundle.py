from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
OPENAPI_PATH = ROOT / "services/control-plane/openapi/agora_v1_7.openapi.yaml"
SCHEMA_PATH = AGORA_SPECS / "v8/pint_ooda_live_routes.schema.json"
MANIFEST_PATH = AGORA_SPECS / "v8/capability_manifest_v1_7.json"

EXPECTED_ROUTES = {
    "POST /bff/agora/interactions/context:resolve",
    "POST /bff/agora/interactions/participants:eligible",
    "POST /bff/agora/interactions",
    "POST /bff/agora/proposals",
    "GET /bff/agora/proposals/{proposal_id}",
    "GET /bff/agora/proposals/{proposal_id}/revisions",
    "POST /bff/agora/proposals/{proposal_id}/actions",
    "GET /bff/ooda/packets",
    "GET /bff/ooda/packets/{packet_id}",
    "GET /bff/strategies/{strategy_id}/ooda",
    "GET /bff/runtimes/{runtime_id}/ooda",
    "GET /bff/evolution-programs/{program_id}/ooda",
}

ROUTE_SOURCE_FILES = (
    ROOT / "services/control-plane/bff/agora/interaction/router.py",
    ROOT / "services/control-plane/bff/agora/governance/router.py",
    ROOT / "services/control-plane/bff/main.py",
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


def test_v1_7_bundle_extends_frozen_v1_6_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_7.json").read_text(encoding="utf-8"))

    assert _sha256(AGORA_SPECS / "bundle_index.v1_6.json") == (
        "13f1297b75477e691d47229cb35d77c031a9104c63a087298356a861ed2e2ee0"
    )
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_6.json",
        "bundle_version": "1.6",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_6.json"),
    }


def test_v1_7_bundle_hashes_match_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_7.json").read_text(encoding="utf-8"))

    for relative_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / relative_path
        assert path.exists(), relative_path
        assert _sha256(path) == expected_hash
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_7.openapi.yaml",
        "sha256": _sha256(OPENAPI_PATH),
    }


def test_required_definition_checksums_are_locked() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_7.json").read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(bundle["required_definition_checksums"]) == set(schema["definitions"])
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

    assert spec["info"]["x-extends-contract"] == "services/control-plane/specs/agora/bundle_index.v1_6.json"
    assert spec["info"]["x-capability-manifest"] == (
        "services/control-plane/specs/agora/v8/capability_manifest_v1_7.json"
    )
    assert spec["components"]["schemas"]["SubmitInteractionRequest"]["$ref"].endswith(
        "pint_ooda_live_routes.schema.json#/definitions/SubmitInteractionRequest"
    )
    assert spec["components"]["schemas"]["ProposalActionRequest"]["$ref"].endswith(
        "pint_ooda_live_routes.schema.json#/definitions/ProposalActionRequest"
    )


def test_interaction_schema_requires_immutable_strategy_version() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(
        schema["definitions"]["ContextRef"],
        format_checker=FormatChecker(),
    )

    assert list(validator.iter_errors({"type": "strategy", "id": "strategy-1", "version_id": "v1"})) == []
    assert list(validator.iter_errors({"type": "strategy", "id": "strategy-1"}))


def test_proposal_schema_requires_governance_and_rollback_metadata() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(
        schema["definitions"]["ProposalCreateRequest"],
        format_checker=FormatChecker(),
    )
    payload = {
        "proposal_type": "strategy_patch",
        "target_kind": "strategy",
        "target_id": "strategy-1",
        "target_version": "v1",
        "current_value": {"risk": 0.1},
        "proposed_value": {"risk": 0.08},
        "rationale": "reduce drawdown",
        "evidence_refs": ["evidence-1"],
        "confidence": 0.8,
        "expected_benefit": "lower drawdown",
        "adverse_scenarios": ["missed upside"],
        "environment_ceiling": "paper",
        "expires_at": "2027-07-13T00:00:00Z",
        "validation_plan": {"backtest": "bt-1"},
        "rollback_trigger": "drawdown worsens",
        "rollback_action": "restore v1",
        "required_permissions": ["strategy.review"],
        "required_reviewers": ["risk"],
        "human_gate": True,
    }

    assert list(validator.iter_errors(payload)) == []
    payload.pop("rollback_action")
    assert list(validator.iter_errors(payload))


def test_manifest_preserves_no_execution_authority_boundary() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["safety_boundary"] == {
        "direct_order_route": False,
        "capital_binding": False,
        "runtime_binding_write": False,
        "memory_mutation": False,
        "persona_self_approval": False,
        "interaction_execution_authority": "none",
        "ooda_routes_read_only": True,
    }
    assert {capability["implementation_status"] for capability in manifest["capabilities"]} == {
        "live",
        "live_read_only",
    }
