#!/usr/bin/env python3
"""Generate and enforce the exact Agora cross-repository compatibility pair.

The accepted dev manifest is assembled from the machine-readable backend and
frontend handoffs.  Deployment verification reads the handoffs and generated
artifacts from the named Git commits, proves that every identity commit is
reachable from its protected ``dev`` branch, and fails before deployment when
any byte or identity differs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "contracts" / "agora" / "dev-compatibility-manifest.json"
DEFAULT_FRONTEND_ROOT = Path(
    os.environ.get("EXECUTE_PLANS_ROOT", "/home/lupin/code/execute-plans")
).expanduser()
CONTRACT_VERSION = "1.13"
CONTRACT_FAMILY = f"agora.v{CONTRACT_VERSION}"
BACKEND_HANDOFF_PATH = "docs/contracts/agora/backend-generation-input.v1_13.json"
FRONTEND_HANDOFF_PATH = "docs/contracts/agora/frontend-generation-output.v1_13.json"
BUNDLE_PATH = "services/control-plane/specs/agora/bundle_index.v1_13.json"
OPENAPI_PATH = "services/control-plane/openapi/agora_v1_13.openapi.yaml"
CAPABILITY_PATH = "services/control-plane/specs/agora/v14/capability_manifest_v1_13.json"
BASE_CAPABILITY_PATH = "services/control-plane/specs/agora/capability_manifest.json"
EXTENSION_CAPABILITY_PATHS = tuple(
    f"services/control-plane/specs/agora/v{version + 1}/capability_manifest_v1_{version}.json"
    for version in range(1, 14)
)
DEFAULT_GENERATED_TYPE_PATHS = (
    "src/lib/bff-v1/agora/contract-snapshot.json",
    "src/lib/bff-v1/agora/types.ts",
)
DEFAULT_BACKEND_DEV_REF = "refs/remotes/origin/dev"
DEFAULT_FRONTEND_DEV_REF = "refs/remotes/origin/dev"
ZERO_COMMIT = "0" * 40
ZERO_SHA256 = "0" * 64
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
ACCEPTED_STATUS = "accepted"
NON_ACCEPTED_STATUSES = {"pending", "rejected"}
GATE_EVIDENCE_SCHEMA = "pantheon.agora.compatibility-gate-evidence.v1"
RELEASE_CANDIDATE_SCHEMA = "pantheon.dev-release-candidate.v1"
RELEASE_COMPATIBILITY_STATUS = "compatible"


class ManifestError(ValueError):
    """Raised when manifest generation or verification cannot continue."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256_bytes(encoded + b"\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"JSON file must contain an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def git_process(repo_root: Path, args: list[str], *, text: bool = False) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_output(repo_root: Path, args: list[str], default: str = "") -> str:
    proc = git_process(repo_root, args, text=True)
    if proc.returncode != 0:
        return default
    return proc.stdout.strip() or default


def require_git_repo(repo_root: Path, label: str) -> None:
    if not repo_root.is_dir():
        raise ManifestError(f"{label} does not exist: {repo_root}")
    if git_output(repo_root, ["rev-parse", "--show-toplevel"]) == "":
        raise ManifestError(f"{label} is not a Git checkout: {repo_root}")


def git_bytes(repo_root: Path, commit: str, rel_path: str) -> bytes:
    if not COMMIT_RE.fullmatch(commit):
        raise ManifestError(f"invalid Git commit for {rel_path}: {commit}")
    proc = git_process(repo_root, ["show", f"{commit}:{rel_path}"])
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ManifestError(f"{rel_path} is not readable at {commit}: {detail}")
    return bytes(proc.stdout)


def git_json(repo_root: Path, commit: str, rel_path: str) -> tuple[dict[str, Any], str]:
    raw = git_bytes(repo_root, commit, rel_path)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid JSON at {commit}:{rel_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"JSON at {commit}:{rel_path} must contain an object")
    return payload, sha256_bytes(raw)


def latest_file_commit(repo_root: Path, dev_ref: str, rel_path: str) -> str:
    value = git_output(repo_root, ["log", "-n", "1", "--format=%H", dev_ref, "--", rel_path])
    if not COMMIT_RE.fullmatch(value):
        raise ManifestError(f"cannot resolve the owning commit for {rel_path} from {dev_ref}")
    return value


def commit_is_reachable(repo_root: Path, commit: str, dev_ref: str) -> bool:
    if not COMMIT_RE.fullmatch(commit):
        return False
    return git_process(repo_root, ["merge-base", "--is-ancestor", commit, dev_ref]).returncode == 0


def git_tree(repo_root: Path, commit: str) -> str:
    value = git_output(repo_root, ["rev-parse", f"{commit}^{{tree}}"])
    if not COMMIT_RE.fullmatch(value):
        raise ManifestError(f"cannot resolve the Git tree for commit {commit}")
    return value


def sha256_git_generated_types(
    frontend_root: Path,
    commit: str,
    relative_paths: list[str],
) -> str:
    lines: list[str] = []
    for rel_path in sorted(relative_paths):
        lines.append(f"{rel_path}\t{sha256_bytes(git_bytes(frontend_root, commit, rel_path))}\n")
    if not lines:
        return ZERO_SHA256
    return sha256_bytes("".join(lines).encode("utf-8"))


