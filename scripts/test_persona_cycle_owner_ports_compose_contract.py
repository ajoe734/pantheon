"""End-to-end contract tests for PERSONA-CYCLE-OWNER-PORTS-WIRING-20261115.

Verifies that:
1. Persona daily cycle owner ports (research, memory, registry, evolution,
   consultation, governance, search) and their principal token file mounts are
   wired on `persona` in `docker-compose.yml` with zero committed secrets and
   distinct least-privilege principal definitions in
   `scripts/issue_dev_paper_principals.py`.
2. Pool publish (`operator-bff` -> `persona`) and canonical dataset authority
   (`research-orchestrator-svc` reading the dataset pointer published by
   `persona` on `/data/bff/persona-pool-dataset-authority`) are wired.
3. Quarterly paper allocation (`persona-evaluator-agent`, `capital`,
   `paper-signal-producer`, `paper-fleet-reconciler`, and
   `evolution-daily-sweep-scheduler`) have explicit compose wiring and deploy
   script/workflow pass-throughs, with `EVOLUTION_AUTO_CONSUMER_ENABLED`
   defaulting to `false`.
4. Persona `/health` and cycle heartbeat surface `owner_ports` and name every
   missing or unconfigured owner port instead of returning healthy when a port
   is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts import issue_dev_paper_principals as issuer
from services.governance.authz import evaluate_authz_request
from services.persona.owner_port_health import (
    REQUIRED_OWNER_PORTS,
    apply_owner_port_health_to_cycle_report,
    evaluate_owner_ports,
    sync_owner_port_env,
)
from services.runtime_auth_inbound import validate_request_auth


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def _default(interpolation: str) -> str:
    assert interpolation.startswith("${") and interpolation.endswith("}")
    _name, separator, value = interpolation[2:-1].partition(":-")
    assert separator == ":-"
    if value.startswith("${") and value.endswith("}"):
        return _default(value)
    return value


def _env(service: str) -> dict[str, str]:
    environment = SERVICES[service].get("environment") or {}
    assert isinstance(environment, dict)
    return environment


def test_persona_owner_ports_resolve_configured_when_compose_and_issuer_are_applied(
    tmp_path: Path,
) -> None:
    persona_env = _env("persona")
    secret = "persona-owner-ports-contract-secret-0123456789abcdef"
    issued = issuer.issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": secret,
            "PANTHEON_DEV_BFF_JWT_ISSUER": "pantheon-dev",
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": "bff-operators",
        }
    )

    token_dir = tmp_path / "persona"
    token_dir.mkdir(parents=True, exist_ok=True)
    for var_name in issuer.CONSUMER_FILES["persona"]:
        token_path = token_dir / var_name
        token_path.write_text(issued[var_name] + "\n", encoding="utf-8")
        token_path.chmod(0o600)

    runtime_env: dict[str, str] = {}
    for key, raw_value in persona_env.items():
        val = str(raw_value)
        if val.startswith("${") and ":-" in val:
            resolved = _default(val)
        else:
            resolved = val
        resolved = resolved.replace("/run/pantheon-principals", str(token_dir))
        runtime_env[key] = resolved

    sync_owner_port_env(runtime_env)
    owner_ports = evaluate_owner_ports(runtime_env)

    assert set(owner_ports.keys()) == set(REQUIRED_OWNER_PORTS)
    for port_name, detail in owner_ports.items():
        assert detail["status"] == "configured", (port_name, detail)
        assert detail["address"].startswith("http://"), (port_name, detail)
        assert detail["credential_configured"] is True, (port_name, detail)


def test_persona_owner_tokens_verify_against_each_target_owner_contract() -> None:
    secret = "persona-owner-ports-contract-secret-0123456789abcdef"
    issued = issuer.issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": secret,
            "PANTHEON_DEV_BFF_JWT_ISSUER": "pantheon-dev",
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": "bff-operators",
        }
    )

    verifier_env = {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict",
        "PANTHEON_RUNTIME_JWT_SECRET": secret,
        "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-dev",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "bff-operators",
    }

    # 1. Research owner token: viewer role (read-only)
    research_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_RESEARCH_SERVICE_TOKEN']}",
        required_roles=("viewer",),
        env=verifier_env,
    )
    assert list(research_ctx.roles) == ["viewer"]

    # 2. Memory owner token: persona_session role + governance authz check passes
    memory_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_MEMORY_SERVICE_TOKEN']}",
        required_roles=("persona_session",),
        env=verifier_env,
    )
    assert memory_ctx.actor_id == "persona_cycle_controller"
    authz_decision = evaluate_authz_request(
        action="memory.retrieve",
        actor_id=memory_ctx.actor_id,
        actor_roles=list(memory_ctx.roles),
        resource={"scope": "persona", "persona_id": "persona-alpha"},
        context={"session_id": "cycle-1", "session_persona_id": "persona-alpha"},
    )
    assert authz_decision["allowed"] is True

    # 3. Registry owner token: registry-reader role only (read-only)
    registry_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_REGISTRY_SERVICE_TOKEN']}",
        required_roles=("registry-reader",),
        env=verifier_env,
    )
    assert list(registry_ctx.roles) == ["registry-reader"]

    # 4. Evolution owner token: evolution_controller role
    evolution_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_EVOLUTION_SERVICE_TOKEN']}",
        required_roles=("evolution_controller",),
        env=verifier_env,
    )
    assert evolution_ctx.actor_id == "persona-svc"

    # 5. Consultation owner token: service role
    consultation_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_CONSULTATION_SERVICE_TOKEN']}",
        required_roles=("service",),
        env=verifier_env,
    )
    assert consultation_ctx.actor_id == "persona-svc"

    # 6. Governance lesson-rule proposer token: approval_proposer role
    proposer_ctx = validate_request_auth(
        authorization=f"Bearer {issued['LESSON_RULE_PROPOSER_TOKEN']}",
        required_roles=("approval_proposer",),
        env=verifier_env,
    )
    assert list(proposer_ctx.roles) == ["approval_proposer"]

    # 7. Search owner token: researcher + persona_session roles + access_scopes
    search_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_SEARCH_SERVICE_TOKEN']}",
        required_roles=("researcher", "persona_session"),
        env=verifier_env,
    )
    assert search_ctx.claims["access_scopes"] == ["operator", "research", "public"]


def test_pool_publish_and_research_canonical_dataset_authority_wiring() -> None:
    bff_env = _env("operator-bff")
    assert bff_env["PANTHEON_PERSONA_API_URL"] == (
        "${PANTHEON_PERSONA_API_URL:-${PERSONA_URL:-http://persona:8002}}"
    )
    assert bff_env["PANTHEON_PERSONA_SERVICE_URL"] == (
        "${PANTHEON_PERSONA_SERVICE_URL:-${PERSONA_URL:-http://persona:8002}}"
    )

    research = SERVICES["research-orchestrator-svc"]
    research_env = _env("research-orchestrator-svc")
    assert "bff-data:/data/bff:ro" in research["volumes"]
    assert "source-ingest-data:/data/source-ingest:ro" in research["volumes"]
    assert research_env["RESEARCH_RETRAIN_CANONICAL_DATASET_PATH"] == (
        "${RESEARCH_RETRAIN_CANONICAL_DATASET_PATH:-/data/bff/persona-pool-dataset-authority/canonical-dataset-version.json}"
    )
    assert research_env["RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH"] == (
        "${RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH:-/workspace/services/training-session/threshold_policy.json}"
    )
    assert (ROOT / "services" / "training-session" / "threshold_policy.json").is_file()


def test_quarterly_paper_allocation_and_deploy_passthrough_wiring() -> None:
    capital_env = _env("capital")
    assert capital_env["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"

    evaluator_env = _env("persona-evaluator-agent")
    assert evaluator_env["PERSONA_EVALUATOR_PAPER_POOL_ID"] == (
        "${PERSONA_EVALUATOR_PAPER_POOL_ID:-${PANTHEON_PAPER_POOL_ID:-pool-paper-default}}"
    )
    assert _default(evaluator_env["PERSONA_EVALUATOR_BFF_TOKEN_FILE"]) == (
        "/run/pantheon-principals/PERSONA_EVALUATOR_BFF_TOKEN"
    )
    assert _default(evaluator_env["AUTO_CONSUMER_CAPITAL_TOKEN_FILE"]) == (
        "/run/pantheon-principals/AUTO_CONSUMER_CAPITAL_TOKEN"
    )

    sweep = SERVICES["evolution-daily-sweep-scheduler"]
    sweep_env = _env("evolution-daily-sweep-scheduler")
    assert sweep_env["EVOLUTION_AUTO_CONSUMER_ENABLED"] == (
        "${EVOLUTION_AUTO_CONSUMER_ENABLED:-false}"
    )
    assert (
        "dev-paper-evolution-scheduler-tokens:/run/pantheon-principals:ro"
        in sweep["volumes"]
    )
    assert (
        "dev-paper-evolution-scheduler-tokens:/issued/evolution-daily-sweep-scheduler"
        in SERVICES["dev-paper-principal-issuer"]["volumes"]
    )

    deploy_script = (ROOT / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert 'EVOLUTION_AUTO_CONSUMER_ENABLED="${PANTHEON_DEV_EVOLUTION_AUTO_CONSUMER_ENABLED:-false}"' in deploy_script
    assert 'PERSONA_EVALUATOR_PAPER_POOL_ID="${PANTHEON_DEV_PERSONA_EVALUATOR_PAPER_POOL_ID:-pool-paper-default}"' in deploy_script

    workflow = (ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(encoding="utf-8")
    assert "DEV_EVOLUTION_AUTO_CONSUMER_ENABLED: ${{ vars.DEV_EVOLUTION_AUTO_CONSUMER_ENABLED || 'false' }}" in workflow
    assert "DEV_PERSONA_EVALUATOR_PAPER_POOL_ID: ${{ vars.DEV_PERSONA_EVALUATOR_PAPER_POOL_ID || 'pool-paper-default' }}" in workflow


def test_degraded_cycle_report_names_every_missing_owner_port() -> None:
    report: dict[str, Any] = {
        "cycle_id": "cycle-check",
        "status": "succeeded",
        "failures": [],
    }
    updated = apply_owner_port_health_to_cycle_report(report, env={})
    assert updated["status"] == "degraded"
    assert set(updated["owner_ports"].keys()) == set(REQUIRED_OWNER_PORTS)
    for port_name in REQUIRED_OWNER_PORTS:
        assert any(
            item["owner_port"] == port_name for item in updated["failures"]
        )
