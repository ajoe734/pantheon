"""End-to-end contract tests for PERSONA-CYCLE-OWNER-PORTS-WIRING-20261115.

Verifies that:
1. Every owner URL, token, dataset pointer, store, and actor environment
   variable read by the persona daily cycle, pool cycle ports, case-file
   composer, harness adoption, lesson-rule proposal publisher, BFF pool publish
   route, template demand search, and paper quarterly allocation code paths is
   present on its owning service in `docker-compose.yml` (verified via AST
   inspection of the production modules), and removing any required variable
   fails the contract check.
2. Persona daily cycle owner ports (research, memory, registry, evolution,
   consultation, governance, search) and their principal token files/tokens
   resolve as `configured` when `docker-compose.yml` and
   `scripts/issue_dev_paper_principals.py` are applied, with zero committed
   secrets and distinct credentials per owner.
3. Issued credentials verify against each real target owner's inbound auth
   verifier (`validate_request_auth`, `evaluate_authz_request`,
   `services.evolution.main.authenticate_tenant`,
   `services.consultation.auth.authenticate`, and
   `services.capital.inbound_authority.authenticate_capital_request`).
4. Pool publish (`operator-bff` -> `persona`) and canonical dataset authority
   (`research-orchestrator-svc` reading the dataset pointer published by
   `persona` on `/data/bff/persona-pool-dataset-authority/canonical-dataset-version.json`)
   are wired.
5. Quarterly paper allocation (`persona-evaluator-agent`, `capital`,
   `paper-signal-producer`, `paper-fleet-reconciler`, and
   `evolution-daily-sweep-scheduler`) have explicit compose wiring and deploy
   script/workflow pass-throughs, with `EVOLUTION_AUTO_CONSUMER_ENABLED`
   defaulting to `false`.
6. Persona `/health` and cycle heartbeat (`persona_daily_cycle` and
   `per_persona_ooda`) surface `owner_ports` and degrade when any required
   owner port is unconfigured or unreachable.
"""

from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from scripts import issue_dev_paper_principals as issuer
from services.capital.inbound_authority import authenticate_capital_request
from services.consultation import auth as consultation_auth
from services.evolution import main as evolution_main
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


