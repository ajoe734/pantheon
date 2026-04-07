from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from services.execution.artifact_loader import (
    ArtifactLoadError,
    ArtifactLoader,
    ExecutionMode,
    materialize_execution_projection,
)
from services.registry.promotion.gate import PromotionGate, PromotionState


def _checksum(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_metadata(state: str = "paper") -> dict:
    metadata = {
        "registry_id": "reg-strat-001-1.2.3",
        "strategy_id": "strat-001",
        "version": "1.2.3",
        "artifact_type": "model_artifact",
        "promotion_state": state,
        "checksum": _checksum(b'{"weights":[1,2,3]}'),
        "lineage": {
            "parent_registry_ids": ["reg-strat-001-1.2.2"],
            "source_run_ids": ["train-run-001"],
        },
        "created_at": "2026-04-06T12:00:00Z",
    }
    if state == "live":
        metadata["approved_at"] = "2026-04-06T12:05:00Z"
        metadata["approver"] = "risk-committee"
        metadata["rollback"] = {
            "target_registry_id": "reg-strat-001-1.2.2",
            "target_version": "1.2.2",
        }
    return metadata


class FakeObjectStore:
    def __init__(self, mapping: dict[str, bytes | str]):
        self.mapping = mapping

    def ContainsKey(self, key: str) -> bool:
        return key in self.mapping

    def Read(self, key: str) -> str:
        value = self.mapping[key]
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def ReadBytes(self, key: str) -> bytes:
        value = self.mapping[key]
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    def Save(self, key: str, value: str) -> None:
        self.mapping[key] = value

    def SaveBytes(self, key: str, value: bytes) -> None:
        self.mapping[key] = value


class LowerCaseObjectStore:
    def __init__(self):
        self.mapping: dict[str, bytes] = {}
        self.temp_dir = tempfile.TemporaryDirectory()

    def contains_key(self, key: str) -> bool:
        return key in self.mapping

    def read(self, key: str) -> str:
        return self.mapping[key].decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        return self.mapping[key]

    def save(self, key: str, value: str) -> None:
        self.mapping[key] = value.encode("utf-8")

    def save_bytes(self, key: str, value: bytes) -> None:
        self.mapping[key] = value

    def get_file_path(self, key: str) -> str:
        path = Path(self.temp_dir.name) / key.replace("/", "__")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.mapping[key])
        return str(path)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


class FakeLeanAlgorithm:
    def __init__(self, object_store):
        self.object_store = object_store


