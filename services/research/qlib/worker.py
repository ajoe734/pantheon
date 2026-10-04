"""Qlib LightGBM worker entry point for the governed research container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.qlib_adapter import (
    ActivationReadyGate,
    QlibLightGBMBackend,
    QlibWorkflowError,
    StubLightGBMBackend,
    TrainingConfig,
    persist_qlib_run_artifacts,
    run_qlib_workflow,
)


def init_qlib_runtime() -> None:
    try:
        from qlib.workflow import R

        _ = R.exp_manager
    except Exception:
        import mlflow
        import qlib
        import tempfile

        provider_dir = os.environ.get("QLIB_PROVIDER_URI") or tempfile.mkdtemp(
            prefix="qlib-provider-"
        )
        mlflow_dir = Path(tempfile.mkdtemp(prefix="qlib-mlflow-"))
        tracking_uri = os.environ.get("QLIB_TRACKING_URI") or f"sqlite:///{mlflow_dir / 'mlflow.db'}"
        experiment_name = os.environ.get("QLIB_EXPERIMENT_NAME", "Experiment")
        try:
            client = mlflow.tracking.MlflowClient(tracking_uri)
            if client.get_experiment_by_name(experiment_name) is None:
                artifact_loc = f"file://{tempfile.mkdtemp(prefix='qlib-artifacts-')}"
                client.create_experiment(experiment_name, artifact_location=artifact_loc)
        except Exception:
            pass
        exp_manager = {
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {
                "uri": tracking_uri,
                "default_exp_name": experiment_name,
            },
        }
        qlib.init(provider_uri=provider_dir, exp_manager=exp_manager)


def main() -> int:
    try:
        ActivationReadyGate.require_env()
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    dataset_path = os.environ.get("QLIB_DATASET_PATH")
    if not dataset_path:
        print(
            "QLIB_DATASET_PATH not set; using sample dataset for gated smoke mode",
            file=sys.stderr,
        )
        dataset_path = str(SERVICE_DIR / "examples" / "equity_dataset_sample.json")

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    config = TrainingConfig(
        version=os.environ.get("QLIB_ARTIFACT_VERSION", "1.0.0"),
        requested_by=os.environ.get("QLIB_REQUESTED_BY", "worker"),
        n_estimators=int(os.environ.get("QLIB_N_ESTIMATORS", "200")),
    )

    backend_name = os.environ.get("QLIB_BACKEND", "").strip().lower()
    if backend_name not in {"stub", "real"}:
        print("QLIB_BACKEND must be explicitly set to 'stub' or 'real'", file=sys.stderr)
        return 3
    backend = QlibLightGBMBackend() if backend_name == "real" else StubLightGBMBackend()
    if backend_name == "real":
        print("QLIB_BACKEND=real: using QlibLightGBMBackend", file=sys.stderr)
        init_qlib_runtime()

    enforce_floors = os.environ.get("QLIB_ENFORCE_DATA_FLOORS", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    output_dir = Path(
        os.environ.get("QLIB_OUTPUT_DIR", "/tmp/pantheon/research/qlib/activation-ready")
    )

    try:
        result = run_qlib_workflow(
            dataset,
            backend=backend,
            config=config,
            enforce_activation_ready=enforce_floors,
        )
    except QlibWorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    manifest = persist_qlib_run_artifacts(result, output_dir)
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "candidate_next_state": result.candidate_packet["requested_artifact_state"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "checksum": result.registry_entry["checksum"],
        "backend": result.training_result.backend,
        "metrics": result.training_result.metrics,
        "artifact_refs": result.artifact_refs["refs"],
        "artifact_manifest": manifest,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
