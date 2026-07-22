import scripts.probe_loop_prod_per_001_hosted as probe
from scripts.probe_loop_prod_per_001_hosted import is_canonical_strategy_artifact_id


def _json_response(status, *, data=None, meta=None, error=None):
    payload = {}
    if data is not None:
        payload["data"] = data
    if meta is not None:
        payload["meta"] = meta
    if error is not None:
        payload["error"] = error
    return {
        "method": "GET",
        "url": "https://bff.test",
        "started_at": "2026-07-18T00:00:00Z",
        "completed_at": "2026-07-18T00:00:00Z",
        "status": status,
        "ok": 200 <= status < 300,
        "headers": {"content-type": "application/json"},
        "json": payload,
    }


def test_strategy_artifact_id_accepts_persona_artifact_authority_metadata():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
            "strategy_spec_registry_id": "reg-strategy-spec-persona-abc123",
            "authoritative_loader_attestation": {
                "artifact_id": "artifact-persona-paper-abc123",
            },
        }
    }

    assert is_canonical_strategy_artifact_id("artifact-persona-paper-abc123", record)


def test_strategy_artifact_id_rejects_strategy_spec_registry_id():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
            "strategy_spec_registry_id": "reg-strategy-spec-persona-abc123",
        }
    }

    assert not is_canonical_strategy_artifact_id("reg-strategy-spec-persona-abc123", record)


def test_strategy_artifact_id_requires_authority_match_when_metadata_present():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
        }
    }

    assert not is_canonical_strategy_artifact_id("artifact-persona-paper-other", record)


def test_strategy_artifact_id_accepts_legacy_strategy_artifact_prefix_without_metadata():
    assert is_canonical_strategy_artifact_id("reg-strategy-artifact-abc123")


def test_request_json_records_transport_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise TimeoutError("read operation timed out")

    monkeypatch.setattr(probe.urllib.request, "urlopen", raise_timeout)

    response = probe.request_json("GET", "https://example.test/readyz", timeout=3)

    assert response["status"] is None
    assert response["ok"] is False
    assert response["json"]["error"]["code"] == "request_failed"
    assert response["json"]["error"]["details"] == {
        "exception": "TimeoutError",
        "timeout_seconds": 3,
    }


def test_request_json_with_retries_returns_success_after_retryable_failure(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return {"status": None, "ok": False, "json": {"error": {"code": "request_failed"}}}
        return {"status": 201, "ok": True, "json": {"data": {"id": "persona-1"}}}

    monkeypatch.setattr(probe, "request_json", fake_request)
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)

    response = probe.request_json_with_retries(
        "POST",
        "https://example.test/bff/personas",
        attempts=2,
        retry_delay_seconds=5,
        timeout=120,
    )

    assert response["status"] == 201
    assert len(response["retry_attempts"]) == 2
    assert [attempt["attempt"] for attempt in response["retry_attempts"]] == [1, 2]
    assert json_round_trips(response)["retry_attempts"][1]["status"] == 201