# Canonical code-read owner environment specs across all 8 required areas.
# Each entry maps (service, area, relative_file, env_var) -> fallback_group
# where the AST scanner verifies both:
#   (a) the variable name is literally read as an env var in `relative_file`, and
#   (b) `docker-compose.yml` wires `env_var` on `service`.
CODE_READ_OWNER_ENV_SPECS: tuple[tuple[str, str, str, str], ...] = (
    # 1. Persona daily cycle (services/control-plane/persona/main.py, services/persona/persona_cycle_controller.py, services/persona/owner_port_health.py, services/consultation/client.py)
    ("persona", "persona_daily_cycle", "services/control-plane/persona/main.py", "PERSONA_DAILY_CYCLE_ENABLED"),
    ("persona", "persona_daily_cycle", "services/control-plane/persona/main.py", "PERSONA_DEFAULT_TENANT_ID"),
    ("persona", "persona_daily_cycle", "services/control-plane/persona/main.py", "PANTHEON_TENANT_ID"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "DATABASE_URL"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "AGORA_DATASET_STORE_SCHEMA"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_TELEMETRY_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_TELEMETRY_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_INCIDENTS_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_CYCLE_ACTOR_ID"),
    ("persona", "persona_daily_cycle", "services/persona/persona_cycle_controller.py", "PANTHEON_CYCLE_MEMORY_ACTOR_ROLES"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "RESEARCH_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_RESEARCH_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PERSONA_RESEARCH_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_RESEARCH_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "MEMORY_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_MEMORY_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_MEMORY_AUTH_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_MEMORY_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PERSONA_MEMORY_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "REGISTRY_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_REGISTRY_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_REGISTRY_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PERSONA_REGISTRY_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_REGISTRY_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "EVOLUTION_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "PANTHEON_EVOLUTION_API_URL"),
    ("persona", "persona_daily_cycle", "services/persona/owner_port_health.py", "EVOLUTION_AUTH_TOKEN"),
    ("persona", "persona_daily_cycle", "services/consultation/client.py", "PANTHEON_CONSULTATION_API_URL"),
    ("persona", "persona_daily_cycle", "services/consultation/client.py", "PANTHEON_CONSULTATION_SERVICE_URL"),
    ("persona", "persona_daily_cycle", "services/consultation/client.py", "CONSULTATION_SERVICE_TOKEN"),
    ("persona", "persona_daily_cycle", "services/consultation/client.py", "CONSULTATION_API_TOKEN"),
    ("persona", "persona_daily_cycle", "services/consultation/client.py", "CONSULTATION_SERVICE_ACTOR"),
    ("persona", "persona_daily_cycle", "services/consultation/client.py", "PANTHEON_SERVICE_ACTOR"),
    # 2. Pool cycle ports & dataset authority (services/persona/pool_autonomy_cycle.py, services/persona/pool_dataset_authority.py, services/control-plane/persona/main.py)
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "PERSONA_API_URL"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "RESEARCH_API_URL"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "MEMORY_API_URL"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "EVOLUTION_API_URL"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "REGISTRY_URL"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "PERSONA_RESEARCH_SERVICE_TOKEN"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "PANTHEON_MEMORY_AUTH_TOKEN"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "EVOLUTION_AUTH_TOKEN"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "PERSONA_REGISTRY_SERVICE_TOKEN"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "PERSONA_POOL_CANONICAL_DATASET_PATH"),
    ("persona", "pool_cycle_ports", "services/persona/pool_autonomy_cycle.py", "PERSONA_POOL_THRESHOLD_POLICY_PATH"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "SOURCE_INGEST_API_URL"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_SOURCE_CONNECTOR_ID"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_SOURCE_DATASET_ID"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_SOURCE_VOLUME_ROOT"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_SOURCE_AUTHORITY_TIMEOUT_SECONDS"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_SOURCE_READ_TOKEN_FILE"),
    ("persona", "pool_cycle_ports", "services/control-plane/persona/main.py", "PERSONA_SOURCE_INGEST_SERVICE_TOKEN"),
    ("persona", "pool_cycle_ports", "services/persona/pool_dataset_authority.py", "PERSONA_POOL_SOURCE_READ_TENANT_ID"),
    # 3. Case-file composer (services/knowledge/case_file.py)
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_REGISTRY_API_URL"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_REGISTRY_SERVICE_TOKEN"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_RESEARCH_API_URL"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_RESEARCH_SERVICE_TOKEN"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_PERSONA_API_URL"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_PERSONA_SERVICE_TOKEN"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_TELEMETRY_API_URL"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_TELEMETRY_SERVICE_TOKEN"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_MEMORY_API_URL"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_MEMORY_SERVICE_TOKEN"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_SEARCH_API_URL"),
    ("persona", "case_file_composer", "services/knowledge/case_file.py", "PANTHEON_SEARCH_SERVICE_TOKEN"),
    # 4. Harness adoption (services/persona/harness_adoption.py & services/persona/harness.py)
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "MEMORY_API_URL"),
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "PANTHEON_MEMORY_API_URL"),
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "PANTHEON_MEMORY_AUTH_TOKEN"),
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "PANTHEON_MEMORY_AUTH_TOKEN_FILE"),
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "PANTHEON_EVOLUTION_API_URL"),
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "EVOLUTION_AUTH_TOKEN"),
    ("persona", "harness_adoption", "services/persona/harness_adoption.py", "EVOLUTION_AUTH_TOKEN_FILE"),
    ("persona", "harness_adoption", "services/persona/harness.py", "PANTHEON_SHADOW_REPLAY_AGENT_ID"),
    ("persona", "harness_adoption", "services/persona/harness.py", "PANTHEON_SHADOW_REPLAY_POLICY_URL"),
    # 5. Lesson-rule proposals (services/persona/lesson_rule_proposals.py)
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PANTHEON_MEMORY_API_URL"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "MEMORY_API_URL"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PERSONA_LESSON_RULE_STORE_BACKEND"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PERSONA_STORE_DSN"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PERSONA_LESSON_RULE_STORE_TABLE"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PANTHEON_GOVERNANCE_SERVICE_URL"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PANTHEON_GOVERNANCE_API_URL"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "LESSON_RULE_PROPOSER_TOKEN"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "LESSON_RULE_PROPOSER_TOKEN_FILE"),
    ("persona", "lesson_rule_proposals", "services/persona/lesson_rule_proposals.py", "PANTHEON_LESSON_RULE_PROPOSER_ID"),
    # 6. BFF pool publish route (services/control-plane/bff/personas/routes/lifecycle.py & services/control-plane/bff/ports/persona_write_owner.py)
    ("operator-bff", "bff_pool_publish_route", "services/control-plane/bff/ports/persona_write_owner.py", "PERSONA_URL"),
    ("operator-bff", "bff_pool_publish_route", "services/control-plane/bff/personas/routes/lifecycle.py", "PANTHEON_PERSONA_API_URL"),
    ("operator-bff", "bff_pool_publish_route", "services/control-plane/bff/personas/routes/lifecycle.py", "PANTHEON_PERSONA_SERVICE_URL"),
    ("operator-bff", "bff_pool_publish_route", "services/control-plane/bff/ports/persona_write_owner.py", "PANTHEON_PERSONA_SERVICE_TOKEN"),
    ("operator-bff", "bff_pool_publish_route", "services/control-plane/bff/ports/persona_write_owner.py", "PANTHEON_PERSONA_SERVICE_ACTOR_ID"),
    # 7. Template demand search & retrain executor (services/research/main.py)
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "PERSONA_API_URL"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "RESEARCH_PERSONA_SERVICE_TOKEN_FILE"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "RESEARCH_RETRAIN_CANONICAL_DATASET_PATH"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "TRAINING_SESSION_CANONICAL_DATASET_PATH"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "TRAINING_SESSION_THRESHOLD_POLICY_PATH"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "TRAINING_SESSION_URL"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "REGISTRY_URL"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "PANTHEON_REGISTRY_URL"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "RESEARCH_REGISTRY_SERVICE_TOKEN_FILE"),
    ("research-orchestrator-svc", "template_demand_search", "services/research/main.py", "RESEARCH_TRAINING_SERVICE_TOKEN_FILE"),
    # 8. Paper quarterly allocation (persona-evaluator-agent, capital, operator-bff, evolution-daily-sweep-scheduler)
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_PAPER_POOL_ID"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_BFF_URL"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_BFF_TOKEN"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_ACTOR_ID"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_PERSONA_URL"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_PERSONA_TOKEN"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "PERSONA_EVALUATOR_GOVERNANCE_TOKEN"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_CAPITAL_URL"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_CAPITAL_TOKEN"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_TELEMETRY_URL"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_TELEMETRY_TOKEN"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_REGISTRY_URL"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_REGISTRY_TOKEN"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_EVOLUTION_URL"),
    ("persona-evaluator-agent", "paper_quarterly_allocation", "services/persona-evaluator-agent/persona_evaluator_agent.py", "AUTO_CONSUMER_EVOLUTION_TOKEN"),
    ("capital", "paper_quarterly_allocation", "services/capital/allocation_lineage.py", "RANKING_STORE_DSN"),
    ("capital", "paper_quarterly_allocation", "services/capital/allocation_lineage.py", "RANKING_STORE_TABLE"),
    ("operator-bff", "paper_quarterly_allocation", "services/control-plane/bff/ports/rankings.py", "RANKING_STORE_DSN"),
    ("operator-bff", "paper_quarterly_allocation", "services/control-plane/bff/ports/rankings.py", "RANKING_STORE_TABLE"),
    ("operator-bff", "paper_quarterly_allocation", "services/control-plane/bff/command_executor.py", "PANTHEON_CAPITAL_API_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "EVOLUTION_AUTO_CONSUMER_ENABLED"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_PERSONA_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_PERSONA_TOKEN"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_REGISTRY_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_REGISTRY_TOKEN"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_CAPITAL_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_CAPITAL_TOKEN"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_RESEARCH_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_RESEARCH_TOKEN"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_BFF_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_BFF_TOKEN"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_GOVERNANCE_URL"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_PROPOSER_TOKEN"),
    ("evolution-daily-sweep-scheduler", "paper_quarterly_allocation", "services/evolution/auto_consumer_runtime.py", "AUTO_CONSUMER_GATE_TOKEN"),
    # 9. Hypothesis card admission path of the alpha replication worker (CARD-ADMISSION-PRODUCTION-WIRING-20261115)
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "PANTHEON_REGISTRY_URL"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "PANTHEON_GOVERNANCE_URL"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_API_URL"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "PERSONA_API_URL"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_GOVERNANCE_PROPOSER_ID"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_GOVERNANCE_GATE_ID"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_GOVERNANCE_PROPOSER_TOKEN"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_GOVERNANCE_GATE_TOKEN"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_REGISTRY_SERVICE_TOKEN"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_RETRAIN_CANONICAL_DATASET_PATH"),
    ("alpha-replication-worker", "card_admission_path", "services/research/alpha_replication/card_production_wiring.py", "RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH"),
    ("alpha-replication-worker", "card_admission_path", "services/research/candidate_registration.py", "RESEARCH_GOVERNANCE_PROPOSER_TOKEN"),
    ("alpha-replication-worker", "card_admission_path", "services/research/candidate_registration.py", "RESEARCH_GOVERNANCE_GATE_TOKEN"),
)


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


