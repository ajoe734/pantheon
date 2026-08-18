"""Deployed Compose E2E for Pantheon research loops 1 through 4.

This suite is deliberately opt-in because it writes unique, task-scoped proof
records through running product APIs.  It never imports a product store or
starts an in-process service.  Run it against an already-started Compose
project with ``PANTHEON_L12_RESEARCH_E2E=1``.

The four cases form one identity chain:

    SourceRecord -> StrategySpec -> ExperimentRun -> TeachingEvaluationResult

On failure the harness writes one bounded report containing the last
successful boundary and the first failed boundary.  It does not repair the
runtime, create development tasks, or continue into later loop cases.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pytest


TASK_ID = "L12-CURRENT-E2E-RESEARCH-20260814"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "twelve-loop-current"
    / "research-loops"
    / "run-report.json"
)
CASE_NAMES = (
    "source_ingestion",
    "strategy_distillation",
    "alpha_replication",
    "persona_teaching",
)
OWNER_SERVICES = {
    "source_ingestion": "source-ingest-scheduler",
    "strategy_distillation": "strategy-distillation-worker",
    "alpha_replication": "alpha-replication-worker",
    "persona_teaching": "training-session-preview-worker",
}
LOCAL_TRAINING_WORKER_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhbGxvd2VkX3RlbmFudHMiOlsiKiJdLCJyb2xlcyI6WyJ0cmFpbmluZy1zZXJ2aWNlIl0s"
    "InNlcnZpY2UiOiJ0cmFpbmluZy1zZXNzaW9uLXByZXZpZXctd29ya2VyIiwic3ViIjoidHJh"
    "aW5pbmctc2Vzc2lvbi1wcmV2aWV3LXdvcmtlciJ9."
    "eb4LoU20NsZEfH8VYjhl1xyOaa37bzzg7yC-D87Uu2g"
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _bounded_text(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value).replace("\x00", "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _source_digest(source: Mapping[str, Any]) -> str:
    metadata = dict(source.get("metadata") or {})
    for volatile_key in ("ingest_run_id", "source_ingest_run_id", "trace_id"):
        metadata.pop(volatile_key, None)
    identity = {
        "source_id": source.get("source_id"),
        "connector_id": source.get("connector_id"),
        "source_type": source.get("source_type"),
        "title": source.get("title"),
        "content_ref": source.get("content_ref"),
        "metadata": metadata,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


class BoundaryFailure(RuntimeError):
    def __init__(self, boundary: str, detail: Any) -> None:
        super().__init__(_bounded_text(detail))
        self.boundary = boundary
        self.detail = _bounded_text(detail)


class DeployedResearchHarness:
    """One fail-fast, report-producing chain over already-running services."""

    def __init__(self) -> None:
        self.started_at = _utc_now()
        self.run_token = uuid.uuid4().hex[:12]
        self.compose_project = os.getenv("PANTHEON_L12_COMPOSE_PROJECT", "pantheon").strip()
        self.timeout_seconds = float(os.getenv("PANTHEON_L12_POLL_TIMEOUT_SECONDS", "180"))
        self.poll_seconds = float(os.getenv("PANTHEON_L12_POLL_INTERVAL_SECONDS", "2"))
        self.source_url = os.getenv("PANTHEON_L12_SOURCE_URL", "http://127.0.0.1:18097").rstrip("/")
        self.registry_url = os.getenv("PANTHEON_L12_REGISTRY_URL", "http://127.0.0.1:18087").rstrip("/")
        self.research_url = os.getenv("PANTHEON_L12_RESEARCH_URL", "http://127.0.0.1:18101").rstrip("/")
        self.training_url = os.getenv("PANTHEON_L12_TRAINING_URL", "http://127.0.0.1:18099").rstrip("/")
        self.tenant_id = os.getenv("PANTHEON_L12_TENANT_ID", "default").strip()
        self.report_path = Path(os.getenv("PANTHEON_L12_REPORT_PATH", str(DEFAULT_REPORT_PATH)))
        self.git_sha = self._command(["git", "rev-parse", "HEAD"]).strip()
        self.case_results: list[dict[str, Any]] = []
        self.first_failure: dict[str, Any] | None = None
        self.failure_reported = False
        self.chain: dict[str, Any] = {}
        self._current_case: dict[str, Any] | None = None
        self.source_controller_token = os.getenv(
            "PANTHEON_L12_SOURCE_INGEST_CONTROLLER_TOKEN", ""
        ).strip()
        self._write_report("running")

    def _source_ingest_headers(self) -> dict[str, str]:
        if not self.source_controller_token:
            return {}
        return {"Authorization": f"Bearer {self.source_controller_token}"}

    def _command(self, argv: Sequence[str]) -> str:
        completed = subprocess.run(
            list(argv),
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
            raise RuntimeError(f"{argv[0]} exited {completed.returncode}: {_bounded_text(detail)}")
        return completed.stdout

    def _compose_argv(self, *args: str) -> list[str]:
        argv = ["docker", "compose", "-p", self.compose_project]
        compose_file = os.getenv("PANTHEON_L12_COMPOSE_FILE", "").strip()
        if compose_file:
            argv.extend(["-f", compose_file])
        argv.extend(args)
        return argv

    def _service_identity_snapshot(self, service: str) -> dict[str, Any]:
        output = self._command(self._compose_argv("ps", "-q", service))
        container_ids = [line.strip() for line in output.splitlines() if line.strip()]
        if len(container_ids) != 1:
            raise RuntimeError(
                f"expected exactly one Compose owner for {service}, found {len(container_ids)}"
            )
        container_id = container_ids[0]
        inspected = json.loads(self._command(["docker", "inspect", container_id]))
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise RuntimeError(f"docker inspect returned no unique record for {service}")
        record = inspected[0]
        state = record.get("State") or {}
        labels = (record.get("Config") or {}).get("Labels") or {}
        status = str(state.get("Status") or "")
        health = str((state.get("Health") or {}).get("Status") or "not_declared")
        if labels.get("com.docker.compose.service") != service:
            raise RuntimeError(f"container label does not identify Compose service {service}")
        image_id = str(record.get("Image") or "")
        image_revision = ""
        if image_id:
            image_records = json.loads(self._command(["docker", "image", "inspect", image_id]))
            if isinstance(image_records, list) and image_records:
                image_labels = (image_records[0].get("Config") or {}).get("Labels") or {}
                image_revision = str(image_labels.get("org.opencontainers.image.revision") or "")
        return {
            "compose_service": service,
            "container_id": str(record.get("Id") or container_id),
            "image_id": image_id,
            "image_revision": image_revision or None,
            "state": status,
            "health": health,
            "restart_count": int(record.get("RestartCount") or 0),
        }

    def _service_identity(self, service: str) -> dict[str, Any]:
        deadline = time.monotonic() + min(self.timeout_seconds, 120)
        last_identity: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_identity = self._service_identity_snapshot(service)
            if last_identity["state"] == "running" and last_identity["health"] in {
                "healthy",
                "not_declared",
            }:
                return last_identity
            if (
                last_identity["state"] in {"restarting", "exited", "dead"}
                or last_identity["health"] == "unhealthy"
                or int(last_identity["restart_count"]) >= 2
            ):
                break
            time.sleep(self.poll_seconds)
        raise RuntimeError(
            f"Compose owner {service} did not reach a stable healthy identity: "
            f"{_canonical_json(last_identity)}"
        )

    def _http_json(
        self,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: Sequence[int] = (200,),
    ) -> Any:
        body = _canonical_json(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status = response.status
                # source.authority_actual_state reads the full controller
                # readback (connector registry, schedule, DLQ, and record
                # counts), which grows with the shared default compose
                # environment's accumulated history and has already exceeded
                # 1 MB. A too-small cap here does not fail loudly; it
                # silently truncates mid-object and json.loads() reports the
                # truncated body as "non-JSON content", which reads as an
                # API defect rather than the harness under-reading its own
                # response.
                raw = response.read(16_777_216).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read(4_096).decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{method} {path} connection failed: {exc.reason}") from exc
        if status not in expected:
            raise RuntimeError(
                f"{method} {path} returned HTTP {status}; expected {list(expected)}; "
                f"body={_bounded_text(raw)}"
            )
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{method} {path} returned non-JSON content") from exc

    def _poll(self, description: str, read: Callable[[], Any], accept: Callable[[Any], bool]) -> Any:
        deadline = time.monotonic() + self.timeout_seconds
        last_detail = "no readback"
        while time.monotonic() < deadline:
            try:
                value = read()
                if accept(value):
                    return value
                last_detail = _bounded_text(value)
            except RuntimeError as exc:
                last_detail = _bounded_text(exc)
            time.sleep(self.poll_seconds)
        raise RuntimeError(f"timed out waiting for {description}; last={last_detail}")

    def _at(self, boundary: str, action: Callable[[], Any]) -> Any:
        if self._current_case is None:
            raise RuntimeError("boundary executed outside a loop case")
        try:
            result = action()
        except BoundaryFailure:
            raise
        except Exception as exc:
            raise BoundaryFailure(boundary, exc) from exc
        self._current_case["last_successful_boundary"] = boundary
        self._current_case.setdefault("successful_boundaries", []).append(boundary)
        self._write_report("running")
        return result

    @staticmethod
    def _require(condition: Any, detail: str) -> None:
        if not condition:
            raise RuntimeError(detail)

    @staticmethod
    def _entry(view: Mapping[str, Any]) -> Mapping[str, Any]:
        entry = view.get("entry")
        return entry if isinstance(entry, Mapping) else view

    @staticmethod
    def _source_record(view: Mapping[str, Any]) -> Mapping[str, Any]:
        record = view.get("source_record")
        return record if isinstance(record, Mapping) else view

    def _training_headers(self) -> dict[str, str]:
        token = os.getenv("PANTHEON_L12_TRAINING_TOKEN", LOCAL_TRAINING_WORKER_TOKEN).strip()
        if not token:
            raise RuntimeError("PANTHEON_L12_TRAINING_TOKEN is empty")
        return {
            "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Service": "training-session-preview-worker",
        }

    def _report_payload(self, status: str) -> dict[str, Any]:
        return {
            "schema_version": "pantheon.l12.research-loops.deployed-e2e.v1",
            "task_id": TASK_ID,
            "status": status,
            "started_at": self.started_at,
            "completed_at": _utc_now() if status in {"passed", "failed"} else None,
            "git_sha": self.git_sha,
            "compose_project": self.compose_project,
            "anti_fixture": {
                "product_store_imports": False,
                "in_process_service_client": False,
                "temporary_authority_store": False,
                "automatic_repair": False,
            },
            "cases": self.case_results,
            "active_case": self._current_case,
            "first_failure": self.first_failure,
        }

    def _write_report(self, status: str) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._report_payload(status)
        temporary = self.report_path.with_suffix(self.report_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.report_path)

    def run_case(self, target_index: int) -> None:
        while len(self.case_results) < target_index and self.first_failure is None:
            index = len(self.case_results) + 1
            self._execute_case(index)
        if self.first_failure is not None:
            message = (
                f"first deployed boundary failed: {self.first_failure['loop_id']} / "
                f"{self.first_failure['boundary']}: {self.first_failure['detail']}"
            )
            if not self.failure_reported:
                self.failure_reported = True
                pytest.fail(message, pytrace=False)
            pytest.skip(f"not reached after {message}")
        if target_index == len(CASE_NAMES):
            self._write_report("passed")

    def _execute_case(self, index: int) -> None:
        loop_id = CASE_NAMES[index - 1]
        case = {
            "case_index": index,
            "loop_id": loop_id,
            "started_at": _utc_now(),
            "status": "running",
            "trigger": None,
            "owner": None,
            "terminal_output": None,
            "authority_readback": None,
            "next_consumer": None,
            "successful_boundaries": [],
            "last_successful_boundary": None,
        }
        self._current_case = case
        try:
            getattr(self, f"_case_{loop_id}")(case)
        except BoundaryFailure as exc:
            case["status"] = "failed"
            case["completed_at"] = _utc_now()
            case["first_failed_boundary"] = exc.boundary
            self.case_results.append(case)
            self.first_failure = {
                "loop_id": loop_id,
                "case_index": index,
                "boundary": exc.boundary,
                "detail": exc.detail,
                "last_successful_boundary": case.get("last_successful_boundary"),
            }
            self._current_case = None
            self._write_report("failed")
            return
        case["status"] = "passed"
        case["completed_at"] = _utc_now()
        self.case_results.append(case)
        self._current_case = None
        self._write_report("passed" if index == len(CASE_NAMES) else "running")

    def _case_source_ingestion(self, case: dict[str, Any]) -> None:
        source_id = f"src-l12-e2e-{self.run_token}"
        connector_id = f"conn-l12-e2e-{self.run_token}"
        trace_id = f"trace-l12-e2e-{self.run_token}"
        owner = self._at(
            "source.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["source_ingestion"]),
        )
        case["owner"] = owner

        def preflight() -> None:
            self._http_json(
                self.source_url,
                f"/api/source-ingest/source-records/{urllib.parse.quote(source_id, safe='')}",
                expected=(404,),
            )

        self._at("source.anti_preseed_readback", preflight)
        available_time = _utc_now()
        connector_payload = {
            "connector": {
                "connector_id": connector_id,
                "source_type": "internal_note",
                "provider": "L12 deployed E2E controlled source",
                "license_scope": "internal",
                "metadata": {"e2e_task_id": TASK_ID, "e2e_run_token": self.run_token},
            },
            "fetch": {
                "mode": "static_records",
                "next_watermark": available_time,
                "records": [
                    {
                        "source_id": source_id,
                        "title": "Deployed E2E Taiwan equity mean-reversion hypothesis",
                        "content_ref": f"urn:pantheon:l12-e2e:{self.run_token}",
                        "trace_id": trace_id,
                        "metadata": {
                            "body": (
                                "Controlled research evidence for the deployed owner chain. "
                                f"run_token={self.run_token}"
                            ),
                            "access_scope": ["research"],
                            "license_scope": "internal",
                            "available_time": available_time,
                            "strategy_seed": {
                                "hypothesis": "Mean reversion in liquid Taiwan equity pairs",
                                "asset_class": ["equity"],
                                "market_scope": ["Taiwan"],
                                "holding_period": "1 day",
                                "required_data": ["OHLCV"],
                                "backend_hint": "qlib",
                                "feature_hints": ["zscore_spread"],
                                "label_hints": ["return_1d"],
                                "risk_notes": ["research_only"],
                            },
                        },
                    }
                ],
            },
        }
        configured = self._at(
            "source.connector_command",
            lambda: self._http_json(
                self.source_url,
                "/api/source-ingest/connectors",
                method="POST",
                payload=connector_payload,
                expected=(201,),
            ),
        )
        self._require(isinstance(configured, Mapping), "connector command did not return an object")
        scheduled = self._at(
            "source.scheduled_trigger",
            lambda: self._http_json(
                self.source_url,
                f"/api/source-ingest/connectors/{urllib.parse.quote(connector_id, safe='')}/schedule",
                method="PUT",
                payload={"interval_seconds": 1, "enabled": True},
            ),
        )
        schedule = scheduled.get("schedule") if isinstance(scheduled, Mapping) else None
        self._require(
            isinstance(schedule, Mapping) and schedule.get("connector_id") == connector_id,
            "schedule command did not preserve connector identity",
        )
        case["trigger"] = {
            "type": "connector_schedule",
            "id": connector_id,
            "trace_id": trace_id,
        }

        self._at(
            "source.job_trigger",
            lambda: self._http_json(
                self.source_url,
                "/api/source-ingest/jobs",
                method="POST",
                headers=self._source_ingest_headers(),
                payload={
                    "connector_id": connector_id,
                    "trace_id": trace_id,
                    "trigger_type": "scheduled",
                },
                expected=(201,),
            ),
        )

        source = self._source_record(
            self._at(
                "source.durable_terminal_readback",
                lambda: self._poll(
                    source_id,
                    lambda: self._http_json(
                        self.source_url,
                        f"/api/source-ingest/source-records/{urllib.parse.quote(source_id, safe='')}",
                        expected=(200, 404),
                    ),
                    lambda value: (
                        isinstance(value, Mapping)
                        and self._source_record(value).get("source_id") == source_id
                    ),
                ),
            )
        )
        digest = _source_digest(source)
        controller_readback = self._at(
            "source.authority_actual_state",
            lambda: self._http_json(self.source_url, "/api/source-ingest/controller/readback"),
        )
        connector_rows = controller_readback.get("connectors") if isinstance(controller_readback, Mapping) else None
        connector_row = next(
            (
                row
                for row in (connector_rows or [])
                if isinstance(row, Mapping) and row.get("connector_id") == connector_id
            ),
            None,
        )
        latest = connector_row.get("latest_source_record") if isinstance(connector_row, Mapping) else None
        self._require(
            isinstance(latest, Mapping) and latest.get("source_id") == source_id,
            "source controller actual-state readback lacks the exact SourceRecord identity",
        )
        case["terminal_output"] = {"type": "SourceRecord", "id": source_id, "digest": digest}
        case["authority_readback"] = {
            "schema_version": controller_readback.get("schema_version"),
            "source_id": latest.get("source_id"),
            "connector_id": connector_id,
        }
        registry_id = f"reg-strategy-spec-{source_id}-{digest.removeprefix('sha256:')[:12]}"
        distill_owner = self._at(
            "source.next_consumer_owner_identity",
            lambda: self._service_identity(OWNER_SERVICES["strategy_distillation"]),
        )
        registry_view = self._at(
            "source.next_consumer_receipt",
            lambda: self._poll(
                registry_id,
                lambda: self._http_json(
                    self.registry_url,
                    f"/api/registry/strategy-specs/{urllib.parse.quote(registry_id, safe='')}",
                    expected=(200, 404),
                ),
                lambda value: (
                    isinstance(value, Mapping)
                    and self._entry(value).get("registry_id") == registry_id
                ),
            ),
        )
        registry_entry = self._entry(registry_view)
        distillation = (registry_entry.get("metadata") or {}).get("distillation") or {}
        self._require(
            distillation.get("source_id") == source_id and distillation.get("source_digest") == digest,
            "Strategy Distillation receipt does not bind the exact SourceRecord version",
        )
        case["next_consumer"] = {
            "compose_identity": distill_owner,
            "receipt_type": "DistillationJob",
            "receipt_id": distillation.get("distillation_job_id"),
            "source_id": source_id,
        }
        self.chain.update(
            {
                "source": source,
                "source_digest": digest,
                "registry_view": registry_view,
                "registry_id": registry_id,
                "strategy_id": registry_entry.get("strategy_id"),
            }
        )

    def _case_strategy_distillation(self, case: dict[str, Any]) -> None:
        registry_id = str(self.chain["registry_id"])
        strategy_id = str(self.chain["strategy_id"])
        source_id = str(self.chain["source"]["source_id"])
        owner = self._at(
            "distill.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["strategy_distillation"]),
        )
        case["owner"] = owner
        case["trigger"] = {
            "type": "SourceRecord.normalized",
            "id": source_id,
            "digest": self.chain["source_digest"],
        }
        registry_view = self._at(
            "distill.durable_terminal_readback",
            lambda: self._http_json(
                self.registry_url,
                f"/api/registry/strategy-specs/{urllib.parse.quote(registry_id, safe='')}",
            ),
        )
        entry = self._entry(registry_view)
        self._require(entry.get("registry_id") == registry_id, "Registry readback identity drifted")
        self._require(entry.get("strategy_id") == strategy_id, "Registry strategy identity drifted")
        self._require(entry.get("artifact_state") == "draft", "distilled StrategySpec is not a draft")
        self._require(bool(entry.get("checksum")), "distilled StrategySpec lacks checksum")
        case["terminal_output"] = {
            "type": "StrategySpec",
            "id": registry_id,
            "strategy_id": strategy_id,
            "version": entry.get("version"),
            "checksum": entry.get("checksum"),
        }
        case["authority_readback"] = {
            "registry_id": entry.get("registry_id"),
            "artifact_state": entry.get("artifact_state"),
            "checksum": entry.get("checksum"),
        }

        def approve() -> Mapping[str, Any]:
            for target in ("candidate", "approved"):
                payload: dict[str, Any] = {
                    "target_state": target,
                    "approver": f"l12-e2e-reviewer-{self.run_token}",
                }
                if target == "approved":
                    payload["approval_decision_id"] = f"approval-l12-e2e-{self.run_token}"
                view = self._http_json(
                    self.registry_url,
                    f"/api/registry/strategy-specs/{urllib.parse.quote(registry_id, safe='')}/advance",
                    method="POST",
                    payload=payload,
                )
            return self._entry(view)

        approved = self._at("distill.reviewed_admission_prerequisite", approve)
        self._require(approved.get("artifact_state") == "approved", "StrategySpec approval did not persist")
        alpha_owner = self._at(
            "distill.next_consumer_owner_identity",
            lambda: self._service_identity(OWNER_SERVICES["alpha_replication"]),
        )
        admission_payload = {
            "tenant_id": self.tenant_id,
            "strategy_spec_id": registry_id,
            "strategy_id": strategy_id,
            "spec_version": approved.get("version"),
            "checksum": approved.get("checksum"),
            "approval_decision_id": approved.get("approval_decision_id"),
            "approver": approved.get("approver"),
            "mode": "initial",
        }
        admission = self._at(
            "distill.next_consumer_command",
            lambda: self._http_json(
                self.research_url,
                "/api/research-orchestrator/alpha-replication/admissions",
                method="POST",
                payload=admission_payload,
                expected=(201,),
            ),
        )
        admission_readback = self._at(
            "distill.next_consumer_receipt",
            lambda: self._http_json(
                self.research_url,
                "/api/research-orchestrator/alpha-replication/admissions/"
                + urllib.parse.quote(self.tenant_id, safe="")
                + "/"
                + urllib.parse.quote(registry_id, safe=""),
            ),
        )
        self._require(admission_readback == admission, "admission authority readback changed identity")
        self._require(admission.get("strategy_spec_id") == registry_id, "admission lost StrategySpec identity")
        case["next_consumer"] = {
            "compose_identity": alpha_owner,
            "receipt_type": "ReplicationAdmission",
            "receipt_id": admission.get("admission_id"),
            "strategy_spec_id": admission.get("strategy_spec_id"),
        }
        self.chain.update({"approved_entry": approved, "admission": admission})

    def _case_alpha_replication(self, case: dict[str, Any]) -> None:
        registry_id = str(self.chain["registry_id"])
        admission = self.chain["admission"]
        owner = self._at(
            "alpha.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["alpha_replication"]),
        )
        case["owner"] = owner
        case["trigger"] = {
            "type": "ReplicationAdmission",
            "id": admission.get("admission_id"),
            "strategy_spec_id": registry_id,
        }

        def find_run() -> Mapping[str, Any] | None:
            runs = self._http_json(self.research_url, "/api/research-orchestrator/runs")
            if not isinstance(runs, list):
                raise RuntimeError("Research runs readback did not return a list")
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                parameters = run.get("parameters") or {}
                experiment_run = parameters.get("experiment_run") if isinstance(parameters, Mapping) else None
                if (
                    isinstance(experiment_run, Mapping)
                    and parameters.get("record_type") == "ExperimentRun"
                    and parameters.get("strategy_spec_id") == registry_id
                    and experiment_run.get("strategy_spec_id") == registry_id
                ):
                    return run
            return None

        authority_run = self._at(
            "alpha.durable_terminal_readback",
            lambda: self._poll(
                f"ExperimentRun for {registry_id}",
                find_run,
                lambda value: isinstance(value, Mapping),
            ),
        )
        authority_run_id = str(authority_run.get("run_id") or "")
        exact_run = self._at(
            "alpha.authority_actual_state",
            lambda: self._http_json(
                self.research_url,
                f"/api/research-orchestrator/runs/{urllib.parse.quote(authority_run_id, safe='')}",
            ),
        )
        self._require(exact_run == authority_run, "Research authority run readback changed")
        experiment_run = (exact_run.get("parameters") or {}).get("experiment_run") or {}
        self._require(experiment_run.get("status") == "completed", "Alpha ExperimentRun is not completed")
        experiment_run_id = str(experiment_run.get("run_id") or "")
        self._require(bool(experiment_run_id), "Alpha authority output lacks domain ExperimentRun id")
        case["terminal_output"] = {
            "type": "ExperimentRun",
            "id": experiment_run_id,
            "authority_run_id": authority_run_id,
            "strategy_spec_id": registry_id,
        }
        case["authority_readback"] = {
            "authority_run_id": exact_run.get("run_id"),
            "domain_run_id": experiment_run.get("run_id"),
            "status": experiment_run.get("status"),
        }

        teaching_owner = self._at(
            "alpha.next_consumer_owner_identity",
            lambda: self._service_identity(OWNER_SERVICES["persona_teaching"]),
        )
        headers = self._training_headers()
        session = self._at(
            "alpha.next_consumer_command",
            lambda: self._http_json(
                self.training_url,
                "/api/training/sessions",
                method="POST",
                payload={
                    "persona_id": f"persona-l12-e2e-{self.run_token}",
                    "objective": "Evaluate controls against the admitted Alpha replication result",
                    "mode": "evaluation",
                    "actor_id": "l12-deployed-e2e",
                    "trace_id": f"trace-teaching-{self.run_token}",
                    "context_refs": [
                        {
                            "type": "experiment_run",
                            "id": authority_run_id,
                            "domain_run_id": experiment_run_id,
                            "strategy_spec_id": registry_id,
                        }
                    ],
                },
                headers=headers,
                expected=(201,),
            ),
        )
        session_id = str(session.get("session_id") or "")
        session_readback = self._at(
            "alpha.next_consumer_receipt",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}",
                headers=headers,
            ),
        )
        context_refs = session_readback.get("context_refs") or []
        self._require(
            any(
                isinstance(ref, Mapping)
                and ref.get("id") == authority_run_id
                and ref.get("domain_run_id") == experiment_run_id
                for ref in context_refs
            ),
            "Teaching consumer did not persist the exact Alpha authority/domain identities",
        )
        case["next_consumer"] = {
            "compose_identity": teaching_owner,
            "receipt_type": "TeachingSession",
            "receipt_id": session_id,
            "experiment_run_id": experiment_run_id,
        }
        self.chain.update({"authority_run": exact_run, "experiment_run": experiment_run, "session": session})

    def _case_persona_teaching(self, case: dict[str, Any]) -> None:
        session_id = str(self.chain["session"]["session_id"])
        owner = self._at(
            "teaching.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["persona_teaching"]),
        )
        case["owner"] = owner
        case["trigger"] = {"type": "TeachingSession", "id": session_id}
        headers = self._training_headers()
        controls_record = self._at(
            "teaching.governed_controls_readback",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/controls/{urllib.parse.quote(session_id, safe='')}",
                headers=headers,
            ),
        )
        controls = controls_record.get("controls") if isinstance(controls_record, Mapping) else None
        by_key = {
            str(control.get("parameter_key")): control
            for control in (controls or [])
            if isinstance(control, Mapping) and control.get("parameter_key")
        }
        self._require(
            {"short_window", "long_window"}.issubset(by_key),
            "new TeachingSession has no public, governed short_window/long_window controls to command",
        )
        patched = self._at(
            "teaching.command",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/controls",
                method="PATCH",
                payload={
                    "patches": [
                        {"parameter_key": "short_window", "proposed_value": 7},
                        {"parameter_key": "long_window", "proposed_value": 21},
                    ]
                },
                headers=headers,
            ),
        )
        self._require(patched.get("status") == "accepted", "Teaching control command was rejected")
        queued = self._at(
            "teaching.worker_trigger",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/preview-jobs",
                method="POST",
                payload={
                    "mode": "refresh",
                    "requested_by": "l12-deployed-e2e",
                    "terminalize_session": True,
                },
                headers={**headers, "Idempotency-Key": f"l12-e2e-{self.run_token}"},
                expected=(201,),
            ),
        )
        job_id = str(queued.get("job_id") or "")
        self._require(bool(job_id), "Teaching preview queue did not return job_id")
        case["trigger"] = {"type": "PreviewEvaluationJob", "id": job_id, "session_id": session_id}

        def read_job() -> Any:
            return self._http_json(
                self.training_url,
                f"/api/training/preview-jobs/{urllib.parse.quote(job_id, safe='')}",
                headers=headers,
            )

        job = self._at(
            "teaching.durable_terminal_output",
            lambda: self._poll(
                job_id,
                read_job,
                lambda value: isinstance(value, Mapping)
                and value.get("status") in {"completed", "failed"},
            ),
        )
        self._require(job.get("status") == "completed", f"Teaching evaluation job failed: {job.get('error_code')}")
        terminal = self._at(
            "teaching.authority_actual_state",
            lambda: self._poll(
                f"terminal TeachingSession {session_id}",
                lambda: self._http_json(
                    self.training_url,
                    f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/terminal-readback",
                    headers=headers,
                ),
                lambda value: (
                    isinstance(value, Mapping)
                    and value.get("status") == "completed"
                    and bool(value.get("eval_identity"))
                ),
            ),
        )
        evaluation = terminal.get("evaluation_result") or {}
        eval_identity = str(terminal.get("eval_identity") or "")
        self._require(evaluation.get("eval_identity") == eval_identity, "Teaching evaluation identity drifted")
        case["terminal_output"] = {
            "type": "TeachingEvaluationResult",
            "id": eval_identity,
            "session_id": session_id,
            "job_id": job_id,
        }
        case["authority_readback"] = {
            "session_id": terminal.get("session_id"),
            "status": terminal.get("status"),
            "eval_identity": terminal.get("eval_identity"),
        }
        verifier_readback = self._at(
            "teaching.next_consumer_readback",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/terminal-readback",
                headers=headers,
            ),
        )
        self._require(
            verifier_readback.get("eval_identity") == eval_identity,
            "operator/Learning verifier did not read the exact Teaching evaluation identity",
        )
        case["next_consumer"] = {
            "consumer": "operator-learning-verifier",
            "receipt_type": "TeachingTerminalReadback",
            "receipt_id": eval_identity,
            "session_id": session_id,
        }


@pytest.fixture(scope="session")
def deployed_research_e2e() -> DeployedResearchHarness:
    if os.getenv("PANTHEON_L12_RESEARCH_E2E") != "1":
        pytest.skip("set PANTHEON_L12_RESEARCH_E2E=1 to exercise running Compose services")
    return DeployedResearchHarness()


def test_deployed_source_ingestion_identity_chain(
    deployed_research_e2e: DeployedResearchHarness,
) -> None:
    deployed_research_e2e.run_case(1)


def test_deployed_strategy_distillation_identity_chain(
    deployed_research_e2e: DeployedResearchHarness,
) -> None:
    deployed_research_e2e.run_case(2)


def test_deployed_alpha_replication_identity_chain(
    deployed_research_e2e: DeployedResearchHarness,
) -> None:
    deployed_research_e2e.run_case(3)


def test_deployed_persona_teaching_identity_chain(
    deployed_research_e2e: DeployedResearchHarness,
) -> None:
    deployed_research_e2e.run_case(4)


def test_deployed_suite_has_no_fixture_or_product_store_shortcut() -> None:
    """AST guard: keep deployed evidence out of unit-test and private-store paths."""

    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    imported_names: list[str] = []
    function_names: list[str] = []
    fixture_arguments: list[str] = []
    called_names: list[str] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_names.append(node.name)
            fixture_arguments.extend(argument.arg for argument in node.args.args)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.append(node.func.attr)

    assert not any(name == "tempfile" or name.startswith("services.") for name in imported_modules)
    assert not any(name == "unittest.mock" or name.startswith("fastapi.testclient") for name in imported_modules)
    assert "TestClient" not in imported_names
    assert not {"tmp_path", "tmpdir", "monkeypatch"}.intersection(fixture_arguments)
    assert not {"Mock", "MagicMock", "patch", "TestClient"}.intersection(called_names)
    assert [name for name in function_names if name.startswith("test_deployed_")] == [
        "test_deployed_source_ingestion_identity_chain",
        "test_deployed_strategy_distillation_identity_chain",
        "test_deployed_alpha_replication_identity_chain",
        "test_deployed_persona_teaching_identity_chain",
        "test_deployed_suite_has_no_fixture_or_product_store_shortcut",
    ]
    assert "subprocess.run" in inspect.getsource(DeployedResearchHarness._command)
    assert "docker" in inspect.getsource(DeployedResearchHarness._compose_argv)