def require_local_file(rel_path: str) -> Path:
    path = REPO_ROOT / rel_path
    if not path.is_file():
        raise ManifestError(f"required contract file is missing: {rel_path}")
    return path


def compare_versions(left: str, right: str) -> int:
    def parts(value: str) -> tuple[int, ...]:
        parsed: list[int] = []
        for piece in str(value).split("."):
            try:
                parsed.append(int(piece))
            except ValueError:
                parsed.append(0)
        return tuple(parsed)

    left_parts = parts(left)
    right_parts = parts(right)
    width = max(len(left_parts), len(right_parts))
    left_parts = left_parts + (0,) * (width - len(left_parts))
    right_parts = right_parts + (0,) * (width - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def load_capabilities() -> dict[str, str]:
    advertised: dict[str, str] = {}

    def add_capability(name: str, version: str) -> None:
        if name and (name not in advertised or compare_versions(version, advertised[name]) > 0):
            advertised[name] = version

    for rel_path in (BASE_CAPABILITY_PATH, *EXTENSION_CAPABILITY_PATHS):
        payload = read_json(require_local_file(rel_path))
        for capability in payload.get("capabilities", []):
            if not isinstance(capability, dict):
                continue
            add_capability(
                str(capability.get("name") or ""),
                str(capability.get("version") or "1.0"),
            )
    return advertised


def required_capabilities() -> list[dict[str, Any]]:
    return [
        {"name": name, "version": version, "required": True}
        for name, version in sorted(load_capabilities().items())
    ]


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"{key} must be an object")
    return value


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{key} must be a non-empty string")
    return value


