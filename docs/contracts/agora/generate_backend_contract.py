#!/usr/bin/env python3
"""Build and verify the Agora v1.13 backend generation contract.

The v1.13 leaf is a deterministic aggregate of the implemented v1.10,
v1.11, and v1.12 contract leaves.  It gives execute-plans one OpenAPI entry
point without changing the frozen source leaves.  The separate backend
handoff binds those exact bytes to reachable Pantheon runtime and contract
commits while compatibility remains pending for frontend evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_VERSION = "1.13"
CONTRACT_FAMILY = f"agora.v{CONTRACT_VERSION}"
ZERO_COMMIT = "0" * 40
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

OPENAPI_PATH = Path("services/control-plane/openapi/agora_v1_13.openapi.yaml")
CAPABILITY_PATH = Path(
    "services/control-plane/specs/agora/v14/capability_manifest_v1_13.json"
)
BUNDLE_PATH = Path("services/control-plane/specs/agora/bundle_index.v1_13.json")
HANDOFF_PATH = Path("docs/contracts/agora/backend-generation-input.v1_13.json")
GENERATOR_PATH = Path("docs/contracts/agora/generate_backend_contract.py")
PARENT_BUNDLE_PATH = Path(
    "services/control-plane/specs/agora/bundle_index.v1_12.json"
)

LEAVES = (
    {
        "alias": "V110",
        "bundle": Path("services/control-plane/specs/agora/bundle_index.v1_10.json"),
        "manifest": Path(
            "services/control-plane/specs/agora/v11/capability_manifest_v1_10.json"
        ),
        "openapi": Path("services/control-plane/openapi/agora_v1_10.openapi.yaml"),
    },
    {
        "alias": "V111",
        "bundle": Path("services/control-plane/specs/agora/bundle_index.v1_11.json"),
        "manifest": Path(
            "services/control-plane/specs/agora/v12/capability_manifest_v1_11.json"
        ),
        "openapi": Path("services/control-plane/openapi/agora_v1_11.openapi.yaml"),
    },
    {
        "alias": "V112",
        "bundle": Path("services/control-plane/specs/agora/bundle_index.v1_12.json"),
        "manifest": Path(
            "services/control-plane/specs/agora/v13/capability_manifest_v1_12.json"
        ),
        "openapi": Path("services/control-plane/openapi/agora_v1_12.openapi.yaml"),
    },
)

EXPECTED_ROUTES = {
    "GET /bff/agora/trading-room/strategies/{strategy_id}/performance",
    "POST /bff/agora/trading-room/strategies/{strategy_id}/performance/suggestions/{suggestion_id}/actions",
    "GET /bff/agora/performance/action-receipts/{receipt_id}",
    "GET /bff/agora/workshops/{workshop_id}/versions",
    "POST /bff/agora/workshops/{workshop_id}/versions",
    "POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
    "POST /bff/agora/workshops/{workshop_id}/research-runs",
    "POST /bff/agora/workshops/{workshop_id}/consultations",
    "POST /bff/agora/workshops/{workshop_id}/conclude",
    "GET /bff/agora/candidate-pools/{pool_id}/members",
    "GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}",
}

REQUIRED_FRONTEND_EVIDENCE = (
    "frontend_runtime_commit",
    "generated_from_contract_commit",
    "bundle_index_sha256",
    "openapi_sha256",
    "generated_types_sha256",
)
PENDING_REASONS = (
    "frontend-generated-contract-commit-not-supplied",
    "frontend-generated-types-not-supplied",
    "frontend-runtime-commit-not-supplied",
)


class ContractError(ValueError):
    """Raised when generation or verification cannot continue."""


def _read_bytes(path: Path) -> bytes:
    try:
        return (REPO_ROOT / path).read_bytes()
    except FileNotFoundError as exc:
        raise ContractError(f"missing required file: {path.as_posix()}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_read_bytes(path))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {path.as_posix()}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"expected JSON object: {path.as_posix()}")
    return payload


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(_read_bytes(path))
    except yaml.YAMLError as exc:
        raise ContractError(f"invalid YAML in {path.as_posix()}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"expected YAML object: {path.as_posix()}")
    return payload


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _yaml_bytes(payload: Any) -> bytes:
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    ).encode("utf-8")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(_read_bytes(path))


def _git(*args: str, allow_failure: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 and not allow_failure:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ContractError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _git_bytes(commit: str, path: Path) -> bytes:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{path.as_posix()}"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise ContractError(
            f"{path.as_posix()} is not readable from contract commit {commit}: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout


def _commit_timestamp(commit: str) -> str:
    value = _git("show", "-s", "--format=%cI", commit)
    return value[:-6] + "Z" if value.endswith("+00:00") else value


def _is_ancestor(older: str, newer: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.returncode == 0


def _route_set(spec: dict[str, Any]) -> set[str]:
    routes: set[str] = set()
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in path_item:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                routes.add(f"{method.upper()} {path}")
    return routes


def _rewrite_local_refs(payload: Any, mapping: dict[tuple[str, str], str]) -> Any:
    if isinstance(payload, list):
        return [_rewrite_local_refs(item, mapping) for item in payload]
    if not isinstance(payload, dict):
        return copy.deepcopy(payload)
    rewritten: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "$ref" and isinstance(value, str) and value.startswith("#/components/"):
            pieces = value.split("/")
            if len(pieces) == 4:
                target = mapping.get((pieces[2], pieces[3]), pieces[3])
                rewritten[key] = f"#/components/{pieces[2]}/{target}"
                continue
        rewritten[key] = _rewrite_local_refs(value, mapping)
    return rewritten


def _component_mappings(
    specs: list[tuple[str, dict[str, Any]]],
) -> dict[str, dict[tuple[str, str], str]]:
    occurrences: dict[tuple[str, str], list[tuple[str, Any]]] = defaultdict(list)
    for alias, spec in specs:
        for section, entries in (spec.get("components") or {}).items():
            if not isinstance(entries, dict):
                continue
            for name, payload in entries.items():
                occurrences[(section, name)].append((alias, payload))

    mappings: dict[str, dict[tuple[str, str], str]] = {alias: {} for alias, _ in specs}
    for (section, name), variants in occurrences.items():
        distinct = {_canonical_json(payload) for _, payload in variants}
        for alias, _ in variants:
            mappings[alias][(section, name)] = name if len(distinct) == 1 else f"{alias}{name}"
    return mappings


def _build_openapi() -> dict[str, Any]:
    specs = [(str(leaf["alias"]), _read_yaml(leaf["openapi"])) for leaf in LEAVES]
    mappings = _component_mappings(specs)
    components: dict[str, dict[str, Any]] = {}
    paths: dict[str, Any] = {}

    for alias, spec in specs:
        mapping = mappings[alias]
        for section, entries in (spec.get("components") or {}).items():
            if not isinstance(entries, dict):
                continue
            destination = components.setdefault(section, {})
            for name, payload in entries.items():
                target = mapping[(section, name)]
                rewritten = _rewrite_local_refs(payload, mapping)
                existing = destination.get(target)
                if existing is not None and _canonical_json(existing) != _canonical_json(rewritten):
                    raise ContractError(f"unresolved component collision: {section}/{target}")
                destination[target] = rewritten

        for path, path_item in (spec.get("paths") or {}).items():
            if path in paths:
                raise ContractError(f"duplicate aggregate route path: {path}")
            paths[path] = _rewrite_local_refs(path_item, mapping)

    openapi = {
        "openapi": "3.1.0",
        "info": {
            "title": "Pantheon Agora API v1.13 Complete Backend Contract",
            "version": "1.13.0",
            "description": (
                "Deterministic additive aggregate over the exact v1.12 bundle for frontend "
                "generation. It combines the implemented Strategy Performance truth, durable "
                "Workshop version and operation lifecycle, and candidate member truth leaves. "
                "Availability/provenance envelopes and governed write receipts are preserved; "
                "no route in this aggregate has execution authority or a deferred/501 disposition."
            ),
            "x-extends-contract": PARENT_BUNDLE_PATH.as_posix(),
            "x-capability-manifest": CAPABILITY_PATH.as_posix(),
            "x-implementation-status": "implemented",
            "x-source-contracts": [leaf["openapi"].as_posix() for leaf in LEAVES],
        },
        "servers": copy.deepcopy(specs[0][1].get("servers") or []),
        "security": copy.deepcopy(specs[0][1].get("security") or []),
        "components": components,
        "paths": paths,
    }
    if _route_set(openapi) != EXPECTED_ROUTES:
        raise ContractError(
            "aggregate route set mismatch: "
            f"expected {sorted(EXPECTED_ROUTES)}, got {sorted(_route_set(openapi))}"
        )
    return openapi


def _definition_checksums() -> dict[str, str]:
    schema_paths: set[Path] = set()
    for leaf in LEAVES:
        bundle = _read_json(leaf["bundle"])
        for relative in (bundle.get("files") or {}):
            if relative.endswith(".schema.json"):
                schema_paths.add(Path("services/control-plane") / relative)

    checksums: dict[str, str] = {}
    for path in sorted(schema_paths):
        schema = _read_json(path)
        for name, definition in sorted((schema.get("definitions") or {}).items()):
            key = f"{path.as_posix()}#/definitions/{name}"
            checksums[key] = _sha256_bytes(_canonical_json(definition).encode("utf-8"))
    return checksums


def _source_contracts() -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for leaf in LEAVES:
        sources.append(
            {
                "bundle_path": leaf["bundle"].as_posix(),
                "bundle_sha256": _sha256_path(leaf["bundle"]),
                "manifest_path": leaf["manifest"].as_posix(),
                "manifest_sha256": _sha256_path(leaf["manifest"]),
                "openapi_path": leaf["openapi"].as_posix(),
                "openapi_sha256": _sha256_path(leaf["openapi"]),
            }
        )
    return sources


def _build_capability_manifest() -> dict[str, Any]:
    capabilities: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for leaf in LEAVES:
        manifest = _read_json(leaf["manifest"])
        for capability in manifest.get("capabilities") or []:
            if not isinstance(capability, dict):
                raise ContractError(f"invalid capability in {leaf['manifest'].as_posix()}")
            name = str(capability.get("name") or capability.get("id") or "")
            if not name or name in seen_names:
                raise ContractError(f"missing or duplicate capability name: {name!r}")
            seen_names.add(name)
            capabilities.append(copy.deepcopy(capability))

    return {
        "manifest_version": CONTRACT_VERSION,
        "extends": "v1.12",
        "description": (
            "Complete deterministic Agora backend contract input for frontend generation. "
            "The source leaves remain immutable and all advertised routes are implemented."
        ),
        "schema_bundle_index": BUNDLE_PATH.as_posix(),
        "openapi_ref": OPENAPI_PATH.as_posix(),
        "capabilities": capabilities,
        "source_contracts": _source_contracts(),
        "required_definition_checksums": _definition_checksums(),
        "compatibility": {
            "status": "pending",
            "blocking_reasons": list(PENDING_REASONS),
            "accepted_only_after": list(REQUIRED_FRONTEND_EVIDENCE),
        },
        "authority_boundary": {
            "execution_authority": "none",
            "runtime_binding_write": False,
            "capital_binding": False,
            "broker_order_submission": False,
        },
        "task_id": "AG-COMPAT-001-BE",
    }


def build_bundle_artifacts() -> dict[Path, bytes]:
    openapi = _build_openapi()
    capability = _build_capability_manifest()
    openapi_bytes = _yaml_bytes(openapi)
    capability_bytes = _json_bytes(capability)
    parent_sha = _sha256_path(PARENT_BUNDLE_PATH)

    route_families = {
        str(capability.get("name") or capability.get("id")): list(
            capability.get("routes") or []
        )
        for capability in capability["capabilities"]
    }
    bundle = {
        "bundle_version": CONTRACT_VERSION,
        "extends": {
            "bundle_path": PARENT_BUNDLE_PATH.as_posix(),
            "bundle_version": "1.12",
            "bundle_index_sha256": parent_sha,
        },
        "files": {
            CAPABILITY_PATH.as_posix().removeprefix("services/control-plane/"): _sha256_bytes(
                capability_bytes
            )
        },
        "openapi": {
            "path": OPENAPI_PATH.as_posix(),
            "sha256": _sha256_bytes(openapi_bytes),
        },
        "source_contracts": capability["source_contracts"],
        "required_definition_checksums": capability["required_definition_checksums"],
        "route_families": route_families,
        "implementation_status": "implemented",
        "compatibility_status": "pending",
        "blocking_reasons": list(PENDING_REASONS),
        "hash_policy": {
            "file_hash": "sha256-exact-git-bytes-v1",
            "definition_hash": "sha256-canonical-json-sort-keys-compact-v1",
        },
        "note": (
            "AG-COMPAT-001-BE aggregate over exact v1.10-v1.12 leaves. Frontend identity "
            "and generated-type evidence are intentionally absent, so compatibility is pending."
        ),
    }
    return {
        OPENAPI_PATH: openapi_bytes,
        CAPABILITY_PATH: capability_bytes,
        BUNDLE_PATH: _json_bytes(bundle),
    }


def _external_ref_paths(payload: Any, owner: Path) -> set[Path]:
    paths: set[Path] = set()
    if isinstance(payload, list):
        for item in payload:
            paths.update(_external_ref_paths(item, owner))
        return paths
    if not isinstance(payload, dict):
        return paths
    for key, value in payload.items():
        if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
            raw_path = value.split("#", 1)[0]
            resolved = (owner.parent / raw_path).resolve()
            try:
                relative = resolved.relative_to(REPO_ROOT)
            except ValueError as exc:
                raise ContractError(f"external ref escapes repository: {value}") from exc
            paths.add(relative)
        else:
            paths.update(_external_ref_paths(value, owner))
    return paths


def _frontend_required_files() -> list[Path]:
    pending = [OPENAPI_PATH]
    discovered: set[Path] = {OPENAPI_PATH}
    while pending:
        owner = pending.pop()
        payload = _read_yaml(owner) if owner.suffix in {".yaml", ".yml"} else _read_json(owner)
        for path in _external_ref_paths(payload, REPO_ROOT / owner):
            if path not in discovered:
                discovered.add(path)
                pending.append(path)
    return sorted(discovered)


def _derivation_files() -> list[Path]:
    paths: set[Path] = {GENERATOR_PATH, BUNDLE_PATH, CAPABILITY_PATH, OPENAPI_PATH}
    for leaf in LEAVES:
        paths.update({leaf["bundle"], leaf["manifest"], leaf["openapi"]})
        bundle = _read_json(leaf["bundle"])
        for relative in (bundle.get("files") or {}):
            paths.add(Path("services/control-plane") / relative)
    paths.update(_frontend_required_files())
    return sorted(paths)


def _validate_commit(commit: str, label: str) -> None:
    if not COMMIT_RE.fullmatch(commit) or commit == ZERO_COMMIT:
        raise ContractError(f"{label} must be a non-placeholder 40-character lowercase git SHA")
    resolved = _git("rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ContractError(f"{label} does not resolve exactly to {commit}: {resolved}")


def _validate_contract_identity(runtime_commit: str, contract_commit: str) -> None:
    _validate_commit(runtime_commit, "backend runtime commit")
    _validate_commit(contract_commit, "backend contract commit")
    head = _git("rev-parse", "HEAD")
    if not _is_ancestor(runtime_commit, contract_commit):
        raise ContractError("backend runtime commit must be an ancestor of the contract commit")
    if not _is_ancestor(contract_commit, head):
        raise ContractError("backend contract commit must be an ancestor of HEAD")
    for path in _derivation_files():
        local = _read_bytes(path)
        committed = _git_bytes(contract_commit, path)
        if local != committed:
            raise ContractError(
                f"exact-byte mismatch for {path.as_posix()} at contract commit {contract_commit}"
            )


def build_handoff(runtime_commit: str, contract_commit: str) -> dict[str, Any]:
    _validate_contract_identity(runtime_commit, contract_commit)
    required_files = _frontend_required_files()
    source_files = _derivation_files()
    return {
        "input_version": "1.0",
        "contract_family": CONTRACT_FAMILY,
        "generated": True,
        "backend": {
            "repo": "ajoe734/pantheon",
            "runtime_commit": runtime_commit,
            "contract_commit": contract_commit,
        },
        "contract": {
            "bundle_index": {
                "path": BUNDLE_PATH.as_posix(),
                "sha256": _sha256_path(BUNDLE_PATH),
            },
            "openapi": {
                "path": OPENAPI_PATH.as_posix(),
                "sha256": _sha256_path(OPENAPI_PATH),
            },
            "capability_manifest": {
                "path": CAPABILITY_PATH.as_posix(),
                "sha256": _sha256_path(CAPABILITY_PATH),
            },
        },
        "frontend_generation": {
            "entrypoint": OPENAPI_PATH.as_posix(),
            "format": "openapi-3.1",
            "required_files": [path.as_posix() for path in required_files],
            "expected_output_paths": [
                "src/lib/bff-v1/agora/contract-snapshot.json",
                "src/lib/bff-v1/agora/types.ts",
            ],
            "file_hash_algorithm": "sha256-exact-git-bytes-v1",
            "generated_types_hash_algorithm": "sha256-path-tab-filehash-lf-v1",
        },
        "source_files": [
            {"path": path.as_posix(), "sha256": _sha256_path(path)} for path in source_files
        ],
        "compatibility": {
            "status": "pending",
            "blocking_reasons": list(PENDING_REASONS),
            "required_frontend_evidence": list(REQUIRED_FRONTEND_EVIDENCE),
        },
        "generated_at": _commit_timestamp(contract_commit),
    }


def _write_or_check(artifacts: dict[Path, bytes], output_root: Path, check: bool) -> None:
    mismatches: list[str] = []
    for relative, expected in artifacts.items():
        path = output_root / relative
        if check:
            actual = path.read_bytes() if path.is_file() else None
            if actual != expected:
                mismatches.append(relative.as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if mismatches:
        raise ContractError(f"generated contract drift: {', '.join(mismatches)}")


def _validate_openapi() -> None:
    spec = _read_yaml(OPENAPI_PATH)
    if spec.get("openapi") != "3.1.0":
        raise ContractError("aggregate OpenAPI version must be 3.1.0")
    if _route_set(spec) != EXPECTED_ROUTES:
        raise ContractError("checked-in aggregate route set is stale")
    for path, path_item in (spec.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict) or not operation.get("operationId"):
                raise ContractError(f"missing operationId for {method.upper()} {path}")
            responses = operation.get("responses") or {}
            if "501" in responses:
                raise ContractError(f"stale 501 disposition for {method.upper()} {path}")
    lowered = _read_bytes(OPENAPI_PATH).lower()
    if b"not_implemented" in lowered or b"contract_only" in lowered:
        raise ContractError("aggregate OpenAPI contains a stale implementation disposition")
    for path in _frontend_required_files():
        if not (REPO_ROOT / path).is_file():
            raise ContractError(f"unresolved external ref file: {path.as_posix()}")


def verify_handoff(path: Path) -> None:
    _write_or_check(build_bundle_artifacts(), REPO_ROOT, check=True)
    _validate_openapi()
    handoff = _read_json(path)
    if handoff.get("contract_family") != CONTRACT_FAMILY or handoff.get("generated") is not True:
        raise ContractError("handoff contract family/generated marker is invalid")
    backend = handoff.get("backend") if isinstance(handoff.get("backend"), dict) else {}
    runtime_commit = str(backend.get("runtime_commit") or "")
    contract_commit = str(backend.get("contract_commit") or "")
    expected = build_handoff(runtime_commit, contract_commit)
    if handoff != expected:
        raise ContractError("backend generation handoff differs from deterministic regeneration")
    for entry in handoff.get("source_files") or []:
        if not isinstance(entry, dict) or not SHA256_RE.fullmatch(str(entry.get("sha256") or "")):
            raise ContractError("handoff source_files contains an invalid sha256")
    compatibility = handoff.get("compatibility") or {}
    if compatibility.get("status") != "pending":
        raise ContractError("backend-only handoff must remain pending")


def command_bundle(args: argparse.Namespace) -> int:
    _write_or_check(build_bundle_artifacts(), Path(args.output_root).resolve(), args.check)
    print("verified" if args.check else "written", CONTRACT_FAMILY, "bundle")
    return 0


def command_handoff(args: argparse.Namespace) -> int:
    _write_or_check(build_bundle_artifacts(), REPO_ROOT, check=True)
    handoff = build_handoff(args.backend_runtime_commit, args.backend_contract_commit)
    output = Path(args.output).resolve()
    payload = _json_bytes(handoff)
    if args.check:
        if not output.is_file() or output.read_bytes() != payload:
            raise ContractError(f"generated handoff drift: {output}")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
    print("verified" if args.check else "written", output)
    return 0


def command_verify(args: argparse.Namespace) -> int:
    verify_handoff(Path(args.handoff))
    print("verified", Path(args.handoff))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bundle = subparsers.add_parser("bundle", help="write or check the v1.13 aggregate")
    bundle.add_argument("--output-root", default=str(REPO_ROOT))
    bundle.add_argument("--check", action="store_true")
    bundle.set_defaults(func=command_bundle)

    handoff = subparsers.add_parser("handoff", help="write or check the backend handoff")
    handoff.add_argument("--backend-runtime-commit", required=True)
    handoff.add_argument("--backend-contract-commit", required=True)
    handoff.add_argument("--output", default=str(REPO_ROOT / HANDOFF_PATH))
    handoff.add_argument("--check", action="store_true")
    handoff.set_defaults(func=command_handoff)

    verify = subparsers.add_parser("verify", help="verify bundle, hashes, refs, and handoff")
    verify.add_argument("--handoff", default=str(HANDOFF_PATH))
    verify.set_defaults(func=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
