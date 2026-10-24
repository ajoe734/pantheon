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

from l12_owner_auth import (
    HostedPause,
    bearer,
    compose_file_args,
    hosted_chain_enabled,
    hosted_receipt_fields,
    human_token,
    load_hosted_input,
    persona_create_identity,
    persona_writer_identity,
    require_decided_approval,
    token_roles,
    token_subject,
)

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


TEACHING_METRIC_KEYS = ("total_return", "sharpe_ratio", "max_drawdown")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class NoGenuineControlEdit(RuntimeError):
    """Teaching holds no eligible real operator control edit on a real candidate snapshot."""

    def __init__(self, detail: str, resource_ids: Mapping[str, Any], reason: str = "no_control_edit") -> None:
        super().__init__(detail)
        self.reason = reason
        self.resource_ids = dict(resource_ids)


def teaching_agora_content(
    *,
    terminal: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    operator: Mapping[str, Any],
    registry_id: str,
    strategy_id: str,
    checksum: str,
    hosted: bool = False,
    executor_subject: str = "",
    service_subjects: Sequence[str] = (),
    pre_edit_evaluation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Agora content from one genuine operator control edit on a real Teaching candidate.

    BC accepts only operator/approver approve|edit feedback on a candidate or paper
    target. So the feedback is the append-only ``control_patch`` event a verified
    human authority wrote (event actor must equal the issuer-authenticated OPERATOR
    subject, ``actor_type`` user, never a service), the target is the Teaching
    candidate snapshot, and the approved source Spec stays only as lineage. The
    Teaching validation outcome (even a failed one) is preserved in ``teaching`` and
    is not the decision. Nothing is relabelled: with no such event or candidate the
    caller pauses with the actual IDs instead of inventing a positive sample.

    Hosted mode: the demonstration is the edit a human made in the Management UI. An
    edit counts only when its actor is a ``user`` session subject that differs from the
    hosted executor token subject and from every known service subject; the executor's
    own (or a service's) edit is rejected with a named reason. The BC observation is the
    evaluation state captured *before* that edit (``pre_edit_evaluation``), never the
    post-edit metrics.
    """

    session_id = str(terminal.get("session_id") or "")
    eval_identity = str(terminal.get("eval_identity") or "")
    evaluation = terminal.get("evaluation_result")
    if not session_id or not eval_identity or not isinstance(evaluation, Mapping):
        raise RuntimeError("Teaching terminal readback lacks session_id, eval_identity or evaluation_result")
    metrics = evaluation.get("metrics") if isinstance(evaluation.get("metrics"), Mapping) else {}
    vector = [_finite_number(metrics.get(key)) for key in TEACHING_METRIC_KEYS]
    if any(value is None for value in vector):
        raise RuntimeError("Teaching evaluation lacks finite total_return/sharpe_ratio/max_drawdown")
    validation = evaluation.get("validation_status")
    if validation not in {"passed", "failed"}:
        raise RuntimeError(f"Teaching evaluation validation_status {validation!r} is not terminal")
    ids = {"teaching_session_id": session_id, "eval_identity": eval_identity, "eval_id": evaluation.get("eval_id")}
    preview = terminal.get("preview") if isinstance(terminal.get("preview"), Mapping) else {}
    proof = preview.get("evaluation_proof") if isinstance(preview.get("evaluation_proof"), Mapping) else {}
    digest = str(proof.get("candidate_digest") or "").strip()
    snapshot_at = str(preview.get("candidate_snapshot_at") or "").strip()
    if not digest or not snapshot_at:
        raise NoGenuineControlEdit("Teaching exposes no real candidate snapshot to attach feedback to", ids)
    operator_id, operator_roles = str(operator.get("sub") or ""), operator.get("roles") or []
    if hosted:
        executor_subject = executor_subject.strip()
        if not executor_subject:
            raise NoGenuineControlEdit("hosted executor token subject is unknown", ids, "executor_subject_unknown")
        refused = {executor_subject, *(str(s) for s in service_subjects if s)}
        human_events = [
            e for e in events if e.get("event_type") == "control_patch" and e.get("actor_type") == "user"
            and str(e.get("actor") or "") and str(e.get("actor")) not in refused
        ]
        own = [e for e in events if e.get("event_type") == "control_patch" and e.get("actor") == executor_subject]
        service = [
            e for e in events if e.get("event_type") == "control_patch"
            and (e.get("actor_type") != "user" or str(e.get("actor")) in refused) and e.get("actor") != executor_subject
        ]
        if not human_events:
            if own:
                raise NoGenuineControlEdit(
                    "the only control_patch was made with the hosted executor identity, not a human session",
                    ids, "executor_edit_rejected",
                )
            if service:
                raise NoGenuineControlEdit(
                    "the only control_patch was made by a service identity, not a human session",
                    ids, "service_identity_edit_rejected",
                )
            raise NoGenuineControlEdit(
                "waiting for the operator to edit Teaching controls in the Management UI", ids, "awaiting_human_edit"
            )
        operator_id = str(human_events[0].get("actor"))
    elif not operator_id or "operator" not in operator_roles:
        raise NoGenuineControlEdit("no issuer-authenticated OPERATOR identity to verify the edit actor", ids)
    observation_vector = vector
    if hosted:
        pre_metrics = (pre_edit_evaluation or {}).get("metrics") if isinstance(pre_edit_evaluation, Mapping) else None
        pre_metrics = pre_metrics if isinstance(pre_metrics, Mapping) else {}
        observation_vector = [_finite_number(pre_metrics.get(key)) for key in TEACHING_METRIC_KEYS]
        if any(value is None for value in observation_vector):
            raise NoGenuineControlEdit(
                "no pre-edit evaluation metrics; post-edit metrics cannot explain the action", ids, "pre_edit_evaluation_missing"
            )
        if (pre_edit_evaluation or {}).get("eval_id") == evaluation.get("eval_id"):
            raise NoGenuineControlEdit(
                "the pre-edit evaluation is the post-edit evaluation", ids, "pre_edit_evaluation_missing"
            )
    controls = proof.get("controls")
    if (
        not isinstance(controls, list)
        or not proof.get("controls_digest")
        or hashlib.sha256(
            json.dumps(controls, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        != proof["controls_digest"]
    ):
        raise NoGenuineControlEdit("Teaching evaluation proof controls do not match controls_digest", ids)
    snapshot_controls = {
        str(c.get("parameter_key")): c.get("current_value") for c in controls if isinstance(c, Mapping)
    }
    snapshot_time = _parse_time(snapshot_at)
    candidates = [
        event
        for event in events
        if event.get("event_type") == "control_patch"
        and event.get("actor_type") == "user"
        and event.get("actor") == operator_id
        and event.get("event_id")
        and isinstance(event.get("patch_delta"), list)
        and event["patch_delta"]
    ]
    if not candidates:
        raise NoGenuineControlEdit(
            "Teaching holds no control_patch event written by the verified OPERATOR", ids
        )
    edit: Mapping[str, Any] | None = None
    steps: list[dict[str, Any]] = []
    for event in sorted(candidates, key=lambda e: str(e.get("timestamp") or e.get("emitted_at") or ""), reverse=True):
        stamp = _parse_time(str(event.get("timestamp") or event.get("emitted_at") or ""))
        if stamp is None or snapshot_time is None or stamp > snapshot_time:
            continue  # not provably part of this candidate snapshot
        if hosted:
            pre_at = _parse_time(str((pre_edit_evaluation or {}).get("evaluated_at") or ""))
            if pre_at is None or pre_at > stamp:
                continue  # the observation must predate the human edit it explains
        event_steps: list[dict[str, Any]] = []
        for delta in event["patch_delta"]:
            before, after = _finite_number(delta.get("previous_value")), _finite_number(delta.get("new_value"))
            key = str(delta.get("parameter_key") or "")
            if key and before is not None and after is not None and before != after:
                if _finite_number(snapshot_controls.get(key)) == after:
                    event_steps.append(
                        {
                            "observation": [*observation_vector, before],
                            "action": f"set_{key}",
                            "reward": vector[0],
                            "feedback_event_id": f"{event['event_id']}:{key}",
                            "new_value": delta["new_value"],
                        }
                    )
        if len({step["action"] for step in event_steps}) >= 2:
            edit, steps = event, event_steps
            break
    if edit is None:
        raise NoGenuineControlEdit(
            "no OPERATOR control edit with two real non-zero deltas is bound to the candidate snapshot and its controls",
            {**ids, "candidate_snapshot_at": snapshot_at},
        )
    event_id = str(edit["event_id"])
    return {
        "actor_id": operator_id,
        "actor_role": "operator",
        "decision": "edit",
        "target": {
            "strategy_id": strategy_id,
            "artifact_version": snapshot_at,
            "artifact_type": "strategy_spec",
            "lineage_ref": f"teaching-candidate:{session_id}:{digest}",
            "promotion_state": "candidate",
        },
        "strategy_id": strategy_id,
        "source_strategy_spec_id": registry_id,
        "teaching_session_id": session_id,
        "control_edit_event_id": event_id,
        "teaching": {
            "session_id": session_id,
            "eval_identity": eval_identity,
            "eval_id": evaluation.get("eval_id"),
            "validation_status": validation,
            "metrics": {key: metrics[key] for key in TEACHING_METRIC_KEYS},
            "registry_checksum": checksum,
        },
        "steps": steps,
    }


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
        # Hosted chain mode: one phase per process; deterministic IDs from the chain id.
        self.hosted = hosted_chain_enabled()
        self.hosted_input = load_hosted_input(("B_learning",)) if self.hosted else None
        self.paused: dict[str, Any] | None = None
        if self.hosted_input is not None:
            self.run_token = self.hosted_input.chain_id
        self.tenant_id = os.getenv(
            "PANTHEON_L12_HUMAN_LEARNING_TENANT_ID",
            os.getenv("PANTHEON_L12_TENANT_ID", "pantheon-local"),
        ).strip()
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
        self.persona_url = os.getenv(
            "PANTHEON_L12_PERSONA_URL", "http://127.0.0.1:18002"
        ).rstrip("/")
        self.bff_bearer = self._resolve_bff_bearer()
        self.training_url = os.getenv("PANTHEON_L12_TRAINING_URL", "http://127.0.0.1:18099").rstrip("/")
        self.agora_handoff_token = self._service_token(
            "PANTHEON_L12_AGORA_HANDOFF_TOKEN", "pantheon-local-agora-handoff-service-token"
        )
        self.policy_learning_token = self._service_token(
            "PANTHEON_L12_POLICY_LEARNING_TOKEN", "pantheon-local-policy-learning-service"
        )
        self.consultation_token = self._service_token(
            "PANTHEON_L12_CONSULTATION_TOKEN", "pantheon-local-consultation-service"
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
        if self.hosted:
            raise RuntimeError(
                "hosted chain needs a real BFF bearer (PANTHEON_L12_BFF_BEARER or dev-login "
                "credentials); the isolated stub bearer is never synthesized"
            )
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

    def _service_token(self, name: str, isolated_default: str) -> str:
        """Isolated stacks fall back to their local service token; hosted never guesses one."""
        value = os.getenv(name)
        if self.hosted:
            if not (value or "").strip():
                raise RuntimeError(f"{name} must be supplied for the hosted chain")
            return value.strip()
        return value if value is not None else isolated_default

    def _rid(self, default: str, *parts: str) -> str:
        return self.hosted_input.id(*parts) if self.hosted_input is not None else default

    def _chain_labels(self) -> dict[str, Any]:
        return self.hosted_input.labels() if self.hosted_input is not None else {}

    def _restart_or_skip(self, service: str) -> dict[str, Any]:
        """Hosted never restarts a shared service; isolated keeps the real restart proof."""
        if self.hosted:
            return {"service": service, "skipped": "hosted_shared_service_restart_excluded"}
        return self._restart_service(service)

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

        if self.hosted:
            raise RuntimeError("hosted chain mode never restarts a shared Compose service")
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
        except (BoundaryFailure, HostedPause):
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

    def _hosted_resource_ids(self) -> dict[str, Any]:
        keys = (
            "teaching_session_id",
            "registry_id",
            "checksum",
            "evidence_id",
            "dataset_version_id",
            "agora_handoff_id",
            "candidate_id",
            "experiment_task_id",
            "experiment_run_id",
            "consult_request_id",
            "memo_id",
            "governance_handoff_id",
            "h2_consent_ref",
        )
        return {key: self.chain.get(key) for key in keys}

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
            "schema_version": "pantheon.l12.human-learning.deployed-e2e.v1",
            "task_id": TASK_ID,
            "status": status,
            "started_at": self.started_at,
            "completed_at": _utc_now() if status in {"passed", "failed", "paused"} else None,
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

    # -- Loop 5: Agora interaction evidence -> durable handoff -> intake ----

    def _training_headers(self) -> dict[str, str]:
        token = os.getenv("PANTHEON_L12_TRAINING_TOKEN", "").strip()
        if not token:
            raise RuntimeError("PANTHEON_L12_TRAINING_TOKEN is unset or empty")
        return {
            "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
            "X-Tenant-Id": self.tenant_id,
            "X-Pantheon-Service": "training-session-preview-worker",
        }

    def _pre_edit_evaluation(
        self, session_id: str, eval_id: Any, evaluated_at: Any, headers: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """Read the pre-edit evaluation back from the Training owner by its scalar id (owner GET is truth)."""

        eval_id = str(eval_id or "").strip()
        if not eval_id:
            return None
        bundle = self._http_json(
            self.training_url, f"/api/training/previews/{urllib.parse.quote(session_id, safe='')}", headers=headers
        )
        evaluations = bundle.get("evaluations") if isinstance(bundle, Mapping) else None
        preview = evaluations.get(eval_id) if isinstance(evaluations, Mapping) else None
        result = preview.get("evaluation_result") if isinstance(preview, Mapping) else None
        if not isinstance(result, Mapping) or result.get("eval_id") != eval_id:
            return None
        return {
            "eval_id": eval_id,
            "eval_identity": preview.get("eval_identity") or result.get("eval_identity"),
            "evaluated_at": str(evaluated_at or preview.get("candidate_snapshot_at") or ""),
            "metrics": result.get("metrics"),
        }

    def _hosted_teaching_inputs(self) -> dict[str, Any]:
        """Read the chain's real Teaching result (loop 4) and build the Agora evidence from it."""
        chain = self.hosted_input
        assert chain is not None
        wanted = {
            key: chain.predecessor(key)
            for key in ("teaching_session_id", "registry_id", "checksum", "strategy_id")
        }
        missing = [key for key, value in wanted.items() if not value]
        if missing:
            raise HostedPause(
                "agora_interaction_evidence",
                f"B_learning needs A_research predecessors {missing}; none supplied",
            )
        session = urllib.parse.quote(wanted["teaching_session_id"], safe="")
        headers = self._training_headers()
        terminal = self._http_json(
            self.training_url, f"/api/training/sessions/{session}/terminal-readback", headers=headers
        )
        events = self._http_json(self.training_url, f"/api/training/sessions/{session}/events", headers=headers)
        self._require(
            terminal.get("session_id") == wanted["teaching_session_id"],
            "Teaching terminal readback does not match the predecessor session",
        )
        expected_eval = chain.predecessor("eval_identity")
        self._require(
            not expected_eval or terminal.get("eval_identity") == expected_eval,
            "Teaching eval_identity drifted from the A_research receipt",
        )
        operator_token = human_token("OPERATOR")
        pre_edit = self._pre_edit_evaluation(
            wanted["teaching_session_id"], chain.predecessors.get("pre_edit_eval_id"),
            chain.predecessors.get("pre_edit_evaluated_at"), headers,
        )
        writer = persona_writer_identity()
        try:
            content = teaching_agora_content(
                terminal=terminal,
                events=events if isinstance(events, list) else [],
                operator={"sub": token_subject(operator_token), "roles": token_roles(operator_token)},
                hosted=True,
                executor_subject=token_subject(operator_token),
                service_subjects=[writer[1]] if writer else [],
                pre_edit_evaluation=pre_edit if isinstance(pre_edit, Mapping) else None,
                registry_id=wanted["registry_id"],
                strategy_id=wanted["strategy_id"],
                checksum=wanted["checksum"],
            )
        except NoGenuineControlEdit as absent:
            raise HostedPause(
                "agora_interaction_evidence",
                f"{absent.reason}: {absent}",
                {**absent.resource_ids, "registry_id": wanted["registry_id"], "pause_reason": absent.reason},
            ) from absent
        # Replays must be byte-identical for owner idempotency: take the capture time
        # from the Teaching record itself, never from this process's clock.
        evaluation = terminal.get("evaluation_result") or {}
        captured_at = next(
            (
                str(value)
                for value in (
                    evaluation.get("evaluated_at"),
                    evaluation.get("completed_at"),
                    terminal.get("updated_at"),
                    terminal.get("started_at"),
                )
                if value
            ),
            "",
        )
        self._require(bool(captured_at), "Teaching readback carries no timestamp for captured_at")
        self.chain.update(
            {
                "teaching_session_id": wanted["teaching_session_id"],
                "registry_id": wanted["registry_id"],
                "checksum": wanted["checksum"],
                "strategy_id": wanted["strategy_id"],
                "teaching_eval_identity": terminal.get("eval_identity"),
            }
        )
        return {
            "persona_id": str(terminal.get("persona_id") or ""),
            "session_id": wanted["teaching_session_id"],
            "content": content,
            "control_edit_event_id": content["control_edit_event_id"],
            "source_refs": [
                f"session:{wanted['teaching_session_id']}",
                f"control_edit:{content['control_edit_event_id']}",
                f"teaching_eval:{terminal.get('eval_identity')}",
                f"registry:{wanted['registry_id']}",
            ],
            "captured_at": captured_at,
        }

    def _case_agora_interaction_evidence(self, case: dict[str, Any]) -> None:
        if self.hosted_input is not None:
            hosted = self._hosted_teaching_inputs()
            persona_id = hosted["persona_id"]
            self._require(bool(persona_id), "Teaching readback carries no persona_id")
            self._ensure_persona(
                persona_id, name=f"Hosted Persona {persona_id}", mandate="Agora human learning advisor"
            )
        else:
            hosted = None
            self._ensure_persona(
                "persona-advisor",
                name="Advisor Persona",
                mandate="Agora human learning advisor",
            )
        # A new genuine control edit is a new evidence version: its identity comes from that
        # event, never from re-posting changed content under an already admitted evidence ID.
        evidence_id = self._rid(
            f"ev-l12-hl-{self.run_token}", "ev", *([hosted["control_edit_event_id"]] if hosted else [])
        )
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
        if hosted is not None:
            ev_payload.update(
                persona_id=hosted["persona_id"],
                session_id=hosted["session_id"],
                content=hosted["content"],
                source_refs=hosted["source_refs"],
                captured_at=hosted["captured_at"],
            )
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
        # Hosted replays of the same evidence_id return the existing admission.
        self._require(
            self.hosted or created.get("status") == "created", "evidence submission was not created"
        )
        dataset = created["data"]
        dataset_version_id = str(dataset["dataset_version_id"])
        self._require(dataset_version_id.startswith("dsv-"), "dataset_version_id has an unexpected shape")
        self._require(dataset.get("dataset_kind") == "learn", "evidence did not create a learning dataset")
        case["trigger"] = {
            "type": "AgoraInteractionEvidence",
            "id": evidence_id,
            "dataset_version_id": dataset_version_id,
        }

        # Hosted: an earlier process of this chain (e.g. one that paused at H2) may
        # already have drained the handoff, so it is no longer pending; reuse the
        # processed candidate for this exact dataset_version_id instead.
        prior_candidate = None
        if self.hosted:
            prior_candidate = self._at(
                "policy_learning.prior_candidate_readback",
                lambda: next(
                    (
                        item
                        for item in self._http_json(
                            self.policy_learning_url,
                            "/api/policy-learning/candidates",
                            headers=self._policy_learning_headers(),
                        )
                        if (item.get("dataset_ref") or {}).get("dataset_version_id") == dataset_version_id
                    ),
                    None,
                ),
            )
        if prior_candidate is not None:
            handoff_id = str(prior_candidate.get("handoff_id") or "")
            self._require(bool(handoff_id), "prior candidate lost its Agora handoff_id")
        else:
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
            lambda: self._restart_or_skip("policy-learning-shadow-eval-scheduler"),
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
            lambda: self._restart_or_skip("policy-learning-shadow-eval-scheduler"),
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

    def _hosted_h2_consent(self) -> dict[str, Any]:
        """H2 is a data-owner consent with no owner GET: require its recorded reference, never infer it."""
        assert self.hosted_input is not None
        ref = self.hosted_input.decision("H2") or {}
        required = ("record_ref", "decided_by", "recorded_at")
        if not all(str(ref.get(key) or "").strip() for key in required):
            raise HostedPause(
                "consultation_request",
                "H2 Agora synthesis consent is not recorded (needs record_ref, decided_by, recorded_at)",
                self._hosted_resource_ids(),
            )
        return {key: str(ref[key]) for key in required}

    def _case_consultation_governance_handoff(self, case: dict[str, Any]) -> None:
        owners = self._at(
            "consultation_openclaw_governance.owner_compose_identities",
            lambda: self._service_identities(OWNER_SERVICES["consultation_governance_handoff"]),
        )
        case["owner"] = owners

        request_id = self._rid(f"cr-l12-hl-{self.run_token}", "cr")
        hosted_metadata: dict[str, Any] = {}
        requested_by = {"actor_type": "operator", "actor_id": "l12-current-e2e-operator"}
        if self.hosted_input is not None:
            h2 = self._hosted_h2_consent()
            requested_by = {"actor_type": "operator", "actor_id": token_subject(human_token("OPERATOR"))}
            # The consumer record must carry the actual predecessors so a reader can
            # follow Registry -> Teaching -> Agora -> candidate -> consultation by ID.
            hosted_metadata = {
                **self._chain_labels(),
                "h2_consent_ref": h2,
                **{
                    key: self.chain.get(key) or self.hosted_input.predecessor(key)
                    for key in (
                        "registry_id",
                        "checksum",
                        "teaching_session_id",
                        "teaching_eval_identity",
                        "evidence_id",
                        "dataset_version_id",
                        "candidate_id",
                        "experiment_run_id",
                    )
                },
            }
            missing = [key for key, value in hosted_metadata.items() if value in (None, "")]
            self._require(not missing, f"consultation predecessors missing: {missing}")
            self.chain["h2_consent_ref"] = h2["record_ref"]
        req_payload = {
            "request_id": request_id,
            "tenant_id": self.consultation_tenant_id,
            "request_type": "strategy_review",
            "requested_by": requested_by,
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
            "metadata": {
                "paper_only": True,
                "agora_handoff_id": self.chain.get("agora_handoff_id", ""),
                **hosted_metadata,
            },
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
        # Hosted: the deterministic request may already exist from an earlier process.
        self._require(
            self.hosted or created.get("status") == "draft",
            "consult request was not created in draft status",
        )
        if self.hosted_input is not None:
            stored = self._at(
                "consultation.predecessor_metadata_readback",
                lambda: self._http_json(
                    self.consultation_url,
                    f"/api/consult/requests/{urllib.parse.quote(request_id, safe='')}",
                    headers=self._consultation_headers(),
                ),
            )
            persisted = stored.get("metadata") or {}
            drifted = {
                key: persisted.get(key) for key, value in hosted_metadata.items() if persisted.get(key) != value
            }
            self._require(not drifted, f"ConsultRequest did not persist predecessor references: {sorted(drifted)}")
            created = stored

        if not self.hosted or created.get("status") == "draft":
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
            lambda: self._restart_or_skip("consultation-svc"),
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

    if deployed_human_learning_e2e.paused is not None:
        pytest.skip("hosted chain paused; correlation needs a completed phase")
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
