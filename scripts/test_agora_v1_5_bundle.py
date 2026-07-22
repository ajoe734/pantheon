from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
OPENAPI = ROOT / "services/control-plane/openapi"

REQUIRED_DYNAMIC_ROUTES = {
    "/bff/agora/strategies/{strategy_id}/trading-room/proposals",
    "/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}",
    "/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept",
    "/bff/agora/trading-room/workspaces/{workspace_id}",
    "/bff/agora/trading-room/workspaces/{workspace_id}/layout",
    "/bff/agora/trading-room/workspaces/{workspace_id}/views",
    "/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}",
    "/bff/agora/trading-room/workspaces/{workspace_id}/widgets",
    "/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}",
    "/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals",
    "/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept",
    "/bff/agora/trading-room/workspaces/{workspace_id}/versions",
    "/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback",
}

REQUIRED_DEFINITIONS = {
    "TradingRoomWorkspaceProposal",
    "TradingRoomWidgetSpec",
    "WidgetRevisionProposal",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_v1_5_bundle_extends_exact_v1_4_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_5.json").read_text(encoding="utf-8"))

    assert bundle["bundle_version"] == "1.5"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_4.json",
        "bundle_version": "1.4",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_4.json"),
    }


def test_v1_5_bundle_file_hashes_match_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_5.json").read_text(encoding="utf-8"))

    for rel_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / rel_path
        assert path.exists(), rel_path
        assert _sha256(path) == expected_hash

    openapi = bundle["openapi"]
    assert openapi == {
        "path": "services/control-plane/openapi/agora_v1_5.openapi.yaml",
        "sha256": _sha256(OPENAPI / "agora_v1_5.openapi.yaml"),
    }


def test_v1_5_required_dynamic_definition_hashes_are_locked() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_5.json").read_text(encoding="utf-8"))
    schema = json.loads((AGORA_SPECS / "trading_room_workspace.schema.json").read_text(encoding="utf-8"))

    assert set(bundle["required_definition_checksums"]) == REQUIRED_DEFINITIONS
    for name in REQUIRED_DEFINITIONS:
        definition = schema["definitions"][name]
        actual = hashlib.sha256(_stable_json(definition).encode("utf-8")).hexdigest()
        assert bundle["required_definition_checksums"][name] == actual


def test_v1_5_openapi_exposes_dynamic_trading_room_route_family() -> None:
    spec = yaml.safe_load((OPENAPI / "agora_v1_5.openapi.yaml").read_text(encoding="utf-8"))

    assert spec["info"]["title"] == "Pantheon Agora API v1.5 Extension"
    assert spec["info"]["x-extends-contract"] == "services/control-plane/openapi/agora_v1_4.openapi.yaml"
    assert spec["info"]["x-capability-manifest"] == "services/control-plane/specs/agora/v6/capability_manifest_v1_5.json"
    assert REQUIRED_DYNAMIC_ROUTES <= set(spec["paths"])

    schemas = spec["components"]["schemas"]
    assert schemas["TradingRoomWorkspaceProposal"]["$ref"].endswith(
        "trading_room_workspace.schema.json#/definitions/TradingRoomWorkspaceProposal"
    )
    assert schemas["TradingRoomWidgetSpec"]["$ref"].endswith(
        "trading_room_workspace.schema.json#/definitions/TradingRoomWidgetSpec"
    )
    assert schemas["WidgetRevisionProposal"]["$ref"].endswith(
        "trading_room_workspace.schema.json#/definitions/WidgetRevisionProposal"
    )


def test_v1_5_capability_manifest_requires_frontend_drift_checks() -> None:
    manifest = json.loads((AGORA_SPECS / "v6/capability_manifest_v1_5.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "1.5"
    assert manifest["schema_bundle_index"] == "services/control-plane/specs/agora/bundle_index.v1_5.json"
    assert manifest["openapi_ref"] == "services/control-plane/openapi/agora_v1_5.openapi.yaml"
    assert set(manifest["required_definition_checksums"]) == REQUIRED_DEFINITIONS
    assert manifest["safety_boundary"] == {
        "direct_order_route": False,
        "capital_binding": False,
        "arbitrary_frontend_code": False,
        "schema_validator_required": True,
        "widget_chart_allowlist_required": True,
    }
