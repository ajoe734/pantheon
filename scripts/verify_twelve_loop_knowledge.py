#!/usr/bin/env python3
"""L12-VERIFY-KNOW-001 — product drill for loops 1-3 (knowledge flows).

This is an ``EP3`` system-smoke drill: it starts the **real** source-ingest,
registry, and research-orchestrator services as independent OS processes on
real TCP ports, then drives the three **real** loop controllers
(``source-ingestion-controller``, ``strategy-distillation-controller``,
``alpha-replication-controller``) across those service boundaries.

What it checks, end to end and only through service APIs:

  1. a real Persona data requirement provisions a bounded connector and a
     schedule, and a scheduled pull commits one normalized ``SourceRecord``;
  2. that committed SourceRecord distills into exactly one **mutable**
     ``StrategySpec`` draft in the Registry;
  3. an **unapproved** spec never produces replication work;
  4. an **approved immutable** spec produces one authoritative
     ``ExperimentTask``/``ExperimentRun`` in the research authority, and is
     never rewritten by a later distillation tick;
  5. duplicate ticks, two-process concurrency, provider failure + DLQ replay,
     Registry outage + replay-once, research-authority outage, and process
     restart all converge on exactly one terminal artifact per identity;
  6. the loop-control controller record each controller writes conforms to the
     shared contract and projects to the BFF read model with the *same*
     terminal truth the authorities themselves report.

Nothing here is a seeded fixture standing in for a loop result: every
SourceRecord, StrategySpec, ExperimentTask, and ExperimentRun in the evidence
is produced by the real controller against the real service. The only bounded
input is the connector's allowlisted ``static_records`` payload, which is what
keeps the drill from performing an uncontrolled external crawl.

Usage::

    python3 scripts/verify_twelve_loop_knowledge.py \
        --run-dir /tmp/l12-verify-know \
        --evidence-out docs/deployment/evidence/twelve-loop-gap/L12-VERIFY-KNOW-001/drill-run.json

Exit code is ``0`` only when every check passes. A failing or blocked check is
a finding about the product, not a harness error: the run record names the
defect, its root cause, and the surface that owns it.

The drill needs the service dependencies on ``PYTHONPATH`` (``fastapi``,
``uvicorn``, ``jsonschema``, ``asyncpg``); it starts no container and touches
no shared deployment.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import multiprocessing as mp
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_REL_PATH = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "twelve_loop_knowledge_drill.v1"
# The durable store folds these fields into the payload meta column; the drill's
# capture mirrors that shape so lease fencing behaves exactly as in production.
_CONTROLLER_META_KEY = "_loop_controller_record"
TASK_ID = "L12-VERIFY-KNOW-001"
LOOP_IDS = ("source_ingestion", "strategy_distillation", "alpha_replication")

CONNECTOR_ID = "conn-l12-know-bounded-paper"
PERSONA_ID = "persona-l12-know-001"
TENANT_ID = "default"
ENVIRONMENT = "drill"


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

class DrillError(RuntimeError):
    """A check failed; the message is the evidence."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require(condition: Any, message: str) -> None:
    if not condition:
        raise DrillError(message)


def http_json(
    method: str,
    url: str,
    payload: Any = None,
    *,
    token: str | None = None,
    timeout: float = 20.0,
) -> tuple[int, Any]:
    """Issue one real HTTP request and return ``(status, decoded_body)``."""

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            decoded = json.loads(body) if body else None
        except json.JSONDecodeError:
            decoded = {"raw": body}
        return exc.code, decoded
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # Status 0 means "no HTTP conversation happened": the port refused the
        # connection or the transport failed. Outage checks depend on this
        # being observable rather than fatal.
        return 0, {"transport_error": str(exc)}


def expect_http(
    method: str,
    url: str,
    payload: Any = None,
    *,
    token: str | None = None,
    expected: Sequence[int] = (200, 201),
    timeout: float = 20.0,
) -> Any:
    status, body = http_json(method, url, payload, token=token, timeout=timeout)
    require(
        status in expected,
        f"{method} {url} returned {status} (expected {list(expected)}): {json.dumps(body)[:600]}",
    )
    return body


# ---------------------------------------------------------------------------
# Real service processes
# ---------------------------------------------------------------------------

@dataclass
class ServiceProcess:
    """One real service, in its own OS process, on its own TCP port."""

    name: str
    app_module: str
    port: int
    env: dict[str, str]
    extra_pythonpath: tuple[str, ...] = ()
    log_path: Path | None = None
    process: subprocess.Popen | None = None
    start_count: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, *, wait_seconds: float = 60.0) -> None:
        require(self.process is None, f"{self.name} is already running")
        env = dict(os.environ)
        pythonpath = [str(REPO_ROOT), *self.extra_pythonpath]
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        env["PYTHONUNBUFFERED"] = "1"
        env.update(self.env)
        code = (
            "import uvicorn;"
            f"from {self.app_module} import app;"
            f"uvicorn.run(app, host='127.0.0.1', port={self.port}, log_level='warning')"
        )
        handle = None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.log_path, "a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=handle or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        self.start_count += 1
        self._wait_healthy(wait_seconds)

    def _wait_healthy(self, wait_seconds: float) -> None:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                tail = ""
                if self.log_path and self.log_path.exists():
                    tail = self.log_path.read_text(encoding="utf-8")[-2000:]
                raise DrillError(f"{self.name} exited during startup:\n{tail}")
            status, _ = http_json("GET", f"{self.base_url}/health", timeout=3)
            if status == 200:
                return
            time.sleep(0.2)
        raise DrillError(f"{self.name} did not become healthy on {self.base_url}")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None
        # A stopped port must actually refuse connections before the drill can
        # claim it proved an outage.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", self.port)) != 0:
                    return
            time.sleep(0.1)
        raise DrillError(f"{self.name} port {self.port} is still accepting connections after stop")

    def restart(self) -> None:
        self.stop()
        self.start()


# ---------------------------------------------------------------------------
# Loop-control capture: the real writer SDK, without a Postgres dependency
# ---------------------------------------------------------------------------

def _loop_control_module() -> Any:
    return importlib.import_module("services.loop-control")


class CapturingControllerStore:
    """Stand in for the Postgres row store while keeping real validation.

    ``LoopControllerWriter`` builds the controller record and hands it to
    ``LoopControllerStore.upsert_record``. This capture keeps the real record
    construction and the real ``validate_record`` (JSON Schema plus
    ``assert_controller_record_conforms``); only the network write is replaced,
    so the drill never mutates shared loop-control state.
    """

    REQUIRED_PATCH_FIELDS = (
        "loop_id",
        "tenant_id",
        "environment",
        "controller_id",
        "controller_name",
        "deployment_sha",
        "truth_level",
    )

    def __init__(self) -> None:
        module = _loop_control_module()
        self._store = module.LoopControllerStore("postgresql://drill/none")
        self.records: list[dict[str, Any]] = []
        self.merged: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def upsert_record(self, record: dict[str, Any]) -> dict[str, Any]:
        for field_name in self.REQUIRED_PATCH_FIELDS:
            if record.get(field_name) is None:
                raise ValueError(f"controller patch requires {field_name}")
        key = (str(record["loop_id"]), str(record["tenant_id"]), str(record["environment"]))
        merged = self._store._merge_record(  # noqa: SLF001 - real merge, no Postgres
            self.rows.get(key),
            record,
            now=datetime.now(timezone.utc),
        )
        self._store.validate_record(merged)
        # Persist in the same row shape the Postgres store writes, so the next
        # merge normalizes the fenced lease exactly the way production does.
        row = dict(merged)
        persisted_payload = dict(merged["payload"])
        persisted_payload[_CONTROLLER_META_KEY] = {
            "lease_token": merged["lease_token"],
            "desired_state": merged["desired_state"],
            "downstream_actual_state": merged["downstream_actual_state"],
        }
        row["payload"] = persisted_payload
        self.rows[key] = row
        self.merged[key] = merged
        self.records.append(dict(record))
        return merged

    def record_for(self, loop_id: str) -> dict[str, Any]:
        key = (loop_id, TENANT_ID, ENVIRONMENT)
        record = self.merged.get(key)
        require(record is not None, f"no controller record was written for loop {loop_id}")
        return dict(record)


DRILL_LEASE_SECONDS = 300


def make_loop_writer(
    *,
    controller_id: str,
    controller_name: str,
    store: CapturingControllerStore,
    lease_token: str | None = None,
) -> Any:
    """Build the real ``LoopControllerWriter`` bound to the capturing store."""

    module = _loop_control_module()
    writer = module.LoopControllerWriter(
        "postgresql://drill/none",
        tenant_id=TENANT_ID,
        environment=ENVIRONMENT,
        controller_id=controller_id,
        controller_name=controller_name,
        deployment_sha=os.getenv("PANTHEON_DEPLOYMENT_SHA") or _git_sha(),
        lease_token=lease_token,
        lease_duration_seconds=DRILL_LEASE_SECONDS,
    )
    writer.store = store
    return writer


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 - lineage falls back to an explicit unknown
        return "unknown"


def _git_worktree_clean() -> bool | None:
    """True when the repository has no tracked modifications at run time.

    A clean worktree is what lets ``git_sha`` identify the exact drill source;
    otherwise ``script_sha256`` is the only byte-level binding.
    """

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return not result.stdout.strip()
    except Exception:  # noqa: BLE001 - lineage falls back to an explicit unknown
        return None


def _script_sha256() -> str:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001 - lineage falls back to an explicit unknown
        return "unknown"


# ---------------------------------------------------------------------------
# Check bookkeeping
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    check_id: str
    title: str
    acceptance: tuple[str, ...]
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    evidence: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "check_id": self.check_id,
            "title": self.title,
            "acceptance": list(self.acceptance),
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "evidence": self.evidence,
        }
        if self.failure is not None:
            payload["failure"] = self.failure
        return payload


