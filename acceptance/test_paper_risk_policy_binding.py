"""Probe controls; all approval subjects and numeric limits here are fixtures.

The positive subject is explicit audit evidence, not an existing hosted mapping
or a proposal to activate a financial profile.
"""
import copy
import importlib.machinery
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

PATH = Path(__file__).parent / "probes" / "paper-risk-policy-binding"
loader = importlib.machinery.SourceFileLoader("paper_risk_policy_probe", str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
probe = importlib.util.module_from_spec(spec)
loader.exec_module(probe)
NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def evidence():
    policy = {"risk_policy_id": "fixture-policy", "version": "fixture-v1", "status": "active",
              "allowed_stages": ["paper"], "gross_limit": 0.8, "net_limit": 0.7,
              "max_leverage": 0.8, "max_single_name_weight": 0.2}
    persona = {"id": "fixture-persona", "name": "Fixture trader", "tenantId": "fixture-tenant",
               "ownerLifecycleState": "paper_owner", "capitalMode": "paper", "risk": "low",
               "runtimeBindingId": "old-binding"}
    binding = {"binding_id": "active-binding", "runtime_id": "fixture-runtime", "status": "active",
               "capital_pool_id": "fixture-pool", "artifact_id": "fixture-envelope-v2",
               "artifact_version": "1.2.0", "metadata": {"tenant_id": "fixture-tenant",
                                                            "persona_id": "fixture-persona"}}
    pool = {"pool_id": "fixture-pool", "tenant_id": "fixture-tenant", "owner_id": "fixture-tenant",
            "status": "active", "risk_policy_ref": None, "approval_digest": "fixture-activation-digest",
            "metadata": {"persona_id": "fixture-persona", "risk_policy": policy}}
    approval = {"decision_id": "fixture-owner-approval", "target_type": "capital_pool_activation",
                "target_id": "fixture-pool", "target_version": "fixture-activation-digest",
                "tenant_id": "fixture-tenant", "decision_state": "decided", "decision": "approved",
                "authority_status": "authoritative",
                "risk_level": "high", "recorded_at": "2026-11-20T00:00:01Z",
                "controller_record_ref": "fixture-controller-record", "expires_at": "2026-12-01T00:00:00Z",
                "actor_role": "risk_owner", "actor_id": "fixture-owner", "decided_at": "2026-11-20T00:00:00Z",
                "metadata": {"subject": {"pool_id": "fixture-pool", "requested_risk": "low", "risk_direction": "increase",
                                         "risk_policy_id": "fixture-policy", "risk_policy_version": "fixture-v1",
                                         "risk_policy_digest": probe.policy_digest(policy)}}}
    return {"/bff/personas/fixture-persona": {"data": persona},
            "/api/v1/runtime-bindings?deployment_mode=paper": {"data": [binding]},
            "/bff/capital-pools/fixture-pool": {"data": pool},
            "/api/v1/approval-decisions": {"data": [approval]}}


def check(payload, **kwargs):
    return probe.run(lambda path: payload[path], ("fixture-persona",), now=NOW, **kwargs)


def test_valid_inline_and_explicit_profile_proof_uses_active_binding():
    report = check(evidence())
    assert report["passed"] and report["checked"] == 1
    row = report["personas"]["fixture-persona"]
    assert row["policy_origin"] == "inline"
    assert row["binding_id"] == "active-binding"
    assert row["persona_display_binding_id"] == "old-binding"


def test_inline_is_effective_but_low_label_alone_does_not_map_it():
    payload = evidence()
    payload["/api/v1/approval-decisions"]["data"] = []
    row = check(payload)["personas"]["fixture-persona"]
    assert row["effective_policy"]["risk_policy_id"] == "fixture-policy"
    assert row["failed"] == ["required_profile_mapping_unresolved"]


def test_missing_policy_is_distinct_from_valid_inline():
    payload = evidence()
    del payload["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["risk_policy"]
    assert "no_effective_policy" in check(payload)["personas"]["fixture-persona"]["failed"]


@pytest.mark.parametrize("mutation", ["tenant", "version", "limits", "expired", "registry", "revoked", "conditions", "no_actor", "unverified_authority", "no_expiry", "unrecorded", "unauthorized_role", "wrong_scope"])
def test_mapping_proof_cannot_be_stale_foreign_or_registry_admission(mutation):
    payload = evidence()
    approval = payload["/api/v1/approval-decisions"]["data"][0]
    if mutation == "tenant":
        approval["tenant_id"] = "other-tenant"
    elif mutation == "version":
        approval["metadata"]["subject"]["risk_policy_version"] = "older-version"
    elif mutation == "limits":
        payload["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["risk_policy"]["gross_limit"] = 1.3
    elif mutation == "expired":
        approval["expires_at"] = "2026-11-20T00:00:00Z"
    elif mutation == "registry":
        approval.update(target_type="registry_entry", risk_level="low")
    elif mutation == "revoked":
        approval["revoked_at"] = "2026-11-20T23:00:00Z"
    elif mutation == "conditions":
        approval["conditions"] = ["fixture unresolved condition"]
    elif mutation == "no_actor":
        approval["actor_id"] = None
    elif mutation == "no_expiry":
        approval.pop("expires_at")
    elif mutation == "unrecorded":
        approval.pop("controller_record_ref")
    elif mutation == "unauthorized_role":
        approval["actor_role"] = "viewer"
    elif mutation == "wrong_scope":
        approval["authorization_scope"] = {"environment": "production", "allowed_target_stages": ["live"], "max_capital_scale_pct": 100}
    else:
        approval["authority_status"] = "unknown"
    assert not check(payload)["passed"]


@pytest.mark.parametrize("value", [True, -1, float("nan"), "low"])
def test_invalid_financial_limits_fail(value):
    payload = evidence()
    payload["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["risk_policy"]["gross_limit"] = value
    assert not check(payload)["passed"]


def test_absent_per_name_is_not_explicit_zero_and_reserves_are_not_invented():
    policy = evidence()["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["risk_policy"]
    del policy["max_single_name_weight"]
    assert not probe.policy_errors(policy)
    assert "max_single_name_weight" not in policy and "cash_reserve" not in policy
    policy["max_single_name_weight"] = 0
    assert not probe.policy_errors(policy)
    assert policy["max_single_name_weight"] == 0


def test_reference_never_falls_back_to_nearby_inline_policy():
    payload = evidence()
    payload["/bff/capital-pools/fixture-pool"]["data"]["risk_policy_ref"] = "fixture-policy"
    assert not check(payload)["passed"]
    assert "referenced_policy_owner_readback_unavailable" in check(payload)["failed"][0]
    policy = copy.deepcopy(payload["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["risk_policy"])
    assert check(payload, referenced_policy_reader=lambda ref: policy)["passed"]
    policy["risk_policy_id"] = "wrong-policy"
    assert not check(payload, referenced_policy_reader=lambda ref: policy)["passed"]


@pytest.mark.parametrize("scope", ["*", "fixture-policy", "fixture-pool"])
def test_catalog_approval_with_exact_owner_reference(scope):
    payload = evidence()
    pool = payload["/bff/capital-pools/fixture-pool"]["data"]
    policy = pool["metadata"]["risk_policy"]
    pool["risk_policy_ref"] = policy["risk_policy_id"]
    approval = payload["/api/v1/approval-decisions"]["data"][0]
    approval.update(target_id=policy["risk_policy_id"], target_version=policy["version"])
    approval["metadata"]["subject"]["pool_id"] = scope
    approval["metadata"]["subject"]["risk_policy_digest"] = probe.risk_policy_digest(policy)
    owner = {"capital_pool_id": "fixture-pool", "tenant_id": "fixture-tenant", "stage": "paper",
             "resolved": True, "policy": policy}
    assert check(payload, pool_policy_reader=lambda pool: owner)["passed"]
    owner["tenant_id"] = "foreign-tenant"
    assert not check(payload, pool_policy_reader=lambda pool: owner)["passed"]


@pytest.mark.parametrize("mutation", ["foreign_pool", "wrong_digest", "wrong_version", "ambiguous"])
def test_catalog_mapping_rejects_foreign_stale_or_ambiguous_proof(mutation):
    payload = evidence()
    approval = payload["/api/v1/approval-decisions"]["data"][0]
    approval.update(target_id="fixture-policy", target_version="fixture-v1")
    if mutation == "foreign_pool":
        approval["metadata"]["subject"]["pool_id"] = "foreign-pool"
    elif mutation == "wrong_digest":
        approval["metadata"]["subject"]["risk_policy_digest"] = "stale"
    elif mutation == "wrong_version":
        approval["target_version"] = "old"
    else:
        duplicate = copy.deepcopy(approval)
        duplicate["decision_id"] = "another-approval"
        payload["/api/v1/approval-decisions"]["data"].append(duplicate)
    assert not check(payload)["passed"]


@pytest.mark.parametrize("kind", ["retired", "probe", "missing_binding", "foreign_pool", "duplicate_binding"])
def test_no_eligible_traders_and_wrong_tenant_cannot_pass(kind):
    payload = evidence()
    persona = payload["/bff/personas/fixture-persona"]["data"]
    bindings = payload["/api/v1/runtime-bindings?deployment_mode=paper"]["data"]
    if kind == "retired":
        persona["ownerLifecycleState"] = "retired"
    elif kind == "probe":
        persona["name"] = "Pantheon Dev Paper Release fixture"
    elif kind == "missing_binding":
        bindings.clear()
    elif kind == "foreign_pool":
        payload["/bff/capital-pools/fixture-pool"]["data"]["tenant_id"] = "other-tenant"
    else:
        bindings.append(copy.deepcopy(bindings[0]))
    report = check(payload)
    assert not report["passed"] and report["checked"] == 0
    assert "no_expected_personas_checked" in report["failed"]


def test_zero_expected_is_failure_without_any_http_requests():
    report = probe.run(lambda _: pytest.fail("unexpected request"), ())
    assert report["passed"] is False


def test_approval_source_unavailable_still_checks_expected_personas():
    payload = evidence()
    def fetch(path):
        if path == "/api/v1/approval-decisions":
            raise RuntimeError("http_403")
        return payload[path]
    report = probe.run(fetch, ("fixture-persona",), now=NOW)
    assert report["checked"] == 1 and not report["passed"]
    assert report["mapping_source_error"] == "http_403"


def test_missing_credentials_are_skip_but_not_pass(monkeypatch, capsys):
    for name in probe.REQUIRED:
        monkeypatch.delenv(name, raising=False)
    assert probe.main() == 77
    assert "skipped: missing" in capsys.readouterr().out


def test_http_main_uses_policy_reader_only_for_approval_get(monkeypatch, capsys):
    import io
    import json
    from urllib.parse import urlsplit

    payload = evidence()
    for key, value in {"ACCEPTANCE_BFF_URL": "https://fixture.invalid",
                       "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer-id",
                       "ACCEPTANCE_VIEWER_CLIENT_SECRET": "viewer-secret",
                       "ACCEPTANCE_POLICY_READER_CLIENT_ID": "owner-id",
                       "ACCEPTANCE_POLICY_READER_CLIENT_SECRET": "owner-secret",
                       "ACCEPTANCE_EXPECTED_PERSONA_IDS": "fixture-persona"}.items():
        monkeypatch.setenv(key, value)
    calls = []
    def open_request(request, timeout):
        path = urlsplit(request.full_url).path
        query = urlsplit(request.full_url).query
        calls.append((request.get_method(), path))
        if request.get_method() == "POST":
            assert path == "/bff/auth/dev-login"
            body = json.loads(request.data)
            response = {"access_token": "owner-token" if body["client_id"] == "owner-id" else "viewer-token"}
        else:
            expected = "owner-token" if path == "/api/v1/approval-decisions" else "viewer-token"
            assert request.get_header("Authorization") == "Bearer " + expected
            response = payload[path + ("?" + query if query else "")]
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(probe.urllib.request, "urlopen", open_request)
    assert probe.main() == 0
    output = capsys.readouterr().out
    assert '"passed": true' in output
    assert all(secret not in output for secret in ["owner-token", "viewer-token", "viewer-secret", "owner-secret"])
    assert sum(method == "POST" for method, path in calls) == 2


def test_cli_reads_reference_from_capital_owner_with_tenant_and_separate_token(monkeypatch, capsys):
    import io
    import json
    from urllib.parse import urlsplit
    payload = evidence()
    pool = payload["/bff/capital-pools/fixture-pool"]["data"]
    pool["risk_policy_ref"] = "fixture-policy"
    for key, value in {"ACCEPTANCE_BFF_URL": "https://bff.invalid", "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer",
            "ACCEPTANCE_VIEWER_CLIENT_SECRET": "viewer-secret", "ACCEPTANCE_EXPECTED_PERSONA_IDS": "fixture-persona",
            "ACCEPTANCE_CAPITAL_OWNER_URL": "http://capital.invalid", "ACCEPTANCE_CAPITAL_OWNER_TOKEN": "capital-token"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ACCEPTANCE_POLICY_READER_CLIENT_ID", raising=False)
    monkeypatch.delenv("ACCEPTANCE_POLICY_READER_CLIENT_SECRET", raising=False)
    def request(req, timeout):
        parsed = urlsplit(req.full_url)
        if req.get_method() == "POST":
            assert parsed.path == "/bff/auth/dev-login"
            body = {"access_token": "viewer-token"}
        elif parsed.hostname == "capital.invalid":
            assert req.get_header("X-tenant-id") == "fixture-tenant"
            assert req.get_header("Authorization") == "Bearer capital-token"
            assert parsed.path == "/api/capital-pools/fixture-pool/risk-policy" and parsed.query == "stage=paper"
            body = {"capital_pool_id": "fixture-pool", "tenant_id": "fixture-tenant", "stage": "paper",
                    "resolved": True, "policy": pool["metadata"]["risk_policy"]}
        else:
            assert req.get_header("Authorization") == "Bearer viewer-token"
            body = payload[parsed.path + ("?" + parsed.query if parsed.query else "")]
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(probe.urllib.request, "urlopen", request)
    assert probe.main() == 0
    assert "capital-token" not in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["capital", "telemetry", "approvals"])
def test_dev_owner_transport_sends_only_scoped_ids_and_uses_bounded_existing_helper(monkeypatch, kind):
    import json
    import subprocess
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", "https://api.dev.mvl-cap.tw")
    calls = []
    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0].endswith("/scripts/dev_vm_ssh.sh") and argv[1] == "exec"
        assert argv[2].startswith("timeout 15s docker exec -i pantheon-")
        assert json.loads(kwargs["input"]) == {"kind": kind, "identity": "fixture-id", "tenant": "fixture-tenant"}
        assert kwargs["timeout"] == 3 and kwargs["capture_output"] is True
        return subprocess.CompletedProcess(argv, 0, '{"fixture":true}', '')
    monkeypatch.setattr(probe.subprocess, "run", invoke)
    assert probe.dev_owner_read(kind, "fixture-id", "fixture-tenant", timeout=3) == {"fixture": True}
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "exit", "oversize", "invalid", "wrong_origin"])
def test_dev_owner_transport_fails_closed_without_echoing_remote_secrets(monkeypatch, failure):
    import subprocess
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", "https://fixture.invalid" if failure == "wrong_origin"
                       else "https://api.dev.mvl-cap.tw")
    def invoke(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 1, output="fixture-secret")
        return subprocess.CompletedProcess(argv, 1 if failure == "exit" else 0,
            "x" * (probe.MAX_BYTES + 1) if failure == "oversize" else "fixture-secret", "fixture-secret")
    monkeypatch.setattr(probe.subprocess, "run", invoke)
    with pytest.raises(RuntimeError) as error:
        probe.dev_owner_read("telemetry", "fixture-id", "fixture-tenant", timeout=1)
    assert "fixture-secret" not in str(error.value)


def test_cli_uses_installed_dev_transport_for_catalog_and_approval_reads(monkeypatch, capsys):
    import io
    import json
    from urllib.parse import urlsplit
    payload = evidence()
    pool = payload["/bff/capital-pools/fixture-pool"]["data"]
    pool["risk_policy_ref"] = "fixture-policy"
    for key, value in {"ACCEPTANCE_BFF_URL": "https://api.dev.mvl-cap.tw", "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer",
            "ACCEPTANCE_VIEWER_CLIENT_SECRET": "viewer-secret", "ACCEPTANCE_EXPECTED_PERSONA_IDS": "fixture-persona",
            "DEV_DEPLOY_SSH_HOST": "fixture", "DEV_DEPLOY_SSH_KEY_FILE": "/fixture/key"}.items():
        monkeypatch.setenv(key, value)
    for key in ("ACCEPTANCE_POLICY_READER_CLIENT_ID", "ACCEPTANCE_POLICY_READER_CLIENT_SECRET",
                "ACCEPTANCE_CAPITAL_OWNER_URL", "ACCEPTANCE_CAPITAL_OWNER_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    def remote(kind, identity, tenant, *, timeout):
        calls.append((kind, identity, tenant))
        assert 0 < timeout <= probe.MAX_SECONDS
        if kind == "approvals":
            return payload["/api/v1/approval-decisions"]
        assert kind == "capital" and identity == "fixture-pool" and tenant == "fixture-tenant"
        return {"capital_pool_id": identity, "tenant_id": tenant, "stage": "paper",
                "resolved": True, "policy": pool["metadata"]["risk_policy"]}
    def request(req, timeout):
        parsed = urlsplit(req.full_url)
        if req.get_method() == "POST":
            body = {"access_token": "viewer-token"}
        else:
            assert parsed.path != "/api/v1/approval-decisions"
            body = payload[parsed.path + ("?" + parsed.query if parsed.query else "")]
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(probe, "dev_owner_read", remote)
    monkeypatch.setattr(probe.urllib.request, "urlopen", request)
    assert probe.main() == 0
    assert [call[0] for call in calls] == ["approvals", "capital"]
    assert "viewer-secret" not in capsys.readouterr().out
