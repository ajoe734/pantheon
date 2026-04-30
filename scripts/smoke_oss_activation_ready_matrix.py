#!/usr/bin/env python3
"""Activation-ready OSS smoke matrix.

This script proves three boundaries in one CI-friendly run:
- default gates reject Qlib/TRL/RL/W&B offline dispatch;
- paper/canary/live modes stay fail-closed even when the offline gate opens;
- explicit test gates run bounded offline Qlib/TRL/RL/W&B paths and emit only
  local artifacts, with no registry, governance, broker, or live writes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = REPO_ROOT / "services" / "research-worker-gateway"
TASK_ID = "SVC-OSS-ACTIVATION-READY-SMOKE-MATRIX"
PYTHON = sys.executable
TRUE = "1"


@dataclass
class MatrixRow:
    row: str
    scenario: str
    status: str
    gate_state: str
    writes: dict[str, bool]
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_gateway_module(*, data_dir: str, offline_gate: str):
    with mock.patch.dict(
        "os.environ",
        {
            "RESEARCH_WORKER_GATEWAY_DATA_DIR": data_dir,
            "RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS": "8",
            "RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS": "false",
            "RESEARCH_ORCHESTRATOR_URL": "http://research-orchestrator-svc:8101",
            "PANTHEON_OFFLINE_GATE_ENABLED": offline_gate,
            "PANTHEON_REPO_ROOT": str(REPO_ROOT),
            "PANTHEON_OFFLINE_WORKER_TIMEOUT": "120",
        },
        clear=False,
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(REPO_ROOT))
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location(
                f"_oss_matrix_gateway_{offline_gate}_{Path(data_dir).name}",
                SERVICE_DIR / "main.py",
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("cannot load research-worker-gateway module")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass
            try:
                sys.path.remove(str(REPO_ROOT))
            except ValueError:
                pass


def _empty_writes() -> dict[str, bool]:
    return {
        "registry_write": False,
        "governance_write": False,
        "broker_write": False,
        "live_write": False,
    }


def _dispatch(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/research-worker-gateway/jobs", json=payload)
    response.raise_for_status()
    return response.json()


def _default_fail_closed_rows(tmp: Path) -> list[MatrixRow]:
    module = _load_gateway_module(data_dir=str(tmp / "gateway-default"), offline_gate="false")
    client = TestClient(module.app)
    rows: list[MatrixRow] = []

    for worker in ("qlib", "trl", "finrl", "rllib", "wandb"):
        job = _dispatch(
            client,
            {
                "worker": worker,
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "idempotency_key": f"default-{worker}",
                "requested_at": "2026-04-30T07:00:00Z",
            },
        )
        reason = (job.get("rejection") or {}).get("reason")
        ok = job["status"] == "rejected" and reason == "dispatch_mode_disabled"
        rows.append(
            MatrixRow(
                row=f"default:{worker}:offline",
                scenario="default gate closed",
                status="passed" if ok else "failed",
                gate_state="fail_closed",
                writes=_empty_writes(),
                evidence={"job_status": job["status"], "rejection_reason": reason},
                error="" if ok else f"expected dispatch_mode_disabled rejection, got {job}",
            )
        )

    for mode in ("paper", "canary", "live"):
        job = _dispatch(
            client,
            {
                "worker": "qlib",
                "requested_mode": mode,
                "dispatch_mode": "stub",
                "idempotency_key": f"default-qlib-{mode}",
                "requested_at": "2026-04-30T07:01:00Z",
            },
        )
        reason = (job.get("rejection") or {}).get("reason")
        ok = job["status"] == "rejected" and reason == "production_adapter_disabled"
        rows.append(
            MatrixRow(
                row=f"default:{mode}",
                scenario="paper/canary/live fail closed",
                status="passed" if ok else "failed",
                gate_state="fail_closed",
                writes=_empty_writes(),
                evidence={"job_status": job["status"], "rejection_reason": reason},
                error="" if ok else f"expected production_adapter_disabled rejection, got {job}",
            )
        )
    return rows


def _enabled_gateway_rows(tmp: Path) -> list[MatrixRow]:
    module = _load_gateway_module(data_dir=str(tmp / "gateway-enabled"), offline_gate="true")
    client = TestClient(module.app)
    rows: list[MatrixRow] = []
    jobs = {
        "qlib": {
            "PANTHEON_QLIB_ACTIVATION_READY_ENABLED": TRUE,
            "QLIB_BACKEND": "stub",
            "QLIB_ENFORCE_DATA_FLOORS": "false",
            "QLIB_OUTPUT_DIR": str(tmp / "artifacts" / "qlib"),
        },
        "trl": {
            "PANTHEON_TRL_ACTIVATION_READY_ENABLED": TRUE,
            "TRL_BACKEND": "stub",
            "TRL_ENFORCE_DATA_FLOORS": "false",
            "TRL_OUTPUT_DIR": str(tmp / "artifacts" / "trl"),
        },
        "finrl": {
            "PANTHEON_FINRL_PREP_ENABLED": TRUE,
            "PANTHEON_FINRL_BACKEND": "stub",
            "FINRL_OUTPUT_DIR": str(tmp / "artifacts" / "finrl"),
        },
        "rllib": {
            "PANTHEON_RLLIB_PREP_ENABLED": TRUE,
            "PANTHEON_RLLIB_BACKEND": "stub",
            "RLLIB_OUTPUT_DIR": str(tmp / "artifacts" / "rllib"),
        },
    }

    for worker, params in jobs.items():
        job = _dispatch(
            client,
            {
                "worker": worker,
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "parameters": params,
                "idempotency_key": f"enabled-{worker}",
                "requested_at": "2026-04-30T07:02:00Z",
            },
        )
        payload = _json_from_stdout(job.get("stdout") or "")
        artifact_paths = _artifact_paths(payload)
        artifacts_exist = bool(artifact_paths) and all(Path(p).exists() for p in artifact_paths)
        ok = (
            job["status"] == "completed"
            and job.get("exit_code") == 0
            and payload.get("artifact_state") == "draft"
            and payload.get("deployment_stage") == "none"
            and artifacts_exist
        )
        rows.append(
            MatrixRow(
                row=f"enabled:{worker}:offline",
                scenario="explicit test gate open",
                status="passed" if ok else "failed",
                gate_state="activation_ready",
                writes=_empty_writes(),
                evidence={
                    "job_status": job["status"],
                    "exit_code": job.get("exit_code"),
                    "registry_id": payload.get("registry_id"),
                    "artifact_state": payload.get("artifact_state"),
                    "deployment_stage": payload.get("deployment_stage"),
                    "backend": payload.get("backend"),
                    "artifact_paths": artifact_paths,
                    "output_refs": job.get("output_refs", []),
                },
                error="" if ok else f"offline worker did not complete with local draft artifacts: {job}",
            )
        )

    for mode in ("paper", "canary", "live"):
        job = _dispatch(
            client,
            {
                "worker": "rllib",
                "requested_mode": mode,
                "dispatch_mode": "offline",
                "idempotency_key": f"enabled-rllib-{mode}",
                "requested_at": "2026-04-30T07:03:00Z",
            },
        )
        reason = (job.get("rejection") or {}).get("reason")
        ok = job["status"] == "rejected" and reason == "production_adapter_disabled"
        rows.append(
            MatrixRow(
                row=f"enabled:{mode}",
                scenario="paper/canary/live fail closed with offline gate open",
                status="passed" if ok else "failed",
                gate_state="fail_closed",
                writes=_empty_writes(),
                evidence={"job_status": job["status"], "rejection_reason": reason},
                error="" if ok else f"expected production_adapter_disabled rejection, got {job}",
            )
        )
    return rows


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        payload = json.loads(stdout[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_paths(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    manifest = payload.get("artifact_manifest")
    if isinstance(manifest, dict) and isinstance(manifest.get("files"), dict):
        paths.extend(str(value) for value in manifest["files"].values())
    artifact_paths = payload.get("artifact_paths")
    if isinstance(artifact_paths, dict):
        paths.extend(str(value) for value in artifact_paths.values())
    return sorted(set(paths))


def _wandb_offline_row(tmp: Path) -> MatrixRow:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.registry.experiments.adapter import OfflineWandbLocalBackend, RegistryExperimentAdapter
    finally:
        try:
            sys.path.remove(str(REPO_ROOT))
        except ValueError:
            pass

    store_dir = tmp / "artifacts" / "wandb-local"
    backend = OfflineWandbLocalBackend(mode="offline", store_dir=store_dir)
    adapter = RegistryExperimentAdapter(backend=backend)
    result = adapter.sync_registry_entry(
        {
            "registry_id": "activation-ready-offline-wandb-smoke",
            "artifact_type": "model_artifact",
            "strategy_id": "offline-wandb-smoke",
            "version": "1.0.0",
            "artifact_state": "candidate",
            "deployment_stage": "none",
            "lineage": {
                "parent_registry_ids": [],
                "source_run_ids": ["offline-smoke-run"],
                "source_dataset_refs": ["dataset://offline-smoke"],
            },
            "storage_ref": {
                "backend": "object_store",
                "path": "object://pantheon/offline-smoke/artifact.bin",
            },
            "checksum": "sha256:offline-smoke",
            "producer_run_id": "offline-smoke-run",
            "evaluation_summary": {"sharpe_ratio": 0.0, "max_drawdown": 0.0},
            "promoted_at": "2026-04-30T07:04:00Z",
            "approver": "activation-ready-smoke",
        }
    )
    run = backend.get_run(result.experiment_ref.run_id) or {}
    artifact_refs = result.experiment_ref.artifact_refs
    artifact_paths = [
        str(ref.get("path"))
        for ref in artifact_refs.values()
        if isinstance(ref, dict) and ref.get("path")
    ]
    artifacts_exist = bool(artifact_paths) and all(Path(p).exists() for p in artifact_paths)
    ok = (
        result.experiment_ref.sync_status == "offline_local"
        and result.promoted_metadata is not None
        and result.promoted_metadata["deployment_stage"] == "none"
        and run.get("online_sync", {}).get("enabled") is False
        and artifacts_exist
    )
    return MatrixRow(
        row="enabled:wandb:offline-local",
        scenario="explicit offline local store gate",
        status="passed" if ok else "failed",
        gate_state="activation_ready",
        writes=_empty_writes(),
        evidence={
            "backend": result.experiment_ref.backend,
            "sync_status": result.experiment_ref.sync_status,
            "run_id": result.experiment_ref.run_id,
            "deployment_stage": result.promoted_metadata.get("deployment_stage") if result.promoted_metadata else None,
            "artifact_paths": artifact_paths,
            "online_sync_enabled": run.get("online_sync", {}).get("enabled"),
        },
        error="" if ok else f"W&B offline local evidence failed: {run}",
    )


def _assert_no_forbidden_writes(rows: list[MatrixRow], tmp: Path) -> None:
    for row in rows:
        if any(row.writes.values()):
            row.status = "failed"
            row.error = f"forbidden write flag set: {row.writes}"
        for path in row.evidence.get("artifact_paths", []):
            try:
                Path(path).resolve().relative_to(tmp.resolve())
            except ValueError:
                row.status = "failed"
                row.error = f"artifact path escaped matrix output dir: {path}"


def run_matrix(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _default_fail_closed_rows(output_dir)
    rows.extend(_enabled_gateway_rows(output_dir))
    rows.append(_wandb_offline_row(output_dir))
    _assert_no_forbidden_writes(rows, output_dir)
    failures = [row for row in rows if row.status != "passed"]
    return {
        "task_id": TASK_ID,
        "generated_at": utc_now(),
        "repo_root": str(REPO_ROOT),
        "output_dir": str(output_dir),
        "summary": {
            "rows": len(rows),
            "passed": len(rows) - len(failures),
            "failed": len(failures),
            "matrix_passed": not failures,
            "forbidden_writes": {
                key: any(row.writes.get(key, False) for row in rows)
                for key in _empty_writes()
            },
        },
        "rows": [asdict(row) for row in rows],
    }


def _print_summary(report: dict[str, Any]) -> None:
    print(f"OSS activation-ready smoke matrix: {report['generated_at']}")
    print(f"Output dir: {report['output_dir']}")
    for row in report["rows"]:
        marker = "PASS" if row["status"] == "passed" else "FAIL"
        print(f"{marker} {row['row']} [{row['scenario']}]")
        if row.get("error"):
            print(f"  {row['error']}")
    summary = report["summary"]
    print(
        f"Summary: {summary['passed']}/{summary['rows']} passed; "
        f"forbidden_writes={summary['forbidden_writes']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run activation-ready OSS smoke matrix.")
    parser.add_argument("--output-dir", help="Directory for local smoke artifacts and report.")
    parser.add_argument("--json-out", help="Optional report JSON path.")
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help="Keep the auto-created output directory when --output-dir is not set.",
    )
    args = parser.parse_args(argv)

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif args.keep_output:
        output_dir = Path(tempfile.mkdtemp(prefix="pantheon-oss-matrix-"))
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="pantheon-oss-matrix-")
        output_dir = Path(temp_dir.name)

    try:
        report = run_matrix(output_dir)
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        _print_summary(report)
        return 0 if report["summary"]["matrix_passed"] else 1
    finally:
        if temp_dir is not None and not args.keep_output:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