class TestArtifactLoader(unittest.TestCase):
    def setUp(self):
        self.payload = b'{"weights":[1,2,3]}'
        self.projection = ArtifactLoader.build_projection("strat-001", "1.2.3")
        self.gate = PromotionGate()

    def tearDown(self):
        lower_store = getattr(self, "_lower_store", None)
        if lower_store is not None:
            lower_store.cleanup()

    def _build_loader(self, metadata: dict) -> ArtifactLoader:
        store = FakeObjectStore(
            {
                self.projection.metadata_key: json.dumps(metadata),
                self.projection.artifact_key: self.payload,
            }
        )
        return ArtifactLoader(store)

    def test_loads_paper_artifact_for_paper_mode(self):
        loader = self._build_loader(build_metadata())

        loaded = loader.load("strat-001", "1.2.3", ExecutionMode.PAPER)

        self.assertEqual(loaded.metadata["promotion_state"], "paper")
        self.assertEqual(loaded.payload, self.payload)
        self.assertEqual(loaded.projection.metadata_key, self.projection.metadata_key)

    def test_rejects_wrong_promotion_state_for_mode(self):
        loader = self._build_loader(build_metadata(state="live"))

        with self.assertRaises(ArtifactLoadError):
            loader.load("strat-001", "1.2.3", ExecutionMode.PAPER)

    def test_rejects_live_artifact_without_rollback(self):
        metadata = build_metadata(state="live")
        metadata.pop("rollback")
        loader = self._build_loader(metadata)

        with self.assertRaises(ArtifactLoadError):
            loader.load("strat-001", "1.2.3", ExecutionMode.LIVE)

    def test_rejects_checksum_mismatch(self):
        metadata = build_metadata()
        metadata["checksum"] = "sha256:not-the-right-value"
        loader = self._build_loader(metadata)

        with self.assertRaises(ArtifactLoadError):
            loader.load("strat-001", "1.2.3", ExecutionMode.PAPER)

    def test_rejects_live_artifact_with_self_referencing_rollback(self):
        metadata = build_metadata(state="live")
        metadata["rollback"] = {
            "target_registry_id": metadata["registry_id"],
            "target_version": metadata["version"],
        }
        loader = self._build_loader(metadata)

        with self.assertRaises(ArtifactLoadError):
            loader.load("strat-001", "1.2.3", ExecutionMode.LIVE)

    def test_rejects_metadata_mismatch_for_requested_version(self):
        loader = self._build_loader(build_metadata())

        with self.assertRaises(ArtifactLoadError):
            loader.load("strat-001", "9.9.9", ExecutionMode.PAPER)

    def test_from_runtime_supports_lowercase_lean_python_api(self):
        self._lower_store = LowerCaseObjectStore()
        algo = FakeLeanAlgorithm(self._lower_store)
        projection = self.gate.build_execution_projection(
            {
                "registry_id": "reg-strat-001-1.2.3",
                "artifact_type": "model_artifact",
                "strategy_id": "strat-001",
                "version": "1.2.3",
                "lifecycle_state": "paper",
                "checksum": _checksum(self.payload),
                "lineage": {
                    "source_run_ids": ["train-run-001"],
                },
            }
        )

        materialize_execution_projection(algo, projection, self.payload)
        loader = ArtifactLoader.from_runtime(algo)

        loaded = loader.load("strat-001", "1.2.3", ExecutionMode.PAPER)

        self.assertEqual(loaded.payload, self.payload)
        self.assertEqual(loaded.metadata["promotion_state"], "paper")
        self.assertTrue(loaded.artifact_path)
        self.assertTrue(Path(loaded.artifact_path).exists())

    def test_materialize_execution_projection_writes_canonical_keys(self):
        live_entry = {
            "registry_id": "reg-strat-001-1.2.3",
            "artifact_type": "model_artifact",
            "strategy_id": "strat-001",
            "version": "1.2.3",
            "lifecycle_state": "paper",
            "checksum": _checksum(self.payload),
            "lineage": {
                "source_run_ids": ["train-run-001"],
            },
            "evaluation_summary": {
                "risk_review_passed": True,
                "sharpe_ratio": 1.4,
            },
            "approver": "risk-committee",
            "metadata": {
                "rollback": {
                    "target_registry_id": "reg-strat-001-1.2.2",
                    "target_version": "1.2.2",
                }
            },
        }
        live_entry = self.gate.promote(live_entry, PromotionState.LIVE)
        projection = self.gate.build_execution_projection(live_entry)
        store = FakeObjectStore({})

        written = materialize_execution_projection(store, projection, self.payload)

        self.assertEqual(written.metadata_key, self.projection.metadata_key)
        self.assertEqual(written.artifact_key, self.projection.artifact_key)
        self.assertIn(self.projection.metadata_key, store.mapping)
        self.assertIn(self.projection.artifact_key, store.mapping)
        self.assertEqual(store.ReadBytes(self.projection.artifact_key), self.payload)

    def test_materialize_execution_projection_rejects_missing_metadata(self):
        store = FakeObjectStore({})

        with self.assertRaises(ArtifactLoadError):
            materialize_execution_projection(
                store,
                type("BrokenProjection", (), {"metadata_key": "a", "artifact_key": "b"})(),
                self.payload,
            )


if __name__ == "__main__":
    unittest.main()
