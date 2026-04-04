"""
LP-003: MLflow Registry Adapter.
Maps Pantheon REG-001 registry entries to MLflow experiment tracking.
"""
import mlflow
from typing import Any, Dict


class MLflowRegistryAdapter:
    def __init__(self, tracking_uri: str, experiment_name: str = "pantheon-strategies"):
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)
        self.version_pin = "2.11.0"  # Per OSS_INTEGRATION_CHECKLIST.md

    def log_registry_entry(self, entry: Dict[str, Any], metrics: Dict[str, float] = None):
        """Logs a REG-001 registry entry as an MLflow run."""
        with mlflow.start_run(run_name=f"{entry['strategy_id']}-v{entry['version']}"):
            mlflow.log_params({
                "strategy_id": entry.get("strategy_id"),
                "version": entry.get("version"),
                "artifact_type": entry.get("artifact_type"),
                "lifecycle_state": entry.get("lifecycle_state"),
                "producer_run_id": entry.get("producer_run_id", "manual"),
            })

            lineage = entry.get("lineage", {})
            mlflow.set_tags({
                "pantheon.source_run_id": lineage.get("source_run_id", "unknown"),
                "pantheon.promotion_state": entry.get("lifecycle_state"),
                "pantheon.checksum": entry.get("checksum", ""),
                "oss.mlflow_version": self.version_pin,
            })

            if metrics:
                mlflow.log_metrics(metrics)

            eval_summary = entry.get("evaluation_summary", {})
            if eval_summary:
                for key, val in eval_summary.items():
                    if isinstance(val, (int, float)):
                        mlflow.log_metric(f"eval.{key}", val)

            return mlflow.active_run().info.run_id

    def get_adapter_info(self):
        return {"upstream": "mlflow", "version": self.version_pin}
