from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agora_compat_manifest.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
BACKEND_HANDOFF = ROOT / "docs" / "contracts" / "agora" / "backend-generation-input.v1_13.json"
CANONICAL_HASH_FIXTURE = (
    ROOT / "docs" / "contracts" / "agora" / "canonical-hash-conformance-fixture.json"
)
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


def _gate(
    manifest: Path,
    frontend_root: Path,
    *,
    frontend_ref: str = "refs/heads/dev",
    backend_runtime_commit: str = "",
    frontend_runtime_commit: str = "",
    evidence_out: Path | None = None,
):
    args = [
        "deployment-gate",
        "--manifest",
        str(manifest),
        "--frontend-root",
        str(frontend_root),
        "--backend-dev-ref",
        "HEAD",
        "--frontend-dev-ref",
        frontend_ref,
    ]
    if backend_runtime_commit:
        args.extend(["--backend-runtime-commit", backend_runtime_commit])
    if frontend_runtime_commit:
        args.extend(["--frontend-runtime-commit", frontend_runtime_commit])
    if evidence_out is not None:
        args.extend(["--evidence-out", str(evidence_out)])
    return _run(*args)


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


def test_write_manifest_accepts_exact_descendant_delivery_payloads(
    tmp_path: Path,
    frontend_repo: dict[str, object],
) -> None:
    frontend_root = frontend_repo["root"]
    assert isinstance(frontend_root, Path)
    (frontend_root / "delivery-controller.txt").write_text(
        "compatible delivery-only change\n",
        encoding="utf-8",
    )
    _git(frontend_root, "add", "delivery-controller.txt")
    _git(frontend_root, "commit", "-m", "test: add compatible delivery controller")
    frontend_delivery_commit = _git(frontend_root, "rev-parse", "HEAD")
    backend_delivery_commit = _git(ROOT, "rev-parse", "HEAD")
    output = tmp_path / "delivery-manifest.json"

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
        "--backend-runtime-commit",
        backend_delivery_commit,
        "--frontend-runtime-commit",
        frontend_delivery_commit,
        "--compatibility-status",
        "accepted",
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["backend"]["runtime_commit"] == backend_delivery_commit
    assert manifest["frontend"]["runtime_commit"] == frontend_delivery_commit
    assert manifest["frontend"]["handoff_commit"] == frontend_repo["handoff_commit"]
    gate = _gate(
        output,
        frontend_root,
        backend_runtime_commit=backend_delivery_commit,
        frontend_runtime_commit=frontend_delivery_commit,
    )
    assert gate.returncode == 0, gate.stderr


def test_write_manifest_rejects_unreachable_delivery_payload(
    tmp_path: Path,
    frontend_repo: dict[str, object],
) -> None:
    frontend_root = frontend_repo["root"]
    assert isinstance(frontend_root, Path)
    output = tmp_path / "unreachable-delivery.json"

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
        "--frontend-runtime-commit",
        "f" * 40,
        "--compatibility-status",
        "accepted",
    )

    assert result.returncode == 1
    assert "frontend-delivery-runtime-commit-not-reachable-from-dev" in result.stderr
    assert not output.exists()


def test_write_manifest_rejects_delivery_payload_with_changed_generated_types(
    tmp_path: Path,
    frontend_repo: dict[str, object],
) -> None:
    frontend_root = frontend_repo["root"]
    assert isinstance(frontend_root, Path)
    (frontend_root / TYPE_PATHS[1]).write_text(
        "export type AgoraV113 = { accepted: false };\n",
        encoding="utf-8",
    )
    _git(frontend_root, "add", TYPE_PATHS[1])
    _git(frontend_root, "commit", "-m", "test: drift generated types")
    drifted_delivery_commit = _git(frontend_root, "rev-parse", "HEAD")
    output = tmp_path / "drifted-delivery.json"

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
        "--frontend-runtime-commit",
        drifted_delivery_commit,
        "--compatibility-status",
        "accepted",
    )

    assert result.returncode == 1
    assert "frontend-delivery-generated-types-hash-mismatch" in result.stderr
    assert not output.exists()


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


def test_deployment_gate_binds_both_actual_runtime_payloads_and_writes_evidence(
    accepted_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)
    manifest = json.loads(output.read_text(encoding="utf-8"))
    evidence_path = tmp_path / "agora-compatibility-gate.json"

    gate = _gate(
        output,
        frontend_root,
        backend_runtime_commit=manifest["backend"]["runtime_commit"],
        frontend_runtime_commit=manifest["frontend"]["runtime_commit"],
        evidence_out=evidence_path,
    )

    assert gate.returncode == 0, gate.stderr
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "pantheon.agora.compatibility-gate-evidence.v1"
    assert evidence["compatibility_status"] == "accepted"
    assert evidence["blocking_reasons"] == []
    assert evidence["manifest_sha256"] == _sha256(output)
    assert evidence["backend"]["runtime_commit"] == manifest["backend"]["runtime_commit"]
    assert evidence["frontend"]["runtime_commit"] == manifest["frontend"]["runtime_commit"]
    assert len(evidence["backend"]["tree"]) == 40
    assert len(evidence["frontend"]["tree"]) == 40
    release_candidate = evidence["release_candidate"]
    assert release_candidate["schema_version"] == "pantheon.dev-release-candidate.v1"
    assert release_candidate["compatibility_status"] == "compatible"
    assert release_candidate["compatibility_manifest"] == {
        "contract_family": "agora.v1.13",
        "manifest_version": "1.0",
        "sha256": _sha256(output),
        "source_status": "accepted",
    }
    assert release_candidate["backend"] == {
        "repository": "ajoe734/pantheon",
        "branch": "dev",
        "commit": manifest["backend"]["runtime_commit"],
        "tree": evidence["backend"]["tree"],
    }
    assert release_candidate["frontend"] == {
        "repository": "ajoe734/execute-plans",
        "branch": "dev",
        "commit": manifest["frontend"]["runtime_commit"],
        "tree": evidence["frontend"]["tree"],
    }
    identity = {
        key: value
        for key, value in release_candidate.items()
        if key != "release_candidate_id"
    }
    assert release_candidate["release_candidate_id"] == _load_module().canonical_json_sha256(
        identity
    )


