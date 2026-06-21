from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
OPENAPI = ROOT / "services/control-plane/openapi"


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

    assert "specs/agora/v3/workshop_persistence.schema.json" in bundle["files"]
    assert "openapi/agora_v1_2.openapi.yaml" in bundle["files"]
    for rel_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / rel_path
        assert path.exists(), rel_path
        assert _sha256(path) == expected_hash


def test_v1_2_openapi_uses_canonical_workshop_status_filters() -> None:
    openapi = (OPENAPI / "agora_v1_2.openapi.yaml").read_text(encoding="utf-8")

    assert "title: Pantheon Agora API v1.2 Extension" in openapi
    assert 'x-extends-contract: "services/control-plane/openapi/agora_v1_1.openapi.yaml"' in openapi
    assert 'x-persistence-contract: "services/control-plane/specs/agora/v3/workshop_persistence.schema.json"' in openapi
    assert "WorkshopStatus:" in openapi
    assert "enum: [open, in_review, concluded, archived]" in openapi
    assert "name: status_group" in openapi
    assert "enum: [active, closed]" in openapi
    assert "active expands to open + in_review" in openapi
    assert "status=active alias is not part of the v1.2 contract" in openapi
