"""Contract tests for the HTTP-only Agora hosted proof probe."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts.probe_agora_hosted_service_journey import (
    HttpResponse,
    HostedAgoraJourney,
    ProbeConfig,
    ProbeFailure,
    SCHEMA_RESTART,
    SCHEMA_SERVICE,
    STAGE_IDS,
    canonical_origin,
    fingerprint,
)


SHA = "a" * 40


def _config() -> ProbeConfig:
    return ProbeConfig(
        bff_base_url="https://bff.example.test",
        fe_base_url="https://fe.example.test",
        expected_bff_sha=SHA,
        expected_fe_sha="b" * 40,
        tenant_id="tenant-dev",
        foreign_tenant_id="tenant-forbidden",
        restart_workshop_id="restart-workshop-1",
        output=Path("unused.json"),
        operator_client_id="operator-client",
        operator_client_secret="operator-secret",
        peer_client_id="peer-client",
        peer_client_secret="peer-secret",
        viewer_client_id="viewer-client",
        viewer_client_secret="viewer-secret",
        github_run_id="123",
        github_run_attempt="1",
    )


class HostedBffDouble:
    """A transport double; it never imports or executes product application code."""

    def __init__(self) -> None:
        self.message_writes = 0

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
        _timeout: float,
    ) -> HttpResponse:
        if url == "https://fe.example.test/deployment.json":
            return HttpResponse(200, {"commit": "b" * 40, "bffCommit": SHA, "bffHost": "https://bff.example.test"}, {})
        path = url.removeprefix("https://bff.example.test")
        if path == "/bff/auth/dev-login":
            client_id = str((payload or {}).get("client_id"))
            return HttpResponse(200, {"access_token": client_id.replace("-client", "-token")}, {})
        if path == "/bff/version":
            return HttpResponse(200, {"source_commit_sha": SHA, "config_posture": {"auth_mode": "strict", "auth_stub": False}}, {})

        token = headers.get("Authorization", "").removeprefix("Bearer ")
        if path == "/bff/agora/me":
            if headers.get("X-Tenant-Id") == "tenant-forbidden":
                return HttpResponse(403, {"error": "foreign tenant"}, {})
            subjects = {"operator-token": "operator-a", "peer-token": "operator-b", "viewer-token": "viewer-c"}
            return HttpResponse(200, {"data": {"user_id": subjects[token], "tenant_id": "tenant-dev"}}, {})
        if path == "/bff/agora/capabilities":
            return HttpResponse(200, {"data": {"capabilities": ["agora:read"]}}, {})
        if path in {"/bff/agora/broker/orders", "/bff/agora/capital/bindings"}:
            return HttpResponse(404, {"error": "absent"}, {})
        if path.startswith("/bff/agora/workshops/") and method == "GET":
            if token == "peer-token":
                return HttpResponse(403, {"error": "cross user"}, {})
            return HttpResponse(200, {"data": {"workshop_id": "workshop-1"}, "meta": {"etag": 'W/\"workshop-1:v1\"'}}, {"ETag": 'W/"workshop-1:v1"'})
        if path == "/bff/agora/workshops" and method == "POST":
            if token == "viewer-token":
                return HttpResponse(403, {"error": "write denied"}, {})
            if (payload or {}).get("fixture_fallback"):
                return HttpResponse(422, {"error": "extra field"}, {})
            return HttpResponse(201, {"data": {"workshop_id": "workshop-1"}}, {})
        if path.endswith("/messages") and method == "POST":
            self.message_writes += 1
            return HttpResponse(202 if self.message_writes == 1 else 409, {"data": {}}, {})
        if path.endswith("/reconstruct") and method == "POST":
            return HttpResponse(200, {"data": {"reconstruction_id": "recon-1"}}, {})
        if path == "/bff/agora/decision-projection/events" and method == "POST":
            return HttpResponse(200, {"data": {"decision_event_id": "decision-1", "has_broker_authority": False}}, {})
        if path == "/bff/agora/interaction-evidence" and method == "POST":
            return HttpResponse(201, {"data": {"admission_receipt_id": "receipt-1"}}, {})
        if path == "/bff/agora/proposals" and method == "POST":
            return HttpResponse(201, {"data": {"proposal_id": "proposal-1"}}, {"ETag": '"proposal-1"'})
        if path.endswith("/actions") and method == "POST":
            return HttpResponse(422, {"error": "approval requires independent authority"}, {})
        return HttpResponse(200, {"data": {}, "items": [], "meta": {}}, {})


def test_journey_is_service_shaped_and_redacts_runtime_values() -> None:
    artifact = HostedAgoraJourney(_config(), transport=HostedBffDouble()).run()

    assert artifact["schema_version"] == SCHEMA_SERVICE
    assert artifact["mode"] == "hosted"
    assert artifact["result"] == "passed"
    assert [stage["stage_id"] for stage in artifact["stages"]] == list(STAGE_IDS)
    assert all(stage["status"] == "passed" and stage["authenticated_request_count"] > 0 for stage in artifact["stages"])
    assert artifact["authenticated_request_count"] > 20
    assert artifact["authentication"]["operator_subject"] == fingerprint("operator-a")
    assert "operator-a" not in str(artifact)
    assert len(artifact["artifact_digest_sha256"]) == 64
    assert all(artifact["negative_controls"].values())


def test_restart_evidence_requires_a_recreated_instance_and_reads_through_bff() -> None:
    artifact = HostedAgoraJourney(_config(), transport=HostedBffDouble()).restart_evidence(
        before_instance_id="container-before",
        after_instance_id="container-after",
        resource_ids={"workshop_id": "restart-workshop-1", "proposal_id": "proposal-restart", "evidence_id": "evidence-restart"},
    )

    assert artifact["schema_version"] == SCHEMA_RESTART
    assert artifact["before_restart"]["instance_id"] != artifact["after_restart"]["instance_id"]
    assert "container-before" not in str(artifact)
    assert artifact["after_restart"]["ready"] is True
    assert artifact["authenticated_request_count"] >= 1


def test_operator_identity_is_workflow_local_and_not_an_uploaded_artifact() -> None:
    identity = HostedAgoraJourney(_config(), transport=HostedBffDouble()).operator_identity()

    assert identity == {"operator_user_id": "operator-a", "tenant_id": "tenant-dev"}


@pytest.mark.parametrize("value", ["http://bff.example.test", "https://bff.example.test/path", "https:///missing-host"])
def test_origin_rejects_any_non_origin_or_non_https_value(value: str) -> None:
    with pytest.raises(ProbeFailure):
        canonical_origin(value)


def test_restart_evidence_fails_closed_when_the_instance_id_did_not_change() -> None:
    with pytest.raises(ProbeFailure, match="distinct before and after"):
        HostedAgoraJourney(_config(), transport=HostedBffDouble()).restart_evidence(
            before_instance_id="same",
            after_instance_id="same",
            resource_ids={"workshop_id": "restart-workshop-1"},
        )


def test_probe_has_no_product_application_import() -> None:
    source = Path(__file__).with_name("probe_agora_hosted_service_journey.py").read_text(encoding="utf-8")
    assert "from services" not in source
    assert "import fastapi" not in source
    assert "TestClient(" not in source


def test_hosted_workflow_requires_exact_head_lease_and_recreated_instance() -> None:
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "agora-hosted-acceptance.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "environment: dev" in text
    assert '"${GITHUB_REF}" == "refs/heads/dev"' in text
    assert '"$(git rev-parse refs/remotes/origin/dev)" == "${EXPECTED_BFF_SHA}"' in text
    assert "run_with_dev_environment_lease.sh" in text
    assert "google-github-actions/auth@" in text
    assert "docker compose -p pantheon -f docker-compose.yml up -d --no-deps --force-recreate operator-bff" in text
    assert "docker compose -p pantheon -f docker-compose.yml restart operator-bff" not in text
    assert "actions/upload-artifact@v4" in text
