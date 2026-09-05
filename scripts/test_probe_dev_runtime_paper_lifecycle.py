"""Unit and contract tests for fresh-stimulus RuntimeBinding and paper lifecycle probe.

Task: DEV-LOOP8-9-PROBE-20260905

Tests prove:
1. Stale identity rejection (FE and BFF SHA mismatch).
2. Wrong correlation rejection (pairId mismatch and correlation_id envelope mismatch).
3. Missing consumer receipt failure (Loop 8 fleet desired state and Loop 9 heartbeat receipt).
4. Timeout waiting for terminal state and fresh-client reload mismatch.
5. Fail-closed safety on real-capital, real-orders, or live writes.
6. Rejection of retired deploy targets and legacy IPs.
7. Separate reporting of unavailable and blocked states.
8. Complete redaction of bearer tokens and credentials.
9. Full successful execution of Loops 8 and 9 using an honest test double.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
import urllib.parse
import pytest

from scripts.probe_dev_runtime_paper_lifecycle import (
    CorrelationMismatchError,
    DevRuntimePaperLifecycleProbe,
    HISTORICAL_FORBIDDEN_IDS,
    HttpResponse,
    MissingConsumerReceiptError,
    PARENT_STRATEGY_ARTIFACT_ID,
    ProbeConfig,
    ProbeError,
    ProbeTimeoutError,
    RealCapitalOrOrderWriteForbiddenError,
    ReloadMismatchError,
    RetiredDeployTargetError,
    SCHEMA_VERSION,
    StaleIdentityError,
    TASK_ID,
    is_executable_binding,
    redact_secrets,
    seal_evidence,
    validate_host_not_retired,
)

FE_SHA_VALID = "a" * 40
BFF_SHA_VALID = "b" * 40
PAIR_ID_VALID = "pair-valid-12345"


class HonestCanonicalOwnerDouble:
    """Honest test double simulating canonical owner APIs for local unit testing.

    Never contacts external networks or imports product application code.
    """

    def __init__(
        self,
        *,
        fe_commit: str = FE_SHA_VALID,
        bff_commit: str = BFF_SHA_VALID,
        pair_id: str = PAIR_ID_VALID,
        fe_real_writes: str = "false",
        bff_ready: bool = True,
        deployment_terminal_status: str = "executed",
        fleet_desired_accepted: bool = True,
        fleet_worker_running: bool = True,
        telemetry_fill_produced: bool = True,
        telemetry_heartbeat_present: bool = True,
        telemetry_real_capital: bool = False,
        telemetry_real_order: bool = False,
        telemetry_correlation_override: str | None = None,
        reload_binding_differs: bool = False,
        reload_event_differs: bool = False,
        reload_summary_differs: bool = False,
        simulate_timeout_on: set[str] | None = None,
        simulate_unavailable_on: set[str] | None = None,
    ) -> None:
        self.fe_commit = fe_commit
        self.bff_commit = bff_commit
        self.pair_id = pair_id
        self.fe_real_writes = fe_real_writes
        self.bff_ready = bff_ready
        self.deployment_terminal_status = deployment_terminal_status
        self.fleet_desired_accepted = fleet_desired_accepted
        self.fleet_worker_running = fleet_worker_running
        self.telemetry_fill_produced = telemetry_fill_produced
        self.telemetry_heartbeat_present = telemetry_heartbeat_present
        self.telemetry_real_capital = telemetry_real_capital
        self.telemetry_real_order = telemetry_real_order
        self.telemetry_correlation_override = telemetry_correlation_override
        self.reload_binding_differs = reload_binding_differs
        self.reload_event_differs = reload_event_differs
        self.reload_summary_differs = reload_summary_differs
        self.is_reloading = False
        self.event_read_count = 0
        self.summary_read_count = 0
        self.simulate_timeout_on = simulate_timeout_on or set()
        self.simulate_unavailable_on = simulate_unavailable_on or set()

        self.recorded_requests: list[dict[str, Any]] = []
        self.created_plans: dict[str, Any] = {}
        self.created_bindings: dict[str, Any] = {}
        self.created_events: dict[str, Any] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.recorded_requests.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "payload": payload,
        })

        for unavailable_path in self.simulate_unavailable_on:
            if unavailable_path in url:
                return HttpResponse(502, {"error": "Simulated upstream 502"}, {}, url, method)

        parsed = urllib.parse.urlparse(url)
        path = parsed.path

        # FE deployment.json
        if path == "/deployment.json":
            return HttpResponse(
                200,
                {
                    "commit": self.fe_commit,
                    "frontendSha": self.fe_commit,
                    "releaseName": "20260905T000000Z-release",
                    "pairId": self.pair_id,
                    "profile": "read-only",
                    "buildMode": {
                        "VITE_BFF_MODE": "live",
                        "VITE_BFF_FALLBACK": "strict",
                        "VITE_BFF_REAL_WRITES": self.fe_real_writes,
                    },
                },
                {},
                url,
                method,
            )

        # BFF version
        if path == "/bff/version":
            return HttpResponse(
                200,
                {
                    "source_commit_sha": self.bff_commit,
                    "commit": self.bff_commit,
                    "pair_id": self.pair_id,
                    "environment": "dev",
                    "config_posture": {"auth_stub": False, "auth_mode": "strict"},
                },
                {},
                url,
                method,
            )

        # BFF readyz
        if path == "/readyz":
            return HttpResponse(
                200,
                {
                    "ready": self.bff_ready,
                    "dependencies": {
                        "runtime-manager": {"status": "ok" if self.bff_ready else "unready"},
                        "paper-fleet-reconciler": {"status": "ok" if self.bff_ready else "unready"},
                    },
                },
                {},
                url,
                method,
            )

        # BFF loop health
        if path == "/bff/v5/loop-health":
            return HttpResponse(200, {"status": "ok", "rows": {}}, {}, url, method)

        # Capital APIs
        if path == "/api/capital/api/capital-pools" or path == "/api/capital-pools":
            return HttpResponse(201, {"status": "active"}, {}, url, method)
        if path == "/api/capital/api/bindings" or path == "/api/bindings":
            return HttpResponse(201, {"status": "pending"}, {}, url, method)
        if "/activate" in path:
            return HttpResponse(200, {"status": "active", "allowed_deployment_scope": "paper"}, {}, url, method)

        # Registry APIs
        if "/mutate" in path:
            new_id = (payload or {}).get("new_artifact_id", "artifact-new")
            child_strat = {
                "strategy_id": "tw_session_momentum",
                "version": "2.1.0",
                "lineage": [PARENT_STRATEGY_ARTIFACT_ID],
                "parameters": {"symbols": ["2330.TW"]},
            }
            return HttpResponse(
                201,
                {"entry": {"registry_id": new_id, "metadata": {"strategy_artifact": child_strat}}},
                {},
                url,
                method,
            )
        if "/advance" in path:
            return HttpResponse(200, {"entry": {"status": "approved"}}, {}, url, method)

        # Governance approvals
        if path.endswith("/api/governance/approvals") or path.endswith("/api/approvals"):
            return HttpResponse(201, {"decision_id": (payload or {}).get("decision_id")}, {}, url, method)
        if "/decide" in path:
            return HttpResponse(200, {"decision": "approved"}, {}, url, method)

        # Deployment plan validation & creation
        if path.endswith("/api/deployment/plans/validate") or path.endswith("/plans/validate"):
            return HttpResponse(200, {"ok": True}, {}, url, method)
        if (path.endswith("/api/deployment/plans") or path.endswith("/plans")) and method == "POST":
            plan_id = (payload or {}).get("plan_id", "plan-1")
            self.created_plans[plan_id] = payload
            return HttpResponse(201, {"plan_id": plan_id}, {}, url, method)

        # Deployment plan dispatch
        if "/dispatch" in path:
            plan_id = path.split("/")[-2]
            return HttpResponse(
                202,
                {"deployment_saga": {"saga": {"saga_id": f"saga-{plan_id}", "status": "pending"}}},
                {},
                url,
                method,
            )

        # Query deployment plan status
        if "/plans/" in path and method == "GET":
            plan_id = path.split("/")[-1]
            if "plan_terminal" in self.simulate_timeout_on:
                return HttpResponse(200, {"plan_id": plan_id, "status": "pending"}, {}, url, method)
            return HttpResponse(
                200,
                {"plan_id": plan_id, "status": self.deployment_terminal_status},
                {},
                url,
                method,
            )

        # Query RuntimeBinding
        if "runtime-bindings" in path:
            if method == "GET" and parsed.query and "plan_id=" in parsed.query:
                if "binding_created" in self.simulate_timeout_on:
                    return HttpResponse(200, {"bindings": []}, {}, url, method)
                plan_id = urllib.parse.parse_qs(parsed.query)["plan_id"][0]
                binding_id = f"rb-{plan_id.removeprefix('plan-')}"
                strat_id = "tw_session_momentum"
                version = "2.1.0"
                base_key = f"openclaw/registry/{strat_id}/{version}"
                binding_obj = {
                    "binding_id": binding_id,
                    "runtime_id": f"rt-{binding_id}",
                    "capital_pool_id": f"pool-{binding_id}",
                    "artifact_id": f"artifact-{binding_id}",
                    "artifact_version": version,
                    "plan_id": plan_id,
                    "status": "active",
                    "symbol": "2330.TW",
                    "deployment_mode": "paper",
                    "market_data_policy": {"owner": "source-ingest"},
                    "metadata": {
                        "strategy_id": strat_id,
                        "symbol": "2330.TW",
                        "object_store": {
                            f"{base_key}/metadata.json": {
                                "checksum": "sha256:dummychecksum1234567890abcdef",
                                "version": version,
                            },
                            f"{base_key}/artifact.bin": "{}",
                        },
                    },
                }
                self.created_bindings[binding_id] = binding_obj
                return HttpResponse(200, {"bindings": [binding_obj]}, {}, url, method)
            if method == "GET" and not parsed.query:
                # Reload by binding_id
                binding_id = path.split("/")[-1]
                if self.reload_binding_differs:
                    return HttpResponse(
                        200,
                        {"binding_id": binding_id, "status": "inactive"},
                        {},
                        url,
                        method,
                    )
                binding_obj = self.created_bindings.get(binding_id, {
                    "binding_id": binding_id,
                    "status": "active",
                })
                return HttpResponse(200, binding_obj, {}, url, method)

        # Fleet desired state
        if "desired-state" in path:
            if not self.fleet_desired_accepted:
                return HttpResponse(200, {"bindings": []}, {}, url, method)
            active_bindings = [{"binding_id": b_id} for b_id in self.created_bindings]
            return HttpResponse(200, {"bindings": active_bindings}, {}, url, method)

        # Fleet state
        if path.endswith("/api/fleet/state") or path.endswith("/fleet/state"):
            workers = []
            for b_id in self.created_bindings:
                workers.append({
                    "binding_id": b_id,
                    "status": "running" if self.fleet_worker_running else "stopped",
                    "pid": 1234,
                })
            return HttpResponse(200, {"workers": workers}, {}, url, method)

        # Telemetry runtime summary
        if "runtime-summaries" in path:
            runtime_id = path.split("/")[-1]
            self.summary_read_count += 1
            if (self.is_reloading or self.summary_read_count > 1) and self.reload_summary_differs:
                return HttpResponse(200, {"runtime_id": "rt-other"}, {}, url, method)
            event_id = f"ev-{runtime_id}"
            heartbeat_id = f"hb-{runtime_id}" if self.telemetry_heartbeat_present else ""
            summary = {
                "runtime_id": runtime_id,
                "state": "active",
                "last_heartbeat_event_id": heartbeat_id,
                "recent_lifecycle_event_ids": [event_id] if self.telemetry_fill_produced else [],
            }
            return HttpResponse(200, summary, {}, url, method)

        # Telemetry events
        if "telemetry/events" in path or "events/" in path:
            event_id = path.split("/")[-1]
            if "telemetry_event" in self.simulate_timeout_on:
                return HttpResponse(404, {"error": "not found"}, {}, url, method)
            self.event_read_count += 1
            if (self.is_reloading or self.event_read_count > 1) and self.reload_event_differs:
                return HttpResponse(200, {"event_id": "ev-different"}, {}, url, method)

            binding_id = next(iter(self.created_bindings.keys()), "rb-unknown")
            correlation_id = (
                self.telemetry_correlation_override
                or f"correlation-plan-{binding_id.removeprefix('rb-')}"
            )
            ev = {
                "event_id": event_id,
                "event_type": "paper_fill_simulated",
                "binding_id": binding_id,
                "artifact_id": f"artifact-{binding_id}",
                "correlation_envelope": {"correlation_id": correlation_id},
                "metadata": {
                    "is_real_capital": self.telemetry_real_capital,
                    "is_real_order": self.telemetry_real_order,
                    "broker_submission_status": "filled",
                    "sim_fill_flag": True,
                    "correlation_envelope": {"correlation_id": correlation_id},
                },
            }
            self.created_events[event_id] = ev
            return HttpResponse(200, ev, {}, url, method)

        return HttpResponse(200, {"status": "ok"}, {}, url, method)


def _default_test_config(**kwargs: Any) -> ProbeConfig:
    defaults: dict[str, Any] = {
        "bff_base_url": "https://api.dev.mvl-cap.tw",
        "fe_base_url": "https://app.dev.mvl-cap.tw",
        "deployment_url": "https://api.dev.mvl-cap.tw/api/deployment",
        "runtime_url": "https://api.dev.mvl-cap.tw/api/runtime",
        "fleet_url": "https://api.dev.mvl-cap.tw/api/fleet",
        "telemetry_url": "https://api.dev.mvl-cap.tw/api/telemetry",
        "capital_url": "https://api.dev.mvl-cap.tw/api/capital",
        "governance_url": "https://api.dev.mvl-cap.tw/api/governance",
        "registry_url": "https://api.dev.mvl-cap.tw/api/registry",
        "source_ingest_url": "https://api.dev.mvl-cap.tw/api/source-ingest",
        "expected_fe_sha": FE_SHA_VALID,
        "expected_bff_sha": BFF_SHA_VALID,
        "execute_paper_lifecycle": False,
        "paper_only": True,
        "poll_timeout_seconds": 2.0,
        "poll_interval_seconds": 0.05,
        "output_path": Path("/tmp/test-evidence-devprobe.json"),
    }
    defaults.update(kwargs)
    return ProbeConfig(**defaults)


def test_read_only_preflight_default() -> None:
    """Verifies that default mode executes read-only preflight and passes."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(execute_paper_lifecycle=False)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()

    assert evidence["schema_version"] == SCHEMA_VERSION
    assert evidence["task_id"] == TASK_ID
    assert evidence["status"] == "preflight_passed"
    assert evidence["mode"] == "read_only_preflight"
    assert evidence["served_identity"]["fe"]["commit"] == FE_SHA_VALID
    assert evidence["served_identity"]["bff"]["source_commit_sha"] == BFF_SHA_VALID
    assert evidence["served_identity"]["pair_consistent"] is True
    assert evidence["audit"]["bearer_credentials_redacted"] is True
    assert "artifact_digest_sha256" in evidence
    assert len(evidence["artifact_digest_sha256"]) == 64
    # Ensure no mutating plans or bindings were created
    assert len(double.created_plans) == 0
    assert len(double.created_bindings) == 0