def load_handoffs(
    *,
    frontend_root: Path,
    backend_handoff_commit: str,
    frontend_handoff_commit: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    backend, backend_sha = git_json(REPO_ROOT, backend_handoff_commit, BACKEND_HANDOFF_PATH)
    frontend, frontend_sha = git_json(frontend_root, frontend_handoff_commit, FRONTEND_HANDOFF_PATH)
    return backend, backend_sha, frontend, frontend_sha


def working_tree_binding_reasons(
    *,
    frontend_root: Path,
    backend_handoff_sha: str,
    frontend_handoff_sha: str,
    backend_handoff: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    backend_contract = _mapping(backend_handoff, "contract")
    expected_contract_hashes = (
        (BUNDLE_PATH, _mapping(backend_contract, "bundle_index").get("sha256"), "backend-bundle"),
        (OPENAPI_PATH, _mapping(backend_contract, "openapi").get("sha256"), "backend-openapi"),
        (
            CAPABILITY_PATH,
            _mapping(backend_contract, "capability_manifest").get("sha256"),
            "backend-capability",
        ),
    )
    for rel_path, expected, label in expected_contract_hashes:
        if sha256_file(require_local_file(rel_path)) != expected:
            reasons.append(f"{label}-working-tree-hash-mismatch")
    if sha256_file(require_local_file(BACKEND_HANDOFF_PATH)) != backend_handoff_sha:
        reasons.append("backend-handoff-working-tree-hash-mismatch")
    frontend_handoff_path = frontend_root / FRONTEND_HANDOFF_PATH
    if not frontend_handoff_path.is_file():
        reasons.append("frontend-handoff-working-tree-missing")
    elif sha256_file(frontend_handoff_path) != frontend_handoff_sha:
        reasons.append("frontend-handoff-working-tree-hash-mismatch")
    return reasons


def handoff_blocking_reasons(
    backend_handoff: dict[str, Any],
    frontend_handoff: dict[str, Any],
    *,
    frontend_root: Path,
    backend_handoff_commit: str,
    frontend_handoff_commit: str,
    backend_dev_ref: str,
    frontend_dev_ref: str,
) -> list[str]:
    reasons: list[str] = []
    if backend_handoff.get("contract_family") != CONTRACT_FAMILY:
        reasons.append("backend-contract-family-mismatch")
    if frontend_handoff.get("contract_family") != CONTRACT_FAMILY:
        reasons.append("frontend-contract-family-mismatch")

    backend = _mapping(backend_handoff, "backend")
    contract = _mapping(backend_handoff, "contract")
    bundle = _mapping(contract, "bundle_index")
    openapi = _mapping(contract, "openapi")
    capability = _mapping(contract, "capability_manifest")
    frontend = _mapping(frontend_handoff, "frontend")
    generation = _mapping(frontend_handoff, "generation_metadata")

    comparisons = (
        (frontend.get("generated_from_contract_commit"), backend.get("contract_commit"), "contract-commit"),
        (frontend.get("bundle_index_sha256"), bundle.get("sha256"), "bundle-index"),
        (frontend.get("openapi_sha256"), openapi.get("sha256"), "openapi"),
    )
    for left, right, label in comparisons:
        if left != right:
            reasons.append(f"frontend-backend-{label}-mismatch")

    if bundle.get("path") != BUNDLE_PATH:
        reasons.append("backend-bundle-path-mismatch")
    if openapi.get("path") != OPENAPI_PATH:
        reasons.append("backend-openapi-path-mismatch")
    if capability.get("path") != CAPABILITY_PATH:
        reasons.append("backend-capability-path-mismatch")
    if generation.get("generated_types_hash_algorithm") != "sha256-path-tab-filehash-lf-v1":
        reasons.append("frontend-generated-types-algorithm-mismatch")
    if generation.get("file_hash_algorithm") != "sha256-exact-git-bytes-v1":
        reasons.append("frontend-file-hash-algorithm-mismatch")
    if generation.get("expected_output_paths") != list(DEFAULT_GENERATED_TYPE_PATHS):
        reasons.append("frontend-generated-type-paths-mismatch")

    backend_runtime = str(backend.get("runtime_commit") or "")
    backend_contract = str(backend.get("contract_commit") or "")
    frontend_runtime = str(frontend.get("runtime_commit") or "")
    identity_commits = (
        ("backend-runtime", REPO_ROOT, backend_runtime, backend_dev_ref),
        ("backend-contract", REPO_ROOT, backend_contract, backend_dev_ref),
        ("backend-handoff", REPO_ROOT, backend_handoff_commit, backend_dev_ref),
        ("frontend-runtime", frontend_root, frontend_runtime, frontend_dev_ref),
        ("frontend-handoff", frontend_root, frontend_handoff_commit, frontend_dev_ref),
    )
    for label, repo_root, commit, dev_ref in identity_commits:
        if commit in {"", ZERO_COMMIT} or not COMMIT_RE.fullmatch(commit):
            reasons.append(f"{label}-commit-placeholder")
        elif not commit_is_reachable(repo_root, commit, dev_ref):
            reasons.append(f"{label}-commit-not-reachable-from-dev")

    expected_git_hashes = (
        ("backend-bundle", REPO_ROOT, backend_contract, BUNDLE_PATH, bundle.get("sha256")),
        ("backend-openapi", REPO_ROOT, backend_contract, OPENAPI_PATH, openapi.get("sha256")),
        ("backend-capability", REPO_ROOT, backend_contract, CAPABILITY_PATH, capability.get("sha256")),
    )
    for label, repo_root, commit, rel_path, expected in expected_git_hashes:
        try:
            actual = sha256_bytes(git_bytes(repo_root, commit, rel_path))
        except ManifestError:
            reasons.append(f"{label}-git-bytes-missing")
            continue
        if actual != expected:
            reasons.append(f"{label}-hash-mismatch")

    try:
        actual_types = sha256_git_generated_types(
            frontend_root,
            frontend_runtime,
            list(DEFAULT_GENERATED_TYPE_PATHS),
        )
    except ManifestError:
        reasons.append("frontend-generated-types-git-bytes-missing")
    else:
        if actual_types != frontend.get("generated_types_sha256"):
            reasons.append("frontend-generated-types-hash-mismatch")

    for owner, payload, keys in (
        ("backend", backend, ("runtime_commit", "contract_commit")),
        ("frontend", frontend, ("runtime_commit", "generated_from_contract_commit")),
    ):
        for key in keys:
            value = str(payload.get(key) or "")
            if value == ZERO_COMMIT:
                reasons.append(f"{owner}-{key.replace('_', '-')}-placeholder")
    for label, value in (
        ("backend-bundle", bundle.get("sha256")),
        ("backend-openapi", openapi.get("sha256")),
        ("backend-capability", capability.get("sha256")),
        ("frontend-bundle", frontend.get("bundle_index_sha256")),
        ("frontend-openapi", frontend.get("openapi_sha256")),
        ("frontend-generated-types", frontend.get("generated_types_sha256")),
    ):
        if value == ZERO_SHA256 or not SHA_RE.fullmatch(str(value or "")):
            reasons.append(f"{label}-sha256-placeholder")

    return sorted(set(reasons))


def delivery_runtime_blocking_reasons(
    backend_handoff: dict[str, Any],
    frontend_handoff: dict[str, Any],
    *,
    frontend_root: Path,
    backend_runtime_commit: str,
    frontend_runtime_commit: str,
    backend_dev_ref: str,
    frontend_dev_ref: str,
) -> list[str]:
    """Validate exact delivery payloads against their compatible handoff bases."""

    reasons: list[str] = []
    backend_handoff_runtime = str(_mapping(backend_handoff, "backend").get("runtime_commit") or "")
    frontend = _mapping(frontend_handoff, "frontend")
    frontend_handoff_runtime = str(frontend.get("runtime_commit") or "")
    runtime_bindings = (
        (
            "backend",
            REPO_ROOT,
            backend_handoff_runtime,
            backend_runtime_commit,
            backend_dev_ref,
        ),
        (
            "frontend",
            frontend_root,
            frontend_handoff_runtime,
            frontend_runtime_commit,
            frontend_dev_ref,
        ),
    )
    for owner, repo_root, handoff_runtime, delivery_runtime, dev_ref in runtime_bindings:
        if delivery_runtime in {"", ZERO_COMMIT} or not COMMIT_RE.fullmatch(delivery_runtime):
            reasons.append(f"{owner}-delivery-runtime-commit-placeholder")
            continue
        if not commit_is_reachable(repo_root, delivery_runtime, dev_ref):
            reasons.append(f"{owner}-delivery-runtime-commit-not-reachable-from-dev")
            continue
        if (
            COMMIT_RE.fullmatch(handoff_runtime)
            and not commit_is_reachable(repo_root, handoff_runtime, delivery_runtime)
        ):
            reasons.append(f"{owner}-delivery-runtime-not-descendant-of-handoff")

    if COMMIT_RE.fullmatch(frontend_runtime_commit):
        try:
            actual_types = sha256_git_generated_types(
                frontend_root,
                frontend_runtime_commit,
                list(DEFAULT_GENERATED_TYPE_PATHS),
            )
        except ManifestError:
            reasons.append("frontend-delivery-generated-types-git-bytes-missing")
        else:
            if actual_types != frontend.get("generated_types_sha256"):
                reasons.append("frontend-delivery-generated-types-hash-mismatch")
    return sorted(set(reasons))


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    frontend_root = Path(args.frontend_root).expanduser().resolve()
    require_git_repo(REPO_ROOT, "Pantheon root")
    require_git_repo(frontend_root, "execute-plans root")
    backend_handoff_commit = args.backend_handoff_commit or latest_file_commit(
        REPO_ROOT,
        args.backend_dev_ref,
        BACKEND_HANDOFF_PATH,
    )
    frontend_handoff_commit = args.frontend_handoff_commit or latest_file_commit(
        frontend_root,
        args.frontend_dev_ref,
        FRONTEND_HANDOFF_PATH,
    )
    backend_handoff, backend_handoff_sha, frontend_handoff, frontend_handoff_sha = load_handoffs(
        frontend_root=frontend_root,
        backend_handoff_commit=backend_handoff_commit,
        frontend_handoff_commit=frontend_handoff_commit,
    )
    reasons = handoff_blocking_reasons(
        backend_handoff,
        frontend_handoff,
        frontend_root=frontend_root,
        backend_handoff_commit=backend_handoff_commit,
        frontend_handoff_commit=frontend_handoff_commit,
        backend_dev_ref=args.backend_dev_ref,
        frontend_dev_ref=args.frontend_dev_ref,
    )
    reasons.extend(
        working_tree_binding_reasons(
            frontend_root=frontend_root,
            backend_handoff_sha=backend_handoff_sha,
            frontend_handoff_sha=frontend_handoff_sha,
            backend_handoff=backend_handoff,
        )
    )
    backend = _mapping(backend_handoff, "backend")
    contract = _mapping(backend_handoff, "contract")
    bundle = _mapping(contract, "bundle_index")
    openapi = _mapping(contract, "openapi")
    capability = _mapping(contract, "capability_manifest")
    frontend = _mapping(frontend_handoff, "frontend")
    backend_runtime_commit = args.backend_runtime_commit or str(backend.get("runtime_commit") or "")
    frontend_runtime_commit = args.frontend_runtime_commit or str(frontend.get("runtime_commit") or "")
    reasons.extend(
        delivery_runtime_blocking_reasons(
            backend_handoff,
            frontend_handoff,
            frontend_root=frontend_root,
            backend_runtime_commit=backend_runtime_commit,
            frontend_runtime_commit=frontend_runtime_commit,
            backend_dev_ref=args.backend_dev_ref,
            frontend_dev_ref=args.frontend_dev_ref,
        )
    )
    reasons = sorted(set(reasons))

    status = args.compatibility_status
    if status == "auto":
        status = ACCEPTED_STATUS if not reasons else "pending"
    if status == ACCEPTED_STATUS and reasons:
        raise ManifestError(
            "cannot write an accepted manifest: " + ", ".join(reasons)
        )
    if status == "rejected" and not reasons:
        reasons = ["candidate-explicitly-rejected"]

    return {
        "manifest_version": "1.0",
        "contract_family": CONTRACT_FAMILY,
        "environment": args.environment,
        "generated": True,
        "backend": {
            "repo": "ajoe734/pantheon",
            "branch": "dev",
            "runtime_commit": backend_runtime_commit,
            "contract_commit": backend["contract_commit"],
            "handoff_commit": backend_handoff_commit,
            "bundle_index_sha256": bundle["sha256"],
            "openapi_sha256": openapi["sha256"],
            "capability_manifest_sha256": capability["sha256"],
        },
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "branch": "dev",
            "runtime_commit": frontend_runtime_commit,
            "handoff_commit": frontend_handoff_commit,
            "generated_from_contract_commit": frontend["generated_from_contract_commit"],
            "bundle_index_sha256": frontend["bundle_index_sha256"],
            "openapi_sha256": frontend["openapi_sha256"],
            "generated_types_sha256": frontend["generated_types_sha256"],
        },
        "contract_bundle": {
            "bundle_index_path": BUNDLE_PATH,
            "openapi_path": OPENAPI_PATH,
            "capability_manifest_path": CAPABILITY_PATH,
            "generated_type_paths": list(DEFAULT_GENERATED_TYPE_PATHS),
        },
        "source_handoffs": {
            "backend": {
                "path": BACKEND_HANDOFF_PATH,
                "commit": backend_handoff_commit,
                "sha256": backend_handoff_sha,
            },
            "frontend": {
                "path": FRONTEND_HANDOFF_PATH,
                "commit": frontend_handoff_commit,
                "sha256": frontend_handoff_sha,
            },
        },
        "required_capabilities": required_capabilities(),
        "hash_policy": {
            "file_hash": "sha256-exact-git-bytes-v1",
            "generated_types_hash": "sha256-path-tab-filehash-lf-v1",
        },
        "compatibility_status": status,
        "blocking_reasons": reasons,
        "generated_at": args.generated_at or _text(frontend_handoff, "generated_at"),
    }


def validate_manifest_shape(manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required_top = {
        "manifest_version",
        "contract_family",
        "environment",
        "generated",
        "backend",
        "frontend",
        "contract_bundle",
        "source_handoffs",
        "required_capabilities",
        "hash_policy",
        "compatibility_status",
        "blocking_reasons",
        "generated_at",
    }
    issues.extend(
        f"missing top-level field: {field}"
        for field in sorted(required_top - set(manifest))
    )
    if manifest.get("manifest_version") != "1.0":
        issues.append("manifest_version must be 1.0")
    if manifest.get("contract_family") != CONTRACT_FAMILY:
        issues.append(f"contract_family must be {CONTRACT_FAMILY}")
    if manifest.get("environment") not in {"dev", "staging", "production"}:
        issues.append("environment must be dev, staging, or production")
    if manifest.get("generated") is not True:
        issues.append("generated must be true")
    if manifest.get("compatibility_status") not in {ACCEPTED_STATUS, *NON_ACCEPTED_STATUSES}:
        issues.append("compatibility_status must be accepted, pending, or rejected")

    expected_payloads = {
        "backend": {
            "repo": "ajoe734/pantheon",
            "branch": "dev",
            "commits": ("runtime_commit", "contract_commit", "handoff_commit"),
            "hashes": (
                "bundle_index_sha256",
                "openapi_sha256",
                "capability_manifest_sha256",
            ),
        },
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "branch": "dev",
            "commits": (
                "runtime_commit",
                "handoff_commit",
                "generated_from_contract_commit",
            ),
            "hashes": (
                "bundle_index_sha256",
                "openapi_sha256",
                "generated_types_sha256",
            ),
        },
    }
    for owner, expected in expected_payloads.items():
        payload = manifest.get(owner)
        if not isinstance(payload, dict):
            issues.append(f"{owner} must be an object")
            continue
        if payload.get("repo") != expected["repo"]:
            issues.append(f"{owner}.repo must be {expected['repo']}")
        if payload.get("branch") != expected["branch"]:
            issues.append(f"{owner}.branch must be dev")
        for key in expected["commits"]:
            value = str(payload.get(key) or "")
            if not COMMIT_RE.fullmatch(value):
                issues.append(f"{owner}.{key} must be a 40-character lowercase git sha")
        for key in expected["hashes"]:
            value = str(payload.get(key) or "")
            if not SHA_RE.fullmatch(value):
                issues.append(f"{owner}.{key} must be a 64-character lowercase sha256")

    contract_bundle = manifest.get("contract_bundle")
    expected_bundle = {
        "bundle_index_path": BUNDLE_PATH,
        "openapi_path": OPENAPI_PATH,
        "capability_manifest_path": CAPABILITY_PATH,
        "generated_type_paths": list(DEFAULT_GENERATED_TYPE_PATHS),
    }
    if not isinstance(contract_bundle, dict):
        issues.append("contract_bundle must be an object")
    elif contract_bundle != expected_bundle:
        issues.append("contract_bundle must identify the exact Agora v1.13 inputs")

    source_handoffs = manifest.get("source_handoffs")
    if not isinstance(source_handoffs, dict):
        issues.append("source_handoffs must be an object")
    else:
        for owner, expected_path in (
            ("backend", BACKEND_HANDOFF_PATH),
            ("frontend", FRONTEND_HANDOFF_PATH),
        ):
            payload = source_handoffs.get(owner)
            if not isinstance(payload, dict):
                issues.append(f"source_handoffs.{owner} must be an object")
                continue
            if payload.get("path") != expected_path:
                issues.append(f"source_handoffs.{owner}.path must be {expected_path}")
            if not COMMIT_RE.fullmatch(str(payload.get("commit") or "")):
                issues.append(f"source_handoffs.{owner}.commit must be a git sha")
            if not SHA_RE.fullmatch(str(payload.get("sha256") or "")):
                issues.append(f"source_handoffs.{owner}.sha256 must be a sha256")
            elif payload.get("sha256") == ZERO_SHA256:
                issues.append(f"source_handoffs.{owner}.sha256 must not be a placeholder")

    if manifest.get("hash_policy") != {
        "file_hash": "sha256-exact-git-bytes-v1",
        "generated_types_hash": "sha256-path-tab-filehash-lf-v1",
    }:
        issues.append("hash_policy must use the exact-byte and deterministic generated-type algorithms")
    if manifest.get("required_capabilities") != required_capabilities():
        issues.append("required_capabilities must exactly match the advertised Agora capability set")
    if not isinstance(manifest.get("blocking_reasons"), list):
        issues.append("blocking_reasons must be an array")
    return issues


def validate_handoff_bindings(
    manifest: dict[str, Any],
    *,
    frontend_root: Path,
    backend_dev_ref: str,
    frontend_dev_ref: str,
) -> list[str]:
    issues: list[str] = []
    source_handoffs = manifest.get("source_handoffs")
    if not isinstance(source_handoffs, dict):
        return issues
    backend_source = source_handoffs.get("backend")
    frontend_source = source_handoffs.get("frontend")
    if not isinstance(backend_source, dict) or not isinstance(frontend_source, dict):
        return issues
    backend_commit = str(backend_source.get("commit") or "")
    frontend_commit = str(frontend_source.get("commit") or "")
    try:
        backend_handoff, backend_sha, frontend_handoff, frontend_sha = load_handoffs(
            frontend_root=frontend_root,
            backend_handoff_commit=backend_commit,
            frontend_handoff_commit=frontend_commit,
        )
        reasons = handoff_blocking_reasons(
            backend_handoff,
            frontend_handoff,
            frontend_root=frontend_root,
            backend_handoff_commit=backend_commit,
            frontend_handoff_commit=frontend_commit,
            backend_dev_ref=backend_dev_ref,
            frontend_dev_ref=frontend_dev_ref,
        )
    except ManifestError as exc:
        return [str(exc)]
    if reasons:
        issues.extend(f"handoff validation failed: {reason}" for reason in reasons)
    if backend_source.get("sha256") != backend_sha:
        issues.append("source_handoffs.backend.sha256 does not match exact Git bytes")
    if frontend_source.get("sha256") != frontend_sha:
        issues.append("source_handoffs.frontend.sha256 does not match exact Git bytes")
    for reason in working_tree_binding_reasons(
        frontend_root=frontend_root,
        backend_handoff_sha=backend_sha,
        frontend_handoff_sha=frontend_sha,
        backend_handoff=backend_handoff,
    ):
        issues.append(f"working tree validation failed: {reason}")

    backend = _mapping(backend_handoff, "backend")
    contract = _mapping(backend_handoff, "contract")
    frontend = _mapping(frontend_handoff, "frontend")
    expected_backend = {
        "repo": "ajoe734/pantheon",
        "branch": "dev",
        "contract_commit": backend.get("contract_commit"),
        "handoff_commit": backend_commit,
        "bundle_index_sha256": _mapping(contract, "bundle_index").get("sha256"),
        "openapi_sha256": _mapping(contract, "openapi").get("sha256"),
        "capability_manifest_sha256": _mapping(contract, "capability_manifest").get("sha256"),
    }
    expected_frontend = {
        "repo": "ajoe734/execute-plans",
        "branch": "dev",
        "handoff_commit": frontend_commit,
        "generated_from_contract_commit": frontend.get("generated_from_contract_commit"),
        "bundle_index_sha256": frontend.get("bundle_index_sha256"),
        "openapi_sha256": frontend.get("openapi_sha256"),
        "generated_types_sha256": frontend.get("generated_types_sha256"),
    }
    manifest_backend = manifest.get("backend")
    manifest_frontend = manifest.get("frontend")
    actual_backend = (
        {key: value for key, value in manifest_backend.items() if key != "runtime_commit"}
        if isinstance(manifest_backend, dict)
        else manifest_backend
    )
    actual_frontend = (
        {key: value for key, value in manifest_frontend.items() if key != "runtime_commit"}
        if isinstance(manifest_frontend, dict)
        else manifest_frontend
    )
    if actual_backend != expected_backend:
        issues.append("backend identity does not exactly match the backend handoff")
    if actual_frontend != expected_frontend:
        issues.append("frontend identity does not exactly match the frontend handoff")
    if isinstance(manifest_backend, dict) and isinstance(manifest_frontend, dict):
        runtime_reasons = delivery_runtime_blocking_reasons(
            backend_handoff,
            frontend_handoff,
            frontend_root=frontend_root,
            backend_runtime_commit=str(manifest_backend.get("runtime_commit") or ""),
            frontend_runtime_commit=str(manifest_frontend.get("runtime_commit") or ""),
            backend_dev_ref=backend_dev_ref,
            frontend_dev_ref=frontend_dev_ref,
        )
        issues.extend(f"delivery runtime validation failed: {reason}" for reason in runtime_reasons)
    return issues


def validate_local_contract_hashes(manifest: dict[str, Any]) -> list[str]:
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    expected = {
        "bundle_index_sha256": sha256_file(require_local_file(BUNDLE_PATH)),
        "openapi_sha256": sha256_file(require_local_file(OPENAPI_PATH)),
        "capability_manifest_sha256": sha256_file(require_local_file(CAPABILITY_PATH)),
    }
    return [
        f"backend.{key} does not match local exact bytes: expected {actual}, got {backend.get(key)}"
        for key, actual in expected.items()
        if backend.get(key) != actual
    ]


def validate_deployment_rules(
    manifest: dict[str, Any],
    *,
    expected_backend_runtime_commit: str | None = None,
    expected_frontend_runtime_commit: str | None = None,
) -> list[str]:
    issues: list[str] = []
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    frontend = manifest.get("frontend") if isinstance(manifest.get("frontend"), dict) else {}
    if manifest.get("compatibility_status") != ACCEPTED_STATUS:
        issues.append("compatibility_status must be accepted for deployment")
    if manifest.get("blocking_reasons"):
        issues.append("blocking_reasons must be empty for deployment")

    for owner, payload in (("backend", backend), ("frontend", frontend)):
        for key, value in payload.items():
            if key.endswith("_sha256") and value == ZERO_SHA256:
                issues.append(f"{owner}.{key} is a placeholder sha256")
            if key.endswith("_commit") and value == ZERO_COMMIT:
                issues.append(f"{owner}.{key} is a placeholder commit")
    if expected_backend_runtime_commit and backend.get("runtime_commit") != expected_backend_runtime_commit:
        issues.append(
            "backend.runtime_commit does not match the required runtime identity: "
            f"expected {expected_backend_runtime_commit}, got {backend.get('runtime_commit')}"
        )
    if expected_frontend_runtime_commit and frontend.get("runtime_commit") != expected_frontend_runtime_commit:
        issues.append(
            "frontend.runtime_commit does not match the required runtime identity: "
            f"expected {expected_frontend_runtime_commit}, got {frontend.get('runtime_commit')}"
        )

    comparisons = (
        (
            "frontend.generated_from_contract_commit",
            frontend.get("generated_from_contract_commit"),
            "backend.contract_commit",
            backend.get("contract_commit"),
        ),
        (
            "frontend.bundle_index_sha256",
            frontend.get("bundle_index_sha256"),
            "backend.bundle_index_sha256",
            backend.get("bundle_index_sha256"),
        ),
        (
            "frontend.openapi_sha256",
            frontend.get("openapi_sha256"),
            "backend.openapi_sha256",
            backend.get("openapi_sha256"),
        ),
    )
    for left_name, left, right_name, right in comparisons:
        if left != right:
            issues.append(f"{left_name} must equal {right_name}: {left} != {right}")
    return issues


def verify_manifest(
    manifest_path: Path,
    *,
    frontend_root: Path,
    backend_dev_ref: str = DEFAULT_BACKEND_DEV_REF,
    frontend_dev_ref: str = DEFAULT_FRONTEND_DEV_REF,
    allow_pending: bool = False,
    deployment_gate: bool = False,
    expected_backend_runtime_commit: str | None = None,
    expected_frontend_runtime_commit: str | None = None,
) -> list[str]:
    require_git_repo(REPO_ROOT, "Pantheon root")
    require_git_repo(frontend_root, "execute-plans root")
    manifest = read_json(manifest_path)
    issues = validate_manifest_shape(manifest)
    issues.extend(validate_local_contract_hashes(manifest))
    issues.extend(
        validate_handoff_bindings(
            manifest,
            frontend_root=frontend_root,
            backend_dev_ref=backend_dev_ref,
            frontend_dev_ref=frontend_dev_ref,
        )
    )
    if deployment_gate or not allow_pending:
        issues.extend(
            validate_deployment_rules(
                manifest,
                expected_backend_runtime_commit=expected_backend_runtime_commit,
                expected_frontend_runtime_commit=expected_frontend_runtime_commit,
            )
        )
    return issues


def build_gate_evidence(
    manifest_path: Path,
    *,
    frontend_root: Path,
    backend_runtime_commit: str,
    frontend_runtime_commit: str,
) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    controller_commit = git_output(REPO_ROOT, ["rev-parse", "HEAD"])
    if not COMMIT_RE.fullmatch(controller_commit):
        raise ManifestError("cannot resolve the Pantheon gate controller commit")
    release_candidate_identity = {
        "schema_version": RELEASE_CANDIDATE_SCHEMA,
        "environment": manifest["environment"],
        "compatibility_status": RELEASE_COMPATIBILITY_STATUS,
        "compatibility_manifest": {
            "contract_family": manifest["contract_family"],
            "manifest_version": manifest["manifest_version"],
            "sha256": sha256_file(manifest_path),
            "source_status": manifest["compatibility_status"],
        },
        "backend": {
            "repository": "ajoe734/pantheon",
            "branch": "dev",
            "commit": backend_runtime_commit,
            "tree": git_tree(REPO_ROOT, backend_runtime_commit),
        },
        "frontend": {
            "repository": "ajoe734/execute-plans",
            "branch": "dev",
            "commit": frontend_runtime_commit,
            "tree": git_tree(frontend_root, frontend_runtime_commit),
        },
    }
    release_candidate = {
        **release_candidate_identity,
        "release_candidate_id": canonical_json_sha256(release_candidate_identity),
    }
    return {
        "schema_version": GATE_EVIDENCE_SCHEMA,
        "contract_family": manifest["contract_family"],
        "environment": manifest["environment"],
        "compatibility_status": manifest["compatibility_status"],
        "blocking_reasons": manifest["blocking_reasons"],
        "manifest_sha256": sha256_file(manifest_path),
        "gate_controller": {
            "repo": "ajoe734/pantheon",
            "commit": controller_commit,
            "tree": git_tree(REPO_ROOT, controller_commit),
        },
        "backend": {
            "repo": "ajoe734/pantheon",
            "runtime_commit": backend_runtime_commit,
            "tree": git_tree(REPO_ROOT, backend_runtime_commit),
        },
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "runtime_commit": frontend_runtime_commit,
            "tree": git_tree(frontend_root, frontend_runtime_commit),
        },
        "source_handoffs": manifest["source_handoffs"],
        "hash_policy": manifest["hash_policy"],
        "release_candidate": release_candidate,
    }


