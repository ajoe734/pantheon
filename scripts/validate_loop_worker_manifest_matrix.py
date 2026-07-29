#!/usr/bin/env python3
"""Validate per-worker auth and durable-volume applicability against Compose.

The L12 runtime manifest owns a fixed worker inventory in
``scripts/deploy_nonprod_vm.sh``.  A rendered Compose service list alone cannot
show whether authentication or a named durable volume applies to each worker,
and a raw mount count cannot distinguish a stateless client from a missing
state mount.  This validator joins those three sources:

* the deploy script's ``REQUIRED_LOOP_WORKERS`` array;
* a task-owned applicability matrix;
* ``docker compose config --format json`` readback.

Declared gaps fail by default.  ``--allow-declared-gaps`` is an audit mode for
proving that a matrix truthfully reproduces an unresolved parent-task gap
without treating that gap as deployment-ready.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "loop_worker_manifest_applicability.v1"
AUTH_APPLICABILITY = {
    "manifest_required",
    "conditional",
    "service_default",
    "not_applicable",
}
VOLUME_APPLICABILITY = {
    "manifest_required",
    "delegated",
    "not_applicable",
}
ADMISSION_STATUSES = {"pass", "gap"}


def parse_required_loop_workers(deploy_script: Path) -> list[str]:
    """Read the literal REQUIRED_LOOP_WORKERS shell array without sourcing it."""

    lines = deploy_script.read_text(encoding="utf-8").splitlines()
    in_array = False
    workers: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not in_array:
            if stripped == "REQUIRED_LOOP_WORKERS=(":
                in_array = True
            continue
        if stripped == ")":
            if not workers:
                raise ValueError("REQUIRED_LOOP_WORKERS is empty")
            return workers
        if not stripped or stripped.startswith("#"):
            continue
        token = stripped.split("#", 1)[0].strip()
        if not token or any(character.isspace() for character in token):
            raise ValueError(
                f"unsupported REQUIRED_LOOP_WORKERS entry {stripped!r}"
            )
        workers.append(token)
    raise ValueError("REQUIRED_LOOP_WORKERS array not found or unterminated")


def render_compose(
    *,
    compose_file: Path,
    env_file: Path,
    compose_json: Path | None,
) -> Mapping[str, Any]:
    if compose_json is not None:
        payload = json.loads(compose_json.read_text(encoding="utf-8"))
    else:
        command = [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "--env-file",
            str(env_file),
            "config",
            "--format",
            "json",
        ]
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"docker compose config failed ({completed.returncode}): {detail}"
            )
        payload = json.loads(completed.stdout)
    if not isinstance(payload, Mapping):
        raise ValueError("rendered Compose payload must be an object")
    services = payload.get("services")
    if not isinstance(services, Mapping):
        raise ValueError("rendered Compose payload must contain a services object")
    return payload


def _string_list(value: Any, *, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        errors.append(f"{field} must be a list of non-empty strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{field} must not contain duplicates")
    return list(value)


def _nonempty_environment(
    environment: Mapping[str, Any],
    key: str,
) -> bool:
    value = environment.get(key)
    return value is not None and bool(str(value).strip())


def _auth_expectation_satisfied(
    block: Mapping[str, Any],
    environment: Mapping[str, Any],
    *,
    field: str,
    errors: list[str],
) -> tuple[bool, list[str]]:
    all_of = _string_list(
        block.get("environment_all_of", []),
        field=f"{field}.environment_all_of",
        errors=errors,
    )
    any_of = _string_list(
        block.get("environment_any_of", []),
        field=f"{field}.environment_any_of",
        errors=errors,
    )
    missing = [key for key in all_of if not _nonempty_environment(environment, key)]
    if any_of and not any(_nonempty_environment(environment, key) for key in any_of):
        missing.append("any_of(" + ",".join(any_of) + ")")
    return not missing, missing


def _condition_active(
    condition: Any,
    environment: Mapping[str, Any],
    *,
    field: str,
    errors: list[str],
) -> bool:
    if not isinstance(condition, Mapping):
        errors.append(f"{field}.condition must be an object")
        return True
    key = condition.get("environment")
    if not isinstance(key, str) or not key.strip():
        errors.append(f"{field}.condition.environment must be a non-empty string")
        return True
    if "equals" in condition:
        expected = str(condition["equals"])
        return str(environment.get(key, "")) == expected
    if condition.get("nonempty") is True:
        return _nonempty_environment(environment, key)
    errors.append(f"{field}.condition must declare equals or nonempty=true")
    return True


def _named_volume_targets(service_config: Mapping[str, Any]) -> list[str]:
    volumes = service_config.get("volumes") or []
    if not isinstance(volumes, list):
        return []
    return sorted(
        str(volume.get("target"))
        for volume in volumes
        if isinstance(volume, Mapping)
        and volume.get("type") == "volume"
        and str(volume.get("target") or "").strip()
    )


def validate_matrix(
    *,
    matrix: Mapping[str, Any],
    compose: Mapping[str, Any],
    required_workers: Sequence[str],
) -> dict[str, Any]:
    """Return a deterministic validation report without applying gap policy."""

    errors: list[str] = []
    declared_gaps: list[dict[str, str]] = []
    services = compose["services"]

    if matrix.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION!r}")

    workers = matrix.get("workers")
    if not isinstance(workers, list):
        errors.append("workers must be a list")
        workers = []

    by_service: dict[str, Mapping[str, Any]] = {}
    for index, raw_entry in enumerate(workers):
        field = f"workers[{index}]"
        if not isinstance(raw_entry, Mapping):
            errors.append(f"{field} must be an object")
            continue
        service = raw_entry.get("service")
        if not isinstance(service, str) or not service.strip():
            errors.append(f"{field}.service must be a non-empty string")
            continue
        if service in by_service:
            errors.append(f"duplicate worker matrix entry for {service}")
            continue
        by_service[service] = raw_entry

    required_set = set(required_workers)
    matrix_set = set(by_service)
    missing_entries = sorted(required_set - matrix_set)
    unexpected_entries = sorted(matrix_set - required_set)
    if missing_entries:
        errors.append("matrix is missing required workers: " + ", ".join(missing_entries))
    if unexpected_entries:
        errors.append(
            "matrix contains workers outside REQUIRED_LOOP_WORKERS: "
            + ", ".join(unexpected_entries)
        )

    compose_set = set(services)
    missing_services = sorted(required_set - compose_set)
    if missing_services:
        errors.append(
            "rendered Compose is missing required workers: "
            + ", ".join(missing_services)
        )

    zero_named_volume_services: list[str] = []
    auth_counts = {"pass": 0, "gap": 0}
    volume_counts = {"pass": 0, "gap": 0}

    for service in required_workers:
        entry = by_service.get(service)
        service_config = services.get(service)
        if entry is None or not isinstance(service_config, Mapping):
            continue
        field = f"workers[{service}]"
        environment = service_config.get("environment") or {}
        if not isinstance(environment, Mapping):
            errors.append(f"{field}: rendered environment must be an object")
            environment = {}

        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{field}.rationale must be a non-empty string")
        evidence_refs = _string_list(
            entry.get("evidence_refs", []),
            field=f"{field}.evidence_refs",
            errors=errors,
        )
        if not evidence_refs:
            errors.append(f"{field}.evidence_refs must not be empty")

        auth = entry.get("auth")
        if not isinstance(auth, Mapping):
            errors.append(f"{field}.auth must be an object")
        else:
            auth_applicability = auth.get("applicability")
            auth_status = auth.get("status")
            if auth_applicability not in AUTH_APPLICABILITY:
                errors.append(
                    f"{field}.auth.applicability must be one of "
                    + ", ".join(sorted(AUTH_APPLICABILITY))
                )
            if auth_status not in ADMISSION_STATUSES:
                errors.append(f"{field}.auth.status must be pass or gap")
            else:
                auth_counts[auth_status] += 1
            auth_rationale = auth.get("rationale")
            if not isinstance(auth_rationale, str) or not auth_rationale.strip():
                errors.append(f"{field}.auth.rationale must be a non-empty string")

            expectation_applies = auth_applicability == "manifest_required"
            if auth_applicability == "conditional":
                expectation_applies = _condition_active(
                    auth.get("condition"),
                    environment,
                    field=f"{field}.auth",
                    errors=errors,
                )
            has_expectation = bool(auth.get("environment_all_of")) or bool(
                auth.get("environment_any_of")
            )
            satisfied = True
            missing: list[str] = []
            if expectation_applies:
                if not has_expectation:
                    errors.append(
                        f"{field}.auth needs an environment expectation when applicable"
                    )
                    satisfied = False
                else:
                    satisfied, missing = _auth_expectation_satisfied(
                        auth,
                        environment,
                        field=f"{field}.auth",
                        errors=errors,
                    )
            elif has_expectation:
                # Validate list shape even while a conditional is inactive.
                _auth_expectation_satisfied(
                    auth,
                    environment,
                    field=f"{field}.auth",
                    errors=errors,
                )

            if auth_status == "pass" and expectation_applies and not satisfied:
                errors.append(
                    f"{service}: auth status pass but rendered config is missing "
                    + ", ".join(missing)
                )
            if auth_status == "gap":
                reason = auth.get("gap_reason")
                if not isinstance(reason, str) or not reason.strip():
                    errors.append(f"{field}.auth.gap_reason must be non-empty")
                if expectation_applies and satisfied:
                    errors.append(
                        f"{service}: declared auth gap is stale; expectation now passes"
                    )
                declared_gaps.append(
                    {
                        "service": service,
                        "surface": "auth",
                        "reason": str(reason or ""),
                    }
                )

        durable_volume = entry.get("durable_volume")
        named_targets = _named_volume_targets(service_config)
        if not named_targets:
            zero_named_volume_services.append(service)
        if not isinstance(durable_volume, Mapping):
            errors.append(f"{field}.durable_volume must be an object")
        else:
            volume_applicability = durable_volume.get("applicability")
            volume_status = durable_volume.get("status")
            if volume_applicability not in VOLUME_APPLICABILITY:
                errors.append(
                    f"{field}.durable_volume.applicability must be one of "
                    + ", ".join(sorted(VOLUME_APPLICABILITY))
                )
            if volume_status not in ADMISSION_STATUSES:
                errors.append(f"{field}.durable_volume.status must be pass or gap")
            else:
                volume_counts[volume_status] += 1
            volume_rationale = durable_volume.get("rationale")
            if not isinstance(volume_rationale, str) or not volume_rationale.strip():
                errors.append(
                    f"{field}.durable_volume.rationale must be a non-empty string"
                )

            expected_targets = _string_list(
                durable_volume.get("expected_targets", []),
                field=f"{field}.durable_volume.expected_targets",
                errors=errors,
            )
            if volume_applicability == "manifest_required":
                if not expected_targets:
                    errors.append(
                        f"{field}.durable_volume.expected_targets must not be empty"
                    )
                if volume_status == "pass" and sorted(expected_targets) != named_targets:
                    errors.append(
                        f"{service}: named-volume targets differ; expected "
                        f"{sorted(expected_targets)!r}, observed {named_targets!r}"
                    )
            elif volume_applicability in {"delegated", "not_applicable"}:
                if named_targets:
                    errors.append(
                        f"{service}: {volume_applicability} volume adjudication "
                        f"contradicts named mounts {named_targets!r}"
                    )
                if expected_targets:
                    errors.append(
                        f"{field}.durable_volume.expected_targets must be empty "
                        f"for {volume_applicability}"
                    )
                if volume_applicability == "delegated":
                    state_owner = durable_volume.get("state_owner")
                    if not isinstance(state_owner, str) or not state_owner.strip():
                        errors.append(
                            f"{field}.durable_volume.state_owner must be non-empty "
                            "for delegated state"
                        )
            if volume_status == "gap":
                reason = durable_volume.get("gap_reason")
                if not isinstance(reason, str) or not reason.strip():
                    errors.append(f"{field}.durable_volume.gap_reason must be non-empty")
                declared_gaps.append(
                    {
                        "service": service,
                        "surface": "durable_volume",
                        "reason": str(reason or ""),
                    }
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "matrix_task_id": matrix.get("task_id"),
        "parent_task_id": matrix.get("parent_task_id"),
        "worker_count": len(required_workers),
        "matrix_entry_count": len(by_service),
        "auth": auth_counts,
        "durable_volume": volume_counts,
        "zero_named_volume_count": len(zero_named_volume_services),
        "zero_named_volume_services": zero_named_volume_services,
        "declared_gap_count": len(declared_gaps),
        "declared_gaps": declared_gaps,
        "matrix_consistent": not errors,
        "admission_ready": not errors and not declared_gaps,
        "errors": errors,
    }


def render_markdown(report: Mapping[str, Any], matrix: Mapping[str, Any]) -> str:
    by_service = {
        entry["service"]: entry
        for entry in matrix.get("workers", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("service"), str)
    }
    lines = [
        "| service | auth applicability | auth | volume applicability | volume |",
        "| :-- | :-- | :--: | :-- | :--: |",
    ]
    for service, entry in by_service.items():
        auth = entry.get("auth") or {}
        volume = entry.get("durable_volume") or {}
        lines.append(
            f"| `{service}` | {auth.get('applicability', '')} | "
            f"{auth.get('status', '')} | {volume.get('applicability', '')} | "
            f"{volume.get('status', '')} |"
        )
    lines.extend(
        [
            "",
            f"Workers: {report['worker_count']}; "
            f"declared gaps: {report['declared_gap_count']}; "
            f"zero named-volume workers: {report['zero_named_volume_count']}.",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument(
        "--deploy-script",
        type=Path,
        default=REPO_ROOT / "scripts/deploy_nonprod_vm.sh",
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=REPO_ROOT / "docker-compose.yml",
    )
    parser.add_argument("--env-file", type=Path, default=Path("/dev/null"))
    parser.add_argument(
        "--compose-json",
        type=Path,
        help="Read an already-rendered Compose JSON payload instead of invoking Docker.",
    )
    parser.add_argument("--allow-declared-gaps", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        if not isinstance(matrix, Mapping):
            raise ValueError("matrix must be a JSON object")
        required_workers = parse_required_loop_workers(args.deploy_script)
        compose = render_compose(
            compose_file=args.compose_file,
            env_file=args.env_file,
            compose_json=args.compose_json,
        )
        report = validate_matrix(
            matrix=matrix,
            compose=compose,
            required_workers=required_workers,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2

    accepted = report["matrix_consistent"] and (
        args.allow_declared_gaps or report["admission_ready"]
    )
    report = {**report, "status": "pass" if accepted else "fail"}
    if args.format == "markdown":
        print(render_markdown(report, matrix))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
