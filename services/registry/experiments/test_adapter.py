from __future__ import annotations

import unittest

from adapter import InMemoryMlflowBackend, RegistryExperimentAdapter, ExperimentSyncError


def build_registry_entry(lifecycle_state: str = "paper") -> dict:
    entry = {
        "registry_id": "reg-strat-001-1.2.3",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-001",
        "version": "1.2.3",
        "lifecycle_state": lifecycle_state,
        "lineage": {
            "parent_registry_ids": ["reg-strat-001-1.2.2"],
            "source_run_ids": ["train-run-001"],
            "source_dataset_refs": ["dataset://feedback/2026-04-06"],
            "source_strategy_spec_id": "spec-001",
        },
        "storage_ref": {
            "backend": "object_store",
            "path": "object://pantheon/registry/strat-001/1.2.3/artifact.bin",
        },
        "checksum": "sha256:abc123def456",
        "producer_run_id": "train-run-001",
        "evaluation_summary": {
            "sharpe_ratio": 1.34,
            "max_drawdown": 0.11,
            "risk_review_passed": True,
        },
        "promoted_at": "2026-04-06T09:45:00Z",
        "approver": "risk-committee",
        "rollback_target": "1.2.2",
        "metadata": {
            "rollback_target_registry_id": "reg-strat-001-1.2.2",
        },
    }
    if lifecycle_state != "live":
        entry.pop("approver")
        entry.pop("rollback_target")
        entry["metadata"] = {}
    return entry


class TestRegistryExperimentAdapter(unittest.TestCase):
    def test_build_record_maps_tags_metrics_and_handoff(self):
        adapter = RegistryExperimentAdapter(backend=InMemoryMlflowBackend())

        record = adapter.build_record(build_registry_entry())

        self.assertEqual(record.experiment_name, "pantheon/model_artifact/strat-001")
        self.assertEqual(record.run_name, "1.2.3:paper")
        self.assertEqual(record.tags["pantheon.registry_id"], "reg-strat-001-1.2.3")
        self.assertEqual(record.tags["pantheon.lifecycle_state"], "paper")
        self.assertEqual(record.tags["pantheon.aliases"], "[\"paper\"]")
        self.assertEqual(record.metrics["sharpe_ratio"], 1.34)
        self.assertEqual(
            record.artifacts["artifact_handoff.json"]["execution_projection"]["metadata_path"],
            "openclaw/registry/strat-001/1.2.3/metadata.json",
        )
        self.assertEqual(
            record.artifacts["artifact_handoff.json"]["storage_ref"]["path"],
            "object://pantheon/registry/strat-001/1.2.3/artifact.bin",
        )

    def test_sync_registry_entry_returns_promoted_metadata_with_experiment_ref(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)

        result = adapter.sync_registry_entry(build_registry_entry())

        self.assertEqual(result.experiment_ref.backend, "mlflow")
        self.assertEqual(result.experiment_ref.project, "pantheon/model_artifact/strat-001")
        self.assertEqual(result.promoted_metadata["promotion_state"], "paper")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["run_id"], result.experiment_ref.run_id)
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["aliases"], ["paper"])
        self.assertIn(result.experiment_ref.run_id, backend.runs)

    def test_live_entry_requires_rollback_registry_metadata(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        live_entry = build_registry_entry(lifecycle_state="live")
        live_entry["metadata"] = {}

        with self.assertRaises(ExperimentSyncError):
            adapter.sync_registry_entry(live_entry)

    def test_live_entry_builds_reg003_compatible_rollback_object(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        live_entry = build_registry_entry(lifecycle_state="live")

        result = adapter.sync_registry_entry(live_entry)

        self.assertEqual(result.promoted_metadata["promotion_state"], "live")
        self.assertEqual(result.promoted_metadata["rollback"]["target_registry_id"], "reg-strat-001-1.2.2")
        self.assertEqual(result.promoted_metadata["rollback"]["target_version"], "1.2.2")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["aliases"], ["live"])


if __name__ == "__main__":
    unittest.main()
