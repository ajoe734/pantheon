from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
OPENAPI = ROOT / "services/control-plane/openapi"

REQUIRED_V1_2_FILES = {
    "specs/agora/v3/private_content_ref.schema.json",
    "specs/agora/v3/workshop_storage_contract.schema.json",
    "specs/agora/v3/strategy_ref_contract.schema.json",
    "specs/agora/v3/workshop_event.schema.json",
    "specs/agora/v3/workshop_version_link.schema.json",
    "specs/agora/v3/workshop_persistence.schema.json",
    "specs/agora/v3/capability_manifest_v1_2.json",
    "openapi/agora_v1_2.openapi.yaml",
}

SECTION_9_ERROR_CODES = {
    "PRIVATE_CONTENT_STORE_UNAVAILABLE",
    "PRIVATE_CONTENT_REDACTION_UNAVAILABLE",
    "PRIVATE_CONTENT_EXPIRED",
    "PRIVATE_CONTENT_ACCESS_DENIED",
    "STRATEGY_REFERENCE_MISMATCH",
    "STRATEGY_REFERENCE_NOT_FOUND",
    "WORKSHOP_ALREADY_CONCLUDED",
    "WORKSHOP_ARCHIVED",
    "WORKSHOP_VERSION_REQUIRED",
    "CONCURRENT_MODIFICATION",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v1_2_bundle_extends_exact_v1_1_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_2.json").read_text(encoding="utf-8"))

    assert bundle["bundle_version"] == "1.2"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_1.json",
        "bundle_version": "1.1",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_1.json"),
    }


def test_v1_2_bundle_file_hashes_match_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_2.json").read_text(encoding="utf-8"))

    assert REQUIRED_V1_2_FILES <= set(bundle["files"])
    for rel_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / rel_path
        assert path.exists(), rel_path
        assert _sha256(path) == expected_hash


def test_v1_2_openapi_uses_canonical_workshop_status_filters() -> None:
    openapi = (OPENAPI / "agora_v1_2.openapi.yaml").read_text(encoding="utf-8")

    assert "title: Pantheon Agora API v1.2 Extension" in openapi
    assert 'x-extends-contract: "services/control-plane/openapi/agora_v1_1.openapi.yaml"' in openapi
    assert 'x-capability-manifest: "services/control-plane/specs/agora/v3/capability_manifest_v1_2.json"' in openapi
    assert 'x-persistence-contract: "services/control-plane/specs/agora/v3/workshop_persistence.schema.json"' in openapi
    assert "Authority order: v1.2 replaces the v1.1 status=active lifecycle" in openapi
    assert "WorkshopStatus:" in openapi
    assert "enum: [open, in_review, concluded, archived]" in openapi
    assert "name: status_group" in openapi
    assert "enum: [active, closed]" in openapi
    assert "active expands to open + in_review" in openapi
    assert "status=active alias is not part of the v1.2 contract" in openapi


def test_v1_2_openapi_private_content_and_projection_contracts() -> None:
    spec = yaml.safe_load((OPENAPI / "agora_v1_2.openapi.yaml").read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]

    create_props = schemas["WorkshopCreateRequest"]["properties"]
    assert "initial_message" in create_props
    assert "strategy_ref" in create_props
    assert create_props["strategy_spec_ref"]["deprecated"] is True
    assert "private_content_ref" not in create_props

    message_props = schemas["WorkshopMessageRequest"]["properties"]
    assert "content" in message_props
    assert "private_content_ref" not in message_props

    owner_props = schemas["OwnerWorkshopEventResponse"]["properties"]
    assert "content" in owner_props
    assert "private_content_ref" not in owner_props

    management_props = schemas["ManagementWorkshopEventProjection"]["properties"]
    assert "redacted_summary" in management_props
    assert "content" not in management_props
    assert "private_content_ref" not in management_props

    error_codes = set(schemas["ErrorResponse"]["properties"]["error"]["properties"]["code"]["enum"])
    assert SECTION_9_ERROR_CODES <= error_codes


def test_v1_2_capability_manifest_authority_and_privacy_contracts() -> None:
    manifest = json.loads(
        (AGORA_SPECS / "v3/capability_manifest_v1_2.json").read_text(encoding="utf-8")
    )

    assert manifest["manifest_version"] == "1.2"
    assert manifest["extends_manifest"] == "services/control-plane/specs/agora/v2/capability_manifest_v1_1.json"

    workshop = next(cap for cap in manifest["capabilities"] if cap["name"] == "agora.workshop.v1")
    assert workshop["version"] == "1.2"
    assert workshop["lifecycle_filters"]["canonical_status_values"] == [
        "open",
        "in_review",
        "concluded",
        "archived",
    ]
    assert workshop["lifecycle_filters"]["status_group"]["active"] == ["open", "in_review"]
    assert workshop["lifecycle_filters"]["v1_2_replaces_v1_1_status_active_wording"] is True
    assert workshop["private_content_contract"]["server_side_private_content_ref"] is True
    assert workshop["private_content_contract"]["browser_must_not_submit_private_content_ref"] is True
    assert workshop["private_content_contract"]["management_projection_must_not_include_raw_content"] is True
    assert set(workshop["error_codes"]) == SECTION_9_ERROR_CODES
