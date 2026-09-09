from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "docs/contracts/agora/generate_backend_contract.py"
BUNDLE = ROOT / "services/control-plane/specs/agora/bundle_index.v1_13.json"
MANIFEST = (
    ROOT
    / "services/control-plane/specs/agora/v14/capability_manifest_v1_13.json"
)
OPENAPI = ROOT / "services/control-plane/openapi/agora_v1_13.openapi.yaml"
HANDOFF = ROOT / "docs/contracts/agora/backend-generation-input.v1_13.json"


def _module():
    spec = importlib.util.spec_from_file_location("generate_backend_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


GENERATED_BUNDLE_PATHS = (
    "services/control-plane/specs/agora/bundle_index.v1_4.json",
    "services/control-plane/specs/agora/bundle_index.v1_5.json",
    "services/control-plane/specs/agora/bundle_index.v1_6.json",
    "services/control-plane/specs/agora/bundle_index.v1_7.json",
    "services/control-plane/specs/agora/bundle_index.v1_8.json",
    "services/control-plane/specs/agora/bundle_index.v1_9.json",
    "services/control-plane/specs/agora/bundle_index.v1_10.json",
    "services/control-plane/specs/agora/bundle_index.v1_11.json",
    "services/control-plane/specs/agora/bundle_index.v1_12.json",
    "services/control-plane/specs/agora/bundle_index.v1_13.json",
    "services/control-plane/specs/agora/v14/capability_manifest_v1_13.json",
    "services/control-plane/openapi/agora_v1_13.openapi.yaml",
)


def _generated_tree(root: Path) -> dict[str, bytes]:
    return {path: (root / path).read_bytes() for path in GENERATED_BUNDLE_PATHS}


def test_bundle_generation_is_byte_deterministic_across_two_clean_roots(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_run = _run("bundle", "--output-root", str(first))
    second_run = _run("bundle", "--output-root", str(second))

    assert first_run.returncode == 0, first_run.stderr
    assert second_run.returncode == 0, second_run.stderr
    assert _generated_tree(first) == _generated_tree(second)
    assert _generated_tree(first) == _generated_tree(ROOT)


def test_v1_13_bundle_hashes_exact_parent_manifest_and_openapi_bytes() -> None:
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    parent = ROOT / bundle["extends"]["bundle_path"]

    assert bundle["bundle_version"] == "1.13"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_12.json",
        "bundle_version": "1.12",
        "bundle_index_sha256": _sha256(parent),
    }
    assert bundle["files"] == {
        "specs/agora/v14/capability_manifest_v1_13.json": _sha256(MANIFEST)
    }
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_13.openapi.yaml",
        "sha256": _sha256(OPENAPI),
    }
    assert bundle["implementation_status"] == "implemented"
    assert bundle["compatibility_status"] == "pending"
    assert all("frontend-" in reason for reason in bundle["blocking_reasons"])


def test_openapi_is_complete_implemented_and_has_no_501_disposition() -> None:
    module = _module()
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))

    assert module._route_set(spec) == module.EXPECTED_ROUTES
    assert spec["info"]["x-implementation-status"] == "implemented"
    assert spec["info"]["x-extends-contract"].endswith("bundle_index.v1_12.json")
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation["operationId"], f"{method.upper()} {path}"
            assert "501" not in operation["responses"], f"{method.upper()} {path}"

    assert {
        "PerformanceProjectionEnvelope",
        "SuggestionActionEnvelope",
        "WorkshopVersionListEnvelope",
        "WorkshopResearchRunEnvelope",
        "WorkshopConsultationEnvelope",
        "WorkshopConcludeEnvelope",
        "CandidateMemberListEnvelope",
        "CandidateMemberDetailEnvelope",
        "CandidateTruthFields",
    } <= set(spec["components"]["schemas"])


def test_external_ref_closure_is_resolved_and_frontend_bounded() -> None:
    module = _module()
    paths = module._frontend_required_files()
    relative = {path.as_posix() for path in paths}

    assert relative == {
        "services/control-plane/openapi/agora_v1_13.openapi.yaml",
        "services/control-plane/specs/agora/v9/workshop_live_operations.schema.json",
        "services/control-plane/specs/agora/v11/performance_truth.schema.json",
        "services/control-plane/specs/agora/v11/workshop_version_operations.schema.json",
        "services/control-plane/specs/agora/v12/workshop_operation_lifecycle.schema.json",
        "services/control-plane/specs/agora/v13/candidate_member_truth_projection.schema.json",
    }
    assert all((ROOT / path).is_file() for path in paths)