def test_stale_identity_fe_rejection() -> None:
    """Verifies that mismatched FE commit raises StaleIdentityError."""
    double = HonestCanonicalOwnerDouble(fe_commit="c" * 40)
    config = _default_test_config(expected_fe_sha=FE_SHA_VALID)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(StaleIdentityError, match="Served FE commit"):
        probe.run()


def test_stale_identity_bff_rejection() -> None:
    """Verifies that mismatched BFF commit raises StaleIdentityError."""
    double = HonestCanonicalOwnerDouble(bff_commit="d" * 40)
    config = _default_test_config(expected_bff_sha=BFF_SHA_VALID)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(StaleIdentityError, match="Served BFF commit"):
        probe.run()


def test_wrong_correlation_pair_id_rejection() -> None:
    """Verifies that FE/BFF pair ID mismatch raises CorrelationMismatchError."""
    class MismatchedPairDouble(HonestCanonicalOwnerDouble):
        def __call__(self, method: str, url: str, headers: Mapping[str, str], payload: Any, timeout: float) -> HttpResponse:
            if "/bff/version" in url:
                return HttpResponse(200, {"source_commit_sha": BFF_SHA_VALID, "pair_id": "pair-different-999"}, {}, url, method)
            return super().__call__(method, url, headers, payload, timeout)

    double = MismatchedPairDouble()
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(CorrelationMismatchError, match="FE pairId .* does not match BFF pair_id"):
        probe.run()