def _extract_ast_string_literals_with_lines(relative_path: str) -> dict[str, list[int]]:
    """Parse `relative_path` with `ast` and map every uppercase env-like string literal to its line numbers."""
    source_path = ROOT / relative_path
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative_path)
    found: dict[str, list[int]] = {}
    upper_tokens: set[str] = set()
    fstring_prefixes: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val.isupper() and "_" in val:
                found.setdefault(val, []).append(getattr(node, "lineno", 0))
            elif val.isupper() and val.isalpha():
                upper_tokens.add(val)
        elif isinstance(node, ast.JoinedStr) and len(node.values) == 3:
            left, mid, right = node.values
            if (
                isinstance(left, ast.Constant)
                and isinstance(left.value, str)
                and isinstance(mid, ast.FormattedValue)
                and isinstance(right, ast.Constant)
                and isinstance(right.value, str)
            ):
                fstring_prefixes.append(
                    (left.value, right.value, getattr(node, "lineno", 0))
                )
    for prefix, suffix, lineno in fstring_prefixes:
        if prefix.isupper() and suffix.isupper():
            for token in upper_tokens:
                expanded = f"{prefix}{token}{suffix}"
                found.setdefault(expanded, []).append(lineno)
    return found


def scan_code_read_owner_env_contract(
    compose_services: Mapping[str, Any],
) -> list[str]:
    """Scan code-read owner env vars via AST and verify `compose_services` wires every one of them."""
    ast_cache: dict[str, dict[str, list[int]]] = {}
    failures: list[str] = []

    for service, area, rel_file, env_var in CODE_READ_OWNER_ENV_SPECS:
        if rel_file not in ast_cache:
            ast_cache[rel_file] = _extract_ast_string_literals_with_lines(rel_file)
        literals = ast_cache[rel_file]
        # Token `_FILE` vars may be read directly or via `configured_service_token(<BASE_VAR>)`.
        base_var = env_var[:-5] if env_var.endswith("_FILE") else env_var
        lines = literals.get(env_var) or literals.get(base_var) or []
        if not lines:
            failures.append(
                f"code AST missing expected env var {env_var} in {rel_file} ({area})"
            )
            continue

        svc_def = compose_services.get(service) or {}
        svc_env = svc_def.get("environment") or {}
        if env_var not in svc_env:
            line_ref = f"{rel_file}:{lines[0]}"
            failures.append(
                f"docker-compose.yml service '{service}' missing {env_var} (read at {line_ref} for {area})"
            )
    return failures


