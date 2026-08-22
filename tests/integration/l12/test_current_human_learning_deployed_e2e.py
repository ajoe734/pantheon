"""Current-dev E2E for Pantheon human-learning loops 5 through 7.

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
The same admitted candidate must be processed and handed to the deployed
Research authority by that worker.  Loop 7 is completed by the executor
supervised inside the deployed ``consultation-svc`` container; the test never
imports or runs an executor, provider, or product store itself.

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


TASK_ID = "PFG-L12-HUMAN-E2E-LIVE-R2-20260821"
DEFAULT_REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "product-functional-closure"
    / TASK_ID
    / "deployed-run.json"
)
CASE_NAMES = (
    "agora_interaction_evidence",
    "imitation_research_handoff",
    "consultation_governance_handoff",
)
OWNER_SERVICES = {
    "agora_interaction_evidence": (
        "operator-bff",
        "policy-learning-svc",
        "policy-learning-shadow-eval-scheduler",
    ),
    "imitation_research_handoff": "research-orchestrator-svc",
    "consultation_governance_handoff": (
        "consultation-svc",
        "openclaw-gateway-adapter",
        "governance",
    ),
}
AGORA_SERVICE_ACTOR = "policy-learning-agora-handoff-drainer"


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
        self.consultation_tenant_id = os.getenv(
            "PANTHEON_L12_CONSULTATION_TENANT_ID",
            os.getenv("PANTHEON_TENANT_ID", "tenant-dev"),
        ).strip()
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
        self.bff_bearer = self._resolve_bff_bearer()
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
        self.report_path = Path(os.getenv("PANTHEON_L12_REPORT_PATH", str(DEFAULT_REPORT_PATH)))
        self.git_sha = (
            os.getenv("PANTHEON_L12_GIT_SHA")
            or self._command(["git", "rev-parse", "HEAD"]).strip()
        )
        self.case_results: list[dict[str, Any]] = []
        self.first_failure: dict[str, Any] | None = None
        self.failure_reported = False
        self.chain: dict[str, Any] = {}
        self._current_case: dict[str, Any] | None = None
        self._write_report("running")

    def _resolve_bff_bearer(self) -> str:
        explicit = self._secret_from_env_or_file("PANTHEON_L12_BFF_BEARER", default="")
        if explicit:
            return explicit
        client_id = (
            os.getenv("DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID", "")
            or os.getenv("PANTHEON_L12_DEV_LOGIN_CLIENT_ID", "")
            or os.getenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID", "")
            or "pantheon-dev-operator-a-v1"
        )
        client_secret = (
            self._secret_from_env_or_file("DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET", default="")
            or self._secret_from_env_or_file("PANTHEON_L12_DEV_LOGIN_CLIENT_SECRET", default="")
            or self._secret_from_env_or_file("PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET", default="")
        )
        if client_id and client_secret:
            try:
                body = self._http_json(
                    self.bff_url,
                    "/bff/auth/dev-login",
                    method="POST",
                    payload={"client_id": client_id, "client_secret": client_secret},
                    expected=(200,),
                )
                if isinstance(body, dict) and body.get("access_token"):
                    return str(body["access_token"])
            except Exception:
                pass
        return f"l12-current-e2e:operator,admin:{self.tenant_id}"

    # -- infra helpers -----------------------------------------------------

    def _command(self, argv: Sequence[str], *, timeout: float = 60.0) -> str:
        completed = subprocess.run(
            list(argv),
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
            raise RuntimeError(f"{argv[0]} exited {completed.returncode}: {_bounded_text(detail)}")
        return completed.stdout

    @staticmethod
    def _secret_from_env_or_file(name: str, *, default: str) -> str:
        """Read an opt-in credential without ever placing it in run evidence.

        Strict current-dev BFF deployments require a JWT obtained from the
        server-bound dev-login exchange. A file input lets the operator pass
        that short-lived credential without exposing it in an environment
        dump, command history, pytest report, or committed artifact.
        """

        inline_value = os.getenv(name, "").strip()
        file_name = os.getenv(f"{name}_FILE", "").strip()
        if inline_value and file_name:
            raise RuntimeError(f"set only one of {name} or {name}_FILE")
        if file_name:
            try:
                value = Path(file_name).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RuntimeError(f"could not read {name}_FILE") from exc
            if not value:
                raise RuntimeError(f"{name}_FILE is empty")
            return value
        return inline_value or default

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
            "started_at": str(state.get("StartedAt") or ""),
            "restart_count": int(record.get("RestartCount") or 0),
        }

    def _service_identity(self, service: str, *, allow_starting: bool = False) -> dict[str, Any]:
        deadline = time.monotonic() + min(self.timeout_seconds, 120)
        last_identity: dict[str, Any] = {}
        allowed_health = {"healthy", "not_declared"}
        if allow_starting:
            allowed_health.add("starting")
        while time.monotonic() < deadline:
            last_identity = self._service_identity_snapshot(service)
            if last_identity["state"] == "running" and last_identity["health"] in allowed_health:
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

    def _service_identities(self, services: str | Sequence[str]) -> dict[str, dict[str, Any]]:
        names = (services,) if isinstance(services, str) else tuple(services)
        return {service: self._service_identity(service) for service in names}

    def _restart_service(self, service: str) -> dict[str, Any]:
        """Restart a deployed Compose worker and retain its durable identity proof.

        The restart is intentionally a Compose operation against the already
        running dev project.  No local worker, store, or application is
        constructed by this test.  The later HTTP readbacks prove replay did
        not create a second durable result.
        """

        before = self._service_identity(service)
        self._command(self._compose_argv("restart", service))
        after = self._service_identity(service, allow_starting=True)
        self._require(
            after["started_at"] != before["started_at"]
            or int(after["restart_count"]) > int(before["restart_count"]),
            f"Compose restart for {service} did not change its process identity",
        )
        return {"service": service, "before": before, "after": after}

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
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", str(exc))
            raise RuntimeError(f"{method} {path} connection failed: {reason}") from exc
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
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Tenant": self.tenant_id,
            "X-Pantheon-Tenant-Id": self.tenant_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _agora_service_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.agora_handoff_token}",
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Tenant": self.tenant_id,
            "X-Pantheon-Tenant-Id": self.tenant_id,
            "X-Pantheon-Service-Actor": AGORA_SERVICE_ACTOR,
        }

    def _policy_learning_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.policy_learning_token}",
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Tenant": self.tenant_id,
            "X-Pantheon-Tenant-Id": self.tenant_id,
        }

    def _consultation_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.consultation_token}",
            "X-Tenant-Id": self.consultation_tenant_id,
            "X-Pantheon-Tenant": self.consultation_tenant_id,
            "X-Pantheon-Tenant-Id": self.consultation_tenant_id,
        }

    # -- Loop 5: Agora interaction evidence -> durable handoff -> intake ----

    def _case_agora_interaction_evidence(self, case: dict[str, Any]) -> None:
        evidence_id = f"ev-l12-hl-{self.run_token}"
        owners = self._at(
            "agora_and_policy.owner_compose_identities",
            lambda: self._service_identities(OWNER_SERVICES["agora_interaction_evidence"]),
        )
        case["owner"] = owners

        ev_payload = {
            "evidence_id": evidence_id,
            "interaction_kind": "training_example",
            "persona_id": "persona-advisor",
            "session_id": f"session-l12-hl-{self.run_token}",
            "content": {
                "actor_id": "persona-advisor",
                "actor_role": "operator",
                "decision": "approve",
                "target": {
                    "registry_id": "reg-l12-human-learning",
                    "strategy_id": "agora-human-imitation",
                    "artifact_version": "0.0.0",
                    "artifact_type": "strategy_spec",
                    "promotion_state": "candidate",
                },
                "steps": [
                    {
                        "observation": [0.18, -0.06, 0.42],
                        "action": "buy_small",
                        "reward": 0.21,
                    },
                    {
                        "observation": [0.13, 0.04, 0.37],
                        "action": "hold",
                        "reward": 0.08,
                    },
                ],
            },
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
        dataset = created["data"]
        dataset_version_id = str(dataset["dataset_version_id"])
        self._require(dataset_version_id.startswith("dsv-"), "dataset_version_id has an unexpected shape")
        self._require(dataset.get("dataset_kind") == "learn", "evidence did not create a learning dataset")
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

        # The durable policy worker is normally interval-driven. Restarting
        # the deployed worker starts its normal recovery/tick without calling
        # a drainer or policy endpoint from this test process.
        restart = self._at(
            "policy_learning.scheduler_restart_for_durable_handoff",
            lambda: self._restart_service("policy-learning-shadow-eval-scheduler"),
        )
        candidates = self._at(
            "policy_learning.candidate_processed_by_deployed_worker",
            lambda: self._poll(
                f"processed policy-learning candidate for handoff {handoff_id}",
                lambda: self._http_json(
                    self.policy_learning_url,
                    "/api/policy-learning/candidates",
                    headers=self._policy_learning_headers(),
                ),
                lambda value: any(
                    item.get("handoff_id") == handoff_id
                    and item.get("status") == "processed"
                    and item.get("handoff_status") == "completed"
                    for item in value
                ),
            ),
        )
        admitted = next(
            item
            for item in candidates
            if item.get("handoff_id") == handoff_id
            and item.get("status") == "processed"
            and item.get("handoff_status") == "completed"
        )
        candidate_id = str(admitted["candidate_id"])
        self._require(
            admitted.get("dataset_ref", {}).get("dataset_version_id") == dataset_version_id,
            "admitted candidate lost the exact dataset_version_id",
        )
        self._require(
            admitted.get("dataset_source") == "agora_dataset_version_handoff",
            "admitted candidate did not record the Agora handoff dataset source",
        )
        self._require(
            not admitted.get("seed_fallback_used", False) and admitted.get("authoritative") is True,
            "policy candidate was not trained from an authoritative Agora dataset",
        )
        case["terminal_output"] = {
            "type": "ShadowImitationCandidate",
            "id": candidate_id,
            "status": admitted.get("status"),
            "restart": restart,
        }

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
            "policy_learning.restart_replay_is_idempotent",
            lambda: self._restart_service("policy-learning-shadow-eval-scheduler"),
        )
        replayed_candidates = self._at(
            "policy_learning.restart_replay_readback",
            lambda: self._poll(
                f"idempotent policy replay for {handoff_id}",
                lambda: self._http_json(
                    self.policy_learning_url,
                    "/api/policy-learning/candidates",
                    headers=self._policy_learning_headers(),
                ),
                lambda value: len(
                    [item for item in value if item.get("handoff_id") == handoff_id]
                ) == 1,
            ),
        )
        replayed = [item for item in replayed_candidates if item.get("handoff_id") == handoff_id]
        self._require(
            len(replayed) == 1 and replayed[0].get("candidate_id") == candidate_id,
            "replayed scheduler produced a different candidate_id",
        )
        case["next_consumer"] = {
            "consumer": "policy-learning-agora-handoff-drainer",
            "receipt_type": "ShadowImitationCandidate",
            "receipt_id": candidate_id,
            "replay": replay,
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

        candidate_id = str(self.chain.get("candidate_id") or "")
        self._require(candidate_id, "Loop 5 did not produce an imitation candidate")
        case["trigger"] = {"type": "ShadowImitationCandidate", "id": candidate_id}

        candidate = self._at(
            "policy_learning.processed_candidate_readback",
            lambda: self._poll(
                f"processed candidate {candidate_id} with Research receipt",
                lambda: self._http_json(
                    self.policy_learning_url,
                    f"/api/policy-learning/candidates/{urllib.parse.quote(candidate_id, safe='')}",
                    headers=self._policy_learning_headers(),
                ),
                lambda value: value.get("status") == "processed"
                and value.get("handoff_status") == "completed"
                and bool(value.get("experiment_task_id"))
                and bool(value.get("experiment_run_id")),
            ),
        )
        self._require(
            candidate.get("handoff_id") == self.chain.get("agora_handoff_id"),
            "Research handoff candidate drifted from the Agora durable handoff",
        )
        experiment_task_id = str(candidate["experiment_task_id"])
        experiment_run_id = str(candidate["experiment_run_id"])
        case["terminal_output"] = {
            "type": "ExperimentRun",
            "task_id": experiment_task_id,
            "run_id": experiment_run_id,
        }

        run_readback = self._at(
            "research.run_readback",
            lambda: self._http_json(
                self.research_url,
                f"/api/research-orchestrator/runs/{urllib.parse.quote(experiment_run_id, safe='')}",
                headers={
                    "X-Tenant-Id": self.tenant_id,
                    "X-Pantheon-Tenant": self.tenant_id,
                    "X-Pantheon-Tenant-Id": self.tenant_id,
                },
            ),
        )
        self._require(
            run_readback.get("task_id") == experiment_task_id
            and run_readback.get("run_id") == experiment_run_id,
            "Research owner readback drifted from the handoff receipt identity",
        )
        case["authority_readback"] = {
            "task_id": run_readback.get("task_id"),
            "run_id": run_readback.get("run_id"),
        }
        case["next_consumer"] = {
            "consumer": "research-orchestrator-svc",
            "receipt_type": "ExperimentRun",
            "receipt_id": experiment_run_id,
        }

        self.chain["experiment_task_id"] = experiment_task_id
        self.chain["experiment_run_id"] = experiment_run_id

    # -- Loop 7: Consultation -> deployed OpenClaw provider -> Governance ---

    def _case_consultation_governance_handoff(self, case: dict[str, Any]) -> None:
        owners = self._at(
            "consultation_openclaw_governance.owner_compose_identities",
            lambda: self._service_identities(OWNER_SERVICES["consultation_governance_handoff"]),
        )
        case["owner"] = owners

        request_id = f"cr-l12-hl-{self.run_token}"
        req_payload = {
            "request_id": request_id,
            "tenant_id": self.consultation_tenant_id,
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

        memos = self._at(
            "consultation.supervised_executor_published_memo",
            lambda: self._poll(
                f"published consultation memo for {request_id}",
                lambda: self._http_json(
                    self.consultation_url,
                    f"/api/consult/memos?request_id={urllib.parse.quote(request_id, safe='')}",
                    headers=self._consultation_headers(),
                ),
                lambda value: len(value) == 1 and value[0].get("status") == "published",
            ),
        )
        self._require(len(memos) == 1, "expected exactly one published ConsultMemo")
        memo = memos[0]
        self._require(memo.get("status") == "published", "ConsultMemo was not published")
        case["authority_readback"] = {"memo_id": memo.get("memo_id"), "request_id": memo.get("request_id")}

        handoffs = self._at(
            "consultation.governance_handoff_acknowledged",
            lambda: self._poll(
                f"acknowledged Governance handoff for {request_id}",
                lambda: self._http_json(
                    self.consultation_url,
                    f"/api/consult/handoffs?request_id={urllib.parse.quote(request_id, safe='')}",
                    headers=self._consultation_headers(),
                ),
                lambda value: len(value) == 1 and value[0].get("status") == "acknowledged",
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
        case["terminal_output"] = {
            "type": "ConsultMemo",
            "id": memo.get("memo_id"),
            "governance_handoff_id": gov_handoff.get("handoff_id"),
        }
        case["next_consumer"] = {
            "consumer": "governance",
            "receipt_type": "ConsultGateHandoff",
            "receipt_id": gov_handoff.get("handoff_id"),
        }

        # ``consultation-svc`` supervises the real executor inside its own
        # container.  Restart that owner, then prove durable API readback did
        # not gain a second memo or Governance handoff.
        replay = self._at(
            "consultation.supervised_executor_restart_replay",
            lambda: self._restart_service("consultation-svc"),
        )
        replayed_memos = self._at(
            "consultation.restart_memo_readback",
            lambda: self._poll(
                f"one durable memo after consultation restart for {request_id}",
                lambda: self._http_json(
                    self.consultation_url,
                    f"/api/consult/memos?request_id={urllib.parse.quote(request_id, safe='')}",
                    headers=self._consultation_headers(),
                ),
                lambda value: len(value) == 1 and value[0].get("memo_id") == memo.get("memo_id"),
            ),
        )
        replayed_handoffs = self._at(
            "consultation.restart_governance_readback",
            lambda: self._poll(
                f"one acknowledged Governance handoff after consultation restart for {request_id}",
                lambda: self._http_json(
                    self.consultation_url,
                    f"/api/consult/handoffs?request_id={urllib.parse.quote(request_id, safe='')}",
                    headers=self._consultation_headers(),
                ),
                lambda value: len(value) == 1
                and value[0].get("handoff_id") == gov_handoff.get("handoff_id")
                and value[0].get("status") == "acknowledged",
            ),
        )
        self._require(
            len(replayed_memos) == len(replayed_handoffs) == 1,
            "consultation restart replay duplicated a durable memo or Governance handoff",
        )
        case["next_consumer"]["replay"] = replay

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


def test_deployed_source_posture_and_egress_readback(
    deployed_human_learning_e2e: DeployedHumanLearningHarness,
) -> None:
    """Verify live deployed source scheduler, controller mode, and external egress deny posture."""

    source_url = os.getenv("PANTHEON_L12_SOURCE_INGEST_URL", "http://127.0.0.1:18097").rstrip("/")
    body = deployed_human_learning_e2e._http_json(source_url, "/readyz", expected=(200,))
    assert body.get("service") == "pantheon-source-ingest"
    assert body.get("ready") is True
    dependencies = body.get("dependencies") or {}
    source_freshness = dependencies.get("source_freshness") or {}
    assert source_freshness.get("provider_egress_attempted") is False
    source_search_posture = dependencies.get("source_search_posture") or {}
    assert source_search_posture.get("mode") == "dev"

    source_identity = deployed_human_learning_e2e._service_identity("source-ingest")
    assert source_identity["state"] == "running"
    assert source_identity["health"] == "healthy"

    scheduler_identity = deployed_human_learning_e2e._service_identity("source-ingest-scheduler")
    assert scheduler_identity["state"] == "running"
    assert scheduler_identity["health"] == "healthy"

    source_inspect = json.loads(
        deployed_human_learning_e2e._command(["docker", "inspect", source_identity["container_id"]])
    )[0]
    source_env = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in (source_inspect.get("Config") or {}).get("Env", [])
        if "=" in item
    }
    assert source_env.get("PANTHEON_EXTERNAL_EGRESS") == "deny"

    scheduler_inspect = json.loads(
        deployed_human_learning_e2e._command(["docker", "inspect", scheduler_identity["container_id"]])
    )[0]
    scheduler_env = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in (scheduler_inspect.get("Config") or {}).get("Env", [])
        if "=" in item
    }
    assert scheduler_env.get("SOURCE_INGEST_CONTROLLER_MODE") == "reconcile_only"
    assert scheduler_env.get("SOURCE_INGEST_CONTROLLER_MAX_TICKS") == "0"


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
        "services.consultation.provider",
        "services.consultation.workflow_executor",
        "services.consultation.workflow_state",
        "services.governance.main",
        "services.governance.record_store",
        "services.research.main",
        "candidate_experiment_handoff",
        "research_candidate_client",
        "consultation_provider",
    }
    assert not banned_modules.intersection(imported_modules)
    assert "TestClient" not in imported_names
    assert not {
        "FakeOpenClawRiskProvider",
        "ExecutorConfig",
        "HttpContributionProvider",
        "WorkflowStateStore",
        "execute_claim",
        "run_consultation_tick",
    }.intersection(imported_names)
    assert not {"tmp_path", "tmpdir", "monkeypatch"}.intersection(fixture_arguments)
    assert not {
        "Mock",
        "MagicMock",
        "patch",
        "TestClient",
        "create_openclaw_adapter_app",
        "execute_claim",
        "run_consultation_tick",
    }.intersection(
        called_names
    )
    assert [name for name in function_names if name.startswith("test_deployed_")] == [
        "test_deployed_agora_interaction_evidence_identity_chain",
        "test_deployed_imitation_research_handoff_identity_chain",
        "test_deployed_consultation_governance_handoff_identity_chain",
        "test_deployed_human_learning_chain_identity_correlation",
        "test_deployed_source_posture_and_egress_readback",
        "test_deployed_suite_has_no_fixture_or_product_store_shortcut",
    ]
    assert "subprocess.run" in inspect.getsource(DeployedHumanLearningHarness._command)
    assert "docker" in inspect.getsource(DeployedHumanLearningHarness._compose_argv)
    assert "restart" in inspect.getsource(DeployedHumanLearningHarness._restart_service)