def print_issues(issues: list[str]) -> None:
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)


def command_write(args: argparse.Namespace) -> int:
    try:
        manifest = build_manifest(args)
        if args.stdout:
            print(json.dumps(manifest, indent=2, sort_keys=False))
        else:
            write_json(Path(args.output), manifest)
            print(f"written {Path(args.output)}")
        print(f"compatibility_status={manifest['compatibility_status']}")
        for reason in manifest.get("blocking_reasons", []):
            print(f"blocking_reason={reason}")
        return 0
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _verify_from_args(args: argparse.Namespace, *, deployment_gate: bool) -> int:
    try:
        issues = verify_manifest(
            Path(args.manifest),
            frontend_root=Path(args.frontend_root).expanduser().resolve(),
            backend_dev_ref=args.backend_dev_ref,
            frontend_dev_ref=args.frontend_dev_ref,
            allow_pending=getattr(args, "allow_pending", False),
            deployment_gate=deployment_gate,
            expected_backend_runtime_commit=args.backend_runtime_commit or None,
            expected_frontend_runtime_commit=args.frontend_runtime_commit or None,
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if issues:
        print_issues(issues)
        return 1
    if deployment_gate:
        if args.evidence_out:
            if not args.backend_runtime_commit or not args.frontend_runtime_commit:
                print(
                    "ERROR: --evidence-out requires both --backend-runtime-commit "
                    "and --frontend-runtime-commit",
                    file=sys.stderr,
                )
                return 1
            try:
                evidence = build_gate_evidence(
                    Path(args.manifest),
                    frontend_root=Path(args.frontend_root).expanduser().resolve(),
                    backend_runtime_commit=args.backend_runtime_commit,
                    frontend_runtime_commit=args.frontend_runtime_commit,
                )
                write_json(Path(args.evidence_out), evidence)
            except ManifestError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            print(f"gate evidence written {Path(args.evidence_out)}")
        print(f"deployment gate passed {Path(args.manifest)}")
    else:
        print(f"ok {Path(args.manifest)}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    return _verify_from_args(args, deployment_gate=False)


def command_deployment_gate(args: argparse.Namespace) -> int:
    return _verify_from_args(args, deployment_gate=True)


def add_repo_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frontend-root", default=str(DEFAULT_FRONTEND_ROOT))
    parser.add_argument("--backend-dev-ref", default=DEFAULT_BACKEND_DEV_REF)
    parser.add_argument("--frontend-dev-ref", default=DEFAULT_FRONTEND_DEV_REF)


def add_write_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("write", help="generate the exact dev compatibility manifest")
    parser.add_argument("--output", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--environment", default="dev", choices=["dev", "staging", "production"])
    parser.add_argument("--backend-handoff-commit", default="")
    parser.add_argument("--frontend-handoff-commit", default="")
    parser.add_argument(
        "--backend-runtime-commit",
        default="",
        help="exact descendant Pantheon payload to accept instead of the handoff runtime base",
    )
    parser.add_argument(
        "--frontend-runtime-commit",
        default="",
        help="exact descendant execute-plans payload to accept instead of the handoff runtime base",
    )
    parser.add_argument("--generated-at", default="")
    parser.add_argument(
        "--compatibility-status",
        default="auto",
        choices=["auto", ACCEPTED_STATUS, *sorted(NON_ACCEPTED_STATUSES)],
    )
    add_repo_arguments(parser)
    parser.set_defaults(func=command_write)


def add_verify_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("verify", help="verify handoffs, hashes, and dev reachability")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--backend-runtime-commit", default="")
    parser.add_argument("--frontend-runtime-commit", default="")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="allow a pending/rejected status while still checking identities and hashes",
    )
    add_repo_arguments(parser)
    parser.set_defaults(func=command_verify)


def add_deployment_gate_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "deployment-gate",
        help="fail unless the exact accepted pair is reachable and byte-identical",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--backend-runtime-commit", default="")
    parser.add_argument("--frontend-runtime-commit", default="")
    parser.add_argument("--evidence-out", default="")
    add_repo_arguments(parser)
    parser.set_defaults(func=command_deployment_gate)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_write_parser(subparsers)
    add_verify_parser(subparsers)
    add_deployment_gate_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
