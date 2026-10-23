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
    ) -> None:
        self.urls = dict(urls)
        self.token = token
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
        return cls(
            urls,
            _required_env("PANTHEON_L12_HOSTED_OWNER_TOKEN"),
            os.getenv("PANTHEON_L12_HOSTED_OWNER_ACTOR_ID", "").strip(),
            roles,
        )

    def request(
        self,
        service: str,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}
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


# Retirement order matters: binding before pool, runtime/plan before artifact.
RECORD_SPECS: dict[str, OwnerRecordSpec] = {
    "capital_binding_id": OwnerRecordSpec(
        "capital", "/api/bindings/{id}", "PATCH", "/api/bindings/{id}/status",
        _capital_status_body("revoked", ("persona.admin", "operator")), ("revoked",),
    ),
    "runtime_binding_id": OwnerRecordSpec(
        "runtime", "/api/runtime-bindings/{id}", "POST", "/api/runtime-bindings/{id}/retire",
        lambda *_a: {},  # runtime retire reads only an optional retired_at
        ("retired", "inactive"),
    ),
    "plan_id": OwnerRecordSpec(
        "deployment", "/api/deployment/plans/{id}", "POST", "/api/deployment/plans/{id}/status",
        lambda *_a: {"status": "aborted"}, ("aborted",),
    ),
    "capital_pool_id": OwnerRecordSpec(
        "capital", "/api/capital-pools/{id}", "PATCH", "/api/capital-pools/{id}/status",
        _capital_status_body("archived", ("capital.admin", "operator")), ("archived",),
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
            **{str(k): str(v) for k, v in (predecessors or {}).items() if str(v or "").strip()},
        }
        self.decisions: dict[str, str] = {**dict(hosted.get("decisions") or {}), **dict(decision_refs or {})}
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
        if chain != self.chain_id:
            return f"{kind} {record_id}: chain label {chain!r} != {self.chain_id!r}"
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
        for name, decision_id in sorted(self.decisions.items()):
            if name not in GOVERNANCE_DECISIONS:
                if not str(decision_id or "").strip():
                    problems.append(f"decision {name}: empty consent reference")
                continue  # recorded consent (e.g. H2): not a Governance record, never gated here
            problems.extend(self._verify_decision(name, decision_id))
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

    # -- phase ordering ---------------------------------------------------
    def _check_order(self) -> None:
        index = HOSTED_PHASES.index(self.phase)
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

    def _launch_phase(self) -> dict[str, Any]:
        domain = HOSTED_PHASE_SUITE[self.phase]
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
            self.gate._run_domain_suite(
                domain,
                root / f"{domain}-report.json",
                extra_env={
                    HOSTED_FLAG: "1",
                    "PANTHEON_L12_HOSTED_CHAIN_PHASE": self.phase,
                    "PANTHEON_L12_HOSTED_CHAIN_INPUT": str(input_path),
                },
                accepted_statuses=("passed", "paused"),
            )
        report = self.gate._reports[domain]
        if report.get("chain_id") != self.chain_id or report.get("phase") != self.phase:
            raise StimulusProofError(f"{domain} receipt does not echo chain id and phase")
        status = str(report.get("phase_status") or "")
        if status not in HOSTED_PHASE_STATUSES:
            raise StimulusProofError(f"{domain} receipt phase_status {status!r} is not recognised")
        reported = report.get("resource_ids") or {}
        if not isinstance(reported, Mapping):
            raise StimulusProofError(f"{domain} receipt resource_ids must be an object")
        self.resource_ids.update({str(k): str(v) for k, v in reported.items() if str(v or "").strip()})
        cases = _report_cases(domain, report)
        wanted = HOSTED_PHASE_LOOPS[self.phase]
        for loop_id, loop_domain, case_key in LOOP_CASES:
            if loop_id in wanted and case_key in cases:
                self.loops[loop_id] = _normalize_case(
                    loop_id=loop_id, domain=loop_domain, source_case=cases[case_key]
                )
        if status == "completed":
            missing = [loop_id for loop_id in wanted if loop_id not in self.loops]
            if missing:
                raise StimulusProofError(f"{self.phase} completed without loop evidence for {missing}")
        return self._result(
            status,
            report.get("blocked_before"),
            report_sha256=self.gate.evidence.domain_suites[domain]["report_sha256"],
        )

    # -- E_close ----------------------------------------------------------
    def _close(self) -> dict[str, Any]:
        actor = str(getattr(self.owner, "actor_id", "") or "")
        roles = tuple(getattr(self.owner, "roles", ()) or ())
        retired: list[dict[str, Any]] = []
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
            body = spec.retire_body(record, self.chain_id, actor, roles)
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
        h1 = self.decisions.get("H1")
        if h1:
            self._revoke_h1(h1, verified, actor, roles, retired, rejected, incomplete, failures)
        residual = sorted(k for k in self.resource_ids if k not in RECORD_SPECS)
        pending = rejected + incomplete
        return self._result(
            "failed" if failures else ("paused" if pending else "completed"),
            "E_close:cleanup_incomplete" if pending and not failures else None,
            error="; ".join(failures) or None,
            retired=retired,
            rejected_unrelated=rejected,
            incomplete_cleanup=incomplete,
            residual_non_retirable=residual,
            baseline_fleet_check="not_verified_by_parent",
        )

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
