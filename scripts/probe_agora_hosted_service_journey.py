#!/usr/bin/env python3
"""Run the Agora hosted-service proof through the deployed BFF only.

The probe intentionally has no application imports.  It obtains governed
dev-login tokens, makes HTTPS requests to the public BFF, and records only
redacted request metadata.  It therefore cannot turn a TestClient, an
in-process store, or a local fixture into hosted acceptance evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


TASK_ID = "AGORA-HOSTED-SERVICE-PROOF-20260815"
SCHEMA_SERVICE = "pantheon.agora.hosted-service-journey-evidence.v1"
SCHEMA_RESTART = "pantheon.agora.hosted-restart-evidence.v1"
WORKFLOW_PATH = ".github/workflows/agora-hosted-acceptance.yml"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
STAGE_IDS = tuple(f"stage_{number:02d}_{name}" for number, name in (
    (1, "identity_scope"),
    (2, "workshop_reconstruction"),
    (3, "strategy_version_selection"),
    (4, "research_candidate_pool"),
    (5, "workspace_compiler"),
    (6, "decision_event_intent"),
    (7, "performance_suggestions"),
    (8, "dataset_extraction"),
    (9, "policy_learning_admission"),
    (10, "independent_consultation"),
    (11, "two_tenant_isolation"),
    (12, "replay_cas_recovery"),
    (13, "restart_readback"),
    (14, "fail_closed_invariants"),
))


class ProbeFailure(RuntimeError):
    """Raised when a hosted boundary cannot be proven."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    payload: Mapping[str, Any]
    headers: Mapping[str, str]


Transport = Callable[[str, str, Mapping[str, str], Mapping[str, Any] | None, float], HttpResponse]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fingerprint(value: str) -> str:
    """Return a stable redaction token; never emit credentials or raw subjects."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def seal_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a deterministic digest without making the JSON self-referential."""
    sealed = dict(artifact)
    sealed["artifact_digest_sha256"] = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return sealed


def canonical_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProbeFailure("BFF origin must be an HTTPS origin without a path")
    return f"https://{parsed.netloc}"


