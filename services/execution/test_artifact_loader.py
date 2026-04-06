from __future__ import annotations

import hashlib
import json
import unittest

from services.execution.artifact_loader import ArtifactLoadError, ArtifactLoader, ExecutionMode


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


class TestArtifactLoader(unittest.TestCase):
    def setUp(self):
        self.payload = b'{"weights":[1,2,3]}'
        self.projection = ArtifactLoader.build_projection("strat-001", "1.2.3")

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

    def test_rejects_metadata_mismatch_for_requested_version(self):
        loader = self._build_loader(build_metadata())

        with self.assertRaises(ArtifactLoadError):
            loader.load("strat-001", "9.9.9", ExecutionMode.PAPER)


if __name__ == "__main__":
    unittest.main()
