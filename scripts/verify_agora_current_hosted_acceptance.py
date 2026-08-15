#!/usr/bin/env python3
"""Fail-closed acceptance aggregator for the current hosted Agora exact pair.

This command does not simulate a hosted deployment.  It observes the public
frontend manifest, BFF identity, and BFF lifecycle endpoints directly.  The
checks that need credentials or a restart/deployment lease are accepted only
as fresh, exact-pair-bound evidence from successful GitHub Actions runs.

An in-process product test is useful development evidence, but it is never
hosted acceptance evidence and cannot make this command pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "AGORA-HOSTED-REAL-ACCEPTANCE-20260815"
PROGRAM_ID = "agora-product-correction-20260813"
DEFAULT_DEV_BFF_URL = "https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io"
DEFAULT_DEV_FE_URL = "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_CAPABILITY_MANIFEST_SHA = "92ed24a99fb40c60c1e10c31b7923bcc03a89268ab31e6779a63dd4dbb64b9ff"
EXPECTED_BUNDLE_INDEX_SHA = "a1aafe05463548dca37b6d6cd8fb8d3e1b3db88c217c02c0282828d57adf95fd"
EXPECTED_OPENAPI_SHA = "8b0d3d2b217ca9eb360e13894ee43b941518b66fc68a3d9fb8b80ee4582c6d7c"

SERVICE_STAGE_IDS = (
    "stage_01_identity_scope",
    "stage_02_workshop_reconstruction",
    "stage_03_strategy_version_selection",
    "stage_04_research_candidate_pool",
    "stage_05_workspace_compiler",
    "stage_06_decision_event_intent",
    "stage_07_performance_suggestions",
    "stage_08_dataset_extraction",
    "stage_09_policy_learning_admission",
    "stage_10_independent_consultation",
    "stage_11_two_tenant_isolation",
    "stage_12_replay_cas_recovery",
    "stage_13_restart_readback",
    "stage_14_fail_closed_invariants",
)
LINEAGE_KEYS = (
    "workshop_id",
    "strategy_id",
    "version_id",
    "candidate_pool_id",
    "workspace_id",
    "decision_event_id",
    "dataset_version_id",
    "policy_candidate_id",
    "consultation_memo_id",
)
NEGATIVE_CONTROL_KEYS = (
    "cross_tenant_access_rejected",
    "cross_user_access_rejected",
    "self_attestation_rejected",
    "broker_order_authority_absent",
    "capital_authority_absent",
    "fixture_fallback_rejected",
    "client_derived_truth_rejected",
)
RESTART_STORE_KEYS = (
    "workshop",
    "governance",
    "dataset",
)

logger = logging.getLogger("verify_agora_current_hosted_acceptance")
Transport = Callable[[str, float], tuple[int, Mapping[str, Any]]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgoraAcceptanceError(label, f"{label} must be a JSON object")
    return value


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AgoraAcceptanceError(label, f"{label} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgoraAcceptanceError(label, f"{label} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AgoraAcceptanceError(label, f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _default_transport(url: str, timeout_seconds: float) -> tuple[int, Mapping[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "User-Agent": "pantheon-agora-hosted-acceptance/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgoraAcceptanceError("network", f"GET {url} failed: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgoraAcceptanceError("network", f"GET {url} did not return JSON") from exc
    return status, _mapping(payload, "network.response")


class AgoraAcceptanceError(RuntimeError):
    """A required hosted property could not be proven."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


@dataclass
class AcceptanceConfig:
    expected_bff_sha: str
    expected_fe_sha: str
    service_journey_evidence: Optional[Path] = None
    browser_evidence: Optional[Path] = None
    restart_evidence: Optional[Path] = None
    rollback_evidence: Optional[Path] = None
    bff_base_url: str = DEFAULT_DEV_BFF_URL
    fe_base_url: str = DEFAULT_DEV_FE_URL
    evidence_dir: Path = field(
        default_factory=lambda: REPO_ROOT / "docs" / "deployment" / "evidence" / "agora" / TASK_ID
    )
    max_evidence_age_seconds: int = 21600
    request_timeout_seconds: float = 15.0
    strict: bool = False
    mode: str = "hosted"
    task_id: str = TASK_ID
    program_id: str = PROGRAM_ID

    def validate(self) -> None:
        if self.mode != "hosted":
            raise AgoraAcceptanceError(
                "config.mode",
                "only mode=hosted is supported; in-process or simulated evidence cannot qualify a hosted pair",
            )
        for label, value in (
            ("expected_bff_sha", self.expected_bff_sha),
            ("expected_fe_sha", self.expected_fe_sha),
        ):
            if not SHA40_RE.fullmatch(value):
                raise AgoraAcceptanceError("config.sha", f"{label} must be a lowercase 40-character commit SHA")
        for label, value in (("bff_base_url", self.bff_base_url), ("fe_base_url", self.fe_base_url)):
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
                raise AgoraAcceptanceError("config.url", f"{label} must be an HTTPS origin without a path")
        if self.max_evidence_age_seconds <= 0:
            raise AgoraAcceptanceError("config.age", "max_evidence_age_seconds must be positive")


