from __future__ import annotations

import argparse
import json
import os

from adapter import (
    ExperimentSyncError,
    InMemoryMlflowBackend,
    OfflineWandbLocalBackend,
    RegistryExperimentAdapter,
    WANDB_API_KEY_ENV,
    WANDB_ONLINE_SYNC_FLAG,
    WANDB_PROJECT_ENV,
    WandbOnlineBackend,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LP-003 registry-to-experiment smoke test")
    parser.add_argument(
        "--backend",
        choices=("memory", "mlflow", "wandb", "wandb-online"),
        default="memory",
        help=(
            "Use the in-memory MLflow test backend, a real MLflow tracking server, "
            "the offline W&B local store, or explicit-gated SDK-backed W&B online sync."
        ),
    )
    parser.add_argument(
        "--tracking-uri",
        default=None,
        help="Optional MLflow tracking URI when --backend mlflow is selected.",
    )
    return parser.parse_args()


def _online_env_ready() -> tuple[bool, list[str]]:
    missing: list[str] = []
    if os.getenv(WANDB_ONLINE_SYNC_FLAG, "").strip().lower() not in {"1", "true", "yes", "on"}:
        missing.append(WANDB_ONLINE_SYNC_FLAG)
    if not os.getenv(WANDB_PROJECT_ENV) and not os.getenv("WANDB_PROJECT"):
        missing.append(WANDB_PROJECT_ENV)
    if not os.getenv(WANDB_API_KEY_ENV):
        missing.append(WANDB_API_KEY_ENV)
    return not missing, missing


def main() -> None:
    args = parse_args()
    if args.backend == "mlflow":
        adapter = RegistryExperimentAdapter.from_tracking_uri(tracking_uri=args.tracking_uri)
        backend = None
    elif args.backend == "wandb":
        backend = OfflineWandbLocalBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
    elif args.backend == "wandb-online":
        ready, missing = _online_env_ready()
        if not ready:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "backend": "wandb-online",
                        "reason": "missing_explicit_online_sync_config",
                        "missing_env": missing,
                        "secrets_persisted": False,
                    },
                    sort_keys=True,
                )
            )
            return
        try:
            backend = WandbOnlineBackend()
        except ExperimentSyncError as exc:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "backend": "wandb-online",
                        "reason": "online_backend_unavailable",
                        "detail": str(exc),
                        "secrets_persisted": False,
                    },
                    sort_keys=True,
                )
            )
            return
        adapter = RegistryExperimentAdapter(backend=backend)
    else:
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)

    registry_entry = {
        "registry_id": "reg-mean-reversion-2.0.0",
        "artifact_type": "model_artifact",
        "strategy_id": "mean-reversion",
        "version": "2.0.0",
        "artifact_state": "approved",
        "deployment_stage": "live",
        "lineage": {
            "parent_registry_ids": ["reg-mean-reversion-1.9.0"],
            "source_run_ids": ["train-2026-04-06-001"],
            "source_dataset_refs": ["dataset://feedback/live-approved"],
        },
        "storage_ref": {
            "backend": "object_store",
            "path": "object://pantheon/registry/mean-reversion/2.0.0/artifact.bin",
        },
        "checksum": "sha256:9f0f1c3d58f64b2b",
        "producer_run_id": "train-2026-04-06-001",
        "evaluation_summary": {
            "sharpe_ratio": 1.72,
            "max_drawdown": 0.08,
            "turnover": 0.21,
        },
        "promoted_at": "2026-04-06T10:00:00Z",
        "approver": "operator-on-call",
        "rollback_target": "1.9.0",
        "metadata": {
            "rollback_target_registry_id": "reg-mean-reversion-1.9.0",
        },
    }

    result = adapter.sync_registry_entry(registry_entry)
    if backend is not None:
        recorded_run = backend.runs[result.experiment_ref.run_id]
        assert recorded_run["tags"]["pantheon.registry_id"] == "reg-mean-reversion-2.0.0"
        assert recorded_run["tags"]["pantheon.artifact_state"] == "approved"
        assert recorded_run["tags"]["pantheon.deployment_stage"] == "live"
        assert "pantheon.lifecycle_state" not in recorded_run["tags"]
        assert recorded_run["aliases"] == ["approved"]
        assert recorded_run["artifacts"]["artifact_handoff.json"]["storage_ref"]["path"].endswith("/artifact.bin")
        if args.backend == "wandb":
            assert recorded_run["mode"] == "offline"
            assert recorded_run["sync_status"] == "offline_local"
            assert recorded_run["online_sync"]["enabled"] is False
            assert recorded_run["tags"]["pantheon.experiment_backend"] == "wandb"
            assert recorded_run["tags"]["pantheon.wandb.offline_local"] == "true"
            assert result.experiment_ref.artifact_refs["artifact_handoff.json"]["artifact_ref"].startswith(
                "wandb-local://artifacts/"
            )
        elif args.backend == "wandb-online":
            assert recorded_run["sync_status"] == "online_synced"
            assert result.experiment_ref.sync_status == "online_synced"
            assert result.experiment_ref.readback_refs["verified"] is True
            assert recorded_run["tags"]["pantheon.experiment_backend"] == "wandb"
            assert recorded_run["tags"]["pantheon.wandb.offline_local"] == "false"
            assert result.experiment_ref.artifact_refs["artifact_handoff.json"]["artifact_ref"].startswith(
                "wandb://"
            )
        else:
            assert recorded_run["tags"]["pantheon.experiment_backend"] == "mlflow"

    expected_backend = "wandb" if args.backend in {"wandb", "wandb-online"} else "mlflow"
    assert result.promoted_metadata["artifact_state"] == "approved"
    assert result.promoted_metadata["deployment_stage"] == "live"
    assert result.promoted_metadata["experiment_refs"][0]["backend"] == expected_backend
    if args.backend == "wandb-online":
        assert result.promoted_metadata["experiment_refs"][0]["sync_status"] == "online_synced"
        assert result.promoted_metadata["experiment_refs"][0]["readback_refs"]["verified"] is True
    assert result.promoted_metadata["rollback"]["target_version"] == "1.9.0"

    print(f"LP-003 smoke test passed with backend={args.backend}: registry metadata mapped into experiment metadata.")


if __name__ == "__main__":
    main()