class Recorder:
    def __init__(self, *, verbose: bool = True) -> None:
        self.results: list[CheckResult] = []
        self.verbose = verbose

    @contextlib.contextmanager
    def check(self, check_id: str, title: str, acceptance: Sequence[str]) -> Iterator[dict[str, Any]]:
        evidence: dict[str, Any] = {}
        started = time.monotonic()
        started_at = utc_now()
        if self.verbose:
            print(f"[ .. ] {check_id} {title}", flush=True)
        try:
            yield evidence
        except Exception as exc:  # noqa: BLE001 - the failure is the evidence
            failure = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, DrillError):
                failure += "\n" + traceback.format_exc(limit=6)
            result = CheckResult(
                check_id=check_id,
                title=title,
                acceptance=tuple(acceptance),
                status="failed",
                started_at=started_at,
                finished_at=utc_now(),
                duration_seconds=time.monotonic() - started,
                evidence=evidence,
                failure=failure,
            )
            self.results.append(result)
            if self.verbose:
                print(f"[FAIL] {check_id} {title}\n       {failure.splitlines()[0]}", flush=True)
            return
        result = CheckResult(
            check_id=check_id,
            title=title,
            acceptance=tuple(acceptance),
            status="passed",
            started_at=started_at,
            finished_at=utc_now(),
            duration_seconds=time.monotonic() - started,
            evidence=evidence,
        )
        self.results.append(result)
        if self.verbose:
            print(f"[ OK ] {check_id} {title}", flush=True)

    def blocked(
        self,
        check_id: str,
        title: str,
        acceptance: Sequence[str],
        *,
        reason: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a check that could not run because a found defect blocks it."""

        now = utc_now()
        self.results.append(
            CheckResult(
                check_id=check_id,
                title=title,
                acceptance=tuple(acceptance),
                status="blocked",
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
                evidence=dict(evidence or {}),
                failure=reason,
            )
        )
        if self.verbose:
            print(f"[BLKD] {check_id} {title}\n       {reason}", flush=True)

    @property
    def failed(self) -> list[CheckResult]:
        return [result for result in self.results if result.status != "passed"]

    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self.results),
            "passed": len([r for r in self.results if r.status == "passed"]),
            "failed": len([r for r in self.results if r.status == "failed"]),
            "blocked": len([r for r in self.results if r.status == "blocked"]),
        }



def _config_payload(config: Any) -> dict[str, Any]:
    """Serialise a distillation controller config for a spawned child process."""

    return {
        "database_url": config.database_url,
        "registry_url": config.registry_url,
        "interval_seconds": config.interval_seconds,
        "max_ticks": config.max_ticks,
        "state_path": str(config.state_path),
        "alive_path": str(config.alive_path) if config.alive_path else None,
        "job_queue_path": str(config.job_queue_path),
        "seed_store_path": str(config.seed_store_path),
        "evidence_store_path": str(config.evidence_store_path),
        "source_dirs": [str(path) for path in config.source_dirs],
        "lease_seconds": config.lease_seconds,
        "retry_base_seconds": config.retry_base_seconds,
        "max_attempts": config.max_attempts,
    }


def _concurrent_distill_child(repo_root: str, payload: dict, worker_id: str, out: Any, barrier: Any) -> None:
    """Child entry point: contend for the shared ledger from a separate process."""

    import sys as _sys

    if repo_root not in _sys.path:
        _sys.path.insert(0, repo_root)
    try:
        from services.source_ingestion.distillation_controller import (
            DistillationControllerConfig,
            _make_registry_sync,
        )
        from services.source_ingestion.distillation_worker import make_distillation_worker

        config = DistillationControllerConfig(
            database_url=payload["database_url"],
            registry_url=payload["registry_url"],
            interval_seconds=payload["interval_seconds"],
            max_ticks=payload["max_ticks"],
            state_path=Path(payload["state_path"]),
            alive_path=Path(payload["alive_path"]) if payload["alive_path"] else None,
            job_queue_path=Path(payload["job_queue_path"]),
            seed_store_path=Path(payload["seed_store_path"]),
            evidence_store_path=Path(payload["evidence_store_path"]),
            source_dirs=[Path(item) for item in payload["source_dirs"]],
            lease_seconds=payload["lease_seconds"],
            retry_base_seconds=payload["retry_base_seconds"],
            max_attempts=payload["max_attempts"],
        )
        worker = make_distillation_worker(
            queue_path=config.job_queue_path,
            seed_store_path=config.seed_store_path,
            created_by=worker_id,
            worker_id=worker_id,
            lease_seconds=config.lease_seconds,
            retry_base_seconds=config.retry_base_seconds,
            max_attempts=config.max_attempts,
            registry_sync=_make_registry_sync(config),
        )
        totals = {"processed": 0, "created": 0, "failed": 0, "skipped": 0}
        if barrier is not None:
            barrier.wait(timeout=120)
        empty_passes = 0
        while empty_passes < 15:
            # One job per pass keeps both processes writing the shared ledger at
            # the same time instead of letting the first starter drain it.
            result = worker.run_pending(None, limit=1)
            if result.processed == 0:
                empty_passes += 1
                time.sleep(0.05)
                continue
            empty_passes = 0
            totals["processed"] += result.processed
            totals["created"] += result.created
            totals["failed"] += result.failed
            totals["skipped"] += result.skipped
        out.put({"worker_id": worker_id, "totals": totals, "error": None})
    except Exception as exc:  # noqa: BLE001 - the child failure is the evidence
        out.put({"worker_id": worker_id, "totals": {}, "error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# Drill
# ---------------------------------------------------------------------------

STRATEGY_SEED = {
    "hypothesis": "Bounded TW equity momentum evidence carries a research-only tradable signal",
    "asset_class": ["equity"],
    "market_scope": ["Taiwan"],
    "holding_period": "5 days",
    "required_data": ["OHLCV"],
    "backend_hint": "qlib",
    "feature_hints": ["momentum_20d"],
    "label_hints": ["forward_return_5d"],
    "risk_notes": ["research_only", "no_live_capital"],
}


DATASET = "tw_equity_momentum_papers"


def bounded_source_record(
    source_id: str,
    *,
    title: str,
    correlation_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """One bounded, allowlisted provider record. No external crawl.

    The provenance keys are the ones the source-ingestion controller's terminal
    readback guard requires; a record without them fails the loop closed, which
    is the behaviour this drill depends on rather than works around.
    """

    return {
        "source_id": source_id,
        "title": title,
        "content_ref": f"https://doi.org/10.1000/{source_id}",
        "source_type": "paper",
        "status": "normalized",
        "trace_id": f"trace-{correlation_id[:12]}-{source_id[-12:]}",
        "metadata": {
            "trust_score": 0.82,
            "access_scope": ["research"],
            "license_scope": "internal",
            "keywords": ["momentum", "equity", "taiwan"],
            "provider": "pantheon-l12-bounded-allowlist",
            "dataset": DATASET,
            "market": "TW",
            "venue": "research-corpus",
            "event_time": observed_at,
            "available_time": observed_at,
            "api_endpoint": f"bounded-allowlist://{CONNECTOR_ID}/{DATASET}",
            "schema_hash": sha256_json({"schema": "paper_source_record.v1", "dataset": DATASET}),
            "strategy_seed": dict(STRATEGY_SEED),
        },
    }


class KnowledgeLoopDrill:
    """Owns the run directory, the service processes, and every check."""

    def __init__(self, *, run_dir: Path, correlation_id: str, recorder: Recorder) -> None:
        self.run_dir = run_dir
        self.correlation_id = correlation_id
        self.recorder = recorder
        self.controller_token = "l12-know-" + uuid.uuid4().hex + uuid.uuid4().hex
        # The bounded provider payload must be byte-stable across re-registration,
        # otherwise every reconfiguration would look like revised source content
        # and legitimately produce a new committed version.
        self.observed_at = utc_now()

        self.source_ingest_dir = run_dir / "source-ingest"
        self.registry_dir = run_dir / "registry"
        self.research_dir = run_dir / "research-orchestrator"
        self.distillation_dir = run_dir / "distillation"
        self.alpha_dir = run_dir / "alpha-replication"
        self.logs_dir = run_dir / "logs"
        for directory in (
            self.source_ingest_dir,
            self.registry_dir,
            self.research_dir,
            self.distillation_dir,
            self.alpha_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.desired_state_path = run_dir / "desired_state.json"
        self.source_controller_state_path = self.source_ingest_dir / "controller_state.json"
        self.distillation_state_path = self.distillation_dir / "controller_state.json"
        self.alpha_state_path = self.alpha_dir / "controller_state.json"
        self.job_queue_path = self.distillation_dir / "job_queue.sqlite3"
        self.seed_store_path = self.distillation_dir / "seeds.jsonl"
        self.evidence_store_path = self.source_ingest_dir / "source_evidence.jsonl"

        self.loop_store = CapturingControllerStore()
        self._writers: dict[str, Any] = {}
        self.services: dict[str, ServiceProcess] = {}
        self.identity: dict[str, Any] = {}
        self.observations: dict[str, Any] = {}
        self.gaps: list[dict[str, Any]] = []

    def loop_writer(self, *, controller_name: str, controller_id: str, renew: bool = False) -> Any:
        """One writer per controller process, exactly like a deployed controller.

        ``renew=True`` models a controller restart: a new runtime identity
        resumes the loop while inheriting its own fencing token.
        """

        existing = self._writers.get(controller_name)
        if existing is not None and not renew and existing.controller_id == controller_id:
            return existing
        writer = make_loop_writer(
            controller_id=controller_id,
            controller_name=controller_name,
            store=self.loop_store,
            # A restarting controller resumes its own loop ownership through the
            # documented PANTHEON_CONTROLLER_LEASE_TOKEN path rather than
            # stealing an active fence from a foreign writer.
            lease_token=existing.lease_token if existing is not None else None,
        )
        self._writers[controller_name] = writer
        return writer

    # -- service lifecycle -------------------------------------------------

    def build_services(self) -> None:
        self.services["registry"] = ServiceProcess(
            name="registry",
            app_module="services.registry.service",
            port=free_port(),
            env={
                "PANTHEON_ENV": "dev",
                "REGISTRY_DATA_DIR": str(self.registry_dir),
            },
            log_path=self.logs_dir / "registry.log",
        )
        self.services["source_ingest"] = ServiceProcess(
            name="source-ingest",
            app_module="services.source_ingestion.main",
            port=free_port(),
            env={
                "PANTHEON_ENV": "dev",
                "SOURCE_INGEST_DATA_DIR": str(self.source_ingest_dir),
                "SOURCE_INGEST_CONTROLLER_TOKEN": self.controller_token,
                "SOURCE_INGEST_CONTROLLER_STATE_PATH": str(self.source_controller_state_path),
                "SOURCE_INGEST_EVIDENCE_STORE_PATH": str(self.evidence_store_path),
                "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": "2",
                "SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS": "2",
                "SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS": "0",
            },
            log_path=self.logs_dir / "source-ingest.log",
        )
        self.services["research"] = ServiceProcess(
            name="research-orchestrator",
            app_module="services.research.main",
            port=free_port(),
            env={
                "PANTHEON_ENV": "dev",
                "RESEARCH_ORCHESTRATOR_DATA_DIR": str(self.research_dir),
                "RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS": "false",
            },
            extra_pythonpath=(str(REPO_ROOT / "services" / "research"),),
            log_path=self.logs_dir / "research-orchestrator.log",
        )

    def start_services(self) -> None:
        for service in self.services.values():
            service.start()

    def stop_services(self) -> None:
        for service in self.services.values():
            with contextlib.suppress(Exception):
                service.stop()

    @property
    def source_ingest_url(self) -> str:
        return self.services["source_ingest"].base_url

    @property
    def registry_url(self) -> str:
        return self.services["registry"].base_url

    @property
    def research_url(self) -> str:
        return self.services["research"].base_url

    # -- controller drivers ------------------------------------------------

    def source_controller_config(self, *, exclusive: Sequence[str] = ()) -> Any:
        from services.source_ingestion.controller_worker import ControllerConfig

        return ControllerConfig(
            api_url=self.source_ingest_url,
            database_url="postgresql://drill/none",
            interval_seconds=60,
            max_concurrency=2,
            max_ticks=1,
            state_path=self.source_controller_state_path,
            alive_path=self.source_ingest_dir / "controller_alive",
            timeout_seconds=30.0,
            lease_seconds=DRILL_LEASE_SECONDS,
            truth_level="reconciled_live_proof",
            controller_token=self.controller_token,
            mode="reconcile_and_pull",
            force_connector_ids=tuple(exclusive),
            exclusive_connector_ids=tuple(exclusive),
        )

    def run_source_controller_tick(self, *, exclusive: Sequence[str] = (CONNECTOR_ID,)) -> dict[str, Any]:
        from services.source_ingestion import controller_worker
        from services.source_ingestion.controller_state import ControllerStateStore

        os.environ["SOURCE_INGEST_DESIRED_STATE_PATH"] = str(self.desired_state_path)
        # A controller whose deployment identity is unresolved fails its own
        # terminal readback guard, so the drill binds the real repo identity.
        os.environ.setdefault("GIT_SHA", _git_sha())
        os.environ.setdefault("SOURCE_INGEST_DEPLOYMENT_ID", f"drill:{self.correlation_id[:12]}")
        os.environ["PANTHEON_CONTROLLER_NAME"] = "source-ingestion-controller"
        config = self.source_controller_config(exclusive=exclusive)
        store = ControllerStateStore(config.state_path)
        state = store.load()
        if state is None:
            state = controller_worker._new_state()
        else:
            state = controller_worker.refresh_runtime_identity(state)
        state.environment = ENVIRONMENT
        state.tenant_id = TENANT_ID
        state.controller_name = "source-ingestion-controller"
        store.save(state)
        writer = self.loop_writer(
            controller_name=state.controller_name,
            controller_id=state.controller_id,
        )
        result = controller_worker.run_controller_tick(
            config=config, state=state, store=store, writer=writer
        )
        return {"result": result, "controller_id": state.controller_id}

    def distillation_config(
        self,
        *,
        registry_url: str | None = None,
        retry_base_seconds: int = 0,
        job_queue_path: Path | None = None,
        seed_store_path: Path | None = None,
        state_path: Path | None = None,
    ) -> Any:
        from services.source_ingestion.distillation_controller import DistillationControllerConfig

        return DistillationControllerConfig(
            database_url="postgresql://drill/none",
            registry_url=registry_url or self.registry_url,
            interval_seconds=60,
            max_ticks=1,
            state_path=state_path or self.distillation_state_path,
            alive_path=self.distillation_dir / "controller_alive",
            job_queue_path=job_queue_path or self.job_queue_path,
            seed_store_path=seed_store_path or self.seed_store_path,
            evidence_store_path=self.evidence_store_path,
            source_dirs=[self.distillation_dir / "notes"],
            lease_seconds=30,
            retry_base_seconds=retry_base_seconds,
            max_attempts=3,
        )

    def run_distillation_tick(
        self,
        *,
        registry_url: str | None = None,
        retry_base_seconds: int = 0,
        fresh_identity: bool = False,
        job_queue_path: Path | None = None,
        seed_store_path: Path | None = None,
        state_path: Path | None = None,
    ) -> dict[str, Any]:
        from services.source_ingestion import distillation_controller
        from services.source_ingestion.controller_state import ControllerStateStore

        config = self.distillation_config(
            registry_url=registry_url,
            retry_base_seconds=retry_base_seconds,
            job_queue_path=job_queue_path,
            seed_store_path=seed_store_path,
            state_path=state_path,
        )
        store = ControllerStateStore(config.state_path)
        state = store.load()
        if state is None:
            state = distillation_controller._new_state()
            state.controller_name = "strategy-distillation-controller"
        elif fresh_identity:
            state = distillation_controller.refresh_runtime_identity(state)
        state.environment = ENVIRONMENT
        state.tenant_id = TENANT_ID
        store.save(state)
        writer = self.loop_writer(
            controller_name=state.controller_name,
            controller_id=state.controller_id,
            renew=fresh_identity,
        )
        os.environ["PANTHEON_LOOP_ID"] = "strategy_distillation"
        error: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            result = distillation_controller.run_controller_tick(
                config=config, state=state, store=store, writer=writer
            )
        except Exception as exc:  # noqa: BLE001 - failures are an assertion subject
            error = exc
        finally:
            os.environ.pop("PANTHEON_LOOP_ID", None)
        return {"result": result, "error": error, "controller_id": state.controller_id}

    def alpha_config(self, *, registry_url: str | None = None, research_url: str | None = None) -> Any:
        from services.research.alpha_replication.replication_controller import (
            ReplicationControllerConfig,
        )
        from services.research.experiment_orchestrator.authority import (
            ResearchAuthorityHttpClient,
        )

        return ReplicationControllerConfig(
            database_url="",
            registry_url=registry_url or self.registry_url,
            interval_seconds=60,
            max_ticks=1,
            state_path=self.alpha_state_path,
            data_dir=self.alpha_dir,
            seed_store_path=self.seed_store_path,
            authority=ResearchAuthorityHttpClient(
                research_url or self.research_url,
                actor_id="alpha-replication-worker",
            ),
        )

    def run_alpha_tick(
        self,
        *,
        registry_url: str | None = None,
        research_url: str | None = None,
        fresh_identity: bool = False,
    ) -> dict[str, Any]:
        from services.research.alpha_replication import replication_controller
        from services.research.alpha_replication.controller_state import (
            ControllerState,
            ControllerStateStore,
        )

        config = self.alpha_config(registry_url=registry_url, research_url=research_url)
        store = ControllerStateStore(config.state_path)
        state = store.load()
        if state is None or fresh_identity:
            state = ControllerState(
                controller_id=f"alpha-replication-controller-{uuid.uuid4().hex[:8]}",
                controller_name="alpha-replication-controller",
                environment=ENVIRONMENT,
                tenant_id=TENANT_ID,
                deployment={"git_sha": _git_sha()},
            )
        state.environment = ENVIRONMENT
        state.tenant_id = TENANT_ID
        store.save(state)
        writer = self.loop_writer(
            controller_name=state.controller_name,
            controller_id=state.controller_id,
            renew=fresh_identity,
        )
        os.environ["PANTHEON_LOOP_ID"] = "alpha_replication"
        error: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            result = replication_controller.run_controller_tick(
                config=config, state=state, store=store, writer=writer
            )
        except Exception as exc:  # noqa: BLE001 - failures are an assertion subject
            error = exc
        finally:
            os.environ.pop("PANTHEON_LOOP_ID", None)
        return {
            "result": result,
            "error": error,
            "controller_id": state.controller_id,
            "state": state,
        }

    # -- authority readbacks ----------------------------------------------

    def registry_entry(self, registry_id: str) -> dict[str, Any] | None:
        status, body = http_json(
            "GET", f"{self.registry_url}/api/registry/strategy-specs/{registry_id}"
        )
        if status == 404:
            return None
        require(status == 200, f"registry readback for {registry_id} returned {status}: {body}")
        payload = body.get("entry") if isinstance(body, dict) else None
        return dict(payload if isinstance(payload, dict) else body)

    def source_records(self) -> list[dict[str, Any]]:
        body = expect_http("GET", f"{self.source_ingest_url}/api/source-ingest/source-records")
        records = body.get("source_records") if isinstance(body, dict) else None
        if records is None and isinstance(body, dict):
            records = body.get("records")
        return list(records or [])

    def controller_readback(self) -> dict[str, Any]:
        return expect_http("GET", f"{self.source_ingest_url}/api/source-ingest/controller/readback")

    def authority_runs(self) -> list[dict[str, Any]]:
        body = expect_http("GET", f"{self.research_url}/api/research-orchestrator/runs")
        return [item for item in (body or []) if isinstance(item, dict)]

    def replication_runs(self) -> list[dict[str, Any]]:
        """Authoritative ExperimentRun records produced by alpha replication."""

        runs = []
        for record in self.authority_runs():
            parameters = record.get("parameters")
            if isinstance(parameters, dict) and parameters.get("record_type") == "ExperimentRun":
                runs.append(record)
        return runs

    # -- setup -------------------------------------------------------------

    def persona_desired_state(self, source_ids: Sequence[str]) -> dict[str, Any]:
        return {
            "authority": f"drill://{TASK_ID}/persona-data-requirement",
            "personas": [
                {
                    "persona_id": PERSONA_ID,
                    "name": "L12 knowledge-loop research persona",
                    "lifecycle_state": "research_only",
                    "mandate": (
                        "Prove Persona requirement -> SourceRecord -> StrategySpec draft "
                        "-> approved spec -> authoritative ExperimentRun."
                    ),
                    "required_data_sources": [
                        {
                            "dataset": DATASET,
                            "market": "TW",
                            "cadence": "daily",
                            "source_class": "live_pull",
                            "connector_candidates": [CONNECTOR_ID],
                            "policy_gates": ["public-source-only", "no_live_capital"],
                        }
                    ],
                }
            ],
            "drill": {"correlation_id": self.correlation_id, "source_ids": list(source_ids)},
        }

    def configure_bounded_connector(
        self,
        *,
        source_ids: Sequence[str],
        fail_until_attempt: int = 0,
    ) -> dict[str, Any]:
        """Register the one allowlisted provider this drill is permitted to pull."""

        payload = {
            "connector": {
                "connector_id": CONNECTOR_ID,
                "source_type": "paper",
                "provider": "pantheon-l12-bounded-allowlist",
                "license_scope": "internal",
                "auth_type": "none",
                "metadata": {
                    "approval_status": "approved",
                    "bounded_provider": True,
                    "drill_correlation_id": self.correlation_id,
                    "external_crawl_allowed": False,
                },
            },
            "fetch": {
                "mode": "static_records",
                "records": [
                    bounded_source_record(
                        source_id,
                        title=f"TW equity momentum replication evidence {index + 1}",
                        correlation_id=self.correlation_id,
                        observed_at=self.observed_at,
                    )
                    for index, source_id in enumerate(source_ids)
                ],
                "allow_empty": False,
                "fail_until_attempt": fail_until_attempt,
                "failure_reason": "bounded provider outage injected by the L12 knowledge drill",
            },
        }
        return expect_http(
            "POST",
            f"{self.source_ingest_url}/api/source-ingest/connectors",
            payload,
            token=self.controller_token,
        )

    def write_desired_state(self, source_ids: Sequence[str]) -> None:
        self.desired_state_path.write_text(
            json.dumps(self.persona_desired_state(source_ids), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def registry_id_for(self, source_id: str) -> str:
        from services.source_ingestion.distillation_worker import source_version_digest

        source = self.committed_source_record(source_id)
        digest = source_version_digest(source).removeprefix("sha256:")
        return f"reg-strategy-spec-{source_id}-{digest[:12]}"

    def committed_source_record(self, source_id: str) -> Any:
        from services.source_ingestion.pg_store import build_source_evidence_repository

        repository = build_source_evidence_repository(self.evidence_store_path)
        for record in repository.list_source_records():
            if record.source_id == source_id:
                return record
        raise DrillError(f"no committed SourceRecord for {source_id}")

    def approve_strategy_spec(self, registry_id: str, *, approver: str) -> dict[str, Any]:
        advance_url = f"{self.registry_url}/api/registry/strategy-specs/{registry_id}/advance"
        expect_http("POST", advance_url, {"target_state": "candidate"})
        return expect_http(
            "POST",
            advance_url,
            {
                "target_state": "approved",
                "approver": approver,
                "approval_decision_id": f"decision-{self.correlation_id}-{registry_id[-12:]}",
            },
        )

    # -- checks ------------------------------------------------------------

    def check_persona_requirement_binding(self, source_id: str) -> None:
        with self.recorder.check(
            "C01",
            "Real Persona data requirement reaches terminal reconciled source truth",
            ["Real Persona requirement produces SourceRecord and one mutable StrategySpec draft"],
        ) as evidence:
            from services.source_ingestion.controller_worker import ControllerTickError

            self.configure_bounded_connector(source_ids=[source_id])
            self.write_desired_state([source_id])
            tick_error: Exception | None = None
            result: dict[str, Any] = {}
            try:
                tick = self.run_source_controller_tick()
                result = tick["result"]
                evidence["controller_id"] = tick["controller_id"]
            except ControllerTickError as exc:
                tick_error = exc
                result = dict(getattr(exc, "context", {}) or {})
                evidence["tick_stage"] = exc.stage
                evidence["tick_error"] = str(exc)

            readback = self.controller_readback()
            connector_item = next(
                (
                    item
                    for item in readback.get("connectors") or []
                    if item.get("connector_id") == CONNECTOR_ID
                ),
                None,
            )
            reconcile = result.get("reconcile") or {}
            actions = [
                action
                for persona_result in (reconcile.get("results") or [])
                for action in (persona_result.get("actions") or [])
                if action.get("connector_id") == CONNECTOR_ID
            ]
            evidence.update(
                {
                    "reconcile_summary": reconcile.get("summary"),
                    "reconcile_actions": actions,
                    "connector_present_in_readback": connector_item is not None,
                    "connector_desired_state": (connector_item or {}).get("desired_state"),
                    "connector_freshness": (connector_item or {}).get("freshness"),
                    "requirement_snapshot": readback.get("requirement_snapshot"),
                    "readback_route": "/api/source-ingest/controller/readback",
                }
            )

            if tick_error is not None:
                # Root-cause the failure so the evidence names a defect rather
                # than a symptom: the reconciler admitted the requirement, but
                # the connector carries no persona desired-state marker, so the
                # controller's own terminal guard can never accept it.
                marker_missing = not (connector_item or {}).get("desired_state")
                admitted = any(
                    action.get("connector_action") == "verified_existing_custom_connector"
                    for action in actions
                )
                if marker_missing and admitted:
                    self.record_gap(
                        gap_id="G2",
                        title=(
                            "Operator-managed connectors bound to a Persona requirement never "
                            "receive the persona_source_reconciliation desired-state marker"
                        ),
                        loop_id="source_ingestion",
                        owning_surface="services/source_ingestion/persona_source_reconciler.py",
                        detail=(
                            "SourceProvisioningReconciler._reconcile_requirement returns "
                            "'verified_existing_custom_connector' without calling _ensure_connector, "
                            "so metadata['persona_source_reconciliation'] is never written. "
                            "/api/source-ingest/controller/readback therefore reports an empty "
                            "desired_state for the connector, and controller_worker."
                            "_validate_terminal_readback fails closed on desired_dataset_mismatch "
                            "(and, because cadence is unknown, source_data_stale). Loop 1 can only "
                            "reach terminal reconciled truth through the two built-in TW live "
                            "providers, never through an operator-approved bounded connector."
                        ),
                        observed=str(tick_error),
                    )
                raise DrillError(f"source-ingestion controller tick failed: {tick_error}")

            summary = reconcile.get("summary") or {}
            require(
                int(summary.get("conflicts") or 0) == 0 and int(summary.get("unsupported") or 0) == 0,
                f"persona reconcile did not fail closed cleanly: {summary}",
            )
            require(
                int(summary.get("satisfied") or 0) + int(summary.get("mutated") or 0) >= 1,
                f"persona requirement produced no live source binding: {summary}",
            )
            require(
                connector_item is not None,
                "bounded connector missing from authoritative readback",
            )
            schedule = expect_http(
                "GET",
                f"{self.source_ingest_url}/api/source-ingest/connectors/{CONNECTOR_ID}/schedule",
            )["schedule"]
            require(bool(schedule.get("enabled")), f"connector schedule is not active: {schedule}")
            snapshot = readback.get("requirement_snapshot") or {}
            require(
                bool(snapshot.get("authoritative")),
                f"requirement snapshot was not durably admitted: {snapshot}",
            )
            evidence["schedule"] = schedule
            self.observations["source_controller_result"] = result

    def record_gap(
        self,
        *,
        gap_id: str,
        title: str,
        loop_id: str,
        owning_surface: str,
        detail: str,
        observed: str,
    ) -> None:
        if any(gap["gap_id"] == gap_id for gap in self.gaps):
            return
        self.gaps.append(
            {
                "gap_id": gap_id,
                "title": title,
                "loop_id": loop_id,
                "owning_surface": owning_surface,
                "detail": detail,
                "observed": observed,
                "found_at": utc_now(),
                "correlation_id": self.correlation_id,
            }
        )

    def check_source_record_committed(self, source_id: str) -> None:
        with self.recorder.check(
            "C02",
            "Scheduled bounded pull commits one normalized SourceRecord",
            ["Real Persona requirement produces SourceRecord and one mutable StrategySpec draft"],
        ) as evidence:
            records = [r for r in self.source_records() if r.get("source_id") == source_id]
            require(len(records) == 1, f"expected exactly one SourceRecord for {source_id}, got {len(records)}")
            record = records[0]
            require(record.get("status") == "normalized", f"SourceRecord is not normalized: {record.get('status')}")
            require(
                record.get("connector_id") == CONNECTOR_ID,
                f"SourceRecord provenance is not the bounded connector: {record.get('connector_id')}",
            )
            metadata = record.get("metadata") or {}
            require(
                isinstance(metadata.get("strategy_seed"), dict),
                "SourceRecord carries no strategy_seed for distillation",
            )
            require(
                bool(metadata.get("source_ingest_run_id")),
                "SourceRecord is not bound to an ingest run",
            )
            detail = expect_http(
                "GET",
                f"{self.source_ingest_url}/api/source-ingest/source-records/{source_id}",
            )
            evidence.update(
                {
                    "source_id": source_id,
                    "connector_id": record.get("connector_id"),
                    "status": record.get("status"),
                    "ingest_run_id": metadata.get("source_ingest_run_id"),
                    "content_hash": metadata.get("content_hash"),
                    "readback_route": "/api/source-ingest/source-records/{source_id}",
                    "readback_status": "200",
                    "readback_source_id": (detail.get("source_record") or detail).get("source_id"),
                }
            )

    def check_distillation_draft(self, source_id: str) -> None:
        with self.recorder.check(
            "C03",
            "Committed SourceRecord distills into exactly one mutable StrategySpec draft",
            ["Real Persona requirement produces SourceRecord and one mutable StrategySpec draft"],
        ) as evidence:
            from services.source_ingestion.distillation_worker import (
                DistillationJobQueue,
                DistillationJobStatus,
                source_version_digest,
            )

            tick = self.run_distillation_tick()
            require(tick["error"] is None, f"distillation tick failed: {tick['error']}")
            result = tick["result"] or {}
            actual = result.get("actual") or {}
            require(
                int(actual.get("synced_count") or 0) == 1,
                f"expected exactly one Registry sync, got {actual.get('synced_count')}",
            )
            registry_id = self.registry_id_for(source_id)
            entry = self.registry_entry(registry_id)
            require(entry is not None, f"registry has no StrategySpec for {registry_id}")
            require(
                entry.get("artifact_state") == "draft",
                f"distilled StrategySpec is not a mutable draft: {entry.get('artifact_state')}",
            )
            distillation = (entry.get("metadata") or {}).get("distillation") or {}
            source = self.committed_source_record(source_id)
            require(
                distillation.get("source_id") == source_id
                and distillation.get("source_digest") == source_version_digest(source),
                f"StrategySpec draft lineage is not bound to the committed source version: {distillation}",
            )
            queue = DistillationJobQueue(self.job_queue_path)
            job = queue.get(source_id)
            require(
                job is not None and job.status == DistillationJobStatus.DONE.value,
                f"distillation job is not terminal-done: {getattr(job, 'status', None)}",
            )
            require(
                queue.version_count(source_id) == 1,
                f"expected one committed source version, got {queue.version_count(source_id)}",
            )
            evidence.update(
                {
                    "registry_id": registry_id,
                    "artifact_state": entry.get("artifact_state"),
                    "strategy_id": entry.get("strategy_id"),
                    "checksum": entry.get("checksum"),
                    "distillation_lineage": distillation,
                    "job_status": job.status,
                    "registry_route": f"/api/registry/strategy-specs/{registry_id}",
                    "controller_id": tick["controller_id"],
                }
            )
            self.observations["primary_registry_id"] = registry_id
            self.observations["primary_strategy_id"] = entry.get("strategy_id")
            self.observations["distillation_result"] = result

    def check_unapproved_spec_gate(self, source_id: str) -> None:
        with self.recorder.check(
            "C04",
            "Unapproved StrategySpec produces no replication work",
            ["Unapproved spec and immutable approved artifact negative gates pass"],
        ) as evidence:
            registry_id = self.observations["primary_registry_id"]
            entry = self.registry_entry(registry_id)
            require(entry.get("artifact_state") == "draft", "precondition: spec must still be a draft")
            tick = self.run_alpha_tick()
            require(tick["error"] is None, f"alpha tick failed while the spec was unapproved: {tick['error']}")
            state = tick["state"].to_dict()
            desired = state.get("desired_state") or {}
            reconcile = state.get("reconcile") or {}
            require(
                int(desired.get("approved_spec_count") or 0) == 0,
                f"an unapproved spec was treated as desired state: {desired}",
            )
            require(
                not (reconcile.get("created_experiment_run_ids") or []),
                f"replication produced runs from an unapproved spec: {reconcile}",
            )
            runs = self.replication_runs()
            require(
                not runs,
                f"research authority holds ExperimentRuns before any approval: {len(runs)}",
            )
            evidence.update(
                {
                    "registry_id": registry_id,
                    "artifact_state": entry.get("artifact_state"),
                    "approved_spec_count": desired.get("approved_spec_count"),
                    "authority_experiment_run_count": len(runs),
                    "authority_route": "/api/research-orchestrator/runs",
                }
            )

    def alpha_discovery_probe(self, *, source_id: str, registry_id: str, strategy_id: str) -> dict[str, Any]:
        """Isolate which join key the alpha controller's discovery actually uses."""

        def approved_for(key: str) -> list[dict[str, Any]]:
            status, body = http_json(
                "GET",
                f"{self.registry_url}/api/registry/strategies/{key}/strategy-specs?artifact_state=approved",
            )
            if status == 404 or not isinstance(body, list):
                return []
            return [view["entry"] for view in body if isinstance(view, dict) and "entry" in view]

        by_source_id = approved_for(source_id)
        by_strategy_id = approved_for(strategy_id)
        seed_keys: list[str] = []
        if self.seed_store_path.exists():
            for line in self.seed_store_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict) and payload.get("source_id"):
                    seed_keys.append(str(payload["source_id"]))
        return {
            "seed_store_discovery_keys": sorted(set(seed_keys)),
            "registry_strategy_id": strategy_id,
            "approved_by_seed_source_id": [entry.get("registry_id") for entry in by_source_id],
            "approved_by_registry_strategy_id": [entry.get("registry_id") for entry in by_strategy_id],
            "registry_route_used_by_controller": (
                f"/api/registry/strategies/{source_id}/strategy-specs?artifact_state=approved"
            ),
        }

    def enqueue_approved_spec_directly(self, registry_id: str, strategy_id: str) -> dict[str, Any]:
        """Supply the missing discovery join, then run the real alpha worker.

        Everything downstream of the lookup key is the shipped implementation:
        the controller's own registry-entry-to-queue projection, the real
        ``AlphaReplicationQueue``, and the real ``AlphaRevalidationWorker``
        writing through the real research authority. This isolates the defect
        to the discovery join instead of leaving the rest of loop 3 unproven.
        """

        from services.research.alpha_replication.queue import AlphaReplicationQueue
        from services.research.alpha_replication.replication_controller import (
            _get_approved_specs_for_strategy,
            _queue_payload_from_registry_entry,
        )
        from services.research.alpha_replication.revalidation_worker import (
            AlphaRevalidationWorker,
        )
        from services.research.experiment_orchestrator.authority import (
            ResearchAuthorityHttpClient,
        )

        entries = _get_approved_specs_for_strategy(self.registry_url, strategy_id)
        require(entries, f"registry has no approved StrategySpec under strategy_id {strategy_id}")
        entry = next(item for item in entries if item.get("registry_id") == registry_id)
        try:
            payload = _queue_payload_from_registry_entry(entry, tenant_id=TENANT_ID)
        except Exception as exc:  # noqa: BLE001 - the rejection is the finding
            self.record_gap(
                gap_id="G3",
                title=(
                    "Distilled StrategySpec Registry entries carry no tenant binding, so "
                    "alpha replication rejects them even when discovery succeeds"
                ),
                loop_id="strategy_distillation",
                owning_surface=(
                    "services/source_ingestion/distillation_controller.py"
                    " -> services/research/alpha_replication/replication_controller.py"
                ),
                detail=(
                    "_queue_payload_from_registry_entry requires metadata['tenant_id'] or an "
                    "embedded strategy_spec['tenant_id'] on the approved Registry entry. The "
                    "distillation controller builds the entry from "
                    "StrategySpecConversionService.convert_seed and adds only a "
                    "metadata['distillation'] block, so no tenant binding is ever written. "
                    "Even with the discovery join repaired, every approved distilled spec is "
                    "rejected at queue admission."
                ),
                observed=f"{type(exc).__name__}: {exc}",
            )
            return {
                "queue_admission_error": f"{type(exc).__name__}: {exc}",
                "registry_entry_metadata_keys": sorted((entry.get("metadata") or {}).keys()),
                "processed": 0,
                "created_experiment_run_ids": [],
            }
        queue = AlphaReplicationQueue(self.alpha_dir)
        queue.enqueue(payload, enqueued_by="l12-know-drill-discovery-bypass")
        worker = AlphaRevalidationWorker(
            queue,
            self.alpha_dir,
            worker_id="alpha-revalidation-worker",
            authority=ResearchAuthorityHttpClient(
                self.research_url, actor_id="alpha-replication-worker"
            ),
            registry_url=self.registry_url,
        )
        return worker.run_once(tenant_id=TENANT_ID)

    def check_approved_spec_produces_run(self, source_id: str) -> None:
        with self.recorder.check(
            "C05",
            "Approved immutable StrategySpec produces one authoritative ExperimentRun",
            ["Approved StrategySpec produces authoritative ExperimentRun"],
        ) as evidence:
            registry_id = self.observations["primary_registry_id"]
            strategy_id = self.observations["primary_strategy_id"]
            approved = self.approve_strategy_spec(
                registry_id, approver=f"reviewer-{self.correlation_id[:8]}"
            )
            approved_entry = approved.get("entry") if isinstance(approved, dict) else None
            approved_entry = approved_entry if isinstance(approved_entry, dict) else approved
            require(
                approved_entry.get("artifact_state") == "approved",
                f"registry did not approve the spec: {approved_entry.get('artifact_state')}",
            )
            self.observations["approved_entry"] = approved_entry
            evidence.update(
                {
                    "registry_id": registry_id,
                    "strategy_id": strategy_id,
                    "approver": approved_entry.get("approver"),
                    "approval_decision_id": approved_entry.get("approval_decision_id"),
                    "approved_at": approved_entry.get("approved_at"),
                }
            )

            tick = self.run_alpha_tick()
            require(tick["error"] is None, f"alpha replication tick failed: {tick['error']}")
            state = tick["state"].to_dict()
            desired = state.get("desired_state") or {}
            reconcile = state.get("reconcile") or {}
            evidence.update(
                {
                    "controller_id": tick["controller_id"],
                    "approved_spec_count": desired.get("approved_spec_count"),
                    "created_experiment_run_ids": reconcile.get("created_experiment_run_ids"),
                }
            )

            if int(desired.get("approved_spec_count") or 0) == 0:
                probe = self.alpha_discovery_probe(
                    source_id=source_id, registry_id=registry_id, strategy_id=strategy_id
                )
                evidence["discovery_probe"] = probe
                isolation = self.enqueue_approved_spec_directly(registry_id, strategy_id)
                runs = self.replication_runs()
                evidence["discovery_bypass_isolation"] = {
                    "note": (
                        "Discovery join supplied by the drill; every stage after the "
                        "lookup key is the shipped implementation."
                    ),
                    "processed": isolation.get("processed"),
                    "created_experiment_run_ids": isolation.get("created_experiment_run_ids"),
                    "authority_receipts": isolation.get("authority_receipts"),
                    "errors": isolation.get("errors"),
                    "authority_experiment_run_count": len(runs),
                }
                self.record_gap(
                    gap_id="G1",
                    title=(
                        "Alpha replication discovery joins the distillation seed store's "
                        "source_id against the Registry strategy_id"
                    ),
                    loop_id="alpha_replication",
                    owning_surface="services/research/alpha_replication/replication_controller.py",
                    detail=(
                        "run_controller_tick reads discovery keys from the distillation seed "
                        "store field 'source_id' and passes each one to "
                        "_get_approved_specs_for_strategy, which queries "
                        "/api/registry/strategies/{strategy_id}/strategy-specs. The distillation "
                        "controller registers the spec under strategy_id "
                        "'strat-<source_id>-<digest12>' (distillation_controller."
                        "_build_registry_payload), and the seed record carries no registry or "
                        "strategy identity at all. The two keys can never match, so an approved "
                        "StrategySpec is never admitted as alpha replication desired state. "
                        "docker-compose.yml points both services at the same "
                        "STRATEGY_SPEC_SEED_STORE_PATH, so the deployed configuration has the "
                        "same break."
                    ),
                    observed=(
                        f"approved_spec_count=0 while the Registry holds approved {registry_id} "
                        f"under strategy_id {strategy_id}"
                    ),
                )
                raise DrillError(
                    "approved StrategySpec was not discovered as alpha replication desired state: "
                    f"seed discovery key={source_id!r}, registry strategy_id={strategy_id!r}"
                )

            require(
                int(desired.get("approved_spec_count") or 0) == 1,
                f"expected exactly one approved spec in desired state: {desired}",
            )
            run_ids = list(reconcile.get("created_experiment_run_ids") or [])
            require(len(run_ids) == 1, f"expected exactly one ExperimentRun, got {run_ids}")
            runs = self.replication_runs()
            require(len(runs) == 1, f"research authority holds {len(runs)} ExperimentRuns, expected 1")
            authority_run = runs[0]
            parameters = authority_run.get("parameters") or {}
            require(
                parameters.get("strategy_spec_id") == registry_id,
                f"authoritative run is not bound to the approved spec: {parameters.get('strategy_spec_id')}",
            )
            require(
                str(authority_run.get("status") or "").lower() == "completed",
                f"authoritative run is not completed: {authority_run.get('status')}",
            )
            evidence.update(
                {
                    "experiment_task_ids": list(reconcile.get("created_experiment_task_ids") or []),
                    "authority_run_id": authority_run.get("run_id"),
                    "authority_route": f"/api/research-orchestrator/runs/{authority_run.get('run_id')}",
                    "authority_receipts": reconcile.get("authority_receipts"),
                }
            )
            self.observations["alpha_state"] = state

    def check_approved_artifact_immutable(self, source_id: str) -> None:
        with self.recorder.check(
            "C06",
            "An approved StrategySpec is never rewritten by a re-delivering controller replica",
            ["Unapproved spec and immutable approved artifact negative gates pass"],
        ) as evidence:
            registry_id = self.observations["primary_registry_id"]
            before = self.registry_entry(registry_id)
            require(before.get("artifact_state") == "approved", "precondition: spec must be approved")

            # A replacement controller replica with a fresh durable queue
            # re-admits the same committed SourceRecord from the shared evidence
            # store and re-offers the same versioned Registry identity. The
            # approved artifact must survive that at-least-once delivery.
            replica_dir = self.run_dir / "distillation-replica"
            replica_dir.mkdir(parents=True, exist_ok=True)
            tick = self.run_distillation_tick(
                job_queue_path=replica_dir / "job_queue.sqlite3",
                seed_store_path=replica_dir / "seeds.jsonl",
                state_path=replica_dir / "controller_state.json",
            )
            after = self.registry_entry(registry_id)
            result = tick["result"] or {}
            actual = result.get("actual") or {}
            evidence.update(
                {
                    "registry_id": registry_id,
                    "entry_digest_before": sha256_json(before),
                    "entry_digest_after": sha256_json(after),
                    "skipped_immutable_count": actual.get("skipped_immutable_count"),
                    "synced_count": actual.get("synced_count"),
                    "replica_queue": str(replica_dir / "job_queue.sqlite3"),
                    "tick_error": None if tick["error"] is None else str(tick["error"]),
                }
            )
            require(
                tick["error"] is None,
                f"the re-delivering controller replica failed: {tick['error']}",
            )
            require(
                after.get("artifact_state") == "approved",
                f"approved artifact state changed: {after.get('artifact_state')}",
            )
            require(
                sha256_json(before) == sha256_json(after),
                "approved Registry artifact was mutated by a re-delivering distillation controller",
            )
            require(
                int(actual.get("skipped_immutable_count") or 0) >= 1,
                f"immutable skip was not recorded: {actual}",
            )
            require(
                int(actual.get("synced_count") or 0) == 0,
                f"a re-delivery rewrote an approved artifact: {actual}",
            )

    # -- duplicate / concurrency / failure / restart -----------------------

    def add_bounded_sources(self, source_ids: Sequence[str], *, fail_until_attempt: int = 0) -> None:
        """Extend the bounded provider payload and re-run the scheduled pull."""

        self.configure_bounded_connector(
            source_ids=list(self.observations.setdefault("bounded_source_ids", []))
            + [sid for sid in source_ids if sid not in self.observations["bounded_source_ids"]],
            fail_until_attempt=fail_until_attempt,
        )
        for sid in source_ids:
            if sid not in self.observations["bounded_source_ids"]:
                self.observations["bounded_source_ids"].append(sid)

    def run_scheduled_pull(self) -> dict[str, Any]:
        return expect_http(
            "POST",
            f"{self.source_ingest_url}/api/source-ingest/run-scheduled",
            {"max_concurrency": 2, "force_connector_ids": [CONNECTOR_ID]},
            token=self.controller_token,
        )

    def check_duplicate_ticks_idempotent(self, source_id: str) -> None:
        with self.recorder.check(
            "C07",
            "Duplicate ingest and distillation ticks converge instead of duplicating",
            ["Duplicate concurrency provider Registry research failure and restart cases pass"],
        ) as evidence:
            from services.source_ingestion.distillation_worker import DistillationJobQueue
            from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore

            duplicate_source = f"src-l12-know-{self.correlation_id[:8]}-dup"
            self.add_bounded_sources([duplicate_source])

            pulls = [self.run_scheduled_pull() for _ in range(3)]
            records = [r for r in self.source_records() if r.get("source_id") == duplicate_source]
            require(
                len(records) == 1,
                f"three scheduled pulls produced {len(records)} SourceRecords for one bounded source",
            )

            for _ in range(3):
                tick = self.run_distillation_tick()
                require(tick["error"] is None, f"repeated distillation tick failed: {tick['error']}")

            queue = DistillationJobQueue(self.job_queue_path)
            registry_id = self.registry_id_for(duplicate_source)
            entry = self.registry_entry(registry_id)
            require(entry is not None, f"duplicate-source draft missing: {registry_id}")
            require(
                queue.version_count(duplicate_source) == 1,
                f"duplicate ticks committed {queue.version_count(duplicate_source)} source versions",
            )
            seeds = [
                seed
                for seed in StrategySpecSeedStore(self.seed_store_path).list_all()
                if seed.source_id == duplicate_source
            ]
            require(len(seeds) == 1, f"duplicate ticks produced {len(seeds)} seeds")
            evidence.update(
                {
                    "duplicate_source_id": duplicate_source,
                    "scheduled_pull_count": len(pulls),
                    "distillation_tick_count": 3,
                    "source_record_count": len(records),
                    "committed_version_count": queue.version_count(duplicate_source),
                    "seed_count": len(seeds),
                    "registry_id": registry_id,
                    "artifact_state": entry.get("artifact_state"),
                }
            )

    def check_concurrent_distillation(self) -> None:
        with self.recorder.check(
            "C08",
            "Two independent worker processes never double-claim or lose a distillation job",
            ["Duplicate concurrency provider Registry research failure and restart cases pass"],
        ) as evidence:
            from services.source_ingestion.distillation_worker import DistillationJobQueue
            from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore

            batch = [
                f"src-l12-know-{self.correlation_id[:8]}-conc-{index:02d}"
                for index in range(8)
            ]
            self.add_bounded_sources(batch)
            self.run_scheduled_pull()

            concurrent_dir = self.run_dir / "distillation-concurrent"
            concurrent_dir.mkdir(parents=True, exist_ok=True)
            queue_path = concurrent_dir / "job_queue.sqlite3"
            seed_path = concurrent_dir / "seeds.jsonl"

            # Admit the committed versions once, then let two OS processes race.
            config = self.distillation_config(
                job_queue_path=queue_path,
                seed_store_path=seed_path,
                state_path=concurrent_dir / "controller_state.json",
            )
            queue = DistillationJobQueue(queue_path)
            batch_ids = set(batch)
            for record in self.committed_source_records():
                if record.source_id in batch_ids:
                    queue.enqueue_source_record(record)
            admitted = queue.count()
            require(
                admitted == len(batch),
                f"expected {len(batch)} admitted concurrency jobs, got {admitted}",
            )

            context = mp.get_context("spawn")
            results: Any = context.Queue()
            barrier = context.Barrier(2)
            processes = [
                context.Process(
                    target=_concurrent_distill_child,
                    args=(str(REPO_ROOT), _config_payload(config), f"worker-{index}", results, barrier),
                )
                for index in range(2)
            ]
            for process in processes:
                process.start()
            collected = []
            for _ in processes:
                collected.append(results.get(timeout=300))
            for process in processes:
                process.join(timeout=60)

            errors = [item for item in collected if item.get("error")]
            require(not errors, f"a concurrent worker process failed: {errors}")
            processed_total = sum(int(item["totals"].get("processed") or 0) for item in collected)
            created_total = sum(int(item["totals"].get("created") or 0) for item in collected)
            # Attempts may exceed outcomes under at-least-once delivery; what
            # must hold is that every admitted job reaches exactly one terminal
            # artifact, which the queue and seed assertions below check.
            require(
                processed_total >= admitted,
                f"two processes processed {processed_total} of {admitted} admitted jobs",
            )
            require(
                created_total == admitted,
                f"two processes created {created_total} drafts for {admitted} admitted jobs",
            )
            require(
                all(int(item["totals"].get("processed") or 0) > 0 for item in collected),
                f"only one process did work, so this is not a contention proof: {collected}",
            )
            metrics = DistillationJobQueue(queue_path).metrics()
            require(
                metrics["done"] + metrics["skipped"] == admitted,
                f"jobs were lost across two processes: {metrics} of {admitted} admitted",
            )
            require(
                metrics["failed"] == 0 and metrics["dead_letter"] == 0 and metrics["retry_wait"] == 0,
                f"concurrent delivery left degraded work behind: {metrics}",
            )
            seeds = StrategySpecSeedStore(seed_path).list_all()
            require(
                len(seeds) == len({seed.seed_id for seed in seeds}) == admitted,
                f"the shared seed store lost or duplicated a concurrent write: "
                f"{len(seeds)} seeds for {admitted} jobs",
            )
            evidence.update(
                {
                    "admitted_jobs": admitted,
                    "worker_processes": len(processes),
                    "per_worker": collected,
                    "processed_total": processed_total,
                    "created_total": created_total,
                    "queue_metrics": metrics,
                    "seed_count": len(seeds),
                }
            )

    def committed_source_records(self) -> list[Any]:
        from services.source_ingestion.pg_store import build_source_evidence_repository

        repository = build_source_evidence_repository(self.evidence_store_path)
        return list(repository.list_source_records())

    def check_provider_failure_dlq_replay(self) -> None:
        with self.recorder.check(
            "C09",
            "A bounded provider failure dead-letters and replays to a committed SourceRecord",
            ["Duplicate concurrency provider Registry research failure and restart cases pass"],
        ) as evidence:
            failing_source = f"src-l12-know-{self.correlation_id[:8]}-dlq"
            self.add_bounded_sources([failing_source], fail_until_attempt=99)
            pull = self.run_scheduled_pull()
            dlq = expect_http("GET", f"{self.source_ingest_url}/api/source-ingest/dlq")
            require(
                int(dlq.get("unresolved_count") or 0) >= 1,
                f"a failing bounded provider did not dead-letter: {dlq.get('status_counts')}",
            )
            evidence["dlq_after_failure"] = dlq.get("status_counts")
            evidence["failed_pull_summary"] = pull.get("summary")

            # Provider recovers; the operator replays the durable dead letter.
            self.configure_bounded_connector(
                source_ids=list(self.observations["bounded_source_ids"]), fail_until_attempt=0
            )
            replay = expect_http(
                "POST",
                f"{self.source_ingest_url}/api/source-ingest/dlq/replay",
                {"reason": f"L12 knowledge drill replay {self.correlation_id[:8]}"},
                token=self.controller_token,
            )
            dlq_after = expect_http("GET", f"{self.source_ingest_url}/api/source-ingest/dlq")
            records = [r for r in self.source_records() if r.get("source_id") == failing_source]
            require(
                int(dlq_after.get("unresolved_count") or 0) == 0,
                f"dead letters remain unresolved after replay: {dlq_after.get('status_counts')}",
            )
            require(
                len(records) == 1,
                f"replay committed {len(records)} SourceRecords for {failing_source}, expected 1",
            )
            evidence.update(
                {
                    "failing_source_id": failing_source,
                    "replay_summary": replay.get("summary"),
                    "dlq_after_replay": dlq_after.get("status_counts"),
                    "committed_after_replay": len(records),
                }
            )

    def check_registry_outage_replays_once(self) -> None:
        with self.recorder.check(
            "C10",
            "A Registry outage parks a durable retry and replays to exactly one draft",
            ["Duplicate concurrency provider Registry research failure and restart cases pass"],
        ) as evidence:
            from services.source_ingestion.distillation_worker import (
                DistillationJobQueue,
                DistillationJobStatus,
            )

            outage_source = f"src-l12-know-{self.correlation_id[:8]}-outage"
            self.add_bounded_sources([outage_source])
            self.run_scheduled_pull()

            registry = self.services["registry"]
            registry.stop()
            failed = self.run_distillation_tick(retry_base_seconds=0)
            require(failed["error"] is not None, "the controller reported success during a Registry outage")
            require(
                getattr(failed["error"], "stage", None) == "registry_sync",
                f"outage was not attributed to registry_sync: {getattr(failed['error'], 'stage', None)}",
            )
            queue = DistillationJobQueue(self.job_queue_path)
            job = queue.get(outage_source)
            require(
                job is not None and job.status == DistillationJobStatus.RETRY_WAIT.value,
                f"outage did not park a durable retry: {getattr(job, 'status', None)}",
            )
            require(
                self.registry_entry_status_during_outage(outage_source) == 0,
                "the Registry answered while it was supposed to be down",
            )
            evidence["outage_job_status"] = job.status
            evidence["outage_stage"] = getattr(failed["error"], "stage", None)

            registry.start()
            recovered = self.run_distillation_tick(retry_base_seconds=0)
            require(recovered["error"] is None, f"recovery tick failed: {recovered['error']}")
            registry_id = self.registry_id_for(outage_source)
            entry = self.registry_entry(registry_id)
            require(entry is not None, f"recovery did not deliver {registry_id}")
            require(
                entry.get("artifact_state") == "draft",
                f"recovered draft is in an unexpected state: {entry.get('artifact_state')}",
            )
            job = DistillationJobQueue(self.job_queue_path).get(outage_source)
            require(
                job.status == DistillationJobStatus.DONE.value,
                f"recovered job is not terminal-done: {job.status}",
            )
            require(
                queue.version_count(outage_source) == 1,
                "outage replay committed more than one source version",
            )
            evidence.update(
                {
                    "outage_source_id": outage_source,
                    "registry_restart_count": registry.start_count,
                    "registry_id": registry_id,
                    "recovered_job_status": job.status,
                    "committed_version_count": queue.version_count(outage_source),
                }
            )

    def registry_entry_status_during_outage(self, source_id: str) -> int:
        status, _ = http_json(
            "GET",
            f"{self.registry_url}/api/registry/strategy-specs/{self.registry_id_for(source_id)}",
            timeout=3,
        )
        return status

    def check_research_authority_outage(self) -> None:
        """Alpha-loop failure semantics need an admitted queue entry to exercise."""

        from services.research.alpha_replication.queue import AlphaReplicationQueue

        queue = AlphaReplicationQueue(self.alpha_dir)
        metrics = queue.get_metrics()
        title = "A research-authority outage leaves no phantom ExperimentRun"
        acceptance = ["Duplicate concurrency provider Registry research failure and restart cases pass"]
        if int(metrics.get("total") or 0) == 0:
            self.recorder.blocked(
                "C11",
                title,
                acceptance,
                reason=(
                    "The alpha replication queue is empty because no approved StrategySpec can be "
                    "admitted: discovery never matches (G1) and queue admission rejects the entry "
                    "for a missing tenant binding (G3). Research-authority failure and restart "
                    "semantics cannot be exercised against a real approved artifact until those "
                    "are repaired."
                ),
                evidence={
                    "queue_metrics": metrics,
                    "blocking_gaps": ["G1", "G3"],
                    "registry_approved_spec": self.observations.get("primary_registry_id"),
                },
            )
            return

        with self.recorder.check("C11", title, acceptance) as evidence:
            research = self.services["research"]
            before_runs = len(self.replication_runs())
            research.stop()
            tick = self.run_alpha_tick()
            evidence["outage_error"] = None if tick["error"] is None else str(tick["error"])
            research.start()
            after_outage_runs = len(self.replication_runs())
            require(
                after_outage_runs == before_runs,
                f"a research-authority outage created {after_outage_runs - before_runs} phantom run(s)",
            )
            recovered = self.run_alpha_tick()
            require(recovered["error"] is None, f"recovery tick failed: {recovered['error']}")
            final_runs = len(self.replication_runs())
            require(
                final_runs == before_runs,
                f"recovery duplicated ExperimentRuns: {before_runs} -> {final_runs}",
            )
            evidence.update(
                {
                    "runs_before_outage": before_runs,
                    "runs_during_outage": after_outage_runs,
                    "runs_after_recovery": final_runs,
                    "research_start_count": research.start_count,
                }
            )

    def check_registry_durability_across_restart(self) -> None:
        with self.recorder.check(
            "C16",
            "An approved immutable StrategySpec survives an artifact-authority restart",
            [
                "Unapproved spec and immutable approved artifact negative gates pass",
                "Duplicate concurrency provider Registry research failure and restart cases pass",
            ],
        ) as evidence:
            registry_id = self.observations["primary_registry_id"]
            approved_entry = self.observations.get("approved_entry") or {}
            require(
                approved_entry.get("artifact_state") == "approved",
                "precondition: the drill must have an approved artifact to test durability with",
            )
            entry = self.registry_entry(registry_id)
            evidence.update(
                {
                    "registry_id": registry_id,
                    "registry_start_count": self.services["registry"].start_count,
                    "approved_at": approved_entry.get("approved_at"),
                    "approver": approved_entry.get("approver"),
                    "entry_present_after_restart": entry is not None,
                    "artifact_state_after_restart": (entry or {}).get("artifact_state"),
                }
            )
            if entry is None:
                self.record_gap(
                    gap_id="G5",
                    title=(
                        "The Registry artifact authority has no durable storage, so approved "
                        "immutable StrategySpecs do not survive a restart"
                    ),
                    loop_id="strategy_distillation",
                    owning_surface="services/registry/storage.py",
                    detail=(
                        "services/registry/storage.RegistryStore is a process-local in-memory "
                        "dict behind a module singleton, and services/registry/main.py mounts "
                        "that app unchanged. The deployed registry container receives "
                        "DATABASE_URL and an object-store config but never uses them for entry "
                        "storage and mounts no volume. Every StrategySpec — including approved, "
                        "immutable, review-decided ones — is lost when the process restarts. "
                        "The L12-DIST-001 immutability guarantee and the L12-ALPHA-001 "
                        "approved-only admission gate both rest on this authority."
                    ),
                    observed=(
                        f"{registry_id} was approved at {approved_entry.get('approved_at')} and "
                        f"returns 404 after {self.services['registry'].start_count - 1} restart(s)"
                    ),
                )
            require(
                entry is not None,
                f"the approved artifact {registry_id} did not survive an authority restart",
            )
            require(
                entry.get("artifact_state") == "approved",
                f"artifact state changed across restart: {entry.get('artifact_state')}",
            )

    def check_restart_recovery(self) -> None:
        with self.recorder.check(
            "C12",
            "Service and controller restart reload durable truth without duplicating work",
            ["Duplicate concurrency provider Registry research failure and restart cases pass"],
        ) as evidence:
            from services.source_ingestion.controller_state import ControllerStateStore
            from services.source_ingestion.distillation_worker import DistillationJobQueue

            before_records = len(self.source_records())
            queue = DistillationJobQueue(self.job_queue_path)
            before_metrics = queue.metrics()
            before_state = ControllerStateStore(self.distillation_state_path).load()
            require(before_state is not None, "distillation controller has no durable state to reload")

            for name in ("source_ingest", "registry", "research"):
                self.services[name].restart()

            tick = self.run_distillation_tick(fresh_identity=True)
            require(tick["error"] is None, f"post-restart distillation tick failed: {tick['error']}")
            after_state = ControllerStateStore(self.distillation_state_path).load()
            require(
                after_state.controller_id != before_state.controller_id,
                "the restarted controller reused its previous runtime identity",
            )
            require(
                after_state.sequence_no > before_state.sequence_no,
                "the restarted controller did not continue its durable state sequence",
            )
            after_metrics = DistillationJobQueue(self.job_queue_path).metrics()
            require(
                len(self.source_records()) == before_records,
                "restart duplicated committed SourceRecords",
            )
            require(
                sum(after_metrics.values()) == sum(before_metrics.values()),
                f"restart duplicated distillation jobs: {before_metrics} -> {after_metrics}",
            )
            evidence.update(
                {
                    "service_start_counts": {
                        name: service.start_count for name, service in self.services.items()
                    },
                    "controller_id_before": before_state.controller_id,
                    "controller_id_after": after_state.controller_id,
                    "sequence_no_before": before_state.sequence_no,
                    "sequence_no_after": after_state.sequence_no,
                    "queue_metrics_before": before_metrics,
                    "queue_metrics_after": after_metrics,
                    "source_record_count": before_records,
                }
            )

    # -- operator truth ----------------------------------------------------

    def projected_record(self, loop_id: str) -> dict[str, Any]:
        module = _loop_control_module()
        record = self.loop_store.record_for(loop_id)
        return module.project_controller_record_to_bff(record)

    def check_source_loop_truth(self) -> None:
        with self.recorder.check(
            "C13",
            "source_ingestion controller record projects the same truth the service reports",
            ["BFF and controller terminal truth match every authority"],
        ) as evidence:
            record = self.loop_store.record_for("source_ingestion")
            projected = self.projected_record("source_ingestion")
            readback = self.controller_readback()
            require(
                projected["controller_health"]["controller_name"] == "source-ingestion-controller",
                f"projected controller identity is wrong: {projected['controller_health']}",
            )
            require(
                projected["actual_state_query"]
                == self.source_ingest_url.rstrip("/") + "/api/source-ingest/controller/readback",
                f"projected actual-state query does not name the authority: {projected['actual_state_query']}",
            )
            require(
                bool(projected["lease_fenced"]),
                "projected controller truth is not lease fenced",
            )
            require(
                projected["dlq_count"] == readback.get("unresolved_dlq_count")
                or projected["dlq_count"] is None,
                f"projected dlq_count {projected['dlq_count']} contradicts the authority "
                f"{readback.get('unresolved_dlq_count')}",
            )
            evidence.update(
                {
                    "truth_level": record.get("truth_level"),
                    "controller_status": projected["controller_health"]["status"],
                    "desired_state_presence": projected["desired_state_presence"]["status"],
                    "actual_state_query": projected["actual_state_query"],
                    "projected_dlq_count": projected["dlq_count"],
                    "authority_unresolved_dlq_count": readback.get("unresolved_dlq_count"),
                }
            )

    def check_distillation_loop_truth(self) -> None:
        with self.recorder.check(
            "C14",
            "strategy_distillation controller record projects the same truth the queue and Registry report",
            ["BFF and controller terminal truth match every authority"],
        ) as evidence:
            from services.source_ingestion.distillation_worker import DistillationJobQueue

            record = self.loop_store.record_for("strategy_distillation")
            projected = self.projected_record("strategy_distillation")
            payload = projected.get("payload") or {}
            actual = (payload.get("actual") or {})
            outbox = actual.get("outbox") or {}
            metrics = DistillationJobQueue(self.job_queue_path).metrics()
            require(
                projected["controller_health"]["controller_name"] == "strategy-distillation-controller",
                f"projected controller identity is wrong: {projected['controller_health']}",
            )
            require(
                outbox == metrics,
                f"projected outbox truth {outbox} contradicts the durable queue {metrics}",
            )
            require(
                int(actual.get("pending_dead_letter_count") or 0) == 0,
                f"projected truth hides dead letters: {actual}",
            )
            registry_id = self.observations["primary_registry_id"]
            entry = self.registry_entry(registry_id)
            evidence.update(
                {
                    "truth_level": record.get("truth_level"),
                    "controller_status": projected["controller_health"]["status"],
                    "projected_outbox": outbox,
                    "authority_queue_metrics": metrics,
                    "registry_id": registry_id,
                    "registry_artifact_state": (entry or {}).get("artifact_state"),
                    "registry_entry_present": entry is not None,
                }
            )

    def check_alpha_loop_truth(self) -> None:
        with self.recorder.check(
            "C15",
            "alpha_replication operator truth matches the Registry and research authority",
            ["BFF and controller terminal truth match every authority"],
        ) as evidence:
            record = self.loop_store.record_for("alpha_replication")
            projected = self.projected_record("alpha_replication")
            payload = projected.get("payload") or {}
            registry_id = self.observations["primary_registry_id"]
            # The Registry's own approval response is the authority fact; a later
            # loss of that entry is a separate finding (see C16), not a reason to
            # let the alpha loop's truth look consistent.
            approved_entry = self.observations.get("approved_entry") or {}
            approved_in_registry = 1 if approved_entry.get("artifact_state") == "approved" else 0
            entry = self.registry_entry(registry_id)
            authority_runs = len(self.replication_runs())
            desired = (self.observations.get("alpha_state") or {}).get("desired_state") or {}
            controller_approved_count = int(desired.get("approved_spec_count") or 0)
            evidence.update(
                {
                    "truth_level": record.get("truth_level"),
                    "controller_status": projected["controller_health"]["status"],
                    "projected_backlog": projected["backlog"],
                    "projected_evidence_refs": projected["evidence_refs"],
                    "controller_approved_spec_count": controller_approved_count,
                    "registry_approved_spec_count": approved_in_registry,
                    "registry_entry_present_now": entry is not None,
                    "authority_experiment_run_count": authority_runs,
                    "queue_truth": payload.get("queue"),
                }
            )
            if approved_in_registry and controller_approved_count == 0:
                self.record_gap(
                    gap_id="G4",
                    title=(
                        "alpha_replication operator truth reports a healthy, empty loop while an "
                        "approved StrategySpec is stranded"
                    ),
                    loop_id="alpha_replication",
                    owning_surface="services/research/alpha_replication/replication_controller.py",
                    detail=(
                        "Because discovery (G1) never admits the approved spec, the controller "
                        "records a successful tick with approved_spec_count=0, backlog 0, and no "
                        "evidence refs. The projected BFF truth is therefore 'healthy' and shows "
                        "no backlog, so an operator cannot see that an approved artifact is "
                        "waiting. The live deployment shows the same shape: the running "
                        "alpha-replication controller has recorded tens of thousands of "
                        "successful ticks with approved_spec_count=0."
                    ),
                    observed=(
                        f"controller approved_spec_count={controller_approved_count} while the "
                        f"Registry holds {approved_in_registry} approved StrategySpec and the "
                        f"research authority holds {authority_runs} ExperimentRun(s)"
                    ),
                )
            require(
                controller_approved_count == approved_in_registry,
                "alpha_replication operator truth contradicts the Registry: controller sees "
                f"{controller_approved_count} approved spec(s), Registry holds {approved_in_registry}",
            )
            require(
                authority_runs == approved_in_registry,
                f"authority holds {authority_runs} ExperimentRun(s) for {approved_in_registry} approved spec(s)",
            )

    # -- orchestration -----------------------------------------------------

    def run(self) -> None:
        primary_source_id = f"src-l12-know-{self.correlation_id[:8]}-001"
        self.identity = {
            "correlation_id": self.correlation_id,
            "run_dir": str(self.run_dir),
            "git_sha": _git_sha(),
            "git_worktree_clean": _git_worktree_clean(),
            "script": SCRIPT_REL_PATH,
            "script_sha256": _script_sha256(),
            "python": sys.version.split()[0],
            "started_at": utc_now(),
            "primary_source_id": primary_source_id,
            "connector_id": CONNECTOR_ID,
            "persona_id": PERSONA_ID,
            "tenant_id": TENANT_ID,
            "environment": ENVIRONMENT,
        }
        self.build_services()
        self.start_services()
        self.identity["services"] = {
            name: {
                "app_module": service.app_module,
                "base_url": service.base_url,
                "pid": service.process.pid if service.process else None,
            }
            for name, service in self.services.items()
        }
        self.observations["bounded_source_ids"] = [primary_source_id]
        try:
            self.check_persona_requirement_binding(primary_source_id)
            self.check_source_record_committed(primary_source_id)
            self.check_distillation_draft(primary_source_id)
            self.check_unapproved_spec_gate(primary_source_id)
            self.check_approved_spec_produces_run(primary_source_id)
            self.check_approved_artifact_immutable(primary_source_id)
            self.check_duplicate_ticks_idempotent(primary_source_id)
            self.check_concurrent_distillation()
            self.check_provider_failure_dlq_replay()
            self.check_registry_outage_replays_once()
            self.check_registry_durability_across_restart()
            self.check_research_authority_outage()
            self.check_restart_recovery()
            self.check_source_loop_truth()
            self.check_distillation_loop_truth()
            self.check_alpha_loop_truth()
        finally:
            self.identity["finished_at"] = utc_now()
            self.identity["service_start_counts"] = {
                name: service.start_count for name, service in self.services.items()
            }
            self.stop_services()

    def evidence_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": TASK_ID,
            "proof_level": "EP3",
            "loops": list(LOOP_IDS),
            "identity": self.identity,
            "summary": self.recorder.summary(),
            "gaps": self.gaps,
            "checks": [result.to_dict() for result in self.recorder.results],
            "controller_records": {
                loop_id: self.loop_store.merged.get((loop_id, TENANT_ID, ENVIRONMENT))
                for loop_id in LOOP_IDS
            },
        }


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


