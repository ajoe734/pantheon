"""Stimulus-driven deployed closure gate for the twelve canonical loops.

Unlike the retained prebuilt-ID verifier, this gate starts all three domain
E2E suites itself.  Those suites create new records through their deployed
HTTP owners and emit temporary reports.  The parent gate accepts only reports
created during this parent run, normalizes their trigger/terminal/authority/
next-consumer evidence, and accepts the current Management loop-health
readback produced by that same Runtime run.  No test imports a product store
or creates a loop state store.

The gate is opt-in because it writes bounded paper-only proof records and
restarts the isolated runtime worker while validating a functional failure.
Set ``PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E=1`` along with the existing domain
suite configuration, ``PANTHEON_L12_STIMULUS_EXPECTED_SHA``, and a temporary
``PANTHEON_L12_STIMULUS_EVIDENCE_OUTPUT`` path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol

import pytest


TASK_ID = "PFG-L12-TRUTH-CROSSLOOP-20260820"
REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_VERIFIER = "tests/integration/l12/test_current_cross_loop_deployed_e2e.py"
DOMAIN_SUITES = {
    "research": "tests/integration/l12/test_current_research_loops_deployed_e2e.py",
    "human": "tests/integration/l12/test_current_human_learning_deployed_e2e.py",
    "runtime": "tests/integration/l12/test_current_runtime_loops_deployed_e2e.py",
}
LOOP_CASES = (
    ("source_ingestion", "research", "source_ingestion"),
    ("strategy_distillation", "research", "strategy_distillation"),
    ("alpha_replication", "research", "alpha_replication"),
    ("persona_teaching", "research", "persona_teaching"),
    ("agora_interaction_evidence", "human", "agora_interaction_evidence"),
    ("human_imitation_shadow_evaluation", "human", "imitation_research_handoff"),
    ("consultation", "human", "consultation_governance_handoff"),
    ("promotion_deployment", "runtime", "loop_08_promotion_deployment"),
    ("capital_pool_execution", "runtime", "loop_09_capital_artifact_execution"),
    ("telemetry_reconciliation", "runtime", "loop_10_telemetry_reconciliation_incident"),
    ("evolution", "runtime", "loop_11_evolution_decision"),
    ("bff_health_monitoring", "runtime", "loop_12_bff_typed_health"),
)
CANONICAL_LOOP_IDS = tuple(case[0] for case in LOOP_CASES)


class StimulusProofError(AssertionError):
    """A domain owner, evidence report, or Management readback was incomplete."""


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
        raise StimulusProofError(f"{name} is required for the stimulus closure gate")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _report_cases(domain: str, report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if domain == "runtime":
        cases = report.get("cases")
        if not isinstance(cases, Mapping):
            raise StimulusProofError("runtime report has no case mapping")
        return {
            str(case_id): value
            for case_id, value in cases.items()
            if isinstance(value, Mapping)
        }
    cases = report.get("cases") or report.get("case_results")
    if not isinstance(cases, list):
        raise StimulusProofError(f"{domain} report has no ordered case list")
    normalized: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if isinstance(case, Mapping) and str(case.get("loop_id") or "").strip():
            normalized[str(case["loop_id"])] = case
    return normalized


def _required_mapping(value: Any, *, boundary: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise StimulusProofError(f"{boundary} must be a non-empty object")
    return value


def _required_identity(value: Any, *, boundary: str) -> Any:
    if isinstance(value, Mapping):
        if not value:
            raise StimulusProofError(f"{boundary} must not be empty")
        return dict(value)
    if str(value or "").strip():
        return str(value)
    raise StimulusProofError(f"{boundary} must not be empty")


def _normalize_case(
    *,
    loop_id: str,
    domain: str,
    source_case: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize owner evidence without importing any product authority."""

    is_runtime = domain == "runtime"
    trigger = _required_identity(
        source_case.get("trigger_id") if is_runtime else source_case.get("trigger"),
        boundary=f"{loop_id} trigger",
    )
    terminal = _required_identity(
        source_case.get("terminal_output_id") if is_runtime else source_case.get("terminal_output"),
        boundary=f"{loop_id} terminal output",
    )
    authority = _required_mapping(
        source_case.get("authority_readback"),
        boundary=f"{loop_id} authority readback",
    )
    next_receipt = _required_mapping(
        source_case.get("next_consumer_readback") if is_runtime else source_case.get("next_consumer"),
        boundary=f"{loop_id} next receipt",
    )
    owner = _required_mapping(
        source_case.get("owner_worker_identity") if is_runtime else source_case.get("owner"),
        boundary=f"{loop_id} owner observation",
    )
    return {
        "authority_readback": dict(authority),
        "domain": domain,
        "loop_id": loop_id,
        "next_receipt": dict(next_receipt),
        "owner_observation": dict(owner),
        "terminal_output": terminal,
        "trigger": trigger,
    }


@dataclass(frozen=True)
class Settings:
    expected_sha: str
    evidence_output: Path
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        expected_sha = _required_env("PANTHEON_L12_STIMULUS_EXPECTED_SHA")
        if len(expected_sha) != 40 or any(char not in "0123456789abcdef" for char in expected_sha):
            raise StimulusProofError(
                "PANTHEON_L12_STIMULUS_EXPECTED_SHA must be a lowercase 40-character SHA"
            )
        output = Path(_required_env("PANTHEON_L12_STIMULUS_EVIDENCE_OUTPUT")).resolve()
        if "docs/deployment/evidence" in output.as_posix():
            raise StimulusProofError(
                "the closure gate writes a temporary report; checked-in evidence is owner-managed"
            )
        timeout_seconds = float(os.getenv("PANTHEON_L12_STIMULUS_TIMEOUT_SECONDS", "1800"))
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise StimulusProofError("stimulus timeout must be greater than zero and at most 3600 seconds")
        return cls(
            expected_sha=expected_sha,
            evidence_output=output,
            timeout_seconds=timeout_seconds,
        )


@dataclass
class Evidence:
    settings: Settings
    run_id: str
    started_at: str = field(default_factory=_utc_now)
    domain_suites: dict[str, dict[str, Any]] = field(default_factory=dict)
    loops: list[dict[str, Any]] = field(default_factory=list)
    management: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    first_failed_boundary: str | None = None
    error: str | None = None
    hosted: dict[str, Any] | None = None

    def write(self) -> None:
        payload = {
            "code_disposition": {
                "legacy_prebuilt_identity_verifier": {
                    "path": LEGACY_VERIFIER,
                    "status": "retained_readback_verifier_not_closure_gate",
                },
                "loop_truth": {
                    "status": "uses_existing_loop_control_and_bff_projection",
                    "new_loop_state_store": False,
                    "static_catalog_runtime_or_task_claims": False,
                },
            },
            "completed_at": _utc_now(),
            "domain_suites": self.domain_suites,
            "error": self.error,
            "expected_sha": self.settings.expected_sha,
            "first_failed_boundary": self.first_failed_boundary,
            "live_capital_enabled": False,
            "loops": self.loops,
            "management": self.management,
            "run_id": self.run_id,
            "schema_version": "pantheon.product_functional_closure.stimulus_cross_loop_e2e.v1",
            "started_at": self.started_at,
            "status": self.status,
            "task_id": TASK_ID,
        }
        if self.hosted is not None:
            payload["hosted"] = self.hosted
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