@dataclass
class GateCheckResult:
    gate_id: str
    name: str
    status: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class HostedAcceptanceReport:
    schema_version: str
    program_id: str
    task_id: str
    verified_at: str
    mode: str
    overall_status: str
    exact_pair: dict[str, Any]
    gate_results: list[GateCheckResult]
    gap_matrix: list[dict[str, Any]]
    summary: dict[str, Any]


class AgoraHostedAcceptanceVerifier:
    """Aggregate direct hosted observations and externally produced run evidence."""

    def __init__(self, config: AcceptanceConfig, *, transport: Transport = _default_transport):
        config.validate()
        self.config = config
        self.transport = transport
        self.started = time.monotonic()
        self._artifacts: dict[str, Mapping[str, Any]] = {}
        self._manifest: Mapping[str, Any] = {}
        self._bff_version: Mapping[str, Any] = {}

    def _get_json(self, label: str, url: str) -> Mapping[str, Any]:
        status, payload = self.transport(url, self.config.request_timeout_seconds)
        if status != 200:
            raise AgoraAcceptanceError(label, f"GET {url} returned HTTP {status}, expected 200")
        return payload

    def _load_evidence(self, kind: str, path: Optional[Path]) -> Mapping[str, Any]:
        cached = self._artifacts.get(kind)
        if cached is not None:
            return cached
        if path is None:
            raise AgoraAcceptanceError(f"evidence.{kind}", f"--{kind.replace('_', '-')}-evidence is required")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AgoraAcceptanceError(f"evidence.{kind}", f"evidence file does not exist: {path}") from exc
        except json.JSONDecodeError as exc:
            raise AgoraAcceptanceError(f"evidence.{kind}", f"evidence file is not valid JSON: {path}") from exc
        artifact = _mapping(payload, f"evidence.{kind}")
        expected_schema = f"pantheon.agora.hosted-{kind.replace('_', '-')}-evidence.v1"
        if artifact.get("schema_version") != expected_schema:
            raise AgoraAcceptanceError(
                f"evidence.{kind}",
                f"schema_version must be {expected_schema}",
            )
        if artifact.get("result") != "passed" or artifact.get("mode") != "hosted":
            raise AgoraAcceptanceError(f"evidence.{kind}", "evidence must report result=passed and mode=hosted")
        observed_at = _parse_timestamp(artifact.get("observed_at"), f"evidence.{kind}.observed_at")
        age = (datetime.now(timezone.utc) - observed_at).total_seconds()
        if age < -300 or age > self.config.max_evidence_age_seconds:
            raise AgoraAcceptanceError(
                f"evidence.{kind}",
                f"evidence age {age:.0f}s is outside the allowed freshness window",
            )
        pair = _mapping(artifact.get("exact_pair"), f"evidence.{kind}.exact_pair")
        expected_pair = {
            "backend_sha": self.config.expected_bff_sha,
            "frontend_sha": self.config.expected_fe_sha,
            "bff_url": self.config.bff_base_url,
            "fe_url": self.config.fe_base_url,
        }
        for key, expected in expected_pair.items():
            observed = str(pair.get(key) or "").rstrip("/") if key.endswith("_url") else pair.get(key)
            compared = expected.rstrip("/") if key.endswith("_url") else expected
            if observed != compared:
                raise AgoraAcceptanceError(
                    f"evidence.{kind}",
                    f"exact_pair.{key} is {observed!r}, expected {compared!r}",
                )
        digest = artifact.get("artifact_digest_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise AgoraAcceptanceError(f"evidence.{kind}", "artifact_digest_sha256 must be a SHA-256 digest")
        self._artifacts[kind] = artifact
        return artifact

    def _verify_github_run(
        self,
        kind: str,
        artifact: Mapping[str, Any],
        *,
        expected_repository: str,
        expected_head_sha: str,
        allowed_workflows: tuple[str, ...],
    ) -> Mapping[str, Any]:
        producer = _mapping(artifact.get("producer"), f"evidence.{kind}.producer")
        if producer.get("kind") != "github-actions" or producer.get("repository") != expected_repository:
            raise AgoraAcceptanceError(
                f"evidence.{kind}.producer",
                f"producer must be github-actions in {expected_repository}",
            )
        workflow = producer.get("workflow")
        if workflow not in allowed_workflows:
            raise AgoraAcceptanceError(
                f"evidence.{kind}.producer",
                f"workflow {workflow!r} is not an accepted producer for {kind}",
            )
        run_id = str(producer.get("run_id") or "")
        if not run_id.isdigit() or int(run_id) <= 0:
            raise AgoraAcceptanceError(f"evidence.{kind}.producer", "run_id must be a positive integer")
        api_url = f"https://api.github.com/repos/{expected_repository}/actions/runs/{run_id}"
        run = self._get_json(f"evidence.{kind}.github_run", api_url)
        if (
            run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("head_sha") != expected_head_sha
            or run.get("path") != workflow
        ):
            raise AgoraAcceptanceError(
                f"evidence.{kind}.github_run",
                "GitHub run is not a successful exact-head run of the declared workflow",
            )
        html_url = run.get("html_url")
        if not isinstance(html_url, str) or f"/{expected_repository}/actions/runs/{run_id}" not in html_url:
            raise AgoraAcceptanceError(f"evidence.{kind}.github_run", "GitHub run URL is not canonical")
        return {
            "repository": expected_repository,
            "workflow": workflow,
            "run_id": run_id,
            "run_url": html_url,
            "head_sha": expected_head_sha,
        }

    def run_full_acceptance(self) -> HostedAcceptanceReport:
        gates = (
            (
                "gate_01_manifest_exact_pair",
                "Live manifest and exact deployed pair",
                self.verify_gate_01_manifest_exact_pair,
            ),
            (
                "gate_02_readiness_and_liveness",
                "Live BFF health, liveness, and readiness",
                self.verify_gate_02_readiness_and_liveness,
            ),
            (
                "gate_03_agora_product_journey",
                "Hosted service journey and browser E2E",
                self.verify_gate_03_agora_product_journey,
            ),
            (
                "gate_04_security_and_boundaries",
                "Hosted negative controls and authority boundaries",
                self.verify_gate_04_security_and_boundaries,
            ),
            (
                "gate_05_restart_persistence_readback",
                "Actual service restart and durable readback",
                self.verify_gate_05_restart_persistence_readback,
            ),
            (
                "gate_06_rollback_safety",
                "Gate-before-switch and rollback failure drill",
                self.verify_gate_06_rollback_safety,
            ),
        )
        results: list[GateCheckResult] = []
        for gate_id, name, runner in gates:
            started = time.monotonic()
            try:
                details = runner()
                results.append(
                    GateCheckResult(gate_id, name, "PASSED", round((time.monotonic() - started) * 1000, 2), details)
                )
            except Exception as exc:
                results.append(
                    GateCheckResult(
                        gate_id,
                        name,
                        "FAILED",
                        round((time.monotonic() - started) * 1000, 2),
                        error=str(exc),
                    )
                )
                if self.config.strict:
                    break

        passed = len(results) == len(gates) and all(result.status == "PASSED" for result in results)
        manifest_pair_id = self._manifest.get("pairId") if self._manifest else None
        report = HostedAcceptanceReport(
            schema_version="pantheon.agora.hosted-acceptance-report.v2",
            program_id=self.config.program_id,
            task_id=self.config.task_id,
            verified_at=_utc_now(),
            mode="hosted",
            overall_status="PASSED" if passed else "FAILED",
            exact_pair={
                "pair_id": manifest_pair_id,
                "backend_sha": self.config.expected_bff_sha,
                "frontend_sha": self.config.expected_fe_sha,
                "bff_url": self.config.bff_base_url,
                "fe_url": self.config.fe_base_url,
                "deployment_profile": "accepted" if passed else "unaccepted",
            },
            gate_results=results,
            gap_matrix=self._build_gap_matrix(results),
            summary={
                "total_gates": len(gates),
                "executed_gates": len(results),
                "passed_gates": sum(result.status == "PASSED" for result in results),
                "failed_gates": sum(result.status == "FAILED" for result in results),
                "duration_ms": round((time.monotonic() - self.started) * 1000, 2),
            },
        )
        self.write_evidence_artifacts(report)
        return report

    def verify_gate_01_manifest_exact_pair(self) -> dict[str, Any]:
        contracts_dir = REPO_ROOT / "docs" / "contracts" / "agora-product-v2"
        actual_hashes = {
            "capability_manifest_sha256": _sha256_file(contracts_dir / "agora_v2_capability_manifest.json"),
            "bundle_index_sha256": _sha256_file(contracts_dir / "agora_v2_bundle_index.json"),
            "openapi_sha256": _sha256_file(contracts_dir / "agora_product_v2.openapi.yaml"),
        }
        expected_hashes = {
            "capability_manifest_sha256": EXPECTED_CAPABILITY_MANIFEST_SHA,
            "bundle_index_sha256": EXPECTED_BUNDLE_INDEX_SHA,
            "openapi_sha256": EXPECTED_OPENAPI_SHA,
        }
        if actual_hashes != expected_hashes:
            raise AgoraAcceptanceError(
                "gate_01.contracts",
                "local Agora contract bytes do not match the qualified hashes",
            )

        manifest = self._get_json("gate_01.frontend_manifest", f"{self.config.fe_base_url}/deployment.json")
        version = self._get_json("gate_01.bff_version", f"{self.config.bff_base_url}/bff/version")
        self._manifest = manifest
        self._bff_version = version

        frontend = manifest.get("frontend") if isinstance(manifest.get("frontend"), Mapping) else {}
        bff = manifest.get("bff") if isinstance(manifest.get("bff"), Mapping) else {}
        fe_sha = manifest.get("commit") or manifest.get("frontendSha") or frontend.get("commitSha")
        manifest_bff_sha = manifest.get("bffCommit") or manifest.get("bffSourceCommitSha") or bff.get("sourceCommitSha")
        runtime_bff_sha = version.get("source_commit_sha")
        bff_host = str(manifest.get("bffHost") or bff.get("baseUrl") or "").rstrip("/")
        if fe_sha != self.config.expected_fe_sha:
            raise AgoraAcceptanceError(
                "gate_01.fe_sha",
                f"served FE SHA {fe_sha!r} != expected {self.config.expected_fe_sha}",
            )
        if manifest_bff_sha != self.config.expected_bff_sha:
            raise AgoraAcceptanceError(
                "gate_01.manifest_bff_sha",
                f"manifest BFF SHA {manifest_bff_sha!r} != expected {self.config.expected_bff_sha}",
            )
        if runtime_bff_sha != self.config.expected_bff_sha:
            raise AgoraAcceptanceError(
                "gate_01.runtime_bff_sha",
                f"runtime BFF SHA {runtime_bff_sha!r} != expected {self.config.expected_bff_sha}",
            )
        if manifest_bff_sha != runtime_bff_sha:
            raise AgoraAcceptanceError(
                "gate_01.identity_split",
                "frontend manifest BFF SHA and public BFF version disagree",
            )
        if bff_host != self.config.bff_base_url.rstrip("/"):
            raise AgoraAcceptanceError("gate_01.bff_host", "frontend manifest is bound to a different BFF origin")

        build = _mapping(manifest.get("buildMode"), "gate_01.buildMode")
        safe_build = (
            build.get("VITE_BFF_MODE") == "live"
            and build.get("VITE_BFF_FALLBACK") == "strict"
            and str(build.get("VITE_BFF_REAL_WRITES", "")).lower() == "false"
            and str(build.get("VITE_BFF_ALLOW_DEV_STUB_WRITES", "")).lower() == "false"
            and str(build.get("VITE_BFF_EMBEDDED_BEARER_TOKEN", "")).lower() == "false"
        )
        posture = _mapping(version.get("config_posture"), "gate_01.config_posture")
        compatibility = _mapping(manifest.get("agoraCompatibility"), "gate_01.agoraCompatibility")
        compatibility_backend = _mapping(compatibility.get("backend"), "gate_01.agoraCompatibility.backend")
        compatibility_frontend = _mapping(compatibility.get("frontend"), "gate_01.agoraCompatibility.frontend")
        if (
            manifest.get("deploymentState") != "accepted"
            or manifest.get("profile") != "read-only"
            or not safe_build
            or posture.get("auth_mode") != "strict"
            or posture.get("auth_stub") is not False
            or compatibility.get("compatibility_status") != "accepted"
            or compatibility_backend.get("runtime_commit") != self.config.expected_bff_sha
            or compatibility_frontend.get("runtime_commit") != self.config.expected_fe_sha
        ):
            raise AgoraAcceptanceError(
                "gate_01.posture",
                "served pair is not accepted, read-only, strict-auth, safe-write, and compatibility-bound",
            )
        return {
            "observed_frontend_sha": fe_sha,
            "observed_manifest_bff_sha": manifest_bff_sha,
            "observed_runtime_bff_sha": runtime_bff_sha,
            "pair_id": manifest.get("pairId"),
            "deployment_state": manifest.get("deploymentState"),
            "profile": manifest.get("profile"),
            "contract_hashes": actual_hashes,
            "manifest_gate_run": _mapping(manifest.get("gate"), "gate_01.gate"),
        }

    def verify_gate_02_readiness_and_liveness(self) -> dict[str, Any]:
        observed: dict[str, Any] = {}
        for endpoint in ("healthz", "livez", "readyz"):
            observed[endpoint] = self._get_json(
                f"gate_02.{endpoint}", f"{self.config.bff_base_url}/{endpoint}"
            )
            if observed[endpoint].get("status") != "ok":
                raise AgoraAcceptanceError(f"gate_02.{endpoint}", f"/{endpoint} status is not ok")
        if observed["livez"].get("live") is not True or observed["readyz"].get("ready") is not True:
            raise AgoraAcceptanceError("gate_02.lifecycle", "BFF is not live and ready")
        dependencies = _mapping(observed["readyz"].get("dependencies"), "gate_02.dependencies")
        projector = _mapping(dependencies.get("lifecycle_projector"), "gate_02.lifecycle_projector")
        freshness = _mapping(projector.get("freshness"), "gate_02.lifecycle_projector.freshness")
        if (
            projector.get("status") != "ok"
            or projector.get("ready") is not True
            or projector.get("worker_status") != "ready"
            or projector.get("controller_status") != "ready"
            or projector.get("accepted_live") is not True
            or projector.get("deployment_sha") != self.config.expected_bff_sha
            or projector.get("checkpoint") != projector.get("source_high_watermark")
            or projector.get("backlog") != 0
            or freshness.get("stale") is not False
        ):
            raise AgoraAcceptanceError("gate_02.projector", "lifecycle projector is not current for the exact BFF SHA")
        return {
            "health_status": observed["healthz"].get("status"),
            "live": observed["livez"].get("live"),
            "ready": observed["readyz"].get("ready"),
            "projector": {
                "checkpoint": projector.get("checkpoint"),
                "source_high_watermark": projector.get("source_high_watermark"),
                "backlog": projector.get("backlog"),
                "deployment_sha": projector.get("deployment_sha"),
                "freshness": freshness,
            },
        }

    def verify_gate_03_agora_product_journey(self) -> dict[str, Any]:
        service = self._load_evidence("service_journey", self.config.service_journey_evidence)
        browser = self._load_evidence("browser", self.config.browser_evidence)
        service_run = self._verify_github_run(
            "service_journey",
            service,
            expected_repository="ajoe734/pantheon",
            expected_head_sha=self.config.expected_bff_sha,
            allowed_workflows=(".github/workflows/agora-hosted-acceptance.yml",),
        )
        browser_run = self._verify_github_run(
            "browser",
            browser,
            expected_repository="ajoe734/execute-plans",
            expected_head_sha=self.config.expected_fe_sha,
            allowed_workflows=(
                ".github/workflows/pantheon-integration-gate.yml",
                ".github/workflows/agora-hosted-acceptance.yml",
            ),
        )
        stages = service.get("stages")
        if not isinstance(stages, list):
            raise AgoraAcceptanceError("gate_03.service_stages", "service journey stages must be a list")
        stage_map = {str(item.get("stage_id")): item for item in stages if isinstance(item, Mapping)}
        if set(stage_map) != set(SERVICE_STAGE_IDS) or any(
            stage_map[stage].get("status") != "passed" for stage in SERVICE_STAGE_IDS
        ):
            raise AgoraAcceptanceError("gate_03.service_stages", "all 14 hosted service stages must pass exactly once")
        if int(service.get("authenticated_request_count") or 0) <= 0:
            raise AgoraAcceptanceError(
                "gate_03.service_requests",
                "service journey recorded no authenticated hosted requests",
            )
        lineage = _mapping(service.get("lineage"), "gate_03.lineage")
        missing_lineage = [key for key in LINEAGE_KEYS if not lineage.get(key)]
        if missing_lineage:
            raise AgoraAcceptanceError("gate_03.lineage", f"service journey is missing lineage: {missing_lineage}")

        viewports = browser.get("viewports")
        if not isinstance(viewports, list):
            raise AgoraAcceptanceError("gate_03.browser_viewports", "browser viewports must be a list")
        viewport_map = {str(item.get("name")): item for item in viewports if isinstance(item, Mapping)}
        if set(viewport_map) != {"desktop", "mobile"}:
            raise AgoraAcceptanceError("gate_03.browser_viewports", "desktop and mobile evidence are both required")
        required_routes = {"/agora/strategy-workshop", "/agora/trading-room"}
        for name, viewport in viewport_map.items():
            routes = set(viewport.get("routes") or [])
            if (
                viewport.get("status") != "passed"
                or viewport.get("authenticated") is not True
                or int(viewport.get("bff_request_count") or 0) <= 0
                or int(viewport.get("unexpected_console_error_count") or 0) != 0
                or not required_routes.issubset(routes)
            ):
                raise AgoraAcceptanceError("gate_03.browser_viewports", f"{name} browser journey is incomplete")
        return {
            "service_run": service_run,
            "browser_run": browser_run,
            "service_stage_count": len(stage_map),
            "lineage": {key: lineage[key] for key in LINEAGE_KEYS},
            "viewports": sorted(viewport_map),
        }

    def verify_gate_04_security_and_boundaries(self) -> dict[str, Any]:
        service = self._load_evidence("service_journey", self.config.service_journey_evidence)
        controls = _mapping(service.get("negative_controls"), "gate_04.negative_controls")
        failed = [key for key in NEGATIVE_CONTROL_KEYS if controls.get(key) not in (True, "passed")]
        auth = _mapping(service.get("authentication"), "gate_04.authentication")
        if failed:
            raise AgoraAcceptanceError("gate_04.negative_controls", f"negative controls did not pass: {failed}")
        if (
            auth.get("mode") != "strict"
            or auth.get("stub") is not False
            or not auth.get("operator_subject")
            or not auth.get("independent_reviewer_subject")
            or auth.get("operator_subject") == auth.get("independent_reviewer_subject")
        ):
            raise AgoraAcceptanceError(
                "gate_04.authentication",
                "strict, independent hosted identities were not proven",
            )
        return {"negative_controls": {key: "passed" for key in NEGATIVE_CONTROL_KEYS}, "authentication": auth}

    def verify_gate_05_restart_persistence_readback(self) -> dict[str, Any]:
        restart = self._load_evidence("restart", self.config.restart_evidence)
        run = self._verify_github_run(
            "restart",
            restart,
            expected_repository="ajoe734/pantheon",
            expected_head_sha=self.config.expected_bff_sha,
            allowed_workflows=(".github/workflows/nonprod-deploy.yml", ".github/workflows/agora-hosted-acceptance.yml"),
        )
        stores = _mapping(restart.get("store_backends"), "gate_05.store_backends")
        before = _mapping(restart.get("before_restart"), "gate_05.before_restart")
        after = _mapping(restart.get("after_restart"), "gate_05.after_restart")
        before_resources = _mapping(before.get("resource_ids"), "gate_05.before_restart.resource_ids")
        after_resources = _mapping(after.get("resource_ids"), "gate_05.after_restart.resource_ids")
        if any(stores.get(key) != "postgres" for key in RESTART_STORE_KEYS):
            raise AgoraAcceptanceError("gate_05.store_backends", "all durable Agora stores must be postgres")
        if (
            restart.get("restart_executed") is not True
            or not before.get("instance_id")
            or not after.get("instance_id")
            or before.get("instance_id") == after.get("instance_id")
            or before_resources != after_resources
            or not before_resources
            or after.get("ready") is not True
            or after.get("deployment_sha") != self.config.expected_bff_sha
            or restart.get("data_loss_detected") is not False
            or restart.get("corruption_detected") is not False
        ):
            raise AgoraAcceptanceError("gate_05.restart", "actual restart and exact durable readback were not proven")
        return {"run": run, "store_backends": stores, "before_restart": before, "after_restart": after}

    def verify_gate_06_rollback_safety(self) -> dict[str, Any]:
        rollback = self._load_evidence("rollback", self.config.rollback_evidence)
        run = self._verify_github_run(
            "rollback",
            rollback,
            expected_repository="ajoe734/execute-plans",
            expected_head_sha=self.config.expected_fe_sha,
            allowed_workflows=(
                ".github/workflows/pantheon-dev-fe-deploy.yml",
                ".github/workflows/pantheon-integration-gate.yml",
                ".github/workflows/agora-hosted-acceptance.yml",
            ),
        )
        checks = _mapping(rollback.get("checks"), "gate_06.checks")
        required = (
            "candidate_pre_switch_passed",
            "atomic_switch_passed",
            "post_switch_exact_pair_passed",
            "failure_injection_executed",
            "failed_candidate_rejected",
            "last_accepted_pair_preserved",
            "rollback_target_verified",
        )
        failed = [key for key in required if checks.get(key) is not True]
        if failed:
            raise AgoraAcceptanceError("gate_06.rollback", f"rollback safety checks did not pass: {failed}")
        prior_pair = _mapping(rollback.get("prior_accepted_pair"), "gate_06.prior_accepted_pair")
        if not SHA40_RE.fullmatch(str(prior_pair.get("backend_sha") or "")) or not SHA40_RE.fullmatch(
            str(prior_pair.get("frontend_sha") or "")
        ):
            raise AgoraAcceptanceError("gate_06.prior_pair", "rollback target is not an exact FE/BFF pair")
        return {"run": run, "checks": checks, "prior_accepted_pair": prior_pair}

    def _build_gap_matrix(self, gate_results: list[GateCheckResult]) -> list[dict[str, Any]]:
        passed = {result.gate_id for result in gate_results if result.status == "PASSED"}
        rows: list[tuple[str, str, str]] = [
            ("S01", "Identity and private Agora scope", "gate_03_agora_product_journey"),
            ("S02", "Workshop creation", "gate_03_agora_product_journey"),
            ("S03", "Strategy reconstruction", "gate_03_agora_product_journey"),
            ("S04", "Typed Workshop cards", "gate_03_agora_product_journey"),
            ("S05", "Immutable StrategySpec selection", "gate_03_agora_product_journey"),
            ("S06", "Governed research", "gate_03_agora_product_journey"),
            ("S07", "Candidate pool", "gate_03_agora_product_journey"),
            ("S08", "Trading Room workspace", "gate_03_agora_product_journey"),
            ("S09", "Candidate actions", "gate_03_agora_product_journey"),
            ("S10", "Decision event and governed intent", "gate_04_security_and_boundaries"),
            ("S11", "Strategy performance suggestions", "gate_03_agora_product_journey"),
            ("S12", "Tenant-safe dataset extraction", "gate_03_agora_product_journey"),
            ("S13", "Policy-learning candidate", "gate_03_agora_product_journey"),
            ("S14", "Independent consultation", "gate_04_security_and_boundaries"),
            ("S15", "Current hosted exact pair", "gate_01_manifest_exact_pair"),
            ("GAP-W01", "Workshop UI creation", "gate_03_agora_product_journey"),
            ("GAP-W02", "Workshop reconstruction composer", "gate_03_agora_product_journey"),
            ("GAP-W03", "Typed Workshop cards in UI", "gate_03_agora_product_journey"),
            ("GAP-W04", "Server-derived completeness", "gate_04_security_and_boundaries"),
            ("HOSTED-READY", "BFF lifecycle readiness", "gate_02_readiness_and_liveness"),
            ("HOSTED-RESTART", "Actual restart persistence", "gate_05_restart_persistence_readback"),
            ("HOSTED-ROLLBACK", "Failure-closed rollback", "gate_06_rollback_safety"),
        ]
        return [
            {
                "gap_id": gap_id,
                "description": description,
                "gate": gate,
                "status": "RESOLVED" if gate in passed else "UNRESOLVED",
                "evidence": "hosted gate passed" if gate in passed else "hosted evidence missing or rejected",
            }
            for gap_id, description, gate in rows
        ]

    def write_evidence_artifacts(self, report: HostedAcceptanceReport) -> None:
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        payload = asdict(report)
        (self.config.evidence_dir / "evidence.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        qualification = {
            "schema_version": "pantheon.agora.hosted-qualification.v2",
            "task_id": report.task_id,
            "qualification_status": report.overall_status,
            "verified_at": report.verified_at,
            "mode": report.mode,
            "exact_pair": report.exact_pair,
            "gates_summary": report.summary,
        }
        (self.config.evidence_dir / "QUALIFICATION.json").write_text(
            json.dumps(qualification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        gate_lines = [
            "# Agora real hosted acceptance",
            "",
            f"- Task: `{report.task_id}`",
            f"- Verified at: `{report.verified_at}`",
            f"- Mode: `{report.mode}`",
            f"- Status: **{report.overall_status}**",
            "",
            "| Gate | Status | Evidence or rejection |",
            "|---|---|---|",
        ]
        for result in report.gate_results:
            note = result.error or "real hosted evidence accepted"
            gate_lines.append(f"| `{result.gate_id}` | **{result.status}** | {note.replace('|', '\\|')} |")
        (self.config.evidence_dir / "VERIFICATION_REPORT.md").write_text(
            "\n".join(gate_lines) + "\n", encoding="utf-8"
        )
        gap_lines = [
            "# Agora hosted gap evidence matrix",
            "",
            f"Status: **{report.overall_status}**",
            "",
            "| Gap | Description | Status | Gate |",
            "|---|---|---|---|",
        ]
        for row in report.gap_matrix:
            gap_lines.append(
                f"| `{row['gap_id']}` | {row['description']} | **{row['status']}** | `{row['gate']}` |"
            )
        (self.config.evidence_dir / "GAP_EVIDENCE_MATRIX.md").write_text(
            "\n".join(gap_lines) + "\n", encoding="utf-8"
        )
        audit_lines = [
            "# Agora hosted deployment audit",
            "",
            f"- Status: **{'ACCEPTED' if report.overall_status == 'PASSED' else 'REJECTED'}**",
            f"- Frontend SHA: `{report.exact_pair['frontend_sha']}`",
            f"- Backend SHA: `{report.exact_pair['backend_sha']}`",
            f"- Pair ID: `{report.exact_pair.get('pair_id')}`",
            "- Acceptance source: live HTTP observations plus exact-head GitHub Actions evidence",
            "- Simulated and in-process results are not accepted.",
        ]
        (self.config.evidence_dir / "DEPLOYMENT_AUDIT.md").write_text(
            "\n".join(audit_lines) + "\n", encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Agora real hosted acceptance aggregator")
    parser.add_argument("--mode", choices=["hosted"], default="hosted")
    parser.add_argument("--bff-url", default=DEFAULT_DEV_BFF_URL)
    parser.add_argument("--fe-url", default=DEFAULT_DEV_FE_URL)
    parser.add_argument("--expected-bff-sha", required=True)
    parser.add_argument("--expected-fe-sha", required=True)
    parser.add_argument("--service-journey-evidence", required=True, type=Path)
    parser.add_argument("--browser-evidence", required=True, type=Path)
    parser.add_argument("--restart-evidence", required=True, type=Path)
    parser.add_argument("--rollback-evidence", required=True, type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--max-evidence-age-seconds", type=int, default=21600)
    parser.add_argument("--request-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    config = AcceptanceConfig(
        expected_bff_sha=args.expected_bff_sha,
        expected_fe_sha=args.expected_fe_sha,
        service_journey_evidence=args.service_journey_evidence,
        browser_evidence=args.browser_evidence,
        restart_evidence=args.restart_evidence,
        rollback_evidence=args.rollback_evidence,
        bff_base_url=args.bff_url.rstrip("/"),
        fe_base_url=args.fe_url.rstrip("/"),
        max_evidence_age_seconds=args.max_evidence_age_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        strict=args.strict,
        mode=args.mode,
        evidence_dir=args.evidence_dir
        or REPO_ROOT / "docs" / "deployment" / "evidence" / "agora" / TASK_ID,
    )
    try:
        report = AgoraHostedAcceptanceVerifier(config).run_full_acceptance()
    except AgoraAcceptanceError as exc:
        logger.error("Agora hosted acceptance configuration failed: %s", exc)
        return 2
    if report.overall_status == "PASSED":
        logger.info("Agora real hosted exact pair accepted")
        return 0
    logger.error("Agora real hosted exact pair rejected")
    return 1


if __name__ == "__main__":
    sys.exit(main())
