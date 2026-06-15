#!/usr/bin/env python3
"""Project real route/workflow/hook/job producers into BFF read stores.

The management console reads these surfaces from BFF store files:

  * /bff/route-policies -> PANTHEON_BFF_ROUTE_POLICY_STORE
  * /bff/workflows      -> PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE
  * /bff/hooks          -> PANTHEON_BFF_HOOK_REGISTRY_STORE
  * /bff/jobs           -> PANTHEON_BFF_JOB_STORE

This projector intentionally does not synthesize sample rows. Route policies
come from the persona service policy catalog, workflow/hooks come from the
control-plane cron workflow catalog, and jobs come from live service job APIs or
their service-owned JSON stores when mounted.

Usage inside the compose network:

    OUT_DIR=/data/bff python3 scripts/project_console_data_route_workflows_to_bff_surfaces.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

STORE_TARGETS = {
    "route_policies": ("PANTHEON_BFF_ROUTE_POLICY_STORE", "route_policies.json"),
    "workflow_templates": ("PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE", "workflow_templates.json"),
    "hook_registry": ("PANTHEON_BFF_HOOK_REGISTRY_STORE", "hook_registry.json"),
    "jobs": ("PANTHEON_BFF_JOB_STORE", "jobs.json"),
}

JOB_API_SOURCES = (
    {
        "source_id": "source_ingest",
        "base_envs": ("PANTHEON_SOURCE_INGEST_API_URL", "SOURCE_INGEST_URL"),
        "default_base": "http://source-ingest:8097",
        "path": "/api/source-ingest/jobs",
    },
    {
        "source_id": "policy_learning",
        "base_envs": ("PANTHEON_POLICY_LEARNING_API_URL", "POLICY_LEARNING_URL"),
        "default_base": "http://policy-learning-svc:8100",
        "path": "/api/policy-learning/jobs",
    },
    {
        "source_id": "research_orchestrator",
        "base_envs": ("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "RESEARCH_ORCHESTRATOR_URL", "RO_URL"),
        "default_base": "http://research-orchestrator-svc:8101",
        "path": "/api/research-orchestrator/runs",
    },
    {
        "source_id": "research_worker_gateway",
        "base_envs": ("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL", "RESEARCH_WORKER_GATEWAY_URL"),
        "default_base": "http://research-worker-gateway-svc:8103",
        "path": "/api/research-worker-gateway/jobs",
    },
)

JOB_STORE_SOURCES = (
    {
        "source_id": "policy_learning",
        "dir_env": "POLICY_LEARNING_DATA_DIR",
        "filename": "policy_learning_jobs.json",
    },
    {
        "source_id": "research_orchestrator",
        "dir_env": "RESEARCH_ORCHESTRATOR_DATA_DIR",
        "filename": "research_runs.json",
    },
    {
        "source_id": "research_worker_gateway",
        "dir_env": "RESEARCH_WORKER_GATEWAY_DATA_DIR",
        "filename": "worker_jobs.json",
    },
)


def _load_module(name: str, path: Path, extra_sys_path: Path) -> Any:
    added_paths: list[str] = []
    for candidate in (str(REPO_ROOT), str(extra_sys_path)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            added_paths.append(candidate)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for candidate in added_paths:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if path.suffix.lower() == ".jsonl":
        records = []
        for line in text.splitlines():
            raw = line.strip()
            if raw:
                records.append(json.loads(raw))
        return records
    return json.loads(text)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "data", "runs", "jobs", "tasks"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [item for item in payload.values() if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _keyed(records: list[dict[str, Any]], key_candidates: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in key_candidates:
            value = record.get(key)
            if value not in (None, ""):
                keyed[str(value)] = record
                break
    return keyed


def _store_path(dataset: str, out_dir: Path) -> Path:
    env_name, filename = STORE_TARGETS[dataset]
    explicit = os.getenv(env_name, "").strip()
    return Path(explicit) if explicit else out_dir / filename


def _write_store(path: Path, payload: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def project_route_policies() -> dict[str, dict[str, Any]]:
    persona_dir = REPO_ROOT / "services" / "control-plane" / "persona"
    module = _load_module(
        "_pantheon_persona_main_for_bff_projection",
        persona_dir / "main.py",
        persona_dir,
    )
    resolver = getattr(module, "_DEFAULT_POLICY_RESOLVER")
    policies = getattr(resolver, "_route_policies", {})
    records: list[dict[str, Any]] = []
    for policy_id, policy in sorted(policies.items()):
        records.append(
            {
                "id": str(policy_id),
                "policy_id": str(getattr(policy, "policy_id", policy_id)),
                "route_policy_id": str(getattr(policy, "policy_id", policy_id)),
                "allowed_workflows": list(getattr(policy, "allowed_workflows", []) or []),
                "allowed_skills": list(getattr(policy, "allowed_skills", []) or []),
                "status": "active",
                "producer": "persona-agent",
                "source": "services/control-plane/persona/main.py::_DEFAULT_POLICY_RESOLVER",
            }
        )
    return _keyed(records, ("policy_id", "route_policy_id", "id"))


def _cron_workflow_records() -> list[dict[str, Any]]:
    cron_dir = REPO_ROOT / "services" / "control-plane" / "cron"
    module = _load_module(
        "_pantheon_cron_workflows_for_bff_projection",
        cron_dir / "workflows.py",
        cron_dir,
    )
    catalog = getattr(module, "WORKFLOW_CATALOG", {})
    records: list[dict[str, Any]] = []
    for workflow_id, workflow in sorted(catalog.items()):
        record = workflow.to_dict()
        record.update(
            {
                "id": str(workflow_id),
                "template_id": str(workflow_id),
                "workflow_id": str(workflow_id),
                "name": str(workflow_id),
                "status": "registered",
                "producer": "control-plane-cron",
                "source": "services/control-plane/cron/workflows.py::WORKFLOW_CATALOG",
                "dispatch_safety": {
                    "dev_default": "dry_run",
                    "live_capital_side_effects": False,
                },
            }
        )
        records.append(record)
    return records


def project_workflow_templates() -> dict[str, dict[str, Any]]:
    return _keyed(_cron_workflow_records(), ("workflow_id", "template_id", "id"))


def project_hook_registry() -> dict[str, dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    for workflow in _cron_workflow_records():
        workflow_id = str(workflow["workflow_id"])
        hooks.append(
            {
                "id": f"cron.{workflow_id}",
                "hook_id": f"cron.{workflow_id}",
                "cron_id": f"cron.{workflow_id}",
                "workflow_id": workflow_id,
                "kind": "cron",
                "schedule": workflow.get("schedule"),
                "status": "registered",
                "execution_context": workflow.get("execution_context"),
                "producer": "control-plane-cron",
                "source": "services/control-plane/cron/workflows.py::WORKFLOW_CATALOG",
                "target_entrypoint": workflow.get("upstream_entrypoint"),
                "dispatch_safety": workflow.get("dispatch_safety"),
            }
        )
    return _keyed(hooks, ("hook_id", "cron_id", "id"))


def _base_url(env_names: tuple[str, ...], default_base: str) -> str:
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value.rstrip("/")
    return default_base.rstrip("/")


def _get_json(base_url: str, path: str, timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8").strip()
            return json.loads(text) if text else None
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


def _first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_job(source_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
    raw_id = _first_value(record, ("job_id", "run_id", "ingest_run_id", "task_id", "id"))
    if raw_id in (None, ""):
        return None
    producer_job_id = str(raw_id)
    job_id = str(record.get("job_id") or f"{source_id}:{producer_job_id}")
    created_at = _first_value(
        record,
        ("created_at", "submitted_at", "started_at", "requested_at", "queued_at", "completed_at"),
    )
    updated_at = _first_value(
        record,
        ("updated_at", "completed_at", "finished_at", "last_event_at", "created_at", "submitted_at"),
    )
    normalized = {
        "id": job_id,
        "job_id": job_id,
        "producer": source_id,
        "producer_job_id": producer_job_id,
        "job_type": str(record.get("job_type") or record.get("type") or source_id),
        "status": str(record.get("status") or record.get("state") or "unknown"),
        "created_at": created_at,
        "updated_at": updated_at,
        "summary": str(record.get("title") or record.get("name") or record.get("summary") or producer_job_id),
        "source_ref": f"{source_id}:{producer_job_id}",
        "source_payload": record,
    }
    if isinstance(record.get("logs"), list):
        normalized["logs"] = record["logs"]
    return normalized


def _collect_job_api_records(timeout: float) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in JOB_API_SOURCES:
        source_id = str(spec["source_id"])
        base = _base_url(tuple(spec["base_envs"]), str(spec["default_base"]))
        payload = _get_json(base, str(spec["path"]), timeout)
        for item in _items(payload):
            projected = _normalize_job(source_id, item)
            if projected is not None:
                records.append(projected)
    return records


def _collect_job_store_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in JOB_STORE_SOURCES:
        base = os.getenv(str(spec["dir_env"]), "").strip()
        if not base:
            continue
        path = Path(base) / str(spec["filename"])
        if not path.exists():
            continue
        for item in _items(_read_json(path)):
            projected = _normalize_job(str(spec["source_id"]), item)
            if projected is not None:
                projected.setdefault("source_store_path", str(path))
                records.append(projected)
    return records


def project_jobs(timeout: float) -> dict[str, dict[str, Any]]:
    records = _collect_job_api_records(timeout)
    records.extend(_collect_job_store_records())
    return _keyed(records, ("job_id", "id"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=os.getenv("OUT_DIR", "/data/bff"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("CONSOLE_PROJECTION_TIMEOUT", "10")))
    parser.add_argument("--require-jobs", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    datasets = {
        "route_policies": project_route_policies(),
        "workflow_templates": project_workflow_templates(),
        "hook_registry": project_hook_registry(),
        "jobs": project_jobs(timeout=max(args.timeout, 0.1)),
    }

    for dataset, records in datasets.items():
        path = _store_path(dataset, out_dir)
        _write_store(path, records)
        print(f"projected {len(records)} {dataset} -> {path}")

    if args.require_jobs and not datasets["jobs"]:
        print("ERROR: no real producer jobs were available to project", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
