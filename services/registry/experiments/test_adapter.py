from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from adapter import (
    ExperimentSyncError,
    InMemoryMlflowBackend,
    LocalWandbRunStore,
    OfflineWandbLocalBackend,
    OfflineWandbPrepBackend,
    RegistryExperimentAdapter,
)
from config import selected_backend, selected_wandb_mode


def build_registry_entry(artifact_state: str = "approved", deployment_stage: str = "paper") -> dict:
    entry = {
        "registry_id": "reg-strat-001-1.2.3",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-001",
        "version": "1.2.3",
        "artifact_state": artifact_state,
        "deployment_stage": deployment_stage,
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
    if deployment_stage != "live":
        entry.pop("approver")
        entry.pop("rollback_target")
        entry["metadata"] = {}
    return entry


class TestRegistryExperimentAdapter(unittest.TestCase):
    def test_build_record_maps_tags_metrics_and_handoff(self):
        adapter = RegistryExperimentAdapter(backend=InMemoryMlflowBackend())

        record = adapter.build_record(build_registry_entry())

        self.assertEqual(record.experiment_name, "pantheon/model_artifact/strat-001")
        self.assertEqual(record.run_name, "1.2.3:approved:paper")
        self.assertEqual(record.tags["pantheon.registry_id"], "reg-strat-001-1.2.3")
        self.assertEqual(record.tags["pantheon.artifact_state"], "approved")
        self.assertEqual(record.tags["pantheon.deployment_stage"], "paper")
        self.assertEqual(record.tags["pantheon.experiment_backend"], "mlflow")
        self.assertEqual(record.tags["pantheon.experiment_backend_version"], "3.10.1")
        self.assertEqual(record.tags["pantheon.aliases"], "[\"approved\"]")
        self.assertEqual(record.metrics["sharpe_ratio"], 1.34)
        self.assertEqual(
            record.artifacts["artifact_handoff.json"]["execution_projection"]["metadata_path"],
            "openclaw/registry/strat-001/1.2.3/metadata.json",
        )
        self.assertEqual(
            record.artifacts["artifact_handoff.json"]["storage_ref"]["path"],
            "object://pantheon/registry/strat-001/1.2.3/artifact.bin",
        )
        self.assertEqual(record.artifacts["artifact_handoff.json"]["backend"], "mlflow")
        self.assertEqual(record.artifacts["artifact_handoff.json"]["tracking_version"], "3.10.1")

    def test_sync_registry_entry_returns_promoted_metadata_with_experiment_ref(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)

        result = adapter.sync_registry_entry(build_registry_entry())

        self.assertEqual(result.experiment_ref.backend, "mlflow")
        self.assertEqual(result.experiment_ref.project, "pantheon/model_artifact/strat-001")
        self.assertEqual(result.promoted_metadata["artifact_state"], "approved")
        self.assertEqual(result.promoted_metadata["deployment_stage"], "paper")
        self.assertEqual(result.promoted_metadata["promotion_state"], "approved")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["run_id"], result.experiment_ref.run_id)
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["aliases"], ["approved"])
        self.assertIn(result.experiment_ref.run_id, backend.runs)

    def test_live_entry_requires_rollback_registry_metadata(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        live_entry = build_registry_entry(deployment_stage="live")
        live_entry["metadata"] = {}

        with self.assertRaises(ExperimentSyncError):
            adapter.sync_registry_entry(live_entry)
        self.assertEqual(backend.runs, {})

    def test_live_entry_builds_reg003_compatible_rollback_object(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        live_entry = build_registry_entry(deployment_stage="live")

        result = adapter.sync_registry_entry(live_entry)

        self.assertEqual(result.promoted_metadata["artifact_state"], "approved")
        self.assertEqual(result.promoted_metadata["deployment_stage"], "live")
        self.assertEqual(result.promoted_metadata["promotion_state"], "approved")
        self.assertEqual(result.promoted_metadata["rollback"]["target_registry_id"], "reg-strat-001-1.2.2")
        self.assertEqual(result.promoted_metadata["rollback"]["target_version"], "1.2.2")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["aliases"], ["approved"])

    def test_frozen_stage_is_accepted_and_mirrored_for_approved_artifacts(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        frozen_entry = build_registry_entry(deployment_stage="frozen")

        result = adapter.sync_registry_entry(frozen_entry)

        self.assertEqual(result.record.run_name, "1.2.3:approved:frozen")
        self.assertEqual(result.record.tags["pantheon.deployment_stage"], "frozen")
        self.assertEqual(result.record.artifacts["artifact_handoff.json"]["deployment_stage"], "frozen")
        self.assertEqual(result.promoted_metadata["deployment_stage"], "frozen")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["aliases"], ["approved"])
        self.assertIn(result.experiment_ref.run_id, backend.runs)

    def test_non_approved_artifact_cannot_use_non_none_deployment_stage(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        invalid_entry = build_registry_entry(artifact_state="candidate", deployment_stage="paper")

        with self.assertRaisesRegex(ExperimentSyncError, "artifact_state=approved"):
            adapter.sync_registry_entry(invalid_entry)

        self.assertEqual(backend.runs, {})

    def test_legacy_lifecycle_state_is_compatibility_projection_only(self):
        backend = InMemoryMlflowBackend()
        adapter = RegistryExperimentAdapter(backend=backend)
        legacy_entry = build_registry_entry()
        legacy_entry.pop("artifact_state")
        legacy_entry.pop("deployment_stage")
        legacy_entry["lifecycle_state"] = "live"
        legacy_entry["rollback_target"] = "1.2.2"
        legacy_entry["metadata"] = {"rollback_target_registry_id": "reg-strat-001-1.2.2"}

        result = adapter.sync_registry_entry(legacy_entry)

        self.assertEqual(result.record.tags["pantheon.artifact_state"], "approved")
        self.assertEqual(result.record.tags["pantheon.deployment_stage"], "live")
        self.assertEqual(result.record.tags["pantheon.compat.lifecycle_state"], "live")
        self.assertNotIn("pantheon.lifecycle_state", result.record.tags)
        self.assertEqual(result.promoted_metadata["compat"]["legacy_lifecycle_state"], "live")

    def test_wandb_local_backend_preserves_promoted_metadata_shape_and_refs(self):
        backend = OfflineWandbLocalBackend(mode="offline")
        adapter = RegistryExperimentAdapter(backend=backend)

        result = adapter.sync_registry_entry(build_registry_entry())

        self.assertEqual(result.experiment_ref.backend, "wandb")
        self.assertTrue(result.experiment_ref.run_id.startswith("wandb-local-"))
        self.assertTrue(result.experiment_ref.run_uri.startswith("wandb-local://runs/"))
        self.assertEqual(result.experiment_ref.sync_status, "offline_local")
        self.assertIn("artifact_handoff.json", result.experiment_ref.artifact_refs)
        self.assertEqual(result.record.tags["pantheon.experiment_backend"], "wandb")
        self.assertEqual(result.record.tags["pantheon.wandb.offline_local"], "true")
        self.assertEqual(result.record.tags["pantheon.wandb.online_sync_gate"], "PANTHEON_WANDB_ONLINE_SYNC_ENABLED")
        self.assertEqual(result.record.artifacts["artifact_handoff.json"]["backend"], "wandb")
        self.assertEqual(result.record.artifacts["artifact_handoff.json"]["artifact_state"], "approved")
        self.assertEqual(result.record.artifacts["artifact_handoff.json"]["deployment_stage"], "paper")
        self.assertEqual(result.promoted_metadata["artifact_state"], "approved")
        self.assertEqual(result.promoted_metadata["deployment_stage"], "paper")
        self.assertEqual(result.promoted_metadata["promotion_state"], "approved")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["backend"], "wandb")
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["aliases"], ["approved"])
        self.assertEqual(result.promoted_metadata["experiment_refs"][0]["sync_status"], "offline_local")
        self.assertIn("artifact_refs", result.promoted_metadata["experiment_refs"][0])
        self.assertIn(result.experiment_ref.run_id, backend.runs)

    def test_wandb_local_store_resolves_run_and_artifact_refs(self):
        with tempfile.TemporaryDirectory() as store_dir:
            backend = OfflineWandbLocalBackend(mode="dryrun", store_dir=store_dir)
            adapter = RegistryExperimentAdapter(backend=backend)

            result = adapter.sync_registry_entry(build_registry_entry())

            run = backend.get_run(result.experiment_ref.run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run["run_uri"], result.experiment_ref.run_uri)
            self.assertEqual(run["sync_status"], "offline_local")
            self.assertEqual(run["online_sync"]["enabled"], False)

            artifact = backend.get_artifact(result.experiment_ref.run_id, "artifact_handoff.json")
            self.assertIsNotNone(artifact)
            self.assertEqual(artifact["artifact_name"], "artifact_handoff.json")
            self.assertEqual(
                artifact["checksum"],
                result.experiment_ref.artifact_refs["artifact_handoff.json"]["checksum"],
            )

    def test_wandb_online_sync_requires_explicit_gate_and_still_has_no_sdk_path(self):
        backend = OfflineWandbLocalBackend(mode="offline")
        adapter = RegistryExperimentAdapter(backend=backend)
        result = adapter.sync_registry_entry(build_registry_entry())

        with self.assertRaisesRegex(ExperimentSyncError, "online sync is disabled"):
            backend.sync_online(result.experiment_ref.run_id)

        with patch.dict(os.environ, {"PANTHEON_WANDB_ONLINE_SYNC_ENABLED": "1"}, clear=False):
            with self.assertRaisesRegex(ExperimentSyncError, "SDK-backed/network sync is not implemented"):
                backend.sync_online(result.experiment_ref.run_id)

    def test_wandb_prep_backend_alias_remains_compatible(self):
        self.assertIs(OfflineWandbPrepBackend, OfflineWandbLocalBackend)
        with tempfile.TemporaryDirectory() as store_dir:
            self.assertTrue(hasattr(LocalWandbRunStore(store_dir), "list_runs"))

    def test_selected_backend_rejects_wandb_without_feature_flag(self):
        with patch.dict(os.environ, {"EXPERIMENT_BACKEND": "wandb"}, clear=False):
            with self.assertRaises(EnvironmentError):
                selected_backend()

    def test_selected_backend_accepts_wandb_with_deferred_prep_flag(self):
        env = {
            "EXPERIMENT_BACKEND": "wandb",
            "PANTHEON_ENABLE_WANDB_DEFERRED_PREP": "1",
            "PANTHEON_WANDB_MODE": "dryrun",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(selected_backend(), "wandb")
            self.assertEqual(selected_wandb_mode(), "dryrun")
            adapter = RegistryExperimentAdapter.from_env()

        self.assertEqual(adapter.backend.backend_name, "wandb")
        self.assertEqual(adapter.backend.tracking_version, "offline-local-store-v1")

    def test_selected_backend_rejects_online_wandb_mode(self):
        env = {
            "EXPERIMENT_BACKEND": "wandb",
            "PANTHEON_ENABLE_WANDB_DEFERRED_PREP": "1",
            "PANTHEON_WANDB_MODE": "online",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(EnvironmentError):
                selected_backend()


if __name__ == "__main__":
    unittest.main()
