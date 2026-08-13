"""Integration and contract tests for L12-MFC-R4-DISTILL-001.

Validates:
1. Terminal output persists canonical strategy_id, registry_id, version, and checksum.
2. Controller tick succeeded requires real Registry readback.
3. Same-source retries maintain stable versioned identity.
4. Real SourceRecord-to-Registry draft integration with real Registry service (no patched Registry client).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    DistillationControllerError,
    run_controller_tick,
)
from services.source_ingestion.distillation_worker import (
    DistillationJobQueue,
    DistillationJobStatus,
    make_distillation_worker,
    source_version_digest,
)


class DummyLoopWriter:
    def __init__(self) -> None:
        self.successes: list[dict[str, Any]] = []
        self.ticks: list[dict[str, Any]] = []

    async def record_success(self, **kwargs: Any) -> None:
        self.successes.append(kwargs)

    async def record_tick(self, **kwargs: Any) -> None:
        self.ticks.append(kwargs)


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def real_registry_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    registry_dir = tmp_path / "registry_storage"
    registry_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PANTHEON_REGISTRY_DATA_DIR", str(registry_dir))
    monkeypatch.setenv("REGISTRY_DATA_DIR", str(registry_dir))

    from services.registry.main import app

    port = _get_free_port()
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/health"
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=2)


def _normalized_source(
    source_id: str = "src-l12-distill-001",
    title: str = "TW Equity Statistical Arbitrage Signal Paper",
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id="conn-l12-distill",
        source_type="paper",
        title=title,
        content_ref=f"https://doi.org/10.1000/{source_id}",
        status="normalized",
        metadata={
            "trust_score": 0.85,
            "access_scope": ["research"],
            "license_scope": "internal",
            "keywords": ["pairs", "stat-arb", "tw-equity"],
            "strategy_seed": {
                "hypothesis": "Mean reversion in cointegrated TW equity pairs",
                "asset_class": ["equity"],
                "market_scope": ["Taiwan"],
                "holding_period": "1 day",
                "required_data": ["OHLCV"],
                "backend_hint": "qlib",
                "feature_hints": ["zscore_spread"],
                "label_hints": ["return_1d"],
                "risk_notes": ["sector_neutral"],
            },
        },
    )


def test_real_source_record_to_registry_draft_integration(
    tmp_path: Path, real_registry_server: str
) -> None:
    """Test 1: Real SourceRecord-to-Registry integration without patched Registry client.

    Proves that a normalized SourceRecord is distilled, registered into the real
    Registry FastAPI service, read back from the real HTTP GET endpoint, and
    produces a terminal observation carrying strategy_id, registry_id, version,
    and checksum.
    """
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.sqlite3"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"
    alive_path = tmp_path / "controller_alive"

    record = _normalized_source()
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")

    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url=real_registry_server,
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
    )

    state = ControllerState(
        controller_id="l12-distill-controller",
        controller_name="test-l12-distill-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "l12-test-sha"},
    )
    state_store = ControllerStateStore(state_path)
    state_store.save(state)

    writer = DummyLoopWriter()

    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)

    assert res["status"] == "success"
    assert res["actual"]["synced_count"] == 1
    assert len(res["actual"]["terminal_drafts"]) == 1

    draft = res["actual"]["terminal_drafts"][0]
    assert draft["source_id"] == record.source_id
    assert draft["status"] == "done"
    assert draft["registry_id"].startswith("reg-strategy-spec-src-l12-distill-001-")
    assert draft["strategy_id"].startswith("strat-src-l12-distill-001-")
    assert draft["version"] == "1.0.0"
    assert isinstance(draft["checksum"], str) and len(draft["checksum"]) > 0

    # Query the real HTTP Registry endpoint directly to prove HTTP readback truth
    url = f"{real_registry_server}/api/registry/strategy-specs/{draft['registry_id']}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200
        data = json.loads(response.read().decode("utf-8"))
        entry = data.get("entry") or data
        assert entry["registry_id"] == draft["registry_id"]
        assert entry["strategy_id"] == draft["strategy_id"]
        assert entry["version"] == draft["version"]
        assert entry["checksum"] == draft["checksum"]

    # Verify queue persistence carries the identical fields
    queue = DistillationJobQueue(job_queue_path)
    job = queue.get(record.source_id)
    assert job is not None
    assert job.status == DistillationJobStatus.DONE
    assert job.registry_id == draft["registry_id"]
    assert job.strategy_id == draft["strategy_id"]
    assert job.version == draft["version"]
    assert job.checksum == draft["checksum"]


def test_registry_readback_failure_blocks_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 2: Succeeded requires successful Registry readback.

    If write to Registry succeeds but readback fails (returns None or raises error),
    the tick must raise DistillationControllerError and fail fast.
    """
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.sqlite3"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"
    alive_path = tmp_path / "controller_alive"

    record = _normalized_source("src-readback-fail-001")
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")

    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-failing-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
    )

    state = ControllerState(
        controller_id="readback-fail-controller",
        controller_name="test-readback-fail-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(state_path)
    state_store.save(state)

    writer = DummyLoopWriter()

    # Mock registry: write succeeds but readback returns None (readback failed)
    def mock_get_registry_entry(url: str, registry_id: str) -> dict | None:
        return None

    def mock_register_strategy_spec(url: str, payload: dict) -> dict:
        return {"status": "ok"}

    monkeypatch.setattr(
        "services.source_ingestion.distillation_controller._get_registry_entry",
        mock_get_registry_entry,
    )
    monkeypatch.setattr(
        "services.source_ingestion.distillation_controller._register_strategy_spec_if_absent",
        mock_register_strategy_spec,
    )

    with pytest.raises(DistillationControllerError) as exc_info:
        run_controller_tick(config=config, state=state, store=state_store, writer=writer)

    assert "Distillation Registry delivery is degraded" in str(exc_info.value)
    assert len(writer.successes) == 0


def test_same_source_retry_has_stable_identity(
    tmp_path: Path, real_registry_server: str
) -> None:
    """Test 3: Same-source retries have stable identity across runs and readbacks.

    Running distillation multiple times for identical source content produces
    the exact same registry_id, strategy_id, version, and checksum.
    """
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.sqlite3"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"
    alive_path = tmp_path / "controller_alive"

    record = _normalized_source("src-retry-stable-001")
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")

    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url=real_registry_server,
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
    )

    state = ControllerState(
        controller_id="stable-id-controller",
        controller_name="test-stable-id-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(state_path)
    state_store.save(state)
    writer = DummyLoopWriter()

    res1 = run_controller_tick(config=config, state=state, store=state_store, writer=writer)
    assert res1["status"] == "success"
    draft1 = res1["actual"]["terminal_drafts"][0]

    # Run second tick with the same source record
    res2 = run_controller_tick(config=config, state=state, store=state_store, writer=writer)
    assert res2["status"] == "success"
    draft2 = res2["actual"]["terminal_drafts"][0]

    assert draft1["registry_id"] == draft2["registry_id"]
    assert draft1["strategy_id"] == draft2["strategy_id"]
    assert draft1["version"] == draft2["version"]
    assert draft1["checksum"] == draft2["checksum"]