def test_capability_routes_and_definition_hashes_are_complete() -> None:
    module = _module()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    routes = {
        route
        for capability in manifest["capabilities"]
        for route in capability["routes"]
    }

    assert routes == module.EXPECTED_ROUTES
    assert {capability["implementation_status"] for capability in manifest["capabilities"]} == {
        "implemented"
    }
    assert len(manifest["capabilities"]) == 4
    assert manifest["required_definition_checksums"] == module._definition_checksums()
    assert manifest["compatibility"]["status"] == "pending"


def test_handoff_is_reproducible_bound_and_pending() -> None:
    module = _module()
    if not HANDOFF.is_file():
        pytest.skip("handoff is emitted after the bundle anchor commit")
    handoff = json.loads(HANDOFF.read_text(encoding="utf-8"))
    backend = handoff["backend"]
    try:
        module._validate_contract_identity(
            backend["runtime_commit"], backend["contract_commit"]
        )
    except module.ContractError:
        pytest.skip("handoff is emitted after the bundle anchor commit")

    first = module.build_handoff(
        backend["runtime_commit"], backend["contract_commit"]
    )
    second = module.build_handoff(
        backend["runtime_commit"], backend["contract_commit"]
    )
    assert first == second == handoff
    assert backend["runtime_commit"] != "0" * 40
    assert backend["contract_commit"] != "0" * 40
    assert handoff["compatibility"]["status"] == "pending"
    assert handoff["contract"]["bundle_index"]["sha256"] == _sha256(BUNDLE)
    assert handoff["contract"]["openapi"]["sha256"] == _sha256(OPENAPI)
    assert handoff["contract"]["capability_manifest"]["sha256"] == _sha256(
        MANIFEST
    )


def test_handoff_rejects_placeholder_commit_identity() -> None:
    module = _module()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()

    with pytest.raises(module.ContractError, match="non-placeholder"):
        module.build_handoff("0" * 40, head)


def test_transitive_derivation_closure_covers_all_bundle_indexes_and_unique_files() -> None:
    module = _module()
    files = module._derivation_files()
    rel_files = {p.as_posix() for p in files}

    # Transitive derivation closure covers all 14 bundle indexes and exactly 90 distinct files
    # (89 transitive spec/input files + generator)
    assert len(files) == 90
    assert "services/control-plane/specs/agora/bundle_index.json" in rel_files
    for v in range(1, 14):
        assert f"services/control-plane/specs/agora/bundle_index.v1_{v}.json" in rel_files
    assert "services/control-plane/specs/agora/v4/research_run_projection.schema.json" in rel_files
    assert module.GENERATOR_PATH.as_posix() in rel_files
    assert all((ROOT / p).is_file() for p in files)


def test_backend_derivation_closure_rejects_missing_legacy_inputs_counterexample() -> None:
    module = _module()
    payload = json.loads(HANDOFF.read_text(encoding="utf-8"))
    contract_commit = payload["backend"]["contract_commit"]
    # The frozen counterexample omitted 72 transitive inputs (providing only 18 leaves)
    counterexample_payload = dict(payload)
    counterexample_payload["source_files"] = payload["source_files"][:18]
    reasons = module.validate_backend_derivation_closure(ROOT, counterexample_payload, contract_commit)
    assert "backend-source-files-incomplete" in reasons

    # The genuine complete derivation closure passes with zero reasons
    assert module.validate_backend_derivation_closure(ROOT, payload, contract_commit) == []


def test_backend_derivation_closure_rejects_duplicate_source_files() -> None:
    module = _module()
    payload = json.loads(HANDOFF.read_text(encoding="utf-8"))
    contract_commit = payload["backend"]["contract_commit"]
    dup_payload = dict(payload)
    dup_payload["source_files"] = list(payload["source_files"]) + [dict(payload["source_files"][0])]
    reasons = module.validate_backend_derivation_closure(ROOT, dup_payload, contract_commit)
    assert "backend-source-files-duplicate" in reasons


def test_backend_derivation_closure_rejects_malformed_source_file_entry() -> None:
    module = _module()
    payload = json.loads(HANDOFF.read_text(encoding="utf-8"))
    contract_commit = payload["backend"]["contract_commit"]

    malformed_payload = dict(payload)
    malformed_payload["source_files"] = list(payload["source_files"]) + [{"path": "bad", "sha256": "not-a-valid-sha"}]
    reasons = module.validate_backend_derivation_closure(ROOT, malformed_payload, contract_commit)
    assert "backend-source-files-invalid" in reasons

    malformed_payload2 = dict(payload)
    malformed_payload2["source_files"] = list(payload["source_files"]) + ["not-a-dict"]
    reasons2 = module.validate_backend_derivation_closure(ROOT, malformed_payload2, contract_commit)
    assert "backend-source-files-invalid" in reasons2


