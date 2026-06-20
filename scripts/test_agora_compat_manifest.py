from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agora_compat_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agora_compat_manifest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_write_manifest_records_current_v1_1_hashes(tmp_path: Path) -> None:
    output = tmp_path / "dev-compatibility-manifest.json"

    result = _run("write", "--output", str(output))

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["contract_family"] == "agora.v1.1"
    assert manifest["environment"] == "dev"
    assert manifest["generated"] is True
    assert manifest["backend"]["base_bundle_index_sha256"] == _sha256(
        ROOT / "services/control-plane/specs/agora/bundle_index.json"
    )
    assert manifest["backend"]["extension_bundle_index_sha256"] == _sha256(
        ROOT / "services/control-plane/specs/agora/bundle_index.v1_1.json"
    )
    assert manifest["backend"]["openapi_sha256"] == _sha256(
        ROOT / "services/control-plane/openapi/agora_v1_1.openapi.yaml"
    )
    assert manifest["hash_policy"] == {
        "file_hash": "sha256-exact-git-bytes-v1",
        "generated_types_hash": "sha256-path-tab-filehash-lf-v1",
    }
    assert {"name": "agora.dashboard.v2", "version": "2.0", "required": True} in manifest[
        "required_capabilities"
    ]
    assert manifest["compatibility_status"] == "pending"
    assert "frontend-generated-types-not-agora-v1.1" in manifest["blocking_reasons"]


def test_verify_allows_pending_but_deployment_gate_fails_closed(tmp_path: Path) -> None:
    output = tmp_path / "dev-compatibility-manifest.json"
    assert _run("write", "--output", str(output)).returncode == 0

    verify = _run("verify", "--allow-pending", "--manifest", str(output))
    gate = _run("deployment-gate", "--manifest", str(output))

    assert verify.returncode == 0, verify.stderr
    assert gate.returncode == 1
    assert "compatibility_status must be compatible" in gate.stderr
    assert "frontend.runtime_commit is a placeholder commit" in gate.stderr


def test_deployment_gate_rejects_frontend_backend_hash_mismatch(tmp_path: Path) -> None:
    output = tmp_path / "dev-compatibility-manifest.json"
    assert _run("write", "--output", str(output), "--compatibility-status", "compatible").returncode == 0
    manifest = json.loads(output.read_text(encoding="utf-8"))
    manifest["blocking_reasons"] = []
    manifest["frontend"]["runtime_commit"] = manifest["backend"]["runtime_commit"]
    manifest["frontend"]["generated_from_contract_commit"] = manifest["backend"]["contract_commit"]
    manifest["frontend"]["openapi_sha256"] = "f" * 64
    output.write_text(json.dumps(manifest), encoding="utf-8")

    gate = _run("deployment-gate", "--manifest", str(output))

    assert gate.returncode == 1
    assert "frontend.openapi_sha256 must equal backend.openapi_sha256" in gate.stderr


def test_generated_types_hash_algorithm_sorts_relative_paths(tmp_path: Path) -> None:
    module = _load_module()
    frontend = tmp_path / "execute-plans"
    (frontend / "z").mkdir(parents=True)
    (frontend / "a").mkdir(parents=True)
    (frontend / "z" / "type.ts").write_text("z\n", encoding="utf-8")
    (frontend / "a" / "snapshot.json").write_text("a\n", encoding="utf-8")
    paths = ["z/type.ts", "a/snapshot.json"]

    actual = module.sha256_generated_types(frontend, paths)
    lines = "".join(f"{path}\t{_sha256(frontend / path)}\n" for path in sorted(paths))
    expected = hashlib.sha256(lines.encode("utf-8")).hexdigest()

    assert actual == expected


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
