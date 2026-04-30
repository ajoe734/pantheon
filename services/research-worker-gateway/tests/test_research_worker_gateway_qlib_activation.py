from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]


def _load_gateway_module(
    *,
    data_dir: str | None = None,
    offline_gate: str = "false",
):
    with mock.patch.dict(
        "os.environ",
        {
            "RESEARCH_WORKER_GATEWAY_DATA_DIR": data_dir or tempfile.mkdtemp(),
            "RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS": "4",
            "RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS": "false",
            "RESEARCH_ORCHESTRATOR_URL": "http://research-orchestrator-svc:8101",
            "PANTHEON_OFFLINE_GATE_ENABLED": offline_gate,
            "PANTHEON_REPO_ROOT": str(REPO_ROOT),
            "PANTHEON_OFFLINE_WORKER_TIMEOUT": "120",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location(
                "rwg_qlib_activation_test_main",
                SERVICE_DIR / "main.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["rwg_qlib_activation_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_closed_gateway_rejects_qlib_offline_activation_path() -> None:
    module = _load_gateway_module(offline_gate="false")
    client = TestClient(module.app)

    result = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "qlib",
            "requested_mode": "offline",
            "dispatch_mode": "offline",
            "objective": "Run qlib activation-ready offline research.",
            "parameters": {
                "PANTHEON_QLIB_ACTIVATION_READY_ENABLED": "1",
                "QLIB_BACKEND": "stub",
            },
            "idempotency_key": "qlib-closed-gate",
            "requested_at": "2026-04-30T06:00:00Z",
        },
    )

    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    assert payload["rejection"]["reason"] == "dispatch_mode_disabled"
    assert payload["production_activation"] == "disabled"


def test_open_gateway_runs_qlib_worker_and_persists_handoff_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dataset_path = Path(tmp) / "qlib_activation_ready_dataset.json"
        output_dir = Path(tmp) / "qlib-output"
        dataset_path.write_text(
            json.dumps(_activation_ready_dataset()),
            encoding="utf-8",
        )
        module = _load_gateway_module(offline_gate="true")
        client = TestClient(module.app)

        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": "qlib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "objective": "Run qlib activation-ready offline research.",
                "parameters": {
                    "PANTHEON_QLIB_ACTIVATION_READY_ENABLED": "1",
                    "QLIB_BACKEND": "stub",
                    "QLIB_DATASET_PATH": str(dataset_path),
                    "QLIB_OUTPUT_DIR": str(output_dir),
                    "QLIB_ENFORCE_DATA_FLOORS": "true",
                    "QLIB_N_ESTIMATORS": "10",
                },
                "idempotency_key": "qlib-open-gate",
                "requested_at": "2026-04-30T06:01:00Z",
            },
        )

        assert result.status_code == 201
        job = result.json()
        assert job["status"] == "completed"
        assert job["exit_code"] == 0
        assert job["stderr"] == ""
        assert job["production_activation"] == "disabled"
        payload = json.loads(job["stdout"])
        assert payload["backend"] == "stub_lgbm"
        assert payload["candidate_next_state"] == "candidate"
        assert payload["artifact_state"] == "draft"
        assert payload["deployment_stage"] == "none"
        assert payload["checksum"].startswith("sha256:")
        for path in payload["artifact_manifest"]["files"].values():
            assert Path(path).exists()


def _activation_ready_dataset() -> dict:
    records = []
    start = date(2024, 1, 1)
    for inst_idx in range(50):
        instrument = f"SYM{inst_idx:03d}"
        base = 100.0 + inst_idx
        for period_idx in range(504):
            day = start + timedelta(days=period_idx * 2)
            close = base + period_idx * 0.05
            records.append(
                {
                    "instrument": instrument,
                    "date": day.isoformat(),
                    "open": close - 0.2,
                    "high": close + 0.4,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000_000 + inst_idx * 1000 + period_idx,
                }
            )
    return {
        "dataset_id": "dataset:activation-ready-daily",
        "strategy_id": "equity-cross-sectional-alpha",
        "source_dataset_refs": ["dataset:equity-top50-daily-activation-ready"],
        "source_strategy_spec_id": "strategy-spec:rs003-alpha-v1",
        "data_frequency": "daily",
        "records": records,
    }
