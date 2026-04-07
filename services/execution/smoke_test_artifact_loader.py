#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.artifact_loader import (
    ArtifactLoader,
    ExecutionMode,
    materialize_execution_projection,
)
from services.registry.promotion.gate import PromotionGate, PromotionState


class InMemoryObjectStore:
    def __init__(self):
        self._objects: dict[str, bytes] = {}

    def ContainsKey(self, key: str) -> bool:
        return key in self._objects

    def Read(self, key: str) -> str:
        return self._objects[key].decode("utf-8")

    def ReadBytes(self, key: str) -> bytes:
        return self._objects[key]

    def Save(self, key: str, value: str) -> None:
        self._objects[key] = value.encode("utf-8")

    def SaveBytes(self, key: str, value: bytes) -> None:
        self._objects[key] = value


def _checksum(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_registry_entry() -> dict:
    payload = b'{"weights":[0.25,0.75]}'
    return {
        "registry_id": "reg-smoke-alpha-1.4.0",
        "artifact_type": "model_artifact",
        "strategy_id": "smoke-alpha",
        "version": "1.4.0",
        "lifecycle_state": "paper",
        "checksum": _checksum(payload),
        "lineage": {
            "parent_registry_ids": ["reg-smoke-alpha-1.3.0"],
            "source_run_ids": ["train-smoke-001"],
        },
        "evaluation_summary": {
            "risk_review_passed": True,
            "sharpe_ratio": 1.31,
        },
        "metadata": {
            "rollback": {
                "target_registry_id": "reg-smoke-alpha-1.3.0",
                "target_version": "1.3.0",
            }
        },
        "_artifact_bytes": payload,
    }


def run_smoke() -> None:
    gate = PromotionGate()
    entry = build_registry_entry()
    artifact_bytes = entry.pop("_artifact_bytes")

    live_entry = gate.promote(entry, PromotionState.LIVE, approver="risk-committee")
    projection = gate.build_execution_projection(live_entry)

    store = InMemoryObjectStore()
    materialize_execution_projection(store, projection, artifact_bytes)

    loader = ArtifactLoader.from_runtime(store)
    loaded = loader.load(
        strategy_id=live_entry["strategy_id"],
        version=live_entry["version"],
        execution_mode=ExecutionMode.LIVE,
    )

    assert loaded.metadata["promotion_state"] == "live"
    assert loaded.metadata["rollback"]["target_version"] == "1.3.0"
    assert loaded.payload == artifact_bytes
    assert loaded.artifact_path is None
    print(
        "EX-001 smoke test passed: promotion metadata projected through the LEAN Object Store helper and loaded safely."
    )


if __name__ == "__main__":
    try:
        run_smoke()
    except Exception as exc:  # pragma: no cover - smoke path
        print(f"EX-001 smoke test failed: {exc}", file=sys.stderr)
        raise