class StimulusDrivenClosureGate:
    """Run fresh owner stimuli, then project their current Management truth."""

    def __init__(self, settings: Settings, *, run_id: str | None = None) -> None:
        self.settings = settings
        self.run_id = run_id or f"{TASK_ID.lower()}-{uuid.uuid4().hex[:12]}"
        self.evidence = Evidence(settings=settings, run_id=self.run_id)
        self._reports: dict[str, Mapping[str, Any]] = {}

    def _run_domain_suite(
        self,
        domain: str,
        report_path: Path,
        *,
        extra_env: Mapping[str, str] | None = None,
        accepted_statuses: tuple[str, ...] = ("passed",),
    ) -> None:
        report_path.unlink(missing_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "PANTHEON_L12_CROSS_LOOP_RUN_ID": self.run_id,
                "PANTHEON_L12_EXPECTED_SHA": self.settings.expected_sha,
                "PANTHEON_L12_REPORT_PATH": str(report_path),
            }
        )
        if domain == "research":
            env["PANTHEON_L12_RESEARCH_E2E"] = "1"
        elif domain == "human":
            env["PANTHEON_L12_HUMAN_LEARNING_E2E"] = "1"
        elif domain == "runtime":
            env["PANTHEON_L12_DEPLOYED_E2E"] = "1"
            env["PANTHEON_L12_EVIDENCE_OUTPUT"] = str(report_path)
        else:  # pragma: no cover - fixed internal dispatch table
            raise StimulusProofError(f"unknown domain suite {domain!r}")

        env.update(extra_env or {})
        started_at = _utc_now()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", DOMAIN_SUITES[domain]],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.settings.timeout_seconds,
        )
        if completed.returncode != 0:
            # The report lives in a TemporaryDirectory that is removed on exit,
            # so carry the child's output in the error to keep failures diagnosable.
            raise StimulusProofError(
                f"{domain} stimulus suite exited {completed.returncode}\n"
                f"--- stdout tail ---\n{completed.stdout[-8000:]}\n"
                f"--- stderr tail ---\n{completed.stderr[-4000:]}"
            )
        if not report_path.is_file():
            raise StimulusProofError(f"{domain} stimulus suite did not create a report")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StimulusProofError(f"{domain} stimulus report is invalid") from exc
        if not isinstance(report, Mapping) or report.get("status") not in accepted_statuses:
            raise StimulusProofError(f"{domain} stimulus report did not pass")
        report_sha = str(report.get("git_sha") or report.get("expected_sha") or "")
        if report_sha != self.settings.expected_sha:
            raise StimulusProofError(
                f"{domain} report SHA {report_sha!r} does not match expected deployed SHA"
            )
        self._reports[domain] = report
        self.evidence.domain_suites[domain] = {
            "report_sha256": _sha256(report_path),
            "report_status": report.get("status"),
            "started_by_parent_at": started_at,
            "suite": DOMAIN_SUITES[domain],
        }

    def _collect_loop_evidence(self) -> None:
        for loop_id, domain, case_key in LOOP_CASES:
            source_case = _report_cases(domain, self._reports[domain]).get(case_key)
            if source_case is None:
                raise StimulusProofError(
                    f"{domain} report does not contain the required {case_key} case"
                )
            self.evidence.loops.append(
                _normalize_case(
                    loop_id=loop_id,
                    domain=domain,
                    source_case=source_case,
                )
            )
        if tuple(item["loop_id"] for item in self.evidence.loops) != CANONICAL_LOOP_IDS:
            raise StimulusProofError("stimulus evidence did not normalize all twelve canonical loops")

    def _management_readback(self) -> None:
        loop12_case = _report_cases("runtime", self._reports["runtime"]).get(
            "loop_12_bff_typed_health"
        )
        if not isinstance(loop12_case, Mapping):
            raise StimulusProofError("runtime report lacks the BFF Management readback case")
        management_readback = _required_mapping(
            (loop12_case.get("next_consumer_readback") or {}).get("management_loop_health"),
            boundary="BFF Management loop-health readback",
        )
        rows = _required_mapping(
            management_readback.get("rows"),
            boundary="BFF Management loop-health rows",
        )
        if (
            management_readback.get("endpoint") != "/bff/v5/loop-health"
            or tuple(sorted(management_readback.get("canonical_loop_ids") or []))
            != tuple(sorted(CANONICAL_LOOP_IDS))
            or tuple(sorted(rows)) != tuple(sorted(CANONICAL_LOOP_IDS))
        ):
            raise StimulusProofError("Management loop-health does not contain exactly twelve canonical rows")
        observations = {
            loop_id: dict(_required_mapping(row, boundary=f"Management row {loop_id}"))
            for loop_id, row in rows.items()
        }
        negative = _report_cases("runtime", self._reports["runtime"]).get(
            "negative_typed_worker_failure"
        )
        if not isinstance(negative, Mapping):
            raise StimulusProofError("runtime report lacks the functional worker-failure case")
        failure_readback = _required_mapping(
            negative.get("authority_readback"),
            boundary="runtime functional worker failure readback",
        )
        if failure_readback.get("ok") is not False:
            raise StimulusProofError("runtime functional worker failure was not visible to BFF")
        failure_attribution = _required_mapping(
            (negative.get("next_consumer_readback") or {}).get("failure_attribution"),
            boundary="BFF worker-failure loop attribution",
        )
        # Component probe failures never manufacture a canonical loop-health
        # row (management_read_models/loop_truth.py; ACG-04-008 removed the
        # cross-loop map) and nothing writes a capital_pool_execution
        # controller record.  The Loop 12 record names the failing target and
        # the probe registry declares that target a Capital worker.
        failure_registry = failure_readback.get("registry") or {}
        if (
            failure_attribution.get("loop_id") != "bff_health_monitoring"
            or failure_attribution.get("status") != "degraded"
            or "paper-fleet-reconciler" not in str(failure_attribution.get("summary") or "")
            or failure_registry.get("component_kind") != "capital-worker-api"
        ):
            raise StimulusProofError(
                "BFF did not attribute the functional paper-fleet failure to the Capital worker"
            )
        self.evidence.management = {
            "canonical_loop_count": len(rows),
            "endpoint": management_readback.get("endpoint"),
            "functional_worker_failure": {
                "reporting_loop": failure_attribution.get("loop_id"),
                "target": "paper-fleet-reconciler",
                "target_component_kind": failure_registry.get("component_kind"),
                "target_ok": failure_readback.get("ok"),
                "summary": failure_attribution.get("summary"),
            },
            "rows": observations,
        }

    def run(self) -> Evidence:
        try:
            with tempfile.TemporaryDirectory(prefix="pantheon-l12-stimulus-") as temp_dir:
                root = Path(temp_dir)
                for domain in ("research", "human", "runtime"):
                    self._run_domain_suite(domain, root / f"{domain}-report.json")
                self._collect_loop_evidence()
                self._management_readback()
            self.evidence.status = "passed"
            return self.evidence
        except Exception as exc:
            self.evidence.status = "failed"
            self.evidence.first_failed_boundary = next(
                (
                    domain
                    for domain in ("research", "human", "runtime", "management")
                    if domain not in self.evidence.domain_suites
                ),
                "normalization",
            )
            self.evidence.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.evidence.write()


# --------------------------------------------------------------------------
# Hosted chain phases (PANTHEON_L12_HOSTED_CHAIN=1).  The same parent launches
# the same domain suites; hosted adds a stable chain id, one requested phase,
# owner-verified predecessors and E_close retirement.  Owner GETs are truth;
# receipt files are lookup hints only.  Default isolated behaviour is unchanged.
# --------------------------------------------------------------------------

HOSTED_FLAG = "PANTHEON_L12_HOSTED_CHAIN"
HOSTED_PHASES = ("A_research", "B_learning", "C_promote", "D_observe", "E_close")
HOSTED_IDS_PATH_ENV = "PANTHEON_L12_HOSTED_CHAIN_IDS_PATH"
HOSTED_PHASE_SUITE = {
    "A_research": "research",
    "B_learning": "human",
    "C_promote": "runtime",
    "D_observe": "runtime",
}
HOSTED_PHASE_LOOPS = {
    "A_research": CANONICAL_LOOP_IDS[0:4],
    "B_learning": CANONICAL_LOOP_IDS[4:7],
    "C_promote": CANONICAL_LOOP_IDS[7:9],
    "D_observe": CANONICAL_LOOP_IDS[9:12],
}
HOSTED_PHASE_STATUSES = ("completed", "paused", "failed")
HOSTED_RECEIPT_SCHEMA = "pantheon.hosted_all12.stimulus_phase_receipt.v1"
_CHAIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
_WRAPPER_KEYS = ("entry", "binding", "pool", "plan", "approval", "capital_pool")


class OwnerClient(Protocol):
    def request(
        self,
        service: str,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]: ...


