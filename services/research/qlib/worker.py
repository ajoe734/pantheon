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
        import mlflow, qlib, tempfile
        p_dir = os.environ.get("QLIB_PROVIDER_URI") or tempfile.mkdtemp(prefix="qlib-provider-")
        t_uri = os.environ.get("QLIB_TRACKING_URI") or f"sqlite:///{Path(tempfile.mkdtemp(prefix='qlib-mlflow-')) / 'mlflow.db'}"
        exp_name = os.environ.get("QLIB_EXPERIMENT_NAME", "Experiment")
        try:
            client = mlflow.tracking.MlflowClient(t_uri)
            if client.get_experiment_by_name(exp_name) is None:
                client.create_experiment(exp_name, artifact_location=f"file://{tempfile.mkdtemp(prefix='qlib-artifacts-')}")
        except Exception:
            pass
        qlib.init(provider_uri=p_dir, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": t_uri, "default_exp_name": exp}})


def main() -> int:
    try:
        ActivationReadyGate.require_env()
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    dataset_path = os.environ.get("QLIB_DATASET_PATH")
    if not dataset_path:
        print("QLIB_DATASET_PATH not set; using sample dataset for gated smoke mode", file=sys.stderr)
        dataset_path = str(SERVICE_DIR / "examples" / "equity_dataset_sample.json")

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    config = TrainingConfig(
        version=os.environ.get("QLIB_ARTIFACT_VERSION", "1.0.0"), requested_by=os.environ.get("QLIB_REQUESTED_BY", "worker"), n_estimators=int(os.environ.get("QLIB_N_ESTIMATORS", "200")),
    )

    backend_name = os.environ.get("QLIB_BACKEND", "").strip().lower()
    if backend_name not in {"stub", "real"}:
        print("QLIB_BACKEND must be explicitly set to 'stub' or 'real'", file=sys.stderr)
        return 3
    backend = QlibLightGBMBackend() if backend_name == "real" else StubLightGBMBackend()
    if backend_name == "real":
        print("QLIB_BACKEND=real: using QlibLightGBMBackend", file=sys.stderr)
        init_qlib_runtime()

    enforce_floors = os.environ.get("QLIB_ENFORCE_DATA_FLOORS", "true").lower() in {"1", "true", "yes", "on"}
    output_dir = Path(os.environ.get("QLIB_OUTPUT_DIR", "/tmp/pantheon/research/qlib/activation-ready"))

    try:
        result = run_qlib_workflow(dataset, backend=backend, config=config, enforce_activation_ready=enforce_floors)
    except QlibWorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    re, tr = result.registry_entry, result.training_result
    manifest = persist_qlib_run_artifacts(result, output_dir)
    print(json.dumps({
        "registry_id": re["registry_id"], "artifact_state": re["artifact_state"],
        "deployment_stage": re["deployment_summary"]["current_stage"],
        "candidate_next_state": result.candidate_packet["requested_artifact_state"],
        "storage_path": re["storage_ref"]["path"], "checksum": re["checksum"],
        "backend": tr.backend, "metrics": tr.metrics,
        "artifact_refs": result.artifact_refs["refs"], "artifact_manifest": manifest,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
