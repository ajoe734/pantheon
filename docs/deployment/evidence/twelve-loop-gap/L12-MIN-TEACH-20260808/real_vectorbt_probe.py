from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[5]
TESTS_DIR = REPO_ROOT / "services/training-session/tests"
sys.path.insert(0, str(TESTS_DIR))

from test_preview_eval_worker import (  # noqa: E402
    _TestClientResponse,
    _load_service_module,
    _load_worker_module,
)
from strict_test_support import seed_changed_supported_controls  # noqa: E402
from services.research.vectorbt.adapter.vectorbt_adapter import (  # noqa: E402
    PRIMARY_BACKEND,
    run_vectorbt_workflow,
)


worker = _load_worker_module()
service, fixture = _load_service_module(
    Path(tempfile.mkdtemp(prefix="l12-min-teach-real-"))
)
service.run_vectorbt_workflow = run_vectorbt_workflow

# Materialize bounded, oscillating OHLCV input so the real MA crossover has
# finite return, Sharpe, and drawdown metrics. The source and threshold files
# remain digest-bound and are confined to the ephemeral authority root.
dataset = json.loads(fixture.dataset_path.read_text(encoding="utf-8"))
positions: dict[str, int] = {}
records = []
for record in dataset["records"]:
    instrument = record["instrument"]
    position = positions.get(instrument, 0)
    positions[instrument] = position + 1
    base = 60_000.0 if instrument == "BTCUSD" else 3_000.0
    close = base * (1.0 + 0.06 * math.sin(position * math.pi / 4.0))
    records.append(
        {
            "instrument": instrument,
            "date": record["date"],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": record["volume"],
        }
    )
records.sort(key=lambda item: (item["date"], item["instrument"]))
canonical_bytes = b"".join(
    json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    + b"\n"
    for record in records
)
fixture.normalized_jsonl_path.write_bytes(canonical_bytes)
dataset["records"] = records
dataset["normalized_storage_refs"][0]["sha256"] = hashlib.sha256(
    canonical_bytes
).hexdigest()
dataset["normalized_storage_refs"][0]["row_count"] = len(records)
fixture.dataset_path.write_text(
    json.dumps(dataset, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
policy = json.loads(fixture.policy_path.read_text(encoding="utf-8"))
policy.update(
    {
        "min_sharpe_ratio": -100.0,
        "min_total_return": -1.0,
        "max_drawdown": 1.0,
    }
)
fixture.policy_path.write_text(
    json.dumps(policy, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

client = TestClient(service.app)
os.environ.update(
    {
        "TRAINING_SESSION_WORKER_TOKEN": "worker:training-service",
        "TRAINING_SESSION_TENANT_ID": "tenant-test",
    }
)
created = client.post(
    "/api/training/sessions",
    json={
        "persona_id": "persona-alpha",
        "objective": "Minimum functional Teaching command with real vectorbt evaluation",
        "actor_id": "operator-1",
    },
)
created.raise_for_status()
session_id = created.json()["session_id"]
seed_changed_supported_controls(
    service,
    session_id,
    baseline_short_window=5,
    short_window=3,
    baseline_long_window=20,
    long_window=8,
)
queued = client.post(
    f"/api/training/sessions/{session_id}/preview-jobs",
    json={
        "mode": "refresh",
        "requested_by": "operator-1",
        "terminalize_session": True,
    },
    headers={"Idempotency-Key": "l12-min-teach-real-vectorbt-001"},
)
queued.raise_for_status()
job_id = queued.json()["job_id"]


def service_urlopen(request, timeout):  # noqa: ANN001
    del timeout
    path = request.full_url.removeprefix("http://training-session-svc:8099")
    response = client.request(
        request.get_method(),
        path,
        content=request.data,
        headers=dict(request.header_items()),
    )
    response.raise_for_status()
    return _TestClientResponse(response)


worker.urllib.request.urlopen = service_urlopen
result = worker.run_tick(api_url="http://training-session-svc:8099", limit=1)
persisted = service.TrainingSessionStore(fixture.data_dir).get_session(session_id)
job = service.store.get_preview_job(job_id)
if not job or "preview" not in job:
    raise RuntimeError(
        json.dumps(
            {"worker_result": result, "persisted_job": job},
            sort_keys=True,
        )
    )
proof = job["preview"]["evaluation_proof"]

assert result["failed"] == 0, result
assert result["terminal_session_ids"] == [session_id], result
assert persisted is not None and persisted["status"] == "completed", persisted
assert proof["backend"]["name"] == PRIMARY_BACKEND, proof
assert proof["backend"]["run_id"].startswith("vbt-real-"), proof
assert proof["governance_gate_state"] == "passed", proof

print(
    json.dumps(
        {
            "task_id": "L12-MIN-TEACH-20260808",
            "trigger": {
                "session_id": session_id,
                "job_id": job_id,
                "terminalize_session": queued.json()["terminalize_session"],
            },
            "evaluation": {
                "backend": proof["backend"]["name"],
                "framework": proof["backend"]["framework"],
                "framework_version": proof["backend"]["framework_version"],
                "run_id": proof["backend"]["run_id"],
                "proof_ref": proof["proof_ref"],
                "governance_gate_state": proof["governance_gate_state"],
            },
            "worker": {
                "jobs_found": result["jobs_found"],
                "completed": result["completed"],
                "failed": result["failed"],
                "terminal_session_ids": result["terminal_session_ids"],
            },
            "persisted_readback": {
                "path": f"/api/training/sessions/{session_id}",
                "session_id": persisted["session_id"],
                "status": persisted["status"],
                "ended_at": persisted["ended_at"],
            },
        },
        indent=2,
        sort_keys=True,
    )
)