EVIDENCE_DIR = REPO_ROOT / "docs/deployment/evidence/twelve-loop-gap" / TASK_ID


def verify_manifest(evidence_dir: Path) -> list[str]:
    """Cross-check the reviewed manifest against the archived run record.

    The manifest is hand-authored around a machine-written run record, so the
    two can drift: a re-cut run, an ``observed`` string copied from an earlier
    correlation id, a hand-typed sha. Every drift is a reason to distrust the
    evidence, so each one is reported instead of the first one raising.
    """

    problems: list[str] = []
    manifest_path = evidence_dir / "evidence.json"
    checksum_path = evidence_dir / "evidence.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    drill = manifest["drill"]

    record_path = REPO_ROOT / drill["run_record"]
    record_bytes = record_path.read_bytes()
    record = json.loads(record_bytes.decode("utf-8"))
    identity = record["identity"]

    def compare(label: str, claimed: Any, actual: Any) -> None:
        if claimed != actual:
            problems.append(f"{label}: manifest={claimed!r} run record={actual!r}")

    for field in ("correlation_id", "git_sha", "script", "script_sha256", "python", "started_at", "finished_at"):
        compare(f"drill.{field}", drill.get(field), identity.get(field))
    compare("drill.git_worktree_clean", drill.get("git_worktree_clean"), identity.get("git_worktree_clean"))
    compare("drill.service_start_counts", drill.get("service_start_counts"), identity.get("service_start_counts"))
    compare("drill.summary", drill.get("summary"), record["summary"])
    compare("task.evidence_cut_at", manifest["task"].get("evidence_cut_at"), identity.get("finished_at"))
    compare(
        "drill.run_record_sha256",
        drill.get("run_record_sha256"),
        hashlib.sha256(record_bytes).hexdigest(),
    )

    statuses = {check["check_id"]: check["status"] for check in record["checks"]}
    for status, field in (("passed", "checks_passed"), ("failed", "checks_failed"), ("blocked", "checks_blocked")):
        compare(
            f"drill.{field}",
            sorted(drill.get(field, ())),
            sorted(check_id for check_id, value in statuses.items() if value == status),
        )

    recorded_gaps = {gap["gap_id"]: gap for gap in record["gaps"]}
    compare("gap id set", sorted(gap["gap_id"] for gap in manifest["gaps"]), sorted(recorded_gaps))
    for gap in manifest["gaps"]:
        recorded = recorded_gaps.get(gap["gap_id"])
        if recorded is None:
            continue
        for field in ("observed", "loop_id", "found_by_check"):
            if field in recorded:
                compare(f"gap {gap['gap_id']}.{field}", gap.get(field), recorded[field])

    current_script = _script_sha256()
    if identity.get("script_sha256") != current_script:
        problems.append(
            "the archived run was produced by different drill source than the working tree: "
            f"run record={identity.get('script_sha256')!r} current {SCRIPT_REL_PATH}={current_script!r}. "
            "Re-run the drill and re-cut the manifest in the same commit."
        )

    if checksum_path.exists():
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, name = line.partition("  ")
            target = evidence_dir / name.strip()
            if not target.exists():
                problems.append(f"checksum entry {name.strip()}: file is missing")
                continue
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != digest.strip():
                problems.append(f"checksum {name.strip()}: recorded={digest.strip()} actual={actual}")
    else:
        problems.append(f"{checksum_path} is missing")

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default=None, help="working directory for the drill")
    parser.add_argument("--evidence-out", default=None, help="write the drill evidence JSON here")
    parser.add_argument("--keep-run-dir", action="store_true", help="do not delete an existing run dir")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--verify-manifest",
        nargs="?",
        const=str(EVIDENCE_DIR),
        default=None,
        metavar="EVIDENCE_DIR",
        help="verify the reviewed evidence manifest against its archived run record and exit",
    )
    args = parser.parse_args(argv)

    if args.verify_manifest is not None:
        evidence_dir = Path(args.verify_manifest)
        problems = verify_manifest(evidence_dir)
        if problems:
            print(f"{TASK_ID} manifest verification FAILED ({len(problems)} problem(s)):", flush=True)
            for problem in problems:
                print(f"  - {problem}", flush=True)
            return 1
        print(f"{TASK_ID} manifest verification OK: {evidence_dir}", flush=True)
        return 0

    correlation_id = uuid.uuid4().hex
    run_dir = Path(args.run_dir) if args.run_dir else Path("/tmp") / f"l12-verify-know-{correlation_id[:12]}"
    if run_dir.exists() and not args.keep_run_dir:
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    recorder = Recorder(verbose=not args.quiet)
    drill = KnowledgeLoopDrill(run_dir=run_dir, correlation_id=correlation_id, recorder=recorder)
    try:
        drill.run()
    except Exception as exc:  # noqa: BLE001 - a harness failure is still a result
        recorder.results.append(
            CheckResult(
                check_id="C00",
                title="drill harness",
                acceptance=(),
                status="failed",
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_seconds=0.0,
                evidence={},
                failure=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}",
            )
        )

    document = drill.evidence_document()
    if args.evidence_out:
        out = Path(args.evidence_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(document, indent=2, sort_keys=True, default=_json_default) + "\n",
            encoding="utf-8",
        )
    summary = document["summary"]
    print(
        f"\n{TASK_ID} drill: {summary['passed']}/{summary['total']} checks passed "
        f"(correlation_id={correlation_id})",
        flush=True,
    )
    for result in recorder.failed:
        print(f"  FAILED {result.check_id} {result.title}: {result.failure}", flush=True)
    return 0 if not recorder.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
