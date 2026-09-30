"""Reject digest drift across all versions of the Agora contract bundle."""

import json
import pytest

from scripts import agora_schema_bundle as bundle


def test_all_repository_bundle_digests():
    assert bundle.verify_all_indices()


@pytest.mark.parametrize("kind", ["schema", "missing", "parent", "openapi"])
def test_verify_cli_rejects_versioned_bundle_drift(tmp_path, monkeypatch, kind):
    specs = tmp_path / "services/control-plane/specs/agora"
    specs.mkdir(parents=True)
    schema = specs / "test.schema.json"
    schema.write_text("{}\n")
    index = {"files": {"specs/agora/test.schema.json": bundle.sha256_file(schema)}}
    (specs / "bundle_index.json").write_text(json.dumps(index))
    versioned = json.loads(json.dumps(index))
    if kind == "schema":
        versioned["files"]["specs/agora/test.schema.json"] = "0" * 64
    elif kind == "missing":
        versioned["files"]["specs/agora/missing.schema.json"] = "0" * 64
    elif kind == "parent":
        versioned["extends"] = {
            "bundle_path": "services/control-plane/specs/agora/bundle_index.json",
            "bundle_index_sha256": "0" * 64,
        }
    else:
        versioned["openapi"] = {
            "path": str(schema.relative_to(tmp_path)), "sha256": "0" * 64,
        }
    (specs / "bundle_index.v9_99.json").write_text(json.dumps(versioned))
    monkeypatch.setattr(bundle, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bundle, "AGORA_SPECS", specs)
    monkeypatch.setattr("sys.argv", ["agora_schema_bundle.py", "--verify"])
    with pytest.raises(SystemExit) as result:
        bundle.main()
    assert result.value.code == 1


def test_empty_bundle_directory_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "AGORA_SPECS", tmp_path)
    assert not bundle.verify_all_indices()


def test_generation_inputs_record_current_source_digests():
    inputs = sorted((bundle.REPO_ROOT / "docs/contracts/agora").glob("backend-generation-input*.json"))
    assert inputs
    for path in inputs:
        for entry in json.loads(path.read_text())["source_files"]:
            assert bundle.sha256_file(bundle.REPO_ROOT / entry["path"]) == entry["sha256"], (path, entry["path"])


def test_inherited_bundle_digests():
    for path in bundle.AGORA_SPECS.glob("bundle_index*.json"):
        for entry in json.loads(path.read_text()).get("source_contracts", []):
            assert bundle.sha256_file(bundle.REPO_ROOT / entry["bundle_path"]) == entry["bundle_sha256"]


def test_trading_decision_readback_fields_remain_optional():
    schema = json.loads((bundle.AGORA_SPECS / "v4/trading_decision_event.schema.json").read_text())
    fields = {"tenant_id", "user_id", "intent_ref", "etag"}
    assert fields <= schema["properties"].keys()
    assert fields.isdisjoint(schema.get("required", []))