def test_wrong_correlation_telemetry_envelope_rejection() -> None:
    """Verifies that telemetry event correlation_id mismatch raises CorrelationMismatchError."""
    double = HonestCanonicalOwnerDouble(
        telemetry_correlation_override="correlation-wrong-12345"
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(CorrelationMismatchError, match="Telemetry event correlation_id .* != expected"):
        probe.run()


def test_missing_consumer_receipt_failure_loop8() -> None:
    """Verifies that missing Loop 8 fleet desired state receipt raises MissingConsumerReceiptError."""
    double = HonestCanonicalOwnerDouble(fleet_desired_accepted=False)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Fleet desired state did not accept binding"):
        probe.run()


def test_missing_consumer_receipt_failure_loop9() -> None:
    """Verifies that missing Loop 9 heartbeat receipt raises MissingConsumerReceiptError."""
    double = HonestCanonicalOwnerDouble(telemetry_heartbeat_present=False)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Loop 9 next-consumer heartbeat receipt missing"):
        probe.run()


def test_timeout_waiting_for_deployment_terminal() -> None:
    """Verifies that timeout waiting for deployment plan raises ProbeTimeoutError."""
    double = HonestCanonicalOwnerDouble(simulate_timeout_on={"plan_terminal"})
    config = _default_test_config(execute_paper_lifecycle=True, poll_timeout_seconds=0.3)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeTimeoutError, match="Timed out waiting for DeploymentPlan"):
        probe.run()


