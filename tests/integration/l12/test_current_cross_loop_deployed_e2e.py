"""Deployed cross-loop proof for the current Pantheon twelve-loop system.

This suite never creates an in-process FastAPI application or a replacement
store.  It consumes IDs emitted by the already-completed per-loop deployed
runs and reads those IDs back from the real HTTP owners.  Its one write case
uses Research's real idempotency boundary to prove that a concurrent replay
does not create a second task or run.

Set ``PANTHEON_L12_CROSS_LOOP_DEPLOYED_E2E=1`` and provide the service URLs,
an input manifest, and a temporary evidence output path documented by
``Settings.from_env``.  The governed owner copies the resulting evidence only
after the deployed run passes; this test never rewrites checked-in evidence.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest


TASK_ID = "L12-CURRENT-CROSS-LOOP-E2E-20260814"
INPUT_SCHEMA = "pantheon.l12.cross-loop-input.v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKED_IN_EVIDENCE_ROOT = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "twelve-loop-current"
    / "cross-loop"
)

pytestmark = pytest.mark.skipif(
    os.getenv("PANTHEON_L12_CROSS_LOOP_DEPLOYED_E2E", "").strip().lower()
    not in {"1", "true", "yes"},
    reason="set PANTHEON_L12_CROSS_LOOP_DEPLOYED_E2E=1 for deployed HTTP proof",
)


class DeployedProofError(AssertionError):
    """A real owner boundary or exact-identity assertion failed."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DeployedProofError(f"{name} is required for the deployed proof")
    return value


def _required_id(section: Mapping[str, Any], field_name: str) -> str:
    value = str(section.get(field_name) or "").strip()
    if not value:
        raise DeployedProofError(f"cross-loop input field {field_name!r} is required")
    return value


def _contains_exact(value: Any, expected: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_exact(item, expected) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact(item, expected) for item in value)
    return str(value) == expected


def _require_exact(value: Any, expected: str, *, boundary: str) -> None:
    if not _contains_exact(value, expected):
        raise DeployedProofError(
            f"{boundary} did not preserve exact identity {expected!r}"
        )


