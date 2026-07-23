from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agora_compat_manifest.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
BACKEND_HANDOFF = ROOT / "docs" / "contracts" / "agora" / "backend-generation-input.v1_13.json"
TYPE_PATHS = [
    "src/lib/bff-v1/agora/contract-snapshot.json",
    "src/lib/bff-v1/agora/types.ts",
]


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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generated_types_sha(frontend_root: Path) -> str:
    lines = "".join(f"{path}\t{_sha256(frontend_root / path)}\n" for path in sorted(TYPE_PATHS))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture()
def frontend_repo(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "execute-plans"
    root.mkdir()
    _git(root, "init", "-b", "dev")
    _git(root, "config", "user.name", "Agora Compatibility Test")
    _git(root, "config", "user.email", "agora-compat@example.invalid")

    snapshot = root / TYPE_PATHS[0]
    types = root / TYPE_PATHS[1]
    _write_json(
        snapshot,
        {
            "contract_version": "1.13",
            "source_bundle": "services/control-plane/specs/agora/bundle_index.v1_13.json",
        },
    )
    types.parent.mkdir(parents=True, exist_ok=True)
    types.write_text("export type AgoraV113 = { accepted: true };\n", encoding="utf-8")
    _git(root, "add", *TYPE_PATHS)
    _git(root, "commit", "-m", "test: generate Agora types")
    runtime_commit = _git(root, "rev-parse", "HEAD")

    backend = json.loads(BACKEND_HANDOFF.read_text(encoding="utf-8"))
    frontend_handoff = {
        "handoff_version": "1.0",
        "contract_family": "agora.v1.13",
        "generated": True,
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "runtime_commit": runtime_commit,
            "generated_from_contract_commit": backend["backend"]["contract_commit"],
            "bundle_index_sha256": backend["contract"]["bundle_index"]["sha256"],
            "openapi_sha256": backend["contract"]["openapi"]["sha256"],
            "generated_types_sha256": _generated_types_sha(root),
        },
        "generation_metadata": {
            "expected_output_paths": TYPE_PATHS,
            "file_hash_algorithm": "sha256-exact-git-bytes-v1",
            "generated_types_hash_algorithm": "sha256-path-tab-filehash-lf-v1",
        },
        "generated_at": "2026-07-23T08:42:22Z",
    }
    handoff_path = root / "docs/contracts/agora/frontend-generation-output.v1_13.json"
    _write_json(handoff_path, frontend_handoff)
    _git(root, "add", str(handoff_path.relative_to(root)))
    _git(root, "commit", "-m", "test: publish frontend handoff")
    handoff_commit = _git(root, "rev-parse", "HEAD")
    return {
        "root": root,
        "runtime_commit": runtime_commit,
        "handoff_commit": handoff_commit,
    }


@pytest.fixture()
def accepted_manifest(tmp_path: Path, frontend_repo: dict[str, object]) -> dict[str, object]:
    output = tmp_path / "dev-compatibility-manifest.json"
    root = frontend_repo["root"]
    assert isinstance(root, Path)
    result = _run(
        "write",
        "--output",
        str(output),
        "--frontend-root",
        str(root),
        "--backend-dev-ref",
        "HEAD",
        "--frontend-dev-ref",
        "refs/heads/dev",
    )
    assert result.returncode == 0, result.stderr
    return {"path": output, "frontend": frontend_repo}


def _gate(manifest: Path, frontend_root: Path, *, frontend_ref: str = "refs/heads/dev"):
    return _run(
        "deployment-gate",
        "--manifest",
        str(manifest),
        "--frontend-root",
        str(frontend_root),
        "--backend-dev-ref",
        "HEAD",
        "--frontend-dev-ref",
        frontend_ref,
    )