class UrllibOwnerClient:
    """Plain HTTP owner access under the real executing identity from env."""

    def __init__(
        self,
        urls: Mapping[str, str],
        token: str,
        actor_id: str = "",
        roles: tuple[str, ...] = (),
        service_headers: Mapping[str, Mapping[str, str]] | None = None,
        capital_authorization: Callable[[], str] | None = None,
        tenant_id: str = "",
    ) -> None:
        self.urls = dict(urls)
        # Capital tokens live ~120s: one genuine token is fetched per request, never held.
        self.capital_authorization = capital_authorization
        self.tenant_id = tenant_id
        self.token = token
        # Services whose existing owner auth is not the generic bearer (Training,
        # Research, Consultation) read under their own configured headers.
        self.service_headers = {k: dict(v) for k, v in (service_headers or {}).items()}
        # Identity the token really carries; owners reject any other actor/role.
        self.actor_id, self.roles = actor_id, tuple(roles)

    @classmethod
    def from_env(cls) -> "UrllibOwnerClient":
        services = ("registry", "governance", "capital", "deployment", "runtime")
        urls = {
            name: _required_env(f"PANTHEON_L12_{name.upper()}_URL").rstrip("/")
            for name in services
        }
        roles = tuple(
            r.strip()
            for r in os.getenv("PANTHEON_L12_HOSTED_OWNER_ROLES", "").split(",")
            if r.strip()
        )
        tenant = _required_env("PANTHEON_L12_TENANT_ID")

        def tenant_headers(tenant_id: str) -> dict[str, str]:
            return {
                "X-Tenant-Id": tenant_id,
                "X-Pantheon-Tenant": tenant_id,
                "X-Pantheon-Tenant-Id": tenant_id,
            }

        def bearer(name: str) -> dict[str, str]:
            token = os.getenv(name, "").strip()
            return {"Authorization": token if token.startswith("Bearer ") else f"Bearer {token}"} if token else {}

        service_headers: dict[str, dict[str, str]] = {}
        for service, env_name in (("training", "TRAINING"), ("research", "RESEARCH"), ("consultation", "CONSULTATION")):
            url = os.getenv(f"PANTHEON_L12_{env_name}_URL", "").strip().rstrip("/")
            if not url:
                continue  # unreadable later: the runner pauses with concrete evidence
            urls[service] = url
        if "training" in urls:
            service_headers["training"] = {
                **bearer("PANTHEON_L12_TRAINING_TOKEN"),
                "X-Tenant-Id": tenant,
                "X-Pantheon-Service": "training-session-preview-worker",
            }
        if "research" in urls:
            service_headers["research"] = tenant_headers(tenant)
        if "consultation" in urls:
            consult_tenant = os.getenv("PANTHEON_L12_CONSULTATION_TENANT_ID", "").strip() or tenant
            service_headers["consultation"] = {
                **bearer("PANTHEON_L12_CONSULTATION_TOKEN"),
                **tenant_headers(consult_tenant),
            }
        capital_authorization = None
        if os.getenv("PANTHEON_L12_COMPOSE_PROJECT", "").strip():
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from l12_owner_auth import capital_authorization as hosted_capital_authorization

            capital_authorization = hosted_capital_authorization
        return cls(
            urls,
            _required_env("PANTHEON_L12_HOSTED_OWNER_TOKEN"),
            os.getenv("PANTHEON_L12_HOSTED_OWNER_ACTOR_ID", "").strip(),
            roles,
            service_headers,
            capital_authorization,
            tenant,
        )

    def _capital_request_auth(self) -> tuple[dict[str, str], str, tuple[str, ...]]:
        """Fresh Capital headers plus the (actor, roles) that token really carries."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from l12_owner_auth import capital_identity

        authorization = self.capital_authorization()  # type: ignore[misc]
        actor, service = capital_identity(authorization)
        headers = {"Authorization": authorization, "X-Pantheon-Service": service}
        if self.tenant_id:
            headers["X-Tenant-Id"] = self.tenant_id
        # The same roles Hosted C names under this Capital principal.
        return headers, actor, ("capital.admin", "persona.admin")

    def identity_for(self, service: str) -> tuple[str, tuple[str, ...]]:
        """Actor/roles an owner compares a mutation body against (Capital: its own principal)."""
        if service == "capital" and self.capital_authorization is not None:
            _headers, actor, roles = self._capital_request_auth()
            return actor, roles
        return self.actor_id, self.roles

    def request(
        self,
        service: str,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        if service not in self.urls:
            raise StimulusProofError(f"no owner URL configured for {service}")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}
        if self.tenant_id:
            headers["X-Tenant-Id"] = self.tenant_id  # owners enforce the tenant boundary on this header
        if service == "capital" and self.capital_authorization is not None:
            headers = {"Accept": "application/json", **self._capital_request_auth()[0]}
        elif service in self.service_headers:
            headers = {"Accept": "application/json", **self.service_headers[service]}
        data = None
        if body is not None:
            data = _canonical_json(dict(body)).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            f"{self.urls[service]}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw, status = response.read().decode("utf-8"), response.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read().decode("utf-8", errors="replace"), exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise StimulusProofError(f"{method} {service}{path} transport failed: {exc}") from exc
        try:
            return status, (json.loads(raw) if raw else {})
        except json.JSONDecodeError:
            return status, {"raw": raw}


def _registry_retire_body(record: Mapping[str, Any], chain_id: str, *_identity: Any) -> dict[str, Any]:
    # Registry advance is a CAS on the entry the caller just read.
    return {
        "target_state": "retired",
        "command_key": f"{chain_id}-e-close-registry",
        "expected_artifact_state": record.get("artifact_state"),
        "expected_version": record.get("version"),
        "expected_updated_at": record.get("updated_at"),
    }


def _execution_artifact_retire_body(record: Mapping[str, Any], chain_id: str, *_identity: Any) -> dict[str, Any]:
    # Same CAS as the Spec, but a distinct command key: it retires a different Registry entry.
    return {**_registry_retire_body(record, chain_id), "command_key": f"{chain_id}-e-close-execution-artifact"}


def _capital_status_body(
    status: str, roles: tuple[str, ...]
) -> Callable[[Mapping[str, Any], str, str, tuple[str, ...]], dict[str, Any] | None]:
    # Capital binds actor_id to the authenticated actor and needs a granted role.
    def build(_record: Mapping[str, Any], _chain: str, actor_id: str, granted: tuple[str, ...]):
        role = next((r for r in roles if r in granted), None)
        if not actor_id or role is None:
            return None
        return {"actor_id": actor_id, "actor_role": role, "status": status}

    return build


@dataclass(frozen=True)
class OwnerRecordSpec:
    """Existing owner read (and retirement, when the owner has one)."""

    service: str
    read_path: str
    retire_method: str | None = None
    retire_path: str | None = None
    retire_body: Callable[..., dict[str, Any] | None] | None = None
    retired_states: tuple[str, ...] = ()


# Retirement order matters: dependants retire before their authority -- the runtime binding,
# then the Capital binding, then the pool; runtime/plan before artifact.
RECORD_SPECS: dict[str, OwnerRecordSpec] = {
    "runtime_binding_id": OwnerRecordSpec(
        "runtime", "/api/runtime-bindings/{id}", "POST", "/api/runtime-bindings/{id}/retire",
        lambda *_a: {},  # runtime retire reads only an optional retired_at
        ("retired", "inactive"),
    ),
    "plan_id": OwnerRecordSpec(
        "deployment", "/api/deployment/plans/{id}", "POST", "/api/deployment/plans/{id}/status",
        lambda *_a: {"status": "aborted"},
        # failed/rejected plans have no transition left: nothing to retire
        ("aborted", "failed", "rejected"),
    ),
    "capital_binding_id": OwnerRecordSpec(
        "capital", "/api/bindings/{id}", "PATCH", "/api/bindings/{id}/status",
        _capital_status_body("revoked", ("persona.admin", "operator")), ("revoked",),
    ),
    "capital_pool_id": OwnerRecordSpec(
        "capital", "/api/capital-pools/{id}", "PATCH", "/api/capital-pools/{id}/status",
        _capital_status_body("archived", ("capital.admin", "operator")), ("archived",),
    ),
    # The distinct execution artifact derived from the Spec retires before the Spec it came from.
    "execution_artifact_id": OwnerRecordSpec(
        "registry", "/api/registry/strategy-artifacts/{id}", "POST",
        "/api/registry/strategy-artifacts/{id}/advance", _execution_artifact_retire_body, ("retired",),
    ),
    "registry_id": OwnerRecordSpec(
        "registry", "/api/registry/strategy-specs/{id}", "POST",
        "/api/registry/strategy-specs/{id}/advance", _registry_retire_body, ("retired",),
    ),
}
APPROVAL_READ = "/api/governance/approvals/{id}"
APPROVAL_REVOKE = "/api/governance/approvals/{id}/revoke"
H1_REVOKE_ROLES = ("risk_owner", "governance_committee")  # governance REVOKE_AUTHORITY
# Only H1 is a Governance approval; H2-H4 are recorded consents carried as references.
GOVERNANCE_DECISIONS = ("H1",)
_STATE_KEYS = ("status", "artifact_state", "state")


def _unwrap(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    for key in _WRAPPER_KEYS:
        inner = payload.get(key)
        if isinstance(inner, Mapping):
            return inner
    return payload


def _record_tenant(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    for candidate in (record.get("tenant_id"), metadata.get("tenant_id"), record.get("owner_tenant")):
        if str(candidate or "").strip():
            return str(candidate)
    return ""


def _record_chain(record: Mapping[str, Any]) -> str:
    for holder in (record.get("metadata"), record.get("labels"), record):
        if isinstance(holder, Mapping) and str(holder.get("chain_id") or "").strip():
            return str(holder["chain_id"])
    return ""


def _decision_id(value: Any) -> str:
    """Shared packets carry decisions as objects with ``decision_id`` (strings tolerated)."""

    if isinstance(value, Mapping):
        value = value.get("decision_id")
    return str(value or "").strip()


def _typed_id(kind: str, value: Any) -> str:
    """Resource values are nonsecret scalars. Only real owner endpoint IDs (those
    interpolated into owner paths) get path-safety rules; other values such as the
    opaque H2 consent record_ref only need to be nonempty scalars."""

    if isinstance(value, (Mapping, list, tuple)) or isinstance(value, bool):
        raise StimulusProofError(f"resource id {kind} must be a scalar owner id")
    text = str(value or "").strip()
    if not text:
        raise StimulusProofError(f"resource id {kind} is empty")
    if kind in RECORD_SPECS and any(ch.isspace() or ch in "/?#" for ch in text):
        raise StimulusProofError(f"resource id {kind} is not a valid owner id")
    return text


FLEET_PATH = "/api/runtime-fleet/desired-state?stage=paper&include_excluded=true"


def _fleet_snapshot(payload: Any) -> dict[str, Any] | None:
    """Existing Runtime owner read: active count and desired binding ids."""

    if not isinstance(payload, Mapping) or not isinstance(payload.get("bindings"), list):
        return None
    ids = sorted(
        str(item["binding_id"])
        for item in payload["bindings"]
        if isinstance(item, Mapping) and item.get("binding_id")
    )
    active = payload.get("active_count")
    if not isinstance(active, int):
        return None
    return {"active_count": active, "binding_ids": ids}


def _record_state(record: Mapping[str, Any]) -> str:
    for key in _STATE_KEYS:
        if str(record.get(key) or "").strip():
            return str(record[key]).lower()
    return ""


class HostedChainRunner:
    """Run one requested phase, resuming from owner truth instead of receipts."""

    def __init__(
        self,
        settings: Settings,
        *,
        chain_id: str,
        phase: str,
        tenant_id: str,
        owner: OwnerClient,
        prior_receipt: Mapping[str, Any] | None = None,
        decision_refs: Mapping[str, str] | None = None,
        predecessors: Mapping[str, str] | None = None,
    ) -> None:
        if not _CHAIN_ID_PATTERN.match(chain_id):
            raise StimulusProofError("chain id must be a stable lowercase nonsecret identifier")
        if phase not in HOSTED_PHASES:
            raise StimulusProofError(f"phase {phase!r} is not one of {HOSTED_PHASES}")
        if tenant_id in {"", "*"}:
            raise StimulusProofError("hosted chain requires a concrete tenant, never '*'")
        hosted = (prior_receipt or {}).get("hosted") or {}
        if hosted and hosted.get("chain_id") != chain_id:
            raise StimulusProofError("prior receipt belongs to a different chain id")
        self.settings, self.chain_id, self.phase = settings, chain_id, phase
        self.tenant_id, self.owner = tenant_id, owner
        self.phases: dict[str, dict[str, Any]] = {
            name: dict(value) for name, value in (hosted.get("phases") or {}).items()
        }
        self.resource_ids: dict[str, str] = {
            **dict(hosted.get("resource_ids") or {}),
            **{str(k): _typed_id(str(k), v) for k, v in (predecessors or {}).items() if str(v or "").strip()},
        }
        self.decisions: dict[str, Any] = {**dict(hosted.get("decisions") or {}), **dict(decision_refs or {})}
        self.baseline_fleet: dict[str, Any] | None = hosted.get("baseline_fleet") or None
        self.loops: dict[str, dict[str, Any]] = {
            item["loop_id"]: item for item in hosted.get("loops") or []
        }
        self.gate = StimulusDrivenClosureGate(settings, run_id=chain_id)
        self.gate.evidence.hosted = {}

    # -- owner truth ------------------------------------------------------
    def _read(self, spec: OwnerRecordSpec, path: str) -> tuple[int, Mapping[str, Any]]:
        status, payload = self.owner.request(spec.service, "GET", path)
        return status, _unwrap(payload)

    def _check_scope(self, kind: str, record_id: str, record: Mapping[str, Any]) -> str | None:
        tenant, chain = _record_tenant(record), _record_chain(record)
        if tenant != self.tenant_id:
            return f"{kind} {record_id}: tenant {tenant!r} != {self.tenant_id!r}"
        if kind == "registry_id" and not chain:
            return self._check_registry_lineage(record_id, record)
        if chain != self.chain_id:
            return f"{kind} {record_id}: chain label {chain!r} != {self.chain_id!r}"
        if kind == "execution_artifact_id":
            return self._check_execution_artifact(record_id, record)
        return None

    def _check_execution_artifact(self, record_id: str, record: Mapping[str, Any]) -> str | None:
        """The derived artifact must be the one C registered, linked to this chain's original Spec."""
        spec_id = self.resource_ids.get("registry_id")
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        embedded = metadata.get("strategy_artifact") if isinstance(metadata.get("strategy_artifact"), Mapping) else {}
        linked = (embedded.get("lineage") or {}).get("source_strategy_spec_id")
        if not spec_id or record_id == spec_id or linked != spec_id:
            return f"execution_artifact_id {record_id}: lineage.source_strategy_spec_id {linked!r} is not this chain's Spec {spec_id!r}"
        for field, key in (("checksum", "execution_artifact_checksum"), ("version", "execution_artifact_version")):
            want = self.resource_ids.get(key)
            if want and record.get(field) != want:
                return f"execution_artifact_id {record_id}: {field} {record.get(field)!r} differs from the chain's {want!r}"
        return None

    def _check_registry_lineage(self, record_id: str, record: Mapping[str, Any]) -> str | None:
        # Actual distillation stores source/digest lineage, not a direct chain_id.
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        lineage = metadata.get("distillation") if isinstance(metadata.get("distillation"), Mapping) else {}
        source_id = str(lineage.get("source_id") or "")
        if not source_id or not str(lineage.get("source_digest") or ""):
            return f"registry_id {record_id}: no source/distillation lineage to prove chain ownership"
        if source_id != self.resource_ids.get("source_id") or source_id != f"hosted-all12-{self.chain_id}-src":
            return f"registry_id {record_id}: lineage source {source_id!r} is not this chain's Source"
        supplied_digest = self.resource_ids.get("source_digest")
        if supplied_digest and str(lineage.get("source_digest")) != supplied_digest:
            return f"registry_id {record_id}: lineage source digest differs from the chain's supplied digest"
        checksum = self.resource_ids.get("checksum")
        if checksum and record.get("checksum") != checksum:
            return f"registry_id {record_id}: checksum differs from the chain checksum"
        return None

    def verify_predecessors(self) -> list[str]:
        """Read every referenced owner record; return concrete mismatches."""

        problems: list[str] = []
        for kind, record_id in sorted(self.resource_ids.items()):
            spec = RECORD_SPECS.get(kind)
            if spec is None:
                continue  # carried as a nonsecret reference; owner read not defined here
            status, record = self._read(spec, spec.read_path.format(id=urllib.parse.quote(record_id, safe="")))
            if status != 200 or not record:
                problems.append(f"{kind} {record_id}: owner GET returned {status}")
                continue
            mismatch = self._check_scope(kind, record_id, record)
            if mismatch:
                problems.append(mismatch)
        for name, reference in sorted(self.decisions.items()):
            if name in GOVERNANCE_DECISIONS:
                decision_id = _decision_id(reference)
                if not decision_id:
                    problems.append(f"decision {name}: no decision_id")
                    continue
                problems.extend(self._verify_decision(name, decision_id))
            elif not (reference if isinstance(reference, Mapping) else str(reference or "").strip()):
                problems.append(f"decision {name}: empty consent reference")
            elif name == "H3" and not _decision_id(reference):
                problems.append("decision H3: no decision_id")
            elif name == "H3" and self.resource_ids.get("execution_artifact_id"):
                problems.extend(self._verify_h3(_decision_id(reference)))
            # recorded consent (e.g. H2) is forwarded as given; never a Governance gate
        problems.extend(self._verify_domain_outputs())
        return problems

    # -- completed-phase domain outputs ------------------------------------
    def _get_domain(self, service: str, path: str, what: str) -> tuple[Mapping[str, Any] | None, str | None]:
        try:
            status, payload = self.owner.request(service, "GET", path)
        except (StimulusProofError, KeyError) as exc:
            return None, f"{what}: owner unreadable ({exc})"
        if status != 200 or not isinstance(payload, Mapping) or not payload:
            return None, f"{what}: owner GET returned {status}"
        return payload, None

    @staticmethod
    def _loop_ref(loop: Mapping[str, Any] | None, *places: tuple[str, str]) -> str:
        for section, key in places:
            holder = (loop or {}).get(section)
            if isinstance(holder, Mapping) and str(holder.get(key) or "").strip():
                return str(holder[key])
        return ""

    def _verify_domain_outputs(self) -> list[str]:
        """Re-read the real Teaching/Research/Consultation outputs of completed A/B.

        Uses the per-loop receipts already kept in ``hosted.loops`` (the flat
        resource map is overwritten by B), the existing owner GET routes and
        tenant/identity lineage; never reruns a provider or business suite.
        """

        done = lambda name: self.phases.get(name, {}).get("phase_status") == "completed"  # noqa: E731
        quote = lambda value: urllib.parse.quote(value, safe="")  # noqa: E731
        problems: list[str] = []

        def need(loop_id: str, label: str, value: str) -> str:
            if not value:
                problems.append(f"{loop_id}: completed receipt lacks required {label}")
            return value

        def tenant_ok(label: str, record: Mapping[str, Any]) -> None:
            tenant = _record_tenant(record) or _record_tenant(
                record.get("parameters") if isinstance(record.get("parameters"), Mapping) else {}
            )
            if tenant != self.tenant_id:
                problems.append(f"{label}: tenant {tenant!r} != {self.tenant_id!r}")

        def chain_ref(key: str) -> str:
            value = str(self.resource_ids.get(key) or "").strip()
            if not value:
                problems.append(f"completed phase lacks chain-owned {key} to cross-join owner outputs")
            return value

        def lineage(label: str, record: Mapping[str, Any], expected: Mapping[str, str], need_chain: bool = True) -> None:
            holders = [record.get("metadata"), record.get("parameters"), record]
            holders = [h for h in holders if isinstance(h, Mapping)]
            chain = _record_chain(record) or next(
                (str(h["chain_id"]) for h in holders if str(h.get("chain_id") or "").strip()), ""
            )
            if need_chain and chain != self.chain_id:
                problems.append(f"{label}: chain label {chain!r} != {self.chain_id!r}")
            for key, want in expected.items():
                if not want:
                    continue
                got = next((str(h[key]) for h in holders if str(h.get(key) or "").strip()), "")
                if got != want:
                    problems.append(f"{label}: lineage {key} {got!r} != this chain's {want!r}")

        if done("A_research"):
            alpha = self.loops.get("alpha_replication")
            teaching = self.loops.get("persona_teaching")
            if alpha is None or teaching is None:
                problems.append("A_research completed but alpha/teaching loop receipts are missing")
            else:
                authority_id = need("alpha_replication", "authority_run_id",
                                    self._loop_ref(alpha, ("terminal_output", "authority_run_id"), ("authority_readback", "authority_run_id")))
                domain_id = need("alpha_replication", "domain run id",
                                 self._loop_ref(alpha, ("authority_readback", "domain_run_id"), ("terminal_output", "id")))
                if authority_id:
                    label = f"alpha authority run {authority_id}"
                    run, err = self._get_domain("research", f"/api/research-orchestrator/runs/{quote(authority_id)}", label)
                    if err:
                        problems.append(err)
                    else:
                        params = run.get("parameters") if isinstance(run.get("parameters"), Mapping) else {}
                        embedded = params.get("experiment_run") if isinstance(params.get("experiment_run"), Mapping) else {}
                        tenant_ok(label, run)
                        if run.get("run_id") != authority_id:
                            problems.append(f"{label}: run_id {run.get('run_id')!r} differs")
                        if domain_id and embedded.get("run_id") != domain_id:
                            problems.append(f"{label}: embedded ExperimentRun {embedded.get('run_id')!r} != {domain_id!r}")
                        if embedded.get("status") != "completed":
                            problems.append(f"{label}: embedded ExperimentRun is not completed")
                        spec = self._loop_ref(alpha, ("terminal_output", "strategy_spec_id"))
                        if spec and params.get("strategy_spec_id") != spec:
                            problems.append(f"{label}: strategy_spec_id {params.get('strategy_spec_id')!r} != {spec!r}")
                        registry_id = chain_ref("registry_id")
                        if registry_id and (params.get("strategy_spec_id") != registry_id or (spec and spec != registry_id)):
                            problems.append(
                                f"{label}: strategy_spec_id {params.get('strategy_spec_id')!r} is not this chain's Registry {registry_id!r}"
                            )
                session_id = need("persona_teaching", "session_id",
                                  self._loop_ref(teaching, ("terminal_output", "session_id"), ("authority_readback", "session_id")))
                eval_identity = self._loop_ref(teaching, ("terminal_output", "id"), ("authority_readback", "eval_identity"))
                eval_id = self._loop_ref(teaching, ("terminal_output", "eval_id"))
                if session_id:
                    label = f"teaching session {session_id}"
                    record, err = self._get_domain("training", f"/api/training/sessions/{quote(session_id)}", label)
                    if err:
                        problems.append(err)
                    else:
                        tenant_ok(label, record)
                        if record.get("session_id") != session_id:
                            problems.append(f"{label}: session_id {record.get('session_id')!r} differs")
                        refs = record.get("context_refs") if isinstance(record.get("context_refs"), list) else []
                        if authority_id and not any(
                            isinstance(ref, Mapping) and ref.get("id") == authority_id
                            and (not domain_id or ref.get("domain_run_id") == domain_id)
                            for ref in refs
                        ):
                            problems.append(f"{label}: context_refs do not carry the Alpha authority/domain run identities")
                    readback, err = self._get_domain(
                        "training", f"/api/training/sessions/{quote(session_id)}/terminal-readback", f"{label} terminal readback"
                    )
                    if err:
                        problems.append(err)
                    else:
                        if eval_identity and readback.get("eval_identity") != eval_identity:
                            problems.append(f"{label}: eval_identity differs from the Teaching loop receipt")
                        result = readback.get("evaluation_result") if isinstance(readback.get("evaluation_result"), Mapping) else {}
                        if eval_id and result.get("eval_id") != eval_id:
                            problems.append(f"{label}: evaluation_result.eval_id differs from the Teaching loop receipt")
        if done("B_learning"):
            imitation = self.loops.get("human_imitation_shadow_evaluation")
            consult = self.loops.get("consultation")
            if imitation is None or consult is None:
                problems.append("B_learning completed but imitation/consultation loop receipts are missing")
            else:
                run_id = need("human_imitation_shadow_evaluation", "run_id", self._loop_ref(imitation, ("terminal_output", "run_id")))
                task_id = need("human_imitation_shadow_evaluation", "task_id", self._loop_ref(imitation, ("terminal_output", "task_id")))
                if run_id:
                    label = f"imitation experiment run {run_id}"
                    run, err = self._get_domain("research", f"/api/research-orchestrator/runs/{quote(run_id)}", label)
                    if err:
                        problems.append(err)
                    else:
                        tenant_ok(label, run)
                        if run.get("run_id") != run_id or (task_id and run.get("task_id") != task_id):
                            problems.append(f"{label}: run/task identity differs from the imitation loop receipt")
                        lineage(label, run, {"candidate_id": chain_ref("candidate_id")}, need_chain=False)
                request_id = need("consultation", "request_id", self._loop_ref(consult, ("authority_readback", "request_id")))
                handoff_id = need("consultation", "governance_handoff_id", self._loop_ref(consult, ("terminal_output", "governance_handoff_id")))
                if request_id:
                    label = f"consult request {request_id}"
                    request, err = self._get_domain("consultation", f"/api/consult/requests/{quote(request_id)}", label)
                    if err:
                        problems.append(err)
                    else:
                        tenant_ok(label, request)
                        if request.get("request_id") != request_id:
                            problems.append(f"{label}: request_id {request.get('request_id')!r} differs")
                        if run_id and (request.get("target_type") != "experiment_run" or request.get("target_id") != run_id):
                            problems.append(f"{label}: target is not the imitation ExperimentRun {run_id!r}")
                        teaching = self.loops.get("persona_teaching")
                        lineage(label, request, {
                            "registry_id": chain_ref("registry_id"),
                            "teaching_session_id": self._loop_ref(
                                teaching, ("terminal_output", "session_id"), ("authority_readback", "session_id")
                            ),
                            "dataset_version_id": chain_ref("dataset_version_id"),
                            "candidate_id": chain_ref("candidate_id"),
                            "experiment_run_id": run_id,
                        })
                if handoff_id and request_id:
                    label = f"governance handoff {handoff_id}"
                    handoff, err = self._get_domain("consultation", f"/api/consult/handoffs/{quote(handoff_id)}", label)
                    if err:
                        problems.append(err)
                    else:
                        if handoff.get("handoff_id") != handoff_id or handoff.get("request_id") != request_id:
                            problems.append(f"{label}: handoff/request identity differs")
                        if handoff.get("status") != "acknowledged":
                            problems.append(f"{label}: status {handoff.get('status')!r} is not acknowledged")
        return problems

    def _verify_decision(self, name: str, decision_id: str) -> list[str]:
        status, record = self.owner.request(
            "governance", "GET", APPROVAL_READ.format(id=urllib.parse.quote(decision_id, safe=""))
        )
        record = _unwrap(record)
        if status != 200 or not record:
            return [f"decision {name} {decision_id}: owner GET returned {status}"]
        problems: list[str] = []
        if _record_tenant(record) != self.tenant_id:
            problems.append(f"decision {name} {decision_id}: tenant mismatch")
        if record.get("decision_state") != "decided" or record.get("decision") != "approved":
            problems.append(
                f"decision {name} {decision_id}: not an approved decided record "
                f"(state={record.get('decision_state')!r} decision={record.get('decision')!r})"
            )
        registry_id = self.resource_ids.get("registry_id")
        if name == "H1" and registry_id and record.get("target_id") != registry_id:
            problems.append(f"decision H1 {decision_id}: targets {record.get('target_id')!r}, not {registry_id!r}")
        return problems

    def _verify_h3(self, decision_id: str) -> list[str]:
        """H3 must be a live decision for the derived artifact, never the Spec's H1.

        Same exact-ID read and expected fields Hosted C enforces before covered writes.
        """
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from l12_owner_auth import HostedPause, require_decided_approval

        def read(wanted_id: str) -> Mapping[str, Any] | None:
            status, record = self.owner.request(
                "governance", "GET", APPROVAL_READ.format(id=urllib.parse.quote(wanted_id, safe=""))
            )
            record = _unwrap(record)
            if status != 200 or not isinstance(record, Mapping) or record.get("decision_id") != wanted_id:
                return None
            return record

        ids = self.resource_ids
        try:
            require_decided_approval(
                read,
                {"decision_id": decision_id},
                point="H3",
                blocked_before="execution_artifact_approval",
                tenant_id=self.tenant_id,
                expected={
                    "target_type": "registry_entry",
                    "target_id": ids.get("execution_artifact_id"),
                    "target_version": ids.get("execution_artifact_version"),
                    "candidate_digest": ids.get("execution_artifact_checksum"),
                    "capital_pool_id": ids.get("capital_pool_id"),
                    "persona_id": ids.get("persona_id"),
                },
            )
        except HostedPause as exc:
            return [f"decision {exc.detail}"]
        return []

    # -- phase ordering ---------------------------------------------------
    def _check_order(self) -> None:
        index = HOSTED_PHASES.index(self.phase)
        if self.phase == "E_close":
            return  # retirement is also the governed exit when the chain stopped early
        if index and self.phases.get(HOSTED_PHASES[index - 1], {}).get("phase_status") != "completed":
            raise StimulusProofError(
                f"phase {self.phase} requires completed predecessor phase {HOSTED_PHASES[index - 1]}"
            )

    def _result(self, status: str, blocked_before: str | None = None, **extra: Any) -> dict[str, Any]:
        record = {
            "phase": self.phase,
            "phase_status": status,
            "blocked_before": blocked_before,
            "updated_at": _utc_now(),
            **extra,
        }
        self.phases[self.phase] = record
        return record

    # -- dispatch ---------------------------------------------------------
    def run(self) -> Evidence:
        evidence = self.gate.evidence
        try:
            self._check_order()
            previous = self.phases.get(self.phase, {})
            # E_close verifies and rejects per record instead of pausing as a whole.
            problems = [] if self.phase == "E_close" else self.verify_predecessors()
            if previous.get("phase_status") == "completed" and self.phase not in {"D_observe", "E_close"} and not problems:
                record = dict(previous, reused=True)  # old receipt is a hint; owner truth just re-verified
                self.phases[self.phase] = record
            else:
                if problems:
                    record = self._result("paused", f"{self.phase}:predecessor_verification", evidence=problems)
                elif self.phase == "E_close":
                    record = self._close()
                else:
                    record = self._launch_phase()
            evidence.status = "passed" if record["phase_status"] == "completed" else record["phase_status"]
            if record["phase_status"] == "failed":
                evidence.error = str(record.get("error") or "phase failed")
            return evidence
        except Exception as exc:
            evidence.status, evidence.error = "failed", f"{type(exc).__name__}: {exc}"
            self._result("failed", error=evidence.error)
            raise
        finally:
            evidence.loops = [self.loops[k] for k in CANONICAL_LOOP_IDS if k in self.loops]
            evidence.hosted = self._receipt()
            evidence.write()

    def _receipt(self) -> dict[str, Any]:
        current = self.phases.get(self.phase, {})
        all_done = all(self.phases.get(p, {}).get("phase_status") == "completed" for p in HOSTED_PHASES)
        # The parent cannot read the baseline fleet count; without it the chain is not proven closed.
        all_done = all_done and self.phases.get("E_close", {}).get("baseline_fleet_check") == "verified"
        return {
            "all12_passed": bool(all_done and len(self.loops) == len(CANONICAL_LOOP_IDS)),
            "baseline_fleet": self.baseline_fleet,
            "blocked_before": current.get("blocked_before"),
            "chain_id": self.chain_id,
            "decisions": self.decisions,
            "loops": [self.loops[k] for k in CANONICAL_LOOP_IDS if k in self.loops],
            "phase": self.phase,
            "phase_status": current.get("phase_status"),
            "phases": self.phases,
            "resource_ids": self.resource_ids,
            "schema_version": HOSTED_RECEIPT_SCHEMA,
            "tenant_id": self.tenant_id,
        }

    def _capture_baseline(self) -> str | None:
        """Record the fleet before the first relevant mutation; return a pause cause if unreadable."""

        if self.baseline_fleet or self.resource_ids.get("runtime_binding_id") or self.resource_ids.get("capital_binding_id"):
            return None  # already recorded, or too late to claim a pre-mutation baseline
        status, payload = self.owner.request("runtime", "GET", FLEET_PATH)
        snapshot = _fleet_snapshot(payload) if status == 200 else None
        if snapshot is None:
            return f"runtime fleet desired-state read returned {status}; baseline cannot be recorded"
        self.baseline_fleet = dict(snapshot, recorded_at=_utc_now())
        return None

    def _launch_phase(self) -> dict[str, Any]:
        domain = HOSTED_PHASE_SUITE[self.phase]
        if self.phase == "C_promote":
            cause = self._capture_baseline()
            if cause:
                return self._result("paused", "C_promote:baseline_unavailable", evidence=[cause])
        with tempfile.TemporaryDirectory(prefix="pantheon-l12-hosted-") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "chain-input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "chain_id": self.chain_id,
                        "decisions": self.decisions,
                        "phase": self.phase,
                        "predecessors": self.resource_ids,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            ids_path = root / "created-resource-ids.json"
            try:
                self.gate._run_domain_suite(
                    domain,
                    root / f"{domain}-report.json",
                    extra_env={
                        HOSTED_FLAG: "1",
                        "PANTHEON_L12_HOSTED_CHAIN_PHASE": self.phase,
                        "PANTHEON_L12_HOSTED_CHAIN_INPUT": str(input_path),
                        HOSTED_IDS_PATH_ENV: str(ids_path),
                    },
                    accepted_statuses=("passed", "paused"),
                )
            finally:
                # The child flushes every id it creates; keep them even if it crashed or the
                # report is unusable, so E_close can still retire what the phase created.
                self._merge_created_ids(ids_path)
        report = self.gate._reports[domain]
        if report.get("chain_id") != self.chain_id or report.get("phase") != self.phase:
            raise StimulusProofError(f"{domain} receipt does not echo chain id and phase")
        status = str(report.get("phase_status") or "")
        if status not in HOSTED_PHASE_STATUSES:
            raise StimulusProofError(f"{domain} receipt phase_status {status!r} is not recognised")
        reported = report.get("resource_ids") or {}
        if not isinstance(reported, Mapping):
            raise StimulusProofError(f"{domain} receipt resource_ids must be an object")
        self.resource_ids.update(
            {str(k): _typed_id(str(k), v) for k, v in reported.items() if str(v or "").strip()}
        )
        cases = _report_cases(domain, report)
        wanted = HOSTED_PHASE_LOOPS[self.phase]
        partial: dict[str, str] = {}
        for loop_id, loop_domain, case_key in LOOP_CASES:
            if loop_id in wanted and case_key in cases:
                try:
                    self.loops[loop_id] = _normalize_case(
                        loop_id=loop_id, domain=loop_domain, source_case=cases[case_key]
                    )
                except StimulusProofError as exc:
                    if status == "completed":
                        raise
                    # A paused phase's unfinished case is progress, not a completed loop.
                    partial[loop_id] = str(exc)
        if status == "completed":
            missing = [loop_id for loop_id in wanted if loop_id not in self.loops]
            if missing:
                raise StimulusProofError(f"{self.phase} completed without loop evidence for {missing}")
        return self._result(
            status,
            report.get("blocked_before"),
            partial_cases=partial,
            report_sha256=self.gate.evidence.domain_suites[domain]["report_sha256"],
        )

    def _merge_created_ids(self, ids_path: Path) -> None:
        try:
            created = json.loads(ids_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(created, Mapping):
            return
        for key, value in created.items():
            try:
                self.resource_ids[str(key)] = _typed_id(str(key), value)
            except StimulusProofError:
                continue  # an unusable id is never retired; the report path still rejects it loudly

    # -- E_close ----------------------------------------------------------
    def _close(self) -> dict[str, Any]:
        actor = str(getattr(self.owner, "actor_id", "") or "")
        roles = tuple(getattr(self.owner, "roles", ()) or ())
        retired: list[dict[str, Any]] = []
        retained: list[dict[str, Any]] = []
        rejected: list[str] = []
        incomplete: list[str] = []
        failures: list[str] = []
        verified: set[str] = set()
        for kind, spec in RECORD_SPECS.items():
            record_id = self.resource_ids.get(kind)
            if not record_id:
                continue
            quoted = urllib.parse.quote(record_id, safe="")
            path = spec.read_path.format(id=quoted)
            status, record = self._read(spec, path)
            mismatch = self._check_scope(kind, record_id, record) if status == 200 else f"{kind} {record_id}: GET {status}"
            if mismatch:
                rejected.append(mismatch)  # unrelated or missing: never touched, never retired
                continue
            verified.add(kind)
            if _record_state(record) in spec.retired_states:
                retired.append({"kind": kind, "id": record_id, "already": True})
                continue
            if kind == "plan_id" and _record_state(record) == "executed":
                # An executed plan is immutable (executed -> aborted is refused): retire its saga instead.
                self._retire_executed_plan_saga(record_id, retired, retained, incomplete, failures)
                continue
            identity = getattr(self.owner, "identity_for", None)
            body_actor, body_roles = identity(spec.service) if callable(identity) else (actor, roles)
            body = spec.retire_body(record, self.chain_id, body_actor, body_roles)
            if body is None:
                incomplete.append(f"{kind} {record_id}: executing identity has no role for retirement")
                continue
            code, _ = self.owner.request(
                spec.service,
                spec.retire_method or "POST",
                spec.retire_path.format(id=quoted),
                body,
                idempotency_key=f"{self.chain_id}-e-close-{kind}",
            )
            if code not in (200, 201, 202):
                failures.append(f"{kind} {record_id}: retire returned {code}")
                continue
            status, after = self._read(spec, path)  # acceptance is not retirement: read it back
            if status == 200 and _record_state(after) in spec.retired_states:
                retired.append({"kind": kind, "id": record_id, "http": code})
            else:
                incomplete.append(f"{kind} {record_id}: retire accepted ({code}) but state is {_record_state(after)!r}")
        h1 = _decision_id(self.decisions.get("H1"))
        if h1:
            self._revoke_h1(h1, verified, actor, roles, retired, rejected, incomplete, failures)
        residual = sorted(k for k in self.resource_ids if k not in RECORD_SPECS)
        baseline_check, baseline_note = self._baseline_check()
        if baseline_check != "verified":
            incomplete.append(f"baseline fleet: {baseline_note}")
        pending = rejected + incomplete
        return self._result(
            "failed" if failures else ("paused" if pending else "completed"),
            "E_close:cleanup_incomplete" if pending and not failures else None,
            error="; ".join(failures) or None,
            retired=retired,
            retained_history=retained,
            rejected_unrelated=rejected,
            incomplete_cleanup=incomplete,
            residual_non_retirable=residual,
            baseline_fleet_check=baseline_check,
        )

    def _retire_executed_plan_saga(self, plan_id, retired, retained, incomplete, failures) -> None:
        """Retire the same-plan deployment saga; the executed plan itself stays as labelled history."""
        label = f"plan_id {plan_id}"
        binding_id = self.resource_ids.get("runtime_binding_id")
        if not binding_id or not any(r.get("kind") == "runtime_binding_id" for r in retired):
            incomplete.append(f"{label}: executed; runtime binding is not retired, saga left untouched")
            return
        quoted = urllib.parse.quote(plan_id, safe="")
        status, listed = self.owner.request("deployment", "GET", f"/api/deployment/sagas?plan_id={quoted}")
        items = listed if isinstance(listed, list) else (listed.get("sagas") or listed.get("items") or []) if isinstance(listed, Mapping) else []
        sagas = [
            s for s in items
            if isinstance(s, Mapping) and s.get("plan_id") == plan_id and s.get("tenant_id") == self.tenant_id
        ]
        if status != 200 or len(sagas) != 1 or not sagas[0].get("saga_id"):
            incomplete.append(f"{label}: expected exactly one same-plan saga (GET {status}, found {len(sagas)})")
            return
        saga = sagas[0]
        if saga.get("binding_id") != binding_id:
            incomplete.append(f"{label}: saga {saga['saga_id']} binding {saga.get('binding_id')!r} != chain runtime binding {binding_id!r}")
            return
        saga_id = urllib.parse.quote(str(saga["saga_id"]), safe="")
        saga_path = f"/api/deployment/sagas/{saga_id}"
        state = str(saga.get("status") or "").lower()
        http = None
        if state != "retired":
            if state != "retiring":
                http, _ = self.owner.request(
                    "deployment", "POST", f"{saga_path}/retire",
                    {"reason": f"{self.chain_id} E_close"}, idempotency_key=f"{self.chain_id}-e-close-saga",
                )
                if http not in (200, 201, 202):
                    failures.append(f"{label}: saga retire returned {http}")
                    return
            http, _ = self.owner.request(
                "deployment", "POST", f"{saga_path}/retire/finalize",
                {"note": f"{self.chain_id} E_close"}, idempotency_key=f"{self.chain_id}-e-close-saga-finalize",
            )
            if http not in (200, 201, 202):
                failures.append(f"{label}: saga retire finalize returned {http}")
                return
        status, after = self.owner.request("deployment", "GET", saga_path)
        after_state = str(_unwrap(after).get("status") or "").lower() if status == 200 else ""
        if after_state != "retired":
            incomplete.append(f"{label}: saga {saga['saga_id']} retire accepted but state is {after_state!r}")
            return
        retained.append({
            "kind": "plan_id", "id": plan_id, "plan_state": "executed",
            "saga_id": saga["saga_id"], "saga_state": "retired", "binding_id": binding_id,
            "note": "executed plan is immutable; retained as history, not retired",
        })

    def _baseline_check(self) -> tuple[str, str]:
        """Re-read the Runtime owner and compare with the baseline recorded before mutation."""

        if not self.baseline_fleet:
            return "baseline_not_recorded", "no pre-mutation baseline in the receipt; fleet not compared"
        status, payload = self.owner.request("runtime", "GET", FLEET_PATH)
        now = _fleet_snapshot(payload) if status == 200 else None
        if now is None:
            return "unreadable", f"runtime fleet desired-state read returned {status}"
        chain_active = sorted(
            set(now["binding_ids"]) & {self.resource_ids.get("runtime_binding_id") or ""}
        )
        if chain_active:
            return "chain_binding_still_active", f"chain binding still desired: {chain_active}"
        if now["active_count"] != self.baseline_fleet.get("active_count") or now["binding_ids"] != self.baseline_fleet.get("binding_ids"):
            return "mismatch", f"fleet now {now} differs from baseline {self.baseline_fleet}"
        return "verified", ""

    def _revoke_h1(self, h1, verified, actor, roles, retired, rejected, incomplete, failures) -> None:
        registry_id = self.resource_ids.get("registry_id")
        quoted = urllib.parse.quote(h1, safe="")
        status, record = self.owner.request("governance", "GET", APPROVAL_READ.format(id=quoted))
        record = _unwrap(record)
        if status != 200 or not record:
            rejected.append(f"H1 {h1}: owner GET returned {status}")
            return
        if "registry_id" not in verified:
            rejected.append(f"H1 {h1}: chain registry entry not verified; approval left untouched")
            return
        if _record_tenant(record) != self.tenant_id or record.get("target_id") != registry_id:
            rejected.append(f"H1 {h1}: not an approval for this chain registry entry")
            return
        if str(record.get("decision_state") or "").lower() == "revoked":
            retired.append({"kind": "H1_approval", "id": h1, "already": True})
            return
        role = next((r for r in H1_REVOKE_ROLES if r in roles), None)
        version = record.get("version")
        if not actor or role is None or not isinstance(version, int):
            incomplete.append(f"H1 {h1}: revoke needs actor, a Governance revoke role and the record version")
            return
        code, _ = self.owner.request(
            "governance", "POST", APPROVAL_REVOKE.format(id=quoted),
            {"actor_id": actor, "actor_role": role, "expected_version": version},
            idempotency_key=f"{self.chain_id}-e-close-H1",
        )
        if code not in (200, 201, 202):
            failures.append(f"H1 {h1}: revoke returned {code}")
            return
        status, after = self.owner.request("governance", "GET", APPROVAL_READ.format(id=quoted))
        if status == 200 and str(_unwrap(after).get("decision_state") or "").lower() == "revoked":
            retired.append({"kind": "H1_approval", "id": h1, "http": code})
        else:
            incomplete.append(f"H1 {h1}: revoke accepted ({code}) but not read back as revoked")


def _load_prior_receipt() -> Mapping[str, Any] | None:
    raw = os.getenv("PANTHEON_L12_HOSTED_CHAIN_PRIOR_RECEIPT", "").strip()
    if not raw:
        return None
    return json.loads(Path(raw).read_text(encoding="utf-8"))


def _load_chain_input(chain_id: str, phase: str) -> Mapping[str, Any]:
    """Shared packet (PANTHEON_L12_HOSTED_CHAIN_INPUT): chain_id, phase, decisions, predecessors."""

    raw = os.getenv("PANTHEON_L12_HOSTED_CHAIN_INPUT", "").strip()
    if not raw:
        return {}
    packet = json.loads(Path(raw).read_text(encoding="utf-8"))
    if not isinstance(packet, Mapping):
        raise StimulusProofError("hosted chain input must be a JSON object")
    for key, expected in (("chain_id", chain_id), ("phase", phase)):
        if packet.get(key) not in (None, expected):
            raise StimulusProofError(f"hosted chain input {key} does not match the requested {expected!r}")
    return packet


def run_hosted_phase_from_env() -> Evidence:
    chain_id = _required_env("PANTHEON_L12_CROSS_LOOP_RUN_ID")
    phase = _required_env("PANTHEON_L12_HOSTED_CHAIN_PHASE")
    packet = _load_chain_input(chain_id, phase)
    refs = os.getenv("PANTHEON_L12_HOSTED_CHAIN_DECISION_REFS", "").strip()
    decisions = {**(json.loads(refs) if refs else {}), **dict(packet.get("decisions") or {})}
    runner = HostedChainRunner(
        Settings.from_env(),
        chain_id=chain_id,
        phase=phase,
        tenant_id=_required_env("PANTHEON_L12_TENANT_ID"),
        owner=UrllibOwnerClient.from_env(),
        prior_receipt=_load_prior_receipt(),
        decision_refs=decisions,
        predecessors=packet.get("predecessors") or None,
    )
    return runner.run()


def test_stimulus_gate_maps_all_twelve_canonical_loops() -> None:
    assert len(CANONICAL_LOOP_IDS) == 12
    assert len(set(CANONICAL_LOOP_IDS)) == 12
    assert {domain for _, domain, _ in LOOP_CASES} == {"research", "human", "runtime"}


def test_stimulus_gate_normalizes_fresh_owner_receipts() -> None:
    research = _normalize_case(
        loop_id="source_ingestion",
        domain="research",
        source_case={
            "authority_readback": {"source_id": "source-fresh-1"},
            "next_consumer": {"receipt_id": "distill-fresh-1"},
            "owner": {"compose_service": "source-ingest"},
            "terminal_output": {"id": "source-fresh-1"},
            "trigger": {"ingest_run_id": "ingest-fresh-1"},
        },
    )
    runtime = _normalize_case(
        loop_id="capital_pool_execution",
        domain="runtime",
        source_case={
            "authority_readback": {"event_id": "event-fresh-1"},
            "next_consumer_readback": {"queue_depth": 0},
            "owner_worker_identity": {"service": "paper-fleet-reconciler"},
            "terminal_output_id": "paper-fill-fresh-1",
            "trigger_id": "binding-fresh-1",
        },
    )
    assert research["trigger"]["ingest_run_id"] == "ingest-fresh-1"
    assert research["terminal_output"]["id"] == "source-fresh-1"
    assert runtime["trigger"] == "binding-fresh-1"
    assert runtime["owner_observation"]["service"] == "paper-fleet-reconciler"


def test_stimulus_gate_binds_its_expected_sha_to_child_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected_sha = "a" * 40
    gate = StimulusDrivenClosureGate(
        Settings(
            expected_sha=expected_sha,
            evidence_output=tmp_path / "parent-report.json",
            timeout_seconds=60,
        )
    )
    captured_environment: dict[str, str] = {}

    def fake_run(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        environment = kwargs["env"]
        captured_environment.update(environment)
        Path(environment["PANTHEON_L12_REPORT_PATH"]).write_text(
            json.dumps({"git_sha": expected_sha, "status": "passed"}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    gate._run_domain_suite("runtime", tmp_path / "runtime-report.json")

    assert captured_environment["PANTHEON_L12_EXPECTED_SHA"] == expected_sha
    assert captured_environment["PANTHEON_L12_DEPLOYED_E2E"] == "1"


def test_prebuilt_verifier_is_explicitly_not_the_closure_gate() -> None:
    source = (REPO_ROOT / LEGACY_VERIFIER).read_text(encoding="utf-8")
    assert "prebuilt_identity_verifier_not_closure_gate" in source
    assert "PANTHEON_L12_PREBUILT_IDENTITY_VERIFIER" in source
    assert "PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E" not in source


@pytest.mark.skipif(
    os.getenv("PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E", "").strip().lower()
    not in {"1", "true", "yes"}
    or os.getenv(HOSTED_FLAG, "").strip().lower() in {"1", "true", "yes"},
    reason="set PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E=1 (without hosted mode) for the full isolated closure gate",
)
def test_stimulus_driven_cross_loop_management_closure() -> None:
    evidence = StimulusDrivenClosureGate(Settings.from_env()).run()
    assert evidence.status == "passed"
    assert len(evidence.loops) == 12
    assert evidence.management["canonical_loop_count"] == 12


@pytest.mark.skipif(
    os.getenv(HOSTED_FLAG, "").strip().lower() not in {"1", "true", "yes"}
    or os.getenv("PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E", "").strip().lower()
    not in {"1", "true", "yes"},
    reason="set PANTHEON_L12_HOSTED_CHAIN=1 with the stimulus gate for a hosted phase",
)
def test_hosted_chain_phase() -> None:
    # paused is normal incomplete progress, not a failure and never all12 success
    evidence = run_hosted_phase_from_env()
    assert evidence.status in {"passed", "paused"}
