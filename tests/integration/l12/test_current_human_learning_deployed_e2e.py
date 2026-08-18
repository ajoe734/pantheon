"""Deployed Compose E2E for Pantheon human-learning loops 5 through 7.

This suite is deliberately opt-in. It never starts an in-process ASGI app,
never imports a product store, and never uses a fake OpenClaw provider or a
mocked HTTP boundary. Run it against an already-started Compose project with
``PANTHEON_L12_HUMAN_LEARNING_E2E=1``.

The three cases form one identity chain:

    AgoraInteractionEvidence -> DatasetVersion -> durable Agora handoff
    -> Policy Learning imitation candidate -> Research experiment run
    -> Consultation memo -> Governance handoff acknowledgement

Loop 5's durable handoff is drained by the real
``policy-learning-shadow-eval-scheduler`` Compose worker; this suite polls
its HTTP-visible effect instead of calling the drainer module in-process.
Loop 7 calls the real Consultation workflow executor against the deployed
Consultation API, the deployed OpenClaw gateway adapter, and the deployed
Governance handoff sink -- no fake provider, no direct store readback.

On failure the harness writes one bounded report containing the last
successful boundary and the first failed boundary. It does not repair the
runtime, create development tasks, or continue into later loop cases.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
PL_DIR = SERVICES_DIR / "policy-learning"
ADAPTER_DIR = SERVICES_DIR / "openclaw-gateway-adapter"

for _p in (str(REPO_ROOT), str(SERVICES_DIR), str(PL_DIR), str(ADAPTER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Policy Learning -> Research handoff client (HTTP boundary only).
from candidate_experiment_handoff import (  # noqa: E402
    CandidateHandoffError,
    handoff_candidate_to_experiment_authority,
)
from research_candidate_client import (  # noqa: E402
    ResearchCandidateClientError,
    post_imitation_candidate_intake_http,
)

# Consultation workflow executor (real production HTTP-client code).
from services.consultation.provider import (  # noqa: E402
    HttpContributionProvider,
)
from services.consultation.workflow_executor import (  # noqa: E402
    ExecutorConfig,
    execute_claim,
    run_tick as run_consultation_tick,
)
from services.consultation.workflow_state import WorkflowStateStore  # noqa: E402

# The contribution path is a plain string constant owned by the adapter
# route module; importing it is not an in-process app construction.
from consultation_provider import CONSULTATION_CONTRIBUTION_PATH  # noqa: E402


TASK_ID = "L12-GAP-F07-E2E-HUMAN-20260818"
DEFAULT_REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "twelve-loop-current"
    / "human-learning"
    / "run-report.json"
)
CASE_NAMES = (
    "agora_interaction_evidence",
    "imitation_research_handoff",
    "consultation_governance_handoff",
)
OWNER_SERVICES = {
    "agora_interaction_evidence": "operator-bff",
    "imitation_research_handoff": "research-orchestrator-svc",
    "consultation_governance_handoff": "consultation-svc",
}
AGORA_SERVICE_ACTOR = "policy-learning-agora-handoff-drainer"
CONSULTATION_SERVICE_ACTOR = "consultation-workflow-executor"


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


class BoundaryFailure(RuntimeError):
    def __init__(self, boundary: str, detail: Any) -> None:
        super().__init__(_bounded_text(detail))
        self.boundary = boundary
        self.detail = _bounded_text(detail)


class DeployedHumanLearningHarness:
    """One fail-fast, report-producing chain over already-running services."""

    def __init__(self) -> None:
        self.started_at = _utc_now()
        self.run_token = uuid.uuid4().hex[:10]
        self.compose_project = os.getenv("PANTHEON_L12_COMPOSE_PROJECT", "pantheon").strip()
        self.timeout_seconds = float(os.getenv("PANTHEON_L12_POLL_TIMEOUT_SECONDS", "210"))
        self.poll_seconds = float(os.getenv("PANTHEON_L12_POLL_INTERVAL_SECONDS", "2"))
        self.tenant_id = os.getenv("PANTHEON_L12_HUMAN_LEARNING_TENANT_ID", "pantheon-local").strip()
        self.bff_url = os.getenv("PANTHEON_L12_BFF_URL", "http://127.0.0.1:18001").rstrip("/")
        self.policy_learning_url = os.getenv(
            "PANTHEON_L12_POLICY_LEARNING_URL", "http://127.0.0.1:18100"
        ).rstrip("/")
        self.research_url = os.getenv(
            "PANTHEON_L12_RESEARCH_URL", "http://127.0.0.1:18101"
        ).rstrip("/")
        self.consultation_url = os.getenv(
            "PANTHEON_L12_CONSULTATION_URL", "http://127.0.0.1:18096"
        ).rstrip("/")
        self.governance_url = os.getenv(
            "PANTHEON_L12_GOVERNANCE_URL", "http://127.0.0.1:18082"
        ).rstrip("/")
        self.openclaw_adapter_url = os.getenv(
            "PANTHEON_L12_OPENCLAW_ADAPTER_URL", "http://127.0.0.1:18104"
        ).rstrip("/")
        self.bff_bearer = os.getenv(
            "PANTHEON_L12_BFF_BEARER",
            f"l12-current-e2e:operator,admin:{self.tenant_id}",
        )
        self.agora_handoff_token = os.getenv(
            "PANTHEON_L12_AGORA_HANDOFF_TOKEN",
            "pantheon-local-agora-handoff-service-token",
        )
        self.policy_learning_token = os.getenv(
            "PANTHEON_L12_POLICY_LEARNING_TOKEN",
            "pantheon-local-policy-learning-service",
        )
        self.consultation_token = os.getenv(
            "PANTHEON_L12_CONSULTATION_TOKEN",
            "pantheon-local-consultation-service",
        )
        self.consultation_provider_token = os.getenv(
            "PANTHEON_L12_CONSULTATION_PROVIDER_TOKEN",
            "pantheon-local-consultation-provider",
        )
        self.consultation_handoff_token = os.getenv(
            "PANTHEON_L12_CONSULTATION_HANDOFF_TOKEN",
            "pantheon-local-consultation-handoff-token",
        )
        self.provider_timeout_seconds = float(
            os.getenv("PANTHEON_L12_OPENCLAW_TIMEOUT_SECONDS", "190")
        )
        self.report_path = Path(os.getenv("PANTHEON_L12_REPORT_PATH", str(DEFAULT_REPORT_PATH)))
        # Worker-lease state is inherent to ExecutorConfig.state_path in real
        # deployments; it is process-local by design, not a product store.
        self.state_dir = Path(tempfile.mkdtemp(prefix=f"l12-hl-e2e-{self.run_token}-"))
        self.git_sha = self._command(["git", "rev-parse", "HEAD"]).strip()
        self.case_results: list[dict[str, Any]] = []
        self.first_failure: dict[str, Any] | None = None
        self.failure_reported = False
        self.chain: dict[str, Any] = {}
        self._current_case: dict[str, Any] | None = None
        self._write_report("running")

    # -- infra helpers -----------------------------------------------------

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
        record = inspected[0]
        state = record.get("State") or {}
        labels = (record.get("Config") or {}).get("Labels") or {}
        status = str(state.get("Status") or "")
        health = str((state.get("Health") or {}).get("Status") or "not_declared")
        if labels.get("com.docker.compose.service") != service:
            raise RuntimeError(f"container label does not identify Compose service {service}")
        return {
            "compose_service": service,
            "container_id": str(record.get("Id") or container_id),
            "image_id": str(record.get("Image") or ""),
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
        timeout: float = 20.0,
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
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read(4_194_304).decode("utf-8", errors="replace")
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
        except Exception as exc:  # noqa: BLE001 - fail-closed boundary wrapper
            raise BoundaryFailure(boundary, exc) from exc
        self._current_case["last_successful_boundary"] = boundary
        self._current_case.setdefault("successful_boundaries", []).append(boundary)
        self._write_report("running")
        return result

    @staticmethod
    def _require(condition: Any, detail: str) -> None:
        if not condition:
            raise RuntimeError(detail)

    def _report_payload(self, status: str) -> dict[str, Any]:
        return {
            "schema_version": "pantheon.l12.human-learning.deployed-e2e.v1",
            "task_id": TASK_ID,
            "status": status,
            "started_at": self.started_at,
            "completed_at": _utc_now() if status in {"passed", "failed"} else None,
            "git_sha": self.git_sha,
            "compose_project": self.compose_project,
            "anti_fixture": {
                "product_store_imports": False,
                "in_process_service_client": False,
                "fake_provider": False,
                "direct_drainer_call": False,
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

    # -- case driver ---------------------------------------------------------

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

    # -- headers -------------------------------------------------------------

    def _bff_headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.bff_bearer}",
            "X-Pantheon-Tenant-Id": self.tenant_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _agora_service_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.agora_handoff_token}",
            "X-Pantheon-Tenant-Id": self.tenant_id,
            "X-Pantheon-Service-Actor": AGORA_SERVICE_ACTOR,
        }

    def _policy_learning_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.policy_learning_token}",
            "X-Pantheon-Tenant-Id": self.tenant_id,
        }

    def _consultation_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.consultation_token}",
            "X-Pantheon-Tenant-Id": self.tenant_id,
        }

    # -- Loop 5: Agora interaction evidence -> durable handoff -> intake ----

    def _case_agora_interaction_evidence(self, case: dict[str, Any]) -> None:
        evidence_id = f"ev-l12-hl-{self.run_token}"
        owner = self._at(
            "agora.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["agora_interaction_evidence"]),
        )
        case["owner"] = owner

        ev_payload = {
            "evidence_id": evidence_id,
            "interaction_kind": "ask",
            "persona_id": "persona-advisor",
            "session_id": f"session-l12-hl-{self.run_token}",
            "content": {"question": "Evaluate macro regime and risk threshold for imitation learning."},
            "source_refs": [f"session:session-l12-hl-{self.run_token}"],
            "learning_eligible": True,
            "captured_at": _utc_now(),
        }
        created = self._at(
            "agora.interaction_evidence_created",
            lambda: self._http_json(
                self.bff_url,
                "/bff/agora/interaction-evidence",
                method="POST",
                payload=ev_payload,
                headers=self._bff_headers(idempotency_key=f"idem-ev-{evidence_id}"),
                expected=(200, 201),
            ),
        )
        self._require(created.get("status") == "created", "evidence submission was not created")
        dataset_version_id = str(created["data"]["dataset_version_id"])
        self._require(dataset_version_id.startswith("dsv-"), "dataset_version_id has an unexpected shape")
        case["trigger"] = {
            "type": "AgoraInteractionEvidence",
            "id": evidence_id,
            "dataset_version_id": dataset_version_id,
        }

        processed = self._at(
            "agora.dataset_worker_process",
            lambda: self._http_json(
                self.bff_url,
                "/bff/agora/dataset-worker/process",
                method="POST",
                headers=self._bff_headers(),
                expected=(200, 201),
            ),
        )
        self._require(processed.get("status") == "success", "dataset worker did not process the inbox")

        pending = self._at(
            "agora.durable_handoff_visible",
            lambda: self._poll(
                f"durable handoff for {dataset_version_id}",
                lambda: self._http_json(
                    self.bff_url,
                    "/internal/agora/dataset-handoffs",
                    headers=self._agora_service_headers(),
                ),
                lambda value: any(
                    item.get("dataset_version_id") == dataset_version_id
                    for item in value.get("items", [])
                ),
            ),
        )
        handoff = next(
            item for item in pending["items"] if item.get("dataset_version_id") == dataset_version_id
        )
        handoff_id = str(handoff["handoff_id"])
        self._require(handoff_id.startswith("gh-"), "handoff_id has an unexpected shape")
        case["authority_readback"] = {"dataset_version_id": dataset_version_id, "handoff_id": handoff_id}

        # Do not call the drainer module directly: the real
        # policy-learning-shadow-eval-scheduler Compose worker drains this
        # handoff on its own interval. Poll its HTTP-visible effect only.
        candidates = self._at(
            "policy_learning.candidate_admitted",
            lambda: self._poll(
                f"policy-learning candidate for handoff {handoff_id}",
                lambda: self._http_json(
                    self.policy_learning_url,
                    "/api/policy-learning/candidates",
                    headers=self._policy_learning_headers(),
                ),
                lambda value: any(item.get("handoff_id") == handoff_id for item in value),
            ),
        )
        admitted = next(item for item in candidates if item.get("handoff_id") == handoff_id)
        candidate_id = str(admitted["candidate_id"])
        self._require(
            admitted.get("dataset_ref", {}).get("dataset_version_id") == dataset_version_id,
            "admitted candidate lost the exact dataset_version_id",
        )
        self._require(
            admitted.get("dataset_source") == "agora_dataset_version_handoff",
            "admitted candidate did not record the Agora handoff dataset source",
        )
        case["terminal_output"] = {"type": "ShadowImitationCandidate", "id": candidate_id}

        pending_after = self._at(
            "agora.handoff_no_longer_pending",
            lambda: self._http_json(
                self.bff_url,
                "/internal/agora/dataset-handoffs",
                headers=self._agora_service_headers(),
            ),
        )
        self._require(
            not any(item.get("handoff_id") == handoff_id for item in pending_after.get("items", [])),
            "handoff is still pending after the real drainer worker should have acknowledged it",
        )

        replay = self._at(
            "policy_learning.replay_admission_idempotent",
            lambda: self._http_json(
                self.policy_learning_url,
                "/api/policy-learning/agora-handoff",
                method="POST",
                headers=self._policy_learning_headers(),
                payload={
                    "handoff_id": handoff_id,
                    "eval_type": "shadow",
                    "actor_id": "l12-current-e2e-replayer",
                    "tenant_id": self.tenant_id,
                    "dataset_ref": {"dataset_version_id": dataset_version_id, "tenant_id": self.tenant_id},
                },
                expected=(200, 201, 409),
            ),
        )
        self._require(
            replay.get("candidate_id", candidate_id) == candidate_id,
            "replayed admission produced a different candidate_id",
        )
        case["next_consumer"] = {
            "consumer": "policy-learning-agora-handoff-drainer",
            "receipt_type": "ShadowImitationCandidate",
            "receipt_id": candidate_id,
        }

        self.chain["evidence_id"] = evidence_id
        self.chain["dataset_version_id"] = dataset_version_id
        self.chain["agora_handoff_id"] = handoff_id
        self.chain["candidate_id"] = candidate_id

    # -- Loop 6: imitation candidate -> Research HTTP handoff ---------------

    def _case_imitation_research_handoff(self, case: dict[str, Any]) -> None:
        owner = self._at(
            "research.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["imitation_research_handoff"]),
        )
        case["owner"] = owner

        candidate_id = f"sic-l12-hl-{self.run_token}"
        candidate = {
            "candidate_id": candidate_id,
            "id": candidate_id,
            "tenant_id": self.tenant_id,
            "status": "processed",
            "handoff_id": self.chain.get("agora_handoff_id"),
            "dataset_version_id": self.chain.get("dataset_version_id", f"dsv-l12-hl-{self.run_token}"),
            "dataset_lineage": {
                "version_id": self.chain.get("dataset_version_id", f"dsv-l12-hl-{self.run_token}"),
                "tenant_id": self.tenant_id,
                "source": "agora_dataset_authority",
                "evidence_refs": [self.chain.get("evidence_id", "")],
                "authoritative": True,
            },
            "artifact_checksum": "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex,
            "dataset_mode": "agora",
            "metrics": {"action_match_rate": 0.94, "return_gap": 0.008},
            "evaluation_summary": {"action_match_rate": 0.94, "evaluator_id": "l12-current-e2e"},
            "strategy_id": "strat-l12-hl-e2e",
            "strategy_spec_version": "1.0.0",
            "strategy_spec_id": "spec-strat-l12-hl-e2e",
            "code_version": self.git_sha,
            "trace_id": f"trace-l12-hl-{candidate_id}",
        }
        case["trigger"] = {"type": "ShadowImitationCandidate", "id": candidate_id}

        receipt = self._at(
            "research.candidate_intake_http",
            lambda: handoff_candidate_to_experiment_authority(candidate, research_url=self.research_url),
        )
        self._require(
            receipt.experiment_task_id == candidate["experiment_task_id"],
            "candidate handoff did not attach the exact task id",
        )
        case["terminal_output"] = {
            "type": "ExperimentRun",
            "task_id": receipt.experiment_task_id,
            "run_id": receipt.experiment_run_id,
        }

        run_readback = self._at(
            "research.run_readback",
            lambda: self._http_json(
                self.research_url,
                f"/api/research-orchestrator/runs/{urllib.parse.quote(receipt.experiment_run_id, safe='')}",
                headers={"X-Pantheon-Tenant-Id": self.tenant_id},
            ),
        )
        self._require(
            run_readback.get("task_id") == receipt.experiment_task_id
            and run_readback.get("run_id") == receipt.experiment_run_id,
            "Research owner readback drifted from the handoff receipt identity",
        )
        case["authority_readback"] = {
            "task_id": run_readback.get("task_id"),
            "run_id": run_readback.get("run_id"),
        }

        replay = self._at(
            "research.replay_intake_idempotent",
            lambda: post_imitation_candidate_intake_http(candidate, research_url=self.research_url),
        )
        self._require(
            replay.task_id == receipt.experiment_task_id and replay.run_id == receipt.experiment_run_id,
            "replayed candidate intake produced a different task/run id",
        )
        case["next_consumer"] = {
            "consumer": "research-orchestrator-svc",
            "receipt_type": "ExperimentRun",
            "receipt_id": receipt.experiment_run_id,
        }

        empty_candidate = {"candidate_id": "", "tenant_id": self.tenant_id, "status": "processed"}
        try:
            handoff_candidate_to_experiment_authority(empty_candidate, research_url=self.research_url)
        except (CandidateHandoffError, ResearchCandidateClientError):
            pass
        else:
            raise BoundaryFailure(
                "research.empty_candidate_fails_closed",
                "an empty candidate id was accepted by the Research intake boundary",
            )

        self.chain["candidate_id"] = candidate_id
        self.chain["experiment_task_id"] = receipt.experiment_task_id
        self.chain["experiment_run_id"] = receipt.experiment_run_id

    # -- Loop 7: Consultation -> deployed OpenClaw provider -> Governance ---

    def _case_consultation_governance_handoff(self, case: dict[str, Any]) -> None:
        owner = self._at(
            "consultation.owner_compose_identity",
            lambda: self._service_identity(OWNER_SERVICES["consultation_governance_handoff"]),
        )
        case["owner"] = owner

        request_id = f"cr-l12-hl-{self.run_token}"
        req_payload = {
            "request_id": request_id,
            "tenant_id": self.tenant_id,
            "request_type": "strategy_review",
            "requested_by": {"actor_type": "operator", "actor_id": "l12-current-e2e-operator"},
            "target_type": "experiment_run",
            "target_id": self.chain.get("experiment_run_id", request_id),
            "task": "Review imitation policy risk bounds before promotion.",
            "context_refs": [
                f"candidate:{self.chain.get('candidate_id', '')}",
                f"dataset:{self.chain.get('dataset_version_id', '')}",
                f"evidence:{self.chain.get('evidence_id', '')}",
            ],
            "evidence_refs": [f"research_run:{self.chain.get('experiment_run_id', '')}"],
            "priority": "high",
            "metadata": {"paper_only": True, "agora_handoff_id": self.chain.get("agora_handoff_id", "")},
            "trace_id": f"trace-l12-hl-{request_id}",
        }
        case["trigger"] = {"type": "ConsultRequest", "id": request_id}

        created = self._at(
            "consultation.request_created",
            lambda: self._http_json(
                self.consultation_url,
                "/api/consult/requests",
                method="POST",
                payload=req_payload,
                headers=self._consultation_headers(),
                expected=(200, 201),
            ),
        )
        self._require(created.get("status") == "draft", "consult request was not created in draft status")

        self._at(
            "consultation.request_submitted",
            lambda: self._http_json(
                self.consultation_url,
                f"/api/consult/requests/{urllib.parse.quote(request_id, safe='')}/submit",
                method="POST",
                payload={},
                headers=self._consultation_headers(),
            ),
        )

        executor_config = ExecutorConfig(
            api_url=self.consultation_url,
            tenant_id=self.tenant_id,
            api_token=self.consultation_token,
            provider_url=self.openclaw_adapter_url + CONSULTATION_CONTRIBUTION_PATH,
            provider_token=self.consultation_provider_token,
            provider_service_actor=CONSULTATION_SERVICE_ACTOR,
            handoff_sink_url=self.governance_url + "/api/governance/consultation-handoffs",
            handoff_token=self.consultation_handoff_token,
            worker_id=f"l12-current-e2e-{self.run_token}",
            state_path=str(self.state_dir / "executor_state.sqlite3"),
            lease_seconds=60,
            retry_after_seconds=0,
            max_blocked_attempts=2,
            batch_size=1,
            timeout_seconds=self.provider_timeout_seconds,
        )
        executor_state = WorkflowStateStore(executor_config.state_path)

        tick_result = self._at(
            "consultation.executor_tick_completed",
            lambda: run_consultation_tick(config=executor_config, state=executor_state),
        )
        self._require(tick_result.get("completed") == 1, "executor tick did not complete the deployed request")
        self._require(tick_result.get("blocked", 0) == 0, "executor tick blocked against deployed owners")
        self._require(tick_result.get("dead_lettered", 0) == 0, "executor tick dead-lettered against deployed owners")
        case["terminal_output"] = {"type": "ConsultationExecutorTick", "id": request_id}

        memos = self._at(
            "consultation.memo_readback",
            lambda: self._http_json(
                self.consultation_url,
                f"/api/consult/memos?request_id={urllib.parse.quote(request_id, safe='')}",
                headers=self._consultation_headers(),
            ),
        )
        self._require(len(memos) == 1, "expected exactly one published ConsultMemo")
        memo = memos[0]
        self._require(memo.get("status") == "published", "ConsultMemo was not published")
        case["authority_readback"] = {"memo_id": memo.get("memo_id"), "request_id": memo.get("request_id")}

        handoffs = self._at(
            "consultation.governance_handoff_acknowledged",
            lambda: self._http_json(
                self.consultation_url,
                f"/api/consult/handoffs?request_id={urllib.parse.quote(request_id, safe='')}",
                headers=self._consultation_headers(),
            ),
        )
        self._require(len(handoffs) == 1, "expected exactly one Governance handoff record")
        gov_handoff = handoffs[0]
        # Governance receipt: the executor only marks a handoff "acknowledged"
        # after the real deployed Governance owner accepted the POST over
        # HTTP. There is no Governance readback route, so this acknowledged
        # status (not a store read) is the deployed Governance receipt.
        self._require(
            gov_handoff.get("status") == "acknowledged",
            "Governance owner did not acknowledge the durable consultation handoff",
        )
        case["next_consumer"] = {
            "consumer": "governance",
            "receipt_type": "ConsultGateHandoff",
            "receipt_id": gov_handoff.get("handoff_id"),
        }

        # Recovery proof: a fresh local lease store recovers the already
        # acknowledged handoff without a duplicate deployed OpenClaw turn.
        recovery_config = ExecutorConfig(
            **{**executor_config.__dict__, "state_path": str(self.state_dir / "recovery_state.sqlite3")}
        )
        recovery_state = WorkflowStateStore(recovery_config.state_path)
        recovery_state.ensure_request(tenant_id=self.tenant_id, request_id=request_id)
        claim = self._at(
            "consultation.recovery_claim",
            lambda: recovery_state.claim_next(
                tenant_id=self.tenant_id,
                lease_owner="l12-current-e2e-recovery",
                lease_seconds=60,
            ),
        )
        self._require(claim is not None, "recovery worker could not claim the acknowledged request")
        provider_client = HttpContributionProvider(
            endpoint=recovery_config.provider_url,
            bearer_token=recovery_config.provider_token,
            service_actor=recovery_config.provider_service_actor,
            timeout_seconds=recovery_config.timeout_seconds,
        )
        recovered = self._at(
            "consultation.recovery_no_duplicate_turn",
            lambda: execute_claim(
                config=recovery_config,
                state=recovery_state,
                provider=provider_client,
                claim=claim,
            ),
        )
        self._require(
            recovered.get("outcome") == "completed" and "recovered" in str(recovered.get("detail", "")),
            "recovery worker re-invoked the deployed OpenClaw provider instead of reusing the acknowledged handoff",
        )

        self.chain["consult_request_id"] = request_id
        self.chain["memo_id"] = memo.get("memo_id")
        self.chain["governance_handoff_id"] = gov_handoff.get("handoff_id")


@pytest.fixture(scope="session")
def deployed_human_learning_e2e() -> DeployedHumanLearningHarness:
    if os.getenv("PANTHEON_L12_HUMAN_LEARNING_E2E") != "1":
        pytest.skip("set PANTHEON_L12_HUMAN_LEARNING_E2E=1 to exercise running Compose services")
    return DeployedHumanLearningHarness()


def test_deployed_agora_interaction_evidence_identity_chain(
    deployed_human_learning_e2e: DeployedHumanLearningHarness,
) -> None:
    deployed_human_learning_e2e.run_case(1)


def test_deployed_imitation_research_handoff_identity_chain(
    deployed_human_learning_e2e: DeployedHumanLearningHarness,
) -> None:
    deployed_human_learning_e2e.run_case(2)


def test_deployed_consultation_governance_handoff_identity_chain(
    deployed_human_learning_e2e: DeployedHumanLearningHarness,
) -> None:
    deployed_human_learning_e2e.run_case(3)


def test_deployed_human_learning_chain_identity_correlation(
    deployed_human_learning_e2e: DeployedHumanLearningHarness,
) -> None:
    """Read-only correlation over identifiers this run already produced."""

    chain = deployed_human_learning_e2e.chain
    for key in (
        "evidence_id",
        "dataset_version_id",
        "agora_handoff_id",
        "candidate_id",
        "experiment_task_id",
        "experiment_run_id",
        "consult_request_id",
        "memo_id",
        "governance_handoff_id",
    ):
        assert chain.get(key), f"identity chain is missing {key}"


def test_deployed_suite_has_no_fixture_or_product_store_shortcut() -> None:
    """AST guard: keep this suite on deployed HTTP boundaries only."""

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

    banned_modules = {
        "uvicorn",
        "fastapi.testclient",
        "unittest.mock",
        "agora_handoff_drainer",
        "agora.dataset_extraction.extractor",
        "services.consultation.store",
        "services.consultation.main",
        "services.governance.main",
        "services.governance.record_store",
        "services.research.main",
    }
    assert not banned_modules.intersection(imported_modules)
    assert "TestClient" not in imported_names
    assert "FakeOpenClawRiskProvider" not in imported_names
    assert not {"tmp_path", "tmpdir", "monkeypatch"}.intersection(fixture_arguments)
    assert not {"Mock", "MagicMock", "patch", "TestClient", "create_openclaw_adapter_app"}.intersection(
        called_names
    )
    assert [name for name in function_names if name.startswith("test_deployed_")] == [
        "test_deployed_agora_interaction_evidence_identity_chain",
        "test_deployed_imitation_research_handoff_identity_chain",
        "test_deployed_consultation_governance_handoff_identity_chain",
        "test_deployed_human_learning_chain_identity_correlation",
        "test_deployed_suite_has_no_fixture_or_product_store_shortcut",
    ]
    assert "subprocess.run" in inspect.getsource(DeployedHumanLearningHarness._command)
    assert "docker" in inspect.getsource(DeployedHumanLearningHarness._compose_argv)