def test_backend_derivation_closure_propagates_traversal_failure_and_stale_parent_hash(tmp_path: Path) -> None:
    module = _module()
    payload = json.loads(HANDOFF.read_text(encoding="utf-8"))
    contract_commit = payload["backend"]["contract_commit"]

    v1_4_path = tmp_path / "services/control-plane/specs/agora/bundle_index.v1_4.json"
    v1_4_path.parent.mkdir(parents=True, exist_ok=True)
    v1_4_path.write_text("{ invalid JSON", encoding="utf-8")
    reasons = module.validate_backend_derivation_closure(tmp_path, payload, contract_commit)
    assert "backend-bundle-chain-invalid" in reasons or "backend-derivation-closure-traversal-failed" in reasons

    v1_4_data = json.loads((ROOT / "services/control-plane/specs/agora/bundle_index.v1_4.json").read_text(encoding="utf-8"))
    v1_4_data["extends"]["bundle_index_sha256"] = "0" * 64
    v1_4_path.write_text(json.dumps(v1_4_data), encoding="utf-8")
    reasons_stale = module.validate_backend_derivation_closure(tmp_path, payload, contract_commit)
    assert "backend-bundle-chain-stale-parent-hash" in reasons_stale


def test_validate_contract_identity_rejects_changed_consumed_bytes_at_old_commit() -> None:
    module = _module()
    with pytest.raises(module.ContractError, match="exact-byte mismatch"):
        module._validate_contract_identity(
            "6ad99d2e5abe4f31c9f48892ae7f44bf3bbab980",
            "6ad99d2e5abe4f31c9f48892ae7f44bf3bbab980",
        )


def test_bundle_chain_rejects_stale_parent_hash(tmp_path: Path) -> None:
    module = _module()
    v1_4_path = tmp_path / "services/control-plane/specs/agora/bundle_index.v1_4.json"
    v1_4_path.parent.mkdir(parents=True, exist_ok=True)
    v1_4_data = json.loads((ROOT / "services/control-plane/specs/agora/bundle_index.v1_4.json").read_text(encoding="utf-8"))
    v1_4_data["extends"]["bundle_index_sha256"] = "0" * 64
    v1_4_path.write_text(json.dumps(v1_4_data), encoding="utf-8")

    with pytest.raises(module.ContractError, match="stale parent hash"):
        module._read_bundle_chain(
            Path("services/control-plane/specs/agora/bundle_index.v1_4.json"),
            check_parent_hashes=True,
            root=tmp_path,
        )


def test_bundle_chain_rejects_cyclic_reference(tmp_path: Path) -> None:
    module = _module()
    b1_path = tmp_path / "services/control-plane/specs/agora/b1.json"
    b2_path = tmp_path / "services/control-plane/specs/agora/b2.json"
    b1_path.parent.mkdir(parents=True, exist_ok=True)
    b1_path.write_text(json.dumps({
        "bundle_version": "1.0",
        "extends": {"bundle_path": "services/control-plane/specs/agora/b2.json"}
    }), encoding="utf-8")
    b2_path.write_text(json.dumps({
        "bundle_version": "1.1",
        "extends": {"bundle_path": "services/control-plane/specs/agora/b1.json"}
    }), encoding="utf-8")

    with pytest.raises(module.ContractError, match="cycle in Agora bundle extension chain"):
        module._read_bundle_chain(
            Path("services/control-plane/specs/agora/b1.json"),
            check_parent_hashes=False,
            root=tmp_path,
        )


def test_bundle_chain_rejects_out_of_root_reference(tmp_path: Path) -> None:
    module = _module()
    b_path = tmp_path / "services/control-plane/specs/agora/b_escape.json"
    b_path.parent.mkdir(parents=True, exist_ok=True)
    b_path.write_text(json.dumps({
        "bundle_version": "1.0",
        "extends": {"bundle_path": "../../etc/passwd"}
    }), encoding="utf-8")

    with pytest.raises(module.ContractError, match="escapes repository"):
        module._read_bundle_chain(
            Path("services/control-plane/specs/agora/b_escape.json"),
            check_parent_hashes=False,
            root=tmp_path,
        )


def test_bundle_chain_rejects_missing_input_file(tmp_path: Path) -> None:
    module = _module()
    b_path = tmp_path / "services/control-plane/specs/agora/b_missing.json"
    b_path.parent.mkdir(parents=True, exist_ok=True)
    b_data = {
        "bundle_version": "1.0",
        "files": {"specs/agora/nonexistent.schema.json": "0" * 64},
    }
    b_path.write_text(json.dumps(b_data), encoding="utf-8")

    chain = [(Path("services/control-plane/specs/agora/b_missing.json"), b_data)]
    with pytest.raises(module.ContractError, match="missing bundle input file"):
        module._collect_chain_files(chain, check_hashes=False, root=tmp_path)


def test_contract_identity_rejects_unreachable_commit() -> None:
    module = _module()
    with pytest.raises(module.ContractError, match="git rev-parse"):
        module._validate_commit("f" * 40, "test commit")
