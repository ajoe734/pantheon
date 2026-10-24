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
import math
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
from l12_owner_auth import (
    HostedPause,
    approve_registry_entry,
    bearer,
    compose_file_args,
    hosted_chain_enabled,
    hosted_receipt_fields,
    human_token,
    load_hosted_input,
    persona_create_identity,
    persona_writer_identity,
    registry_advance_body,
    require_decided_approval,
    token_subject,
)


TASK_ID = "L12-GAP-F07-E2E-RESEARCH-20260818"
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
    "source_ingestion": "source-ingest",
    "strategy_distillation": "strategy-distillation-worker",
    "alpha_replication": "alpha-replication-worker",
    "persona_teaching": "training-session-preview-worker",
}


# The default isolated stimulus keeps its pairs mean-reversion hypothesis: research-only and honestly
# not executable. The hosted chain explicitly names the one supported executable stimulus instead:
# close-to-close momentum with a complete typed recipe that Source carries verbatim into the Spec.
# Nothing here parses prose, invents a value, or claims the pairs research executes.
HOSTED_EXECUTION_DATA_SOURCE = "source-ingest:normalized/taiwanstockprice/finmind-daily"
HOSTED_EXECUTION_RECIPE: dict[str, Any] = {
    "kind": "close_to_close_momentum",
    "symbols": ["2330.TW"],
    "bar_frequency": "1d",
    "data_source": HOSTED_EXECUTION_DATA_SOURCE,
    "lookback_bars": 2,
    "momentum_threshold": 0.01,
    "positive_action": "BUY",
    "non_positive_action": "SELL",
    "order_quantity": 1,
    "quantity_type": "SHARES",
}


