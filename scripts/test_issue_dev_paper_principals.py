"""Synthetic issuance/verification tests; no hosted credentials are loaded."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from scripts import issue_dev_paper_principals as issuer

from scripts.issue_dev_paper_principals import (
    CONSUMER_FILES, PAPER_SCOPE, PAPER_SUBJECT, READERS, WRITERS, TTL_SECONDS, issue_environment, write_environment,
)
from services.runtime_auth_inbound import AuthError, _verify_jwt_hs256, validate_request_auth

claims_roles = lambda role: [role] if isinstance(role, str) else list(role)
NOW = 2_000_000_000
KEY = "synthetic-dev-principal-unit-key-" * 2


def configured():
    return {
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        "PANTHEON_DEV_BFF_JWT_SECRET": KEY,
        "PANTHEON_DEV_BFF_JWT_ISSUER": "isolated-issuer",
        "PANTHEON_DEV_BFF_JWT_AUDIENCE": "isolated-audience",
    }


def verify(token, **overrides):
    values = dict(secret=KEY, issuer="isolated-issuer", audience="isolated-audience", now=NOW + 1)
    values.update(overrides)
    return _verify_jwt_hs256(token, **values)


def test_fixed_product_profiles_are_tenant_scoped_short_lived_and_separate():
    values = issue_environment(configured(), now=NOW)
    ids = set()
    for variable, (subject, role) in READERS.items():
        claims = verify(values[variable])
        assert claims["sub"] == subject
        assert claims["roles"] == ([role] if isinstance(role, str) else list(role))
        assert claims["tenant_id"] == "tenant-dev"
        assert claims["allowed_tenants"] == ["tenant-dev"]
        assert claims["exp"] - claims["iat"] == TTL_SECONDS
        assert claims["scope"] == "pantheon:dev-owner-read"
        ids.add(claims["jti"])
    for variable, (subject, role, scope) in WRITERS.items():
        claims = verify(values[variable])
        assert claims["sub"] == subject
        assert claims["roles"] == [role]
        assert claims["tenant_id"] == "tenant-dev"
        assert claims["allowed_tenants"] == ["tenant-dev"]
        assert claims["exp"] - claims["iat"] == TTL_SECONDS
        assert claims["scope"] == scope
        # RESEARCH_BFF_SERVICE_TOKEN and PERSONA_ALLOCATION_TOKEN are the operator-role paper BFF principals.
        blocked = {"admin", "risk_owner", "operator", "approval_reader", "automated_gate", "runtime", "capital"}
        if variable in {"RESEARCH_BFF_SERVICE_TOKEN", "PERSONA_ALLOCATION_TOKEN"}:
            blocked.discard("operator")
        assert not blocked.intersection(claims["roles"])
        ids.add(claims["jti"])
    assert len(ids) == len(READERS) + len(WRITERS)
    writer = verify(values["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"])
    assert writer["sub"] == PAPER_SUBJECT
    assert writer["roles"] == ["automated_gate"]
    assert writer["scope"] == PAPER_SCOPE
    assert writer["tenant_id"] == "tenant-dev"
    assert not {"admin", "risk_owner", "operator"}.intersection(writer["roles"])


@pytest.mark.parametrize("field,value", [
    ("PANTHEON_ENV", "production"), ("PANTHEON_ENV", "staging"),
    ("PANTHEON_DEV_BFF_TENANT_ID", "other"),
    ("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED", "false"),
    ("PANTHEON_DEV_BFF_JWT_SECRET", "short"),
    ("PANTHEON_DEV_BFF_JWT_ISSUER", ""), ("PANTHEON_DEV_BFF_JWT_AUDIENCE", ""),
])
def test_missing_authority_or_wrong_environment_fails_closed(field, value):
    with pytest.raises(ValueError):
        issue_environment({**configured(), field: value}, now=NOW)


@pytest.mark.parametrize("overrides", [
    {"issuer": "foreign"}, {"audience": "foreign"}, {"secret": "wrong-key"},
    {"now": NOW + TTL_SECONDS + 1}, {"now": NOW - 100},
])
def test_real_verifier_rejects_wrong_or_expired_token(overrides):
    token = issue_environment(configured(), now=NOW)["DEPLOYMENT_REGISTRY_SERVICE_TOKEN"]
    with pytest.raises(AuthError):
        verify(token, **overrides)


@pytest.mark.parametrize("variable", READERS)
def test_readers_do_not_have_product_write_roles(variable):
    token = issue_environment(configured())[variable]
    env = {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict", "PANTHEON_RUNTIME_JWT_SECRET": KEY,
        "PANTHEON_RUNTIME_JWT_ISSUER": "isolated-issuer",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "isolated-audience",
    }
    context = validate_request_auth(authorization="Bearer " + token,
                                    required_roles=tuple(claims_roles(READERS[variable][1])), env=env)
    assert context.actor_id == READERS[variable][0]
    with pytest.raises(AuthError) as rejected:
        validate_request_auth(authorization="Bearer " + token, mfa_header=None,
                              required_roles=("automated_gate", "admin", "operator", "registry-writer", "approval_decider"),
                              mfa_required=False, env=env)
    assert rejected.value.status_code == 403


@pytest.mark.parametrize("variable", [v for v in WRITERS if v not in {"RESEARCH_BFF_SERVICE_TOKEN", "PERSONA_ALLOCATION_TOKEN"}])
def test_writers_do_not_have_admin_operator_or_approval_roles(variable):
    token = issue_environment(configured())[variable]
    env = {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict", "PANTHEON_RUNTIME_JWT_SECRET": KEY,
        "PANTHEON_RUNTIME_JWT_ISSUER": "isolated-issuer",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "isolated-audience",
    }
    subject, role, scope = WRITERS[variable]
    context = validate_request_auth(authorization="Bearer " + token,
                                    required_roles=(role,), env=env)
    assert context.actor_id == subject
    with pytest.raises(AuthError) as rejected:
        validate_request_auth(authorization="Bearer " + token, mfa_header=None,
                              required_roles=("automated_gate", "admin", "operator", "approval_reader"),
                              mfa_required=False, env=env)
    assert rejected.value.status_code == 403


def test_private_file_never_overwrites_or_follows_existing_target(tmp_path):
    path = tmp_path / "principals.env"
    values = issue_environment(configured(), now=NOW)
    write_environment(path, values)
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_environment(path, values)
    assert path.read_bytes() == original
    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        write_environment(link, values)
    assert path.read_bytes() == original


def test_cli_outputs_no_secrets(tmp_path):
    path = tmp_path / "generated.env"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("issue_dev_paper_principals.py")),
         "--output-env", str(path)],
        env={**os.environ, **configured(),
             "PANTHEON_PERSONA_TOOL_BROKER_TOKEN_FILE": str(tmp_path / "broker.token")},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert KEY not in result.stdout + result.stderr
    assert "eyJ" not in result.stdout + result.stderr
    assert "values withheld" in result.stdout
    assert path.stat().st_mode & 0o777 == 0o600


def test_deploy_and_compose_connect_authorized_environment_without_embedded_tokens():
    import yaml
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/deploy_nonprod_vm.sh").read_text()
    assert 'DEV_PAPER_PRINCIPALS_AUTHORIZED="${DEV_PAPER_PRINCIPALS_AUTHORIZED:-false}"' in script
    assert "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED=$(shell_quote" in script
    assert "PANTHEON_ENV=dev python3 scripts/issue_dev_paper_principals.py" in script
    root_case = script.split('case "${PANTHEON_DEPLOY_COMPONENT}" in', 1)[1].split("  bff)", 1)[0]
    assert root_case.index("prepare_deploy_worktree") < root_case.index("prepare_dev_paper_principals")
    assert root_case.index("prepare_dev_paper_principals") < root_case.index("docker-compose.yml build")
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    for service in ("governance", "deployment", "registry", "runtime-manager"):
        assert compose[service]["environment"]["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"
    assert "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN" in compose["operator-bff"]["environment"]
    assert "GOVERNANCE_REGISTRY_SERVICE_TOKEN" in compose["governance"]["environment"]
    assert "DISTILLATION_REGISTRY_SERVICE_TOKEN" in compose["strategy-distillation-worker"]["environment"]
    assert "DISTILLATION_REGISTRY_SERVICE_TOKEN_FILE" in compose["strategy-distillation-worker"]["environment"]
    assert "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN" in compose["alpha-replication-worker"]["environment"]
    assert "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN_FILE" in compose["alpha-replication-worker"]["environment"]
    assert "TRAINING_SESSION_WORKER_TOKEN_FILE" in compose["alpha-replication-worker"]["environment"]
    assert "RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN" in compose["runtime-manager"]["environment"]
    assert "RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN_FILE" in compose["runtime-manager"]["environment"]
    assert "CAPITAL_ALLOWED_READER_SERVICES" in compose["capital"]["environment"]


def test_distillation_research_reader_is_dedicated_and_mounted():
    import yaml
    variable = "DISTILLATION_RESEARCH_SERVICE_TOKEN"
    claims = verify(issue_environment(configured(), now=NOW)[variable])
    assert claims["sub"] == "strategy-distillation-worker"
    assert claims["roles"] == ["viewer"] and claims["scope"] == "pantheon:dev-owner-read"
    assert variable in CONSUMER_FILES["strategy-distillation-worker"]
    assert all(variable not in variables for consumer, variables in CONSUMER_FILES.items()
               if consumer != "strategy-distillation-worker")
    compose = yaml.safe_load((Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text())
    env = compose["services"]["strategy-distillation-worker"]["environment"]
    assert env["DISTILLATION_RESEARCH_URL"] == "http://research-orchestrator-svc:8101"
    assert "/run/pantheon-principals/" + variable in env[variable + "_FILE"]
    assert env["PANTHEON_L2_EXTRACTION_DAILY_CALL_CAP"] == "${PANTHEON_L2_EXTRACTION_DAILY_CALL_CAP:-400}"


def test_training_session_source_reader_principal_is_dedicated_and_mounted():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "TRAINING_SESSION_SOURCE_READ_TOKEN"
    authority = "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN"
    assert CONSUMER_FILES["training-session-svc"] == (variable, authority)
    issued = verify(issue_environment(configured(), now=NOW)[authority])
    assert issued["roles"] == ["persona.admin", "approval_reader"]
    assert issued["allowed_tenants"] == ["tenant-dev"]
    claims = verify(issue_environment(configured(), now=NOW)[variable])
    assert claims["roles"] == ["source_ingest_reader"]
    assert claims["tenant_id"] == "tenant-dev"
    assert claims["allowed_tenants"] == ["tenant-dev"]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    training = compose["services"]["training-session-svc"]
    assert "dev-paper-training-session-tokens:/run/pantheon-principals:ro" in training["volumes"]
    assert training["environment"]["TRAINING_SESSION_SOURCE_READ_TOKEN_FILE"].endswith(variable + "}")
    assert training["environment"][authority + "_FILE"].endswith("/" + authority + "}")
    assert authority not in training["environment"]
    issuer = compose["services"]["dev-paper-principal-issuer"]["volumes"]
    assert "dev-paper-training-session-tokens:/issued/training-session-svc" in issuer


def test_training_preview_worker_principal_is_dedicated_exact_and_mounted():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "TRAINING_SESSION_WORKER_TOKEN"
    assert CONSUMER_FILES["training-session-preview-worker"] == (variable,)
    claims = verify(issue_environment(configured(), now=NOW)[variable])
    assert claims["sub"] == claims["service"] == "training-session-preview-worker"
    assert claims["roles"] == ["training-service"]
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert claims["exp"] - claims["iat"] <= TTL_SECONDS
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    worker = compose["services"]["training-session-preview-worker"]
    assert "dev-paper-training-worker-tokens:/run/pantheon-principals:ro" in worker["volumes"]
    assert worker["environment"][variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert variable not in worker["environment"]  # no published fixture token
    assert "dev-paper-training-worker-tokens:/issued/training-session-preview-worker" in (
        compose["services"]["dev-paper-principal-issuer"]["volumes"])


def test_bff_health_telemetry_principal_is_exact_and_mounted_for_operator_bff():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "PANTHEON_BFF_HEALTH_TELEMETRY_JWT"
    assert variable in CONSUMER_FILES["operator-bff"]
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == claims["service"] == "control-plane-bff-health-monitor"
    assert claims["roles"] == ["service"]
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert claims["allowed_producers"] == ["control-plane-bff"]
    assert 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    bff = compose["services"]["operator-bff"]
    assert "dev-paper-bff-tokens:/run/pantheon-principals:ro" in bff["volumes"]
    assert bff["environment"][variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert variable not in bff["environment"]  # no published fixture token


def _telemetry_client():
    """Real telemetry infrastructure-health authority behind a minimal route."""
    from flask import Flask, jsonify, request
    from services.telemetry.auth import (
        TelemetryAuthorityError, bind_event_producer, request_authority, require_infrastructure_health_authority,
    )

    app = Flask(__name__)

    @app.post("/ingest")
    @require_infrastructure_health_authority()
    def ingest():
        try:
            event = bind_event_producer(request.get_json(), request_authority())
        except TelemetryAuthorityError as exc:
            payload, status = exc.as_response()
            return jsonify(payload), status
        return jsonify({"producer": event["producer"]}), 202

    return app.test_client()


def _post_health(monkeypatch, token, *, tenant="tenant-dev", producer="control-plane-bff", secret=KEY):
    monkeypatch.setenv("PANTHEON_TELEMETRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_TELEMETRY_JWT_SECRET", secret)  # compose: falls back to the dev signer
    monkeypatch.setenv("PANTHEON_TELEMETRY_INFRA_PRODUCERS", "control-plane-bff")
    for name in ("PANTHEON_TELEMETRY_JWT_ISSUER", "PANTHEON_TELEMETRY_JWT_AUDIENCE"):
        monkeypatch.delenv(name, raising=False)
    return _telemetry_client().post(
        "/ingest", json={"producer": producer},
        headers={"Authorization": "Bearer " + token, "X-Tenant-Id": tenant})


def test_telemetry_ingest_accepts_issued_health_principal_and_rejects_others(monkeypatch):
    token = issue_environment(configured())["PANTHEON_BFF_HEALTH_TELEMETRY_JWT"]
    accepted = _post_health(monkeypatch, token)
    assert accepted.status_code == 202 and accepted.get_json() == {"producer": "control-plane-bff"}
    # Wrong signer: telemetry verifies a different secret than the issuer signed with.
    assert _post_health(monkeypatch, token, secret="a-different-signer-" * 3).status_code == 401
    # Foreign tenant and wrong producer are refused with the correct signer.
    foreign = _post_health(monkeypatch, token, tenant="tenant-other")
    assert foreign.status_code == 403 and foreign.get_json()["error"]["code"] == "TENANT_FORBIDDEN"
    producer = _post_health(monkeypatch, token, producer="rogue-probe-agent")
    assert producer.status_code == 403 and producer.get_json()["error"]["code"] == "PRODUCER_FORBIDDEN"


def test_agora_projector_principal_is_exact_and_mounted_for_projector():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "AGORA_PROJECTOR_SERVICE_JWT"
    assert CONSUMER_FILES["source-ingest-agora-projector"] == (variable,)
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == claims["service"] == "pantheon-dev-agora-projector-reader"
    assert claims["roles"] == ["source_ingest_reader"]
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert claims["scope"] == "pantheon:dev-owner-read"
    assert 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    projector = compose["services"]["source-ingest-agora-projector"]
    assert "dev-paper-agora-projector-tokens:/run/pantheon-principals:ro" in projector["volumes"]
    assert projector["environment"][variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert "dev-paper-agora-projector-tokens:/issued/source-ingest-agora-projector" in (
        compose["services"]["dev-paper-principal-issuer"]["volumes"]
    )
    assert "dev-paper-agora-projector-tokens" in compose["volumes"]


def test_alpha_replication_worker_receives_training_principal_and_compose_mounts():
    import yaml
    root = Path(__file__).resolve().parents[1]
    registry_var = "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN"
    training_var = "TRAINING_SESSION_WORKER_TOKEN"
    assert CONSUMER_FILES["alpha-replication-worker"][:2] == (registry_var, training_var)
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[training_var])
    assert claims["sub"] == claims["service"] == "training-session-preview-worker"
    assert claims["roles"] == ["training-service"]
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert claims["exp"] - claims["iat"] <= TTL_SECONDS
    assert values[training_var + "_FILE"] == "/run/pantheon-principals/" + training_var
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    worker = compose["services"]["alpha-replication-worker"]
    assert "dev-paper-alpha-replication-tokens:/run/pantheon-principals:ro" in worker["volumes"]
    assert worker["environment"]["TRAINING_SESSION_WORKER_TOKEN_FILE"].endswith(
        "/run/pantheon-principals/" + training_var + "}"
    )
    assert training_var not in worker["environment"]  # no published fixture token
    assert "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN" not in worker["environment"]
    assert "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN_FILE" not in worker["environment"]
    issuer_volumes = compose["services"]["dev-paper-principal-issuer"]["volumes"]
    assert "dev-paper-alpha-replication-tokens:/issued/alpha-replication-worker" in issuer_volumes
    assert "dev-paper-alpha-replication-tokens" in compose["volumes"]




def test_persona_evaluator_bff_reader_has_viewer_and_registry_read_only():
    values = issue_environment(configured(), now=NOW)
    for variable in ("PERSONA_EVALUATOR_BFF_TOKEN", "PERSONA_RANKING_TOKEN"):
        claims = verify(values[variable])
        assert claims["sub"] == claims["service"] == "pantheon-dev-persona-evaluator-bff-reader"
        assert claims["roles"] == ["viewer", "registry-reader"]
        assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
        assert claims["scope"] == "pantheon:dev-owner-read"
        assert 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS
        assert not {"admin", "operator", "registry-writer", "capital", "runtime", "automated_gate", "approval_decider"}.intersection(claims["roles"])
    alloc_claims = verify(values["PERSONA_ALLOCATION_TOKEN"])
    assert alloc_claims["sub"] == alloc_claims["service"] == "pantheon-dev-persona-evaluator-allocation"
    assert alloc_claims["azp"] == "control-plane-bff"
    assert alloc_claims["roles"] == ["operator"]
    assert alloc_claims["scope"] == "pantheon:dev-owner-write"
    assert alloc_claims["tenant_id"] == "tenant-dev" and alloc_claims["allowed_tenants"] == ["tenant-dev"]
    assert not {"admin", "registry-writer", "capital", "runtime", "automated_gate", "approval_decider"}.intersection(alloc_claims["roles"])


def test_evolution_owner_read_principals_are_reader_only_and_file_delivered():
    values = issue_environment(configured(), now=NOW)
    expected = {
        "EVOLUTION_REGISTRY_SERVICE_TOKEN": ["registry-reader"],
        "EVOLUTION_PERSONA_SERVICE_TOKEN": ["viewer"],
    }
    for variable, roles in expected.items():
        claims = verify(values[variable])
        assert claims["roles"] == roles
        assert claims["scope"] == "pantheon:dev-owner-read"
        assert claims["tenant_id"] == "tenant-dev"
        assert variable in CONSUMER_FILES["evolution"]
        assert values[variable + "_FILE"] == f"/run/pantheon-principals/{variable}"
        assert not {"registry-writer", "approval_proposer", "approval_decider", "admin", "operator"}.intersection(claims["roles"])


def test_evolution_execute_proposer_is_propose_only_and_mounted_for_evolution():
    variable = "EVOLUTION_EXECUTE_PROPOSER_TOKEN"
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == claims["service"] == "evolution-execute-proposer"
    assert claims["roles"] == ["approval_proposer"]
    assert claims["scope"] == "pantheon:dev-owner-write"
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS
    # No decide/revoke/read-write authority: a human must decide, never the proposer.
    assert not {"admin", "operator", "approver", "risk_owner", "governance_reviewer",
                "governance_committee", "approval_reader", "registry-writer", "automated_gate"}.intersection(claims["roles"])
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    from scripts import issue_dev_paper_principals as issuer
    assert variable in issuer.CONSUMER_FILES["evolution"]
    assert not any(variable in names for consumer, names in issuer.CONSUMER_FILES.items() if consumer != "evolution")


def test_research_retrain_executor_principals_are_exact_and_mounted_read_only():
    import yaml
    root = Path(__file__).resolve().parents[1]
    registry_var = "RESEARCH_REGISTRY_SERVICE_TOKEN"
    training_var = "RESEARCH_TRAINING_SERVICE_TOKEN"
    bff_var = "RESEARCH_BFF_SERVICE_TOKEN"
    # The original five are unchanged and in order; RC2 appends exactly the five read-only case-file principals
    # (asserted in test_research_case_file_principals_are_read_only_file_delivered_and_mounted_to_research_only).
    assert CONSUMER_FILES["research-orchestrator-svc"] == (
        registry_var, training_var, bff_var,
        "RESEARCH_PERSONA_SERVICE_TOKEN", "RESEARCH_CAPITAL_SERVICE_TOKEN",
        "RESEARCH_CASE_FILE_MEMORY_READ_TOKEN", "RESEARCH_CASE_FILE_TELEMETRY_READ_TOKEN",
        "RESEARCH_CASE_FILE_REGISTRY_READ_TOKEN", "RESEARCH_CASE_FILE_PERSONA_POOL_READ_TOKEN",
        "RESEARCH_CASE_FILE_SEARCH_READ_TOKEN",
    )
    values = issue_environment(configured(), now=NOW)

    registry = verify(values[registry_var])
    assert registry["roles"] == ["registry-writer"]
    assert registry["scope"] == "pantheon:dev-owner-write"
    assert registry["tenant_id"] == "tenant-dev" and registry["allowed_tenants"] == ["tenant-dev"]

    training = verify(values[training_var])
    # The subject is the X-Pantheon-Service the executor sends to Training.
    assert training["sub"] == training["service"] == "research-orchestrator"
    assert training["roles"] == ["training-service"]
    assert training["tenant_id"] == "tenant-dev" and training["allowed_tenants"] == ["tenant-dev"]
    for claims in (registry, training):
        assert claims["exp"] - claims["iat"] <= TTL_SECONDS
        forbidden = {"decide", "persona.admin", "approver", "approval_proposer", "approval_reader", "operator", "admin"}
        assert not forbidden & set(claims["roles"])
    bff = verify(values[bff_var])
    assert bff["sub"] == bff["service"] == "research-orchestrator"
    assert bff["roles"] == ["operator"] and bff["scope"] == "pantheon:dev-owner-write"
    assert bff["tenant_id"] == "tenant-dev" and bff["allowed_tenants"] == ["tenant-dev"]
    assert bff["exp"] - bff["iat"] <= TTL_SECONDS
    assert not {"admin", "approver", "decide", "persona.admin", "automated_gate"} & set(bff["roles"])
    for var, role in (("RESEARCH_PERSONA_SERVICE_TOKEN", "viewer"), ("RESEARCH_CAPITAL_SERVICE_TOKEN", "capital-reader")):
        c = verify(values[var])
        assert c["roles"] == [role] and c["scope"] == "pantheon:dev-owner-read"
        assert c["tenant_id"] == "tenant-dev" and c["sub"] == "research-orchestrator"
        assert values[var + "_FILE"] == "/run/pantheon-principals/" + var
    assert values[bff_var + "_FILE"] == "/run/pantheon-principals/" + bff_var
    assert values[registry_var + "_FILE"] == "/run/pantheon-principals/" + registry_var
    assert values[training_var + "_FILE"] == "/run/pantheon-principals/" + training_var

    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    research = compose["services"]["research-orchestrator-svc"]
    assert "dev-paper-research-tokens:/run/pantheon-principals:ro" in research["volumes"]
    for variable in (registry_var, training_var, bff_var):
        assert research["environment"][variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
        assert variable not in research["environment"]  # no published fixture token
    assert "dev-paper-research-tokens:/issued/research-orchestrator-svc" in (
        compose["services"]["dev-paper-principal-issuer"]["volumes"])
    assert "dev-paper-research-tokens" in compose["volumes"]
    training_env = compose["services"]["training-session-svc"]["environment"]
    assert "research-orchestrator" in training_env["TRAINING_SESSION_ALLOWED_CALLER_SERVICES"].split(":-", 1)[1].rstrip("}").split(",")


def test_persona_cycle_owner_port_principals_are_dedicated_least_privilege_and_mounted():
    values = issue_environment(configured(), now=NOW)
    expected_persona_files = {
        "PERSONA_GOVERNANCE_SERVICE_TOKEN",
        "PERSONA_RESEARCH_SERVICE_TOKEN",
        "PERSONA_POOL_SOURCE_READ_TOKEN",
        "PERSONA_REGISTRY_SERVICE_TOKEN",
        "PERSONA_MEMORY_SERVICE_TOKEN",
        "PERSONA_MEMORY_WRITE_TOKEN",
        "LESSON_RULE_PROPOSER_TOKEN",
        "PERSONA_SEARCH_SERVICE_TOKEN",
        "PERSONA_POOL_PUBLISH_BFF_TOKEN",
    }
    assert set(CONSUMER_FILES["persona"]) == expected_persona_files
    jti_by_var = {}
    for var in expected_persona_files:
        claims = verify(values[var])
        assert claims["tenant_id"] == "tenant-dev"
        assert claims["allowed_tenants"] == ["tenant-dev"]
        assert 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS
        assert values[var + "_FILE"] == f"/run/pantheon-principals/{var}"
        jti_by_var[var] = claims["jti"]
    # No credential is shared across owners (5 JWT owner principals + static evolution & consultation service tokens).
    assert len(set(jti_by_var.values())) == len(expected_persona_files)
    assert values["EVOLUTION_AUTH_TOKEN"] == "pantheon-local-evolution-service"
    assert values["CONSULTATION_SERVICE_TOKEN"] == "pantheon-local-consultation-service"
    assert values["EVOLUTION_AUTH_TOKEN"] != values["CONSULTATION_SERVICE_TOKEN"]

    search_claims = verify(values["PERSONA_SEARCH_SERVICE_TOKEN"])
    assert search_claims["sub"] == "persona_cycle_controller"
    assert search_claims["roles"] == ["researcher", "persona_session"]
    assert search_claims["access_scopes"] == ["operator", "research", "public"]

    memory_claims = verify(values["PERSONA_MEMORY_SERVICE_TOKEN"])
    assert memory_claims["sub"] == "persona_cycle_controller"
    assert memory_claims["roles"] == ["persona_session"]
    assert memory_claims["scope"] == "pantheon:dev-owner-write"

    write_claims = verify(values["PERSONA_MEMORY_WRITE_TOKEN"])
    assert write_claims["sub"] == "persona-svc"
    assert write_claims["roles"] == ["persona-svc"]
    assert "persona_session" not in write_claims["roles"]

    proposer_claims = verify(values["LESSON_RULE_PROPOSER_TOKEN"])
    assert proposer_claims["sub"] == "persona-svc"
    assert proposer_claims["roles"] == ["approval_proposer"]
    assert proposer_claims["scope"] == "pantheon:dev-owner-write"



def test_enrollment_proposer_is_distinct_propose_only_and_withdrawn_on_revoke():
    variable = "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"
    subject, role, scope = issuer.WRITERS[variable]
    assert subject != issuer.PAPER_SUBJECT and role == "approval_proposer"
    assert scope == "pantheon:dev-owner-write"
    assert variable in issuer.CONSUMER_FILES["operator-bff"]
    revoked = issuer.revoked_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false",
    })
    assert revoked["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID"] == "" and revoked[variable] == ""


CARD_VARIABLES = (
    "ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN", "ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN",
    "RESEARCH_REGISTRY_SERVICE_TOKEN", "RESEARCH_GOVERNANCE_PROPOSER_TOKEN", "RESEARCH_GOVERNANCE_GATE_TOKEN",
)


def test_card_admission_principals_are_distinct_propose_only_and_withdrawn_on_revoke():
    variable = "RESEARCH_GOVERNANCE_PROPOSER_TOKEN"
    subject, role, scope = issuer.WRITERS[variable]
    assert subject == issuer.CARD_PROPOSER_SUBJECT
    assert subject not in (issuer.PAPER_SUBJECT, issuer.ENROLLMENT_PROPOSER_SUBJECT)
    assert role == "approval_proposer" and scope == "pantheon:dev-owner-write"
    assert set(CARD_VARIABLES) <= set(issuer.CONSUMER_FILES["alpha-replication-worker"])
    values = issue_environment(configured(), now=NOW)
    gate = verify(values["RESEARCH_GOVERNANCE_GATE_TOKEN"])
    assert gate["sub"] == issuer.PAPER_SUBJECT and gate["roles"] == ["automated_gate"]
    proposer = verify(values[variable])
    assert proposer["sub"] == issuer.CARD_PROPOSER_SUBJECT and proposer["roles"] == ["approval_proposer"]
    assert values["RESEARCH_GOVERNANCE_PROPOSER_ID"] == issuer.CARD_PROPOSER_SUBJECT
    assert values["RESEARCH_GOVERNANCE_GATE_ID"] == issuer.PAPER_SUBJECT
    for name in CARD_VARIABLES:
        assert values[name + "_FILE"] == "/run/pantheon-principals/" + name
    for name in ("ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN", "ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN"):
        assert verify(values[name])["roles"] == ["viewer"]
    revoked = issuer.revoked_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false",
    })
    assert revoked["RESEARCH_GOVERNANCE_PROPOSER_ID"] == "" and revoked["RESEARCH_GOVERNANCE_GATE_ID"] == ""
    assert all(revoked[name] == "" for name in CARD_VARIABLES)


def test_grant_content_carries_both_explicit_widenings_the_issued_proposers_match():
    from services.governance.paper_approval_scope import _grant_widenings
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "grant"
        path.write_text(issuer.GOVERNANCE_GRANT_CONTENT)
        path.chmod(0o600)
        lifecycle, card = _grant_widenings({"GOVERNANCE_DEV_PAPER_GRANT_FILE": str(path)})
        from services.governance.paper_approval_scope import configured_risk_policy_grant
        risk = configured_risk_policy_grant({"GOVERNANCE_DEV_PAPER_GRANT_FILE": str(path)})
    assert risk.proposer_subject == issuer.ENROLLMENT_PROPOSER_SUBJECT
    assert lifecycle.proposer_subject == issuer.ENROLLMENT_PROPOSER_SUBJECT
    assert card.proposer_subject == issuer.CARD_PROPOSER_SUBJECT


def test_governance_binding_readers_are_distinct_read_only_and_least_privilege():
    from scripts import issue_dev_paper_principals as issuer
    values = issue_environment(configured(), now=NOW)
    capital_var, runtime_var = "GOVERNANCE_CAPITAL_SERVICE_TOKEN", "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN"
    capital, runtime = verify(values[capital_var]), verify(values[runtime_var])
    assert capital["roles"] == ["capital-reader"] and runtime["roles"] == ["viewer"]
    assert capital["scope"] == runtime["scope"] == "pantheon:dev-owner-read"
    assert capital["sub"] == issuer.GOVERNANCE_CAPITAL_READER_SUBJECT
    assert runtime["sub"] == issuer.GOVERNANCE_RUNTIME_READER_SUBJECT
    assert capital["sub"] != runtime["sub"] != issuer.PAPER_SUBJECT
    assert values[capital_var] != values[runtime_var]
    for claims in (capital, runtime):
        assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
        assert not {"admin", "operator", "approver", "risk_owner", "automated_gate", "approval_proposer",
                    "registry-writer", "capital.admin", "persona.admin"}.intersection(claims["roles"])
    assert values["GOVERNANCE_CAPITAL_BASE_URL"] == "http://capital:8092"
    assert values["GOVERNANCE_RUNTIME_MANAGER_BASE_URL"] == "http://runtime-manager:8081"
    for variable in (capital_var, runtime_var):
        assert variable in issuer.CONSUMER_FILES["governance"]
        assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
        assert variable not in issuer.WRITERS


def test_broker_environment_is_persistent_private_and_dev_only(tmp_path):
    from scripts.issue_dev_paper_principals import broker_environment
    env = {"PANTHEON_ENV": "dev", "PANTHEON_PERSONA_TOOL_BROKER_TOKEN_FILE": str(tmp_path / "s" / "t")}
    first = broker_environment(env)
    assert first["PERSONA_TOOL_BROKER_ENABLED"] == "true"
    assert len(first["PERSONA_TOOL_BROKER_TOKEN"]) >= 32
    assert broker_environment(env) == first
    assert (tmp_path / "s" / "t").stat().st_mode & 0o077 == 0
    import pytest
    with pytest.raises(ValueError):
        broker_environment({**env, "PANTHEON_ENV": "prod"})


def test_cli_output_env_enables_broker_without_printing_token(tmp_path, monkeypatch, capsys):
    from scripts import issue_dev_paper_principals as m
    monkeypatch.setattr(m, "issue_environment", lambda env: {"X": "1"})
    for k, v in {"PANTHEON_ENV": "dev", "PANTHEON_PERSONA_TOOL_BROKER_TOKEN_FILE": str(tmp_path / "t")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED", "true")
    monkeypatch.setattr("sys.argv", ["x", "--output-env", str(tmp_path / "o.env")])
    assert m.main() == 0
    out = (tmp_path / "o.env").read_text()
    token = (tmp_path / "t").read_text()
    assert "export PERSONA_TOOL_BROKER_ENABLED=true" in out and token in out
    assert token not in capsys.readouterr().out


def test_openclaw_adapter_memory_principal_is_read_only_file_delivered_and_mounted():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "OPENCLAW_ADAPTER_MEMORY_TOKEN"
    assert CONSUMER_FILES["openclaw-gateway-adapter"] == (variable,)
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items()
               if consumer != "openclaw-gateway-adapter")
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == "openclaw-persona-sync" and claims["roles"] == ["persona_memory_reader"]
    assert claims["scope"] == "pantheon:dev-owner-read" and claims["tenant_id"] == "tenant-dev"
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    adapter = compose["services"]["openclaw-gateway-adapter"]
    assert "dev-paper-openclaw-adapter-tokens:/run/pantheon-principals:ro" in adapter["volumes"]
    env = adapter["environment"]
    assert env["OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert env["PANTHEON_OPENCLAW_MEMORY_ACTOR_ID"].endswith(":-" + claims["sub"] + "}")
    assert env["PANTHEON_OPENCLAW_MEMORY_ACTOR_ROLES"].endswith(":-persona_memory_reader}")
    assert "dev-paper-openclaw-adapter-tokens:/issued/openclaw-gateway-adapter" in (
        compose["services"]["dev-paper-principal-issuer"]["volumes"]
    )
    assert "dev-paper-openclaw-adapter-tokens" in compose["volumes"]


def test_broker_token_survives_compensation_and_baseline_restore(tmp_path, monkeypatch):
    """AC3: the deploy-time env path is re-derived from a host-persistent file.

    A compensation / baseline restore recreates containers and the per-deploy
    principals.env; the broker token must come back identical, and the adapter and
    gateway must resolve the very same value from it.
    """
    import re
    from scripts import issue_dev_paper_principals as m
    secret = tmp_path / "secrets" / "broker.token"
    monkeypatch.setattr(m, "issue_environment", lambda env: {"X": "1"})
    for k, v in {"PANTHEON_ENV": "dev", "PANTHEON_PERSONA_TOOL_BROKER_TOKEN_FILE": str(secret),
                 "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true"}.items():
        monkeypatch.setenv(k, v)

    def deploy(name):
        out = tmp_path / name / "principals.env"  # fresh private dir per deploy, deleted after sourcing
        out.parent.mkdir()
        monkeypatch.setattr("sys.argv", ["x", "--output-env", str(out)])
        assert m.main() == 0
        values = dict(re.findall(r"export (\w+)=(\S+)", out.read_text()))
        out.unlink()
        out.parent.rmdir()
        return values

    first = deploy("initial")
    compensated = deploy("after-compensation")  # principals.env gone, containers recreated
    restored = deploy("after-baseline-restore")
    assert first["PERSONA_TOOL_BROKER_ENABLED"] == "true"
    assert len(first["PERSONA_TOOL_BROKER_TOKEN"]) >= 32
    assert compensated["PERSONA_TOOL_BROKER_TOKEN"] == first["PERSONA_TOOL_BROKER_TOKEN"]
    assert restored["PERSONA_TOOL_BROKER_TOKEN"] == first["PERSONA_TOOL_BROKER_TOKEN"]
    assert secret.read_text().strip() == first["PERSONA_TOOL_BROKER_TOKEN"]


def test_persona_pool_publish_bff_principal_is_least_privilege_file_delivered_and_mounted():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "PERSONA_POOL_PUBLISH_BFF_TOKEN"
    assert variable in CONSUMER_FILES["persona"]
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items() if consumer != "persona")
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == "persona-pool-cycle" and claims["roles"] == ["persona_pool_publisher"]
    assert claims["scope"] == "pantheon:dev-owner-read"
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    persona = compose["services"]["persona"]
    assert "dev-paper-persona-tokens:/run/pantheon-principals:ro" in persona["volumes"]
    assert persona["environment"][variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert variable not in persona["environment"]  # never an env value, so it cannot appear in env dumps


def test_persona_registry_principal_is_reader_plus_regime_evaluator_without_write_role():
    values = issue_environment(configured(), now=NOW)
    claims = verify(values["PERSONA_REGISTRY_SERVICE_TOKEN"])
    assert claims["roles"] == ["registry-reader", "regime-evaluator"]
    assert not {"operator", "registry-writer", "admin"}.intersection(claims["roles"])


def test_consultation_persona_pool_read_principal_is_pool_get_only_file_delivered_and_mounted():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "CONSULTATION_PERSONA_POOL_READ_TOKEN"
    assert variable in CONSUMER_FILES["consultation-svc"]
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items() if consumer != "consultation-svc")
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == "pantheon-dev-consultation-persona-pool-reader"
    assert claims["roles"] == ["persona_pool_reader"] and claims["scope"] == "pantheon:dev-owner-read"
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    # PANTHEON_PERSONA_SERVICE_TOKEN is the persona owner's own service-token name: never exported.
    assert "PANTHEON_PERSONA_SERVICE_TOKEN" not in values
    revoked = issuer.revoked_environment({**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})
    assert revoked[variable] == ""
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    consultation = compose["services"]["consultation-svc"]
    assert "dev-paper-consultation-tokens:/run/pantheon-principals:ro" in consultation["volumes"]
    assert consultation["environment"]["PANTHEON_PERSONA_SERVICE_TOKEN_FILE"].endswith(
        "/run/pantheon-principals/" + variable + "}")
    assert "dev-paper-consultation-tokens:/issued/consultation-svc" in (
        compose["services"]["dev-paper-principal-issuer"]["volumes"])
    assert "dev-paper-consultation-tokens" in compose["volumes"]


def test_consultation_persona_directory_principal_is_viewer_only_file_delivered_and_mounted():
    import yaml
    root = Path(__file__).resolve().parents[1]
    variable = "CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN"
    assert variable in CONSUMER_FILES["consultation-svc"]
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items() if consumer != "consultation-svc")
    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["roles"] == ["viewer"]  # existing read-only persona role; nothing broader
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert not {"operator", "admin", "persona.admin", "persona_pool_reader"} & set(claims["roles"])
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    assert issuer.revoked_environment(
        {**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})[variable] == ""
    env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["consultation-svc"]["environment"]
    assert env[variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")


def test_writeback_probe_payload_passes_real_memory_authority_check_and_writes_nothing(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from services.memory import main as memory_main

    now = int(time.time())
    values = issue_environment(configured(), now=now)
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "m.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", KEY)
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_ISSUER", "isolated-issuer")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_AUDIENCE", "isolated-audience")
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None
    client = TestClient(memory_main.app)
    url = "/api/memory/writebacks/learn-feedback"
    auth = {"Authorization": "Bearer " + values["CONSULTATION_MEMORY_WRITE_TOKEN"]}
    probe = {"write_authority": "consultation-svc", "tenant_id": "tenant-dev"}
    assert client.post(url, json={}, headers=auth).status_code == 403  # the old probe
    assert client.post(url, json=probe, headers=auth).status_code == 422  # validation only
    assert client.get("/api/memory/entries", headers=auth).status_code in (200, 403)
    assert not (tmp_path / "m.json").exists() or '"entry_id"' not in (tmp_path / "m.json").read_text()


def test_consultation_memory_principal_writes_learn_feedback_as_consultation_svc_only_and_is_mounted(
        tmp_path, monkeypatch):
    import yaml
    from fastapi.testclient import TestClient
    from services.memory import main as memory_main

    root = Path(__file__).resolve().parents[1]
    variable = "CONSULTATION_MEMORY_WRITE_TOKEN"
    assert variable in CONSUMER_FILES["consultation-svc"]
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items() if consumer != "consultation-svc")
    import time
    now = int(time.time())
    values = issue_environment(configured(), now=now)
    claims = verify(values[variable], now=now + 1)
    # Exactly one role: the write_authority itself. No operator/admin/system/memory-writer.
    assert claims["roles"] == ["consultation-svc"]
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    # Why the hosted credential was refused 403: PANTHEON_MEMORY_AUTH_TOKEN is the persona-svc writer;
    # it passes the write-role gate but holds none of {consultation-svc, operator, admin, system,
    # memory-writer}, so writeback_learn_feedback raises "cannot represent this memory writer".
    assert verify(values["PERSONA_MEMORY_WRITE_TOKEN"], now=now + 1)["roles"] == ["persona-svc"]

    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "m.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", KEY)
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_ISSUER", "isolated-issuer")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_AUDIENCE", "isolated-audience")
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None
    client = TestClient(memory_main.app)
    payload = {
        "tenant_id": "tenant-dev", "source_event_type": "consultation_closed",
        "source_event_id": "consultation:meeting:mtg-x", "write_authority": "consultation-svc",
        "sponsor_persona_id": "persona-a", "contributing_persona_ids": ["persona-a"],
        "evidence_refs": ["consultation://memos/mem-x"], "summary": "Meeting closed.", "headline": "Meeting outcome", "body": "Body.",
    }
    persona = client.post("/api/memory/writebacks/learn-feedback", json=payload,
                          headers={"Authorization": "Bearer " + values["PERSONA_MEMORY_WRITE_TOKEN"]})
    assert persona.status_code == 403
    assert "cannot represent this memory writer" in persona.text
    issued = client.post("/api/memory/writebacks/learn-feedback", json=payload,
                         headers={"Authorization": "Bearer " + values[variable]})
    assert issued.status_code in (200, 201), issued.text
    # The same principal cannot write as another authority.
    other = client.post("/api/memory/writebacks/learn-feedback",
                        json={**payload, "write_authority": "telemetry-svc", "source_event_id": "x"},
                        headers={"Authorization": "Bearer " + values[variable]})
    assert other.status_code == 403

    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    revoked = issuer.revoked_environment({**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})
    assert revoked[variable] == ""
    consultation = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["consultation-svc"]
    assert "dev-paper-consultation-tokens:/run/pantheon-principals:ro" in consultation["volumes"]
    assert consultation["environment"]["PANTHEON_MEMORY_AUTH_TOKEN_FILE"].endswith(
        "/run/pantheon-principals/" + variable + "}")


def test_consultation_memory_read_principal_is_read_only_and_distinct_from_write(tmp_path, monkeypatch):
    import time
    import yaml
    from fastapi.testclient import TestClient
    from services.memory import main as memory_main

    root = Path(__file__).resolve().parents[1]
    read, write = "CONSULTATION_MEMORY_READ_TOKEN", "CONSULTATION_MEMORY_WRITE_TOKEN"
    assert read in CONSUMER_FILES["consultation-svc"]
    assert all(read not in names for c, names in CONSUMER_FILES.items() if c != "consultation-svc")
    now = int(time.time())
    values = issue_environment(configured(), now=now)
    rc, wc = verify(values[read], now=now + 1), verify(values[write], now=now + 1)
    assert rc["roles"] == ["consultation_session"] and rc["sub"] == "pantheon-dev-consultation-memory-reader"
    assert wc["roles"] == ["consultation-svc"]  # write stays write-only
    assert rc["tenant_id"] == "tenant-dev"

    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "m.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", KEY)
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_ISSUER", "isolated-issuer")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_AUDIENCE", "isolated-audience")
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None
    client = TestClient(memory_main.app)
    params = {"actor_id": rc["sub"], "actor_roles": "consultation_session", "session_id": "s1", "scope": "institutional"}
    ok = client.get("/api/memory/retrieve", params=params, headers={"Authorization": "Bearer " + values[read]})
    assert ok.status_code == 200, ok.text
    # The write principal is not a read role; the read principal cannot write.
    assert client.get("/api/memory/retrieve", params={**params, "actor_id": wc["sub"], "actor_roles": "consultation-svc"},
                      headers={"Authorization": "Bearer " + values[write]}).status_code == 403
    payload = {"tenant_id": "tenant-dev", "source_event_type": "consultation_closed", "source_event_id": "x",
               "write_authority": "consultation-svc", "sponsor_persona_id": "persona-a",
               "contributing_persona_ids": ["persona-a"], "evidence_refs": ["consultation://memos/m"],
               "summary": "s", "headline": "h", "body": "b"}
    assert client.post("/api/memory/writebacks/learn-feedback", json=payload,
                       headers={"Authorization": "Bearer " + values[read]}).status_code == 403

    env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["consultation-svc"]["environment"]
    assert env["CONSULTATION_MEMORY_READ_TOKEN_FILE"].endswith("/run/pantheon-principals/" + read + "}")
    assert env["PANTHEON_MEMORY_READ_ACTOR_ID"] == rc["sub"]
    assert env["PANTHEON_MEMORY_READ_ACTOR_ROLES"] == "consultation_session"
    # Hosted-shaped config: nothing in compose leaves the case-file caller empty.
    assert env.get("PANTHEON_MEMORY_ACTOR_ID", "x") and env.get("PANTHEON_MEMORY_ACTOR_ROLES", "x")


def test_consultation_telemetry_read_principal_is_viewer_only_file_delivered_and_mounted(tmp_path, monkeypatch):
    import time
    import types
    import yaml
    from services.telemetry import main as telemetry_main
    from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore

    root = Path(__file__).resolve().parents[1]
    variable = "CONSULTATION_TELEMETRY_READ_TOKEN"
    assert variable in CONSUMER_FILES["consultation-svc"]
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items() if consumer != "consultation-svc")
    now = int(time.time())
    values = issue_environment(configured(), now=now)
    claims = verify(values[variable], now=now + 1)
    assert claims["sub"] == "pantheon-dev-consultation-telemetry-reader"
    assert claims["roles"] == ["viewer", "reviewer"] and claims["scope"] == "pantheon:dev-owner-read"
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert not {"service", "operator", "admin", "automated_gate", "approval_decider", "registry-writer"} & set(claims["roles"])
    assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    assert "PANTHEON_TELEMETRY_SERVICE_TOKEN" not in values
    revoked = issuer.revoked_environment({**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})
    assert revoked[variable] == ""
    env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["consultation-svc"]["environment"]
    assert env[variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert env["PANTHEON_TELEMETRY_SERVICE_TOKEN_FILE"].endswith("/run/pantheon-principals/" + variable + "}")

    # Exercise real services/telemetry/main.py routes in strict mode with the issued principal.
    monkeypatch.setenv("PANTHEON_TELEMETRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_TELEMETRY_JWT_SECRET", KEY)
    monkeypatch.setenv("PANTHEON_TELEMETRY_JWT_ISSUER", "isolated-issuer")
    monkeypatch.setenv("PANTHEON_TELEMETRY_JWT_AUDIENCE", "isolated-audience")
    ep_store = TradeEpisodeProjectionStore(
        projections_path=tmp_path / "projections.json",
        events_path=tmp_path / "events.jsonl",
    )
    ep_store.project_event({
        "event_id": "ev-open-ep-dev-1",
        "event_type": "trade_episode.opened",
        "trade_episode_id": "ep-dev-1",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "persona_id": "persona-a",
        "occurred_at": "2026-11-11T01:00:00Z",
        "sequence_number": 1,
        "payload": {
            "strategy_id": "strat-1",
            "instrument_id": "2330.TW",
            "side": "buy",
            "status": "open",
            "opened_at": "2026-11-11T01:00:00Z",
            "entry_actor": "persona",
            "requested_quantity": 100.0,
            "filled_quantity": 100.0,
            "remaining_quantity": 0.0,
            "vwap": 580.0,
            "fees": 12.5,
        },
    })
    fake_svc = types.SimpleNamespace(
        list_runtime_summaries=lambda **kwargs: [],
        get_trade_episode_projection=lambda ep_id, as_of=None, as_of_sequence=None, tenant_id=None: ep_store.get(
            ep_id, as_of=as_of, as_of_sequence=as_of_sequence, tenant_id=tenant_id
        ),
    )
    monkeypatch.setattr(telemetry_main, "_get_service", lambda: fake_svc)
    client = telemetry_main.app.test_client()
    headers = {"Authorization": "Bearer " + values[variable], "X-Tenant-Id": "tenant-dev"}

    assert client.get("/api/telemetry/runtime-summaries", headers=headers).status_code == 200
    ep_resp = client.get("/api/telemetry/trade-episodes/ep-dev-1", headers=headers)
    assert ep_resp.status_code == 200 and ep_resp.get_json()["trade_episode_id"] == "ep-dev-1"
    assert client.get("/api/telemetry/trade-episodes/ep-missing", headers=headers).status_code == 404
    assert client.get(
        "/api/telemetry/trade-episodes/ep-dev-1",
        headers={"Authorization": "Bearer " + values[variable], "X-Tenant-Id": "tenant-other"},
    ).status_code == 403

    for write_path in (
        "/api/telemetry/ingest",
        "/api/telemetry/ingest/batch",
        "/api/v1/telemetry/infrastructure-health",
        "/api/v1/telemetry/heartbeats",
        "/api/telemetry/replay",
    ):
        denied = client.post(write_path, json={}, headers=headers)
        assert denied.status_code == 403 and denied.get_json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_consultation_research_principal_is_admitted_by_research_owner_checks_and_file_delivered(tmp_path):
    import yaml
    from types import SimpleNamespace
    from services.research.write_owner import ResearchWriteOwner as Owner
    from services.service_token_file import configured_service_token

    root = Path(__file__).resolve().parents[1]
    variable = "CONSULTATION_RESEARCH_SERVICE_TOKEN"
    # One consumer only; the persona-pool-cycle research file used by the daily Persona is untouched.
    assert variable in CONSUMER_FILES["consultation-svc"]
    assert all(variable not in names for consumer, names in CONSUMER_FILES.items() if consumer != "consultation-svc")
    assert "PERSONA_RESEARCH_SERVICE_TOKEN" in CONSUMER_FILES["persona"]
    assert READERS["PERSONA_RESEARCH_SERVICE_TOKEN"][0] == "persona-pool-cycle"

    values = issue_environment(configured(), now=NOW)
    claims = verify(values[variable])
    assert claims["sub"] == "consultation-svc" and claims["roles"] == ["service"]
    assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
    assert not {"operator", "admin", "viewer"} & set(claims["roles"])  # least role the owner admits
    assert values[variable] != values["PERSONA_RESEARCH_SERVICE_TOKEN"]  # no shared credential

    def ctx(c):
        return SimpleNamespace(tenant_id=c["tenant_id"], actor_id=c["sub"], roles=set(c["roles"]))

    mine = ctx(claims)
    assert Owner._action_controller(mine, "consultation-svc")
    assert not Owner._action_operator(mine)  # creation passes through the controller branch, not operator
    for other in ("pantheon-dev-consultation-persona-pool-reader", "pantheon-dev-persona-evaluator-bff-reader",
                  "persona-pool-cycle"):
        subject_claims = verify(values[next(v for v, (s, _) in READERS.items() if s == other)])
        assert not Owner._action_controller(ctx(subject_claims), "consultation-svc")
    # Same subject without the service role, and a wrong tenant, are refused by the owner and verifier.
    assert not Owner._action_controller(SimpleNamespace(actor_id="consultation-svc", roles={"consultation_session"},
                                                         tenant_id="tenant-dev"), "consultation-svc")
    with pytest.raises(AuthError):
        verify(values[variable], audience="other-audience")

    # Rotating file: request-time read, rotation visible, missing/unsafe file fails closed (no env fallback).
    path = tmp_path / variable
    path.write_text(values[variable]); path.chmod(0o600)
    env = {variable + "_FILE": str(path), variable: "stale-env-value"}
    assert configured_service_token(variable, env) == values[variable]
    rotated = issue_environment(configured(), now=NOW + 5)[variable]
    path.write_text(rotated)
    assert configured_service_token(variable, env) == rotated
    path.chmod(0o644)
    with pytest.raises(RuntimeError):
        configured_service_token(variable, env)
    path.unlink()
    with pytest.raises(RuntimeError) as err:
        configured_service_token(variable, env)
    assert rotated not in str(err.value)

    compose = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["consultation-svc"]["environment"]
    assert compose[variable + "_FILE"].endswith("/run/pantheon-principals/" + variable + "}")
    assert not any("JWT_SECRET" in key for key in compose)  # no signing secret distributed
    assert issuer.revoked_environment(
        {**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})[variable] == ""


CASE_FILE_READERS = {
    "RESEARCH_CASE_FILE_MEMORY_READ_TOKEN": ("pantheon-dev-research-case-file-memory-reader", ["consultation_session"]),
    "RESEARCH_CASE_FILE_TELEMETRY_READ_TOKEN": ("pantheon-dev-research-case-file-telemetry-reader", ["viewer", "reviewer"]),
    "RESEARCH_CASE_FILE_REGISTRY_READ_TOKEN": ("pantheon-dev-research-case-file-registry-reader", ["registry-reader"]),
    "RESEARCH_CASE_FILE_PERSONA_POOL_READ_TOKEN": ("pantheon-dev-research-case-file-persona-pool-reader", ["persona_pool_reader"]),
    "RESEARCH_CASE_FILE_SEARCH_READ_TOKEN": ("pantheon-dev-research-case-file-search-reader", ["researcher", "persona_session"]),
}
CASE_FILE_FORBIDDEN_ROLES = {
    "service", "operator", "admin", "automated_gate", "approval_decider", "approval_proposer", "registry-writer",
    "consultation-svc", "persona-svc", "training-service", "capital-reader", "risk_owner", "runtime", "system",
}


def test_research_case_file_principals_are_read_only_file_delivered_and_mounted_to_research_only():
    import time
    import yaml

    root = Path(__file__).resolve().parents[1]
    now = int(time.time())
    values = issue_environment(configured(), now=now)
    jtis = set()
    for variable, (subject, roles) in CASE_FILE_READERS.items():
        assert variable in READERS and variable not in WRITERS
        claims = verify(values[variable], now=now + 1)
        assert claims["sub"] == subject and claims["roles"] == roles
        assert claims["scope"] == "pantheon:dev-owner-read"  # never an owner-write scope
        assert claims["tenant_id"] == "tenant-dev" and claims["allowed_tenants"] == ["tenant-dev"]
        assert not CASE_FILE_FORBIDDEN_ROLES & set(claims["roles"])
        assert values[variable + "_FILE"] == "/run/pantheon-principals/" + variable
        # Mounted to research-orchestrator-svc only.
        assert variable in CONSUMER_FILES["research-orchestrator-svc"]
        assert all(variable not in names for c, names in CONSUMER_FILES.items() if c != "research-orchestrator-svc")
        assert issuer.revoked_environment({**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})[variable] == ""
        jtis.add(claims["jti"])
    assert len(jtis) == len(CASE_FILE_READERS)  # no credential shared between owners
    assert verify(values["RESEARCH_CASE_FILE_SEARCH_READ_TOKEN"], now=now + 1)["access_scopes"] == ["operator", "research", "public"]
    # Actor id equals the memory credential subject; roles are exactly the memory read role.
    memory = verify(values["RESEARCH_CASE_FILE_MEMORY_READ_TOKEN"], now=now + 1)
    assert values["RESEARCH_CASE_FILE_ACTOR_ID"] == memory["sub"]
    assert values["RESEARCH_CASE_FILE_ACTOR_ROLES"] == ",".join(memory["roles"])
    revoked = issuer.revoked_environment({**configured(), "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"})
    assert revoked["RESEARCH_CASE_FILE_ACTOR_ID"] == "" and revoked["RESEARCH_CASE_FILE_ACTOR_ROLES"] == ""

    # The existing write-capable research principals are not what case files read with.
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    env = compose["research-orchestrator-svc"]["environment"]
    mapping = {
        "PANTHEON_MEMORY_SERVICE_TOKEN_FILE": "RESEARCH_CASE_FILE_MEMORY_READ_TOKEN",
        "PANTHEON_TELEMETRY_SERVICE_TOKEN_FILE": "RESEARCH_CASE_FILE_TELEMETRY_READ_TOKEN",
        "PANTHEON_REGISTRY_SERVICE_TOKEN_FILE": "RESEARCH_CASE_FILE_REGISTRY_READ_TOKEN",
        "PANTHEON_PERSONA_SERVICE_TOKEN_FILE": "RESEARCH_CASE_FILE_PERSONA_POOL_READ_TOKEN",
        "PANTHEON_SEARCH_SERVICE_TOKEN_FILE": "RESEARCH_CASE_FILE_SEARCH_READ_TOKEN",
    }
    for owner_var, variable in mapping.items():
        assert env[owner_var].endswith("/run/pantheon-principals/" + variable + "}")
        assert env[variable + "_FILE"] == env[owner_var]
        # Case-file owner tokens are never plain env values (files only) and never a write-capable RESEARCH_* file.
        assert owner_var.removesuffix("_FILE") not in env
        assert "WRITE" not in env[owner_var] and "RESEARCH_REGISTRY_SERVICE_TOKEN" not in env[owner_var]
    for owner in ("REGISTRY", "PERSONA", "TELEMETRY", "MEMORY", "SEARCH"):
        assert env[f"PANTHEON_{owner}_API_URL"].startswith("http://")
    assert env["RESEARCH_CASE_FILE_ACTOR_ID"] == "${RESEARCH_CASE_FILE_ACTOR_ID:-}"
    assert env["RESEARCH_CASE_FILE_ACTOR_ROLES"] == "${RESEARCH_CASE_FILE_ACTOR_ROLES:-}"
    assert "dev-paper-research-tokens:/run/pantheon-principals:ro" in compose["research-orchestrator-svc"]["volumes"]
    # No other service references a case-file principal, and no other compose file carries a default for one.
    for name, service in compose.items():
        if name != "research-orchestrator-svc":
            assert "RESEARCH_CASE_FILE" not in str(service), name
    for other in root.glob("docker-compose.*.yml"):
        assert "RESEARCH_CASE_FILE_" not in other.read_text(), other.name
    for path in (root / "deploy").rglob("*"):
        if path.is_file() and path.suffix in {".yml", ".yaml", ".env", ""}:
            assert "RESEARCH_CASE_FILE_" not in path.read_text(errors="ignore"), str(path)
