#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import posixpath
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX_PATH = ROOT / ".github" / "pantheon-stage0-matrix.json"
DEFAULT_DOC_PATH = ROOT / "Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md"
DEFAULT_COMPOSE_PATH = ROOT / "docker-compose.yml"
WAVE1_HEADING = "### 4.3 Wave 1 core service inventory"


class Stage0ConfigError(RuntimeError):
    pass


def normalize_path(path: str | Path) -> str:
    return PurePosixPath(str(path)).as_posix()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Stage0ConfigError(f"Missing config file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Stage0ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def parse_wave1_inventory_ids(doc_path: Path = DEFAULT_DOC_PATH) -> list[str]:
    text = doc_path.read_text(encoding="utf-8")
    try:
        start = text.index(WAVE1_HEADING)
    except ValueError as exc:
        raise Stage0ConfigError(f"Missing heading in deployment doc: {WAVE1_HEADING}") from exc

    ids: list[str] = []
    seen: set[str] = set()
    in_table = False
    for line in text[start:].splitlines()[1:]:
        if line.startswith("### ") and in_table:
            break
        if not line.startswith("|"):
            continue
        in_table = True
        if set(line.replace("|", "").strip()) == {"-"}:
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        first_cell = cells[1]
        for service_id in re.findall(r"`([^`]+)`", first_cell):
            if service_id not in seen:
                ids.append(service_id)
                seen.add(service_id)
    return ids


def parse_compose_services_details(compose_path: Path = DEFAULT_COMPOSE_PATH) -> dict[str, dict[str, Any]]:
    """Compose services built from project code; ``docker compose config`` resolves profiles and ${VAR:-default}."""
    result = subprocess.run(
        ["docker", "compose", "--file", str(compose_path), "--profile", "*", "config", "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Stage0ConfigError(f"docker compose config failed for {compose_path}: {result.stderr.strip()}")
    details: dict[str, dict[str, Any]] = {}
    for name, service in json.loads(result.stdout)["services"].items():
        build = service.get("build")
        if not build or name == "openclaw-gateway":  # upstream node gateway, not project code
            continue
        context = os.path.relpath(build["context"], compose_path.parent)
        details[name] = {
            "context": context,
            "dockerfile": posixpath.normpath(posixpath.join(context, build.get("dockerfile", "Dockerfile"))),
            "args": [f"{key}={value}" for key, value in (build.get("args") or {}).items()],
            "environment": service.get("environment") or {},
            "entrypoint": service.get("entrypoint"),
            "command": service.get("command"),
        }
    return details


def dockerfile_instruction(dockerfile_text: str, name: str) -> list[str]:
    """Argv of the last ENTRYPOINT/CMD instruction; shell form is wrapped as ``sh -c``."""
    joined = re.sub(r"\\\n", " ", dockerfile_text)
    found = re.findall(rf"^{name}\s+(.*)$", joined, re.MULTILINE)
    if not found:
        return []
    raw = found[-1].strip()
    return json.loads(raw) if raw.startswith("[") else ["sh", "-c", raw]


def effective_argv(details: dict[str, Any], dockerfile_text: str) -> list[str]:
    """What the container runs: compose entrypoint/command layered over the Dockerfile ENTRYPOINT/CMD."""
    entrypoint = dockerfile_instruction(dockerfile_text, "ENTRYPOINT")
    command = dockerfile_instruction(dockerfile_text, "CMD")
    if details.get("entrypoint") is not None:
        entrypoint, command = details["entrypoint"], []  # compose clears the image CMD with an entrypoint
    if details.get("command") is not None:
        command = details["command"]
    return entrypoint + command


MODULE_CHECK = "import importlib, sys\nsys.path.insert(0, {path!r})\nmodule = importlib.import_module({module!r})\n"
APP_CHECK = "if not hasattr(module, {attr!r}):\n    raise SystemExit('{module}:{attr} is missing')\n"
# run_name is not __main__, so main guards stay quiet; runpy registers the module so dataclasses resolve.
SCRIPT_CHECK = (
    "import os, runpy, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath({script!r})))\n"
    "runpy.run_path({script!r}, run_name='__import_check__')\n"
)


def resolve_import_check(argv: list[str]) -> list[str] | None:
    """Container command that imports the real entrypoint, or None when it has nothing to import (python -c)."""
    if argv[:1] and posixpath.basename(argv[0]) in ("sh", "bash") and argv[1:2] in (["-c"], ["-ec"]) and len(argv) > 2:
        argv = shlex.split(argv[2])
    while argv[:1] == ["exec"]:
        argv = argv[1:]
    program = posixpath.basename(argv[0]) if argv else ""
    if program in ("sh", "bash") and len(argv) > 1 and not argv[1].startswith("-"):
        return [program, "-n", argv[1]]
    if program.startswith("python") and len(argv) > 1:
        if argv[1] == "-c":
            return None
        if argv[1] == "-m" and argv[2] != "uvicorn":
            return ["python", "-c", MODULE_CHECK.format(path="", module=argv[2])]
        if argv[1] != "-m":
            return ["python", "-c", SCRIPT_CHECK.format(script=argv[1])]
        argv = argv[2:]
    if not argv or posixpath.basename(argv[0]) != "uvicorn":
        raise Stage0ConfigError(f"cannot derive an import check for entrypoint: {shlex.join(argv)}")
    spec = next(arg for arg in argv[1:] if ":" in arg and not arg.startswith("-"))
    app_dir = next((argv[i + 1] for i, arg in enumerate(argv) if arg == "--app-dir"), "")
    module, attr = spec.split(":", 1)
    return ["python", "-c", MODULE_CHECK.format(path=app_dir, module=module) + APP_CHECK.format(module=module, attr=attr)]


def compose_images(compose_path: Path = DEFAULT_COMPOSE_PATH) -> dict[str, dict[str, Any]]:
    """Group compose services by the image they build: {image_id: {dockerfile, context, args, services}}."""
    images: dict[str, dict[str, Any]] = {}
    for name, details in parse_compose_services_details(compose_path).items():
        image_id = re.sub(r"[^A-Za-z0-9._-]+", "-", details["dockerfile"])
        image = images.setdefault(image_id, {**details, "services": {}})
        image["services"][name] = details
    return images


def image_affected(image: dict[str, Any], changed_files: list[str], root: Path = ROOT) -> bool:
    """True when the diff touches the Dockerfile, a file the Dockerfile names (locks, requirements), or Python source in its context."""
    dockerfile_text = (root / image["dockerfile"]).read_text(encoding="utf-8")
    context = posixpath.normpath(image["context"])
    for path in map(normalize_path, changed_files):
        relative = path if context == "." else path.removeprefix(context + "/")
        is_source = path.endswith(".py") and not PurePosixPath(path).name.startswith("test_")
        if path == image["dockerfile"] or ((context == "." or relative != path) and (relative in dockerfile_text or is_source)):
            return True
    return False


def run_import_smoke(image_id: str, image: dict[str, Any], tag_suffix: str, compose_path: Path = DEFAULT_COMPOSE_PATH) -> dict[str, str]:
    """Build one image and import-check every compose service that runs it; returns {service: result}."""
    dockerfile_text = (ROOT / image["dockerfile"]).read_text(encoding="utf-8")
    tag = f"pantheon-import-smoke/{image_id.lower()}:{tag_suffix}"
    build = ["docker", "build", "--file", image["dockerfile"]]
    for build_arg in image["args"]:
        build += ["--build-arg", build_arg]
    run_shell_command(shlex.join(build + ["--tag", tag, image["context"]]))

    # Some services open their Postgres stores while importing, so give them the compose postgres service.
    project = f"pantheon-import-smoke-{image_id.lower()}"
    needs_postgres = any("@postgres:" in str(v) for d in image["services"].values() for v in d["environment"].values())
    network = ["--network", f"{project}_default"] if needs_postgres else []
    compose = ["docker", "compose", "--file", str(compose_path), "--project-name", project, "--profile", "core"]
    results = {}
    try:
        if needs_postgres:
            run_shell_command("POSTGRES_PORT=0 " + shlex.join(compose + ["up", "--detach", "--wait", "postgres"]))  # no fixed host port
        for service, details in image["services"].items():
            check = resolve_import_check(effective_argv(details, dockerfile_text))
            if check is None:
                results[service] = "skipped: python -c entrypoint has nothing to import"
                continue
            env = [part for key, value in details["environment"].items() if value is not None for part in ("--env", f"{key}={value}")]
            try:
                run_shell_command(shlex.join(["docker", "run", "--rm", *network, *env, "--entrypoint", check[0], tag, *check[1:]]))
                results[service] = "passed"
            except subprocess.CalledProcessError as exc:
                results[service] = f"failed: exit {exc.returncode}"
    finally:
        if needs_postgres:
            subprocess.run(compose + ["down", "--volumes"], check=False)
    return results


def ensure_sequence(name: str, value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise Stage0ConfigError(f"{name} must be a JSON array")
    return value


def ensure_string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Stage0ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def validate_paths_exist(label: str, paths: list[str], root: Path) -> None:
    for rel_path in paths:
        path = root / rel_path
        if not path.exists():
            raise Stage0ConfigError(f"{label} path does not exist: {rel_path}")


def validate_target(target: dict[str, Any], root: Path) -> None:
    target_id = ensure_string("target.id", target.get("id"))
    ensure_string(f"{target_id}.family", target.get("family"))
    ensure_string(f"{target_id}.profile", target.get("profile"))

    repo_paths = [ensure_string(f"{target_id}.repo_paths[]", item) for item in ensure_sequence(f"{target_id}.repo_paths", target.get("repo_paths", []))]
    changed_paths = [ensure_string(f"{target_id}.changed_paths[]", item) for item in ensure_sequence(f"{target_id}.changed_paths", target.get("changed_paths", []))]
    if not changed_paths:
        raise Stage0ConfigError(f"{target_id} must declare at least one changed_paths rule")
    validate_paths_exist(f"{target_id}.repo_paths", repo_paths, root)

    verify = target.get("verify")
    if verify is not None:
        if not isinstance(verify, dict):
            raise Stage0ConfigError(f"{target_id}.verify must be an object")
        for command in ensure_sequence(f"{target_id}.verify.commands", verify.get("commands", [])):
            ensure_string(f"{target_id}.verify.commands[]", command)
        for setup_command in ensure_sequence(f"{target_id}.verify.setup", verify.get("setup", [])):
            ensure_string(f"{target_id}.verify.setup[]", setup_command)
        if not verify.get("commands"):
            raise Stage0ConfigError(f"{target_id}.verify.commands must not be empty when verify exists")

    build = target.get("build")
    if build is not None:
        if not isinstance(build, dict):
            raise Stage0ConfigError(f"{target_id}.build must be an object")
        context = ensure_string(f"{target_id}.build.context", build.get("context"))
        dockerfile = ensure_string(f"{target_id}.build.dockerfile", build.get("dockerfile"))
        ensure_string(f"{target_id}.build.tag", build.get("tag"))
        for build_arg in ensure_sequence(f"{target_id}.build.args", build.get("args", [])):
            ensure_string(f"{target_id}.build.args[]", build_arg)
        validate_paths_exist(f"{target_id}.build", [context, dockerfile], root)


def load_config(matrix_path: Path = DEFAULT_MATRIX_PATH, doc_path: Path = DEFAULT_DOC_PATH) -> dict[str, Any]:
    config = load_json(matrix_path)

    schema_version = config.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 1:
        raise Stage0ConfigError("schema_version must be an integer >= 1")

    baseline = config.get("baseline")
    if not isinstance(baseline, dict):
        raise Stage0ConfigError("baseline must be an object")
    baseline_setup = ensure_sequence("baseline.setup", baseline.get("setup", []))
    baseline_commands = ensure_sequence("baseline.commands", baseline.get("commands", []))
    if not baseline_commands:
        raise Stage0ConfigError("baseline.commands must not be empty")
    for command in baseline_setup + baseline_commands:
        ensure_string("baseline command", command)

    global_paths = ensure_sequence("global_paths", config.get("global_paths", []))
    if not global_paths:
        raise Stage0ConfigError("global_paths must not be empty")
    for pattern in global_paths:
        ensure_string("global_paths[]", pattern)

    targets = ensure_sequence("targets", config.get("targets", []))
    if not targets:
        raise Stage0ConfigError("targets must not be empty")

    seen_ids: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            raise Stage0ConfigError("Each target must be an object")
        target_id = ensure_string("target.id", target.get("id"))
        if target_id in seen_ids:
            raise Stage0ConfigError(f"Duplicate target id: {target_id}")
        seen_ids.add(target_id)
        validate_target(target, ROOT)

    documented_ids = parse_wave1_inventory_ids(doc_path)
    missing_ids = [service_id for service_id in documented_ids if service_id not in seen_ids]
    if missing_ids:
        raise Stage0ConfigError(
            "Wave 1 deployment doc service ids missing from stage-0 matrix: "
            + ", ".join(missing_ids)
        )

    doc_text = doc_path.read_text(encoding="utf-8")
    matrix_reference = normalize_path(matrix_path.relative_to(ROOT))
    if matrix_reference not in doc_text:
        raise Stage0ConfigError(
            f"Deployment doc must reference the machine-readable stage-0 matrix: {matrix_reference}"
        )

    return config


def matches_any(path: str, patterns: list[str]) -> bool:
    normalized = normalize_path(path)
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def compute_changed_targets(config: dict[str, Any], changed_files: list[str]) -> dict[str, Any]:
    normalized_files = [normalize_path(path) for path in changed_files]
    global_changed = any(matches_any(path, config["global_paths"]) for path in normalized_files)
    targets = config["targets"]
    if global_changed:
        matched = list(targets)
    else:
        matched = [
            target
            for target in targets
            if any(matches_any(path, target["changed_paths"]) for path in normalized_files)
        ]

    target_ids = [target["id"] for target in matched]
    verify_ids = [target["id"] for target in matched if target.get("verify")]
    build_ids = [target["id"] for target in matched if target.get("build")]
    return {
        "global_changed": global_changed,
        "changed_files": normalized_files,
        "target_ids": target_ids,
        "verify_ids": verify_ids,
        "build_ids": build_ids,
    }


def diff_changed_files(base: str | None, head: str | None) -> tuple[list[str], bool]:
    if not base or not head or set(base) == {"0"}:
        return [], True

    ranges = (f"{base}...{head}", f"{base}..{head}")
    for revision_range in ranges:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--name-only", revision_range],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return files, False
    raise Stage0ConfigError(
        f"Unable to diff changed files between {base!r} and {head!r}: {result.stderr.strip()}"
    )


def write_output(output_path: Path, key: str, value: str) -> None:
    delimiter = f"EOF_{secrets.token_hex(4)}"
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def run_shell_command(command: str) -> None:
    stripped = command.strip()
    if stripped.startswith("python3 -m pip") and importlib.util.find_spec("pip") is None:
        raise Stage0ConfigError(
            "python3 -m pip is unavailable in this environment; provision pip or run the stage-0 baseline inside CI/container."
        )
    if stripped.startswith("docker build") and shutil.which("docker") is None:
        raise Stage0ConfigError(
            "docker is required for stage-0 build dry runs."
        )
    print(f"$ {command}", flush=True)
    subprocess.run(command, cwd=str(ROOT), shell=True, check=True)


def run_steps(steps: list[str]) -> None:
    for command in steps:
        run_shell_command(command)


def find_target(config: dict[str, Any], target_id: str) -> dict[str, Any]:
    for target in config["targets"]:
        if target["id"] == target_id:
            return target
    raise Stage0ConfigError(f"Unknown target id: {target_id}")


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.matrix, args.doc)
    report = {
        "schema_version": config["schema_version"],
        "global_path_rules": len(config["global_paths"]),
        "target_count": len(config["targets"]),
        "documented_wave1_service_ids": parse_wave1_inventory_ids(args.doc),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_detect_changes(args: argparse.Namespace) -> int:
    config = load_config(args.matrix, args.doc)
    changed_files, fallback_full_sweep = diff_changed_files(args.base, args.head)
    report = compute_changed_targets(config, changed_files)
    if fallback_full_sweep:
        report["global_changed"] = True
        report["target_ids"] = [target["id"] for target in config["targets"]]
        report["verify_ids"] = [target["id"] for target in config["targets"] if target.get("verify")]
        report["build_ids"] = [target["id"] for target in config["targets"] if target.get("build")]

    print(json.dumps(report, indent=2, ensure_ascii=False))

    if args.github_output:
        write_output(args.github_output, "target_ids", json.dumps(report["target_ids"]))
        write_output(args.github_output, "verify_ids", json.dumps(report["verify_ids"]))
        write_output(args.github_output, "build_ids", json.dumps(report["build_ids"]))
        write_output(args.github_output, "changed_files", json.dumps(report["changed_files"]))
        write_output(args.github_output, "global_changed", str(report["global_changed"]).lower())
        write_output(args.github_output, "target_count", str(len(report["target_ids"])))
        write_output(args.github_output, "verify_count", str(len(report["verify_ids"])))
        write_output(args.github_output, "build_count", str(len(report["build_ids"])))
    return 0


def cmd_run_baseline(args: argparse.Namespace) -> int:
    config = load_config(args.matrix, args.doc)
    baseline = config["baseline"]
    run_steps(baseline.get("setup", []))
    run_steps(baseline["commands"])
    return 0


def cmd_run_target(args: argparse.Namespace) -> int:
    config = load_config(args.matrix, args.doc)
    target = find_target(config, args.target_id)

    if args.mode == "verify":
        verify = target.get("verify")
        if not verify:
            print(f"No verify steps declared for {args.target_id}.")
            return 0
        run_steps(verify.get("setup", []))
        run_steps(verify["commands"])
        return 0

    if args.mode == "build":
        build = target.get("build")
        if not build:
            print(f"No build steps declared for {args.target_id}.")
            return 0
        docker_tag = ensure_string("build.tag", build["tag"])
        if args.tag_suffix:
            docker_tag = f"{docker_tag}:{args.tag_suffix}"
        command_parts = ["docker", "build", "--file", build["dockerfile"]]
        for build_arg in ensure_sequence(f"{args.target_id}.build.args", build.get("args", [])):
            command_parts.extend(["--build-arg", ensure_string(f"{args.target_id}.build.args[]", build_arg)])
        command_parts.extend(["--tag", docker_tag, build["context"]])
        command = " ".join(shlex.quote(part) for part in command_parts)
        run_shell_command(command)
        return 0

    raise Stage0ConfigError(f"Unsupported mode: {args.mode}")


def cmd_import_smoke_plan(args: argparse.Namespace) -> int:
    """Emit the image ids to import-check: the ones the diff can affect, or every image when there is no base to diff."""
    changed_files, full_sweep = diff_changed_files(args.base, args.head)
    selected = [image_id for image_id, image in compose_images(args.compose).items() if full_sweep or image_affected(image, changed_files)]
    print(json.dumps(selected))
    if args.github_output:
        write_output(args.github_output, "image_ids", json.dumps(selected))
    return 0


def cmd_import_smoke_run(args: argparse.Namespace) -> int:
    images = compose_images(args.compose)
    if args.image_id not in images:
        raise Stage0ConfigError(f"Unknown image id: {args.image_id}")
    results = run_import_smoke(args.image_id, images[args.image_id], args.tag_suffix, args.compose)
    for service, result in results.items():
        print(f"import-smoke {args.image_id} {service}: {result}")
    return 1 if any(result.startswith("failed") for result in results.values()) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pantheon stage-0 CI helper")
    parser.add_argument(
      "--matrix",
      type=Path,
      default=DEFAULT_MATRIX_PATH,
      help="Path to the stage-0 matrix JSON file.",
    )
    parser.add_argument(
      "--doc",
      type=Path,
      default=DEFAULT_DOC_PATH,
      help="Path to the deployment design document.",
    )

    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE_PATH, help="Path to docker-compose.yml file.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate the stage-0 matrix and doc sync.")
    validate.set_defaults(func=cmd_validate)

    detect = subparsers.add_parser("detect-changes", help="Compute changed target ids from a git diff.")
    detect.add_argument("--base", default=os.environ.get("STAGE0_BASE_SHA"))
    detect.add_argument("--head", default=os.environ.get("STAGE0_HEAD_SHA"))
    detect.add_argument("--github-output", type=Path, default=None)
    detect.set_defaults(func=cmd_detect_changes)

    baseline = subparsers.add_parser("run-baseline", help="Run the baseline stage-0 checks.")
    baseline.set_defaults(func=cmd_run_baseline)

    run_target = subparsers.add_parser("run-target", help="Run verify/build steps for a single target id.")
    run_target.add_argument("--target-id", required=True)
    run_target.add_argument("--mode", choices=("verify", "build"), required=True)
    run_target.add_argument("--tag-suffix", default=os.environ.get("GITHUB_SHA", "local"))
    run_target.set_defaults(func=cmd_run_target)

    plan = subparsers.add_parser("import-smoke-plan", help="List compose images whose in-image import check must run.")
    plan.add_argument("--base", default=os.environ.get("STAGE0_BASE_SHA"))
    plan.add_argument("--head", default=os.environ.get("STAGE0_HEAD_SHA"))
    plan.add_argument("--github-output", type=Path, default=None)
    plan.set_defaults(func=cmd_import_smoke_plan)

    smoke = subparsers.add_parser("import-smoke", help="Build one compose image and import-check its services' entrypoints.")
    smoke.add_argument("--image-id", required=True)
    smoke.add_argument("--tag-suffix", default=os.environ.get("GITHUB_SHA", "local"))
    smoke.set_defaults(func=cmd_import_smoke_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Stage0ConfigError as exc:
        print(f"stage0-config-error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