def test_timeout_waiting_for_telemetry_event() -> None:
    """Verifies that timeout waiting for telemetry event raises ProbeTimeoutError."""
    double = HonestCanonicalOwnerDouble(simulate_timeout_on={"telemetry_event"})
    config = _default_test_config(execute_paper_lifecycle=True, poll_timeout_seconds=0.3)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeTimeoutError, match="Timed out waiting for telemetry fill event"):
        probe.run()


def test_reload_mismatch_binding() -> None:
    """Verifies that fresh-client reload returning different binding state raises ReloadMismatchError."""
    double = HonestCanonicalOwnerDouble(reload_binding_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded binding status .* is not active"):
        probe.run()


def test_reload_mismatch_event() -> None:
    """Verifies that fresh-client reload returning different event ID raises ReloadMismatchError."""
    double = HonestCanonicalOwnerDouble(reload_event_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded event ID"):
        probe.run()


def test_reload_mismatch_summary() -> None:
    """Verifies that fresh-client reload returning different summary raises ReloadMismatchError."""
    double = HonestCanonicalOwnerDouble(reload_summary_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded summary runtime_id"):
        probe.run()


def test_fail_closed_on_real_capital_flag() -> None:
    """Verifies that enabling real capital writes fails closed."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(allow_real_capital=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(RealCapitalOrOrderWriteForbiddenError, match="Real capital or real order writes"):
        probe.run()


def test_fail_closed_on_real_order_flag() -> None:
    """Verifies that enabling real order writes fails closed."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(allow_real_orders=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(RealCapitalOrOrderWriteForbiddenError, match="Real capital or real order writes"):
        probe.run()


def test_fail_closed_on_fe_real_writes() -> None:
    """Verifies that FE bundle reporting VITE_BFF_REAL_WRITES=true fails closed."""
    double = HonestCanonicalOwnerDouble(fe_real_writes="true")
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(RealCapitalOrOrderWriteForbiddenError, match="FE served bundle has VITE_BFF_REAL_WRITES='true'"):
        probe.run()


def test_reject_retired_deploy_target() -> None:
    """Verifies that retired hosts, IPs, or legacy projects are rejected."""
    for retired in (
        "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
        "https://35.201.239.38/bff",
        "https://pantheon-benjamin-20260528.appspot.com",
    ):
        with pytest.raises(RetiredDeployTargetError, match="targets retired deploy host or project"):
            validate_host_not_retired(retired)


def test_reports_unavailable_separately() -> None:
    """Verifies that upstream 502/503 is caught and reported as unavailable without crashing."""
    double = HonestCanonicalOwnerDouble(simulate_unavailable_on={"/readyz"})
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()

    assert evidence["status"] == "unavailable"
    assert "unavailable" in evidence["error"]


def test_reports_blocked_separately() -> None:
    """Verifies that unready dependency state is reported as blocked without crashing."""
    double = HonestCanonicalOwnerDouble(bff_ready=False)
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()

    assert evidence["status"] == "blocked"
    assert "unready" in evidence["blocked_reason"]


def test_credentials_never_printed_or_persisted() -> None:
    """Verifies that secrets are fingerprinted and never emitted in plain text."""
    secret_token = "ey.bearer.verysecrettoken123456789"
    data = {
        "Authorization": f"Bearer {secret_token}",
        "access_token": secret_token,
        "jwt_secret": "my-secret-key",
        "normal_field": "safe_value",
    }
    redacted = redact_secrets(data)
    assert secret_token not in json.dumps(redacted)
    assert "[REDACTED]" in str(redacted) or "sha256:" in str(redacted)
    assert redacted["normal_field"] == "safe_value"


def test_full_successful_paper_lifecycle_run() -> None:
    """Verifies complete execution of Loops 8 and 9 with fresh stimulus and reload."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(execute_paper_lifecycle=True)

    # Use fresh double instance factory to prove clean client isolation on reload
    def transport_factory() -> HonestCanonicalOwnerDouble:
        fresh_double = HonestCanonicalOwnerDouble()
        fresh_double.created_bindings = double.created_bindings
        fresh_double.created_events = double.created_events
        return fresh_double

    probe = DevRuntimePaperLifecycleProbe(config, transport=double)
    evidence = probe.run(fresh_transport_factory=transport_factory)

    assert evidence["status"] == "passed"
    assert evidence["mode"] == "paper_lifecycle"

    # Loop 8 assertions
    loop8 = evidence["loops"]["loop_08_promotion_deployment"]
    assert loop8["trigger_id"].startswith("plan-devprobe-")
    assert loop8["terminal_output_id"].startswith("rb-devprobe-")
    assert loop8["next_consumer_receipt_id"] == loop8["terminal_output_id"]
    assert loop8["owner_worker_identity"]["service"] == "deployment-outbox-consumer"
    assert loop8["assertions"]["executable_runtime_binding"] is True
    assert loop8["durable_reload"]["verified"] is True

    # Loop 9 assertions
    loop9 = evidence["loops"]["loop_09_capital_artifact_execution"]
    assert loop9["trigger_id"] == loop8["terminal_output_id"]
    assert loop9["terminal_output_id"].startswith("ev-rt-rb-")
    assert loop9["next_consumer_receipt_id"].startswith("hb-rt-rb-")
    assert loop9["owner_worker_identity"]["service"] == "paper-signal-producer"
    assert loop9["assertions"]["is_real_capital"] is False
    assert loop9["assertions"]["is_real_order"] is False
    assert loop9["assertions"]["broker_submission_status"] == "filled"
    assert loop9["durable_reload"]["verified"] is True

    # Fresh client reload section
    reload_sec = evidence["durable_fresh_client_reload"]
    assert reload_sec["verified"] is True
    assert reload_sec["fresh_client_isolated"] is True

    # Check that stimulus IDs were fresh and did not use historical forbidden IDs
    for hist_id in HISTORICAL_FORBIDDEN_IDS:
        assert hist_id != loop8["trigger_id"]
        assert hist_id != loop8["terminal_output_id"]
        assert hist_id != loop9["terminal_output_id"]


def test_is_executable_binding_validator() -> None:
    """Verifies that is_executable_binding validates contract fields."""
    strat = "tw_session_momentum"
    ver = "2.1.0"
    base_key = f"openclaw/registry/{strat}/2.1.0"
    valid_binding = {
        "binding_id": "rb-123",
        "runtime_id": "rt-123",
        "capital_pool_id": "pool-123",
        "artifact_id": "artifact-123",
        "artifact_version": ver,
        "plan_id": "plan-123",
        "status": "active",
        "symbol": "2330.TW",
        "market_data_policy": {"owner": "source-ingest"},
        "metadata": {
            "strategy_id": strat,
            "symbol": "2330.TW",
            "object_store": {
                f"{base_key}/metadata.json": json.dumps({"checksum": "sha256:abc"}),
            },
        },
    }
    assert is_executable_binding(valid_binding) is True

    # Missing field
    invalid_1 = dict(valid_binding)
    invalid_1["plan_id"] = ""
    assert is_executable_binding(invalid_1) is False

    # Inactive status
    invalid_2 = dict(valid_binding)
    invalid_2["status"] = "inactive"
    assert is_executable_binding(invalid_2) is False

    # Missing checksum
    invalid_3 = dict(valid_binding)
    invalid_3["metadata"] = {
        "strategy_id": strat,
        "object_store": {f"{base_key}/metadata.json": json.dumps({"checksum": ""})},
    }
    assert is_executable_binding(invalid_3) is False


def test_seal_evidence_deterministic() -> None:
    """Verifies that evidence seal produces deterministic SHA-256."""
    payload = {"task_id": TASK_ID, "status": "passed", "probes": 5}
    sealed_1 = seal_evidence(payload)
    sealed_2 = seal_evidence(payload)
    assert sealed_1["artifact_digest_sha256"] == sealed_2["artifact_digest_sha256"]