def test_code_read_owner_env_vars_are_wired_and_missing_var_fails_contract() -> None:
    # 1. All 8 areas must pass with zero missing variables on the real docker-compose.yml.
    assert scan_code_read_owner_env_contract(SERVICES) == []

    # 2. Removing a code-read variable in each of the 8 areas must fail the contract check.
    representative_by_area = {
        area: (service, rel_file, env_var)
        for service, area, rel_file, env_var in CODE_READ_OWNER_ENV_SPECS
    }
    assert len(representative_by_area) == 9
    for area, (service, rel_file, env_var) in representative_by_area.items():
        mutated = copy.deepcopy(SERVICES)
        del mutated[service]["environment"][env_var]
        failures = scan_code_read_owner_env_contract(mutated)
        assert failures, f"Expected contract failure when removing {env_var} from {service} ({area})"
        assert any(
            service in item and env_var in item and rel_file in item and area in item
            for item in failures
        ), failures


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


def test_persona_owner_tokens_verify_against_each_target_owner_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    # 4. Evolution owner token: verified against evolution's real authenticate_tenant middleware
    from types import SimpleNamespace

    evolution_compose_token = _default(_env("evolution")["EVOLUTION_AUTH_TOKEN"])
    persona_evolution_token = _default(_env("persona")["EVOLUTION_AUTH_TOKEN"])
    assert persona_evolution_token == evolution_compose_token
    assert issued["EVOLUTION_AUTH_TOKEN"] == evolution_compose_token
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", evolution_compose_token)
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "tenant-dev")
    fake_request = SimpleNamespace(
        url=SimpleNamespace(path="/api/evolution/proposals"),
        headers={
            "authorization": f"Bearer {issued['EVOLUTION_AUTH_TOKEN']}",
            "x-tenant-id": "tenant-dev",
        },
        state=SimpleNamespace(),
    )

    async def _call_next(req: Any) -> Any:
        return SimpleNamespace(status_code=200, headers={}, tenant_id=req.headers["x-tenant-id"])

    evo_response = asyncio.run(
        evolution_main.authenticate_tenant(fake_request, _call_next)  # type: ignore[arg-type]
    )
    assert evo_response.status_code == 200
    assert evo_response.tenant_id == "tenant-dev"

    # 5. Consultation owner token: verified against consultation-svc's real authenticate
    consultation_compose_token = _default(_env("consultation-svc")["CONSULTATION_SERVICE_TOKEN"])
    persona_consultation_token = _default(_env("persona")["CONSULTATION_SERVICE_TOKEN"])
    persona_consultation_actor = _default(_env("persona")["CONSULTATION_SERVICE_ACTOR"])
    assert persona_consultation_token == consultation_compose_token
    assert issued["CONSULTATION_SERVICE_TOKEN"] == consultation_compose_token
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", consultation_compose_token)
    consultation_principal = consultation_auth.authenticate(
        {
            "authorization": f"Bearer {issued['CONSULTATION_SERVICE_TOKEN']}",
            "x-pantheon-service-actor": persona_consultation_actor,
            "x-pantheon-tenant-id": "tenant-dev",
        }
    )
    assert consultation_principal.actor_type == "service"
    assert consultation_principal.actor_id == "persona-svc"
    assert consultation_principal.tenant_id == "tenant-dev"

    # 6. Governance approval reader + lesson-rule proposer tokens
    gov_reader_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_GOVERNANCE_SERVICE_TOKEN']}",
        required_roles=("approval_reader",),
        env=verifier_env,
    )
    assert list(gov_reader_ctx.roles) == ["approval_reader"]

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

    # 8. Paper quarterly allocation BFF + Capital inbound verifier:
    # PERSONA_EVALUATOR_BFF_TOKEN / PERSONA_RANKING_TOKEN carry viewer, registry-reader (read-only);
    # PERSONA_ALLOCATION_TOKEN carries operator and azp=control-plane-bff for paper allocation.
    ranking_ctx = validate_request_auth(
        authorization=f"Bearer {issued['PERSONA_RANKING_TOKEN']}",
        required_roles=("viewer", "registry-reader"),
        env=verifier_env,
    )
    assert set(ranking_ctx.roles) == {"viewer", "registry-reader"}
    monkeypatch.setenv("CAPITAL_AUTH_MODE", "strict")
    monkeypatch.setenv("CAPITAL_JWT_SECRET", secret)
    monkeypatch.setenv("CAPITAL_JWT_ISSUER", "pantheon-dev")
    monkeypatch.setenv("CAPITAL_JWT_AUDIENCE", "bff-operators")
    cap_authority = authenticate_capital_request(
        method="POST",
        authorization=f"Bearer {issued['PERSONA_ALLOCATION_TOKEN']}",
        tenant_id="tenant-dev",
        actor_service="control-plane-bff",
        persistence_enforced=True,
    )
    assert "operator" in cap_authority.roles
    assert cap_authority.tenant_id == "tenant-dev"


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
    assert capital_env["RANKING_STORE_DSN"] == (
        "${RANKING_STORE_DSN:-${DATABASE_URL:-postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon}}"
    )
    assert capital_env["RANKING_STORE_TABLE"] == "${RANKING_STORE_TABLE:-rankings.rankings}"

    evaluator_env = _env("persona-evaluator-agent")
    assert evaluator_env["PERSONA_EVALUATOR_PAPER_POOL_ID"] == (
        "${PERSONA_EVALUATOR_PAPER_POOL_ID:-${PANTHEON_PAPER_POOL_ID:-pool-paper-default}}"
    )
    assert evaluator_env["PERSONA_EVALUATOR_ACTOR_ID"] == (
        "${PERSONA_EVALUATOR_ACTOR_ID:-persona-evaluator-agent}"
    )
    assert _default(evaluator_env["PERSONA_EVALUATOR_BFF_TOKEN_FILE"]) == (
        "/run/pantheon-principals/PERSONA_EVALUATOR_BFF_TOKEN"
    )
    assert _default(evaluator_env["PERSONA_RANKING_TOKEN_FILE"]) == (
        "/run/pantheon-principals/PERSONA_RANKING_TOKEN"
    )
    assert _default(evaluator_env["PERSONA_ALLOCATION_TOKEN_FILE"]) == (
        "/run/pantheon-principals/PERSONA_ALLOCATION_TOKEN"
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
    assert 'PERSONA_RANKING_TOKEN_FILE="${PANTHEON_DEV_PERSONA_RANKING_TOKEN_FILE:-/run/pantheon-principals/PERSONA_RANKING_TOKEN}"' in deploy_script
    assert 'PERSONA_ALLOCATION_TOKEN_FILE="${PANTHEON_DEV_PERSONA_ALLOCATION_TOKEN_FILE:-/run/pantheon-principals/PERSONA_ALLOCATION_TOKEN}"' in deploy_script

    workflow = (ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(encoding="utf-8")
    assert "DEV_EVOLUTION_AUTO_CONSUMER_ENABLED: ${{ vars.DEV_EVOLUTION_AUTO_CONSUMER_ENABLED || 'false' }}" in workflow
    assert "DEV_PERSONA_EVALUATOR_PAPER_POOL_ID: ${{ vars.DEV_PERSONA_EVALUATOR_PAPER_POOL_ID || 'pool-paper-default' }}" in workflow
    assert "DEV_PERSONA_RANKING_TOKEN_FILE: ${{ vars.DEV_PERSONA_RANKING_TOKEN_FILE || '/run/pantheon-principals/PERSONA_RANKING_TOKEN' }}" in workflow
    assert "DEV_PERSONA_ALLOCATION_TOKEN_FILE: ${{ vars.DEV_PERSONA_ALLOCATION_TOKEN_FILE || '/run/pantheon-principals/PERSONA_ALLOCATION_TOKEN' }}" in workflow


def test_degraded_cycle_report_names_every_missing_owner_port() -> None:
    report: dict[str, Any] = {
        "cycle_id": "cycle-check",
        "status": "succeeded",
        "tenant_scopes": [{"tenant_id": "tenant-dev", "personas": ["persona-alpha"]}],
        "failures": [],
    }
    updated = apply_owner_port_health_to_cycle_report(report, env={})
    assert updated["status"] == "degraded"
    assert set(updated["owner_ports"].keys()) == set(REQUIRED_OWNER_PORTS)
    for port_name in REQUIRED_OWNER_PORTS:
        assert any(
            item["owner_port"] == port_name and item.get("persona_id") == "persona-alpha"
            for item in updated["failures"]
        )


def test_enrollment_proposer_principal_is_wired_distinct_and_propose_only() -> None:
    names = (
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID",
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN",
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN_FILE",
    )
    bff_env = _env("operator-bff")
    for name in names:
        # Empty default: unconfigured enrolment fails closed, no value is committed.
        assert _default(bff_env[name]) == "", name
    # The names are the ones the enrolment code reads.
    source = (ROOT / "services/control-plane/bff/persona_paper_enrollment.py").read_text(encoding="utf-8")
    assert "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID" in source
    assert "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN" in source

    variable = "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"
    assert variable in issuer.CONSUMER_FILES["operator-bff"]
    subject, role, scope = issuer.WRITERS[variable]
    assert subject != issuer.PAPER_SUBJECT
    assert role == "approval_proposer" and scope == "pantheon:dev-owner-write"
    issued = issuer.issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": "enrollment-proposer-contract-secret-0123456789",
            "PANTHEON_DEV_BFF_JWT_ISSUER": "pantheon-dev",
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": "bff-operators",
        }
    )
    assert issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID"] == subject
    assert issued[variable + "_FILE"] == "/run/pantheon-principals/" + variable
    assert issued[variable] != issued["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"]
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert issued[variable] not in compose_text