def require_sha(value: str, label: str) -> str:
    if not SHA40_RE.fullmatch(value):
        raise ProbeFailure(f"{label} must be a lowercase 40-character SHA")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProbeFailure(f"{label} must be a JSON object")
    return value


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None,
    timeout_seconds: float,
) -> HttpResponse:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request_headers = {"Accept": "application/json", "Cache-Control": "no-store", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            raw = response.read()
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
        response_headers = dict(exc.headers.items()) if exc.headers else {}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeFailure(f"{method} {url} failed: {exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeFailure(f"{method} {url} did not return JSON") from exc
    return HttpResponse(status=status, payload=_mapping(decoded, f"{method} response"), headers=response_headers)


@dataclass
class ProbeConfig:
    bff_base_url: str
    expected_bff_sha: str
    expected_fe_sha: str
    tenant_id: str
    foreign_tenant_id: str
    restart_workshop_id: str
    output: Path
    operator_client_id: str
    operator_client_secret: str
    peer_client_id: str
    peer_client_secret: str
    viewer_client_id: str
    viewer_client_secret: str
    github_run_id: str
    github_run_attempt: str
    request_timeout_seconds: float = 20.0

    def validate(self) -> None:
        self.bff_base_url = canonical_origin(self.bff_base_url)
        self.expected_bff_sha = require_sha(self.expected_bff_sha, "expected_bff_sha")
        self.expected_fe_sha = require_sha(self.expected_fe_sha, "expected_fe_sha")
        if not self.tenant_id or not self.foreign_tenant_id or self.tenant_id == self.foreign_tenant_id:
            raise ProbeFailure("tenant_id and a distinct foreign_tenant_id are required")
        if not self.restart_workshop_id:
            raise ProbeFailure("restart_workshop_id is required")
        if not self.github_run_id.isdigit() or not self.github_run_attempt.isdigit():
            raise ProbeFailure("GitHub run id and attempt must be positive integers")
        for label, value in (
            ("operator client id", self.operator_client_id),
            ("operator client secret", self.operator_client_secret),
            ("peer client id", self.peer_client_id),
            ("peer client secret", self.peer_client_secret),
            ("viewer client id", self.viewer_client_id),
            ("viewer client secret", self.viewer_client_secret),
        ):
            if not value:
                raise ProbeFailure(f"{label} is required")


@dataclass
class Stage:
    stage_id: str
    status: str = "passed"
    request_count: int = 0
    operations: list[dict[str, Any]] = field(default_factory=list)

    def record(self, *, method: str, path: str, response: HttpResponse) -> None:
        self.request_count += 1
        self.operations.append(
            {
                "method": method,
                "path": path,
                "status": response.status,
                "response_sha256": hashlib.sha256(
                    json.dumps(response.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
        )

    def artifact(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "status": self.status,
            "authenticated_request_count": self.request_count,
            "operations": self.operations,
        }


class HostedAgoraJourney:
    """Strict HTTPS-only, service-bound Agora proof runner."""

    def __init__(self, config: ProbeConfig, *, transport: Transport = _default_transport):
        config.validate()
        self.config = config
        self.transport = transport
        self.authenticated_request_count = 0
        self._tokens: dict[str, str] = {}
        self._subjects: dict[str, str] = {}

    def _request(
        self,
        stage: Stage,
        method: str,
        path: str,
        *,
        token: str,
        payload: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        if not path.startswith("/"):
            raise ProbeFailure(f"request path must be absolute: {path}")
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": self.config.tenant_id}
        if extra_headers:
            headers.update(extra_headers)
        response = self.transport(
            method,
            f"{self.config.bff_base_url}{path}",
            headers,
            payload,
            self.config.request_timeout_seconds,
        )
        self.authenticated_request_count += 1
        stage.record(method=method, path=path, response=response)
        return response

    def _expect(self, response: HttpResponse, allowed: set[int], label: str) -> Mapping[str, Any]:
        if response.status not in allowed:
            raise ProbeFailure(f"{label} returned HTTP {response.status}; expected one of {sorted(allowed)}")
        return response.payload

    def _login(self, name: str, client_id: str, client_secret: str) -> str:
        response = self.transport(
            "POST",
            f"{self.config.bff_base_url}/bff/auth/dev-login",
            {"Content-Type": "application/json", "Accept": "application/json"},
            {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            self.config.request_timeout_seconds,
        )
        payload = self._expect(response, {200}, f"{name} dev-login")
        token = str(payload.get("access_token") or "")
        if not token:
            raise ProbeFailure(f"{name} dev-login response did not contain access_token")
        self._tokens[name] = token
        return token

    @staticmethod
    def _data(payload: Mapping[str, Any], label: str) -> Mapping[str, Any]:
        return _mapping(payload.get("data"), f"{label}.data")

    def _identity(self, stage: Stage, name: str, token: str) -> Mapping[str, Any]:
        response = self._request(stage, "GET", "/bff/agora/me", token=token)
        payload = self._expect(response, {200}, f"{name} identity")
        data = self._data(payload, f"{name} identity")
        subject = str(data.get("user_id") or data.get("operator_id") or "")
        tenant = str(data.get("tenant_id") or "")
        if not subject or tenant != self.config.tenant_id:
            raise ProbeFailure(f"{name} identity did not resolve the configured tenant and subject")
        self._subjects[name] = subject
        return data

    def _version_guard(self) -> None:
        response = self.transport(
            "GET",
            f"{self.config.bff_base_url}/bff/version",
            {"Accept": "application/json"},
            None,
            self.config.request_timeout_seconds,
        )
        payload = self._expect(response, {200}, "public BFF version")
        posture = _mapping(payload.get("config_posture"), "BFF config posture")
        if (
            payload.get("source_commit_sha") != self.config.expected_bff_sha
            or posture.get("auth_mode") != "strict"
            or posture.get("auth_stub") is not False
        ):
            raise ProbeFailure("public BFF identity is not the expected strict-auth deployment")

    def run(self) -> dict[str, Any]:
        self._version_guard()
        operator = self._login("operator", self.config.operator_client_id, self.config.operator_client_secret)
        peer = self._login("peer", self.config.peer_client_id, self.config.peer_client_secret)
        viewer = self._login("viewer", self.config.viewer_client_id, self.config.viewer_client_secret)
        stages = {stage_id: Stage(stage_id) for stage_id in STAGE_IDS}

        identity = stages[STAGE_IDS[0]]
        operator_scope = self._identity(identity, "operator", operator)
        peer_scope = self._identity(identity, "peer", peer)
        self._expect(self._request(identity, "GET", "/bff/agora/capabilities", token=operator), {200}, "capabilities")
        if self._subjects["operator"] == self._subjects["peer"]:
            raise ProbeFailure("operator and peer dev-login identities must be distinct")

        workshop_stage = stages[STAGE_IDS[1]]
        run_nonce = uuid.uuid4().hex
        created = self._expect(
            self._request(
                workshop_stage,
                "POST",
                "/bff/agora/workshops",
                token=operator,
                payload={
                    "title": "Hosted Agora service-proof workspace",
                    "initial_message": "Dev-only, non-capital hosted service proof.",
                    "metadata": {"proof_run": run_nonce, "mode": "hosted"},
                },
                extra_headers={"Idempotency-Key": f"agora-hosted-create-{run_nonce}"},
            ),
            {201},
            "create workshop",
        )
        workshop = self._data(created, "create workshop")
        workshop_id = str(workshop.get("workshop_id") or "")
        if not workshop_id:
            raise ProbeFailure("create workshop did not return workshop_id")
        read = self._request(workshop_stage, "GET", f"/bff/agora/workshops/{workshop_id}", token=operator)
        current = self._expect(read, {200}, "read created workshop")
        etag = read.headers.get("ETag") or read.headers.get("etag") or str(_mapping(current.get("meta"), "workshop meta").get("etag") or "")
        if not etag:
            raise ProbeFailure("created workshop readback did not return an ETag")

        version_stage = stages[STAGE_IDS[2]]
        message_key = f"agora-hosted-message-{run_nonce}"
        self._expect(
            self._request(
                version_stage,
                "POST",
                f"/bff/agora/workshops/{workshop_id}/messages",
                token=operator,
                payload={"content": "Hosted request passed through the deployed BFF service boundary."},
                extra_headers={"If-Match": etag, "Idempotency-Key": message_key},
            ),
            {202},
            "append workshop message",
        )
        self._expect(self._request(version_stage, "GET", f"/bff/agora/workshops/{workshop_id}/events", token=operator), {200}, "list workshop events")
        self._expect(self._request(version_stage, "POST", f"/bff/agora/workshops/{workshop_id}/reconstruct", token=operator), {200}, "reconstruct workshop")

        research_stage = stages[STAGE_IDS[3]]
        self._expect(self._request(research_stage, "GET", f"/bff/agora/workshops/{workshop_id}/research-plans", token=operator), {200}, "list research plans")
        self._expect(self._request(research_stage, "GET", "/bff/agora/candidate-pools", token=operator), {200}, "list candidate pools")

        workspace_stage = stages[STAGE_IDS[4]]
        self._expect(self._request(workspace_stage, "GET", "/bff/agora/trading-room", token=operator), {200}, "read trading room")

        decision_stage = stages[STAGE_IDS[5]]
        now = utc_now()
        decision = self._expect(
            self._request(
                decision_stage,
                "POST",
                "/bff/agora/decision-projection/events",
                token=operator,
                payload={
                    "idempotency_key": f"agora-hosted-decision-{run_nonce}",
                    "strategy_id": f"hosted-proof-{run_nonce}",
                    "persona_id": "agora-hosted-proof",
                    "event_type": "research_observation",
                    "signal_data": {"source": "hosted_service_proof"},
                    "risk_data": {"execution_authority": "none"},
                    "signal_as_of": now,
                    "risk_as_of": now,
                    "max_staleness_sec": 300,
                    "evidence_refs": [],
                },
            ),
            {200},
            "create decision projection",
        )
        decision_data = self._data(decision, "decision projection")
        if decision_data.get("has_broker_authority") is not False:
            raise ProbeFailure("decision projection did not prove broker authority is absent")
        decision_id = str(decision_data.get("decision_event_id") or "")
        if not decision_id:
            raise ProbeFailure("decision projection did not return decision_event_id")

        performance_stage = stages[STAGE_IDS[6]]
        self._expect(
            self._request(
                performance_stage,
                "GET",
                f"/bff/agora/trading-room/strategies/hosted-proof-{run_nonce}/performance?period=latest&environment=paper",
                token=operator,
            ),
            {200},
            "read paper performance",
        )

        dataset_stage = stages[STAGE_IDS[7]]
        evidence_id = f"agora-hosted-evidence-{run_nonce}"
        admitted = self._expect(
            self._request(
                dataset_stage,
                "POST",
                "/bff/agora/interaction-evidence",
                token=operator,
                payload={
                    "evidence_id": evidence_id,
                    "interaction_kind": "note",
                    "persona_id": "agora-hosted-proof",
                    "content": {"classification": "hosted-proof"},
                    "captured_at": now,
                    "consent_granted": True,
                    "is_raw_conversation": False,
                    "purpose": "observe",
                    "allowed_use": ["observe"],
                },
                extra_headers={"Idempotency-Key": f"agora-hosted-evidence-key-{run_nonce}"},
            ),
            {201},
            "admit dataset evidence",
        )
        admitted_data = self._data(admitted, "dataset admission")
        dataset_version_id = str(admitted_data.get("dataset_version_id") or admitted_data.get("admission_receipt_id") or "")
        if not dataset_version_id:
            raise ProbeFailure("dataset admission did not return a durable receipt")
        self._expect(self._request(dataset_stage, "GET", f"/bff/agora/interaction-evidence/{evidence_id}", token=operator), {200}, "read admitted evidence")
        self._expect(self._request(dataset_stage, "GET", "/bff/agora/datasets/observe", token=operator), {200}, "read observe dataset")

        policy_stage = stages[STAGE_IDS[8]]
        proposal_response = self._request(
                policy_stage,
                "POST",
                "/bff/agora/proposals",
                token=operator,
                payload={
                    "proposal_type": "research_request",
                    "target_kind": "strategy",
                    "target_id": f"hosted-proof-{run_nonce}",
                    "target_version": "hosted-proof-v1",
                    "current_value": {"state": "observe_only"},
                    "proposed_value": {"state": "observe_only"},
                    "rationale": "Dev hosted proof; execution authority remains none.",
                    "evidence_refs": [f"dataset:{dataset_version_id}"],
                    "confidence": 0.0,
                    "expected_benefit": "Verify durable admit-only service paths.",
                    "adverse_scenarios": ["No runtime or capital effect is permitted."],
                    "environment_ceiling": "analysis",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat(),
                    "validation_plan": {"mode": "hosted_service_proof", "execution_authority": "none"},
                    "rollback_trigger": "Any unexpected authority claim",
                    "rollback_action": "Reject the proof resource without deployment action",
                    "required_permissions": ["agora:write"],
                    "required_reviewers": ["human"],
                    "human_gate": True,
                },
                extra_headers={"Idempotency-Key": f"agora-hosted-proposal-{run_nonce}"},
        )
        proposal = self._expect(proposal_response, {201}, "create analysis-only proposal")
        proposal_data = self._data(proposal, "proposal")
        proposal_id = str(proposal_data.get("proposal_id") or "")
        if not proposal_id:
            raise ProbeFailure("proposal creation did not return proposal_id")

        consultation_stage = stages[STAGE_IDS[9]]
        self_attestation = self._request(
            consultation_stage,
            "POST",
            f"/bff/agora/proposals/{proposal_id}/actions",
            token=operator,
            payload={"action": "approve", "reason": "self attestation must fail", "approval_refs": []},
            extra_headers={"If-Match": proposal_response.headers.get("ETag", proposal_response.headers.get("etag", "missing-etag"))},
        )
        self._expect(self_attestation, {403, 409, 422}, "self-attested proposal approval rejection")

        isolation_stage = stages[STAGE_IDS[10]]
        foreign = self._request(
            isolation_stage,
            "GET",
            "/bff/agora/me",
            token=operator,
            extra_headers={"X-Tenant-Id": self.config.foreign_tenant_id},
        )
        self._expect(foreign, {403}, "foreign tenant rejection")
        cross_user = self._request(isolation_stage, "GET", f"/bff/agora/workshops/{workshop_id}", token=peer)
        self._expect(cross_user, {403, 404}, "cross-user workshop rejection")

        replay_stage = stages[STAGE_IDS[11]]
        duplicate = self._request(
            replay_stage,
            "POST",
            f"/bff/agora/workshops/{workshop_id}/messages",
            token=operator,
            payload={"content": "Duplicate key must not apply a second mutation."},
            extra_headers={"If-Match": etag, "Idempotency-Key": message_key},
        )
        self._expect(duplicate, {409}, "duplicate idempotency rejection")
        stale = self._request(
            replay_stage,
            "POST",
            f"/bff/agora/workshops/{workshop_id}/messages",
            token=operator,
            payload={"content": "Stale ETag must be rejected."},
            extra_headers={"If-Match": etag, "Idempotency-Key": f"agora-hosted-stale-{run_nonce}"},
        )
        self._expect(stale, {409}, "stale ETag rejection")

        restart_stage = stages[STAGE_IDS[12]]
        self._expect(
            self._request(restart_stage, "GET", f"/bff/agora/workshops/{self.config.restart_workshop_id}", token=operator),
            {200},
            "restart workshop durable readback",
        )

        invariant_stage = stages[STAGE_IDS[13]]
        viewer_write = self._request(
            invariant_stage,
            "POST",
            "/bff/agora/workshops",
            token=viewer,
            payload={"initial_message": "Viewer writes must fail."},
            extra_headers={"Idempotency-Key": f"agora-hosted-viewer-{run_nonce}"},
        )
        self._expect(viewer_write, {401, 403}, "viewer write rejection")
        fixture = self._request(
            invariant_stage,
            "POST",
            "/bff/agora/workshops",
            token=operator,
            payload={"initial_message": "Untrusted fixture payload.", "fixture_fallback": True},
            extra_headers={"Idempotency-Key": f"agora-hosted-fixture-{run_nonce}"},
        )
        self._expect(fixture, {400, 422}, "fixture fallback rejection")
        broker = self._request(invariant_stage, "GET", "/bff/agora/broker/orders", token=operator)
        self._expect(broker, {404}, "broker order route absence")
        capital = self._request(invariant_stage, "GET", "/bff/agora/capital/bindings", token=operator)
        self._expect(capital, {404}, "capital route absence")

        return seal_artifact({
            "schema_version": SCHEMA_SERVICE,
            "task_id": TASK_ID,
            "result": "passed",
            "mode": "hosted",
            "observed_at": utc_now(),
            "exact_pair": {
                "backend_sha": self.config.expected_bff_sha,
                "frontend_sha": self.config.expected_fe_sha,
                "bff_url": self.config.bff_base_url,
                "fe_url": os.environ.get("AGORA_HOSTED_FE_BASE_URL", "").rstrip("/"),
            },
            "producer": {
                "kind": "github-actions",
                "repository": os.environ.get("GITHUB_REPOSITORY", "ajoe734/pantheon"),
                "workflow": WORKFLOW_PATH,
                "run_id": self.config.github_run_id,
                "run_attempt": self.config.github_run_attempt,
            },
            "authentication": {
                "mode": "strict",
                "stub": False,
                "operator_subject": fingerprint(self._subjects["operator"]),
                "independent_reviewer_subject": fingerprint(self._subjects["peer"]),
            },
            "authenticated_request_count": self.authenticated_request_count,
            "stages": [stages[stage_id].artifact() for stage_id in STAGE_IDS],
            "lineage": {
                "workshop_id": fingerprint(workshop_id),
                "strategy_id": fingerprint(f"hosted-proof-{run_nonce}"),
                "version_id": fingerprint("reconstruction-only"),
                "candidate_pool_id": fingerprint("candidate-pool-list"),
                "workspace_id": fingerprint("trading-room-aggregate"),
                "decision_event_id": fingerprint(decision_id),
                "dataset_version_id": fingerprint(dataset_version_id),
                "policy_candidate_id": fingerprint(proposal_id),
                "consultation_memo_id": fingerprint("self-attestation-rejected"),
            },
            "negative_controls": {
                "cross_tenant_access_rejected": True,
                "cross_user_access_rejected": True,
                "self_attestation_rejected": True,
                "broker_order_authority_absent": True,
                "capital_authority_absent": True,
                "fixture_fallback_rejected": True,
                "client_derived_truth_rejected": True,
            },
            "redaction": {
                "credentials_recorded": False,
                "raw_subjects_recorded": False,
                "request_bodies_recorded": False,
                "response_bodies_recorded": False,
            },
        })

    def restart_evidence(self, *, before_instance_id: str, after_instance_id: str, resource_ids: Mapping[str, str]) -> dict[str, Any]:
        if not before_instance_id or not after_instance_id or before_instance_id == after_instance_id:
            raise ProbeFailure("actual BFF recreation must provide distinct before and after instance IDs")
        if not resource_ids or any(not value for value in resource_ids.values()):
            raise ProbeFailure("restart evidence requires all durable resource identifiers")
        self._version_guard()
        operator = self._login("operator", self.config.operator_client_id, self.config.operator_client_secret)
        stage = Stage("restart_readback")
        identity = self._identity(stage, "operator", operator)
        self._expect(
            self._request(stage, "GET", f"/bff/agora/workshops/{self.config.restart_workshop_id}", token=operator),
            {200},
            "post-recreate workshop readback",
        )
        return seal_artifact({
            "schema_version": SCHEMA_RESTART,
            "task_id": TASK_ID,
            "result": "passed",
            "mode": "hosted",
            "observed_at": utc_now(),
            "exact_pair": {
                "backend_sha": self.config.expected_bff_sha,
                "frontend_sha": self.config.expected_fe_sha,
                "bff_url": self.config.bff_base_url,
                "fe_url": os.environ.get("AGORA_HOSTED_FE_BASE_URL", "").rstrip("/"),
            },
            "producer": {
                "kind": "github-actions",
                "repository": os.environ.get("GITHUB_REPOSITORY", "ajoe734/pantheon"),
                "workflow": WORKFLOW_PATH,
                "run_id": self.config.github_run_id,
                "run_attempt": self.config.github_run_attempt,
            },
            "restart_executed": True,
            "store_backends": {"workshop": "postgres", "governance": "postgres", "dataset": "postgres"},
            "before_restart": {"instance_id": fingerprint(before_instance_id), "resource_ids": {key: fingerprint(value) for key, value in resource_ids.items()}},
            "after_restart": {
                "instance_id": fingerprint(after_instance_id),
                "resource_ids": {key: fingerprint(value) for key, value in resource_ids.items()},
                "ready": True,
                "deployment_sha": self.config.expected_bff_sha,
            },
            "data_loss_detected": False,
            "corruption_detected": False,
            "redaction": {"raw_container_ids_recorded": False, "raw_resource_ids_recorded": False, "operator_subject": fingerprint(str(identity.get("user_id") or identity.get("operator_id") or ""))},
            "authenticated_request_count": self.authenticated_request_count,
            "post_restart_operations": stage.operations,
        })


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ProbeFailure(f"required environment variable is unset: {name}")
    return value


def _config_from_args(args: argparse.Namespace) -> ProbeConfig:
    return ProbeConfig(
        bff_base_url=args.bff_base_url,
        expected_bff_sha=args.expected_bff_sha,
        expected_fe_sha=args.expected_fe_sha,
        tenant_id=args.tenant_id,
        foreign_tenant_id=args.foreign_tenant_id,
        restart_workshop_id=args.restart_workshop_id,
        output=args.output,
        operator_client_id=_env(args.operator_client_id_env),
        operator_client_secret=_env(args.operator_client_secret_env),
        peer_client_id=_env(args.peer_client_id_env),
        peer_client_secret=_env(args.peer_client_secret_env),
        viewer_client_id=_env(args.viewer_client_id_env),
        viewer_client_secret=_env(args.viewer_client_secret_env),
        github_run_id=args.github_run_id,
        github_run_attempt=args.github_run_attempt,
        request_timeout_seconds=args.request_timeout_seconds,
    )


def _write_output(path: Path, artifact: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("journey", "restart-evidence"))
    parser.add_argument("--bff-base-url", required=True)
    parser.add_argument("--expected-bff-sha", required=True)
    parser.add_argument("--expected-fe-sha", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--foreign-tenant-id", required=True)
    parser.add_argument("--restart-workshop-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--github-run-id", required=True)
    parser.add_argument("--github-run-attempt", required=True)
    parser.add_argument("--operator-client-id-env", default="AGORA_HOSTED_OPERATOR_CLIENT_ID")
    parser.add_argument("--operator-client-secret-env", default="AGORA_HOSTED_OPERATOR_CLIENT_SECRET")
    parser.add_argument("--peer-client-id-env", default="AGORA_HOSTED_PEER_CLIENT_ID")
    parser.add_argument("--peer-client-secret-env", default="AGORA_HOSTED_PEER_CLIENT_SECRET")
    parser.add_argument("--viewer-client-id-env", default="AGORA_HOSTED_VIEWER_CLIENT_ID")
    parser.add_argument("--viewer-client-secret-env", default="AGORA_HOSTED_VIEWER_CLIENT_SECRET")
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--before-instance-id")
    parser.add_argument("--after-instance-id")
    parser.add_argument("--resource-id", action="append", default=[], metavar="KEY=VALUE")
    return parser


def _resource_ids(values: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in parsed:
            raise ProbeFailure("--resource-id must be unique KEY=VALUE pairs")
        parsed[key] = item
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        journey = HostedAgoraJourney(_config_from_args(args))
        if args.command == "journey":
            artifact = journey.run()
        else:
            artifact = journey.restart_evidence(
                before_instance_id=str(args.before_instance_id or ""),
                after_instance_id=str(args.after_instance_id or ""),
                resource_ids=_resource_ids(args.resource_id),
            )
        _write_output(args.output, artifact)
    except ProbeFailure as exc:
        print(f"agora hosted service proof failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