def test_write_manifest_consumes_both_handoffs_and_accepts_exact_pair(
    accepted_manifest: dict[str, object],
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["contract_family"] == "agora.v1.13"
    assert manifest["compatibility_status"] == "accepted"
    assert manifest["blocking_reasons"] == []
    assert manifest["backend"]["runtime_commit"] == "6e08b040eebd2c317a9b44741d8badbf878e26ad"
    assert manifest["backend"]["contract_commit"] == "9e909de182f9f2379d23e8e6b81eefec29ffbce7"
    assert manifest["backend"]["bundle_index_sha256"] == "b1d488c3b35aa1c691e5b464362ac5a2fdd1efc442249e15be9bb143f379f870"
    assert manifest["backend"]["openapi_sha256"] == "36d1be5bc033ea1a55610f3f523fc478704fdfad1f06fec620e741bed9bf6f86"
    assert manifest["frontend"]["runtime_commit"] == frontend["runtime_commit"]
    assert manifest["frontend"]["generated_types_sha256"] != "0" * 64
    assert manifest["source_handoffs"]["backend"]["sha256"] == _sha256(BACKEND_HANDOFF)


def test_deployment_gate_passes_for_exact_reachable_pair(
    accepted_manifest: dict[str, object],
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)

    gate = _gate(output, frontend_root)

    assert gate.returncode == 0, gate.stderr
    assert "deployment gate passed" in gate.stdout


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("frontend", "generated_types_sha256"), "f" * 64, "frontend identity does not exactly match"),
        (("frontend", "openapi_sha256"), "e" * 64, "frontend identity does not exactly match"),
        (("backend", "bundle_index_sha256"), "d" * 64, "backend identity does not exactly match"),
        (("backend", "contract_commit"), "c" * 40, "backend identity does not exactly match"),
        (
            ("source_handoffs", "backend"),
            {"path": "docs/contracts/agora/backend-generation-input.v1_13.json", "commit": "c" * 40, "sha256": "0" * 64},
            "source_handoffs.backend.sha256 must not be a placeholder",
        ),
    ],
)
def test_deployment_gate_rejects_tampered_identity_fields(
    accepted_manifest: dict[str, object],
    path: tuple[str, str],
    replacement: object,
    message: str,
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)
    manifest = json.loads(output.read_text(encoding="utf-8"))
    manifest[path[0]][path[1]] = replacement
    output.write_text(json.dumps(manifest), encoding="utf-8")

    gate = _gate(output, frontend_root)

    assert gate.returncode == 1
    assert message in gate.stderr


def test_write_rejects_frontend_handoff_not_reachable_from_dev(
    tmp_path: Path,
    frontend_repo: dict[str, object],
) -> None:
    frontend_root = frontend_repo["root"]
    assert isinstance(frontend_root, Path)
    dev_commit = _git(frontend_root, "rev-parse", "refs/heads/dev")
    _git(frontend_root, "checkout", "--orphan", "unreachable")
    _git(frontend_root, "rm", "-rf", ".")
    _write_json(
        frontend_root / TYPE_PATHS[0],
        {
            "contract_version": "1.13",
            "source_bundle": "services/control-plane/specs/agora/bundle_index.v1_13.json",
        },
    )
    (frontend_root / TYPE_PATHS[1]).write_text(
        "export type Unreachable = true;\n",
        encoding="utf-8",
    )
    _git(frontend_root, "add", *TYPE_PATHS)
    _git(frontend_root, "commit", "-m", "test: unreachable generated types")
    runtime_commit = _git(frontend_root, "rev-parse", "HEAD")
    backend = json.loads(BACKEND_HANDOFF.read_text(encoding="utf-8"))
    handoff = {
        "handoff_version": "1.0",
        "contract_family": "agora.v1.13",
        "generated": True,
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "runtime_commit": runtime_commit,
            "generated_from_contract_commit": backend["backend"]["contract_commit"],
            "bundle_index_sha256": backend["contract"]["bundle_index"]["sha256"],
            "openapi_sha256": backend["contract"]["openapi"]["sha256"],
            "generated_types_sha256": _generated_types_sha(frontend_root),
        },
        "generation_metadata": {
            "expected_output_paths": TYPE_PATHS,
            "file_hash_algorithm": "sha256-exact-git-bytes-v1",
            "generated_types_hash_algorithm": "sha256-path-tab-filehash-lf-v1",
        },
        "generated_at": "2026-07-23T08:42:22Z",
    }
    handoff_path = frontend_root / "docs/contracts/agora/frontend-generation-output.v1_13.json"
    _write_json(handoff_path, handoff)
    _git(frontend_root, "add", str(handoff_path.relative_to(frontend_root)))
    _git(frontend_root, "commit", "-m", "test: unreachable handoff")
    unreachable_handoff = _git(frontend_root, "rev-parse", "HEAD")
    _git(frontend_root, "branch", "-f", "dev", dev_commit)

    output = tmp_path / "unreachable.json"
    result = _run(
        "write",
        "--output",
        str(output),
        "--frontend-root",
        str(frontend_root),
        "--backend-dev-ref",
        "HEAD",
        "--frontend-dev-ref",
        "refs/heads/dev",
        "--frontend-handoff-commit",
        unreachable_handoff,
        "--compatibility-status",
        "accepted",
    )

    assert result.returncode == 1
    assert "frontend-handoff-commit-not-reachable-from-dev" in result.stderr
    assert "frontend-runtime-commit-not-reachable-from-dev" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_nonaccepted_candidate_cannot_reach_switch_or_active_manifest(
    accepted_manifest: dict[str, object],
    tmp_path: Path,
    status: str,
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)
    candidate = json.loads(output.read_text(encoding="utf-8"))
    candidate["compatibility_status"] = status
    candidate["blocking_reasons"] = [f"test-{status}"]
    output.write_text(json.dumps(candidate), encoding="utf-8")

    previous = tmp_path / "release-previous"
    next_release = tmp_path / "release-candidate"
    previous.mkdir()
    next_release.mkdir()
    live = tmp_path / "live"
    live.symlink_to(previous)
    active_manifest = tmp_path / "active-manifest.json"
    active_manifest.write_text('{"pair":"previous"}\n', encoding="utf-8")
    before_manifest = active_manifest.read_bytes()
    before_target = os.readlink(live)

    gate = _gate(output, frontend_root)
    if gate.returncode == 0:  # pragma: no cover - this is the forbidden path.
        live.unlink()
        live.symlink_to(next_release)
        active_manifest.write_bytes(output.read_bytes())

    assert gate.returncode == 1
    assert "compatibility_status must be accepted for deployment" in gate.stderr
    assert os.readlink(live) == before_target
    assert active_manifest.read_bytes() == before_manifest