GOVERNANCE_BINDING_READER_SETTINGS = (
    "GOVERNANCE_CAPITAL_BASE_URL",
    "GOVERNANCE_CAPITAL_SERVICE_TOKEN",
    "GOVERNANCE_CAPITAL_SERVICE_TOKEN_FILE",
    "GOVERNANCE_RUNTIME_MANAGER_BASE_URL",
    "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN",
    "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN_FILE",
)


def scan_governance_binding_reader_contract(services: Mapping[str, Any]) -> list[str]:
    environment = (services.get("governance") or {}).get("environment") or {}
    failures = [f"governance missing {name}" for name in GOVERNANCE_BINDING_READER_SETTINGS if name not in environment]
    # The deployed reader reads exactly these names from the real production module.
    source = (ROOT / "services/governance/paper_approval_scope.py").read_text(encoding="utf-8")
    failures += [f"paper_approval_scope does not read {name}" for name in
                 ("GOVERNANCE_CAPITAL_BASE_URL", "GOVERNANCE_CAPITAL_SERVICE_TOKEN",
                  "GOVERNANCE_RUNTIME_MANAGER_BASE_URL", "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN")
                 if name not in source]
    return failures


def test_governance_binding_readers_are_wired_and_each_missing_setting_fails_contract() -> None:
    assert scan_governance_binding_reader_contract(SERVICES) == []
    env = _env("governance")
    for name in ("GOVERNANCE_CAPITAL_SERVICE_TOKEN", "GOVERNANCE_CAPITAL_SERVICE_TOKEN_FILE",
                 "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN", "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN_FILE"):
        assert _default(env[name]) == "", name  # no secret value committed
    assert _default(env["GOVERNANCE_CAPITAL_BASE_URL"]) == "http://capital:8092"
    assert _default(env["GOVERNANCE_RUNTIME_MANAGER_BASE_URL"]) == "http://runtime-manager:8081"
    assert issuer.GOVERNANCE_CAPITAL_READER_SUBJECT in _default(
        _env("capital")["CAPITAL_ALLOWED_READER_SERVICES"]).split(",")
    for name in GOVERNANCE_BINDING_READER_SETTINGS:
        mutated = copy.deepcopy(SERVICES)
        del mutated["governance"]["environment"][name]
        assert scan_governance_binding_reader_contract(mutated) == [f"governance missing {name}"]


