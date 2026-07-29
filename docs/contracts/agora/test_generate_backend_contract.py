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


def _generated_tree(root: Path) -> dict[str, bytes]:
    paths = (
        "services/control-plane/openapi/agora_v1_13.openapi.yaml",
        "services/control-plane/specs/agora/bundle_index.v1_13.json",
        "services/control-plane/specs/agora/v14/capability_manifest_v1_13.json",
    )
    return {path: (root / path).read_bytes() for path in paths}


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