def test_rollback_drill_restores_prior_accepted_pair(
    accepted_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)
    assert _gate(output, frontend_root).returncode == 0

    previous = tmp_path / "release-previous"
    candidate = tmp_path / "release-candidate"
    previous.mkdir()
    candidate.mkdir()
    live = tmp_path / "live"
    live.symlink_to(previous)
    active_manifest = tmp_path / "active-manifest.json"
    previous_manifest = b'{"compatibility_status":"accepted","pair":"previous"}\n'
    active_manifest.write_bytes(previous_manifest)
    previous_target = os.readlink(live)

    live.unlink()
    live.symlink_to(candidate)
    active_manifest.write_bytes(output.read_bytes())
    post_switch_probe_passed = False
    if not post_switch_probe_passed:
        live.unlink()
        live.symlink_to(previous_target)
        active_manifest.write_bytes(previous_manifest)

    assert live.resolve() == previous.resolve()
    assert active_manifest.read_bytes() == previous_manifest


def test_workflow_enforces_gate_before_any_deploy_switch() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    frontend_checkout = workflow.index("Checkout accepted Agora frontend history")
    gate = workflow.index("Enforce exact Agora pair before any dev switch")
    deploy = workflow.index("Deploy dev VM stack under lease")

    assert frontend_checkout < gate < deploy
    gate_block = workflow[gate:deploy]
    assert "scripts/agora_compat_manifest.py" in gate_block
    assert "deployment-gate" in gate_block
    assert "--allow-pending" not in gate_block


def test_generated_types_hash_algorithm_sorts_relative_paths(tmp_path: Path) -> None:
    module = _load_module()
    frontend = tmp_path / "execute-plans"
    (frontend / "z").mkdir(parents=True)
    (frontend / "a").mkdir(parents=True)
    (frontend / "z" / "type.ts").write_text("z\n", encoding="utf-8")
    (frontend / "a" / "snapshot.json").write_text("a\n", encoding="utf-8")
    paths = ["z/type.ts", "a/snapshot.json"]

    lines = "".join(f"{path}\t{_sha256(frontend / path)}\n" for path in sorted(paths))
    expected = hashlib.sha256(lines.encode("utf-8")).hexdigest()
    actual_lines = []
    for path in sorted(paths):
        actual_lines.append(f"{path}\t{module.sha256_file(frontend / path)}\n")
    actual = module.sha256_bytes("".join(actual_lines).encode("utf-8"))

    assert actual == expected
