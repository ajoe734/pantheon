"""Integration proof for explicit-gated W&B online sync."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS_DIR = REPO_ROOT / "services" / "registry" / "experiments"
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

from adapter import ExperimentSyncError, RegistryExperimentAdapter, WandbOnlineBackend  # noqa: E402


def _registry_entry(
    *,
    artifact_state: str = "approved",
    deployment_stage: str = "paper",
) -> dict[str, object]:
    return {
        "registry_id": "reg-wandb-proof-1.0.0",
        "artifact_type": "model_artifact",
        "strategy_id": "wandb-proof-strategy",
        "version": "1.0.0",
        "artifact_state": artifact_state,
        "deployment_stage": deployment_stage,
        "lineage": {
            "parent_registry_ids": ["reg-wandb-proof-0.9.0"],
            "source_run_ids": ["train-wandb-proof-001"],
            "source_dataset_refs": ["dataset://wandb-proof/pit"],
            "source_strategy_spec_id": "strategy-spec-wandb-proof-v1",
        },
        "storage_ref": {
            "backend": "object_store",
            "path": "object://pantheon/registry/wandb-proof-strategy/1.0.0/artifact.bin",
        },
        "checksum": "sha256:wnbproof123",
        "producer_run_id": "train-wandb-proof-001",
        "evaluation_summary": {
            "sharpe_ratio": 1.28,
            "max_drawdown": 0.09,
            "turnover": 0.18,
        },
        "promoted_at": "2026-05-19T15:00:00Z",
        "approver": "research-activation-review",
        "metadata": {},
    }


class _FakeWandbConfig(dict):
    def update(self, payload, allow_val_change=False):  # noqa: ANN001 - fake SDK signature
        super().update(payload)


class _FakeWandbRun:
    def __init__(self, *, project: str, entity: str | None, name: str):
        self.id = "credentialed-run-001"
        self.project = project
        self.entity = entity
        self.name = name
        self.path = [entity or "test-entity", project, self.id]
        self.url = f"https://wandb.ai/{entity or 'test-entity'}/{project}/runs/{self.id}"
        self.config = _FakeWandbConfig()
        self.logged_metrics: list[dict[str, object]] = []
        self.logged_artifacts: list[dict[str, object]] = []
        self.finished = False

    def log(self, metrics: dict[str, object]) -> None:
        self.logged_metrics.append(metrics)

    def log_artifact(self, artifact, aliases=None):  # noqa: ANN001 - fake SDK signature
        self.logged_artifacts.append({"artifact": artifact, "aliases": aliases or []})
        return artifact

    def finish(self) -> None:
        self.finished = True


class _FakeWandbArtifact:
    def __init__(self, name: str, type: str, metadata=None):  # noqa: A002, ANN001
        self.name = name
        self.type = type
        self.metadata = metadata or {}
        self.files: list[dict[str, str | None]] = []

    def add_file(self, path: str, name: str | None = None) -> None:
        self.files.append({"path": path, "name": name})

    def wait(self):
        return self


class _FakeApiRun:
    id = "credentialed-run-001"


class _FakeApiArtifact:
    name = "credentialed-artifact"
    version = "approved"


class _FakeWandbApi:
    def run(self, path: str) -> _FakeApiRun:
        self.run_path = path
        return _FakeApiRun()

    def artifact(self, path: str) -> _FakeApiArtifact:
        self.artifact_path = path
        return _FakeApiArtifact()


class _FakeWandbModule:
    __version__ = "0.16.0-test"
    Artifact = _FakeWandbArtifact
    Api = _FakeWandbApi

    def __init__(self) -> None:
        self.init_calls: list[dict[str, object]] = []
        self.last_run: _FakeWandbRun | None = None

    class Settings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def init(self, **kwargs):  # noqa: ANN001 - fake SDK signature
        self.init_calls.append(kwargs)
        self.last_run = _FakeWandbRun(
            project=kwargs["project"],
            entity=kwargs.get("entity"),
            name=kwargs["name"],
        )
        return self.last_run


def _credentialed_env() -> dict[str, str]:
    return {
        "PANTHEON_WANDB_ONLINE_SYNC_ENABLED": "1",
        "PANTHEON_WANDB_PROJECT": "pantheon-test",
        "PANTHEON_WANDB_ENTITY": "pantheon-ci",
        "WANDB_API_KEY": "credentialed-secret-token",
    }


def test_wandb_online_sync_uses_credentials_without_becoming_registry_truth() -> None:
    fake_wandb = _FakeWandbModule()
    entry = _registry_entry()

    with patch.dict(os.environ, _credentialed_env(), clear=False):
        with patch.dict(sys.modules, {"wandb": fake_wandb}, clear=False):
            backend = WandbOnlineBackend()
            result = RegistryExperimentAdapter(backend=backend).sync_registry_entry(entry)

    assert result.experiment_ref.backend == "wandb"
    assert result.experiment_ref.sync_status == "online_synced"
    assert result.experiment_ref.readback_refs["verified"] is True
    assert result.promoted_metadata["artifact_state"] == entry["artifact_state"]
    assert result.promoted_metadata["deployment_stage"] == entry["deployment_stage"]
    assert result.promoted_metadata["experiment_refs"][0]["backend"] == "wandb"
    assert result.promoted_metadata["experiment_refs"][0]["sync_status"] == "online_synced"
    assert result.record.tags["pantheon.registry_id"] == entry["registry_id"]
    assert result.record.tags["pantheon.wandb.online_sync_gate"] == "PANTHEON_WANDB_ONLINE_SYNC_ENABLED"
    assert result.record.artifacts["artifact_handoff.json"]["storage_ref"] == entry["storage_ref"]
    assert result.record.artifacts["artifact_handoff.json"]["backend"] == "wandb"
    assert result.experiment_ref.artifact_refs["artifact_handoff.json"]["artifact_ref"].startswith(
        "wandb://pantheon-ci/pantheon-test/"
    )
    assert fake_wandb.init_calls[0]["mode"] == "online"
    assert fake_wandb.last_run is not None
    assert fake_wandb.last_run.logged_metrics == [
        {"sharpe_ratio": 1.28, "max_drawdown": 0.09, "turnover": 0.18}
    ]
    assert fake_wandb.last_run.logged_artifacts[0]["aliases"] == ["approved"]
    assert fake_wandb.last_run.finished is True
    assert "credentialed-secret-token" not in str(result.record)
    assert "credentialed-secret-token" not in str(result.promoted_metadata)
    assert "credentialed-secret-token" not in str(backend.runs)


def test_wandb_online_backend_requires_api_key_before_sdk_sync() -> None:
    fake_wandb = _FakeWandbModule()
    env = _credentialed_env()
    env.pop("WANDB_API_KEY")

    with patch.dict(os.environ, env, clear=False):
        with patch.dict(sys.modules, {"wandb": fake_wandb}, clear=False):
            with pytest.raises(ExperimentSyncError, match="WANDB_API_KEY"):
                WandbOnlineBackend()

    assert fake_wandb.init_calls == []


def test_registry_semantics_reject_invalid_state_before_wandb_api_call() -> None:
    fake_wandb = _FakeWandbModule()
    invalid_entry = _registry_entry(artifact_state="candidate", deployment_stage="paper")

    with patch.dict(os.environ, _credentialed_env(), clear=False):
        with patch.dict(sys.modules, {"wandb": fake_wandb}, clear=False):
            backend = WandbOnlineBackend()
            with pytest.raises(ExperimentSyncError, match="artifact_state=approved"):
                RegistryExperimentAdapter(backend=backend).sync_registry_entry(invalid_entry)

    assert fake_wandb.init_calls == []


def test_wandb_online_smoke_reports_structured_skip_without_credentials() -> None:
    env = os.environ.copy()
    for key in (
        "PANTHEON_WANDB_ONLINE_SYNC_ENABLED",
        "PANTHEON_WANDB_PROJECT",
        "PANTHEON_WANDB_ENTITY",
        "WANDB_PROJECT",
        "WANDB_ENTITY",
        "WANDB_API_KEY",
    ):
        env.pop(key, None)

    proc = subprocess.run(
        [sys.executable, "services/registry/experiments/smoke_test.py", "--backend", "wandb-online"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload == {
        "backend": "wandb-online",
        "missing_env": [
            "PANTHEON_WANDB_ONLINE_SYNC_ENABLED",
            "PANTHEON_WANDB_PROJECT",
            "WANDB_API_KEY",
        ],
        "reason": "missing_explicit_online_sync_config",
        "secrets_persisted": False,
        "status": "skipped",
    }


def test_credentialed_sync_proof_artifact_records_truth_boundary() -> None:
    proof = (REPO_ROOT / "integrations" / "wandb" / "credentialed_sync_proof.md").read_text(
        encoding="utf-8"
    )

    assert "Task: `RES-ACT-WANDB-001-V2`" in proof
    assert "Parent: `RES-ACT-001-V2`" in proof
    assert "Task: `WNB-ACT-001-V2`" not in proof
    assert "Pantheon registry remains" in proof
    assert "artifact-admission system of record" in proof
    assert "PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1" in proof
    assert "WANDB_API_KEY=<test-api-key>" in proof
    assert "W&B refs do not approve, promote, retire, deploy, or roll back artifacts" in proof