APPROVAL_USAGE_CONTEXT_SERVICES = {
    "persona": "services/persona/write_owner.py",
    "deployment": "services/deployment/service.py",
    "governance": "services/governance/paper_approval_scope.py",
    "runtime-manager": "services/runtime-manager/deploy_authority.py",
}


def scan_approval_usage_context_env_contract(services: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    for service in APPROVAL_USAGE_CONTEXT_SERVICES:
        env = (services[service].get("environment") or {})
        value = env.get("PANTHEON_ENV")
        if value != "${PANTHEON_ENV:-dev}":
            failures.append(f"{service} missing PANTHEON_ENV")
    return failures


def test_approval_usage_context_services_all_wire_pantheon_env() -> None:
    for service, path in APPROVAL_USAGE_CONTEXT_SERVICES.items():
        assert "ApprovalUsageContext(" in (ROOT / path).read_text(encoding="utf-8"), service
    assert scan_approval_usage_context_env_contract(SERVICES) == []
    for service in APPROVAL_USAGE_CONTEXT_SERVICES:
        mutated = copy.deepcopy(SERVICES)
        del mutated[service]["environment"]["PANTHEON_ENV"]
        assert scan_approval_usage_context_env_contract(mutated) == [
            f"{service} missing PANTHEON_ENV"
        ]


def test_dev_persona_tool_broker_resolves_enabled_with_shared_token():
    import re
    env = issuer.broker_environment({
        "PANTHEON_ENV": "dev",
        "PANTHEON_PERSONA_TOOL_BROKER_TOKEN_FILE": "/tmp/pantheon-broker-contract-token",
    })
    try:
        adapter = SERVICES["openclaw-gateway-adapter"]["environment"]
        gateway = SERVICES["openclaw-gateway"]["environment"]
        def resolve(value):
            m = re.fullmatch(r"\$\{(\w+)(?::-([^}]*))?\}", value)
            return env.get(m.group(1), m.group(2) or "")
        assert resolve(adapter["PERSONA_TOOL_BROKER_ENABLED"]) == "true"
        assert resolve(adapter["PERSONA_TOOL_BROKER_TOKEN"]) == resolve(gateway["PERSONA_TOOL_BROKER_TOKEN"]) != ""
        assert "PERSONA_TOOL_BROKER_TOKEN" in str(SERVICES["openclaw-gateway"]["command"])
    finally:
        Path("/tmp/pantheon-broker-contract-token").unlink(missing_ok=True)


def test_adapter_requires_principal_file_mount_and_memory_credential() -> None:
    """The adapter must read its memory credential from the issuer-delivered read-only file."""
    adapter = SERVICES["openclaw-gateway-adapter"]
    env = adapter["environment"]
    mount = "dev-paper-openclaw-adapter-tokens:/run/pantheon-principals:ro"
    assert mount in adapter["volumes"]
    file_var = env["OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE"]
    assert "/run/pantheon-principals/OPENCLAW_ADAPTER_MEMORY_TOKEN" in file_var
    assert "OPENCLAW_ADAPTER_MEMORY_TOKEN" in issuer.CONSUMER_FILES["openclaw-gateway-adapter"]
    # Contract fails if either the mount or the credential variable is dropped.
    for broken in (
        {**adapter, "volumes": [v for v in adapter["volumes"] if v != mount]},
        {**adapter, "environment": {k: v for k, v in env.items() if k != "OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE"}},
    ):
        ok = (mount in broken["volumes"]
              and "OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE" in broken["environment"])
        assert not ok
