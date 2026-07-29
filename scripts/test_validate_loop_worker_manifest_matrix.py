from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import validate_loop_worker_manifest_matrix as validator  # noqa: E402


def _matrix() -> dict:
    return {
        "schema_version": validator.SCHEMA_VERSION,
        "task_id": "matrix-task",
        "parent_task_id": "parent-task",
        "workers": [
            {
                "service": "durable-worker",
                "rationale": "Owns its durable state and authenticates its API call.",
                "evidence_refs": ["docker-compose.yml", "services/durable.py"],
                "auth": {
                    "applicability": "manifest_required",
                    "status": "pass",
                    "environment_all_of": ["SERVICE_TOKEN"],
                    "environment_any_of": [],
                    "environment_equals": {"AUTH_MODE": "token"},
                    "rationale": "The target API requires a bearer token.",
                },
                "durable_volume": {
                    "applicability": "manifest_required",
                    "status": "pass",
                    "expected_targets": ["/data/durable"],
                    "rationale": "The worker owns a local durable queue.",
                },
            },
            {
                "service": "stateless-worker",
                "rationale": "Stateless API poller with server-owned durable state.",
                "evidence_refs": ["docker-compose.yml", "services/stateless.py"],
                "auth": {
                    "applicability": "manifest_required",
                    "status": "gap",
                    "environment_all_of": ["CLIENT_TOKEN", "TENANT_ID"],
                    "environment_any_of": [],
                    "rationale": "The client refuses to run without both values.",
                    "gap_reason": "Compose does not inject either required value.",
                },
                "durable_volume": {
                    "applicability": "delegated",
                    "status": "pass",
                    "expected_targets": [],
                    "state_owner": "durable-api",
                    "rationale": "Claims, retries, and idempotency are server-owned.",
                },
            },
        ],
    }


def _compose(*, include_stateless_auth: bool = False) -> dict:
    stateless_environment = {}
    if include_stateless_auth:
        stateless_environment = {
            "CLIENT_TOKEN": "redacted-nonempty",
            "TENANT_ID": "tenant-a",
        }
    return {
        "services": {
            "durable-worker": {
                "environment": {
                    "AUTH_MODE": "token",
                    "SERVICE_TOKEN": "redacted-nonempty",
                },
                "volumes": [
                    {
                        "type": "volume",
                        "source": "durable-data",
                        "target": "/data/durable",
                    }
                ],
            },
            "stateless-worker": {
                "environment": stateless_environment,
                "volumes": [],
            },
        }
    }


def test_parse_required_loop_workers_ignores_comments(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy.sh"
    deploy.write_text(
        """
before=true
REQUIRED_LOOP_WORKERS=(
  # lane one
  durable-worker
  stateless-worker # inline note
)
after=true
""",
        encoding="utf-8",
    )

    assert validator.parse_required_loop_workers(deploy) == [
        "durable-worker",
        "stateless-worker",
    ]


def test_truthful_declared_gap_and_delegated_volume_are_consistent() -> None:
    report = validator.validate_matrix(
        matrix=_matrix(),
        compose=_compose(),
        required_workers=["durable-worker", "stateless-worker"],
    )

    assert report["matrix_consistent"] is True
    assert report["admission_ready"] is False
    assert report["declared_gap_count"] == 1
    assert report["zero_named_volume_services"] == ["stateless-worker"]
    assert report["errors"] == []


def test_declared_auth_gap_fails_when_it_becomes_stale() -> None:
    report = validator.validate_matrix(
        matrix=_matrix(),
        compose=_compose(include_stateless_auth=True),
        required_workers=["durable-worker", "stateless-worker"],
    )

    assert report["matrix_consistent"] is False
    assert any("declared auth gap is stale" in error for error in report["errors"])


def test_named_volume_target_drift_fails() -> None:
    compose = _compose()
    compose["services"]["durable-worker"]["volumes"][0]["target"] = "/wrong"

    report = validator.validate_matrix(
        matrix=_matrix(),
        compose=compose,
        required_workers=["durable-worker", "stateless-worker"],
    )

    assert report["matrix_consistent"] is False
    assert any("named-volume targets differ" in error for error in report["errors"])


def test_auth_mode_value_drift_fails() -> None:
    compose = _compose()
    compose["services"]["durable-worker"]["environment"]["AUTH_MODE"] = "disabled"

    report = validator.validate_matrix(
        matrix=_matrix(),
        compose=compose,
        required_workers=["durable-worker", "stateless-worker"],
    )

    assert report["matrix_consistent"] is False
    assert any("AUTH_MODE=token" in error for error in report["errors"])


def test_required_worker_inventory_drift_fails() -> None:
    report = validator.validate_matrix(
        matrix=_matrix(),
        compose=_compose(),
        required_workers=["durable-worker", "new-worker"],
    )

    assert report["matrix_consistent"] is False
    assert any("matrix is missing required workers" in error for error in report["errors"])
    assert any("rendered Compose is missing required workers" in error for error in report["errors"])
