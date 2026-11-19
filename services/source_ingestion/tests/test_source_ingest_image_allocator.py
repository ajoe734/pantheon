"""Guard that services/source_ingestion/Dockerfile and all compose services
built from it run with MALLOC_ARENA_MAX=2 (SOURCE-INGEST-MALLOC-ARENA-LIMIT-20261119).
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_INGEST_DOCKERFILE = REPO_ROOT / "services" / "source_ingestion" / "Dockerfile"
BFF_DOCKERFILE = REPO_ROOT / "services" / "control-plane" / "bff" / "Dockerfile"
DOCKER_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

EXPECTED_SOURCE_INGEST_IMAGE_SERVICES = frozenset(
    {
        "source-ingest",
        "source-ingest-controller-migrate",
        "source-ingest-scheduler",
        "source-ingest-agora-projector",
        "strategy-distillation-worker",
    }
)


def parse_dockerfile_instructions(dockerfile_text: str) -> list[tuple[str, str]]:
    """Collapse backslash continuations and return (INSTRUCTION, rest) pairs."""
    logical_lines: list[str] = []
    current: list[str] = []
    for raw_line in dockerfile_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            if not current:
                continue
        if raw_line.rstrip().endswith("\\"):
            current.append(raw_line.rstrip()[:-1].strip())
        else:
            current.append(stripped)
            joined = " ".join(part for part in current if part)
            if joined:
                logical_lines.append(joined)
            current = []
    if current:
        joined = " ".join(part for part in current if part)
        if joined:
            logical_lines.append(joined)

    instructions: list[tuple[str, str]] = []
    for line in logical_lines:
        parts = line.split(None, 1)
        if not parts:
            continue
        op = parts[0].upper()
        rest = parts[1].strip() if len(parts) > 1 else ""
        instructions.append((op, rest))
    return instructions


def parse_dockerfile_env(dockerfile_text: str) -> dict[str, str]:
    """Extract literal ENV key/value assignments from a Dockerfile."""
    env: dict[str, str] = {}
    for op, rest in parse_dockerfile_instructions(dockerfile_text):
        if op != "ENV" or not rest:
            continue
        tokens = shlex.split(rest)
        if not tokens:
            continue
        if "=" in tokens[0]:
            for token in tokens:
                if "=" in token:
                    key, value = token.split("=", 1)
                    env[key] = value
        elif len(tokens) >= 2:
            env[tokens[0]] = " ".join(tokens[1:])
    return env


def validate_malloc_arena_max_in_dockerfile(dockerfile_text: str) -> None:
    """Raise AssertionError if MALLOC_ARENA_MAX=2 is missing, different, or misplaced."""
    instructions = parse_dockerfile_instructions(dockerfile_text)
    env = parse_dockerfile_env(dockerfile_text)
    if "MALLOC_ARENA_MAX" not in env:
        raise AssertionError("Dockerfile is missing ENV MALLOC_ARENA_MAX=2")
    if env["MALLOC_ARENA_MAX"] != "2":
        raise AssertionError(
            f"Dockerfile sets MALLOC_ARENA_MAX={env['MALLOC_ARENA_MAX']!r}; expected '2'"
        )

    # Verify MALLOC_ARENA_MAX is declared in the initial Python runtime ENV section
    # right next to PYTHONDONTWRITEBYTECODE before any RUN or CMD instruction.
    py_env_index: int | None = None
    malloc_env_index: int | None = None
    first_run_or_cmd_index: int | None = None
    for idx, (op, rest) in enumerate(instructions):
        if op in {"RUN", "CMD", "ENTRYPOINT"} and first_run_or_cmd_index is None:
            first_run_or_cmd_index = idx
        if op == "ENV" and "PYTHONDONTWRITEBYTECODE=" in rest and py_env_index is None:
            py_env_index = idx
        if op == "ENV" and "MALLOC_ARENA_MAX=" in rest:
            malloc_env_index = idx

    assert py_env_index is not None, "Dockerfile must define ENV PYTHONDONTWRITEBYTECODE=1"
    assert malloc_env_index is not None, "Dockerfile must define ENV MALLOC_ARENA_MAX=2"
    assert abs(malloc_env_index - py_env_index) <= 1, (
        "ENV MALLOC_ARENA_MAX=2 must be placed next to ENV PYTHONDONTWRITEBYTECODE=1"
    )
    if first_run_or_cmd_index is not None:
        assert malloc_env_index < first_run_or_cmd_index, (
            "ENV MALLOC_ARENA_MAX=2 must appear before RUN/CMD instructions"
        )


def _compose_env_map(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    result: dict[str, str] = {}
    for item in env:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def test_source_ingestion_dockerfile_sets_malloc_arena_max_2() -> None:
    text = SOURCE_INGEST_DOCKERFILE.read_text(encoding="utf-8")
    validate_malloc_arena_max_in_dockerfile(text)
    env = parse_dockerfile_env(text)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["MALLOC_ARENA_MAX"] == "2"

    # Parity with BFF Dockerfile from BFF-MALLOC-ARENA-LIMIT-20261107.
    bff_env = parse_dockerfile_env(BFF_DOCKERFILE.read_text(encoding="utf-8"))
    assert bff_env.get("MALLOC_ARENA_MAX") == "2"


@pytest.mark.parametrize(
    "mutated_text,expected_error",
    [
        (
            "FROM python:3.12-slim\nWORKDIR /workspace\nENV PYTHONDONTWRITEBYTECODE=1 \\\n    PYTHONUNBUFFERED=1\nCMD [\"python\"]\n",
            "missing ENV MALLOC_ARENA_MAX=2",
        ),
        (
            "FROM python:3.12-slim\nWORKDIR /workspace\nENV PYTHONDONTWRITEBYTECODE=1 \\\n    PYTHONUNBUFFERED=1\nENV MALLOC_ARENA_MAX=4\nCMD [\"python\"]\n",
            "expected '2'",
        ),
        (
            "FROM python:3.12-slim\nWORKDIR /workspace\nENV PYTHONDONTWRITEBYTECODE=1 \\\n    PYTHONUNBUFFERED=1\nENV MALLOC_ARENA_MAX=0\nCMD [\"python\"]\n",
            "expected '2'",
        ),
    ],
)
def test_validator_fails_when_malloc_arena_max_is_missing_or_different(
    mutated_text: str, expected_error: str
) -> None:
    with pytest.raises(AssertionError, match=expected_error):
        validate_malloc_arena_max_in_dockerfile(mutated_text)


def test_every_compose_service_built_from_source_ingestion_dockerfile_runs_with_malloc_arena_max_2() -> None:
    dockerfile_text = SOURCE_INGEST_DOCKERFILE.read_text(encoding="utf-8")
    validate_malloc_arena_max_in_dockerfile(dockerfile_text)
    image_env = parse_dockerfile_env(dockerfile_text)

    compose = yaml.safe_load(DOCKER_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose.get("services") or {}

    matched_services: set[str] = set()
    for service_name, spec in services.items():
        build = spec.get("build")
        dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
        if dockerfile != "services/source_ingestion/Dockerfile":
            continue
        matched_services.add(service_name)
        effective_env = {**image_env, **_compose_env_map(spec)}
        assert effective_env.get("MALLOC_ARENA_MAX") == "2", (
            f"Service {service_name} effective MALLOC_ARENA_MAX={effective_env.get('MALLOC_ARENA_MAX')!r} != '2'"
        )

    assert matched_services == EXPECTED_SOURCE_INGEST_IMAGE_SERVICES


def test_arena_driver_smaps_parser_classifies_main_heap_and_64mb_aligned_arenas() -> None:
    import importlib.util

    driver_path = (
        REPO_ROOT
        / "docs"
        / "deployment"
        / "evidence"
        / "SOURCE-INGEST-MALLOC-ARENA-LIMIT-20261119"
        / "arena_driver.py"
    )
    spec = importlib.util.spec_from_file_location("arena_driver", driver_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sample_smaps = "\n".join(
        [
            "55a100000000-55a102000000 rw-p 00000000 00:00 0                          [heap]",
            "Size:              32768 kB",
            "Rss:               16384 kB",
            "7f0000000000-7f0004000000 rw-p 00000000 00:00 0 ",
            "Size:              65536 kB",
            "Rss:               30720 kB",
            "7f0004000000-7f0006000000 rw-p 00000000 00:00 0 ",
            "Size:              32768 kB",
            "Rss:               20480 kB",
            "7f0006000000-7f0008000000 ---p 00000000 00:00 0 ",
            "Size:              32768 kB",
            "Rss:                   0 kB",
            "7f0010000000-7f0018000000 rw-p 00000000 00:00 0 ",
            "Size:             131072 kB",
            "Rss:               65536 kB",
            "7f0021000000-7f0021100000 rw-p 00000000 00:00 0 ",
            "Size:               1024 kB",
            "Rss:                 512 kB",
        ]
    )
    parsed = module.parse_smaps_text(sample_smaps)
    assert parsed["main_heap_rss_mb"] == 16.0
    assert parsed["aligned_64mb_arenas"] == 2
    assert parsed["aligned_64mb_arena_rss_mb"] == 50.0
    assert parsed["above_64mb_rss_mb"] == 64.0
    assert parsed["small_mappings_rss_mb"] == 0.5
    assert parsed["total_rss_mb"] == 130.5


def test_evidence_records_hosted_series_and_200_round_driver_before_after() -> None:
    import json

    evidence_path = (
        REPO_ROOT
        / "docs"
        / "deployment"
        / "evidence"
        / "SOURCE-INGEST-MALLOC-ARENA-LIMIT-20261119"
        / "evidence.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["task_id"] == "SOURCE-INGEST-MALLOC-ARENA-LIMIT-20261119"

    hosted = evidence["1_coordinator_hosted_series_and_off_hosted_reproduction"][
        "coordinator_hosted_series"
    ]
    assert hosted["backend_commit"] == "08d748790"
    assert hosted["container_started_at"] == "2026-11-19T00:32:31Z"
    assert hosted["smaps_series"][0]["aligned_64mb_arenas"] == 36
    assert hosted["smaps_series"][0]["aligned_64mb_arena_rss_mb"] == 1174
    assert hosted["smaps_series"][-1]["aligned_64mb_arenas"] == 48
    assert hosted["smaps_series"][-1]["aligned_64mb_arena_rss_mb"] == 1889

    output = evidence["3_and_4_driver_before_after_and_wall_time_output"]
    before = output["default_allocator"]
    after = output["malloc_arena_max_2"]
    comparison = output["comparison"]

    assert before["rounds"] >= 200
    assert after["rounds"] >= 200
    assert before["raw_read_window_workers"] == 16
    assert before["summary"]["max_aligned_64mb_arenas"] > 2
    assert after["summary"]["max_aligned_64mb_arenas"] <= 2
    assert after["summary"]["final_rss_mb"] < before["summary"]["final_rss_mb"]
    assert "median_ms" in before["summary"]["raw_read_window_wall_ms"]
    assert "p99_ms" in before["summary"]["raw_read_window_wall_ms"]
    assert "median_ms" in after["summary"]["raw_read_window_wall_ms"]
    assert "p99_ms" in after["summary"]["raw_read_window_wall_ms"]
    assert "median_ms" in before["summary"]["stored_snapshot_lookup_wall_ms"]
    assert "p99_ms" in before["summary"]["stored_snapshot_lookup_wall_ms"]
    assert "median_ms" in after["summary"]["stored_snapshot_lookup_wall_ms"]
    assert "p99_ms" in after["summary"]["stored_snapshot_lookup_wall_ms"]
    assert comparison["slowdown_above_10_pct"] is False


