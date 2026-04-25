from __future__ import annotations

import argparse

from adapter import InMemoryMlflowBackend, OfflineWandbPrepBackend, RegistryExperimentAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LP-003 registry-to-experiment smoke test")
    parser.add_argument(
        "--backend",
        choices=("memory", "mlflow", "wandb"),
        default="memory",
        help="Use the in-memory MLflow test backend, a real MLflow tracking server, or the offline W&B prep scaffold.",
    )
    parser.add_argument(
        "--tracking-uri",
        default=None,
        help="Optional MLflow tracking URI when --backend mlflow is selected.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.backend == "mlflow":
        adapter = RegistryExperimentAdapter.from_tracking_uri(tracking_uri=args.tracking_uri)
        backend = None
    elif args.backend == "wandb":
        backend = OfflineWandbPrepBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
    else:
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)

    registry_entry = {
        "registry_id": "reg-mean-reversion-2.0.0",
        "artifact_type": "model_artifact",
        "strategy_id": "mean-reversion",
        "version": "2.0.0",
        "lifecycle_state": "live",
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
        assert recorded_run["tags"]["pantheon.lifecycle_state"] == "live"
        assert recorded_run["aliases"] == ["live"]
        assert recorded_run["artifacts"]["artifact_handoff.json"]["storage_ref"]["path"].endswith("/artifact.bin")
        if args.backend == "wandb":
            assert recorded_run["mode"] == "offline"
            assert recorded_run["tags"]["pantheon.experiment_backend"] == "wandb"
            assert recorded_run["tags"]["pantheon.wandb.prep_only"] == "true"
        else:
            assert recorded_run["tags"]["pantheon.experiment_backend"] == "mlflow"

    expected_backend = "wandb" if args.backend == "wandb" else "mlflow"
    assert result.promoted_metadata["experiment_refs"][0]["backend"] == expected_backend
    assert result.promoted_metadata["rollback"]["target_version"] == "1.9.0"

    print(f"LP-003 smoke test passed with backend={args.backend}: registry metadata mapped into experiment metadata.")


if __name__ == "__main__":
    main()