def test_release_candidate_id_is_deterministic_and_pair_sensitive(
    accepted_manifest: dict[str, object],
    tmp_path: Path,
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)
    manifest = json.loads(output.read_text(encoding="utf-8"))
    first_path = tmp_path / "first-gate.json"
    second_path = tmp_path / "second-gate.json"

    first = _gate(
        output,
        frontend_root,
        backend_runtime_commit=manifest["backend"]["runtime_commit"],
        frontend_runtime_commit=manifest["frontend"]["runtime_commit"],
        evidence_out=first_path,
    )
    second = _gate(
        output,
        frontend_root,
        backend_runtime_commit=manifest["backend"]["runtime_commit"],
        frontend_runtime_commit=manifest["frontend"]["runtime_commit"],
        evidence_out=second_path,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_evidence = json.loads(first_path.read_text(encoding="utf-8"))
    second_evidence = json.loads(second_path.read_text(encoding="utf-8"))
    assert (
        first_evidence["release_candidate"]["release_candidate_id"]
        == second_evidence["release_candidate"]["release_candidate_id"]
    )
    changed_identity = dict(first_evidence["release_candidate"])
    changed_identity.pop("release_candidate_id")
    changed_identity["frontend"] = {
        **changed_identity["frontend"],
        "commit": "f" * 40,
    }
    assert _load_module().canonical_json_sha256(changed_identity) != first_evidence[
        "release_candidate"
    ]["release_candidate_id"]


@pytest.mark.parametrize("owner", ["backend", "frontend"])
def test_deployment_gate_rejects_actual_runtime_payload_mismatch(
    accepted_manifest: dict[str, object],
    owner: str,
) -> None:
    output = accepted_manifest["path"]
    frontend = accepted_manifest["frontend"]
    assert isinstance(output, Path)
    assert isinstance(frontend, dict)
    frontend_root = frontend["root"]
    assert isinstance(frontend_root, Path)
    manifest = json.loads(output.read_text(encoding="utf-8"))
    backend_runtime = manifest["backend"]["runtime_commit"]
    frontend_runtime = manifest["frontend"]["runtime_commit"]
    if owner == "backend":
        backend_runtime = "f" * 40
    else:
        frontend_runtime = "f" * 40

    gate = _gate(
        output,
        frontend_root,
        backend_runtime_commit=backend_runtime,
        frontend_runtime_commit=frontend_runtime,
    )

    assert gate.returncode == 1
    assert f"{owner}.runtime_commit does not match the required runtime identity" in gate.stderr


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


def test_workflow_enforces_gate_before_any_deploy_switch() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate_controller = workflow.index("Checkout accepted Agora gate controller")
    frontend_checkout = workflow.index("Checkout accepted Agora frontend history")
    gate = workflow.index(
        "Generate immutable exact-pair admission before any dev switch"
    )
    seal = workflow.index("Seal exact-pair admission artifact before any dev switch")
    deploy = workflow.index("Deploy dev VM stack under lease")

    assert gate_controller < gate
    assert frontend_checkout < gate < seal < deploy
    gate_block = workflow[gate:seal]
    assert "scripts/agora_compat_manifest.py" in gate_block
    assert "agora_compat_manifest.py write" in gate_block
    assert "deployment-gate" in gate_block
    assert '--backend-runtime-commit "${{ steps.target.outputs.sha }}"' in gate_block
    assert (
        '--frontend-runtime-commit "${{ steps.frontend.outputs.sha }}"'
        in gate_block
    )
    assert "--compatibility-status accepted" in gate_block
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


def test_canonical_json_sha256_matches_cross_repo_fixture() -> None:
    """Pin `canonical_json_sha256` against a fixture shared with execute-plans.

    execute-plans reimplements this exact algorithm in JS
    (scripts/deploy-dev-vm.sh::canonicalize + sha256) because it cannot import
    this Python module. The two implementations drifted once already (a
    missing trailing-LF byte, fixed 2026-08-16 by
    OPS-AGORA-RELEASE-CANDIDATE-LF-20260816, which is what rejected the
    AGORA-HOSTED-SERVICE-PROOF-20260815 release candidate). This test and its
    identical counterpart in execute-plans both assert against the same
    payload/hash pair recorded in
    docs/contracts/agora/canonical-hash-conformance-fixture.json, so a future
    change to either side's algorithm is caught locally instead of surfacing
    weeks later as a live cross-repo release rejection. Keep the fixture file
    byte-identical in both repositories; update it in the same change on both
    sides if the algorithm itself ever needs to change, never independently.
    """

    module = _load_module()
    fixture = json.loads(CANONICAL_HASH_FIXTURE.read_text(encoding="utf-8"))
    assert module.canonical_json_sha256(fixture["payload"]) == fixture["expected_sha256"]