def test_probe_replays_terminal_reconcile_after_timeout(monkeypatch):
    monkeypatch.setenv("DEV_BFF_JWT_SECRET", "unit-test-secret")
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    reconcile_calls = {"count": 0}
    expected_sha = "a" * 40
    persona_id = "persona-unit"
    runtime_id = "rt-unit"
    runtime_binding_id = "rb-unit"
    deployment_plan_id = "plan-unit"
    artifact_id = "artifact-persona-paper-unit"
    schedule_meta = {
        "persona_id": persona_id,
        "workflow_id": probe.FIRST_EVALUATION_WORKFLOW_ID,
        "registered": True,
        "job_id": "job-unit",
        "job_name": "pantheon-first-evaluation-persona-unit",
        "request_id": f"persona-provisioning:{persona_id}:{probe.FIRST_EVALUATION_WORKFLOW_ID}",
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "capital_pool_id": "pool-unit",
        "persona_capital_binding_id": "pcb-unit",
    }
    terminal_meta = {
        "status": "ok",
        "degraded_dependencies": [],
        "authoritative_readback": {
            "available": True,
            "first_evaluation_schedule": schedule_meta,
        },
    }
    terminal_persona = {
        "id": persona_id,
        "state": "paper_running",
        "runtimeId": runtime_id,
        "runtimeBindingId": runtime_binding_id,
        "deploymentPlanId": deployment_plan_id,
    }

    def fake_request_json(method, url, **kwargs):
        body = kwargs.get("body") or {}
        if url.endswith("/bff/version"):
            return {
                **_json_response(
                    200,
                    data=None,
                ),
                "json": {
                    "source_commit_sha": expected_sha,
                    "build_time": "2026-07-18T00:00:00Z",
                    "config_posture": {"auth_stub": False, "auth_mode": "strict"},
                },
            }
        if url.endswith("/readyz"):
            return {
                **_json_response(200),
                "json": {
                    "ready": True,
                    "dependencies": {"deployment": {"status": "ok"}},
                },
            }
        if url.endswith("/bff/personas") and method == "POST":
            if kwargs.get("token") is None:
                return _json_response(
                    401,
                    error={"code": "AUTH_REQUIRED", "details": {"reason": "401"}},
                )
            if body.get("tenantId") == "tenant-other":
                return _json_response(
                    403,
                    error={
                        "code": "FORBIDDEN",
                        "details": {"reason": "tenant_scope"},
                    },
                )
            if body.get("capitalMode") == "live":
                return _json_response(
                    422,
                    error={
                        "code": "VALIDATION_FAILED",
                        "details": {"reason": "capital_mode"},
                    },
                )
            return _json_response(
                201,
                data={
                    "id": persona_id,
                    "state": "provisioning",
                    "deploymentPlanId": deployment_plan_id,
                },
                meta={
                    "first_evaluation_workflow_id": probe.FIRST_EVALUATION_WORKFLOW_ID,
                },
            )
        if url.endswith(f"/bff/personas/{persona_id}/provisioning/reconcile"):
            reconcile_calls["count"] += 1
            if reconcile_calls["count"] == 1:
                return {
                    "method": method,
                    "url": url,
                    "started_at": "2026-07-18T00:00:00Z",
                    "completed_at": "2026-07-18T00:01:00Z",
                    "status": None,
                    "ok": False,
                    "headers": {},
                    "json": {
                        "error": {
                            "code": "request_failed",
                            "details": {"exception": "TimeoutError"},
                        },
                    },
                }
            return _json_response(200, data=terminal_persona, meta=terminal_meta)
        if url.endswith(f"/bff/personas/{persona_id}"):
            return _json_response(200, data=terminal_persona)
        if url.endswith(f"/api/v1/runtime-bindings/{runtime_binding_id}"):
            return _json_response(
                200,
                data={
                    "binding_id": runtime_binding_id,
                    "runtime_id": runtime_id,
                    "artifact_id": artifact_id,
                    "deployment_mode": "paper",
                },
            )
        if url.endswith(f"/api/v1/runtimes/{runtime_id}/status"):
            return _json_response(
                200,
                data={"runtime_binding_id": runtime_binding_id, "runtime_id": runtime_id},
            )
        if url.endswith(f"/api/v1/deployment-plans/{deployment_plan_id}"):
            return _json_response(200, data={"id": deployment_plan_id, "artifact_id": artifact_id})
        if url.endswith(f"/bff/personas/{persona_id}/runtime-profile"):
            return _json_response(200, data={})
        if url.endswith(f"/bff/personas/{persona_id}/capabilities"):
            return _json_response(200, data={"effectiveWorkflows": []})
        if url.endswith(f"/bff/personas/{persona_id}/evaluations"):
            return _json_response(200, data={})
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(probe, "request_json", fake_request_json)
    args = probe.argparse.Namespace(
        base_url="https://bff.test",
        expected_sha=expected_sha,
        tenant_id="pantheon-dev",
        run_id="unit",
        timeout_seconds=1,
        poll_seconds=0,
    )

    evidence = probe.run_probe(args)

    assert evidence["result"]["status"] == "passed"
    assert "persona_terminal_reconcile_replay" in evidence["calls"]
    assert reconcile_calls["count"] == 2
    checks = {row["name"]: row["status"] for row in evidence["checks"]}
    assert checks["persona.reconcile_terminal_paper_running"] == "pass"
    assert checks["first_evaluation_schedule.readback_gated_final_state"] == "pass"


def json_round_trips(value):
    import json

    return json.loads(json.dumps(value))
