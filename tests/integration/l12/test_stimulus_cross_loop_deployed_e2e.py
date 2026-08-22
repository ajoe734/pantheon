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
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

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

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.run_id = f"{TASK_ID.lower()}-{uuid.uuid4().hex[:12]}"
        self.evidence = Evidence(settings=settings, run_id=self.run_id)
        self._reports: dict[str, Mapping[str, Any]] = {}

    def _run_domain_suite(self, domain: str, report_path: Path) -> None:
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
            raise StimulusProofError(
                f"{domain} stimulus suite exited {completed.returncode}; its temporary report is retained"
            )
        if not report_path.is_file():
            raise StimulusProofError(f"{domain} stimulus suite did not create a report")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StimulusProofError(f"{domain} stimulus report is invalid") from exc
        if not isinstance(report, Mapping) or report.get("status") != "passed":
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
        if (
            failure_attribution.get("loop_id") != "capital_pool_execution"
            or failure_attribution.get("status") != "degraded"
            or "paper-fleet-reconciler" not in str(failure_attribution.get("summary") or "")
        ):
            raise StimulusProofError(
                "BFF did not attribute the functional paper-fleet failure to Capital loop"
            )
        self.evidence.management = {
            "canonical_loop_count": len(rows),
            "endpoint": management_readback.get("endpoint"),
            "functional_worker_failure": {
                "owner_loop": failure_attribution.get("loop_id"),
                "target": "paper-fleet-reconciler",
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
    not in {"1", "true", "yes"},
    reason="set PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E=1 for the deployed closure gate",
)
def test_stimulus_driven_cross_loop_management_closure() -> None:
    evidence = StimulusDrivenClosureGate(Settings.from_env()).run()
    assert evidence.status == "passed"
    assert len(evidence.loops) == 12
    assert evidence.management["canonical_loop_count"] == 12