@dataclass(frozen=True)
class Settings:
    urls: dict[str, str]
    tokens: dict[str, str]
    tenant_ids: dict[str, str]
    timeouts: dict[str, float]
    expected_sha: str
    input_manifest: dict[str, Any]
    evidence_output: Path

    @classmethod
    def from_env(cls) -> "Settings":
        services = (
            "research",
            "runtime",
            "policy_learning",
            "telemetry",
            "incidents",
            "evolution",
            "bff",
        )
        urls = {
            service: _required_env(
                f"PANTHEON_L12_CROSS_{service.upper()}_URL"
            ).rstrip("/")
            for service in services
        }
        for service, url in urls.items():
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise DeployedProofError(
                    f"{service} URL must be a deployed HTTP origin, got {url!r}"
                )

        expected_sha = _required_env("PANTHEON_L12_CROSS_EXPECTED_SHA")
        if len(expected_sha) != 40 or any(
            character not in "0123456789abcdef" for character in expected_sha
        ):
            raise DeployedProofError(
                "PANTHEON_L12_CROSS_EXPECTED_SHA must be a lowercase 40-character SHA"
            )

        input_path = Path(_required_env("PANTHEON_L12_CROSS_INPUT")).resolve()
        if not input_path.is_file():
            raise DeployedProofError("PANTHEON_L12_CROSS_INPUT must be a JSON file")
        try:
            manifest = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeployedProofError(f"cross-loop input is invalid: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != INPUT_SCHEMA:
            raise DeployedProofError(
                f"cross-loop input must use schema_version {INPUT_SCHEMA!r}"
            )
        if manifest.get("expected_sha") != expected_sha:
            raise DeployedProofError(
                "cross-loop input expected_sha does not match the deployed revision"
            )
        for section_name in (
            "research_to_runtime",
            "human_learning",
            "health_incident",
        ):
            if not isinstance(manifest.get(section_name), dict):
                raise DeployedProofError(
                    f"cross-loop input section {section_name!r} is required"
                )

        output = Path(_required_env("PANTHEON_L12_CROSS_EVIDENCE_OUTPUT")).resolve()
        try:
            output.relative_to(CHECKED_IN_EVIDENCE_ROOT.resolve())
        except ValueError:
            pass
        else:
            raise DeployedProofError(
                "deployed test output must be temporary; checked-in evidence is owner-managed"
            )

        tokens = {
            service: _required_env(
                f"PANTHEON_L12_CROSS_{service.upper()}_TOKEN"
            )
            for service in services
        }
        default_tenant_id = _required_env("PANTHEON_L12_CROSS_TENANT_ID")
        tenant_ids = {
            service: os.getenv(
                f"PANTHEON_L12_CROSS_{service.upper()}_TENANT_ID",
                default_tenant_id,
            ).strip()
            or default_tenant_id
            for service in services
        }
        timeouts = {
            service: float(
                os.getenv(
                    f"PANTHEON_L12_CROSS_{service.upper()}_HTTP_TIMEOUT_SECONDS",
                    "90" if service == "bff" else "30",
                )
            )
            for service in services
        }
        if any(
            not math.isfinite(timeout) or timeout <= 0 or timeout > 300
            for timeout in timeouts.values()
        ):
            raise DeployedProofError(
                "owner HTTP timeouts must be greater than zero and at most 300 seconds"
            )
        return cls(
            urls=urls,
            tokens=tokens,
            tenant_ids=tenant_ids,
            timeouts=timeouts,
            expected_sha=expected_sha,
            input_manifest=manifest,
            evidence_output=output,
        )


class HttpOwners:
    """HTTP client that records every real service boundary it crosses."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.boundaries: list[dict[str, Any]] = []

    def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: set[int] = frozenset({200}),
    ) -> Any:
        url = f"{self.settings.urls[service]}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        encoded = None if body is None else _canonical_json(dict(body)).encode("utf-8")
        final_headers = {
            "Accept": "application/json",
            "X-Pantheon-Tenant-Id": self.settings.tenant_ids[service],
            "X-Tenant-Id": self.settings.tenant_ids[service],
            **dict(headers or {}),
        }
        token = self.settings.tokens.get(service, "")
        if token:
            final_headers["Authorization"] = f"Bearer {token}"
        if encoded is not None:
            final_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=final_headers,
            method=method,
        )
        started_at = _utc_now()
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.timeouts[service],
            ) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as exc:
            raise DeployedProofError(
                f"{method} {service}{path} transport failed: {exc}"
            ) from exc
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        self.boundaries.append(
            {
                "ended_at": _utc_now(),
                "method": method,
                "path": path,
                "service": service,
                "started_at": started_at,
                "status": status,
            }
        )
        if status not in expected:
            raise DeployedProofError(
                f"{method} {service}{path} returned {status}, expected "
                f"{sorted(expected)}: {_canonical_json(payload)}"
            )
        return payload


@dataclass
class Evidence:
    settings: Settings
    started_at: str = field(default_factory=_utc_now)
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = "running"
    first_failed_boundary: str | None = None
    error: str | None = None

    def write(self, boundaries: list[dict[str, Any]]) -> None:
        payload = {
            "boundaries": boundaries,
            "cases": self.cases,
            "completed_at": _utc_now(),
            "environment": "deployed_http_readback",
            "error": self.error,
            "expected_sha": self.settings.expected_sha,
            "first_failed_boundary": self.first_failed_boundary,
            "live_capital_enabled": False,
            "schema_version": "pantheon.l12.cross-loop.deployed-e2e.v2",
            "started_at": self.started_at,
            "status": self.status,
            "task_id": TASK_ID,
        }
        self.settings.evidence_output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.settings.evidence_output.parent,
            prefix=f".{self.settings.evidence_output.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.settings.evidence_output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class CrossLoopDeployedProof:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = HttpOwners(settings)
        self.evidence = Evidence(settings)
        self.results: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _quote(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    def _simultaneous_requests(
        self,
        service: str,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any],
        expected: set[int],
    ) -> tuple[Any, Any]:
        barrier = threading.Barrier(2, timeout=10.0)

        def send() -> Any:
            barrier.wait()
            return self.http.request(
                service,
                method,
                path,
                body=body,
                expected=expected,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(send) for _ in range(2)]
            return futures[0].result(), futures[1].result()

    def research_to_runtime(self) -> dict[str, Any]:
        if "research_to_runtime" in self.results:
            return self.results["research_to_runtime"]
        section = self.settings.input_manifest["research_to_runtime"]
        run_id = _required_id(section, "research_run_id")
        artifact_id = _required_id(section, "artifact_id")
        binding_id = _required_id(section, "runtime_binding_id")
        run = self.http.request(
            "research",
            "GET",
            f"/api/research-orchestrator/runs/{self._quote(run_id)}",
        )
        artifacts = self.http.request(
            "research",
            "GET",
            f"/api/research-orchestrator/runs/{self._quote(run_id)}/artifacts",
        )
        binding = self.http.request(
            "runtime",
            "GET",
            f"/api/runtime-bindings/{self._quote(binding_id)}",
        )
        _require_exact(run, run_id, boundary="research run authority")
        _require_exact(artifacts, artifact_id, boundary="research artifact authority")
        _require_exact(binding, artifact_id, boundary="runtime binding authority")
        _require_exact(binding, binding_id, boundary="runtime binding authority")
        result = {
            "ids": {
                "artifact_id": artifact_id,
                "research_run_id": run_id,
                "runtime_binding_id": binding_id,
            },
            "status": "passed",
        }
        self.results["research_to_runtime"] = result
        self.evidence.cases["research_to_runtime"] = result
        return result

    def human_learning(self) -> dict[str, Any]:
        if "human_learning" in self.results:
            return self.results["human_learning"]
        section = self.settings.input_manifest["human_learning"]
        candidate_id = _required_id(section, "candidate_id")
        run_id = _required_id(section, "research_run_id")
        candidates = self.http.request(
            "policy_learning",
            "GET",
            "/api/policy-learning/candidates",
        )
        run = self.http.request(
            "research",
            "GET",
            f"/api/research-orchestrator/runs/{self._quote(run_id)}",
        )
        _require_exact(candidates, candidate_id, boundary="Policy Learning candidate owner")
        _require_exact(run, candidate_id, boundary="Research imitation handoff")
        _require_exact(run, run_id, boundary="Research imitation run")
        result = {
            "ids": {"candidate_id": candidate_id, "research_run_id": run_id},
            "status": "passed",
        }
        self.results["human_learning"] = result
        self.evidence.cases["human_learning"] = result
        return result

    def health_incident(self) -> dict[str, Any]:
        if "health_incident" in self.results:
            return self.results["health_incident"]
        section = self.settings.input_manifest["health_incident"]
        event_id = _required_id(section, "telemetry_event_id")
        incident_id = _required_id(section, "incident_id")
        decision_id = _required_id(section, "evolution_decision_id")
        event = self.http.request(
            "telemetry",
            "GET",
            f"/api/telemetry/events/{self._quote(event_id)}",
        )
        incident = self.http.request(
            "incidents",
            "GET",
            f"/api/incidents/{self._quote(incident_id)}",
        )
        decision = self.http.request(
            "evolution",
            "GET",
            f"/api/evolution/proposals/{self._quote(decision_id)}",
        )
        _require_exact(event, event_id, boundary="Telemetry event authority")
        _require_exact(incident, event_id, boundary="Incident telemetry lineage")
        _require_exact(incident, incident_id, boundary="Incident authority")
        _require_exact(decision, incident_id, boundary="Evolution incident lineage")
        _require_exact(decision, decision_id, boundary="Evolution decision authority")
        result = {
            "ids": {
                "evolution_decision_id": decision_id,
                "incident_id": incident_id,
                "telemetry_event_id": event_id,
            },
            "status": "passed",
        }
        self.results["health_incident"] = result
        self.evidence.cases["health_incident"] = result
        return result

    def concurrency(self) -> dict[str, Any]:
        if "concurrency" in self.results:
            return self.results["concurrency"]
        suffix = uuid.uuid4().hex[:12]
        actor_id = f"cross-loop-e2e-owner-{suffix}"
        task_key = f"{TASK_ID}:task:{suffix}"
        task_body = {
            "actor_id": actor_id,
            "constraints": {"capital_mode": "paper", "live_execution": False},
            "idempotency_key": task_key,
            "objective": "Prove replay-safe cross-loop action ownership",
            "source_refs": [{"id": TASK_ID, "type": "canonical_task"}],
            "title": f"Cross-loop concurrency proof {suffix}",
        }
        first_task, replay_task = self._simultaneous_requests(
            "research",
            "POST",
            "/api/research-orchestrator/tasks",
            body=task_body,
            expected={200, 201},
        )
        task_id = _required_id(first_task, "task_id")
        if replay_task != first_task:
            raise DeployedProofError("task idempotency replay changed the owner action")

        run_key = f"{TASK_ID}:run:{suffix}"
        run_body = {
            "actor_id": actor_id,
            "adapter": "stub",
            "dispatch_mode": "stub",
            "idempotency_key": run_key,
            "input_refs": [{"id": task_id, "type": "research_task"}],
            "parameters": {"capital_mode": "paper", "live_execution": False},
            "requested_mode": "stub",
        }
        first_run, replay_run = self._simultaneous_requests(
            "research",
            "POST",
            f"/api/research-orchestrator/tasks/{self._quote(task_id)}/runs",
            body=run_body,
            expected={200, 201},
        )
        run_id = _required_id(first_run, "run_id")
        if replay_run != first_run:
            raise DeployedProofError("run idempotency replay changed the owner action")
        tasks = self.http.request("research", "GET", "/api/research-orchestrator/tasks")
        runs = self.http.request(
            "research",
            "GET",
            "/api/research-orchestrator/runs",
            query={"task_id": task_id},
        )
        task_matches = [item for item in tasks if _contains_exact(item, task_id)]
        run_matches = [item for item in runs if _contains_exact(item, run_id)]
        if len(task_matches) != 1 or len(run_matches) != 1:
            raise DeployedProofError(
                "concurrency replay created a duplicate task or run owner action"
            )
        _require_exact(first_task, actor_id, boundary="Research task owner")
        _require_exact(first_run, actor_id, boundary="Research run owner")
        result = {
            "assertions": {
                "one_owner": True,
                "one_run_action": True,
                "one_task_action": True,
            },
            "ids": {"research_run_id": run_id, "research_task_id": task_id},
            "owner": actor_id,
            "status": "passed",
        }
        self.results["concurrency"] = result
        self.evidence.cases["concurrency"] = result
        return result

    def management_identity(self) -> dict[str, Any]:
        if "management_identity" in self.results:
            return self.results["management_identity"]
        prior = (
            self.research_to_runtime(),
            self.human_learning(),
            self.health_incident(),
            self.concurrency(),
        )
        emitted_ids = sorted(
            {
                identity
                for result in prior
                for identity in result["ids"].values()
            }
        )
        question = (
            "Read back every exact cross-loop identity supplied in the operator "
            "context. Return only identities resolved from owner-visible evidence."
        )
        if any(identity in question for identity in emitted_ids):
            raise DeployedProofError("Management question unexpectedly contains owner IDs")
        request_body = {
            "context": _canonical_json(
                {
                    "cross_loop_ids": emitted_ids,
                    "instruction": "Resolve each identity from owner-visible evidence.",
                }
            ),
            "focus": "overview",
            "question": question,
        }
        idempotency_key = f"{TASK_ID}:{uuid.uuid4().hex}"
        timeout_seconds = float(
            os.getenv("PANTHEON_L12_CROSS_MANAGEMENT_TIMEOUT_SECONDS", "120")
        )
        deadline = time.monotonic() + min(max(timeout_seconds, 10.0), 300.0)
        while True:
            response = self.http.request(
                "bff",
                "POST",
                "/bff/management/nl/ask",
                body=request_body,
                headers={"Idempotency-Key": idempotency_key},
                expected={202},
            )
            data = response.get("data") if isinstance(response, Mapping) else None
            if isinstance(data, Mapping) and data.get("status") == "completed":
                break
            if time.monotonic() >= deadline:
                raise DeployedProofError(
                    "Management NL provider did not complete before the deployed deadline"
                )
            time.sleep(2.0)
        data = response.get("data") if isinstance(response, Mapping) else None
        if not isinstance(data, Mapping):
            raise DeployedProofError("Management NL response has no data object")
        provider_status = data.get("provider_status")
        if not isinstance(provider_status, Mapping):
            raise DeployedProofError("Management NL response has no provider status")
        if provider_status.get("used") is not True or provider_status.get("fallback"):
            raise DeployedProofError(
                "Management NL completed without a real provider answer"
            )
        readback_surface = {
            "answer": data.get("answer"),
            "evidence_refs": data.get("evidence_refs"),
            "sources": data.get("sources"),
        }
        missing = [
            identity
            for identity in emitted_ids
            if not _contains_exact(readback_surface, identity)
            and identity not in _canonical_json(readback_surface)
        ]
        if missing:
            raise DeployedProofError(
                "Management readback did not resolve exact emitted IDs: "
                + ", ".join(missing)
            )
        result = {
            "ids": {"resolved_ids": emitted_ids},
            "management_message_id": data.get("message_id"),
            "management_status": data.get("status"),
            "provider": provider_status.get("provider"),
            "provider_used": True,
            "status": "passed",
        }
        self.results["management_identity"] = result
        self.evidence.cases["management_identity"] = result
        return result

    def run_all(self) -> None:
        current_case = "research_to_runtime"
        try:
            for current_case, method in (
                ("research_to_runtime", self.research_to_runtime),
                ("human_learning", self.human_learning),
                ("health_incident", self.health_incident),
                ("concurrency", self.concurrency),
                ("management_identity", self.management_identity),
            ):
                method()
            self.evidence.status = "passed"
        except Exception as exc:
            self.evidence.status = "failed"
            self.evidence.first_failed_boundary = current_case
            self.evidence.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.evidence.write(self.http.boundaries)


@pytest.fixture(scope="module")
def deployed_proof() -> CrossLoopDeployedProof:
    return CrossLoopDeployedProof(Settings.from_env())


def test_cross_loop_case_1_research_to_runtime(
    deployed_proof: CrossLoopDeployedProof,
) -> None:
    assert deployed_proof.research_to_runtime()["status"] == "passed"


def test_cross_loop_case_2_human_learning(
    deployed_proof: CrossLoopDeployedProof,
) -> None:
    assert deployed_proof.human_learning()["status"] == "passed"


def test_cross_loop_case_3_health_incident(
    deployed_proof: CrossLoopDeployedProof,
) -> None:
    assert deployed_proof.health_incident()["status"] == "passed"


def test_cross_loop_case_4_concurrency(
    deployed_proof: CrossLoopDeployedProof,
) -> None:
    result = deployed_proof.concurrency()
    assert result["assertions"] == {
        "one_owner": True,
        "one_run_action": True,
        "one_task_action": True,
    }


def test_cross_loop_case_5_management_identity_and_atomic_evidence(
    deployed_proof: CrossLoopDeployedProof,
) -> None:
    deployed_proof.run_all()
    assert deployed_proof.results["management_identity"]["status"] == "passed"
    assert deployed_proof.evidence.status == "passed"