def _strategy_seed(hosted: bool) -> dict[str, Any]:
    if hosted:
        return {
            "hypothesis": "Close-to-close momentum in a liquid Taiwan equity",
            "asset_class": ["equity"],
            "market_scope": ["Taiwan"],
            "holding_period": "1 day",
            "required_data": [HOSTED_EXECUTION_DATA_SOURCE],
            "backend_hint": "qlib",
            "feature_hints": ["close_to_close_momentum"],
            "label_hints": ["return_1d"],
            "risk_notes": ["paper_only"],
            "execution_recipe": dict(HOSTED_EXECUTION_RECIPE),
        }
    return {
        "hypothesis": "Mean reversion in liquid Taiwan equity pairs",
        "asset_class": ["equity"],
        "market_scope": ["Taiwan"],
        "holding_period": "1 day",
        "required_data": ["OHLCV"],
        "backend_hint": "qlib",
        "feature_hints": ["zscore_spread"],
        "label_hints": ["return_1d"],
        "risk_notes": ["research_only"],
    }


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
        self.persona_url = os.getenv("PANTHEON_L12_PERSONA_URL", "http://127.0.0.1:18002").rstrip("/")
        self.governance_url = os.getenv("PANTHEON_L12_GOVERNANCE_URL", "http://127.0.0.1:18082").rstrip("/")
        self.tenant_id = os.getenv("PANTHEON_L12_TENANT_ID", "default").strip()
        self.report_path = Path(os.getenv("PANTHEON_L12_REPORT_PATH", str(DEFAULT_REPORT_PATH)))
        # Hosted chain mode: one phase per process, deterministic owner IDs derived
        # from the stable chain id so a later process reads back instead of re-creating.
        self.hosted = hosted_chain_enabled()
        self.hosted_input = load_hosted_input(("A_research",)) if self.hosted else None
        self.paused: dict[str, Any] | None = None
        if self.hosted_input is not None:
            self.run_token = self.hosted_input.chain_id
        self.git_sha = self._command(["git", "rev-parse", "HEAD"]).strip()
        self.case_results: list[dict[str, Any]] = []
        self.first_failure: dict[str, Any] | None = None
        self.failure_reported = False
        self.chain: dict[str, Any] = {}
        self._current_case: dict[str, Any] | None = None
        self.source_controller_token = os.getenv(
            "PANTHEON_L12_SOURCE_INGEST_CONTROLLER_TOKEN", ""
        ).strip()
        # Source-record reads require a runtime reader token since 2026-10-04.
        self.source_reader_token = os.getenv("PANTHEON_L12_SOURCE_READER_TOKEN", "").strip()
        self.source_reader_tenant = os.getenv("PANTHEON_L12_SOURCE_READER_TENANT_ID", self.tenant_id).strip()
        self._write_report("running")

    def _rid(self, default: str, *parts: str) -> str:
        """Owner ID: the isolated per-run ID, or the chain-deterministic hosted ID."""
        return self.hosted_input.id(*parts) if self.hosted_input is not None else default

    def _chain_labels(self) -> dict[str, Any]:
        return self.hosted_input.labels() if self.hosted_input is not None else {}

    def _governance_read(self, decision_id: str) -> Mapping[str, Any] | None:
        view = self._http_json(
            self.governance_url,
            f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}",
            headers=self._registry_headers(),
            expected=(200, 404),
        )
        return view if isinstance(view, Mapping) and view.get("decision_id") == decision_id else None

    def _hosted_resource_ids(self) -> dict[str, Any]:
        chain = self.chain
        source = chain.get("source") or {}
        return {
            "source_id": source.get("source_id"),
            "source_digest": chain.get("source_digest"),
            "distillation_job_id": (chain.get("distillation") or {}).get("distillation_job_id"),
            "distillation_source_id": (chain.get("distillation") or {}).get("source_id"),
            "distillation_source_digest": (chain.get("distillation") or {}).get("source_digest"),
            "registry_id": chain.get("registry_id"),
            "strategy_id": chain.get("strategy_id"),
            "checksum": chain.get("checksum"),
            "h1_decision_id": chain.get("h1_decision_id"),
            "admission_id": (chain.get("admission") or {}).get("admission_id"),
            "authority_run_id": (chain.get("authority_run") or {}).get("run_id"),
            "experiment_run_id": (chain.get("experiment_run") or {}).get("run_id"),
            "teaching_session_id": (chain.get("session") or {}).get("session_id"),
            "persona_id": chain.get("persona_id") or (chain.get("session") or {}).get("persona_id"),
            "eval_identity": chain.get("eval_identity"),
            "pre_edit_eval_id": chain.get("pre_edit_eval_id"),
            "pre_edit_evaluated_at": chain.get("pre_edit_evaluated_at"),
            "eval_id": chain.get("eval_id"),
        }

    def _source_reader_headers(self) -> dict[str, str]:
        if not self.source_reader_token:
            return {}
        return {
            "Authorization": f"Bearer {self.source_reader_token}",
            "X-Tenant-Id": self.source_reader_tenant,
        }

    @staticmethod
    def _registry_headers() -> dict[str, str]:
        return bearer(human_token("OPERATOR"))

    def _governance_post(
        self, path: str, payload: Mapping[str, Any], headers: Mapping[str, str], expected: Sequence[int]
    ) -> Mapping[str, Any]:
        return self._http_json(
            self.governance_url, path, method="POST", payload=payload, headers=headers, expected=expected
        )

    def _source_ingest_headers(self) -> dict[str, str]:
        if self.source_controller_token:
            return {"Authorization": f"Bearer {self.source_controller_token}"}
        try:
            from tests.integration.l12.l12_owner_auth import bearer, human_token
            return bearer(human_token("OPERATOR"))
        except Exception:
            return {}

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
        argv.extend(compose_file_args())
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
        except (BoundaryFailure, HostedPause):
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
        # The isolated harness passes the issuer-issued preview-worker principal;
        # there is no published fixture token to fall back to.
        token = os.getenv("PANTHEON_L12_TRAINING_TOKEN", "").strip()
        if not token:
            raise RuntimeError("PANTHEON_L12_TRAINING_TOKEN is unset or empty")
        return {
            "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Service": "training-session-preview-worker",
        }

    def _operator_training_headers(self) -> dict[str, str]:
        # Control commands are human authority: the issuer-authenticated OPERATOR token
        # through the authorized Training gateway binding, never the preview-worker service.
        return {
            **bearer(human_token("OPERATOR")),
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Service": "control-plane-bff",
        }

    @staticmethod
    def _hosted_persona_id(strategy_id: str) -> str:
        return f"persona-{strategy_id}"

    def _persona_headers(self) -> dict[str, str]:
        writer = persona_writer_identity()
        headers = {"Authorization": writer[0]} if writer else bearer(human_token("OPERATOR"))
        headers["X-Tenant-Id"] = self.tenant_id
        return headers

    def _ensure_persona(self, persona_id: str, *, name: str, mandate: str) -> None:
        headers = self._persona_headers()
        existing = self._http_json(
            self.persona_url,
            f"/api/personas/{urllib.parse.quote(persona_id, safe='')}",
            headers=headers,
            expected=(200, 404),
        )
        if isinstance(existing, Mapping) and existing.get("persona_id") == persona_id:
            self._require(
                existing.get("tenant_id") == self.tenant_id,
                f"Existing persona {persona_id!r} tenant {existing.get('tenant_id')!r} does not match required tenant {self.tenant_id!r}",
            )
            return
        self._http_json(
            self.persona_url,
            "/api/personas",
            method="POST",
            payload={
                **persona_create_identity(),
                "persona_id": persona_id,
                "name": name,
                "mandate": mandate,
                "tenant_id": self.tenant_id,
                "status": "active",
            },
            headers=headers,
            expected=(201, 409),
        )
        readback = self._http_json(
            self.persona_url,
            f"/api/personas/{urllib.parse.quote(persona_id, safe='')}",
            headers=headers,
            expected=(200,),
        )
        self._require(
            isinstance(readback, Mapping)
            and readback.get("persona_id") == persona_id
            and readback.get("tenant_id") == self.tenant_id,
            f"Persona {persona_id!r} verification readback failed closed: tenant or identity mismatch",
        )

    def _report_payload(self, status: str) -> dict[str, Any]:
        payload = self._base_report_payload(status)
        if self.hosted_input is not None:
            if self.paused is not None:
                phase_status, blocked_before, detail = "paused", self.paused["blocked_before"], self.paused["detail"]
            elif self.first_failure is not None:
                phase_status, blocked_before = "failed", self.first_failure.get("boundary")
                detail = self.first_failure.get("detail")
            else:
                phase_status = "completed" if status == "passed" else "running"
                blocked_before, detail = None, None
            if phase_status != "running":
                payload.update(
                    hosted_receipt_fields(
                        self.hosted_input,
                        phase_status=phase_status,
                        blocked_before=blocked_before,
                        resource_ids=self._hosted_resource_ids(),
                        detail=detail,
                    )
                )
        return payload

    def _base_report_payload(self, status: str) -> dict[str, Any]:
        return {
            "schema_version": "pantheon.l12.research-loops.deployed-e2e.v1",
            "task_id": TASK_ID,
            "status": status,
            "started_at": self.started_at,
            "completed_at": _utc_now() if status in {"passed", "failed", "paused"} else None,
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
        while (
            len(self.case_results) < target_index
            and self.first_failure is None
            and self.paused is None
        ):
            index = len(self.case_results) + 1
            self._execute_case(index)
        if self.paused is not None:
            pytest.skip(
                f"hosted chain paused before {self.paused['blocked_before']}: {self.paused['detail']}"
            )
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
        except HostedPause as pause:
            case["status"] = "paused"
            case["completed_at"] = _utc_now()
            case["blocked_before"] = pause.blocked_before
            self.case_results.append(case)
            self.paused = {"loop_id": loop_id, "blocked_before": pause.blocked_before, "detail": pause.detail}
            self._current_case = None
            self._write_report("paused")
            return
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
        source_id = self._rid(f"src-l12-e2e-{self.run_token}", "src")
        connector_id = self._rid(f"conn-l12-e2e-{self.run_token}", "conn")
        trace_id = self._rid(f"trace-l12-e2e-{self.run_token}", "trace")
        owner = self._at(
            "source.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["source_ingestion"]),
        )
        case["owner"] = owner

        def preflight() -> Mapping[str, Any] | None:
            # Hosted: a prior process of this chain may already have ingested the
            # deterministic SourceRecord; read it back instead of ingesting twice.
            view = self._http_json(
                self.source_url,
                f"/api/source-ingest/source-records/{urllib.parse.quote(source_id, safe='')}",
                headers=self._source_reader_headers(),
                expected=(200, 404) if self.hosted else (404,),
            )
            if self.hosted and isinstance(view, Mapping) and (
                self._source_record(view).get("source_id") == source_id
            ):
                return view
            return None

        existing_source = self._at("source.anti_preseed_readback", preflight)
        if existing_source is not None:
            case["trigger"] = {
                "type": "manual_pull_job",
                "id": connector_id,
                "trace_id": trace_id,
                "reused_existing_source_record": True,
            }
        else:
            available_time = _utc_now()
            connector_payload = {
                "connector": {
                    "connector_id": connector_id,
                    "source_type": "internal_note",
                    "provider": "L12 deployed E2E controlled source",
                    "license_scope": "internal",
                    "metadata": {
                            "e2e_task_id": TASK_ID,
                            "e2e_run_token": self.run_token,
                            **self._chain_labels(),
                        },
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
                                # Source evidence reads are tenant-scoped; the record's
                                # tenant claim is what makes it readable at all.
                                "tenant_id": self.tenant_id,
                                **self._chain_labels(),
                                "access_scope": ["research"],
                                "license_scope": "internal",
                                "available_time": available_time,
                                "strategy_seed": _strategy_seed(self.hosted),
                            },
                        }
                    ],
                },
            }
            headers = self._source_ingest_headers()
            configured = self._at(
                "source.connector_command",
                lambda: self._http_json(
                    self.source_url,
                    "/api/source-ingest/connectors",
                    method="POST",
                    payload=connector_payload,
                    headers=headers,
                    expected=(201,),
                ),
            )
            self._require(isinstance(configured, Mapping), "connector command did not return an object")
            job_result = self._at(
                "source.manual_pull_job",
                lambda: self._http_json(
                    self.source_url,
                    "/api/source-ingest/jobs",
                    method="POST",
                    payload={
                        "connector_id": connector_id,
                        "trigger_type": "manual",
                        "trace_id": trace_id,
                    },
                    headers=headers,
                    expected=(201,),
                ),
            )
            job_run = job_result.get("run") if isinstance(job_result, Mapping) else None
            ingest_run_id = job_run.get("ingest_run_id") if isinstance(job_run, Mapping) else None
            self._require(bool(ingest_run_id), "manual pull job did not return ingest_run_id")
            case["trigger"] = {
                "type": "manual_pull_job",
                "id": connector_id,
                "ingest_run_id": ingest_run_id,
                "trace_id": trace_id,
            }

        source = self._source_record(
            self._at(
                "source.durable_terminal_readback",
                lambda: self._poll(
                    source_id,
                    lambda: self._http_json(
                        self.source_url,
                        f"/api/source-ingest/source-records/{urllib.parse.quote(source_id, safe='')}",
                        headers=self._source_reader_headers(),
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
        schedule_info = connector_row.get("schedule") if isinstance(connector_row, Mapping) else None
        self._require(
            schedule_info is None or not schedule_info.get("enabled", False),
            "connector schedule must be null or disabled after manual bounded pull",
        )
        schedule_enabled = schedule_info.get("enabled", False) if isinstance(schedule_info, Mapping) else False
        self._require(
            isinstance(latest, Mapping) and latest.get("source_id") == source_id,
            "source controller actual-state readback lacks the exact SourceRecord identity",
        )
        case["terminal_output"] = {"type": "SourceRecord", "id": source_id, "digest": digest}
        case["authority_readback"] = {
            "schema_version": controller_readback.get("schema_version"),
            "source_id": latest.get("source_id"),
            "connector_id": connector_id,
            "schedule_enabled": schedule_enabled,
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
                    headers=self._registry_headers(),
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
                headers=self._registry_headers(),
            ),
        )
        entry = self._entry(registry_view)
        self._require(entry.get("registry_id") == registry_id, "Registry readback identity drifted")
        self._require(entry.get("strategy_id") == strategy_id, "Registry strategy identity drifted")
        # Hosted: a later process of this chain finds the same entry already advanced by H1.
        allowed_states = ("draft", "candidate", "approved") if self.hosted else ("draft",)
        self._require(
            entry.get("artifact_state") in allowed_states, "distilled StrategySpec is not a draft"
        )
        self._require(bool(entry.get("checksum")), "distilled StrategySpec lacks checksum")
        if self.hosted:
            # The recipe the Source declared must be the one the owner stored in the checksummed Spec.
            stored = (
                (((entry.get("metadata") or {}).get("strategy_spec") or {}).get("metadata") or {})
            ).get("execution_recipe")
            self._require(
                stored == HOSTED_EXECUTION_RECIPE,
                f"owner-read-back StrategySpec execution_recipe {stored!r} differs from the Source's "
                f"explicit recipe {HOSTED_EXECUTION_RECIPE!r}",
            )
            self.chain["execution_recipe_kind"] = stored["kind"]
        self.chain["checksum"] = entry.get("checksum")
        # The Registry keeps no direct chain label: ownership is provable only through the stored
        # distillation lineage, so expose the actual source/digest/job the owner recorded.
        recorded = (entry.get("metadata") or {}).get("distillation") or {}
        self.chain["distillation"] = {
            "source_id": recorded.get("source_id"),
            "source_digest": recorded.get("source_digest"),
            "distillation_job_id": recorded.get("distillation_job_id"),
        }
        if self.hosted_input is not None:
            expected_registry = self.hosted_input.predecessor("registry_id")
            expected_checksum = self.hosted_input.predecessor("checksum")

            def same_predecessor() -> None:
                # A2 must consume the very draft A1 paused on, not a re-distilled one.
                self._require(
                    not expected_registry or expected_registry == registry_id,
                    f"chain predecessor registry_id {expected_registry!r} drifted to {registry_id!r}",
                )
                self._require(
                    not expected_checksum or expected_checksum == entry.get("checksum"),
                    "chain predecessor checksum drifted from the Registry readback",
                )

            self._at("distill.predecessor_identity", same_predecessor)
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
            observed = entry
            if self.hosted_input is not None:
                return approve_hosted(observed)
            for target in ("candidate", "approved"):
                decision_id = None
                if target == "approved":
                    decision_id = approve_registry_entry(
                        self._governance_post,
                        decision_id=f"approval-l12-e2e-{self.run_token}",
                        entry=observed,
                        tenant_id=self.tenant_id,
                        rationale=f"L12 deployed E2E research admission {self.run_token}",
                    ).get("decision_id")
                payload = registry_advance_body(
                    observed, target, command_key=f"l12-e2e-{self.run_token}-{target}",
                    approval_decision_id=decision_id,
                )
                view = self._http_json(
                    self.registry_url,
                    f"/api/registry/strategy-specs/{urllib.parse.quote(registry_id, safe='')}/advance",
                    method="POST",
                    payload=payload,
                    headers=self._registry_headers(),
                )
                observed = self._entry(view)
            return observed

        def approve_hosted(observed: Mapping[str, Any]) -> Mapping[str, Any]:
            """H1: read the human decision back; advance only the exact entry it covers."""
            assert self.hosted_input is not None
            proof = require_decided_approval(
                self._governance_read,
                self.hosted_input.decision("H1"),
                point="H1",
                blocked_before="alpha_admission",
                tenant_id=self.tenant_id,
                target_ids=(registry_id,),
                candidate_digest=str(entry.get("checksum")),
                resource_ids=self._hosted_resource_ids(),
            )
            self.chain["h1_decision_id"] = proof["decision_id"]
            order = ("draft", "candidate", "approved")
            for target in ("candidate", "approved"):
                if order.index(str(observed.get("artifact_state"))) >= order.index(target):
                    continue
                payload = registry_advance_body(
                    observed,
                    target,
                    command_key=f"{self.hosted_input.id('registry', target)}",
                    approval_decision_id=proof["decision_id"] if target == "approved" else None,
                )
                view = self._http_json(
                    self.registry_url,
                    f"/api/registry/strategy-specs/{urllib.parse.quote(registry_id, safe='')}/advance",
                    method="POST",
                    payload=payload,
                    headers=self._registry_headers(),
                )
                observed = self._entry(view)
            self._require(
                observed.get("approval_decision_id") == proof["decision_id"],
                "approved StrategySpec is not bound to the H1 decision",
            )
            return observed

        approved = self._at("distill.reviewed_admission_prerequisite", approve)
        self._require(approved.get("artifact_state") == "approved", "StrategySpec approval did not persist")
        if self.hosted:
            # The Alpha worker's own Teaching call uses persona-{strategy_id}; ensure that exact
            # Persona (after H1, before admission) and keep it as the chain's one Persona id.
            self.chain["persona_id"] = self._hosted_persona_id(strategy_id)
            self._ensure_persona(
                self.chain["persona_id"],
                name=f"L12 Research Persona {self.run_token}",
                mandate="Evaluate controls against the admitted Alpha replication result",
            )
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
        def read_admission() -> Any:
            return self._http_json(
                self.research_url,
                "/api/research-orchestrator/alpha-replication/admissions/"
                + urllib.parse.quote(self.tenant_id, safe="")
                + "/"
                + urllib.parse.quote(registry_id, safe=""),
                expected=(200, 404) if self.hosted else (200,),
            )

        def admit() -> Any:
            if self.hosted:
                prior = read_admission()
                if isinstance(prior, Mapping) and prior.get("strategy_spec_id") == registry_id:
                    return prior
            return self._http_json(
                self.research_url,
                "/api/research-orchestrator/alpha-replication/admissions",
                method="POST",
                payload=admission_payload,
                expected=(201,),
            )

        admission = self._at("distill.next_consumer_command", admit)
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

    @staticmethod
    def _worker_ref(session: Mapping[str, Any]) -> Mapping[str, Any]:
        return next(
            (
                ref
                for ref in session.get("context_refs") or []
                if isinstance(ref, Mapping) and ref.get("type") == "experiment_run"
            ),
            {},
        )

    def _prior_session(
        self, persona_id: str, registry_id: str, trace_id: str | None, headers: Mapping[str, str]
    ) -> Mapping[str, Any] | None:
        for prior in self._http_json(
            self.training_url,
            f"/api/training/sessions?persona_id={urllib.parse.quote(persona_id, safe='')}",
            headers=headers,
        ):
            if not isinstance(prior, Mapping) or prior.get("persona_id") not in (None, persona_id):
                continue
            ref = self._worker_ref(prior)
            if (
                (trace_id is not None and prior.get("trace_id") == trace_id)
                or (
                    ref.get("strategy_spec_id") == registry_id
                    and ref.get("tenant_id") in (None, self.tenant_id)
                    and ref.get("id")
                    and ref.get("domain_run_id")
                )
            ):
                return prior
        return None

    def _wait_worker_session(self) -> Mapping[str, Any]:
        persona_id = str(self.chain["persona_id"])
        registry_id = str(self.chain["registry_id"])
        headers = self._training_headers()
        deadline = time.monotonic() + min(self.timeout_seconds, 60)
        while True:
            prior = self._prior_session(persona_id, registry_id, None, headers)
            if prior is not None:
                return prior
            if time.monotonic() >= deadline:
                raise HostedPause(
                    "teaching_session",
                    "Alpha worker Teaching receipt not visible yet; no alternate Teaching session opened",
                    {"registry_id": registry_id, "persona_id": persona_id},
                )
            time.sleep(self.poll_seconds)

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

        worker_session: Mapping[str, Any] | None = None
        if self.hosted:
            # The Alpha worker's Teaching receipt names the actual completed authority/domain run
            # pair; resolve it by tenant/Persona/Spec refs rather than pinning a historical run.
            worker_session = self._at("alpha.next_consumer_receipt_wait", self._wait_worker_session)
            worker_ref = self._worker_ref(worker_session)
            authority_run_id = str(worker_ref.get("id") or "")
            authority_run = self._at(
                "alpha.durable_terminal_readback",
                lambda: self._http_json(
                    self.research_url,
                    f"/api/research-orchestrator/runs/{urllib.parse.quote(authority_run_id, safe='')}",
                ),
            )
        else:
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
        if worker_session is not None:
            self._require(
                (exact_run.get("parameters") or {}).get("strategy_spec_id") == registry_id
                and self._worker_ref(worker_session).get("domain_run_id") == experiment_run_id,
                "Teaching receipt does not match the completed authority/domain run pair",
            )
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
        persona_id = str(
            self.chain.get("persona_id")
            or self._rid(f"persona-l12-e2e-{self.run_token}", "persona")
        )
        self._ensure_persona(
            persona_id,
            name=f"L12 Research Persona {self.run_token}",
            mandate="Evaluate controls against the admitted Alpha replication result",
        )
        headers = self._training_headers()
        teaching_trace_id = self._rid(f"trace-teaching-{self.run_token}", "trace", "teaching")

        def open_teaching_session() -> Any:
            if worker_session is not None:
                return worker_session
            if self.hosted:
                prior = self._prior_session(persona_id, registry_id, teaching_trace_id, headers)
                if prior is not None:
                    return prior
            return self._http_json(
                self.training_url,
                "/api/training/sessions",
                method="POST",
                payload={
                    "persona_id": persona_id,
                    "objective": "Evaluate controls against the admitted Alpha replication result",
                    "mode": "evaluation",
                    "trace_id": teaching_trace_id,
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
            )

        session = self._at("alpha.next_consumer_command", open_teaching_session)
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

    def _operator_teaching_patch(self, session_id: str, by_key: Mapping[str, Any]) -> dict[str, Any]:
        """Isolated mode only: the harness itself plays the operator and PATCHes the controls."""
        # A recovered session may already hold the usual values from an earlier service-only
        # patch; pick values that differ from the current ones so the edit is a real delta.
        wanted = {
            key: next(v for v in options if v != by_key[key].get("current_value"))
            for key, options in (("short_window", (7, 5)), ("long_window", (21, 30)))
        }
        patched = self._at(
            "teaching.command",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/controls",
                method="PATCH",
                payload={
                    "patches": [
                        {"parameter_key": key, "proposed_value": value} for key, value in wanted.items()
                    ]
                },
                headers=self._operator_training_headers(),
            ),
        )
        self._require(patched.get("status") == "accepted", "Teaching control command was rejected")
        self._require(
            {d.get("parameter_key") for d in patched.get("patch_delta") or [] if d.get("previous_value") != d.get("new_value")}
            == set(wanted),
            "Teaching control command produced no real patch delta",
        )
        return wanted

    def _await_human_teaching_edit(
        self, session_id: str, by_key: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        """Hosted: never write controls. Record the pre-edit evaluation ids, then wait for a human edit.

        Returns the control values the human set (read back from the control_patch event). Only
        scalar owner ids cross segments; B reads the pre-edit evaluation back from Training.
        """
        quoted = urllib.parse.quote(session_id, safe="")
        events = self._http_json(self.training_url, f"/api/training/sessions/{quoted}/events", headers=headers)
        writer = persona_writer_identity()
        executor = token_subject(human_token("OPERATOR"))
        refused = {executor, *([str(writer[1])] if writer and writer[1] else [])}
        control_patches = [
            e for e in (events if isinstance(events, list) else [])
            if isinstance(e, Mapping) and e.get("event_type") == "control_patch" and e.get("patch_delta")
        ]
        patches = [
            e for e in control_patches
            if e.get("actor_type") == "user" and str(e.get("actor") or "") and str(e.get("actor")) not in refused
        ]
        carried = self.hosted_input.predecessors if self.hosted_input else {}
        pre_id = str(carried.get("pre_edit_eval_id") or "").strip()
        pre_at = str(carried.get("pre_edit_evaluated_at") or "").strip()
        if not pre_id:
            terminal = self._http_json(
                self.training_url, f"/api/training/sessions/{quoted}/terminal-readback", headers=headers
            )
            evaluation = terminal.get("evaluation_result") if isinstance(terminal, Mapping) else None
            preview = terminal.get("preview") if isinstance(terminal, Mapping) and isinstance(terminal.get("preview"), Mapping) else {}
            if isinstance(evaluation, Mapping) and isinstance(evaluation.get("metrics"), Mapping):
                pre_id = str(evaluation.get("eval_id") or "").strip()
                pre_at = str(preview.get("candidate_snapshot_at") or "").strip()
        if pre_id:
            self.chain["pre_edit_eval_id"] = pre_id
        if pre_at:
            self.chain["pre_edit_evaluated_at"] = pre_at
        ids = {**self._hosted_resource_ids(), "teaching_session_id": session_id}
        ids = {k: v for k, v in ids.items() if v not in (None, "")}
        if not patches:
            rejected = (
                " (an existing control_patch from the hosted executor or a service identity was ignored)"
                if control_patches else ""
            )
            raise HostedPause(
                "teaching_human_control_edit",
                "awaiting_human_edit: waiting for the operator to edit short_window/long_window of "
                f"TeachingSession {session_id} in the Management UI{rejected}; "
                "this suite never writes Teaching controls",
                ids,
            )
        latest = max(patches, key=lambda e: str(e.get("timestamp") or e.get("emitted_at") or ""))
        wanted = {
            str(d["parameter_key"]): d["new_value"]
            for d in latest["patch_delta"]
            if d.get("parameter_key") in {"short_window", "long_window"} and d.get("previous_value") != d.get("new_value")
        }
        self._require(
            set(wanted) == {"short_window", "long_window"},
            "the human Teaching edit did not change both short_window and long_window",
        )
        self._require(
            all(by_key[k].get("current_value") == v for k, v in wanted.items()),
            "Teaching controls no longer hold the human's edited values",
        )
        self._require(bool(pre_id), "no pre-edit evaluation id was recorded before the human edit")
        return wanted

    def _case_persona_teaching(self, case: dict[str, Any]) -> None:
        session_id = str(self.chain["session"]["session_id"])
        persona_id = str(
            self.chain.get("persona_id")
            or self.chain["session"].get("persona_id")
            or self._rid(f"persona-l12-e2e-{self.run_token}", "persona")
        )
        self._ensure_persona(
            persona_id,
            name=f"L12 Research Persona {self.run_token}",
            mandate="Evaluate controls against the admitted Alpha replication result",
        )
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
        if self.hosted:
            wanted = self._await_human_teaching_edit(session_id, by_key, headers)
        else:
            wanted = self._operator_teaching_patch(session_id, by_key)
        queued = self._at(
            "teaching.worker_trigger",
            lambda: self._http_json(
                self.training_url,
                f"/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/preview-jobs",
                method="POST",
                payload={
                    "mode": "refresh",
                    "terminalize_session": False,
                },
                headers={**headers, "Idempotency-Key": f"l12-e2e-{self.run_token}-{wanted['short_window']}-{wanted['long_window']}"},
                expected=(201,),
            ),
        )
        job_id = str(queued.get("job_id") or "")
        self._require(bool(job_id), "Teaching preview queue did not return job_id")
        eval_id = str(queued.get("eval_id") or "")
        self._require(bool(eval_id), "Teaching preview queue did not return eval_id")
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
        self._require(
            isinstance(job, Mapping) and job.get("status") == "completed",
            f"Teaching evaluation job failed: {job.get('error_code')}: {job.get('failure_reason')}",
        )
        self._require(
            job.get("job_id") == job_id and job.get("session_id") == session_id,
            "Teaching preview job returned mismatched job/session identity",
        )
        job_eval_id = str(job.get("eval_id") or "")
        self._require(bool(job_eval_id), "Teaching preview job did not return eval_id")
        self._require(job_eval_id == eval_id, "Teaching preview job eval_id mismatch")
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
                    and value.get("session_id") == session_id
                    and bool(value.get("eval_identity"))
                    and isinstance(value.get("evaluation_result"), Mapping)
                    and bool(value.get("evaluation_result", {}).get("eval_id"))
                ),
            ),
        )
        self._require(terminal.get("session_id") == session_id, "Teaching readback session_id mismatch")
        self._require(terminal.get("persona_id") == persona_id, "Teaching readback persona_id mismatch")
        self._require(
            terminal.get("status") in {"active", "completed"},
            f"Unexpected TeachingSession status: {terminal.get('status')}",
        )
        evaluation = terminal.get("evaluation_result")
        self._require(isinstance(evaluation, Mapping), "Teaching readback missing evaluation_result")
        eval_identity = str(terminal.get("eval_identity") or "")
        self._require(bool(eval_identity), "Teaching readback missing eval_identity")
        self._require(evaluation.get("eval_identity") == eval_identity, "Teaching evaluation identity drifted")
        self._require(evaluation.get("session_id") == session_id, "Teaching evaluation session_id mismatch")
        self._require(evaluation.get("persona_id") == persona_id, "Teaching evaluation persona_id mismatch")
        self._require(evaluation.get("eval_id") == eval_id, "Teaching evaluation eval_id mismatch")

        preview = terminal.get("preview") if isinstance(terminal.get("preview"), Mapping) else (job.get("preview") if isinstance(job.get("preview"), Mapping) else {})
        self._require(isinstance(preview, Mapping) and bool(preview), "Teaching preview missing from readback")
        proof = preview.get("evaluation_proof") if isinstance(preview, Mapping) else {}
        self._require(isinstance(proof, Mapping) and bool(proof), "Teaching evaluation proof missing from readback")
        self._require(proof.get("session_id") == session_id, "Teaching evaluation proof session_id mismatch")
        self._require(proof.get("job_id") == job_id, "Teaching evaluation proof job_id mismatch")
        self._require(proof.get("eval_id") == eval_id, "Teaching evaluation proof eval_id mismatch")
        self._require(proof.get("eval_id") == evaluation.get("eval_id"), "Teaching evaluation proof eval_id mismatch")
        if evaluation.get("evaluation_proof_ref"):
            self._require(proof.get("proof_ref") == evaluation.get("evaluation_proof_ref"), "Teaching evaluation proof_ref mismatch")
        self._require(bool(proof.get("proof_digest")), "Teaching evaluation proof missing proof_digest")

        backend = proof.get("backend") if isinstance(proof.get("backend"), Mapping) else {}
        self._require(backend.get("framework") == "vectorbt", f"Teaching computation framework mismatch: {backend.get('framework')}")
        self._require(bool(backend.get("run_id")), "Teaching computation missing backend run_id")
        self._require(bool(preview.get("preview_quality")), "Teaching preview missing preview_quality")

        metrics = evaluation.get("metrics")
        self._require(isinstance(metrics, Mapping) and len(metrics) > 0, "Teaching evaluation missing metrics")
        for metric_key in ("total_return", "sharpe_ratio", "max_drawdown"):
            val = metrics.get(metric_key)
            self._require(
                isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val),
                f"Teaching evaluation metric {metric_key} non-finite or invalid: {val}",
            )

        authority = proof.get("authority") if isinstance(proof.get("authority"), Mapping) else {}
        dataset = authority.get("dataset") if isinstance(authority.get("dataset"), Mapping) else {}
        self._require(bool(dataset.get("dataset_version_id")), "Teaching evaluation missing authority dataset_version_id")
        self._require(bool(dataset.get("digest")), "Teaching evaluation missing authority dataset digest")
        policy = authority.get("policy") if isinstance(authority.get("policy"), Mapping) else {}
        self._require(bool(policy.get("policy_id")), "Teaching evaluation missing authority policy_id")
        self._require(bool(policy.get("digest")), "Teaching evaluation missing authority policy digest")

        val_status = evaluation.get("validation_status")
        self._require(val_status in {"passed", "failed"}, f"Unexpected Teaching evaluation validation_status: {val_status}")
        if val_status == "failed":
            self._require(proof.get("governance_gate_state") == "failed", "Failed evaluation must preserve failed governance_gate_state")
            self._require(proof.get("status") == "failed", "Failed evaluation proof must preserve failed status")
            self._require(proof.get("commit_eligible") is not True, "Failed evaluation must not be commit eligible")
            self._require(bool(evaluation.get("validation_errors")), "Failed evaluation missing validation_errors")
            self._require(evaluation.get("consultation_required") is True, "Failed evaluation must require consultation")
            self._require(bool(evaluation.get("consult_request_id")), "Failed evaluation missing consult_request_id")
            self._require(isinstance(evaluation.get("consultation_receipt"), Mapping), "Failed evaluation missing consultation_receipt")
            self._require(terminal.get("status") == "active" and terminal.get("ended_at") is None, "Failed evaluation session must remain active with ended_at null")
        else:
            self._require(proof.get("governance_gate_state") == "passed", "Passed evaluation must preserve passed governance_gate_state")
            self._require(evaluation.get("validation_errors") == [], "Passed evaluation must have empty validation_errors")

        case["terminal_output"] = {
            "type": "TeachingEvaluationResult",
            "id": eval_identity,
            "session_id": session_id,
            "job_id": job_id,
            "eval_id": evaluation.get("eval_id"),
            "validation_status": val_status,
        }
        case["authority_readback"] = {
            "session_id": terminal.get("session_id"),
            "status": terminal.get("status"),
            "eval_identity": terminal.get("eval_identity"),
            "validation_status": val_status,
            "governance_gate_state": proof.get("governance_gate_state"),
            "consultation_required": evaluation.get("consultation_required"),
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
        self._require(
            (verifier_readback.get("evaluation_result") or {}).get("eval_id") == evaluation.get("eval_id"),
            "operator/Learning verifier did not read the exact Teaching evaluation result",
        )
        self.chain["eval_identity"] = eval_identity
        self.chain["eval_id"] = evaluation.get("eval_id")
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
